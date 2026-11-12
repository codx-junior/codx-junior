"""
Calendar (agenda) subsystem – a **global** multi-calendar agenda.

The calendar is installation-wide (decision D1 in
``design/06-calendar/01-analysis.md``): a user works on several projects and
needs one agenda, so a project is a **filter**, never a namespace. That is why
the data lives in ``CODX_JUNIOR_CALENDAR_PATH`` (default
``/home/codx-junior/calendar``) and never in a project's ``.codx``.

This package contains:

* :mod:`codx.junior.calendar.model` – the domain model: ``Calendar`` (scope,
  owner, audience, members), ``CalendarEvent`` (the EVENT owns its
  memberships), the derived ``Occurrence``, ``ReminderRule`` and the request
  payloads.
* :mod:`codx.junior.calendar.store` – the crash-safe JSON store:
  ``calendars.json``, ``events/<YYYY-MM>.json`` month shards, the reminder
  delivery log and the per-user preferences.

Milestone M0 only needs these two modules: the ACL/projection service, the
``/api/calendar`` router and the client arrive in the following steps, and
recurrence expansion (M3) / reminder delivery (M2) reuse the same documents.

Example:
    >>> from codx.junior.calendar import Calendar, CalendarStore
    >>> store = CalendarStore("/tmp/calendar")            # doctest: +SKIP
    >>> store.save_calendar(Calendar(name="Work"))        # doctest: +SKIP

Made with ❤️ by codx-junior
"""

from .model import (
    CALENDAR_ROLES,
    CALENDAR_SCOPES,
    DEFAULT_CALENDAR_COLOR,
    DEFAULT_TIMEZONE,
    EVENT_SOURCES,
    READ_ONLY_SCOPES,
    WRITE_ROLES,
    Calendar,
    CalendarCreate,
    CalendarEvent,
    CalendarMember,
    CalendarUpdate,
    EventCreate,
    EventQuery,
    EventUpdate,
    Occurrence,
    Recurrence,
    ReminderRule,
    month_key,
    months_between,
    new_id,
    now_iso,
    parse_iso,
    shift_month,
)
from .store import (
    CALENDAR_PATH_ENV,
    CALENDARS_FILE,
    DEFAULT_CALENDAR_PATH,
    DELIVERED_FILE,
    EVENTS_DIR,
    MAX_EVENT_DURATION_DAYS,
    PREFS_DIR,
    SHARD_PADDING_MONTHS,
    CalendarStore,
    CalendarStoreError,
    get_calendar_root,
)

__all__ = [
    # Canonical values / helpers
    "CALENDAR_ROLES",
    "CALENDAR_SCOPES",
    "CALENDAR_PATH_ENV",
    "CALENDARS_FILE",
    "DEFAULT_CALENDAR_COLOR",
    "DEFAULT_CALENDAR_PATH",
    "DEFAULT_TIMEZONE",
    "DELIVERED_FILE",
    "EVENT_SOURCES",
    "EVENTS_DIR",
    "MAX_EVENT_DURATION_DAYS",
    "PREFS_DIR",
    "READ_ONLY_SCOPES",
    "SHARD_PADDING_MONTHS",
    "WRITE_ROLES",
    # Models
    "Calendar",
    "CalendarCreate",
    "CalendarEvent",
    "CalendarMember",
    "CalendarUpdate",
    "EventCreate",
    "EventQuery",
    "EventUpdate",
    "Occurrence",
    "Recurrence",
    "ReminderRule",
    # Store
    "CalendarStore",
    "CalendarStoreError",
    "get_calendar_root",
    # Date helpers
    "month_key",
    "months_between",
    "new_id",
    "now_iso",
    "parse_iso",
    "shift_month",
]

# Made with ❤️ by codx-junior
