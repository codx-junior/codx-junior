"""
REST surface of the codx-junior harness.

Exposes the observability and audit layer of the native harness:

* ``GET /api/harness/audit``    — the append-only audit journal of the project
  (every registered request, run, step and completion, newest first).
* ``GET /api/harness/status``   — harness health: orchestration worker presence,
  active runs and the watchdog state for the project.
* ``GET/POST /api/harness/sessions`` and
  ``GET /api/harness/sessions/{id}`` / ``POST .../{id}/prompt`` — harness mode:
  create/list/inspect the DSH-like sessions (normal task chats) and schedule
  their turns through the same chat engine every other task uses.

The audit/status endpoints are read-only: the journal is written by the
orchestration runtime, the chat engine and the watchdog, never through HTTP.
The session endpoints persist a task chat and schedule a turn; they do not
implement a second agent loop.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Harness mode payloads
# ---------------------------------------------------------------------------


class HarnessSessionCreateRequest(BaseModel):
    """Body of ``POST /api/harness/sessions``: the ticket to start."""

    prompt: str = Field(description="The request/ticket the harness must resolve.")
    name: Optional[str] = Field(default=None, description="Optional session title.")
    profiles: List[str] = Field(
        default_factory=list,
        description="Extra profiles; the codx-junior harness profile is always added.",
    )
    model: Optional[str] = Field(default=None, description="Optional LLM model.")
    budget_cxjcoins: float = Field(
        default=0.0, description="Optional chat budget in cxjcoins (0 = no limit)."
    )
    attachments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Files/images pasted or uploaded in the composer. They are stored on "
            "the first user message so the turn sees them like a chat attachment."
        ),
    )
    start: bool = Field(
        default=True,
        description=(
            "Start the first turn in the background. The turn uses the same "
            "engine/socket events as a chat; the HTTP response does not wait."
        ),
    )


class HarnessSessionPromptRequest(BaseModel):
    """Body of ``POST /api/harness/sessions/{id}/prompt``: a follow-up."""

    content: str = Field(description="Follow-up message.")
    profiles: List[str] = Field(default_factory=list)
    model: Optional[str] = Field(default=None)
    attachments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Files/images attached to this follow-up message.",
    )
    start: bool = Field(default=True, description="Start the turn in the background.")


def _session(request: Request):
    """Return the session injected by the codx_path middleware."""
    session = getattr(request.state, "codx_junior_session", None)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="codx_path is required (query string or x-codx-path header)",
        )
    return session


@router.get("/harness/audit")
def harness_audit(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    event: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    chat_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """
    Read the harness audit journal of the project (newest first).

    The journal registers every piece of harness work: ``task_requested``,
    run lifecycle events (``run_created`` ... ``run_completed``), step
    dispatches and finishes, notifications and worker supervision
    (``worker_stalled`` / ``worker_restarted`` / ``worker_recovered``).

    ``run_id`` and ``chat_id`` scope the journal to one harness task, which is
    what the diagnostic view uses to explain why a task is where it is.
    """
    from codx.junior.harness.audit import HarnessAuditLog, audit_path

    session = _session(request)
    codx_path = str(getattr(session.settings, "codx_path", "") or "")
    journal = HarnessAuditLog(codx_path)
    return {
        "codx_path": codx_path,
        "exists": journal.exists(),
        "path": audit_path(codx_path),
        "total": None,
        "entries": journal.read(
            limit=limit,
            offset=offset,
            event=event,
            run_id=run_id,
            chat_id=chat_id,
        ),
    }


@router.get("/harness/status")
def harness_status(request: Request) -> Dict[str, Any]:
    """
    Harness health of the project: worker presence, active runs and watchdog.
    """
    session = _session(request)
    codx_path = str(getattr(session.settings, "codx_path", "") or "")

    worker: Dict[str, Any] = {}
    active_runs: List[str] = []
    runtime_path: Optional[str] = None
    heartbeat_path: Optional[str] = None
    try:
        from codx.junior.orchestration.store import RunStore

        store = RunStore(codx_path)
        worker = store.worker_status()
        active_runs = store.active_run_ids()
        # The API and the background worker are separate processes/containers:
        # exposing both paths makes a runtime-folder mismatch (the cause of a
        # permanent "worker offline") directly visible in the harness view.
        runtime_path = store.runtime_dir
        heartbeat_path = store.worker_path()
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("Harness status: could not read worker state: %s", ex)

    watchdog: Dict[str, Any] = {}
    try:
        from codx.junior.harness.watchdog import (
            get_harness_watchdog,
            watchdog_enabled,
        )

        instance = get_harness_watchdog()
        watchdog = (instance.status if instance is not None else {}) or {}
        watchdog["enabled"] = watchdog_enabled()
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("Harness status: could not read watchdog state: %s", ex)

    return {
        "codx_path": codx_path,
        "runtime_path": runtime_path,
        "heartbeat_path": heartbeat_path,
        "worker": worker,
        "worker_running": bool(worker.get("present")),
        "active_runs": active_runs,
        "active_run_count": len(active_runs),
        "watchdog": watchdog,
    }


# ---------------------------------------------------------------------------
# Harness mode: sessions
#
# A session IS a task chat (one copy of the conversation, tool events and
# cost), so these routes only compose existing pieces:
#
# * the chat is created/read through ChatManager,
# * the closed-loop contract lives in codx.junior.harness.prompts,
# * the worktree/diff is read through TaskWorkspaceManager + git.
#
# Starting the AI turn reuses the SAME session call the `codx-junior-chat`
# socket handler uses (`CODXJuniorSession.chat_with_project`), scheduled in the
# background: streaming, cancellation, budgets and crash recovery are shared
# with every other task instead of being reimplemented here.
# ---------------------------------------------------------------------------


#: Background turns started by the harness API. Keeping a reference prevents
#: asyncio from garbage-collecting a task that is still running.
_SESSION_TASKS: set = set()


def _schedule_session_turn(request: Request, session_id: str) -> bool:
    """
    Start the first/next turn of a harness session in the background.

    The turn is executed by :meth:`HarnessSessionService.run_turn`, which uses
    the same ``ChatEngine.chat_with_project`` the ``codx-junior-chat`` socket
    handler reaches (in the task worktree when isolation is available), so
    streaming, cancellation, crash recovery and the chat events are identical.
    The HTTP request returns immediately; the client follows the chat socket /
    harness session endpoint.

    Args:
        request: The FastAPI request (it owns the codx-junior session).
        session_id: Chat id of the harness session.

    Returns:
        True when the turn was scheduled.
    """
    chat_session = _session(request)
    try:
        from codx.junior.harness.sessions import HarnessSessionService

        service = HarnessSessionService(
            settings=chat_session.settings,
            chat_manager=chat_session.get_chat_manager(),
            user=getattr(getattr(chat_session, "user", None), "username", "") or "harness",
        )
        task = asyncio.create_task(
            service.run_turn(session_id, chat_session),
            name=f"harness-session-{session_id}",
        )
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("Harness session: could not start turn %s: %s", session_id, ex)
        return False

    _SESSION_TASKS.add(task)

    def _done(completed: Any) -> None:
        _SESSION_TASKS.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:  # pragma: no cover - normal stop
            logger.info("Harness session turn %s was cancelled", session_id)
        except Exception:  # pragma: no cover - logged, never crashes the API
            logger.exception("Harness session turn %s failed", session_id)

    task.add_done_callback(_done)
    return True


def _harness_session_service(request: Request):
    """Build a HarnessSessionService for the request project."""
    from codx.junior.harness.sessions import HarnessSessionService

    session = _session(request)
    user = getattr(session, "user", None)
    return HarnessSessionService(
        settings=session.settings,
        chat_manager=session.get_chat_manager(),
        user=getattr(user, "username", "") or "harness",
    )


@router.get("/harness/sessions")
def harness_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    phase: Optional[str] = Query(
        default=None, description="Only sessions in this phase (running/done/...)"
    ),
) -> Dict[str, Any]:
    """
    List the harness sessions of the project (newest first).

    A harness session is a task chat executed by the ``codx-junior`` profile in
    ``task`` mode. The response is already the shape the sidebar renders:
    title, phase, timings, cost, preview and run id.
    """
    service = _harness_session_service(request)
    sessions = service.list(limit=limit, phase=phase)
    return {
        "codx_path": service.codx_path,
        "project_id": service.project_id,
        "count": len(sessions),
        "sessions": sessions,
    }


@router.post("/harness/sessions", status_code=201)
async def harness_session_create(
    request: Request, payload: HarnessSessionCreateRequest
) -> Dict[str, Any]:
    """
    Create a harness session from a ticket.

    The chat lands on the project's ``Harness`` board (``Working`` column) with
    the harness contract appended to the first message. The first turn is
    scheduled in the background (``start=false`` disables it) with the same call
    the chat socket uses, so the session starts even if the browser closes.
    """
    from codx.junior.harness.sessions import HarnessSessionError

    service = _harness_session_service(request)
    try:
        summary = service.create(
            payload.prompt,
            name=payload.name,
            profiles=payload.profiles,
            model=payload.model,
            budget_cxjcoins=payload.budget_cxjcoins,
            attachments=payload.attachments,
        )
        summary["started"] = bool(payload.start and _schedule_session_turn(request, summary["session_id"]))
        return summary
    except HarnessSessionError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Harness session create failed: %s", ex)
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/harness/sessions/{session_id}")
def harness_session_detail(request: Request, session_id: str) -> Dict[str, Any]:
    """
    Return one harness session: chat, metrics, worktree, changed files, run.

    Every computed block degrades to an empty value, so a project without git
    or a session that did not write files still opens the conversation.
    """
    from codx.junior.harness.sessions import HarnessSessionError

    service = _harness_session_service(request)
    try:
        return service.get(session_id)
    except HarnessSessionError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Harness session detail failed: %s", ex)
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/harness/sessions/{session_id}/prompt")
async def harness_session_prompt(
    request: Request, session_id: str, payload: HarnessSessionPromptRequest
) -> Dict[str, Any]:
    """
    Append a follow-up message to a harness session.

    The message is persisted and the next turn is scheduled in the background
    (``start=false`` disables it); the response is the updated session detail.
    """
    from codx.junior.harness.sessions import HarnessSessionError

    service = _harness_session_service(request)
    try:
        detail = service.prompt(
            session_id,
            payload.content,
            profiles=payload.profiles,
            model=payload.model,
            attachments=payload.attachments,
        )
        detail["started"] = bool(
            payload.start and _schedule_session_turn(request, session_id)
        )
        return detail
    except HarnessSessionError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Harness session prompt failed: %s", ex)
        raise HTTPException(status_code=500, detail=str(ex)) from ex
