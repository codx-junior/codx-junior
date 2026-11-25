"""
Browser session files REST API — the project folders mapped into a session.

A browser session mounts any number of project folders inside its container
under ``/home/<project_name>/<folder>`` and writes every download inside the
project, so this router exposes the mapping and the files the browser saved:

    GET /api/browsers/<session_id>/files?limit=200

The router is discovered automatically by ``codx.junior.api.discover_routers``
(every module in this package exporting ``router`` is included with the
``/api`` prefix), so ``app.py`` and ``api/browser.py`` stay untouched by it.
"""

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from codx.junior.engine.session import CODXJuniorSession
from codx.junior.api.browser_models import (
    BrowserSharedFile,
    BrowserSharedFilesResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browsers", tags=["browser"])


async def get_session_context(request: Request) -> CODXJuniorSession:
    """Dependency: get the current session context from request state.

    Async on purpose: every endpoint awaits it (``await get_session_context``).
    A sync function here yields the session object directly and the await then
    fails with "object CODXJuniorSession can't be used in 'await' expression".
    """
    session = getattr(request.state, "codx_junior_session", None)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session",
        )
    return session


def _browser_manager(session: CODXJuniorSession):
    """
    Return a BrowserManager for the session.

    Reuses ``session.browser_manager`` when present so a long-lived manager can
    be injected, otherwise constructs one from the project settings.
    """
    manager = getattr(session, "browser_manager", None)
    if manager:
        return manager
    from codx.junior.browser.browser_manager import BrowserManager
    return BrowserManager(session.settings)


class SendFileToPageRequest(BaseModel):
    """One file from the session folders pushed into the page's uploader."""

    path: str = Field(
        description=(
            "File name (looked up in the session's downloads folder, then the "
            "shared folders), a path relative to one of them, a browser path "
            "(/downloads/photo.jpg) or a host absolute path inside them."
        )
    )
    selector: Optional[str] = Field(
        default=None,
        description=(
            "CSS selector of the file input or of the upload button/area. Omit to "
            "let the runtime pick the page's file input (or the one a click opens)."
        ),
    )


class SendFileToPageResponse(BaseModel):
    """Receipt of pushing a file into the page."""

    sent: bool
    file: str
    selector: Optional[str] = None
    strategy: Optional[str] = None
    count: int = 0


class UploadedFileResponse(BaseModel):
    """A local file stored in the session's downloads folder."""

    name: str
    path: str
    size: int
    folder: str = ""


@router.get(
    "/{session_id}/files",
    response_model=BrowserSharedFilesResponse,
    summary="List the folders and files shared with a browser session",
    responses={
        200: {"description": "Project mapping and downloaded files"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def get_shared_files(
    request: Request,
    session_id: str,
    limit: int = 200,
) -> BrowserSharedFilesResponse:
    """
    Return the folder mapping of a session and the files it downloaded.

    Every project folder is mounted inside the browser container under
    ``home_path`` — so pages can open ``file:///home/<project>/<folder>/...`` and
    a file picker can upload project files — while everything the browser
    downloads is written to ``downloads_path`` inside the project.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("get_shared_files: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        paths = browser_manager.get_shared_paths(session_id) or {"session_id": session_id}
        files: List[dict] = browser_manager.get_shared_files(session_id, limit=limit)
        logger.info(
            "get_shared_files: session %s shares %d folder(s) and %d file(s)",
            session_id[:8], len(paths.get("folders") or []), len(files),
        )
        return BrowserSharedFilesResponse(
            **paths,
            count=len(files),
            files=[BrowserSharedFile(**entry) for entry in files],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_shared_files: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/files/upload",
    response_model=UploadedFileResponse,
    summary="Store a local file in the session downloads folder",
    responses={
        200: {"description": "File stored"},
        400: {"description": "Invalid file/folder"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        413: {"description": "File too large"},
        500: {"description": "Server error"},
    },
)
async def upload_shared_file(
    request: Request,
    session_id: str,
    file: UploadFile = File(...),
    folder: str = Form(""),
) -> UploadedFileResponse:
    """
    Store a file chosen on the human's machine into the session downloads folder.

    This is the first half of the upload bridge: the browser image's *native*
    file dialog cannot browse the project (and does not deliver the selection on
    the kiosk image), so the UI uploads the bytes here and then calls
    ``send-to-page`` to push the stored file into the page's file input.
    """
    from codx.junior.browser.uploads import max_upload_bytes, safe_filename, unique_path

    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not browser_session.downloads_path:
            raise HTTPException(status_code=400, detail="This session has no downloads folder")

        relative = str(folder or "").strip().replace("\\", "/").strip("/")
        if any(part in ("", ".", "..") for part in relative.split("/") if part):
            if relative:
                raise HTTPException(status_code=400, detail="Invalid folder")
        target_dir = Path(browser_session.downloads_path) / relative if relative else Path(browser_session.downloads_path)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise HTTPException(status_code=500, detail=f"Cannot create the folder: {ex}")

        name = safe_filename(file.filename)
        target = unique_path(target_dir, name)
        limit = max_upload_bytes()
        written = 0
        try:
            with open(target, "wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File is larger than the {limit} byte upload limit",
                        )
                    handle.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except Exception as ex:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Could not store the file: {ex}")

        logger.info(
            "upload_shared_file: session %s stored %s (%d bytes)",
            session_id[:8], target.name, written,
        )
        return UploadedFileResponse(
            name=target.name,
            path=str(target),
            size=written,
            folder=relative,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_shared_file: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/files/send-to-page",
    response_model=SendFileToPageResponse,
    summary="Push a session file into the page's uploader",
    responses={
        200: {"description": "File attached to the page"},
        400: {"description": "File not resolved or the page refused it"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        409: {"description": "Session is not running"},
        500: {"description": "Server error"},
    },
)
async def send_file_to_page(
    request: Request,
    session_id: str,
    payload: SendFileToPageRequest,
) -> SendFileToPageResponse:
    """
    Attach a session file to the page, without the browser's native dialog.

    The bytes are streamed over CDP to the page's file input (or to the file
    chooser its button opens), so the kiosk image's broken file dialog is never
    involved.  This is the same path the AI uses with
    ``browse_action(action="upload")``.
    """
    from codx.junior.browser import uploads as browser_uploads

    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")
        if browser_session.status != "running":
            raise HTTPException(status_code=409, detail="The browser session is not running")

        try:
            resolved = browser_uploads.resolve_session_file(browser_session, payload.path)
            file_payload = browser_uploads.file_payload(resolved)
        except browser_uploads.UploadError as ex:
            raise HTTPException(status_code=400, detail=str(ex))

        runtime = await manager.get_runtime(browser_session)
        try:
            receipt = await runtime.run("upload_files", payload.selector, [file_payload])
        except Exception as ex:
            raise HTTPException(status_code=400, detail=f"The page did not accept the file: {ex}")

        logger.info(
            "send_file_to_page: session %s sent %s (%s)",
            session_id[:8], resolved.name, receipt.get("strategy"),
        )
        return SendFileToPageResponse(
            sent=True,
            file=resolved.describe(),
            selector=receipt.get("selector") or payload.selector,
            strategy=receipt.get("strategy"),
            count=int(receipt.get("count") or 0),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("send_file_to_page: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# Made with ❤️ by codx-junior