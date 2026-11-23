"""
Browser Session REST API Router

Handles CRUD operations for browser sessions with full async support.

REST Endpoints:
    GET    /api/browsers              - List all sessions
    POST   /api/browsers              - Create new session
    GET    /api/browsers/<session_id> - Get session details
    PUT    /api/browsers/<session_id> - Rename session
    DELETE /api/browsers/<session_id> - Stop & delete session
    POST   /api/browsers/<session_id>/action     - Execute browser action
    POST   /api/browsers/<session_id>/screenshot - Take screenshot
    GET    /api/browsers/<session_id>/source     - Get page source
    GET    /api/browsers/<session_id>/activity   - User/AI audit trail
    GET    /api/browsers/<session_id>/cost       - AI cost/usage summary
    POST   /api/browsers/<session_id>/restart    - Restart session
    POST   /api/browsers/<session_id>/stop       - Stop session (keep container)

Socket.IO Events (real-time status updates for long-running operations):
    codx-junior-browser-create   - Create session with progress
    codx-junior-browser-delete   - Delete session with progress
    codx-junior-browser-restart  - Restart session with progress
    codx-junior-browser-stop     - Stop session with progress

    Emits: codx-junior-browser-status with {status, session_id, message, error, session}

Project folders (see ``codx/junior/api/browser_files.py``):
    GET    /api/browsers/<session_id>/files      - Folder mapping + downloads
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status

from codx.junior.engine.session import CODXJuniorSession
from codx.junior.sio.sio import sio, sio_api_endpoint
from codx.junior.sio.session_channel import SessionChannel
from codx.junior.api.browser_models import (
    BrowserSessionResponse,
    BrowserSessionListResponse,
    BrowserSessionCreateRequest,
    BrowserSessionUpdateRequest,
    BrowserSessionChatRequest,
    BrowserSessionChatResponse,
    BrowserSessionActionRequest,
    BrowserActionResponse,
    BrowserScreenshotResponse,
    BrowserSourceResponse,
    BrowserActivityResponse,
    BrowserCostResponse,
    BrowserScreenshotRequest,
    BrowserControlRequest,
    BrowserControlResponse,
    BrowserRecordingState,
    BrowserRecordingStartRequest,
    BrowserRecordingStopResponse,
    BrowserRecordingSaveRequest,
    BrowserRecordingAnalyzeRequest,
    BrowserScriptSaveRequest,
    BrowserScriptRunRequest,
)
from codx.junior.chat_manager import ChatManager
from codx.junior.browser import events as browser_events
from codx.junior.api.session_access import (
    apply_control_action,
    requester_from_request,
    require_user_control,
)
from codx.junior.browser.recording import (
    RecordingState,
    RecordingStore,
    build_steps,
    suggested_title,
)
from codx.junior.browser.recipe_ai import analyze_recording
from codx.junior.browser.scripts import BrowserScriptStore, normalize_site, parse_steps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browsers", tags=["browser"])

BROWSER_STATUS_EVENT = "codx-junior-browser-status"

#: User actions that do not change the page and therefore must not pause the AI.
READ_ONLY_ACTIONS = {"text", "read", "links", "snapshot", "screenshot"}

# ============================================================================
# Helpers
# ============================================================================

async def get_session_context(request: Request) -> CODXJuniorSession:
    """Dependency: get the current session context from request state."""
    session = getattr(request.state, 'codx_junior_session', None)
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
    from codx.junior.browser.browser_manager import get_browser_manager
    return get_browser_manager(session.settings)


def _emit(channel: SessionChannel, payload: dict) -> None:
    """Send a browser status event (keeps the ts-stamping in one place)."""
    channel.send_event(BROWSER_STATUS_EVENT, payload)


# ---------------------------------------------------------------------------
# Recording + reusable recipe helpers
# ---------------------------------------------------------------------------
def _project_root(manager) -> str:
    """Return the project folder that owns ``.codx/browser_scripts``."""
    settings = getattr(manager, "settings", None)
    for value in (
        getattr(settings, "abs_project_path", None),
        getattr(settings, "project_path", None),
        os.environ.get("CODX_PROJECT_PATH"),
        os.getcwd(),
    ):
        if isinstance(value, str) and value.strip():
            return os.path.abspath(os.path.expanduser(value.strip()))
    return os.getcwd()


def _script_store(manager) -> BrowserScriptStore:
    """Return the per-project recipe library."""
    return BrowserScriptStore(_project_root(manager))


def _recording_store(manager, session_id: str) -> RecordingStore:
    """Return the recording state file of one session."""
    return RecordingStore(manager._session_folder_path(session_id))


def _recording_payload(
    state: Optional[RecordingState], session_id: str = ""
) -> Dict[str, Any]:
    """Serializable recording state (empty when the session never recorded)."""
    if state is None:
        return BrowserRecordingState(session_id=session_id, active=False).model_dump()
    return BrowserRecordingState(**state.summary()).model_dump()


def _require_browser_session(manager, session_id: str):
    """Load a session and reject desktop/remote sessions for DOM recording."""
    browser_session = manager.get_session(session_id)
    if not browser_session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not browser_session.is_browser:
        raise HTTPException(
            status_code=400,
            detail="Recording is only available for browser sessions (DOM recorder)",
        )
    return browser_session


def _script_summary(script) -> Dict[str, Any]:
    """Return one script including its parsed steps."""
    data = script.to_dict(include_code=True)
    data["steps"] = parse_steps(script.code) if script.language == "steps" else []
    data["step_count"] = len(data["steps"])
    return data


def _recording_events(manager, state: RecordingState) -> List[Dict[str, Any]]:
    """Return the human events captured since the recording started."""
    entries = manager.get_activity(state.session_id, limit=2000)
    return [
        entry for entry in entries
        if (entry.get("actor") or "") == "user"
        and (entry.get("ts") or "") >= (state.started_at or "")
    ]


# ============================================================================
# Background lifecycle (create / delete orders)
# ============================================================================
def _emit_session_status(browser_session, action, status=None, message=None, settings=None):
    """
    Announce the status of a co-desktop to every connected client.

    Creating one (image pull + ``docker run``) and deleting one take seconds,
    so the request only accepts the order: the card stays in ``creating`` /
    ``deleting`` until this event says what actually happened.

    Args:
        browser_session: Session whose status changed.
        action: Collection action (``created``, ``updated``, ``deleted``...).
        status: New status when it cannot be read from the record yet.
        message: Human-readable progress line for the UI.
        settings: Project settings used to scope the event.
    """
    try:
        from codx.junior.events.collection_events import (
            COLLECTION_COMPUTER_SESSIONS,
            emit_collection_event,
        )

        emit_collection_event(
            COLLECTION_COMPUTER_SESSIONS,
            action,
            item={
                "id": getattr(browser_session, "id", None),
                "name": getattr(browser_session, "name", None),
                "kind": getattr(browser_session, "kind", None),
                "status": status or getattr(browser_session, "status", None),
            },
            settings=settings,
            source="computer_session",
            message=message,
        )
    except Exception as ex:  # never break a lifecycle for a status message
        logger.debug("Could not emit the session status event: %s", ex)


async def _start_session_in_background(browser_manager, browser_session, settings=None):
    """
    Start a session created with ``background=True`` and report its status.

    Runs as an asyncio task right after the create order was answered, so the
    slow half of the creation (image pull, container run, readiness polling)
    never blocks the frontend.
    """
    from codx.junior.events.collection_events import ACTION_UPDATED

    session_id = browser_session.id
    _emit_session_status(
        browser_session,
        ACTION_UPDATED,
        status="starting",
        message="Starting the machine (image pull and boot)…",
        settings=settings,
    )
    try:
        started = await asyncio.to_thread(browser_manager.start_session, session_id)
    except Exception as ex:
        logger.exception("create_session: background start of %s failed: %s", session_id[:8], ex)
        _emit_session_status(
            browser_session,
            ACTION_UPDATED,
            status="error",
            message=f"Could not start the machine: {ex}",
            settings=settings,
        )
        return None
    if not started:
        return None
    # The recorder/watcher is warm from the first client connection anyway: it is
    # best effort here, never a reason to report a failed creation.
    try:
        await browser_manager.warm_runtime(started)
    except Exception as ex:
        logger.debug("warm_runtime after a background start failed: %s", ex)
    _emit_session_status(
        started,
        ACTION_UPDATED,
        status=started.status,
        message="Machine ready",
        settings=settings,
    )
    return started


# ============================================================================
# REST Endpoints
# ============================================================================

@router.get(
    "",
    response_model=BrowserSessionListResponse,
    summary="List all browser sessions",
    responses={
        200: {"description": "List of browser sessions"},
        401: {"description": "Unauthorized"},
        500: {"description": "Server error"},
    },
)
async def list_sessions(request: Request):
    """List all browser sessions for the current project."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        sessions = await asyncio.to_thread(browser_manager.list_sessions)
        responses = [BrowserSessionResponse(**s.to_public_dict()) for s in sessions]
        logger.info("list_sessions: returned %d browser sessions", len(sessions))
        return BrowserSessionListResponse(count=len(responses), sessions=responses)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_sessions: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "",
    response_model=BrowserSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new browser session",
    responses={
        201: {"description": "Session created"},
        400: {"description": "Invalid request (unknown project folder)"},
        401: {"description": "Unauthorized"},
        500: {"description": "Server error"},
    },
)
async def create_session(request: Request, req: BrowserSessionCreateRequest):
    """
    Create and start a new browser session.

    Starts a Chromium Docker container with Traefik routing and a persistent profile.
    The session is named (``req.name``) and optionally connected to ``req.chat_id``.
    Multiple chats may connect to the same session.

    Project folders listed in ``req.share_folders`` are mounted inside the
    container under ``/home/<project_name>/<folder>``; ``req.downloads_folder``
    selects the project folder receiving the browser downloads.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        chat = None
        chat_manager = None
        if req.chat_id:
            chat_manager = ChatManager(session.settings)
            chat = chat_manager.find_by_id(req.chat_id)
            if not chat:
                logger.error("create_session: chat not found: %s", req.chat_id)
                raise HTTPException(status_code=404, detail="Chat not found")

        # Docker run + readiness polling + profile setup take seconds and must
        # never block the API event loop (which also serves chats, streaming
        # and every other browser action).
        browser_session = await asyncio.to_thread(
            browser_manager.create_session,
            name=req.name,
            chat_id=req.chat_id,
            initial_url=req.initial_url,
            description=req.description,
            share_project=req.share_project,
            share_folders=req.share_folders,
            readonly=req.readonly,
            downloads_folder=req.downloads_folder,
            downloads_mount=req.downloads_mount,
            kind=req.kind,
            os_version=req.os_version,
            image=req.image,
            image_provider=req.image_provider,
            library_item_id=req.library_item_id,
            control=req.control,
            vnc_host=req.vnc_host,
            vnc_port=req.vnc_port,
            vnc_password=req.vnc_password,
            vnc_public_url=req.vnc_public_url,
            ssh_host=req.ssh_host,
            ssh_port=req.ssh_port,
            ssh_user=req.ssh_user,
            ssh_key_path=req.ssh_key_path,
            ssh_password=req.ssh_password,
            guest_share_path=req.guest_share_path,
            cpu_cores=req.cpu_cores,
            ram_size=req.ram_size,
            disk_size=req.disk_size,
            shm_size=req.shm_size,
            extra_env=req.extra_env,
            desktop_width=req.desktop_width,
            desktop_height=req.desktop_height,
            control_mode=getattr(req, "control_mode", None),
            defer_start=bool(getattr(req, "background", False)),
        )

        if chat and chat_manager:
            browser_manager.save_session_to_chat(browser_session, chat)
            # The chat store is rewritten as a whole JSON document: keep it off
            # the event loop, the single worker must stay responsive.
            await asyncio.to_thread(chat_manager.save_chat, chat)

        # A remote session is already "running": only a machine that is still
        # pending has a slow half left to run in background.
        if getattr(req, "background", False) and browser_session.status == "creating":
            # The order is accepted: the record already exists in "creating" and
            # the machine starts behind the response, reporting every status
            # change through the computer_sessions collection event.  The client
            # keeps the card as "creating" and follows those messages.
            from codx.junior.events.collection_events import ACTION_CREATED

            _emit_session_status(
                browser_session,
                ACTION_CREATED,
                status=browser_session.status,
                message="Creating the machine…",
                settings=session.settings,
            )
            asyncio.create_task(
                _start_session_in_background(
                    browser_manager, browser_session, session.settings
                )
            )
        else:
            # Start the recorder/watcher without blocking the response.  If the
            # user drives before the AI, their actions must still be visible.
            try:
                asyncio.create_task(browser_manager.warm_runtime(browser_session))
            except RuntimeError:
                pass

        logger.info(
            "create_session: created browser session %s ('%s') for chat %s with %d shared folder(s)",
            browser_session.id[:8],
            browser_session.name,
            req.chat_id,
            len(browser_session.shared_folders),
        )
        return BrowserSessionResponse(**browser_session.to_public_dict())

    except HTTPException:
        raise
    except ValueError as e:
        logger.error("create_session: invalid share configuration: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("create_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{session_id}",
    response_model=BrowserSessionResponse,
    summary="Get session details",
    responses={
        200: {"description": "Session details"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def get_session(request: Request, session_id: str):
    """Get details for a specific browser session."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("get_session: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        logger.info("get_session: retrieved session %s", session_id[:8])
        return BrowserSessionResponse(**browser_session.to_public_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{session_id}",
    response_model=BrowserSessionResponse,
    summary="Update a browser/computer session",
    responses={
        200: {"description": "Session updated"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def update_session(request: Request, session_id: str, req: BrowserSessionUpdateRequest):
    """Update name, connection or advanced image/resource settings.

    Resource changes (image, CPUs, RAM, disk, shared memory, environment) are
    stored on the session and applied when it is restarted: Docker cannot
    change them on a running container, and the restart recreates it while
    keeping the profile and the VM disk.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)
        advanced_keys = {
            "image", "cpu_cores", "ram_size", "disk_size", "shm_size",
            "extra_env", "desktop_width", "desktop_height",
        }
        browser_session = browser_manager.update_session(
            session_id,
            name=req.name,
            description=req.description,
            control=req.control,
            vnc_host=req.vnc_host,
            vnc_port=req.vnc_port,
            vnc_password=req.vnc_password,
            vnc_public_url=req.vnc_public_url,
            ssh_host=req.ssh_host,
            ssh_port=req.ssh_port,
            ssh_user=req.ssh_user,
            ssh_key_path=req.ssh_key_path,
            ssh_password=req.ssh_password,
            advanced=req.model_dump(include=advanced_keys, exclude_unset=True),
        )
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")
        return BrowserSessionResponse(**browser_session.to_public_dict())
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning("update_session: invalid advanced settings: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("update_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/chat",
    response_model=BrowserSessionChatResponse,
    summary="Open or create the chat linked to a session",
    responses={
        200: {"description": "Chat linked to the session"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session or chat not found"},
        500: {"description": "Server error"},
    },
)
async def open_session_chat(
    request: Request,
    session_id: str,
    req: Optional[BrowserSessionChatRequest] = None,
):
    """
    Return a chat that can drive the browser session from the session view.

    If the session already has a linked chat, that chat is reused. If it has
    none (or the caller passes a different ``chat_id``), a chat is created and
    linked to the session. Chats opened this way get the built-in ``browser``
    profile so the agent receives the browser tools.
    """
    from codx.junior.db import Chat

    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)
        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")

        chat_manager = ChatManager(session.settings)
        chat = None

        requested_chat_id = req.chat_id if req else None
        create_new = bool(req.create_new) if req else False
        if requested_chat_id:
            chat = chat_manager.find_by_id(requested_chat_id)
            if not chat:
                raise HTTPException(status_code=404, detail="Chat not found")
        elif not create_new and browser_session.chat_ids:
            chat = chat_manager.find_by_id(browser_session.chat_ids[0])

        # Browser sessions use the browser co-pilot profile; desktops/VMs use
        # the computer profile (same manager, desktop action tools).
        profile_name = "browser" if browser_session.kind == "browser" else "computer"
        profile_available = False
        try:
            profile_available = bool(
                session.get_profile_manager().get_profiles_by_name([profile_name])
            )
        except Exception as ex:
            logger.debug("Could not check the %s profile: %s", profile_name, ex)

        if not chat:
            base_name = f"{browser_session.kind_label}: {browser_session.name or browser_session.id[:8]}"
            suffix = f" ({len(browser_session.chat_ids) + 1})" if browser_session.chat_ids else ""
            chat = Chat(
                name=f"{base_name}{suffix}",
                mode="chat",
                project_id=session.settings.project_id,
                owner_project_id=session.settings.project_id,
                profiles=[profile_name] if profile_available else [],
                meta_data={},
            )
            await asyncio.to_thread(chat_manager.save_chat, chat)

        # The chat explicitly asks to operate this computer: make sure the
        # matching profile (and therefore its tools) is active.
        if profile_available and profile_name not in (chat.profiles or []):
            chat.profiles = [*(chat.profiles or []), profile_name]

        browser_manager.save_session_to_chat(browser_session, chat)
        await asyncio.to_thread(chat_manager.save_chat, chat)
        logger.info(
            "open_session_chat: session %s linked to chat %s",
            session_id[:8], chat.id,
        )
        return BrowserSessionChatResponse(
            session=BrowserSessionResponse(**browser_session.to_public_dict()),
            chat=chat.model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("open_session_chat: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop and delete a session",
    responses={
        204: {"description": "Session deleted"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def delete_session(request: Request, session_id: str, background: bool = False):
    """
    Stop the Docker container and delete a browser session.

    ``background=true`` accepts the order and returns immediately: the session
    is persisted as ``deleting`` and a daemon thread stops the container and
    moves the profile to trash, so the frontend never waits for Docker.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("delete_session: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        from codx.junior.events.collection_events import ACTION_DELETED

        if background:
            await asyncio.to_thread(browser_manager.mark_session_deleting, browser_session)
            _emit_session_status(
                browser_session,
                ACTION_DELETED,
                status="deleting",
                message="Deleting the machine…",
                settings=session.settings,
            )
            logger.info("delete_session: queued session %s for deletion", session_id[:8])
            return None

        await asyncio.to_thread(browser_manager.delete_session, session_id)
        _emit_session_status(
            browser_session,
            ACTION_DELETED,
            status="deleted",
            message="Machine deleted",
            settings=session.settings,
        )
        logger.info("delete_session: deleted session %s", session_id[:8])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/control",
    response_model=BrowserControlResponse,
    summary="Move the human/AI control lease",
    responses={
        200: {"description": "Control updated"},
        400: {"description": "Invalid action"},
        404: {"description": "Session not found"},
    },
)
async def control_session(request: Request, session_id: str, req: BrowserControlRequest):
    """Take control, hand it to the user/AI, or inspect the current lease.

    Co-browsing works because both actors share one page; this endpoint makes
    the hand-off explicit so the AI does not type over the human (and the human
    is not surprised by an AI navigation).
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)
        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")

        runtime = await browser_manager.get_runtime(browser_session)
        # The runtime keeps a cached session object; make it the same instance
        # so control moves apply to the entity the AI actions check.
        if getattr(runtime, "session", None) is not None:
            runtime.session = browser_session
        requester = requester_from_request(request, session, req)
        action = (req.action or "").strip().lower()
        if action in ("handoff", "request_handoff"):
            if not req.reason:
                raise HTTPException(400, "reason is required for a handoff")
            # The AI (or a chat) hands the screen to the human: keep the
            # legacy "needs user" flag/notification, and let the first human
            # that clicks claim the human lease.
            browser_session.ensure_participant(
                requester.participant_id,
                username=requester.username,
                name=requester.display_name,
                avatar=requester.avatar,
                role=requester.role,
            )
            runtime.request_handoff(req.reason, chat_id=req.chat_id)
            browser_session = getattr(runtime, "session", None) or browser_session
            message = f"Control handed to the user: {req.reason}"
        else:
            result = apply_control_action(browser_session, action, requester, req)
            message = result["message"]
        if action not in ("status", "info", "state"):
            browser_manager.save_session(browser_session)
        browser_events.broadcast_control(browser_session)
        browser_events.broadcast_participants(browser_session)
        return BrowserControlResponse(
            success=True,
            message=message,
            session=BrowserSessionResponse(**browser_session.to_public_dict()),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("control_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/restart",
    response_model=BrowserSessionResponse,
    summary="Restart a stopped session",
    responses={
        200: {"description": "Session restarted"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def restart_session(request: Request, session_id: str):
    """Restart a stopped browser session (preserves the Chromium profile)."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = await browser_manager.restart_session(session_id)
        if not browser_session:
            logger.error("restart_session: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        logger.info("restart_session: restarted session %s", session_id[:8])
        return BrowserSessionResponse(**browser_session.to_public_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("restart_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/stop",
    response_model=BrowserSessionResponse,
    summary="Stop a session (keep container)",
    responses={
        200: {"description": "Session stopped"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def stop_session(request: Request, session_id: str):
    """Stop the container but keep it so it can be restarted later."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        if browser_manager.peek_runtime(session_id) is not None:
            runtime = browser_manager.peek_runtime(session_id)
            await runtime.close()
            browser_manager._runtimes.pop(session_id, None)
        browser_session = await asyncio.to_thread(browser_manager.stop_session, session_id)
        if not browser_session:
            logger.error("stop_session: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        logger.info("stop_session: stopped session %s", session_id[:8])
        return BrowserSessionResponse(**browser_session.to_public_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("stop_session: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/action",
    response_model=BrowserActionResponse,
    summary="Execute a browser action",
    responses={
        200: {"description": "Action executed"},
        400: {"description": "Invalid action or session not running"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def execute_action(request: Request, session_id: str, req: BrowserSessionActionRequest):
    """
    Execute a browser action on the shared page.

    The action is performed through the same CDP connection the AI uses, so the
    user's live VNC view reflects it immediately.
    """
    browser_manager = None
    browser_session = None
    action = (req.action or "").lower().strip()
    started_at = time.monotonic()
    params: Dict[str, Any] = {
        key: value
        for key, value in {
            "url": req.url,
            "selector": req.selector,
            "ref": req.ref,
            "text": req.text,
            "value": req.value,
            "script": req.script,
            "key": req.key,
            "timeout": req.timeout,
            "x": req.x,
            "y": req.y,
            "full_page": req.full_page,
        }.items()
        if value is not None
    }
    if action == "batch":
        params["steps"] = len(req.steps or [])
    title = None

    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("execute_action: session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Session not found")

        # Two humans can watch the same session; only the control owner (or an
        # admin) may change the page. The live-view overlay is the friendly
        # gate, this is the authoritative one. Read-only actions (snapshot,
        # screenshot, links...) remain available to observers.
        if action not in READ_ONLY_ACTIONS:
            require_user_control(
                browser_session,
                requester_from_request(request, session),
            )

        if browser_session.status != "running":
            logger.error(
                "execute_action: session not running: %s (status=%s)",
                session_id, browser_session.status,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Session not running (status: {browser_session.status})",
            )

        runtime = await browser_manager.get_runtime(browser_session)
        # The user is performing this action: it bypasses the AI control lease
        # (the human is allowed to drive at any time) and marks them active.
        if action == "navigate":
            if not req.url:
                raise HTTPException(400, "URL required for navigate action")
            title = await runtime.run("navigate", req.url, force=True)
            message, data = f"Navigated to {req.url}", {"title": title, "url": req.url}

        elif action == "click":
            if req.ref:
                await runtime.run("click_ref", req.ref, force=True)
                message, data = f"Clicked ref {req.ref}", {"ref": req.ref}
            elif req.text:
                await runtime.run("click_text", req.text, force=True)
                message, data = f"Clicked text {req.text!r}", {"text": req.text}
            elif req.selector:
                await runtime.run("click", req.selector, force=True)
                message, data = f"Clicked {req.selector}", {"selector": req.selector}
            else:
                raise HTTPException(400, "Selector, ref or text required for click action")

        elif action == "fill":
            if req.value is None:
                raise HTTPException(400, "Value required for fill action")
            if req.ref:
                await runtime.run("fill_ref", req.ref, req.value, force=True)
                message, data = f"Filled ref {req.ref}", {"ref": req.ref}
            elif req.selector:
                await runtime.run("fill", req.selector, req.value, force=True)
                message, data = f"Filled {req.selector}", {"selector": req.selector}
            else:
                raise HTTPException(400, "Selector or ref required for fill action")

        elif action == "execute":
            if not req.script:
                raise HTTPException(400, "Script required for execute action")
            result = await runtime.run("execute_script", req.script, force=True)
            message, data = "Script executed", {"result": result}

        elif action == "text" or action == "read":
            text = await runtime.run("get_text", read_only=True, force=True)
            message, data = "Page text read", {"text": text}

        elif action == "links":
            links = await runtime.run("get_links", read_only=True, force=True)
            message, data = "Page links read", {"links": links}

        elif action == "snapshot":
            # "Ask AI about this page" is a user read: bypass the prefetch so a
            # field the human just edited is included.
            snapshot = await runtime.snapshot(prefer_cached=False)
            message, data = "Page snapshot captured", {"snapshot": snapshot}

        elif action == "back":
            title = await runtime.run("go_back", force=True)
            message, data = "Navigated back", {"title": title}

        elif action == "forward":
            title = await runtime.run("go_forward", force=True)
            message, data = "Navigated forward", {"title": title}

        elif action == "refresh":
            title = await runtime.run("reload", force=True)
            message, data = "Page refreshed", {"title": title}

        elif action == "wait":
            if req.text:
                await runtime.run("wait_for_text", req.text, timeout=req.timeout or 5000, force=True)
                message, data = f"Text appeared: {req.text!r}", {"text": req.text}
            elif req.selector:
                await runtime.run("wait_for_selector", req.selector, timeout=req.timeout or 5000, force=True)
                message, data = f"Element appeared: {req.selector}", {"selector": req.selector}
            elif req.url:
                await runtime.run("wait_for_url", req.url, timeout=req.timeout or 5000, force=True)
                message, data = f"URL matched: {req.url}", {"url": req.url}
            else:
                raise HTTPException(400, "Text, selector or URL required for wait action")

        elif action == "press":
            if not req.selector or not req.key:
                raise HTTPException(400, "Selector and key required for press action")
            await runtime.run("press", req.selector, req.key, force=True)
            message, data = f"Pressed {req.key} on {req.selector}", {"selector": req.selector, "key": req.key}

        elif action == "select":
            if not req.selector or req.value is None:
                raise HTTPException(400, "Selector and value required for select action")
            await runtime.run("select", req.selector, req.value, force=True)
            message, data = f"Selected {req.value} on {req.selector}", {"selector": req.selector, "value": req.value}

        elif action == "hover":
            if not req.selector:
                raise HTTPException(400, "Selector required for hover action")
            await runtime.run("hover", req.selector, force=True)
            message, data = f"Hovered {req.selector}", {"selector": req.selector}

        elif action == "scroll":
            await runtime.run("scroll", x=req.x or 0, y=req.y or 0, force=True)
            message, data = "Page scrolled", {"x": req.x or 0, "y": req.y or 0}

        elif action == "screenshot":
            screenshot = await runtime.screenshot(full_page=bool(req.full_page))
            message, data = "Screenshot captured", {"screenshot": screenshot, "full_page": bool(req.full_page)}

        elif action == "batch":
            if not req.steps:
                raise HTTPException(400, "Steps required for batch action")
            result = await runtime.run_steps(req.steps, req.variables or {}, force=True)
            message, data = "Batch executed", {"batch": result}

        else:
            raise HTTPException(400, f"Unknown action: {req.action}")

        browser_session = getattr(runtime, "session", None) or browser_session
        # Read-only context reads (snapshot/text/links/screenshot) must not
        # pause the AI: the "Ask AI about this page" button uses them right
        # before the user sends a message.
        if action not in READ_ONLY_ACTIONS:
            browser_session.mark_user_active(seconds=10)
        data.update({
            "url": browser_session.current_url,
            "title": browser_session.current_title,
        })
        browser_manager.log_activity(
            browser_session,
            actor="user",
            action=action,
            params=params,
            url=browser_session.current_url,
            title=browser_session.current_title,
            duration_ms=(time.monotonic() - started_at) * 1000,
            result={"message": message},
        )
        logger.info("execute_action: '%s' completed for session %s", action, session_id[:8])
        return BrowserActionResponse(success=True, message=message, data=data)

    except HTTPException as ex:
        if browser_session is not None and browser_manager is not None:
            browser_manager.log_activity(
                browser_session,
                actor="user",
                action=action or "unknown",
                params=params,
                success=False,
                error=str(ex.detail),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
        raise
    except Exception as e:
        logger.exception("execute_action: unexpected error: %s", e)
        if browser_session is not None and browser_manager is not None:
            browser_manager.log_activity(
                browser_session,
                actor="user",
                action=action or "unknown",
                params=params,
                success=False,
                error=str(e),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/screenshot",
    response_model=BrowserScreenshotResponse,
    summary="Take a screenshot",
    responses={
        200: {"description": "Screenshot captured"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def take_screenshot(request: Request, session_id: str, req: Optional[BrowserScreenshotRequest] = None):
    """Take a screenshot of the current page."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("take_screenshot: session not found: %s", session_id)
            return BrowserScreenshotResponse(success=False, error="Session not found")

        if browser_session.status != "running":
            logger.error(
                "take_screenshot: session not running: %s (status=%s)",
                session_id, browser_session.status,
            )
            return BrowserScreenshotResponse(
                success=False,
                error=f"Session not running (status: {browser_session.status})",
            )

        runtime = await browser_manager.get_runtime(browser_session)
        screenshot = await runtime.screenshot(
            full_page=req.full_page if req else True,
        )
        browser_session = getattr(runtime, "session", None) or browser_session
        browser_session.mark_active()
        browser_manager.log_activity(
            browser_session,
            actor="user",
            action="screenshot",
            params={"full_page": bool(req.full_page) if req else True},
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        logger.info("take_screenshot: screenshot completed for session %s", session_id[:8])
        return BrowserScreenshotResponse(success=True, screenshot=screenshot)

    except Exception as e:
        logger.exception("take_screenshot: unexpected error: %s", e)
        return BrowserScreenshotResponse(success=False, error=str(e))


@router.get(
    "/{session_id}/source",
    response_model=BrowserSourceResponse,
    summary="Get page source HTML",
    responses={
        200: {"description": "Page source retrieved"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
        500: {"description": "Server error"},
    },
)
async def get_source(request: Request, session_id: str):
    """Get the current page HTML source code."""
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)

        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            logger.error("get_source: session not found: %s", session_id)
            return BrowserSourceResponse(success=False, error="Session not found")

        if browser_session.status != "running":
            logger.error(
                "get_source: session not running: %s (status=%s)",
                session_id, browser_session.status,
            )
            return BrowserSourceResponse(
                success=False,
                error=f"Session not running (status: {browser_session.status})",
            )

        runtime = await browser_manager.get_runtime(browser_session)
        html = await runtime.run("get_content", read_only=True, force=True)
        browser_session = getattr(runtime, "session", None) or browser_session
        url = browser_session.current_url
        browser_session.mark_active()
        browser_manager.log_activity(
            browser_session,
            actor="user",
            action="source",
            url=url,
            title=browser_session.current_title,
            result={"chars": len(html or "")},
        )
        logger.info("get_source: source retrieved for session %s", session_id[:8])
        return BrowserSourceResponse(
            success=True,
            html=html,
            url=url,
            title=browser_session.current_title,
        )

    except Exception as e:
        logger.exception("get_source: unexpected error: %s", e)
        return BrowserSourceResponse(success=False, error=str(e))


@router.get(
    "/{session_id}/activity",
    response_model=BrowserActivityResponse,
    summary="Get the user/AI audit trail",
    responses={
        200: {"description": "Activity entries"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
    },
)
async def get_activity(
    request: Request,
    session_id: str,
    limit: int = 100,
    since: Optional[str] = None,
    actor: Optional[str] = None,
):
    """Return the most recent user and AI actions performed on the session.

    Args:
        limit: Maximum entries (tail-read, so the endpoint is O(limit), not
            O(file size)).
        since: Only entries with ``ts >= since`` (ISO timestamp).
        actor: Filter by ``user``, ``ai`` or ``system``.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)
        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Pick up live VNC input before answering (the watcher is 1 s, but the
        # UI asks right after an action and must see it immediately).
        runtime = browser_manager.peek_runtime(session_id)
        if runtime is not None:
            try:
                await runtime.drain()
            except Exception as ex:
                logger.debug("get_activity drain failed: %s", ex)

        entries = browser_manager.get_activity(session_id, limit=limit)
        if actor:
            entries = [entry for entry in entries if entry.get("actor") == actor]
        if since:
            entries = [entry for entry in entries if (entry.get("ts") or "") >= since]
        return BrowserActivityResponse(
            session_id=session_id,
            count=len(entries),
            activity=entries,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_activity: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{session_id}/cost",
    response_model=BrowserCostResponse,
    summary="Get the AI cost/usage summary",
    responses={
        200: {"description": "Cost summary"},
        401: {"description": "Unauthorized"},
        404: {"description": "Session not found"},
    },
)
async def get_cost(request: Request, session_id: str):
    """
    Return the accumulated AI cost, tokens, LLM calls and tool calls for every
    chat connected to the browser session.

    The numbers come from SmolAgent's token-usage analytics and ``Chat.cost``,
    so they reconcile with the chat header's cost indicator.
    """
    try:
        session = await get_session_context(request)
        browser_manager = _browser_manager(session)
        browser_session = browser_manager.get_session(session_id)
        if not browser_session:
            raise HTTPException(status_code=404, detail="Session not found")
        summary = await asyncio.to_thread(browser_manager.get_cost_summary, session_id)
        return BrowserCostResponse(**summary)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_cost: unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Recording: turn a human co-browsing session into a reusable recipe
# ============================================================================

@router.get(
    "/{session_id}/recording",
    summary="Get the recording state of a session",
)
async def recording_state(request: Request, session_id: str):
    """Return the in-flight/last recording and the recipes for its site."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        state = _recording_store(manager, session_id).load()
        scripts: List[Dict[str, Any]] = []
        store = _script_store(manager)
        site = (state.site if state else "") or normalize_site(browser_session.current_url)
        try:
            scripts = store.search_scripts(site=site, limit=50)
        except Exception as ex:
            logger.debug("Could not list recipes: %s", ex)
        if state is not None:
            state.event_count = len(_recording_events(manager, state))
            if not state.site:
                state.site = site
        return {
            "session_id": session_id,
            "recording": _recording_payload(state, session_id),
            "scripts": scripts,
            "site": site,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("recording_state failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/recording/start",
    summary="Start recording the human's actions",
)
async def recording_start(
    request: Request,
    session_id: str,
    req: Optional[BrowserRecordingStartRequest] = None,
):
    """Start capturing the human's clicks/typing into script steps.

    The recorder itself is already running (it powers the shared activity
    trail); starting a recording just marks a timestamp and stores the metadata
    the save dialog will use.  Calling it again while active updates that
    metadata without losing the events captured so far.
    """
    req = req or BrowserRecordingStartRequest()
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        if browser_session.status != "running":
            raise HTTPException(
                status_code=400,
                detail="Start the browser before recording a flow",
            )

        store = _recording_store(manager, session_id)
        state = store.load()
        script = None
        store_scripts = _script_store(manager)
        script_id = req.script_id or (state.script_id if state else None)
        if script_id:
            script = store_scripts.get_script(script_id)
            if not script:
                raise HTTPException(status_code=404, detail="Script to update not found")

        # Flush the recorder before stamping the start time: events from before
        # "Record" must not leak into the new recipe.
        runtime = await manager.get_runtime(browser_session)
        try:
            await runtime.drain()
        except Exception as ex:
            logger.debug("recording_start drain failed: %s", ex)
        browser_session = getattr(runtime, "session", None) or browser_session
        current_url = browser_session.current_url

        if state is None or not state.active:
            state = RecordingState(
                session_id=session_id,
                active=True,
                started_at=datetime.now(timezone.utc).isoformat(),
                start_url=current_url,
                start_title=browser_session.current_title,
                title=(req.title or (script.title if script else "")),
                description=req.description or (script.description if script else ""),
                site=req.site or (script.site if script else normalize_site(current_url)),
                tags=list(req.tags or (script.tags if script else []) or []),
                script_id=script.id if script else None,
            )
        else:
            if req.title:
                state.title = req.title
            if req.description is not None:
                state.description = req.description
            if req.site:
                state.site = normalize_site(req.site)
            if req.tags is not None:
                state.tags = list(req.tags)
            if script:
                state.script_id = script.id
        state.active = True
        state.stopped_at = None
        state.steps = []
        state.warnings = []
        state.event_count = 0
        if not state.title:
            state.title = suggested_title(state.start_url)
        store.save(state)
        manager.log_activity(
            browser_session,
            actor="system",
            action="record_start",
            params={"title": state.title, "script_id": state.script_id},
            url=current_url,
            title=browser_session.current_title,
        )
        return {
            "recording": _recording_payload(state),
            "scripts": store_scripts.search_scripts(site=state.site, limit=50),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("recording_start failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/recording/stop",
    response_model=BrowserRecordingStopResponse,
    summary="Stop recording and preview the generated steps",
)
async def recording_stop(request: Request, session_id: str):
    """Convert the events captured since ``start`` into script steps."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _recording_store(manager, session_id)
        state = store.load()
        if state is None:
            raise HTTPException(status_code=404, detail="No recording for this session")

        runtime = manager.peek_runtime(session_id)
        if runtime is not None:
            try:
                await runtime.drain()
            except Exception as ex:
                logger.debug("recording_stop drain failed: %s", ex)
            browser_session = getattr(runtime, "session", None) or browser_session

        events = _recording_events(manager, state)
        preview = build_steps(events, start_url=state.start_url)
        state.active = False
        state.stopped_at = datetime.now(timezone.utc).isoformat()
        state.event_count = len(events)
        state.steps = preview["steps"]
        state.warnings = preview["warnings"]
        if not state.title:
            state.title = suggested_title(state.start_url)
        if not state.site:
            state.site = normalize_site(state.start_url or browser_session.current_url)
        store.save(state)
        manager.log_activity(
            browser_session,
            actor="system",
            action="record_stop",
            params={
                "title": state.title,
                "events": len(events),
                "steps": len(preview["steps"]),
            },
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        scripts = _script_store(manager).search_scripts(site=state.site, limit=50)
        return BrowserRecordingStopResponse(
            recording=_recording_payload(state),
            steps=preview["steps"],
            warnings=preview["warnings"],
            variables=preview["variables"],
            event_count=len(events),
            skipped=preview["skipped"],
            truncated=preview["truncated"],
            suggested_title=suggested_title(state.start_url, state.title),
            existing_scripts=scripts,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("recording_stop failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/recording/analyze",
    summary="Let codx-junior review the recording and update the recipe",
)
async def recording_analyze(
    request: Request,
    session_id: str,
    req: Optional[BrowserRecordingAnalyzeRequest] = None,
):
    """Analyze the stopped recording like a Selenium/Playwright trace.

    The deterministic steps are always the baseline.  When an AI model is
    available it reviews the ordered transcript, removes recorder noise, fixes
    selectors it can justify and returns the final steps.  ``save=true`` writes
    them into the same recipe (version bump) or creates a new one; the old body
    stays archived under ``versions/``.
    """
    req = req or BrowserRecordingAnalyzeRequest()
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _recording_store(manager, session_id)
        state = store.load()
        if state is None:
            raise HTTPException(status_code=404, detail="No recording for this session")
        if state.active:
            raise HTTPException(status_code=400, detail="Stop the recording before analyzing it")

        events = _recording_events(manager, state)
        baseline = (
            list(req.steps or [])
            or list(state.steps or [])
            or build_steps(events, start_url=state.start_url)["steps"]
        )
        if not baseline:
            raise HTTPException(
                status_code=400,
                detail="The recording has no replayable actions to analyze",
            )

        script_store = _script_store(manager)
        script_id = req.script_id or state.script_id
        existing = script_store.get_script(script_id) if script_id else None
        # The destination page is useful context for selector cleanup.  It is
        # best effort: a disconnected runtime must not block the review.
        page_context = None
        runtime = manager.peek_runtime(session_id)
        if runtime is not None:
            try:
                page_context = await runtime.snapshot()
            except Exception as ex:
                logger.debug("recording_analyze page context failed: %s", ex)
        analysis = await analyze_recording(
            manager.settings,
            state,
            events,
            baseline,
            state.warnings,
            existing_script=existing,
            use_ai=bool(req.use_ai),
            page_context=page_context,
        )
        steps = analysis.get("steps") or baseline
        title = (
            (existing.title if existing else "")
            or state.title
            or analysis.get("title")
            or suggested_title(state.start_url)
        )
        site = state.site or (existing.site if existing else "") or normalize_site(
            state.start_url or browser_session.current_url
        )
        description = (
            analysis.get("description")
            or state.description
            or (existing.description if existing else "")
        )
        tags = analysis.get("tags") or state.tags or (existing.tags if existing else [])
        previous_version = existing.version if existing else None

        script = None
        if req.save:
            script = script_store.save_script(
                title=title,
                code=steps,
                site=site,
                description=description,
                language="steps",
                tags=tags or [],
                script_id=existing.id if existing else None,
            )

        state.active = False
        state.title = title
        state.site = normalize_site(site)
        state.description = description
        state.tags = list(tags or [])
        state.steps = steps
        state.warnings = list(analysis.get("warnings") or [])
        if script is not None:
            state.script_id = script.id
            state.saved_script_id = script.id
        store.save(state)
        analysis.update({
            "saved": bool(script),
            "version": script.version if script else None,
            "previous_version": previous_version,
        })
        manager.log_activity(
            browser_session,
            actor="system",
            action="record_analyze",
            params={
                "title": title,
                "script_id": script.id if script else script_id,
                "saved": bool(script),
                "used_ai": bool(analysis.get("used_ai")),
                "model": analysis.get("model") or "",
                "events": len(events),
                "steps": len(steps),
            },
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        return {
            "script": _script_summary(script) if script else None,
            "recording": _recording_payload(state),
            "steps": steps,
            "warnings": analysis.get("warnings") or [],
            "variables": [
                name for name in ("value",)
                if "{{value}}" in json.dumps(steps, ensure_ascii=False)
            ],
            "analysis": analysis,
            "existing_scripts": script_store.search_scripts(site=state.site, limit=50),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("recording_analyze failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/recording/save",
    summary="Save the recorded flow as a reusable script",
)
async def recording_save(
    request: Request,
    session_id: str,
    req: BrowserRecordingSaveRequest,
):
    """Create a script, or update the selected one with a new version.

    The client may send edited ``steps`` from the preview; when omitted the
    last stopped recording is used.  Passing ``script_id`` updates that recipe
    in place (the store archives the previous body and bumps the version).
    """
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _recording_store(manager, session_id)
        state = store.load()

        title = (req.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="A title is required")
        steps = req.steps if req.steps is not None else (state.steps if state else [])
        if not steps:
            raise HTTPException(
                status_code=400,
                detail="No steps to save: record some actions and stop the recording first",
            )

        script_id = req.script_id or (state.script_id if state else None)
        site = req.site or (state.site if state else "") or normalize_site(browser_session.current_url)
        description = req.description or (state.description if state else "")
        tags = req.tags if req.tags is not None else (state.tags if state else [])
        script = _script_store(manager).save_script(
            title=title,
            code=steps,
            site=site,
            description=description,
            language="steps",
            tags=tags or [],
            script_id=script_id,
        )

        if state is None:
            state = RecordingState(
                session_id=session_id,
                active=False,
                started_at=datetime.now(timezone.utc).isoformat(),
                start_url=browser_session.current_url,
                title=title,
                description=description,
                site=normalize_site(site),
                tags=list(tags or []),
                script_id=script.id,
            )
        state.active = False
        state.title = title
        state.description = description
        state.site = normalize_site(site)
        state.tags = list(tags or [])
        state.script_id = script_id or script.id
        state.saved_script_id = script.id
        state.steps = steps
        state.event_count = max(state.event_count, len(steps))
        state.stopped_at = state.stopped_at or datetime.now(timezone.utc).isoformat()
        store.save(state)
        manager.log_activity(
            browser_session,
            actor="system",
            action="record_save",
            params={
                "title": script.title,
                "script_id": script.id,
                "version": script.version,
                "steps": len(steps),
                "updated": bool(script_id),
            },
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        return {
            "script": _script_summary(script),
            "recording": _recording_payload(state),
            "scripts": _script_store(manager).search_scripts(site=state.site, limit=50),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("recording_save failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{session_id}/recording",
    summary="Discard the current recording",
)
async def recording_cancel(request: Request, session_id: str):
    """Throw away the in-flight recording (the activity trail is kept)."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _recording_store(manager, session_id)
        state = store.load()
        store.clear()
        manager.log_activity(
            browser_session,
            actor="system",
            action="record_cancel",
            params={"title": state.title if state else ""},
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        return {"cancelled": True, "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("recording_cancel failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Reusable browser scripts (recipes) for a session
# ============================================================================

@router.get(
    "/{session_id}/scripts",
    summary="List the recipes available for a session's site",
)
async def list_session_scripts(
    request: Request,
    session_id: str,
    site: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search the project recipe library, scoped to the session's site."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _script_store(manager)
        resolved_site = site or normalize_site(browser_session.current_url)
        scripts = store.search_scripts(query=query, site=resolved_site, limit=limit)
        return {
            "session_id": session_id,
            "site": resolved_site,
            "count": len(scripts),
            "scripts": scripts,
            "stats": store.stats(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_session_scripts failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{session_id}/scripts/{script_id}",
    summary="Read one recipe with its steps",
)
async def get_session_script(request: Request, session_id: str, script_id: str):
    """Return one script, its parsed steps and a model-readable rendering."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        _require_browser_session(manager, session_id)
        store = _script_store(manager)
        script = store.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")
        return {
            "script": _script_summary(script),
            "markdown": store.render_script(script),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_session_script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{session_id}/scripts/{script_id}",
    summary="Edit a recipe's steps (versioned)",
)
async def update_session_script(
    request: Request,
    session_id: str,
    script_id: str,
    req: BrowserScriptSaveRequest,
):
    """Update an existing recipe in place, archiving the previous body."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _script_store(manager)
        existing = store.get_script(script_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Script not found")
        script = store.save_script(
            title=(req.title or existing.title),
            code=req.steps,
            site=(req.site or existing.site),
            description=(req.description if req.description is not None else existing.description),
            language="steps",
            tags=(req.tags if req.tags is not None else existing.tags),
            script_id=script_id,
        )
        manager.log_activity(
            browser_session,
            actor="system",
            action="script_update",
            params={"script_id": script.id, "version": script.version, "steps": len(req.steps)},
            url=browser_session.current_url,
            title=browser_session.current_title,
        )
        return {"script": _script_summary(script)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("update_session_script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{session_id}/scripts/{script_id}",
    summary="Delete a recipe (soft delete by default)",
)
async def delete_session_script(
    request: Request,
    session_id: str,
    script_id: str,
    hard: bool = False,
):
    """Move a recipe to ``.codx/browser_scripts/.trash`` (or delete it)."""
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        store = _script_store(manager)
        deleted = store.delete_script(script_id, hard=hard)
        if deleted:
            manager.log_activity(
                browser_session,
                actor="system",
                action="script_delete",
                params={"script_id": script_id, "hard": bool(hard)},
                url=browser_session.current_url,
                title=browser_session.current_title,
            )
        return {"deleted": bool(deleted), "script_id": script_id, "hard": bool(hard)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_session_script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{session_id}/scripts/{script_id}/run",
    summary="Run a recipe on the shared browser",
)
async def run_session_script(
    request: Request,
    session_id: str,
    script_id: str,
    req: Optional[BrowserScriptRunRequest] = None,
):
    """Execute the whole recipe in one call on the session's connection.

    The human clicked Run, so ``force`` defaults to true: the run bypasses the
    AI control lease just like a click in the live view would.
    """
    req = req or BrowserScriptRunRequest()
    started_at = time.monotonic()
    browser_session = None
    manager = None
    try:
        session = await get_session_context(request)
        manager = _browser_manager(session)
        browser_session = _require_browser_session(manager, session_id)
        if browser_session.status != "running":
            raise HTTPException(status_code=400, detail="The browser is not running")
        store = _script_store(manager)
        script = store.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")
        steps = parse_steps(script.code) if script.language == "steps" else []
        if not steps:
            raise HTTPException(status_code=400, detail="This script has no executable steps")

        runtime = await manager.get_runtime(browser_session)
        result = await runtime.run_steps(
            steps,
            req.variables or {},
            force=bool(req.force),
        )
        browser_session = getattr(runtime, "session", None) or browser_session
        duration = (time.monotonic() - started_at) * 1000
        if result.get("ok"):
            store.mark_used(script.id)
        manager.log_activity(
            browser_session,
            actor="user",
            action="run_script",
            params={"script_id": script.id, "title": script.title, "steps": len(steps)},
            url=browser_session.current_url,
            title=browser_session.current_title,
            success=bool(result.get("ok")),
            error=result.get("error"),
            duration_ms=duration,
            result={
                "ok": result.get("ok"),
                "failed_step": result.get("failed_step"),
                "ms": result.get("ms"),
            },
        )
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "script": _script_summary(store.get_script(script.id) or script),
            "duration_ms": round(duration, 1),
        }
    except HTTPException as ex:
        if manager is not None and browser_session is not None:
            manager.log_activity(
                browser_session,
                actor="user",
                action="run_script",
                params={"script_id": script_id},
                success=False,
                error=str(ex.detail),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
        raise
    except Exception as e:
        logger.exception("run_session_script failed: %s", e)
        if manager is not None and browser_session is not None:
            manager.log_activity(
                browser_session,
                actor="user",
                action="run_script",
                params={"script_id": script_id},
                success=False,
                error=str(e),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Socket.IO Events (long-running operations with real-time updates)
# ============================================================================

@sio.on("codx-junior-browser-create")
@sio_api_endpoint
async def sio_browser_create(sid: str, data: dict, codxjunior_session: CODXJuniorSession):
    """
    Create a new browser session via Socket.IO, emitting status updates.

    Request data: ``name`` (str, required) · ``chat_id`` (str, optional) ·
    ``share_project`` (bool, optional) · ``share_folders`` (list, optional) ·
    ``downloads_folder`` (str, optional) · ``codx_path`` (str, injected by the client).
    Emits: ``codx-junior-browser-status`` {status, session_id, message, error, session}.
    """
    channel = SessionChannel(sio=sio, sid=sid)
    chat_id = data.get("chat_id")
    name = data.get("name")
    initial_url = data.get("initial_url")
    description = data.get("description")
    share_project = data.get("share_project")
    share_folders = data.get("share_folders")
    readonly = data.get("readonly")
    downloads_folder = data.get("downloads_folder")
    downloads_mount = data.get("downloads_mount")
    kind = data.get("kind") or "browser"
    os_version = data.get("os_version")
    image = data.get("image")
    image_provider = data.get("image_provider")
    library_item_id = data.get("library_item_id")
    control = data.get("control")
    vnc_host = data.get("vnc_host")
    vnc_port = data.get("vnc_port")
    vnc_password = data.get("vnc_password")
    ssh_host = data.get("ssh_host")
    ssh_port = data.get("ssh_port")
    ssh_user = data.get("ssh_user")
    ssh_key_path = data.get("ssh_key_path")
    ssh_password = data.get("ssh_password")
    guest_share_path = data.get("guest_share_path")
    cpu_cores = data.get("cpu_cores")
    ram_size = data.get("ram_size")
    disk_size = data.get("disk_size")
    shm_size = data.get("shm_size")
    extra_env = data.get("extra_env")
    desktop_width = data.get("desktop_width")
    desktop_height = data.get("desktop_height")
    control_mode = data.get("control_mode")

    if not codxjunior_session:
        _emit(channel, {"status": "error", "message": "Missing codx_path", "error": "Missing codx_path"})
        return
    if not name:
        _emit(channel, {"status": "error", "message": "name is required", "error": "Missing name"})
        return

    try:
        chat = None
        if chat_id:
            chat_manager = ChatManager(codxjunior_session.settings)
            chat = chat_manager.find_by_id(chat_id)
            if not chat:
                _emit(channel, {"status": "error", "message": "Chat not found", "error": f"Chat {chat_id} not found"})
                return

        _emit(channel, {"status": "starting", "message": "Creating browser session...", "name": name, "chat_id": chat_id})

        browser_manager = codxjunior_session.get_browser_manager()
        browser_session = browser_manager.create_session(
            name=name,
            chat_id=chat_id,
            initial_url=initial_url,
            description=description,
            share_project=share_project,
            share_folders=share_folders,
            readonly=readonly,
            downloads_folder=downloads_folder,
            downloads_mount=downloads_mount,
            kind=kind,
            os_version=os_version,
            image=image,
            image_provider=image_provider,
            library_item_id=library_item_id,
            control=control,
            vnc_host=vnc_host,
            vnc_port=vnc_port,
            vnc_password=vnc_password,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_key_path=ssh_key_path,
            ssh_password=ssh_password,
            guest_share_path=guest_share_path,
            cpu_cores=cpu_cores,
            ram_size=ram_size,
            disk_size=disk_size,
            shm_size=shm_size,
            extra_env=extra_env,
            desktop_width=desktop_width,
            desktop_height=desktop_height,
            control_mode=control_mode,
        )

        _emit(channel, {
            "status": "created",
            "session_id": browser_session.id,
            "name": browser_session.name,
            "message": f"Browser session '{browser_session.name}' ({browser_session.id[:8]}) created",
            "session": browser_session.to_public_dict(),
        })

        if chat:
            browser_manager.save_session_to_chat(browser_session, chat)
            await asyncio.to_thread(
                ChatManager(codxjunior_session.settings).save_chat, chat
            )

        _emit(channel, {
            "status": "ready",
            "session_id": browser_session.id,
            "name": browser_session.name,
            "message": "Browser session ready",
            "session": browser_session.to_public_dict(),
        })
        logger.info("sio_browser_create: created session %s for chat %s", browser_session.id[:8], chat_id)

    except Exception as e:
        logger.exception("sio_browser_create: unexpected error: %s", e)
        _emit(channel, {"status": "error", "message": "Failed to create browser session", "error": str(e)})


@sio.on("codx-junior-browser-delete")
@sio_api_endpoint
def sio_browser_delete(sid: str, data: dict, codxjunior_session: CODXJuniorSession):
    """
    Delete a browser session via Socket.IO, emitting status updates.

    Request data: ``session_id`` (str).
    Emits: ``codx-junior-browser-status``.
    """
    channel = SessionChannel(sio=sio, sid=sid)
    session_id = data.get("session_id")

    if not codxjunior_session:
        _emit(channel, {"status": "error", "message": "Missing codx_path", "error": "Missing codx_path"})
        return
    if not session_id:
        _emit(channel, {"status": "error", "message": "session_id is required", "error": "Missing session_id"})
        return

    try:
        browser_manager = codxjunior_session.get_browser_manager()

        _emit(channel, {"status": "stopping", "session_id": session_id, "message": "Stopping browser session..."})

        if not browser_manager.get_session(session_id):
            _emit(channel, {"status": "error", "session_id": session_id, "message": "Session not found", "error": f"Session {session_id} not found"})
            return

        browser_manager.delete_session(session_id)
        _emit(channel, {"status": "deleted", "session_id": session_id, "message": f"Browser session {session_id[:8]} deleted"})
        logger.info("sio_browser_delete: deleted session %s", session_id[:8])

    except Exception as e:
        logger.exception("sio_browser_delete: unexpected error: %s", e)
        _emit(channel, {"status": "error", "session_id": session_id, "message": "Failed to delete browser session", "error": str(e)})


@sio.on("codx-junior-browser-restart")
@sio_api_endpoint
async def sio_browser_restart(sid: str, data: dict, codxjunior_session: CODXJuniorSession):
    """
    Restart a browser session via Socket.IO, emitting status updates.

    Request data: ``session_id`` (str).
    Emits: ``codx-junior-browser-status``.
    """
    channel = SessionChannel(sio=sio, sid=sid)
    session_id = data.get("session_id")

    if not codxjunior_session:
        _emit(channel, {"status": "error", "message": "Missing codx_path", "error": "Missing codx_path"})
        return
    if not session_id:
        _emit(channel, {"status": "error", "message": "session_id is required", "error": "Missing session_id"})
        return

    try:
        browser_manager = codxjunior_session.get_browser_manager()

        _emit(channel, {"status": "restarting", "session_id": session_id, "message": "Restarting browser session..."})

        browser_session = await browser_manager.restart_session(session_id)
        if not browser_session:
            _emit(channel, {"status": "error", "session_id": session_id, "message": "Session not found", "error": f"Session {session_id} not found"})
            return

        _emit(channel, {
            "status": "ready",
            "session_id": session_id,
            "name": browser_session.name,
            "message": f"Browser session {session_id[:8]} restarted",
            "session": browser_session.to_public_dict(),
        })
        logger.info("sio_browser_restart: restarted session %s", session_id[:8])

    except Exception as e:
        logger.exception("sio_browser_restart: unexpected error: %s", e)
        _emit(channel, {"status": "error", "session_id": session_id, "message": "Failed to restart browser session", "error": str(e)})


@sio.on("codx-junior-browser-stop")
@sio_api_endpoint
def sio_browser_stop(sid: str, data: dict, codxjunior_session: CODXJuniorSession):
    """
    Stop a browser session (without deleting) via Socket.IO, emitting status updates.

    Request data: ``session_id`` (str).
    Emits: ``codx-junior-browser-status``.
    """
    channel = SessionChannel(sio=sio, sid=sid)
    session_id = data.get("session_id")

    if not codxjunior_session:
        _emit(channel, {"status": "error", "message": "Missing codx_path", "error": "Missing codx_path"})
        return
    if not session_id:
        _emit(channel, {"status": "error", "message": "session_id is required", "error": "Missing session_id"})
        return

    try:
        browser_manager = codxjunior_session.get_browser_manager()

        _emit(channel, {"status": "stopping", "session_id": session_id, "message": "Stopping browser session..."})

        browser_manager.forget_runtime(session_id)
        browser_session = browser_manager.stop_session(session_id)
        if not browser_session:
            _emit(channel, {"status": "error", "session_id": session_id, "message": "Session not found", "error": f"Session {session_id} not found"})
            return

        _emit(channel, {"status": "stopped", "session_id": session_id, "message": f"Browser session {session_id[:8]} stopped"})
        logger.info("sio_browser_stop: stopped session %s", session_id[:8])

    except Exception as e:
        logger.exception("sio_browser_stop: unexpected error: %s", e)
        _emit(channel, {"status": "error", "session_id": session_id, "message": "Failed to stop browser session", "error": str(e)})


# Made with ❤️ by codx-junior