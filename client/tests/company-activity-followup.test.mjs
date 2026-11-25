/**
 * Company Activity contract: reuse the workspace Follow-up view and make the
 * workspace switch a real navigation.
 *
 * Run with: node --test tests/*.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = path => readFile(new URL(path, import.meta.url), 'utf8')

const activity = await source('../src/components/company/CompanyActivity.vue')
const companyView = await source('../src/views/CompanyView.vue')
const globalTasks = await source('../src/views/GlobalTasksView.vue')
const followup = await source('../src/components/kanban/board/FollowupView.vue')

test('company Activity reuses the workspace Follow-up task list', () => {
  assert.match(activity, /import GlobalTasksView from '@\/views\/GlobalTasksView\.vue'/)
  assert.doesNotMatch(activity, /import FollowupView/)
  assert.match(activity, /<GlobalTasksView\s+show-date-filter\s*\/>/)
})

test('the reused Follow-up list shows messages, status and unread tasks', () => {
  assert.match(globalTasks, /task\.latest_message\?\.content/)
  assert.match(globalTasks, /statusMeta\(task\.status\)/)
  assert.match(globalTasks, /task\.unread_count > 0/)
})

test('the company Follow-up opens on the last 7 days and offers no this-week option', () => {
  assert.match(globalTasks, /showDateFilter: \{ type: Boolean, default: false \}/)
  assert.match(globalTasks, /const rangeFilter = ref\(DEFAULT_TASK_RANGE\)/)
  assert.match(globalTasks, /<PlanningRangeFilter[\s\S]*?v-if="showDateFilter"/)
  assert.match(globalTasks, /from_date: dateRange\.value\.from_date/)
  assert.doesNotMatch(globalTasks, /this_week/)
  assert.doesNotMatch(followup, /this_week/)
  assert.match(followup, /rangeFilter: DEFAULT_TASK_RANGE/)
})

test('switching to the workspace view is a real, guarded navigation', () => {
  assert.match(companyView, /async function openAdvancedView\(\)/)
  assert.match(companyView, /await \$storex\.projects\.activeProjectChanged\(selected\)/)
  assert.match(companyView, /const routeName = \$storex\.ui\.isMobile \? 'home' : 'codx-junior-split'/)
  assert.match(companyView, /name: routeName/)
  assert.match(companyView, /companyProjects\.value/)
  assert.doesNotMatch(companyView, /workspace\/\$\{encodeURIComponent\(selected\.project_name\)\}\/desktop/)
})
