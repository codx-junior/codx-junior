"""Tests for the human working hours / non-working-hours scheduler.

The working window is installation-wide. ``non-working-hours`` must resolve to
the next instant at which humans stop working (the closing edge of the window),
or to "now" when they are already offline. These tests inject the settings so
they never depend on the real global settings file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from codx.junior.working_hours import (
    is_non_working_token,
    next_non_working_start,
    resolve_when,
)

SETTINGS = {
    "enabled": True,
    "timezone": "UTC",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "start": "09:00",
    "end": "18:00",
}


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_inside_working_hours_schedules_at_the_end_of_the_day():
    resolved = next_non_working_start(
        moment=_utc(2026, 3, 2, 10, 30),  # Monday
        settings=SETTINGS,
    )

    assert resolved == "2026-03-02T18:00:00+00:00"


def test_outside_working_hours_schedules_now():
    # Night: already non-working → immediate
    assert next_non_working_start(
        moment=_utc(2026, 3, 2, 22, 0), settings=SETTINGS
    ) == "2026-03-02T22:00:00+00:00"

    # Weekend: immediate
    assert next_non_working_start(
        moment=_utc(2026, 3, 7, 10, 0), settings=SETTINGS
    ) == "2026-03-07T10:00:00+00:00"


def test_overnight_working_window_schedules_the_morning_close():
    settings = {
        "enabled": True,
        "timezone": "UTC",
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "start": "22:00",
        "end": "06:00",
    }

    # Monday 23:00 is inside the overnight window; it closes Tuesday at 06:00.
    assert next_non_working_start(
        moment=_utc(2026, 3, 2, 23, 0), settings=settings
    ) == "2026-03-03T06:00:00+00:00"

    # Tuesday 02:00 is the tail of Monday's window; it closes Tuesday at 06:00.
    assert next_non_working_start(
        moment=_utc(2026, 3, 3, 2, 0), settings=settings
    ) == "2026-03-03T06:00:00+00:00"


def test_disabled_schedule_falls_back_to_after_hours_clock():
    settings = {**SETTINGS, "enabled": False}

    assert next_non_working_start(
        moment=_utc(2026, 3, 2, 10, 0), settings=settings
    ) == "2026-03-03T01:00:00+00:00"


def test_tokens_and_resolution():
    assert is_non_working_token("non-working-hours")
    assert is_non_working_token("after-hours")
    assert not is_non_working_token("+2d")
    assert resolve_when(
        "non-working-hours", moment=_utc(2026, 3, 2, 10, 0)
    ).endswith("T18:00:00+00:00")
