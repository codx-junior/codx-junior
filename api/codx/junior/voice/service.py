"""Business operations of the voice conversation mode.

The service owns the interactions that are more than storage:

* attach a call to a chat (new, continue or resume),
* move a running call to another chat and tell both chats about the move,
* end a call,
* mirror the call state into ``Chat.meta_data['voice_session']`` so the chat
  header/sidebar can show it without reading the voice store.

The conversation itself is NEVER copied here: utterances remain normal chat
messages (``Message.meta_data['voice']`` marks the spoken ones). The notice
messages created on switch/end are ordinary assistant messages with a
``voice_notice`` marker, so they are visible in the transcript and part of the
context the model sees afterwards.

Everything is synchronous and receives the request's ``CODXJuniorSession``, so
the API router is a thin layer and the tests can drive the service with a fake
session.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from codx.junior.db import ROLE_ASSISTANT, Chat, Message
from codx.junior.voice.model import (
    VoiceSession,
    VoiceLanguageRequest,
    VoiceSessionCreateRequest,
    VoiceSessionEndRequest,
    VoiceSessionStatus,
    VoiceSessionSwitchRequest,
    VoiceTurnRecord,
)
from codx.junior.voice.store import VoiceSessionStore

logger = logging.getLogger(__name__)

VOICE_META_KEY = "voice_session"
VOICE_NOTICE_KEY = "voice_notice"
VOICE_USER = "codx-junior"

START_NOTICE = (
    "Voice call started. Speak (or type) your instruction and I will answer "
    "aloud. Everything we say is kept in this chat."
)
RESUME_NOTICE = "Voice call resumed in this chat. I am listening again."
END_NOTICE = "Voice call ended. The conversation stays in this chat."


class VoiceConversationService:
    """Voice session operations for one project session."""

    def __init__(self, session, store: Optional[VoiceSessionStore] = None):
        self.session = session
        self.settings = getattr(session, "settings", None)
        if store is None:
            store = VoiceSessionStore.for_settings(self.settings)
        self.store = store

    # ------------------------------------------------------------------ helpers
    def _resolve_session(self, owner_project_id: Optional[str] = None):
        """Return the session whose project owns ``owner_project_id``."""
        if not owner_project_id:
            return self.session
        current = getattr(self.settings, "project_id", None)
        if owner_project_id == current:
            return self.session
        try:
            return self.session.switch_project(project_id=owner_project_id)
        except Exception:  # noqa: BLE001 - fall back to the request project
            logger.debug(
                "Could not switch to project '%s' for voice chat", owner_project_id
            )
            return self.session

    def _manager(self, owner_project_id: Optional[str] = None):
        return self._resolve_session(owner_project_id).get_chat_manager()

    def _find_chat(
        self,
        chat_id: str,
        owner_project_id: Optional[str] = None,
        required: bool = True,
    ) -> Optional[Chat]:
        if not chat_id:
            if required:
                raise ValueError("chat_id is required")
            return None
        chat = self._manager(owner_project_id).find_by_id(chat_id=str(chat_id))
        if chat is None and required:
            raise ValueError(f"chat '{chat_id}' not found")
        return chat

    @staticmethod
    def _chat_name(chat: Optional[Chat], fallback: str = "") -> str:
        if chat is None:
            return fallback
        return (chat.name or fallback or chat.id or "chat").strip()

    def _meta_for(
        self,
        session: VoiceSession,
        status: str,
        moved_to: Optional[Dict[str, str]] = None,
        moved_from: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "chat_id": session.chat_id,
            "language": session.language,
            "started_at": session.created_at,
            "updated_at": session.updated_at,
        }
        if moved_to:
            payload["moved_to"] = moved_to
        if moved_from:
            payload["moved_from"] = moved_from
        return payload

    def _set_chat_voice_meta(
        self,
        chat: Chat,
        session: VoiceSession,
        status: str,
        moved_to: Optional[Dict[str, str]] = None,
        moved_from: Optional[Dict[str, str]] = None,
    ) -> Chat:
        """Mirror the session state into the chat metadata (best effort)."""
        if chat is None:
            return chat
        manager = self._manager(chat.owner_project_id or chat.project_id)
        stored = manager.find_by_id(chat_id=chat.id) or chat
        meta = dict(stored.meta_data or {})
        meta[VOICE_META_KEY] = self._meta_for(
            session, status, moved_to=moved_to, moved_from=moved_from
        )
        try:
            return manager.update_chat_metadata(stored, {"meta_data": meta})
        except Exception as ex:  # noqa: BLE001 - the call must survive metadata issues
            logger.warning(
                "Could not mirror voice session '%s' into chat '%s': %s",
                session.session_id,
                chat.id,
                ex,
            )
            return stored

    def _add_notice(
        self,
        chat: Chat,
        session: VoiceSession,
        text: str,
        notice: str,
    ) -> Optional[Message]:
        """Persist a visible assistant notice in ``chat`` (best effort)."""
        if chat is None:
            return None
        manager = self._manager(chat.owner_project_id or chat.project_id)
        stored = manager.find_by_id(chat_id=chat.id)
        if stored is None:
            return None
        message = Message(
            role=ROLE_ASSISTANT,
            content=text,
            user=VOICE_USER,
            done=True,
            meta_data={
                "voice_session_id": session.session_id,
                VOICE_NOTICE_KEY: notice,
            },
        )
        try:
            manager.add_message(chat=stored, message=message)
            session.add_message_ids(message.doc_id)
            self.store.add_message_ids(session.session_id, message.doc_id)
            return message
        except Exception as ex:  # noqa: BLE001 - a notice is never worth a 500
            logger.warning(
                "Could not add voice notice to chat '%s': %s", chat.id, ex
            )
            return None

    @staticmethod
    def serialize_chat(chat: Optional[Chat]) -> Optional[dict]:
        """Return the compact chat summary the voice client needs.

        The call responses are frequent (start/switch/get) and the chat can be
        huge (messages, base64 attachments). The conversation is loaded by the
        client with the normal chat endpoint, so only identity/metadata is
        returned here; drafts therefore never leave the server either.
        """
        if chat is None:
            return None
        fields = (
            "id",
            "doc_id",
            "name",
            "description",
            "owner_project_id",
            "project_id",
            "parent_id",
            "root_id",
            "mode",
            "status",
            "board",
            "column",
            "meta_data",
            "created_at",
            "updated_at",
        )
        summary = {}
        for field in fields:
            value = getattr(chat, field, None)
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            summary[field] = value
        return summary

    # ------------------------------------------------------------------ start
    def start_session(
        self, payload: VoiceSessionCreateRequest
    ) -> Tuple[dict, VoiceSession, bool]:
        """Create, continue or resume the call of ``payload.chat_id``.

        Returns:
            ``(response, session, created)`` where ``response`` is ready for the
            HTTP layer (session + serialized chat + notice text).
        """
        chat = self._find_chat(payload.chat_id, payload.owner_project_id)
        if getattr(chat, "harness", None) or getattr(chat, "external_source", None):
            raise ValueError(
                "this task is executed by an external harness: its transcript is "
                "read-only, so a voice call cannot run here"
            )
        title = (payload.title or self._chat_name(chat)).strip()
        before = self.store.get(payload.session_id) if payload.session_id else None
        resumed = bool(before and before.status == VoiceSessionStatus.ENDED)

        session, created = self.store.create_or_resume(
            chat_id=chat.id,
            owner_project_id=payload.owner_project_id or chat.owner_project_id,
            language=payload.language,
            title=title,
            session_id=payload.session_id,
            project_id=chat.project_id,
            meta=payload.meta,
        )
        if payload.language:
            session.language = payload.language
        chat = self._set_chat_voice_meta(chat, session, "active")

        notice = None
        if payload.announce and created:
            notice = START_NOTICE
            self._add_notice(chat, session, START_NOTICE, "started")
        elif payload.announce and resumed:
            notice = RESUME_NOTICE
            self._add_notice(chat, session, RESUME_NOTICE, "resumed")
        # Refresh: the notice/meta writes changed the persisted chat.
        chat = self._find_chat(chat.id, required=False) or chat

        response = {
            "session": session.model_dump(mode="json"),
            "chat": self.serialize_chat(chat),
            "created": created,
            "resumed": resumed,
            "notice": notice,
        }
        return response, session, created

    # ----------------------------------------------------------------- switch
    def switch_session(
        self,
        session_id: str,
        payload: VoiceSessionSwitchRequest,
    ) -> dict:
        """Move an active call to another chat, telling both chats."""
        session = self.store.get(session_id, required=True)
        if not session.is_active:
            raise ValueError("voice session is not active")
        target = self._find_chat(payload.chat_id)
        if getattr(target, "harness", None) or getattr(target, "external_source", None):
            raise ValueError(
                "the destination task is executed by an external harness: its "
                "transcript is read-only, so the call cannot move there"
            )
        if target.id == session.chat_id:
            return {
                "session": session.model_dump(mode="json"),
                "chat": self.serialize_chat(target),
                "previous_chat_id": session.chat_id,
                "notice": f"The call is already in “{self._chat_name(target)}”.",
                "switched": False,
            }

        source = self._find_chat(session.chat_id, required=False)
        source_name = self._chat_name(source, "the previous chat")
        target_name = self._chat_name(target)
        session = self.store.switch(
            session_id=session.session_id,
            chat_id=target.id,
            chat_name=target_name,
        )

        moved_to = {"chat_id": target.id, "name": target_name}
        moved_from = {"chat_id": source.id if source else "", "name": source_name}
        target = self._set_chat_voice_meta(
            target,
            session,
            "active",
            moved_from=moved_from,
        )
        if source is not None:
            source = self._set_chat_voice_meta(
                source,
                session,
                "switched",
                moved_to=moved_to,
            )

        reason = (payload.reason or "").strip()
        target_notice = f"Voice conversation switched here from “{source_name}”."
        if reason:
            target_notice += f" Reason: {reason}."
        source_notice = f"Voice conversation moved to “{target_name}”."
        if payload.announce:
            self._add_notice(target, session, target_notice, "switch_target")
            if source is not None:
                self._add_notice(source, session, source_notice, "switch_source")
        return {
            "session": session.model_dump(mode="json"),
            "chat": self.serialize_chat(target),
            "previous_chat_id": source.id if source else None,
            "notice": target_notice,
            "switched": True,
        }

    # --------------------------------------------------------------- language
    def set_language(self, session_id: str, payload: VoiceLanguageRequest) -> dict:
        """Change the language of a call and mirror it into the chat metadata."""
        session = self.store.set_language(session_id, payload.language)
        chat = self._find_chat(session.chat_id, required=False)
        if chat is not None:
            chat = self._set_chat_voice_meta(
                chat,
                session,
                "active" if session.is_active else "ended",
            )
        return {"session": session.model_dump(mode="json"), "chat": self.serialize_chat(chat)}

    # -------------------------------------------------------------------- end
    def end_session(self, session_id: str, payload: VoiceSessionEndRequest) -> dict:
        session = self.store.get(session_id, required=True)
        was_active = session.is_active
        session = self.store.end(session.session_id, reason=payload.reason)
        chat = self._find_chat(session.chat_id, required=False)
        if chat is not None:
            chat = self._set_chat_voice_meta(chat, session, "ended")
            if payload.announce and was_active:
                self._add_notice(chat, session, END_NOTICE, "ended")
            chat = self._find_chat(chat.id, required=False) or chat
        return {
            "session": session.model_dump(mode="json"),
            "chat": self.serialize_chat(chat),
            "notice": END_NOTICE if payload.announce and was_active else None,
        }

    # ------------------------------------------------------------------ turns
    def record_turn(self, session_id: str, payload: VoiceTurnRecord) -> dict:
        session = self.store.record_turn(
            session_id=session_id,
            user_message_id=payload.user_message_id,
            assistant_message_id=payload.assistant_message_id,
            user_text=payload.user_text,
            assistant_text=payload.assistant_text,
            at=payload.at,
        )
        return {"session": session.model_dump(mode="json")}

    # ------------------------------------------------------------------- read
    def get_session(self, session_id: str) -> dict:
        session = self.store.get(session_id, required=True)
        chat = self._find_chat(session.chat_id, required=False)
        return {
            "session": session.model_dump(mode="json"),
            "chat": self.serialize_chat(chat),
        }

    def list_sessions(
        self,
        status: Optional[str] = None,
        chat_id: Optional[str] = None,
        limit: int = 20,
    ) -> list:
        return [
            session.model_dump(mode="json")
            for session in self.store.list_sessions(
                status=status, chat_id=chat_id, limit=limit
            )
        ]
