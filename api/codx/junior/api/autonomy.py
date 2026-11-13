"""
Actors API router.

An **actor** is a profile that works on its own: role, boards, cadence, weekly
availability and budgets (see :mod:`codx.junior.autonomy`). This router is the
control surface of the feature for the UI and for scripts.

Routing
-------
``/api/actors/...`` is the canonical surface; ``/api/autonomy/...`` is kept as a
hidden alias because the first release of the feature shipped under that name
and the UI/tests of that release still call it.

GET    /actors/status                       actors, schedule, cost, next wake
GET    /actors                              the ``actors`` list only
GET    /actors/boards                       boards that can be watched
PUT    /actors/{profile_name}               configure (role, boards, schedule, budgets)
POST   /actors/{profile_name}/enable        switch the actor on
POST   /actors/{profile_name}/disable       switch the actor off
POST   /actors/{profile_name}/wake          ask for a shift now (or at ``when``)
POST   /actors/{profile_name}/schedule      set the next shift instant
POST   /actors/{profile_name}/run           one shift synchronously (debug)

Conventions
-----------
* ``PUT`` is an **upsert** on the actor block: only the provided fields change,
  the rest of the profile (content, tools, knowledge, linked profiles) is never
  touched, because the write goes through ``ProfileManager.save_profile``.
* ``POST .../wake`` never runs an AI turn inline: it records the intent and the
  background worker executes it, so an API request always stays short.
* ``POST .../run`` is the only blocking endpoint and is meant for debugging.
* A **manual** wake always wins over the weekly schedule (an explicit human
  decision); automatic wakes only happen inside the availability windows.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import get_current_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["actors"])


class ScheduleWindowRequest(BaseModel):
    """One availability window of the weekly schedule."""

    days: List[str] = Field(
        default_factory=list,
        description="Weekdays (mon..sun). Empty means every day.",
    )
    start: str = Field(default="09:00", description="Opening time (HH:MM)")
    end: str = Field(default="18:00", description="Closing time (HH:MM, may wrap over midnight)")


class ScheduleRequest(BaseModel):
    """Weekly availability of an actor."""

    enabled: bool = Field(default=True, description="Enforce the windows")
    timezone: str = Field(default="UTC", description="IANA zone, e.g. Europe/Madrid")
    windows: List[ScheduleWindowRequest] = Field(
        default_factory=list,
        description="Recurring windows. Enabled with no window means paused.",
    )


class ActorConfigRequest(BaseModel):
    """Partial update of the actor block of a profile."""

    enabled: Optional[bool] = Field(
        default=None, description="Turn the actor on/off for this profile."
    )
    role: Optional[str] = Field(
        default=None,
        description="'coworker' (owns a job) or 'manager' (curates boards).",
    )
    policy: Optional[str] = Field(
        default=None,
        description="'full' (every tool) or 'board_only' (board work only).",
    )
    session: Optional[str] = Field(
        default=None,
        description="'single' (one turn per shift) or 'until_done' (turns while work remains).",
    )
    board: Optional[str] = Field(
        default=None, description="Desk board where the shift reports are written."
    )
    boards: Optional[List[str]] = Field(
        default=None, description="Boards the actor is responsible for."
    )
    interval_minutes: Optional[int] = Field(
        default=None, description="Cadence between two automatic shifts."
    )
    mode: Optional[str] = Field(
        default=None, description="'active' (acts) or 'dry_run' (reports only)."
    )
    budget_cxjcoins: Optional[float] = Field(
        default=None, description="Budget of one shift (0 = project default)."
    )
    daily_budget_cxjcoins: Optional[float] = Field(
        default=None, description="Budget of one day across shifts (0 = no cap)."
    )
    max_runtime_minutes: Optional[int] = Field(
        default=None, description="Hard limit of one shift, in minutes."
    )
    max_turns: Optional[int] = Field(
        default=None, description="Maximum turns inside one 'until_done' shift."
    )
    max_agenda: Optional[int] = Field(
        default=None, description="How many due tasks one shift must handle."
    )
    max_tasks: Optional[int] = Field(
        default=None, description="How many tasks of the boards are given as context."
    )
    include_done: Optional[bool] = Field(
        default=None, description="Include closed tasks as context."
    )
    instructions: Optional[str] = Field(
        default=None, description="Standing orders added to the profile mandate."
    )
    schedule: Optional[ScheduleRequest] = Field(
        default=None, description="Weekly availability windows."
    )


class WakeRequest(BaseModel):
    """Optional schedule for a manually requested shift."""

    when: Optional[str] = Field(
        default=None,
        description=(
            "Schedule expression: '+2d', '3 hours', an ISO date "
            "(2026-04-01) or an ISO instant. Empty means 'as soon as possible'."
        ),
    )


def _service(request: Request) -> Any:
    """Build the :class:`ActorService` of the request project."""
    from codx.junior.autonomy.service import ActorService

    session = get_current_session(request)
    settings = getattr(session, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=400,
            detail="No project in the request: pass the codx_path of a project.",
        )
    return ActorService(settings=settings)


def _read_block(profile: Any) -> Dict[str, Any]:
    """Return the current actor block of a profile (legacy key included)."""
    for key in ("actor", "autonomy"):
        value = getattr(profile, key, None)
        if isinstance(value, dict) and value:
            return dict(value)
    return {}


def _save_actor(service: Any, profile_name: str, payload: ActorConfigRequest):
    """
    Merge the payload into the actor block of a profile and persist it.

    The legacy ``autonomy`` block, when present, is migrated into ``actor`` and
    removed, so a profile has exactly one declaration.
    """
    from codx.junior.autonomy.models import ActorConfig

    manager = service._profiles()
    profile = manager.read_profile(profile_name)
    if profile is None:
        raise HTTPException(
            status_code=404, detail=f"profile '{profile_name}' does not exist"
        )
    data = _read_block(profile)
    for key, value in payload.model_dump(exclude_none=True).items():
        data[key] = value
    profile.actor = ActorConfig(**data).normalized().model_dump()
    if hasattr(profile, "autonomy"):
        profile.autonomy = None
    manager.save_profile(profile=profile)
    return profile


def _entry_for(service: Any, profile_name: str) -> Dict[str, Any]:
    """Return the status entry of one actor (or a minimal payload)."""
    for entry in service.status().get("actors") or []:
        if str(entry.get("actor", "")).lower() == profile_name.lower():
            return entry
    return {"actor": profile_name, "agent": profile_name, "enabled": False,
            "note": "the actor is disabled"}


# -- read ---------------------------------------------------------------------


@router.get("/actors/status", summary="Actors of the project")
@router.get("/autonomy/status", include_in_schema=False)
def get_actors_status(request: Request) -> Dict[str, Any]:
    """Return every actor with its configuration, state, schedule and next wake."""
    return _service(request).status()


@router.get("/actors", summary="Actors only")
@router.get("/autonomy/agents", include_in_schema=False)
def get_actors(request: Request) -> List[Dict[str, Any]]:
    """Return the ``actors`` list of :func:`get_actors_status`."""
    return _service(request).status().get("actors") or []


@router.get("/actors/boards", summary="Boards that can be watched")
def get_actor_boards(request: Request) -> List[Dict[str, Any]]:
    """
    Return the boards of the project (title, id, columns).

    The UI uses it to let a user pick the boards of an actor instead of typing
    their titles.
    """
    service = _service(request)
    try:
        kanban = service._manager().load_kanban() or {}
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("actors: cannot read the kanban catalogue: %s", ex)
        return []
    raw = kanban.get("boards")
    boards: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for board in raw:
            if not isinstance(board, dict):
                continue
            boards.append({
                "name": str(board.get("name") or board.get("title") or ""),
                "id": str(board.get("id") or ""),
                "type": str(board.get("type") or "kanban"),
                "columns": [
                    str(column.get("title") or "")
                    for column in (board.get("columns") or [])
                    if isinstance(column, dict)
                ],
            })
    elif isinstance(raw, dict):
        for name, board in raw.items():
            if not isinstance(board, dict):
                continue
            boards.append({
                "name": str(board.get("name") or name),
                "id": str(board.get("id") or ""),
                "type": str(board.get("type") or "kanban"),
                "columns": [
                    str(column.get("title") or "")
                    for column in (board.get("columns") or [])
                    if isinstance(column, dict)
                ],
            })
    return sorted(boards, key=lambda board: board["name"].lower())


# -- write --------------------------------------------------------------------


@router.put("/actors/{profile_name}", summary="Configure an actor")
@router.put("/autonomy/agents/{profile_name}", include_in_schema=False)
def put_actor(
    request: Request,
    profile_name: str,
    payload: ActorConfigRequest,
) -> Dict[str, Any]:
    """
    Update the actor block of *profile_name*.

    Only the provided fields change; the profile instructions, tools, knowledge
    and linked profiles are preserved.
    """
    service = _service(request)
    _save_actor(service, profile_name, payload)
    return _entry_for(service, profile_name)


@router.post("/actors/{profile_name}/enable", summary="Enable an actor")
@router.post("/autonomy/agents/{profile_name}/enable", include_in_schema=False)
def post_actor_enable(request: Request, profile_name: str) -> Dict[str, Any]:
    """Switch the profile into an actor."""
    service = _service(request)
    _save_actor(service, profile_name, ActorConfigRequest(enabled=True))
    return service.status()


@router.post("/actors/{profile_name}/disable", summary="Disable an actor")
@router.post("/autonomy/agents/{profile_name}/disable", include_in_schema=False)
def post_actor_disable(request: Request, profile_name: str) -> Dict[str, Any]:
    """Stop the profile from taking shifts (its boards and history are kept)."""
    service = _service(request)
    _save_actor(service, profile_name, ActorConfigRequest(enabled=False))
    return service.status()


@router.post("/actors/{profile_name}/wake", summary="Request a shift")
@router.post("/autonomy/agents/{profile_name}/wake", include_in_schema=False)
def post_actor_wake(
    request: Request,
    profile_name: str,
    payload: Optional[WakeRequest] = None,
) -> Dict[str, Any]:
    """
    Record "take a shift now" (or at ``when``).

    A manual wake ignores the weekly schedule: it is an explicit decision. The
    background worker executes it; nothing runs inline.
    """
    service = _service(request)
    try:
        return service.request_wake(profile_name, when=(payload.when if payload else "") or "")
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/actors/{profile_name}/schedule", summary="Set the next shift")
@router.post("/autonomy/agents/{profile_name}/schedule", include_in_schema=False)
def post_actor_schedule(
    request: Request,
    profile_name: str,
    when: str = Query(description="Schedule expression, e.g. '+2d' or '2026-04-01'"),
) -> Dict[str, Any]:
    """Set the instant of the next automatic shift of the actor."""
    service = _service(request)
    try:
        return service.schedule_wake(profile_name, when)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/actors/{profile_name}/run", summary="Run one shift now (blocking)")
@router.post("/autonomy/agents/{profile_name}/run", include_in_schema=False)
async def post_actor_run(request: Request, profile_name: str) -> Dict[str, Any]:
    """
    Take one shift immediately and return its report.

    Debugging endpoint: a real shift can last minutes, so the UI uses
    ``/wake`` and reads ``/status`` instead.
    """
    service = _service(request)
    try:
        return await service.run_shift(profile_name, force=True, trigger="api")
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))

# Made with ❤️ by codx-junior
