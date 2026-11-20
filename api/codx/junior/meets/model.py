"""
Data models for the Meets subsystem (WebRTC call management).

A *meet* represents a single call session. Each meet tracks:

* its participants (``MeetParticipant``),
* the WebRTC signalling messages exchanged between them (``MeetSignal``),
* and lifecycle metadata (status, timestamps, initiator).

Diagram:
```mermaid
classDiagram
    class Meet {
        +str meet_id
        +str name
        +str initiator
        +MeetStatus status
        +List~MeetParticipant~ participants
        +List~MeetSignal~ signals
    }
    class MeetParticipant {
        +str sid
        +str username
        +bool is_initiator
    }
    class MeetSignal {
        +MeetSignalType type
        +str sender
        +str to
        +Any data
    }
    Meet "1" --> "*" MeetParticipant
    Meet "1" --> "*" MeetSignal
```
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class MeetStatus(str, Enum):
    """Lifecycle status of a meet."""

    ACTIVE = "active"
    ENDED = "ended"


class MeetSignalType(str, Enum):
    """Types of WebRTC signalling messages."""

    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "ice-candidate"


class ParticipantKind(str, Enum):
    """Whether a meet participant is a human or an AI profile."""

    USER = "user"
    PROFILE = "profile"


class ParticipantStatus(str, Enum):
    """Lifecycle of a participant inside a meet."""

    INVITED = "invited"
    JOINED = "joined"
    LEFT = "left"


class MeetParticipant(BaseModel):
    """A single participant of a meet.

    Human participants have a ``sid`` and WebRTC media. AI profiles are
    *virtual* participants: they are listed in the room, get a camera-off tile
    with their profile avatar, listen to the meeting transcript and answer in
    the meet chat (and aloud through their configured voice). They never join
    the WebRTC mesh, so ``audio``/``video`` stay false for them.
    """

    sid: Optional[str] = Field(
        default=None, description="Socket.IO session id used for signalling"
    )
    username: str = Field(default="", description="Authenticated username")
    name: str = Field(default="", description="Display name shown in the UI")
    avatar: str = Field(default="", description="Avatar URL")
    kind: ParticipantKind = Field(
        default=ParticipantKind.USER, description="Human user or AI profile"
    )
    profile_name: str = Field(
        default="", description="Profile name when kind == 'profile'"
    )
    project_id: Optional[str] = Field(
        default=None, description="Project that owns the participant/profile"
    )
    is_initiator: bool = Field(
        default=False, description="True for the participant who created the meet"
    )
    audio: bool = Field(default=False, description="Microphone enabled")
    video: bool = Field(default=False, description="Camera enabled")
    screen: bool = Field(default=False, description="Screen sharing enabled")
    muted: bool = Field(default=False, description="Participant is muted")
    speaking: bool = Field(
        default=False, description="Participant currently holds the floor"
    )
    status: ParticipantStatus = Field(
        default=ParticipantStatus.JOINED,
        description="Invited, joined or left",
    )
    voice: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Resolved voice of an AI profile (voice_id, provider, ...)",
    )
    joined_at: str = Field(
        default_factory=utc_now, description="ISO-8601 join timestamp"
    )
    updated_at: str = Field(
        default_factory=utc_now, description="ISO-8601 last update timestamp"
    )

    @property
    def is_profile(self) -> bool:
        """True for a virtual AI participant."""
        return self.kind == ParticipantKind.PROFILE


class MeetTranscriptEntry(BaseModel):
    """One utterance of the meeting transcript.

    The transcript is the canonical record: profile answers, chat messages and
    spoken user messages all end up here (with a reference to the chat message
    that holds the visible text). Minutes are generated from it.
    """

    entry_id: str = Field(default="", description="Stable transcript entry id")
    at: str = Field(default_factory=utc_now, description="ISO-8601 timestamp")
    speaker: str = Field(default="", description="Username or profile name")
    speaker_kind: ParticipantKind = Field(
        default=ParticipantKind.USER, description="Human or AI speaker"
    )
    text: str = Field(default="", description="What was said")
    message_id: Optional[str] = Field(
        default=None, description="Chat message doc_id holding the utterance"
    )
    source: str = Field(
        default="speech", description="speech | chat | system | profile"
    )
    interrupted: bool = Field(
        default=False,
        description="The utterance was cut short because somebody else spoke",
    )


class MeetActionItem(BaseModel):
    """A follow-up proposed by the meeting notes."""

    id: str = Field(default="", description="Stable action item id")
    title: str = Field(default="", description="Short actionable title")
    description: str = Field(default="", description="What has to be done")
    assignee: str = Field(default="", description="Username or profile name")
    profile_name: str = Field(
        default="", description="Profile suggested to execute the task"
    )
    priority: str = Field(default="medium", description="high | medium | low")
    due_date: Optional[str] = Field(
        default=None, description="Deadline (YYYY-MM-DD)"
    )
    scheduled_at: Optional[str] = Field(
        default=None,
        description="Instant (ISO-8601) when the task should be picked up",
    )
    run: bool = Field(
        default=False, description="Run the task immediately after creating it"
    )
    status: str = Field(
        default="pending", description="pending | created | dismissed | error"
    )
    task_id: Optional[str] = Field(
        default=None, description="Task (chat) created from this item"
    )
    error: Optional[str] = Field(
        default=None, description="Why the task could not be created"
    )
    created_at: str = Field(default_factory=utc_now)


class MeetMinutes(BaseModel):
    """The notes/minutes of a meeting.

    ``status`` makes the asynchronous generation explicit: the client can open
    the summary while the model is still writing and refresh when the
    ``minutes`` socket event arrives.
    """

    status: str = Field(
        default="none", description="none | generating | ready | error"
    )
    summary: str = Field(default="", description="Executive summary")
    key_points: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    action_items: List[MeetActionItem] = Field(default_factory=list)
    attendees: List[str] = Field(default_factory=list)
    generated_at: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)

    def task_items(self) -> List[MeetActionItem]:
        """Action items that have not been materialised as tasks yet."""
        return [
            item
            for item in self.action_items
            if item.status not in {"created", "dismissed"}
        ]


class MeetRecording(BaseModel):
    """State of the meeting recording.

    The host's browser mixes the call audio (microphone + every remote stream
    + the profile voices it plays) and uploads one file when the meeting ends.
    The recording lives next to the meeting document, so it survives reloads
    and can be replayed from the summary page.
    """

    status: str = Field(
        default="none", description="none | recording | uploading | ready | error"
    )
    owner: str = Field(default="", description="Participant that recorded it")
    filename: str = Field(default="", description="Stored file name")
    mime: str = Field(default="audio/webm")
    size: int = Field(default=0, description="Bytes")
    duration_seconds: float = Field(default=0.0)
    started_at: Optional[str] = Field(default=None)
    ended_at: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)


class MeetFloorState(BaseModel):
    """Who currently holds the floor (speaks) in the room."""

    holder: str = Field(default="", description="Speaker holding the floor")
    kind: ParticipantKind = Field(default=ParticipantKind.USER)
    since: Optional[str] = Field(default=None)
    updated_at: str = Field(default_factory=utc_now)


class MeetSettings(BaseModel):
    """Behaviour of a meeting."""

    recording_enabled: bool = Field(
        default=True, description="The host records the call audio"
    )
    auto_minutes: bool = Field(
        default=True, description="Generate minutes when the meeting ends"
    )
    auto_respond: bool = Field(
        default=True, description="Profiles answer the conversation automatically"
    )
    live_notes: bool = Field(
        default=False, description="Refresh notes during the meeting"
    )
    notes_interval_seconds: int = Field(
        default=180, description="Minimum seconds between live-notes refreshes"
    )
    max_profile_turns: int = Field(
        default=2, description="Profiles allowed to answer each human turn"
    )
    language: str = Field(default="en-US", description="Speech language")
    max_speech_chars: int = Field(
        default=1200, description="Longest profile answer spoken aloud"
    )


class MeetSignal(BaseModel):
    """A WebRTC signalling message (offer, answer or ICE candidate)."""

    type: MeetSignalType = Field(description="Signalling message type")
    sender: str = Field(default="", description="Username or sid of the sender")
    to: Optional[str] = Field(
        default="",
        description="Target participant (empty = broadcast to the whole meet)",
    )
    data: Any = Field(
        default=None, description="Raw SDP offer/answer or ICE candidate payload"
    )
    created_at: str = Field(
        default_factory=utc_now, description="ISO-8601 creation timestamp"
    )


class Meet(BaseModel):
    """A meet session.

    The meet is both the live WebRTC room and the durable meeting record: it
    owns the associated chat, the transcript, the notes/minutes and the
    recording reference. Human participants join the peer mesh; AI profiles are
    virtual participants that answer in the chat and are spoken aloud.
    """

    meet_id: str = Field(description="Unique meet identifier")
    name: str = Field(default="", description="Human readable meet name")
    agenda: str = Field(default="", description="Meeting agenda / objective")
    initiator: str = Field(
        default="", description="Username of the participant who created the meet"
    )
    status: MeetStatus = Field(
        default=MeetStatus.ACTIVE, description="Meet lifecycle status"
    )
    participants: List[MeetParticipant] = Field(
        default_factory=list, description="Meet participants (humans and profiles)"
    )
    invited: List[str] = Field(
        default_factory=list,
        description="Usernames invited to the meet (human participants)",
    )
    invited_profiles: List[str] = Field(
        default_factory=list, description="AI profile names invited to the meet"
    )
    signals: List[MeetSignal] = Field(
        default_factory=list, description="WebRTC signalling history"
    )
    # Browser/computer session started for the whole room. Kept on the meet (and
    # not only announced over the socket) so a participant joining later — or
    # reloading the page — opens the same co-browsing session.
    shared_browser: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Browser/computer session shared with the meet: "
            "{session_id, session_name, kind, owner, timestamp}"
        ),
    )
    project_id: Optional[str] = Field(
        default=None, description="Project the meet belongs to, if any"
    )
    # ── Meeting artifacts ─────────────────────────────────────────────────
    chat_id: Optional[str] = Field(
        default=None,
        description="Chat associated with the meeting (all responses live there)",
    )
    source: str = Field(
        default="manual", description="manual | channel | team | invite"
    )
    channel_id: Optional[str] = Field(
        default=None, description="Channel that started the meeting"
    )
    channel_name: str = Field(default="", description="Channel display name")
    team_id: Optional[str] = Field(
        default=None, description="Team whose members were called"
    )
    board: Optional[str] = Field(
        default=None, description="Board whose tasks the meeting reviews"
    )
    task_ids: List[str] = Field(
        default_factory=list, description="Specific tasks reviewed by the meeting"
    )
    settings: MeetSettings = Field(
        default_factory=MeetSettings, description="Meeting behaviour"
    )
    transcript: List[MeetTranscriptEntry] = Field(
        default_factory=list, description="Durable meeting transcript"
    )
    minutes: MeetMinutes = Field(
        default_factory=MeetMinutes, description="Notes / minutes of the meeting"
    )
    recording: MeetRecording = Field(
        default_factory=MeetRecording, description="Recording state"
    )
    floor: MeetFloorState = Field(
        default_factory=MeetFloorState, description="Current speaker"
    )
    profile_turns: Dict[str, int] = Field(
        default_factory=dict, description="Answers produced by each profile"
    )
    message_ids: List[str] = Field(
        default_factory=list, description="Chat message ids that belong to the meet"
    )
    created_at: str = Field(
        default_factory=utc_now, description="ISO-8601 creation timestamp"
    )
    updated_at: str = Field(
        default_factory=utc_now, description="ISO-8601 last update timestamp"
    )
    ended_at: Optional[str] = Field(
        default=None, description="ISO-8601 end timestamp"
    )

    @property
    def is_active(self) -> bool:
        """Return True while the meet status is ``active``."""
        return self.status == MeetStatus.ACTIVE

    def participant_usernames(self) -> List[str]:
        """Return the human participant usernames (the WebRTC peers).

        AI profiles are deliberately absent: a client must never try to open a
        peer connection with a virtual participant.
        """
        return [
            p.username
            for p in self.participants
            if p.username
            and p.kind == ParticipantKind.USER
            and p.status == ParticipantStatus.JOINED
        ]

    def profile_usernames(self) -> List[str]:
        """Return the AI profile names that are part of the meet."""
        return [
            p.profile_name or p.username
            for p in self.participants
            if p.kind == ParticipantKind.PROFILE
            and p.status != ParticipantStatus.LEFT
        ]

    def profile_participants(self) -> List[Dict[str, Any]]:
        """Serialize the virtual profile participants for socket clients."""
        return [
            p.model_dump(mode="json")
            for p in self.participants
            if p.kind == ParticipantKind.PROFILE
        ]

    def find_participant(self, name: str) -> Optional[MeetParticipant]:
        """Find a participant by username or profile name (case insensitive)."""
        wanted = str(name or "").strip().lower()
        if not wanted:
            return None
        for participant in self.participants:
            if (
                participant.username.lower() == wanted
                or participant.profile_name.lower() == wanted
            ):
                return participant
        return None

    def touch(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = utc_now()


# ---------------------------------------------------------------------------
# Request payloads – these mirror the frontend `meets` API module.
# ---------------------------------------------------------------------------


class MeetInitiateRequest(BaseModel):
    """Payload for ``POST /api/meets/initiate``.

    ``participants`` accepts plain usernames (humans) and objects. An object
    with ``kind='profile'`` (or a ``profile_name``/``profile`` key) creates a
    virtual AI participant. The separate ``profiles`` shortcut is what the
    setup dialog uses for the profile check-list.
    """

    meetId: Optional[str] = Field(
        default=None, description="Client supplied meet id (generated when empty)"
    )
    name: Optional[str] = Field(default="", description="Meet name")
    participants: Optional[List[Any]] = Field(
        default_factory=list, description="Invited participants (usernames or objects)"
    )
    profiles: Optional[List[Any]] = Field(
        default_factory=list, description="AI profile names invited to the meeting"
    )
    invitedUsers: Optional[List[str]] = Field(
        default_factory=list, description="Additional human usernames to invite"
    )
    agenda: Optional[str] = Field(default="", description="Meeting agenda")
    board: Optional[str] = Field(
        default=None, description="Board whose tasks the meeting reviews"
    )
    taskIds: Optional[List[str]] = Field(
        default_factory=list, description="Specific task ids to review"
    )
    channelId: Optional[str] = Field(default=None, description="Origin channel")
    channelName: Optional[str] = Field(default="", description="Origin channel name")
    teamId: Optional[str] = Field(default=None, description="Team that was called")
    source: Optional[str] = Field(default="manual", description="manual | channel | team")
    createChat: bool = Field(
        default=True, description="Create the associated meeting chat"
    )
    settings: Optional[MeetSettings] = Field(
        default=None, description="Meeting behaviour overrides"
    )
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetJoinRequest(BaseModel):
    """Payload for ``POST /api/meets/join``."""

    meetId: Optional[str] = Field(default=None, description="Meet to join")
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetLeaveRequest(BaseModel):
    """Payload for ``POST /api/meets/leave``."""

    meetId: Optional[str] = Field(default=None, description="Meet to leave")
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetEndRequest(BaseModel):
    """Payload for ``POST /api/meets/end``."""

    meetId: Optional[str] = Field(default=None, description="Meet to end")
    generateMinutes: Optional[bool] = Field(
        default=None,
        description="Generate the minutes (defaults to the meeting setting)",
    )
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetParticipantsRequest(BaseModel):
    """Payload for ``POST/DELETE /api/meets/{meet_id}/participants``."""

    users: List[str] = Field(default_factory=list, description="Human usernames")
    profiles: List[Any] = Field(
        default_factory=list, description="AI profile names or profile objects"
    )
    notify: bool = Field(
        default=True, description="Tell the invited users through the socket"
    )


class MeetUtteranceRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/utterance``.

    The browser sends the final transcription (SpeechRecognition or the server
    fallback) or a typed chat message. The server records it in the transcript,
    stores it in the meeting chat and (optionally) lets profiles answer.
    """

    text: str = Field(description="What was said")
    speaker: Optional[str] = Field(
        default="", description="Speaker (defaults to the authenticated user)"
    )
    kind: Optional[ParticipantKind] = Field(
        default=ParticipantKind.USER, description="Human or AI speaker"
    )
    source: str = Field(
        default="speech", description="speech | chat | note"
    )
    clientMessageId: Optional[str] = Field(
        default=None,
        description="Client-side idempotency key (retries do not duplicate)",
    )
    language: Optional[str] = Field(default=None, description="Speech language")
    autoRespond: Optional[bool] = Field(
        default=None, description="Override the meeting auto-respond setting"
    )


class MeetFloorRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/floor``.

    Sent by every client when the local microphone starts/ends picking up
    speech. The server marks the floor as taken by a human and interrupts the
    profile that is talking, so profiles never talk over people.
    """

    event: str = Field(description="start | end")
    kind: ParticipantKind = Field(default=ParticipantKind.USER)
    speaker: Optional[str] = Field(default="", description="Who is speaking")
    turnId: Optional[str] = Field(
        default=None, description="Profile turn being interrupted"
    )


class MeetNotesRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/notes``."""

    instructions: Optional[str] = Field(
        default="", description="Extra instructions for the minute taker"
    )
    force: bool = Field(
        default=False, description="Generate even when notes already exist"
    )
    async_generation: bool = Field(
        default=True,
        alias="async",
        description="Return immediately and publish the result over the socket",
    )


class MeetTasksRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/tasks``.

    ``items`` may be omitted: the pending action items of the stored minutes
    are then materialised as tasks. Each item accepts the task fields plus
    ``run`` and ``scheduled_at``/``when``.
    """

    items: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Action items to create (defaults to minutes)"
    )
    item_ids: Optional[List[str]] = Field(
        default_factory=list, description="Ids of stored action items to create"
    )
    board: Optional[str] = Field(default=None, description="Destination board")
    column: Optional[str] = Field(default=None, description="Destination column")
    run: bool = Field(
        default=False, description="Run the created task immediately"
    )
    schedule_at: Optional[str] = Field(
        default=None,
        description="Schedule the created tasks at this ISO instant / expression",
    )
    timezone: Optional[str] = Field(
        default=None, description="Timezone used for relative scheduling"
    )


class MeetSpeechRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/speech``.

    Either a ``messageId`` (a profile answer already stored in the meeting
    chat) or explicit ``text`` can be synthesised. The resolved profile voice
    is used so a profile always sounds the same, in every meeting.
    """

    messageId: Optional[str] = Field(
        default=None, description="Chat message to speak"
    )
    text: Optional[str] = Field(default=None, description="Text to speak")
    profile: Optional[str] = Field(
        default=None, description="Profile whose voice should be used"
    )


class MeetChatRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/chat``."""

    name: Optional[str] = Field(default="", description="Chat name")


class MeetBrowserRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/browser``.

    Shares a browser/computer session with the whole meet.
    """

    sessionId: str = Field(description="Browser/computer session id to share")
    sessionName: Optional[str] = Field(
        default="", description="Human readable session name"
    )
    kind: Optional[str] = Field(
        default="browser", description="Session kind (browser, linux, windows, ...)"
    )
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetOfferRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/signal/offer``."""

    offer: Optional[Any] = Field(default=None, description="SDP offer")
    to: Optional[str] = Field(default="", description="Target participant")
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetAnswerRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/signal/answer``."""

    answer: Optional[Any] = Field(default=None, description="SDP answer")
    to: Optional[str] = Field(default="", description="Target participant")
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")


class MeetIceCandidateRequest(BaseModel):
    """Payload for ``POST /api/meets/{meet_id}/signal/ice-candidate``."""

    candidate: Optional[Any] = Field(default=None, description="ICE candidate")
    to: Optional[str] = Field(default="", description="Target participant")
    timestamp: Optional[int] = Field(default=None, description="Client timestamp")