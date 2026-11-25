import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// COMPANY-FIRST CONTRACT
//
// codx-junior is a company-first product: the company owns projects (many of
// them), so the company shell is the entry point and every project screen is
// mounted below the company workspace. These assertions guard the routing and
// shell invariants that make that true. They read the sources instead of
// rendering the SPA, so the contract is checked without a browser.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const routerSource = await read('../src/router/index.ts')
const appSource = await read('../src/App.vue')
const shellSource = await read('../src/views/CompanyView.vue')

test('the application root renders the company shell', () => {
  // "/" must be company-first even on a fresh install (no company yet): the
  // shell is what opens the onboarding wizard. The project welcome screen is
  // never the entry point.
  // The route block starts at its path declaration; the previous helper began
  // after `name` and therefore never saw `path: '/'`.
  const rootStart = routerSource.indexOf("path: '/'")
  const block = routerSource.slice(rootStart, routerSource.indexOf('},', rootStart))
  assert.match(block, /path: '\/'/, 'the root route keeps the / path')
  assert.match(
    block,
    /component: CompanyView/,
    'the root route renders CompanyView, not the project shell'
  )
})

test('project screens live under the company workspace prefix', () => {
  assert.match(
    routerSource,
    /path: `\/:companySlug\/workspace\/:projectName\$\{route\.path\}`/,
    'every project route is mounted under /:companySlug/workspace/:projectName'
  )
  assert.match(
    routerSource,
    /path: `\/:companySlug\/:section\(\$\{COMPANY_SECTION_RE\}\)\?`/,
    'the company shell owns /<slug> and /<slug>/<section>'
  )
})

test('the company shell owns the work tasks and the workplaces', () => {
  // Tasks (work) and the workplaces (meet, coworking, calendar) used to be
  // project-level screens; they are company sections now.
  for (const section of ['work', 'tickets', 'coworking', 'meet', 'calendar', 'projects']) {
    assert.match(
      routerSource,
      new RegExp(`'${section}'`),
      `"${section}" is a company section`
    )
    assert.match(
      shellSource,
      new RegExp(`id: '${section}'`),
      `the company shell exposes the "${section}" section`
    )
  }
})

test('the root route is treated as a full-screen company route', () => {
  assert.match(
    appSource,
    /route\.name === 'company-home' \|\| route\.name === 'root'/,
    'App.vue renders the company shell full screen on / as well'
  )
})

test('the company shell has no active-project selector', () => {
  // A company aggregates every project it links. There is deliberately no
  // top-bar project picker that would scope the whole company to one project
  // (and used to offer inherited child projects as if they were company links).
  assert.doesNotMatch(shellSource, /cp-project-select/)
  assert.doesNotMatch(shellSource, /onSelectProject/)
  assert.doesNotMatch(shellSource, /companyProjectOptions/)
  assert.match(
    shellSource,
    /\$storex\.company\.linkedProjects/,
    'company surfaces read the projects the company defines'
  )
  assert.match(
    shellSource,
    /\$storex\.company\.linkedProjectIds/,
    'company-wide actions use every linked project'
  )
})
