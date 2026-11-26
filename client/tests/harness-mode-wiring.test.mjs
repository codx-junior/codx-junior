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
  // The read-only diagnostics view stays available.
  assert.match(menu, /harness: \(\) => this\.\$ui\.openApp\('harness'\)/)
})

test('the harness view talks to the session API and starts nothing itself', () => {
  const view = read('src/views/HarnessModeView.vue')
  assert.match(view, /harness\.createSession/)
  assert.match(view, /harness\.promptSession/)
  // The backend schedules the turn; the client must not emit a second one.
  assert.doesNotMatch(view, /chatWihProject/)
})
