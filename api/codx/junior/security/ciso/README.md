# CISO — secrets never reach a repo and never reach a model

This folder is the security control plane of codx-junior. It exists because of a
real incident: a fine-grained GitHub PAT ended up inside `.codx` task cards
(git-tracked), a remote push was rejected (`GH013`), and the remedy was a
history rewrite plus a mandatory rotation.

The rule that comes out of it:

> A credential must not survive **one write**. Everything that persists or sends
> content calls a gate from `gate.py`; redacting anywhere else is security debt.

## Why `.codx` is the dangerous part

`.codx/tasks/**` is **tracked by git on purpose** — boards and task cards are
shared project state. But a task card is also the most trusted sink in the
platform: every message, every streamed answer and every `tool_events[].output`
is written there verbatim. A tool that prints `.git/config`, an environment
dump, `docker inspect` or a container log turns conversation state into
permanent repository history. A task card is one local file away from being
`git history`, so the gate sits on the **write path**, not on the commit.

## Gates and call sites

| # | Process | Call site | Gate | Status |
|---|---|---|---|---|
| 1 | `.codx` task/chat persistence | `chat_manager.py` → `store_chat` | `redact_payload` | **wired** |
| 2 | Prompt sent to the model | `chat_prompt_builder.py` → `build_ai_prompt_messages` | `redact_output`/`redact_text` | **wired** |
| 3 | Agent file write | `tools/project_tools.py` → `project_write_file` | `guard_file_write` | **wired** |
| 4 | Tool output rendered to the user | `tools/repository_tools.py` → `repository_overview` | `guard_output` | **wired** |
| 5 | Commit / push | `.githooks/pre-commit` → `ciso.precommit` | `guard_diff` | **wired** |
| 6 | Batch edit | `tools/apply_file_changes.py` | `guard_file_write` | pending |
| 7 | Commit from the platform | `engine/git_engine.py` `create_pr_branch`, `tools/git_tools.py` | `guard_diff` | pending |
| 8 | Shell / container exec | `computer_action(run_command)`, `docker_exec`, console | `guard_command` | pending |
| 9 | Container build/run | `docker_run/build/compose` | `guard_container` | pending |
| 10 | Profile save | `profiles/profile_manager.py` → `save_profile` | `guard_profile` | pending |
| 11 | Logs and exports | `ai_logger`, `api_logs`, `chat_export` | `sanitize_for_log` | pending |
| 12 | Knowledge ingestion | `knowledge_loader`, `fetch_webpage`, wiki, task bodies | `guard_knowledge` | pending |

Items 1–5 are the ones the incident proves load-bearing. Items 6–12 are grouped
by class in the follow-up tickets of the incident report.

## Pre-commit gate

```bash
# once per clone
git config core.hooksPath .githooks

# it runs on every commit; to skip a single commit
git commit --no-verify

# fail instead of warn when the scanner cannot run
CISO_PRECOMMIT_STRICT=1 git commit ...
```

Exit codes of `python -m codx.junior.security.ciso.precommit`:
`0` clean · `1` a credential blocks the commit · `3` the scanner could not run.

The gate scans **added lines only** (`git diff --cached --unified=0`) and prints
a report whose evidence is already redacted — a security report must never
reproduce what it detected.

## Verify

```bash
PYTHONPATH=<repo>/api <repo>/api/.venv/bin/python \
    api/tests/security/ciso_wiring_check.py    # behaviour + wiring + real git repo
PYTHONPATH=<repo>/api <repo>/api/.venv/bin/python \
    api/tests/security/ciso_selfcheck.py       # detectors and policy ladder
```

## Detectors

| Module | Answers |
|---|---|
| `secrets.py` | known provider token shapes, credential assignments, URL credentials, entropy, placeholder filter |
| `malicious.py` | malicious / obfuscated / destructive / supply-chain / exfiltration code, shell commands |
| `injection.py` | stored prompt injection (override, coercion, hidden HTML/invisible chars) |
| `policy.py` | the deterministic ladder `ALLOW → WARN → REQUIRE_APPROVAL → REDACT → BLOCK` |
| `scanner.py` | `scan_content`, `scan_file`, `scan_diff`, `scan_project` (all size-bounded) |
| `payload.py` | recursion for structures: task cards and prompt messages |
| `gate.py` | the one call each process makes; audits to `.codx/harness/audit.jsonl` |

Gates never raise: a scanner failure allows the operation and logs a warning,
unless `policy.fail_closed` is set.

## Rules to keep

1. A credential is never written to a durable artifact: no `.codx` JSON, no log,
   no audit line, no commit, no chat message.
2. Redaction is centralised here. A local regex elsewhere is a future incident
   with a delay — `repository_overview` had one that knew a single token shape
   and covered a single field.
3. Runtime state is a **data plane**, not source code: it carries unbounded,
   model-influenced, untrusted content.
4. Scanning runs locally *before* the commit, not only remotely after the push.
5. Every tool that can print config, env, URLs, logs or file lists is reviewed
   against one question: *does its output pass a gate?*

Made with ❤️ by codx-junior
