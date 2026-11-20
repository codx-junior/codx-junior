"""
Business layer of a meeting.

The service connects the in-memory :class:`~codx.junior.meets.meet_manager.MeetManager`,
the durable :class:`~codx.junior.meets.store.MeetStore`, the meeting chat and the
AI agents. The API router is a thin HTTP layer over it, and the socket handler
uses it too when a message of a meeting chat is sent from the normal chat
composer.

Responsibilities
----------------
* create the associated chat and mirror the meeting state into it,
* record utterances in the transcript and in the chat,
* orchestrate the profile answers (one speaker at a time, human barge-in wins),
* generate notes/minutes asynchronously,
* create/schedule/run tasks from the minutes,
* store the recording.

Everything that changes the meeting goes through :meth:`_save`, so a reload or
a restart never loses the transcript/minutes. Socket emission is best-effort:
a disconnected client must never break the meeting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codx.junior.meets.agents import (
    generate_minutes as agent_generate_minutes,
    generate_profile_answer,
    speech_text,
)
from codx.junior.meets.chat_bridge import MeetingChatBridge
from codx.junior.meets.meet_manager import get_meet_manager
from codx.junior.meets.model import (
    Meet,
    MeetActionItem,
    MeetMinutes,
    MeetParticipant,
    MeetRecording,
    MeetStatus,
    MeetTranscriptEntry,
    ParticipantKind,
    ParticipantStatus,
    utc_now,
)
from codx.junior.meets.orchestrator import get_orchestrator
from codx.junior.meets.profiles import participant_snapshot
from codx.junior.meets.store import MeetStore, MeetStoreError

logger = logging.getLogger(__name__)

MEET_EVENT = "codx-junior-meet"

#: Utterances longer than this are clipped before entering the transcript.
MAX_UTTERANCE_CHARS = 8000

#: How long a single profile answer may be spoken (seconds).
MAX_SPEECH_SECONDS = 50.0

#: Minimum pause between two profile answers (seconds).
SPEECH_GAP_SECONDS = 0.8


def _now() -> str:
    """Current UTC instant."""
    return datetime.now(timezone.utc).isoformat()


def _seconds_for_speech(text: str) -> float:
    """Estimate the playback duration of ``text``."""
    words = len(str(text or "").split())
    return min(MAX_SPEECH_SECONDS, max(2.5, words / 2.6 + SPEECH_GAP_SECONDS))


async def _default_emit(event: str, data: dict) -> None:
    """Broadcast a Socket.IO event (best effort)."""
    try:
        from codx.junior.sio.sio import sio_send_event

        await sio_send_event(event, data)
    except Exception as ex:  # noqa: BLE001 - a meeting never depends on the socket
        logger.debug("Meeting: could not emit '%s': %s", event, ex)


def event_payload(meet: Meet, *, compact: bool = False, **extra) -> Dict[str, Any]:
    """Build the standard socket/REST payload of a meeting.

    ``compact`` is used for the frequent socket events (floor, profile turn,
    transcript): the transcript can grow to hundreds of entries and re-sending
    it on every event would exhaust the socket frame budget. The client
    receives each transcript entry separately and can always reload the full
    meeting document from the REST endpoint.
    """
    excluded = {"signals"}
    if compact:
        excluded.update({"transcript", "message_ids", "profile_turns"})
    payload = meet.model_dump(mode="json", exclude=excluded)
    # Aliases used by the existing frontend meeting store.
    payload["meetId"] = meet.meet_id
    payload["profileParticipants"] = meet.profile_participants()
    payload["participantsUsers"] = meet.participant_usernames()
    payload.update(extra)
    return payload


class MeetingService:
    """Meeting operations for one project session."""

    def __init__(
        self,
        session: Any,
        *,
        manager=None,
        store: Optional[MeetStore] = None,
        emit=None,
    ):
        """
        Args:
            session: The request's project ``CODXJuniorSession``.
            manager: Override the process-wide meet registry (tests).
            store: Override the durable store (tests).
            emit: Async ``(event, data)`` emitter (tests/socket).
        """
        self.session = session
        self.manager = manager or get_meet_manager()
        self._store = store
        self._emit = emit or _default_emit
        self._minutes_tasks: Dict[str, asyncio.Task] = {}
        self._orchestrator = get_orchestrator()

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    @property
    def settings(self) -> Any:
        """Project settings of the session."""
        return getattr(self.session, "settings", None)

    def _get_store(self) -> Optional[MeetStore]:
        """Lazily resolve the durable store (``None`` without a project)."""
        if self._store is not None:
            return self._store
        if self.settings is None:
            return None
        try:
            self._store = MeetStore.for_settings(self.settings)
        except MeetStoreError:
            logger.debug("Meeting: no durable store for this session")
            self._store = None
        return self._store

    def _store_for(self, meet: Meet) -> Optional[MeetStore]:
        """Durable store of the project that owns ``meet``.

        A meeting may be joined from another project (company mode). The
        artifacts must always be written to the owner project, otherwise the
        minutes/recording would be duplicated in the guest's project.
        """
        owner_project_id = getattr(meet, "project_id", None)
        current_project_id = getattr(self.settings, "project_id", None)
        if owner_project_id and owner_project_id != current_project_id:
            try:
                from codx.junior.project.project_discover import find_project_by_id

                owner_settings = find_project_by_id(owner_project_id)
                if owner_settings is not None:
                    return MeetStore.for_settings(owner_settings)
            except Exception:  # noqa: BLE001 - fall back to the request project
                logger.debug(
                    "Meeting: cannot resolve the owner project '%s'",
                    owner_project_id,
                    exc_info=True,
                )
        return self._get_store()

    async def emit(self, event: str, data: dict) -> None:
        """Emit a socket event (best effort)."""
        try:
            await self._emit(event, data)
        except Exception as ex:  # noqa: BLE001
            logger.debug("Meeting: emit failed: %s", ex)

    async def emit_meet(self, event_type: str, meet: Meet, **extra) -> None:
        """Emit one ``codx-junior-meet`` lifecycle/artifact event."""
        await self.emit(
            MEET_EVENT,
            {
                "type": event_type,
                "meetId": meet.meet_id,
                "meet": event_payload(meet, compact=True, **extra),
                **extra,
            },
        )

    def get_meet(self, meet_id: str, *, register_loaded: bool = True) -> Optional[Meet]:
        """Resolve a live or persisted meeting."""
        meet = self.manager.get_meet(meet_id)
        if meet is not None:
            return meet
        store = self._get_store()
        if store is None:
            return None
        loaded = store.load(meet_id)
        if loaded is not None and register_loaded:
            self.manager.register(loaded)
        return loaded

    def require_meet(self, meet_id: str) -> Meet:
        """Return a meeting or raise ``KeyError``."""
        meet = self.get_meet(meet_id)
        if meet is None:
            raise KeyError(meet_id)
        return meet

    def persist(self, meet: Meet) -> Meet:
        """Public alias of :meth:`_save` for the API layer."""
        return self._save(meet)

    def _save(self, meet: Meet) -> Meet:
        """Persist the meeting document (best effort)."""
        store = self._store_for(meet)
        if store is not None:
            try:
                store.save(meet)
            except Exception as ex:  # noqa: BLE001 - in-memory state is still valid
                logger.warning("Meeting '%s': cannot persist: %s", meet.meet_id, ex)
        return meet

    def _session_for(self, meet: Meet):
        """Session of the project that owns ``meet`` (guest joins allowed)."""
        owner_project_id = getattr(meet, "project_id", None)
        current_project_id = getattr(self.settings, "project_id", None)
        if not owner_project_id or owner_project_id == current_project_id:
            return self.session
        try:
            switched = self.session.switch_project(project_id=owner_project_id)
            if switched is not None:
                return switched
        except Exception:  # noqa: BLE001 - fall back to the request project
            logger.debug(
                "Meeting: cannot switch to the owner project '%s'",
                owner_project_id,
                exc_info=True,
            )
        return self.session

    def _bridge(self, meet: Meet) -> MeetingChatBridge:
        """Chat bridge of ``meet`` (in the project that owns it)."""
        return MeetingChatBridge(self._session_for(meet), meet)

    # ------------------------------------------------------------------
    # Creation / chat
    # ------------------------------------------------------------------
    def ensure_chat(self, meet: Meet, name: str = "") -> Optional[Any]:
        """Create/load the associated chat and persist the link."""
        if self.session is None or self.settings is None:
            return None
        try:
            chat = self._bridge(meet).ensure_chat(name=name)
            self.manager.set_chat_id(meet.meet_id, chat.id)
            self._save(meet)
            return chat
        except Exception as ex:  # noqa: BLE001 - a meeting can run without a chat
            logger.warning("Meeting '%s': cannot create its chat: %s", meet.meet_id, ex)
            return None

    async def start_meeting(self, meet: Meet, *, chat_name: str = "") -> Optional[Any]:
        """Create the chat, add the opening notice and announce the meeting."""
        chat = self.ensure_chat(meet, name=chat_name)
        bridge = self._bridge(meet)
        if chat is not None and not meet.transcript:
            people = ", ".join(
                [p.name or p.username for p in meet.participants]
            ) or "nobody yet"
            lines = [f"Meeting **{meet.name}** started."]
            if meet.agenda:
                lines.append(f"Agenda: {meet.agenda}")
            lines.append(f"Participants: {people}")
            if meet.profile_usernames():
                lines.append(
                    "AI profiles in the room: " + ", ".join(meet.profile_usernames())
                )
            try:
                bridge.add_notice("\n\n".join(lines), notice="started")
            except Exception:  # noqa: BLE001
                logger.debug("Meeting: could not add the start notice", exc_info=True)
        self._save(meet)
        await self.emit_meet("started", meet, chatId=meet.chat_id)
        return chat

    async def add_participants(
        self,
        meet: Meet,
        *,
        users: Optional[List[str]] = None,
        profiles: Optional[List[Any]] = None,
        notify: bool = True,
    ) -> Meet:
        """Invite humans and add AI profiles to a running meeting."""
        added_users: List[str] = []
        for username in users or []:
            name = str(username or "").strip()
            if not name or name in added_users:
                continue
            self.manager.add_participant(
                meet.meet_id,
                username=name,
                kind=ParticipantKind.USER,
                status=ParticipantStatus.INVITED,
                invited=True,
            )
            added_users.append(name)

        added_profiles: List[MeetParticipant] = []
        for profile in profiles or []:
            if isinstance(profile, dict):
                name = str(
                    profile.get("profile_name")
                    or profile.get("profile")
                    or profile.get("name")
                    or profile.get("username")
                    or ""
                ).strip()
            else:
                name = str(profile or "").strip()
            if not name:
                continue
            snapshot = participant_snapshot(self._session_for(meet), name)
            if snapshot is None:
                logger.warning("Meeting: unknown profile '%s' ignored", name)
                continue
            self.manager.add_participant(
                meet.meet_id,
                username=snapshot["profile_name"],
                name=snapshot["name"],
                avatar=snapshot["avatar"],
                kind=ParticipantKind.PROFILE,
                profile_name=snapshot["profile_name"],
                project_id=snapshot.get("project_id"),
                voice=snapshot.get("voice"),
                status=ParticipantStatus.JOINED,
            )
            participant = meet.find_participant(snapshot["profile_name"])
            if participant is not None:
                added_profiles.append(participant)

        bridge = self._bridge(meet)
        bridge.sync_chat()
        self._save(meet)
        if added_profiles:
            try:
                bridge.add_notice(
                    "Joined the meeting: "
                    + ", ".join(p.name or p.username for p in added_profiles),
                    notice="participants-added",
                )
            except Exception:  # noqa: BLE001
                pass
        await self.emit_meet(
            "participants",
            meet,
            addedUsers=added_users,
            addedProfiles=[p.model_dump(mode="json") for p in added_profiles],
            invitedUsers=meet.invited,
        )
        if notify and added_users:
            await self.emit(
                MEET_EVENT,
                {
                    "type": "participant-invited",
                    "meetId": meet.meet_id,
                    "meet": event_payload(meet),
                    "users": added_users,
                    "from": meet.initiator,
                    "fromName": meet.name,
                },
            )
        return meet

    # ------------------------------------------------------------------
    # Utterances / transcript
    # ------------------------------------------------------------------
    def _already_recorded(self, meet: Meet, client_message_id: Optional[str]) -> bool:
        """True when ``client_message_id`` is already in the transcript."""
        if not client_message_id:
            return False
        return any(
            entry.message_id == client_message_id for entry in meet.transcript
        )

    async def record_utterance(
        self,
        meet: Meet,
        *,
        text: str,
        speaker: str,
        kind: ParticipantKind = ParticipantKind.USER,
        source: str = "speech",
        client_message_id: Optional[str] = None,
        auto_respond: Optional[bool] = None,
        chat_message=None,
    ) -> Optional[MeetTranscriptEntry]:
        """
        Record one utterance in the transcript and in the meeting chat.

        Args:
            meet: Meeting that receives the utterance.
            text: What was said.
            speaker: Username/profile name.
            kind: Human or AI speaker.
            source: ``speech``, ``chat``, ``system`` or ``note``.
            client_message_id: Idempotency key; when a chat message was already
                stored, its ``doc_id``.
            auto_respond: Override the meeting setting.
            chat_message: Existing chat message (do not add it again).

        Returns:
            The transcript entry, or ``None`` for an empty/duplicate utterance.
        """
        clean = str(text or "").strip()
        if not clean:
            return None
        if self._already_recorded(meet, client_message_id):
            return None
        clean = clean[:MAX_UTTERANCE_CHARS]
        message = chat_message
        if message is None:
            try:
                message = self._bridge(meet).add_user_utterance(
                    speaker,
                    clean,
                    source=source,
                    client_message_id=client_message_id,
                )
            except Exception as ex:  # noqa: BLE001 - transcript still works
                logger.warning("Meeting: cannot add the utterance to its chat: %s", ex)
        entry = MeetTranscriptEntry(
            entry_id=str(uuid.uuid4()),
            at=utc_now(),
            speaker=speaker,
            speaker_kind=kind,
            text=clean,
            message_id=getattr(message, "doc_id", None) or client_message_id,
            source=source,
        )
        self.manager.append_transcript(meet.meet_id, entry)
        self._save(meet)
        await self.emit_meet(
            "transcript",
            meet,
            entry=entry.model_dump(mode="json"),
        )
        should_respond = (
            meet.settings.auto_respond if auto_respond is None else bool(auto_respond)
        )
        if should_respond and source != "note" and kind == ParticipantKind.USER:
            self.schedule_profile_turns(meet, clean)
        if meet.settings.live_notes and kind == ParticipantKind.USER:
            try:
                asyncio.create_task(self._maybe_live_notes(meet.meet_id))
            except RuntimeError:  # no running loop (tests / sync callers)
                pass
        return entry

    async def handle_chat_message(self, chat: Any, message: Any) -> Optional[MeetTranscriptEntry]:
        """
        Record a message sent from the normal chat composer to a meeting chat.

        The message is already persisted by ``POST /api/chats/message``; the
        service only needs to add it to the transcript (and trigger the profile
        answers). Assistant messages are ignored so the meeting does not record
        its own notices twice.
        """
        if chat is None or message is None:
            return None
        if str(getattr(chat, "mode", "")).lower() != "meet":
            return None
        meta = dict(getattr(chat, "meta_data", None) or {})
        meeting_id = (
            meta.get("meet_id")
            or meta.get("meeting_id")
            or (meta.get("meeting") or {}).get("meet_id")
        )
        if not meeting_id:
            return None
        meet = self.get_meet(str(meeting_id))
        if meet is None or not meet.is_active:
            return None
        if getattr(message, "role", "") != "user":
            return None
        doc_id = getattr(message, "doc_id", None)
        if doc_id and self._already_recorded(meet, doc_id):
            return None
        speaker = str(
            getattr(message, "user", "")
            or getattr(getattr(self.session, "user", None), "username", "")
            or "guest"
        )
        return await self.record_utterance(
            meet,
            text=str(getattr(message, "content", "") or ""),
            speaker=speaker,
            source="chat",
            client_message_id=doc_id,
            chat_message=message,
        )

    # ------------------------------------------------------------------
    # Profile turn orchestration
    # ------------------------------------------------------------------
    def _profile_participants(self, meet: Meet) -> List[MeetParticipant]:
        """Joined virtual participants of the meeting."""
        return [
            participant
            for participant in meet.participants
            if participant.kind == ParticipantKind.PROFILE
            and participant.status != ParticipantStatus.LEFT
        ]

    @staticmethod
    def _mentioned_profiles(meet: Meet, text: str) -> List[str]:
        """Profile names explicitly mentioned in ``text``."""
        lowered = (text or "").lower()
        mentioned: List[str] = []
        for participant in meet.participants:
            if participant.kind != ParticipantKind.PROFILE:
                continue
            name = (participant.profile_name or participant.username or "").strip()
            if not name:
                continue
            if (
                f"@{name.lower()}" in lowered
                or re.search(
                    rf"(?<![\w@]){re.escape(name.lower())}(?![\w])", lowered
                )
            ):
                mentioned.append(name)
        return mentioned

    def select_responders(self, meet: Meet, text: str) -> List[str]:
        """Choose which profiles answer a human turn (bounded)."""
        profiles = self._profile_participants(meet)
        if not profiles:
            return []
        limit = max(1, min(int(meet.settings.max_profile_turns or 1), len(profiles)))
        mentioned = self._mentioned_profiles(meet, text)
        if mentioned:
            return mentioned[:limit]
        lowered = (text or "").lower()
        for participant in profiles:
            name = (participant.profile_name or participant.username or "").lower()
            if name and f"@{name}" in lowered:
                return [participant.profile_name or participant.username]
        # Round robin: prefer the profiles that spoke least, so the whole team
        # participates instead of the same profile answering every turn.
        ordered = sorted(
            profiles,
            key=lambda participant: (
                int(meet.profile_turns.get(
                    participant.profile_name or participant.username, 0
                )),
                participant.name or participant.username,
            ),
        )
        return [
            participant.profile_name or participant.username
            for participant in ordered[:limit]
        ]

    def schedule_profile_turns(self, meet: Meet, text: str) -> bool:
        """Schedule the profile answers of one human turn."""
        responders = self.select_responders(meet, text)
        if not responders:
            return False
        meet_id = meet.meet_id

        async def runner(interrupt: asyncio.Event) -> None:
            await self._run_profile_turns(meet_id, responders, interrupt)

        return self._orchestrator.schedule(meet_id, runner)

    async def _wait_or_interrupt(
        self, event: asyncio.Event, timeout: float
    ) -> bool:
        """Wait ``timeout`` seconds; return True when interrupted."""
        if event.is_set():
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_profile_turns(
        self,
        meet_id: str,
        responders: List[str],
        interrupt: asyncio.Event,
    ) -> None:
        """Generate and "speak" one answer at a time, yielding to humans."""
        for profile_name in responders:
            meet = self.get_meet(meet_id)
            if meet is None or not meet.is_active:
                return
            if interrupt.is_set() or (
                meet.floor.holder and meet.floor.kind == ParticipantKind.USER
            ):
                return
            participant = meet.find_participant(profile_name)
            if participant is None or participant.status == ParticipantStatus.LEFT:
                continue
            # The profile takes the floor while it answers.
            self.manager.take_floor(
                meet_id, profile_name, kind=ParticipantKind.PROFILE
            )
            self._save(meet)
            await self.emit_meet(
                "floor",
                meet,
                holder=profile_name,
                kind=ParticipantKind.PROFILE.value,
                event="start",
            )

            generation = asyncio.create_task(
                generate_profile_answer(self._session_for(meet), meet, profile_name)
            )
            interrupt_wait = asyncio.create_task(interrupt.wait())
            try:
                done, pending = await asyncio.wait(
                    {generation, interrupt_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if interrupt_wait in done and interrupt_wait.result():
                    generation.cancel()
                    self.manager.release_floor(meet_id, profile_name)
                    self._save(meet)
                    await self.emit_meet(
                        "profile-turn",
                        meet,
                        profile=profile_name,
                        text="",
                        speech="",
                        interrupted=True,
                        messageId=None,
                    )
                    return
                result = generation.result()
            except asyncio.CancelledError:
                generation.cancel()
                raise
            finally:
                if not interrupt_wait.done():
                    interrupt_wait.cancel()

            if interrupt.is_set():
                self.manager.release_floor(meet_id, profile_name)
                self._save(meet)
                return
            if result.get("passed") or not result.get("text"):
                self.manager.release_floor(meet_id, profile_name)
                self._save(meet)
                await self.emit_meet(
                    "floor",
                    meet,
                    holder="",
                    kind=ParticipantKind.PROFILE.value,
                    event="end",
                )
                continue

            text = result["text"]
            speech = result.get("speech") or speech_text(
                text, max_chars=meet.settings.max_speech_chars
            )
            turn_id = str(uuid.uuid4())
            message = None
            try:
                message = self._bridge(meet).add_profile_answer(
                    profile_name,
                    text,
                    turn_id=turn_id,
                    interrupted=False,
                )
            except Exception as ex:  # noqa: BLE001
                logger.warning("Meeting: cannot store the profile answer: %s", ex)
            entry = MeetTranscriptEntry(
                entry_id=turn_id,
                at=utc_now(),
                speaker=profile_name,
                speaker_kind=ParticipantKind.PROFILE,
                text=text,
                message_id=getattr(message, "doc_id", None) or turn_id,
                source="profile",
            )
            self.manager.append_transcript(meet_id, entry)
            self.manager.record_profile_turn(meet_id, profile_name)
            self._save(meet)
            await self.emit_meet(
                "profile-turn",
                meet,
                profile=profile_name,
                text=text,
                speech=speech,
                interrupted=False,
                messageId=getattr(message, "doc_id", None) or turn_id,
                turnId=turn_id,
                voice=participant.voice,
            )
            # Keep the floor until the answer has been (roughly) spoken. A
            # human who starts talking sets the interrupt event immediately.
            interrupted = await self._wait_or_interrupt(
                interrupt, _seconds_for_speech(speech)
            )
            if interrupted:
                self.manager.mark_speaking_interrupted(meet_id, profile_name)
                self.manager.release_floor(meet_id, profile_name)
                self._save(meet)
                await self.emit_meet(
                    "profile-turn-end",
                    meet,
                    profile=profile_name,
                    turnId=turn_id,
                    interrupted=True,
                )
                return
            self.manager.release_floor(meet_id, profile_name)
            self._save(meet)
            await self.emit_meet(
                "profile-turn-end",
                meet,
                profile=profile_name,
                turnId=turn_id,
                interrupted=False,
            )
        # A live-notes refresh can follow an answer when enabled.
        await self._maybe_live_notes(meet_id)

    async def _maybe_live_notes(self, meet_id: str) -> None:
        """Refresh the notes during a long meeting (opt-in)."""
        meet = self.get_meet(meet_id)
        if meet is None or not meet.is_active or not meet.settings.live_notes:
            return
        generated = meet.minutes.generated_at or meet.minutes.updated_at
        if generated:
            try:
                age = time.time() - datetime.fromisoformat(
                    generated.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                age = 0
            if age < max(30, int(meet.settings.notes_interval_seconds)):
                return
        self.schedule_minutes(meet, instructions="", background=True)

    # ------------------------------------------------------------------
    # Floor / speaking
    # ------------------------------------------------------------------
    async def handle_floor(
        self,
        meet: Meet,
        *,
        event: str,
        kind: ParticipantKind,
        speaker: str,
    ) -> Meet:
        """Handle a client speech start/end notification."""
        if event == "start":
            if kind == ParticipantKind.USER:
                # A human takes the floor: interrupt the profile and mark the
                # current answer as cut short.
                previous = meet.floor
                if previous.holder and previous.kind == ParticipantKind.PROFILE:
                    self.manager.mark_speaking_interrupted(
                        meet.meet_id, previous.holder
                    )
                self.manager.take_floor(
                    meet.meet_id, speaker, kind=ParticipantKind.USER
                )
                self._orchestrator.interrupt(
                    meet.meet_id, reason=f"human {speaker} took the floor"
                )
            else:
                self.manager.take_floor(
                    meet.meet_id, speaker, kind=ParticipantKind.PROFILE
                )
            self._save(meet)
            await self.emit_meet(
                "floor",
                meet,
                holder=speaker,
                kind=kind.value,
                event="start",
            )
        elif event == "end":
            current = meet.floor
            if not current.holder or current.holder == speaker:
                self.manager.release_floor(meet.meet_id, speaker)
                self._save(meet)
                await self.emit_meet(
                    "floor",
                    meet,
                    holder="",
                    kind=kind.value,
                    event="end",
                )
        return meet

    # ------------------------------------------------------------------
    # Minutes / notes
    # ------------------------------------------------------------------
    def schedule_minutes(
        self,
        meet: Meet,
        *,
        instructions: str = "",
        background: bool = True,
    ) -> asyncio.Task:
        """Generate the minutes now or in the background."""
        existing = self._minutes_tasks.get(meet.meet_id)
        if existing is not None and not existing.done():
            return existing

        async def runner() -> None:
            try:
                await self.generate_minutes(meet, instructions=instructions)
            finally:
                self._minutes_tasks.pop(meet.meet_id, None)

        task = asyncio.create_task(runner())
        self._minutes_tasks[meet.meet_id] = task
        if not background:
            # The caller awaits the returned task explicitly.
            return task
        return task

    async def generate_minutes(self, meet: Meet, *, instructions: str = "") -> MeetMinutes:
        """Generate and store the minutes (async, safe to run in background)."""
        meet.minutes = MeetMinutes(
            status="generating",
            summary=meet.minutes.summary,
            key_points=list(meet.minutes.key_points),
            decisions=list(meet.minutes.decisions),
            action_items=list(meet.minutes.action_items),
            attendees=list(meet.minutes.attendees),
            updated_at=utc_now(),
        )
        self._save(meet)
        await self.emit_meet("minutes", meet, minutes=meet.minutes.model_dump(mode="json"))
        minutes = await agent_generate_minutes(
            self._session_for(meet), meet, instructions=instructions
        )
        now = utc_now()
        minutes.updated_at = now
        if minutes.status == "ready":
            minutes.generated_at = now
        self.manager.set_minutes(meet.meet_id, minutes)
        self._save(meet)
        await self.emit_meet(
            "minutes", meet, minutes=meet.minutes.model_dump(mode="json")
        )
        return minutes

    async def end_meeting(
        self,
        meet: Meet,
        *,
        generate_minutes: Optional[bool] = None,
    ) -> Meet:
        """End the meeting, persist it and (optionally) start the minutes."""
        self._orchestrator.cancel(meet.meet_id)
        self.manager.end_meet(meet.meet_id, username=meet.initiator)
        try:
            self._bridge(meet).sync_chat(status="ended")
            self._bridge(meet).add_notice(
                "The meeting ended. The minutes and the recording stay available "
                "in the meeting summary.",
                notice="ended",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Meeting: cannot close its chat", exc_info=True)
        self._save(meet)
        should_generate = (
            meet.settings.auto_minutes if generate_minutes is None else generate_minutes
        )
        if should_generate and meet.transcript:
            self.schedule_minutes(meet, background=True)
        return meet

    # ------------------------------------------------------------------
    # Tasks from the minutes
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_scheduled(value: str, timezone_name: Optional[str] = None) -> Optional[str]:
        """Resolve a scheduling expression to an ISO instant."""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            from codx.junior.tools.schedule_task_tool import _resolve_when

            return _resolve_when(text, timezone_name=timezone_name)
        except Exception:  # noqa: BLE001 - fall back to the generic parser
            try:
                from codx.junior.task_manager import to_wake_at

                return to_wake_at(text)
            except Exception:
                return None

    @staticmethod
    def _item_from_payload(value: Dict[str, Any]) -> MeetActionItem:
        """Build an action item from a request object."""
        return MeetActionItem(
            id=str(value.get("id") or uuid.uuid4()),
            title=str(value.get("title") or value.get("name") or "").strip(),
            description=str(
                value.get("description") or value.get("content") or ""
            ).strip(),
            assignee=str(value.get("assignee") or "").strip(),
            profile_name=str(
                value.get("profile") or value.get("profile_name") or ""
            ).strip(),
            priority=str(value.get("priority") or "medium").strip().lower(),
            due_date=(str(value.get("due_date")).strip() if value.get("due_date") else None),
            scheduled_at=(
                str(value.get("scheduled_at") or value.get("when")).strip()
                if value.get("scheduled_at") or value.get("when")
                else None
            ),
            run=bool(value.get("run", False)),
        )

    async def create_tasks(
        self,
        meet: Meet,
        *,
        items: Optional[List[Dict[str, Any]]] = None,
        item_ids: Optional[List[str]] = None,
        board: Optional[str] = None,
        column: Optional[str] = None,
        run: bool = False,
        schedule_at: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create tasks from the meeting's action items.

        Returns:
            ``{tasks: [...], meet: ...}``. The created task documents are chat
            payloads; the meeting minutes are updated with ``task_id`` and
            ``status='created'`` so creating twice never duplicates work.
        """
        from codx.junior.task_manager import TaskManager

        source_items: List[MeetActionItem] = []
        if items:
            source_items = [self._item_from_payload(value) for value in items]
        else:
            stored = list(meet.minutes.action_items)
            wanted = set(str(item) for item in (item_ids or []))
            source_items = [
                item
                for item in stored
                if item.status not in {"created", "dismissed"}
                and (not wanted or item.id in wanted)
            ]
        if not source_items:
            return {"tasks": [], "meet": meet, "error": "no action items to create"}

        owner_session = self._session_for(meet)
        try:
            manager = TaskManager(
                settings=owner_session.settings,
                chat_manager=owner_session.get_chat_manager(),
                event_manager=getattr(owner_session, "event_manager", None),
            )
        except Exception as ex:  # noqa: BLE001
            return {"tasks": [], "meet": meet, "error": str(ex)}

        created: List[Any] = []
        run_ids: List[str] = []
        for item in source_items:
            title = (item.title or "").strip()
            description = (item.description or "").strip() or title
            if not title:
                item.status = "error"
                item.error = "the action item has no title"
                continue
            item_board = board or (meet.board or "kanban")
            item_column = column or "tasks"
            when = item.scheduled_at or schedule_at
            wake_at = self._resolve_scheduled(when, timezone_name) if when else None
            try:
                task = manager.create_task(
                    name=title,
                    description=description,
                    content=description,
                    board=item_board,
                    column=item_column,
                    status="todo",
                    priority=item.priority or "medium",
                    due_date=item.due_date,
                    wake_at=wake_at,
                    assignees=[item.assignee] if item.assignee else [],
                    profiles=[item.profile_name] if item.profile_name else [],
                    tags=["meeting", meet.meet_id],
                    user=meet.initiator,
                )
            except Exception as ex:  # noqa: BLE001 - keep the other items going
                logger.warning(
                    "Meeting '%s': cannot create task '%s': %s",
                    meet.meet_id,
                    title,
                    ex,
                )
                item.status = "error"
                item.error = str(ex)
                continue
            item.status = "created"
            item.task_id = task.id
            item.error = None
            created.append(task)
            if run or item.run:
                run_ids.append(task.id)
            # Keep the item in the stored minutes, even when it came from the
            # request (so the summary reflects what was created).
            if not any(existing.id == item.id for existing in meet.minutes.action_items):
                meet.minutes.action_items.append(item)

        self._save(meet)
        for task_id in run_ids:
            asyncio.create_task(self._run_task(meet, task_id))
        if created:
            await self.emit_meet(
                "tasks-created",
                meet,
                tasks=[task.model_dump(mode="json") for task in created],
            )
        return {
            "tasks": [task.model_dump(mode="json") for task in created],
            "meet": meet,
            "run_ids": run_ids,
        }

    async def _run_task(self, meet: Meet, task_id: str) -> None:
        """Execute one created task through the normal chat pipeline."""
        try:
            await self._session_for(meet).chat_with_project(
                chat_id=task_id,
                owner_project_id=meet.project_id,
            )
        except Exception as ex:  # noqa: BLE001 - running is best effort
            logger.exception(
                "Meeting '%s': task '%s' failed to run: %s",
                meet.meet_id,
                task_id,
                ex,
            )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    async def save_recording(
        self,
        meet: Meet,
        *,
        content: bytes,
        filename: str,
        mime: str = "audio/webm",
        duration_seconds: float = 0.0,
        owner: str = "",
    ) -> MeetRecording:
        """Persist the uploaded recording and update the meeting."""
        store = self._store_for(meet)
        if store is None:
            raise MeetStoreError("the project has no meeting storage")
        self.manager.update_recording_fields(
            meet.meet_id,
            status="uploading",
            owner=owner or meet.initiator,
            mime=mime or "audio/webm",
        )
        self._save(meet)
        recording = store.save_recording(
            meet.meet_id,
            content,
            filename=filename,
            mime=mime,
            duration_seconds=duration_seconds,
        )
        recording.owner = owner or meet.initiator
        recording.started_at = meet.recording.started_at
        recording.ended_at = utc_now()
        self.manager.set_recording(meet.meet_id, recording)
        self._save(meet)
        await self.emit_meet(
            "recording",
            meet,
            recording=recording.model_dump(mode="json"),
        )
        return recording

    def recording_file(self, meet: Meet) -> Optional[str]:
        """Absolute path of the meeting recording."""
        store = self._store_for(meet)
        return store.recording_file(meet) if store is not None else None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def serialize(self, meet: Meet, chat: Any = None) -> Dict[str, Any]:
        """Payload returned by the REST endpoints."""
        payload = event_payload(meet)
        if chat is not None:
            payload["chat"] = MeetingChatBridge.serialize_chat(chat)
        payload["recording_url"] = (
            f"/api/meets/{meet.meet_id}/recording"
            if meet.recording.status == "ready"
            else None
        )
        return payload

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List the persisted meetings of the project."""
        store = self._get_store()
        if store is None:
            return []
        project_id = getattr(self.settings, "project_id", None)
        return store.list(limit=limit, project_id=project_id)
