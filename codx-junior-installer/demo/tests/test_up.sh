#!/usr/bin/env bash
#
# demo/tests/test_up.sh -- executable checks for demo/up.sh
#
# Runs on a host WITHOUT Docker: a shim `docker` on PATH records every call and
# the CODX_JUNIOR_IMAGE it was given, and the script under test is exercised
# through a temporary "demo" directory built from symlinks, so the repository is
# never touched.
#
#   bash demo/tests/test_up.sh
#
# Output is TAP-like ("ok N - label", "not ok N - label"); the exit code is 0
# only when every check passes.
#
set -uo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd -P)
UP="$ROOT_DIR/demo/up.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

SHIM="$WORK/bin"
mkdir -p "$SHIM"
DOCKER_LOG="$WORK/docker.log"
: > "$DOCKER_LOG"

cat > "$SHIM/docker" <<'SHIM_EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_SHIM_LOG}"
printf 'image=%s\n' "${CODX_JUNIOR_IMAGE:-}" >> "${DOCKER_SHIM_LOG}"
if [ "${1:-}" = "compose" ] && [ "${2:-}" = "port" ]; then
  printf '0.0.0.0:19981\n'
fi
exit 0
SHIM_EOF
chmod +x "$SHIM/docker"

n=0
bad=0
ok()          { n=$((n + 1)); printf 'ok %d - %s\n' "$n" "$1"; }
not_ok()      { n=$((n + 1)); bad=$((bad + 1)); printf 'not ok %d - %s\n' "$n" "$1"; }
check_exit()  { if [ "$2" = "$3" ]; then ok "$1"; else not_ok "$1 (expected exit $3, got $2)"; fi; }
check_has()   { if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else not_ok "$1 (missing: $3)"; fi; }
check_lacks() { if printf '%s' "$2" | grep -qF -- "$3"; then not_ok "$1 (unexpected: $3)"; else ok "$1"; fi; }

# A throwaway "demo" directory: up.sh resolves its own directory, so symlinking
# it here is enough to redirect COMPOSE_FILE / ENV_FILE.
demo_dir() {
  local d="$WORK/demo-$1"
  mkdir -p "$d"
  ln -s "$UP" "$d/up.sh"
  ln -s "$ROOT_DIR/demo/docker-compose.yaml" "$d/docker-compose.yaml"
  ln -s "$ROOT_DIR/demo/.env.example" "$d/.env.example"
  printf '%s\n' "$d"
}

run() { PATH="$SHIM:$PATH" DOCKER_SHIM_LOG="$DOCKER_LOG" bash "$@" 2>&1; }

# 1 -- the script parses
run -n "$UP"
check_exit "bash -n accepts demo/up.sh" "$?" "0"

# 2 -- help documents both flags the README advertises
out=$(run "$UP" --help)
check_exit "--help exits 0" "$?" "0"
check_has "--help prints a Usage line" "$out" "Usage:"
check_has "--help documents --list-providers" "$out" "--list-providers"
check_has "--help documents --ref" "$out" "--ref"

# 3 -- providers can be listed with no .env and no Docker running
d=$(demo_dir providers)
: > "$DOCKER_LOG"
out=$(run "$d/up.sh" --list-providers)
check_exit "--list-providers exits 0 without .env" "$?" "0"
check_has "--list-providers lists deepseek" "$out" "deepseek"
check_has "--list-providers lists openrouter" "$out" "openrouter"
check_has "--list-providers lists custom" "$out" "custom"
check_lacks "--list-providers never calls docker" "$(cat "$DOCKER_LOG")" "compose"

# 4 -- an unknown option fails with the usage exit code
d=$(demo_dir badopt)
out=$(run "$d/up.sh" --bogus)
check_exit "an unknown option exits 2" "$?" "2"

# 5 -- an X.Y.Z ref selects the published pinned tag
d=$(demo_dir pin)
cp "$ROOT_DIR/demo/.env.example" "$d/.env"
out=$(run "$d/up.sh" --ref 1.2.3 --no-wait)
check_exit "--ref 1.2.3 exits 0" "$?" "0"
check_has "--ref 1.2.3 pins v1.2.3" "$out" "codxjuniorai/codx-junior:v1.2.3"
check_has "--ref 1.2.3 starts the stack" "$(cat "$DOCKER_LOG")" "compose -f"
check_has "--ref 1.2.3 hands v1.2.3 to compose" "$(cat "$DOCKER_LOG")" "image=codxjuniorai/codx-junior:v1.2.3"

# 6 -- a vX.Y.Z ref is accepted as-is
out=$(run "$d/up.sh" --ref v2.0.1 --no-wait)
check_exit "--ref v2.0.1 exits 0" "$?" "0"
check_has "--ref v2.0.1 pins v2.0.1" "$out" "codxjuniorai/codx-junior:v2.0.1"
check_has "--ref v2.0.1 hands v2.0.1 to compose" "$(cat "$DOCKER_LOG")" "image=codxjuniorai/codx-junior:v2.0.1"

# 7 -- a value that cannot be a tag falls back to the default instead of failing
out=$(run "$d/up.sh" --ref 'not a tag!' --no-wait)
check_exit "an unusable --ref still exits 0" "$?" "0"
check_has "an unusable --ref falls back to :latest" "$out" "codxjuniorai/codx-junior:latest"
check_has "an unusable --ref hands :latest to compose" "$(cat "$DOCKER_LOG")" "image=codxjuniorai/codx-junior:latest"

# 7b -- a full image reference is refused instead of silently accepted
out=$(run "$d/up.sh" --ref codxjuniorai/codx-junior:latest --no-wait)
check_exit "an image reference instead of a tag exits 2" "$?" "2"
check_has "an image reference is explained" "$out" "--ref takes a tag"

# 8 -- a missing .env is reported instead of started
d=$(demo_dir noenv)
: > "$DOCKER_LOG"
out=$(run "$d/up.sh" --no-wait)
check_exit "a missing .env exits 2" "$?" "2"
check_has "a missing .env is explained" "$out" ".env"
check_lacks "a missing .env never starts the stack" "$(cat "$DOCKER_LOG")" "up -d"

printf '\n1..%d\n' "$n"
if [ "$bad" -gt 0 ]; then
  printf '# %d check(s) failed\n' "$bad"
  exit 1
fi
printf '# all %d checks passed\n' "$n"
