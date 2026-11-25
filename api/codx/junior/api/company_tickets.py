"""
REST surface of the company ticket workbench (``/api/companies/{id}/tickets``).

The company shell shows one queue per linked project, places tickets on the
project that must do the work and reviews the deliveries of unattended runs.
The rules live in :mod:`codx.junior.company.tickets` and
:mod:`codx.junior.ticketing`; this router only validates the payloads and maps
errors to HTTP codes.

Route map
---------
```text
GET    /api/companies/{id}/tickets                 queues + projects + counts
GET    /api/companies/{id}/tickets/summary         counts only (home widget)
GET    /api/companies/{id}/tickets/{ticket_id}     ticket + messages + autonomy
POST   /api/companies/{id}/tickets                 place a ticket on a project
POST   /api/companies/{id}/tickets/{ticket_id}/comment
POST   /api/companies/{id}/tickets/{ticket_id}/configure
POST   /api/companies/{id}/tickets/{ticket_id}/queue
POST   /api/companies/{id}/tickets/{ticket_id}/run
POST   /api/companies/{id}/tickets/{ticket_id}/cancel
POST   /api/companies/{id}/tickets/{ticket_id}/review
```

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from codx.junior.api.company import (
    _company_role,
    _manager,
    _run,
)
from codx.junior.company.tickets import CompanyTicketError, CompanyTicketService
from codx.junior.model.model import CodxUser
from codx.junior.model.user import COMPANY_ROLE_ADMIN, COMPANY_ROLE_MANAGER
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field  # noqa: E402  (kept near the models)


class CompanyTicketCreateRequest(BaseModel):
    """Body of ``POST /companies/{id}/tickets``."""

    project_id: str = Field(description="Company project that will do the work.")
    name: str = Field(description="Short ticket title.")
    description: str = Field(description="What must be done, with acceptance criteria.")
    kind: Optional[str] = Field(default=None, description="bug | feature | change | question")
    priority: Optional[str] = Field(default=None, description="high | medium | low")
    profiles: List[str] = Field(default_factory=list, description="Profiles the target may use.")
    tags: List[str] = Field(default_factory=list)
    autonomous: bool = Field(
        default=True,
        description="Work the ticket without a human in the loop (default true).",
    )
    auto_accept: bool = Field(
        default=True,
        description=(
            "Accept the ticket automatically when the run starts. The company "
            "owns the target project, so its workbench defaults to true."
        ),
    )
    profile: Optional[str] = Field(default=None, description="Profile (agent) for the run.")
    validation: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Validation gate override: {source, commands, suite, required}.",
    )


class CompanyTicketCommentRequest(BaseModel):
    """Body of the comment endpoint."""

    comment: str = Field(description="Message appended to the ticket conversation.")


class CompanyTicketConfigureRequest(BaseModel):
    """Body of the autonomy configuration endpoint (all fields optional)."""

    enabled: Optional[bool] = None
    profile: Optional[str] = None
    auto_accept: Optional[bool] = None
    max_rounds: Optional[int] = None
    max_runtime_minutes: Optional[int] = None
    budget_cxjcoins: Optional[float] = None
    validation: Optional[Dict[str, Any]] = None
    accept: Optional[bool] = None


class CompanyTicketReviewRequest(BaseModel):
    """Body of the review endpoint."""

    decision: str = Field(description="accept | changes")
    note: str = Field(default="", description="Why the delivery is accepted or sent back.")
    queue: bool = Field(
        default=True,
        description="On `changes`, queue another autonomous run immediately.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(user: Optional[CodxUser]) -> CompanyTicketService:
    """Build the company ticket service for the authenticated user."""
    return CompanyTicketService(manager=_manager(), user=user)


def _company(company_id: str) -> Any:
    """Resolve a company document or raise 404."""
    return _run(lambda: _manager().get_company(company_id))


def _assert_can_read(company: Any, user: Optional[CodxUser]) -> str:
    """Return the role, refusing users outside a company that already has an owner."""
    role = _company_role(company, user)
    owner = getattr(company, "owner", "") or ""
    if owner and not role:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this company.",
        )
    return role


def _assert_can_manage(company: Any, user: Optional[CodxUser]) -> str:
    """Refuse writes from roles that cannot manage the company work."""
    role = _assert_can_read(company, user)
    if role not in (COMPANY_ROLE_ADMIN, COMPANY_ROLE_MANAGER):
        raise HTTPException(
            status_code=403,
            detail="Only a company manager can place or run tickets.",
        )
    return role


def _failure(ex: Exception) -> HTTPException:
    """Map a ticketing failure to the HTTP status the client expects."""
    message = str(ex)
    lowered = message.lower()
    status_code = 404 if "not found" in lowered else 400
    return HTTPException(status_code=status_code, detail=message)


def _call(operation):
    """Execute a service operation translating its errors into HTTP errors."""
    try:
        return operation()
    except HTTPException:
        raise
    except (CompanyTicketError, ValueError) as ex:
        raise _failure(ex) from ex
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Company tickets API error: %s", ex)
        raise HTTPException(status_code=500, detail=str(ex)) from ex


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/companies/{company_id}/tickets")
def api_company_tickets(
    company_id: str,
    project_id: str = Query(default="", description="Only this target project."),
    status: str = Query(default="", description="Only this ticket status."),
    include_closed: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """List the tickets of every company project (grouped queues)."""
    company = _company(company_id)
    _assert_can_read(company, user)
    return _call(
        lambda: _service(user).list_tickets(
            company,
            project_id=project_id,
            status=status,
            include_closed=include_closed,
            limit=limit,
        )
    )


@router.get("/companies/{company_id}/tickets/summary")
def api_company_tickets_summary(
    company_id: str,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Return only the counts the company home renders."""
    company = _company(company_id)
    _assert_can_read(company, user)
    return _call(lambda: _service(user).summary(company))


@router.get("/companies/{company_id}/tickets/{ticket_id}")
def api_company_ticket(
    company_id: str,
    ticket_id: str,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Return one ticket with its conversation and autonomy evidence."""
    company = _company(company_id)
    _assert_can_read(company, user)
    return _call(lambda: _service(user).detail(company, ticket_id))


@router.post("/companies/{company_id}/tickets", status_code=201)
def api_company_create_ticket(
    company_id: str,
    payload: CompanyTicketCreateRequest,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Place a ticket on one company project (unattended by default)."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    data = payload.model_dump()
    data["profiles"] = [profile for profile in (data.get("profiles") or []) if profile]
    return _call(lambda: _service(user).create(company, data))


@router.post("/companies/{company_id}/tickets/{ticket_id}/comment")
def api_company_comment_ticket(
    company_id: str,
    ticket_id: str,
    payload: CompanyTicketCommentRequest,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Append a comment/instruction to the ticket conversation."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    return _call(lambda: _service(user).comment(company, ticket_id, payload.comment))


@router.post("/companies/{company_id}/tickets/{ticket_id}/configure")
def api_company_configure_ticket(
    company_id: str,
    ticket_id: str,
    payload: CompanyTicketConfigureRequest,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Configure the autonomous run of one ticket (profile, budgets, gate)."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    options = payload.model_dump(exclude_unset=True)
    return _call(lambda: _service(user).configure(company, ticket_id, **options))


@router.post("/companies/{company_id}/tickets/{ticket_id}/queue")
def api_company_queue_ticket(
    company_id: str,
    ticket_id: str,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Enable autonomy and queue the ticket for the background worker."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    return _call(lambda: _service(user).queue(company, ticket_id))


@router.post("/companies/{company_id}/tickets/{ticket_id}/run")
def api_company_run_ticket(
    company_id: str,
    ticket_id: str,
    wait: bool = Query(default=False),
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Start an autonomous run now (background unless ``wait=true``)."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    return _call(lambda: _service(user).run(company, ticket_id, wait=wait))


@router.post("/companies/{company_id}/tickets/{ticket_id}/cancel")
def api_company_cancel_ticket(
    company_id: str,
    ticket_id: str,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Stop the current run without closing the ticket."""
    company = _company(company_id)
    _assert_can_manage(company, user)
    return _call(lambda: _service(user).cancel(company, ticket_id))


@router.post("/companies/{company_id}/tickets/{ticket_id}/review")
def api_company_review_ticket(
    company_id: str,
    ticket_id: str,
    payload: CompanyTicketReviewRequest,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Accept a delivery or send it back.

    ``accept`` records the human decision without changing the closed status.
    ``changes`` reopens the ticket, clears the previous review and (by default)
    queues another autonomous run with the reviewer note in the conversation.
    """
    company = _company(company_id)
    _assert_can_manage(company, user)
    return _call(
        lambda: _service(user).review(
            company,
            ticket_id,
            decision=payload.decision,
            note=payload.note,
            queue=payload.queue,
        )
    )


__all__ = ["router", "CompanyTicketService"]
