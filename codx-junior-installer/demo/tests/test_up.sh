#!/usr/bin/env bash
# =============================================================================
# demo/tests/test_up.sh - smoke test for the CLI surface of demo/up.sh
#
# Why it exists: `demo/up.sh` is the demo's single entry point and nothing in
# the repo executed it, so `--list-providers` and `--ref` were advertised in
# `usage()` but unreachable in the parser (card 2639c296). Static reading
# cannot prove a flag works; this file can.
#
# No Docker, no network, no writes inside the repository:
#   * --help, --list-providers and an unknown option all exit inside the
#     argument parser, i.e. before up.sh does anything at all (the docker
#     check is line 138, .env creation is line 169), so they run against the
#     real demo/up.sh and the ".env untouched" assertion below is meaningful;
#   * --ref and the usage-vs-parser guard cross the docker gate and rewrite
#     .env, so they run against a throw-away copy of demo/ with a stub
#     `docker` first on PATH. Nothing is pulled, started or contacted.
#
# Run from anywhere (no executable bit required):
#   bash demo/tests/test_up.sh
#
# Output is TAP-like: `ok N - case` / `not ok N - case`.
# Exit code: 0 = every check passed, 1 = at least one check failed.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${HERE}/.." && pwd)"
SUT="${DEMO_DIR}/up.sh"

TMP="$(mktemp -d)"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

CHECKED=0
FAILED=0

pass() {
    printf 'ok %d - %s\n' "${CHECKED}" "$1"
}

fail() {
    FAILED=$((FAILED + 1))
    printf 'not ok %d - %s\n' "${CHECKED}" "$1"
    if [[ $# -gt 1 && -n "$2" ]]; then printf '       %s\n' "$2"; fi
    return 0
}

# run <dir> <cmd...> - captures stdout/stderr/exit code, never aborts the suite.
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

first_err() { head -n1 "${RUN_ERR}" 2>/dev/null || true; }

# Fingerprint of the repository's .env: the suite must never create or modify it.
env_fingerprint() {
    if [[ -f "${DEMO_DIR}/.env" ]]; then
        stat -c '%s:%Y' "${DEMO_DIR}/.env" 2>/dev/null || echo present
    else
        echo absent
    fi
}

# Top-level entries of demo/: the suite must not add or remove any.
demo_listing() { ls -A "${DEMO_DIR}" | sort; }

# --- fixtures ---------------------------------------------------------------
# Stub docker: answers the probes up.sh makes before starting anything (info,
# compose version) and every later call, and reports a healthy container.
STUB_BIN="${TMP}/bin"
mkdir -p "${STUB_BIN}"
cat > "${STUB_BIN}/docker" <<'STUB'
#!/usr/bin/env bash
# Stand-in for the docker CLI used by demo/tests/test_up.sh.
case "${1:-}" in
    info)    exit 0 ;;
    compose) exit 0 ;;
    inspect) echo healthy; exit 0 ;;
    exec)    exit 0 ;;
    *)       exit 0 ;;
esac
STUB
chmod +x "${STUB_BIN}/docker"

# Stub docker for the --list-providers case: proves the flag never consults
# docker (the printer must work on a host where docker is not installed yet).
NODOCKER_BIN="${TMP}/nodocker"
mkdir -p "${NODOCKER_BIN}"
cat > "${NODOCKER_BIN}/docker" <<'STUB'
#!/usr/bin/env bash
echo "FAIL: --list-providers must not need docker" >&2
exit 99
STUB
chmod +x "${NODOCKER_BIN}/docker"

# Throw-away copy of demo/ for the cases that must not touch the repository.
SANDBOX="${TMP}/sandbox"
mkdir -p "${SANDBOX}/demo"
cp -a "${DEMO_DIR}/." "${SANDBOX}/demo/"
rm -f "${SANDBOX}/demo/.env"
SUT_COPY="${SANDBOX}/demo/up.sh"

# run_sut_copy [VAR=value ...] bash <up.sh args...> - sandbox run with stub docker.
run_sut_copy() {
    run "${SANDBOX}/demo" env PATH="${STUB_BIN}:${PATH}" "$@"
}

BEFORE_ENV="$(env_fingerprint)"
BEFORE_LISTING="$(demo_listing)"

# --- case 1: --help ---------------------------------------------------------
run "${DEMO_DIR}" bash "${SUT}" --help
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then
    pass "--help exits 0"
else
    fail "--help exits 0" "rc=${RUN_RC} $(first_err)"
fi

CHECKED=$((CHECKED + 1))
if out_has "Usage: ./up.sh"; then
    pass "--help prints the usage block"
else
    fail "--help prints the usage block" "stdout does not contain 'Usage: ./up.sh'"
fi

# --- case 2: --list-providers ----------------------------------------------
run "${DEMO_DIR}" env PATH="${NODOCKER_BIN}:${PATH}" bash "${SUT}" --list-providers
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then
    pass "--list-providers exits 0 without docker"
else
    fail "--list-providers exits 0 without docker" "rc=${RUN_RC} $(first_err)"
fi

for name in openai deepseek gemini openrouter mistral groq ollama custom; do
    CHECKED=$((CHECKED + 1))
    if grep -qE "^[[:space:]]+${name}[[:space:]]" "${RUN_OUT}"; then
        pass "--list-providers lists the '${name}' preset"
    else
        fail "--list-providers lists the '${name}' preset" "no row for ${name}"
    fi
done

for model in gpt-4.1-mini deepseek-chat gemini-2.0-flash openai/gpt-4o-mini \
             mistral-large-latest llama-3.3-70b-versatile llama3.1 \
             text-embedding-3-small text-embedding-004 mistral-embed nomic-embed-text \
             CODX_JUNIOR_AI_MODEL CODX_JUNIOR_AI_API_URL CODX_JUNIOR_AI_EMBEDDINGS_MODEL; do
    CHECKED=$((CHECKED + 1))
    if out_has "${model}"; then
        pass "--list-providers shows '${model}'"
    else
        fail "--list-providers shows '${model}'" "missing from stdout"
    fi
done

for url in https://api.openai.com/v1 https://api.deepseek.com/v1 \
           https://generativelanguage.googleapis.com/v1beta/openai \
           https://openrouter.ai/api/v1 https://api.mistral.ai/v1 \
           https://api.groq.com/openai/v1 http://localhost:11434/v1; do
    CHECKED=$((CHECKED + 1))
    if out_has "${url}"; then
        pass "--list-providers shows base URL ${url}"
    else
        fail "--list-providers shows base URL ${url}" "missing from stdout"
    fi
done

AFTER_ENV="$(env_fingerprint)"
CHECKED=$((CHECKED + 1))
if [[ "${BEFORE_ENV}" == "${AFTER_ENV}" ]]; then
    pass "--help / --list-providers leave demo/.env untouched (${AFTER_ENV})"
else
    fail "--help / --list-providers leave demo/.env untouched" "before=${BEFORE_ENV} after=${AFTER_ENV}"
fi

# --- case 3: --ref and CODX_JUNIOR_REF -------------------------------------
run_sut_copy bash "${SUT_COPY}" --provider ollama --key test-key --ref v1.2.3
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then
    pass "--ref v1.2.3 completes in the sandbox"
else
    fail "--ref v1.2.3 completes in the sandbox" "rc=${RUN_RC} $(first_err)"
fi

CHECKED=$((CHECKED + 1))
if out_has "codxjunior/codx-junior:v1.2.3"; then
    pass "--ref v1.2.3 resolves the pinned image tag"
else
    fail "--ref v1.2.3 resolves the pinned image tag" "image reference not found in stdout"
fi

CHECKED=$((CHECKED + 1))
if out_has "codxjunior/codx-junior:demo"; then
    fail "--ref v1.2.3 does not fall back to the rolling tag" "stdout still contains codxjunior/codx-junior:demo"
else
    pass "--ref v1.2.3 does not fall back to the rolling tag"
fi

run_sut_copy CODX_JUNIOR_REF=v2.3.4 bash "${SUT_COPY}" --provider ollama --key test-key
CHECKED=$((CHECKED + 1))
if out_has "codxjunior/codx-junior:v2.3.4"; then
    pass "CODX_JUNIOR_REF v2.3.4 resolves the pinned image tag"
else
    fail "CODX_JUNIOR_REF v2.3.4 resolves the pinned image tag" "rc=${RUN_RC} image reference not found"
fi

run_sut_copy bash "${SUT_COPY}" --provider ollama --key test-key --ref main
CHECKED=$((CHECKED + 1))
if out_has "codxjunior/codx-junior:demo" && ! out_has "codxjunior/codx-junior:main"; then
    pass "--ref main keeps the rolling demo tag"
else
    fail "--ref main keeps the rolling demo tag" "rc=${RUN_RC} unexpected image reference"
fi

# --- case 4: unknown option ------------------------------------------------
run "${DEMO_DIR}" bash "${SUT}" --bogus
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]]; then
    pass "--bogus exits 1"
else
    fail "--bogus exits 1" "rc=${RUN_RC}"
fi

CHECKED=$((CHECKED + 1))
if err_has "Unknown option: --bogus"; then
    pass "--bogus reports 'Unknown option: --bogus' on stderr"
else
    fail "--bogus reports 'Unknown option: --bogus' on stderr" "$(first_err)"
fi

CHECKED=$((CHECKED + 1))
if out_has "Usage: ./up.sh"; then
    pass "--bogus still prints the usage block"
else
    fail "--bogus still prints the usage block" "the *) fallback does not print usage"
fi

# --- case 5: every flag advertised in usage() is handled by the parser -----
# This is the regression guard for 2639c296: --list-providers was advertised in
# usage() and absent from the case statement, and nothing noticed.
VALUE_FLAGS=" --provider --key --url --model --port --projects --ref "
USAGE_FLAGS="$(run "${DEMO_DIR}" bash "${SUT}" --help; grep -oE -- '--[a-z][a-z-]*' "${RUN_OUT}" | sort -u)"

CHECKED=$((CHECKED + 1))
if [[ -n "${USAGE_FLAGS}" ]]; then
    pass "usage() advertises flags to check ($(printf '%s ' ${USAGE_FLAGS}))"
else
    fail "usage() advertises flags to check" "could not extract any --flag token from --help"
fi

for flag in ${USAGE_FLAGS}; do
    rm -f "${SANDBOX}/demo/.env"
    case "${VALUE_FLAGS}" in
        *" ${flag} "*) run_sut_copy bash "${SUT_COPY}" "${flag}" dummy ;;
        *)             run_sut_copy bash "${SUT_COPY}" "${flag}" ;;
    esac
    CHECKED=$((CHECKED + 1))
    if err_has "Unknown option"; then
        fail "${flag} is advertised in usage() but rejected by the parser" "$(first_err)"
    else
        pass "${flag} is advertised in usage() and handled by the parser"
    fi
done

# -h is advertised as "-h, --help"; the long form is covered above, the short
# one needs its own run.
run "${DEMO_DIR}" bash "${SUT}" -h
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "Usage: ./up.sh"; then
    pass "-h exits 0 and prints the usage block"
else
    fail "-h exits 0 and prints the usage block" "rc=${RUN_RC}"
fi

# --- the whole suite must leave the repository untouched -------------------
CHECKED=$((CHECKED + 1))
if [[ "$(env_fingerprint)" == "${BEFORE_ENV}" ]]; then
    pass "the suite leaves demo/.env untouched (${BEFORE_ENV})"
else
    fail "the suite leaves demo/.env untouched" "before=${BEFORE_ENV} after=$(env_fingerprint)"
fi

CHECKED=$((CHECKED + 1))
AFTER_LISTING="$(demo_listing)"
if [[ "${BEFORE_LISTING}" == "${AFTER_LISTING}" ]]; then
    pass "the suite creates nothing new inside demo/"
else
    fail "the suite creates nothing new inside demo/" "listing changed"
fi

printf '\n%d check(s), %d failure(s)\n' "${CHECKED}" "${FAILED}"
if [[ ${FAILED} -ne 0 ]]; then
    exit 1
fi
exit 0
