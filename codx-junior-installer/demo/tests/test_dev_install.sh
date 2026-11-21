#!/usr/bin/env bash
# =============================================================================
# tests/test_dev_install.sh - CLI smoke test for dev-install.sh
#
# No Docker, no network, no writes inside the repository: a shell function named
# `docker` (exported to the installer) records every call and answers the probes.
# The installer runs against a throw-away copy of dev-install.sh and the demo
# compose files, so the real .env and projects folder are never touched.
#
# Run from anywhere:
#   bash codx-junior-installer/demo/tests/test_dev_install.sh
#
# Output is TAP-like: `ok N - case` / `not ok N - case`.
# Exit code: 0 = every check passed, 1 = at least one check failed.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="$(cd "${HERE}/.." && pwd)"
ROOT="$(cd "${DEMO}/../.." && pwd)"
SUT_SRC="${ROOT}/dev-install.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/codx-dev-install-test.XXXXXX" 2>/dev/null \
    || mktemp -d "${ROOT}/.dev-install-test.XXXXXX")"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

CHECKED=0
FAILED=0
pass() { printf 'ok %d - %s\n' "${CHECKED}" "$1"; }
fail() {
    FAILED=$((FAILED + 1))
    printf 'not ok %d - %s\n' "${CHECKED}" "$1"
    if [[ $# -gt 1 && -n "$2" ]]; then printf '       %s\n' "$2"; fi
}

RUN_OUT="${TMP}/out"
RUN_ERR="${TMP}/err"
RUN_RC=0
run() {
    local dir="$1"
    shift
    set +e
    ( cd "${dir}" && "$@" ) >"${RUN_OUT}" 2>"${RUN_ERR}"
    RUN_RC=$?
    set -e
}
out_has() { grep -qF -- "$1" "${RUN_OUT}"; }
err_has() { grep -qF -- "$1" "${RUN_ERR}"; }
both_has() { grep -qF -- "$1" "${RUN_OUT}" "${RUN_ERR}"; }

# --- stub docker -------------------------------------------------------------
# A function, not a file: the sandbox this suite is developed in cannot chmod a
# new file, and an exported function is inherited by `bash dev-install.sh`.
export STUB_LOG="${TMP}/docker.log"
: >"${STUB_LOG}"
docker() {
    printf '%s\n' "$*" >>"${STUB_LOG}"
    if [[ "${DOCKER_FAIL:-0}" == "1" ]]; then
        echo "FAIL: docker must not be called for this case" >&2
        return 99
    fi
    case "${1:-}" in
        info)    return 0 ;;
        compose) return 0 ;;
        image)   return 0 ;;
        inspect) echo healthy; return 0 ;;
        exec)
            case "$*" in
                *"/api/bootstrap"*) printf '{"status":"ok"}\n' ;;
            esac
            return 0 ;;
        *) return 0 ;;
    esac
}
export -f docker

# --- sandbox -----------------------------------------------------------------
SANDBOX="${TMP}/repo"
mkdir -p "${SANDBOX}/codx-junior-installer/demo"
cp "${SUT_SRC}" "${SANDBOX}/dev-install.sh"
for f in docker-compose.yaml docker-compose.dev.yaml .env.example; do
    cp "${DEMO}/${f}" "${SANDBOX}/codx-junior-installer/demo/${f}"
done
SUT="${SANDBOX}/dev-install.sh"
ENV_FILE="${SANDBOX}/codx-junior-installer/demo/.env"

env_fingerprint() {
    if [[ -f "${DEMO}/.env" ]]; then stat -c '%s:%Y' "${DEMO}/.env" 2>/dev/null || echo present; else echo absent; fi
}
BEFORE_ENV="$(env_fingerprint)"

# --- 1. --help must not need docker -----------------------------------------
run "${SANDBOX}" env DOCKER_FAIL=1 bash "${SUT}" --help
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "--help exits 0"; else fail "--help exits 0" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if out_has "DEV installer"; then pass "--help prints the dev installer header"; else fail "--help prints the dev installer header"; fi
CHECKED=$((CHECKED + 1))
if [[ ! -s "${STUB_LOG}" ]]; then pass "--help never calls docker"; else fail "--help never calls docker" "$(cat "${STUB_LOG}")"; fi

# --- 2. unknown option -------------------------------------------------------
run "${SANDBOX}" bash "${SUT}" --bogus
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]]; then pass "--bogus exits 1"; else fail "--bogus exits 1" "rc=${RUN_RC}"; fi
CHECKED=$((CHECKED + 1))
if both_has "unknown option: --bogus"; then pass "--bogus names the option"; else fail "--bogus names the option"; fi

# --- 3. --down needs no provider --------------------------------------------
: >"${STUB_LOG}"
run "${SANDBOX}" bash "${SUT}" --down
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "--down exits 0 without a provider"; else fail "--down exits 0 without a provider" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if grep -qE 'compose .* down' "${STUB_LOG}"; then pass "--down runs compose down"; else fail "--down runs compose down"; fi

# --- 4. start: settings, no rebuild when the image exists --------------------
: >"${STUB_LOG}"
rm -f "${ENV_FILE}"
run "${SANDBOX}" bash "${SUT}" --provider deepseek --key sk-test --port 18080 --reload
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "start exits 0 with a provider and key"; else fail "start exits 0 with a provider and key" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
for setting in \
    "CODX_JUNIOR_AI_PROVIDER=deepseek" \
    "CODX_JUNIOR_AI_API_KEY=sk-test" \
    "CODX_JUNIOR_WEB_PORT=18080" \
    "CODX_JUNIOR_API_RELOAD=1"; do
    CHECKED=$((CHECKED + 1))
    if grep -qx "${setting}" "${ENV_FILE}"; then pass "start writes ${setting%%=*}"; else fail "start writes ${setting%%=*}"; fi
done
CHECKED=$((CHECKED + 1))
if out_has "http://localhost:18080"; then pass "start prints the requested URL"; else fail "start prints the requested URL"; fi
CHECKED=$((CHECKED + 1))
if grep -qE 'compose .* up -d' "${STUB_LOG}"; then pass "start runs compose up -d"; else fail "start runs compose up -d"; fi
CHECKED=$((CHECKED + 1))
if grep -qE 'compose .* build$' "${STUB_LOG}"; then fail "start reuses the existing dev image"; else pass "start reuses the existing dev image"; fi
CHECKED=$((CHECKED + 1))
if [[ -d "${SANDBOX}/codx-junior-installer/demo/projects" ]]; then
    pass "start creates the projects directory"
else
    fail "start creates the projects directory"
fi

# --- 5. ollama needs no key; --build forces a build -------------------------
: >"${STUB_LOG}"
rm -f "${ENV_FILE}"
run "${SANDBOX}" bash "${SUT}" --provider ollama --build
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "ollama starts without a key"; else fail "ollama starts without a key" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if grep -qx "CODX_JUNIOR_AI_API_KEY=ollama" "${ENV_FILE}"; then pass "ollama fills the placeholder key"; else fail "ollama fills the placeholder key"; fi
CHECKED=$((CHECKED + 1))
if grep -qE 'compose .* build$' "${STUB_LOG}"; then pass "--build runs compose build"; else fail "--build runs compose build"; fi

# --- 6. environment wins over a previous .env -------------------------------
run "${SANDBOX}" env CODX_JUNIOR_AI_PROVIDER=openai CODX_JUNIOR_AI_API_KEY=from-env bash "${SUT}"
CHECKED=$((CHECKED + 1))
if grep -qx "CODX_JUNIOR_AI_PROVIDER=openai" "${ENV_FILE}" && grep -qx "CODX_JUNIOR_AI_API_KEY=from-env" "${ENV_FILE}"; then
    pass "environment overrides the previous .env"
else
    fail "environment overrides the previous .env"
fi

# --- 7. missing provider is a clear failure ---------------------------------
rm -f "${ENV_FILE}"
run "${SANDBOX}" bash "${SUT}"
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]] && err_has "an AI provider is required"; then
    pass "missing provider fails with a clear message"
else
    fail "missing provider fails with a clear message" "rc=${RUN_RC}"
fi

# --- 8. the real repository was never written -------------------------------
CHECKED=$((CHECKED + 1))
if [[ "$(env_fingerprint)" == "${BEFORE_ENV}" ]]; then
    pass "the suite leaves the repository's demo/.env untouched"
else
    fail "the suite leaves the repository's demo/.env untouched" "before=${BEFORE_ENV} after=$(env_fingerprint)"
fi

printf '\n%d check(s), %d failure(s)\n' "${CHECKED}" "${FAILED}"
[[ ${FAILED} -eq 0 ]] || exit 1
exit 0
