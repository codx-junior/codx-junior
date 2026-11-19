# 001 — `repository_commit` must never stage everything

**Card:** `e816cc41` — Add a command-execution tool to the registry (scope-corrected to
`repository_stage` / `repository_commit`)
**Status:** fix authored, **not applied** (no patch/hunk tool in this checkout — see `76d97f04`)
**Patch:** [`001-repository-commit-no-stage-all.patch`](./001-repository-commit-no-stage-all.patch)

## The defect

`codx/junior/tools/repository_tools.py` documents this contract (lines 1395-1398):

> Without `paths` the commit contains what is already staged (`git commit -m`). With
> `paths` only those files are staged first and then committed, so changes nobody named
> are never swept into the commit.

The implementation did the opposite:

```python
1447|    stage_all = not requested          # no paths -> True
1448|    if stage_all:
1449|        command = f'git commit -m "{message}"'
```

`stage_all=True` is not cosmetic — it reaches the engine:

| Hop | Location | Effect |
|---|---|---|
| tool | `repository_tools.py:1468-1470` | `engine.commit_changes(stage_all=True)` |
| engine | `codx/junior/engine/git_engine.py:4185-4188` | `if stage_all: self._commit_all(...)` |
| engine | `codx/junior/engine/git_engine.py:3183` | `_commit_all` → **`git add -A`** |

Consequences, all reproduced statically:

1. `repository_commit(message="x")` with no paths runs `git add -A` and commits **every**
   dirty file in the tree — the sweep the card forbids.
2. The command handed to the CISO gate (`ciso/gate.py` `guard_command`) is
   `git commit -m "x"`, i.e. the gate reviews a **strict subset** of what actually runs.
   A path-less commit is therefore an unreviewed `git add -A`.
3. The report line is inverted too (`repository_tools.py:1487`): a path-less commit
   printed "the files given in `paths`".

`repository_stage` is clean: it requires explicit paths (≤ `MAX_STAGE_PATHS`), has no
stage-all switch and stages through `GitWorkspaceEngine.stage_files`
(`codx/junior/engine/git_workspace.py:209`).

## The fix

Stop deriving staging from the *absence* of paths. Staging is done explicitly by
`engine.stage_files(requested)` and the commit never stages on its own:

* `stage_all=False` unconditionally (`git_engine.py:4189-4193` then runs a plain
  `git commit -m` against the index, exactly what the docstring promises).
* The gate command is built from `requested`, so gate and execution always agree.
* `stage_all` disappears from the function; the JSON report exposes `staged_paths`.

Four hunks, `+6/−7`. `stage_all` occurs only inside `repository_commit`
(verified: 5 occurrences, lines 1447/1448/1469/1487/1505), so the patch is self-contained.

## Apply

```bash
cd <repo root>
git apply --check docs/patches/001-repository-commit-no-stage-all.patch
git apply docs/patches/001-repository-commit-no-stage-all.patch
```

## Verify

```bash
# 1. no path-less add: this must stay empty
git diff docs/patches/001-repository-commit-no-stage-all.patch   # sanity: file exists, tracked

# 2. behaviour, with a dirty tree
#    a) repository_commit(message="chore: probe")  -> commits only already-staged files
#    b) repository_commit(message="chore: probe", paths=["README.md"])
#       -> stages README.md only; another dirty file stays dirty
```

Regression test to add alongside (none exists today — neither tool has a test):

* dirty tree with `a.txt` staged and `b.txt` dirty → `repository_commit(message=...)`
  must return `success` with `b.txt` **still modified** (`git status --porcelain`).

## Patch history

The `.patch` file was corrected twice before it became appliable; both defects are
recorded so they are not repeated:

1. Hunk headers were derived from the *expected* line numbers instead of the counted
   old/new body lengths, which desynchronises every later hunk.
2. Hunk 2 omitted the `        )` context line (file line 1470) while still declaring
   `@@ -1465,8 +1464,8 @@` — an 8-line body carrying 7 lines. `git apply` rejects that
   as a corrupt patch.

Current state: all four hunks were re-derived from the file (`repository_tools.py:1440-1512`)
and counted line by line (`11/10`, `8/8`, `7/7`, `7/7`), with a consistent offset
(`new − old = −1` in every hunk).

## Related cards

* `76d97f04` — actors cannot apply a 13-line change to a 2018-line file: `file_patch`
  is not reachable (`codx/junior/tools/file_patch_tools.py` and
  `codx/junior/api/file_patch.py` **do not exist in this checkout**), so this patch is
  the deliverable until a hunk editor is exposed.
* `f8e10ad1` (DECISION D1) — each of these shifts ends plan-only for the same reason.
* `4cce4b23`, `39a2d6a4` — both need the same git plumbing and are blocked on the same
  missing capability.
