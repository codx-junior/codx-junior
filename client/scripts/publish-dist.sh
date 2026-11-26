#!/bin/bash
# Publish the client SPA build that `vite build` just produced in `dist.stage`.
#
# Lives inside `client/` on purpose: the demo image builds the SPA in a stage
# that only copies `client/` (see codx-junior-installer/demo/Dockerfile), so a
# script under the repository-root `scripts/` would not be there.
#
# Why this exists: the production client image bind-mounts the checkout and
# nginx serves `client/dist` straight off disk. `vite build` used to write into
# `dist` itself (and empties it first), so any build that failed or was killed
# halfway left a `dist` without `index.html`. `scripts/run_client.sh` correctly
# refuses to serve a SPA that has no entry point, exits 1, and the container
# then crash-loops: `docker ps` keeps saying "Up", the health check never
# reports, and the web tier answers nothing.
#
# Building into `dist.stage` and swapping the directory in one step keeps the
# last good build live until a complete build exists. A failed build now leaves
# `dist` untouched and `npm run build-only` simply exits non-zero.
set -euo pipefail

CLIENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${CLIENT_DIR}"

if [ ! -f dist.stage/index.html ]; then
  echo "ERROR: dist.stage/index.html is missing; the build did not complete." >&2
  echo "       Leaving the published client/dist untouched." >&2
  exit 1
fi

# Keep the previous tree until the new one is in place, so a failure here can
# still be recovered by hand (mv dist.old dist).
rm -rf dist.old
if [ -d dist ]; then
  mv dist dist.old
fi
mv dist.stage dist
rm -rf dist.old

echo "client dist published from dist.stage"
