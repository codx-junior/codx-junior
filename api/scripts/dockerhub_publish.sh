#!/usr/bin/env bash
#
# Compatibility shim — do not add logic here.
#
# The Docker Hub tools (dockerhub_publish / dockerhub_whoami) execute their
# helper script with the API working directory as cwd, so the configured
# relative path "scripts/dockerhub_publish.sh" resolves to api/scripts/ and the
# real script is never found. This shim only hands over to the real one from the
# repository root, which is what CONTEXT=. and codx-junior-installer/demo/*.Dockerfile
# paths assume. Keep it a pure delegation.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
exec ./scripts/dockerhub_publish.sh "$@"
