#!/usr/bin/env bash
#
# Sync the Docker Hub repository page from a markdown file in this repository.
#
# The Docker Hub listing is the first thing a user sees after `docker pull`.
# Keeping it in git (DOCKERHUB.md) means a documentation change is reviewed like
# any other change, and the release workflow can publish it without a second
# source of truth.
#
# Usage:
#   DOCKERHUB_TOKEN=... ./scripts/dockerhub_description.sh
#   DOCKERHUB_TOKEN=... ./scripts/dockerhub_description.sh --readme DOCKERHUB.md
#   ./scripts/dockerhub_description.sh --dry-run
#
# Environment:
#   DOCKERHUB_TOKEN        access token with read/write access (required to push)
#   DOCKERHUB_USER         Docker Hub login user (default: DOCKERHUB_USERNAME,
#                          then DOCKERHUB_NAMESPACE)
#   DOCKERHUB_USERNAME     fallback login user (GitHub Actions secret name)
#   DOCKERHUB_NAMESPACE    organisation/user (default: codxjuniorai)
#   IMAGE                  full image coordinate; when set, overrides the
#                          namespace/repository parts (for example
#                          codxjuniorai/codx-junior)
#   IMAGE_NAME             repository name (default: codx-junior)
#   DOCKERHUB_DESCRIPTION  short description, max 100 chars
#
# The token is fed to curl through stdin and never appears in the command line.
set -euo pipefail

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

README="DOCKERHUB.md"
DRY_RUN="false"

# Allow the image coordinate to drive the target, so CI has one source of truth.
IMAGE="${IMAGE:-}"
if [[ -n "$IMAGE" ]]; then
  DOCKERHUB_NAMESPACE="${DOCKERHUB_NAMESPACE:-${IMAGE%%/*}}"
  IMAGE_NAME="${IMAGE_NAME:-${IMAGE##*/}}"
fi

DOCKERHUB_NAMESPACE="${DOCKERHUB_NAMESPACE:-codxjuniorai}"
IMAGE_NAME="${IMAGE_NAME:-codx-junior}"
DOCKERHUB_USER="${DOCKERHUB_USER:-${DOCKERHUB_USERNAME:-${DOCKERHUB_NAMESPACE}}}"
DOCKERHUB_TOKEN="${DOCKERHUB_TOKEN:-}"
# A token pasted from the Docker Hub UI can carry a trailing newline; a newline
# inside the curl config would break authentication. Strip all whitespace.
DOCKERHUB_TOKEN="$(printf '%s' "$DOCKERHUB_TOKEN" | tr -d '[:space:]')"
DOCKERHUB_USER="$(printf '%s' "$DOCKERHUB_USER" | tr -d '[:space:]')"
# Docker Hub caps the short description at 100 characters.
DOCKERHUB_DESCRIPTION="${DOCKERHUB_DESCRIPTION:-Open-source multi-AI workspace to plan, code, test and ship software. Self-host with Docker.}"

HUB_API="https://hub.docker.com/v2/repositories"

log()  { printf '[dockerhub-description] %s\n' "$*"; }
die()  { printf '[dockerhub-description] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
$SCRIPT_NAME - update the Docker Hub repository page from markdown

Usage:
  $SCRIPT_NAME [--readme FILE] [--dry-run] [--help]

Flags:
  --readme FILE   markdown file to publish (default: DOCKERHUB.md)
  --dry-run       validate and print the target; do not call Docker Hub
  --help          show this help

Environment:
  DOCKERHUB_TOKEN        access token (required unless --dry-run)
  DOCKERHUB_USER         login user (default: DOCKERHUB_USERNAME or namespace)
  DOCKERHUB_USERNAME     fallback login user
  DOCKERHUB_NAMESPACE    organisation/user (default: codxjuniorai)
  IMAGE                  full image coordinate, overrides namespace/repository
  IMAGE_NAME             repository name (default: codx-junior)
  DOCKERHUB_DESCRIPTION  short description (max 100 chars)

Target:
  ${HUB_API}/${DOCKERHUB_NAMESPACE}/${IMAGE_NAME}/
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --readme)   README="${2:?--readme needs a file}"; shift 2 ;;
    --dry-run)  DRY_RUN="true"; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl is not installed"
command -v python3 >/dev/null 2>&1 || die "python3 is not installed"
[[ -f "$README" ]] || die "readme not found: $README"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
payload="${tmpdir}/repository.json"
response="${tmpdir}/response.json"

python3 - "$README" "$DOCKERHUB_DESCRIPTION" "$payload" <<'PY'
import json
import pathlib
import sys

readme_path, description, payload_path = sys.argv[1], sys.argv[2], sys.argv[3]
readme = pathlib.Path(readme_path).read_text(encoding="utf-8")
description = description.strip()
if not readme.strip():
    raise SystemExit(f"{readme_path} is empty")
if len(description) > 100:
    raise SystemExit(
        f"short description is {len(description)} chars; Docker Hub allows at most 100"
    )
# Docker Hub rejects a full description above 25,000 characters.
if len(readme) > 25_000:
    raise SystemExit(
        f"{readme_path} is {len(readme)} chars; Docker Hub allows at most 25,000"
    )
pathlib.Path(payload_path).write_text(
    json.dumps(
        {"description": description, "full_description": readme},
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"readme: {len(readme)} chars | short: {len(description)} chars")
PY

target="${HUB_API}/${DOCKERHUB_NAMESPACE}/${IMAGE_NAME}/"
log "target: ${target}"

if [[ "$DRY_RUN" == "true" ]]; then
  log "dry run: nothing sent to Docker Hub"
  exit 0
fi

[[ -n "$DOCKERHUB_TOKEN" ]] || die "DOCKERHUB_TOKEN is required (or pass --dry-run)"

# `--config -` reads the credentials from stdin, so the token is not in argv.
# Do not use -f: on 401/403 the response body names the missing scope, which is
# the difference between "rotate the token" and "fix the repository".
http_code="$(
  printf 'user = "%s:%s"\n' "$DOCKERHUB_USER" "$DOCKERHUB_TOKEN" | \
    curl -sS --proto '=https' --config - \
      -X PATCH \
      -H 'Content-Type: application/json' \
      --data-binary "@${payload}" \
      -o "$response" -w '%{http_code}' \
      "$target"
)" || die "curl could not reach Docker Hub"

case "$http_code" in
  2*) ;;
  *)
    [[ -s "$response" ]] && cat "$response" >&2
    die "Docker Hub returned HTTP ${http_code}; the token needs Read/Write/Delete scope and Admin on the repository (an organization token is rejected)"
    ;;
esac

python3 - "$response" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    "updated:",
    payload.get("namespace", "?"),
    "/",
    payload.get("name", "?"),
    "| short:",
    (payload.get("description") or "")[:100],
    "| full:",
    len(payload.get("full_description") or ""),
    "chars",
)
PY
