import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// 0.0.1 RELEASE NAVIGATION / FIRST-RUN UX
//
// Source-level guards for the launch UI: disabled features stay out of every
// launcher, New chat is harness mode, the setup assistant is a real toggleable
// card and the first-run wizard lands on the project workspace (so the project
// selector is visible from the first screen).
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')
const exists = path => stat(new URL(path, import.meta.url)).then(() => true, () => false)

const appsSection = await read('../src/components/layout/AppsSection.vue')
const mainMenu = await read('../src/components/main-menu/MainMenuItems.vue')
const windowsMenu = await read('../src/components/main-menu/WindowsMenuItems.vue')
const settingsMenu = await read('../src/components/main-menu/SettingsMenu.vue')
const mobileMenu = await read('../src/components/mobile/MobileMenu.vue')
const featureConfig = await read('../src/config/features.js')
const router = await read('../src/router/index.ts')
const wizard = await read('../src/components/setup/WorkspaceWizard.vue')
const launcher = await read('../src/components/setup/SetupAssistantLauncher.vue')
const assistant = await read('../src/components/setup/SetupAssistantPanel.vue')
const appMap = await read('../src/config/appComponentsMap.js')
const globalSettings = await read('../src/views/GlobalSettings.vue')
const setupApi = await read('../../api/codx/junior/api/setup.py')
const settingsModel = await read('../../api/codx/junior/model/model.py')
const mobileConfig = await read('../src/config/mobile.js')
const appSource = await read('../src/App.vue')

test('gated release surfaces are hidden from desktop and mobile launchers', () => {
  assert.match(featureConfig, /infra: 'infra'/, 'Infrastructure is feature-gated')
  assert.match(featureConfig, /channel: 'channel-creator'/, 'Channel is feature-gated')
  assert.match(featureConfig, /messenger: 'teams'/, 'Messenger is feature-gated')
  assert.match(featureConfig, /calendar: 'calendar'/, 'Calendar is feature-gated')

  assert.match(appsSection, /v-if="\$users\.isAdmin && appEnabled\('infra'\)"/)
  assert.match(appsSection, /v-if="appEnabled\('channel'\)"/)
  assert.match(appsSection, /v-if="appEnabled\('calendar'\)"/)
  assert.doesNotMatch(appsSection, /label="Harness"/, 'the Harness launcher item is removed')

  assert.match(windowsMenu, /!item\.feature \|\| isFeatureEnabled\(this\.\$storex\.features, item\.feature\)/)
  assert.match(windowsMenu, /\{ label: 'New chat'.*action: 'harness-mode' \}/)
  assert.match(windowsMenu, /\{ label: 'Infrastructure'.*feature: 'infra' \}/)
  assert.doesNotMatch(windowsMenu, /Harness diagnostics/, 'the diagnostics link is removed')
  assert.doesNotMatch(windowsMenu, /WorkspacesMenu/, 'the deprecated workspaces menu is removed')

  assert.match(mobileMenu, /isAppEnabled\(\$storex\.features, app\.key\)/)
  assert.match(mobileMenu, /appKey: 'harness-mode'/, 'mobile New chat opens harness mode')
  assert.doesNotMatch(mobileConfig, /key: 'harness'/)
})

test('new chat switches to harness mode and settings menus drop deprecated entries', () => {
  assert.match(appsSection, /handleNavigateToNewChat\(\)[\s\S]*?this\.openApp\('harness-mode'\)/)
  assert.match(mainMenu, /function openNewChat\(\)[\s\S]*?openApp\('harness-mode'\)/)
  assert.doesNotMatch(mainMenu, /WorkspacesMenu/, 'the deprecated workspaces menu is removed')
  assert.match(settingsMenu, /featureEnabled\('logs'\)/)
  assert.match(settingsMenu, /featureEnabled\('analytics'\)/)
})

test('the setup assistant is a hidden, fixed-height card with the codx-junior logo', () => {
  assert.match(wizard, /const assistantVisible = ref\(false\)/, 'it starts hidden')
  assert.match(wizard, /function toggleAssistant\(\)/)
  assert.match(wizard, /v-if="assistantVisible"/)
  assert.match(wizard, /h-\[320px\].*lg:h-\[520px\]/, 'the card has a fixed height')
  assert.match(wizard, /lg:w-\[390px\].*lg:border-l/, 'right column on desktop, bottom on mobile')
  assert.match(wizard, /\/only_icon\.png/)
  assert.doesNotMatch(wizard, /fa-robot/)
  assert.match(assistant, /\/only_icon\.png/)
  assert.doesNotMatch(assistant, /fa-robot/)

  assert.match(launcher, /v-if="open"/)
  assert.match(launcher, /const open = ref\(false\)/)
  assert.match(launcher, /\/only_icon\.png/)
  assert.match(appSource, /SetupAssistantLauncher/)
})

test('the first-run wizard lands on the project workspace, not the catalogue', () => {
  assert.match(wizard, /const target = slug && projectName\n?\s*\? `\/\$\{slug\}\/workspace\/\$\{encodeURIComponent\(projectName\)\}`/)
  assert.match(wizard, /window\.location\.assign\(target\)/)
  assert.match(appsSection, /canShowProjectSelector\(\)[\s\S]*?activeProject/)
})

test('a real chat model is prefilled for every documented provider', () => {
  for (const model of ['deepseek-flash', 'gpt-4.1-mini', 'claude-sonnet-4-5', 'qwen2.5-coder:7b']) {
    assert.match(wizard, new RegExp(model.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(wizard, /value="anthropic"/)
  assert.match(wizard, /value="ollama"/)
  assert.match(setupApi, /"deepseek-flash"/)
  assert.match(setupApi, /"gpt-4\.1-mini"/)
  assert.match(setupApi, /"claude-sonnet-4-5"/)
  assert.match(setupApi, /"qwen2\.5-coder:7b"/)
  assert.match(setupApi, /provider_type == "ollama" and not api_key/)
})

test('the old project chart view is gone and Home is the new start page', () => {
  assert.match(appMap, /'home': lazy\(\(\) => import\('\.\.\/views\/HomeView\.vue'\)\)/)
  assert.match(appMap, /'projects': lazy\(\(\) => import\('\.\.\/views\/ProjectsView\.vue'\)\)/)
  assert.match(router, /const WELCOME_APP = 'home'/)
  assert.doesNotMatch(globalSettings, /Workspaces/, 'the workspaces settings tab is removed')
  assert.match(settingsModel, /workspaces: Optional\[List\[Workspace\]\] = Field\(default=\[\]\)/)
})

test('the removed components no longer exist', async () => {
  assert.equal(await exists('../src/components/project/ProjectOverview.vue'), false)
  assert.equal(await exists('../src/components/project/ProjectCard.vue'), false)
  assert.equal(await exists('../src/components/metrics/HeatMap.vue'), false)
})
