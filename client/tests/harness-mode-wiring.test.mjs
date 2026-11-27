/**
 * Wiring tests for harness mode (no DOM).
 *
 * The view itself is exercised by the build + the pure view-model tests. These
 * assertions guard the three places that make the route actually reachable:
 * the router entry, the full-screen switch in the shell and the app registry
 * used by every launcher.
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import test from 'node:test'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (path) => readFileSync(join(ROOT, path), 'utf8')

test('harness mode is a full-screen route', () => {
  const router = read('src/router/index.ts')
  const app = read('src/App.vue')

  assert.match(
    router,
    /path: '\/harness-mode',[\s\S]*?name: 'harness-mode',[\s\S]*?fullScreen: true/
  )
  // The route is mounted under the company workspace prefix like every other
  // project route, so it must be in PROJECT_ROUTES (ADVANCED_ROUTES maps it).
  const projectRoutes = router.slice(
    router.indexOf('const PROJECT_ROUTES'),
    router.indexOf('const ADVANCED_ROUTES')
  )
  assert.match(projectRoutes, /name: 'harness-mode'/)
  // The shell must hand the whole window to the view (no desktop sidebar).
  assert.match(app, /route\.meta\?\.fullScreen === true/)
})

test('harness mode is registered as an app component and launcher target', () => {
  const apps = read('src/config/apps.js')
  const components = read('src/config/appComponentsMap.js')
  const sidebar = read('src/components/layout/AppsSection.vue')
  const menu = read('src/components/main-menu/WindowsMenuItems.vue')

  assert.match(apps, /'harness-mode': \{[\s\S]*?routeOnly: true,[\s\S]*?route: \(\) => \(\{ name: 'harness-mode' \}\)/)
  assert.match(components, /'harness-mode': lazy\(\(\) => import\('\.\.\/views\/HarnessModeView\.vue'\)\)/)
  assert.match(sidebar, /this\.openApp\('harness-mode'\)/)
  assert.match(read('src/router/navigate.js'), /!isDesktopMode\(\) \|\| def\.routeOnly/)
  assert.match(menu, /'harness-mode': \(\) => this\.\$ui\.openApp\('harness-mode'\)/)
  // New chat is the harness entry point; the read-only diagnostics link was
  // removed from the launch menus (still reachable from the harness footer).
  assert.match(menu, /label: 'New chat'/)
  assert.doesNotMatch(menu, /Harness diagnostics/)
  assert.doesNotMatch(menu, /harness: \(\) => this\.\$ui\.openApp\('harness'\)/)
})

test('the harness view talks to the session API and starts nothing itself', () => {
  const view = read('src/views/HarnessModeView.vue')
  assert.match(view, /harness\.createSession/)
  assert.match(view, /harness\.promptSession/)
  // The backend schedules the turn; the client must not emit a second one.
  assert.doesNotMatch(view, /chatWihProject/)
})

test('the harness transcript reuses ChatMessageList and ChatEntry', () => {
  const session = read('src/components/harness/mode/HarnessSessionView.vue')
  const list = read('src/components/chat/ChatMessageList.vue')
  const entry = read('src/components/ChatEntry.vue')
  const steps = read('src/utils/harnessSteps.js')

  // The transcript is the standard chat list, composed with the standard entry
  // (the list imports it), and the harness lens is a prop rather than a fork.
  assert.match(session, /import ChatMessageList from '@\/components\/chat\/ChatMessageList\.vue'/)
  assert.match(session, /:harness="true"/)
  assert.match(session, /@answer-option="onAnswerOption"/)
  assert.match(list, /import ChatEntry from '@\/components\/ChatEntry\.vue'/)
  assert.match(list, /:harness="harness"/)
  assert.match(list, /:options-active="harness \? true/)
  assert.match(entry, /'harness'/)
  assert.match(entry, /stripHarnessBrief/)
  // Every persisted message is projected into the transcript (hidden ones too).
  assert.match(steps, /export function harnessTranscript/)
})

test('the harness Git button opens Review on the task branch', () => {
  const view = read('src/views/HarnessModeView.vue')
  const git = read('src/components/repo/GitView.vue')
  const apps = read('src/config/apps.js')
  const ui = read('src/store/ui.js')

  assert.match(view, /openGitView\(\{\s*tab: 'review',\s*compareBranch: branch/)
  assert.match(ui, /openGitView\(options = \{\}\)/)
  assert.match(apps, /'git-view': \{[\s\S]*?desktopParams:[\s\S]*?compareBranch/)
  assert.match(apps, /'git-view': \{[\s\S]*?compareBranch: p\.compareBranch/)
  assert.match(git, /applyRequestedReview\(\)/)
  assert.match(git, /this\.reviewComparison = \{[\s\S]*?from: branch,[\s\S]*?to: target/)
  assert.match(git, /this\.activeTab = 'review'/)
})

test('the harness effort tier is reachable while continuing a session', () => {
  const view = read('src/views/HarnessModeView.vue')
  const session = read('src/components/harness/mode/HarnessSessionView.vue')
  const mode = read('src/utils/harnessMode.js')

  assert.match(session, /v-for="tier in HARNESS_TIERS"/)
  assert.match(session, /@click="emit\('update:mode', tier\.id\)"/)
  assert.match(view, /:mode="mode"[\s\S]*?@update:mode="setMode"/)
  assert.match(view, /followUpProfiles/)
  assert.match(mode, /export function modeForProfiles/)
})
