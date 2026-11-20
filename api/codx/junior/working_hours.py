"""
Human working hours and "non-working hours" scheduling.

Tasks can be scheduled to run **outside the hours humans work** (at night, over
the weekend, ...), so autonomous profiles do not compete with the daily work of
the team. The working window is an installation-wide setting
(``GlobalSettings.working_hours``); ``non-working-hours`` is resolved to the
next instant at which that window closes.

The schedule vocabulary lives in :mod:`codx.junior.autonomy.models`
(``ScheduleWindow``/``WeeklySchedule``), so this module only adapts the human
configuration and adds the inverse operation: :func:`next_non_working_start`.
"""

from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from codx.junior.autonomy.models import (
    ScheduleWindow,
    WeeklySchedule,
    timezone_for,
    utcnow,
)

logger = logging.getLogger(__name__)

#: Tokens understood by the task tools and the schedule resolver.
NON_WORKING_TOKENS = {
    "non-working-hours",
    "non_working_hours",
    "nonworking-hours",
    "nonworking_hours",
    "after-hours",
    "after_hours",
    "off-hours",
    "off_hours",
}

#: Default human window, used when the setting is missing or disabled.
DEFAULT_DAYS = ["mon", "tue", "wed", "thu", "fri"]
DEFAULT_START = "09:00"
DEFAULT_END = "18:00"
DEFAULT_TIMEZONE = "UTC"

WORKING_HOURS_SECTION = "working_hours"


def is_non_working_token(value: Any) -> bool:
    """Tell whether *value* asks for the non-working-hours schedule."""
    return str(value or "").strip().lower() in NON_WORKING_TOKENS


def _human_settings() -> Any:
    """Read the human working hours from the global settings (safe default)."""
    try:
        from codx.junior.global_settings import read_global_settings

        settings = read_global_settings()
        return getattr(settings, WORKING_HOURS_SECTION, None)
    except Exception as ex:  # pragma: no cover - settings may be unavailable
        logger.debug("working hours: cannot read global settings: %s", ex)
        return None


def _as_schedule(settings: Any = None) -> WeeklySchedule:
    """Convert the human settings into a normalized :class:`WeeklySchedule`."""
    settings = settings if settings is not None else _human_settings()
    if settings is None:
        return WeeklySchedule(
            enabled=True,
            timezone=DEFAULT_TIMEZONE,
            windows=[
                ScheduleWindow(
                    days=list(DEFAULT_DAYS),
                    start=DEFAULT_START,
                    end=DEFAULT_END,
                )
            ],
        ).normalized()

    def value(name: str, default: Any) -> Any:
        if isinstance(settings, dict):
            return settings.get(name, default)
        return getattr(settings, name, default)

    days = value("days", DEFAULT_DAYS)
    if isinstance(days, str):
        days = [days]
    window = ScheduleWindow(
        days=list(days or DEFAULT_DAYS),
        start=value("start", DEFAULT_START),
        end=value("end", DEFAULT_END),
    )
    return WeeklySchedule(
        enabled=bool(value("enabled", True)),
        timezone=str(value("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE),
        windows=[window],
    ).normalized()


def working_hours_description() -> str:
    """Return a short human description of the configured working window."""
    return _as_schedule().describe()


def is_working_time(moment: Optional[datetime] = None) -> bool:
    """Return True when *moment* falls inside the human working window."""
    return _as_schedule().is_open(moment or utcnow())


def _window_end(local: datetime, window: ScheduleWindow) -> Optional[datetime]:
    """Return the end instant of *window* that contains *local* (or None)."""
    window = window.normalized()
    start = _minutes(window.start)
    end = _minutes(window.end)
    if start is None or end is None or start == end:
        return None
    day = local.weekday()
    day_name = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day]
    minutes = local.hour * 60 + local.minute
    if start < end:
        if not window.open_at(day_name, minutes):
            return None
        return local.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    # Overnight window: 22:00 -> 06:00.
    if minutes >= start:
        if not window.open_at(day_name, minutes):
            return None
        end_date = local.date() + timedelta(days=1)
    else:
        # The tail (00:00 -> end) belongs to the previous day's window.
        if not window.open_at(day_name, minutes):
            return None
        end_date = local.date()
    return datetime(
        end_date.year, end_date.month, end_date.day, tzinfo=local.tzinfo
    ) + timedelta(minutes=end)


def _minutes(value: Any) -> Optional[int]:
    """Parse ``"HH:MM"`` into minutes since midnight."""
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    try:
        hour, minute = text.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def next_non_working_start(
    moment: Optional[datetime] = None,
    settings: Any = None,
) -> str:
    """
    Resolve the next instant at which humans are not working.

    * Outside working hours (night, weekend, holiday break) → **now**: the task
      can start immediately.
    * Inside a working window → the window's closing instant.

    The instant is returned as an ISO-8601 UTC string and is what
    ``Chat.wake_at`` stores, so the autonomy worker picks the task up at that
    time.

    Args:
        moment: Reference instant (tests). Defaults to the current UTC instant.
        settings: Optional working-hours settings (defaults to global settings).

    Returns:
        ISO-8601 UTC instant.
    """
    schedule = _as_schedule(settings)
    now = moment or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    if not schedule.enabled:
        # No configured human window: still avoid the working day by using the
        # conventional after-hours clock (01:00 UTC).
        local = now.astimezone(timezone_for(DEFAULT_TIMEZONE))
        candidate = local.replace(hour=1, minute=0, second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc).isoformat()

    local = now.astimezone(timezone_for(schedule.timezone))
    if not schedule.is_open(now):
        return now.isoformat()

    ends = [
        end for end in (_window_end(local, window) for window in schedule.windows)
        if end is not None and end > local
    ]
    if not ends:
        # Inside an open window but no closing time could be derived: fall back
        # to the next whole hour outside it.
        return (now + timedelta(hours=1)).isoformat()
    return min(ends).astimezone(timezone.utc).isoformat()


def resolve_when(
    value: Any,
    timezone_name: Optional[str] = None,
    moment: Optional[datetime] = None,
) -> str:
    """
    Resolve a scheduling expression, including the ``non-working-hours`` token.

    Args:
        value: Expression (``"non-working-hours"``, ``"+2d"``, ``"01:00"``...).
        timezone_name: Optional zone for relative expressions (unused for the
            working-hours token, which uses the configured zone).
        moment: Reference instant for tests.

    Returns:
        ISO-8601 UTC instant, or ``""`` when the value cannot be resolved.
    """
    if is_non_working_token(value):
        return next_non_working_start(moment=moment)
    try:
        from codx.junior.autonomy.models import parse_when

        return parse_when(value, now=moment) or ""
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("working hours: cannot resolve '%s': %s", value, ex)
        return ""


# Made with ❤️ by codx-junior
