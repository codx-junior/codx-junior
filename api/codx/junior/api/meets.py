"""
Meets API router – REST endpoints for meetings and WebRTC calls.

The :class:`~codx.junior.meets.meet_manager.MeetManager` keeps the live
registry, while :class:`~codx.junior.meets.service.MeetingService` owns the
meeting artifacts (chat, transcript, profiles, notes/minutes, recording) and
the profile turn orchestration.

Route layout
------------
POST   /api/meets/initiate                       – create a meeting
POST   /api/meets/join                           – join an existing meeting
POST   /api/meets/leave                          – leave a meeting
POST   /api/meets/end                            – end a meeting (+ minutes)
GET    /api/meets                                – list live meetings
GET    /api/meets/history                        – persisted meetings of the project
GET    /api/meets/{meet_id}                      – meeting details/status
POST   /api/meets/{meet_id}/chat                 – create/load the meeting chat
POST   /api/meets/{meet_id}/participants         – invite users / add profiles
DELETE /api/meets/{meet_id}/participants/{name}  – remove an invited participant
POST   /api/meets/{meet_id}/invite               – alias of participants
POST   /api/meets/{meet_id}/utterance            – record a spoken/typed utterance
POST   /api/meets/{meet_id}/floor                – speech start/end (barge-in)
GET    /api/meets/{meet_id}/transcript           – meeting transcript
GET    /api/meets/{meet_id}/notes                – notes / minutes
POST   /api/meets/{meet_id}/notes                – generate notes / minutes
POST   /api/meets/{meet_id}/minutes              – alias of notes
POST   /api/meets/{meet_id}/tasks                – create/run/schedule tasks
POST   /api/meets/{meet_id}/recording            – upload the call recording
GET    /api/meets/{meet_id}/recording            – download/play the recording
POST   /api/meets/{meet_id}/speech               – profile voice (TTS)
POST   /api/meets/{meet_id}/settings             – meeting behaviour
POST   /api/meets/{meet_id}/browser              – share a browser session
DELETE /api/meets/{meet_id}/browser              – stop sharing the browser session
POST   /api/meets/{meet_id}/signal/offer         – relay an SDP offer
POST   /api/meets/{meet_id}/signal/answer        – relay an SDP answer
POST   /api/meets/{meet_id}/signal/ice-candidate – relay an ICE candidate

Diagram:
```mermaid
flowchart TD
    C[Client] -->|POST /api/meets/initiate| A[MeetingService + MeetManager]
    C -->|POST .../utterance| T[Transcript + chat]
    T --> O[Profile orchestrator]
    O -->|socket profile-turn| C
    C -->|POST .../floor| F[Barge-in: profiles yield]
    A --> S[(.codx/meetings)]
    A --> CH[(meeting chat)]
    C -->|POST .../notes| M[Minutes]
    M -->|POST .../tasks| Task[TaskManager]
```
"""

from __future__ import annotations

import logging
import os
import time

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from codx.junior.meets.meet_manager import get_meet_manager
from codx.junior.meets.model import (
    MeetAnswerRequest,
    MeetBrowserRequest,
    MeetChatRequest,
    MeetEndRequest,
    MeetFloorRequest,
    MeetIceCandidateRequest,
    MeetInitiateRequest,
    MeetJoinRequest,
    MeetLeaveRequest,
    MeetNotesRequest,
    MeetOfferRequest,
    MeetParticipantsRequest,
    MeetSettings,
    MeetSignalType,
    MeetSpeechRequest,
    MeetTasksRequest,
    MeetUtteranceRequest,
    ParticipantKind,
    ParticipantStatus,
)
from codx.junior.meets.profiles import participant_snapshot
from codx.junior.meets.service import MeetingService, event_payload
from codx.junior.meets.store import MeetStoreError
from codx.junior.security.user_management import get_optional_authenticated_user
from codx.junior.voice.speech import SpeechSynthesizer, SpeechUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meets", tags=["meets"])

#: Maximum size of an uploaded meeting recording (200 MB).
MAX_RECORDING_BYTES = 200 * 1024 * 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_identity(request: Request) -> tuple:
    """
    Resolve the calling participant identity from the request.

    Prefers the authenticated user (``authentication`` header) and falls back
    to the ``x-username`` header or ``"guest"``. The Socket.IO session
    (``x-sid``) is used to target signalling messages.

    Returns:
        Tuple of ``(username, sid, avatar)``.
    """
    user = get_optional_authenticated_user(request)
    username = (
        getattr(user, "username", None)
        or request.headers.get("x-username")
        or "guest"
    )
    sid = request.headers.get("x-sid")
    avatar = getattr(user, "avatar", "") or ""
    return username, sid, avatar


def _resolve_project_id(request: Request) -> Optional[str]:
    """Return the active project id from the request session, if available."""
    session = getattr(request.state, "codx_junior_session", None)
    settings = getattr(session, "settings", None)
    return getattr(settings, "project_id", None)


def _session(request: Request):
    """Return the request project session (may be ``None`` in tests)."""
    return getattr(request.state, "codx_junior_session", None)


def get_meeting_service(request: Request) -> MeetingService:
    """Dependency: the meeting service bound to the request project."""
    return MeetingService(_session(request), emit=_emit)


async def _emit(event: str, data: dict) -> None:
    """
    Emit a Socket.IO event to connected clients.

    Emission is best-effort: failures (e.g. when running in background mode)
    are logged but never break the HTTP response.
    """
    try:
        from codx.junior.sio.sio import sio_send_event

        await sio_send_event(event, data)
    except Exception as ex:
        logger.warning("Failed to emit socket.io event '%s': %s", event, ex)


def _service(request: Request) -> MeetingService:
    """Build the service for this request (kept explicit for tests)."""
    return MeetingService(_session(request), emit=_emit)


def _resolved_profiles(request: Request, values) -> list:
    """Resolve profile names/objects to participant snapshots."""
    session = _session(request)
    resolved = []
    for value in values or []:
        name = (
            value.get("profile_name")
            or value.get("profile")
            or value.get("name")
            or value.get("username")
            if isinstance(value, dict)
            else value
        )
        name = str(name or "").strip()
        if not name:
            continue
        snapshot = participant_snapshot(session, name) if session is not None else None
        resolved.append(snapshot or name)
    return resolved


def _meet_payload(service: MeetingService, meet, chat=None) -> dict:
    """Serialize a meeting for this API (includes the dashboard snapshot)."""
    try:
        return service.serialize(meet, chat=chat)
    except Exception:  # noqa: BLE001 - a broken snapshot must not break the call
        logger.debug("Could not serialize the meeting", exc_info=True)
        return event_payload(meet)


def _raise_for_meet_error(ex: Exception) -> None:
    """Translate a domain error into an HTTP error."""
    if isinstance(ex, KeyError):
        raise HTTPException(status_code=404, detail=f"Meet '{ex.args[0]}' not found")
    if isinstance(ex, MeetStoreError):
        raise HTTPException(status_code=500, detail=str(ex))
    if isinstance(ex, ValueError):
        raise HTTPException(status_code=400, detail=str(ex))
    raise HTTPException(status_code=500, detail=str(ex))


async def _relay_signal(
    meet_id: str,
    signal_type: str,
    data: Any,
    to: Optional[str],
    request: Request,
) -> dict:
    """Store a signalling message and broadcast it over Socket.IO."""
    manager = get_meet_manager()
    username, sid, _ = _resolve_identity(request)

    try:
        manager.add_signal(
            meet_id=meet_id,
            signal_type=signal_type,
            sender=username,
            to=to or "",
            data=data,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")

    await _emit(
        "codx-junior-meet-signal",
        {
            "meetId": meet_id,
            "type": signal_type,
            "from": username,
            "fromSid": sid,
            "to": to or "",
            "signal": data,
        },
    )
    return {"success": True, "meetId": meet_id, "type": signal_type, "to": to or ""}


# ---------------------------------------------------------------------------
# Lifecycle endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_meets(request: Request, active: bool = True):
    """Return the list of live meetings (active only by default)."""
    manager = get_meet_manager()
    meets = manager.list_meets(active_only=active)
    service = _service(request)
    return {
        "success": True,
        "count": len(meets),
        "meets": [_meet_payload(service, meet) for meet in meets],
    }


@router.get("/history")
async def list_meeting_history(request: Request, limit: int = 30):
    """Return the persisted meetings of the project, newest first."""
    try:
        return {"success": True, "meetings": _service(request).history(limit=limit)}
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)


@router.get("/{meet_id}")
async def get_meet_info(meet_id: str, request: Request):
    """Return details and status for a single meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.post("/initiate")
async def initiate_meet(payload: MeetInitiateRequest, request: Request):
    """Create (or reactivate) a meeting and register the initiator."""
    manager = get_meet_manager()
    username, sid, avatar = _resolve_identity(request)
    service = _service(request)

    try:
        meet = manager.initiate_meet(
            meet_id=payload.meetId,
            name=payload.name,
            initiator=username,
            sid=sid,
            avatar=avatar,
            participants=payload.participants,
            project_id=_resolve_project_id(request),
            profiles=_resolved_profiles(request, payload.profiles),
            invited_users=payload.invitedUsers,
            agenda=payload.agenda,
            board=payload.board,
            task_ids=payload.taskIds,
            channel_id=payload.channelId,
            channel_name=payload.channelName,
            team_id=payload.teamId,
            source=payload.source or "manual",
            settings=payload.settings,
        )
        chat = None
        if payload.createChat:
            chat = await service.start_meeting(meet)
        else:
            service.persist(meet)
            await service.emit_meet("started", meet)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        logger.exception("Could not initiate the meeting: %s", ex)
        _raise_for_meet_error(ex)

    meeting_payload = _meet_payload(service, meet, chat=chat)
    await _emit(
        "codx-junior-meet",
        {
            "type": "initiated",
            "meetId": meet.meet_id,
            "from": username,
            # The session id lets the other participants key the WebRTC peer
            # connection by *device*, not only by username.
            "fromSid": sid,
            "meet": meeting_payload,
            "chatId": meet.chat_id,
            "chat": meeting_payload.get("chat"),
        },
    )
    return {"success": True, "meet": meeting_payload}


@router.post("/join")
async def join_meet(payload: MeetJoinRequest, request: Request):
    """Add the caller as a participant of an active meeting."""
    manager = get_meet_manager()
    meet_id = payload.meetId
    if not meet_id:
        raise HTTPException(status_code=400, detail="Missing 'meetId' in request body")

    username, sid, avatar = _resolve_identity(request)
    service = _service(request)
    try:
        meet = manager.join_meet(
            meet_id,
            username=username,
            sid=sid,
            avatar=avatar,
            project_id=_resolve_project_id(request),
        )
        try:
            service._bridge(meet).sync_chat()
        except Exception:  # noqa: BLE001
            logger.debug("Could not sync the meeting chat", exc_info=True)
        service.persist(meet)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))

    await _emit(
        "codx-junior-meet",
        {
            "type": "joined",
            "meetId": meet.meet_id,
            "from": username,
            # Existing peers offer a connection keyed by `username::fromSid`;
            # without the session id the answer was looked up under a key that
            # did not exist and the call stayed silent.
            "fromSid": sid,
            "participants": meet.participant_usernames(),
            "profileParticipants": meet.profile_participants(),
            "chatId": meet.chat_id,
            # Sent so a newcomer starts in the session the room is already in.
            "sharedBrowser": meet.shared_browser,
        },
    )
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.post("/leave")
async def leave_meet(payload: MeetLeaveRequest, request: Request):
    """Remove the caller from a meeting."""
    manager = get_meet_manager()
    meet_id = payload.meetId
    if not meet_id:
        raise HTTPException(status_code=400, detail="Missing 'meetId' in request body")

    username, sid, _ = _resolve_identity(request)
    service = _service(request)
    try:
        meet = manager.leave_meet(meet_id, username=username, sid=sid)
        try:
            service._bridge(meet).sync_chat()
        except Exception:  # noqa: BLE001
            pass
        service.persist(meet)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")

    await _emit(
        "codx-junior-meet",
        {
            "type": "left",
            "meetId": meet.meet_id,
            "from": username,
            "fromSid": sid,
            "participants": meet.participant_usernames(),
            "profileParticipants": meet.profile_participants(),
        },
    )
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.post("/end")
async def end_meet(payload: MeetEndRequest, request: Request):
    """End a meeting (typically called by the initiator).

    The minutes are generated in the background when the meeting setting says
    so; the ``minutes`` socket event delivers the result to the summary page.
    """
    meet_id = payload.meetId
    if not meet_id:
        raise HTTPException(status_code=400, detail="Missing 'meetId' in request body")

    username, sid, _ = _resolve_identity(request)
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        meet = await service.end_meeting(
            meet, generate_minutes=payload.generateMinutes
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        logger.exception("Could not end the meeting: %s", ex)
        _raise_for_meet_error(ex)

    await _emit(
        "codx-junior-meet",
        {
            "type": "ended",
            "meetId": meet.meet_id,
            "from": username,
            "fromSid": sid,
            "meet": event_payload(meet, compact=True),
        },
    )
    return {"success": True, "meet": _meet_payload(service, meet)}


# ---------------------------------------------------------------------------
# Meeting artifacts
# ---------------------------------------------------------------------------

@router.post("/{meet_id}/chat")
async def meeting_chat(
    meet_id: str,
    payload: Optional[MeetChatRequest] = None,
    request: Request = None,
):
    """Create/load the chat associated with the meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        chat = service.ensure_chat(meet, name=(payload.name if payload else "") or "")
        if chat is None:
            raise HTTPException(
                status_code=400,
                detail="The meeting has no project chat (no project session)",
            )
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {
        "success": True,
        "chatId": meet.chat_id,
        "chat": service._bridge(meet).serialize_chat(chat),
        "meet": _meet_payload(service, meet, chat=chat),
    }


@router.post("/{meet_id}/participants")
async def add_meeting_participants(
    meet_id: str,
    payload: MeetParticipantsRequest,
    request: Request,
):
    """Invite human users and add AI profiles to a running meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        if not meet.is_active:
            raise HTTPException(status_code=400, detail="The meeting already ended")
        meet = await service.add_participants(
            meet,
            users=payload.users,
            profiles=payload.profiles,
            notify=payload.notify,
        )
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.post("/{meet_id}/invite")
async def invite_meeting_participants(
    meet_id: str,
    payload: MeetParticipantsRequest,
    request: Request,
):
    """Alias of ``POST /participants`` (the UI uses the word invite)."""
    return await add_meeting_participants(meet_id, payload, request)


@router.delete("/{meet_id}/participants/{name}")
async def remove_meeting_participant(
    meet_id: str,
    name: str,
    request: Request,
    kind: str = "user",
):
    """Remove an invited participant or an AI profile from the meeting."""
    service = _service(request)
    participant_kind = (
        ParticipantKind.PROFILE
        if str(kind or "").lower() == "profile"
        else ParticipantKind.USER
    )
    try:
        meet = service.require_meet(meet_id)
        meet = service.manager.remove_participant(
            meet_id, username=name, kind=participant_kind
        )
        service.persist(meet)
        await service.emit_meet(
            "participants",
            meet,
            removed=[name],
            removedKind=participant_kind.value,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.post("/{meet_id}/utterance")
async def record_meeting_utterance(
    meet_id: str,
    payload: MeetUtteranceRequest,
    request: Request,
):
    """
    Record one spoken/typed utterance.

    This is the entry point of the browser speech recognition / MediaRecorder
    fallback and of the "type in the meeting" box. When the user uses the
    normal meeting chat, the Socket.IO handler routes the persisted message to
    the same service (:meth:`MeetingService.handle_chat_message`).
    """
    username, _sid, _avatar = _resolve_identity(request)
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        if not meet.is_active:
            raise HTTPException(status_code=400, detail="The meeting already ended")
        speaker = (
            payload.speaker
            if payload.kind == ParticipantKind.PROFILE and payload.speaker
            else username
        )
        entry = await service.record_utterance(
            meet,
            text=payload.text,
            speaker=speaker or username,
            kind=payload.kind,
            source=payload.source or "speech",
            client_message_id=payload.clientMessageId,
            auto_respond=payload.autoRespond,
        )
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {
        "success": True,
        "entry": entry.model_dump(mode="json") if entry else None,
        "meet": _meet_payload(service, meet),
    }


@router.post("/{meet_id}/floor")
async def meeting_floor(
    meet_id: str,
    payload: MeetFloorRequest,
    request: Request,
):
    """
    Announce that a participant started or stopped speaking.

    A human ``start`` immediately interrupts the profile that is talking, so a
    profile never talks over a person.
    """
    username, _sid, _avatar = _resolve_identity(request)
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        speaker = payload.speaker or username
        meet = await service.handle_floor(
            meet,
            event=payload.event,
            kind=payload.kind,
            speaker=speaker,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {"success": True, "floor": meet.floor.model_dump(mode="json")}


@router.get("/{meet_id}/transcript")
async def meeting_transcript(meet_id: str, request: Request):
    """Return the transcript of a meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {
        "success": True,
        "meetId": meet.meet_id,
        "transcript": [entry.model_dump(mode="json") for entry in meet.transcript],
    }


@router.get("/{meet_id}/notes")
async def meeting_notes(meet_id: str, request: Request):
    """Return the notes/minutes of a meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {
        "success": True,
        "meetId": meet.meet_id,
        "minutes": meet.minutes.model_dump(mode="json"),
    }


async def _generate_notes(
    meet_id: str, payload: MeetNotesRequest, request: Request
):
    """Shared implementation of the notes/minutes endpoints."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        if (
            meet.minutes.status == "ready"
            and not payload.force
            and meet.minutes.summary
        ):
            return {
                "success": True,
                "status": "ready",
                "minutes": meet.minutes.model_dump(mode="json"),
            }
        task = service.schedule_minutes(
            meet,
            instructions=payload.instructions or "",
            background=bool(payload.async_generation),
        )
        if not payload.async_generation:
            await task
        meet = service.require_meet(meet_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    body = {
        "success": True,
        "status": meet.minutes.status,
        "minutes": meet.minutes.model_dump(mode="json"),
    }
    if payload.async_generation:
        return JSONResponse(status_code=202, content=body)
    return body


@router.post("/{meet_id}/notes")
async def generate_meeting_notes(
    meet_id: str,
    payload: Optional[MeetNotesRequest] = None,
    request: Request = None,
):
    """Generate the notes/minutes (async by default)."""
    return await _generate_notes(meet_id, payload or MeetNotesRequest(), request)


@router.post("/{meet_id}/minutes")
async def generate_meeting_minutes(
    meet_id: str,
    payload: Optional[MeetNotesRequest] = None,
    request: Request = None,
):
    """Alias of ``POST /notes`` (minutes is the product word)."""
    return await _generate_notes(meet_id, payload or MeetNotesRequest(), request)


@router.post("/{meet_id}/tasks")
async def create_meeting_tasks(
    meet_id: str,
    payload: Optional[MeetTasksRequest] = None,
    request: Request = None,
):
    """
    Create tasks from the meeting minutes (and optionally run them).

    With no ``items`` the pending action items of the stored minutes are used,
    so the summary page can create all follow-ups in one call.
    """
    payload = payload or MeetTasksRequest()
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        result = await service.create_tasks(
            meet,
            items=payload.items,
            item_ids=payload.item_ids,
            board=payload.board,
            column=payload.column,
            run=payload.run,
            schedule_at=payload.schedule_at,
            timezone_name=payload.timezone,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {
        "success": True,
        "tasks": result.get("tasks") or [],
        "run_ids": result.get("run_ids") or [],
        "error": result.get("error"),
        "meet": _meet_payload(service, meet),
    }


@router.post("/{meet_id}/settings")
async def update_meeting_settings(
    meet_id: str,
    payload: MeetSettings,
    request: Request,
):
    """Update the behaviour of a running meeting."""
    service = _service(request)
    try:
        meet = service.require_meet(meet_id)
        service.manager.update_settings(meet_id, payload)
        service.persist(meet)
        await service.emit_meet("settings", meet, settings=payload.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    except Exception as ex:  # noqa: BLE001
        _raise_for_meet_error(ex)
    return {"success": True, "meet": _meet_payload(service, meet)}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

@router.post("/{meet_id}/recording")
async def upload_meeting_recording(
    meet_id: str,
    file: UploadFile = File(..., description="Mixed call recording"),
    duration: float = Form(default=0.0),
    mime: Optional[str] = Form(default=None),
    request: Request = None,
):
    """Store the recording of the meeting (uploaded by the host)."""
    service = _service(request)
    meet = service.get_meet(meet_id)
    if meet is None:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RECORDING_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "the recording is too large (max "
                    f"{MAX_RECORDING_BYTES // (1024 * 1024)} MB)"
                ),
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=422, detail="empty recording")
    username, _sid, _avatar = _resolve_identity(request)
    filename = file.filename or f"{meet_id}.webm"
    try:
        recording = await service.save_recording(
            meet,
            content=content,
            filename=filename,
            mime=mime or file.content_type or "audio/webm",
            duration_seconds=duration,
            owner=username,
        )
    except MeetStoreError as ex:
        raise HTTPException(status_code=500, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        logger.exception("Could not store the meeting recording: %s", ex)
        raise HTTPException(status_code=500, detail=str(ex))
    return {
        "success": True,
        "recording": recording.model_dump(mode="json"),
        "recording_url": f"/api/meets/{meet_id}/recording",
    }


@router.get("/{meet_id}/recording")
async def download_meeting_recording(meet_id: str, request: Request):
    """Stream the stored recording (inline so the browser can play it)."""
    service = _service(request)
    meet = service.get_meet(meet_id)
    if meet is None:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    path = service.recording_file(meet)
    if not path:
        raise HTTPException(status_code=404, detail="The meeting has no recording")
    return FileResponse(
        path,
        media_type=meet.recording.mime or "audio/webm",
        filename=meet.recording.filename or os.path.basename(path),
    )


@router.post("/{meet_id}/recording/start")
async def mark_recording_started(meet_id: str, request: Request):
    """Mark the recording as started (called by the host recorder)."""
    service = _service(request)
    meet = service.get_meet(meet_id)
    if meet is None:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")
    username, _sid, _avatar = _resolve_identity(request)
    service.manager.update_recording_fields(
        meet_id,
        status="recording",
        owner=username,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    service.persist(meet)
    await service.emit_meet(
        "recording", meet, recording=meet.recording.model_dump(mode="json")
    )
    return {"success": True, "recording": meet.recording.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Profile speech
# ---------------------------------------------------------------------------

@router.post("/{meet_id}/speech")
async def speak_meeting_text(
    meet_id: str,
    payload: MeetSpeechRequest,
    request: Request,
):
    """
    Synthesize a profile answer with the profile's configured voice.

    The browser falls back to ``SpeechSynthesis`` when this endpoint answers
    503 (no TTS provider configured), so a self-hosted installation still has
    voice.
    """
    service = _service(request)
    meet = service.get_meet(meet_id)
    if meet is None:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")

    entry = None
    if payload.messageId:
        entry = next(
            (
                item
                for item in meet.transcript
                if item.message_id == payload.messageId
                or item.entry_id == payload.messageId
            ),
            None,
        )
    text = (payload.text or (entry.text if entry else "") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text or messageId is required")
    profile_name = (
        payload.profile
        or (entry.speaker if entry and entry.speaker_kind == ParticipantKind.PROFILE else "")
        or ""
    )
    participant = meet.find_participant(profile_name) if profile_name else None
    voice = dict(participant.voice or {}) if participant else {}
    if voice and voice.get("enabled") is False:
        raise HTTPException(
            status_code=409,
            detail="the profile is silent (voice disabled in its settings)",
        )
    voice_profile = voice.get("voice") or {}
    synthesizer = SpeechSynthesizer()
    try:
        result = synthesizer.synthesize(
            text=text,
            provider=voice_profile.get("provider") or "",
            voice_id=voice_profile.get("voice_id") or "",
            language=voice_profile.get("language") or meet.settings.language or None,
            speed=float(voice_profile.get("speed") or 1.0),
            style=voice_profile.get("style") or None,
        )
    except SpeechUnavailable as ex:
        raise HTTPException(
            status_code=503,
            detail={"error": str(ex), "capability": ex.capability, "reason": ex.reason},
        )
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        logger.exception("Meeting speech failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"synthesis failed: {ex}")
    headers = {
        "Cache-Control": "no-store",
        "X-Voice-Provider": str(result.get("provider") or ""),
        "X-Voice-Model": str(result.get("model") or ""),
        "X-Meet-Profile": profile_name,
    }
    return Response(
        content=result["content"],
        media_type=result.get("mime") or "audio/mpeg",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Shared browser / computer session
# ---------------------------------------------------------------------------

@router.post("/{meet_id}/browser")
async def share_browser(meet_id: str, payload: MeetBrowserRequest, request: Request):
    """
    Share a browser/computer session with the whole meet.

    The association is stored on the meet (so late joiners and page reloads get
    it back) and broadcast to every connected client, which makes the session
    "started for everyone in the room" instead of only for the caller.
    """
    manager = get_meet_manager()
    service = _service(request)
    session_id = (payload.sessionId or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing 'sessionId' in request body")

    username, sid, _ = _resolve_identity(request)
    shared_browser = {
        "session_id": session_id,
        "session_name": payload.sessionName or "Browser session",
        "kind": payload.kind or "browser",
        "owner": username,
        "timestamp": payload.timestamp or int(time.time() * 1000),
    }
    try:
        meet = manager.set_shared_browser(meet_id, shared_browser)
        service.persist(meet)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")

    await _emit(
        "codx-junior-meet",
        {
            "type": "browser-session",
            "meetId": meet.meet_id,
            "from": username,
            "fromSid": sid,
            "sharedBrowser": shared_browser,
        },
    )
    return {"success": True, "meet": _meet_payload(service, meet)}


@router.delete("/{meet_id}/browser")
async def unshare_browser(meet_id: str, request: Request):
    """Stop sharing the browser/computer session with the meet."""
    manager = get_meet_manager()
    service = _service(request)
    username, sid, _ = _resolve_identity(request)
    try:
        meet = manager.set_shared_browser(meet_id, None)
        service.persist(meet)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Meet '{meet_id}' not found")

    await _emit(
        "codx-junior-meet",
        {
            "type": "browser-session",
            "meetId": meet.meet_id,
            "from": username,
            "fromSid": sid,
            "sharedBrowser": None,
        },
    )
    return {"success": True, "meet": _meet_payload(service, meet)}


# ---------------------------------------------------------------------------
# WebRTC signalling endpoints
# ---------------------------------------------------------------------------

@router.post("/{meet_id}/signal/offer")
async def send_offer(meet_id: str, payload: MeetOfferRequest, request: Request):
    """Relay an SDP offer to the target participant."""
    return await _relay_signal(
        meet_id, MeetSignalType.OFFER.value, payload.offer, payload.to, request
    )


@router.post("/{meet_id}/signal/answer")
async def send_answer(meet_id: str, payload: MeetAnswerRequest, request: Request):
    """Relay an SDP answer to the target participant."""
    return await _relay_signal(
        meet_id, MeetSignalType.ANSWER.value, payload.answer, payload.to, request
    )


@router.post("/{meet_id}/signal/ice-candidate")
async def add_ice_candidate(
    meet_id: str, payload: MeetIceCandidateRequest, request: Request
):
    """Relay an ICE candidate to the target participant."""
    return await _relay_signal(
        meet_id,
        MeetSignalType.ICE_CANDIDATE.value,
        payload.candidate,
        payload.to,
        request,
    )
