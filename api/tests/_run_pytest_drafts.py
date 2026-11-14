"""Temporary diagnostic runner: run pytest and always exit (log to /tmp).

Some module imported by the suite leaves a non-daemon thread alive, so the
pytest process never returns to the shell and its output is never flushed.
This wrapper duplicates stdout/stderr into a log file and force-exits.
"""

import os
import sys

LOG = "/tmp/pytest_drafts.log"

fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)

import threading  # noqa: E402

import pytest  # noqa: E402

code = pytest.main(["-q", "/home/codx-junior/codx-junior/api/tests/test_chat_drafts.py"])

print("\n--- lingering threads (why the plain CLI never exits) ---")
for thread in threading.enumerate():
    print(f"{thread.name!r} daemon={thread.daemon} alive={thread.is_alive()}")

sys.stdout.flush()
sys.stderr.flush()
os._exit(int(code))
