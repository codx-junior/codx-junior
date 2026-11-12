"""Restart the API server a few minutes from now, detached from this turn.

The API runs `uvicorn --workers 8` without --reload, so the endpoints added to
codx/junior/api/files.py (copy / move / archive / extract) only exist after the
process is restarted. Restarting it while a chat turn is running would kill the
turn, so the restart is delegated to a detached process that waits first.

The container has `restart: unless-stopped`, so killing run_api.sh (PID 1) makes
Docker start it again with the current code - the same thing restar_api.sh does.
"""
import subprocess

DELAY_SECONDS = 180
COMMAND = f"sleep {DELAY_SECONDS} && kill -9 $(pgrep -f run_api.sh)"

subprocess.Popen(
    ["sh", "-c", COMMAND],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)

print(f"API restart scheduled in {DELAY_SECONDS}s: {COMMAND}")
