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
  const rootRoute = routerSource.slice(routerSource.indexOf("name: 'root'"))
  const block = rootRoute.slice(0, rootRoute.indexOf('}'))
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
  for (const section of ['work', 'coworking', 'meet', 'calendar', 'projects']) {
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

test('the company shell offers a project selector', () => {
  // A company runs several projects: the active one is chosen in the top bar
  // and every company screen follows it.
  assert.match(
    shellSource,
    /const companyProjectOptions = computed/,
    'the shell lists the projects linked to the company'
  )
  assert.match(
    shellSource,
    /async function onSelectProject\(/,
    'switching the project updates the active project in place'
  )
  assert.match(
    shellSource,
    /class="cp-project-select"/,
    'the selector is rendered in the company top bar'
  )
  assert.match(
    shellSource,
    /\$storex\.company\.companyProjects/,
    'the options come from the projects linked to the company'
  )
})
