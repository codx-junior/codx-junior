"""
Ticketing API router – the ticket queue of a project, over HTTP.

The router is discovered automatically by
:func:`codx.junior.api.discover_routers` (every module of this package exporting
a ``router`` is registered under ``/api``), so no change is needed in
``codx.junior.app``.

It is a thin layer over :class:`codx.junior.ticketing.service.TicketService`, the
same implementation the LLM tools use (``ticket_create`` / ``ticket_update`` /
``ticket_query`` / ``ticket_abandon``), so the HTTP client and a profile can
never disagree about who may do what: the target project accepts, declines or
closes a ticket, the requester comments and abandons it.

Route layout
------------
GET    /api/tickets                        – list the tickets of the project
GET    /api/tickets/targets                – projects a ticket can be placed on
POST   /api/tickets                        – place a ticket (returns its id)
GET    /api/tickets/{ticket_id}            – one ticket
POST   /api/tickets/{ticket_id}/comment    – append a message to the conversation
POST   /api/tickets/{ticket_id}/status     – move the ticket (accept/decline/done)
POST   /api/tickets/{ticket_id}/abandon    – withdraw a placed ticket

``GET /api/tickets`` returns both directions of the project: ``outgoing`` (placed
by this project on another one, refreshed from the requester's ledger) and
``incoming`` (placed here by a parent or a linked project, read from the
``tickets`` board).

Diagram
-------
```mermaid
flowchart LR
    Client -->|GET /api/tickets| List
    Client -->|POST /api/tickets| Create
    Client -->|POST /:id/status| Status
    Client -->|POST /:id/comment| Comment
    Client -->|POST /:id/abandon| Abandon

    Create --> Service[TicketService]
    List --> Service
    Status --> Service
    Comment --> Service
    Abandon --> Service

    Service -->|target project| TargetChat[.codx/tasks/tickets/...]
    Service -->|requester project| Ledger[.codx/tickets/tickets.json]
```

Made with ❤️ by codx-junior
"""

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codx.junior.api import get_current_session
from codx.junior.ticketing.autonomy import (
    TicketAutonomyService,
    autonomy_enabled,
)
from codx.junior.ticketing.model import (
    TICKET_KINDS,
    TICKET_SIDE_SOURCE,
    TICKET_SIDE_TARGET,
    TICKET_STATUSES,
    TicketRecord,
)
from codx.junior.ticketing.service import TicketError, TicketService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class TicketCreateRequest(BaseModel):
    """Body of ``POST /api/tickets``: the whole request, no inherited values."""

    target_project: str = Field(
        description="Target project name or id (a child, linked or parent project)."
    )
    name: str = Field(description="Short title of the request.")
    description: str = Field(description="Complete request the target will read.")
    kind: Optional[str] = Field(
        default=None, description=f"Request kind: {', '.join(TICKET_KINDS)}."
    )
    priority: Optional[str] = Field(default=None, description="high | medium | low")
    reference: Optional[str] = Field(
        default=None, description="Id/url of the request in THIS project."
    )
    content: Optional[str] = Field(
        default=None, description="First message (defaults to the description)."
    )
    profiles: List[str] = Field(
        default_factory=list, description="Profiles the target may use (a suggestion)."
    )
    files: List[str] = Field(
        default_factory=list, description="Files of the target project the request refers to."
    )
    tags: List[str] = Field(default_factory=list, description="Triage tags.")
    assignees: List[str] = Field(
        default_factory=list, description="Usernames of the target project."
    )
    autonomous: bool = Field(
        default=False,
        description=(
            "Ask the target to work the ticket autonomously until its validation "
            "gate is green."
        ),
    )
    autonomy_profile: Optional[str] = Field(
        default=None,
        description="Optional profile (agent) for the run; it must exist in the target.",
    )


class TicketAutonomyRequest(BaseModel):
    """Body of the autonomy configuration endpoint (every field optional)."""

    enabled: Optional[bool] = Field(
        default=None, description="Enable/disable autonomous work."
    )
    profile: Optional[str] = Field(
        default=None, description="Profile (agent) used by the run."
    )
    auto_accept: Optional[bool] = Field(
        default=None, description="Accept an open ticket when the run starts."
    )
    max_rounds: Optional[int] = Field(
        default=None, description="Maximum validated rounds of one run."
    )
    max_runtime_minutes: Optional[int] = Field(
        default=None, description="Wall-clock limit of one run."
    )
    budget_cxjcoins: Optional[float] = Field(
        default=None, description="Budget of one run (0 = project default)."
    )
    validation: Optional[Dict[str, Any]] = Field(
        default=None, description="Validation declaration (source/commands/suite...)."
    )
    accept: Optional[bool] = Field(
        default=None, description="Accept the open ticket in the same operation."
    )


class TicketUpdateRequest(BaseModel):
    """Body of the update endpoints: every field is optional."""

    comment: Optional[str] = Field(default=None, description="Message for the conversation.")
    status: Optional[str] = Field(
        default=None, description=f"New status: {', '.join(TICKET_STATUSES)}."
    )
    kind: Optional[str] = Field(default=None, description=f"New kind: {', '.join(TICKET_KINDS)}.")
    priority: Optional[str] = Field(default=None, description="high | medium | low")
    name: Optional[str] = Field(default=None, description="New title.")
    profiles: Optional[List[str]] = Field(default=None, description="Profiles (replaces the list).")
    assignees: Optional[List[str]] = Field(default=None, description="Assignees (replaces the list).")
    note: Optional[str] = Field(default=None, description="Reason stored in the history.")


class TicketAbandonRequest(BaseModel):
    """Body of ``POST /api/tickets/{id}/abandon``."""

    reason: Optional[str] = Field(default=None, description="Why the request is withdrawn.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(request: Request) -> TicketService:
    """
    Build the :class:`TicketService` of the project in context.

    Args:
        request: The incoming HTTP request.

    Returns:
        A service bound to the project's settings and the authenticated user.

    Raises:
        HTTPException: 400 when the request carries no project session, 500 when
            the project has no control plane.
    """
    session = get_current_session(request)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="No project session for this request (codx_path is required)",
        )
    user = getattr(getattr(session, "user", None), "username", None)
    try:
        return TicketService(settings=session.settings, user=user)
    except TicketError as ex:
        raise HTTPException(status_code=500, detail=str(ex)) from ex


def _autonomy_service(request: Request) -> TicketAutonomyService:
    """Build the autonomy service of the project in context (target side)."""
    session = get_current_session(request)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="No project session for this request (codx_path is required)",
        )
    user = getattr(session, "user", None)
    try:
        return TicketAutonomyService(settings=session.settings, user=user)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex)) from ex


def _payload(record: TicketRecord) -> Dict[str, Any]:
    """Return the JSON payload of one ticket record."""
    return record.dict()


def _failure(ex: TicketError) -> HTTPException:
    """
    Translate a :class:`TicketError` into an HTTP error.

    A missing ticket is a 404; every other rule/invalid-input case is a 400 so
    the client sees the reason (the message names the valid targets, statuses or
    the project that owns the decision).

    Args:
        ex: The service error.

    Returns:
        The HTTP exception to raise.
    """
    message = str(ex)
    status_code = 404 if "not found" in message.lower() else 400
    return HTTPException(status_code=status_code, detail=message)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tickets/targets")
def list_ticket_targets(request: Request) -> List[Dict[str, Any]]:
    """
    List the projects this one may place a ticket on.

    Returns:
        One entry per target: ``project_id``, ``project_name``, ``relationship``
        (``child`` / ``linked`` / ``parent``), ``write`` and ``path``. ``write``
        reports the file-tool permission of the relation; a read-only target can
        still receive a ticket.
    """
    return _service(request).targets()


@router.get("/tickets")
def list_tickets(
    request: Request,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    target_project: Optional[str] = None,
    limit: Optional[int] = None,
    include_closed: bool = True,
) -> Dict[str, Any]:
    """
    List the tickets of the project, in both directions.

    Args:
        direction: ``outgoing`` / ``incoming`` / ``all`` (default ``all``).
        status: Only tickets in this status.
        target_project: Only tickets whose target project matches.
        limit: Maximum number of tickets (default 20).
        include_closed: Include done/abandoned tickets (default True).

    Returns:
        ``{"tickets": [...], "count": n, "direction": ...}``; each ticket carries
        its ``direction`` so the UI can group the queue.
    """
    service = _service(request)
    try:
        records = service.query(
            direction=direction,
            status=status,
            target=target_project,
            limit=limit,
            include_closed=include_closed,
        )
    except TicketError as ex:
        raise _failure(ex) from ex
    return {
        "tickets": [_payload(record) for record in records],
        "count": len(records),
        "direction": direction or "all",
    }


@router.post("/tickets")
def create_ticket(request: Request, payload: TicketCreateRequest) -> Dict[str, Any]:
    """
    Place a ticket on another project and return its id.

    Returns:
        The created ticket record, including ``ticket_id`` (== chat id).

    Raises:
        HTTPException: 400 when the target or the data is invalid.
    """
    service = _service(request)
    try:
        record = service.create(
            target=payload.target_project,
            name=payload.name,
            description=payload.description,
            kind=payload.kind,
            priority=payload.priority,
            reference=payload.reference,
            content=payload.content,
            profiles=payload.profiles,
            files=payload.files,
            tags=payload.tags,
            assignees=payload.assignees,
            autonomous=payload.autonomous,
            autonomy_profile=payload.autonomy_profile,
        )
    except TicketError as ex:
        raise _failure(ex) from ex
    return _payload(record)


@router.get("/tickets/autonomy")
def list_autonomous_tickets(request: Request) -> Dict[str, Any]:
    """
    List the autonomous tickets of this project (target side) and the worker state.

    Returns:
        ``{"tickets": [...], "worker_present": bool, "enabled": bool}``.
    """
    service = _autonomy_service(request)
    payload = service.status()
    payload["enabled"] = bool(autonomy_enabled())
    return payload


@router.get("/tickets/{ticket_id}/autonomy")
def get_ticket_autonomy(request: Request, ticket_id: str) -> Dict[str, Any]:
    """Return the autonomy declaration and live state of one ticket."""
    service = _autonomy_service(request)
    try:
        return service.status(ticket_id)
    except TicketError as ex:
        raise _failure(ex) from ex


@router.post("/tickets/{ticket_id}/autonomy")
def configure_ticket_autonomy(
    request: Request, ticket_id: str, payload: TicketAutonomyRequest
) -> Dict[str, Any]:
    """
    Create/update the autonomy declaration of a ticket (target project only).

    This is how a human enables autonomy, chooses the profile and declares the
    validation gate (commands or the project QA suite).
    """
    service = _autonomy_service(request)
    try:
        return service.configure(
            ticket_id,
            enabled=payload.enabled,
            profile=payload.profile,
            auto_accept=payload.auto_accept,
            max_rounds=payload.max_rounds,
            max_runtime_minutes=payload.max_runtime_minutes,
            budget_cxjcoins=payload.budget_cxjcoins,
            validation=payload.validation,
            accept=bool(payload.accept),
        )
    except TicketError as ex:
        raise _failure(ex) from ex


@router.post("/tickets/{ticket_id}/autonomy/queue")
def queue_ticket_autonomy(request: Request, ticket_id: str) -> Dict[str, Any]:
    """Enable autonomy and queue the ticket for the background worker."""
    service = _autonomy_service(request)
    try:
        return service.queue(ticket_id)
    except TicketError as ex:
        raise _failure(ex) from ex


@router.post("/tickets/{ticket_id}/autonomy/run")
def run_ticket_autonomy(
    request: Request, ticket_id: str, wait: bool = False
) -> Dict[str, Any]:
    """
    Start an autonomous run now.

    Args:
        ticket_id: Ticket id.
        wait: When true, block until the run finishes (for tests/debug and
            direct user requests). The default queues the run in a background
            thread so the HTTP request returns immediately.

    Returns:
        The final payload when ``wait`` is true, or an accepted/queued payload.
    """
    service = _autonomy_service(request)
    if wait:
        try:
            return asyncio.run(service.run(ticket_id, force=True))
        except TicketError as ex:
            raise _failure(ex) from ex

    def _run() -> None:
        try:
            asyncio.run(service.run(ticket_id, force=True))
        except Exception as ex:  # pragma: no cover - background failure is logged
            logger.exception("ticket autonomy: background run failed: %s", ex)

    thread = threading.Thread(
        target=_run, name=f"TicketAutonomy-{ticket_id[:8]}", daemon=True
    )
    thread.start()
    return {"ticket_id": ticket_id, "queued": True, "wait": False}


@router.post("/tickets/{ticket_id}/autonomy/cancel")
def cancel_ticket_autonomy(request: Request, ticket_id: str) -> Dict[str, Any]:
    """Cancel a queued/running autonomous run without closing the ticket."""
    service = _autonomy_service(request)
    try:
        return service.cancel(ticket_id)
    except TicketError as ex:
        raise _failure(ex) from ex


@router.get("/tickets/{ticket_id}")
def get_ticket(request: Request, ticket_id: str) -> Dict[str, Any]:
    """
    Return one ticket.

    Raises:
        HTTPException: 404 when the project has no such ticket.
    """
    service = _service(request)
    try:
        records = service.query(ticket_id=ticket_id)
    except TicketError as ex:
        raise _failure(ex) from ex
    if not records:
        raise HTTPException(status_code=404, detail=f"ticket '{ticket_id}' not found")
    return _payload(records[0])


@router.post("/tickets/{ticket_id}/comment")
def comment_ticket(
    request: Request, ticket_id: str, payload: TicketUpdateRequest
) -> Dict[str, Any]:
    """
    Append a message to the ticket conversation (the channel between projects).
    """
    service = _service(request)
    try:
        record = service.update(
            ticket_id=ticket_id,
            comment=payload.comment,
            kind=payload.kind,
            priority=payload.priority,
            name=payload.name,
            profiles=payload.profiles,
            assignees=payload.assignees,
            note=payload.note,
        )
    except TicketError as ex:
        raise _failure(ex) from ex
    return _payload(record)


@router.post("/tickets/{ticket_id}/status")
def set_ticket_status(
    request: Request, ticket_id: str, payload: TicketUpdateRequest
) -> Dict[str, Any]:
    """
    Move a ticket in its lifecycle.

    The target project sets ``accepted`` / ``declined`` / ``done``; ``abandoned``
    is reserved to the requester (``POST /api/tickets/{id}/abandon``).
    """
    service = _service(request)
    try:
        record = service.update(
            ticket_id=ticket_id,
            status=payload.status,
            comment=payload.comment,
            note=payload.note,
        )
    except TicketError as ex:
        raise _failure(ex) from ex
    return _payload(record)


@router.post("/tickets/{ticket_id}/abandon")
def abandon_ticket(
    request: Request, ticket_id: str, payload: Optional[TicketAbandonRequest] = None
) -> Dict[str, Any]:
    """
    Withdraw a ticket placed by this project.

    The ticket is not deleted: it moves to the ``Abandoned`` column of the
    target's board and the conversation is kept.
    """
    service = _service(request)
    try:
        record = service.abandon(
            ticket_id=ticket_id,
            reason=(payload.reason if payload else None),
        )
    except TicketError as ex:
        raise _failure(ex) from ex
    return _payload(record)


@router.get("/tickets/{ticket_id}/ledger")
def ticket_ledger_entry(request: Request, ticket_id: str) -> Dict[str, Any]:
    """
    Return the raw ledger entry of a ticket placed by this project.

    Useful to debug a stale record: it is what the requester stored locally,
    without refreshing it from the target project.

    Raises:
        HTTPException: 404 when the project has no ledger entry for the ticket.
    """
    entry = _service(request).ledger_entry(ticket_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"ticket '{ticket_id}' is not in this project's ledger",
        )
    return entry


__all__ = ["router"]

# Made with ❤️ by codx-junior
