import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// WORKSPACE PROJECT SELECTOR
//
// The advanced (workspace) left bar offers a project selector, but only while
// the URL is inside a company workspace
// (/<companySlug>/workspace/<projectName>/…). Picking a project must switch the
// ACTIVE project — the same store-driven switch used by the top bar and the
// mobile menu — instead of navigating to a standalone project URL.
//
// The suite runs without a browser, so it reads the sources and pins the wiring.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const appsSection = await read('../src/components/layout/AppsSection.vue')
const desktopSidebar = await read('../src/components/layout/DesktopSidebar.vue')

test('the left bar renders the project selector only in workspace mode', () => {
  assert.match(
    appsSection,
    /import ProjectDetailt from '\.\.\/ProjectDetailt\.vue'/,
    'the shared project selector is used'
  )
  assert.match(
    appsSection,
    /v-if="!isCollapsed && inWorkspaceMode"/,
    'the selector is hidden outside workspace mode and on the collapsed rail'
  )
  assert.match(
    desktopSidebar,
    /:in-workspace-mode="isWorkspaceMode"/,
    'the sidebar passes the workspace-mode flag to the apps section'
  )
})

test('workspace mode is the project-scoped company URL', () => {
  const computed = desktopSidebar.slice(
    desktopSidebar.indexOf('isWorkspaceMode()'),
    desktopSidebar.indexOf('userName()')
  )
  assert.match(computed, /params\.companySlug/, 'the company segment is required')
  assert.match(computed, /params\.projectName/, 'the project segment is required')
})

test('selecting a project switches the active project in place', () => {
  const method = appsSection.slice(
    appsSection.indexOf('async onProjectSelected('),
    appsSection.indexOf('// Single entry point for opening an app')
  )
  assert.match(
    method,
    /activeProjectChanged\(project\)/,
    'the canonical store switch is used'
  )
  assert.doesNotMatch(
    method,
    /window\.location/,
    'the selector never replaces the document with a standalone project URL'
  )
  assert.match(
    method,
    /isOpenInNewTabEvent/,
    'the ctrl/cmd+click "open in a new tab" gesture is preserved'
  )
})
