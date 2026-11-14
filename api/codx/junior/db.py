import os
import logging
import uuid
from slugify import slugify

from codx.junior.settings import CODXJuniorSettings

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Any, Dict

from datetime import datetime
from enum import Enum

from codx.junior.model.agent import CostInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
MAX_IMAGE_SIZE_MB = 50
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024

# Attachments are not image-only: the chat composer records voice notes and
# stores them as audio attachments (audio/webm, audio/ogg, audio/mp4 ...).
# The generic constants keep the same limit that was previously image-only.
MAX_ATTACHMENT_SIZE_MB = MAX_IMAGE_SIZE_MB
MAX_ATTACHMENT_SIZE_BYTES = MAX_IMAGE_SIZE_BYTES

ALLOWED_IMAGE_TYPES = [
    'image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp', 'image/svg+xml'
]
ALLOWED_AUDIO_TYPES = [
    'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav',
    'audio/x-wav', 'audio/x-m4a', 'audio/m4a', 'audio/aac', 'audio/flac'
]
ALLOWED_ATTACHMENT_TYPES = ALLOWED_IMAGE_TYPES + ALLOWED_AUDIO_TYPES


class ChatAttachment(BaseModel):
    """Represents a media file (image or audio note) attached to a chat or message,
    with its metadata and base64 encoded data."""
    file_name: str = Field(description="Original file name of the attachment")
    file_type: str = Field(description="MIME type (e.g., 'image/png', 'audio/webm')")
    file_size: int = Field(description="File size in bytes")
    base64_data: str = Field(description="Base64 encoded file data")
    uploaded_at: str = Field(default_factory=lambda: str(datetime.now()), description="Timestamp when the attachment was uploaded")
    
    @validator('file_size')
    def validate_file_size(cls, v):
        if v > MAX_ATTACHMENT_SIZE_BYTES:
            raise ValueError(f"Attachment size exceeds maximum allowed size of {MAX_ATTACHMENT_SIZE_MB}MB")
        return v
    
    @validator('file_type')
    def validate_file_type(cls, v):
        # `MediaRecorder` reports the recording type together with its codec list
        # ("audio/webm;codecs=opus"). Attachments store the container type only,
        # so the parameters are stripped before validating (and persisting).
        media_type = (v or '').split(';')[0].strip().lower()
        # Mobile uploads report whatever the device knows: an iPhone gallery
        # screenshot is `image/heic`, some gallery apps send `image/bmp` or an
        # empty/generic type. Rejecting those made the whole message fail, so any
        # `image/*` is accepted and stored as-is: the real format is sniffed from
        # the payload and converted to PNG/JPEG before the model request (see
        # ChatPromptBuilder._prepare_attachment).
        if media_type.startswith('image/'):
            return media_type
        if media_type not in ALLOWED_ATTACHMENT_TYPES:
            raise ValueError(
                f"Attachment type '{v}' not allowed. Allowed types: {', '.join(ALLOWED_ATTACHMENT_TYPES)}"
            )
        return media_type


class KanbanColumn(BaseModel):
    """Represents a column in a Kanban board."""
    doc_id: Optional[str] = Field(default=None)
    title: str = Field(default=None)
    color: Optional[str] = Field(default=None)
    index: int = Field(default=0)
    chats: List[str] = Field(default=[])


class Kanban(BaseModel):
    """Represents a Kanban board with columns and chats."""
    doc_id: Optional[str] = Field(default=None)
    title: str = Field(default=None)
    description: Optional[str] = Field(default=None)
    index: int = Field(default=0)
    columns: Optional[List[KanbanColumn]] = Field(default=[])
    created_at: str = Field(default_factory=lambda: str(datetime.now()))
    updated_at: str = Field(default_factory=lambda: str(datetime.now()))


class MessageTaskItem(Enum):
    """Enum for message task item types."""
    SUMMARY = "summary"


class ToolEvent(BaseModel):
    """
    Represents a tool execution event.
    
    Used to track tool calls, their execution status, and results.
    Attached to the assistant response message that triggered the tool call.

    Tool output handling
    --------------------
    ``output`` carries the tool's user-facing content (``ToolResponse.user_response``).
    It is deliberately kept OUT of ``Message.content`` so the chat is not polluted:
    clients read it from this property and render it collapsed or hidden at their
    discretion.
    """
    tool: str = Field(description="Name of the tool that was executed")
    tool_call_id: str = Field(description="Unique identifier for this tool call from the provider")
    status: str = Field(description="Execution status: 'running', 'done', or 'error'")
    request: Optional[Dict[str, Any]] = Field(default=None, description="Parsed JSON arguments sent to the tool")
    response: Optional[str] = Field(default=None, description="Truncated tool result preview")
    output: Optional[str] = Field(
        default=None,
        description=(
            "User-facing output produced by the tool (ToolResponse.user_response). "
            "Kept OUT of Message.content so clients can render tool output collapsed "
            "or hidden. Only dual-response tools populate this field."
        )
    )
    duration_ms: Optional[float] = Field(default=None, description="Execution time in milliseconds")
    error: Optional[str] = Field(default=None, description="Error details when status is 'error'")
    started_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 (UTC) timestamp of the moment the call started. Emitted by "
            "the agent runtime; without it a client can only guess the position "
            "of the call inside the turn."
        )
    )
    finished_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 (UTC) timestamp of the moment the call finished"
    )
    cached: Optional[bool] = Field(
        default=None,
        description="True when the call was served from the tool cache (no execution)"
    )


class LifeCycleEvent(BaseModel):
    """
    Represents a lifecycle event in the agent run.
    
    Used to track agent run status changes and execution metrics.
    Attached to the assistant response message produced by the run.
    """
    status: str = Field(description="Lifecycle status: 'running', 'done', or 'error'")
    run_id: str = Field(description="Unique identifier for the agent run")
    duration_ms: Optional[float] = Field(default=None, description="Execution time in milliseconds")
    error: Optional[str] = Field(default=None, description="Error details when status is 'error'")
    started_at: Optional[str] = Field(
        default=None, description="ISO-8601 (UTC) timestamp of the run start"
    )
    finished_at: Optional[str] = Field(
        default=None, description="ISO-8601 (UTC) timestamp of the run end"
    )


class PhaseEvent(BaseModel):
    """
    One PHASE of a turn, i.e. a period of waiting that is not a tool call.

    A turn is not only tool calls: most of its wall-clock time is spent WAITING
    — for the model to accept the request, for its first token, for the answer
    to stream, for the process to come back after a restart. Those waits are
    invisible in :class:`ToolEvent` (no call is running), so a client could only
    reconstruct them by spreading the leftover time over the gaps, which is a
    guess. This model makes them real, timestamped facts.

    Kinds emitted by the agent runtime:

    - ``llm_request``  — request sent, waiting for the FIRST token (TTFT).
    - ``llm_response`` — tokens are streaming: the model is writing.
    - ``notice``       — a tool/runtime notice (loop guard, course correction).
    - ``wallet_check`` — budget pre-flight before the run.
    - ``interrupted``  — the process died (or the turn was stopped) while this
      phase was running. Written by the recovery worker on restart, so a power
      cut leaves a visible trace instead of a turn that silently stops.
    """
    kind: str = Field(description="llm_request | llm_response | notice | wallet_check | interrupted")
    status: str = Field(description="Phase status: 'running', 'done', or 'error'")
    label: Optional[str] = Field(default=None, description="Short human label")
    detail: Optional[str] = Field(default=None, description="What happened, in one line")
    started_at: Optional[str] = Field(default=None, description="ISO-8601 (UTC) phase start")
    finished_at: Optional[str] = Field(default=None, description="ISO-8601 (UTC) phase end")
    duration_ms: Optional[float] = Field(default=None, description="Measured phase duration in milliseconds")
    meta: Optional[Dict[str, Any]] = Field(default=None, description="Free-form phase metrics (model, tokens, ...)")


class Message(BaseModel):
    """
    A single chat message.

    Tool and run-lifecycle events emitted while generating an assistant
    response are ASSOCIATED with that response message (they are NOT separate
    chat messages):

    - ``tool_events``:      one :class:`ToolEvent` per tool call executed
                            during this response, updated in place
                            (running → done/error). Each event carries the
                            tool's user-facing ``output`` (see below) so clients
                            can render tool output collapsed/hidden without it
                            polluting ``content``.
    - ``lifecycle_events``: one :class:`LifeCycleEvent` per agent run
                            performed to produce this response.

    Both lists are streamed in real time together with the message and are
    persisted with the chat.

    **Tool output (`tool_events[].output`)**

    Tool output (``ToolResponse.user_response``) is NOT merged into
    ``content``. It is attached to the response message as the
    :attr:`ToolEvent.output` property (streamed live with every message event
    and persisted with the chat) so the client decides whether to show it
    inline, collapsed, or hidden.

    **Cost Tracking (`cost` / `cost_info` fields)**

    ``cost`` records the cost, in cxjcoins, of the AI iteration that produced
    this message. SmolAgent registers the FULL processing cost of the response
    here (every LLM round plus every tool cost reported via
    ``ToolResponse.cost``). ``ChatManager`` accumulates this value into
    :attr:`Chat.cost` when the message is added, so the chat budget
    (``Chat.estimation_cost``) is preserved even if the message is later
    deleted.

    ``cost_info`` carries the detailed breakdown (tokens, input/output/tool
    costs, chat budget state) produced by SmolAgent's typed
    :class:`AgentRunResult`. It is streamed to clients together with the
    message via ``message-event``.

    **Project Scope (`project_id` field)**

    Like :attr:`Chat.project_id`, a message may declare the project it works
    on. When the current USER message sets it, that project becomes the MAIN
    project of the turn: the chat engine switches its context to it and
    resolves file references (``files``) against it, so a single chat can work
    on files that live in another project.

    The field is NOT inherited from the chat: :meth:`get_project_id` returns
    ``None`` when the message does not declare a project, leaving the fallback
    (``Chat.project_id`` / ``Chat.owner_project_id``) to the caller.

    **Message Read Tracking (`read_by` field)**
    
    The `read_by` field tracks which users have viewed a message in collaborative chats.
    
    **Usage Context:**
    - Primarily for assistant messages (role='assistant') in multi-user chat environments
    - Indicates message delivery and read status to other users
    - User's own messages are excluded from tracking (message.user !== current_username)
    
    **Automatic Tracking:**
    - When a user scrolls past and views an assistant message from another user,
      their username is automatically added to the `read_by` list
    - Tracked via ChatEntry.vue which monitors viewport visibility
    - After 500ms idle time, calls `/api/chats/markMessageAsSeen` endpoint
    - Endpoint signature: POST /api/chats/markMessageAsSeen
      - Params: (chat_id, message_id, username)
      - Effect: Adds username to message.read_by list if not already present
    
    **Display Indicators:**
    - ChatMiniMap: Double-check marks
      - Gray checks (✓) = message unread by current user
      - Green checks (✓✓) = message read by current user
      - Only shown for assistant messages from other users
    - ChatSidebarNodeCompact/Extended: Unread badge
      - Shows count of unread assistant messages from others
      - Triggers awareness of new messages in the chat
    - Message Headers: Optional read status indicators
    
    **Initialization & Lifecycle:**
    - Always initialized as empty list [] when message is created
    - Populated over time as different users view the message
    - Never decreases (read state is permanent per user)
    - Cleared only if message is deleted
    """
    doc_id: Optional[str] = Field(default=None)
    role: str = Field(default='')
    task_item: str = Field(default='')
    content: str = Field(default='')
    think: Optional[str] = Field(default=None)
    hide: bool = Field(default=False)
    unsent: bool = Field(
        default=False,
        description=(
            "DRAFT marker: an ``unsent`` message is the text a client composer "
            "is still typing, persisted with the chat (so it survives a reload) "
            "but NEVER part of the conversation. At most ONE unsent message "
            "exists per chat and only the composer that owns it may read, "
            "update or delete it. Every reader drops it through "
            "``_visible_messages`` so it cannot reach chat lists, summaries, "
            "LLM prompts, socket chat events, analytics or exports. See "
            "``get_unsent_message`` / ``upsert_unsent_message`` / "
            "``delete_unsent_message``."
        )
    )
    is_answer: bool = Field(default=False)
    improvement: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: str(datetime.now()))
    updated_at: str = Field(default_factory=lambda: str(datetime.now()))
    attachments: List[ChatAttachment] = Field(default=[], description="Attachments for this message")
    files: List[str] = Field(default=[])
    project_id: Optional[str] = Field(
        default=None,
        description=(
            "Project this message works on, like Chat.project_id. When the "
            "current user message sets it, it becomes the MAIN project of the "
            "turn and the message's file references (files) are resolved "
            "against it instead of the chat's project, so a single chat can "
            "work on files that live in another project. It is NOT inherited: "
            "None means the message declares no project (see get_project_id())."
        )
    )
    meta_data: Optional[Dict[str, Any]] = Field(default=None, description="Free-form supplementary metadata (timings, model, analytics...)")
    tool_events: List[ToolEvent] = Field(default=[], description="Tool execution events associated with this response message")
    lifecycle_events: List[LifeCycleEvent] = Field(default=[], description="Agent run lifecycle events associated with this response message")
    phase_events: List[PhaseEvent] = Field(default=[], description="Non-tool phases of the turn (waiting for the model, streaming, interruptions) with real timestamps")
    cost: float = Field(
        default=0.0,
        description=(
            "Cost in cxjcoins of the AI iteration that produced this message. "
            "SmolAgent registers the FULL processing cost here (all LLM rounds "
            "plus any tool cost) and ChatManager accumulates it into Chat.cost "
            "when the message is added."
        )
    )
    cost_info: Optional[CostInfo] = Field(
        default=None,
        description=(
            "Detailed cost/usage breakdown of the AI run that produced this "
            "message (tokens, input/output/tool costs, chat budget state). "
            "Streamed to clients via message-event. The numeric `cost` field is "
            "what ChatManager accumulates into Chat.cost."
        )
    )
    profiles: List[str] = Field(default=[])
    user: Optional[str] = Field(default=None)
    knowledge_topics: List[str] = Field(description="This message will be indexed for knowledge and tagged with this topics", default=[])
    done: Optional[bool] = Field(default=True, description="Indicates if user is done writing")
    is_thinking: Optional[bool] = Field(default=False)
    disable_knowledge: Optional[bool] = Field(default=False)
    read_by: List[str] = Field(default=[], description="List of usernames who have viewed this assistant message; automatically populated when users view the message")
    error: Optional[str] = Field(default=None)
    linked_chat_ids: Optional[List[str]] = Field(default=[], description="Linked chat ids")

    def get_project_id(self) -> Optional[str]:
        """
        Return the project this message declares, or None when it declares
        none.

        Unlike :attr:`Chat.project_id`, the message does NOT inherit the
        project of its chat: the fallback (chat ``project_id`` first, then
        ``owner_project_id``) is the caller's responsibility, so callers can
        tell an explicitly scoped message from an unscoped one.

        :return: The project id this message declares, or None.
        """
        return self.project_id


class ChatHistoryEntry(BaseModel):
    """Represents a historical entry in a chat with timestamp, summary, and associated message IDs."""
    timestamp: str = Field(default_factory=lambda: str(datetime.now()), description="Timestamp when this history entry was generated")
    summary: str = Field(default='', description="Summary of this history entry")
    message_ids: List[str] = Field(default=[], description="List of message IDs associated with this history entry")


class ChatStatusEvent(BaseModel):
    """
    Append-only record of a single chat status transition.

    Registered by :class:`codx.junior.chat_manager.ChatManager` whenever
    ``Chat.status`` changes, so task-performance metrics (cycle time,
    throughput, reliability...) can be derived from a complete transition
    history instead of the single mutable ``Chat.status`` value.
    """
    doc_id: Optional[str] = Field(default=None, description="Unique identifier of this transition record")
    from_status: Optional[str] = Field(default=None, description="Previous status, None on the first record")
    to_status: str = Field(default='', description="New status")
    user: Optional[str] = Field(default=None, description="User that triggered the change, if any")
    at: str = Field(default_factory=lambda: str(datetime.now()), description="Timestamp of the transition")
    meta_data: Optional[Dict[str, Any]] = Field(default=None, description="Free-form supplementary metadata (source, reason...)")


class ChatId(BaseModel):
    """Represents a reference to a chat in another project."""
    chat_id: str = Field(default=None, description="Chat id")
    project_id: str = Field(default=None, description="Defines the project which this chat belongs")


class Chat(BaseModel):
    """Represents a chat session with messages, metadata, and kanban board associations."""
    id: Optional[str] = Field(default=None)
    doc_id: Optional[str] = Field(default=None)
    project_id: Optional[str] = Field(default=None, description="Defines the project which this chat works, see owner_project_id for the project where the chat was created")
    owner_project_id: Optional[str] = Field(default=None, description="Project owner.")
    parent_id: Optional[str] = Field(default=None, description="Parent chat")
    root_id: Optional[str] = Field(
        default=None,
        description=(
            "Id of the topmost ancestor of this chat (the 'main task' of a task "
            "tree). It is a CACHE of the parent_id walk, resolved and backfilled "
            "by codx.junior.identity.resolve_root_id: a chat without parent_id is "
            "its own root. Every task of a long-running run shares the same "
            "root_id, no matter how deep it is in the hierarchy."
        )
    )
    linked_chat_ids: Optional[List[str]] = Field(default=[], description="Linked chat ids")
    parent_owner_project_id: Optional[str] = Field(default=None, description="Parent chat project owner.")
    parent_project_id: Optional[str] = Field(default=None, description="Parent chat project id")
    child_index: Optional[int] = Field(default=0, description="Child index. Used to sort chat content among other siblings")
    message_id: Optional[str] = Field(default=None, description="Parent message for threads")
    status: str = Field(default='')
    status_history: List[ChatStatusEvent] = Field(
        default=[],
        description=(
            "Append-only log of status transitions (from_status -> to_status) "
            "used to build performance metrics like cycle time and throughput. "
            "Maintained by ChatManager.save_chat whenever status changes."
        )
    )
    tags: Optional[List[str]] = Field(default=[], description="Informative set of tags")
    file_list: List[str] = Field(default=[])
    check_lists: Optional[List[dict]] = Field(default=[])
    profiles: List[str] = Field(default=[])
    users: List[str] = Field(default=[])
    name: str = Field(default='')
    pinned: Optional[bool] = Field(default=False)
    is_template: Optional[bool] = Field(default=False, description="Chat will be used as temlate")
    description: str = Field(default='')
    meta_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Free-form chat-level metadata persisted with the chat. "
            "Used, for example, by the browser co-browsing session snapshot "
            "(meta_data['browser'])."
        )
    )
    messages: List[Message] = Field(default=[])
    created_at: str = Field(default_factory=lambda: str(datetime.now()))
    updated_at: str = Field(default_factory=lambda: str(datetime.now()))
    mode: str = Field(default='chat')
    kanban_id: str = Field(default='')
    column_id: str = Field(default='')
    board: str = Field(default='')
    column: str = Field(default='')
    sprints: List[str] = Field(
        default=[],
        description=(
            "Identifiers of the sprint boards this chat belongs to. "
            "A chat/task can be part of multiple sprints; membership is used to "
            "filter which cards are shown on a 'sprint' type board. Persisted "
            "through the existing /api/chats/metadata endpoint."
        )
    )

    # ── Planning / task fields ─────────────────────────────────────────────
    # Persisted through the existing /api/chats/metadata endpoint
    # (ChatManager.update_chat_metadata uses setattr for known attributes).
    priority: Optional[str] = Field(
        default=None,
        description="Task priority: 'high' | 'medium' | 'low'"
    )
    due_date: Optional[str] = Field(
        default=None,
        description="Task due date in ISO format (YYYY-MM-DD)"
    )
    progress: int = Field(
        default=0,
        description="Task completion percentage (0-100)"
    )
    assignees: List[str] = Field(
        default=[],
        description="Usernames assigned to this task (distinct from participants in 'users')"
    )
    wake_at: Optional[str] = Field(
        default=None,
        description=(
            "Instant (ISO-8601) when this task must be delivered to an autonomous "
            "agent. It is NOT a deadline (that is ``due_date``): it is the alarm "
            "clock the autonomy worker reads. An agent schedules a follow-up with "
            "``wake_at='+2d'`` and is woken when it becomes due; the wake is "
            "consumed (cleared) once a shift has handled the task."
        )
    )
    sort_index: Optional[int] = Field(
        default=None,
        description="Manual ordering index within a board column"
    )

    # ── Archive (compressed, out of the live tree) ─────────────────────────
    # Additive and derived: they are stamped by ChatManager when a task is
    # listed with `include_archived` and never stored inside the task file.
    archived: bool = Field(
        default=False,
        description=(
            "True when the task is archived: compressed into "
            "<codx_path>/tasks/.archive and no longer part of the live tree. "
            "Archived tasks are only returned when the caller asks for them "
            "(`include_archived`)."
        )
    )
    archived_at: Optional[str] = Field(
        default=None,
        description="Instant (ISO-8601) when the task was archived"
    )
    archived_by: Optional[str] = Field(
        default=None,
        description="User or profile that archived the task"
    )

    # ── Calendar (agenda) fields ───────────────────────────────────────────
    # Additive and defaulted: existing task files load unchanged and the
    # existing /api/chats/metadata endpoint persists them (it setattrs
    # declared fields), so no migration is needed. `due_date` keeps its
    # deadline meaning; `scheduled_start/end` is the work block drawn on the
    # calendar (see design/06-calendar/01-analysis.md §5.4).
    calendar_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Calendars this task is pinned to, beyond the implicit "
            "'codx-junior' system calendar. The task is ONE item with N "
            "memberships, never a copy per calendar."
        )
    )
    scheduled_start: Optional[str] = Field(
        default=None,
        description=(
            "Start of the work block on the calendar (ISO datetime). "
            "None means the task is only drawn on its due date."
        )
    )
    scheduled_end: Optional[str] = Field(
        default=None,
        description="End of the work block on the calendar (ISO datetime)"
    )
    all_day: bool = Field(
        default=False,
        description=(
            "The work block is an all-day marker instead of a time slot "
            "(used together with scheduled_start/end)."
        )
    )

    columns: List[KanbanColumn] = Field(default=[])
    chat_index: Optional[int] = Field(default=0)
    url: str = Field(default='')
    branch: str = Field(default='')
    file_path: str = Field(default='')
    llm_model: Optional[str] = Field(default='')

    # ── Cost budget ────────────────────────────────────────────────────────
    estimation_cost: Optional[float] = Field(
        default=None,
        description=(
            "Estimated maximum cost (in cxjcoins) allowed for this chat. "
            "When the accumulated cost reaches this value, SmolAgent stops "
            "executing to keep the chat within budget. None (or <= 0) means "
            "no cost limit. On the main task of a long-running run this field "
            "holds the run budget, because the cost of every descendant rolls "
            "up into it."
        )
    )
    cost: float = Field(
        default=0.0,
        description=(
            "Accumulated cost (in cxjcoins) spent on this chat. Updated by "
            "ChatManager whenever a message with a cost is added or updated. "
            "This value is MONOTONIC: deleting a message never reduces it, so "
            "the budget reflects everything the chat has actually spent. The "
            "cost of every descendant chat also rolls up into the root chat "
            "(Chat.root_id), which makes the root budget cover the whole tree."
        )
    )

    # ── Orchestration (durable multi-step runs) ────────────────────────────
    # A run is anchored on one main task (its "dashboard") and every plan step
    # is materialized as a child task. The full plan/results live in
    # ``.codx/orchestration/runs/<run_id>.json`` (see codx.junior.orchestration);
    # these fields are the board-facing back-references.
    run_id: Optional[str] = Field(
        default=None,
        description=(
            "Id of the orchestration run this chat belongs to: the long-running "
            "process id ChatView listens to. The main task of "
            "a run carries run_id + run_status; every step card carries run_id + "
            "run_step_id. The run document is the source of truth; these fields "
            "keep the board, sprint and planning views filterable."
        )
    )
    run_status: Optional[str] = Field(
        default=None,
        description=(
            "Mirror of the run status on the main task (running, paused, "
            "completed, budget_exhausted, stalled, ...). None for chats outside "
            "an orchestration run."
        )
    )
    run_step_id: Optional[str] = Field(
        default=None,
        description=(
            "Id of the plan step when this chat is a worker card of a run. Empty "
            "on the main task and on regular chats."
        )
    )

    visibility: Optional[str] = Field(default='')
    remote_url: Optional[str] = Field(default='')
    attachments: List[ChatAttachment] = Field(default=[], description="Attachments for this chat")
    knowledge_topics: List[str] = Field(description="This chat will be indexed for knowledge and tagged with this topics", default=[])
    chat_links: List[ChatId] = Field(default=[])
    pr_view: Optional[dict] = Field(default={}, description="Pull request view")
    history: List[ChatHistoryEntry] = Field(default=[], description="Historical entries of this chat")
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session/conversation identifier used to link chat with AI request/response logs"
    )
    auto_initialize: Optional[bool] = Field(
        default=False,
        description=(
            "Indicates this is a new chat that has not been initialized yet. "
            "When True, AI will auto-fill board, column and name fields on first response."
        )
    )
    ignore_parent_knowledge: Optional[bool] = Field(
        default=False,
        description="When True, disconnects from parent chat knowledge/context and only uses own messages"
    )
    ignore_parent_files: Optional[bool] = Field(
        default=False,
        description="When True, excludes parent chat file list from the working context"
    )

    # ── External task mirrors ──────────────────────────────────────────────
    # A chat may mirror a task that lives in another system (currently DSH).
    # The mirror stores only identity + board placement; the conversation is
    # fetched live from the external system when the chat is opened, so no
    # message content is duplicated in codx-junior's task files.
    external_source: Optional[str] = Field(
        default=None,
        description="External system owning this task (e.g. 'dsh'). None for native chats."
    )
    external_id: Optional[str] = Field(
        default=None,
        description="Task/session id in the external system."
    )
    external_parent_id: Optional[str] = Field(
        default=None,
        description="Parent task id in the external system, when it is known there."
    )
    external_instance_id: Optional[str] = Field(
        default=None,
        description="DSH instance that owns the external session, when applicable."
    )
    external_status: Optional[str] = Field(
        default=None,
        description="Last status reported by the external system (idle/running/error/closed)."
    )
    external_url: Optional[str] = Field(
        default=None,
        description="Deep link to open the task in the external system."
    )
    external_updated_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp of the last external update."
    )

    # ── Harness association ────────────────────────────────────────────────
    # A task may be executed by an external harness (DSH, Claude Code, Codex).
    # The harness fields are the generic association; the external_* fields
    # above remain as compatibility aliases for the original DSH mirror.
    # `status` is the workflow status and `harness_status` is the execution
    # status; they must not be conflated.
    harness: Optional[str] = Field(
        default=None,
        description="Execution harness kind (e.g. 'dsh', 'claude-code', 'codex')."
    )
    harness_instance_id: Optional[str] = Field(
        default=None,
        description="Harness instance that currently owns the task."
    )
    harness_task_id: Optional[str] = Field(
        default=None,
        description="Task/session/thread id in the harness."
    )
    harness_parent_task_id: Optional[str] = Field(
        default=None,
        description="Parent task id in the harness namespace, when it is known there."
    )
    harness_mode: Optional[str] = Field(
        default=None,
        description="Association mode: 'managed', 'mirror' or 'attached'."
    )
    harness_status: Optional[str] = Field(
        default=None,
        description=(
            "Execution status reported by the harness "
            "(queued/running/waiting_input/completed/failed/cancelled/stale)."
        )
    )
    harness_model: Optional[str] = Field(
        default=None,
        description="Model selected for the harness run, from codx-junior's model catalogue."
    )
    harness_usage: Dict[str, Any] = Field(
        default_factory=dict,
        description="Last usage counters reported by the harness (tokens, cost, model...)."
    )
    harness_cost: Optional[float] = Field(
        default=None,
        description="Last harness-native cost reported by the harness. Kept separate from Chat.cost."
    )
    harness_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form harness metadata (session url, selection errors, labels...)."
    )


# ---------------------------------------------------------------------------
# Unsent drafts (message composer state)
# ---------------------------------------------------------------------------
# The text a user is still typing is persisted with the chat as ONE draft
# message (``Message.unsent == True``). It survives reloads and crashes, but it
# is NOT part of the conversation: it must never be listed, summarized, sent to
# the model, broadcast as a chat message, exported or counted by analytics.
# Persistence is delegated to the owner ``ChatManager`` (the only chat store);
# the imports are lazy because ``chat_manager`` imports this module.


def _visible_messages(messages: Optional[List[Message]]) -> List[Message]:
    """
    Return only the messages that belong to the conversation.

    Unsent drafts (``Message.unsent``) are filtered out here, so every reader
    that goes through this helper is draft-free by construction.

    :param messages: Message list of a chat (may be ``None``).
    :return: A new list without the unsent drafts.
    """
    return [message for message in (messages or []) if not message.unsent]


def _find_unsent_message(chat: "Chat") -> Optional[Message]:
    """
    Return the unsent draft of *chat* (the most recent one when legacy data has
    more than one), or ``None`` when the chat has no draft.

    :param chat: Chat to inspect.
    :return: The draft message or ``None``.
    """
    for message in reversed(chat.messages or []):
        if message.unsent:
            return message
    return None


def _resolve_chat_manager(chat_id: str, chat_manager: Any = None) -> Any:
    """
    Resolve the ``ChatManager`` that stores *chat_id*.

    The manager is injectable (``chat_manager``) because the API layer already
    holds the session manager; otherwise the projects are searched the same way
    ``/api/chats/message`` resolves a chat id.

    :param chat_id: Chat whose store is needed.
    :param chat_manager: Optional already-resolved manager.
    :return: The manager holding the chat, or ``None`` when it is unknown.
    """
    if chat_manager is not None:
        return chat_manager

    from codx.junior.chat_manager import ChatManager
    from codx.junior.project.project_discover import find_all_projects

    try:
        projects = find_all_projects()
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("draft: cannot list projects for chat '%s': %s", chat_id, ex)
        return None

    for settings in projects.values():
        try:
            manager = ChatManager(settings=settings)
            if manager.find_by_id(chat_id) is not None:
                return manager
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning(
                "draft: project scan failed for chat '%s': %s", chat_id, ex
            )
    return None


def get_unsent_message(chat_id: str, chat_manager: Any = None) -> Optional[Message]:
    """
    Return the unsent draft of *chat_id*.

    Only the client composer that owns the draft should call this: the returned
    message is not part of the conversation (see :func:`_visible_messages`).

    :param chat_id: Chat whose draft is requested.
    :param chat_manager: Optional already-resolved ``ChatManager``.
    :return: The draft message, or ``None`` when the chat has no draft.
    """
    if not chat_id:
        return None
    manager = _resolve_chat_manager(chat_id, chat_manager)
    chat = manager.find_by_id(chat_id) if manager else None
    if chat is None:
        return None
    return _find_unsent_message(chat)


def upsert_unsent_message(
    chat_id: str,
    content: str,
    author: Optional[str] = None,
    chat_manager: Any = None,
) -> Message:
    """
    Create or replace the single unsent draft of *chat_id*.

    The existing draft ``doc_id`` is reused when updating, so the client keeps
    a stable key while typing. The chat keeps at most ONE unsent message.

    :param chat_id: Chat owning the draft.
    :param content: Current composer text.
    :param author: Optional username of the composer (stored in ``Message.user``).
    :param chat_manager: Optional already-resolved ``ChatManager``.
    :return: The persisted draft message.
    :raises ValueError: When *chat_id* is missing or unknown.
    """
    if not chat_id:
        raise ValueError("upsert_unsent_message requires a chat_id")

    manager = _resolve_chat_manager(chat_id, chat_manager)
    chat = manager.find_by_id(chat_id) if manager else None
    if chat is None:
        raise ValueError(f"upsert_unsent_message: chat '{chat_id}' not found")

    message = _find_unsent_message(chat)
    if message is None:
        message = Message(
            doc_id=str(uuid.uuid4()),
            role=ROLE_USER,
            content=content or '',
            user=author,
            unsent=True,
        )
        manager.add_message(chat=chat, message=message)
        return message

    message.role = ROLE_USER
    message.content = content or ''
    message.unsent = True
    if author is not None:
        message.user = author
    manager.update_message(chat=chat, message=message)
    return message


def delete_unsent_message(chat_id: str, chat_manager: Any = None) -> bool:
    """
    Remove the unsent draft of *chat_id*.

    :param chat_id: Chat whose draft must be discarded.
    :param chat_manager: Optional already-resolved ``ChatManager``.
    :return: ``True`` when a draft was deleted, ``False`` when there was none.
    """
    if not chat_id:
        return False
    manager = _resolve_chat_manager(chat_id, chat_manager)
    chat = manager.find_by_id(chat_id) if manager else None
    if chat is None:
        return False
    message = _find_unsent_message(chat)
    if message is None:
        return False
    manager.remove_message(chat=chat, message_doc_id=message.doc_id)
    return True


# Made with ❤️ by codx-junior