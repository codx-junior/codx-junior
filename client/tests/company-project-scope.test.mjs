import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// COMPANY PROJECT SCOPE
//
// A company defines its projects once (Projects → links / "*"). Every company
// surface must aggregate that same list, never a single active project and
// never an unrelated project discovered by the installation. These assertions
// read the sources because the suite runs without a browser.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const store = await read('../src/store/company.js')
const shell = await read('../src/views/CompanyView.vue')
const security = await read('../src/components/company/CompanySecurity.vue')
const chatPanel = await read('../src/components/company/CompanyChatPanel.vue')
const calendarWidget = await read('../src/components/company/CompanyCalendar.vue')
const calendarView = await read('../src/views/CalendarView.vue')
const tasks = await read('../src/views/GlobalTasksView.vue')

test('the company store exposes the projects the company defines', () => {
  assert.match(store, /linkedProjects: \(state, getters\) =>/)
  assert.match(store, /linkedProjectIds: \(state, getters\) =>/)
  assert.match(
    store,
    /state\.snapshot\?\.projects/,
    'the linked list is the snapshot Projects section, not every visible project'
  )
})

test('the company shell reads the company-linked projects', () => {
  assert.match(shell, /\$storex\.company\.linkedProjects/)
  assert.match(shell, /\$storex\.company\.linkedProjectIds/)
  assert.doesNotMatch(shell, /companyProjectOptions/)
  assert.doesNotMatch(shell, /onSelectProject/)
})

test('security shows every company project, not one active project', () => {
  assert.doesNotMatch(
    security,
    /projectId: \{ type: String/,
    'CompanySecurity no longer accepts a single project id'
  )
  assert.doesNotMatch(security, /props\.projectId\b/)
  assert.match(
    security,
    /api\.security\(props\.companyId\)/,
    'the endpoint returns the company-wide project list'
  )
})

test('the company calendar widget queries every company project', () => {
  assert.match(calendarWidget, /projectIds = computed/)
  assert.match(calendarWidget, /projects: projectIds\.value/)
  assert.doesNotMatch(calendarWidget, /activeProject/)
})

test('the calendar view keeps its project filter inside the company', () => {
  assert.match(calendarView, /companyScopeIds = computed/)
  assert.match(calendarView, /ensureCompanyScope/)
  assert.match(calendarView, /All company projects/)
  assert.match(calendarView, /scopeEmpty/)
})

test('the company task list defaults to every linked project', () => {
  assert.match(tasks, /linkedProjectIds/)
  assert.doesNotMatch(tasks, /rootProjectIds/)
})

test('company workspaces only offer company projects', async () => {
  for (const file of [
    '../src/components/workspaces/Workspaces.vue',
    '../src/components/workspaces/WorkspaceProjectSelector.vue',
    '../src/components/workspaces/WorkspaceCreate.vue',
    '../src/components/workspaces/WorkspacesList.vue'
  ]) {
    const source = await read(file)
    assert.match(
      source,
      /linkedProjects/,
      `${file} must read the company-linked project list`
    )
  }
  const selector = await read('../src/components/workspaces/WorkspaceProjectSelector.vue')
  assert.match(
    selector,
    /v-for="proj in availableProjects"/,
    'the selector renders the company-scoped list'
  )
})

test('the channel modal never offers an undiscovered project as the host', () => {
  // A link is a reference; only a discovered project can store the chat. The
  // modal used to list every linked project and submit the picked one, so an
  // undiscovered project produced "Project '...' is not available (not
  // discovered)" from the API after the owner did what the selector suggested.
  assert.match(
    chatPanel,
    /project\.available !== false/,
    'the default host is a discovered project'
  )
  assert.match(
    chatPanel,
    /selectedProjectUnavailable/,
    'the modal explains why the selected project cannot host the channel'
  )
  assert.match(
    chatPanel,
    /project\.available === false \? ' \(not discovered\)'/,
    'undiscovered projects are labelled in the selector'
  )
  assert.match(
    chatPanel,
    /:disabled="busy \|\| selectedProjectUnavailable \|\| !form\.project_id"/,
    'the create button is disabled for an undiscovered host'
  )
})
