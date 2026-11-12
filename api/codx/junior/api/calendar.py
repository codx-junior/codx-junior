"""
Calendar API router – the REST surface of the global agenda (M0).

The calendar is **installation-wide** (decision D1): one agenda, many projects,
so the store lives in ``CODX_JUNIOR_CALENDAR_PATH`` and a project is only a
filter. Nothing in this module may use ``settings.codx_path`` as a data root —
``request.state.codx_junior_session`` is read for the caller's identity and
project context only.

This module needs no registration: :func:`codx.junior.api.discover_routers`
imports every module of this package that exports ``router`` and mounts it under
``/api``.

Route layout (analysis §5.1 and §5.2)
-------------------------------------
GET    /api/calendar/calendars                        – calendars the viewer may see
POST   /api/calendar/calendars                        – create (201)
PUT    /api/calendar/calendars/{calendar_id}           – partial update (owner/admin)
DELETE /api/calendar/calendars/{calendar_id}           – delete (409 if in use)
GET    /api/calendar/calendars/{calendar_id}/share     – sharing panel
GET    /api/calendar/projects                          – projects of the filter bar
GET    /api/calendar/prefs                             – caller's UI preferences
PUT    /api/calendar/prefs                             – save them
POST   /api/calendar/events/query                      – **the main read**
GET    /api/calendar/events/{event_id}                  – raw event (editor)
POST   /api/calendar/events                            – create (201)
PUT    /api/calendar/events/{event_id}                  – update (If-Match)
DELETE /api/calendar/events/{event_id}                  – delete (scope=single|following|all)
POST   /api/calendar/events/{event_id}/snooze           – postpone a reminder
POST   /api/calendar/events/quick                       – parse a draft (not persisted)

Errors (§5.3)
-------------
The service raises :class:`~codx.junior.calendar.service.CalendarError` with a
string code (``read_only_calendar``, ``revision_mismatch``, ``event_too_long``,
``invalid_recurrence``, ``unknown_calendar``, ``calendar_in_use``) and the HTTP
status to use; this router only translates it into an ``HTTPException`` where
the detail keeps the code as its prefix.

Collection events
-----------------
After every successful write the change is broadcast with
:func:`codx.junior.events.collection_events.emit_collection_event`, so every open
tab reloads only the affected collection instead of polling
(``calendars`` / ``calendar_events``).

Diagram:
```mermaid
flowchart LR
    C[Client] -->|POST /api/calendar/events/query| Query[CalendarService.query]
    C -->|POST/PUT/DELETE| Write[CalendarService.create/update/delete]
    Query --> S[(CalendarStore<br/>CODX_JUNIOR_CALENDAR_PATH)]
    Write --> S
    Write --> E[emit_collection_event]
    E --> Sio[socket.io broadcast]
```

Made with ❤️ by codx-junior
"""

import logging

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from codx.junior.calendar.model import (
    Calendar,
    CalendarCreate,
    CalendarEvent,
    CalendarUpdate,
    EventCreate,
    EventQuery,
    EventUpdate,
)
from codx.junior.calendar.service import CalendarError, CalendarService
from codx.junior.calendar.store import CalendarStore
from codx.junior.events.collection_events import (
    ACTION_CREATED,
    ACTION_DELETED,
    ACTION_UPDATED,
    COLLECTION_CALENDARS,
    COLLECTION_CALENDAR_EVENTS,
    emit_collection_event,
)
from codx.junior.model.model import CodxUser
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(user: CodxUser, request: Request) -> CalendarService:
    """
    Build a calendar service scoped to the caller.

    ``settings`` is taken from the request session for **context only** (the
    display timezone today, the active project from M1); the data root is always
    the global calendar store, never ``settings.codx_path``.
    """
    session = getattr(request.state, "codx_junior_session", None)
    settings = getattr(session, "settings", None)
    return CalendarService(store=CalendarStore(), viewer=user, settings=settings)


def _run(action: Callable[[], Any]) -> Any:
    """Run *action*, translating a :class:`CalendarError` into an HTTP error."""
    try:
        return action()
    except CalendarError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


def _if_match(request: Request) -> Optional[int]:
    """
    Read the ``If-Match`` revision of an update (§5.3).

    Accepts ``3``, ``"3"`` and the weak form ``W/"3"``; anything else is
    ignored so the service can still fall back to ``body.revision``.
    """
    raw = str(request.headers.get("if-match") or "").strip()
    if not raw:
        return None
    value = raw.removeprefix("W/").strip().strip('"').strip()
    try:
        return int(value)
    except ValueError:
        logger.warning("Ignoring an invalid If-Match header: %r", raw)
        return None


def _emit_calendars(action: str, calendar: Calendar) -> None:
    """Announce a change of the calendar catalogue (every client reloads it)."""
    emit_collection_event(
        COLLECTION_CALENDARS,
        action,
        {"id": calendar.id, "name": calendar.name},
        source="calendar_api",
    )


def _emit_events(action: str, event: CalendarEvent) -> None:
    """Announce a change of one event, scoped to its project."""
    emit_collection_event(
        COLLECTION_CALENDAR_EVENTS,
        action,
        {"id": event.id, "calendar_ids": list(event.calendar_ids)},
        project_id=event.project_id,
        source="calendar_api",
    )


# ---------------------------------------------------------------------------
# Calendars (§5.1)
# ---------------------------------------------------------------------------


@router.get("/calendars", summary="Calendars the caller may see")
def list_calendars(
    request: Request,
    orphans: bool = Query(
        False, description="Also report the events with a dangling calendar id"
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Return the calendars visible to the caller (scope resolved per request).

    A ``private`` calendar of another user is not returned at all — not even as a
    count or a name — because *not found* and *not allowed* must be
    indistinguishable (§11.1). ``orphans=1`` adds the events whose
    ``calendar_ids`` point to a calendar that no longer exists, so they can be
    repaired instead of being dropped silently (§3.2).
    """
    service = _service(user, request)
    payload: Dict[str, Any] = {
        "calendars": [
            service.calendar_payload(calendar)
            for calendar in service.visible_calendars()
        ],
        "hidden_calendar_ids": service.hidden_calendar_ids(),
    }
    if orphans:
        payload["orphans"] = _run(service.orphan_events)
    return payload


@router.post("/calendars", status_code=201, summary="Create a calendar")
def create_calendar(
    body: CalendarCreate,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Create a calendar owned by the caller.

    A ``workspace`` calendar requires an installation administrator; ``system``
    and ``external`` calendars are materialised and cannot be created by hand.
    """
    service = _service(user, request)
    calendar = _run(lambda: service.create_calendar(body))
    _emit_calendars(ACTION_CREATED, calendar)
    return {"calendar": service.calendar_payload(calendar)}


@router.put("/calendars/{calendar_id}", summary="Update a calendar")
def update_calendar(
    calendar_id: str,
    body: CalendarUpdate,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Apply a partial update to a calendar (name, colour, scope, audience,
    visibility, order, timezone, reminders).

    Only the owner (or an installation administrator) may, and a
    ``system``/``external`` calendar answers 409 ``read_only_calendar``.
    """
    service = _service(user, request)
    calendar = _run(lambda: service.update_calendar(calendar_id, body))
    _emit_calendars(ACTION_UPDATED, calendar)
    return {"calendar": service.calendar_payload(calendar)}


@router.delete("/calendars/{calendar_id}", summary="Delete a calendar")
def delete_calendar(
    calendar_id: str,
    request: Request,
    move_to: Optional[str] = Query(
        None, description="Calendar that inherits the events of the deleted one"
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Delete a calendar.

    Answers 409 ``calendar_in_use`` when it still holds events and ``move_to``
    was not given, and 409 ``read_only_calendar`` for a ``system`` calendar.
    """
    service = _service(user, request)
    calendar = _run(lambda: service.delete_calendar(calendar_id, move_to))
    _emit_calendars(ACTION_DELETED, calendar)
    return {"success": True, "calendar": service.calendar_payload(calendar)}


@router.get("/calendars/{calendar_id}/share", summary="Sharing panel of a calendar")
def get_calendar_share(
    calendar_id: str,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Return ``{scope, owner, audience, members[]}`` for the sharing panel.

    The member list merges the explicit roles with the team audience, so a team
    member that has no explicit role still shows up (as a ``reader``).
    """
    service = _service(user, request)
    calendar = _run(lambda: service.get_calendar(calendar_id))
    return _run(lambda: service.share_payload(calendar))


# ---------------------------------------------------------------------------
# Projects and preferences (§5.1)
# ---------------------------------------------------------------------------


@router.get("/projects", summary="Projects the caller can put events in")
def list_projects(
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Return ``[{project_id, name, role}]`` for the project filter and the event
    editor's project selector.

    The list comes from the existing project discovery, so it never offers a
    project the caller cannot open.
    """
    service = _service(user, request)
    return {"projects": _run(service.projects)}


@router.get("/prefs", summary="The caller's calendar preferences")
def get_prefs(
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Return ``prefs/<username>.json``: hidden calendars, order and week start.

    Visibility and order are per user, never shared, so two users cannot fight
    over one document (§4.1).
    """
    return _run(_service(user, request).get_prefs)


@router.put("/prefs", summary="Save the caller's calendar preferences")
def save_prefs(
    request: Request,
    body: Dict[str, Any] = Body(default_factory=dict),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Merge and persist the caller's preferences (unknown keys are kept)."""
    return _run(lambda: _service(user, request).save_prefs(body))


# ---------------------------------------------------------------------------
# Events: the main read (§5.2)
# ---------------------------------------------------------------------------


@router.post("/events/query", summary="Occurrences of the requested window")
def query_events(
    body: EventQuery,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    **The main read**: visible calendars → project filter → occurrences.

    ``projects[]`` is the only dimension D1 adds: absent or ``[]`` returns
    everything the caller may see, ``["personal"]`` matches ``project_id =
    None`` and several tokens are OR-ed — never AND-ed.

    M0 returns **one occurrence per event**: recurrence expansion is M3, so a
    recurring event is placed at its stored start.
    """
    service = _service(user, request)
    return _run(lambda: service.query(body))


@router.post("/events/quick", summary="Parse a draft from a sentence (not saved)")
def quick_add(
    request: Request,
    body: Dict[str, Any] = Body(default_factory=dict),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Parse ``{"text": "Lunch Ana tom 13:00 1h @Café"}`` into a draft.

    The draft is a ready ``POST /api/calendar/events`` payload but it is **not
    persisted**: the editor confirms it first.
    """
    service = _service(user, request)
    return _run(lambda: service.quick_add(body.get("text")))


@router.get("/events/{event_id}", summary="Raw event document")
def get_event(
    event_id: str,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Return the stored event (the editor needs the rule and the reminders, not
    the computed instances).

    A ``visibility: private`` event of somebody else is masked: the slot is
    returned, the title becomes *Private* (§11.1).
    """
    service = _service(user, request)
    event = _run(lambda: service.get_event(event_id))
    return _run(lambda: service.event_payload(event))


@router.post("/events", status_code=201, summary="Create an event")
def create_event(
    body: EventCreate,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Create an event.

    ``calendar_ids[]`` is required, de-duplicated and every id must resolve
    (``unknown_calendar``); a ``system``/``external``-only event is refused with
    409 ``read_only_calendar``; the event inherits the default reminders of its
    primary calendar when it declares none.
    """
    service = _service(user, request)
    event = _run(lambda: service.create_event(body))
    _emit_events(ACTION_CREATED, event)
    return {"event": event.model_dump()}


@router.put("/events/{event_id}", summary="Update an event")
def update_event(
    event_id: str,
    body: EventUpdate,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Apply a partial update, guarded by ``If-Match: <revision>``.

    A stale copy answers 409 ``revision_mismatch`` (with the current revision in
    the message) instead of silently overwriting the other tab's change.
    """
    service = _service(user, request)
    expected = _if_match(request)
    event = _run(lambda: service.update_event(event_id, body, expected))
    _emit_events(ACTION_UPDATED, event)
    return {"event": event.model_dump()}


@router.delete("/events/{event_id}", summary="Delete an event")
def delete_event(
    event_id: str,
    request: Request,
    scope: str = Query("all", description="single | following | all"),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Delete an event.

    ``scope`` keeps the Google semantics; per-instance deletes of a recurring
    event arrive with the recurrence engine (M3) and are refused (400) instead
    of deleting the whole series by accident.
    """
    service = _service(user, request)
    event = _run(lambda: service.delete_event(event_id, scope))
    _emit_events(ACTION_DELETED, event)
    return {"success": True, "event": event.model_dump()}


@router.post("/events/{event_id}/snooze", summary="Postpone a reminder")
def snooze_event(
    event_id: str,
    request: Request,
    body: Dict[str, Any] = Body(default_factory=dict),
    user: CodxUser = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Validate ``{minutes, occurrence_start}`` and answer the new trigger times.

    Delivery belongs to milestone M2 (background ticker + ``delivered.json``), so
    the answer carries ``persisted: false``: nothing is scheduled yet, and the
    contract the client is built against is already final.
    """
    service = _service(user, request)
    return _run(
        lambda: service.snooze(
            event_id, body.get("minutes"), body.get("occurrence_start")
        )
    )


# Made with ❤️ by codx-junior
