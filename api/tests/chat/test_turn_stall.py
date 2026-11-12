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

from codx.junior.chat.turn_store import STATUS_CANCELLED, get_turn_store
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
