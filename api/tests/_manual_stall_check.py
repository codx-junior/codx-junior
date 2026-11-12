"""
Manual runtime check of the DEAD-turn (stall) detection.

Runs the same assertions as ``tests/chat/test_turn_stall.py`` without pytest, so
it can be executed inside the API container (which ships no pytest):

    .venv/bin/python tests/_manual_stall_check.py

Prints one line per check and exits non-zero on the first failure.

NOTE: importing the API package starts the socket event-queue thread
(``session_channel.py``) as a NON-daemon thread, so this script ends with
``os._exit`` — otherwise the interpreter would wait for it forever.

Made with ❤️ by codx-junior
"""

import os
import sys
import tempfile
from types import SimpleNamespace

# Run from anywhere: the API package root must be importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codx.junior.chat.turn_store import STATUS_CANCELLED, get_turn_store
from codx.junior.chat.turn_watch import publish_turn_states, turn_snapshot

HUNG_HEARTBEAT = "2020-01-01T00:00:00+00:00"


def _chat(chat_id="chat-1"):
    return SimpleNamespace(id=chat_id, owner_project_id="project-1")


def check(label, condition):
    """Print and enforce one assertion."""
    print(f"{'OK  ' if condition else 'FAIL'} {label}")
    sys.stdout.flush()
    if not condition:
        os._exit(1)


def main():
    """Run every check."""
    with tempfile.TemporaryDirectory() as tmp_path:
        store = get_turn_store(tmp_path)
        record = store.begin(_chat("chat-1"))
        check("a turn record is created", record is not None)
        store.mark_running(record)
        check("mark_running stamps the heartbeat", bool(record.heartbeat_at))
        check("a working turn is not stale", store.is_stale(record) is False)
        check("its age is fresh", store.activity_age(record) < 5)

        # A hung turn: lock still held (same process), no progress at all.
        record.heartbeat_at = HUNG_HEARTBEAT
        store.save(record)
        check("a turn without progress is stale", store.is_stale(record) is True)

        snapshot = turn_snapshot(tmp_path)
        turn = {item["chat_id"]: item for item in snapshot["turns"]}["chat-1"]
        check("the snapshot reports the turn as stalled", turn["stalled"] is True)
        check("the owner process is still alive (alive=True)", turn["alive"] is True)
        check("the heartbeat age is reported", turn["heartbeat_age"] > 60)
        check("the stall window is exposed", snapshot["stale_seconds"] > 0)
        # The socket broadcast (`codx-junior-chat-turns`) is queued, never raised.
        check(
            "the state is broadcast over socket.io",
            publish_turn_states(tmp_path, force=True) is True,
        )

        # A streamed chunk / tool event is progress again.
        check("touch persists a heartbeat", store.touch(record, force=True) is True)
        check("the turn is alive again", store.is_stale(record) is False)
        check(
            "stall detection can be disabled",
            store.is_stale(record, stale_seconds=0) is False,
        )

        store.finalize(record, STATUS_CANCELLED, error="check cleanup")
        check(
            "a finished turn is gone from the snapshot",
            turn_snapshot(tmp_path)["turns"] == [],
        )

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    # The API package starts a non-daemon thread on import: leave immediately.
    os._exit(exit_code)
