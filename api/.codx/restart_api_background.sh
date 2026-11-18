#!/bin/sh
# Runs INSIDE codx-junior-api: restarts the background worker container.
# The docker client lives in the api container, the target is the background
# container, so the restart request is never cancelled by its own kill.
BASE=/home/codx-junior-projects/codx-junior/codx-junior-platform/api/.codx
sleep 10
echo "$(date -Is) restarting codx-junior-api-background" >>"$BASE/restart_api.log"
docker restart -t 30 codx-junior-api-background >>"$BASE/restart_api.log" 2>&1
echo "$(date -Is) background restarted" >>"$BASE/restart_api.log"
