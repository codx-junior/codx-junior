#!/usr/bin/env bash
# =============================================================================
# tests/qa_suite.sh -- policy-driven test dispatcher for codx-junior-installer
#
# WHY THIS EXISTS
#   The platform QA engine (`qa_run_tests`) does not read `qa_policy.suites`.
#   A correctly declared shell suite is answered with `tests.no-suite`, every
#   other name is prefixed with `pytest`, and `changed_files` comes back 0 on
#   a real diff. Measured, reproduced and recorded on card `de08e8d9`; the
#   engine lives outside this repository and cannot be fixed from here.
#
#   This script gives the repository the semantics the engine is supposed to
#   provide, so the gate is runnable today and does not depend on it:
#
#     * the suite list comes from the policy file -- one source of truth,
#     * `command` is executed as an argv array: no shell string, no
#       interpreter prefixing (`pytest:shell` and friends),
#     * the runner is resolved BEFORE dispatch and reported as
#       `runner-missing` instead of failing afterwards with no explanation,
#     * the suite exit code becomes a verdict: 0 -> pass, non-zero -> fail.
#
# USAGE
#   tests/qa_suite.sh --list                # suites declared in the policy
#   tests/qa_suite.sh shell                 # run one suite
#   tests/qa_suite.sh --all                 # every suite, stops at first failure
#   tests/qa_suite.sh --json shell          # machine-readable verdict
#   tests/qa_suite.sh --check               # parse policy + verify every runner
#   QA_POLICY=/path/policy.json tests/qa_suite.sh shell
#
# EXIT CODES
#   0 pass | 1 suite failed | 2 usage or policy error | 3 runner missing
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
POLICY="${QA_POLICY:-${REPO_ROOT}/.codx/qa_policy.json}"

# Suites declare repository-relative paths, so the dispatcher always runs from
# the repository root: `bash tests/qa_suite.sh shell` and `npm test` must behave
# the same no matter which directory the caller is in.
cd -- "${REPO_ROOT}"

COMMAND=()

usage() {
    sed -n '22,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- policy access -----------------------------------------------------------
# JSON is parsed once, by one implementation, so `--list`, `--check` and the
# dispatcher can never disagree about what the policy says.
policy_query() {
    # The policy is JSON: one parser, used by --list/--check/dispatch alike. If it
    # is missing, say so here instead of failing with "command not found".
    if ! command -v python3 > /dev/null 2>&1; then
        printf 'qa_suite: python3 is required to read %s (not found in PATH)\n' \
            "${POLICY}" >&2
        return 2
    fi
    python3 - "${POLICY}" "$@" <<'PY'
import json
import sys

path = sys.argv[1]
mode = sys.argv[2]
name = sys.argv[3] if len(sys.argv) > 3 else ""

try:
    with open(path, "r", encoding="utf-8") as handle:
        policy = json.load(handle)
except FileNotFoundError:
    sys.stderr.write("qa_suite: policy not found: {0}\n".format(path))
    sys.exit(2)
except ValueError as error:
    sys.stderr.write("qa_suite: invalid JSON in {0}: {1}\n".format(path, error))
    sys.exit(2)

suites = policy.get("suites")
if not isinstance(suites, dict) or not suites:
    sys.stderr.write("qa_suite: {0} declares no suites\n".format(path))
    sys.exit(2)

if mode == "list":
    for suite in suites:
        print(suite)
    sys.exit(0)

if mode == "command":
    if name not in suites:
        sys.stderr.write(
            "qa_suite: unknown suite '{0}' (declared: {1})\n".format(
                name, ", ".join(sorted(suites))
            )
        )
        sys.exit(2)
    command = suites[name].get("command")
    if not isinstance(command, list) or not command:
        sys.stderr.write(
            "qa_suite: suite '{0}' has no 'command' argv array\n".format(name)
        )
        sys.exit(2)
    for item in command:
        print(item)
    sys.exit(0)

sys.stderr.write("qa_suite: unsupported query '{0}'\n".format(mode))
sys.exit(2)
PY
}

resolve_command() {
    local suite="$1"
    local raw
    if ! raw="$(policy_query command "${suite}")"; then
        return 2
    fi
    # A portable read loop, not `mapfile`: mapfile is bash 4+, and on the
    # /bin/bash 3.2 shipped by macOS it aborts the gate with "command not
    # found" instead of a verdict. One item per line, empty lines dropped.
    COMMAND=()
    local line
    while IFS= read -r line; do
        [ -n "${line}" ] || continue
        COMMAND+=("${line}")
    done <<< "${raw}"
    if [ "${#COMMAND[@]}" -eq 0 ] || [ -z "${COMMAND[0]}" ]; then
        printf 'qa_suite: suite %s resolved to an empty command\n' "${suite}" >&2
        return 2
    fi
}

# --- reporting ---------------------------------------------------------------
report() { # status suite detail
    local status="$1" suite="$2" detail="${3:-}"
    if [ "${JSON}" -eq 1 ]; then
        printf '{"suite":"%s","status":"%s","detail":"%s"}\n' \
            "${suite}" "${status}" "${detail//\"/\\\"}"
    else
        printf '%-14s %-10s %s\n' "${suite}" "${status}" "${detail}"
    fi
}

# --- suites ------------------------------------------------------------------
check_runner() { # suite -> 0 ok, 3 missing
    local suite="$1"
    resolve_command "${suite}" || return $?
    local runner="${COMMAND[0]}"
    if ! command -v "${runner}" > /dev/null 2>&1; then
        report runner-missing "${suite}" "${runner} not found in PATH"
        return 3
    fi
    # Every non-flag argument must exist inside the repository. A suite whose
    # target file is gone is a broken gate, not an "ok" one -- so the path is
    # checked for every declared suite, slashes included.
    local candidate
    for candidate in "${COMMAND[@]:1}"; do
        case "${candidate}" in
            -*) continue ;;
        esac
        local target="${candidate}"
        case "${target}" in
            /*) ;;
            *) target="${REPO_ROOT}/${candidate}" ;;
        esac
        if [ ! -e "${target}" ]; then
            report missing-file "${suite}" "${candidate} does not exist"
            return 1
        fi
    done
    report ok "${suite}" "${COMMAND[*]}"
    return 0
}

run_suite() {
    local suite="$1"
    resolve_command "${suite}" || return $?
    local runner="${COMMAND[0]}"
    if ! command -v "${runner}" > /dev/null 2>&1; then
        report runner-missing "${suite}" "${runner} not found in PATH"
        return 3
    fi
    [ "${JSON}" -eq 1 ] || printf '# suite %s -> %s\n' "${suite}" "${COMMAND[*]}"
    local started=${SECONDS}
    local code=0
    "${COMMAND[@]}" || code=$?
    local elapsed=$((SECONDS - started))
    if [ "${code}" -eq 0 ]; then
        report pass "${suite}" "${elapsed}s"
        return 0
    fi
    report fail "${suite}" "exit ${code} after ${elapsed}s"
    return 1
}

# --- main --------------------------------------------------------------------
JSON=0
if [ "$#" -eq 0 ]; then
    usage >&2
    exit 2
fi

mode=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --json) JSON=1; shift ;;
        --list) mode="list"; shift ;;
        --all) mode="all"; shift ;;
        --check) mode="check"; shift ;;
        -h|--help) usage; exit 0 ;;
        --*) printf 'qa_suite: unknown option %s\n' "$1" >&2; exit 2 ;;
        *) mode="suite"; TARGET="$1"; shift ;;
    esac
done

case "${mode}" in
    list)
        policy_query list
        ;;
    check)
        # Read the policy eagerly: a missing or invalid policy must abort with
        # exit 2, never be mistaken for "no suites" and reported as a pass.
        rc=0
        suites_out="$(policy_query list)" || exit $?
        while IFS= read -r suite; do
            [ -n "${suite}" ] || continue
            check_runner "${suite}" || { rc=$?; break; }
        done <<< "${suites_out}"
        exit "${rc}"
        ;;
    all)
        rc=0
        suites_out="$(policy_query list)" || exit $?
        while IFS= read -r suite; do
            [ -n "${suite}" ] || continue
            run_suite "${suite}" || { rc=$?; break; }
        done <<< "${suites_out}"
        exit "${rc}"
        ;;
    suite)
        run_suite "${TARGET}"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
