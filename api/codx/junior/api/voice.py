"""HTTP API of the voice conversation mode.

Routes (all prefixed with ``/api/voice``)::

    GET    /capabilities            server speech backends available
    GET    /sessions                recent calls (filter by status/chat)
    POST   /sessions                start / continue / resume a call
    GET    /sessions/{id}           session + current chat
    POST   /sessions/{id}/switch    move the call to another chat
    POST   /sessions/{id}/end       end the call
    POST   /sessions/{id}/turns     record the messages of a completed turn
    POST   /transcribe              audio clip -> text (faster-whisper, optional)
    POST   /speech                  text -> audio (TTS provider, optional)

The conversation itself is not an API concern: the client posts the spoken
message with the normal ``POST /api/chats/message`` endpoint, triggers the turn
with the existing socket event and stores the answer as a normal chat message.
This router only owns call lifecycle, switching and the optional speech
backends.

The router is auto-discovered by ``codx.junior.api.discover_routers``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from codx.junior.api import get_current_session
from codx.junior.voice.model import (
    SpeechRequest,
    VoiceLanguageRequest,
    VoiceSessionCreateRequest,
    VoiceSessionEndRequest,
    VoiceSessionSwitchRequest,
    VoiceTurnRecord,
)
from codx.junior.voice.service import VoiceConversationService
from codx.junior.voice.speech import (
    MAX_AUDIO_BYTES,
    SpeechSynthesizer,
    SpeechToText,
    SpeechUnavailable,
    capabilities,
)
from codx.junior.voice.store import VoiceSessionNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


# ---------------------------------------------------------------------------
# Dependencies (overridable in tests)
# ---------------------------------------------------------------------------


def get_speech_to_text() -> SpeechToText:
    """Return the server transcription backend."""
    return SpeechToText()


def get_synthesizer() -> SpeechSynthesizer:
    """Return the server synthesis backend."""
    return SpeechSynthesizer()


def get_voice_service(request: Request) -> VoiceConversationService:
    """Build the voice service for the request's project session."""
    return VoiceConversationService(get_current_session(request))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service_error(ex: Exception) -> HTTPException:
    """Translate a domain error into the right HTTP response."""
    if isinstance(ex, VoiceSessionNotFound):
        return HTTPException(status_code=404, detail=f"Voice session not found: {ex}")
    if isinstance(ex, SpeechUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "error": str(ex),
                "capability": ex.capability,
                "reason": ex.reason,
            },
        )
    message = str(ex) or ex.__class__.__name__
    status_code = 404 if "not found" in message.lower() else 400
    return HTTPException(status_code=status_code, detail=message)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@router.get("/capabilities")
def voice_capabilities(
    stt: SpeechToText = Depends(get_speech_to_text),
    synthesizer: SpeechSynthesizer = Depends(get_synthesizer),
):
    """Report whether the server can transcribe/synthesize speech.

    The client uses this to decide between its browser engines and the server
    backends; browser support is detected in the browser and is not reported
    here.
    """
    return capabilities(speech_to_text=stt, synthesizer=synthesizer)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions")
def list_voice_sessions(
    status: Optional[str] = None,
    chat_id: Optional[str] = None,
    limit: int = 20,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """List recent voice sessions, most recently updated first."""
    sessions = service.list_sessions(status=status, chat_id=chat_id, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/sessions")
def create_voice_session(
    payload: VoiceSessionCreateRequest,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """Start a call, continue the active call of a chat or resume a session."""
    try:
        response, _session, _created = service.start_session(payload)
        return response
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001 - translated for the caller
        logger.warning("Could not start voice session: %s", ex)
        raise _service_error(ex)


@router.get("/sessions/{session_id}")
def get_voice_session(
    session_id: str,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """Return one session plus its current chat."""
    try:
        return service.get_session(session_id)
    except Exception as ex:  # noqa: BLE001
        raise _service_error(ex)


@router.post("/sessions/{session_id}/switch")
def switch_voice_session(
    session_id: str,
    payload: VoiceSessionSwitchRequest,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """Move an active call to ``payload.chat_id`` and tell both chats."""
    try:
        return service.switch_session(session_id, payload)
    except Exception as ex:  # noqa: BLE001
        logger.warning("Could not switch voice session '%s': %s", session_id, ex)
        raise _service_error(ex)


@router.post("/sessions/{session_id}/language")
def set_voice_language(
    session_id: str,
    payload: VoiceLanguageRequest,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """Change the speech language of a call (used by the in-call switch)."""
    try:
        return service.set_language(session_id, payload)
    except Exception as ex:  # noqa: BLE001
        raise _service_error(ex)


@router.post("/sessions/{session_id}/end")
def end_voice_session(
    session_id: str,
    payload: Optional[VoiceSessionEndRequest] = None,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """End a call (idempotent)."""
    try:
        return service.end_session(session_id, payload or VoiceSessionEndRequest())
    except Exception as ex:  # noqa: BLE001
        raise _service_error(ex)


@router.post("/sessions/{session_id}/turns")
def record_voice_turn(
    session_id: str,
    payload: VoiceTurnRecord,
    service: VoiceConversationService = Depends(get_voice_service),
):
    """Register the chat messages that belong to one completed speech turn."""
    try:
        return service.record_turn(session_id, payload)
    except Exception as ex:  # noqa: BLE001
        raise _service_error(ex)


# ---------------------------------------------------------------------------
# Optional server speech backends
# ---------------------------------------------------------------------------


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(..., description="Recorded utterance"),
    language: Optional[str] = Form(default=None),
    stt: SpeechToText = Depends(get_speech_to_text),
):
    """Transcribe one recorded utterance with the optional local backend."""
    # Read in bounded chunks: an unexpected huge body must not be buffered.
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"audio is too large (max {MAX_AUDIO_BYTES // (1024 * 1024)} MB)",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    try:
        result = await run_in_threadpool(
            stt.transcribe,
            content,
            file.filename,
            file.content_type,
            language,
        )
        return result
    except SpeechUnavailable as ex:
        raise _service_error(ex)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        logger.exception("Unexpected transcription failure")
        raise HTTPException(status_code=500, detail=f"transcription failed: {ex}")


@router.post("/speech")
def speak_text(
    payload: SpeechRequest,
    synthesizer: SpeechSynthesizer = Depends(get_synthesizer),
):
    """Synthesize ``payload.text`` and stream the audio back.

    Answers 503 with a machine-readable reason when no TTS provider is
    configured: the client then falls back to the browser voice instead of
    failing the call.
    """
    try:
        result = synthesizer.synthesize(
            text=payload.text,
            provider=payload.provider,
            voice_id=payload.voice_id,
            language=payload.language,
            speed=payload.speed,
            style=payload.style,
        )
    except SpeechUnavailable as ex:
        raise _service_error(ex)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        logger.exception("Unexpected synthesis failure")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {ex}")
    headers = {
        "Cache-Control": "no-store",
        "X-Voice-Provider": str(result.get("provider") or ""),
        "X-Voice-Model": str(result.get("model") or ""),
    }
    return Response(
        content=result["content"],
        media_type=result.get("mime") or "audio/mpeg",
        headers=headers,
    )
