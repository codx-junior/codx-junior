# Delivery contract

Every delivery in codx-junior is closed with the **same four sections**. The shape is
not a style preference: it is the contract between the assistant and the human, and it
is written **once** here. It applies to:

* the `codx-junior` harness answer (the chat bubble),
* the orchestration supervisor's `finish` summary (the run delivery report),
* every worker step result (each isolated step closes with the same headings).

## The shape

```md
## What happened
## What I did
## Demo
## Next tasks

**Delivered.** Run the Demo above. If something fails, reply with the failing step.
```

### One line per section

* `## What happened` — the situation in plain language: the problem, what you found, what you decided and why. Facts and evidence (`path:line`, command output), not intentions.
* `## What I did` — the complete behaviour delivered in this turn, one line per file/area. If the request was too large, say which **complete vertical slice** landed and which requested scope is still missing: a partial layer is never a delivery.
* `## Demo` — exactly ONE runnable thing the human can do to see the result (a command, a URL, a test, a card). One, not a list.
* `## Next tasks` — only genuinely out-of-scope follow-ups, each tagged `quick` or `max`. Never the remaining requested work; if the request is complete, write `None — request delivered`.

### The delivered line

The delivery ends with:

```md
**Delivered.** Run the Demo above. If something fails, reply with the failing step.
```

It is a statement, not a permission question. The turn **never** ends with
"do you want me to continue?", "should I proceed?" or a "good enough?" menu.
The user presses Stop if they want less, or replies with the failing step.

## Validation tiers

Validation is **never optional**; only its *breadth* changes with the effort level.

| Tier | Applies to | What must be run before delivering |
| --- | --- | --- |
| `agile` | `codx-junior` (default) | Targeted tests for the touched files (or a manual reproduction when there are none) + `qa_review_changes`. A `warn` verdict is acceptable **only** when the gap is genuinely outside the request; requested work is fixed now. |
| `max` | `codx-junior-max` | Full test suite + coverage threshold + `security_review_changes` + browser explorer for UI changes. |

Both tiers close with the same four sections; a delivery without its tier's validation
is not a delivery.

### QA gate availability

In this repository the QA gate (`qa_plan` / `qa_run_tests`) currently **cannot run**:
the project is opened at `docs/`, its file tools are jailed there, while the QA tools
resolve the git root one level up — and no suite is declared for either root.

* `qa_plan()` → `suites: none`, `default_suite: none`, `mapped_tests: 0`,
  `unmapped_sources: 14` (measured 2026-11-17) and `16` (measured 2026-11-18). The
  count tracks the parent repo's pending change: treat it as a moving value, not as a
  contract.
* `qa_run_tests(suite="pytest")` → `decision: block`, `[high] tests.no-suite`.
* A runnable command *does* exist for the docs site itself — `npm run docs:build`
  (VitePress, dead-link aware), see `package.json:11`.

Until the platform root is opened instead of `docs/`, or `docs/` is registered as its
own sub-project, deliveries on this project fall back to a **manual reproduction** for
validation, as allowed by the `agile` tier above.

**Decision taken on 2026-11-18 (card `b776e9a7`) — keep `docs/` as the project root,
do not open the platform root, and declare the docs build as this project's only
suite.** Opening the platform root would hand the QA gate the whole monorepo — ~50
changed files under `api/`, `client/` and the effort/atlas work — none of which this
project publishes, and none of which it can even read, because the file tools stay
jailed at `docs/`. The decision is recorded here; it is **not applied yet**, because
applying it needs project-root control or `qa_policy` write access, and this assistant
has neither. Whoever holds that access applies exactly this declaration to the project
config, so the gate executes it from the docs root:

```json
"qa_policy": {
  "suites": {
    "docs": { "command": ["npm", "run", "docs:build"] }
  }
}
```

**Unverified:** whether `.codx/project.json` accepts a `qa_policy` key. Re-confirmed on
2026-11-18 that the schema cannot be read from this project: `codx-api` (root
`…/codx-junior-platform/api`), the service that owns `qa_*`, is indexed as a related
project but its atlas times out (>120 s) on every search — including a single-file
scope — and direct reads of its files are refused by the `docs/` jail. Until the key is
accepted, `qa_plan()` keeps returning `suites: none` and deliveries keep using the
manual reproduction described above. This schema question is tracked as its own task and
does not block this contract.

### Posting a reviewer verdict

A card that sits in `Review` is gated with **two** calls, never one:
`project_manager(action="request_help", comment=…)` posts the verdict as a comment without
processing it (`processed: false`, no run, no column move), and
`project_manager(action="update_task", …)` then performs the move the reviewer decided.
`request_help` here is a *naming* problem only — it is the call that adds a comment without
triggering the engine, which is exactly what a verdict is. `request_changes` must **never** be the
verdict call on a card in `Review`: it queues an engine run that re-applies the column the card
was enqueued from, so the card bounces back into `Review` and the reviewer re-gates the work it
has just rejected. The exact sequence, the rule and the measured evidence (3/3 clean on
2026-11-25; both 2026-11-23 reproductions bounced) live in the docs QA runbook,
[`qa/README.md`](./qa/README.md), section *Posting a verdict from `Review`* — this contract links
to it so the two cannot drift. Documented under card `106eef6b`; the platform fix that makes the workaround
unnecessary is card `35a21c13`.

### PDF render availability

`markdown -> pdf` is **not available** in this environment, and not because of the
markdown being rendered. Every engine the converter can reach fails for a different,
environmental reason (measured 2026-11-17 and re-confirmed 2026-11-18):

| engine | result |
| --- | --- |
| `xelatex` (pandoc default) | not found on the host |
| `lualatex` / `pdflatex` (pandoc fallbacks) | abort on the `❤️` in the page footer |
| `weasyprint` / `typst` | not installed |
| Chromium (browser renderer) | binary present in `~/.cache/ms-playwright/`, but it cannot start: `libnspr4.so: cannot open shared object file` (exit code 127) |

So a PDF render is **not** a valid validation signal for a markdown change: it fails
identically on every change, and a red result says nothing about the change under
review. The accepted replacement is `markdown -> docx` via `doc_converter`, which
exercises the same markdown parse and yields a non-zero-byte file to check; for the docs
site itself the stronger check stays `npm run docs:build`.

PDF export returns as soon as the host provides one working engine: `weasyprint` or
`typst`, or the Chromium runtime library `libnspr4` — the browser binary itself is
already cached, it is only missing that shared object.

## Effort levels

* `codx-junior` — **agile**. Small BASE budget and an ADVISORY clock: reaching
  either grants a bounded course correction and marks the response, but it never
  cancels the task — only the user stops a task. The turn delivers the requested
  behaviour, or one **complete vertical slice** when the request is too large;
  a partial layer is not a delivery. The policy is resolved at runtime from the
  profile name, not from prompt phrasing.
* `codx-junior-max` — **max**. The heavier tier: bigger budget, longer course
  corrections, full validation breadth, used for long or complex work and for
  orchestration runs. It does not copy the base profile; it links
  `profiles: ["codx-junior"]` and holds only its delta (budget, validation
  breadth, orchestration use). It keeps executing batches until the objective
  is met or blocked; the first green phase is not a delivery.

Made with ❤️ by codx-junior
