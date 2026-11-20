# QA — docs site integrity

Three behavioural checks over the VitePress tree, run with `vitest`. They assert on the
**real** `.vitepress/config.mts` and on the markdown files on disk — never on a copy of the
sidebar, so they cannot drift from what the site actually builds.

## The command

```bash
npm install && npm test
```

`npm install` (not `npm ci`) for the **first** run: `vitest` was added to `devDependencies`
without a matching `package-lock.json` update, and `npm ci` refuses to run when the two are out
of sync (`EUSAGE`). After that run the lockfile is updated; from the next commit on, the strict
form is valid:

```bash
npm ci && npm test
```

`npm test` is `vitest run`: non-interactive, exits non-zero on any failed assertion, and
`passWithNoTests: false` so a run that discovers no test fails instead of reporting success.

## What each test protects

| Test | Protects | Fails when | Card |
| --- | --- | --- | --- |
| **T1** | `srcExclude` keeps the two `features/workspaces-*-review.md` engineering reviews out of the published build | the exclusion is deleted or edited so a review page becomes a live, indexable URL | `07006e18` |
| **T2** | every `link` in `nav` and in every `sidebar` block resolves to a real `.md` file | a sidebar/nav line points at a page that does not exist | `54658755` |
| **T3** | every publishable `features/*.md` is reachable from the `/features/` sidebar or from `features/index.md` | a page is published but linked from nowhere | `07ba6b72` |

T2 exists because `ignoreDeadLinks: true` (`.vitepress/config.mts:15`) was set deliberately so
cross-links inside page bodies do not fail the build. That also means `npm run docs:build` can
never fail on a broken nav entry — this test is the only automated check for it.

## Current state — the gate is NOT green

**T3 fails today on `features/channel-creator.md`**: the page is published, is in no sidebar and
is in no index. That is intended. A suite that cannot fail is not a suite, and this failing case
is the evidence that the assertions have teeth.

The fix belongs to card `07ba6b72` (link the page from `features/index.md`, or exclude it
deliberately) — **not** to this suite, and not to a `srcExclude` entry added just to quiet the
test.

Expected once the docs gate is healthy:

```
✓ T1 srcExclude guard                      (green)
✓ T2 no dead nav/sidebar links             (green)
✗ T3 no orphan feature pages               (red on features/channel-creator.md)
 Tests  1 failed | 6 passed
```

## Scope — deliberately not here

- `transformation/` is excluded by `srcExclude` and not scanned by T3.
- `delivery-contract.md` sits at the root and is intentionally not in the nav; T3 only inspects
  `features/`.
- No coverage threshold: a coverage percentage over static markdown measures nothing.
- `docs:build` / `build:css` are **not** the QA suite. A successful build verifies compilation,
  not behaviour, and declaring it would manufacture an empty green (card `6c697929`).
