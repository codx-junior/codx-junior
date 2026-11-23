#!/usr/bin/env bash
# =============================================================================
# tests/test_install.sh — CLI/behaviour tests for the unified installer
#
# No Docker, no network, no writes outside a throw-away directory:
#   * a shell function `docker` (exported to the installer) records every call
#     and answers the probes;
#   * installs run with --no-wait and --no-systemd;
#   * --help / validation / dry-run cases never reach the daemon.
#
# Run from anywhere:
#   bash codx-junior-installer/tests/test_install.sh
#
# Output is TAP-like: `ok N - case` / `not ok N - case`.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$(cd "${HERE}/.." && pwd)/install.sh"
ROOT="$(cd "${HERE}/../.." && pwd)"

TMP="$(mktemp -d "${ROOT}/tmp/cj-installer-tests.XXXXXX" 2>/dev/null \
    || mktemp -d "${ROOT}/tmp/cj-installer-tests.XXXXXX")"
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
    ( cd "${dir}" && env -u CODX_JUNIOR_DOMAIN "$@" ) >"${RUN_OUT}" 2>"${RUN_ERR}"
    RUN_RC=$?
    set -e
}
out_has() { grep -qF -- "$1" "${RUN_OUT}"; }
err_has() { grep -qF -- "$1" "${RUN_ERR}"; }
both_has() { grep -qF -- "$1" "${RUN_OUT}" "${RUN_ERR}"; }

# --- stub docker -------------------------------------------------------------
export STUB_LOG="${TMP}/docker.log"
: >"${STUB_LOG}"
export NETWORK_MISSING="${NETWORK_MISSING:-0}"
docker() {
    printf '%s\n' "$*" >>"${STUB_LOG}"
    if [[ "${DOCKER_FAIL:-0}" == "1" ]]; then
        echo "FAIL: docker must not be called for this case" >&2
        return 99
    fi
    case "${1:-}" in
        info)    return 0 ;;
        compose)
            case "$*" in
                *"version"*) return 0 ;;
                *) return 0 ;;
            esac ;;
        inspect) printf 'healthy\n'; return 0 ;;
        exec)    return 0 ;;
        pull)    return 0 ;;
        build)   return 0 ;;
        network)
            case "${2:-}" in
                inspect) [[ "${NETWORK_MISSING}" == "1" ]] && return 1; return 0 ;;
                *) return 0 ;;
            esac ;;
        *) return 0 ;;
    esac
}
export -f docker

new_home() { printf '%s/%s' "${TMP}" "$1"; }

# --- 1. --help must not need docker -----------------------------------------
: >"${STUB_LOG}"
run "${ROOT}" env DOCKER_FAIL=1 bash "${INSTALLER}" --help
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "--help exits 0"; else fail "--help exits 0" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if out_has "codx-junior unified installer"; then pass "--help prints the header"; else fail "--help prints the header"; fi
CHECKED=$((CHECKED + 1))
if [[ ! -s "${STUB_LOG}" ]]; then pass "--help never calls docker"; else fail "--help never calls docker" "$(cat "${STUB_LOG}")"; fi

# --- 2. unknown option -------------------------------------------------------
run "${ROOT}" bash "${INSTALLER}" --bogus
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]]; then pass "--bogus exits 1"; else fail "--bogus exits 1" "rc=${RUN_RC}"; fi
CHECKED=$((CHECKED + 1))
if both_has "unknown option or command: --bogus"; then pass "--bogus names the option"; else fail "--bogus names the option"; fi

# --- 3. local dry run --------------------------------------------------------
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "$(new_home local)" --instance cjlocal --port 18080 --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "local --dry-run exits 0"; else fail "local --dry-run exits 0" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if out_has "docker-compose.local.yaml"; then pass "local dry run selects the local overlay"; else fail "local dry run selects the local overlay"; fi
CHECKED=$((CHECKED + 1))
if out_has "http://localhost:18080"; then pass "local dry run reports the loopback URL"; else fail "local dry run reports the loopback URL"; fi
CHECKED=$((CHECKED + 1))
if [[ ! -e "$(new_home local)" ]]; then pass "dry run creates nothing"; else fail "dry run creates nothing" "$(new_home local) exists"; fi

# --- 4. server validation ----------------------------------------------------
run "${ROOT}" bash "${INSTALLER}" --mode server --provider deepseek --key sk-test \
    --acme-email ops@example.com --home "$(new_home s1)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]] && both_has "needs --domain"; then
    pass "server without a domain fails with an actionable message"
else
    fail "server without a domain fails with an actionable message" "rc=${RUN_RC}"
fi

run "${ROOT}" bash "${INSTALLER}" --mode server --domain codx.example.com \
    --provider deepseek --key sk-test --home "$(new_home s2)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]] && both_has "needs --acme-email"; then
    pass "server without an ACME e-mail fails"
else
    fail "server without an ACME e-mail fails" "rc=${RUN_RC}"
fi

# --- 5. master dry run -------------------------------------------------------
run "${ROOT}" bash "${INSTALLER}" --mode server --domain codx.example.com \
    --acme-email ops@example.com --provider deepseek --key sk-test \
    --home "$(new_home master)" --instance cjmaster --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "docker-compose.master.yaml"; then
    pass "server dry run selects the Traefik master overlay"
else
    fail "server dry run selects the Traefik master overlay" "rc=${RUN_RC}"
fi
CHECKED=$((CHECKED + 1))
if out_has "https://codx.example.com"; then pass "server dry run reports the public HTTPS URL"; else fail "server dry run reports the public HTTPS URL"; fi

# --- 6. slave dry run --------------------------------------------------------
NETWORK_MISSING=1 run "${ROOT}" bash "${INSTALLER}" --mode slave --domain team.example.com \
    --proxy-network traefik --provider openai --key sk-test \
    --home "$(new_home slave)" --instance cjslave --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "docker-compose.slave.yaml"; then
    pass "slave dry run selects the Traefik slave overlay"
else
    fail "slave dry run selects the Traefik slave overlay" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if err_has "is missing and must be created" || out_has "is missing and must be created"; then
    pass "slave dry run warns about the missing master network"
else
    fail "slave dry run warns about the missing master network"
fi

# --- 7. dind dry run ---------------------------------------------------------
run "${ROOT}" bash "${INSTALLER}" --mode dind --dind-mode server --domain acme.example.com \
    --acme-email ops@example.com --provider deepseek --key sk-test \
    --home "$(new_home dind)" --instance cjdind --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "docker-compose.master.yaml"; then
    pass "dind dry run selects the outer master overlay"
else
    fail "dind dry run selects the outer master overlay" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "dind/docker-compose.yaml"; then pass "dind dry run uses the DIND wrapper compose"; else fail "dind dry run uses the DIND wrapper compose"; fi

# --- 8. real (stubbed) local install writes the settings + wrapper ----------
HOME_DIR="$(new_home installed)"
: >"${STUB_LOG}"
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "${HOME_DIR}" --instance cjinstalled --port 18081 --no-wait --no-systemd --no-pull
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]]; then pass "stubbed local install exits 0"; else fail "stubbed local install exits 0" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"; fi
CHECKED=$((CHECKED + 1))
if [[ -f "${HOME_DIR}/config/cjinstalled.env" ]]; then pass "settings file is created"; else fail "settings file is created"; fi
CHECKED=$((CHECKED + 1))
if grep -q '^CODX_JUNIOR_AI_API_KEY=sk-test$' "${HOME_DIR}/config/cjinstalled.env" 2>/dev/null; then
    pass "settings file holds the provider key"
else
    fail "settings file holds the provider key"
fi
CHECKED=$((CHECKED + 1))
MODE_PROBE="${TMP}/mode-probe"
: >"${MODE_PROBE}"
if chmod 600 "${MODE_PROBE}" 2>/dev/null && [[ "$(stat -c '%a' "${MODE_PROBE}" 2>/dev/null)" == "600" ]]; then
    if [[ "$(stat -c '%a' "${HOME_DIR}/config/cjinstalled.env" 2>/dev/null || echo unknown)" == "600" ]]; then
        pass "settings file is mode 600"
    else
        fail "settings file is mode 600" "mode=$(stat -c '%a' "${HOME_DIR}/config/cjinstalled.env" 2>/dev/null || echo unknown)"
    fi
else
    # This workspace is a FUSE mount that silently ignores chmod/umask. The
    # installer warns on such a filesystem; assert the warning instead.
    if both_has "could not restrict"; then
        pass "settings file mode is enforced where the filesystem supports it"
    else
        fail "settings file mode is enforced where the filesystem supports it" "no warning"
    fi
fi
CHECKED=$((CHECKED + 1))
if [[ -f "${HOME_DIR}/bin/codx-junior-compose" ]]; then pass "compose wrapper is created"; else fail "compose wrapper is created"; fi
CHECKED=$((CHECKED + 1))
if grep -q 'docker-compose.local.yaml' "${HOME_DIR}/bin/codx-junior-compose" 2>/dev/null; then
    pass "compose wrapper keeps the selected overlays"
else
    fail "compose wrapper keeps the selected overlays"
fi
CHECKED=$((CHECKED + 1))
if grep -qE 'compose .* up -d --remove-orphans' "${STUB_LOG}"; then
    pass "install starts the stack with compose up"
else
    fail "install starts the stack with compose up" "$(tail -n3 "${STUB_LOG}")"
fi

# --- 9. config command -------------------------------------------------------
# The command form is `config` (not a flag).
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "$(new_home cfg)" --instance cjcfg config
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "mode=local" && out_has "instance=cjcfg"; then
    pass "config command prints the resolved configuration"
else
    fail "config command prints the resolved configuration" "rc=${RUN_RC}"
fi

# --- 10. interactive wizard (no args / --interactive) -----------------------
run_input() {
    local input="$1" dir="$2"
    shift 2
    set +e
    ( cd "${dir}" && printf '%s\n' "${input}" | \
        env -u CODX_JUNIOR_DOMAIN CODX_JUNIOR_INTERACTIVE=1 "$@" ) >"${RUN_OUT}" 2>"${RUN_ERR}"
    RUN_RC=$?
    set -e
}

run_input "local





deepseek
sk-test

n
y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "mode      : local" && out_has "provider  : deepseek"; then
    pass "interactive local wizard uses defaults and accepts answers"
else
    fail "interactive local wizard uses defaults and accepts answers" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if err_has "Deployment mode (local|server|slave|dind)"; then
    pass "wizard prints the mode prompt with its default"
else
    fail "wizard prints the mode prompt with its default"
fi

run_input "dind
slave
slave
demo.codx-junior.ai
traefik
letsencrypt
websecure
y




deepseek
sk-test

y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "mode      : dind (slave)" && out_has "sysbox    : 1"; then
    pass "interactive DIND slave wizard configures proxy and Sysbox"
else
    fail "interactive DIND slave wizard configures proxy and Sysbox" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "domains=demo.codx-junior.ai" && out_has "public_url=https://demo.codx-junior.ai"; then
    pass "interactive DIND slave wizard keeps the public domain"
else
    fail "interactive DIND slave wizard keeps the public domain"
fi

# --- 11. dind + local + slave ------------------------------------------------
run_input "dind
local
slave
demo.codx-junior.ai
traefik
letsencrypt
websecure
y




deepseek
sk-test

y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "traefik_mode=slave" && out_has "dind_mode=local"; then
    pass "dind local slave combines local access with the slave proxy"
else
    fail "dind local slave combines local access with the slave proxy" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "docker-compose.slave.yaml" && out_has "docker-compose.local.yaml"; then
    pass "dind local slave includes both proxy and local-publish overlays"
else
    fail "dind local slave includes both proxy and local-publish overlays"
fi
CHECKED=$((CHECKED + 1))
if out_has "domains=demo.codx-junior.ai" && out_has "public_url=https://demo.codx-junior.ai"; then
    pass "dind local slave keeps the public domain"
else
    fail "dind local slave keeps the public domain"
fi

# --- 12. the root front door delegates ---------------------------------------
run "${ROOT}" bash "${ROOT}/install.sh" --version
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "codx-junior install.sh 2.0.0"; then
    pass "root install.sh --version works"
else
    fail "root install.sh --version works" "rc=${RUN_RC}"
fi

run "${ROOT}" bash "${ROOT}/install.sh" --mode local --provider deepseek --key sk-test \
    --home "$(new_home shim)" --instance cjshim --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "docker-compose.local.yaml"; then
    pass "root install.sh delegates to the unified installer"
else
    fail "root install.sh delegates to the unified installer" "rc=${RUN_RC}"
fi

# --- 11. the cloud compatibility shim ---------------------------------------
run "${ROOT}" bash "${ROOT}/codx-junior-installer/cloud/bootstrap.sh" --help
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "codx-junior unified installer"; then
    pass "cloud/bootstrap.sh is a shim to the unified installer"
else
    fail "cloud/bootstrap.sh is a shim to the unified installer" "rc=${RUN_RC}"
fi

printf '\n%d checks, %d failed\n' "${CHECKED}" "${FAILED}"
[[ "${FAILED}" -eq 0 ]]
