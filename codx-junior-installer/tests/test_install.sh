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
    rm -f "${TMP}/installer-state.env"
    ( cd "${dir}" && env -u CODX_JUNIOR_DOMAIN \
        CODX_JUNIOR_INSTALLER_STATE="${TMP}/installer-state.env" "$@" ) >"${RUN_OUT}" 2>"${RUN_ERR}"
    RUN_RC=$?
    set -e
}
run_input() {
    local input="$1" dir="$2"
    shift 2
    set +e
    rm -f "${TMP}/installer-state.env"
    ( cd "${dir}" && printf '%s\n' "${input}" | \
        env -u CODX_JUNIOR_DOMAIN CODX_JUNIOR_INSTALLER_STATE="${TMP}/installer-state.env" \
            CODX_JUNIOR_INTERACTIVE=1 "$@" ) >"${RUN_OUT}" 2>"${RUN_ERR}"
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
CHECKED=$((CHECKED + 1))
if grep -q '/var/run/docker.sock:/var/run/docker.sock:ro' "${HERE}/../runtime/docker-compose.slave.yaml" 2>/dev/null; then
    pass "slave Traefik mounts the Docker socket for provider discovery"
else
    fail "slave Traefik mounts the Docker socket for provider discovery"
fi
CHECKED=$((CHECKED + 1))
if out_has "docker-compose.slave-edge.yaml"; then
    pass "host slave adds the external-primary edge labels"
else
    fail "host slave adds the external-primary edge labels"
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

# DIND inner installation is always server; the old local/slave choices fail.
run "${ROOT}" bash "${INSTALLER}" --mode dind --dind-mode local --domain x.example.com \
    --provider deepseek --key sk-test --home "$(new_home dind-bad)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -ne 0 ]] && both_has "DIND inner installation is always server"; then
    pass "dind rejects non-server inner modes"
else
    fail "dind rejects non-server inner modes" "rc=${RUN_RC}"
fi

# Inside DIND the host Traefik network name must not be required by the inner
# installer; its nested slave network is Compose-managed.
run "${ROOT}" bash "${INSTALLER}" --container --mode server --traefik slave \
    --domain demo.codx-junior.ai --proxy-network docker_default \
    --provider deepseek --key sk-test --home "$(new_home inner)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "proxy_external=false" \
    && ! both_has "does not exist"; then
    pass "dind inner slave does not require the host Traefik network"
else
    fail "dind inner slave does not require the host Traefik network" "rc=${RUN_RC}"
fi
CHECKED=$((CHECKED + 1))
if ! out_has "docker-compose.slave-edge.yaml"; then
    pass "dind inner slave does not load the outer edge labels"
else
    fail "dind inner slave does not load the outer edge labels"
fi

# DIND development option: DEBUG=1 inside, release image, no host dev overlay.
run "${ROOT}" bash "${INSTALLER}" --mode dind --dev --traefik slave \
    --domain demo.codx-junior.ai --proxy-network docker_default \
    --provider deepseek --key sk-test --home "$(new_home dind-dev)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "dev_mode=1" \
    && out_has "image=codxjuniorai/codx-junior:latest" \
    && ! out_has "docker-compose.dev.yaml"; then
    pass "dind development option enables DEBUG without a host dev overlay"
else
    fail "dind development option enables DEBUG without a host dev overlay" "rc=${RUN_RC}"
fi

# --- 7b. dind --update must recreate the wrapper ----------------------------
# Only the wrapper entrypoint runs the inner installer (it pulls the app image
# and recreates the nested stack). A DIND image that did not change must not
# turn `--update` into a no-op: the wrapper has to be recreated explicitly.
DIND_UP_HOME="$(new_home dind-upgrade)"
DIND_ARGS=(--mode dind --traefik master --domain dind.example.com
           --acme-email ops@example.com --provider deepseek --key sk-test
           --home "${DIND_UP_HOME}" --instance cjdinupdate
           --no-wait --no-systemd --no-pull)
run "${ROOT}" bash "${INSTALLER}" "${DIND_ARGS[@]}"
: >"${STUB_LOG}"
run "${ROOT}" bash "${INSTALLER}" "${DIND_ARGS[@]}" --update \
    --image codxjuniorai/codx-junior:v9
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -q 'up -d --remove-orphans --force-recreate' "${STUB_LOG}"; then
    pass "dind update recreates the wrapper so the inner installer runs"
else
    fail "dind update recreates the wrapper so the inner installer runs" \
        "rc=${RUN_RC} $(grep -E 'compose .* up' "${STUB_LOG}" | tail -n1)"
fi
CHECKED=$((CHECKED + 1))
if grep -q 'CODX_JUNIOR_IMAGE: "codxjuniorai/codx-junior:v9"' "${DIND_UP_HOME}/docker-compose.yaml" 2>/dev/null; then
    pass "dind update passes the new app image to the inner installer"
else
    fail "dind update passes the new app image to the inner installer" \
        "$(grep CODX_JUNIOR_IMAGE "${DIND_UP_HOME}/docker-compose.yaml" 2>/dev/null)"
fi

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

# The API cannot see the installation home (only <data>/settings is mounted),
# so the installer publishes the facts an in-app update needs. Without this
# file the settings page can only show the manual upgrade command.
CHECKED=$((CHECKED + 1))
DESCRIPTOR="${HOME_DIR}/data/settings/installer.json"
if [[ -f "${DESCRIPTOR}" ]] \
   && grep -q "\"home\": \"${HOME_DIR}\"" "${DESCRIPTOR}" \
   && grep -q '"instance": "cjinstalled"' "${DESCRIPTOR}" \
   && grep -q '"container": false' "${DESCRIPTOR}" \
   && grep -q '\"cli\": \"'"${HOME_DIR}"'/bin/codx-junior-cjinstalled\"' "${DESCRIPTOR}"; then
    pass "update descriptor names the installation (in-app updates)"
else
    fail "update descriptor names the installation (in-app updates)" "$(head -n3 "${DESCRIPTOR}" 2>/dev/null)"
fi
CHECKED=$((CHECKED + 1))
if grep -q '^CODX_JUNIOR_MODE=local$' "${TMP}/installer-state.env" 2>/dev/null \
    && grep -q '^CODX_JUNIOR_INSTANCE=cjinstalled$' "${TMP}/installer-state.env" 2>/dev/null; then
    pass "installer selections are saved for the next run"
else
    fail "installer selections are saved for the next run"
fi

# --- 9. saved selections become defaults ------------------------------------
REUSE_STATE="${TMP}/state-reuse.env"
cat > "${REUSE_STATE}" <<EOF
CODX_JUNIOR_MODE=local
CODX_JUNIOR_DEV=1
CODX_JUNIOR_INSTANCE=state-reuse
CODX_JUNIOR_HOME=${TMP}/state-home
CODX_JUNIOR_PROJECTS_PATH=${TMP}/state-projects
CODX_JUNIOR_WEB_PORT=19995
CODX_JUNIOR_AI_PROVIDER=deepseek
CODX_JUNIOR_AI_API_KEY=sk-state
EOF
run "${ROOT}" env CODX_JUNIOR_INSTALLER_STATE="${REUSE_STATE}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "dev_mode=1" && out_has "instance=state-reuse"; then
    pass "saved selections are reused as defaults"
else
    fail "saved selections are reused as defaults" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "web_port=19995" && out_has "state_file=${REUSE_STATE}"; then
    pass "saved port and state path are reported"
else
    fail "saved port and state path are reported"
fi

# --- 10. changing the instance resets instance-derived paths -----------------
MIX_STATE="${TMP}/mix-state.env"
cat > "${MIX_STATE}" <<EOF
CODX_JUNIOR_MODE=dind
CODX_JUNIOR_INSTANCE=demo-codx-junior-ai
CODX_JUNIOR_HOME=${TMP}/instances/demo-codx-junior-ai
CODX_DATA_DIR=${TMP}/instances/demo-codx-junior-ai/data
CODX_JUNIOR_PROJECTS_PATH=${TMP}/instances/demo-codx-junior-ai/data/projects
CODX_JUNIOR_DOMAIN=demo.codx-junior.ai
CODX_JUNIOR_TRAEFIK=slave
CODX_PROXY_NETWORK=docker_default
CODX_MASTER_CERT_RESOLVER=myresolver
CODX_MASTER_ENTRYPOINT=websecure
CODX_JUNIOR_AI_PROVIDER=deepseek
CODX_JUNIOR_AI_API_KEY=sk-state
EOF
run_input "${TMP}/target-mix

n
dev.codx-junior.ai




n







y" "${ROOT}" env CODX_JUNIOR_INSTALLER_STATE="${MIX_STATE}" \
    bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] \
    && out_has "home=${TMP}/target-mix" \
    && out_has "projects=${TMP}/target-mix/data/projects"; then
    pass "changing the instance recomputes home and projects"
else
    fail "changing the instance recomputes home and projects" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi

# --- 10b. install-time feature profile (0.0.1 defaults to workspace mode) ---
# The release profile is `core`: the company OS is hidden, so the image starts
# in the project workspace and never opens the company wizard. `all` is the
# explicit full-featured opt-in.
FEATURES_HOME="$(new_home features)"
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "${FEATURES_HOME}" --instance cjfeatures --port 18087 \
    --no-wait --no-systemd --no-pull
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -q '^CODX_JUNIOR_ACTIVE_FEATURES=core$' "${FEATURES_HOME}/config/cjfeatures.env" 2>/dev/null; then
    pass "a default install writes the core (workspace-mode) feature profile"
else
    fail "a default install writes the core (workspace-mode) feature profile" \
        "$(grep CODX_JUNIOR_ACTIVE_FEATURES "${FEATURES_HOME}/config/cjfeatures.env" 2>/dev/null)"
fi
CHECKED=$((CHECKED + 1))
if out_has "core (workspace mode; company hidden)"; then
    pass "the default install reports the workspace-mode profile"
else
    fail "the default install reports the workspace-mode profile" "$(grep -i 'features' "${RUN_OUT}" | tail -n1)"
fi

FEATURES_ALL_HOME="$(new_home features-all)"
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "${FEATURES_ALL_HOME}" --instance cjfeaturesall --port 18088 \
    --features all --no-wait --no-systemd --no-pull
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -q '^CODX_JUNIOR_ACTIVE_FEATURES=all$' "${FEATURES_ALL_HOME}/config/cjfeaturesall.env" 2>/dev/null; then
    pass "--features all opts into the full product"
else
    fail "--features all opts into the full product" \
        "$(grep CODX_JUNIOR_ACTIVE_FEATURES "${FEATURES_ALL_HOME}/config/cjfeaturesall.env" 2>/dev/null)"
fi

FEATURES_FULL_HOME="$(new_home features-full)"
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "${FEATURES_FULL_HOME}" --instance cjfeaturesfull --port 18089 \
    --full-featured --no-wait --no-systemd --no-pull
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -q '^CODX_JUNIOR_ACTIVE_FEATURES=all$' "${FEATURES_FULL_HOME}/config/cjfeaturesfull.env" 2>/dev/null; then
    pass "--full-featured is the full-product alias"
else
    fail "--full-featured is the full-product alias" \
        "$(grep CODX_JUNIOR_ACTIVE_FEATURES "${FEATURES_FULL_HOME}/config/cjfeaturesfull.env" 2>/dev/null)"
fi

run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "$(new_home features-bad)" --features core,not-a-feature --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 1 ]] && both_has "unknown feature" && both_has "not-a-feature"; then
    pass "an unknown feature id fails the install"
else
    fail "an unknown feature id fails the install" "rc=${RUN_RC}"
fi

# An upgrade must preserve the chosen profile, not silently fall back to core.
run "${ROOT}" bash "${INSTALLER}" --mode local --provider deepseek --key sk-test \
    --home "${FEATURES_ALL_HOME}" --instance cjfeaturesall --port 18088 \
    --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "features=all"; then
    pass "a re-run preserves the installed feature profile"
else
    fail "a re-run preserves the installed feature profile" "rc=${RUN_RC} $(grep -i features "${RUN_OUT}" | tail -n1)"
fi

# Hidden codx-junior project flag.
run "${ROOT}" bash "${INSTALLER}" --mode local --hidden --provider deepseek --key sk-test \
    --home "$(new_home hidden)" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "hidden=1"; then
    pass "--hidden marks the codx-junior project as hidden"
else
    fail "--hidden marks the codx-junior project as hidden" "rc=${RUN_RC}"
fi

# Upgrade without --image must move a pinned saved tag back to latest.
run "${ROOT}" env CODX_JUNIOR_IMAGE=codxjuniorai/codx-junior:old \
    bash "${INSTALLER}" --mode local --update --dry-run \
    --provider deepseek --key sk-test --home "$(new_home upgrade)"
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "image=codxjuniorai/codx-junior:latest"; then
    pass "--update refreshes the app image unless --image is explicit"
else
    fail "--update refreshes the app image unless --image is explicit" "rc=${RUN_RC}"
fi

# `update --dry-run` must never touch Docker.
: >"${STUB_LOG}"
run "${ROOT}" env DOCKER_FAIL=1 bash "${INSTALLER}" --mode local --dry-run --no-pull \
    --provider deepseek --key sk-test --home "$(new_home update-dry)" update
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 && ! -s "${STUB_LOG}" ]]; then
    pass "update --dry-run does not call docker"
else
    fail "update --dry-run does not call docker" "rc=${RUN_RC}"
fi

# Remove an installation and its generated files.
REMOVE_HOME="$(new_home remove-home)"
mkdir -p "${REMOVE_HOME}"
printf 'CODX_JUNIOR_MODE=local\nCODX_JUNIOR_INSTANCE=cjremove\n' >"${REMOVE_HOME}/.env"
printf 'name: cjremove\n' >"${REMOVE_HOME}/docker-compose.yaml"
printf 'CODX_JUNIOR_INSTANCE=cjremove\n' >"${REMOVE_HOME}/installation.env"
: >"${STUB_LOG}"
run "${ROOT}" env CODX_JUNIOR_INSTALLER_STATE="${REMOVE_HOME}/installation.env" \
    bash "${INSTALLER}" --mode local --home "${REMOVE_HOME}" --instance cjremove \
    --env-file "${REMOVE_HOME}/.env" remove --delete-files --confirm-delete
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -qF 'down -v --remove-orphans' "${STUB_LOG}" \
    && [[ ! -e "${REMOVE_HOME}/.env" && ! -e "${REMOVE_HOME}/docker-compose.yaml" ]]; then
    pass "remove --delete-files removes resources and generated files"
else
    fail "remove --delete-files removes resources and generated files" "rc=${RUN_RC}"
fi

# Remove assistant supplies missing settings and defaults to keeping files.
ASSIST_HOME="$(new_home remove-assistant)"
run_input "${ASSIST_HOME}
local
cjassistant

n" "${ROOT}" bash "${INSTALLER}" remove
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && err_has "Remove settings" \
    && both_has "No files will be removed" && both_has "irreversible"; then
    pass "remove assistant asks for missing settings and defaults to keeping files"
else
    fail "remove assistant asks for missing settings and defaults to keeping files" "rc=${RUN_RC}"
fi

# DIND removal also deletes the nested-storage volume.
DIND_REMOVE_HOME="$(new_home dind-remove)"
mkdir -p "${DIND_REMOVE_HOME}"
printf 'CODX_JUNIOR_MODE=dind\nCODX_JUNIOR_INSTANCE=cjdindremove\nCODX_DIND_STORAGE=cjdindremove-dind\n' >"${DIND_REMOVE_HOME}/.env"
printf 'name: cjdindremove\n' >"${DIND_REMOVE_HOME}/docker-compose.yaml"
: >"${STUB_LOG}"
run "${ROOT}" env CODX_JUNIOR_INSTALLER_STATE="${DIND_REMOVE_HOME}/installation.env" \
    bash "${INSTALLER}" --mode dind --home "${DIND_REMOVE_HOME}" --instance cjdindremove \
    --env-file "${DIND_REMOVE_HOME}/.env" remove --delete-files --confirm-delete
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && grep -qF 'volume rm cjdindremove-dind' "${STUB_LOG}" \
    && [[ ! -e "${DIND_REMOVE_HOME}/docker-compose.yaml" ]]; then
    pass "remove deletes the DIND volume and generated files"
else
    fail "remove deletes the DIND volume and generated files" "rc=${RUN_RC}"
fi

# --- 11. config command ------------------------------------------------------
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
run_input "${TMP}/target-local
local
n





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
if err_has "Deployment mode (local|server|dind)"; then
    pass "wizard prints the mode prompt with its default"
else
    fail "wizard prints the mode prompt with its default"
fi

run_input "${TMP}/target-dind
dind
n
demo.codx-junior.ai
slave
traefik
letsencrypt
websecure
y




deepseek
sk-test

y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "mode      : dind (server)" && out_has "sysbox    : 1"; then
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

# --- 11. dind slave joins the external Traefik network ----------------------
run_input "${TMP}/target-dind2
dind
n
demo.codx-junior.ai
slave
traefik
letsencrypt
websecure
y




deepseek
sk-test

y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "traefik_mode=slave" && out_has "dind_mode=server"; then
    pass "dind slave keeps the inner installation in server mode"
else
    fail "dind slave keeps the inner installation in server mode" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "docker-compose.slave.yaml" && ! out_has "docker-compose.local.yaml"; then
    pass "dind slave joins the Traefik network without a local publish overlay"
else
    fail "dind slave joins the Traefik network without a local publish overlay"
fi
CHECKED=$((CHECKED + 1))
if out_has "domains=demo.codx-junior.ai" && out_has "public_url=https://demo.codx-junior.ai"; then
    pass "dind slave keeps the public domain"
else
    fail "dind slave keeps the public domain"
fi

# --- 12. dev option -----------------------------------------------------------
run_input "${TMP}/target-dev
local
y





deepseek
sk-test

n
y" "${ROOT}" bash "${INSTALLER}" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "mode=local" && out_has "dev_mode=1"; then
    pass "interactive dev option selects the live checkout"
else
    fail "interactive dev option selects the live checkout" "rc=${RUN_RC} $(head -n1 "${RUN_ERR}")"
fi
CHECKED=$((CHECKED + 1))
if out_has "image=codxjuniorai/codx-junior:dev" && out_has "docker-compose.dev.yaml"; then
    pass "dev option uses the dev image and overlay"
else
    fail "dev option uses the dev image and overlay"
fi

run "${ROOT}" bash "${INSTALLER}" --mode local --dev --traefik slave \
    --domain dev.codx-junior.ai --proxy-network traefik \
    --provider deepseek --key sk-test --home "${ROOT}/tmp/dev-slave" --dry-run
CHECKED=$((CHECKED + 1))
if [[ ${RUN_RC} -eq 0 ]] && out_has "docker-compose.dev.yaml" \
    && out_has "docker-compose.slave.yaml" && out_has "docker-compose.local.yaml"; then
    pass "dev option composes with slave Traefik and local access"
else
    fail "dev option composes with slave Traefik and local access" "rc=${RUN_RC}"
fi
CHECKED=$((CHECKED + 1))
if out_has "public_url=https://dev.codx-junior.ai" && out_has "source=${ROOT}"; then
    pass "dev option keeps the public domain and checkout source path"
else
    fail "dev option keeps the public domain and checkout source path"
fi

# --- 13. the root front door delegates ---------------------------------------
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
