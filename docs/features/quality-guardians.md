# Quality guardians

Quality guardians keep a project's changes aligned with **the best practices of
the file that changed** — a Vue component is reviewed with the project's Vue
rules, a migration with its data rules, a Dockerfile with its infrastructure
rules. They are not a second AI reviewer bolted onto the Git view: they are
file-scoped **profiles** that are created automatically, run on changes and can
be polished like any other teammate.

![The quality panel](/images/NOV2025/codx-junior-pull-request.png)

## Why guardians are profiles

codx-junior already associates profiles with files through `file_match`, and the
Validator already uses those associations. Making a guardian a profile means:

* one registry (`.codx/profiles`), one editor and one learning model;
* the same profile can be mentioned in a chat, used by the Validator, assigned
  to a guardian run and enriched with `profile_learning`;
* there is no separate prompt store to keep in sync;
* a project can adopt an existing profile (for example a `VueDeveloper` profile
  with `file_match: \.vue$`) as a guardian instead of inventing a new one.

The guardian-specific settings live in an optional `guardian` block on the
profile. Everything else is a normal profile: `name`, `description`, `category`,
`file_match`, `content` (the review rules), `tools` and `use_knowledge`.

```json
{
  "name": "guardian_python",
  "category": "guardian",
  "file_match": "\\.pyi?$",
  "content": "You are the Python guardian of this project...",
  "guardian": {
    "enabled": true,
    "kind": "code",
    "triggers": ["change", "staged", "manual"],
    "checks": [
      {"id": "syntax", "kind": "builtin", "builtin": "syntax", "severity": "high"},
      {"id": "conflict-markers", "kind": "builtin", "builtin": "conflict_markers", "severity": "critical"}
    ],
    "severity": "medium",
    "blocking_severity": "high",
    "auto_generated": true,
    "generator": "python",
    "polished": false
  }
}
```

## The two lanes

Running a model on every editor save would be noisy, slow and expensive, and it
would still miss syntax errors that a parser catches for free. A guardian has
two lanes:

| Lane | What it does | When it runs | Cost |
| --- | --- | --- | --- |
| **Fast** | Syntax (Python/JSON/YAML/TOML), merge-conflict markers, local docs links and the project's installed linters/formatters (ruff, eslint, prettier, shellcheck, hadolint, gofmt) | After a file-change burst, on staged changes and on demand | No tokens |
| **Deep** | An evidence-bound LLM review against the guardian's rules and learned project rules | Only when the policy/trigger allows it (staged and manual by default; file change is opt-in) | Bounded files/calls per run |

A run returns **structured findings**, never a free-form essay:

```
[severity] guardian rule @ path:line
title
evidence (quoted from the diff)
recommendation
```

The AI answer is parsed and validated: a finding must reference a path that was
actually reviewed, and its line is located from the quoted evidence. Prose is
never silently converted into findings.

## Automatic creation

Guardians are created **lazily**, the first time a human (or an explicit
manual/staged/commit review) asks for quality: opening the Quality panel, using
`quality_guardians(action="bootstrap")`, or running a review. The catalog scans
the project and creates the guardians the detected stack needs:

| Guardian | Created when | Deterministic checks |
| --- | --- | --- |
| `guardian_python` | `.py` files exist | syntax, conflict markers, ruff errors when available |
| `guardian_web` | JS/TS/Vue/CSS files exist | conflict markers, eslint/prettier when configured and installed |
| `guardian_tests` | test files or a test runner exist | syntax, conflict markers |
| `guardian_docs` | Markdown/reST docs exist | conflict markers, broken local links |
| `guardian_infra` | Docker/CI/shell/Terraform/Kubernetes files exist | conflict markers, shellcheck, hadolint when available |
| `guardian_data` | SQL/GraphQL/proto or migrations exist | conflict markers |
| `guardian_dependencies` | dependency manifests exist | manifest syntax, conflict markers |
| `guardian_config` | JSON/YAML/TOML config exists | syntax, conflict markers (skips manifests owned by the dependency guardian) |
| `guardian_code` | another code language exists | conflict markers; gofmt for Go when available |

The scan is deterministic and bounded: it reads manifests, counts extensions and
looks for the tools that are actually installed. A guardian never declares a
command that this environment cannot run; a missing linter is reported as a
skipped check, not as a failure.

The **file-change lane never writes profiles**. It only runs guardians that
already exist; a project that never asked for quality is skipped. This keeps an
unattended save from modifying `.codx/profiles` and avoids creating guardian
sets in every nested project of a monorepo.

Creation is idempotent:

* missing guardians are created from the catalog;
* an auto-generated guardian whose detected signature changed is refreshed **only
  if the user never edited it** (`generated_profile_hash` detects the edit);
* a polished guardian is never overwritten;
* user-created profiles are never touched;
* `bootstrap(adopt=true)` (or `quality_guardians(action="bootstrap", adopt=true)`)
  converts existing file-matching profiles into guardians without changing their
  category or content.

## Running on file changes

The background service watches the project through the existing change pipeline.
Changes are coalesced per project and run after the project has been quiet for
`debounce_seconds` (default 12 s), capped by `max_delay_seconds` (default 5 min).
A per-file cooldown (default 5 min) stops the same file from being reviewed
repeatedly while it is being edited.

The default policy is deliberately cheap:

* deterministic checks run on file changes;
* the AI lane runs on **staged** changes and on **manual** reviews;
* `ai_on_change` is opt-in, because it spends tokens on every change burst.

The trigger does not block knowledge indexing, mention checks, wiki updates or
the chat. It is a best-effort side channel: a broken guardian cannot stop the
project pipeline.

## Reviewing globally or by file type

The `quality_review` tool (and `POST /api/quality/review`) supports four scopes:

| Scope | Use it for |
| --- | --- |
| `changed` | The working tree (default). Uses git diff and includes untracked files. |
| `staged` | The next commit (`trigger="staged"` or `change_scope="staged"`). |
| `file_type` | One language/domain: `python`, `vue`, `docs`, `tests`, `infra`, `.ts`... |
| `files` | An explicit path set. |
| `project` | A bounded global audit. It reads a policy-bounded number of files; the report says how many were reviewed. |

`mode` selects the lane: `checks` (deterministic, free), `review` (AI only) or
`auto` (both when the policy allows). A manual review runs every selected
guardian; `change`/`staged`/`commit` runs respect the guardian's `triggers`.

## Findings, verdicts and fixing

A run produces a deterministic verdict:

```
ALLOW → WARN → REQUIRE_APPROVAL → BLOCK
```

The thresholds are policy fields (`warn_above`, `approval_above`,
`block_above`), defaulting to low/medium/high. Every finding has a stable
fingerprint (guardian + rule + path + normalized title), so it survives line
movement and can be dismissed or suppressed once.

Fixing is a separate, explicit action:

* `quality_fix` defaults to **proposal only** (`apply=false`);
* deterministic `fix_command`s run first (ruff --fix, prettier --write, gofmt -w)
  when the guardian declares `auto_fix` (or the project policy enables it), and
  still pass through the CISO command gate; without the flag they are shown as a
  plan and the explicit `quality_fix(apply=true)` does not execute them;
* for the remaining findings the model returns exact `search`/`replace` pairs,
  never a free-form patch; each search must match exactly once or the whole file
  is left untouched;
* applying is atomic per file;
* after applying, the affected guardians run again in `checks` mode and the
  verification is returned. "Fixed" is evidence, not a claim.

## Polishing a guardian

A guardian is noise until it encodes the project's real conventions. The loop is:

1. **Learn** a rule: `quality_feedback(action="learn")` (or the Quality panel's
   *Learn* button) writes a durable rule to the guardian's `enrich.md`, which is
   composed into every future review.
2. **Dismiss** a one-off finding with a reason. It is closed in the run history.
3. **Suppress** a repeated false positive. A suppression can target one finding,
   one rule on one path, or a rule across the project; it always has a reason and
   can expire.
4. **Edit** the guardian from the team view: persona (the review rules),
   `file_match`, kind, triggers, severities, deterministic checks and auto-fix.
5. **Adopt** an existing file-matching profile instead of duplicating it.

## Tools

| Tool | What it does |
| --- | --- |
| `quality_guardians` | `list`, `bootstrap`, `adopt`, `enable`/`disable`, `update`, `learn` |
| `quality_review` | Run a review: changed, project, file type or explicit files; checks/AI/auto |
| `quality_fix` | Propose or apply fixes for the open findings of a run, then verify |
| `quality_feedback` | Dismiss/suppress/learn/reopen a finding |
| `quality_policy` | Read/update the triggers, cost limits, ladder and ignores |

The same actions are available through `/api/quality/*` and the **Quality** tab
of the Git view. The **Team** view exposes the guardian role and a Quality tab
for editing; the `quality_guardian` built-in profile is the teammate you ask to
analyze, fix or polish the project.

## Storage and security

Generated data lives in `.codx/quality`:

```
.codx/quality/
  policy.json          # project policy overrides
  runs/index.json      # newest-first run summaries (bounded)
  runs/<run-id>.json   # full run: files, checks, findings, verdict
  suppressions.json    # dismissed findings that must not come back
  cache.json           # short-lived AI review cache
  trigger.json         # debounce/cooldown status of the file-change lane
```

The policy is **not** stored in `project.json` (which also holds credentials).
Runs are generated data and are never committed by the guardian subsystem.

Safety boundaries:

* the diff and file contents are untrusted data; they are never followed as
  instructions;
* a finding must reference a reviewed path; the parser drops anything else;
* commands run as a fixed argv with no shell, inside the project, with a timeout
  and through the CISO command gate;
* the guardian bootstrap never overwrites a profile the user edited;
* editing a guardian profile, adopting one or changing its policy requires the
  same permissions as profile learning (project admin, knowledge admin or
  codx-junior admin); the profile API enforces the same rule, so the team view
  is not a permission bypass;
* dismissing or suppressing a finding is a review decision available to project
  members; learning a durable rule requires the admin role;
* the file-change lane never blocks the rest of the background pipeline.

## Example

```
You: Review my change with the quality guardians and fix what is safe.

Agent:
1. quality_guardians(action="list")        → 3 guardians, 0 open findings
2. quality_review(scope="changed")          → block: 1 syntax error, 1 conflict marker
3. quality_fix(run_id=..., apply=false)     → proposal for app.py, no write
4. quality_fix(run_id=..., apply=true)      → 1 file fixed, verification allow
5. quality_feedback(... action="learn")     → guardian learned the project convention
```

The delivery report says what actually ran, what was skipped and what the
verification found — never just "quality improved".
