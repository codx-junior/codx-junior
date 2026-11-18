#!/bin/sh
# Delayed restart of the codx-junior api container.
# Runs INSIDE codx-junior-api-background (the client survives the api restart),
# waits so the chat answer can stream, then hands the background restart to a
# process running inside the freshly restarted api container.
BASE=/home/codx-junior-projects/codx-junior/codx-junior-platform/api/.codx
sleep 30
echo "$(date -Is) restarting codx-junior-api" >>"$BASE/restart_api.log"
docker restart -t 30 codx-junior-api >>"$BASE/restart_api.log" 2>&1
# wait until the api is accepting execs again (max ~5 min)
i=0
while [ $i -lt 60 ] ; do
  if docker exec codx-junior-api true >>"$BASE/restart_api.log" 2>&1 ; then
    break
  fi
  i=$((i + 1))
  sleep 5
done
echo "$(date -Is) api exec ready after $i tries" >>"$BASE/restart_api.log"
docker exec codx-junior-api setsid -f sh "$BASE/restart_api_background.sh" >>"$BASE/restart_api.log" 2>&1
echo "$(date -Is) done" >>"$BASE/restart_api.log"
