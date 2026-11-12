#!/usr/bin/env bash
# One-shot deferred restart of codx-junior-api.
#
# Why: the API process started at 2026-11-12T18:18:16Z, BEFORE
# api/codx/junior/logs/ (docker log sources, insights) existed, and uvicorn
# runs without --reload. The code on disk is complete and tested; only the
# process is stale, which is why /api/logs/explorer/sources lists files but no
# containers and /insights answers 404.
#
# The delay gives the in-flight chat answer time to reach the browser before
# the process is replaced.
set -u
LOG=/tmp/codx-logexplorer-restart.log
{
  echo "=== $(date -Is) scheduled restart of codx-junior-api (log explorer fixes) ==="
  sleep 90
  echo "=== $(date -Is) restarting codx-junior-api ==="
  /usr/bin/sudo -n /usr/bin/docker restart codx-junior-api
  echo "=== $(date -Is) restart finished with exit $? ==="
} >> "$LOG" 2>&1
rm -f "$0"
