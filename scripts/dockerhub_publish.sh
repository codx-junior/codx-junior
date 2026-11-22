#!/usr/bin/env bash
#
# Build (and optionally push) a project image to Docker Hub.
#
# Everything is driven by environment variables so the same script works on a
# laptop, in CI and from the AI tools. No credential is ever passed on the
# command line: the token is fed to `docker login --password-stdin`.
#
# Minimum usage:
#   DOCKERHUB_TOKEN=xxx ./scripts/dockerhub_publish.sh
#
# Typical release:
#   DOCKERHUB_TOKEN=xxx IMAGE_TAGS="1.4.0,latest" PLATFORMS="linux/amd64,linux/arm64" \
#     ./scripts/dockerhub_publish.sh
#
# Use --help for the full list of variables and flags.
set -euo pipefail

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

# ---------------------------------------------------------------------------
# Configuration (environment first, defaults last)
# ---------------------------------------------------------------------------
DOCKERHUB_REGISTRY="${DOCKERHUB_REGISTRY:-docker.io}"
# New Docker Hub home for the project images: https://hub.docker.com/u/codxjuniorai
DOCKERHUB_NAMESPACE="${DOCKERHUB_NAMESPACE:-codxjuniorai}"
DOCKERHUB_USER="${DOCKERHUB_USER:-${DOCKERHUB_NAMESPACE}}"
DOCKERHUB_TOKEN="${DOCKERHUB_TOKEN:-}"

IMAGE_NAME="${IMAGE_NAME:-codx-junior}"
# Comma separated list of tags pushed for the same build (e.g. "1.4.0,latest").
IMAGE_TAGS="${IMAGE_TAGS:-${IMAGE_TAG:-latest}}"

DOCKERFILE="${DOCKERFILE:-codx-junior-installer/demo/Dockerfile}"
CONTEXT="${CONTEXT:-.}"
BUILD_TARGET="${BUILD_TARGET:-}"
BUILD_ARGS="${BUILD_ARGS:-}"
# Multi-arch by default: one manifest list, native on amd64 and arm64 hosts.
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

PUSH="${PUSH:-true}"
BUILD="${BUILD:-true}"
NO_CACHE="${NO_CACHE:-false}"
PULL="${PULL:-false}"
# File where `docker buildx build --metadata-file` writes the digest/driver data.
METADATA_FILE="${METADATA_FILE:-}"

DRY_RUN="${DRY_RUN:-false}"
LOG_TAG="dockerhub"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$LOG_TAG" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$LOG_TAG" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$LOG_TAG" "$*" >&2; exit 1; }

is_true() {
  case "${1,,}" in
    true|1|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

# True when the docker CLI already holds a credential for the registry in its
# config store (a previous `docker login`). Pushing then works without the
# token being exported in the environment.
has_stored_credentials() {
  local cfg="${DOCKER_CONFIG:-${HOME}/.docker}/config.json"
  [[ -f "$cfg" ]] || return 1
  grep -q '"auth"' "$cfg"
}

run() {
  if is_true "$DRY_RUN"; then
    printf '[%s] (dry-run) %s\n' "$LOG_TAG" "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<EOF
$SCRIPT_NAME - build and publish $DOCKERHUB_NAMESPACE/$IMAGE_NAME to Docker Hub

Usage:
  $SCRIPT_NAME [--no-push] [--no-build] [--dry-run] [--help]

Flags:
  --no-push    build only, do not push to the registry (PUSH=false)
  --no-build   push an image that already exists locally (BUILD=false)
  --dry-run    print the docker commands instead of running them
  --help       show this help

Environment:
  DOCKERHUB_NAMESPACE   Docker Hub organisation/user  (default: codxjuniorai)
  DOCKERHUB_USER        registry user for login      (default: the namespace)
  DOCKERHUB_TOKEN       access token, required to push (never logged)
  DOCKERHUB_REGISTRY    registry host               (default: docker.io)
  IMAGE_NAME            repository name             (default: codx-junior)
  IMAGE_TAGS            comma separated tags pushed  (default: latest)
  IMAGE_TAG             single tag shortcut for IMAGE_TAGS
  DOCKERFILE            Dockerfile path             (default: Dockerfile)
  CONTEXT               build context               (default: .)
  BUILD_TARGET          multi-stage target to build
  BUILD_ARGS            space separated KEY=VALUE build arguments
  PLATFORMS             buildx platforms (default: linux/amd64,linux/arm64)
  PUSH / BUILD / NO_CACHE / PULL / DRY_RUN   true|false toggles
  METADATA_FILE         write buildx metadata (digest) to this file

Generated references:
  $DOCKERHUB_REGISTRY/$DOCKERHUB_NAMESPACE/$IMAGE_NAME:<tag>
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push)  PUSH="false" ;;
    --no-build) BUILD="false" ;;
    --dry-run)  DRY_RUN="true" ;;
    -h|--help)  usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker is not installed or not in PATH"
[[ -f "$DOCKERFILE" ]] || die "dockerfile not found: $DOCKERFILE"
[[ -d "$CONTEXT" ]] || die "build context is not a directory: $CONTEXT"

IFS=',' read -r -a TAGS <<< "$IMAGE_TAGS"
REFS=()
for raw_tag in "${TAGS[@]}"; do
  tag="$(printf '%s' "$raw_tag" | tr -d '[:space:]')"
  [[ -n "$tag" ]] || continue
  REFS+=("${DOCKERHUB_REGISTRY}/${DOCKERHUB_NAMESPACE}/${IMAGE_NAME}:${tag}")
done
[[ ${#REFS[@]} -gt 0 ]] || die "IMAGE_TAGS did not produce any tag"

PLATFORM_COUNT=$(awk -F',' '{print NF}' <<< "$PLATFORMS")
MULTI_ARCH="false"
[[ "$PLATFORM_COUNT" -gt 1 ]] && MULTI_ARCH="true"

# A dry run only prints the docker commands, so it must not require the token.
if is_true "$PUSH" && ! is_true "$DRY_RUN" && [[ -z "$DOCKERHUB_TOKEN" ]] && ! has_stored_credentials; then
  die "DOCKERHUB_TOKEN is required to push (or pass --no-push / use DRY_RUN=true)"
fi

# buildx is required to build (platform selection and manifests). A
# --no-build publish of an image that is already local only needs `docker push`.
if is_true "$BUILD"; then
  docker buildx version >/dev/null 2>&1 || die "docker buildx is required (install the buildx plugin)"
fi

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
login() {
  if [[ -z "$DOCKERHUB_TOKEN" ]]; then
    if has_stored_credentials; then
      log "no DOCKERHUB_TOKEN set: using the existing docker credential store"
    else
      warn "no DOCKERHUB_TOKEN set, skipping login (public pulls only)"
    fi
    return 0
  fi
  log "logging in to ${DOCKERHUB_REGISTRY} as ${DOCKERHUB_USER}"
  if is_true "$DRY_RUN"; then
    printf '[%s] (dry-run) docker login --username %s --password-stdin %s\n' \
      "$LOG_TAG" "$DOCKERHUB_USER" "$DOCKERHUB_REGISTRY"
    return 0
  fi
  # --password-stdin keeps the token out of the process list and the shell history.
  printf '%s' "$DOCKERHUB_TOKEN" | \
    docker login --username "$DOCKERHUB_USER" --password-stdin "$DOCKERHUB_REGISTRY" >/dev/null
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
build_image() {
  local -a cmd=(
    docker buildx build
    -f "$DOCKERFILE"
    --platform "$PLATFORMS"
  )

  local ref
  for ref in "${REFS[@]}"; do
    cmd+=(-t "$ref")
  done

  if [[ -n "$BUILD_TARGET" ]]; then
    cmd+=(--target "$BUILD_TARGET")
  fi
  if is_true "$NO_CACHE"; then
    cmd+=(--no-cache)
  fi
  if is_true "$PULL"; then
    cmd+=(--pull)
  fi
  if [[ -n "$BUILD_ARGS" ]]; then
    local pair
    for pair in $BUILD_ARGS; do
      cmd+=(--build-arg "$pair")
    done
  fi
  if [[ -n "$METADATA_FILE" ]]; then
    cmd+=(--metadata-file "$METADATA_FILE")
  fi

  if is_true "$PUSH"; then
    cmd+=(--push)
  elif is_true "$MULTI_ARCH"; then
    warn "multi-arch build without --push cannot be loaded locally; using --load for the first platform"
    cmd+=(--load --platform "${PLATFORMS%%,*}")
  else
    cmd+=(--load)
  fi

  cmd+=("$CONTEXT")

  log "building ${REFS[*]} for ${PLATFORMS}"
  run "${cmd[@]}"
}

# ---------------------------------------------------------------------------
# Push (only needed when the image was built locally without --push)
# ---------------------------------------------------------------------------
push_images() {
  local ref
  if is_true "$MULTI_ARCH"; then
    warn "multi-arch images are pushed by buildx; skipping the per-tag push"
    return 0
  fi
  for ref in "${REFS[@]}"; do
    log "pushing ${ref}"
    run docker push "$ref"
  done
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
summary() {
  log "done"
  local ref
  for ref in "${REFS[@]}"; do
    printf '  %s\n' "$ref"
  done
  if [[ -n "$METADATA_FILE" && -f "$METADATA_FILE" ]]; then
    printf '  metadata: %s\n' "$METADATA_FILE"
  fi
  printf '  https://hub.docker.com/r/%s/%s/tags\n' "$DOCKERHUB_NAMESPACE" "$IMAGE_NAME"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
login
if is_true "$BUILD"; then
  build_image
  if is_true "$PUSH" && ! is_true "$MULTI_ARCH"; then
    push_images
  fi
else
  if is_true "$PUSH"; then
    push_images
  fi
fi
summary
