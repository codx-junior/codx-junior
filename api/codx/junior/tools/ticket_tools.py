"""
Ticketing tools: let a model place, follow, answer and withdraw tickets.

These are the tools other projects use to ask for a change without editing a
codebase they do not own. The flow is the business case of the SaaS platform::

    codx-junior-cloud                     codx-junior
    ------------------                    -----------
    ticket_create(target_project=...) --> ticket chat on the "tickets" board
                     <-- ticket_id
    ticket_query(ticket_id)           <-- status: open / accepted / declined / done
    ticket_update(comment=...)        <-> the conversation IS the protocol
    ticket_abandon(ticket_id)         --> the request is withdrawn

Ownership rules (enforced by :class:`codx.junior.ticketing.service.TicketService`,
so the model cannot get them wrong):

* the **target** project accepts, declines or closes a ticket — the requester
  cannot decide on its own behalf;
* the **requester** abandons (withdraws) its ticket;
* **both** comment, rename and re-prioritise.

The tools are thin: all the logic (target resolution, ledger, board, lifecycle,
transitions) lives in the service, which is also what the HTTP API uses.

Made with ❤️ by codx-junior
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from codx.junior.ticketing.model import (
    TICKET_BOARD,
    TICKET_KINDS,
    TICKET_SIDE_SOURCE,
    TICKET_SIDE_TARGET,
    TICKET_STATUSES,
    TICKET_STATUS_ABANDONED,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
    TICKET_STATUS_OPEN,
    TicketRecord,
)
from codx.junior.ticketing.service import TicketError, TicketService

from .model import ToolResponse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from codx.junior.settings import CODXJuniorSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


def _resolve_settings(**kwargs) -> "CODXJuniorSettings":
    """
    Return the project settings of the running context.

    Args:
        **kwargs: Tool keyword arguments (``settings`` is injected by the
            agent runtime).

    Returns:
        The project settings.

    Raises:
        ValueError: When the runtime injected neither ``settings`` nor a session.
    """
    settings = kwargs.get("settings")
    if settings is None:
        session = kwargs.get("session")
        settings = getattr(session, "settings", None) if session is not None else None
    if settings is None:
        raise ValueError(
            "the ticketing tools require a project session or project settings "
            "(none were injected by the runtime)"
        )
    return settings


def _resolve_username(**kwargs) -> Optional[str]:
    """
    Return the username behind the running session, when it is known.

    Args:
        **kwargs: Tool keyword arguments.

    Returns:
        The username or ``None``.
    """
    session = kwargs.get("session")
    user = getattr(session, "user", None) if session is not None else None
    return getattr(user, "username", None) if user else None


def _service(**kwargs) -> TicketService:
    """
    Build the :class:`TicketService` of the running project.

    Args:
        **kwargs: Tool keyword arguments.

    Returns:
        A service bound to the acting project and user.
    """
    settings = _resolve_settings(**kwargs)
    return TicketService(settings=settings, user=_resolve_username(**kwargs))


def _json(payload: Any) -> str:
    """Serialise *payload* as compact JSON (the LLM-facing half of a response)."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _record_payload(record: TicketRecord) -> Dict[str, Any]:
    """Return the dict a tool hands to the model for one ticket."""
    payload = record.dict()
    payload["board"] = payload.get("board") or TICKET_BOARD
    return payload


def _render_list(records: List[TicketRecord]) -> str:
    """
    Render a list of tickets as a markdown table plus their details.

    Args:
        records: The tickets to render.

    Returns:
        The markdown block shown to the user.
    """
    if not records:
        return "No tickets found."

    lines = [
        f"> {len(records)} ticket(s)",
        "",
        "**Ticket** | **Kind** | **Status** | **Direction** | **Project** | **Name**",
        "---|---|---|---|---|---",
    ]
    for record in records:
        other = (
            record.target_project_name or record.target_project_id or "?"
            if record.direction == TICKET_SIDE_SOURCE
            else record.source_project_name or record.source_project_id or "?"
        )
        lines.append(
            f"`{record.ticket_id}` | {record.kind} | **{record.status}** | "
            f"{record.direction} | {other} | {record.name}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool JSON definitions
# ---------------------------------------------------------------------------

TICKET_CREATE_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "ticket_create",
        "description": (
            "Place a TICKET on another project: a request for a bug fix, a feature or a "
            "change that must be implemented in a project this one does not own. "
            "Valid targets are the CHILD projects of this one, EVERY linked project "
            "(read-only, read/write or the legacy dependency list) and the PARENT "
            "projects that contain this one. A ticket is a request, so a read-only "
            "link may receive one. "
            "The ticket is a chat stored by the target project on its 'tickets' board, "
            "so the target reviews it, accepts/declines it and implements it with its "
            "own profiles; this project keeps full control of the request through the "
            "returned ticket id (query, comment, abandon). "
            f"Kinds: {', '.join(TICKET_KINDS)}. "
            "Set `autonomous=true` when the target should work the ticket without "
            "a human until its validation gate is green. "
            "Returns the ticket id: quote it in every later ticket_query / "
            "ticket_update / ticket_abandon call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_project": {
                    "type": "string",
                    "description": (
                        "Project that must do the work: its name or id. It must be a "
                        "child, linked or parent project."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Short title of the request (what must change).",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Complete request the target will read: context, observed "
                        "behaviour, expected behaviour and acceptance criteria. This is "
                        "the ticket body, not a summary."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": list(TICKET_KINDS),
                    "description": f"Request kind (default 'change'). One of: {', '.join(TICKET_KINDS)}.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Priority hint for the target's triage.",
                },
                "reference": {
                    "type": "string",
                    "description": (
                        "Optional id/url of this request in the CURRENT project "
                        "(issue, incident, deployment) so both sides can cross-check."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "First message of the ticket conversation (defaults to 'description').",
                },
                "profiles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Profiles the target project may use (a suggestion it can change).",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files of the TARGET project the request refers to.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for the target's triage.",
                },
                "assignees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Usernames of the target project to assign the ticket to.",
                },
                "autonomous": {
                    "type": "boolean",
                    "description": (
                        "Ask the target to work the ticket autonomously: it dives "
                        "into the codebase, implements the request, runs the "
                        "validation gate and only closes it when the gate is green. "
                        "The target still owns the profile and the validation."
                    ),
                },
                "autonomy_profile": {
                    "type": "string",
                    "description": (
                        "Optional profile (agent) for the autonomous run, e.g. "
                        "`software_developer`. It must exist in the target project."
                    ),
                },
            },
            "required": ["target_project", "name", "description"],
        },
    },
}

TICKET_UPDATE_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "ticket_update",
        "description": (
            "Update a ticket: add a comment to its conversation and/or change its "
            "status or metadata. The conversation is the channel between the two "
            "projects, so a comment is how a requester answers a question. "
            "Status ownership: the TARGET project sets 'accepted', 'declined' and "
            "'done' (the requester cannot decide on its own request); the requester "
            "withdraws with ticket_abandon (status 'abandoned'). Both sides may "
            "comment, rename, change the kind or re-prioritise. "
            f"Statuses: {', '.join(TICKET_STATUSES)}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket id returned by ticket_create.",
                },
                "comment": {
                    "type": "string",
                    "description": "Message appended to the ticket conversation.",
                },
                "status": {
                    "type": "string",
                    "enum": list(TICKET_STATUSES),
                    "description": (
                        "New status. The target sets accepted/declined/done; abandoned "
                        "is reserved for ticket_abandon."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": list(TICKET_KINDS),
                    "description": "New kind.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "New priority.",
                },
                "name": {
                    "type": "string",
                    "description": "New ticket title.",
                },
                "profiles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Profiles to use for the ticket (replaces the list).",
                },
                "assignees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Usernames assigned to the ticket (replaces the list).",
                },
                "note": {
                    "type": "string",
                    "description": "Reason stored in the ticket history.",
                },
            },
            "required": ["ticket_id"],
        },
    },
}

TICKET_QUERY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "ticket_query",
        "description": (
            "Query tickets. Without arguments it lists this project's tickets in both "
            "directions: 'outgoing' (placed by this project on another one) and "
            "'incoming' (placed here by a parent or a linked project). Pass ticket_id "
            "to read one ticket (status, board column, last message). Statuses are "
            "refreshed from the ticket chat, so a status changed by the target is "
            "always visible here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Return only this ticket.",
                },
                "direction": {
                    "type": "string",
                    "enum": [TICKET_SIDE_SOURCE, TICKET_SIDE_TARGET, "all"],
                    "description": (
                        "'outgoing' = placed by this project, 'incoming' = placed here, "
                        "'all' (default) = both."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": list(TICKET_STATUSES),
                    "description": "Only tickets in this status.",
                },
                "target_project": {
                    "type": "string",
                    "description": "Only tickets whose target project matches this name/id.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tickets (default 20).",
                },
                "include_closed": {
                    "type": "boolean",
                    "description": "Include done/abandoned tickets (default true).",
                },
            },
            "required": [],
        },
    },
}

TICKET_ABANDON_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "ticket_abandon",
        "description": (
            "Abandon (withdraw) a ticket THIS project placed on another one, with an "
            "optional reason. The ticket is not deleted: it moves to the 'Abandoned' "
            "column of the target's board and the conversation is kept, so the target "
            "sees the request was called off. Only the requesting project may abandon "
            "a ticket; a target declines or closes it with ticket_update instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket id returned by ticket_create.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the request is withdrawn.",
                },
            },
            "required": ["ticket_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def ticket_create(
    target_project: str,
    name: str,
    description: str,
    kind: Optional[str] = None,
    priority: Optional[str] = None,
    reference: Optional[str] = None,
    content: Optional[str] = None,
    profiles: Optional[List[str]] = None,
    files: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    autonomous: bool = False,
    autonomy_profile: Optional[str] = None,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
) -> ToolResponse:
    """
    Place a ticket on a child, linked or parent project.

    Args:
        target_project: Target project name or id (child, linked or parent).
        name: Short title of the request.
        description: Complete request (context, observed vs expected behaviour,
            acceptance criteria).
        kind: ``bug`` / ``feature`` / ``change`` / ``question`` (default ``change``).
        priority: Optional ``high`` / ``medium`` / ``low``.
        reference: Optional id of the request in this project.
        content: First message of the conversation (defaults to ``description``).
        profiles: Optional profiles the target may use.
        files: Optional files of the target project the request refers to.
        tags: Optional triage tags.
        assignees: Optional usernames of the target project.
        autonomous: Ask the target project to work the ticket without a human
            until its validation gate is green. The requester only enables the
            queue; the target owns the profile and the validation gate.
        autonomy_profile: Optional profile (agent) for the autonomous run (must
            exist in the target project).
        settings: Project settings (injected by the agent runtime).
        session: Active session (injected by the agent runtime).

    Returns:
        ToolResponse with the created ticket, its id and the target project.

    Raises:
        ValueError: When the target or the ticket data is invalid.
    """
    service = _service(settings=settings, session=session)
    try:
        record = service.create(
            target=target_project,
            name=name,
            description=description,
            kind=kind,
            priority=priority,
            reference=reference,
            content=content,
            profiles=profiles,
            files=files,
            tags=tags,
            assignees=assignees,
            autonomous=autonomous,
            autonomy_profile=autonomy_profile,
        )
    except TicketError as ex:
        logger.warning("ticket_create: %s", ex)
        raise ValueError(str(ex)) from ex

    user_lines = [
        f"✅ Ticket placed: **{record.name}**",
        f"- id: `{record.ticket_id}`",
        f"- project: **{record.target_project_name}** → board `{record.board}` / "
        f"`{record.column}`",
        f"- kind: {record.kind} · status: {record.status}",
    ]
    if autonomous:
        autonomy = record.autonomy or {}
        user_lines.append(
            f"- autonomy: **{autonomy.get('state') or 'queued'}** · the target "
            "will dive, implement and validate it without a human"
        )
    user_lines += [
        "",
        "The target project owns the queue now: it reviews, accepts or declines the "
        "ticket and implements it with its own profiles. Follow it with "
        f"`ticket_query(ticket_id='{record.ticket_id}')`, answer with "
        "`ticket_update(comment=...)` and withdraw it with `ticket_abandon`.",
    ]
    if autonomous:
        user_lines.append(
            "Track the autonomous run with "
            f"`ticket_autonomy(action='status', ticket_id='{record.ticket_id}')`."
        )
    return ToolResponse(
        user_response="\n".join(user_lines),
        llm_response=_json(
            {
                "status": "created",
                "ticket_id": record.ticket_id,
                "chat_id": record.chat_id,
                "ticket": _record_payload(record),
            }
        ),
    )


def ticket_update(
    ticket_id: str,
    comment: Optional[str] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    priority: Optional[str] = None,
    name: Optional[str] = None,
    profiles: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    note: Optional[str] = None,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
) -> ToolResponse:
    """
    Comment a ticket and/or change its status or metadata.

    The target project sets ``accepted`` / ``declined`` / ``done``; the requesting
    project comments, re-prioritises and withdraws (``ticket_abandon``).

    Args:
        ticket_id: Ticket id (== chat id).
        comment: Message appended to the ticket conversation.
        status: New status (validated against the allowed transitions).
        kind: New kind.
        priority: New priority.
        name: New title.
        profiles: Profiles to use (replaces the list).
        assignees: Usernames assigned (replaces the list).
        note: Reason stored in the ticket history.
        settings: Project settings (injected by the agent runtime).
        session: Active session (injected by the agent runtime).

    Returns:
        ToolResponse with the updated ticket.

    Raises:
        ValueError: When the ticket does not exist, the caller has no access or the
            transition/owner rules are violated.
    """
    service = _service(settings=settings, session=session)
    try:
        record = service.update(
            ticket_id=ticket_id,
            comment=comment,
            status=status,
            kind=kind,
            priority=priority,
            name=name,
            profiles=profiles,
            assignees=assignees,
            note=note,
        )
    except TicketError as ex:
        logger.warning("ticket_update: %s", ex)
        raise ValueError(str(ex)) from ex

    user_lines = [
        f"✅ Ticket updated: **{record.name}** (`{record.ticket_id}`)",
        f"- status: **{record.status}** · kind: {record.kind}",
    ]
    if record.column:
        user_lines.append(f"- board: `{record.board}` / `{record.column}`")
    if comment:
        user_lines.append(f"- comment sent to {record.target_project_name}")
    return ToolResponse(
        user_response="\n".join(user_lines),
        llm_response=_json({"status": "updated", "ticket": _record_payload(record)}),
    )


def ticket_query(
    ticket_id: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    target_project: Optional[str] = None,
    limit: Optional[int] = None,
    include_closed: bool = True,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
) -> ToolResponse:
    """
    Query one ticket or list the tickets of this project.

    Args:
        ticket_id: Return only this ticket.
        direction: ``outgoing`` / ``incoming`` / ``all`` (default ``all``).
        status: Only tickets in this status.
        target_project: Only tickets whose target project matches this reference.
        limit: Maximum number of tickets (default 20).
        include_closed: Include done/abandoned tickets (default True).
        settings: Project settings (injected by the agent runtime).
        session: Active session (injected by the agent runtime).

    Returns:
        ToolResponse with one markdown row per ticket and the JSON payload.

    Raises:
        ValueError: When the direction/status is unknown.
    """
    service = _service(settings=settings, session=session)
    try:
        records = service.query(
            ticket_id=ticket_id,
            direction=direction,
            status=status,
            target=target_project,
            limit=limit,
            include_closed=include_closed,
        )
    except TicketError as ex:
        logger.warning("ticket_query: %s", ex)
        raise ValueError(str(ex)) from ex

    if ticket_id and not records:
        user_message = f"No ticket '{ticket_id}' found for this project."
    elif ticket_id:
        record = records[0]
        user_message = TicketService.render(record)
        if record.status == TICKET_STATUS_OPEN:
            user_message += "\n\nThe target has not triaged it yet."
        elif record.status == TICKET_STATUS_ACCEPTED:
            user_message += "\n\nThe target accepted it and will implement it."
        elif record.status == TICKET_STATUS_DECLINED:
            user_message += "\n\nThe target declined it; check the conversation."
        elif record.status == TICKET_STATUS_DONE:
            user_message += "\n\nThe target finished it."
        elif record.status == TICKET_STATUS_ABANDONED:
            user_message += "\n\nThe request was abandoned."
    else:
        user_message = _render_list(records)

    return ToolResponse(
        user_response=user_message,
        llm_response=_json(
            {
                "count": len(records),
                "tickets": [_record_payload(record) for record in records],
            }
        ),
    )


def ticket_abandon(
    ticket_id: str,
    reason: Optional[str] = None,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
) -> ToolResponse:
    """
    Withdraw a ticket this project placed.

    Args:
        ticket_id: Ticket id (== chat id).
        reason: Why the request is withdrawn.
        settings: Project settings (injected by the agent runtime).
        session: Active session (injected by the agent runtime).

    Returns:
        ToolResponse with the abandoned ticket.

    Raises:
        ValueError: When the ticket does not exist, this project is not the
            requester or the ticket is already closed.
    """
    service = _service(settings=settings, session=session)
    try:
        record = service.abandon(ticket_id=ticket_id, reason=reason)
    except TicketError as ex:
        logger.warning("ticket_abandon: %s", ex)
        raise ValueError(str(ex)) from ex

    user_lines = [
        f"✅ Ticket abandoned: **{record.name}** (`{record.ticket_id}`)",
        f"- board: `{record.board}` / `{record.column}`",
        "- the conversation is kept so the target sees the request was called off",
    ]
    if reason:
        user_lines.append(f"- reason: {reason}")
    return ToolResponse(
        user_response="\n".join(user_lines),
        llm_response=_json({"status": "abandoned", "ticket": _record_payload(record)}),
    )

# Made with ❤️ by codx-junior


# ---------------------------------------------------------------------------
# Autonomous tickets
# ---------------------------------------------------------------------------

TICKET_AUTONOMY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "ticket_autonomy",
        "description": (
            "Run, configure and inspect the AUTONOMOUS work of a ticket. An "
            "autonomous ticket is worked by its TARGET project without a human in "
            "the loop: the agent dives into the codebase, implements the request, "
            "runs the validation gate after every round and only closes the ticket "
            "when the gate is green. This tool is available to the target project "
            "(the ticket owner). Actions: status, enable, disable, configure, "
            "queue, run, cancel. Use `validation` to declare how the ticket is "
            "proven (commands or the project QA suite); `run` is synchronous and "
            "can take minutes, so prefer `queue` and let the background worker run "
            "it unless the user asks to wait."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "list", "enable", "disable", "configure",
                        "queue", "run", "cancel",
                    ],
                    "description": "Operation to perform.",
                },
                "ticket_id": {
                    "type": "string",
                    "description": (
                        "Ticket id (required except for status/list without id)."
                    ),
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable/disable autonomous work (configure).",
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "Profile (agent) that works the ticket, e.g. "
                        "`software_developer`. Default resolves the project profile."
                    ),
                },
                "auto_accept": {
                    "type": "boolean",
                    "description": "Accept the open ticket when the run starts.",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": (
                        "Maximum agent rounds (each round ends with a validation "
                        "gate). Default 6, max 40."
                    ),
                },
                "max_runtime_minutes": {
                    "type": "integer",
                    "description": "Hard wall-clock limit of one run (default 30).",
                },
                "budget_cxjcoins": {
                    "type": "number",
                    "description": "Budget of one run (0 = project default).",
                },
                "validation": {
                    "type": "object",
                    "description": (
                        "The gate that must be green before the ticket is closed."
                    ),
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["auto", "commands", "none"],
                            "description": (
                                "auto: project QA default suite; commands: the "
                                "commands list; none: explicitly unvalidated."
                            ),
                        },
                        "commands": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Command lines run in the ticket workspace "
                                "(shell disabled)."
                            ),
                        },
                        "suite": {
                            "type": "string",
                            "description": "QA suite name for source=auto.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional test paths for the QA suite.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Timeout of one validation run.",
                        },
                        "required": {
                            "type": "boolean",
                            "description": (
                                "When true (default) the ticket cannot close "
                                "without a green validation."
                            ),
                        },
                    },
                },
            },
            "required": ["action"],
        },
    },
}


def _autonomy_service(**kwargs) -> Any:
    """Build the :class:`TicketAutonomyService` of the running project."""
    from codx.junior.ticketing.autonomy import TicketAutonomyService

    settings = _resolve_settings(**kwargs)
    return TicketAutonomyService(
        settings=settings,
        user=kwargs.get("session").user if kwargs.get("session") else None,
    )


def _render_autonomy_status(payload: Dict[str, Any]) -> str:
    """Render one autonomy status payload as markdown."""
    autonomy = payload.get("autonomy") or {}
    lines = [
        f"**{payload.get('name')}** — `{payload.get('ticket_id')}`",
        f"- ticket status: **{payload.get('status')}** · autonomy: "
        f"**{autonomy.get('state') or 'off'}**",
    ]
    if autonomy.get("enabled"):
        lines.append(
            f"- profile: `{autonomy.get('profile') or '(project default)'}` "
            f"· rounds: {autonomy.get('rounds', 0)}/{autonomy.get('max_rounds', '?')} "
            f"· attempts: {autonomy.get('attempts', 0)}"
        )
        gate = autonomy.get("validation") or {}
        lines.append(
            f"- validation: `{gate.get('source') or 'auto'}`"
            + (f" · {gate.get('suite')}" if gate.get("suite") else "")
        )
        if autonomy.get("workspace_branch"):
            lines.append(f"- workspace branch: `{autonomy.get('workspace_branch')}`")
        if autonomy.get("validation_passed"):
            lines.append("- validation: ✅ passed")
        if autonomy.get("last_error"):
            lines.append(f"- last error: {autonomy.get('last_error')}")
        if autonomy.get("delivery"):
            lines += ["", "### Last delivery", str(autonomy.get("delivery"))[:4000]]
    else:
        lines.append("- autonomy is disabled")
    return "\n".join(lines)


async def ticket_autonomy(
    action: str,
    ticket_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    profile: Optional[str] = None,
    auto_accept: Optional[bool] = None,
    max_rounds: Optional[int] = None,
    max_runtime_minutes: Optional[int] = None,
    budget_cxjcoins: Optional[float] = None,
    validation: Optional[Dict[str, Any]] = None,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
    **kwargs,
) -> ToolResponse:
    """
    Configure/run an autonomous ticket (target project only).

    Args:
        action: ``status``, ``list``, ``enable``, ``disable``, ``configure``,
            ``queue``, ``run`` or ``cancel``.
        ticket_id: Ticket id (required except for status/list without id).
        enabled: Enable/disable autonomy (``configure``).
        profile: Profile (agent) used by the run.
        auto_accept: Accept the open ticket when the run starts.
        max_rounds: Maximum validated rounds of one run.
        max_runtime_minutes: Wall-clock limit of one run.
        budget_cxjcoins: Budget of one run (0 = project default).
        validation: Validation declaration (see the tool schema).
        settings: Project settings (injected by the agent runtime).
        session: Active session (injected by the agent runtime).
        **kwargs: Runtime injection.

    Returns:
        ToolResponse with the status payload (or the finished run).
    """
    action = str(action or "").strip().lower()
    service = _autonomy_service(settings=settings, session=session)
    try:
        if action in ("status", "list"):
            payload = service.status(ticket_id)
            if ticket_id:
                return ToolResponse(
                    user_response=_render_autonomy_status(payload),
                    llm_response=_json(payload),
                )
            entries = payload.get("tickets") or []
            if not entries:
                text = "No autonomous tickets in this project."
            else:
                lines = [f"## {len(entries)} autonomous ticket(s)", ""]
                for entry in entries:
                    autonomy = entry.get("autonomy") or {}
                    lines.append(
                        f"- `{entry.get('ticket_id')}` **{entry.get('name')}** · "
                        f"{entry.get('status')} · autonomy "
                        f"**{autonomy.get('state') or 'off'}**"
                    )
                if not payload.get("worker_present"):
                    lines += [
                        "",
                        "⚠️ No ticket worker heartbeat: queued tickets will not "
                        "run until the background service is up.",
                    ]
                text = "\n".join(lines)
            return ToolResponse(user_response=text, llm_response=_json(payload))

        if not ticket_id:
            raise ValueError(f"action='{action}' requires ticket_id")

        if action == "enable":
            payload = service.configure(
                ticket_id, enabled=True, accept=True,
                profile=profile, auto_accept=auto_accept,
                max_rounds=max_rounds,
                max_runtime_minutes=max_runtime_minutes,
                budget_cxjcoins=budget_cxjcoins, validation=validation,
            )
            return ToolResponse(
                user_response=(
                    "✅ Autonomy enabled and queued.\n\n"
                    + _render_autonomy_status(payload)
                ),
                llm_response=_json(payload),
            )

        if action == "disable":
            payload = service.configure(
                ticket_id, enabled=False, profile=profile, auto_accept=auto_accept,
                max_rounds=max_rounds, max_runtime_minutes=max_runtime_minutes,
                budget_cxjcoins=budget_cxjcoins, validation=validation,
            )
            return ToolResponse(
                user_response="✅ Autonomy disabled.\n\n" + _render_autonomy_status(payload),
                llm_response=_json(payload),
            )

        if action in ("configure", "queue"):
            payload = service.configure(
                ticket_id,
                enabled=True if action == "queue" else enabled,
                accept=action == "queue",
                profile=profile, auto_accept=auto_accept,
                max_rounds=max_rounds,
                max_runtime_minutes=max_runtime_minutes,
                budget_cxjcoins=budget_cxjcoins, validation=validation,
            )
            return ToolResponse(
                user_response="✅ Ticket autonomy updated.\n\n" + _render_autonomy_status(payload),
                llm_response=_json(payload),
            )

        if action == "run":
            payload = await service.run(ticket_id, force=True)
            return ToolResponse(
                user_response="## Autonomous run finished\n\n" + _render_autonomy_status(payload),
                llm_response=_json(payload),
            )

        if action == "cancel":
            payload = service.cancel(ticket_id)
            return ToolResponse(
                user_response="⏹️ Autonomous run cancelled.\n\n" + _render_autonomy_status(payload),
                llm_response=_json(payload),
            )

        raise ValueError(
            f"unknown action '{action}'. Available: status, list, enable, disable, "
            "configure, queue, run, cancel"
        )
    except (ValueError, TicketError) as ex:
        logger.warning("ticket_autonomy: %s", ex)
        return ToolResponse(
            user_response=f"⚠️ {ex}",
            llm_response=_json({"error": str(ex)}),
        )
