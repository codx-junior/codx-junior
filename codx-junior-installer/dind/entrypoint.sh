#!/bin/bash
# =============================================================================
# codx-junior — DIND entrypoint
#
#   1. start the nested dockerd;
#   2. wait for it;
#   3. run the SAME installer with --container, so the inner stack is defined by
#      the same runtime compose files as a host install;
#   4. keep the wrapper alive (a failure must leave the container inspectable
#      instead of exiting and losing the nested state).
#
# No Coolify, no workspaces, no local model sidecars: none of them are part
# of the product runtime.
# =============================================================================
set -uo pipefail

MODE="${CODX_MODE:-server}"
DOMAIN="${CODX_JUNIOR_DOMAIN:-localhost}"

log()  { printf '[dind] %s\n' "$*"; }
warn() { printf '[dind] WARNING: %s\n' "$*" >&2; }

mkdir -p /data/config /data/projects /data/settings /data/analytics /data/traefik /var/log

log "Starting the nested Docker daemon"
rm -f /var/run/docker.pid /var/run/docker/containerd/containerd.pid 2>/dev/null || true
dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 &
DOCKERD_PID=$!

log "Waiting for the nested daemon"
for _ in $(seq 1 120); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    tail -n 1 /var/log/dockerd.log 2>/dev/null || true
    sleep 1
done
if ! docker info >/dev/null 2>&1; then
    warn "the nested daemon did not start; see /var/log/dockerd.log"
    tail -n 40 /var/log/dockerd.log || true
    # Stay alive: the operator can docker exec into the container.
    while true; do sleep 60; done
fi
log "Nested daemon is ready"

ARGS=(
    --container
    --mode "${MODE}"
    --traefik "${CODX_TRAEFIK_MODE:-master}"
    --home /data
    --data-dir /data
    --projects /data/projects
    --env-file /data/config/codx-junior.env
    --instance codx-junior
    --no-systemd
    --coworking
    --skip-tls-wait
)
[[ -n "${DOMAIN}" ]] && ARGS+=(--domain "${DOMAIN}")
[[ -n "${CODX_ACME_EMAIL:-}" ]] && ARGS+=(--acme-email "${CODX_ACME_EMAIL}")
[[ -n "${CODX_TRUSTED_PROXIES:-}" ]] && ARGS+=(--trusted-proxy "${CODX_TRUSTED_PROXIES}")
[[ "${CODX_ACME_CA:-prod}" == "staging" ]] && ARGS+=(--acme-ca staging)
[[ "${CODX_NO_PULL:-0}" == "1" ]] && ARGS+=(--no-pull)

# The outer --dev flag is a DIND-level debug switch. The inner installer must
# NOT enter its source-mount/Vite dev path; it only needs DEBUG=1 for the app.
export CODX_JUNIOR_DEV=0
export CODX_JUNIOR_DEBUG="${CODX_JUNIOR_DEBUG:-1}"

INNER_LOG=/var/log/inner-install.log
log "Installing the inner codx-junior (mode: ${MODE})"
if ! /opt/codx-junior/install.sh "${ARGS[@]}" 2>&1 | tee "${INNER_LOG}"; then
    warn "the inner installation failed; the container stays up for inspection"
    warn "  inner installer log: ${INNER_LOG}"
    warn "  docker logs:"
    tail -n 80 "${INNER_LOG}" || true
    warn "  nested containers:"
    docker ps -a || true
    warn "  nested daemon log:"
    tail -n 40 /var/log/dockerd.log || true
fi

log "Inner stack started. Open the wrapper logs for the nested daemon."
# Keep PID 1 alive and report the inner state periodically without spamming.
while true; do
    if ! docker info >/dev/null 2>&1; then
        warn "the nested daemon is down"
        tail -n 20 /var/log/dockerd.log || true
    fi
    sleep 30
done
