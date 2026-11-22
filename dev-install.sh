#!/usr/bin/env bash
# =============================================================================
# codx-junior — DEV installer
#
# One command, from a checkout, to a running instance with the checkout mounted
# live. This is the development counterpart of ./install.sh and it is deliberately
# the opposite of it: no git clone, no Docker install, no TLS/Caddy, no domain,
# no firewall, no cloud-init, no admin bootstrap and no operations CLI. It builds
# the current tree, starts the single container and stops there.
#
#   ./dev-install.sh --provider deepseek --key sk-xxxxxxxx
#   ./dev-install.sh --reload      # uvicorn --reload on api/ changes
#   ./dev-install.sh --logs | --down | --purge
#
# Flags (environment equivalent in parentheses):
#   --provider NAME   (CODX_JUNIOR_AI_PROVIDER)  required, unless already in .env
#   --key KEY         (CODX_JUNIOR_AI_API_KEY)   required
#   --url URL         (CODX_JUNIOR_AI_API_URL)   only for provider `custom`
#   --model ID        (CODX_JUNIOR_AI_MODEL)     override the preset model
#   --port PORT       (CODX_JUNIOR_WEB_PORT)     default: 19981
#   --projects PATH   (CODX_JUNIOR_PROJECTS_PATH) default: demo/projects
#   --build           rebuild the image even if codxjuniorai/codx-junior:dev exists
#   --reload          uvicorn --reload (off by default: it interrupts agent turns)
#   --no-wait         print the URL without waiting for the health check
#   --down            stop the stack, keep projects and settings
#   --purge           stop and delete the settings/analytics volumes
#   --logs            follow the container logs
#   -h, --help        this help
#
# Requirements: docker and the compose v2 plugin. Everything else is in the
# checkout. Settings live in codx-junior-installer/demo/.env (mode 600).
# =============================================================================
set -euo pipefail
# Everything this script creates may contain the API key: keep it private.
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="${ROOT}/codx-junior-installer/demo"
ENV_FILE="${DEMO}/.env"
EXAMPLE_FILE="${DEMO}/.env.example"
CONTAINER="codx-junior-demo"
IMAGE="codxjuniorai/codx-junior:dev"

PROVIDER="${CODX_JUNIOR_AI_PROVIDER:-}"
API_KEY="${CODX_JUNIOR_AI_API_KEY:-}"
API_URL="${CODX_JUNIOR_AI_API_URL:-}"
MODEL="${CODX_JUNIOR_AI_MODEL:-}"
PORT="${CODX_JUNIOR_WEB_PORT:-}"
PROJECTS="${CODX_JUNIOR_PROJECTS_PATH:-}"
RELOAD="${CODX_JUNIOR_API_RELOAD:-}"

DO_BUILD=0
DO_DOWN=0
DO_PURGE=0
DO_LOGS=0
NO_WAIT=0

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

need() {
    [[ $# -ge 2 && -n "${2:-}" ]] || die "option $1 needs a value (see --help)"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider|-p)  need "$@"; PROVIDER="$2"; shift 2 ;;
        --key|-k)       need "$@"; API_KEY="$2"; shift 2 ;;
        --url|-u)       need "$@"; API_URL="$2"; shift 2 ;;
        --model|-m)     need "$@"; MODEL="$2"; shift 2 ;;
        --port|-P)      need "$@"; PORT="$2"; shift 2 ;;
        --projects)     need "$@"; PROJECTS="$2"; shift 2 ;;
        --build)        DO_BUILD=1; shift ;;
        --reload)       RELOAD=1; shift ;;
        --no-wait)      NO_WAIT=1; shift ;;
        --down)         DO_DOWN=1; shift ;;
        --purge)        DO_PURGE=1; shift ;;
        --logs)         DO_LOGS=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              usage >&2; die "unknown option: $1" ;;
    esac
done

# --- preflight: only what a developer workstation actually needs -------------
[[ -d "${DEMO}" ]] || die "not a codx-junior checkout: ${DEMO} is missing"
command -v docker >/dev/null 2>&1 || die "docker is not installed (https://docs.docker.com/get-docker/)"
docker info >/dev/null 2>&1 || die "the docker daemon is not running"
docker compose version >/dev/null 2>&1 || die "the docker compose v2 plugin is required"

compose() {
    ( cd "${DEMO}" && docker compose -f docker-compose.yaml -f docker-compose.dev.yaml "$@" )
}

# --- settings file -----------------------------------------------------------
[[ -f "${ENV_FILE}" ]] || { cp "${EXAMPLE_FILE}" "${ENV_FILE}"; log "Created ${ENV_FILE#"${ROOT}/"} from .env.example"; }
# umask already creates it 0600; repair a file from an older run when the
# filesystem supports chmod.
chmod 600 "${ENV_FILE}" 2>/dev/null || warn "could not chmod 600 ${ENV_FILE}"

set_env_var() {
    local key="$1" value="$2"
    if grep -Eq "^#?[[:space:]]*${key}=" "${ENV_FILE}"; then
        awk -v k="${key}" -v v="${value}" '
            $0 ~ "^#?[[:space:]]*" k "=" && !done { print k "=" v; done=1; next }
            { print }
        ' "${ENV_FILE}" > "${ENV_FILE}.tmp"
        mv "${ENV_FILE}.tmp" "${ENV_FILE}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    fi
}

get_env_var() {
    grep -E "^$1=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true
}

if [[ -n "${PROVIDER}" ]]; then set_env_var CODX_JUNIOR_AI_PROVIDER "${PROVIDER}"; fi
if [[ -n "${API_KEY}" ]];  then set_env_var CODX_JUNIOR_AI_API_KEY "${API_KEY}"; fi
if [[ -n "${API_URL}" ]];  then set_env_var CODX_JUNIOR_AI_API_URL "${API_URL}"; fi
if [[ -n "${MODEL}" ]];    then set_env_var CODX_JUNIOR_AI_MODEL "${MODEL}"; fi
if [[ -n "${PORT}" ]];     then set_env_var CODX_JUNIOR_WEB_PORT "${PORT}"; fi
if [[ -n "${PROJECTS}" ]]; then set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}"; fi
if [[ -n "${RELOAD}" ]];   then set_env_var CODX_JUNIOR_API_RELOAD "${RELOAD}"; fi

# --- lifecycle actions need no AI provider ----------------------------------
if [[ "${DO_PURGE}" -eq 1 ]]; then
    warn "Deleting the settings and analytics volumes (projects are kept)"
    compose down -v --remove-orphans
    exit 0
fi
if [[ "${DO_DOWN}" -eq 1 ]]; then
    log "Stopping codx-junior (projects and settings are kept)"
    compose down --remove-orphans
    exit 0
fi
if [[ "${DO_LOGS}" -eq 1 ]]; then
    compose logs -f --tail 100
    exit 0
fi

# --- required settings -------------------------------------------------------
FINAL_PROVIDER="$(get_env_var CODX_JUNIOR_AI_PROVIDER)"
FINAL_KEY="$(get_env_var CODX_JUNIOR_AI_API_KEY)"
FINAL_URL="$(get_env_var CODX_JUNIOR_AI_API_URL)"
WEB_PORT="$(get_env_var CODX_JUNIOR_WEB_PORT)"; WEB_PORT="${WEB_PORT:-19981}"
PROJECTS_PATH="$(get_env_var CODX_JUNIOR_PROJECTS_PATH)"; PROJECTS_PATH="${PROJECTS_PATH:-./projects}"
FINAL_RELOAD="$(get_env_var CODX_JUNIOR_API_RELOAD)"; FINAL_RELOAD="${FINAL_RELOAD:-0}"

if [[ -z "${FINAL_PROVIDER}" || -z "${FINAL_KEY}" ]]; then
    cat >&2 <<MSG
ERROR: an AI provider is required before codx-junior can chat.

  ./dev-install.sh --provider deepseek --key sk-xxxxxxxx
  ./dev-install.sh --provider openai   --key sk-xxxxxxxx

Providers: openai, deepseek, gemini, openrouter, mistral, groq, custom.
Or edit ${ENV_FILE#"${ROOT}/"} directly.
MSG
    exit 1
fi
if [[ "${FINAL_PROVIDER}" == "custom" && -z "${FINAL_URL}" ]]; then
    die "provider 'custom' needs --url (an OpenAI-compatible endpoint ending in /v1)"
fi

# --- project folder ----------------------------------------------------------
case "${PROJECTS_PATH}" in
    /*) ;;
    *)  PROJECTS_PATH="${DEMO}/${PROJECTS_PATH}" ;;
esac
mkdir -p "${PROJECTS_PATH}"
PROJECTS_PATH="$(cd "${PROJECTS_PATH}" && pwd)"
set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS_PATH}"

# --- image: build once, reuse afterwards ------------------------------------
# `up.sh --dev` (and this script) tag the local build :dev. The overlay mounts
# the checkout, so a code change needs no rebuild; only Dockerfile/pyproject
# changes do, via --build.
export CODX_JUNIOR_IMAGE="${IMAGE}"
export CODX_JUNIOR_BUILD_VERSION="${CODX_JUNIOR_BUILD_VERSION:-$(git -C "${ROOT}" describe --tags --always 2>/dev/null || echo unknown)}"
export CODX_JUNIOR_BUILD_COMMIT="${CODX_JUNIOR_BUILD_COMMIT:-$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
export CODX_JUNIOR_BUILD_REF="${CODX_JUNIOR_BUILD_VERSION}"

if [[ "${DO_BUILD}" -eq 1 ]] || ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    log "Building ${IMAGE} (first run: a few minutes)"
    compose build
fi

# --- start -------------------------------------------------------------------
log "Starting codx-junior from ${ROOT}"
compose up -d --remove-orphans

if [[ "${NO_WAIT}" -eq 0 ]]; then
    log "Waiting for the API to become healthy..."
    STATUS="starting"
    for _ in $(seq 1 90); do
        STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}' "${CONTAINER}" 2>/dev/null || echo missing)"
        [[ "${STATUS}" == "healthy" ]] && break
        if [[ "${STATUS}" == "unhealthy" || "${STATUS}" == "exited" || "${STATUS}" == "missing" ]]; then
            compose logs --tail 60 || true
            die "codx-junior did not start (${STATUS}). Logs above."
        fi
        sleep 2
    done
    if [[ "${STATUS}" != "healthy" ]]; then
        compose logs --tail 60 || true
        die "health check timeout. Use --logs to follow the API log."
    fi
fi

# --- apply the provider once the API is up -----------------------------------
log "Applying the AI provider configuration..."
docker exec "${CONTAINER}" \
    curl -fsS -X POST http://127.0.0.1:19980/api/bootstrap/apply >/dev/null 2>&1 \
    || warn "could not apply the provider automatically (see --logs)"
SUMMARY="$(docker exec "${CONTAINER}" curl -fsS http://127.0.0.1:19980/api/bootstrap 2>/dev/null || true)"
if [[ -n "${SUMMARY}" ]]; then
    printf '%s\n' "${SUMMARY}"
fi

cat <<DONE

=============================================================================
 codx-junior dev is running

   Web UI  : http://localhost:${WEB_PORT}
   Projects: ${PROJECTS_PATH}
   Settings: ${ENV_FILE#"${ROOT}/"}
   Source  : ${ROOT} (mounted live; reload: ${FINAL_RELOAD})

   ./dev-install.sh --logs      follow the container logs
   ./dev-install.sh --reload    restart with uvicorn --reload
   ./dev-install.sh --build     rebuild after Dockerfile/pyproject changes
   ./dev-install.sh --down      stop (projects and settings are kept)
   ./dev-install.sh --purge     stop and delete the settings/analytics volumes
=============================================================================
DONE
