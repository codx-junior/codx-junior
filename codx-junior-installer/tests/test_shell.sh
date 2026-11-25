#!/usr/bin/env bash
# =============================================================================
# tests/test_shell.sh -- the SHELL gate for codx-junior-installer
#
# This repository is shell + yaml: there is no pytest, so the gate that decides
# whether a change "is verified" has to be shell. Three checks, over the files
# that actually ship the demo:
#
#   1. bash -n         syntax check of every tracked *.sh file
#   2. shellcheck      static analysis of every tracked *.sh file (when installed)
#   3. compose config  the yaml under demo/ must resolve (when docker is present)
#
# Output is TAP-like: `ok N - case` / `not ok N - case`. Exit 0 only when every
# check that could run passed; a check that cannot run is reported as SKIP with
# the reason, never as a silent pass.
#
# Run from anywhere:
#   bash codx-junior-installer/tests/test_shell.sh
#
# Prove the gate can actually fail (writes a broken file in a temp dir):
#   bash codx-junior-installer/tests/test_shell.sh --self-test
# =============================================================================
set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(cd -- "${HERE}/.." && pwd -P)

CHECKED=0
FAILED=0
SKIPPED=0

pass() { printf 'ok %d - %s\n' "${CHECKED}" "$1"; }
fail() {
    FAILED=$((FAILED + 1))
    printf 'not ok %d - %s\n' "${CHECKED}" "$1"
    if [ -n "${2:-}" ]; then printf '       %s\n' "$2"; fi
    return 0
}
skip() {
    SKIPPED=$((SKIPPED + 1))
    printf 'ok %d - %s # SKIP %s\n' "${CHECKED}" "$1" "${2:-not available}"
}
check() { CHECKED=$((CHECKED + 1)); }

# First line of a multi-line tool output, for the one-line failure detail.
first_line() { printf '%s' "$1" | sed -n '1p'; }

# --- the shell files under test ----------------------------------------------
# Tracked files only: an untracked scratch script is not part of the repo and a
# gate over it would be noise.
shell_files() {
    if git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "${ROOT}" ls-files '*.sh'
    else
        ( cd "${ROOT}" && find . -name '*.sh' -not -path './.git/*' -not -path './.codx/*' | sed 's|^\./||' )
    fi | sort -u
}

# --- 1. bash -n ---------------------------------------------------------------
check_bash_syntax() {
    local file out
    while IFS= read -r file; do
        [ -n "${file}" ] || continue
        if [ ! -f "${ROOT}/${file}" ]; then
            check; skip "bash -n ${file}" "file is listed but not on disk"
            continue
        fi
        check
        if out=$(bash -n "${ROOT}/${file}" 2>&1); then
            pass "bash -n ${file}"
        else
            fail "bash -n ${file}" "$(first_line "${out}")"
        fi
    done < <(shell_files)
}

# --- 2. shellcheck ------------------------------------------------------------
check_shellcheck() {
    local file out
    if ! command -v shellcheck >/dev/null 2>&1; then
        check
        skip "shellcheck" "shellcheck is not installed; bash -n ran but style/static analysis did not"
        return 0
    fi
    while IFS= read -r file; do
        [ -n "${file}" ] || continue
        [ -f "${ROOT}/${file}" ] || continue
        check
        if out=$(shellcheck --format=gcc -- "${ROOT}/${file}" 2>&1); then
            pass "shellcheck ${file}"
        else
            fail "shellcheck ${file}" "$(first_line "${out}")"
        fi
    done < <(shell_files)
}

# --- 3. docker compose config -------------------------------------------------
check_compose_config() {
    local file out
    local files=()
    local candidate
    for candidate in "${ROOT}"/demo/*.yaml "${ROOT}"/demo/*.yml; do
        [ -f "${candidate}" ] || continue
        case "${candidate}" in
            *docker-compose*) files+=("${candidate}") ;;
        esac
    done
    if [ "${#files[@]}" -eq 0 ]; then
        check
        skip "docker compose config" "no demo/docker-compose*.yaml to validate"
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        check
        skip "docker compose config" "docker is not installed on this host"
        return 0
    fi
    if ! docker compose version >/dev/null 2>&1; then
        check
        skip "docker compose config" "the docker compose v2 plugin is not available"
        return 0
    fi
    for file in "${files[@]}"; do
        check
        if out=$(docker compose -f "${file}" config -q 2>&1); then
            pass "docker compose config $(basename -- "${file}")"
        elif printf '%s' "${out}" | grep -q 'env file .* not found' &&
            docker compose -f "${file}" config -q --no-env-resolution >/dev/null 2>&1; then
            # demo/.env is gitignored on purpose (only demo/.env.example ships),
            # so a clean checkout can never resolve it and this check would fail
            # for every contributor. The yaml is still fully validated -- only the
            # local values are absent. The reason is named in the TAP line, so it
            # is a documented pass, never a silent one, and a real yaml error
            # still fails the second attempt.
            pass "docker compose config $(basename -- "${file}") (env_file absent: validated with --no-env-resolution)"
        else
            fail "docker compose config $(basename -- "${file}")" \
                "$(first_line "$(printf '%s' "${out}" | grep -v '^$' | sed -n '1p')")"
        fi
    done
}

# --- --self-test: the gate must be able to fail -------------------------------
# A gate nobody has seen fail is a gate nobody can trust. This builds a known
# broken script and a known good one in a temp dir and asserts that `bash -n`
# rejects the first and accepts the second.
self_test() {
    local tmp rc_bad rc_good
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/cj-shell-gate.XXXXXX") || exit 1
    printf 'if [ 1 -eq 1 ; then\n  echo broken\nfi\n' >"${tmp}/broken.sh"
    printf 'set -euo pipefail\necho fine\n' >"${tmp}/good.sh"

    bash -n "${tmp}/broken.sh" >/dev/null 2>&1
    rc_bad=$?
    bash -n "${tmp}/good.sh" >/dev/null 2>&1
    rc_good=$?

    printf '# self-test: deliberate syntax error must be caught\n'
    CHECKED=$((CHECKED + 1))
    if [ "${rc_bad}" -ne 0 ]; then
        pass "self-test catches a deliberate syntax error"
    else
        fail "self-test catches a deliberate syntax error" "bash -n accepted a broken script"
    fi
    CHECKED=$((CHECKED + 1))
    if [ "${rc_good}" -eq 0 ]; then
        pass "self-test accepts a valid script"
    else
        fail "self-test accepts a valid script" "bash -n rejected a valid script"
    fi

    if command -v shellcheck >/dev/null 2>&1; then
        CHECKED=$((CHECKED + 1))
        if shellcheck --format=gcc -- "${tmp}/broken.sh" >/dev/null 2>&1; then
            fail "self-test catches a shellcheck finding" "shellcheck accepted a broken script"
        else
            pass "self-test catches a shellcheck finding"
        fi
    else
        CHECKED=$((CHECKED + 1))
        skip "self-test shellcheck finding" "shellcheck is not installed"
    fi

    rm -rf "${tmp}"
    summary
}

summary() {
    printf '# checked %d, failed %d, skipped %d\n' "${CHECKED}" "${FAILED}" "${SKIPPED}"
    if [ "${FAILED}" -gt 0 ]; then
        printf '# SHELL gate: FAIL\n'
        return 1
    fi
    printf '# SHELL gate: PASS\n'
    return 0
}

case "${1:-}" in
    --self-test) self_test; exit $? ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") : ;;
    *) printf 'test_shell.sh: unknown option: %s\n' "$1" >&2; exit 2 ;;
esac

printf '# SHELL gate: %s\n' "${ROOT}"
check_bash_syntax
check_shellcheck
check_compose_config
summary
