"""
Recurring task schedules.

``Chat.wake_at`` is a one-shot alarm clock: the autonomy worker delivers the
task once and the wake is consumed. A **recurrence** turns it into a series: the
task also carries ``Chat.schedule`` (canonical text such as
``"every friday at 12:00 @Europe/Madrid"``) and the runtime re-arms ``wake_at``
to the next occurrence every time a shift delivered the task. That is what makes
"every friday at noon review the week and write the report" keep running without
a human in the loop.

Why canonical *text* instead of a cron object: the rule is stored in the task
card (JSON), it must stay readable for the human reviewing the board, and it is
usually written by a model through a tool. Parsing is cheap and deterministic, so
the rule is parsed on demand instead of being kept as a second source of truth.

Supported forms (v1, case insensitive; an optional trailing ``@Zone`` or
`` in Zone`` sets the zone the clock time is interpreted in, UTC by default):

* ``every day at 12:00``, ``daily 09:00``, ``every day at noon``
* ``every friday at 12:00``, ``every fri noon``, ``weekly on monday 09:00``
* ``every weekday at 09:00``, ``every mon,thu 18:00``, ``every weekend 10:00``
* ``every 2 hours``, ``every 30m``, ``every 1h``

A day/week rule without a clock time defaults to :data:`DEFAULT_HOUR` (09:00).

The module only depends on the standard library so it can be imported from the
tool layer, the HTTP API and the autonomy worker alike.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone as dt_timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = dt_timezone.utc

#: Clock used when the rule names a day but no hour ("every friday").
DEFAULT_HOUR = 9

#: ``history`` keeps the task (and its conversation) between runs, so every run
#: sees what the previous ones did; ``clean`` starts every run in a brand new
#: card with no previous messages.
CONTEXT_HISTORY = "history"
CONTEXT_CLEAN = "clean"
CONTEXTS = (CONTEXT_HISTORY, CONTEXT_CLEAN)

#: Monday = 0, like :meth:`datetime.date.weekday`.
WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_WEEKDAY = (0, 1, 2, 3, 4)
_WEEKEND = (5, 6)

CLOCK_WORDS = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0)}

_UNIT_SECONDS = {
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

_PREFIX_RE = re.compile(r"^(?:every|each|all)\s+", re.I)
_ZONE_RE = re.compile(
    r"(?:\s+in\s+|\s*@\s*|\s*\[)\s*"
    r"([A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)+)\s*\]?\s*$",
    re.I,
)
_INTERVAL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([A-Za-z]+)?$")
_AT_CLOCK_RE = re.compile(
    r"(?:^|\s)(?:at\s+|@\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?=$|\s)",
    re.I,
)
_WORD_RE = re.compile(r"[a-z]+")


def zone_for(name: Optional[str]) -> dt_timezone:
    """
    Resolve an IANA zone name to a tzinfo, falling back to UTC.

    Args:
        name: IANA zone (``"Europe/Madrid"``) or an empty value.

    Returns:
        The matching zone, or UTC when the name is unknown/unavailable.
    """
    text = str(name or "").strip()
    if not text or text.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(text)
    except Exception as ex:  # pragma: no cover - tzdata may be missing
        logger.warning("recurrence: unknown timezone '%s': %s", text, ex)
        return UTC


@dataclass(frozen=True)
class Schedule:
    """
    A parsed recurrence rule.

    Attributes:
        kind: ``"interval"`` (every N minutes/hours), ``"daily"`` or
            ``"weekly"``.
        timezone_name: IANA zone the clock time is interpreted in.
        hour: Hour of the day (daily/weekly).
        minute: Minute of the hour (daily/weekly).
        weekdays: Days of the week the rule fires (0 = monday).
        interval_seconds: Period between two runs (interval rules).
    """

    kind: str
    timezone_name: str = "UTC"
    hour: int = DEFAULT_HOUR
    minute: int = 0
    weekdays: Tuple[int, ...] = ()
    interval_seconds: int = 0

    def zone(self):
        """Return the tzinfo the rule is written in."""
        return zone_for(self.timezone_name)

    def text(self) -> str:
        """
        Return the canonical, human readable form of the rule.

        Example:
            >>> Schedule(kind="weekly", hour=12, weekdays=(4,)).text()
            'every friday at 12:00'
        """
        if self.kind == "interval":
            core = f"every {_format_interval(self.interval_seconds)}"
        elif self.kind == "weekly":
            names = ", ".join(_DAY_NAMES[day] for day in self.weekdays) or "monday"
            core = f"every {names} at {self.hour:02d}:{self.minute:02d}"
        else:
            core = f"every day at {self.hour:02d}:{self.minute:02d}"
        if self.timezone_name and self.timezone_name.upper() != "UTC":
            return f"{core} @{self.timezone_name}"
        return core


def _format_interval(seconds: int) -> str:
    """Render a period in the most readable unit (``2h``, ``30m``, ``45s``)."""
    seconds = int(seconds or 0)
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _interval_seconds(amount: str, unit: Optional[str]) -> int:
    """Return the period in seconds of an ``<amount><unit>`` pair (0 = invalid)."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return 0
    name = str(unit or "h").strip().lower()
    seconds = _UNIT_SECONDS.get(name) or _UNIT_SECONDS.get(name.rstrip("s"))
    if not seconds or value <= 0:
        return 0
    return int(value * seconds)


def _extract_clock(body: str) -> Tuple[int, int, str]:
    """
    Split the clock time out of *body*.

    Args:
        body: The rule text without the ``every`` prefix.

    Returns:
        ``(hour, minute, remaining_text)``; the default hour when no clock is
        written.
    """
    for word, (hour, minute) in CLOCK_WORDS.items():
        match = re.search(rf"\b{word}\b", body, re.I)
        if match:
            rest = f"{body[: match.start()]} {body[match.end():]}".strip()
            return hour, minute, rest
    match = _AT_CLOCK_RE.search(body)
    if not match:
        return DEFAULT_HOUR, 0, body
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    rest = f"{body[: match.start()]} {body[match.end():]}".strip()
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEFAULT_HOUR, 0, rest
    return hour, minute, rest


def _extract_weekdays(body: str) -> List[int]:
    """Return the sorted weekdays named in *body* (0 = monday)."""
    found: List[int] = []
    for word in _WORD_RE.findall(body):
        if word in ("weekday", "weekdays"):
            candidates = list(_WEEKDAY)
        elif word in ("weekend", "weekends"):
            candidates = list(_WEEKEND)
        elif word in WEEKDAYS:
            candidates = [WEEKDAYS[word]]
        else:
            continue
        for day in candidates:
            if day not in found:
                found.append(day)
    return sorted(found)


def parse_schedule(
    value, timezone_name: Optional[str] = None
) -> Optional[Schedule]:
    """
    Parse a recurrence expression.

    Args:
        value: The expression ("every friday at noon", "every 2 hours", ...).
        timezone_name: Zone used when the expression does not name one.

    Returns:
        The parsed :class:`Schedule`, or ``None`` when *value* is not a
        recurrence (a one-shot instant such as ``"tonight 01:00"`` is not).

    Example:
        >>> parse_schedule("every friday at noon").text()
        'every friday at 12:00'
    """
    text = str(value or "").strip()
    if not text:
        return None

    zone_name = str(timezone_name or "").strip() or "UTC"
    zone_match = _ZONE_RE.search(text)
    if zone_match:
        zone_name = zone_match.group(1)
        text = text[: zone_match.start()].strip()

    lowered = text.lower().strip().rstrip(".")
    if not _PREFIX_RE.match(lowered):
        return None
    body = _PREFIX_RE.sub("", lowered, count=1).strip()
    if not body:
        return None

    interval = _INTERVAL_RE.match(body)
    if interval:
        seconds = _interval_seconds(interval.group(1), interval.group(2))
        if not seconds:
            return None
        return Schedule(
            kind="interval", timezone_name=zone_name, interval_seconds=seconds
        )

    hour, minute, rest = _extract_clock(body)
    weekdays = _extract_weekdays(rest)
    tokens = set(_WORD_RE.findall(rest))
    if weekdays:
        kind = "weekly"
    elif tokens & {"day", "daily", "days"}:
        kind = "daily"
    else:
        return None
    return Schedule(
        kind=kind,
        timezone_name=zone_name,
        hour=hour,
        minute=minute,
        weekdays=tuple(weekdays),
    )


def next_occurrence(schedule: Schedule, now: Optional[datetime] = None) -> str:
    """
    Return the first occurrence of *schedule* strictly after *now*.

    Args:
        schedule: The parsed rule.
        now: Reference instant (default: now, UTC).

    Returns:
        ISO-8601 UTC instant.
    """
    moment = now or datetime.now(tz=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    if schedule.kind == "interval":
        period = max(60, int(schedule.interval_seconds or 0))
        return (moment + timedelta(seconds=period)).astimezone(UTC).isoformat()

    zone = schedule.zone()
    local = moment.astimezone(zone)
    weekdays = schedule.weekdays if schedule.kind == "weekly" else ()
    for offset in range(0, 8):
        day = (local + timedelta(days=offset)).date()
        if weekdays and day.weekday() not in weekdays:
            continue
        candidate = datetime.combine(
            day, dt_time(schedule.hour, schedule.minute), tzinfo=zone
        )
        if candidate > local:
            return candidate.astimezone(UTC).isoformat()
    # Unreachable for a valid rule; never leave a task without an alarm clock.
    return (local + timedelta(days=1)).astimezone(UTC).isoformat()


def next_wake_at(
    value,
    now: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
) -> str:
    """
    Resolve a recurrence expression to the instant of its next run.

    Args:
        value: The recurrence expression.
        now: Reference instant (tests).
        timezone_name: Zone used when the expression does not name one.

    Returns:
        ISO-8601 UTC instant, or ``""`` when *value* is not a recurrence.

    Example:
        >>> next_wake_at("every 2 hours", datetime(2026, 4, 1, 15, 30, tzinfo=timezone.utc))
        '2026-04-01T17:30:00+00:00'
    """
    schedule = parse_schedule(value, timezone_name=timezone_name)
    if schedule is None:
        return ""
    return next_occurrence(schedule, now=now)


def is_recurring(value) -> bool:
    """Return True when *value* is a recurrence expression."""
    return parse_schedule(value) is not None


def canonical(value, timezone_name: Optional[str] = None) -> str:
    """
    Return the canonical form of *value* (the raw text when unparsable).

    Args:
        value: The recurrence expression.
        timezone_name: Zone used when the expression does not name one.
    """
    schedule = parse_schedule(value, timezone_name=timezone_name)
    if schedule is None:
        return str(value or "").strip()
    return schedule.text()


def parse_context(value) -> str:
    """
    Normalize how a recurring run starts.

    Args:
        value: ``"history"`` (or empty) / ``"clean"``, any case.

    Returns:
        :data:`CONTEXT_HISTORY` or :data:`CONTEXT_CLEAN`.
    """
    text = str(value or "").strip().lower()
    return CONTEXT_CLEAN if text in ("clean", "fresh", "isolated", "reset") else CONTEXT_HISTORY


# Made with ❤️ by codx-junior
