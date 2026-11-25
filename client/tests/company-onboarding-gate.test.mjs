import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// FIRST-RUN GATE CONTRACT
//
// A fresh installation has no company basics yet. codx-junior must run the
// company wizard and must not let the owner use the product until it is
// finished. These assertions read the sources (the suite runs without a
// browser), so they guard the gate wiring that a refactor could silently drop.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const appSource = await read('../src/App.vue')
const wizardSource = await read('../src/components/company/CompanyWizard.vue')

test('the app shell is replaced by the company wizard until setup is done', () => {
  assert.match(
    appSource,
    /v-else-if="setupRequired"/,
    'App.vue gates the shell on the server setup state'
  )
  assert.match(
    appSource,
    /<CompanyWizard\s+:open="true"\s+mandatory/s,
    'the gate renders the mandatory wizard'
  )
  assert.match(
    appSource,
    /status\?\.setup_required \?\? status\?\.needs_setup/,
    'the gate reads the server setup flag, so a reload cannot bypass it'
  )
})

test('the gate fails closed when the setup state cannot be read', () => {
  assert.match(appSource, /v-else-if="setupError"/)
  assert.match(appSource, /Cannot read the setup state/)
  assert.doesNotMatch(
    appSource,
    /catch[^}]+setupRequired\.value = true/s,
    'a failing request must not pretend setup is required forever'
  )
})

test('the wizard cannot be closed or skipped while it is the gate', () => {
  assert.match(wizardSource, /v-if="!mandatory"/, 'the close control is hidden')
  assert.match(wizardSource, /v-if="!mandatory"\s+class="btn btn-xs"[^>]*@click="skipWizard"/s)
  assert.match(
    wizardSource,
    /function close\(\)\s*\{\s*\/\/[^\n]*\n\s*if \(mandatory\.value\) return/s,
    'close is a no-op while the wizard is mandatory'
  )
  assert.match(
    wizardSource,
    /async function skipWizard\(\)\s*\{\s*if \(mandatory\.value\) return/s,
    'skip setup is a no-op while the wizard is mandatory'
  )
})

test('the company step cannot be skipped and skipping never finishes the wizard', () => {
  assert.match(wizardSource, /current === 'company'/)
  assert.match(wizardSource, /step !== 'company'/)
  assert.doesNotMatch(
    wizardSource,
    /finish\(\{ skipped: true \}\)/,
    'skipping a step must never complete the installation gate'
  )
  assert.match(
    wizardSource,
    /async function finish\(\)/,
    'there is exactly one way to finish: apply the company and mark it completed'
  )
})

test('the first-run wizard configures the provisioned company, not a duplicate', () => {
  assert.match(
    wizardSource,
    /existingCompanies\.length && !mandatory/,
    'creating a second company is an optional-mode action, not a way out of the gate'
  )
})

test('the projects step defaults to every discovered project', () => {
  assert.match(
    wizardSource,
    /form\.value\.projectIds = allByDefault \? catalog : linked/,
    'the whole catalog is selected by default and a narrowed company keeps its list'
  )
  assert.match(
    wizardSource,
    /all_projects: true/,
    'finishing with all projects keeps the wildcard for future projects'
  )
})
