"""TEMPORARY verification script for the M0 calendar service + router (deleted after use)."""

import importlib.util
import os
import sys
import tempfile

API_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, API_ROOT)

# Never touch the real installation folder.
ROOT = tempfile.mkdtemp(prefix="codx-calendar-api-")
os.environ["CODX_JUNIOR_CALENDAR_PATH"] = ROOT

from fastapi import FastAPI  # noqa: E402

FAILURES = []
PASSED = []
VERBOSE = os.environ.get("CHECK_VERBOSE") == "1"


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        if VERBOSE:
            print(f"[ok  ] {name} {detail}", flush=True)
        return
    FAILURES.append(f"{name} {detail}")
    print(f"[FAIL] {name} {detail}", flush=True)


def _safe_404(fn):
    """Return True when *fn* raises a 404 CalendarError."""
    try:
        fn()
    except Exception as ex:  # noqa: BLE001
        return getattr(ex, "code", None) == "not_found" and ex.status_code == 404
    return False


# ---------------------------------------------------------------------------
# 1. The router module, loaded exactly as `discover_routers` would find it
#    (a module of `codx/junior/api/` exporting `router`).
#    `codx.junior.api.__init__` pulls the whole engine in, so the module is
#    loaded by path here; the contract discover_routers checks is verified
#    explicitly below.
# ---------------------------------------------------------------------------
API_PACKAGE_DIR = os.path.join(API_ROOT, "codx", "junior", "api")
ROUTER_PATH = os.path.join(API_PACKAGE_DIR, "calendar.py")

spec = importlib.util.spec_from_file_location("codx_calendar_router_probe", ROUTER_PATH)
router_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router_module)

print("=== discovery contract (codx/junior/api/__init__.py) ===", flush=True)
print(f"file                : {os.path.relpath(ROUTER_PATH, API_ROOT)}", flush=True)
print(f"in api package      : {os.path.dirname(ROUTER_PATH) == API_PACKAGE_DIR}", flush=True)
print(f"exports 'router'    : {hasattr(router_module, 'router')}", flush=True)
print(f"router prefix/tags  : {router_module.router.prefix!r} {router_module.router.tags}", flush=True)
print(f"router routes       : {len(router_module.router.routes)}", flush=True)
for _r in router_module.router.routes:
    print(f"  raw {sorted(getattr(_r, 'methods', []))} {getattr(_r, 'path', None)!r}", flush=True)
check("module exports a module-level router", hasattr(router_module, "router"))
check(
    "module lives in the auto-discovered api package",
    os.path.dirname(ROUTER_PATH) == API_PACKAGE_DIR
    and not os.path.basename(ROUTER_PATH).startswith("test_"),
)

APP = FastAPI()
APP.include_router(router_module.router, prefix="/api")  # what load_routers() does
print(f"app routes          : {len(APP.routes)}", flush=True)
for _route in APP.routes:
    print("  app", sorted(getattr(_route, "methods", [])), getattr(_route, "path", None), flush=True)

print()
print("=== FastAPI route table (the calendar routes) ===", flush=True)
for _route in APP.routes:
    _path = getattr(_route, "path", "")
    if "/calendar" not in _path:
        continue
    _methods = ",".join(
        sorted(m for m in getattr(_route, "methods", set()) if m not in ("HEAD", "OPTIONS"))
    )
    print(f"{_methods:22s} {_path}", flush=True)
print(flush=True)

CALENDAR_ROUTES = {
    (method, route.path)
    for route in APP.routes
    for method in getattr(route, "methods", set())
    if "/calendar" in getattr(route, "path", "")
}
EXPECTED_ROUTES = {
    ("GET", "/api/calendar/calendars"),
    ("POST", "/api/calendar/calendars"),
    ("PUT", "/api/calendar/calendars/{calendar_id}"),
    ("DELETE", "/api/calendar/calendars/{calendar_id}"),
    ("GET", "/api/calendar/calendars/{calendar_id}/share"),
    ("GET", "/api/calendar/projects"),
    ("GET", "/api/calendar/prefs"),
    ("PUT", "/api/calendar/prefs"),
    ("POST", "/api/calendar/events/query"),
    ("GET", "/api/calendar/events/{event_id}"),
    ("POST", "/api/calendar/events"),
    ("PUT", "/api/calendar/events/{event_id}"),
    ("DELETE", "/api/calendar/events/{event_id}"),
    ("POST", "/api/calendar/events/{event_id}/snooze"),
    ("POST", "/api/calendar/events/quick"),
}
check(
    "route table matches §5.1 + §5.2",
    CALENDAR_ROUTES - {("HEAD", p) for _, p in CALENDAR_ROUTES} == EXPECTED_ROUTES,
    str(sorted(EXPECTED_ROUTES ^ {r for r in CALENDAR_ROUTES if r[0] != "HEAD"})),
)

# ---------------------------------------------------------------------------
# 2. Service: ACL, query and writes against a throw-away calendar root.
# ---------------------------------------------------------------------------
from codx.junior.calendar.model import (  # noqa: E402
    Calendar,
    CalendarCreate,
    CalendarEvent,
    CalendarUpdate,
    EventCreate,
    EventQuery,
    EventUpdate,
    Recurrence,
    ReminderRule,
)
from codx.junior.calendar.service import CalendarError, CalendarService  # noqa: E402
from codx.junior.calendar.store import CalendarStore  # noqa: E402
from codx.junior.model.user import CodxUser  # noqa: E402


def raises(name, code, status, fn):
    try:
        fn()
    except CalendarError as ex:
        check(name, ex.code == code and ex.status_code == status, f"-> {ex}")
        return
    check(name, False, "-> no CalendarError")


def user(name, role="user"):
    return CodxUser(username=name, role=role)


ALICE, MARTA, JORDI, BOB, ADMIN = (
    user("alice"),
    user("marta"),
    user("jordi"),
    user("bob"),
    user("admin", "admin"),
)

STORE = CalendarStore(ROOT)

CAL_WORK = STORE.save_calendar(
    Calendar(
        name="Work",
        scope="private",
        owner="alice",
        timezone="Europe/Madrid",
        members=[{"user": "alice", "role": "owner"}],
        default_reminders=[ReminderRule(minutes_before=10)],
    )
)
CAL_PERSONAL = STORE.save_calendar(
    Calendar(name="Personal", scope="private", owner="alice", order=1)
)
CAL_TEAM = STORE.save_calendar(
    Calendar(
        name="Team — Platform",
        scope="team",
        owner="alice",
        order=2,
        audience={
            "kind": "team",
            "project": "codx-junior-platform",
            "name": "Platform",
            "members": ["alice", "marta", "jordi"],
        },
        members=[
            {"user": "alice", "role": "owner"},
            {"user": "marta", "role": "writer"},
            {"user": "jordi", "role": "reader"},
        ],
    )
)
CAL_ALLHANDS = STORE.save_calendar(
    Calendar(
        name="All hands",
        scope="workspace",
        owner="admin",
        order=3,
        audience={"kind": "workspace"},
        members=[{"user": "admin", "role": "owner"}],
    )
)
CAL_CODX = STORE.save_calendar(
    Calendar(
        name="codx-junior",
        scope="system",
        read_only=True,
        order=90,
        audience={"kind": "workspace"},
    )
)
CAL_ICS = STORE.save_calendar(
    Calendar(
        name="Holidays (ICS)",
        scope="external",
        read_only=True,
        visible=False,
        order=100,
        audience={"kind": "workspace"},
    )
)


def save_event(**kwargs):
    payload = {
        "title": "event",
        "start": "2026-02-09T09:00:00Z",
        "end": "2026-02-09T10:00:00Z",
    }
    payload.update(kwargs)
    return STORE.save_event(CalendarEvent(**payload))


EVT_WORK = save_event(
    title="Spec review", calendar_ids=[CAL_WORK.id], project_id="codx-junior-platform"
)
EVT_TEAM = save_event(
    title="Team retro",
    calendar_ids=[CAL_TEAM.id],
    project_id="codx-junior-platform",
    start="2026-02-10T09:00:00Z",
    end="2026-02-10T10:00:00Z",
)
EVT_ACME = save_event(
    title="Acme kickoff",
    calendar_ids=[CAL_ALLHANDS.id, CAL_WORK.id],
    project_id="acme-web",
    start="2026-02-12T09:00:00Z",
    end="2026-02-12T10:00:00Z",
)
EVT_PERSONAL = save_event(
    title="1:1 with Marta",
    calendar_ids=[CAL_PERSONAL.id],
    project_id=None,
    start="2026-02-11T11:00:00Z",
    end="2026-02-11T11:30:00Z",
)
EVT_RECURRING = save_event(
    title="Daily standup",
    calendar_ids=[CAL_PERSONAL.id, CAL_WORK.id],
    project_id="codx-junior-platform",
    start="2026-02-13T08:15:00Z",
    end="2026-02-13T08:30:00Z",
    recurrence=Recurrence(rrule="FREQ=WEEKLY;BYDAY=MO,WE"),
)

print("=== ACL: who sees which calendar (§11.1) ===", flush=True)
ALICE_SERVICE = CalendarService(STORE, ALICE)
MARTA_SERVICE = CalendarService(STORE, MARTA)
JORDI_SERVICE = CalendarService(STORE, JORDI)
BOB_SERVICE = CalendarService(STORE, BOB)
ADMIN_SERVICE = CalendarService(STORE, ADMIN)

check(
    "alice sees her private + team + shared",
    {c.id for c in ALICE_SERVICE.visible_calendars()}
    == {CAL_WORK.id, CAL_PERSONAL.id, CAL_TEAM.id, CAL_ALLHANDS.id, CAL_CODX.id, CAL_ICS.id},
)
check(
    "marta never sees Work/Personal",
    {c.id for c in MARTA_SERVICE.visible_calendars()}
    == {CAL_TEAM.id, CAL_ALLHANDS.id, CAL_CODX.id, CAL_ICS.id},
)
check(
    "bob sees only workspace-scoped calendars",
    {c.id for c in BOB_SERVICE.visible_calendars()}
    == {CAL_ALLHANDS.id, CAL_CODX.id, CAL_ICS.id},
)
check(
    "a private calendar of another user answers 404 (not 403)",
    _safe_404(lambda: MARTA_SERVICE.get_calendar(CAL_WORK.id)),
)
check("team writer can write", MARTA_SERVICE.can_write(CAL_TEAM))
check("team reader can read", JORDI_SERVICE.can_view(CAL_TEAM))
check("team reader cannot write", not JORDI_SERVICE.can_write(CAL_TEAM))
check("non-member cannot write the team calendar", not BOB_SERVICE.can_write(CAL_TEAM))
check("workspace calendar: non-owner does not write", not ALICE_SERVICE.can_write(CAL_ALLHANDS))
check("workspace calendar: admin writes", ADMIN_SERVICE.can_write(CAL_ALLHANDS))
check("system calendar is read-only for admins", not ADMIN_SERVICE.can_write(CAL_CODX))
check("external calendar is read-only", not ADMIN_SERVICE.can_write(CAL_ICS))

print()
print("=== query: one occurrence per event, project filter (§5.2) ===", flush=True)


def query(service, projects=None, **kwargs):
    payload = {"from": "2026-02-09T00:00:00Z", "to": "2026-02-16T00:00:00Z"}
    if projects is not None:
        payload["projects"] = projects
    payload.update(kwargs)
    return service.query(EventQuery(**payload))


full = query(ALICE_SERVICE)
check("unfiltered returns every event once", len(full["occurrences"]) == 5)
check(
    "recurring event yields ONE occurrence (no expansion in M0)",
    len([o for o in full["occurrences"] if o["event_id"] == EVT_RECURRING.id]) == 1,
)
check(
    "by_source counts add up",
    sum(full["counts"]["by_source"].values()) == len(full["occurrences"]),
)
check("by_calendar counts add up", sum(full["counts"]["by_calendar"].values()) == 5)
check(
    "by_project uses 'personal' for project_id None",
    "personal" in full["counts"]["by_project"],
)
check("range echoes UTC", full["range"]["from"] == "2026-02-09T00:00:00Z")
check("warnings empty", full["warnings"] == [])
check("truncated_projects empty (projection is M1)", full["truncated_projects"] == [])

check("absent projects == all", len(full["occurrences"]) == 5)
check("empty projects == all", len(query(ALICE_SERVICE, projects=[])["occurrences"]) == 5)
check(
    "projects=['codx-junior-platform']",
    query(ALICE_SERVICE, projects=["codx-junior-platform"])["counts"]["by_project"]
    == {"codx-junior-platform": 3},
)
check(
    "projects=['acme-web']",
    query(ALICE_SERVICE, projects=["acme-web"])["counts"]["by_project"] == {"acme-web": 1},
)
check(
    "projects=['personal'] matches project_id None",
    query(ALICE_SERVICE, projects=["personal"])["counts"]["by_project"] == {"personal": 1},
)
check(
    "several projects are OR-ed",
    len(query(ALICE_SERVICE, projects=["acme-web", "personal"])["occurrences"]) == 2,
)
check(
    "filter[acme-web + codx] == 1 + 3",
    len(query(ALICE_SERVICE, projects=["acme-web", "codx-junior-platform"])["occurrences"]) == 4,
)

marta_query = query(MARTA_SERVICE)
marta_ids = {occurrence["event_id"] for occurrence in marta_query["occurrences"]}
check(
    "marta sees the team + workspace events only",
    marta_ids == {EVT_TEAM.id, EVT_ACME.id},
    str(marta_ids),
)
acme_for_marta = [o for o in marta_query["occurrences"] if o["event_id"] == EVT_ACME.id][0]
check(
    "an invisible membership is not leaked in calendar_ids",
    acme_for_marta["calendar_ids"] == [CAL_ALLHANDS.id],
    str(acme_for_marta["calendar_ids"]),
)
check(
    "alice sees both memberships (multi-calendar item)",
    sorted([o for o in full["occurrences"] if o["event_id"] == EVT_ACME.id][0]["calendar_ids"])
    == sorted([CAL_ALLHANDS.id, CAL_WORK.id]),
)
check(
    "read_only occurrence flag: marta cannot write All hands",
    acme_for_marta["read_only"] is True,
)

EVT_SECRET = save_event(
    title="Secret 1:1",
    calendar_ids=[CAL_TEAM.id],
    visibility="private",
    created_by="alice",
    start="2026-02-11T12:00:00Z",
    end="2026-02-11T12:30:00Z",
)
masked = [o for o in query(JORDI_SERVICE)["occurrences"] if o["event_id"] == EVT_SECRET.id]
check("private event keeps its slot for a reader", len(masked) == 1)
check("private event masks its title for a reader", bool(masked) and masked[0]["title"] == "Private")
check(
    "private event stays readable for a writer of its calendar",
    [o for o in query(MARTA_SERVICE)["occurrences"] if o["event_id"] == EVT_SECRET.id][0]["title"]
    == "Secret 1:1",
)
check(
    "private event is readable by its creator",
    [o for o in query(ALICE_SERVICE)["occurrences"] if o["event_id"] == EVT_SECRET.id][0]["title"]
    == "Secret 1:1",
)

filtered = query(MARTA_SERVICE, calendar_ids=[CAL_TEAM.id, CAL_WORK.id])
check("calendar_ids filter keeps only visible ids", len(filtered["occurrences"]) == 2)
check(
    "unknown calendar id is reported as a warning",
    any("unknown_calendar" in warning for warning in filtered["warnings"]),
)

print()
print("=== writes: §3.2 invariants and §5.3 errors ===", flush=True)
raises(
    "unknown_calendar (404)",
    "unknown_calendar",
    404,
    lambda: MARTA_SERVICE.create_event(
        EventCreate(
            calendar_ids=["cal_does_not_exist"],
            title="x",
            start="2026-02-09T09:00:00Z",
            end="2026-02-09T10:00:00Z",
        )
    ),
)
raises(
    "system-only write is read_only_calendar (409)",
    "read_only_calendar",
    409,
    lambda: ADMIN_SERVICE.create_event(
        EventCreate(
            calendar_ids=[CAL_CODX.id],
            title="x",
            start="2026-02-09T09:00:00Z",
            end="2026-02-09T10:00:00Z",
        )
    ),
)
raises(
    "reader cannot write the team calendar (403)",
    "read_only_calendar",
    403,
    lambda: JORDI_SERVICE.create_event(
        EventCreate(
            calendar_ids=[CAL_TEAM.id],
            title="x",
            start="2026-02-09T09:00:00Z",
            end="2026-02-09T10:00:00Z",
        )
    ),
)
raises(
    "end before start is rejected",
    "invalid_event",
    400,
    lambda: ALICE_SERVICE.create_event(
        EventCreate(
            calendar_ids=[CAL_WORK.id],
            title="x",
            start="2026-02-09T10:00:00Z",
            end="2026-02-09T09:00:00Z",
        )
    ),
)
raises(
    "event_too_long (400)",
    "event_too_long",
    400,
    lambda: ALICE_SERVICE.create_event(
        EventCreate(
            calendar_ids=[CAL_WORK.id],
            title="x",
            start="2026-02-01T00:00:00Z",
            end="2026-03-20T00:00:00Z",
        )
    ),
)
raises(
    "invalid_recurrence (400)",
    "invalid_recurrence",
    400,
    lambda: ALICE_SERVICE.create_event(
        EventCreate(
            calendar_ids=[CAL_WORK.id],
            title="x",
            start="2026-02-09T09:00:00Z",
            end="2026-02-09T10:00:00Z",
            recurrence=Recurrence(rrule="BYDAY=MO"),
        )
    ),
)
raises(
    "range wider than 92 days is rejected",
    "invalid_range",
    400,
    lambda: query(
        ALICE_SERVICE, **{"from": "2026-01-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"}
    ),
)

created = ALICE_SERVICE.create_event(
    EventCreate(
        calendar_ids=[CAL_WORK.id, CAL_WORK.id, CAL_PERSONAL.id],
        title="Created",
        start="2026-02-14T15:00:00+01:00",
        end="2026-02-14T16:00:00+01:00",
    )
)
check("created event is deduplicated", created.calendar_ids == [CAL_WORK.id, CAL_PERSONAL.id])
check("created event starts at revision 1", created.revision == 1)
check("created event stores UTC", created.start == "2026-02-14T14:00:00Z", created.start)
check(
    "created event inherits the calendar default reminder",
    [r.minutes_before for r in created.reminders] == [10],
)
check("created event records its creator", created.created_by == "alice")
check("created event source is native", created.source == "native")

raises(
    "revision_mismatch (409)",
    "revision_mismatch",
    409,
    lambda: ALICE_SERVICE.update_event(created.id, EventUpdate(title="stale"), expected_revision=7),
)
updated = ALICE_SERVICE.update_event(created.id, EventUpdate(title="Renamed"))
check("update increments the revision", updated.revision == 2)
check("update applies the change", updated.title == "Renamed")
check(
    "update via the body revision also works",
    ALICE_SERVICE.update_event(created.id, EventUpdate(title="Again", revision=2)).revision == 3,
)
raises(
    "update through the body revision is guarded",
    "revision_mismatch",
    409,
    lambda: ALICE_SERVICE.update_event(created.id, EventUpdate(title="x", revision=1)),
)
check("delete returns the document", ALICE_SERVICE.delete_event(created.id).id == created.id)
raises(
    "deleted event is gone (404)",
    "not_found",
    404,
    lambda: ALICE_SERVICE.get_event(created.id),
)

raises(
    "system calendar cannot be created (409)",
    "read_only_calendar",
    409,
    lambda: ADMIN_SERVICE.create_calendar(CalendarCreate(name="Fake", scope="system")),
)
raises(
    "workspace calendar needs an admin (403)",
    "read_only_calendar",
    403,
    lambda: ALICE_SERVICE.create_calendar(
        CalendarCreate(name="All hands 2", scope="workspace")
    ),
)
team_cal = ALICE_SERVICE.create_calendar(
    CalendarCreate(
        name="Team — QA",
        scope="team",
        audience={"name": "QA", "project": "codx-junior-platform", "members": ["alice", "bob"]},
    )
)
check("created calendar is owned by the caller", team_cal.owner == "alice")
check("created calendar audience keeps kind=team", team_cal.audience["kind"] == "team")
check("bob sees the new team calendar", BOB_SERVICE.can_view(team_cal))
raises(
    "a non-owner cannot update the calendar (403)",
    "read_only_calendar",
    403,
    lambda: BOB_SERVICE.update_calendar(team_cal.id, CalendarUpdate(name="hijacked")),
)
renamed = ALICE_SERVICE.update_calendar(team_cal.id, CalendarUpdate(name="Team — QA 2"))
check("owner can update the calendar", renamed.name == "Team — QA 2")
share = ALICE_SERVICE.share_payload(renamed)
check(
    "share payload lists the audience members",
    [member["user"] for member in share["members"]] == ["alice", "bob"],
    str(share["members"]),
)
raises(
    "system calendar cannot be deleted (409)",
    "read_only_calendar",
    409,
    lambda: ADMIN_SERVICE.delete_calendar(CAL_CODX.id),
)
raises(
    "calendar_in_use (409)",
    "calendar_in_use",
    409,
    lambda: ALICE_SERVICE.delete_calendar(CAL_WORK.id),
)
check("empty calendar is deleted", ALICE_SERVICE.delete_calendar(team_cal.id).id == team_cal.id)

MOVING = ALICE_SERVICE.create_calendar(CalendarCreate(name="Moving"))
STORE.save_event(
    CalendarEvent(
        calendar_ids=[MOVING.id],
        title="To be moved",
        start="2026-02-20T09:00:00Z",
        end="2026-02-20T10:00:00Z",
    )
)
ALICE_SERVICE.delete_calendar(MOVING.id, move_to=CAL_PERSONAL.id)
check(
    "move_to rescues the events",
    all(
        event.calendar_ids == [CAL_PERSONAL.id]
        for event in STORE.list_events_month("2026-02")
        if event.title == "To be moved"
    ),
)

print()
print("=== prefs / projects / snooze / quick add ===", flush=True)
DEFAULT_PREFS = ALICE_SERVICE.get_prefs()
check(
    "prefs defaults",
    DEFAULT_PREFS == {"hidden_calendars": [], "order": {}, "week_start": "monday"},
)
check("initial visibility hides the ICS calendar", CAL_ICS.id in ALICE_SERVICE.hidden_calendar_ids())
saved = ALICE_SERVICE.save_prefs({"hidden_calendars": [CAL_ICS.id], "week_start": "sunday"})
check("prefs round-trip", saved["week_start"] == "sunday")
check("prefs are per user", MARTA_SERVICE.get_prefs()["week_start"] == "monday")
raises(
    "invalid week_start is rejected",
    "invalid_event",
    400,
    lambda: ALICE_SERVICE.save_prefs({"week_start": "friday"}),
)

check("projects() never raises", isinstance(ALICE_SERVICE.projects(), list))
snoozed = ALICE_SERVICE.snooze(EVT_WORK.id, 10)
check("snooze answers but persists nothing (M2)", snoozed["persisted"] is False)
raises(
    "snooze needs a positive delay",
    "invalid_event",
    400,
    lambda: ALICE_SERVICE.snooze(EVT_WORK.id, 0),
)
draft = ALICE_SERVICE.quick_add("Lunch Ana tom 13:00 1h @Café")["draft"]
check("quick add parses the location", draft["location"] == "Café", str(draft["location"]))
check("quick add parses the time", draft["start"].endswith("13:00:00Z"), draft["start"])
check("quick add parses the duration", draft["end"].endswith("14:00:00Z"), draft["end"])
check(
    "quick add picks a writable calendar",
    draft["calendar_ids"] == [CAL_WORK.id],
    str(draft["calendar_ids"]),
)
check("quick add keeps the title", draft["title"] == "Lunch Ana", str(draft["title"]))
check("quick add does not persist", ALICE_SERVICE.quick_add("Call")["persisted"] is False)
check("orphans are reported", isinstance(ALICE_SERVICE.orphan_events(), list))

print()
print("ROOT:", ROOT)
print(f"PASSED: {len(PASSED)}  FAILED: {len(FAILURES)}")
for _failure in FAILURES:
    print(f"  FAILED {_failure}")

# The socket.io emitter thread started on import is not a daemon thread: without
# an explicit exit the interpreter would never return from this script.
sys.stdout.flush()
os._exit(0 if not FAILURES else 1)
