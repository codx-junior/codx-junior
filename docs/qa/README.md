# QA — docs site integrity

Four behavioural test groups over the VitePress tree, run with `vitest`. They assert on the
**real** `.vitepress/config.mts` and on the markdown files on disk — never on a copy of the
sidebar, so they cannot drift from what the site actually builds.

## The command

```bash
npm install && npm test
```

`package-lock.json` is committed — `git status` reports it tracked and unmodified — but it predates
the `vitest` devDependency: it contains no `vitest` entry at all
(`grep -c vitest package-lock.json` → `0`) and `node_modules/` has no `vitest` either, so `npm ci`
stops with
`EUSAGE` (`Missing: vitest@2.1.9 from lock file`) until a plain `npm install` reconciles the two.
That run rewrites the lockfile; once it is committed, the strict form works from a clean
checkout:

```bash
npm ci && npm test
```

Regenerating the lockfile needs npm plus network access, and no agent profile can do it: the
runtime has **no `npm` binary at all** — `qa_run_tests(suite="npm")` returns
`npm: npm not found in PATH` with `[high] tests.no-runner`, and `qa_plan()` lists the declared
`npm` suite as `available: false` for the same reason. That one step is a host/CI action (IT team
card `537e8ee7`, split out of `46c91d6b`).

`npm test` is `vitest run`: non-interactive, exits non-zero on any failed assertion, and
`passWithNoTests: false` so a run that discovers no test fails instead of reporting success.

## What each test protects

| Test | Protects | Fails when | Card |
| --- | --- | --- | --- |
| **T1** | `srcExclude` keeps the two `features/workspaces-*-review.md` engineering reviews out of the published build | the exclusion is deleted or edited so a review page becomes a live, indexable URL | `07006e18` |
| **T2** | every `link` in `nav` and in every `sidebar` block resolves to a real `.md` file | a sidebar/nav line points at a page that does not exist | `54658755` |
| **T3** | every publishable `features/*.md` is reachable from the `/features/` sidebar or from `features/index.md` | a page is published but linked from nowhere | `07ba6b72` |
| **T4** | every `METHOD /api/...` claim written in `features/*.md` exists in a REST table published by those pages | a page claims an endpoint no route table declares (or a claim scan/table parse breaks silently — both have canaries) | `49007737` |

T2 exists because `ignoreDeadLinks: true` (`.vitepress/config.mts:15`) was set deliberately so
cross-links inside page bodies do not fail the build. That also means `npm run docs:build` can
never fail on a broken nav entry — this test is the only automated check for it.

T4 exists because `97583327` shipped a route claim (`/api/computers`) that the published route
table does not contain, and both the build and T1–T3 stayed green. Its oracle is the docs' own
REST tables: a row whose first cell names an HTTP method and whose route cell holds `/api/...`,
or a path relative to the `... are under `/api/x`` sentence that introduces the table. A claim
outside a table must match a published route segment for segment, or be exactly one segment
deeper than one (a concrete action of a published resource) — `/api/computers` being published
does not cover `/api/computers/{id}/recording/start`. A `{}` written in a *claim* must also be
matched by a `{}` in the table, so `/api/computers/{id}/recording` is never absorbed by
`/api/computers/library/{item}`. Routes that live outside the docs' tables
(auth, settings, the wiki/theme endpoints, Traefik…) are waived in
`UNPUBLISHED_ROUTE_WAIVERS`, each with a reason; the last case runs the pre-fix sentence from
`97583327` through the same extractor and asserts it is reported, so the guard cannot rot into
a no-op.

## Current state — expected green: verified statically, not yet by a run

The suite declares twelve `it()` cases — the four groups below plus the canary assertions that
fail when an exclusion, a directory scan or a route-claim scan becomes empty. Each expectation was
re-checked against the config and the files it points at, so the expected output is `12 passed`. The last recorded run
(2026-11-19) reported `1 failed | 6 passed`: that was before `features/channel-creator.md` was
linked and before the canary assertions were added. Until the command prints the current code's
output, this section states what the tree implies — not what a terminal showed.

- **T1** — `srcExclude` is `['features/workspaces-*-review.md', 'transformation/**']`
  (`.vitepress/config.mts:13`) and the glob matches both review pages.
- **T2** — every declared link resolves: `guide/introduction`, `guide/architecture`,
  `getting-started`, `initial-setup`, `configuration`, `managed`, `demo`, `team` and every
  `/features/*` sidebar entry exist on disk. That includes the two links card `54658755` reported as
  dead — `features/suggested-replies.md` and `features/voice-conversation.md` are in the tree now,
  so the nav no longer points at pages that do not exist.
- **T4** — 56 route claims across 16 pages, all either declared by a table or waived; the two
  canaries require more than 30 claims and more than 80 parsed table routes. Verified by hand
  against the tables (waivers: auth, `/api/settings`, `/api/global`, `/api/profiles`, the four
  wiki/theme endpoints, `/api/traefik`, `/api/kanban`, `/api/chats/message`, `/api/chats/tasks`,
  `/api/tasks`, `/api/teams`, `/api/companies`, `/api/external/settings`).
- **T3** — the orphan set is empty. `features/channel-creator.md` was the last unreachable page
  and card `07ba6b72` linked it (`/features/` sidebar, `.vitepress/config.mts:60`; index row,
  `features/index.md:38`). `features/autonomous-agents.md` is reachable from
  `features/index.md:16`. The only unlinked pages left are the two `workspaces-*-review.md`
  reviews, which `srcExclude` keeps out of the build on purpose.

Nothing in this tree can execute the suite yet: `node_modules/` contains no `vitest`, and the
runtime has no `npm` binary (`npm not found in PATH`, measured 2026-11-23), so `npm install`
cannot be run here (card `537e8ee7`). The suite command itself is already declared correctly, so
**no policy or config change is outstanding**: `qa_plan()` reports suite `npm`,
`command: ["npm", "test", "--silent"]`, `cwd: "."`, `available: false`,
`reason: "npm not found in PATH"`. Installing npm on the host is the only remaining step. The
first verbatim output — the value this file must then match line for line — is a host/CI step.

## Scope — deliberately not here

- `transformation/` is excluded by `srcExclude` and not scanned by T3.
- `delivery-contract.md` sits at the root and is intentionally not in the nav; T3 only inspects
  `features/`.
- No coverage threshold: a coverage percentage over static markdown measures nothing.
- `docs:build` / `build:css` are **not** the QA suite. A successful build verifies compilation,
  not behaviour, and declaring it would manufacture an empty green (card `6c697929`).
