"""
Domain model of the Agenda / Calendar manager.

The calendar is **global** (installation-wide, decision D1): a user works on
several projects and needs one agenda, so a project is a **filter** and never a
namespace. Four entities describe the domain (analysis §3):

* :class:`Calendar` – a colour, a timezone, default reminders and a **scope**
  (``private`` / ``team`` / ``workspace`` / ``system`` / ``external``). The ACL
  is resolved per request from the scope plus the member list; nothing about a
  viewer is ever baked into the stored document (§11.1).
* :class:`CalendarEvent` – the stored document. The *event* owns its
  memberships (``calendar_ids``), so one item can live in several calendars
  without being duplicated (requirement R6), and a task is never copied into
  the calendar: it is *projected* (§6).
* :class:`Occurrence` – a **derived** placement of an event inside a requested
  date range (recurring instances included). Never stored.
* :class:`ReminderRule` – ``minutes_before`` + channel, stored on the event or
  as a calendar default.

Milestone M0 only stores and validates these documents: recurrence is kept
verbatim (expansion is M3) and reminders are not scheduled yet (M2), so nothing
in this module interprets them.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import uuid

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Canonical values
# ---------------------------------------------------------------------------

#: ``private`` = only its owner, ``team`` = one team, ``workspace`` = everybody,
#: ``system`` = the virtual read-only ``codx-junior`` calendar,
#: ``external`` = a subscribed feed.
CALENDAR_SCOPES = ("private", "team", "workspace", "system", "external")

#: Calendars nobody can write, whatever the viewer's role is (§3.1).
READ_ONLY_SCOPES = ("system", "external")

#: Roles a calendar member may hold (§11.1).
CALENDAR_ROLES = ("owner", "writer", "reader")

#: Roles allowed to create/update/delete events in a calendar.
WRITE_ROLES = ("owner", "writer")

#: Where a calendar item comes from. Only ``native`` documents are writable;
#: the others are projections (§6).
EVENT_SOURCES = ("native", "task", "meet", "orchestration", "external")

EVENT_STATUSES = ("confirmed", "tentative", "cancelled")
EVENT_VISIBILITIES = ("default", "public", "private")

#: Reminder channels. Only ``popup`` is implemented in M2.
REMINDER_CHANNELS = ("popup", "email", "push")

#: Default colour of a new calendar (brand magenta, design tokens).
DEFAULT_CALENDAR_COLOR = "#CC0099"

#: Default timezone of a new calendar; the API overwrites it with the caller's.
DEFAULT_TIMEZONE = "UTC"

CalendarScope = Literal["private", "team", "workspace", "system", "external"]
CalendarRole = Literal["owner", "writer", "reader"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """
    Return the current UTC time as an ISO-8601 string.

    The store always writes **UTC** (``2026-02-10T09:00:00Z``): the event keeps
    its own timezone for display, while every comparison, sort and shard key is
    timezone independent (§8.2).

    Returns:
        Something like ``"2026-02-10T09:00:00Z"``.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """
    Return a readable, collision-safe id.

    Args:
        prefix: Short kind prefix, e.g. ``"cal"`` or ``"evt"``.

    Returns:
        ``"<prefix>_<uuid4 hex>"``.
    """
    return f"{prefix}_{uuid.uuid4().hex}"


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO-8601 date-time, returning ``None`` when it is empty/invalid.

    A value without an offset is treated as UTC, because that is what the store
    writes and mixing naive and aware datetimes would raise on comparison.

    Args:
        value: ISO-8601 string (``Z`` or ``+00:00`` accepted).

    Returns:
        A timezone-aware :class:`datetime`, or ``None`.
    """
    if not value:
        return None
    try:
        # ``Z`` is not accepted by every supported Python version.
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_utc_iso(value: datetime) -> str:
    """Serialise *value* as UTC ISO-8601 with a ``Z`` suffix."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def month_key(value: Optional[str]) -> str:
    """
    Return the ``YYYY-MM`` shard key of an ISO date-time.

    Events are stored in the shard of their **start** month, in UTC (§4.4).

    Args:
        value: ISO-8601 date-time.

    Returns:
        ``"YYYY-MM"``.

    Raises:
        ValueError: When the value is missing or not an ISO date-time.
    """
    parsed = parse_iso(value)
    if parsed is None:
        raise ValueError(f"invalid ISO date-time: {value!r}")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m")


def shift_month(month: str, delta: int = 0) -> str:
    """
    Move a ``YYYY-MM`` key by *delta* months.

    Args:
        month: ``"YYYY-MM"`` key.
        delta: Months to add (may be negative).

    Returns:
        The shifted ``"YYYY-MM"`` key.

    Raises:
        ValueError: When *month* is not a ``YYYY-MM`` string.

    Example:
        >>> shift_month("2026-01", -1)
        '2025-12'
    """
    year, _, number = str(month or "").partition("-")
    try:
        index = int(year) * 12 + (int(number) - 1) + int(delta)
    except (TypeError, ValueError) as ex:
        raise ValueError(f"invalid month key: {month!r}") from ex
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_between(start_month: str, end_month: str) -> List[str]:
    """
    List every ``YYYY-MM`` key from *start_month* to *end_month* inclusive.

    Args:
        start_month: First ``"YYYY-MM"`` key.
        end_month: Last ``"YYYY-MM"`` key; before the start yields a single key.

    Returns:
        The ordered month keys.
    """
    months: List[str] = []
    current = start_month
    while True:
        months.append(current)
        if current >= end_month:
            return months
        current = shift_month(current, 1)


def _normalize_ids(value: Any) -> List[str]:
    """Trim, drop blanks and de-duplicate a list of ids, order preserved."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    ids: List[str] = []
    for item in value:
        clean = str(item or "").strip()
        if clean and clean not in ids:
            ids.append(clean)
    return ids


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class ReminderRule(BaseModel):
    """
    One reminder of an event (or a calendar default).

    ``minutes_before`` is counted from the **occurrence start**; ``0`` means
    "when the event starts". Channels are limited to :data:`REMINDER_CHANNELS`;
    the background ticker of M2 implements ``popup`` only.
    """

    channel: str = Field(
        default="popup", description="popup | email | push (popup in M2)"
    )
    minutes_before: int = Field(
        default=10, ge=0, description="Minutes before the occurrence start"
    )

    @field_validator("channel", mode="before")
    @classmethod
    def _normalize_channel(cls, value: Any) -> str:
        """Lower-case the channel and reject unknown values."""
        channel = str(value or "popup").strip().lower()
        if channel not in REMINDER_CHANNELS:
            raise ValueError(
                f"unknown reminder channel {value!r}; "
                f"expected one of {', '.join(REMINDER_CHANNELS)}"
            )
        return channel


class CalendarMember(BaseModel):
    """A user's role inside a calendar (see :data:`CALENDAR_ROLES`)."""

    user: str = Field(default="", description="Username")
    role: CalendarRole = Field(default="reader", description="owner | writer | reader")

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value: Any) -> str:
        """Lower-case the role and reject unknown values."""
        role = str(value or "reader").strip().lower()
        if role not in CALENDAR_ROLES:
            raise ValueError(
                f"unknown calendar role {value!r}; "
                f"expected one of {', '.join(CALENDAR_ROLES)}"
            )
        return role


class Calendar(BaseModel):
    """
    One calendar of the global agenda (§3.1).

    The document carries **no project**: since the store is global, a calendar
    is shared by every project and a project is only a filter on the events.
    Visibility is decided per request from ``scope`` + ``members`` (§11.1), so
    the same document can be listed for one user and be invisible to another.

    A ``team`` calendar names its team in ``audience`` as
    ``{kind: "team", project: <project_id>, name: <team>, members: [...]}``:
    teams live in the project control plane and are resolved on read.

    Example:
        >>> Calendar(name="Work", scope="private", owner="alice").is_read_only
        False
    """

    id: str = Field(
        default="", description="Calendar id (``cal_...``), assigned on first save"
    )
    name: str = Field(default="", description="Human readable name")
    color: str = Field(
        default=DEFAULT_CALENDAR_COLOR, description="Hex colour used by the chips"
    )
    description: str = Field(default="", description="Free-form description")
    scope: CalendarScope = Field(
        default="private", description="private | team | workspace | system | external"
    )
    owner: Optional[str] = Field(
        default=None,
        description="Username that owns the calendar; None for system/external ones",
    )
    audience: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Who the calendar is shared with, per scope: "
            '{kind: "team", project, name, members} or {kind: "workspace"}'
        ),
    )
    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        description="Timezone used to render and (M3) expand recurrences",
    )
    read_only: bool = Field(
        default=False, description="Stored read-only flag (system/external are too)"
    )
    provider: str = Field(
        default="local", description="local | ics (only ``local`` is writable)"
    )
    default_reminders: List[ReminderRule] = Field(
        default_factory=list,
        description="Reminders copied to new events (and tasks) of this calendar",
    )
    members: List[CalendarMember] = Field(
        default_factory=list, description="Explicit roles of the shared members"
    )
    visible: bool = Field(
        default=True, description="Initial visibility of the calendar in the UI"
    )
    order: int = Field(default=0, description="Sort index in the sidebar")
    created_at: str = Field(
        default_factory=now_iso, description="ISO-8601 creation timestamp (UTC)"
    )
    updated_at: str = Field(
        default_factory=now_iso, description="ISO-8601 last update timestamp (UTC)"
    )

    @field_validator("scope", mode="before")
    @classmethod
    def _normalize_scope(cls, value: Any) -> str:
        """Lower-case the scope and reject unknown values."""
        scope = str(value or "private").strip().lower()
        if scope not in CALENDAR_SCOPES:
            raise ValueError(
                f"unknown calendar scope {value!r}; "
                f"expected one of {', '.join(CALENDAR_SCOPES)}"
            )
        return scope

    @field_validator("members", mode="before")
    @classmethod
    def _normalize_members(cls, value: Any) -> List[Any]:
        """Drop members without a username and de-duplicate them (first wins)."""
        if not value:
            return []
        seen: List[str] = []
        members: List[Any] = []
        for item in value:
            user = (
                item.get("user") if isinstance(item, dict) else getattr(item, "user", None)
            )
            clean = str(user or "").strip()
            if not clean or clean in seen:
                continue
            seen.append(clean)
            members.append(item)
        return members

    @property
    def is_read_only(self) -> bool:
        """
        Tell whether the calendar can never be written by anybody.

        A ``system`` (the virtual ``codx-junior`` calendar) or ``external``
        (subscribed feed) calendar is read-only for every viewer, admins
        included (§11.1).
        """
        return bool(self.read_only) or self.scope in READ_ONLY_SCOPES

    def member_role(self, username: Optional[str]) -> Optional[str]:
        """
        Return the role of *username* in this calendar, or ``None``.

        The ``owner`` field wins over ``members`` so a stored document whose
        member list forgot its own owner still behaves correctly.

        Args:
            username: Viewer username.

        Returns:
            ``"owner"``, ``"writer"``, ``"reader"`` or ``None``.
        """
        if not username:
            return None
        if self.owner and username == self.owner:
            return "owner"
        for member in self.members:
            if member.user == username:
                return member.role
        return None

    def member_usernames(self) -> List[str]:
        """Return the usernames explicitly listed in the calendar."""
        return [member.user for member in self.members if member.user]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class Recurrence(BaseModel):
    """
    RFC 5545 recurrence rule and its exceptions (§8.1).

    M0 **stores and returns it verbatim**: ``python-dateutil`` is not a
    dependency yet and expansion is milestone M3, so nothing in M0 interprets
    these fields. Declaring the model now keeps M3 purely additive.

    ``overrides`` is keyed by the *original* occurrence start (``RECURRENCE-ID``
    in ICS terms), e.g.::

        {"2026-03-11T09:00:00Z": {"start": "2026-03-11T14:00:00Z",
                                  "title": "Spec review (moved)"}}
    """

    rrule: str = Field(
        default="",
        description='RFC 5545 rule, e.g. "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"',
    )
    exdates: List[str] = Field(
        default_factory=list, description="Occurrence starts removed from the series"
    )
    overrides: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-instance changes keyed by the original occurrence start",
    )

    @property
    def is_empty(self) -> bool:
        """Tell whether the document recurrences nothing (a single event)."""
        return not (self.rrule or "").strip()


class CalendarEvent(BaseModel):
    """
    One stored calendar event (§3.2).

    **Invariants**

    1. ``calendar_ids`` is non-empty, de-duplicated, and every id must resolve
       to an existing calendar (validated on write by the service; a dangling
       id is reported by ``GET /calendars?orphans=1`` for repair).
    2. :attr:`source` ``system``/``external`` content is never writable: the
       service rejects the write with ``read_only_calendar``.
    3. ``end`` is after ``start`` (``all_day`` events are inclusive-exclusive,
       so ``end = start + 1 day``).
    4. :attr:`revision` is incremented on every write; clients send it back as
       ``If-Match`` (optimistic locking, §5.3).
    5. A recurring event stores the **first** ``start``; instances are computed
       (M3), never persisted.
    6. A recurring event cannot span different calendars per instance (a Google
       limitation kept on purpose: it removes a whole class of bugs).

    Only invariant 1 is enforced here, because it is the one that makes a
    document meaningless; the rest need the service (calendar existence,
    viewer's rights) or the recurrence engine.

    Example:
        >>> event = CalendarEvent(calendar_ids=["cal_work"],
        ...                       start="2026-02-10T09:00:00Z",
        ...                       end="2026-02-10T10:00:00Z")
        >>> event.shard_month
        '2026-02'
    """

    id: str = Field(
        default="", description="Event id (``evt_...``), assigned on first save"
    )
    calendar_ids: List[str] = Field(
        default_factory=list,
        description="Calendars the event is listed in (R6: the EVENT owns them)",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Project the event belongs to; None = personal",
    )
    title: str = Field(default="", description="Title shown in the chip")
    description: str = Field(default="", description="Free-form description")
    location: str = Field(default="", description="Free-form location or meet link")
    start: str = Field(default="", description="Start as a UTC ISO-8601 string")
    end: str = Field(default="", description="End as a UTC ISO-8601 string")
    all_day: bool = Field(default=False, description="All-day (date, not slot)")
    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        description="Timezone the event is expressed in (display + M3 expansion)",
    )
    recurrence: Optional[Recurrence] = Field(
        default=None, description="Recurrence rule, None for a single event"
    )
    reminders: List[ReminderRule] = Field(
        default_factory=list,
        description="Reminders of this event (calendar defaults are inherited)",
    )
    status: str = Field(default="confirmed", description="confirmed | tentative | cancelled")
    visibility: str = Field(
        default="default",
        description="default | public | private (private masks the details, §11.1)",
    )
    source: str = Field(
        default="native",
        description="native | task | meet | orchestration | external",
    )
    refs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Back-references, e.g. {chat_id} / {meet_id} / {run_id} / {external_uid}",
    )
    guests: List[str] = Field(default_factory=list, description="Invited usernames")
    created_by: Optional[str] = Field(default=None, description="Username that created it")
    created_at: str = Field(
        default_factory=now_iso, description="ISO-8601 creation timestamp (UTC)"
    )
    updated_at: str = Field(
        default_factory=now_iso, description="ISO-8601 last update timestamp (UTC)"
    )
    revision: int = Field(
        default=1, description="Optimistic-locking version, incremented on every write"
    )

    @field_validator("calendar_ids", mode="before")
    @classmethod
    def _validate_calendar_ids(cls, value: Any) -> List[str]:
        """De-duplicate the memberships and refuse an event in no calendar."""
        ids = _normalize_ids(value)
        if not ids:
            raise ValueError("calendar_ids must contain at least one calendar id")
        return ids

    @property
    def shard_month(self) -> str:
        """
        Return the ``YYYY-MM`` shard this event is stored in.

        Raises:
            ValueError: When ``start`` is missing or not an ISO date-time.
        """
        return month_key(self.start)

    @property
    def is_recurring(self) -> bool:
        """Tell whether the event expands to several occurrences (M3)."""
        return bool(self.recurrence and not self.recurrence.is_empty)

    def start_datetime(self) -> Optional[datetime]:
        """Return :attr:`start` parsed, or ``None`` when it is invalid."""
        return parse_iso(self.start)

    def end_datetime(self) -> Optional[datetime]:
        """
        Return :attr:`end` parsed, falling back to :attr:`start`.

        The fallback keeps a half-written document renderable instead of
        losing the whole event.
        """
        return parse_iso(self.end) or self.start_datetime()


# ---------------------------------------------------------------------------
# Occurrences (derived, never stored)
# ---------------------------------------------------------------------------


class Occurrence(BaseModel):
    """
    One placement of an event inside a requested range (§3.3).

    ``calendar_id`` (singular) is the calendar whose colour the chip uses; the
    full :attr:`calendar_ids` list is what the UI shows as multi-calendar
    badges. This is the key to requirement R6: **one occurrence, N
    memberships.**

    An occurrence is always the result of a server-side expansion: the client
    formats what the API returns and never does date maths (§8.3).
    """

    event_id: str = Field(default="", description="Id of the source event/task/run")
    calendar_ids: List[str] = Field(
        default_factory=list, description="Every calendar the item belongs to"
    )
    project_id: Optional[str] = Field(
        default=None, description="Project of the item; None = personal"
    )
    title: str = Field(default="", description="Title (masked when private)")
    start: str = Field(default="", description="Occurrence start, UTC ISO-8601")
    end: str = Field(default="", description="Occurrence end, UTC ISO-8601")
    all_day: bool = Field(default=False, description="All-day marker (no time slot)")
    source: str = Field(default="native", description="native | task | meet | ...")
    refs: Dict[str, Any] = Field(
        default_factory=dict, description="Back-references to the source document"
    )
    calendar_id: str = Field(
        default="", description="Calendar that provides the colour of the chip"
    )
    is_recurring_instance: bool = Field(
        default=False, description="True for the computed instances of a series"
    )
    original_start: Optional[str] = Field(
        default=None, description="Original occurrence start of a moved instance"
    )
    overdue: bool = Field(
        default=False, description="Deadline already passed and the item is not done"
    )
    read_only: bool = Field(
        default=False, description="Item comes from a projection: move the source instead"
    )


# ---------------------------------------------------------------------------
# Request payloads – these mirror the frontend `calendar` API module.
# ---------------------------------------------------------------------------


class CalendarCreate(BaseModel):
    """Payload for ``POST /api/calendar/calendars``."""

    name: str = Field(description="Calendar name")
    color: str = Field(default=DEFAULT_CALENDAR_COLOR, description="Hex colour")
    description: str = Field(default="", description="Free-form description")
    scope: CalendarScope = Field(default="private", description="Calendar scope")
    audience: Optional[Dict[str, Any]] = Field(
        default=None, description="Team/workspace the calendar is shared with"
    )
    timezone: str = Field(default=DEFAULT_TIMEZONE, description="Calendar timezone")
    default_reminders: List[ReminderRule] = Field(
        default_factory=list, description="Reminders copied to new events"
    )
    visible: bool = Field(default=True, description="Initial visibility")
    order: int = Field(default=0, description="Sort index in the sidebar")


class CalendarUpdate(BaseModel):
    """Payload for ``PUT /api/calendar/calendars/{id}`` (every field optional)."""

    name: Optional[str] = Field(default=None, description="Calendar name")
    color: Optional[str] = Field(default=None, description="Hex colour")
    description: Optional[str] = Field(default=None, description="Description")
    scope: Optional[CalendarScope] = Field(default=None, description="Calendar scope")
    audience: Optional[Dict[str, Any]] = Field(
        default=None, description="Team/workspace the calendar is shared with"
    )
    timezone: Optional[str] = Field(default=None, description="Calendar timezone")
    default_reminders: Optional[List[ReminderRule]] = Field(
        default=None, description="Reminders copied to new events"
    )
    members: Optional[List[CalendarMember]] = Field(
        default=None, description="Explicit member roles"
    )
    visible: Optional[bool] = Field(default=None, description="Visibility in the UI")
    order: Optional[int] = Field(default=None, description="Sort index in the sidebar")


class EventQuery(BaseModel):
    """
    Payload for ``POST /api/calendar/events/query`` – the main read (§5.2).

    ``from`` is a Python keyword, so the field is named ``from_`` and exposed
    with an alias: clients keep sending ``from``.

    The project filter is the only dimension the global store adds:

    * ``projects`` absent or empty → everything the viewer can see;
    * ``["acme-web"]`` → items of that project;
    * ``["personal"]`` → items whose ``project_id`` is ``None``;
    * several ids → the **OR** of the above, never AND.

    Example:
        >>> query = EventQuery(**{"from": "2026-02-01T00:00:00Z",
        ...                       "to": "2026-03-01T00:00:00Z"})
        >>> query.projects is None
        True
    """

    model_config = ConfigDict(populate_by_name=True)

    calendar_ids: Optional[List[str]] = Field(
        default=None, description="Only these calendars; None/[] = every visible one"
    )
    from_: str = Field(
        alias="from", description="Range start (inclusive), UTC ISO-8601"
    )
    to: str = Field(description="Range end (exclusive), UTC ISO-8601")
    projects: Optional[List[str]] = Field(
        default=None,
        description="Project filter (OR). 'personal' matches project_id = None",
    )
    q: Optional[str] = Field(default=None, description="Free text search")
    sources: Optional[List[str]] = Field(
        default=None, description="native | task | meet | orchestration | external"
    )
    include_hidden: bool = Field(
        default=False, description="Include occurrences of hidden calendars"
    )

    @field_validator("calendar_ids", "projects", "sources", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> Optional[List[str]]:
        """Trim and de-duplicate the list filters (``None`` stays ``None``)."""
        if value is None:
            return None
        return _normalize_ids(value)


class EventCreate(BaseModel):
    """Payload for ``POST /api/calendar/events`` (``calendar_ids`` required)."""

    calendar_ids: List[str] = Field(description="Calendars the event is listed in")
    title: str = Field(default="", description="Event title")
    description: str = Field(default="", description="Free-form description")
    location: str = Field(default="", description="Location or meet link")
    start: str = Field(description="Start, ISO-8601")
    end: str = Field(description="End, ISO-8601")
    all_day: bool = Field(default=False, description="All-day event")
    timezone: str = Field(default=DEFAULT_TIMEZONE, description="Event timezone")
    project_id: Optional[str] = Field(
        default=None, description="Project of the event; None = personal"
    )
    recurrence: Optional[Recurrence] = Field(
        default=None, description="Recurrence rule (stored, expanded in M3)"
    )
    reminders: List[ReminderRule] = Field(
        default_factory=list, description="Explicit reminders of this event"
    )
    status: str = Field(default="confirmed", description="confirmed | tentative | cancelled")
    visibility: str = Field(
        default="default", description="default | public | private"
    )
    guests: List[str] = Field(default_factory=list, description="Invited usernames")
    refs: Dict[str, Any] = Field(
        default_factory=dict, description="Back-references to other documents"
    )

    @field_validator("calendar_ids", mode="before")
    @classmethod
    def _validate_calendar_ids(cls, value: Any) -> List[str]:
        """De-duplicate the memberships and refuse an event in no calendar."""
        ids = _normalize_ids(value)
        if not ids:
            raise ValueError("calendar_ids must contain at least one calendar id")
        return ids


class EventUpdate(BaseModel):
    """Payload for ``PUT /api/calendar/events/{id}`` (every field optional)."""

    calendar_ids: Optional[List[str]] = Field(
        default=None, description="Replacement membership list"
    )
    title: Optional[str] = Field(default=None, description="Event title")
    description: Optional[str] = Field(default=None, description="Description")
    location: Optional[str] = Field(default=None, description="Location or meet link")
    start: Optional[str] = Field(default=None, description="Start, ISO-8601")
    end: Optional[str] = Field(default=None, description="End, ISO-8601")
    all_day: Optional[bool] = Field(default=None, description="All-day event")
    timezone: Optional[str] = Field(default=None, description="Event timezone")
    project_id: Optional[str] = Field(
        default=None, description="Project of the event; None = personal"
    )
    recurrence: Optional[Recurrence] = Field(
        default=None, description="Recurrence rule (None clears it)"
    )
    reminders: Optional[List[ReminderRule]] = Field(
        default=None, description="Replacement reminder list"
    )
    status: Optional[str] = Field(default=None, description="Event status")
    visibility: Optional[str] = Field(default=None, description="Event visibility")
    guests: Optional[List[str]] = Field(default=None, description="Invited usernames")
    revision: Optional[int] = Field(
        default=None,
        description="Revision the client last saw (If-Match equivalent, §5.3)",
    )

    @field_validator("calendar_ids", mode="before")
    @classmethod
    def _validate_calendar_ids(cls, value: Any) -> Optional[List[str]]:
        """De-duplicate the memberships when they are being replaced."""
        if value is None:
            return None
        ids = _normalize_ids(value)
        if not ids:
            raise ValueError("calendar_ids must contain at least one calendar id")
        return ids


# Made with ❤️ by codx-junior
