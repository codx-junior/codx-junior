"""
Calendar service – per-request ACL, filtered query and the write path.

The store (:mod:`codx.junior.calendar.store`) only persists documents; this
module is the layer that knows **who is asking** and **what the question means**
(analysis §5, §11):

* **ACL, resolved per request** (§11.1). ``scope`` decides who *sees* a calendar
  (``private`` = its owner only, ``team`` = the audience members, ``workspace`` /
  ``system`` / ``external`` = every authenticated user) and the member role
  decides who *writes* it (``owner`` / ``writer``; a ``reader`` gets a 403, a
  ``system``/``external`` calendar is read-only for everybody, admins included).
  A private calendar of another user is answered with the same 404 as a
  calendar that does not exist: *not found* and *not allowed* must be
  indistinguishable, otherwise the error message leaks the calendar (§11.1).
* **ACL at the calendar list, not only at the event level.** Occurrences carry
  only the memberships the viewer may see, so a hidden calendar is never
  revealed through an event's ``calendar_ids`` or through a count.
* **The project filter** (§5.2) is the only dimension decision D1 adds: absent
  or ``[]`` means *every project*, ``["personal"]`` matches ``project_id =
  None`` and several tokens are **OR**-ed, never AND-ed. It is applied *after*
  visibility and *before* any expansion.
* **Writes** enforce the invariants of §3.2: at least one calendar, every id
  must resolve (``unknown_calendar``), a read-only calendar refuses the write
  (``read_only_calendar``), ``end > start`` unless all-day, at most
  ``MAX_EVENT_DURATION_DAYS``, and the ``revision`` grows on every write while
  ``If-Match`` protects it (``revision_mismatch``).

Milestone M0 deliberately stops there: **no recurrence expansion** (a recurring
event yields exactly one occurrence at its stored start, M3) and **no
projection** (tasks, meets and orchestration runs join the agenda in M1, which
is why ``truncated_projects`` is always empty for now). Reminders are not
scheduled either (M2): :meth:`CalendarService.snooze` validates and answers the
request without persisting a delivery.

Error handling
--------------
Everything the caller did wrong is raised as :class:`CalendarError` carrying a
§5.3 string code (``read_only_calendar``, ``revision_mismatch``,
``event_too_long``, ``invalid_recurrence``, ``unknown_calendar``,
``calendar_in_use``) and the HTTP status the router must use, so the same
service can be driven from a test, an AI tool or the REST API.

Example:
    >>> from codx.junior.calendar.service import CalendarService
    >>> service = CalendarService(viewer="alice")            # doctest: +SKIP
    >>> service.visible_calendars()                          # doctest: +SKIP

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import os
import re

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from pydantic import ValidationError

from codx.junior.model.user import USER_ROLE_ADMIN, USER_ROLE_USER

from .model import (
    DEFAULT_TIMEZONE,
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
    now_iso,
    parse_iso,
    to_utc_iso,
)
from .store import MAX_EVENT_DURATION_DAYS, CalendarStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Project filter token that matches the items with ``project_id = None``
#: ("personal" events belong to the user, not to a project, §3.1).
PERSONAL_TOKEN = "personal"

#: A query window wider than this is rejected: the agenda paginates (§12).
MAX_QUERY_DAYS = 92

#: Allowed ``week_start`` values of the per-user preferences.
WEEK_STARTS = ("monday", "saturday", "sunday")
DEFAULT_WEEK_START = "monday"

#: Longest snooze of a single reminder, in minutes (a day).
MAX_SNOOZE_MINUTES = 24 * 60

# Default reminder used by :meth:`CalendarService.quick_add` / an event that
# declares none and lives in a calendar that declares none either.
DEFAULT_EVENT_MINUTES = 60

# -- error codes (§5.3) ------------------------------------------------------

ERROR_UNKNOWN_CALENDAR = "unknown_calendar"
ERROR_READ_ONLY_CALENDAR = "read_only_calendar"
ERROR_REVISION_MISMATCH = "revision_mismatch"
ERROR_EVENT_TOO_LONG = "event_too_long"
ERROR_INVALID_RECURRENCE = "invalid_recurrence"
ERROR_CALENDAR_IN_USE = "calendar_in_use"
#: The payload is incoherent (``end`` before ``start``, no calendar, ...).
ERROR_INVALID_EVENT = "invalid_event"
#: The requested range is reversed or wider than :data:`MAX_QUERY_DAYS`.
ERROR_INVALID_RANGE = "invalid_range"
#: The document does not exist *or* the viewer may not know it exists (§11.1).
ERROR_NOT_FOUND = "not_found"

# -- quick add patterns ------------------------------------------------------

_QUICK_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_QUICK_MERIDIEM = re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
_QUICK_DURATION = re.compile(
    r"\b(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b", re.IGNORECASE
)
_QUICK_LOCATION = re.compile(r"@([^\s,]+)")
_QUICK_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

#: Standalone words that move the draft to another day. ``tom`` is matched
#: case-sensitively (so a guest called *Tom* is not eaten) and counts as
#: *tomorrow*, which is what the design example uses.
_DAY_TOKENS = ("tomorrow", "today", "tom")

#: Keys the preferences document owns. Anything else is preserved verbatim, so
#: a later milestone can add a key without migrating the file.
PREFS_KEYS = ("hidden_calendars", "order", "week_start")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CalendarError(Exception):
    """
    A calendar request that cannot be satisfied.

    Carries the §5.3 string code (``detail`` must *start* with it), a human
    readable message and the HTTP status the router must answer with.

    Args:
        code: Machine readable code, see the ``ERROR_*`` constants.
        message: Human readable explanation.
        status_code: HTTP status the API answers with.

    Example:
        >>> error = CalendarError(ERROR_REVISION_MISMATCH, "stale", 409)
        >>> str(error)
        'revision_mismatch: stale'
    """

    def __init__(self, code: str, message: str = "", status_code: int = 400):
        self.code = code
        self.status_code = status_code
        self.message = message or code
        super().__init__(f"{code}: {message}" if message else code)

    @property
    def detail(self) -> str:
        """Return the string used as the ``HTTPException`` detail."""
        return str(self)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _dedupe(values: Optional[Iterable[Any]]) -> List[str]:
    """Trim, drop blanks and de-duplicate *values*, order preserved."""
    result: List[str] = []
    for value in values or []:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _as_dict_list(value: Any) -> List[Any]:
    """Return *value* as a list (``None`` becomes ``[]``)."""
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CalendarService:
    """
    Read and write the global agenda on behalf of one viewer.

    Args:
        store: Store to use; defaults to the global calendar store
            (``CODX_JUNIOR_CALENDAR_PATH``).
        viewer: The authenticated :class:`~codx.junior.model.user.CodxUser`
            (a plain username is accepted for tests and tools).
        settings: Optional project settings, used only for context (the
            display timezone today, the project scope the caller is working in
            from M1). It is **never** a storage root: the calendar is global.

    Example:
        >>> service = CalendarService(viewer="alice")          # doctest: +SKIP
        >>> [calendar.name for calendar in service.visible_calendars()]
        ['Work', 'Personal', 'Team — Platform']
    """

    def __init__(
        self,
        store: Optional[CalendarStore] = None,
        viewer: Any = None,
        settings: Any = None,
    ):
        self.store = store or CalendarStore()
        self.viewer = viewer
        self.settings = settings
        self._catalog: Optional[List[Calendar]] = None

    # -- identity ----------------------------------------------------------

    @property
    def username(self) -> str:
        """Return the viewer's username (``""`` when anonymous)."""
        if isinstance(self.viewer, str):
            return self.viewer.strip()
        return str(getattr(self.viewer, "username", "") or "").strip()

    @property
    def user(self) -> Any:
        """Return the viewer object, or ``None`` when only a name was given."""
        return None if isinstance(self.viewer, str) else self.viewer

    @property
    def is_admin(self) -> bool:
        """
        Tell whether the viewer is an installation administrator.

        Reuses the existing user model (``CodxUser.role == "admin"``), the same
        check as ``require_admin`` and ``get_user_project_access``; no new role
        concept is introduced here.
        """
        user = self.user
        return bool(user and getattr(user, "role", None) == USER_ROLE_ADMIN)

    @property
    def project_id(self) -> Optional[str]:
        """Return the project the caller is working in, when known."""
        return getattr(self.settings, "project_id", None)

    # -- calendar catalogue ------------------------------------------------

    def _load_calendars(self) -> List[Calendar]:
        """Return every stored calendar, cached for the life of the request."""
        if self._catalog is None:
            self._catalog = self.store.list_calendars()
        return self._catalog

    def _invalidate(self) -> None:
        """Drop the cached catalogue after a write."""
        self._catalog = None

    def _calendars_by_id(self) -> Dict[str, Calendar]:
        """Index the stored calendars by id."""
        return {calendar.id: calendar for calendar in self._load_calendars()}

    # -- ACL (§11.1) -------------------------------------------------------

    def _is_audience_member(self, calendar: Calendar) -> bool:
        """
        Tell whether the viewer is listed in the calendar's audience.

        A ``team`` calendar stores ``{kind: "team", project, name, members[]}``
        and the members are usernames, so membership is resolved from that list
        and from the explicit ``members`` roles — no extra lookup, and the same
        answer for the API, an AI tool and a test.
        """
        username = self.username
        if not username:
            return False
        if calendar.owner and calendar.owner == username:
            return True
        if calendar.member_role(username):
            return True
        audience = calendar.audience or {}
        if str(audience.get("kind") or "") != "team":
            return False
        return username in _dedupe(audience.get("members"))

    def can_view(self, calendar: Optional[Calendar]) -> bool:
        """
        Tell whether the viewer may know the calendar exists.

        ``private`` → only its owner; ``team`` → its audience members;
        ``workspace`` / ``system`` / ``external`` → every authenticated user.
        A calendar the viewer cannot view is reported as *not found* (§11.1).

        Args:
            calendar: Calendar to test.

        Returns:
            ``True`` when the calendar may be listed, read and counted.
        """
        if calendar is None or not self.username:
            return False
        if calendar.scope == "private":
            return calendar.owner == self.username
        if calendar.scope == "team":
            return self._is_audience_member(calendar)
        return True

    def can_write(self, calendar: Optional[Calendar]) -> bool:
        """
        Tell whether the viewer may create, change or delete events in it.

        ``system`` / ``external`` calendars are read-only for everybody, admins
        included. Otherwise the member role decides (``owner`` / ``writer``); a
        ``workspace`` calendar additionally accepts an installation admin.
        A ``reader`` — or a user who is not a member — never writes.

        Args:
            calendar: Calendar to test.

        Returns:
            ``True`` when the viewer may write its events.
        """
        if calendar is None or not self.can_view(calendar):
            return False
        if calendar.is_read_only:
            return False
        if calendar.member_role(self.username) in WRITE_ROLES:
            return True
        return bool(self.is_admin and calendar.scope == "workspace")

    def can_share(self, calendar: Optional[Calendar]) -> bool:
        """
        Tell whether the viewer may change the sharing of a calendar.

        Only the owner (or an installation admin) may change ``scope``,
        ``audience`` or ``members`` (§11.1).

        Args:
            calendar: Calendar to test.

        Returns:
            ``True`` when the viewer may share it.
        """
        if calendar is None or not self.can_view(calendar):
            return False
        return bool(calendar.owner == self.username or self.is_admin)

    def visible_calendars(self) -> List[Calendar]:
        """
        Return the calendars the viewer may see, in sidebar order.

        Returns:
            The visible calendars (possibly empty).
        """
        return [
            calendar
            for calendar in self._load_calendars()
            if self.can_view(calendar)
        ]

    # -- per-user visibility (prefs) --------------------------------------

    def _has_stored_prefs(self) -> bool:
        """Tell whether the viewer already saved a preferences document."""
        if not self.username:
            return False
        try:
            return os.path.isfile(self.store.prefs_path(self.username))
        except (ValueError, OSError):
            return False

    def hidden_calendar_ids(self) -> List[str]:
        """
        Return the ids of the calendars hidden in the viewer's UI.

        A calendar stored with ``visible: false`` is hidden **until the user
        saves their preferences**: from then on the explicit
        ``hidden_calendars`` list is the single truth, so a calendar the user
        enabled can never be hidden again by its stored default.

        Returns:
            The hidden calendar ids.
        """
        hidden = _dedupe(self.get_prefs().get("hidden_calendars"))
        if not hidden and not self._has_stored_prefs():
            hidden = [
                calendar.id for calendar in self._load_calendars() if not calendar.visible
            ]
        return hidden

    # -- payloads ----------------------------------------------------------

    def calendar_payload(self, calendar: Calendar) -> Dict[str, Any]:
        """
        Serialise a calendar for the API, with the viewer's rights resolved.

        ``read_only`` reflects the effective rule (scope *or* flag) and
        ``can_write``/``role`` save the client from re-implementing the ACL.

        Args:
            calendar: Calendar to serialise.

        Returns:
            The calendar document plus ``read_only``, ``can_write`` and
            ``role``.
        """
        payload = calendar.model_dump()
        hidden = self.hidden_calendar_ids()
        payload["read_only"] = calendar.is_read_only
        payload["can_write"] = self.can_write(calendar)
        payload["can_share"] = self.can_share(calendar)
        payload["role"] = calendar.member_role(self.username) or ""
        payload["visible"] = calendar.id not in hidden
        return payload

    def share_payload(self, calendar: Calendar) -> Dict[str, Any]:
        """
        Return what the sharing panel needs (§5.1).

        Args:
            calendar: Calendar whose sharing is described.

        Returns:
            ``{id, name, scope, owner, audience, members, can_write,
            read_only}``; the team members are resolved from the audience.
        """
        audience = calendar.audience or {}
        members = [
            {"user": member.user, "role": member.role}
            for member in calendar.members
            if member.user
        ]
        audience_members = _dedupe(audience.get("members"))
        known = {member["user"] for member in members}
        for username in audience_members:
            if username not in known:
                members.append({"user": username, "role": "reader"})
        return {
            "id": calendar.id,
            "name": calendar.name,
            "scope": calendar.scope,
            "owner": calendar.owner,
            "audience": calendar.audience,
            "members": members,
            "can_write": self.can_write(calendar),
            "can_share": self.can_share(calendar),
            "read_only": calendar.is_read_only,
        }

    def _timezone(self) -> str:
        """
        Return the timezone used to describe the queried range.

        Resolution order: the viewer's preference, the project settings (when
        they ever carry one) and the first visible calendar's timezone.
        """
        preferred = self.get_prefs().get("timezone") or getattr(
            self.settings, "timezone", None
        )
        if preferred:
            return str(preferred)
        for calendar in self.visible_calendars():
            if calendar.timezone:
                return calendar.timezone
        return DEFAULT_TIMEZONE

    # -- calendars: read ---------------------------------------------------

    def get_calendar(self, calendar_id: str) -> Calendar:
        """
        Return one calendar the viewer may see.

        Args:
            calendar_id: Calendar id.

        Returns:
            The calendar.

        Raises:
            CalendarError: 404 when the calendar does not exist **or** the
                viewer may not know it exists (§11.1: the two answers are
                deliberately identical).
        """
        calendar = self._calendars_by_id().get(str(calendar_id or "").strip())
        if calendar is None or not self.can_view(calendar):
            raise CalendarError(
                ERROR_NOT_FOUND, f"calendar '{calendar_id}' not found", 404
            )
        return calendar

    def orphan_events(self) -> List[Dict[str, Any]]:
        """
        List the events that point to a calendar which no longer exists.

        Invariant 1 of §3.2 says a dangling id is *reported for repair* by
        ``GET /calendars?orphans=1``, never silently dropped.

        Returns:
            ``[{event_id, title, calendar_ids}]`` for every event with at least
            one unknown membership.
        """
        known = set(self._calendars_by_id())
        orphans: List[Dict[str, Any]] = []
        for event in self._iter_events():
            if not self.can_view_event(event):
                continue
            dangling = [cid for cid in event.calendar_ids if cid not in known]
            if dangling:
                orphans.append(
                    {
                        "event_id": event.id,
                        "title": event.title,
                        "calendar_ids": dangling,
                    }
                )
        return orphans

    # -- calendars: write --------------------------------------------------

    def _require_owner(self, calendar: Calendar) -> None:
        """Raise 403 unless the viewer may change the calendar itself."""
        if self.can_share(calendar):
            return
        raise CalendarError(
            ERROR_READ_ONLY_CALENDAR,
            f"only the owner of '{calendar.name}' or an administrator may change it",
            403,
        )

    def create_calendar(self, payload: CalendarCreate) -> Calendar:
        """
        Create a calendar owned by the viewer (§5.1).

        Args:
            payload: Calendar to create.

        Returns:
            The persisted calendar.

        Raises:
            CalendarError: 403 for a ``workspace`` calendar requested by a
                non-admin, 409 for a ``system``/``external`` scope (they are
                materialised, never created by hand) or for an incomplete team
                audience.
        """
        if not self.username:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR, "authentication required", 403
            )
        if payload.scope in READ_ONLY_SCOPES:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR,
                f"'{payload.scope}' calendars are materialised, not created",
                409,
            )
        if payload.scope == "workspace" and not self.is_admin:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR,
                "only an installation administrator can create a workspace calendar",
                403,
            )

        audience = dict(payload.audience or {})
        if payload.scope == "workspace":
            audience["kind"] = "workspace"
        elif payload.scope == "team":
            audience["kind"] = "team"
            if not str(audience.get("name") or "").strip():
                raise CalendarError(
                    ERROR_INVALID_EVENT,
                    "a team calendar needs audience.name (the team name)",
                    400,
                )
            audience["members"] = _dedupe(
                audience.get("members") or [self.username]
            )
        else:
            audience = None

        calendar = Calendar(
            name=payload.name,
            color=payload.color,
            description=payload.description,
            scope=payload.scope,
            owner=self.username,
            audience=audience,
            timezone=payload.timezone or DEFAULT_TIMEZONE,
            default_reminders=payload.default_reminders or [],
            members=[CalendarMember(user=self.username, role="owner")],
            visible=payload.visible,
            order=payload.order,
        )
        saved = self.store.save_calendar(calendar)
        self._invalidate()
        return saved

    def update_calendar(self, calendar_id: str, payload: CalendarUpdate) -> Calendar:
        """
        Partially update a calendar the viewer owns (or administers).

        Args:
            calendar_id: Calendar id.
            payload: Only the fields that were sent are applied.

        Returns:
            The persisted calendar.

        Raises:
            CalendarError: 404 unknown/invisible calendar, 403 not the owner,
                409 on a read-only calendar or when escalating the scope to
                ``workspace`` without being an admin.
        """
        calendar = self.get_calendar(calendar_id)
        self._require_owner(calendar)
        if calendar.is_read_only:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR,
                f"'{calendar.name}' is read-only",
                409,
            )

        changes = payload.model_dump(exclude_unset=True)
        new_scope = changes.get("scope")
        if new_scope and new_scope != calendar.scope:
            if new_scope in READ_ONLY_SCOPES:
                raise CalendarError(
                    ERROR_READ_ONLY_CALENDAR,
                    f"'{new_scope}' calendars are materialised, not created",
                    409,
                )
            if new_scope == "workspace" and not self.is_admin:
                raise CalendarError(
                    ERROR_READ_ONLY_CALENDAR,
                    "only an installation administrator can create a workspace calendar",
                    403,
                )

        try:
            updated = Calendar(**{**calendar.model_dump(), **changes})
        except ValidationError as ex:
            raise CalendarError(ERROR_INVALID_EVENT, str(ex), 400) from ex
        saved = self.store.save_calendar(updated)
        self._invalidate()
        return saved

    def delete_calendar(
        self, calendar_id: str, move_to: Optional[str] = None
    ) -> Calendar:
        """
        Delete a calendar, refusing to orphan its events (§5.1).

        Args:
            calendar_id: Calendar id.
            move_to: Calendar that inherits the events of the deleted one.

        Returns:
            The deleted calendar document.

        Raises:
            CalendarError: 404 unknown/invisible calendar, 403 not the owner,
                409 when it is read-only or still holds events and ``move_to``
                was not given, 404 when ``move_to`` does not resolve.
        """
        calendar = self.get_calendar(calendar_id)
        self._require_owner(calendar)
        if calendar.is_read_only:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR, f"'{calendar.name}' is read-only", 409
            )

        references = [
            event for event in self._iter_events() if calendar.id in event.calendar_ids
        ]
        target: Optional[Calendar] = None
        if references and not move_to:
            raise CalendarError(
                ERROR_CALENDAR_IN_USE,
                f"'{calendar.name}' still holds {len(references)} event(s); "
                "pass move_to to move them",
                409,
            )
        if move_to:
            target = self.get_calendar(move_to)
            if target.id == calendar.id:
                raise CalendarError(
                    ERROR_INVALID_EVENT, "move_to must be a different calendar", 400
                )
            if not self.can_write(target):
                raise CalendarError(
                    ERROR_READ_ONLY_CALENDAR,
                    f"'{target.name}' is not writable for '{self.username}'",
                    403,
                )
            for event in references:
                event.calendar_ids = _dedupe(
                    target.id if cid == calendar.id else cid
                    for cid in event.calendar_ids
                )
                event.revision += 1
                self.store.save_event(event)

        self.store.delete_calendar(calendar.id)
        self._invalidate()
        return calendar

    # -- projects (the filter bar) ----------------------------------------

    def projects(self) -> List[Dict[str, Any]]:
        """
        Return the projects the viewer may put events in (§5.1).

        Reuses the existing project discovery (``find_all_user_projects`` for
        everybody else, ``find_all_projects`` for an administrator), so the
        filter never offers a project the caller cannot open.

        Returns:
            ``[{project_id, name, role}]`` sorted by name.
        """
        entries: List[Dict[str, Any]] = []
        try:
            from codx.junior.project.project_discover import (
                find_all_projects,
                find_all_user_projects,
            )
        except Exception as ex:  # pragma: no cover - discovery must never break
            logger.warning("calendar: project discovery unavailable: %s", ex)
            return entries

        try:
            if self.is_admin:
                settings_list = list((find_all_projects() or {}).values())
                role_for = lambda _settings: USER_ROLE_ADMIN  # noqa: E731
            elif self.user is not None:
                settings_list = list(find_all_user_projects(self.user))
                role_for = lambda settings: (  # noqa: E731
                    getattr(settings, "permissions", "") or USER_ROLE_USER
                )
            else:
                settings_list = []
                role_for = lambda _settings: USER_ROLE_USER  # noqa: E731
        except Exception as ex:  # noqa: BLE001 - never break the filter bar
            logger.error("calendar: project discovery failed: %s", ex)
            return entries

        seen: List[str] = []
        for settings in settings_list:
            project_id = getattr(settings, "project_id", None)
            if not project_id or project_id in seen:
                continue
            seen.append(project_id)
            entries.append(
                {
                    "project_id": project_id,
                    "name": getattr(settings, "project_name", None) or project_id,
                    "role": role_for(settings),
                }
            )
        entries.sort(key=lambda entry: str(entry["name"]).lower())
        return entries

    # -- preferences -------------------------------------------------------

    def get_prefs(self) -> Dict[str, Any]:
        """
        Return the caller's preferences (hidden calendars, order, week start).

        A missing or unreadable document degrades to the defaults instead of
        failing the request: preferences are a convenience, not data.

        Returns:
            The preferences document, completed with the defaults.
        """
        prefs: Dict[str, Any] = {
            "hidden_calendars": [],
            "order": {},
            "week_start": DEFAULT_WEEK_START,
        }
        if not self.username:
            return prefs
        try:
            stored = self.store.get_prefs(self.username)
        except (ValueError, OSError) as ex:
            logger.warning("calendar: cannot read prefs of '%s': %s", self.username, ex)
            return prefs
        if isinstance(stored, dict):
            prefs.update(stored)
        return prefs

    def save_prefs(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Persist the caller's preferences.

        Args:
            payload: The (partial) preferences document; unknown keys are kept
                as they are so a later milestone can extend the file.

        Returns:
            The stored document.

        Raises:
            CalendarError: 400 when a known key has an invalid value.
        """
        if not self.username:
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR, "authentication required", 403
            )
        prefs = {**self.get_prefs(), **dict(payload or {})}
        if not isinstance(prefs.get("hidden_calendars"), (list, tuple)):
            raise CalendarError(
                ERROR_INVALID_EVENT, "hidden_calendars must be a list", 400
            )
        prefs["hidden_calendars"] = _dedupe(prefs.get("hidden_calendars"))
        if not isinstance(prefs.get("order"), dict):
            raise CalendarError(ERROR_INVALID_EVENT, "order must be an object", 400)
        try:
            prefs["order"] = {
                str(key): int(value) for key, value in prefs["order"].items()
            }
        except (TypeError, ValueError) as ex:
            raise CalendarError(
                ERROR_INVALID_EVENT, "order values must be integers", 400
            ) from ex
        week_start = str(prefs.get("week_start") or DEFAULT_WEEK_START).lower()
        if week_start not in WEEK_STARTS:
            raise CalendarError(
                ERROR_INVALID_EVENT,
                f"week_start must be one of {', '.join(WEEK_STARTS)}",
                400,
            )
        prefs["week_start"] = week_start
        return self.store.save_prefs(self.username, prefs)

    # -- events: helpers ---------------------------------------------------

    def _iter_events(self) -> Iterator[CalendarEvent]:
        """Yield every stored event (all shards)."""
        for month in self.store.list_event_months():
            for event in self.store.list_events_month(month):
                yield event

    def _visible_memberships(self, event: CalendarEvent) -> List[str]:
        """Return the calendar ids of *event* the viewer may see."""
        by_id = self._calendars_by_id()
        return [
            calendar_id
            for calendar_id in event.calendar_ids
            if calendar_id in by_id and self.can_view(by_id[calendar_id])
        ]

    def can_view_event(self, event: Optional[CalendarEvent]) -> bool:
        """Tell whether the viewer sees at least one membership of *event*."""
        if event is None:
            return False
        return bool(self._visible_memberships(event))

    def _writable_memberships(self, calendar_ids: Iterable[str]) -> List[str]:
        """Return the calendar ids of *calendar_ids* the viewer may write."""
        by_id = self._calendars_by_id()
        return [
            calendar_id
            for calendar_id in _dedupe(calendar_ids)
            if calendar_id in by_id and self.can_write(by_id[calendar_id])
        ]

    @staticmethod
    def matches_projects(
        project_id: Optional[str], projects: Optional[Iterable[str]]
    ) -> bool:
        """
        Apply the ``projects[]`` filter to one item (§5.2).

        ``absent`` or ``[]`` → no filtering; ``"personal"`` matches a ``None``
        project; several tokens are **OR**-ed. Never AND.

        Args:
            project_id: Project of the item (``None`` = personal).
            projects: The filter tokens.

        Returns:
            ``True`` when the item belongs to the selection.

        Example:
            >>> CalendarService.matches_projects(None, ["personal"])
            True
            >>> CalendarService.matches_projects("acme-web", ["personal"])
            False
        """
        tokens = _dedupe(projects)
        if not tokens:
            return True
        for token in tokens:
            if token == PERSONAL_TOKEN:
                if not project_id:
                    return True
            elif project_id and project_id == token:
                return True
        return False

    def _is_masked(self, event: CalendarEvent, calendar_ids: List[str]) -> bool:
        """
        Tell whether the details of *event* must be hidden from the viewer.

        An event with ``visibility: private`` keeps its slot but masks its
        title, description, location and guests for anybody who is neither its
        creator nor a writer of one of its calendars (§11.1).
        """
        if event.visibility != "private":
            return False
        if event.created_by and event.created_by == self.username:
            return False
        by_id = self._calendars_by_id()
        return not any(
            by_id[calendar_id].member_role(self.username) in WRITE_ROLES
            for calendar_id in calendar_ids
            if calendar_id in by_id
        )

    def _occurrence(
        self, event: CalendarEvent, calendar_ids: List[str]
    ) -> Occurrence:
        """
        Build the single occurrence of *event* inside the visible memberships.

        M0 does not expand recurrences: a recurring event yields exactly one
        occurrence at its stored start (M3 expands the rule).
        """
        hidden = set(self.hidden_calendar_ids())
        primary = next(
            (cid for cid in calendar_ids if cid not in hidden), calendar_ids[0]
        )
        masked = self._is_masked(event, calendar_ids)
        return Occurrence(
            event_id=event.id,
            calendar_ids=calendar_ids,
            project_id=event.project_id,
            title="Private" if masked else event.title,
            start=event.start,
            end=event.end or event.start,
            all_day=event.all_day,
            source=event.source,
            refs={} if masked else dict(event.refs or {}),
            calendar_id=primary,
            is_recurring_instance=False,
            original_start=None,
            overdue=False,
            read_only=not self._writable_memberships(calendar_ids),
        )

    # -- events: query -----------------------------------------------------

    def query(self, query: EventQuery) -> Dict[str, Any]:
        """
        Answer the main read of the agenda (§5.2).

        The pipeline is fixed: **visibility** (ACL) → **calendars[]** →
        **hidden** calendars → **projects[]** → **sources[]** → **q** →
        occurrences. The project filter is applied after visibility so a token
        can never widen what the viewer sees.

        Args:
            query: The validated request payload.

        Returns:
            ``{range, calendars, projects, occurrences, counts, warnings,
            truncated_projects}``.

        Raises:
            CalendarError: 400 ``invalid_range`` when the window is reversed or
                wider than :data:`MAX_QUERY_DAYS` (§12 guard rail).
        """
        start_dt = parse_iso(query.from_)
        end_dt = parse_iso(query.to)
        if start_dt is None or end_dt is None:
            raise CalendarError(
                ERROR_INVALID_RANGE,
                f"invalid range: from={query.from_!r} to={query.to!r}",
                400,
            )
        if end_dt <= start_dt:
            raise CalendarError(
                ERROR_INVALID_RANGE, "the range end must be after its start", 400
            )
        if end_dt - start_dt > timedelta(days=MAX_QUERY_DAYS):
            raise CalendarError(
                ERROR_INVALID_RANGE,
                f"the range is {(end_dt - start_dt).days} days wide, "
                f"the maximum is {MAX_QUERY_DAYS}",
                400,
            )

        warnings: List[str] = []
        calendars = self.visible_calendars()
        by_id = {calendar.id: calendar for calendar in calendars}
        hidden = set(self.hidden_calendar_ids())

        requested = _dedupe(query.calendar_ids)
        for calendar_id in requested:
            if calendar_id not in by_id:
                warnings.append(
                    f"{ERROR_UNKNOWN_CALENDAR}: '{calendar_id}' is not a visible calendar"
                )
        selected = [calendar_id for calendar_id in requested if calendar_id in by_id]
        if requested and not selected:
            calendars_in_response = calendars
        else:
            calendars_in_response = [
                calendar
                for calendar in calendars
                if not selected or calendar.id in selected
            ]

        sources = set(_dedupe(query.sources))
        needle = (query.q or "").strip().lower()

        occurrences: List[Occurrence] = []
        for event in self.store.list_events_range(query.from_, query.to):
            memberships = self._visible_memberships(event)
            if selected:
                memberships = [cid for cid in memberships if cid in selected]
            if not memberships:
                continue
            if not query.include_hidden and all(cid in hidden for cid in memberships):
                continue
            if sources and event.source not in sources:
                continue
            if not self.matches_projects(event.project_id, query.projects):
                continue
            if needle:
                haystack = " ".join(
                    [event.title or "", event.description or "", event.location or ""]
                ).lower()
                if needle not in haystack:
                    continue
            occurrences.append(self._occurrence(event, memberships))

        occurrences.sort(key=lambda item: (item.start or "", item.title or ""))

        by_calendar: Counter = Counter(item.calendar_id for item in occurrences)
        by_source: Counter = Counter(item.source for item in occurrences)
        by_project: Counter = Counter(
            item.project_id or PERSONAL_TOKEN for item in occurrences
        )

        return {
            "range": {
                "from": to_utc_iso(start_dt),
                "to": to_utc_iso(end_dt),
                "timezone": self._timezone(),
            },
            "calendars": [
                self.calendar_payload(calendar) for calendar in calendars_in_response
            ],
            "projects": sorted(by_project.keys()),
            "occurrences": [occurrence.model_dump() for occurrence in occurrences],
            "counts": {
                "by_calendar": dict(by_calendar),
                "by_source": dict(by_source),
                "by_project": dict(by_project),
            },
            "warnings": warnings,
            # The projection of the 'codx-junior' calendar is M1: no project is
            # read outside the global store yet, so nothing is ever truncated.
            "truncated_projects": [],
        }

    # -- events: read ------------------------------------------------------

    def event_payload(self, event: CalendarEvent) -> Dict[str, Any]:
        """
        Serialise an event for the API, masking what the viewer may not read.

        The memberships are reduced to the calendars the viewer may see, the
        details of a ``visibility: private`` event of somebody else are masked
        (the slot stays visible so overlapping meetings keep their place, §11.1)
        and ``can_write`` is resolved here instead of in the client.

        Args:
            event: Event to serialise.

        Returns:
            The event document plus ``can_write``.
        """
        memberships = self._visible_memberships(event) or list(event.calendar_ids)
        payload = event.model_dump()
        payload["calendar_ids"] = memberships
        if self._is_masked(event, memberships):
            payload.update(
                {"title": "Private", "description": "", "location": "", "guests": []}
            )
        payload["can_write"] = bool(self._writable_memberships(memberships))
        return payload

    def get_event(self, event_id: str) -> CalendarEvent:
        """
        Return the raw event document (the editor needs the rule, not the
        instances).

        Args:
            event_id: Event id.

        Returns:
            The stored event.

        Raises:
            CalendarError: 404 when the event does not exist or none of its
                calendars is visible to the viewer.
        """
        event = self.store.get_event(str(event_id or "").strip())
        if not self.can_view_event(event):
            raise CalendarError(ERROR_NOT_FOUND, f"event '{event_id}' not found", 404)
        return event

    # -- events: validation ------------------------------------------------

    def _resolve_calendars(self, calendar_ids: Iterable[str]) -> List[Calendar]:
        """
        Resolve every calendar id of a write, rejecting unknown ones.

        Args:
            calendar_ids: Calendar ids the event must belong to.

        Returns:
            The resolved calendars.

        Raises:
            CalendarError: 404 ``unknown_calendar`` for the first unresolvable
                id (an id the viewer cannot see is unresolvable, §11.1).
        """
        by_id = self._calendars_by_id()
        resolved: List[Calendar] = []
        for calendar_id in _dedupe(calendar_ids):
            calendar = by_id.get(calendar_id)
            if calendar is None or not self.can_view(calendar):
                raise CalendarError(
                    ERROR_UNKNOWN_CALENDAR,
                    f"'{calendar_id}' does not resolve to a calendar",
                    404,
                )
            resolved.append(calendar)
        return resolved

    def _require_writable(self, calendars: List[Calendar]) -> None:
        """
        Raise unless the viewer may write the event in at least one calendar.

        A write whose only calendars are ``system``/``external`` is refused with
        409 ``read_only_calendar`` (§3.2 invariant 2: projected content cannot be
        edited); a missing role is refused with 403 ``read_only_calendar``
        (§11.1: a ``reader`` cannot write).
        """
        if any(self.can_write(calendar) for calendar in calendars):
            return
        names = ", ".join(calendar.name for calendar in calendars) or "-"
        if all(calendar.is_read_only for calendar in calendars):
            raise CalendarError(
                ERROR_READ_ONLY_CALENDAR,
                f"'{names}' is read-only; projected items are moved in their source",
                409,
            )
        raise CalendarError(
            ERROR_READ_ONLY_CALENDAR,
            f"'{self.username}' cannot write '{names}'",
            403,
        )

    @staticmethod
    def _normalize_times(start: str, end: Optional[str], all_day: bool) -> tuple:
        """
        Validate the time slot of an event and return it as a UTC pair.

        Enforces invariant 3 of §3.2 (``end > start``, all-day events default to
        one day) and the ``MAX_EVENT_DURATION_DAYS`` limit of §4.4.

        Args:
            start: Start, ISO-8601.
            end: End, ISO-8601 (``None`` for an all-day event).
            all_day: Whether the event is all-day.

        Returns:
            ``(start_iso, end_iso)`` normalised to UTC.

        Raises:
            CalendarError: 400 ``invalid_event`` for a reversed slot or an
                unparsable date, 400 ``event_too_long`` beyond the limit.
        """
        start_dt = parse_iso(start)
        if start_dt is None:
            raise CalendarError(
                ERROR_INVALID_EVENT, f"invalid start: {start!r}", 400
            )
        end_dt = parse_iso(end)
        if end_dt is None or end_dt <= start_dt:
            if not all_day:
                raise CalendarError(
                    ERROR_INVALID_EVENT,
                    f"end ({end!r}) must be after start ({start!r})",
                    400,
                )
            end_dt = start_dt + timedelta(days=1)
        if end_dt - start_dt > timedelta(days=MAX_EVENT_DURATION_DAYS):
            raise CalendarError(
                ERROR_EVENT_TOO_LONG,
                f"an event may last at most {MAX_EVENT_DURATION_DAYS} days; "
                "model a longer period as a recurring event",
                400,
            )
        return to_utc_iso(start_dt), to_utc_iso(end_dt)

    @staticmethod
    def _validate_recurrence(recurrence: Any) -> None:
        """
        Reject a recurrence rule that is not an RFC 5545 ``RRULE``.

        M0 stores the rule verbatim (expansion is M3), so the only check is the
        structural one: a rule must declare ``FREQ`` — anything else would be
        silently unexpandable later.
        """
        if recurrence is None:
            return
        if isinstance(recurrence, dict):
            rule = str(recurrence.get("rrule") or "").strip()
        else:
            rule = str(getattr(recurrence, "rrule", "") or "").strip()
        if rule and "FREQ=" not in rule.upper():
            raise CalendarError(
                ERROR_INVALID_RECURRENCE,
                f"'{rule}' is not an RFC 5545 RRULE (missing FREQ)",
                400,
            )

    # -- events: write -----------------------------------------------------

    def create_event(self, payload: EventCreate) -> CalendarEvent:
        """
        Create an event (``calendar_ids[]`` required, §5.2).

        Args:
            payload: Event to create.

        Returns:
            The persisted event, ``revision`` 1.

        Raises:
            CalendarError: ``unknown_calendar`` (404), ``read_only_calendar``
                (403/409), ``invalid_event``, ``event_too_long`` or
                ``invalid_recurrence`` (400).
        """
        calendars = self._resolve_calendars(payload.calendar_ids)
        self._require_writable(calendars)
        start, end = self._normalize_times(payload.start, payload.end, payload.all_day)
        self._validate_recurrence(payload.recurrence)

        reminders = payload.reminders or []
        if not reminders:
            primary = next(
                (calendar for calendar in calendars if self.can_write(calendar)),
                calendars[0],
            )
            reminders = list(primary.default_reminders)

        event = CalendarEvent(
            calendar_ids=[calendar.id for calendar in calendars],
            project_id=payload.project_id,
            title=payload.title,
            description=payload.description,
            location=payload.location,
            start=start,
            end=end,
            all_day=payload.all_day,
            timezone=payload.timezone or DEFAULT_TIMEZONE,
            recurrence=payload.recurrence,
            reminders=reminders,
            status=payload.status,
            visibility=payload.visibility,
            source="native",
            refs=payload.refs or {},
            guests=payload.guests or [],
            created_by=self.username or None,
            revision=1,
        )
        try:
            saved = self.store.save_event(event)
        except ValidationError as ex:
            raise CalendarError(ERROR_INVALID_EVENT, str(ex), 400) from ex
        return saved

    def update_event(
        self,
        event_id: str,
        payload: EventUpdate,
        expected_revision: Optional[int] = None,
    ) -> CalendarEvent:
        """
        Apply a partial update, guarded by the optimistic lock (§5.3).

        Args:
            event_id: Event id.
            payload: The fields to change.
            expected_revision: The ``If-Match`` revision; falls back to
                ``payload.revision`` when the header is absent.

        Returns:
            The persisted event with its incremented ``revision``.

        Raises:
            CalendarError: 404 not found/invisible, 409 ``revision_mismatch``,
                ``unknown_calendar``, ``read_only_calendar``, ``invalid_event``,
                ``event_too_long`` or ``invalid_recurrence``.
        """
        current = self.get_event(event_id)
        changes = payload.model_dump(exclude_unset=True)
        expected = (
            expected_revision
            if expected_revision is not None
            else changes.pop("revision", None)
        )
        changes.pop("revision", None)
        if expected is not None and int(expected) != current.revision:
            raise CalendarError(
                ERROR_REVISION_MISMATCH,
                f"the event is at revision {current.revision}, "
                f"your copy is {expected}",
                409,
            )

        merged = {**current.model_dump(), **changes}

        calendars = self._resolve_calendars(merged.get("calendar_ids") or [])
        # The viewer must be able to write both the old and the new memberships:
        # an event can never be moved *out* of a calendar it cannot write.
        self._require_writable(calendars)
        self._require_writable(
            self._resolve_calendars(self._visible_memberships(current))
        )

        start, end = self._normalize_times(
            merged.get("start"), merged.get("end"), bool(merged.get("all_day"))
        )
        merged["start"], merged["end"] = start, end
        self._validate_recurrence(merged.get("recurrence"))

        merged["revision"] = current.revision + 1
        try:
            event = CalendarEvent(**merged)
        except ValidationError as ex:
            raise CalendarError(ERROR_INVALID_EVENT, str(ex), 400) from ex
        return self.store.save_event(event)

    def delete_event(self, event_id: str, scope: str = "all") -> CalendarEvent:
        """
        Delete an event.

        ``scope`` keeps the Google semantics of §5.2 (``single`` | ``following``
        | ``all``). M0 does not expand recurrences, so only ``all`` is
        meaningful: deleting one instance or a suffix of a series needs the
        recurrence engine (M3) and is refused instead of silently deleting the
        whole series.

        Args:
            event_id: Event id.
            scope: ``single`` | ``following`` | ``all``.

        Returns:
            The deleted event document.

        Raises:
            CalendarError: 404 not found/invisible, 403 not writable, 400 when a
                per-instance delete is requested on a recurring event.
        """
        event = self.get_event(event_id)
        scope = str(scope or "all").strip().lower()
        if scope not in ("single", "following", "all"):
            raise CalendarError(
                ERROR_INVALID_EVENT,
                f"scope must be single, following or all (got '{scope}')",
                400,
            )
        if scope != "all" and event.is_recurring:
            raise CalendarError(
                ERROR_INVALID_RECURRENCE,
                "deleting a single instance of a recurring event arrives with "
                "the recurrence engine (M3); use scope=all",
                400,
            )
        self._require_writable(
            self._resolve_calendars(self._visible_memberships(event))
        )
        self.store.delete_event(event.id)
        return event

    # -- reminders (M2 owns the delivery) ---------------------------------

    def snooze(
        self,
        event_id: str,
        minutes: int,
        occurrence_start: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate a snooze request and answer the new trigger times.

        Reminder *delivery* is milestone M2: the background ticker and the
        durable ``delivered.json`` key do not exist yet, so this method
        deliberately persists nothing and says so (``persisted: false``). It
        still enforces the contract of §5.2 — the event must exist, be visible
        and be writable, and ``minutes`` must be sane — so the client can be
        built (and tested) against the final shape.

        Args:
            event_id: Event id.
            minutes: Minutes to postpone the reminder.
            occurrence_start: Occurrence whose reminder is snoozed; defaults to
                the stored ``start``.

        Returns:
            ``{event_id, occurrence_start, snooze_until, reminders, persisted}``.

        Raises:
            CalendarError: 404 not found/invisible, 403 not writable, 400 for a
                non-positive or excessive delay.
        """
        event = self.get_event(event_id)
        try:
            delay = int(minutes)
        except (TypeError, ValueError) as ex:
            raise CalendarError(
                ERROR_INVALID_EVENT, "minutes must be an integer", 400
            ) from ex
        if delay <= 0 or delay > MAX_SNOOZE_MINUTES:
            raise CalendarError(
                ERROR_INVALID_EVENT,
                f"minutes must be between 1 and {MAX_SNOOZE_MINUTES}",
                400,
            )
        self._require_writable(
            self._resolve_calendars(self._visible_memberships(event))
        )

        base = parse_iso(occurrence_start) or event.start_datetime() or datetime.now(
            timezone.utc
        )
        until = datetime.now(timezone.utc) + timedelta(minutes=delay)
        return {
            "event_id": event.id,
            "occurrence_start": to_utc_iso(base),
            "snooze_until": to_utc_iso(until),
            "reminders": [
                {
                    "channel": reminder.channel,
                    "minutes_before": reminder.minutes_before,
                    "trigger_at": to_utc_iso(until),
                }
                for reminder in event.reminders
            ],
            "persisted": False,
        }

    # -- quick add ---------------------------------------------------------

    def quick_add(self, text: str) -> Dict[str, Any]:
        """
        Parse ``"Lunch Ana tom 13:00 1h @Café"`` into a draft (§5.2).

        The draft is **never persisted**: it is a proposal the editor confirms.
        Recognised tokens: ``HH:MM`` or ``9am`` (time), ``1h`` / ``30m``
        (duration), ``@place`` (location), ``YYYY-MM-DD`` / ``today`` /
        ``tomorrow`` (day) and ``all day``. Everything else is the title.

        Args:
            text: The raw sentence typed by the user.

        Returns:
            ``{text, draft, persisted, warnings}`` where ``draft`` is a ready
            ``POST /api/calendar/events`` payload.

        Raises:
            CalendarError: 400 when the text is empty.
        """
        raw = str(text or "").strip()
        if not raw:
            raise CalendarError(ERROR_INVALID_EVENT, "text is required", 400)
        remaining = raw
        warnings: List[str] = []

        location = ""
        match = _QUICK_LOCATION.search(remaining)
        if match:
            location = match.group(1)
            remaining = _QUICK_LOCATION.sub(" ", remaining)

        day: Optional[date] = None
        match = _QUICK_DATE.search(remaining)
        if match:
            try:
                day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                remaining = _QUICK_DATE.sub(" ", remaining)
            except ValueError:
                warnings.append(f"ignored the date '{match.group(1)}'")

        lowered = remaining.lower()
        today = datetime.now(timezone.utc).date()
        for token in _DAY_TOKENS:
            if token in lowered:
                day = today + timedelta(days=1 if token == "tomorrow" else 0)
                remaining = re.sub(
                    rf"\b{token}\b", " ", remaining, flags=re.IGNORECASE
                )
                lowered = remaining.lower()
                break

        all_day = "all day" in lowered or "allday" in lowered
        if all_day:
            remaining = re.sub(r"\ball\s*day\b", " ", remaining, flags=re.IGNORECASE)

        duration = DEFAULT_EVENT_MINUTES
        match = _QUICK_DURATION.search(remaining)
        if match:
            amount = int(match.group(1))
            unit = match.group(2).lower()
            duration = amount * 60 if unit.startswith("h") else amount
            remaining = _QUICK_DURATION.sub(" ", remaining)

        hour: Optional[int] = None
        minute = 0
        match = _QUICK_TIME.search(remaining)
        if match and int(match.group(1)) < 24 and int(match.group(2)) < 60:
            hour, minute = int(match.group(1)), int(match.group(2))
            remaining = _QUICK_TIME.sub(" ", remaining)
        else:
            match = _QUICK_MERIDIEM.search(remaining)
            if match:
                hour = int(match.group(1)) % 12
                if match.group(2).lower() == "pm":
                    hour += 12
                remaining = _QUICK_MERIDIEM.sub(" ", remaining)

        if hour is None:
            next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
            hour, minute = next_hour.hour, 0

        title = " ".join(remaining.split()) or "Untitled event"
        target_day = day or today
        start_dt = datetime(
            target_day.year,
            target_day.month,
            target_day.day,
            hour,
            minute,
            tzinfo=timezone.utc,
        )
        if all_day:
            start_dt = datetime(
                target_day.year, target_day.month, target_day.day, tzinfo=timezone.utc
            )
            end_dt = start_dt + timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(minutes=duration)

        calendars = [
            calendar
            for calendar in self.visible_calendars()
            if self.can_write(calendar)
        ]
        calendar_ids = [calendars[0].id] if calendars else []
        if not calendar_ids:
            warnings.append(
                f"{ERROR_READ_ONLY_CALENDAR}: the viewer cannot write any calendar"
            )

        return {
            "text": raw,
            "persisted": False,
            "warnings": warnings,
            "draft": {
                "calendar_ids": calendar_ids,
                "title": title,
                "description": "",
                "location": location,
                "start": to_utc_iso(start_dt),
                "end": to_utc_iso(end_dt),
                "all_day": all_day,
                "timezone": self._timezone(),
                "project_id": None,
                "reminders": list(calendars[0].default_reminders) if calendars else [],
                "guests": [],
            },
        }


# Made with ❤️ by codx-junior
