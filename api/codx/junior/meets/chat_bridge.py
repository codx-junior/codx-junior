"""
Chat bridge of a meeting.

Every meeting owns **one chat**. That is where the conversation lives: human
utterances, AI profile answers and meeting notices are ordinary chat messages,
so the transcript can be read, searched and exported like any other
conversation, and the "all responses in the associated chat" rule is enforced
by construction.

The chat is stored in the project that started the meeting, under the
``Meetings`` board/``Live`` column, so it is not a task and never appears in a
board column. ``Chat.meta_data['meeting']`` keeps the link in both directions.
"""

from __future__ import annotations

import logging
import uuid

from typing import Any, Dict, Optional

from codx.junior.db import Chat, Message

logger = logging.getLogger(__name__)

MEETING_META_KEY = "meeting"
MEETING_NOTICE_KEY = "meeting_notice"
MEETING_CHAT_MODE = "meet"
MEETING_BOARD = "Meetings"
MEETING_COLUMN = "Live"
MEETING_USER = "codx-junior"


class MeetingChatBridge:
    """Create/load the chat of a meeting and append messages to it."""

    def __init__(self, session: Any, meet: Any):
        """
        Args:
            session: The project ``CODXJuniorSession``.
            meet: The meeting document (its ``chat_id`` is updated by the bridge).
        """
        self.session = session
        self.meet = meet

    # ------------------------------------------------------------------
    # Session / manager helpers
    # ------------------------------------------------------------------
    @property
    def settings(self) -> Any:
        """Settings of the project that owns the meeting."""
        return getattr(self.session, "settings", None)

    def _manager(self):
        """Chat manager of the active project."""
        return self.session.get_chat_manager()

    def _chat_metadata(self, status: str = "active") -> Dict[str, Any]:
        """Metadata mirrored into the chat, used by chat headers and lists."""
        return {
            "meet_id": self.meet.meet_id,
            "meeting_id": self.meet.meet_id,
            "name": self.meet.name,
            "agenda": self.meet.agenda,
            "status": status,
            "initiator": self.meet.initiator,
            "participants": self.meet.participant_usernames(),
            "profiles": self.meet.profile_usernames(),
            "source": self.meet.source,
            "channel_id": self.meet.channel_id,
            "channel_name": self.meet.channel_name,
            "team_id": self.meet.team_id,
            "created_at": self.meet.created_at,
        }

    @staticmethod
    def serialize_chat(chat: Optional[Chat]) -> Optional[dict]:
        """Return the compact chat summary needed by the meeting client."""
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
            "profiles",
            "users",
            "meta_data",
            "created_at",
            "updated_at",
        )
        summary: Dict[str, Any] = {}
        for field in fields:
            value = getattr(chat, field, None)
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            summary[field] = value
        return summary

    # ------------------------------------------------------------------
    # Chat lifecycle
    # ------------------------------------------------------------------
    def existing_chat(self) -> Optional[Chat]:
        """Return the associated chat when it still exists."""
        chat_id = getattr(self.meet, "chat_id", None)
        if not chat_id:
            return None
        try:
            return self._manager().find_by_id(chat_id=str(chat_id))
        except Exception:  # noqa: BLE001 - an unreadable chat means "create one"
            logger.warning("Meeting: cannot load chat '%s'", chat_id, exc_info=True)
            return None

    def ensure_chat(self, name: str = "") -> Chat:
        """Return the meeting chat, creating it when necessary."""
        existing = self.existing_chat()
        if existing is not None:
            try:
                return self._manager().update_chat_metadata(
                    existing,
                    {
                        "meta_data": {
                            **dict(existing.meta_data or {}),
                            MEETING_META_KEY: self._chat_metadata(),
                        }
                    },
                )
            except Exception:  # noqa: BLE001 - metadata sync is best effort
                logger.debug("Meeting: cannot refresh chat metadata", exc_info=True)
                return existing

        if self.settings is None:
            raise RuntimeError("the project session has no settings")

        chat = Chat(
            name=(name or self.meet.name or "Meeting").strip()[:200],
            description=f"Meeting chat: {self.meet.name}".strip(),
            mode=MEETING_CHAT_MODE,
            board=MEETING_BOARD,
            column=MEETING_COLUMN,
            project_id=self.meet.project_id
            or getattr(self.settings, "project_id", None),
            owner_project_id=getattr(self.settings, "project_id", None),
            users=list(
                dict.fromkeys(
                    self.meet.participant_usernames() + list(self.meet.invited)
                )
            ),
            profiles=self.meet.profile_usernames(),
            meta_data={MEETING_META_KEY: self._chat_metadata()},
        )
        saved = self._manager().save_chat(chat=chat)
        self.meet.chat_id = saved.id
        logger.info("Meeting '%s' created chat '%s'", self.meet.meet_id, saved.id)
        return saved

    def sync_chat(self, status: str = "active") -> Optional[Chat]:
        """Mirror the meeting state into the chat metadata (best effort)."""
        chat = self.existing_chat()
        if chat is None:
            return None
        try:
            metadata = {
                **dict(chat.meta_data or {}),
                MEETING_META_KEY: self._chat_metadata(status=status),
            }
            return self._manager().update_chat_metadata(chat, {"meta_data": metadata})
        except Exception:  # noqa: BLE001 - a chat that cannot be updated is not fatal
            logger.warning(
                "Meeting '%s': cannot sync chat metadata", self.meet.meet_id, exc_info=True
            )
            return chat

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def add_message(
        self,
        role: str,
        content: str,
        user: str = "",
        *,
        meta_data: Optional[Dict[str, Any]] = None,
        done: bool = True,
        doc_id: Optional[str] = None,
        create: bool = True,
    ) -> Optional[Message]:
        """Append a message to the meeting chat and return it."""
        text = (content or "").strip()
        if not text:
            return None
        chat = self.existing_chat()
        if chat is None and create:
            chat = self.ensure_chat()
        if chat is None:
            return None
        message = Message(
            role=role,
            content=text,
            user=user or (MEETING_USER if role == "assistant" else ""),
            done=done,
            doc_id=doc_id or str(uuid.uuid4()),
            meta_data={
                **dict(meta_data or {}),
                "meeting_id": self.meet.meet_id,
            },
        )
        saved = self._manager().add_message(chat=chat, message=message)
        if saved is not None and getattr(message, "doc_id", None):
            if message.doc_id not in self.meet.message_ids:
                self.meet.message_ids.append(message.doc_id)
        return message

    def add_notice(self, text: str, notice: str = "notice") -> Optional[Message]:
        """Add an assistant notice to the meeting chat."""
        return self.add_message(
            "assistant",
            text,
            MEETING_USER,
            meta_data={MEETING_NOTICE_KEY: notice},
        )

    def add_user_utterance(
        self,
        speaker: str,
        text: str,
        *,
        source: str = "speech",
        client_message_id: Optional[str] = None,
    ) -> Optional[Message]:
        """Store a human utterance in the meeting chat."""
        meta = {
            "meeting_speaker": speaker,
            "meeting_speaker_kind": "user",
            "meeting_source": source,
        }
        if client_message_id:
            meta["meeting_client_message_id"] = client_message_id
        return self.add_message(
            "user",
            text,
            speaker,
            meta_data=meta,
            doc_id=client_message_id or None,
        )

    def add_profile_answer(
        self,
        profile_name: str,
        text: str,
        *,
        turn_id: str,
        interrupted: bool = False,
    ) -> Optional[Message]:
        """Store an AI profile answer in the meeting chat."""
        return self.add_message(
            "assistant",
            text,
            profile_name,
            meta_data={
                "meeting_speaker": profile_name,
                "meeting_speaker_kind": "profile",
                "meeting_turn_id": turn_id,
                "meeting_interrupted": bool(interrupted),
            },
            doc_id=turn_id,
        )
