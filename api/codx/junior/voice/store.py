"""Crash-safe persistence of voice conversation sessions.

Sessions live in one small JSON document inside the project's generated data
plane (``codx.junior.runtime.data_dir``) so ``CODX_JUNIOR_CODX_DATA_PATH`` can
move them out of the repository::

    <data_root>/voice/sessions.json
    <data_root>/voice/.voice.lock

Why a project document and not the chat itself
----------------------------------------------
The *conversation* is always the chat (messages are normal chat messages).
The session document only holds call state that must survive a reload and be
listable without scanning every task: the current chat, the trail of switched
chats, turn counters and a pending assistant-requested switch.

Conventions
-----------
* Atomic writes: temp file in the same folder -> ``flush`` -> ``fsync`` ->
  ``os.replace``. A crash never leaves half a document.
* Cross-process lock (``flock``) around every read-modify-write: the API runs
  several workers and a session can be touched from more than one request.
* Tolerant reads: a missing/corrupt file is logged and treated as empty. One
  broken document must never make the feature unusable.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from codx.junior.runtime import data_dir
from codx.junior.utils.file_lock import interprocess_lock

from .model import (
    VoiceChatRef,
    VoiceSession,
    VoiceSessionStatus,
    VoiceSwitchRequest,
    utc_now,
)

logger = logging.getLogger(__name__)

SESSIONS_FILENAME = "sessions.json"
LOCK_FILENAME = ".voice.lock"
STORE_VERSION = 1

#: Keep the session document bounded; active calls always survive pruning.
MAX_SESSIONS = 300


class VoiceSessionNotFound(KeyError):
    """Raised when a session id is unknown."""


class VoiceSessionStore:
    """Filesystem store of voice sessions for one project."""

    def __init__(self, root: Union[str, Path], max_sessions: int = MAX_SESSIONS):
        self.root = Path(root)
        self.sessions_path = self.root / SESSIONS_FILENAME
        self.lock_path = self.root / LOCK_FILENAME
        self.max_sessions = max(1, int(max_sessions))
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_settings(cls, settings) -> "VoiceSessionStore":
        """Store of the project described by ``settings``."""
        codx_path = getattr(settings, "codx_path", None)
        if not codx_path:
            raise ValueError("settings has no codx_path")
        # Generated data plane: honours CODX_JUNIOR_CODX_DATA_PATH and is never
        # committed (see runtime.DATA_PLANE_ENTRIES / .gitignore).
        return cls(data_dir(codx_path, "voice"))

    # ------------------------------------------------------------------ io
    def _read_sessions(self) -> Dict[str, VoiceSession]:
        try:
            payload = json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(raw_sessions, dict):
            return {}
        sessions: Dict[str, VoiceSession] = {}
        for session_id, data in raw_sessions.items():
            try:
                session = VoiceSession.model_validate(data)
            except Exception as ex:  # noqa: BLE001 - one bad record must not blank all
                logger.warning("Ignoring unreadable voice session '%s': %s", session_id, ex)
                continue
            sessions[session.session_id] = session
        return sessions

    def _write_sessions(self, sessions: Dict[str, VoiceSession]) -> None:
        payload = {
            "version": STORE_VERSION,
            "updated_at": utc_now(),
            "sessions": {
                session_id: session.model_dump(mode="json")
                for session_id, session in sessions.items()
            },
        }
        data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.root), prefix="sessions-", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.sessions_path)
        except BaseException:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    def _prune(self, sessions: Dict[str, VoiceSession]) -> Dict[str, VoiceSession]:
        """Bound the document, always keeping active calls."""
        if len(sessions) <= self.max_sessions:
            return sessions
        active = [s for s in sessions.values() if s.is_active]
        ended = sorted(
            (s for s in sessions.values() if not s.is_active),
            key=lambda s: s.updated_at,
            reverse=True,
        )
        keep = active + ended[: max(0, self.max_sessions - len(active))]
        return {session.session_id: session for session in keep}

    # ------------------------------------------------------------------ reads
    def get(self, session_id: str, required: bool = False) -> Optional[VoiceSession]:
        """Return a session by id (``None`` when unknown unless ``required``)."""
        if not session_id:
            return None
        session = self._read_sessions().get(str(session_id))
        if session is None and required:
            raise VoiceSessionNotFound(session_id)
        return session

    def list_sessions(
        self,
        status: Optional[str] = None,
        chat_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[VoiceSession]:
        """Sessions ordered by most recently updated first."""
        sessions = list(self._read_sessions().values())
        if status:
            wanted = str(status).lower()
            sessions = [s for s in sessions if s.status.value == wanted]
        if chat_id:
            sessions = [s for s in sessions if s.chat_id == str(chat_id)]
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions[: max(1, min(int(limit or 20), 200))]

    def active_for_chat(self, chat_id: str) -> Optional[VoiceSession]:
        """The active call of ``chat_id``, when there is one."""
        if not chat_id:
            return None
        for session in self._read_sessions().values():
            if session.chat_id == str(chat_id) and session.is_active:
                return session
        return None

    # ----------------------------------------------------------------- writes
    def create_or_resume(
        self,
        chat_id: str,
        owner_project_id: Optional[str] = None,
        language: str = "en-US",
        title: str = "",
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Tuple[VoiceSession, bool]:
        """Create a call, or continue an existing one.

        * ``session_id`` given: that session is reactivated (a call can be
          resumed after it ended) and attached to ``chat_id``. Its message
          history is kept.
        * no ``session_id`` but the chat already has an active call: that call
          is returned unchanged (starting twice is idempotent).
        * otherwise a brand new session is created.

        Returns:
            ``(session, created)`` where ``created`` is False for a resumed or
            already-active call.
        """
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id)) if session_id else None
            if session_id and session is None:
                raise VoiceSessionNotFound(session_id)

            if session is not None:
                created = False
                if session.status == VoiceSessionStatus.ENDED:
                    session.status = VoiceSessionStatus.ACTIVE
                    session.ended_at = None
                    session.resumptions += 1
                if session.chat_id != chat_id:
                    # Resuming directly into another chat is a move without the
                    # UI notices: the client asked for it explicitly.
                    self._move_session(session, chat_id, title)
                session.chat_id = chat_id
                if owner_project_id:
                    session.owner_project_id = owner_project_id
                if project_id:
                    session.project_id = project_id
                if language:
                    session.language = language
                if title:
                    session.title = title
                if meta:
                    session.meta.update(meta)
                session.pending_switch = None
                session.touch()
                sessions[session.session_id] = session
                self._write_sessions(self._prune(sessions))
                return session, created

            existing = next(
                (
                    candidate
                    for candidate in sessions.values()
                    if candidate.chat_id == chat_id and candidate.is_active
                ),
                None,
            )
            if existing is not None:
                if language:
                    existing.language = language
                if owner_project_id:
                    existing.owner_project_id = owner_project_id
                existing.touch()
                self._write_sessions(self._prune(sessions))
                return existing, False

            now = utc_now()
            session = VoiceSession(
                session_id=f"vs-{uuid.uuid4().hex[:16]}",
                chat_id=chat_id,
                title=title or "Voice conversation",
                owner_project_id=owner_project_id,
                project_id=project_id,
                status=VoiceSessionStatus.ACTIVE,
                language=language or "en-US",
                created_at=now,
                updated_at=now,
                chats=[VoiceChatRef(chat_id=chat_id, name=title or "", since=now)],
                meta=dict(meta or {}),
            )
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            logger.info("Voice session '%s' started on chat '%s'", session.session_id, chat_id)
            return session, True

    @staticmethod
    def _move_session(session: VoiceSession, chat_id: str, chat_name: str = "") -> None:
        """Close the current chat ref and open the new one (in memory only)."""
        now = utc_now()
        for ref in session.chats:
            if ref.until is None:
                ref.until = now
        session.chat_id = str(chat_id)
        session.chats.append(
            VoiceChatRef(chat_id=str(chat_id), name=chat_name or "", since=now)
        )
        ref = session.current_chat_ref()
        if ref is not None and chat_name and not ref.name:
            ref.name = chat_name

    def set_language(self, session_id: str, language: str) -> VoiceSession:
        """Change the speech language of a session (active or ended)."""
        value = str(language or "").strip()
        if not value:
            raise ValueError("language is required")
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            session.language = value
            session.touch()
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            return session

    def switch(
        self,
        session_id: str,
        chat_id: str,
        chat_name: str = "",
    ) -> VoiceSession:
        """Move an active call to another chat and clear any pending switch."""
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            if session.chat_id != str(chat_id):
                self._move_session(session, chat_id, chat_name)
            elif chat_name:
                ref = session.current_chat_ref()
                if ref is not None:
                    ref.name = chat_name
            if chat_name:
                # The list shows where the call is now, not where it started.
                session.title = chat_name
            session.pending_switch = None
            session.status = VoiceSessionStatus.ACTIVE
            session.ended_at = None
            session.touch()
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            logger.info("Voice session '%s' switched to chat '%s'", session_id, chat_id)
            return session

    def end(self, session_id: str, reason: str = "") -> VoiceSession:
        """Mark a call as ended (idempotent)."""
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            if session.is_active:
                session.status = VoiceSessionStatus.ENDED
                session.ended_at = utc_now()
                if reason:
                    session.meta["end_reason"] = reason
                session.pending_switch = None
                session.touch()
                sessions[session.session_id] = session
                self._write_sessions(self._prune(sessions))
                logger.info("Voice session '%s' ended (%s)", session_id, reason or "no reason")
            return session

    def request_switch(
        self,
        session_id: str,
        chat_id: str,
        chat_name: str = "",
        reason: str = "",
        requested_by: str = "assistant",
    ) -> VoiceSession:
        """Store a switch the assistant wants the client to apply."""
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            if not session.is_active:
                raise ValueError("voice session is not active")
            session.pending_switch = VoiceSwitchRequest(
                chat_id=str(chat_id),
                chat_name=chat_name or "",
                reason=reason or "",
                requested_by=requested_by or "assistant",
            )
            session.touch()
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            return session

    def add_message_ids(self, session_id: str, *message_ids: Optional[str]) -> VoiceSession:
        """Append message ids to the call trail, without counting a turn."""
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            session.add_message_ids(*message_ids)
            session.touch()
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            return session

    def record_turn(
        self,
        session_id: str,
        user_message_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        user_text: str = "",
        assistant_text: str = "",
        at: Optional[str] = None,
    ) -> VoiceSession:
        """Record the chat messages of one completed speech turn."""
        with interprocess_lock(str(self.lock_path)):
            sessions = self._read_sessions()
            session = sessions.get(str(session_id))
            if session is None:
                raise VoiceSessionNotFound(session_id)
            session.add_message_ids(user_message_id, assistant_message_id)
            session.turn_count += 1
            if user_text:
                session.last_user_text = user_text[:2000]
                if not session.title or session.title == "Voice conversation":
                    session.title = user_text[:80]
            if assistant_text:
                session.last_assistant_text = assistant_text[:4000]
            if at:
                session.meta["last_turn_at"] = at
            else:
                session.meta["last_turn_at"] = utc_now()
            session.touch()
            sessions[session.session_id] = session
            self._write_sessions(self._prune(sessions))
            return session
