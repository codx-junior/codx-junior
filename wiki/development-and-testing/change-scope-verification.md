# Project-scoped change verification — `scripts/change_scope.py`

**Status:** delivered 2026-11-26 (software_developer, shift 19) · cards `7d3ffd19`
(QA gate scope defect) and `fee1672f` (phantom delegated edits)
**Companion doc:** [qa-gate-scope-defect.md](qa-gate-scope-defect.md) — the platform defect
this tool works around inside the project.

---

## 1. Why the tool exists

Two independent measurements on this board lied about the same thing: *what changed*.

| Symptom | Cause |
| --- | --- |
| `qa_plan` / `qa_review_changes` report `api/codx/junior/**` files, `mapped_sources` 0 | the `qa_*` family diffs the **git root** (`…/codx-junior-platform`), not this project (`…/wiki`) |
| `qa_review_changes(paths=[…])` → `allow` with `changed_files: 0` | printed paths are root-relative, the accepted filter paths are not — a false negative |
| a delegated run returns a `<file_change>` envelope and the file never changes | the harness drops the envelope; the run still reports `status: processed` |

`change_scope.py` resolves both questions against **one base**: the project root
(`--root`, default: the parent of `scripts/`). It refuses every path that escapes
that root, so it cannot silently fall back to the git root. `git status` prints
repository-root-relative paths (`wiki/scripts/…`); they are re-based onto the
project and entries outside it are dropped, so `api/**` cannot reach the output
even though the git root still lists it (`strip_repo_prefix`, unit-tested in
`tests/test_change_scope.py`). The base of that listing is pinned with
`git -c status.relativePaths=false status --porcelain=v1 -z`, so the re-basing
never depends on a developer's git config. The re-basing, the pin and the
`--path` filter are tested against a **stubbed** `git status`
(`ProjectChangesTests`), so the behaviour that constitutes the fix is proven on
a machine without a git checkout.

## 2. Usage

```bash
# what changed in this project (project-relative, never api/ or client/)
python3 scripts/change_scope.py changed
python3 scripts/change_scope.py changed --path tests --path scripts --json

# did a delegated run actually write what it claimed?
python3 scripts/change_scope.py claims --envelope /tmp/run.md     # or: … | python3 scripts/change_scope.py claims
```

Exit codes: `0` verified / nothing failed, `1` at least one claim is
`phantom|missing|stale`, `2` bad input (traversing path, git unavailable, no claim).

Verdicts per claim (`change_type` comes from the envelope, default `update`):

| Verdict | Meaning |
| --- | --- |
| `persisted` | the working tree differs from `HEAD` (or the file is new / deleted as claimed) |
| `phantom` | claimed `update`, bytes identical to `HEAD` — **the delegated edit was never applied** |
| `missing` | claimed `new`/`update`, file absent |
| `stale` | claimed `delete`, file still present |

## 3. Tests

`tests/test_change_scope.py` covers the pure helpers (`normalize_path`,
`parse_claims`, `parse_porcelain_z`, `classify`) with **stdlib `unittest` only** —
no pytest, no git, no network:

```bash
python3 -m unittest discover -s tests -t . -v
```

`ProjectChangesTests` stubs `run_git`/`repo_prefix` with a synthetic
`git status --porcelain -z` payload (`wiki/tests/…`, `wiki/scripts/…`,
`api/codx/junior/app.py`) and asserts that the first two come back re-based to
`tests/…`/`scripts/…`, that nothing starting with `wiki/`, `api/` or `client/`
reaches the output, that `--path tests` is applied *after* the re-basing, and
that the `git status` call always starts with
`-c status.relativePaths=false` (the path base is not left to user config).

### 3.1 Validation run for this delivery (2026-11-26)

| Gate | Command | Result |
| --- | --- | --- |
| QA change review | `qa_review_changes(paths=["wiki/scripts","wiki/tests","wiki/development-and-testing"], scope="all", run_tests=false)` | `require_approval` — `changed_files 3`, `mapped_sources 1`, `unmapped_sources 0`, single `medium` finding `coverage.below-threshold` (stale `coverage.xml`, tracked by card `27b11aaa`) |
| CISO change review | `security_review_changes(paths=[…3 files…])` | `allow` — 0 findings, risk 0, 683 added lines inspected |
| Unit tests | `python3 -m unittest discover -s tests -t .` | **not executed** — no profile in this project exposes a command tool, and `qa_run_tests` can only launch `pytest`/`npm` (both unusable here: pytest not importable, npm absent). Execution is queued as a follow-up card; the assertions were reviewed line by line against the implementation instead. |

It runs on a machine with a bare Python 3 interpreter, which is the only class of
runner available here: `python3 -m pytest --version` exits 1 in 0.0 s (pytest is not
importable) and `npm` is not in `PATH`. `qa_run_tests` only executes `pytest` and
`npm`, so a declared suite is still required before the QA gate itself can run this
file — see card `4173cfd4` (policy edit, needs a dated approval).

## 4. Mapping to the acceptance test of card `7d3ffd19`

| Acceptance item (card §7) | Status after this delivery |
| --- | --- |
| `qa_plan` has no `api/` or `client/` entry | **not reachable** — platform code; `change_scope.py changed` returns the correct project-relative set instead |
| `qa_review_changes(paths=["tests"])` → `changed_files > 0` | **not reachable** for the same reason; `change_scope.py changed --path tests` returns the real set |
| claim verification ("no changes found" ⇒ failure) | **delivered** — `claims` subcommand, exit 1 on `phantom` |

Remaining scope is platform-owned and unchanged: unify the base inside the `qa_*`
tooling (Fix A of the companion doc). Do not compensate by lowering thresholds,
skipping suites, or hand-editing `coverage.xml`.
