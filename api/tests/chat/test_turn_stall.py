"""
Tests for the DEAD-turn (stall) detection.

A turn that stops making progress never releases its ``flock``: the process is
alive, ``is_owned`` is True and the "owner died" heuristic of the recovery
worker cannot see it. The heartbeat is what makes that state observable, so the
server can stop the execution and restart it instead of leaving the message on
"Processing request, please wait..." forever.

Run with::

    cd api && pytest tests/chat/test_turn_stall.py -q
"""

from types import SimpleNamespace

from codx.junior.chat.turn_store import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    get_turn_store,
)
from codx.junior.chat.turn_watch import turn_snapshot

# A heartbeat old enough to be older than any sane stall window.
HUNG_HEARTBEAT = "2020-01-01T00:00:00+00:00"


def _chat(chat_id="chat-1"):
    return SimpleNamespace(id=chat_id, owner_project_id="project-1")


def test_a_running_turn_with_fresh_progress_is_not_stale(tmp_path):
    store = get_turn_store(str(tmp_path))
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)

    # Marking the turn running stamps the first heartbeat.
    assert bool(record.heartbeat_at) is True
    assert store.is_stale(record) is False
    assert store.activity_age(record) < 5

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")


def test_a_turn_without_progress_is_reported_as_stalled(tmp_path):
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)

    # Simulate a hung execution: the lock is still held by this process (which
    # is exactly the case the flock cannot detect) but nothing progresses.
    record.heartbeat_at = HUNG_HEARTBEAT
    store.save(record)

    assert store.is_stale(record) is True

    snapshot = turn_snapshot(codx_path)
    assert snapshot["stale_seconds"] > 0
    turns = {turn["chat_id"]: turn for turn in snapshot["turns"]}
    assert "chat-1" in turns
    turn = turns["chat-1"]
    assert turn["stalled"] is True
    # The process is alive: this is the message that "looks like it is working".
    assert turn["alive"] is True
    assert turn["heartbeat_age"] > 60

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")
    assert turn_snapshot(codx_path)["turns"] == []


def test_a_progress_tick_brings_a_stalled_turn_back_to_life(tmp_path):
    store = get_turn_store(str(tmp_path))
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)

    record.heartbeat_at = HUNG_HEARTBEAT
    store.save(record)
    assert store.is_stale(record) is True

    # A streamed chunk / tool event is progress: the heartbeat advances.
    assert store.touch(record, force=True) is True
    assert store.is_stale(record) is False

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")


def test_stall_detection_can_be_disabled(tmp_path):
    store = get_turn_store(str(tmp_path))
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)
    record.heartbeat_at = HUNG_HEARTBEAT
    store.save(record)

    # 0 disables the detector (a very long tool call is not a dead turn).
    assert store.is_stale(record, stale_seconds=0) is False

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")


def test_wall_clock_is_advisory_and_disabled_by_default(tmp_path):
    """Elapsed time is a diagnostic: it never stops or restarts a turn."""
    store = get_turn_store(str(tmp_path))
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)

    # A fresh heartbeat: the STALL detector sees nothing wrong.
    assert store.is_stale(record) is False
    # The wall-clock flag is OFF by default, however long the turn runs.
    assert store.max_seconds == 0
    assert store.is_expired(record) is False

    record.started_at = HUNG_HEARTBEAT
    store.save(record)

    assert store.turn_age(record) > 60
    assert store.is_expired(record) is False

    # An operator may enable the FLAG for diagnostics; it still changes nothing
    # for the turn, it only shows up in the snapshot.
    store.max_seconds = 600.0
    assert store.is_expired(record) is True
    assert store.describe(record)["expired"] is True
    assert store.is_expired(record, max_seconds=0) is False

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")


def test_recovery_never_stops_a_turn_by_wall_clock(tmp_path, monkeypatch):
    """A turn that keeps working is doing the work asked for: leave it alone."""
    from codx.junior.chat.turn_recovery import ChatTurnRecovery

    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(_chat("chat-1"))
    assert record is not None
    store.mark_running(record)
    record.started_at = HUNG_HEARTBEAT
    store.save(record)
    # The turn is old enough to flag, but its process still owns it and its
    # heartbeat is fresh: it is a long turn, not a dead one.
    store.max_seconds = 600.0

    repaired = []
    worker = ChatTurnRecovery(interval_seconds=60)
    monkeypatch.setattr(
        worker,
        "_projects",
        lambda: [SimpleNamespace(codx_path=codx_path, project_id="project-1")],
    )
    monkeypatch.setattr(
        worker,
        "_repair_chat",
        lambda *args, **kwargs: repaired.append(args[1].turn_id),
    )
    monkeypatch.setattr(worker, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_audit", lambda *args, **kwargs: None)

    result = worker.recover_once()

    assert result["failed"] == []
    assert result["recovered"] == []
    assert repaired == []
    assert [item.turn_id for item in store.list_active()] == [record.turn_id]
    assert record.status == STATUS_RUNNING

    store.finalize(record, STATUS_CANCELLED, error="test cleanup")
