import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// WORKSPACE-MODE DEFAULT (0.0.1)
//
// The 0.0.1 installer writes `CODX_JUNIOR_ACTIVE_FEATURES=core`. That profile
// hides the company OS, so the client must:
//   * read the profile before rendering the shell,
//   * never open the company wizard,
//   * land on the project workspace (or `/projects` when no repository exists
//     yet), not on the company home,
//   * keep `all` installs company-first.
//
// The suite reads sources because it runs without a browser or a server.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const appSource = await read('../src/App.vue')
const routerSource = await read('../src/router/index.ts')
const featureStore = await read('../src/store/features.js')
const featureApi = await read('../src/api/modules/features.js')
const apiSource = await read('../src/api/api.js')
const appsSection = await read('../src/components/layout/AppsSection.vue')
const mobileMenu = await read('../src/components/mobile/MobileMenu.vue')

test('the client has a feature-profile store backed by /api/features', () => {
  assert.match(featureStore, /companyEnabled:/, 'the store exposes the shell switch')
  assert.match(featureStore, /companyWizardEnabled:/, 'the store exposes the wizard switch')
  assert.match(featureStore, /API\.features\.get\(\)/, 'the profile is loaded from the API')
  assert.match(featureApi, /\/api\/features/, 'the API module names the endpoint')
  assert.match(apiSource, /get features\(\)/, 'API exposes the features module')
})

test('the company wizard is gated on the feature profile', () => {
  assert.match(
    appSource,
    /v-else-if="setupRequired && companyEnabled"/,
    'the wizard renders only for company-enabled installs'
  )
  assert.match(
    appSource,
    /await \$storex\.features\?\.ensureLoaded\?\.\(\)/,
    'the profile is loaded before the setup state is interpreted'
  )
})

test('workspace mode lands on the project workspace, not the company home', () => {
  assert.match(routerSource, /function defaultWorkspaceHome\(/, 'there is a workspace landing')
  assert.match(
    routerSource,
    /companyModeEnabled\(\) \? defaultCompanyHome\(\) : defaultWorkspaceHome\(\)/,
    'the landing follows the feature profile'
  )
  assert.match(
    routerSource,
    /name: 'workspace-projects'/,
    'a fresh workspace install has a project bootstrap route'
  )
  assert.match(
    routerSource,
    /path: '\/projects'/,
    'the bootstrap route is addressable'
  )
})

test('the company launcher is hidden in workspace mode', () => {
  assert.match(
    appsSection,
    /companyEnabled\(\)/,
    'the sidebar derives visibility from the profile'
  )
  assert.match(
    appsSection,
    /\$storex\.features\?\.companyEnabled !== false/,
    'the sidebar reads the feature profile'
  )
  assert.match(appsSection, /v-if="companyEnabled"\s+icon="fas fa-building-columns"/, 'the Company item is filtered')
  assert.match(mobileMenu, /app\.key !== 'company' \|\| companyEnabled\.value/, 'the mobile launcher filters the company tile')
})

test('a company deep link/reload is redirected after the profile resolves', () => {
  assert.match(
    appSource,
    /!companyEnabled\.value && route\.name === 'company-home'/,
    'the shell leaves a company URL once workspace mode is known'
  )
  assert.match(
    appSource,
    /companyEnabled\.value && route\.name === 'workspace-projects'/,
    'the /projects bootstrap returns to the company home in full mode'
  )
})

test('company URLs redirect away when the company shell is hidden', () => {
  assert.match(
    routerSource,
    /to\.meta\?\.company && !companyModeEnabled\(\)/,
    'company sections are guarded'
  )
  assert.match(
    routerSource,
    /return defaultWorkspaceHome\(\)/,
    'a hidden company shell falls back to the workspace'
  )
})
