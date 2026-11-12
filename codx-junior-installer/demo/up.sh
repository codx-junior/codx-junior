#!/usr/bin/env bash
# =============================================================================
# codx-junior demo — one command to a working instance
#
#   ./up.sh                                  # reads .env (created from .env.example)
#   ./up.sh --provider deepseek --key sk-...  # configures .env for you
#   ./up.sh --knowledge                       # also start Milvus (RAG search)
#   ./up.sh --build                           # build the image locally (developers)
#
# What it does: validate the settings you must pass, pull the image, start the
# stack, wait until the API reports healthy and apply the AI provider
# configuration. Idempotent: running it again just restarts the stack.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ENV_FILE="${SCRIPT_DIR}/.env"
EXAMPLE_FILE="${SCRIPT_DIR}/.env.example"
CONTAINER="codx-junior-demo"

PROVIDER=""
API_KEY=""
PORT=""
PROJECTS=""
DO_BUILD=0
KNOWLEDGE=0

usage() {
    cat <<USAGE
codx-junior demo installer

Usage: ./up.sh [options]

  --provider <name>   which provider preset to use; it also defines the models
                      (openai | deepseek | gemini | openrouter | mistral | groq | ollama | custom)
  --key <api-key>     API key of the provider (also read from CODX_JUNIOR_AI_API_KEY)
  --list-providers    Show every provider preset with the models it configures, then exit
  --url <base-url>    OpenAI-compatible endpoint (required for `custom`)
  --model <model-id>  Override the default chat model of the provider
  --port <port>       Web port (default 19981)
  --projects <path>   Host folder that holds your projects (default ./projects)
  --knowledge         Also start Milvus, needed by knowledge/RAG search
  --build             Build the image from source instead of pulling it
  --no-wait           Do not wait for the health check
  -h, --help          This help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider) PROVIDER="${2:-}"; shift 2 ;;
        --key)      API_KEY="${2:-}"; shift 2 ;;
        --url)      CODX_JUNIOR_AI_API_URL="${2:-}"; shift 2 ;;
        --model)    MODEL="${2:-}"; shift 2 ;;
        --port)     PORT="${2:-}"; shift 2 ;;
        --projects) PROJECTS="${2:-}"; shift 2 ;;
        --knowledge) KNOWLEDGE=1; shift ;;
        --build)    DO_BUILD=1; shift ;;
        --no-wait)  NO_WAIT=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

NO_WAIT="${NO_WAIT:-0}"
MODEL="${MODEL:-}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- docker ----------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker is not installed (https://docs.docker.com/get-docker/)."
docker info >/dev/null 2>&1 || die "The Docker daemon is not running."
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "docker compose (v2) is required."
fi

# --- .env helpers ----------------------------------------------------------
set_env_var() {
    # Replace the first (possibly commented) KEY=... line, else append.
    local key="$1" value="$2" file="$3"
    if grep -Eq "^#?[[:space:]]*${key}=" "$file"; then
        awk -v k="$key" -v v="$value" '
            $0 ~ "^#?[[:space:]]*" k "=" && !done { print k "=" v; done=1; next }
            { print }
        ' "$file" > "${file}.tmp"
        mv "${file}.tmp" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

get_env_var() {
    local key="$1" file="$2"
    [ -f "$file" ] || return 0
    grep -E "^${key}=" "$file" | tail -n1 | cut -d= -f2- || true
}

[ -f "${ENV_FILE}" ] || { cp "${EXAMPLE_FILE}" "${ENV_FILE}"; log "Created .env from .env.example"; }
chmod 600 "${ENV_FILE}"

# --- settings: flags win, then .env, then the environment ------------------
[[ -n "${PROVIDER}" ]] && set_env_var CODX_JUNIOR_AI_PROVIDER "${PROVIDER}" "${ENV_FILE}"
[[ -n "${API_KEY}"  ]] && set_env_var CODX_JUNIOR_AI_API_KEY  "${API_KEY}"  "${ENV_FILE}"
[[ -n "${CODX_JUNIOR_AI_API_URL:-}" ]] && set_env_var CODX_JUNIOR_AI_API_URL "${CODX_JUNIOR_AI_API_URL}" "${ENV_FILE}"
[[ -n "${MODEL}"    ]] && set_env_var CODX_JUNIOR_AI_MODEL    "${MODEL}"    "${ENV_FILE}"
[[ -n "${PORT}"     ]] && set_env_var CODX_JUNIOR_WEB_PORT    "${PORT}"     "${ENV_FILE}"
[[ -n "${PROJECTS}" ]] && set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}" "${ENV_FILE}"

FINAL_PROVIDER="$(get_env_var CODX_JUNIOR_AI_PROVIDER "${ENV_FILE}")"
FINAL_KEY="$(get_env_var CODX_JUNIOR_AI_API_KEY "${ENV_FILE}")"
FINAL_URL="$(get_env_var CODX_JUNIOR_AI_API_URL "${ENV_FILE}")"
WEB_PORT="$(get_env_var CODX_JUNIOR_WEB_PORT "${ENV_FILE}")"
WEB_PORT="${WEB_PORT:-19981}"
PROJECTS_PATH="$(get_env_var CODX_JUNIOR_PROJECTS_PATH "${ENV_FILE}")"
PROJECTS_PATH="${PROJECTS_PATH:-./projects}"

if [[ -z "${FINAL_PROVIDER}" || -z "${FINAL_KEY}" ]]; then
    cat >&2 <<MISSING
ERROR: codx-junior needs an AI provider before it can do anything.

  ./up.sh --provider deepseek --key sk-xxxxxxxx
  ./up.sh --provider openai   --key sk-xxxxxxxx
  ./up.sh --provider gemini   --key AIza-xxxxxxxx

Supported: openai, deepseek, gemini, openrouter, mistral, groq, ollama, custom.
`custom` also needs --url https://your-gateway/v1
Alternatively edit ${ENV_FILE} (see README.md).
MISSING
    exit 1
fi

if [[ "${FINAL_PROVIDER}" == "custom" && -z "${FINAL_URL}" ]]; then
    die "provider 'custom' needs --url (an OpenAI-compatible endpoint ending in /v1)."
fi

# Project folder must exist and be absolute for the bind mount.
mkdir -p "${PROJECTS_PATH}"
PROJECTS_PATH="$(cd "${PROJECTS_PATH}" && pwd)"
set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS_PATH}" "${ENV_FILE}"
log "Projects folder: ${PROJECTS_PATH}"

# --- start -----------------------------------------------------------------
COMPOSE_FILES=(-f docker-compose.yaml)
COMPOSE_PROFILES=()
[[ "${KNOWLEDGE}" -eq 1 ]] && COMPOSE_PROFILES=(--profile knowledge)

if [[ "${DO_BUILD}" -eq 1 ]]; then
    log "Building the demo image (first build takes a few minutes)..."
    "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILES[@]}" build
else
    log "Pulling the demo image..."
    "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" pull --quiet codx-junior || \
        warn "Pull failed - falling back to a local build."
fi

log "Starting codx-junior..."
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILES[@]}" up -d --remove-orphans

if [[ "${NO_WAIT}" -ne 1 ]]; then
    log "Waiting for the API to become healthy (first start ~30s)..."
    STATUS="starting"
    for _ in $(seq 1 60); do
        STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}' "${CONTAINER}" 2>/dev/null || echo starting)"
        [[ "${STATUS}" == "healthy" ]] && break
        if [[ "${STATUS}" == "unhealthy" || "${STATUS}" == "exited" ]]; then
            echo
            "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" logs --tail 60 codx-junior || true
            die "codx-junior did not start (${STATUS}). Logs above."
        fi
        sleep 2
    done
    [[ "${STATUS}" == "healthy" ]] || { "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" logs --tail 60 codx-junior || true; die "Health check timeout."; }
fi

# --- AI provider configuration --------------------------------------------
log "Configuring the AI provider from the environment..."
docker exec "${CONTAINER}" \
    curl -fsS -X POST http://127.0.0.1:19980/api/bootstrap/apply >/dev/null 2>&1 \
    || warn "Could not apply the provider automatically; check the container logs."

SUMMARY="$(docker exec "${CONTAINER}" curl -fsS http://127.0.0.1:19980/api/bootstrap 2>/dev/null || true)"
if [[ -n "${SUMMARY}" ]]; then
    printf '%s\n' "${SUMMARY}"
fi

cat <<DONE

=============================================================================
 codx-junior is running

   Web UI : http://localhost:${WEB_PORT}
   Projects: ${PROJECTS_PATH}
   Settings: ${ENV_FILE}

 Next steps
   1. Open the UI and pick (or create) a project.
   2. Ask something in the chat - the provider is already configured.

 Logs / stop
   ${COMPOSE[*]} -f docker-compose.yaml logs -f codx-junior
   ${COMPOSE[*]} -f docker-compose.yaml down
=============================================================================
DONE
