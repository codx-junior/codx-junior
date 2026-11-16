"""
Tests for the ``schedule_task`` tool (after-hours task scheduling).

The tests cover the two things that can silently break: the natural-language
time resolution (which must never schedule something in the past) and the tool
registration (an unregistered tool is invisible to every profile).
"""

from datetime import datetime, timezone

import pytest

from codx.junior.tools import TOOLS, all_tool_names
from codx.junior.tools.schedule_task_tool import _resolve_when

NOW = datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc)


def _entry():
    for entry in TOOLS:
        if (entry.get("tool_json") or {}).get("function", {}).get("name") == "schedule_task":
            return entry
    return None


def test_schedule_task_is_registered():
    """The tool name and its callable are part of the runtime catalogue."""
    assert "schedule_task" in all_tool_names()
    entry = _entry()
    assert entry is not None
    assert entry["tool_call"].__name__ == "schedule_task"
    assert "wake_at" in (entry.get("tags") or [])


def test_schedule_task_tool_json_declares_when():
    """'when' is mandatory so the model cannot schedule without a moment."""
    parameters = _entry()["tool_json"]["function"]["parameters"]
    assert parameters["required"] == ["when"]
    for field in ("task_id", "task", "name", "profiles", "timezone", "clear"):
        assert field in parameters["properties"]


@pytest.mark.parametrize(
    "when,expected",
    [
        # A clock time already passed today means tonight, never the past.
        ("01:00", "2026-04-02T01:00:00+00:00"),
        ("tonight 01:00", "2026-04-02T01:00:00+00:00"),
        ("after hours", "2026-04-02T01:00:00+00:00"),
        ("tomorrow 1am", "2026-04-02T01:00:00+00:00"),
        ("at 23:30", "2026-04-01T23:30:00+00:00"),
    ],
)
def test_resolve_when_clock_times(when, expected):
    """Clock expressions resolve to the next occurrence in UTC by default."""
    assert _resolve_when(when, now=NOW) == expected


def test_resolve_when_uses_the_given_timezone():
    """'01:00' in Europe/Madrid is 23:00 UTC during CEST."""
    assert (
        _resolve_when("01:00", timezone_name="Europe/Madrid", now=NOW)
        == "2026-04-01T23:00:00+00:00"
    )


def test_resolve_when_delegates_relative_and_iso():
    """Relative expressions and ISO instants keep working."""
    assert _resolve_when("+2d", now=NOW) == "2026-04-03T15:00:00+00:00"
    assert _resolve_when("2026-05-01T09:30:00", now=NOW) == "2026-05-01T09:30:00+00:00"


@pytest.mark.parametrize("when", ["", "   ", "banana", "at 25:99"])
def test_resolve_when_rejects_garbage(when):
    """An unparsable schedule is an error, never a silent no-op."""
    with pytest.raises(ValueError):
        _resolve_when(when, now=NOW)
