#!/usr/bin/env bash
#
# demo/up.sh -- one-command bring-up of the codx-junior demo stack.
#
# Uses the single-container stack in demo/docker-compose.yaml: the web UI is
# published on port 19981 (CODX_JUNIOR_WEB_PORT) and the image is the published
# application image, so nothing is compiled on the demo host.
#
# Settings come from demo/.env -- ONE provider and ONE API key, exactly as
# demo/.env.example describes. This script never reads or writes those values:
# it only checks that .env exists.
#
#   ./up.sh                     start the stack and wait until it answers
#   ./up.sh --ref v1.2.3        pin a published image tag
#   ./up.sh --list-providers    show the provider values .env accepts
#   ./up.sh --url               print the URL of a running stack
#   ./up.sh --logs              follow the stack logs
#   ./up.sh --down              stop the stack
#
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yaml"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"

IMAGE_REPO="${CODX_JUNIOR_IMAGE_REPO:-codxjuniorai/codx-junior}"
DEFAULT_TAG="${CODX_JUNIOR_TAG:-latest}"
WEB_PORT=19981

ACTION="up"
BUILD=0
WAIT=1
REF=""

die()  { printf 'up.sh: error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'up.sh: warning: %s\n' "$*" >&2; }
info() { printf 'up.sh: %s\n' "$*"; }
usage_error() {
  printf 'up.sh: error: %s\n' "$*" >&2
  printf "Try '%s --help'.\n" "${0##*/}" >&2
  exit 2
}

usage() {
  cat <<EOF
${0##*/} -- bring the codx-junior demo stack up from a clean checkout

Usage: ${0##*/} [options]

  --ref <tag>         Pin the published image tag. "vX.Y.Z" or "X.Y.Z" both
                      select ${IMAGE_REPO}:vX.Y.Z. Default: ${DEFAULT_TAG}.
  --list-providers    Show the AI provider values demo/.env accepts, then exit.
  --build             Build the image from this checkout instead of pulling it.
  --url               Print the URL of the running stack, then exit.
  --logs              Follow the stack logs, then exit.
  --down              Stop the stack, then exit.
  --no-wait           Do not wait for the stack to answer on /api/health.
  -h, --help          Show this help, then exit.

demo/.env is created from demo/.env.example on the first run and holds ONE
provider and ONE API key. The web UI is published on port ${WEB_PORT}.
EOF
}

usage_providers() {
  cat <<'EOF'
AI providers accepted by demo/.env (CODX_JUNIOR_AI_PROVIDER):

  openai       OpenAI-hosted models
  deepseek     DeepSeek chat models
  gemini       Google Gemini models
  openrouter   OpenRouter routing over many upstream models
  mistral      Mistral hosted models
  groq         Groq hosted fast inference
  custom       Any OpenAI-compatible endpoint. Requires CODX_JUNIOR_AI_API_URL
               too; it must end in /v1
  none         No AI: the UI still starts and AI features stay disabled

Model ids are chosen per provider by codx-junior on first start; override one
with CODX_JUNIOR_AI_MODEL in demo/.env. A local Ollama or any other
OpenAI-compatible server is used as the `custom` provider.

Only the provider and one API key are required -- see demo/.env.example.
EOF
}

# Turns --ref into a full image reference. "1.2.3" and "v1.2.3" both mean the
# published pinned release; an unusable value falls back to the default tag
# instead of failing the demo.
resolve_image() {
  local ref=${1:-} tag
  if [ -z "$ref" ]; then
    printf '%s:%s\n' "$IMAGE_REPO" "$DEFAULT_TAG"
    return 0
  fi
  case $ref in
    */*) usage_error "--ref takes a tag, not an image reference ('$ref')" ;;
  esac
  if [[ $ref =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    tag="v$ref"
  elif [[ $ref =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    tag="$ref"
  elif [[ $ref =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
    tag="$ref"
  else
    warn "'$ref' is not a usable image tag; using '$DEFAULT_TAG'"
    tag="$DEFAULT_TAG"
  fi
  printf '%s:%s\n' "$IMAGE_REPO" "$tag"
}

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

host_port() {
  local port
  port=$(compose port codx-junior "$WEB_PORT" 2>/dev/null | sed -n '1s/.*://p') || port=""
  [ -n "$port" ] || port="${CODX_JUNIOR_WEB_PORT:-$WEB_PORT}"
  printf '%s\n' "$port"
}

wait_for_health() {
  local url=$1 timeout=${CODX_JUNIOR_WAIT_SECONDS:-180} waited=0
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found, not waiting for the stack to answer"
    return 0
  fi
  printf 'up.sh: waiting for %s ' "$url"
  while [ "$waited" -lt "$timeout" ]; do
    if curl -fsS --max-time 3 -o /dev/null "$url"; then
      printf 'ready\n'
      return 0
    fi
    printf '.'
    sleep 3
    waited=$((waited + 3))
  done
  printf 'not ready\n'
  warn "the stack did not answer within ${timeout}s; run '${0##*/} --logs'"
  return 1
}

require_docker() {
  command -v docker >/dev/null 2>&1 \
    || die "docker is required for this action ('${0##*/} --list-providers' works without it)"
  docker compose version >/dev/null 2>&1 \
    || die "the docker compose v2 plugin is required"
}

while [ $# -gt 0 ]; do
  case $1 in
    -h|--help)         usage; exit 0 ;;
    --list-providers)  usage_providers; exit 0 ;;
    --ref)             [ $# -ge 2 ] || usage_error "--ref needs a tag"
                       REF="$2"; shift 2 ;;
    --ref=*)           REF="${1#*=}"; shift ;;
    --build)           BUILD=1; shift ;;
    --no-wait)         WAIT=0; shift ;;
    -r)                [ $# -ge 2 ] || usage_error "-r needs a tag"
                       REF="$2"; shift 2 ;;
    --url)             ACTION="url"; shift ;;
    --logs)            ACTION="logs"; shift ;;
    --down)            ACTION="down"; shift ;;
    -*)                usage_error "unknown option: $1" ;;
    *)                 usage_error "unexpected argument: $1" ;;
  esac
done

[ -f "$COMPOSE_FILE" ] || die "cannot find $COMPOSE_FILE"
require_docker

case $ACTION in
  down)
    info "stopping the demo stack"
    compose down
    exit 0
    ;;
  logs)
    compose logs -f --tail=100
    exit 0
    ;;
  url)
    printf 'http://localhost:%s\n' "$(host_port)"
    exit 0
    ;;
esac

IMAGE_REF=$(resolve_image "$REF")
export CODX_JUNIOR_IMAGE="$IMAGE_REF"
info "image: $IMAGE_REF"

if [ ! -f "$ENV_FILE" ]; then
  [ -f "$ENV_EXAMPLE" ] || die "both $ENV_FILE and $ENV_EXAMPLE are missing"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  warn "created $ENV_FILE from .env.example"
  cat >&2 <<EOF
up.sh: $ENV_FILE holds CODX_JUNIOR_AI_PROVIDER and CODX_JUNIOR_AI_API_KEY.
up.sh: the stack starts once those two values are present in that file.
up.sh: '${0##*/} --list-providers' prints the accepted provider values.
EOF
  exit 2
fi

if [ "$BUILD" = "1" ]; then
  info "building $IMAGE_REF from this checkout"
  compose up -d --build
else
  if ! compose pull --ignore-pull-failures codx-junior >/dev/null 2>&1; then
    warn "could not pull $IMAGE_REF; continuing with a local image if present"
  fi
  compose up -d --no-build
fi

url="http://localhost:$(host_port)"
if [ "$WAIT" = "1" ]; then
  wait_for_health "http://127.0.0.1:$(host_port)/api/health" || true
fi

info "web UI: $url"
printf '%s\n' "$url"
