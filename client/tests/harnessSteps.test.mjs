/**
 * Tests of the harness-mode view model (client/src/utils/harnessSteps.js).
 *
 * Run with: `cd client && node --test tests/harnessSteps.test.mjs`
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  HARNESS_BRIEF_MARKER,
  activitySummary,
  buildHarnessView,
  diffFiles,
  filesSummary,
  formatDuration,
  formatTokens,
  stripHarnessBrief
} from '../src/utils/harnessSteps.js'

const brief = `${HARNESS_BRIEF_MARKER}## Harness mode\nresolve it`

function userMessage(content, id = 'u1') {
  return { doc_id: id, role: 'user', content: `${content}${brief}`, done: true }
}

function assistantMessage(overrides = {}) {
  return {
    doc_id: 'a1',
    role: 'assistant',
    content: 'Delivered.',
    done: true,
    created_at: '2026-11-26T10:00:00Z',
    tool_events: [],
    phase_events: [],
    lifecycle_events: [],
    ...overrides
  }
}

test('strips only the appended harness contract', () => {
  assert.equal(stripHarnessBrief(`Fix it${brief}`), 'Fix it')
  assert.equal(stripHarnessBrief('hello'), 'hello')
  assert.equal(stripHarnessBrief(''), '')
})

test('formats durations and tokens the DSH way', () => {
  assert.equal(formatDuration(0), '0s')
  assert.equal(formatDuration(9), '9s')
  assert.equal(formatDuration(63), '1m 3s')
  assert.equal(formatDuration(821), '13m 41s')
  assert.equal(formatDuration(3725), '1h 2m')
  assert.equal(formatTokens(999), '999')
  assert.equal(formatTokens(1200), '1.2k')
  assert.equal(formatTokens(221000), '221.0k')
  assert.equal(formatTokens(2000000), '2.0m')
})

test('builds the request, report, activity and files of a session', () => {
  const messages = [
    userMessage('Fix the empty-board CSV export'),
    assistantMessage({
      content: '## Problem\nEmpty boards crash.\n\n## Root cause\nNo header.',
      tool_events: [
        {
          tool: 'project_read_file',
          tool_call_id: 't1',
          status: 'done',
          duration_ms: 120,
          request: { file_path: 'api/export.py' }
        },
        {
          tool: 'project_write_file',
          tool_call_id: 't2',
          status: 'done',
          duration_ms: 80,
          request: { file_path: 'api/export.py' }
        },
        {
          tool: 'qa_run_tests',
          tool_call_id: 't3',
          status: 'error',
          duration_ms: 500,
          request: { suite: 'api' },
          error: '2 failed'
        }
      ],
      phase_events: [
        {
          kind: 'llm_request',
          status: 'done',
          duration_ms: 1000,
          label: 'Thinking'
        }
      ],
      lifecycle_events: [{ run_id: 'r1', status: 'done', duration_ms: 2000 }]
    })
  ]
  const view = buildHarnessView(messages, {
    diff: {
      files: [
        { path: 'api/export.py', additions: 12, deletions: 3 },
        { path: 'api/tests/test_export.py', additions: 30, deletions: 0 }
      ]
    },
    session: { phase: 'done' }
  })

  assert.equal(view.request, 'Fix the empty-board CSV export')
  assert.equal(view.exchanges.length, 1)
  assert.equal(view.report.content.startsWith('## Problem'), true)
  assert.equal(view.toolCount, 3)
  assert.equal(view.failedCount, 1)
  assert.equal(view.steps.length, 5) // 3 tools + 1 phase + 1 lifecycle
  assert.equal(view.durationMs, 3700)
  assert.equal(view.live, false)
  assert.equal(view.activity, 'Read files, edited files, ran tests')
  assert.deepEqual(
    view.steps.filter(step => step.kind === 'tool').map(step => step.label),
    ['Read', 'Edited', 'Ran tests']
  )
  assert.equal(view.steps.find(step => step.tool === 'project_read_file').detail, 'api/export.py')
  assert.equal(view.files.length, 2)
  assert.equal(filesSummary(view.files), '2 files +42 -3')
})

test('reports a session as live while the assistant is streaming', () => {
  const view = buildHarnessView(
    [userMessage('Do it'), assistantMessage({ done: false, content: 'Working…' })],
    { session: { phase: 'running' } }
  )
  assert.equal(view.live, true)
})

test('a just-created session with only the ticket is pending, not done', () => {
  const view = buildHarnessView([userMessage('Do it')], { session: { phase: 'idle' } })
  assert.equal(view.pending, true)
  assert.equal(view.live, true)
  assert.equal(view.report, null)
})

test('groups follow-ups into exchanges and keeps the last report', () => {
  const view = buildHarnessView([
    userMessage('First', 'u1'),
    assistantMessage({ doc_id: 'a1', content: 'First answer' }),
    userMessage('Second', 'u2'),
    assistantMessage({ doc_id: 'a2', content: 'Second answer' })
  ])
  assert.equal(view.exchanges.length, 2)
  assert.equal(view.exchanges[0].assistant.content, 'First answer')
  assert.equal(view.exchanges[1].assistant.content, 'Second answer')
  assert.equal(view.report.content, 'Second answer')
  assert.equal(view.request, 'First')
})

test('a follow-up awaiting its turn is pending and live', () => {
  const view = buildHarnessView([
    userMessage('First', 'u1'),
    assistantMessage({ doc_id: 'a1', content: 'First answer' }),
    userMessage('Second', 'u2')
  ])
  assert.equal(view.pending, true)
  assert.equal(view.live, true)
  assert.equal(view.report.content, 'First answer')
  assert.equal(view.exchanges.length, 2)
})

test('ignores drafts and archived messages', () => {
  const view = buildHarnessView([
    userMessage('Real'),
    { doc_id: 'draft', role: 'user', content: 'typing', unsent: true },
    assistantMessage({ doc_id: 'a1', hide: true, content: 'archived' })
  ])
  assert.equal(view.steps.length, 0)
  assert.equal(view.report, null)
  assert.equal(view.request, 'Real')
})

test('diff helpers are defensive', () => {
  assert.deepEqual(diffFiles(null), [])
  assert.equal(filesSummary([]), 'No files changed')
  assert.equal(filesSummary([{ path: 'a', additions: 0, deletions: 0 }]), '1 file')
  assert.equal(activitySummary([]), 'Worked')
})

test('renders phase rows as thinking activity', () => {
  const view = buildHarnessView([
    userMessage('Why?'),
    assistantMessage({
      phase_events: [
        { kind: 'llm_request', status: 'done', duration_ms: 400 },
        { kind: 'notice', status: 'done', detail: 'Loop guard' }
      ]
    })
  ])
  assert.deepEqual(view.steps.map(step => step.label), ['Thinking', 'Notice'])
  assert.equal(view.activity, 'Worked') // 'think' has no category label
})
