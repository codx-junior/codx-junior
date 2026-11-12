"""
Tests for the calendar store (milestone M0, backend foundation).

Everything here runs against a ``tmp_path`` root, never against the real
``/home/codx-junior/calendar``: the store is constructed with an explicit
``root_path`` and the environment override is exercised with ``monkeypatch``.

The dataset is the canonical design fixture
(``design/06-calendar/03-fixtures/calendar-fixture.json``, version 2): the
storage tests seed the store from it instead of retyping calendars and events,
so what is proven is the exact document set the API will serve. The few tests
that build a document by hand do so for a primitive the fixture does not carry
(deduplication, an invalid range, a cross-month event).

The behaviours asserted are the ones M0 exists for:

* the fixture round-trips through the store (6 calendars, 8 events),
* the event lands in the shard of its **start** month (UTC),
* a range read sees the shards one month either side (§4.4),
* a corrupt shard returns ``[]`` instead of raising,
* moving an event's ``start`` moves the document instead of duplicating it,
* concurrent writers keep the shard a single valid document,
* nothing is ever written outside the store's own root,
* the additive ``Chat`` fields exist with their defaults.
"""

import json
import os
import threading

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

#: Canonical dataset of the design (analysis §9-§12 cover its assertions).
FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "design",
    "06-calendar",
    "03-fixtures",
    "calendar-fixture.json",
)

#: The anchor week of the fixture (§$comment: Monday 2026-02-09 .. Sunday 2026-02-15).
ANCHOR_FROM = "2026-02-09T00:00:00Z"
ANCHOR_TO = "2026-02-16T00:00:00Z"


def load_fixture() -> dict:
    """Load the canonical v2 fixture, skipping when the checkout lacks it."""
    if not os.path.isfile(FIXTURE_PATH):
        pytest.skip(f"design fixture not available in this checkout: {FIXTURE_PATH}")
    with open(FIXTURE_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_fixture(store: CalendarStore, fixture: dict = None) -> CalendarStore:
    """
    Write every fixture calendar and event into *store*, ids preserved.

    ``save_calendar`` / ``save_event`` only assign an id when the document has
    none, so the canonical ids (``cal_work``, ``evt_spec_review``, ...) survive
    the round-trip and the tests can address them by name.
    """
    data = fixture if fixture is not None else load_fixture()
    for item in data["calendars"]:
        store.save_calendar(Calendar(**item))
    for item in data["events"]:
        store.save_event(CalendarEvent(**item))
    return store


@pytest.fixture
def store(tmp_path):
    """Store rooted in a temporary folder (never the real installation one)."""
    return CalendarStore(str(tmp_path / "calendar"))


@pytest.fixture
def seeded(tmp_path):
    """Store pre-loaded with the canonical fixture (fresh folder per test)."""
    return seed_fixture(CalendarStore(str(tmp_path / "calendar")))


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


def test_seeding_the_fixture_round_trips_every_document(seeded):
    fixture = load_fixture()

    calendars = CalendarStore(seeded.root_path).list_calendars()
    assert [calendar.id for calendar in calendars] == [
        "cal_work",
        "cal_personal",
        "cal_team",
        "cal_allhands",
        "cal_codx",
        "cal_ics_holidays",
    ]
    # ``order`` drives the sidebar order, so the store must keep it.
    assert [calendar.order for calendar in calendars] == [0, 1, 2, 3, 90, 100]

    team = next(calendar for calendar in calendars if calendar.id == "cal_team")
    assert team.scope == "team"
    assert team.audience["members"] == ["alice", "marta", "jordi"]
    assert team.member_role("marta") == "writer"
    assert team.member_role("jordi") == "reader"

    events = CalendarStore(seeded.root_path).list_events_month("2026-02")
    assert len(events) == len(fixture["events"]) == 8
    assert {event.id for event in events} == {item["id"] for item in fixture["events"]}
    known = {calendar.id for calendar in calendars}
    for event in events:
        assert event.shard_month == "2026-02"
        assert set(event.calendar_ids) <= known


def test_events_land_in_the_shard_of_their_start_month(seeded):
    shard = os.path.join(seeded.events_dir(), "2026-02.json")
    assert seeded.event_shard_path("2026-02") == shard
    assert seeded.list_event_months() == ["2026-02"]
    assert os.path.isfile(shard)

    with open(shard, "r", encoding="utf-8") as handle:
        stored = json.load(handle)
    expected = sorted(
        load_fixture()["events"], key=lambda item: (item["start"], item["title"])
    )
    assert [item["id"] for item in stored] == [item["id"] for item in expected]

    reloaded = CalendarStore(seeded.root_path).get_event("evt_spec_review")
    assert reloaded is not None
    assert reloaded.calendar_ids == ["cal_work"]
    assert reloaded.shard_month == "2026-02"


def test_event_requires_at_least_one_calendar():
    with pytest.raises(ValueError):
        CalendarEvent(calendar_ids=[], start="2026-02-09T09:00:00Z", end="2026-02-09T10:00:00Z")


def test_event_calendar_ids_are_deduplicated():
    event = _event(calendar_ids=["cal_work", " cal_work ", "cal_team"])
    assert event.calendar_ids == ["cal_work", "cal_team"]


def test_corrupt_shard_returns_empty_instead_of_raising(seeded):
    shard = seeded.event_shard_path("2026-02")
    with open(shard, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    assert seeded.list_events_month("2026-02") == []
    assert seeded.list_events_range("2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z") == []
    assert seeded.get_event("evt_spec_review") is None
    # The catalogue is a different document: one broken shard never blanks it.
    assert len(seeded.list_calendars()) == 6


def test_range_read_covers_the_neighbouring_shards(seeded):
    # The fixture has no event spanning two months, so one is added: it starts
    # in January and overlaps February, which is the case the ±1 month pad of
    # §4.4 exists for.
    long_event = CalendarEvent(
        calendar_ids=["cal_work"],
        title="Long offsite",
        start="2026-01-30T09:00:00Z",
        end="2026-02-02T09:00:00Z",
    )
    seeded.save_event(long_event)
    assert seeded.list_event_months() == ["2026-01", "2026-02"]

    # A February window must read the January shard, otherwise this event of a
    # one-month-earlier shard would be lost.
    february = seeded.list_events_range("2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z")
    assert long_event.id in {event.id for event in february}
    assert "evt_flight" in {event.id for event in february}

    # Overlap filtering still applies: a window that ends before the event
    # starts returns nothing.
    assert seeded.list_events_range("2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z") == []

    # The anchor week of the fixture holds exactly its four native events: M0
    # stores and returns them unexpanded (recurrence is M3, projections M1).
    anchor = seeded.list_events_range(ANCHOR_FROM, ANCHOR_TO)
    assert [event.id for event in anchor] == [
        "evt_spec_review",
        "evt_one_on_one",
        "evt_acme_kickoff",
        "evt_retro",
    ]


def test_range_read_rejects_an_invalid_range(store):
    with pytest.raises(ValueError):
        store.list_events_range("not-a-date", "2026-03-01T00:00:00Z")


def test_moving_an_event_between_months_removes_the_old_copy(seeded):
    retro = seeded.get_event("evt_retro")
    retro.start = "2026-03-13T14:00:00Z"
    retro.end = "2026-03-13T15:00:00Z"
    seeded.save_event(retro)

    assert "evt_retro" not in {
        event.id for event in seeded.list_events_month("2026-02")
    }
    assert [event.id for event in seeded.list_events_month("2026-03")] == ["evt_retro"]
    assert seeded.list_event_months() == ["2026-02", "2026-03"]


def test_concurrent_writers_keep_the_shard_a_single_valid_document(tmp_path):
    root = str(tmp_path / "calendar")
    events = [CalendarEvent(**item) for item in load_fixture()["events"]]
    errors = []

    def write(subset):
        """Save *subset* through its own store instance, as another process would."""
        writer = CalendarStore(root)
        for event in subset:
            try:
                writer.save_event(event)
            except Exception as ex:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(ex)

    threads = [
        threading.Thread(target=write, args=(events[index::3],)) for index in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    store = CalendarStore(root)
    with open(store.event_shard_path("2026-02"), "r", encoding="utf-8") as handle:
        stored = json.load(handle)  # one complete document, never a half write
    assert {item["id"] for item in stored} == {event.id for event in events}
    assert [name for name in os.listdir(store.events_dir()) if name.endswith(".tmp")] == []
    for event in events:
        assert store.get_event(event.id).title == event.title


def test_the_store_writes_nothing_outside_its_own_root(seeded, tmp_path):
    assert seeded.root_path == str(tmp_path / "calendar")
    assert seeded.root_path != "/home/codx-junior/calendar"
    for path in seeded.snapshot_paths():
        assert os.path.abspath(path).startswith(seeded.root_path + os.sep)


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
    fixture = load_fixture()

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
