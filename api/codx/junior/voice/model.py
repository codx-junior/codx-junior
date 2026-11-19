"""Data models of the voice conversation mode.

A *voice session* is the durable counterpart of a phone call with
codx-junior. It never duplicates the conversation: every utterance and every
answer is a normal chat message, persisted with the chat. The session only
keeps what the chat cannot express:

* which chat the call is currently talking to (it moves when the conversation
  is switched),
* the call lifecycle (active / ended, resumptions, timestamps),
* the messages that belong to the call, so a call can be resumed and audited,
* a pending switch requested by the assistant (``voice_switch_chat`` tool),
  applied by the client once the turn that requested it has finished.

The module is pure data + stdlib + pydantic, with no I/O, so the store, the
API router and the tests can all import it without cycles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class VoiceSessionStatus(str, Enum):
    """Lifecycle of a voice session."""

    ACTIVE = "active"
    ENDED = "ended"


class VoiceChatRef(BaseModel):
    """A chat the call was attached to, and for how long.

    A session starts on one chat and may be switched to another by the user or
    by the assistant. ``chats`` is the ordered trail of those moves: the last
    entry is always the chat named by ``VoiceSession.chat_id``.
    """

    chat_id: str = Field(description="Chat id")
    name: str = Field(default="", description="Chat name at the time of the move")
    since: str = Field(default_factory=utc_now, description="When the call arrived")
    until: Optional[str] = Field(
        default=None, description="When the call left; None while it is the current chat"
    )


class VoiceSwitchRequest(BaseModel):
    """A switch requested by the assistant and still waiting to be applied.

    The assistant cannot move the user's screen from the backend: the tool sets
    this record and the client applies it after the turn (``POST
    /api/voice/sessions/{id}/switch``). Keeping it on the session means the
    switch is not lost if the page is reloaded before it is applied.
    """

    chat_id: str = Field(description="Target chat id")
    chat_name: str = Field(default="", description="Target chat name")
    reason: str = Field(default="", description="Why the assistant wants to switch")
    requested_by: str = Field(default="assistant", description="user or assistant")
    created_at: str = Field(default_factory=utc_now)


class VoiceSession(BaseModel):
    """Durable state of one voice conversation."""

    session_id: str = Field(description="Stable session id")
    chat_id: str = Field(description="Chat the call is talking to right now")
    title: str = Field(default="", description="Human readable title (usually the chat name)")
    owner_project_id: Optional[str] = Field(
        default=None, description="Project that owns the current chat"
    )
    project_id: Optional[str] = Field(
        default=None, description="Project the call is working on"
    )
    status: VoiceSessionStatus = Field(
        default=VoiceSessionStatus.ACTIVE, description="active or ended"
    )
    language: str = Field(default="en-US", description="Preferred speech language")
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    ended_at: Optional[str] = Field(default=None)
    turn_count: int = Field(default=0, description="Completed speech turns")
    resumptions: int = Field(default=0, description="How many times the call was resumed")
    last_user_text: str = Field(default="", description="Last spoken instruction")
    last_assistant_text: str = Field(
        default="", description="Last spoken assistant answer"
    )
    message_ids: List[str] = Field(
        default_factory=list,
        description="Chat messages that belong to this call (user + assistant)",
    )
    chats: List[VoiceChatRef] = Field(
        default_factory=list, description="Ordered trail of chats the call used"
    )
    pending_switch: Optional[VoiceSwitchRequest] = Field(
        default=None, description="Switch requested by the assistant, not applied yet"
    )
    meta: Dict[str, Any] = Field(
        default_factory=dict, description="Free-form metadata (client, device, ...)"
    )

    @property
    def is_active(self) -> bool:
        """True while the call is running."""
        return self.status == VoiceSessionStatus.ACTIVE

    def current_chat_ref(self) -> Optional[VoiceChatRef]:
        """Return the open entry of ``chats`` (the current chat), if any."""
        for chat in reversed(self.chats):
            if chat.chat_id == self.chat_id and chat.until is None:
                return chat
        return None

    def touch(self) -> None:
        """Stamp ``updated_at``; every mutation calls this before saving."""
        self.updated_at = utc_now()

    def add_message_ids(self, *message_ids: Optional[str]) -> None:
        """Append message ids once, keeping the conversation order."""
        for message_id in message_ids:
            if message_id and message_id not in self.message_ids:
                self.message_ids.append(str(message_id))


# ---------------------------------------------------------------------------
# Request payloads (HTTP + client)
# ---------------------------------------------------------------------------


class VoiceSessionCreateRequest(BaseModel):
    """Payload for ``POST /api/voice/sessions``."""

    chat_id: str = Field(description="Chat to attach the call to")
    session_id: Optional[str] = Field(
        default=None,
        description="Existing session to continue; omitted starts a new one",
    )
    owner_project_id: Optional[str] = Field(
        default=None, description="Owner project of the chat, when known"
    )
    language: str = Field(default="en-US", description="Preferred speech language")
    title: str = Field(default="", description="Optional title; the chat name is used otherwise")
    announce: bool = Field(
        default=True, description="Add a short notice message to the chat"
    )
    meta: Dict[str, Any] = Field(default_factory=dict, description="Free-form client metadata")


class VoiceSessionSwitchRequest(BaseModel):
    """Payload for ``POST /api/voice/sessions/{id}/switch``."""

    chat_id: str = Field(description="Target chat id")
    reason: str = Field(default="", description="Why the conversation is moving")
    requested_by: str = Field(default="user", description="user or assistant")
    announce: bool = Field(
        default=True, description="Add a notice message to both chats"
    )


class VoiceLanguageRequest(BaseModel):
    """Payload for ``POST /api/voice/sessions/{id}/language``."""

    language: str = Field(
        min_length=2,
        max_length=35,
        description="Speech locale/ISO language, e.g. 'es-ES', 'pt-BR', 'ja'",
    )


class VoiceSessionEndRequest(BaseModel):
    """Payload for ``POST /api/voice/sessions/{id}/end``."""

    reason: str = Field(default="", description="Why the call ended")
    announce: bool = Field(
        default=True, description="Add a short notice message to the chat"
    )


class VoiceTurnRecord(BaseModel):
    """Payload for ``POST /api/voice/sessions/{id}/turns``.

    The conversation itself lives in the chat; this only records which messages
    belong to the call, so resuming/auditing does not have to scan the whole
    conversation.
    """

    user_message_id: Optional[str] = Field(default=None)
    assistant_message_id: Optional[str] = Field(default=None)
    user_text: str = Field(default="", max_length=20000)
    assistant_text: str = Field(default="", max_length=40000)
    at: Optional[str] = Field(default=None, description="Turn instant (defaults to now)")


class SpeechRequest(BaseModel):
    """Payload for ``POST /api/voice/speech``."""

    text: str = Field(min_length=1, max_length=5000, description="Text to speak")
    provider: str = Field(default="", description="Preferred TTS provider")
    voice_id: str = Field(default="", description="Provider voice id")
    language: Optional[str] = Field(default=None, description="Language hint")
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    style: Optional[str] = Field(default=None)
