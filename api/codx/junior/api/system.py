"""
Self-update endpoints (admin only).

The running API cannot replace its own container, so ``POST /api/system/update``
pulls the target image and starts a detached helper that runs the installer's
``upgrade`` (see :mod:`codx.junior.update` for the full flow). The response is
``202`` with the bookkeeping state; the browser polls ``GET /api/system/update``
until the new container answers.

Why polling instead of a long request
-------------------------------------
A release image pull can take minutes and the container is restarted in the
middle of the operation. A single request would either time out at the proxy or
die with the old process; the state/log live in the settings folder and are
shared by the old and the new container, so polling survives the restart.

Security
--------
Both routes require the ``admin`` role: the operation is root-equivalent on the
host (the helper mounts the Docker socket and recreates the application
container). The update target image is validated before it reaches the helper.

Error statuses: ``400`` invalid image reference, ``409`` this instance cannot
update itself (no compose installation, DIND, remote daemon) or an update is
already running, ``503`` the daemon/registry failed.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from codx.junior import update
from codx.junior.api import require_admin
from codx.junior.model.model import CodxUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


class SystemUpdateRequest(BaseModel):
    """Body of ``POST /api/system/update``."""

    image: str = ""
    """Target image reference (default: the image this container runs)."""

    force: bool = False
    """Recreate the container even when the image did not change."""


class SystemUpdateCheckRequest(BaseModel):
    """Body of ``POST /api/system/update/check``."""

    image: str = ""
    """Target image reference (default: the image this container runs)."""


def _raise_http(ex: update.UpdateError) -> None:
    raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


@router.get(
    "/update",
    summary="[Admin] Current version, installation and last update state",
)
def get_update_status(_: CodxUser = Depends(require_admin)) -> dict:
    """
    Return what the settings page needs to offer an update.

    The payload is always complete, including when the instance cannot update
    itself: ``supported`` is then ``false``, ``reason`` explains why and
    ``manual_command`` is the host command that does the same thing.

    Requires the ``admin`` role.
    """
    try:
        return update.snapshot()
    except Exception as ex:  # noqa: BLE001 - never break the settings page
        logger.exception("Cannot build the update status")
        raise HTTPException(
            status_code=500, detail=f"Cannot read the update status: {ex}"
        ) from ex


@router.post(
    "/update/check",
    status_code=202,
    summary="[Admin] Pull the target image and report the version it contains",
)
def check_update(
    payload: SystemUpdateCheckRequest,
    _: CodxUser = Depends(require_admin),
) -> dict:
    """
    Check for a newer image without recreating the container.

    The pull happens in the background (a release image can take minutes); the
    answer is the initial state and the browser polls ``GET /api/system/update``
    for ``check.status``/``latest``. Nothing is restarted, so the check is safe
    to run every time the Updates tab opens.

    Requires the ``admin`` role.
    """
    image = ""
    if payload.image.strip():
        try:
            image = update.validate_image(payload.image)
        except update.UpdateError as ex:
            _raise_http(ex)
    try:
        return update.start_check(image=image)
    except update.UpdateError as ex:
        _raise_http(ex)
    except Exception as ex:  # noqa: BLE001 - the state file has the detail
        logger.exception("Cannot check for an update")
        raise HTTPException(
            status_code=500, detail=f"Cannot check for an update: {ex}"
        ) from ex


@router.post(
    "/update",
    status_code=202,
    summary="[Admin] Pull the target image and recreate the container",
)
def start_update(
    payload: SystemUpdateRequest,
    _: CodxUser = Depends(require_admin),
) -> dict:
    """
    Start the update in the background and return the initial state.

    The image is pulled first; when it did not change (and ``force`` is false)
    the answer's ``status`` is ``up_to_date`` and the container is left alone.
    Otherwise a helper container runs ``<install-home>/bin/codx-junior upgrade``
    and this container is recreated by the installer.

    Requires the ``admin`` role.
    """
    image = ""
    if payload.image.strip():
        try:
            image = update.validate_image(payload.image)
        except update.UpdateError as ex:
            _raise_http(ex)
    try:
        return update.start_update(image=image, force=bool(payload.force))
    except update.UpdateError as ex:
        _raise_http(ex)
    except Exception as ex:  # noqa: BLE001 - the state file has the detail
        logger.exception("Cannot start the update")
        raise HTTPException(
            status_code=500, detail=f"Cannot start the update: {ex}"
        ) from ex


@router.get(
    "/update/log",
    summary="[Admin] Tail of the current/last update log",
)
def get_update_log(lines: int = 200, _: CodxUser = Depends(require_admin)) -> dict:
    """
    Return the current/last update log (it survives the container restart).

    Requires the ``admin`` role.
    """
    state = update.describe_state()
    return {
        "status": state["status"],
        "log": update.tail_log(state.get("log_path", ""), max(10, min(lines, 2000))),
    }


# Made with ❤️ by codx-junior
