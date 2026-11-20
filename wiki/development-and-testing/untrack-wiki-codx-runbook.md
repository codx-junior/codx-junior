# Runbook — untrack `wiki/.codx` (index surgery)

> Companion to card **`b370c370`** (follow-up of `4966baf0`, CISO `.codx` control plane in git).
> Written **2026-11-20T08:4xZ** by `software_developer`. The shift that wrote this file had **no git
> exec tool** (`project_changes`, `repository_overview` and `project_read_file` are read-only), so the
> commands below are **unexecuted**. They are ordered, idempotent-checkable and each one carries the
> output it must produce. Do not improvise a different order.

---

## 1. Corrected premise (measured, do not re-derive)

The card was written on shift 5 (2026-11-19T09:5xZ), when `.codx` entries were sitting in the **index**
as `A` additions. Between then and 2026-11-20T08:4xZ the tree changed. Re-measured this shift:

| Probe | Verbatim result |
|---|---|
| `project_changes(scope="staged")` | `No changes found.` |
| `repository_overview.staged` | **only** `docs/.vitepress/config.mts` (`M`) |
| `project_changes(scope="untracked")` | `No changes found.` |
| `project_changes(scope="all")` | `4 file(s), +212 / -40` — all `M` under `.codx/` |
| `project_changes(paths=[".codx/autonomy/state.json"], include_patch=true)` | `diff --git a/.codx/autonomy/state.json b/.codx/autonomy/state.json` / `index d4041f2..262a947 100644` |

Consequences:

1. **Nothing is staged under `.codx` any more.** There is no `A` pile to unstage; `git status --porcelain wiki/.codx`
   will show ` M`/` D` drift, not `A`.
2. **The four `.codx` files are TRACKED AT `HEAD`.** `M` (not `A`) plus the `a/…` `b/…` patch header proves
   they exist in the committed tree. So the surgery is *"remove tracked control-plane files from the index"*,
   not *"unstage additions"*. `git rm -r --cached` is still the right verb, but the acceptance state
   (`git status --porcelain wiki/.codx` empty) is only reachable **after the commit**, not before it.
3. **The stray `wiki/wiki/.gitignore` is still on disk** (21 lines, the stale shift-5 draft whose header
   wrongly claims `.codx` is untracked), and it is **no longer `A` or `?`** — i.e. it was committed.
   It must be removed from the index and from disk.

---

## 2. Command sequence

Run **from the repository root**, not from the project root:

```bash
cd /home/codx-junior-projects/codx-junior/codx-junior-platform
```

### Step 0 — pre-flight (quote all four outputs)

```bash
git rev-parse --short HEAD
git status --porcelain wiki/.codx
git ls-files wiki/.codx | wc -l
git ls-files wiki/wiki
git check-ignore --no-index -v wiki/.codx/tasks wiki/.codx/harness/audit.jsonl wiki/.codx/autonomy/state.json
```

Expected:
- `rev-parse` → the SHA **before** the surgery (this is the "commit SHA (before)" for the card comment).
- `status --porcelain wiki/.codx` → non-empty (` M` / ` M` / ` D` drift). **Not** empty. If it is empty, stop:
  the tree moved again and this runbook must be re-measured.
- `ls-files wiki/.codx | wc -l` → **N > 0** (the number of tracked control-plane files).
- `ls-files wiki/wiki` → `wiki/wiki/.gitignore` (one line).
- `check-ignore` → three lines `wiki/.gitignore:24:.codx/`, exit 0.

Capture the repo-wide number that card `4966baf0` needs:

```bash
git status --porcelain | wc -l
```

### Step 1 — isolate the index

`git commit` commits **the whole index**. Today the index holds one unrelated entry
(`docs/.vitepress/config.mts`, staged by another card). Either leave it if the owner agreed to ship it,
or park it for the duration of this commit:

```bash
git diff --cached --name-status
# If the only entries are wiki/.codx/** and wiki/wiki/.gitignore -> go to Step 2.
# If an unrelated path is listed -> park it:
git restore --staged -- docs/.vitepress/config.mts
```

> `git restore --staged` only touches the index; the working-tree file is unchanged. Re-stage it at Step 4.

### Step 2 — untrack (index only, working tree untouched)

```bash
git rm -r --cached --quiet -- wiki/.codx
git rm -f --cached --quiet -- wiki/wiki/.gitignore
```

- `--cached` never deletes the files from disk; the control plane keeps running.
- Add `-f` to the first command if git refuses with *"has staged content different from both the file and
  the HEAD"* — `-f` with `--cached` still affects the index only.
- If `git rm --cached -- wiki/wiki/.gitignore` fails with *"pathspec did not match"*, the stray was never
  committed: delete it from disk instead with `rm -f wiki/wiki/.gitignore` (and `rmdir wiki/wiki`).

Verify the index now holds **only** the intended change:

```bash
git diff --cached --name-status
```

Expected: nothing but `D  wiki/.codx/…` lines and `D  wiki/wiki/.gitignore`.

### Step 3 — commit (no `git add -A`, no force, no rewrite)

```bash
git commit -m "chore(wiki): stop tracking wiki/.codx control plane; drop stray wiki/wiki/.gitignore"
git rev-parse --short HEAD
```

`rev-parse` here is the **commit SHA** the card asks for.

### Step 4 — post-flight (quote all outputs)

```bash
git status --porcelain wiki/.codx          # MUST be empty
git ls-files wiki/.codx | wc -l            # MUST be 0
git ls-files wiki/wiki                     # MUST be empty
git check-ignore -v wiki/.codx             # MUST exit 0, wiki/.gitignore:24:.codx/
git status --porcelain | wc -l             # the repo-wide "after" number for 4966baf0
```

Then restore anything parked in Step 1:

```bash
git add -- docs/.vitepress/config.mts      # only if it was parked
```

### Step 5 — gate evidence (card `4966baf0` prerequisite)

```bash
# before the surgery  (Step 0 state) and after it (Step 4 state)
security_review_changes(scope="staged")    # via the CISO tool, not the shell
```

Acceptance is **0 findings inside `.codx/`** after the commit. Quote the raw counts, both of them.

---

## 3. Rollback

The commit is local (`repository_overview.ahead` was `1` before the surgery). No force-push is needed:

```bash
git reset --mixed HEAD^     # re-tracks the files in the index; working tree and history untouched
```

---

## 4. Acceptance mapping

| Acceptance (card `b370c370`) | Proof |
|---|---|
| `git status --porcelain wiki/.codx` empty | Step 4, first command |
| `security_review_changes(scope="staged")` → 0 findings in `.codx/` | Step 5, quoted before/after |
| `wiki/wiki/` no longer exists | Step 4, `git ls-files wiki/wiki` empty **and** `test ! -e wiki/wiki` |
| Comment on `4966baf0` with before/after | Step 0 / Step 4 `git status --porcelain \| wc -l` |

## 5. Hard NOs

- **No** `git add -A` (it would absorb `docs.json`, `llms.txt`, `.vitepress/config.json` churn owned by nobody).
- **No** force-push, **no** history rewrite, **no** `git rm` without `--cached` on `wiki/.codx`
  (without `--cached` the control plane is erased from disk and the running project loses its tasks).
- **No** touching `.codx/tasks/**` at the repository **root** — that set is tracked by design
  (`api/codx/junior/runtime.py:108-130`, `DATA_PLANE_ENTRIES` omits `tasks`).
