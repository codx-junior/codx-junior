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

**Good enough?** ok / next / what is wrong
```

### One line per section

* `## What happened` — the situation in plain language: the problem, what you found, what you decided and why. Facts and evidence (`path:line`, command output), not intentions.
* `## What I did` — the increment(s) delivered in this turn, one line each, with the files touched. If the increment is partial, say so: partial delivery is a valid outcome.
* `## Demo` — exactly ONE runnable thing the human can do to see the result (a command, a URL, a test, a card). One, not a list.
* `## Next tasks` — 2-3 candidate next steps, each tagged `quick` (an agile increment) or `max` (needs the `codx-junior-max` effort level).

### The checkpoint

The delivery ends with the line `**Good enough?** ok / next / what is wrong`:

```md
**Good enough?** ok / next / what is wrong
```

It is a real question, not decoration: `ok` accepts the increment, `next` takes the
first `Next tasks` entry, `what is wrong` opens a correction. Never remove it, never
answer it yourself.

## Validation tiers

Validation is **never optional**; only its *breadth* changes with the effort level.

| Tier | Applies to | What must be run before delivering |
| --- | --- | --- |
| `agile` | `codx-junior` (default) | Targeted tests for the touched files (or a manual reproduction when there are none) + `qa_review_changes`. A `warn` verdict is acceptable **only** if the gap is named in **Next tasks**. |
| `max` | `codx-junior-max` | Full test suite + coverage threshold + `security_review_changes` + browser explorer for UI changes. |

Both tiers close with the same four sections; a delivery without its tier's validation
is not a delivery.

### QA gate availability

In this repository the QA gate (`qa_plan` / `qa_run_tests`) currently **cannot run**:
the project is opened at `docs/`, its file tools are jailed there, while the QA tools
resolve the git root one level up — and no suite is declared for either root.

* `qa_plan()` → `suites: none`, `default_suite: none`, `mapped_tests: 0`, `unmapped_sources: 14`.
* `qa_run_tests(suite="pytest")` → `decision: block`, `[high] tests.no-suite`.
* A runnable command *does* exist for the docs site itself — `npm run docs:build`
  (VitePress, dead-link aware), see `package.json:11`.

Until the platform root is opened instead of `docs/`, or `docs/` is registered as its
own sub-project, deliveries on this project fall back to a **manual reproduction** for
validation, as allowed by the `agile` tier above.

**Default taken by the shift of 2026-11-17 — pending human confirmation (card
`b776e9a7`): keep `docs/` as the project root, do not open the platform root.**
Opening the platform root would hand the QA gate the whole monorepo — 50 changed
files under `api/`, `ui/` and the effort/atlas work — none of which this project
publishes, and none of which it can even read, because the file tools stay jailed at
`docs/`. The fix therefore belongs to *this* project: declare the only runnable suite
in the project config, so the gate executes it from the docs root.

```json
"qa_policy": {
  "suites": {
    "docs": { "command": ["npm", "run", "docs:build"] }
  }
}
```

**Unverified:** whether `.codx/project.json` accepts a `qa_policy` key. The service
that owns `qa_*` (`codx-api`, root `…/codx-junior-platform/api`) is indexed as a
related project, but its atlas times out (>120 s) on every search, so the config
schema was not read. Until the key is accepted, `qa_plan()` keeps returning
`suites: none` and deliveries keep using the manual reproduction described above.

## Effort levels

* `codx-junior` — **agile**. Small BASE budget and an ADVISORY clock: reaching
  either grants a bounded course correction and marks the response, but it never
  cancels the task — only the user stops a task. One increment per turn, partial
  delivery legal. The policy is resolved at runtime from the profile name, not
  from prompt phrasing.
* `codx-junior-max` — **max**. The heavier tier: bigger budget, longer course
  corrections, full validation breadth, used for long or complex work and for
  orchestration runs. It does not copy the base profile; it links
  `profiles: ["codx-junior"]` and holds only its delta (budget, validation
  breadth, orchestration use).

Made with ❤️ by codx-junior
