"""
Tests for the calendar store (milestone M0, backend foundation).

Everything here runs against a ``tmp_path`` root, never against the real
``/home/codx-junior/calendar``: the store is constructed with an explicit
``root_path`` and the environment override is exercised with ``monkeypatch``.

The behaviours asserted are the ones M0 exists for:

* a calendar and an event survive a save/reload round-trip,
* the event lands in the shard of its **start** month (UTC),
* a range read sees the shards one month either side (§4.4),
* a corrupt shard returns ``[]`` instead of raising,
* moving an event's ``start`` moves the document instead of duplicating it,
* the additive ``Chat`` fields exist with their defaults.
"""

import json
import os

import pytest

from codx.junior.calendar.model import (
    Calendar,
    CalendarEvent,
    EventQuery,
    ReminderRule,
    month_key,
    shift_month,
)
from codx.junior.calendar.store import CALENDAR_PATH_ENV, CalendarStore, get_calendar_root


@pytest.fixture
def store(tmp_path):
    """Store rooted in a temporary folder (never the real installation one)."""
    return CalendarStore(str(tmp_path / "calendar"))


def _event(**kwargs):
    """Build a valid event (``calendar_ids`` is mandatory)."""
    payload = {
        "calendar_ids": ["cal_work"],
        "title": "Spec review",
        "start": "2026-02-09T09:00:00Z",
        "end": "2026-02-09T10:00:00Z",
    }
    payload.update(kwargs)
    return CalendarEvent(**payload)


def test_calendar_round_trip(store):
    saved = store.save_calendar(
        Calendar(
            name="Work",
            color="#CC0099",
            scope="private",
            owner="alice",
            default_reminders=[ReminderRule(minutes_before=10)],
        )
    )
    assert saved.id.startswith("cal_")

    # A second store instance proves the document was actually written.
    reloaded = CalendarStore(store.root_path).get_calendar(saved.id)
    assert reloaded is not None
    assert reloaded.name == "Work"
    assert reloaded.owner == "alice"
    assert reloaded.scope == "private"
    assert reloaded.default_reminders[0].minutes_before == 10
    assert reloaded.is_read_only is False
    assert reloaded.member_role("alice") == "owner"
    assert reloaded.member_role("marta") is None


def test_system_and_external_calendars_are_read_only_whatever_read_only_says():
    assert Calendar(name="codx", scope="system").is_read_only is True
    assert Calendar(name="ics", scope="external").is_read_only is True
    assert Calendar(name="w", scope="workspace").is_read_only is False


def test_event_round_trip_lands_in_start_month_shard(store):
    saved = store.save_event(_event())
    assert saved.id.startswith("evt_")

    shard = os.path.join(store.events_dir(), "2026-02.json")
    assert os.path.isfile(shard)
    with open(shard, "r", encoding="utf-8") as handle:
        assert json.load(handle)[0]["id"] == saved.id

    reloaded = CalendarStore(store.root_path).get_event(saved.id)
    assert reloaded is not None
    assert reloaded.calendar_ids == ["cal_work"]
    assert reloaded.shard_month == "2026-02"


def test_event_requires_at_least_one_calendar():
    with pytest.raises(ValueError):
        CalendarEvent(calendar_ids=[], start="2026-02-09T09:00:00Z", end="2026-02-09T10:00:00Z")


def test_event_calendar_ids_are_deduplicated():
    event = _event(calendar_ids=["cal_work", " cal_work ", "cal_team"])
    assert event.calendar_ids == ["cal_work", "cal_team"]


def test_corrupt_shard_returns_empty_instead_of_raising(store):
    saved = store.save_event(_event())
    shard = os.path.join(store.events_dir(), "2026-02.json")
    with open(shard, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    assert store.list_events_month("2026-02") == []
    assert store.list_events_range("2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z") == []
    assert store.get_event(saved.id) is None


def test_range_read_is_padded_one_month_either_side(store):
    # Starts in January and overlaps February: only visible thanks to the pad.
    store.save_event(
        _event(
            title="Long offsite",
            start="2026-01-30T09:00:00Z",
            end="2026-02-02T09:00:00Z",
        )
    )
    store.save_event(_event())  # inside February
    # Starts in March, outside the window: must not be returned.
    store.save_event(
        _event(title="Later", start="2026-03-05T09:00:00Z", end="2026-03-05T10:00:00Z")
    )

    events = store.list_events_range("2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z")
    assert [event.title for event in events] == ["Long offsite", "Spec review"]


def test_range_read_rejects_an_invalid_range(store):
    with pytest.raises(ValueError):
        store.list_events_range("not-a-date", "2026-03-01T00:00:00Z")


def test_moving_an_event_between_months_removes_the_old_copy(store):
    saved = store.save_event(_event())
    saved.start = "2026-03-09T09:00:00Z"
    saved.end = "2026-03-09T10:00:00Z"
    store.save_event(saved)

    assert store.list_events_month("2026-02") == []
    assert [event.id for event in store.list_events_month("2026-03")] == [saved.id]


def test_delete_event_and_calendar(store):
    calendar = store.save_calendar(Calendar(name="Work"))
    event = store.save_event(_event(calendar_ids=[calendar.id]))

    assert store.delete_event(event.id) is True
    assert store.delete_event(event.id) is False
    assert store.get_event(event.id) is None

    assert store.delete_calendar(calendar.id) is True
    assert store.delete_calendar(calendar.id) is False
    assert store.list_calendars() == []


def test_prefs_and_delivered_log_round_trip(store):
    store.save_prefs("alice", {"hidden": ["cal_personal"], "week_start": 1})
    assert CalendarStore(store.root_path).get_prefs("alice")["week_start"] == 1
    assert store.get_prefs("bob") == {}

    key = "evt_1|2026-02-10T09:00:00Z|15"
    store.write_delivered({key: "2026-02-10T08:45:03Z"})
    assert CalendarStore(store.root_path).read_delivered() == {key: "2026-02-10T08:45:03Z"}

    with pytest.raises(ValueError):
        store.get_prefs("../escape")


def test_root_defaults_to_the_globals_constant_and_honours_the_env(monkeypatch):
    from codx.junior import globals as codx_globals

    assert month_key("2026-02-09T23:30:00Z") == "2026-02"
    assert shift_month("2026-01", -1) == "2025-12"

    monkeypatch.delenv(CALENDAR_PATH_ENV, raising=False)
    assert get_calendar_root() == codx_globals.CODX_JUNIOR_CALENDAR_PATH

    monkeypatch.setenv(CALENDAR_PATH_ENV, "/tmp/calendar-override")
    assert get_calendar_root() == "/tmp/calendar-override"


def test_event_query_exposes_the_from_alias():
    query = EventQuery(**{"from": "2026-02-09T00:00:00Z", "to": "2026-02-16T00:00:00Z"})
    assert query.from_ == "2026-02-09T00:00:00Z"
    assert query.projects is None

    filtered = EventQuery(
        **{
            "from": "2026-02-09T00:00:00Z",
            "to": "2026-02-16T00:00:00Z",
            "projects": ["acme-web", "acme-web", " "],
        }
    )
    assert filtered.projects == ["acme-web"]


def test_the_design_fixture_validates_against_the_model():
    """The canonical dataset of the design must load unchanged (M0 is storage)."""
    fixture_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "design",
        "06-calendar",
        "03-fixtures",
        "calendar-fixture.json",
    )
    if not os.path.isfile(fixture_path):
        pytest.skip("design fixture not available in this checkout")
    with open(fixture_path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)

    calendars = {
        calendar.id: calendar
        for calendar in (Calendar(**item) for item in fixture["calendars"])
    }
    assert len(calendars) == 6
    team = calendars["cal_team"]
    assert team.scope == "team"
    assert team.member_role("alice") == "owner"
    assert team.member_role("marta") == "writer"
    assert team.member_role("jordi") == "reader"
    assert calendars["cal_codx"].is_read_only is True
    assert calendars["cal_ics_holidays"].is_read_only is True
    assert calendars["cal_ics_holidays"].visible is False

    events = [CalendarEvent(**item) for item in fixture["events"]]
    assert len(events) == 8
    assert {event.shard_month for event in events} == {"2026-02"}

    one_on_one = next(event for event in events if event.id == "evt_one_on_one")
    assert one_on_one.calendar_ids == ["cal_work", "cal_personal"]
    assert one_on_one.project_id is None

    standup = next(event for event in events if event.id == "evt_standup")
    assert standup.is_recurring is True
    assert standup.reminders[0].minutes_before == 5
    assert (
        standup.recurrence.overrides["2026-02-11T08:15:00Z"]["title"]
        == "Daily standup (moved)"
    )


def test_chat_carries_the_calendar_fields_with_defaults():
    from codx.junior.db import Chat

    chat = Chat(name="Sprint 42 review", project_id="codx-junior-platform")
    assert chat.calendar_ids == []
    assert chat.scheduled_start is None
    assert chat.scheduled_end is None
    assert chat.all_day is False

    scheduled = Chat(
        name="Sprint 42 review",
        calendar_ids=["cal_work"],
        scheduled_start="2026-02-10T15:00:00Z",
        scheduled_end="2026-02-10T16:00:00Z",
    )
    assert scheduled.calendar_ids == ["cal_work"]
    assert scheduled.scheduled_start == "2026-02-10T15:00:00Z"
    # The deadline keeps its own (date) meaning.
    assert scheduled.due_date is None
