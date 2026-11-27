import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// RUNTIME REGRESSION GUARDS
//
// Console errors seen in a real session:
//   * project.js: `[...undefined]` in the userList getter
//   * calendar.js: `order.indexOf` on the API's `{calendar_id: position}` object
//   * ChatMessageList: <ChatFindBar> was used but never imported
//   * /api/tasks/boards 504: board editors fetched the full board/task scan just
//     to render the team picker.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const projectStore = await read('../src/store/project.js')
const calendarStore = await read('../src/store/calendar.js')
const messageList = await read('../src/components/chat/ChatMessageList.vue')
const kanban = await read('../src/components/kanban/board/KanbanBoardView.vue')
const sprintModals = await read('../src/components/kanban/board/SprintBoardModals.vue')
const tasksApi = await read('../src/api/modules/tasks.js')
const tasksRouter = await read('../../api/codx/junior/api/tasks.py')

test('the chat user list tolerates a project without profiles', () => {
  assert.match(
    projectStore,
    /userList: \(\) => \[[\s\S]*?\$storex\.projects\.profiles \|\| \[\]/,
    'profiles are defaulted to an array before spreading'
  )
  assert.doesNotMatch(
    projectStore.slice(projectStore.indexOf('userList:'), projectStore.indexOf('projectApps:')),
    /\.\.\.\$storex\.projects\.profiles\?\.map/,
    'never spread the optional profiles value directly'
  )
})

test('calendar preferences accept the API object order and legacy arrays', () => {
  assert.match(calendarStore, /order: \{\}/, 'the API stores order as an object')
  assert.match(calendarStore, /function normalizePrefs\(/)
  assert.match(calendarStore, /Array\.isArray\(rawOrder\)/)
  assert.match(calendarStore, /Number\(order\?\.\[id\]\)/)
  assert.doesNotMatch(
    calendarStore.slice(calendarStore.indexOf('calendarList:'), calendarStore.indexOf('calendarsById:')),
    /order\.indexOf\(cal\.id\)[^\n]*\n\s*return index/,
    'the object order is not treated as an index-based array'
  )
})

test('ChatMessageList imports the find bar it renders', () => {
  assert.match(messageList, /<ChatFindBar/)
  assert.match(messageList, /import ChatFindBar from '@\/components\/chat\/ChatFindBar\.vue'/)
})

test('board editors use the lightweight teams endpoint', () => {
  assert.match(tasksApi, /teams\(\) \{\s*return API\.get\(`\$\{BASE\}\/teams`\)/)
  assert.match(kanban, /API\.tasks\.teams\(\)/)
  assert.match(sprintModals, /API\.tasks\.teams\(\)/)
  assert.doesNotMatch(kanban, /API\.tasks\.boards\(\)/)
  assert.doesNotMatch(sprintModals, /API\.tasks\.boards\(\)/)
  assert.match(tasksRouter, /@router\.get\("\/teams"\)/)
})
