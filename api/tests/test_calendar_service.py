"""
Tests for the calendar service (milestone M0): ACL, project filter and writes.

The dataset is the canonical v2 design fixture
(``design/06-calendar/03-fixtures/calendar-fixture.json``): every test seeds a
throw-away store under ``tmp_path`` from it and drives
:class:`~codx.junior.calendar.service.CalendarService` as one of the fixture's
viewers (``alice`` / ``marta`` / ``jordi`` / ``bob``). Nothing ever touches the
real ``/home/codx-junior/calendar``: the root is passed explicitly *and* the
environment override points at the temporary folder.

What is asserted (analysis §11.1, §5.2, §5.3):

* **visibility** per viewer matches ``expected_visibility``: a private calendar
  of another user is invisible *and* answered with the same 404 as a missing
  one, a ``team`` calendar is readable by its reader, a ``workspace`` calendar
  is writable only by its owner or an admin, ``system``/``external`` calendars
  are visible but read-only;
* **marta** never sees the Work/Personal calendars, neither their ids nor their
  event titles;
* the **project filter** reproduces ``expected_filters``: the fixture total is
  exactly the documents M0 stores plus the items M1 (task/run/meet projection)
  and M3 (recurrence expansion) still owe, and the numbers of the fixture
  (9 / 2 / 1 / 3 / 1 / 12) are reproduced, never retyped;
* the filter is an **OR** on several project ids and can never widen what the
  viewer is allowed to see;
* the **write paths**: ``unknown_calendar``, ``read_only_calendar``,
  ``revision_mismatch`` (a stale ``If-Match``) and ``calendar_in_use`` without
  ``move_to``, plus ``move_to`` rehoming the events.
"""

import json
import os

from datetime import datetime

import pytest

from codx.junior.calendar.model import (
    Calendar,
    CalendarEvent,
    EventCreate,
    EventQuery,
    EventUpdate,
)
from codx.junior.calendar.service import (
    ERROR_CALENDAR_IN_USE,
    ERROR_NOT_FOUND,
    ERROR_READ_ONLY_CALENDAR,
    ERROR_REVISION_MISMATCH,
    ERROR_UNKNOWN_CALENDAR,
    CalendarError,
    CalendarService,
)
from codx.junior.calendar.store import CALENDAR_PATH_ENV, CalendarStore
from codx.junior.model.user import USER_ROLE_ADMIN, CodxUser

#: Canonical dataset of the design.
FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "design",
    "06-calendar",
    "03-fixtures",
    "calendar-fixture.json",
)

#: The anchor week the fixture's ``expected_query`` / ``expected_filters`` use.
ANCHOR = {"from": "2026-02-09T00:00:00Z", "to": "2026-02-16T00:00:00Z"}


def load_fixture() -> dict:
    """Load the canonical v2 fixture, skipping when the checkout lacks it."""
    if not os.path.isfile(FIXTURE_PATH):
        pytest.skip(f"design fixture not available in this checkout: {FIXTURE_PATH}")
    with open(FIXTURE_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_store(root: str, fixture: dict = None) -> CalendarStore:
    """Persist every fixture calendar and event under *root*, ids preserved."""
    data = fixture if fixture is not None else load_fixture()
    store = CalendarStore(root)
    for item in data["calendars"]:
        store.save_calendar(Calendar(**item))
    for item in data["events"]:
        store.save_event(CalendarEvent(**item))
    return store


def run_query(store: CalendarStore, viewer, projects=None, window=None) -> dict:
    """Run the main read of the agenda (§5.2) for *viewer*."""
    payload = dict(window or ANCHOR)
    if projects is not None:
        payload["projects"] = projects
    return CalendarService(store=store, viewer=viewer).query(EventQuery(**payload))


def occurrence_ids(result: dict) -> list:
    """Return the event ids of a query result, in API order."""
    return [occurrence["event_id"] for occurrence in result["occurrences"]]


# ---------------------------------------------------------------------------
# Helpers that recompute the expectations from the fixture (no retyping).
# ---------------------------------------------------------------------------


def _parse(value: str) -> datetime:
    """Parse a fixture ISO-8601 timestamp into an aware datetime."""
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _window(entry: dict, fixture: dict) -> dict:
    """Return the date range an ``expected_filters`` entry is scored on."""
    return entry.get("range") or fixture["expected_query"]["range"]


def _matches_projects(project_id, tokens) -> bool:
    """The documented project rule: ``personal`` matches ``None``, tokens OR."""
    tokens = [token for token in (tokens or []) if token]
    if not tokens:
        return True
    for token in tokens:
        if token == "personal":
            if not project_id:
                return True
        elif project_id and project_id == token:
            return True
    return False


def _stored_occurrences(fixture: dict, entry: dict) -> list:
    """
    Events M0 actually returns for *entry*: stored documents, not expanded.

    M0 has no recurrence engine (M3) and no projection of tasks, runs or meets
    (M1), so a window yields one occurrence per stored event whose slot
    overlaps it.
    """
    window = _window(entry, fixture)
    start, end = _parse(window["from"]), _parse(window["to"])
    ids = []
    for item in fixture["events"]:
        overlaps = _parse(item["start"]) < end and _parse(item["end"]) > start
        if overlaps and _matches_projects(item.get("project_id"), entry["projects"]):
            ids.append(item["id"])
    return ids


def _projected_occurrences(fixture: dict, entry: dict) -> int:
    """
    Items the later milestones add on top of the M0 documents of *entry*.

    * M1 projects the task blocks (``scheduled_start``, else the ``due_date``),
      the orchestration runs and the meets into the agenda;
    * M3 expands the recurrence rules. The instances are taken from the
      fixture's own ``expected_query.assertions``; a series whose stored start
      is already inside the window is not counted twice.
    """
    window = _window(entry, fixture)
    start, end = _parse(window["from"]), _parse(window["to"])
    stored = set(_stored_occurrences(fixture, entry))
    total = 0

    for task in fixture["tasks"]:
        if not _matches_projects(task.get("project_id"), entry["projects"]):
            continue
        scheduled = task.get("scheduled_start")
        if scheduled:
            total += int(start <= _parse(scheduled) < end)
        elif task.get("due_date"):
            # A deadline-only task is drawn on its due day ($comment_projection).
            total += int(start <= _parse(f"{task['due_date']}T00:00:00Z") < end)

    for run in fixture["runs"]:
        if not _matches_projects(run.get("project_id"), entry["projects"]):
            continue
        overlaps = _parse(run["started_at"]) < end and _parse(run["ends_at"]) > start
        total += int(overlaps)

    instances = {
        assertion["event_id"]: assertion.get("starts") or []
        for assertion in fixture["expected_query"]["assertions"]
        if assertion.get("source") not in ("task", "orchestration")
    }
    for event in fixture["events"]:
        if not event.get("recurrence"):
            continue
        if not _matches_projects(event.get("project_id"), entry["projects"]):
            continue
        inside = [
            value
            for value in instances.get(event["id"], [])
            if start <= _parse(value) < end
        ]
        total += len(inside) - (1 if event["id"] in stored else 0)

    return total


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_calendar_root(tmp_path, monkeypatch):
    """Point the env override at the temp folder: the real root is untouchable."""
    monkeypatch.setenv(CALENDAR_PATH_ENV, str(tmp_path / "env-calendar-never-used"))


@pytest.fixture
def fixture_data():
    """The canonical v2 fixture."""
    return load_fixture()


@pytest.fixture
def store(tmp_path):
    """Store seeded from the fixture, in a fresh temporary folder."""
    return seed_store(str(tmp_path / "calendar"))


# ---------------------------------------------------------------------------
# ACL (§11.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    load_fixture()["expected_visibility"],
    ids=lambda case: case["viewer"],
)
def test_calendar_visibility_matches_the_fixture(store, case):
    """``expected_visibility.sees`` / ``must_not_see``, verbatim, per viewer."""
    service = CalendarService(store=store, viewer=case["viewer"])

    visible = [calendar.id for calendar in service.visible_calendars()]
    assert set(visible) == set(case["sees"])

    for calendar_id in case.get("must_not_see") or []:
        assert calendar_id not in visible
        # "Not allowed" and "not found" are deliberately the same answer.
        with pytest.raises(CalendarError) as error:
            service.get_calendar(calendar_id)
        assert error.value.code == ERROR_NOT_FOUND
        assert error.value.status_code == 404


@pytest.mark.parametrize(
    "viewer,expected",
    [
        ("alice", ["cal_work", "cal_personal", "cal_team"]),
        ("marta", ["cal_team"]),
        ("jordi", []),
        ("bob", []),
    ],
)
def test_calendar_writability_follows_the_roles(store, viewer, expected):
    """
    Only an owner/writer writes a calendar; a reader never does.

    ``cal_allhands`` is a ``workspace`` calendar owned by ``admin``: the fixture
    itself states that "workspace calendars are writable only by their owner or
    an admin", so alice — owner of Work/Personal/Team, which the fixture lists —
    does not write it. See the admin/owner case below.
    """
    service = CalendarService(store=store, viewer=viewer)
    writable = [
        calendar.id for calendar in service.visible_calendars() if service.can_write(calendar)
    ]
    assert writable == expected


def test_a_workspace_calendar_is_written_by_its_owner_or_an_admin(store):
    alice = CalendarService(store=store, viewer="alice")
    admin = CalendarService(
        store=store, viewer=CodxUser(username="admin", role=USER_ROLE_ADMIN)
    )

    all_hands = alice.get_calendar("cal_allhands")
    assert all_hands.scope == "workspace"
    assert all_hands.owner == "admin"
    assert alice.can_write(all_hands) is False
    # The owner writes it even as a plain username...
    assert CalendarService(store=store, viewer="admin").can_write(all_hands) is True
    # ...but admin rights come from the user model, never from a name.
    assert CalendarService(store=store, viewer="bob").can_write(all_hands) is False
    assert admin.can_write(all_hands) is True


def test_marta_never_sees_the_private_calendars_nor_their_events(store, fixture_data):
    """D2/§11.1: another user's private calendars and their events never leak."""
    must_not_see = next(
        case["must_not_see"]
        for case in fixture_data["expected_visibility"]
        if case["viewer"] == "marta"
    )
    assert set(must_not_see) == {"cal_work", "cal_personal"}

    marta = CalendarService(store=store, viewer="marta")
    result = marta.query(EventQuery(**ANCHOR))
    titles = [occurrence["title"] for occurrence in result["occurrences"]]

    assert set(occurrence_ids(result)) == {"evt_retro", "evt_acme_kickoff"}
    for occurrence in result["occurrences"]:
        assert not set(occurrence["calendar_ids"]) & set(must_not_see)

    assert "evt_spec_review" not in occurrence_ids(result)
    assert "evt_one_on_one" not in occurrence_ids(result)
    assert "Spec review — calendar manager" not in titles
    assert "1:1 with Marta" not in titles

    # ...and the document is not readable one by one either.
    with pytest.raises(CalendarError) as error:
        marta.get_event("evt_one_on_one")
    assert error.value.code == ERROR_NOT_FOUND

    # The owner does see them: the rule is per-request visibility, not deletion.
    alice = CalendarService(store=store, viewer="alice")
    alice_ids = occurrence_ids(alice.query(EventQuery(**ANCHOR)))
    assert {"evt_spec_review", "evt_one_on_one"} <= set(alice_ids)


def test_a_reader_reads_the_team_calendar_but_cannot_write_it(store):
    jordi = CalendarService(store=store, viewer="jordi")
    team = jordi.get_calendar("cal_team")
    assert jordi.can_view(team) is True
    assert jordi.can_write(team) is False
    assert "evt_retro" in occurrence_ids(jordi.query(EventQuery(**ANCHOR)))

    with pytest.raises(CalendarError) as error:
        jordi.create_event(
            EventCreate(
                calendar_ids=["cal_team"],
                title="Jordi's meeting",
                start="2026-02-12T09:00:00Z",
                end="2026-02-12T10:00:00Z",
            )
        )
    assert error.value.code == ERROR_READ_ONLY_CALENDAR
    assert error.value.status_code == 403

    with pytest.raises(CalendarError) as error:
        jordi.delete_event("evt_retro")
    assert error.value.code == ERROR_READ_ONLY_CALENDAR
    assert error.value.status_code == 403

    # The writer of the same calendar may write it.
    marta = CalendarService(store=store, viewer="marta")
    created = marta.create_event(
        EventCreate(
            calendar_ids=["cal_team"],
            title="Team sync",
            start="2026-02-12T09:00:00Z",
            end="2026-02-12T10:00:00Z",
        )
    )
    assert created.id.startswith("evt_")
    assert created.created_by == "marta"
    assert created.revision == 1


def test_system_and_external_calendars_are_visible_but_read_only(store):
    alice = CalendarService(store=store, viewer="alice")

    for calendar_id in ("cal_codx", "cal_ics_holidays"):
        calendar = alice.get_calendar(calendar_id)
        assert calendar.is_read_only is True
        assert alice.can_write(calendar) is False

    # A projected/system item is moved in its source, never in the agenda.
    with pytest.raises(CalendarError) as error:
        alice.create_event(
            EventCreate(
                calendar_ids=["cal_codx"],
                title="Task block",
                start="2026-02-12T09:00:00Z",
                end="2026-02-12T10:00:00Z",
            )
        )
    assert error.value.code == ERROR_READ_ONLY_CALENDAR
    assert error.value.status_code == 409


def test_a_private_event_of_a_shared_calendar_is_masked(store):
    """§11.1: the slot stays, the details of somebody else's private event do not."""
    alice = CalendarService(store=store, viewer="alice")
    created = alice.create_event(
        EventCreate(
            calendar_ids=["cal_team"],
            title="Salary review",
            description="numbers",
            location="Room 1",
            guests=["jordi"],
            visibility="private",
            start="2026-02-12T15:00:00Z",
            end="2026-02-12T16:00:00Z",
        )
    )

    jordi = CalendarService(store=store, viewer="jordi")
    masked = jordi.event_payload(jordi.get_event(created.id))
    assert masked["title"] == "Private"
    assert masked["description"] == ""
    assert masked["location"] == ""
    assert masked["guests"] == []
    assert masked["start"] == "2026-02-12T15:00:00Z"
    assert masked["can_write"] is False

    # The creator (and a writer of the calendar) reads it in full.
    assert alice.event_payload(alice.get_event(created.id))["title"] == "Salary review"


# ---------------------------------------------------------------------------
# Project filter (§5.2, decision D1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    load_fixture()["expected_filters"],
    ids=lambda entry: "-".join(entry["projects"]) or "all",
)
def test_project_filter_reproduces_the_fixture_expectations(store, fixture_data, entry):
    """
    ``expected_filters`` is reproduced, and its numbers are explained.

    M0 stores documents: it returns exactly the fixture events whose slot
    overlaps the window, one occurrence each. The fixture totals (9 / 2 / 1 /
    3 / 1 / 12) are those documents **plus** the task/run/meet projection of M1
    and the recurrence instances of M3 — so the gap between the two is asserted
    instead of being silently ignored, and these tests will start failing the
    day the later milestones land, which is when they must be updated.
    """
    window = _window(entry, fixture_data)
    result = run_query(store, "alice", projects=entry["projects"], window=window)

    stored = _stored_occurrences(fixture_data, entry)
    assert sorted(occurrence_ids(result)) == sorted(stored)
    assert len(result["occurrences"]) == len(stored)

    assert entry["total_occurrences"] == len(stored) + _projected_occurrences(
        fixture_data, entry
    )

    if "by_source" in entry:
        # 'native' is the only source M0 already stores; meet/task/orchestration
        # come from the projection.
        assert result["counts"]["by_source"].get("native", 0) == entry["by_source"]["native"]

    if "event_ids" in entry:
        # The stored documents of that selection are a subset of the fixture's.
        assert set(stored) <= set(entry["event_ids"])


def test_the_project_filter_is_an_or_never_an_and(store):
    acme = run_query(store, "alice", projects=["acme-web"])
    personal = run_query(store, "alice", projects=["personal"])
    both = run_query(store, "alice", projects=["acme-web", "personal"])
    unfiltered = run_query(store, "alice", projects=[])
    default = run_query(store, "alice")

    assert not set(occurrence_ids(acme)) & set(occurrence_ids(personal))
    assert set(occurrence_ids(both)) == set(occurrence_ids(acme)) | set(
        occurrence_ids(personal)
    )
    assert len(both["occurrences"]) == len(acme["occurrences"]) + len(personal["occurrences"])

    # An empty filter means "no project filtering at all", the default view.
    assert len(unfiltered["occurrences"]) == len(default["occurrences"]) == 4
    assert set(occurrence_ids(unfiltered)) == set(occurrence_ids(default))


def test_the_project_filter_cannot_widen_what_a_viewer_sees(store):
    """Filtering happens *after* visibility: a private event stays invisible."""
    result = run_query(store, "marta", projects=["codx-junior-platform"])

    # 'Spec review' belongs to that project but lives in alice's private Work
    # calendar; marta only gets the team retro of the same project.
    assert occurrence_ids(result) == ["evt_retro"]


# ---------------------------------------------------------------------------
# Write paths (§5.3)
# ---------------------------------------------------------------------------


def test_an_unknown_calendar_is_rejected(store):
    alice = CalendarService(store=store, viewer="alice")

    with pytest.raises(CalendarError) as error:
        alice.create_event(
            EventCreate(
                calendar_ids=["cal_does_not_exist"],
                title="Ghost meeting",
                start="2026-02-12T09:00:00Z",
                end="2026-02-12T10:00:00Z",
            )
        )
    assert error.value.code == ERROR_UNKNOWN_CALENDAR
    assert error.value.status_code == 404


def test_a_read_only_calendar_refuses_the_write(store):
    alice = CalendarService(store=store, viewer="alice")
    event = CalendarEvent(
        **next(item for item in load_fixture()["events"] if item["id"] == "evt_standup")
    )
    event.calendar_ids = ["cal_ics_holidays"]

    alice.store.save_event(event)
    with pytest.raises(CalendarError) as error:
        alice.update_event(event.id, EventUpdate(title="Edited"))
    assert error.value.code == ERROR_READ_ONLY_CALENDAR
    assert error.value.status_code in (403, 409)


def test_a_stale_revision_is_a_revision_mismatch(store):
    alice = CalendarService(store=store, viewer="alice")
    current = alice.get_event("evt_spec_review")
    assert current.revision == 1

    with pytest.raises(CalendarError) as error:
        alice.update_event("evt_spec_review", EventUpdate(title="Renamed"), expected_revision=99)
    assert error.value.code == ERROR_REVISION_MISMATCH
    assert error.value.status_code == 409

    # The If-Match revision may also travel inside the payload.
    with pytest.raises(CalendarError) as error:
        alice.update_event("evt_spec_review", EventUpdate(title="Renamed", revision=0))
    assert error.value.code == ERROR_REVISION_MISMATCH

    # The current revision goes through, and it grows on every write.
    updated = CalendarService(store=store, viewer="alice").update_event(
        "evt_spec_review", EventUpdate(title="Renamed"), expected_revision=current.revision
    )
    assert updated.revision == 2
    assert updated.title == "Renamed"
    assert CalendarStore(store.root_path).get_event("evt_spec_review").title == "Renamed"


def test_calendar_in_use_requires_move_to_and_move_to_rehomes_the_events(store):
    alice = CalendarService(store=store, viewer="alice")

    with pytest.raises(CalendarError) as error:
        alice.delete_calendar("cal_team")
    assert error.value.code == ERROR_CALENDAR_IN_USE
    assert error.value.status_code == 409

    alice.delete_calendar("cal_team", move_to="cal_work")

    # A fresh store proves the rehoming was persisted, not just applied in memory.
    reloaded = CalendarStore(store.root_path)
    assert reloaded.get_calendar("cal_team") is None
    assert reloaded.get_event("evt_retro").calendar_ids == ["cal_work"]
    assert reloaded.get_event("evt_standup").calendar_ids == ["cal_work"]
    assert reloaded.get_event("evt_retro").revision == 2

    # marta lived in that team calendar: it disappears with it.
    marta = CalendarService(store=store, viewer="marta")
    assert "cal_team" not in {calendar.id for calendar in marta.visible_calendars()}
    with pytest.raises(CalendarError):
        marta.get_calendar("cal_team")
