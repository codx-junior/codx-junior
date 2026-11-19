# Ready-to-apply patches

Both patches are **unapplied**. They were authored by autonomous shifts on the
`IT team` board, where the actor toolset has no shell and no hunk-edit tool
(`project_write_file` is whole-file only, and the files below are 2018 / 3604
lines). Applying them takes one `git apply` per file.

| # | File | Fixes | Patch | Diff |
|---|---|---|---|---|
| 001 | `codx/junior/tools/repository_tools.py` | `repository_commit` ran `git add -A` when no `paths` were given | `001-repository-commit-no-stage-all.patch` | 4 hunks, +6/−7 |
| 002 | `codx/junior/tools/project_manager_tools.py` | `update_task` advertised `profiles` but silently dropped it | `002-update-task-profiles.patch` | 5 hunks, +9/−2 |

## Apply

```bash
git apply --check docs/patches/001-repository-commit-no-stage-all.patch
git apply         docs/patches/001-repository-commit-no-stage-all.patch

git apply --check docs/patches/002-update-task-profiles.patch
git apply         docs/patches/002-update-task-profiles.patch
```

Restart the API afterwards: the tool registry and both modules are imported at
start-up, so nothing changes until the process reloads.

## Why 001 matters

`repository_tools.py:1447` computed `stage_all = not requested` and passed it to
`GitEngine.commit_changes` (`codx/junior/engine/git_engine.py:4157,4185` →
`_commit_all` → `git add -A`). A call without `paths` therefore committed every
uncommitted file in the tree, contradicting the tool's own description
(`repository_tools.py:1395-1398`) and the command handed to the CISO gate
(`ciso/gate.py` `guard_command` saw only `git commit -m …` while `git add -A`
ran). The patch makes `stage_all=False` unconditional and builds the gate
command from the same argument the body uses.

Verification after apply:

```
repository_commit(message="probe", paths=["README.md"])   # stages only README.md
git status --short                                        # other edits stay unstaged
```

## Why 002 matters

`project_manager_tools.py` advertised `profiles` in the `update_task` schema
(`:3483-3491`), but the dispatcher (`:3205-3225`) and `_action_update_task`
(`:2529-2546`) had no `profiles` parameter, so the value was dropped before the
metadata block (`:2615-2634`). Persistence already worked: `update_chat_metadata`
(`codx/junior/chat_manager.py:1232-1247`) sets any attribute present on `Chat`
(`profiles` at `codx/junior/db.py:460`).

Verification after apply:

```
update_task(chat_id="<id>", profiles=["software_developer"])   # then get_task
update_task(chat_id="<id>", profiles=[])                       # clears
update_task(chat_id="<id>")                                    # still raises "Nothing to update"
```

## Hunks are counted, not guessed

Both artifacts were derived from the real file regions and their hunk headers
counted from the body (`old,count` / `new,count`), with a consistent
`new − old` offset across hunks. 001 needed two corrections before that was
true; the failure mode is recorded in
[`001-repository-commit-no-stage-all.md`](./001-repository-commit-no-stage-all.md#patch-history).
If `git apply --check` ever reports *corrupt patch*, the header is the suspect,
not the content.

## Still missing (not covered by these patches)

- No regression test exists for either behaviour; the suggested cases are in
  each `.md` companion file.
- Both fixes are untested against the live registry — they were verified
  statically (`file:line` evidence in the companion files). Run `pytest api`
  after applying.
