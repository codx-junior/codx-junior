# QA — a change is done when a gate says it is

This folder is the quality control plane of codx-junior. It exists because a
green checkmark is easy to fake and expensive to trust: a test that was never
run, a diff that touched no test, a coverage number nobody measured, a failure
that "went away".

The rule that comes out of it:

> A change counts as verified only when a **deterministic** check produced the
> verdict. Everything else is an opinion, and an opinion is not a delivery.

The design mirrors the CISO subsystem on purpose (`codx/junior/security/ciso/`):
a small model layer, a policy with a fixed ladder, deterministic checks and one
gate per process. The CISO decides whether something may cross a **trust
boundary**; QA decides whether it may cross the **definition of done**.

## The ladder

```
ALLOW → WARN → REQUIRE_APPROVAL → BLOCK
```

`policy.evaluate(domain, findings, policy)` maps the highest severity of the
findings onto that ladder, exactly like the CISO policy does. A caller reads
`verdict.action` and reacts; it never re-implements a threshold.

| Severity | Typical finding | Default decision |
| --- | --- | --- |
| `info` | note attached to a clean run | `allow` |
| `low` | `change.no-tests-changed`, `coverage.unavailable` | `warn` |
| `medium` | `change.missing-tests`, `coverage.below-threshold`, `flaky.detected` | `require_approval` |
| `high` | `tests.no-suite`, `tests.no-runner`, `tests.timeout` | `block` |
| `critical` | `tests.failed` (when `block_on_failures`) | `block` |

## Gates and call sites

| # | Process | Call site | Gate | Status |
|---|---|---|---|---|
| 1 | QA Lead validates a change | tool `qa_review_changes` → `guard_change` | mapping + tests + coverage | **wired** |
| 2 | QA Lead runs a suite | tool `qa_run_tests` → `guard_tests` | suite execution | **wired** |
| 3 | Failure does not reproduce | tool `qa_flaky_check` → `guard_flaky` | repeated runs | **wired** |
| 4 | Coverage check | tool `qa_coverage` | report vs threshold | **wired** |
| 5 | `codx-junior` before `orchestrator(finish)` | profile instruction | `qa_review_changes` | **wired (prompt)** |
| 6 | PR merge (`close_pr`) | `engine/git_engine.py` | `guard_change` | pending |
| 7 | Task moved to `done` | `task_manager.py` | `guard_change` | pending |
| 8 | Delivery report / `orchestrator(finish)` | `tools/orchestration_tools.py` | `guard_change` | pending |
| 9 | Autonomy autopilot step | `autonomy/worker.py` | `guard_change` | pending |

Items 1–5 are what the QA profiles use today. Items 6–9 are the automatic hooks
and they all call the **same** `guard_change`: wiring one of them is a two line
change, not a second implementation.

## How a change is verified

1. `changed_files()` — `git diff --name-only` (+ untracked) of the scope.
2. `map_tests()` — every changed source file is matched to the test files whose
   name mentions its stem; the rest is reported as `change.missing-tests`.
3. `run_suite()` — the **declared** argv of the suite (detected or from
   `qa_policy.suites`), executed on the mapped tests first.
4. `read_coverage()` — the report the project already generates
   (`coverage.xml`, `coverage.json`, istanbul `coverage-summary.json`).
5. `evaluate()` — findings → ladder → `QaVerdict`, recorded in
   `.codx/harness/audit.jsonl` as `qa_verdict`.

## Safety rules of the runner

* **No shell.** `subprocess.run(argv, shell=False)`; a suite is a list.
* **Declared commands only.** The argv comes from discovery or from the project
  policy, never from free text.
* **`extra_args` is validated** character by character: `;`, `|`, `&`, `$(`, backticks,
  redirections and newlines are refused with `tests.unsafe-args`.
* **CISO first.** The composed command line passes
  `security.ciso.gate.guard_command` before execution: the QA subsystem inherits
  the project's security policy instead of duplicating it.
* **Bounded.** Timeout (`max_test_seconds`, hard ceiling 3600s) and output
  truncated to 8 KB.
* **Gates never raise.** A failing check becomes a `warn` (or a `block` when
  `fail_closed`).

## Policy

Stored in the project settings under `qa_policy`:

```json
{
  "enabled": true,
  "min_coverage": 70,
  "block_above": "high",
  "approval_above": "medium",
  "require_tests_for_changes": true,
  "block_on_failures": true,
  "block_missing_tests": false,
  "fail_closed": false,
  "max_test_seconds": 900,
  "flaky_repeats": 3,
  "ignore_paths": ["*.md", "api/docs/**"],
  "suites": {"e2e": {"command": ["npm", "run", "test:e2e"], "cwd": "client"}}
}
```

Inspect or change it with the `qa_policy` tool. `CODX_JUNIOR_QA_DISABLED=1` is the
emergency stop.

## Profiles

| Profile | Owns |
|---|---|
| `qa` | the gate: plan, review, verdict, follow-up. No write tools: independence is the feature |
| `qa_automation` | writes and repairs the tests the gate needs (writes code, runs no shell) |
| `qa_explorer` | exploratory validation through the real browser, evidence + tasks |

`codx-junior` (the harness) invokes them: `qa` before finishing a run that
touched code, `qa_automation` for the missing tests, `qa_explorer` when the risk
is in a user flow.

## Verify

```bash
# no dependencies, runs on a bare interpreter
python3 api/tests/quality/qa_selfcheck.py

# runtime wiring: tools registered, profile allowlists valid
PYTHONPATH=<repo>/api <repo>/api/.venv/bin/python api/tests/quality/qa_wiring_check.py

# unit tests
PYTHONPATH=<repo>/api <repo>/api/.venv/bin/python -m pytest -q api/tests/quality/test_qa.py
```

## Rules to keep

1. A verdict is quoted in the report **with the command that produced it**.
2. A failing test is never deleted, skipped or `xfail`ed to pass a gate.
3. Missing tests are a finding, not a pass — and the fix lands in the same change.
4. The runner never gets a shell, and the policy is the only place a command is
   declared.
5. The same `guard_change` serves the manual gate and every automatic hook: one
   implementation, one behaviour.
