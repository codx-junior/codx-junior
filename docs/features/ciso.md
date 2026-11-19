# CISO

The CISO view is the security control room of a project. It answers four
questions on one screen:

* **How exposed are we?** Posture score, severity mix and the trend of the last
  scans.
* **What exactly was detected?** Findings with rule, file, line, redacted
  evidence and remediation.
* **What is the platform doing about it?** The gate journal: every file write,
  commit, command, container action, knowledge ingestion and model output that
  passed through a CISO gate, with its decision.
* **What are the rules?** The full detector catalogue, the decision ladder and
  the scanner options, editable from the screen and applied by the very next
  gate.

The engine is deterministic: detectors use regular expressions, entropy and
structural heuristics. No security decision is delegated to an LLM.

## Overview

The overview is a single request (`GET /api/ciso/overview`) and refreshes while
a scan is running:

| Card | What it shows |
| --- | --- |
| Posture | Score 0–100, grade A–F, risk score, highest severity and the action the policy would take. |
| Latest scan | Scope, status, duration, files scanned and findings per severity. |
| Findings by severity | Bars per severity and a breakdown by category. |
| Gates | Decisions of the last 7 days (allowed, warned, redacted, approval, blocked), top triggered rules and the number of silenced rules. |
| Secret vault | Declared secret names, how many are ready/pending and their scopes. Values are never read or returned. |
| Repository hygiene | Whether the generated data plane is ignored by git and whether any of it is tracked. |

### Posture score

The score is deliberately simple and explainable:

```
risk_score = Σ(detector weights)      # low 1, medium 3, high 7, critical 25
score      = max(0, 100 - risk_score)
```

Grades: **A** ≥ 90, **B** ≥ 75, **C** ≥ 60, **D** ≥ 40, **F** below 40.
A single critical finding therefore costs 25 points; the ladder in the policy
decides whether that finding blocks an operation, not the score.

## Scans

A scan runs in the background and is persisted in the project data plane
(`.codx/ciso/scans`, relocatable with `CODX_JUNIOR_CODX_DATA_PATH`) so it
survives a restart without ever entering git.

| Scope | What is analysed |
| --- | --- |
| **Whole project** | Bounded walk of the tracked and untracked text files (vendor/build folders skipped). |
| **Working tree + untracked** | Added lines of `git diff HEAD` **plus** every untracked file. This is the scope that catches a file an agent created and forgot: a diff alone would not show it. |
| **Staged changes** | Added lines of `git diff --cached`. |
| **Selected paths** | Files and folders you name, relative to the project. |

Each scan records the policy snapshot that produced it, so a result remains
interpretable after the policy changes. Scans can be cancelled cooperatively
(between files) and deleted; retention keeps the newest 25 records (30 days).

The findings panel filters server-side by severity floor, category and free
text, and pages the result. Evidence is always already redacted by the backend:
a security report never reproduces the value it detected.

### From findings to a fix

Findings are selectable (individually or the whole page). **Create task** opens
a small dialog and creates a real project task through `POST /api/chats/tasks`:
name, board, column and priority, with the selected findings grouped by
severity, their redacted evidence, remediation and a done checklist in the task
description. The task also receives the affected file paths and the `ciso` tag,
so the person (or agent) fixing it starts with the evidence instead of a
screenshot. Nothing is auto-fixed and the selection is cleared after creation.

## Rules and policy

The **Rules & policy** tab is two halves of one source of truth:

* **Detector catalogue** — every rule the scanner actually runs (secrets,
  malicious code, prompt injection and configuration checks). Each rule has a
  severity, category and remediation, and can be silenced with one toggle. The
  catalogue is built from the detector tables, never hand-copied, so it cannot
  claim a rule the scanner no longer runs.
* **Decision ladder** — `warn_above`, `approval_above`, `block_above`, the
  categories and severities that always block, the strict domains (where a
  finding blocks instead of asking), ignored paths, entropy/length thresholds
  and the secrets resolver options.

Saving validates the payload and persists it in `.codx/project.json` under
`ciso_policy`. Invalid enum values or unknown fields are rejected with a clear
error instead of being silently dropped. Every gate reads the project policy
from that file (mtime-cached), so a change applies to the next gate across
sessions and workers — no restart.

The catalogue is deliberately **not** a custom-regex playground: the rules are
the deterministic detectors the scanner actually runs, and arbitrary patterns
would be both a ReDoS risk and a second source of truth. A project tunes the
engine by silencing a rule, narrowing `ignore_paths` or moving the thresholds;
a genuinely new detector belongs in the detector module with a test.

```json
{
  "ciso_policy": {
    "block_above": "high",
    "approval_above": "medium",
    "fail_closed": false,
    "ignore_rules": ["secret.high-entropy-token"],
    "ignore_paths": ["vendor/"]
  }
}
```

::: warning Rules are not a suggestion
Silencing a rule removes its findings from every gate (file write, commit,
command, container, model output). Prefer narrowing `ignore_paths` or lowering
the severity of a noisy rule over silencing a family that protects the
repository.
:::

## Activity

The **Activity** tab reads the `ciso_verdict` entries of the harness audit
journal (`.codx/harness/audit.jsonl`). Each entry carries the gate, the action,
the highest severity and the rule ids — never the matched value. It is
append-only and read-only from the UI.

## Ask the CISO

Every finding can become a conversation instead of an interpretation. Select one
or more findings and click **Ask CISO**, or use the **Ask CISO** button in the
CISO header for the current scan. The backend creates a real chat card with:

- `profiles: ["ciso"]` — the CISO profile and its security tools,
- a user message containing the scan summary and the selected findings with
  their **redacted** evidence and remediation,
- a clear question: real issue, rule defect/false positive, or uncertain; plus
  the expected output (verdict, confidence, evidence, recommended action),
- tags `ciso`, `security`, `review` on the `CISO` board.

The first CISO turn starts immediately and can use `security_scan_content`,
`security_check_command`, `project_read_file`, etc. to verify the rule against
the actual content. The conversation then continues like any other chat, so the
CISO can explain which rule or policy needs tuning and whether a code fix is
required. No code is modified by this flow.

## Company security

The company shell has a **Security** section (`GET /api/companies/{id}/security`)
that aggregates the same posture for every linked project: score/grade, critical
and high counters, latest scan, gate activity and a project CISO link. A company
admin or manager can start a scan per project from the same card
(`POST /api/companies/{id}/security/scan`).

## API

All endpoints are project-scoped (the usual `?codx_path=` / `x-codx-path`
convention) and mounted by the dynamic router loader:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/ciso/overview` | Posture, latest scan, trend, gates, secrets, repository hygiene. |
| `GET` | `/api/ciso/scans` | Scan history (newest first). |
| `POST` | `/api/ciso/scans` | Start a scan (`scope`, `paths`, `max_files`, `min_severity`). |
| `GET` | `/api/ciso/scans/{id}` | One scan record. |
| `POST` | `/api/ciso/scans/{id}/cancel` | Cooperative cancellation. |
| `DELETE` | `/api/ciso/scans/{id}` | Delete a record. |
| `GET` | `/api/ciso/scans/{id}/findings` | Filtered/paged findings. |
| `GET` | `/api/ciso/rules` | Detector catalogue with enabled/ignored state. |
| `PUT` | `/api/ciso/rules/{rule_id}` | Enable or silence one rule. |
| `GET` | `/api/ciso/policy` | Effective policy, stored overrides, defaults and options. |
| `PUT` | `/api/ciso/policy` | Merge, validate and persist a partial policy. |
| `POST` | `/api/ciso/policy/reset` | Drop the project overrides. |
| `GET` | `/api/ciso/activity` | Gate journal and aggregate statistics. |
| `POST` | `/api/ciso/chat` | Create a `ciso`-profile chat seeded with findings (optionally starts the assessment). |

## Design notes

* The view is a thin client: detection, policy evaluation and redaction stay in
  `codx/junior/security/ciso` and are shared with the gates.
* Scan records are generated data: they live in the data plane, are ignored by
  git and are never committed.
* The scanner reads at most `max_scan_bytes` per file and `max_files` per scan,
  so a monorepo cannot hang a request or a worker.
* A scan runs in a daemon thread with a cross-process `flock`: several API
  workers can poll and cancel it, but only one scan runs per project.
