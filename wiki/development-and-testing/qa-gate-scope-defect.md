# QA gate scope defect — the `qa_*` tools diff the git root, not this project

**Status:** open, blocked on platform ownership · card `7d3ffd19-0d36-451e-8b21-162f646579aa`
**Recorded:** 2026-11-19 (software_developer, shift 5)
**First found:** 2026-11-18 (qa, shift 4)

The QA gate measures a different directory tree than the one it is invoked on. Every
`qa_plan` / `qa_review_changes` verdict issued for this project so far is contaminated
by it, and `mapped_sources` can never rise above 0 no matter how many tests are written.

---

## 1. Symptom

`qa_plan` reports 82 unmapped changed sources, essentially all `api/codx/junior/**`.
Those paths do not exist in this project: the project root contains no `api/` and no
`client/` directory (see the listing in §3). `mapped_sources` is structurally 0.

Consequence: the 11 `change.missing-tests` findings produced so far are noise, and a
path-filtered review can return `decision: allow` with `changed_files: 0` — a
false-negative, not a pass.

---

## 2. Two bases exist, verified with non-QA tools (2026-11-19)

This part is reproducible today, without any `qa_*` tool:

| Tool | Base it reports | Verbatim evidence |
| --- | --- | --- |
| `read_folder(".")` | this project | `**Root**: /home/codx-junior-projects/codx-junior/codx-junior-platform/wiki` |
| `repository_overview` | the git root | `"repo_path": "/home/codx-junior-projects/codx-junior/codx-junior-platform"` |
| `project_changes` | this project | `coverage.xml` → `? untracked` |

Same artefact, two spellings, in the same instant:

- `repository_overview` lists it as `wiki/coverage.xml` (root-relative)
- `project_changes(paths=["coverage.xml"])` lists it as `coverage.xml` (project-relative)

`project_changes` is currently the only change tool that resolves against the project.
The `qa_*` family behaves like `repository_overview` (root-relative), which is the defect.

---

## 3. What the project root actually contains

`read_folder(".")` top level, abbreviated: `.vitepress/`, `domains/`, `tests/`, `public/`,
`development-and-testing/`, `deployment-and-operations/`, `project-overview/`,
`database-and-data-storage/`, `ai-and-knowledge-management/`, `integrations/`,
plus `server.py`, `conftest.py`, `pytest.ini`, `package.json`, `docs.json`.

No `api/`. No `client/`. Therefore no test written here can ever map an `api/**` source.

---

## 4. Recorded `qa_*` measurements (qa, shift 4 — 2026-11-18)

These were taken with the `qa_*` tool family, which is **not exposed to every profile**;
they are quoted from card `7d3ffd19` and were not re-derived on 2026-11-19.

| Command | Result |
| --- | --- |
| `qa_review_changes(scope="all", run_tests=false)` | `changed_files: 179`, `mapped_sources: 0`, `unmapped_sources: 82`, all `api/**`, findings 12 |
| `qa_review_changes(paths=["wiki"])` | `changed_files: 11`, `unmapped_sources: 1` (`wiki/conftest.py`), findings 2 |
| `qa_review_changes(paths=["tests","pytest.ini","conftest.py","requirements-dev.txt"])` | `decision: allow`, `changed_files: 0` ← vacuous pass |
| `project_changes(paths=["api/codx/junior/app.py"])` | "No changes found" — that path does not exist under `wiki/` |

The fourth line is the tell: a path that exists only at the git root is rejected by the
project-scoped tool, while `qa_review_changes(paths=["wiki"])` — a *root-relative*
filter — is the only form that returns real numbers.

---

## 5. Fix A (preferred) — one base for everything

In the platform QA tooling:

1. resolve the change set with `cwd` = the project path (`…/codx-junior-platform/wiki`),
   i.e. the same base `project_changes` uses;
2. emit **project-relative** paths in `qa_plan` and `qa_review_changes`;
3. make the `paths=[...]` filter accept the same base it prints (today the printed paths
   and the accepted filter paths disagree, which is what produces the vacuous allow).

## 6. Fix B (workaround, needs a dated approval)

Add `api/**`, `client/**`, `.codx/**`, `docs/**` to `qa_policy.ignore_paths`.
This hides the phantom paths but keeps the wrong base, so a path filter can still return
`changed_files: 0`. `qa_policy` edits are owned by card `4173cfd4-75a9-47da-8464-f7ce640ebb61`
and require the user's dated approval — do not apply it from an agent shift.

---

## 7. Acceptance test

```
qa_plan                                   # unmapped has no api/ or client/ entry; changed_files ≈ 4
qa_review_changes(paths=["tests"])        # changed_files > 0   (today: allow + changed_files: 0)
```

Do not compensate by lowering thresholds or skipping suites.

---

## 8. Self-check that needs no `qa_*` tools (run this first)

Two calls, ~5 seconds, reproduce the base mismatch on any shift:

```
project_changes(scope="all", paths=["coverage.xml"], include_patch=false)
#   -> ? untracked  coverage.xml                    (project-relative)

repository_overview()
#   -> untracked list contains  wiki/coverage.xml    (root-relative)
```

If those two spellings ever agree, the base has been unified — re-run §7 before closing
the card.
