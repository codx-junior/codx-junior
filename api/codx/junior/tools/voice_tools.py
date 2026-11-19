"""Tool that lets codx-junior move an active voice call to another chat.

The tool cannot move the user's screen from the backend: it records a
``pending_switch`` on the call and the client applies it as soon as the turn
that requested it finishes (``POST /api/voice/sessions/{id}/switch``). This is
what makes "switch me to the payments task" work in the middle of a call while
keeping the answer in the chat where the user asked it.

The tool is harmless outside a voice call: it simply reports that no call is
active, so the assistant can explain how to start one.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from codx.junior.voice.store import VoiceSessionStore

from .model import ToolResponse

logger = logging.getLogger(__name__)

VOICE_SWITCH_CHAT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "voice_switch_chat",
        "description": (
            "Move the ACTIVE voice call to another chat/task. Use it when the "
            "user asks to change the conversation, e.g. 'switch to the payments "
            "task', 'go back to the voice UI chat' or 'continue in the roadmap "
            "chat'. Resolve the destination by its exact id or by a distinctive "
            "part of its name. The client applies the switch after the current "
            "answer, so confirm the destination in your reply. If there is no "
            "active voice call, the tool explains that and does nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chat": {
                    "type": "string",
                    "description": (
                        "Destination chat: exact id, exact name, or a "
                        "distinctive part of its name."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason to show/spoken to the user.",
                },
            },
            "required": ["chat"],
        },
    },
}


def _resolve_settings(
    settings: Any = None, session: Any = None
) -> Any:
    """Resolve the project settings from the injected values."""
    if settings is not None:
        return settings
    settings = getattr(session, "settings", None)
    if settings is not None:
        return settings
    return None


def _resolve_managers(session: Any, settings: Any):
    """Return ``(chat_manager, settings)`` from the injected context."""
    settings = _resolve_settings(settings=settings, session=session)
    if settings is None:
        return None, None
    manager_owner = session or getattr(settings, "_active_session", None)
    if manager_owner is not None and hasattr(manager_owner, "get_chat_manager"):
        return manager_owner.get_chat_manager(), settings
    return None, settings


def _match_chats(manager: Any, query: str) -> list:
    """Return chats matching ``query`` by id, exact name or substring."""
    value = (query or "").strip()
    if not value:
        return []
    chats = manager.list_chats() or []
    by_id = [chat for chat in chats if str(chat.id) == value]
    if by_id:
        return by_id
    lowered = value.lower()
    exact = [chat for chat in chats if (chat.name or "").strip().lower() == lowered]
    if exact:
        return exact
    return [
        chat
        for chat in chats
        if lowered in (chat.name or "").strip().lower()
    ]


def voice_switch_chat(
    chat: str,
    reason: str = "",
    chat_id: Optional[str] = None,
    session: Any = None,
    settings: Any = None,
    **kwargs: Any,
) -> ToolResponse:
    """Record a client-applied switch of the active voice call.

    Args:
        chat: Destination chat id, exact name or distinctive name fragment.
        reason: Optional reason shown/spoken to the user.
        chat_id: Current chat id, injected by the agent runtime.
        session: Project session, injected when available.
        settings: Project settings, injected by the runtime.

    Returns:
        A :class:`ToolResponse` with a user-facing confirmation and a JSON
        payload describing the pending switch.
    """
    manager, settings = _resolve_managers(session=session, settings=settings)
    if manager is None or settings is None:
        return ToolResponse(
            "I cannot switch the voice conversation right now: the project "
            "context is not available.",
            "voice_switch_chat failed: no project settings/session injected",
        )
    if not chat_id:
        return ToolResponse(
            "I cannot tell which voice conversation to move: no active chat "
            "was provided.",
            "voice_switch_chat failed: missing chat_id",
        )

    try:
        store = VoiceSessionStore.for_settings(settings)
        voice_session = store.active_for_chat(str(chat_id))
    except Exception as ex:  # noqa: BLE001 - a missing store is not a call failure
        logger.warning("voice_switch_chat: could not read voice sessions: %s", ex)
        return ToolResponse(
            "I could not read the voice call state, so I did not switch.",
            f"voice_switch_chat failed: {ex}",
        )

    if voice_session is None:
        return ToolResponse(
            "There is no active voice call to switch. Start one from the phone "
            "button in the chat header first.",
            "voice_switch_chat skipped: no active voice session for this chat",
        )

    query = (chat or "").strip()
    if not query:
        return ToolResponse(
            "Which chat should I switch the call to?",
            "voice_switch_chat failed: empty destination",
        )
    try:
        matches = _match_chats(manager, query)
    except Exception as ex:  # noqa: BLE001
        logger.warning("voice_switch_chat: could not list chats: %s", ex)
        return ToolResponse(
            "I could not list the chats, so I did not switch.",
            f"voice_switch_chat failed: {ex}",
        )

    if not matches:
        return ToolResponse(
            f"I could not find a chat called “{query}”.",
            json.dumps(
                {"ok": False, "reason": "chat_not_found", "query": query},
                ensure_ascii=False,
            ),
        )
    if len(matches) > 1:
        names = ", ".join(f"“{match.name}”" for match in matches[:5])
        return ToolResponse(
            f"“{query}” matches more than one chat: {names}. Which one?",
            json.dumps(
                {
                    "ok": False,
                    "reason": "ambiguous_chat",
                    "query": query,
                    "candidates": [
                        {"chat_id": match.id, "name": match.name}
                        for match in matches[:10]
                    ],
                },
                ensure_ascii=False,
            ),
        )

    target = matches[0]
    if getattr(target, "harness", None) or getattr(target, "external_source", None):
        return ToolResponse(
            f"“{target.name}” is executed by an external harness and its "
            "transcript is read-only, so I cannot move the call there.",
            json.dumps(
                {
                    "ok": False,
                    "reason": "read_only_harness",
                    "chat_id": target.id,
                },
                ensure_ascii=False,
            ),
        )
    if str(target.id) == str(voice_session.chat_id):
        return ToolResponse(
            f"The voice call is already in “{target.name}”.",
            json.dumps(
                {"ok": True, "switched": False, "chat_id": target.id},
                ensure_ascii=False,
            ),
        )

    try:
        store.request_switch(
            session_id=voice_session.session_id,
            chat_id=str(target.id),
            chat_name=target.name or "",
            reason=reason or "",
            requested_by="assistant",
        )
    except Exception as ex:  # noqa: BLE001
        logger.warning("voice_switch_chat: could not request switch: %s", ex)
        return ToolResponse(
            "I could not schedule the switch, so the call stays in this chat.",
            f"voice_switch_chat failed: {ex}",
        )

    return ToolResponse(
        f"I will move the voice conversation to “{target.name}” when this "
        "answer finishes.",
        json.dumps(
            {
                "ok": True,
                "switched": False,
                "pending": True,
                "session_id": voice_session.session_id,
                "chat_id": str(target.id),
                "chat_name": target.name or "",
                "reason": reason or "",
            },
            ensure_ascii=False,
        ),
    )
