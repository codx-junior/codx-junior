"""
The company ticket tool: place, run and review project tickets from a chat.

The per-project ``ticket_*`` tools require the acting project to be the
requester and the target to be a child/linked/parent of that project. The
company shell is different: the company owns **many** projects and can ask any
of them to do work. This tool exposes the same
:class:`~codx.junior.company.tickets.CompanyTicketService` the company
workbench uses, so an agent chat can:

* list the ticket queues of every linked project (with the review state);
* place a ticket on any company project and queue it for unattended work;
* run/queue/cancel a run, comment on it, or review the delivery.

The tool is chat-scoped and injected per run (``*`` grants it to managers). A
read-only company member never receives it (see the assistant tool allowlist).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from codx.junior.company.manager import CompanyManager
from codx.junior.company.tickets import CompanyTicketError, CompanyTicketService
from codx.junior.tools.model import ToolResponse

logger = logging.getLogger(__name__)

VALID_ACTIONS = [
    "list",
    "get",
    "create",
    "comment",
    "configure",
    "queue",
    "run",
    "cancel",
    "review",
]


def _json(payload: Any, limit: int = 12000) -> str:
    """Serialize a payload for the model (bounded)."""
    text = json.dumps(payload, default=str, indent=2)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _service(user: Any = None) -> CompanyTicketService:
    """Build the company ticket service for the acting user."""
    manager = CompanyManager()
    return CompanyTicketService(manager=manager, user=user)


def _one_line(ticket: Dict[str, Any]) -> str:
    """Render one ticket for the chat answer."""
    autonomy = ticket.get("autonomy") or {}
    state = "review" if ticket.get("review_pending") else (autonomy.get("state") or ticket.get("status"))
    return (
        f"- **{ticket.get('name') or ticket.get('ticket_id')}** "
        f"(`{ticket.get('ticket_id')}`) · {ticket.get('project_name') or ticket.get('target_project_name')} "
        f"· {state}"
    )


def company_ticket(
    action: str,
    company_id: Optional[str] = None,
    project_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    kind: Optional[str] = None,
    priority: Optional[str] = None,
    autonomous: Optional[bool] = None,
    profile: Optional[str] = None,
    validation: Optional[Dict[str, Any]] = None,
    max_rounds: Optional[int] = None,
    max_runtime_minutes: Optional[int] = None,
    budget_cxjcoins: Optional[float] = None,
    decision: Optional[str] = None,
    note: Optional[str] = None,
    user: Any = None,
) -> ToolResponse:
    """
    Operate the company ticket workbench.

    Args:
        action: ``list``, ``get``, ``create``, ``comment``, ``configure``,
            ``queue``, ``run``, ``cancel`` or ``review``.
        company_id: Company id (or slug). Required by every action.
        project_id: Target project for ``create`` / optional filter for ``list``.
        ticket_id: Ticket id for the per-ticket actions.
        name: Ticket title (``create``).
        description: Complete request with acceptance criteria (``create``).
        kind: ``bug`` / ``feature`` / ``change`` / ``question``.
        priority: ``high`` / ``medium`` / ``low``.
        autonomous: Work unattended (default true on ``create``).
        profile: Optional agent profile for the run.
        validation: Validation gate override
            (``{source: auto|commands|none, commands, suite, required}``).
        max_rounds: Maximum validated rounds of one run (``configure``).
        max_runtime_minutes: Wall-clock limit of one run (``configure``).
        budget_cxjcoins: Budget of one run (``configure``).
        decision: ``accept`` or ``changes`` on ``review``.
        note: Comment / review note.
        user: Injected authenticated user.

    Returns:
        A :class:`ToolResponse` with a human answer and the JSON payload.
    """
    wanted = str(action or "").strip().lower()
    if wanted not in VALID_ACTIONS:
        return ToolResponse(
            user_response=f"Unknown action '{action}'. Valid: {', '.join(VALID_ACTIONS)}.",
            llm_response=_json({"error": f"unknown action '{action}'"}),
        )
    if not company_id:
        return ToolResponse(
            user_response="company_id is required (the company the tickets belong to).",
            llm_response=_json({"error": "company_id is required"}),
        )
    service = _service(user)
    try:
        if wanted == "list":
            listing = service.list_tickets(
                company_id, project_id=project_id or "", include_closed=True
            )
            tickets = listing.get("tickets") or []
            if not tickets:
                answer = "There are no tickets in this company yet."
            else:
                review = [ticket for ticket in tickets if ticket.get("review_pending")]
                answer = (
                    f"{len(tickets)} ticket(s) across "
                    f"{len(listing.get('projects') or [])} project(s); "
                    f"{len(review)} waiting for review.\n\n"
                    + "\n".join(_one_line(ticket) for ticket in tickets[:20])
                )
                if review:
                    answer += (
                        "\n\nDeliveries to review: "
                        + ", ".join(str(ticket.get("name") or ticket.get("ticket_id")) for ticket in review[:8])
                    )
            return ToolResponse(user_response=answer, llm_response=_json(listing))
        if wanted == "get":
            if not ticket_id:
                raise CompanyTicketError("ticket_id is required")
            detail = service.detail(company_id, ticket_id)
            ticket = detail.get("ticket") or {}
            autonomy = detail.get("autonomy") or {}
            answer = "\n".join(
                [
                    f"### {ticket.get('name')}",
                    f"- Project: {ticket.get('project_name') or ticket.get('target_project_name')}",
                    f"- Status: {ticket.get('status')} · autonomy {autonomy.get('state') or 'off'}",
                    f"- Validation: {'passed' if autonomy.get('validation_passed') else 'not green'}",
                    f"- Review: {'pending' if ticket.get('review_pending') else (autonomy.get('reviewed_by') or 'not required')}",
                ]
            )
            delivery = autonomy.get("delivery")
            if delivery:
                answer += f"\n\n**Delivery**\n{delivery[:2500]}"
            return ToolResponse(user_response=answer, llm_response=_json(detail))
        if wanted == "create":
            detail = service.create(
                company_id,
                {
                    "project_id": project_id,
                    "name": name,
                    "description": description,
                    "kind": kind,
                    "priority": priority,
                    "autonomous": True if autonomous is None else bool(autonomous),
                    "auto_accept": True if autonomous is None else bool(autonomous),
                    "profile": profile,
                    "validation": validation,
                },
            )
            ticket = detail.get("ticket") or {}
            answer = (
                f"Ticket **{ticket.get('name')}** placed on "
                f"**{ticket.get('project_name') or project_id}** (`{ticket.get('ticket_id')}`). "
                + (
                    "It is queued for unattended work and I will report the validated delivery for review."
                    if (ticket.get("autonomy") or {}).get("enabled")
                    else "It is waiting for the target to accept it."
                )
            )
            return ToolResponse(user_response=answer, llm_response=_json(detail))
        if wanted == "comment":
            if not ticket_id or not note:
                raise CompanyTicketError("ticket_id and note are required")
            detail = service.comment(company_id, ticket_id, note)
            return ToolResponse(
                user_response=f"Comment added to ticket `{ticket_id}`.",
                llm_response=_json(detail),
            )
        if wanted == "configure":
            if not ticket_id:
                raise CompanyTicketError("ticket_id is required")
            options = {
                key: value
                for key, value in {
                    "enabled": True,
                    "profile": profile,
                    "validation": validation,
                    "max_rounds": max_rounds,
                    "max_runtime_minutes": max_runtime_minutes,
                    "budget_cxjcoins": budget_cxjcoins,
                }.items()
                if value is not None
            }
            detail = service.configure(company_id, ticket_id, **options)
            return ToolResponse(
                user_response=f"Autonomy configured for ticket `{ticket_id}`.",
                llm_response=_json(detail),
            )
        if wanted == "queue":
            if not ticket_id:
                raise CompanyTicketError("ticket_id is required")
            detail = service.queue(company_id, ticket_id)
            return ToolResponse(
                user_response=f"Ticket `{ticket_id}` queued for unattended work.",
                llm_response=_json(detail),
            )
        if wanted == "run":
            if not ticket_id:
                raise CompanyTicketError("ticket_id is required")
            service.run(company_id, ticket_id, wait=False)
            return ToolResponse(
                user_response=f"Run started for ticket `{ticket_id}` in the background.",
                llm_response=_json({"ticket_id": ticket_id, "queued": True}),
            )
        if wanted == "cancel":
            if not ticket_id:
                raise CompanyTicketError("ticket_id is required")
            detail = service.cancel(company_id, ticket_id)
            return ToolResponse(
                user_response=f"Run cancelled for ticket `{ticket_id}`.",
                llm_response=_json(detail),
            )
        # review
        if not ticket_id:
            raise CompanyTicketError("ticket_id is required")
        detail = service.review(
            company_id,
            ticket_id,
            decision=decision or "accept",
            note=note or "",
            queue=(decision or "accept") == "changes",
        )
        is_accept = (decision or "accept").lower() in ("accept", "approve", "approved", "ok")
        return ToolResponse(
            user_response=(
                f"Delivery accepted for ticket `{ticket_id}`."
                if is_accept
                else f"Changes requested for ticket `{ticket_id}`; it is queued for another run."
            ),
            llm_response=_json(detail),
        )
    except (CompanyTicketError, ValueError) as ex:
        logger.info("company_ticket action=%s failed: %s", wanted, ex)
        return ToolResponse(
            user_response=f"I could not do that: {ex}",
            llm_response=_json({"error": str(ex)}),
        )
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("company_ticket action=%s failed: %s", wanted, ex)
        return ToolResponse(
            user_response=f"I could not do that: {ex}",
            llm_response=_json({"error": str(ex)}),
        )


COMPANY_TICKET_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "company_ticket",
        "description": (
            "Operate the ticket workbench of a COMPANY (the top-level entity that "
            "owns several projects). Use it to list the ticket queues per project, "
            "place a ticket on any company project and let it run unattended, "
            "comment, run/queue/cancel a run, and accept or send back the delivery "
            "when it is done. This is the company-level counterpart of the "
            "per-project ticket_create/ticket_update tools: it resolves the target "
            "from the company projects, not from project links."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": VALID_ACTIONS,
                    "description": "Operation to perform.",
                },
                "company_id": {
                    "type": "string",
                    "description": "Company id (or slug). Required.",
                },
                "project_id": {
                    "type": "string",
                    "description": "Target project for create; optional filter for list.",
                },
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket id (required by every per-ticket action).",
                },
                "name": {"type": "string", "description": "Ticket title (create)."},
                "description": {
                    "type": "string",
                    "description": "Complete request with acceptance criteria (create).",
                },
                "kind": {
                    "type": "string",
                    "enum": ["bug", "feature", "change", "question"],
                    "description": "Request kind.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Priority hint.",
                },
                "autonomous": {
                    "type": "boolean",
                    "description": "Work the ticket unattended (default true).",
                },
                "profile": {
                    "type": "string",
                    "description": "Optional agent profile for the run.",
                },
                "validation": {
                    "type": "object",
                    "description": (
                        "Validation gate override: {source: auto|commands|none, "
                        "commands: [...], suite, required}."
                    ),
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "Maximum validated rounds of one run.",
                },
                "max_runtime_minutes": {
                    "type": "integer",
                    "description": "Wall-clock limit of one run, in minutes.",
                },
                "budget_cxjcoins": {
                    "type": "number",
                    "description": "Budget of one run in cxjcoins (0 = default).",
                },
                "decision": {
                    "type": "string",
                    "enum": ["accept", "changes"],
                    "description": "Review decision.",
                },
                "note": {
                    "type": "string",
                    "description": "Comment or review note.",
                },
            },
            "required": ["action", "company_id"],
        },
    },
}
