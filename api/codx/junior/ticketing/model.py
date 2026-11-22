"""
Ticketing models: cross-project requests expressed as chats.

A **ticket** is a request one project places on ANOTHER project — a child
project, a linked project (read-only, read/write or the legacy dependency list)
or the project that contains it — asking for a bug fix, a feature or a change.
The business case is the SaaS platform: while developing ``codx-junior-cloud`` a
bug is found in ``codx-junior``; instead of editing the other codebase, the
platform places a ticket there, receives an id and keeps using it to
communicate.

Design decisions
----------------
* **A ticket IS a chat.** The conversation is the protocol between the two
  projects: the requester describes the request, both sides comment, the target
  answers. No side-channel, no duplicated text.
* **The ticket chat is stored by its TARGET project** (``Chat.owner_project_id``)
  on the ``tickets`` board, so the target owns the queue and can review, accept,
  decline, schedule or process it with everything it already has (boards,
  profiles, autonomy, orchestration).
* **The kanban column is the visible state.** A ticket lives on the ``tickets``
  board and its column (``Inbox``/``Accepted``/``Declined``/``Done``/
  ``Abandoned``) is what the target's team actually moves, so dragging a card is
  a valid status change. :attr:`ChatTicket.status` is the mirrored value, kept in
  sync by :class:`codx.junior.ticketing.service.TicketService`.
* **``Chat.status`` is NOT the ticket status.** ``Chat.status`` is the kanban
  workflow state (``todo``/``doing``/``onhold``/``done``/``rejected``, see
  :data:`codx.junior.planning_manager.STATUS_ORDER`); the ticketing lifecycle is
  stored separately in :class:`ChatTicket`, exactly like the ``external_*`` /
  ``harness_*`` mirrors do not reuse it either.

Lifecycle
---------
```mermaid
stateDiagram-v2
    [*] --> open: ticket_create (source)
    open --> accepted: target triage
    open --> declined: target triage
    open --> done: target resolves
    open --> abandoned: ticket_abandon (source)
    accepted --> done: target resolves
    accepted --> declined: target refuses
    accepted --> abandoned: source withdraws
    declined --> accepted: target reopens
    declined --> abandoned: source withdraws
    done --> [*]
    abandoned --> [*]
```

Made with ❤️ by codx-junior
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field

# ``Chat.mode`` of a ticket chat. It is not ``task``/``chat``: a ticket behaves
# like a task (it lives on a board) but its lifecycle is the ticketing one.
TICKET_MODE = "ticket"

# Board (``Chat.board``) that holds the ticket queue of a project.
TICKET_BOARD = "tickets"
TICKET_BOARD_DESCRIPTION = (
    "Cross-project tickets: requests placed here by a parent project or by a "
    "linked project. The column is the ticket state."
)

# ── Status ───────────────────────────────────────────────────────────────────
TICKET_STATUS_OPEN = "open"
TICKET_STATUS_ACCEPTED = "accepted"
TICKET_STATUS_DECLINED = "declined"
TICKET_STATUS_DONE = "done"
TICKET_STATUS_ABANDONED = "abandoned"

TICKET_STATUSES = [
    TICKET_STATUS_OPEN,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
    TICKET_STATUS_ABANDONED,
]

# A ticket in a terminal state is closed: neither side changes it any more.
TICKET_TERMINAL_STATUSES = {TICKET_STATUS_DONE, TICKET_STATUS_ABANDONED}

# Columns of the ``tickets`` board, one per status. The column IS the visible
# state of a ticket, so a human moving the card changes the ticket.
TICKET_COLUMNS: Dict[str, str] = {
    TICKET_STATUS_OPEN: "Inbox",
    TICKET_STATUS_ACCEPTED: "Accepted",
    TICKET_STATUS_DECLINED: "Declined",
    TICKET_STATUS_DONE: "Done",
    TICKET_STATUS_ABANDONED: "Abandoned",
}

# Reverse lookup: column title (lowercase) -> status.
TICKET_COLUMN_STATUS: Dict[str, str] = {
    column.lower(): status for status, column in TICKET_COLUMNS.items()
}

# Statuses the TARGET project decides (its triage). The requester cannot
# accept or decline its own request, and cannot close work it does not own.
TICKET_TARGET_STATUSES = {
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
}

# Statuses only the SOURCE project may set (it withdraws its own request).
TICKET_SOURCE_STATUSES = {TICKET_STATUS_ABANDONED}

# Allowed transitions. A closed ticket stays closed.
TICKET_ALLOWED_TRANSITIONS: Dict[str, set] = {
    TICKET_STATUS_OPEN: {
        TICKET_STATUS_ACCEPTED,
        TICKET_STATUS_DECLINED,
        TICKET_STATUS_DONE,
        TICKET_STATUS_ABANDONED,
    },
    TICKET_STATUS_ACCEPTED: {
        TICKET_STATUS_DONE,
        TICKET_STATUS_DECLINED,
        TICKET_STATUS_ABANDONED,
    },
    TICKET_STATUS_DECLINED: {
        TICKET_STATUS_ACCEPTED,
        TICKET_STATUS_ABANDONED,
    },
    TICKET_STATUS_DONE: set(),
    TICKET_STATUS_ABANDONED: set(),
}

# ── Kind ─────────────────────────────────────────────────────────────────────
TICKET_KINDS = ["bug", "feature", "change", "question"]
TICKET_DEFAULT_KIND = "change"

# ── History actions ──────────────────────────────────────────────────────────
TICKET_ACTION_PLACED = "placed"
TICKET_ACTION_COMMENTED = "commented"
TICKET_ACTION_STATUS = "status"
TICKET_ACTION_ABANDONED = "abandoned"
TICKET_ACTION_UPDATED = "updated"

# Which side of a ticket a caller is on.
TICKET_SIDE_SOURCE = "outgoing"
TICKET_SIDE_TARGET = "incoming"
TICKET_SIDES = [TICKET_SIDE_SOURCE, TICKET_SIDE_TARGET]

# Relationship between the two projects of a ticket.
TICKET_RELATION_CHILD = "child"
TICKET_RELATION_LINKED = "linked"
TICKET_RELATION_PARENT = "parent"

MAX_TICKET_NAME_CHARS = 200


def now_iso() -> str:
    """Return the current timestamp in the ISO format used by chats."""
    return datetime.now().isoformat()


# ── Autonomy ─────────────────────────────────────────────────────────────────
# An **autonomous ticket** is a ticket whose target project works it without a
# human in the loop: it dives into the codebase, uses its tools, implements the
# request, runs the validation gate and only closes the ticket when the gate is
# green. The declaration lives with the ticket (``ChatTicket.autonomy``) because
# autonomy is a property of the request, not of the profile that happens to pick
# it up: the requester can ask for it and the target can configure it, but the
# ticket remains the single source of truth.
TICKET_AUTONOMY_OFF = "off"
TICKET_AUTONOMY_QUEUED = "queued"
TICKET_AUTONOMY_RUNNING = "running"
TICKET_AUTONOMY_PASSED = "passed"
TICKET_AUTONOMY_FAILED = "failed"
TICKET_AUTONOMY_BLOCKED = "blocked"
TICKET_AUTONOMY_CANCELLED = "cancelled"

TICKET_AUTONOMY_STATES = [
    TICKET_AUTONOMY_OFF,
    TICKET_AUTONOMY_QUEUED,
    TICKET_AUTONOMY_RUNNING,
    TICKET_AUTONOMY_PASSED,
    TICKET_AUTONOMY_FAILED,
    TICKET_AUTONOMY_BLOCKED,
    TICKET_AUTONOMY_CANCELLED,
]

#: States that mean "this ticket is being worked right now".
TICKET_AUTONOMY_ACTIVE_STATES = {TICKET_AUTONOMY_RUNNING}

#: States a run may start from (``failed`` is retried explicitly, never silently).
TICKET_AUTONOMY_RUNNABLE_STATES = {
    TICKET_AUTONOMY_OFF,
    TICKET_AUTONOMY_QUEUED,
    TICKET_AUTONOMY_FAILED,
    TICKET_AUTONOMY_BLOCKED,
    TICKET_AUTONOMY_CANCELLED,
}

#: Limits of one autonomous ticket run.
DEFAULT_TICKET_MAX_ROUNDS = 6
MAX_TICKET_ROUNDS = 40
DEFAULT_TICKET_RUNTIME_MINUTES = 30
MAX_TICKET_RUNTIME_MINUTES = 180
DEFAULT_TICKET_VALIDATION_TIMEOUT_SECONDS = 900
MAX_TICKET_VALIDATION_TIMEOUT_SECONDS = 3600

#: Profile used when neither the ticket nor the project names one. The first
#: profile that exists in the project is used (see the service resolver), so a
#: project without ``codx-junior`` still works with its own coding profile.
DEFAULT_TICKET_PROFILE_CANDIDATES = (
    "codx-junior",
    "software_developer",
    "coding_agent",
)

#: Validation sources. ``auto`` asks the QA gate for the project default suite;
#: ``commands`` runs the explicit argv lines configured on the ticket.
TICKET_VALIDATION_AUTO = "auto"
TICKET_VALIDATION_COMMANDS = "commands"
TICKET_VALIDATION_NONE = "none"
TICKET_VALIDATION_SOURCES = (
    TICKET_VALIDATION_AUTO,
    TICKET_VALIDATION_COMMANDS,
    TICKET_VALIDATION_NONE,
)


class TicketValidation(BaseModel):
    """
    How the target proves that an autonomous ticket is actually done.

    The gate is deliberately explicit: a ticket is only closed when a command
    or the project QA suite exits successfully. The model's own "it works" is
    never accepted as validation.

    Attributes:
        source: ``auto`` (QA gate default suite), ``commands`` (the ``commands``
            list) or ``none`` (the target explicitly opted out; the ticket can
            still close but it is reported as unvalidated).
        commands: Command lines executed with ``shell=False`` (ARGO style, one
            argv per line) in the ticket workspace. They are configured by the
            target project, never injected by the requester.
        suite: QA suite name for ``source='auto'`` (``auto`` uses the project
            default).
        paths: Optional test files/folders passed to the QA suite.
        timeout_seconds: Per command/run timeout.
        required: When False the ticket may close even when no validation is
            available. Defaults to True: "tested/validated" is the contract.
    """

    source: str = Field(
        default=TICKET_VALIDATION_AUTO,
        description="auto | commands | none",
    )
    commands: List[str] = Field(
        default_factory=list,
        description="Command lines run in the ticket workspace (source='commands').",
    )
    suite: Optional[str] = Field(
        default=None,
        description="QA suite name for source='auto' (None/auto = project default).",
    )
    paths: List[str] = Field(
        default_factory=list,
        description="Optional test files/folders passed to the QA suite.",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TICKET_VALIDATION_TIMEOUT_SECONDS,
        description="Timeout of one validation command/run, in seconds.",
    )
    required: bool = Field(
        default=True,
        description=(
            "When True (default) the ticket cannot be closed without a green "
            "validation; a project with no suite is reported as blocked."
        ),
    )

    def normalized(self) -> "TicketValidation":
        """Return a clamped copy of the validation declaration."""
        source = str(self.source or TICKET_VALIDATION_AUTO).strip().lower()
        if source not in TICKET_VALIDATION_SOURCES:
            source = TICKET_VALIDATION_AUTO
        commands = [
            str(command).strip()
            for command in self.commands or []
            if str(command or "").strip()
        ]
        paths = [
            str(path).strip() for path in self.paths or [] if str(path or "").strip()
        ]
        suite = str(self.suite or "").strip() or None
        return TicketValidation(
            source=source,
            commands=commands,
            suite=suite,
            paths=paths,
            timeout_seconds=max(
                30,
                min(
                    int(self.timeout_seconds or DEFAULT_TICKET_VALIDATION_TIMEOUT_SECONDS),
                    MAX_TICKET_VALIDATION_TIMEOUT_SECONDS,
                ),
            ),
            required=bool(self.required),
        )

    def effective_source(self) -> str:
        """Return the validation source actually used by the gate."""
        if self.source == TICKET_VALIDATION_COMMANDS and self.commands:
            return TICKET_VALIDATION_COMMANDS
        if self.source == TICKET_VALIDATION_NONE:
            return TICKET_VALIDATION_NONE
        return TICKET_VALIDATION_AUTO

    def describe(self) -> str:
        """Return a short human description of the gate."""
        source = self.effective_source()
        if source == TICKET_VALIDATION_COMMANDS:
            return "; ".join(self.commands) or "commands (none configured)"
        if source == TICKET_VALIDATION_NONE:
            return "none (the target opted out of validation)"
        return f"QA suite {self.suite or 'auto'}"


class TicketAutonomy(BaseModel):
    """
    Autonomy declaration and runtime state of one ticket.

    The declaration is written by the target project (or requested by the source
    at creation) and the runtime fields are maintained by
    :class:`codx.junior.ticketing.autonomy.TicketAutonomyService`. Keeping both
    on the ticket gives the requester a complete, cross-project view without a
    second store: rounds, attempts, validation evidence, workspace branch and
    the last delivery/error are all read from the ticket chat.
    """

    enabled: bool = Field(default=False, description="Work this ticket autonomously.")
    profile: Optional[str] = Field(
        default=None,
        description="Profile (agent) used for the run; None resolves the project default.",
    )
    auto_accept: bool = Field(
        default=False,
        description=(
            "Accept the ticket automatically when the run starts. Default false: "
            "the target accepts the request first (the safe gate for a "
            "cross-project request)."
        ),
    )
    max_rounds: int = Field(
        default=DEFAULT_TICKET_MAX_ROUNDS,
        description="Maximum agent rounds of one run (each round is validated).",
    )
    max_runtime_minutes: int = Field(
        default=DEFAULT_TICKET_RUNTIME_MINUTES,
        description="Hard wall-clock limit of one run.",
    )
    budget_cxjcoins: float = Field(
        default=0.0,
        description="Budget of one run (0 = project/chat default).",
    )
    validation: TicketValidation = Field(
        default_factory=TicketValidation,
        description="The gate that must be green before the ticket is closed.",
    )

    # -- runtime ---------------------------------------------------------
    state: str = Field(
        default=TICKET_AUTONOMY_OFF,
        description="off | queued | running | passed | failed | blocked | cancelled",
    )
    rounds: int = Field(default=0, description="Rounds executed by the current/last run.")
    attempts: int = Field(default=0, description="Runs started for this ticket.")
    last_error: Optional[str] = Field(default=None, description="Why the last run stopped.")
    last_validation: Optional[str] = Field(
        default=None, description="Output/evidence of the last validation gate."
    )
    validation_passed: bool = Field(
        default=False, description="The last validation gate was green."
    )
    delivery: Optional[str] = Field(
        default=None, description="Final report of the last run (markdown)."
    )
    queued_at: Optional[str] = Field(default=None)
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)
    last_round_at: Optional[str] = Field(default=None)
    next_retry_at: Optional[str] = Field(default=None)
    workspace_branch: Optional[str] = Field(default=None)
    workspace_path: Optional[str] = Field(default=None)
    requested_by: Optional[str] = Field(
        default=None, description="Project/user that asked for autonomy (source side)."
    )
    updated_at: Optional[str] = Field(default=None)

    def normalized(self) -> "TicketAutonomy":
        """Return a clamped copy of the declaration."""
        state = str(self.state or TICKET_AUTONOMY_OFF).strip().lower()
        if state not in TICKET_AUTONOMY_STATES:
            state = TICKET_AUTONOMY_OFF
        profile = str(self.profile or "").strip().lstrip("@") or None
        return TicketAutonomy(
            enabled=bool(self.enabled),
            profile=profile,
            auto_accept=bool(self.auto_accept),
            max_rounds=max(1, min(int(self.max_rounds or DEFAULT_TICKET_MAX_ROUNDS), MAX_TICKET_ROUNDS)),
            max_runtime_minutes=max(
                1,
                min(
                    int(self.max_runtime_minutes or DEFAULT_TICKET_RUNTIME_MINUTES),
                    MAX_TICKET_RUNTIME_MINUTES,
                ),
            ),
            budget_cxjcoins=max(0.0, float(self.budget_cxjcoins or 0.0)),
            validation=self.validation.normalized(),
            state=state,
            rounds=max(0, int(self.rounds or 0)),
            attempts=max(0, int(self.attempts or 0)),
            last_error=self.last_error or None,
            last_validation=self.last_validation or None,
            validation_passed=bool(self.validation_passed),
            delivery=self.delivery or None,
            queued_at=self.queued_at or None,
            started_at=self.started_at or None,
            finished_at=self.finished_at or None,
            last_round_at=self.last_round_at or None,
            next_retry_at=self.next_retry_at or None,
            workspace_branch=self.workspace_branch or None,
            workspace_path=self.workspace_path or None,
            requested_by=self.requested_by or None,
            updated_at=self.updated_at or None,
        )

    @property
    def active(self) -> bool:
        """Return True while a run is in progress."""
        return self.state in TICKET_AUTONOMY_ACTIVE_STATES

    def mark_queued(self) -> None:
        """Mark the ticket as queued for a run (clears the terminal metadata)."""
        self.state = TICKET_AUTONOMY_QUEUED
        self.queued_at = now_iso()
        self.finished_at = None
        self.last_error = None
        self.last_validation = None
        self.validation_passed = False
        self.delivery = None
        self.next_retry_at = None
        self.updated_at = self.queued_at

    def mark_running(self) -> None:
        """Mark the ticket as running and start a new attempt."""
        self.state = TICKET_AUTONOMY_RUNNING
        self.started_at = now_iso()
        self.finished_at = None
        self.last_error = None
        self.last_validation = None
        self.validation_passed = False
        self.delivery = None
        self.next_retry_at = None
        self.workspace_branch = None
        self.workspace_path = None
        self.rounds = 0
        self.attempts = max(1, int(self.attempts or 0) + 1)
        self.updated_at = self.started_at

    def mark_passed(self, delivery: str = "") -> None:
        """Mark the ticket as done and validated."""
        self.state = TICKET_AUTONOMY_PASSED
        self.finished_at = now_iso()
        self.validation_passed = True
        self.last_error = None
        self.next_retry_at = None
        self.delivery = delivery or None
        self.updated_at = self.finished_at

    def mark_failed(
        self,
        error: str,
        *,
        state: str = TICKET_AUTONOMY_FAILED,
        next_retry_at: Optional[str] = None,
    ) -> None:
        """Mark the run as failed/blocked without closing the ticket."""
        self.state = (
            state if state in TICKET_AUTONOMY_STATES else TICKET_AUTONOMY_FAILED
        )
        self.finished_at = now_iso()
        self.validation_passed = False
        self.last_error = error or None
        self.next_retry_at = next_retry_at or None
        self.updated_at = self.finished_at

    def summary(self) -> str:
        """Return a one-line description of the autonomy state."""
        if not self.enabled:
            return "autonomy off"
        parts = [f"autonomy {self.state}"]
        if self.rounds:
            parts.append(f"{self.rounds} round(s)")
        if self.attempts:
            parts.append(f"attempt {self.attempts}")
        if self.validation_passed:
            parts.append("validated")
        return " · ".join(parts)


def normalize_autonomy_state(value: Optional[str]) -> Optional[str]:
    """
    Normalize an autonomy state argument.

    Args:
        value: Raw state.

    Returns:
        One of :data:`TICKET_AUTONOMY_STATES`, or ``None`` when empty.

    Raises:
        ValueError: When the value is not a known autonomy state.
    """
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    if clean not in TICKET_AUTONOMY_STATES:
        raise ValueError(
            f"unknown autonomy state '{value}' "
            f"(valid states: {', '.join(TICKET_AUTONOMY_STATES)})"
        )
    return clean


def normalize_status(value: Optional[str]) -> Optional[str]:
    """
    Normalize a ticket status argument.

    Args:
        value: Raw status (case/spacing tolerant). ``None`` or empty means "not
            provided".

    Returns:
        One of :data:`TICKET_STATUSES`, or ``None`` when nothing was given.

    Raises:
        ValueError: When the value is not a known ticket status.
    """
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    if clean not in TICKET_STATUSES:
        raise ValueError(
            f"unknown ticket status '{value}' "
            f"(valid statuses: {', '.join(TICKET_STATUSES)})"
        )
    return clean


def normalize_kind(value: Optional[str]) -> Optional[str]:
    """
    Normalize a ticket kind argument.

    Args:
        value: Raw kind (``bug``, ``feature``, ``change``, ``question``).

    Returns:
        One of :data:`TICKET_KINDS`, or ``None`` when nothing was given.

    Raises:
        ValueError: When the value is not a known ticket kind.
    """
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    if clean not in TICKET_KINDS:
        raise ValueError(
            f"unknown ticket kind '{value}' "
            f"(valid kinds: {', '.join(TICKET_KINDS)})"
        )
    return clean


def status_for_column(column: Optional[str]) -> Optional[str]:
    """
    Map the column of a ticket chat to its ticket status.

    Args:
        column: Column title of the ticket chat.

    Returns:
        The matching status, or ``None`` when the column is not a ticket column
        (the target moved the card somewhere else, so the stored status wins).
    """
    return TICKET_COLUMN_STATUS.get(str(column or "").strip().lower())


def column_for_status(status: Optional[str]) -> str:
    """
    Return the ``tickets`` board column of a status.

    Args:
        status: Ticket status (unknown values fall back to the open status).

    Returns:
        The column title.
    """
    return TICKET_COLUMNS.get(
        str(status or "").strip().lower(), TICKET_COLUMNS[TICKET_STATUS_OPEN]
    )


def ticket_columns() -> List[str]:
    """Return the column titles of the ``tickets`` board, in workflow order."""
    return [TICKET_COLUMNS[status] for status in TICKET_STATUSES]


def can_transition(current: Optional[str], target: Optional[str]) -> bool:
    """
    Tell whether a ticket may move from *current* to *target*.

    Args:
        current: Current ticket status (empty counts as ``open``).
        target: Wanted ticket status.

    Returns:
        True when the transition is allowed by
        :data:`TICKET_ALLOWED_TRANSITIONS`.
    """
    source = str(current or TICKET_STATUS_OPEN).strip().lower()
    wanted = str(target or "").strip().lower()
    if not wanted:
        return False
    allowed = TICKET_ALLOWED_TRANSITIONS.get(source)
    if allowed is None:
        return False
    return wanted in allowed


class ChatTicketEvent(BaseModel):
    """
    Append-only record of one thing that happened to a ticket.

    The history is the audit trail both projects read: who placed the ticket,
    who commented, which project moved it to which status and why.
    """

    doc_id: Optional[str] = Field(default=None, description="Unique identifier of this event")
    at: str = Field(default_factory=now_iso, description="Timestamp of the event")
    action: str = Field(
        default=TICKET_ACTION_UPDATED,
        description="placed | commented | status | abandoned | updated",
    )
    from_status: Optional[str] = Field(default=None, description="Status before the event")
    status: Optional[str] = Field(default=None, description="Status after the event")
    project_id: Optional[str] = Field(default=None, description="Project that produced the event")
    project_name: Optional[str] = Field(default=None, description="Name of that project")
    user: Optional[str] = Field(default=None, description="User/agent that produced the event")
    note: Optional[str] = Field(default=None, description="Human readable reason")


class ChatTicket(BaseModel):
    """
    Ticketing data attached to a ticket chat (``Chat.ticket``).

    It carries the two ends of the request (source and target project) plus the
    lifecycle the two projects share. ``id`` equals the chat id, so the ticket id
    returned by ``ticket_create`` is directly usable as a chat id.

    The ticket chat is stored by the TARGET project (``Chat.owner_project_id``),
    therefore ``target_project_id`` and ``owner_project_id`` always match; the
    requester is remembered here so a target can answer without guessing.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Ticket id (== chat id)")
    source_project_id: Optional[str] = Field(
        default=None, description="Project that placed the request"
    )
    source_project_name: Optional[str] = Field(
        default=None, description="Display name of the requesting project"
    )
    target_project_id: Optional[str] = Field(
        default=None, description="Project that owns and processes the request"
    )
    target_project_name: Optional[str] = Field(
        default=None, description="Display name of the target project"
    )
    status: str = Field(
        default=TICKET_STATUS_OPEN,
        description="Ticketing lifecycle status (see TICKET_STATUSES)",
    )
    kind: str = Field(default=TICKET_DEFAULT_KIND, description="bug | feature | change | question")
    reference: Optional[str] = Field(
        default=None,
        description=(
            "Opaque reference of the request in the SOURCE project (issue, "
            "incident, deployment id...). Never interpreted by the target."
        ),
    )
    requested_by: Optional[str] = Field(
        default=None, description="User/agent of the source project that placed the ticket"
    )
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    history: List[ChatTicketEvent] = Field(
        default=[], description="Append-only ticketing history (both directions)"
    )
    autonomy: Optional[TicketAutonomy] = Field(
        default=None,
        description=(
            "Autonomous work declaration/state of the ticket. None means the "
            "ticket is handled manually; an enabled block makes the target "
            "project work it until the validation gate is green."
        ),
    )

    def append_event(
        self,
        action: str,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        user: Optional[str] = None,
        note: Optional[str] = None,
        from_status: Optional[str] = None,
        status: Optional[str] = None,
    ) -> ChatTicketEvent:
        """
        Append one event to the ticket history and refresh ``updated_at``.

        Args:
            action: Event action (see the ``TICKET_ACTION_*`` constants).
            project_id: Project that produced the event.
            project_name: Display name of that project.
            user: User/agent that produced the event.
            note: Human readable reason.
            from_status: Status before the event.
            status: Status after the event.

        Returns:
            The appended event.
        """
        event = ChatTicketEvent(
            doc_id=str(uuid.uuid4()),
            at=now_iso(),
            action=action,
            from_status=from_status,
            status=status,
            project_id=project_id,
            project_name=project_name,
            user=user,
            note=note,
        )
        self.history.append(event)
        self.updated_at = event.at
        return event

    def side_of(self, project_id: Optional[str]) -> Optional[str]:
        """
        Return which side *project_id* is on.

        Args:
            project_id: Project id of the caller.

        Returns:
            :data:`TICKET_SIDE_TARGET` when the caller owns the queue,
            :data:`TICKET_SIDE_SOURCE` when it is the requester, else ``None``.
        """
        wanted = str(project_id or "").strip()
        if not wanted:
            return None
        if wanted == str(self.target_project_id or "").strip():
            return TICKET_SIDE_TARGET
        if wanted == str(self.source_project_id or "").strip():
            return TICKET_SIDE_SOURCE
        return None


class TicketRecord(BaseModel):
    """
    Flattened view of a ticket, returned by the tools and the API.

    A record is built either from a ticket chat (:meth:`from_chat`, complete and
    authoritative) or from a ledger entry (:meth:`from_ledger`, cheap and
    possibly stale when the target project is not reachable).
    """

    ticket_id: str = Field(default="", description="Ticket id (== chat id)")
    chat_id: str = Field(default="", description="Chat id of the ticket conversation")
    name: str = Field(default="")
    description: str = Field(default="")
    status: str = Field(default=TICKET_STATUS_OPEN)
    kind: str = Field(default=TICKET_DEFAULT_KIND)
    priority: Optional[str] = Field(default=None)
    reference: Optional[str] = Field(default=None)
    requested_by: Optional[str] = Field(default=None)
    direction: str = Field(
        default=TICKET_SIDE_SOURCE,
        description="outgoing (this project placed it) | incoming (it was placed here)",
    )
    source_project_id: Optional[str] = Field(default=None)
    source_project_name: Optional[str] = Field(default=None)
    target_project_id: Optional[str] = Field(default=None)
    target_project_name: Optional[str] = Field(default=None)
    board: Optional[str] = Field(default=None)
    column: Optional[str] = Field(default=None)
    message_count: int = Field(default=0)
    last_message: Optional[str] = Field(default=None)
    last_message_at: Optional[str] = Field(default=None)
    available: bool = Field(
        default=True,
        description=(
            "False when the ticket chat could not be read (target project not "
            "reachable or the chat was deleted): the record then comes from the "
            "requester's ledger only."
        ),
    )
    autonomy: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Autonomy declaration and live state (rounds, attempts, validation, "
            "workspace branch, delivery). None means the ticket is manual."
        ),
    )
    created_at: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)

    @classmethod
    def from_chat(cls, chat: Any, direction: str) -> "TicketRecord":
        """
        Build a record from a ticket chat (authoritative).

        Args:
            chat: The ticket :class:`~codx.junior.db.Chat`.
            direction: ``outgoing`` when the reader placed the ticket,
                ``incoming`` when the reader owns the queue.

        Returns:
            The flattened record.
        """
        ticket = getattr(chat, "ticket", None)
        messages = [
            message for message in (getattr(chat, "messages", None) or [])
            if not getattr(message, "unsent", False)
        ]
        last = messages[-1] if messages else None
        content = str(getattr(last, "content", "") or "").strip()
        if len(content) > 400:
            content = content[:397] + "..."
        autonomy = getattr(ticket, "autonomy", None)
        return cls(
            ticket_id=str(getattr(ticket, "id", "") or getattr(chat, "id", "") or ""),
            chat_id=str(getattr(chat, "id", "") or ""),
            name=str(getattr(chat, "name", "") or ""),
            description=str(getattr(chat, "description", "") or ""),
            status=str(getattr(ticket, "status", TICKET_STATUS_OPEN) or TICKET_STATUS_OPEN),
            kind=str(getattr(ticket, "kind", TICKET_DEFAULT_KIND) or TICKET_DEFAULT_KIND),
            priority=getattr(chat, "priority", None),
            reference=getattr(ticket, "reference", None),
            requested_by=getattr(ticket, "requested_by", None),
            direction=direction,
            source_project_id=getattr(ticket, "source_project_id", None),
            source_project_name=getattr(ticket, "source_project_name", None),
            target_project_id=getattr(ticket, "target_project_id", None),
            target_project_name=getattr(ticket, "target_project_name", None),
            board=getattr(chat, "board", None) or None,
            column=getattr(chat, "column", None) or None,
            message_count=len(messages),
            last_message=content or None,
            last_message_at=str(getattr(last, "created_at", "") or None) if last else None,
            available=True,
            autonomy=(
                autonomy.model_dump()
                if hasattr(autonomy, "model_dump")
                else (autonomy if isinstance(autonomy, dict) else None)
            ),
            created_at=getattr(ticket, "created_at", None) or getattr(chat, "created_at", None),
            updated_at=getattr(ticket, "updated_at", None) or getattr(chat, "updated_at", None),
        )

    @classmethod
    def from_ledger(cls, entry: Dict[str, Any], direction: str = TICKET_SIDE_SOURCE) -> "TicketRecord":
        """
        Build a record from a ledger entry (requester side, possibly stale).

        Args:
            entry: One entry of ``.codx/tickets/tickets.json``.
            direction: Direction to stamp on the record.

        Returns:
            The flattened record, flagged ``available=False``.
        """
        data = dict(entry or {})
        ticket_id = str(data.get("ticket_id") or data.get("chat_id") or "")
        return cls(
            ticket_id=ticket_id,
            chat_id=str(data.get("chat_id") or ticket_id),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            status=str(data.get("status") or TICKET_STATUS_OPEN),
            kind=str(data.get("kind") or TICKET_DEFAULT_KIND),
            priority=data.get("priority"),
            reference=data.get("reference"),
            requested_by=data.get("requested_by"),
            direction=direction,
            source_project_id=data.get("source_project_id"),
            source_project_name=data.get("source_project_name"),
            target_project_id=data.get("target_project_id"),
            target_project_name=data.get("target_project_name"),
            board=data.get("board"),
            column=data.get("column"),
            available=False,
            autonomy=data.get("autonomy"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_ledger(self) -> Dict[str, Any]:
        """
        Return the ledger entry of this record.

        Returns:
            A JSON-serialisable dict with the fields the requester needs to find,
            list and re-open the ticket without scanning other projects.
        """
        return {
            "ticket_id": self.ticket_id,
            "chat_id": self.chat_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "kind": self.kind,
            "priority": self.priority,
            "reference": self.reference,
            "requested_by": self.requested_by,
            "source_project_id": self.source_project_id,
            "source_project_name": self.source_project_name,
            "target_project_id": self.target_project_id,
            "target_project_name": self.target_project_name,
            "board": self.board,
            "column": self.column,
            "autonomy": self._ledger_autonomy(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def _ledger_autonomy(self) -> Optional[Dict[str, Any]]:
        """Return a compact, redaction-safe autonomy snapshot for the ledger.

        The ledger is a cache read on every query and is written outside the
        chat redaction path: it must never carry validation output or a delivery
        body (they may quote test logs/secrets). The full evidence lives in the
        ticket chat, which CISO redacts on write. The state, counters and a short
        error are enough for the requester to follow the run.
        """
        if not self.autonomy:
            return None
        data = dict(self.autonomy)
        for key in ("delivery", "last_validation"):
            data.pop(key, None)
        error = data.get("last_error")
        if isinstance(error, str) and len(error) > 500:
            data["last_error"] = error[:500] + "…"
        return data

    def summary(self) -> str:
        """
        Return a one-line summary of the ticket.

        Returns:
            ``"<id> · <kind> · <status> · <name> → <target>"``.
        """
        target = self.target_project_name or self.target_project_id or "?"
        autonomy = ""
        if self.autonomy and self.autonomy.get("enabled"):
            autonomy = f" · {self.autonomy.get('state') or 'off'}"
        return (
            f"{self.ticket_id} · {self.kind} · {self.status}{autonomy} · "
            f"{self.name} → {target}"
        )

# Made with ❤️ by codx-junior
