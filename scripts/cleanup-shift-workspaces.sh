#!/usr/bin/env bash
#
# Remove the per-shift branches and worktrees created before the actor-scoped
# workspace change.
#
# Why this exists
# ---------------
# Every autonomous shift used to create a new task card and therefore a new
# `codx/task/Shift-<timestamp>-<hash>` branch plus a full git worktree. The
# branch was committed but never merged, so the repository accumulated one
# branch and ~600 MB checkout per shift. Most branches contain only a
# `.pytest_cache` symlink; one contains real code.
#
# The new runtime reuses ONE `agent/shift/<actor>` branch per actor and merges
# it when the shift succeeds. This script cleans up the old generation only.
#
# Safety
# ------
# * Dry run by default. Nothing is changed until `--apply` is passed.
# * Worktrees with real uncommitted changes are skipped.
# * Branches with non-cache changes are skipped, so the one real stranded
#   change is preserved for inspection. Use `--force` only when you really
#   want to discard it (the task will be re-run later).
# * Only `codx/task/Shift-*` branches are considered. Task branches and the
#   stable `agent/shift/*` branches are never touched.
# * Registry entries whose branch still exists are kept.
#
# Usage
# -----
#   scripts/cleanup-shift-workspaces.sh                 # report only
#   scripts/cleanup-shift-workspaces.sh --apply         # clean cache-only shifts
#   scripts/cleanup-shift-workspaces.sh --apply --force # also discard real changes
#   scripts/cleanup-shift-workspaces.sh --apply --gc    # then git gc --prune=now
#
# Run it from any directory after pushing the latest changes. Back up the
# repository first if anything in the working tree is not committed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

APPLY=0
FORCE=0
RUN_GC=0
BASE_BRANCH="${BASE_BRANCH:-}"

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while (($#)); do
    case "$1" in
        --apply)
            APPLY=1
            ;;
        --force)
            FORCE=1
            ;;
        --gc)
            RUN_GC=1
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage 2
            ;;
    esac
    shift
done

if [[ -z "${BASE_BRANCH}" ]]; then
    if git show-ref --verify --quiet refs/heads/main; then
        BASE_BRANCH="main"
    elif git show-ref --verify --quiet refs/heads/master; then
        BASE_BRANCH="master"
    else
        BASE_BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
        BASE_BRANCH="${BASE_BRANCH#origin/}"
    fi
fi
if [[ -z "${BASE_BRANCH}" ]] || ! git rev-parse --verify --quiet "${BASE_BRANCH}^{commit}" >/dev/null; then
    echo "Cannot determine a base branch. Set BASE_BRANCH=main." >&2
    exit 1
fi

# Cache-only changes are not "real" dirty state for this cleanup.
real_dirty() {
    local path="$1"
    local status
    status="$(git -C "${path}" status --porcelain 2>/dev/null || true)"
    [[ -n "${status}" ]] || return 1
    if printf '%s\n' "${status}" | grep -Ev '(^|[ /])(\.pytest_cache|\.mypy_cache|\.ruff_cache|\.pnpm-store|\.yarn|\.tox|__pycache__)(/|$)|(^|[ /])\.coverage$|(^|[ /])htmlcov/' >/dev/null; then
        return 0
    fi
    return 1
}

non_cache_changes() {
    local branch="$1"
    local changed
    changed="$(git diff --name-only "${BASE_BRANCH}...${branch}" 2>/dev/null || true)"
    [[ -n "${changed}" ]] || return 1
    if printf '%s\n' "${changed}" | grep -Ev '(^|/)\.pytest_cache(/|$)' >/dev/null; then
        return 0
    fi
    return 1
}

remove_worktree() {
    local path="$1"
    local branch="$2"
    local locked="$3"

    if (( ! APPLY )); then
        printf 'WOULD remove worktree %s\n' "${path}"
        printf 'WOULD delete branch   %s\n' "${branch}"
        return
    fi

    if [[ "${locked}" == "locked" ]]; then
        git worktree unlock "${path}" >/dev/null 2>&1 || true
    fi

    # A cache-only worktree has no real dirt, so --force only discards the
    # generated files we intentionally ignored above.
    local force_flag=""
    if (( FORCE )) || ! real_dirty "${path}"; then
        force_flag="--force"
    fi

    if ! git worktree remove ${force_flag} "${path}"; then
        printf 'KEPT branch %s: could not remove worktree %s\n' "${branch}" "${path}"
        return
    fi
    if git show-ref --verify --quiet "refs/heads/${branch}"; then
        if ! git branch -D "${branch}" >/dev/null; then
            printf 'KEPT branch %s: deletion failed\n' "${branch}"
            return
        fi
    fi
    printf 'removed worktree %s and branch %s\n' "${path}" "${branch}"
}

printf 'Repository:  %s\n' "${REPO_ROOT}"
printf 'Base branch: %s\n' "${BASE_BRANCH}"
printf 'Mode:        %s%s\n' \
    "$( ((APPLY)) && echo apply || echo 'dry run' )" \
    "$( ((FORCE)) && echo ' (force)' || true )"
printf '\n'

CURRENT_PATH=""
CURRENT_BRANCH=""
CURRENT_LOCKED=""
SKIPPED=0
HANDLED=0

handle_worktree() {
    local path="$1"
    local branch="${2#refs/heads/}"
    local locked="$3"

    [[ -n "${path}" && -n "${branch}" ]] || return 0
    [[ "${branch}" == codx/task/Shift-* ]] || return 0

    HANDLED=$((HANDLED + 1))

    if real_dirty "${path}"; then
        if (( ! FORCE )); then
            printf 'SKIP  %s: uncommitted changes (use --force to discard)\n' "${branch}"
            SKIPPED=$((SKIPPED + 1))
            return 0
        fi
    fi

    if non_cache_changes "${branch}"; then
        if (( ! FORCE )); then
            printf 'SKIP  %s: real code changes preserved (use --force to discard)\n' "${branch}"
            SKIPPED=$((SKIPPED + 1))
            return 0
        fi
        printf 'WARN  %s: discarding real code changes because --force was given\n' "${branch}"
    fi

    remove_worktree "${path}" "${branch}" "${locked}"
}

# `git worktree list --porcelain` ends each entry with a blank line.
while IFS= read -r line; do
    case "${line}" in
        "worktree "*)
            CURRENT_PATH="${line#worktree }"
            CURRENT_BRANCH=""
            CURRENT_LOCKED=""
            ;;
        "branch "*)
            CURRENT_BRANCH="${line#branch }"
            ;;
        "locked"*)
            CURRENT_LOCKED="locked"
            ;;
        "")
            handle_worktree "${CURRENT_PATH}" "${CURRENT_BRANCH}" "${CURRENT_LOCKED}"
            CURRENT_PATH=""
            CURRENT_BRANCH=""
            CURRENT_LOCKED=""
            ;;
    esac
done < <(git worktree list --porcelain; printf '\n')

if (( APPLY )); then
    git worktree prune >/dev/null 2>&1 || true

    # Drop registry entries whose branch no longer exists. Entries for kept
    # branches (the real change, task workspaces, actor workspaces) stay.
    python3 - "${REPO_ROOT}" <<'PY'
import json
import os
import subprocess
import sys

root = sys.argv[1]
path = os.path.join(root, ".codx", "task_workspaces", "index.json")
if not os.path.isfile(path):
    sys.exit(0)
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as ex:
    print(f"KEEP registry: cannot read {path}: {ex}")
    sys.exit(0)

workspaces = data.get("workspaces") or {}
kept = {}
removed = 0
for key, entry in workspaces.items():
    branch = str((entry or {}).get("branch") or "")
    if not branch.startswith("codx/task/Shift-"):
        kept[key] = entry
        continue
    exists = subprocess.run(
        ["git", "-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0
    if exists:
        kept[key] = entry
    else:
        removed += 1

if removed:
    data["workspaces"] = kept
    temporary = f"{path}.cleanup.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)
    print(f"removed {removed} stale task-workspace registry entr{'y' if removed == 1 else 'ies'}")
PY
else
    printf '\nDry run: no worktree, branch or registry entry was changed.\n'
fi

if (( APPLY && RUN_GC )); then
    printf '\nRunning git gc --prune=now ...\n'
    git gc --prune=now
fi

printf '\n'
printf 'Shift worktrees inspected: %s\n' "${HANDLED}"
printf 'Skipped (preserved):       %s\n' "${SKIPPED}"
printf 'Branches/worktrees left:   %s\n' "$(git branch --list 'codx/task/Shift-*' | wc -l | tr -d ' ')"
printf 'Stable actor branches:     %s\n' "$(git branch --list 'agent/shift/*' | wc -l | tr -d ' ')"
printf 'Task worktrees:            %s\n' "$(git worktree list | grep -c '/tasks/' || true)"
if (( ! APPLY )); then
    printf '\nRe-run with --apply to perform the cleanup.\n'
fi
