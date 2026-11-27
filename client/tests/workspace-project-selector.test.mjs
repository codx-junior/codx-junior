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

test('the left bar renders the project selector whenever a project exists', () => {
  assert.match(
    appsSection,
    /import ProjectDetailt from '\.\.\/ProjectDetailt\.vue'/,
    'the shared project selector is used'
  )
  assert.match(
    appsSection,
    /v-if="!isCollapsed && canShowProjectSelector"/,
    'the selector is hidden only on the collapsed rail'
  )
  assert.match(
    appsSection,
    /canShowProjectSelector\(\) \{[\s\S]*?inWorkspaceMode \|\| this\.\$storex\.projects\.activeProject/,
    'the selector is visible on the bootstrap screen as soon as a project is known'
  )
  assert.match(
    desktopSidebar,
    /:in-workspace-mode="isWorkspaceMode"/,
    'the sidebar passes the workspace-mode flag to the apps section'
  )
  assert.match(
    appsSection,
    /mounted\(\) \{[\s\S]*?loadAllProjects/,
    'the launcher requests the project catalogue when the shell mounts'
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
