# Security reviewer: attribution and root alignment (handoff spec)

> **Status: spec, not a fix.** The reviewer code (`security_review_changes`,
> `security_audit`) is **not reachable from this workspace**: the project is opened at
> `docs/`, the file tools are jailed there, and neither tool is exposed to this
> profile — `project_locate("security_review_changes")` and
> `project_locate("security_audit")` return **0 code matches** in this tree
> (measured 2026-11-18). Nothing below is presented as demonstrated output.
> This page is the deliverable of card **M5**: the three defects, the exact
> reproducers and the expected output shape, handed to the owner of the reviewer code.
>
> Internal engineering note. It is not part of the user guide and is deliberately
> not linked from the site navigation.

## 1. Why

`security_review_changes(scope="all")` currently answers **`block`** with
`highest_severity: critical`, and that verdict cannot be located: the report is cut at
about ten lines and no `paths=` filter reaches the offending file. A `block` without a
reachable `path:line` is an outage, not a decision — it stops every diff and gives the
author nothing to fix.

Two independent defects sit behind it, and they must not be merged:

| Defect | What changes | Owner card |
| --- | --- | --- |
| **A** — ~1000 `secret.high-entropy-token` findings come from `.codx/` runtime state (timestamps, task UUIDs) | *what is scanned* | `8909f356` (M6) |
| **B** — the remaining report is unreadable and unlocatable | *what is reported* | this card (M5) |

## 2. The three defects

### D1 — Findings are not aggregated and a `block` can carry no location

**Symptom.** One line per finding, repeated across files, so the page fills with
near-duplicates before the highest severity is visible. The `critical` that triggers
the block is never shown with a usable location.

**Reproducer (against the current tree).**

```
security_review_changes(scope="all")
```

**Current output (as reported on card `19883bdd`).** `decision: block`,
`highest_severity: critical`, `628 findings`, report truncated to ~10 lines, no
reachable `path:line` for the `critical`.

**Expected.** Findings grouped by `rule_id` + `path` with a `count`, ordered by
severity (descending), then count. Every finding carries a `path:line` that **resolves
on disk**; the reviewer asserts that before publishing, and if it cannot resolve one it
must not publish `block` on the strength of it.

### D2 — The head of the report is truncated, silently

**Symptom.** The list is cut without saying so. The reader cannot tell 10 findings from
628.

**Reproducer.** Same call as D1; compare `len(findings)` with the number of entries the
report prints.

**Expected.** The report always states `total` **and** `omitted` (`0` when nothing is
cut). Truncation, when it happens, always preserves the highest severity in the head.

### D3 — The `paths=` filter uses a different root than the scanner

**Symptom.** A relative filter is resolved against a different base than the scan, so
paths are rewritten (`docs/.codx/...`) or rejected.

**Reproducers (both, same tree, same run).**

```
security_review_changes(scope="all", paths=["docs"])
security_audit(path=".codx/tasks/codx-junior maintenance/Backlog")
security_audit(path=".codx/tasks")
```

**Current output (as reported on card `19883bdd`).** The first rewrites results to
`docs/.codx/...`; the second returns **path not found**; the third resolves — for a
directory that exists in both cases.

**Expected.** One root, one answer: a relative `path`/`paths` entry is resolved against
a single declared base, echoed in the report, and a subdirectory of an existing tree is
never "not found" while its parent resolves.

## 3. Expected output shape

```json
{
  "decision": "block",
  "highest_severity": "critical",
  "root": "<absolute base the paths were resolved against>",
  "summary": { "findings": 628, "shown": 20, "omitted": 608 },
  "highest": {
    "rule_id": "secret.high-entropy-token",
    "path": "api/codx/junior/…:42",
    "severity": "critical",
    "resolves": true
  },
  "groups": [
    { "rule_id": "…", "path": "…", "severity": "high", "count": 12, "first": "…:7" }
  ]
}
```

Invariants: `summary.omitted = findings - shown`; `highest.resolves` is `true` for every
published `block`; `groups` is sorted by severity then `count`; `root` is present
whenever a `paths` filter was used.

## 4. Acceptance (from the card)

- [ ] The highest severity names a `path:line` that resolves on disk — quoted.
- [ ] The same finding is reachable through a `paths=[...]` filter containing that file — both outputs quoted against the same tree.
- [ ] The report states the total count **and** the number omitted; no silent truncation.
- [ ] No regression on the clean surface: `security_review_changes(scope="all", paths=["delivery-contract.md","docs"])` still returns `allow` / `0 findings`.
- [ ] Before/after output quoted on the card before it moves to Done.

## 5. Out of scope

Detection rules, thresholds, any ignore list (that is M6, `8909f356`, plus the open
policy question), viewer UI, dashboards, new rules.

## 6. Handoff

Owner of the reviewer code: the platform/core team (`api/`), not this `docs/`
workspace. The demo card `f6d1855e` must present this as
**not demonstrated — blocked on repo access**, and name that owner. Once the platform
root is opened as the project, D1–D3 are implementable with the reproducers above and
no further discovery.
