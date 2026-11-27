/**
 * Harness tree grouping: chats are grouped by the ticket branch SAVED in the
 * chat model, and the chats without one land in the "Unknown" node.
 *
 * Run with: `cd client && node --test tests/harnessTree.test.mjs`
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  UNKNOWN_BRANCH,
  UNKNOWN_BRANCH_KEY,
  branchKey,
  harnessTreeRows
} from '../src/utils/harnessTree.js'

const session = (id, branch, extra = {}) => ({
  session_id: id,
  name: `chat ${id}`,
  branch,
  phase: 'idle',
  ...extra
})

test('branchKey uses the recorded branch and falls back to unknown', () => {
  assert.equal(branchKey({ branch: 'feature/a' }), 'feature/a')
  assert.equal(branchKey({ branch: '  ' }), UNKNOWN_BRANCH_KEY)
  assert.equal(branchKey({}), UNKNOWN_BRANCH_KEY)
  assert.equal(branchKey(null), UNKNOWN_BRANCH_KEY)
})

test('chats are grouped by the ticket branch saved in the chat model', () => {
  const rows = harnessTreeRows([
    session('1', 'feature/a'),
    session('2', 'feature/b'),
    session('3', 'feature/a')
  ])

  const headers = rows.filter(row => row.__branchHeader)
  assert.deepEqual(headers.map(row => row.__branchLabel), ['feature/a', 'feature/b'])
  assert.deepEqual(headers.map(row => row.__branchCount), [2, 1])
  assert.deepEqual(
    rows.filter(row => !row.__branchHeader).map(row => [row.session_id, row.__branchKey]),
    [['1', 'feature/a'], ['3', 'feature/a'], ['2', 'feature/b']]
  )
})

test('chats without a branch are grouped in the Unknown node, sorted last', () => {
  const rows = harnessTreeRows([
    session('1', ''),
    session('2', 'feature/a'),
    session('3')
  ])

  const headers = rows.filter(row => row.__branchHeader)
  assert.deepEqual(headers.map(row => row.__branchLabel), ['feature/a', UNKNOWN_BRANCH])
  const unknown = headers[headers.length - 1]
  assert.equal(unknown.__branchUnknown, true)
  // No branch name: the branch menu (diff/merge/archive by branch) is not offered.
  assert.equal(unknown.__branch, '')
  assert.equal(unknown.__branchCount, 2)
  assert.deepEqual(
    rows.filter(row => row.__branchKey === UNKNOWN_BRANCH_KEY).map(row => row.session_id),
    ['1', '3']
  )
})

test('the checked out branch is sorted first and marked as current', () => {
  const rows = harnessTreeRows(
    [session('1', 'aaa'), session('2', 'zzz'), session('3', '')],
    { currentBranch: 'zzz' }
  )

  const headers = rows.filter(row => row.__branchHeader)
  assert.deepEqual(headers.map(row => row.__branchLabel), ['zzz', 'aaa', UNKNOWN_BRANCH])
  assert.deepEqual(headers.map(row => row.__branchCurrent), [true, false, false])
})

test('a collapsed branch renders its header only', () => {
  const rows = harnessTreeRows(
    [session('1', 'feature/a'), session('2', 'feature/b')],
    { collapsed: ['feature/a'] }
  )

  assert.deepEqual(
    rows.map(row => row.__branchHeader ? `header:${row.__branchKey}` : row.session_id),
    ['header:feature/a', 'header:feature/b', '2']
  )
})

test('search filters sessions and drops the empty branches', () => {
  const rows = harnessTreeRows(
    [
      session('1', 'feature/a', { name: 'Fix the export' }),
      session('2', 'feature/b', { name: 'Add docs' })
    ],
    { search: 'export' }
  )

  assert.deepEqual(rows.map(row => row.session_id), ['branch:feature/a', '1'])
})

test('running sessions are counted per branch', () => {
  const rows = harnessTreeRows([
    session('1', 'feature/a', { phase: 'running' }),
    session('2', 'feature/a')
  ])

  const [header] = rows
  assert.equal(header.__branchHeader, true)
  assert.equal(header.__branchRunning, 1)
})
