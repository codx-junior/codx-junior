"""Media API of the Project's Channel: providers, queue, episodes and assets.

Thin HTTP layer over ``codx.junior.media`` (models, store, budget, providers).
No business logic lives here: the endpoints only translate a request into a
``MediaStore``/``ProviderRegistry``/``BudgetGate`` call and dump the result as
plain json, so the client can consume it directly.

The router is auto discovered: ``codx.junior.api.discover_routers()`` imports
every module of this package and registers its ``router`` with the ``/api``
prefix, so ``APIRouter(prefix="/media")`` serves ``/api/media/*``.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import ValidationError

from codx.junior.api import get_current_session
from codx.junior.engine import CODXJuniorSession
from codx.junior.media.budget import BudgetGate
from codx.junior.media.model import EpisodeIR, MediaJob, ProviderInfo
from codx.junior.media.providers import default_image_registry
from codx.junior.media.store import MediaStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

#: Budget mirror kept next to the jobs/episodes/assets of the project store
BUDGET_FILENAME = "budget.json"
#: Never return more than this many jobs in a single queue snapshot
MAX_QUEUE_JOBS = 50
#: An asset id is a content hash; anything with a separator cannot be served
ASSET_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def project_path(session: CODXJuniorSession) -> Path:
    """Project directory of the active session."""
    settings = getattr(session, "settings", None)
    path = getattr(settings, "abs_project_path", None) or getattr(
        settings, "project_path", None
    )
    if not path:
        raise HTTPException(
            status_code=400, detail="The active session has no project path"
        )
    return Path(path)


def store_for_project(project_dir: Path) -> MediaStore:
    """Store of a project: ``<project>/.codx/media``."""
    return MediaStore.for_project(Path(project_dir))


def budget_for_store(store: MediaStore) -> BudgetGate:
    """Budget gate bound to the store, so its counters survive a restart."""
    return BudgetGate(path=store.root / BUDGET_FILENAME)


def _store(session: CODXJuniorSession) -> MediaStore:
    return store_for_project(project_path(session))


def _provider_infos() -> List[ProviderInfo]:
    """Descriptions of the built in image providers (empty when unavailable)."""
    try:
        return default_image_registry().list()
    except Exception as ex:  # noqa: BLE001 - a broken provider must not 500
        logger.warning("Cannot describe the media providers: %s", ex)
        return []


def _queue_counts(jobs: List[MediaJob]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for job in jobs:
        counts[job.state] = counts.get(job.state, 0) + 1
    return dict(sorted(counts.items()))


def asset_file(store: MediaStore, asset_id: str) -> Path:
    """Resolve ``asset_id`` to a file stored under ``<store>/assets``.

    Raises 400 when the id could escape the store (separators, ``..``) and 404
    when no stored file matches it. Content addressed assets live in sharded
    folders (``assets/<id[:2]>/<id><ext>``), hence the shard lookup.
    """
    value = (asset_id or "").strip()
    if not value or ".." in value or not ASSET_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"Invalid asset id: {asset_id!r}")

    root = store.assets_dir.resolve()
    shard = store.assets_dir / value[:2]
    found: Optional[Path] = None
    if shard.is_dir():
        for candidate in sorted(shard.iterdir()):
            if candidate.is_file() and (
                candidate.stem == value or candidate.name == value
            ):
                found = candidate
                break
    if found is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {value}")

    resolved = found.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail=f"Invalid asset id: {asset_id!r}")
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers(
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Registered media providers, their capabilities and the budget snapshot."""
    store = _store(session)
    infos = _provider_infos()
    capabilities = sorted({info.capability for info in infos})
    return {
        "providers": [info.model_dump(mode="json") for info in infos],
        "capabilities": capabilities,
        "budget": budget_for_store(store).snapshot(),
    }


@router.get("/queue")
async def get_queue(
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Job counts per state plus the oldest jobs (at most ``MAX_QUEUE_JOBS``)."""
    store = _store(session)
    jobs = store.list_jobs()
    return {
        "counts": _queue_counts(jobs),
        "jobs": [job.model_dump(mode="json") for job in jobs[:MAX_QUEUE_JOBS]],
    }


@router.post("/episodes")
async def create_episode(
    payload: Dict[str, Any] = Body(...),
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Persist an ``EpisodeIR`` shaped body and return the stored ir."""
    store = _store(session)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="An episode payload must be a json object"
        )
    try:
        ir = EpisodeIR.model_validate(payload)
    except ValidationError as ex:
        raise HTTPException(
            status_code=400, detail={"message": "Invalid episode payload", "errors": ex.errors()[:5]}
        )
    return store.put_episode(ir).model_dump(mode="json")


@router.get("/episodes/{episode_id}")
async def get_episode(
    episode_id: str,
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Stored ``EpisodeIR`` or 404 when the episode is unknown."""
    store = _store(session)
    ir = store.get_episode(episode_id)
    if ir is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
    return ir.model_dump(mode="json")


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    session: CODXJuniorSession = Depends(get_current_session),
) -> FileResponse:
    """Serve an asset file resolved inside the project store root."""
    path = asset_file(_store(session), asset_id)
    return FileResponse(path)
