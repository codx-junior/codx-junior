"""
Crash-safe, **global** persistence of calendars and their events.

Storage layout (§4.1), one folder shared by the whole installation::

    <CODX_JUNIOR_CALENDAR_PATH>/
    ├── calendars.json                 # [Calendar] – small, one document
    ├── events/<YYYY-MM>.json          # [CalendarEvent] sharded by start month (UTC)
    ├── delivered.json                 # reminder dedupe, filled in M2
    └── prefs/<username>.json          # per-user hidden calendars, order, ...

Why global and not inside the project
-------------------------------------
``<codx_path>`` is *the project's* ``.codx``. Storing the agenda there would
mean the same "Team — Platform" calendar existing once per project and
diverging, a reminder firing N times, and a personal calendar disappearing when
the user switches project. The calendar therefore joins
``ANALYTICS_DATA_PATH``, ``API_LOGS_DATA_PATH`` and ``CODX_JUNIOR_COMPANIES_PATH``:
one global folder, outside every repository. Projects are a **filter** on the
events, never a namespace.

Conventions copied from :mod:`codx.junior.company.store`
-------------------------------------------------------
* **Root resolution**: env var → ``codx.junior.globals`` → default constant.
* **Atomic writes**: temp file in the same directory → ``flush`` → ``fsync`` →
  ``os.replace``. A crash never leaves half a document.
* **Cross-process exclusion**: ``flock`` (through
  :func:`codx.junior.utils.file_lock.interprocess_lock`, which also layers a
  thread lock) around every read-modify-write, because the API runs several
  uvicorn workers and the M2 reminder tick is another process.
* **Validated names**: anything that becomes a path component (a username, a
  month key) is validated first, so an id can never escape the root.
* **Tolerant reads**: a missing or corrupt file is logged and treated as empty;
  one broken shard must never blank the whole agenda (never a 500).

Month sharding and overlap (§4.4)
---------------------------------
An event lives in the shard of its **start** month and a range query reads the
requested months **plus one month either side**, keeping every query at a
handful of small file reads with no index to maintain. Events longer than
``MAX_EVENT_DURATION_DAYS`` are rejected by the service; past ~20k events the
documented escape hatch is an ``events/index.json`` (not part of M0).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from codx.junior.utils.file_lock import interprocess_lock

from .model import (
    Calendar,
    CalendarEvent,
    month_key,
    months_between,
    now_iso,
    new_id,
    parse_iso,
    shift_month,
)

logger = logging.getLogger(__name__)

#: Environment variable that overrides the calendar root (tests, containers).
CALENDAR_PATH_ENV = "CODX_JUNIOR_CALENDAR_PATH"

#: Default root, next to the other global codx-junior data folders.
DEFAULT_CALENDAR_PATH = "/home/codx-junior/calendar"

CALENDARS_FILE = "calendars.json"
EVENTS_DIR = "events"
DELIVERED_FILE = "delivered.json"
PREFS_DIR = "prefs"
LOCK_FILE = ".calendar.lock"

#: A range query always reads one month before and after the requested window:
#: a long event (holiday, offsite) may start before the window and overlap it.
SHARD_PADDING_MONTHS = 1

#: Longest an event may last, in days (§4.4). Longer periods must be recurring
#: events or day-by-day all-day blocks. Enforced by the service on write.
MAX_EVENT_DURATION_DAYS = 31

#: How long a writer waits for the cross-process lock.
LOCK_TIMEOUT_SECONDS = 10.0

_MONTH_KEY = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: Per-thread lock depth, so a nested ``with store.lock()`` does not deadlock
#: (``interprocess_lock`` uses a non re-entrant thread lock).
_LOCK_STATE = threading.local()


class CalendarStoreError(RuntimeError):
    """Raised when a calendar document cannot be written."""


def get_calendar_root() -> str:
    """
    Resolve the root folder of the calendar store.

    Resolution order: ``CODX_JUNIOR_CALENDAR_PATH``, the ``codx.junior.globals``
    constant when available, then :data:`DEFAULT_CALENDAR_PATH`.

    Returns:
        Absolute path of the calendar root.
    """
    configured = os.environ.get(CALENDAR_PATH_ENV)
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    try:
        from codx.junior.globals import CODX_JUNIOR_CALENDAR_PATH

        if CODX_JUNIOR_CALENDAR_PATH:
            return os.path.abspath(os.path.expanduser(CODX_JUNIOR_CALENDAR_PATH))
    except Exception:  # pragma: no cover - import guard
        pass
    return DEFAULT_CALENDAR_PATH


def _safe_segment(value: str, kind: str) -> str:
    """
    Validate a value used as a path component (username, month key).

    An id or a username containing a separator (``../..``) would escape the
    root folder, so it is rejected instead of being sanitised silently.

    Args:
        value: Raw value.
        kind: Label used in the error message.

    Returns:
        The trimmed value.

    Raises:
        ValueError: If the value is empty, ``.``/``..`` or contains a separator.
    """
    clean = str(value or "").strip()
    if not clean or clean in (".", "..") or "/" in clean or "\\" in clean:
        raise ValueError(f"invalid {kind}: {value!r}")
    return clean


def _atomic_write(path: str, content: str) -> None:
    """
    Write *content* to *path* atomically.

    A crash either leaves the previous file untouched or the complete new one,
    never a half-written JSON document.

    Args:
        path: Destination path (its folder is created when missing).
        content: Full file content.

    Raises:
        OSError: If the file cannot be written.
    """
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".calendar-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class CalendarStore:
    """
    Read/write the calendars, their events, the user preferences and the
    reminder delivery log.

    The store is deliberately dumb: it persists and validates documents and it
    never decides who may see what (that is the per-request ACL of the service)
    and never expands a recurrence (M3).

    Args:
        root_path: Calendar root. Defaults to :func:`get_calendar_root`.

    Example:
        >>> store = CalendarStore("/tmp/calendar")           # doctest: +SKIP
        >>> store.save_calendar(Calendar(name="Work"))       # doctest: +SKIP
    """

    def __init__(self, root_path: Optional[str] = None):
        self.root_path = os.path.abspath(
            os.path.expanduser(root_path or get_calendar_root())
        )

    # -- paths -------------------------------------------------------------

    def calendars_path(self) -> str:
        """Return the path of the ``calendars.json`` catalogue."""
        return os.path.join(self.root_path, CALENDARS_FILE)

    def events_dir(self) -> str:
        """Return the folder that holds the month shards."""
        return os.path.join(self.root_path, EVENTS_DIR)

    def event_shard_path(self, month: str) -> str:
        """
        Return the path of the shard of *month*.

        Args:
            month: ``"YYYY-MM"`` key.

        Returns:
            Absolute shard path.

        Raises:
            ValueError: When *month* is not a ``YYYY-MM`` key.
        """
        key = _safe_segment(month, "month key")
        if not _MONTH_KEY.match(key):
            raise ValueError(f"invalid month key: {month!r}")
        return os.path.join(self.events_dir(), f"{key}.json")

    def delivered_path(self) -> str:
        """Return the path of the reminder delivery log (§7.3)."""
        return os.path.join(self.root_path, DELIVERED_FILE)

    def prefs_path(self, username: str) -> str:
        """
        Return the preferences file of *username*.

        Args:
            username: Owner of the preferences.

        Returns:
            Absolute path.

        Raises:
            ValueError: When the username is not usable as a file name.
        """
        return os.path.join(
            self.root_path, PREFS_DIR, f"{_safe_segment(username, 'username')}.json"
        )

    def lock_path(self) -> str:
        """Return the path of the cross-process lock file."""
        return os.path.join(self.root_path, LOCK_FILE)

    # -- tolerant json helpers --------------------------------------------

    def _read_json(self, path: str, default: Any) -> Any:
        """
        Read a JSON file, degrading to *default* instead of raising.

        Args:
            path: File to read.
            default: Value returned when the file is missing or unreadable.

        Returns:
            The parsed content, or *default*.
        """
        if not os.path.isfile(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as ex:
            logger.warning("Calendar store could not read '%s': %s", path, ex)
            return default

    def _write_json(self, path: str, payload: Any) -> None:
        """
        Serialise *payload* and write it atomically.

        Raises:
            CalendarStoreError: When the file cannot be written.
        """
        try:
            _atomic_write(
                path,
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            )
        except OSError as ex:
            raise CalendarStoreError(f"Could not write '{path}': {ex}") from ex

    # -- locking -----------------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """
        Serialize a read-modify-write of the whole calendar root.

        Uses ``flock`` so two processes (API + the M2 reminder tick) cannot
        interleave an update, and is *re-entrant per thread* so a caller may
        wrap several store calls in one transaction.

        Yields:
            Nothing; the lock is held while the context is active.
        """
        depths = getattr(_LOCK_STATE, "depths", None)
        if depths is None:
            depths = _LOCK_STATE.depths = {}
        key = self.root_path
        depth = depths.get(key, 0)
        if depth:
            depths[key] = depth + 1
            try:
                yield
            finally:
                depths[key] = depth
            return
        with interprocess_lock(self.lock_path(), timeout=LOCK_TIMEOUT_SECONDS):
            depths[key] = 1
            try:
                yield
            finally:
                depths[key] = 0

    # -- calendars ---------------------------------------------------------

    def list_calendars(self) -> List[Calendar]:
        """
        List every stored calendar, ordered by ``order`` then name.

        Unreadable entries are logged and skipped: one bad document must never
        hide the whole agenda.

        Returns:
            The calendars (possibly empty).
        """
        raw = self._read_json(self.calendars_path(), [])
        if not isinstance(raw, list):
            logger.warning("Calendar catalogue is not a list, ignoring it")
            return []
        calendars: List[Calendar] = []
        for item in raw:
            try:
                calendars.append(Calendar(**item))
            except Exception as ex:
                logger.warning("Skipping unreadable calendar document: %s", ex)
        calendars.sort(key=lambda calendar: (calendar.order, (calendar.name or "").lower()))
        return calendars

    def get_calendar(self, calendar_id: str) -> Optional[Calendar]:
        """
        Return one calendar by id.

        Args:
            calendar_id: Calendar id.

        Returns:
            The calendar, or ``None`` when it does not exist.
        """
        for calendar in self.list_calendars():
            if calendar.id == calendar_id:
                return calendar
        return None

    def save_calendar(self, calendar: Calendar) -> Calendar:
        """
        Persist a calendar, assigning its id and refreshing ``updated_at``.

        Args:
            calendar: Calendar to persist.

        Returns:
            The persisted calendar.

        Raises:
            CalendarStoreError: If the catalogue cannot be written.
        """
        with self.lock():
            if not calendar.id:
                calendar.id = new_id("cal")
            calendar.updated_at = now_iso()
            calendars = [
                item for item in self.list_calendars() if item.id != calendar.id
            ]
            calendars.append(calendar)
            calendars.sort(
                key=lambda item: (item.order, (item.name or "").lower())
            )
            self._write_json(
                self.calendars_path(),
                [item.model_dump() for item in calendars],
            )
        return calendar

    def delete_calendar(self, calendar_id: str) -> bool:
        """
        Remove a calendar from the catalogue.

        Its events are **not** touched: deciding whether they are moved,
        orphaned or refused is the service's job (``move_to`` / 409
        ``calendar_in_use``, §5.1).

        Args:
            calendar_id: Calendar id.

        Returns:
            True when the calendar existed and was removed.

        Raises:
            CalendarStoreError: If the catalogue cannot be written.
        """
        with self.lock():
            calendars = self.list_calendars()
            kept = [item for item in calendars if item.id != calendar_id]
            if len(kept) == len(calendars):
                return False
            self._write_json(
                self.calendars_path(),
                [item.model_dump() for item in kept],
            )
            return True

    # -- events ------------------------------------------------------------

    def list_event_months(self) -> List[str]:
        """
        List the ``YYYY-MM`` shards that exist on disk, oldest first.

        Returns:
            The month keys (empty when no event was ever written).
        """
        folder = self.events_dir()
        if not os.path.isdir(folder):
            return []
        months = [
            name[:-5]
            for name in os.listdir(folder)
            if name.endswith(".json") and _MONTH_KEY.match(name[:-5])
        ]
        months.sort()
        return months

    def list_events_month(self, month: str) -> List[CalendarEvent]:
        """
        Read every event of one month shard.

        A corrupt shard returns ``[]`` (and a warning) instead of raising: the
        calendar keeps rendering the other months.

        Args:
            month: ``"YYYY-MM"`` key.

        Returns:
            The events of that shard.

        Raises:
            ValueError: When *month* is not a ``YYYY-MM`` key.
        """
        path = self.event_shard_path(month)
        raw = self._read_json(path, [])
        if not isinstance(raw, list):
            logger.warning("Calendar shard '%s' is not a list, ignoring it", path)
            return []
        events: List[CalendarEvent] = []
        for item in raw:
            try:
                events.append(CalendarEvent(**item))
            except Exception as ex:
                logger.warning("Skipping unreadable event in '%s': %s", path, ex)
        return events

    def list_events_range(self, start: str, end: str) -> List[CalendarEvent]:
        """
        Read the events that overlap ``[start, end)``.

        The requested months **plus one month either side** are read, so an
        event that starts before the window and overlaps it is not missed
        (§4.4). Only overlapping events are returned.

        Args:
            start: Range start, ISO-8601 (inclusive).
            end: Range end, ISO-8601 (exclusive).

        Returns:
            Events sorted by start, then title. Duplicates are impossible in
            practice (an event lives in one shard) but are collapsed by id.

        Raises:
            ValueError: When *start* or *end* is not an ISO date-time.
        """
        from_dt = parse_iso(start)
        to_dt = parse_iso(end)
        if from_dt is None or to_dt is None:
            raise ValueError(
                f"invalid range: start={start!r} end={end!r}"
            )
        if to_dt <= from_dt:
            return []
        months = months_between(
            shift_month(month_key(start), -SHARD_PADDING_MONTHS),
            shift_month(month_key(end), SHARD_PADDING_MONTHS),
        )
        events: List[CalendarEvent] = []
        seen: List[str] = []
        for month in months:
            for event in self.list_events_month(month):
                if event.id and event.id in seen:
                    continue
                if not self._overlaps(event, from_dt, to_dt):
                    continue
                if event.id:
                    seen.append(event.id)
                events.append(event)
        events.sort(key=lambda event: (event.start or "", event.title or ""))
        return events

    @staticmethod
    def _overlaps(event: CalendarEvent, from_dt: Any, to_dt: Any) -> bool:
        """Tell whether *event* intersects the ``[from_dt, to_dt)`` window."""
        start_dt = event.start_datetime()
        if start_dt is None:
            return False
        end_dt = event.end_datetime() or start_dt
        return start_dt < to_dt and end_dt > from_dt

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        """
        Return one event by id, searching every shard.

        Args:
            event_id: Event id.

        Returns:
            The event, or ``None`` when no shard holds it.
        """
        if not event_id:
            return None
        for month in self.list_event_months():
            for event in self.list_events_month(month):
                if event.id == event_id:
                    return event
        return None

    def save_event(self, event: CalendarEvent) -> CalendarEvent:
        """
        Persist an event in the shard of its start month.

        The id and ``updated_at`` are filled/refreshed, and a copy left in
        another shard is removed, so changing ``start`` moves the event instead
        of duplicating it.

        Args:
            event: Event to persist.

        Returns:
            The persisted event.

        Raises:
            ValueError: When ``start`` is not an ISO date-time.
            CalendarStoreError: If the shard cannot be written.
        """
        with self.lock():
            if not event.id:
                event.id = new_id("evt")
            event.updated_at = now_iso()
            month = event.shard_month
            self._drop_event_from_shards(event.id, keep_month=month)
            events = [
                item
                for item in self.list_events_month(month)
                if item.id != event.id
            ]
            events.append(event)
            events.sort(key=lambda item: (item.start or "", item.title or ""))
            self._write_json(
                self.event_shard_path(month),
                [item.model_dump() for item in events],
            )
        return event

    def delete_event(self, event_id: str) -> bool:
        """
        Remove an event from whichever shard holds it.

        Args:
            event_id: Event id.

        Returns:
            True when at least one shard contained it.

        Raises:
            CalendarStoreError: If a shard cannot be written.
        """
        if not event_id:
            return False
        with self.lock():
            return self._drop_event_from_shards(event_id, keep_month=None)

    def _drop_event_from_shards(
        self, event_id: str, keep_month: Optional[str] = None
    ) -> bool:
        """
        Remove *event_id* from every shard except *keep_month*.

        The raw JSON is filtered (not the validated models) so a corrupt line
        is preserved for later repair instead of being dropped by a write.

        Args:
            event_id: Event id to remove.
            keep_month: Shard that must keep the event (``None`` = none).

        Returns:
            True when at least one shard was rewritten.
        """
        removed = False
        for month in self.list_event_months():
            if month == keep_month:
                continue
            path = self.event_shard_path(month)
            raw = self._read_json(path, [])
            if not isinstance(raw, list):
                continue
            kept = [
                item
                for item in raw
                if not (isinstance(item, dict) and item.get("id") == event_id)
            ]
            if len(kept) == len(raw):
                continue
            self._write_json(path, kept)
            removed = True
        return removed

    # -- preferences -------------------------------------------------------

    def get_prefs(self, username: str) -> Dict[str, Any]:
        """
        Read the UI preferences of *username* (hidden calendars, order, ...).

        Args:
            username: Owner of the preferences.

        Returns:
            The stored document, or ``{}`` when there is none.
        """
        raw = self._read_json(self.prefs_path(username), {})
        return raw if isinstance(raw, dict) else {}

    def save_prefs(self, username: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Persist the UI preferences of *username*.

        Args:
            username: Owner of the preferences.
            prefs: Document to store.

        Returns:
            The stored document.

        Raises:
            CalendarStoreError: If the file cannot be written.
        """
        payload = dict(prefs or {})
        with self.lock():
            self._write_json(self.prefs_path(username), payload)
        return payload

    # -- reminder delivery log (filled in M2) ------------------------------

    def read_delivered(self) -> Dict[str, str]:
        """
        Read the reminder delivery log.

        Keys are ``"<event_id>|<occurrence_start>|<minutes_before>"`` and values
        the delivery timestamp (§7.3). M2 writes it; M0 only provides the
        durable primitive so a restart cannot re-fire a reminder.

        Returns:
            The log, or ``{}`` when it does not exist yet.
        """
        raw = self._read_json(self.delivered_path(), {})
        return raw if isinstance(raw, dict) else {}

    def write_delivered(self, delivered: Dict[str, str]) -> Dict[str, str]:
        """
        Replace the reminder delivery log.

        Args:
            delivered: The full log to persist.

        Returns:
            The persisted log.

        Raises:
            CalendarStoreError: If the file cannot be written.
        """
        payload = dict(delivered or {})
        with self.lock():
            self._write_json(self.delivered_path(), payload)
        return payload

    # -- diagnostics -------------------------------------------------------

    def snapshot_paths(self) -> List[str]:
        """Return every document the store owns (diagnostics and tests)."""
        paths = [self.calendars_path(), self.delivered_path()]
        paths.extend(
            self.event_shard_path(month) for month in self.list_event_months()
        )
        return paths


# Made with ❤️ by codx-junior
