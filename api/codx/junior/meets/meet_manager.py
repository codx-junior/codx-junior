"""
MeetManager – in-memory registry and lifecycle controller for meetings.

The manager is a process-wide singleton so that meet state survives across the
short-lived ``CODXJuniorSession`` objects created per HTTP request. Meets are
cross-project by nature (a call may involve users browsing different projects);
a ``project_id`` is stored when available so listings can be scoped.

The manager owns the live state of a meeting: participants (humans and virtual
AI profiles), the transcript, the current floor, the notes/minutes and the
recording reference. Durable persistence is a separate concern (see
:mod:`codx.junior.meets.store`) so tests can drive the manager without writing
to disk.

Diagram:
```mermaid
flowchart TD
    IM[initiate_meet] --> REG[(meets registry)]
    JM[join_meet] --> REG
    LM[leave_meet] --> REG
    EM[end_meet] --> REG
    GS[get_meet / list_meets] --> REG
    AS[add_signal] --> REG
    TR[transcript / minutes / floor] --> REG
```

Made with ❤️ by codx-junior
"""

import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from codx.junior.meets.model import (
    Meet,
    MeetActionItem,
    MeetFloorState,
    MeetMinutes,
    MeetParticipant,
    MeetRecording,
    MeetSettings,
    MeetSignal,
    MeetStatus,
    MeetTranscriptEntry,
    ParticipantKind,
    ParticipantStatus,
    utc_now,
)

logger = logging.getLogger(__name__)

# Maximum number of signalling messages kept per meet (oldest are dropped).
MAX_SIGNALS_PER_MEET = 500

# Maximum transcript entries kept in memory. The meeting chat remains the full
# record; this cap only bounds the in-memory document of a very long meeting.
MAX_TRANSCRIPT_ENTRIES = 2000


class MeetManager:
    """
    Thread-safe in-memory store for meetings.

    All mutating methods acquire an internal ``RLock``; the class itself is a
    lazily created singleton accessed through :meth:`get_instance`.
    """

    _instance: Optional["MeetManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._meets: Dict[str, Meet] = {}
        self._lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "MeetManager":
        """Return the process-wide singleton instance."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _require_meet(self, meet_id: str) -> Meet:
        """Return the meet or raise ``KeyError`` when it does not exist."""
        meet = self._meets.get(meet_id)
        if not meet:
            raise KeyError(meet_id)
        return meet

    def _require_active(self, meet_id: str) -> Meet:
        """Return the meet or raise ``ValueError`` when it is not active."""
        meet = self._require_meet(meet_id)
        if meet.status != MeetStatus.ACTIVE:
            raise ValueError(f"Meet '{meet_id}' is not active")
        return meet

    @staticmethod
    def _clean_name(value) -> str:
        """Normalize a profile name (no ``@`` prefix, trimmed)."""
        return str(value or "").strip().lstrip("@").strip()

    @classmethod
    def _normalize_invited(cls, participants) -> List[str]:
        """Normalize the ``participants`` payload into human usernames."""
        invited: List[str] = []
        for participant in participants or []:
            if isinstance(participant, str):
                invited.append(participant)
            elif isinstance(participant, dict):
                kind = str(
                    participant.get("kind") or participant.get("type") or ""
                ).lower()
                if kind == ParticipantKind.PROFILE.value or participant.get(
                    "profile_name"
                ) or participant.get("profile"):
                    continue
                value = (
                    participant.get("username")
                    or participant.get("name")
                    or participant.get("id")
                )
                if value:
                    invited.append(str(value))
        return [name for name in dict.fromkeys(invited) if name]

    @classmethod
    def _profile_specs(cls, participants=None) -> List[Dict[str, str]]:
        """Extract profile participant specs from the initiate payload."""
        specs: List[Dict[str, str]] = []
        for participant in participants or []:
            if isinstance(participant, str):
                continue
            if not isinstance(participant, dict):
                continue
            kind = str(
                participant.get("kind") or participant.get("type") or ""
            ).lower()
            profile_name = cls._clean_name(
                participant.get("profile_name")
                or participant.get("profile")
                or (participant.get("username") if kind == "profile" else "")
                or (participant.get("name") if kind == "profile" else "")
            )
            if kind != ParticipantKind.PROFILE.value and not profile_name:
                continue
            if not profile_name:
                continue
            specs.append(
                {
                    "profile_name": profile_name,
                    "name": str(participant.get("name") or profile_name),
                    "avatar": str(participant.get("avatar") or ""),
                    "project_id": str(participant.get("project_id") or ""),
                }
            )
        return specs

    def _upsert_participant(
        self,
        meet: Meet,
        username: str = "",
        sid: Optional[str] = None,
        name: str = "",
        avatar: str = "",
        is_initiator: bool = False,
        kind: ParticipantKind = ParticipantKind.USER,
        profile_name: str = "",
        project_id: Optional[str] = None,
        voice: Optional[dict] = None,
        status: ParticipantStatus = ParticipantStatus.JOINED,
    ) -> MeetParticipant:
        """Find an existing participant (by username/profile/sid) or create one."""
        profile_name = self._clean_name(profile_name)
        lookup_name = profile_name or username
        existing: Optional[MeetParticipant] = None
        for participant in meet.participants:
            if lookup_name and (
                participant.username == lookup_name
                or participant.profile_name == lookup_name
            ):
                existing = participant
                break
            if sid and participant.sid == sid:
                existing = participant
                break

        if existing is None:
            existing = MeetParticipant(
                sid=sid,
                username=lookup_name,
                name=name or lookup_name,
                avatar=avatar,
                is_initiator=is_initiator,
                kind=kind,
                profile_name=profile_name,
                project_id=project_id or meet.project_id,
                voice=voice,
                status=status,
            )
            meet.participants.append(existing)
        else:
            existing.kind = kind or existing.kind
            if sid:
                existing.sid = sid
            if name:
                existing.name = name
            if avatar:
                existing.avatar = avatar
            if profile_name:
                existing.profile_name = profile_name
            if project_id:
                existing.project_id = project_id
            if voice:
                existing.voice = voice
            if status != ParticipantStatus.INVITED:
                existing.status = status
            existing.updated_at = utc_now()

        if is_initiator:
            existing.is_initiator = True
        meet.touch()
        return existing

    def _apply_init_config(
        self,
        meet: Meet,
        *,
        name: str = "",
        agenda: str = "",
        project_id: Optional[str] = None,
        board: Optional[str] = None,
        task_ids=None,
        source: str = "manual",
        channel_id: Optional[str] = None,
        channel_name: str = "",
        team_id: Optional[str] = None,
        settings: Optional[MeetSettings] = None,
    ) -> None:
        """Apply the mutable configuration of a (re)activated meeting."""
        if name:
            meet.name = name
        if agenda:
            meet.agenda = agenda
        if project_id:
            meet.project_id = project_id
        if board:
            meet.board = board
        if task_ids:
            meet.task_ids = list(dict.fromkeys(str(item) for item in task_ids if item))
        if source:
            meet.source = source
        meet.channel_id = channel_id or meet.channel_id
        meet.channel_name = channel_name or meet.channel_name
        meet.team_id = team_id or meet.team_id
        if settings is not None:
            meet.settings = settings
        meet.touch()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def initiate_meet(
        self,
        meet_id: Optional[str] = None,
        name: str = "",
        initiator: str = "",
        sid: Optional[str] = None,
        avatar: str = "",
        participants=None,
        project_id: Optional[str] = None,
        profiles=None,
        invited_users=None,
        agenda: str = "",
        board: Optional[str] = None,
        task_ids=None,
        channel_id: Optional[str] = None,
        channel_name: str = "",
        team_id: Optional[str] = None,
        source: str = "manual",
        settings: Optional[MeetSettings] = None,
    ) -> Meet:
        """
        Create a new meet or reactivate an existing ended one.

        Args:
            meet_id: Client supplied id; a UUID is generated when empty.
            name: Human readable meet name.
            initiator: Username of the participant creating the meet.
            sid: Socket.IO session id of the initiator.
            avatar: Avatar URL of the initiator.
            participants: Invited participants (usernames or objects). Objects
                with ``kind='profile'`` are virtual AI participants.
            project_id: Optional owning project id.
            profiles: Additional profile participants (names or objects).
            invited_users: Additional human usernames to invite.
            agenda: Meeting agenda/objective.
            board: Board whose tasks the meeting reviews.
            task_ids: Specific task ids to review.
            channel_id/channel_name/team_id/source: Where the meeting started.
            settings: Meeting behaviour overrides.

        Returns:
            The created/reactivated :class:`Meet`.
        """
        meet_id = meet_id or str(uuid.uuid4())
        with self._lock:
            meet = self._meets.get(meet_id)
            if not meet or meet.status == MeetStatus.ENDED:
                meet = Meet(
                    meet_id=meet_id,
                    name=name or f"Meet {meet_id[:8]}",
                    initiator=initiator,
                    project_id=project_id,
                    settings=settings or MeetSettings(),
                )
                self._meets[meet_id] = meet
            self._apply_init_config(
                meet,
                name=name or f"Meet {meet_id[:8]}",
                agenda=agenda,
                project_id=project_id,
                board=board,
                task_ids=task_ids,
                source=source,
                channel_id=channel_id,
                channel_name=channel_name,
                team_id=team_id,
                settings=settings,
            )

            invited = self._normalize_invited(participants)
            specs: List[Dict[str, Any]] = []
            for profile in profiles or []:
                if isinstance(profile, dict):
                    profile_name = self._clean_name(
                        profile.get("profile_name")
                        or profile.get("profile")
                        or profile.get("name")
                        or profile.get("username")
                    )
                    if profile_name:
                        specs.append(
                            {
                                "profile_name": profile_name,
                                "name": str(profile.get("name") or profile_name),
                                "avatar": str(profile.get("avatar") or ""),
                                "project_id": str(profile.get("project_id") or ""),
                                "voice": profile.get("voice"),
                            }
                        )
                else:
                    profile_name = self._clean_name(profile)
                    if profile_name:
                        specs.append(
                            {
                                "profile_name": profile_name,
                                "name": profile_name,
                                "avatar": "",
                                "project_id": "",
                                "voice": None,
                            }
                        )
            specs.extend(self._profile_specs(participants))
            for username in invited_users or []:
                invited.append(str(username))
            for spec in specs:
                # The profile object may carry the resolved display name,
                # avatar and voice of the project's profile.
                self._upsert_participant(
                    meet,
                    kind=ParticipantKind.PROFILE,
                    profile_name=spec["profile_name"],
                    username=spec["profile_name"],
                    name=spec.get("name") or spec["profile_name"],
                    avatar=spec.get("avatar") or "",
                    project_id=spec.get("project_id") or project_id,
                    voice=spec.get("voice"),
                    status=ParticipantStatus.JOINED,
                )
            invite_profiles = [spec["profile_name"] for spec in specs]

            meet.invited = list(dict.fromkeys(invited + list(meet.invited)))
            meet.invited_profiles = list(
                dict.fromkeys(invite_profiles + list(meet.invited_profiles))
            )
            meet.updated_at = utc_now()
            self._upsert_participant(
                meet,
                username=initiator,
                sid=sid,
                avatar=avatar,
                is_initiator=True,
                kind=ParticipantKind.USER,
                project_id=project_id,
                status=ParticipantStatus.JOINED,
            )
            logger.info(
                "Meet '%s' initiated by '%s' (invited=%s, profiles=%s)",
                meet.meet_id,
                initiator,
                meet.invited,
                meet.profile_usernames(),
            )
            return meet

    def join_meet(
        self,
        meet_id: str,
        username: str = "",
        sid: Optional[str] = None,
        name: str = "",
        avatar: str = "",
        project_id: Optional[str] = None,
    ) -> Meet:
        """Add a human participant to an active meet."""
        with self._lock:
            meet = self._require_active(meet_id)
            self._upsert_participant(
                meet,
                username=username,
                sid=sid,
                name=name,
                avatar=avatar,
                kind=ParticipantKind.USER,
                project_id=project_id,
                status=ParticipantStatus.JOINED,
            )
            meet.updated_at = utc_now()
            logger.info("'%s' joined meet '%s'", username or sid, meet_id)
            return meet

    def leave_meet(
        self, meet_id: str, username: str = "", sid: Optional[str] = None
    ) -> Meet:
        """Mark a human participant as left (its transcript stays)."""
        with self._lock:
            meet = self._require_meet(meet_id)
            for participant in meet.participants:
                if participant.kind != ParticipantKind.USER:
                    continue
                if (username and participant.username == username) or (
                    sid and participant.sid == sid
                ):
                    participant.status = ParticipantStatus.LEFT
                    participant.speaking = False
                    participant.updated_at = utc_now()
            if meet.floor.holder == username and meet.floor.kind == ParticipantKind.USER:
                meet.floor = MeetFloorState()
            meet.updated_at = utc_now()
            logger.info("'%s' left meet '%s'", username or sid, meet_id)
            return meet

    def end_meet(self, meet_id: str, username: str = "") -> Meet:
        """Mark a meet as ended."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.status = MeetStatus.ENDED
            meet.ended_at = utc_now()
            meet.updated_at = meet.ended_at
            meet.floor = MeetFloorState()
            for participant in meet.participants:
                participant.speaking = False
            logger.info("Meet '%s' ended by '%s'", meet_id, username)
            return meet

    # -------------------------------------------------------------------------
    # Participants
    # -------------------------------------------------------------------------

    def add_participant(
        self,
        meet_id: str,
        username: str = "",
        sid: Optional[str] = None,
        name: str = "",
        avatar: str = "",
        kind: ParticipantKind = ParticipantKind.USER,
        profile_name: str = "",
        project_id: Optional[str] = None,
        voice: Optional[dict] = None,
        status: ParticipantStatus = ParticipantStatus.JOINED,
        invited: bool = False,
    ) -> Meet:
        """Add or update one participant (human or AI profile)."""
        with self._lock:
            meet = self._require_meet(meet_id)
            clean_profile = self._clean_name(profile_name)
            self._upsert_participant(
                meet,
                username=clean_profile or username,
                sid=sid,
                name=name,
                avatar=avatar,
                kind=kind,
                profile_name=clean_profile,
                project_id=project_id,
                voice=voice,
                status=status,
            )
            if kind == ParticipantKind.PROFILE and clean_profile:
                if clean_profile not in meet.invited_profiles:
                    meet.invited_profiles.append(clean_profile)
            elif username and invited and username not in meet.invited:
                meet.invited.append(username)
            meet.updated_at = utc_now()
            return meet

    def remove_participant(
        self,
        meet_id: str,
        username: str = "",
        kind: ParticipantKind = ParticipantKind.USER,
    ) -> Meet:
        """Remove an invited participant/profile that never joined."""
        with self._lock:
            meet = self._require_meet(meet_id)
            wanted = self._clean_name(username) or username
            meet.participants = [
                participant
                for participant in meet.participants
                if not (
                    participant.kind == kind
                    and (
                        participant.username == wanted
                        or participant.profile_name == wanted
                    )
                    and participant.status == ParticipantStatus.INVITED
                )
            ]
            if kind == ParticipantKind.PROFILE:
                meet.invited_profiles = [
                    name for name in meet.invited_profiles if name != wanted
                ]
            else:
                meet.invited = [name for name in meet.invited if name != wanted]
            meet.updated_at = utc_now()
            return meet

    def set_participant_media(
        self,
        meet_id: str,
        username: str,
        *,
        audio: Optional[bool] = None,
        video: Optional[bool] = None,
        screen: Optional[bool] = None,
        muted: Optional[bool] = None,
        speaking: Optional[bool] = None,
    ) -> Meet:
        """Update the media flags of a participant."""
        with self._lock:
            meet = self._require_meet(meet_id)
            participant = meet.find_participant(username)
            if participant:
                for field, value in (
                    ("audio", audio),
                    ("video", video),
                    ("screen", screen),
                    ("muted", muted),
                    ("speaking", speaking),
                ):
                    if value is not None:
                        setattr(participant, field, value)
                participant.updated_at = utc_now()
            meet.updated_at = utc_now()
            return meet

    # -------------------------------------------------------------------------
    # Floor control (who speaks)
    # -------------------------------------------------------------------------

    def take_floor(
        self,
        meet_id: str,
        holder: str,
        kind: ParticipantKind = ParticipantKind.USER,
        turn_id: Optional[str] = None,
    ) -> Meet:
        """Record that ``holder`` is speaking and clear the previous speaker."""
        with self._lock:
            meet = self._require_meet(meet_id)
            for participant in meet.participants:
                participant.speaking = (
                    participant.username == holder
                    or participant.profile_name == holder
                )
            meet.floor = MeetFloorState(
                holder=holder, kind=kind, since=utc_now(), updated_at=utc_now()
            )
            meet.updated_at = utc_now()
            return meet

    def release_floor(self, meet_id: str, holder: str = "") -> Meet:
        """Release the floor (optionally only when ``holder`` still has it)."""
        with self._lock:
            meet = self._require_meet(meet_id)
            if holder and meet.floor.holder and meet.floor.holder != holder:
                return meet
            for participant in meet.participants:
                participant.speaking = False
            meet.floor = MeetFloorState()
            meet.updated_at = utc_now()
            return meet

    def is_human_speaking(self, meet_id: str) -> bool:
        """True while a human holds the floor."""
        with self._lock:
            meet = self._require_meet(meet_id)
            return bool(meet.floor.holder) and meet.floor.kind == ParticipantKind.USER

    # -------------------------------------------------------------------------
    # Artifacts
    # -------------------------------------------------------------------------

    def append_transcript(
        self,
        meet_id: str,
        entry: MeetTranscriptEntry,
    ) -> Meet:
        """Append one transcript entry, keeping the newest bounded history."""
        with self._lock:
            meet = self._require_meet(meet_id)
            if not entry.entry_id:
                entry.entry_id = str(uuid.uuid4())
            if not entry.at:
                entry.at = utc_now()
            meet.transcript.append(entry)
            if len(meet.transcript) > MAX_TRANSCRIPT_ENTRIES:
                meet.transcript = meet.transcript[-MAX_TRANSCRIPT_ENTRIES:]
            if entry.message_id and entry.message_id not in meet.message_ids:
                meet.message_ids.append(entry.message_id)
            meet.touch()
            return meet

    def mark_speaking_interrupted(self, meet_id: str, holder: str) -> Meet:
        """Mark the transcript entries of the current speaker as interrupted."""
        with self._lock:
            meet = self._require_meet(meet_id)
            for entry in reversed(meet.transcript):
                if entry.speaker == holder:
                    entry.interrupted = True
                    break
            for participant in meet.participants:
                if participant.username == holder or participant.profile_name == holder:
                    participant.speaking = False
            meet.touch()
            return meet

    def set_chat_id(self, meet_id: str, chat_id: Optional[str]) -> Meet:
        """Associate (or clear) the meeting chat."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.chat_id = chat_id
            meet.touch()
            return meet

    def update_settings(self, meet_id: str, settings: MeetSettings) -> Meet:
        """Replace the behaviour settings of a meeting."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.settings = settings
            meet.touch()
            return meet

    def set_minutes(self, meet_id: str, minutes: MeetMinutes) -> Meet:
        """Store the notes/minutes document."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.minutes = minutes
            meet.touch()
            return meet

    def update_minutes_fields(self, meet_id: str, **fields) -> Meet:
        """Patch fields of the minutes document."""
        with self._lock:
            meet = self._require_meet(meet_id)
            for key, value in fields.items():
                if hasattr(meet.minutes, key):
                    setattr(meet.minutes, key, value)
            meet.touch()
            return meet

    def set_recording(self, meet_id: str, recording: MeetRecording) -> Meet:
        """Store the recording state."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.recording = recording
            meet.touch()
            return meet

    def update_recording_fields(self, meet_id: str, **fields) -> Meet:
        """Patch fields of the recording document."""
        with self._lock:
            meet = self._require_meet(meet_id)
            for key, value in fields.items():
                if hasattr(meet.recording, key):
                    setattr(meet.recording, key, value)
            meet.touch()
            return meet

    def record_profile_turn(self, meet_id: str, profile_name: str) -> Meet:
        """Increase the answer counter of a profile."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.profile_turns[profile_name] = (
                int(meet.profile_turns.get(profile_name, 0)) + 1
            )
            meet.touch()
            return meet

    def add_message_id(self, meet_id: str, message_id: str) -> Meet:
        """Register a chat message as part of the meeting."""
        with self._lock:
            meet = self._require_meet(meet_id)
            if message_id and message_id not in meet.message_ids:
                meet.message_ids.append(message_id)
            meet.touch()
            return meet

    def set_action_items(self, meet_id: str, items: List[MeetActionItem]) -> Meet:
        """Replace the action items of the stored minutes."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.minutes.action_items = items
            meet.touch()
            return meet

    def add_action_item(self, meet_id: str, item: MeetActionItem) -> Meet:
        """Append one action item to the stored minutes."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.minutes.action_items.append(item)
            meet.touch()
            return meet

    # -------------------------------------------------------------------------
    # Shared browser / computer session
    # -------------------------------------------------------------------------

    def set_shared_browser(
        self, meet_id: str, shared_browser: Optional[dict] = None
    ) -> Meet:
        """
        Attach (or clear) the browser/computer session shared with the meet.

        The association lives on the meet so a participant that joins later, or
        reloads the page, opens the same co-browsing session instead of an empty
        panel. Passing ``None``/an empty dict clears it.

        Args:
            meet_id: Meet that owns the session.
            shared_browser: ``{session_id, session_name, kind, owner, timestamp}``
                (or ``None`` to stop sharing).

        Returns:
            The updated :class:`Meet`.

        Raises:
            KeyError: when the meet does not exist.
        """
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.shared_browser = dict(shared_browser) if shared_browser else None
            meet.updated_at = utc_now()
            session_id = (meet.shared_browser or {}).get("session_id")
            logger.info(
                "Meet '%s' shared browser session '%s'", meet_id, session_id or "(none)"
            )
            return meet

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def get_meet(self, meet_id: str) -> Optional[Meet]:
        """Return a meet by id, or ``None`` when it does not exist."""
        with self._lock:
            return self._meets.get(meet_id)

    def list_meets(
        self, active_only: bool = True, project_id: Optional[str] = None
    ) -> List[Meet]:
        """Return meets, newest first, optionally filtered by status/project."""
        with self._lock:
            meets = list(self._meets.values())
        if active_only:
            meets = [meet for meet in meets if meet.status == MeetStatus.ACTIVE]
        if project_id:
            meets = [meet for meet in meets if meet.project_id == project_id]
        return sorted(meets, key=lambda meet: meet.created_at, reverse=True)

    # -------------------------------------------------------------------------
    # Signalling
    # -------------------------------------------------------------------------

    def add_signal(
        self,
        meet_id: str,
        signal_type: str,
        sender: str = "",
        to: str = "",
        data=None,
    ) -> Meet:
        """Append a signalling message to a meet's history."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.signals.append(
                MeetSignal(type=signal_type, sender=sender, to=to or "", data=data)
            )
            if len(meet.signals) > MAX_SIGNALS_PER_MEET:
                meet.signals = meet.signals[-MAX_SIGNALS_PER_MEET:]
            meet.updated_at = utc_now()
            return meet

    def get_signals(self, meet_id: str):
        """Return the signalling history for a meet."""
        with self._lock:
            meet = self._require_meet(meet_id)
            return list(meet.signals)

    def clear_signals(self, meet_id: str) -> Meet:
        """Clear the signalling history for a meet."""
        with self._lock:
            meet = self._require_meet(meet_id)
            meet.signals = []
            meet.updated_at = utc_now()
            return meet

    # -------------------------------------------------------------------------
    # Persistence helpers
    # -------------------------------------------------------------------------

    def register(self, meet: Meet, *, replace: bool = True) -> Meet:
        """Register a meet loaded from disk (used by the store)."""
        with self._lock:
            existing = self._meets.get(meet.meet_id)
            if existing is not None and not replace:
                return existing
            self._meets[meet.meet_id] = meet
            return meet


def get_meet_manager() -> MeetManager:
    """Return the process-wide singleton :class:`MeetManager` instance."""
    return MeetManager.get_instance()
