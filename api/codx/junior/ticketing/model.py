"""
Ticketing models: cross-project requests expressed as chats.

A **ticket** is a request one project places on ANOTHER project — a child
project or a linked project with read/write permission — asking for a bug fix, a
feature or a change. The business case is the SaaS platform: while developing
``codx-junior-cloud`` a bug is found in ``codx-junior``; instead of editing the
other codebase, the platform places a ticket there, receives an id and keeps
using it to communicate.

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

MAX_TICKET_NAME_CHARS = 200


def now_iso() -> str:
    """Return the current timestamp in the ISO format used by chats."""
    return datetime.now().isoformat()


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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def summary(self) -> str:
        """
        Return a one-line summary of the ticket.

        Returns:
            ``"<id> · <kind> · <status> · <name> → <target>"``.
        """
        target = self.target_project_name or self.target_project_id or "?"
        return (
            f"{self.ticket_id} · {self.kind} · {self.status} · {self.name} → {target}"
        )

# Made with ❤️ by codx-junior
