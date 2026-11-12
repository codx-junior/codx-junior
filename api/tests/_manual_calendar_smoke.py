"""
Manual smoke check of the calendar foundation (M0).

The pytest module `test_calendar_store.py` covers the same ground per case; this
script is the single end-to-end check the milestone asks for: point
``CODX_JUNIOR_CALENDAR_PATH`` at a temporary folder, save and reload a calendar
and an event, confirm the event lands in the shard of its start month, and
confirm a corrupt shard degrades to ``[]`` instead of raising.

It never touches the real installation folder (``/home/codx-junior/calendar``):
the root is randomised and injected through the same environment variable the
container uses.

Run with: .venv_codx_junior_api/bin/python tests/_manual_calendar_smoke.py
"""

import json
import os
import sys
import tempfile

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_ROOT not in sys.path:
    sys.path.insert(0, API_ROOT)

from codx.junior.calendar.model import Calendar, CalendarEvent  # noqa: E402
from codx.junior.calendar.store import (  # noqa: E402
    CALENDAR_PATH_ENV,
    CalendarStore,
    get_calendar_root,
)
from codx.junior.db import Chat  # noqa: E402


def check_chat_fields() -> None:
    """The additive Chat fields must exist and be defaulted (db still imports)."""
    chat = Chat()
    print(
        "Chat defaults  : calendar_ids={} scheduled_start={} scheduled_end={} "
        "all_day={}".format(
            chat.calendar_ids,
            chat.scheduled_start,
            chat.scheduled_end,
            chat.all_day,
        )
    )
    assert chat.calendar_ids == []
    assert chat.scheduled_start is None
    assert chat.scheduled_end is None
    assert chat.all_day is False


def main() -> int:
    """Run the round-trip and return the process exit code."""
    check_chat_fields()
    root = tempfile.mkdtemp(prefix="codx-calendar-smoke-")
    os.environ[CALENDAR_PATH_ENV] = root
    assert get_calendar_root() == root, get_calendar_root()

    store = CalendarStore()  # root comes from the environment variable
    calendar = store.save_calendar(Calendar(name="Work", owner="alice"))
    event = store.save_event(
        CalendarEvent(
            calendar_ids=[calendar.id],
            title="Spec review",
            start="2026-02-09T09:00:00Z",
            end="2026-02-09T10:00:00Z",
        )
    )

    # A fresh instance proves both documents were really written to disk.
    reloaded_calendar = CalendarStore(root).get_calendar(calendar.id)
    reloaded_event = CalendarStore(root).get_event(event.id)
    shards = sorted(os.listdir(store.events_dir()))

    print(f"root           : {root}")
    print(f"calendar       : {reloaded_calendar.name} ({reloaded_calendar.id})")
    print(f"event          : {reloaded_event.title} -> shard {reloaded_event.shard_month}")
    print(f"shards on disk : {shards}")
    assert shards == ["2026-02.json"], shards
    assert reloaded_event.shard_month == "2026-02"

    # A corrupt shard must be tolerated: the agenda keeps rendering.
    shard_path = os.path.join(store.events_dir(), "2026-02.json")
    with open(shard_path, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")
    corrupt = store.list_events_month("2026-02")
    print(f"corrupt shard  : {corrupt!r} (no exception)")
    assert corrupt == []

    # ... and the same file becomes readable again once repaired.
    with open(shard_path, "w", encoding="utf-8") as handle:
        json.dump([reloaded_event.model_dump()], handle, indent=2)
    repaired = store.list_events_month("2026-02")
    print(f"repaired shard : {len(repaired)} event(s) -> {repaired[0].title}")
    assert len(repaired) == 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
