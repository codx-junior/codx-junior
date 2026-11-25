/**
 * Tests for the Follow-up date range.
 *
 * Run with: node --test tests/*.test.mjs
 */
import assert from 'node:assert/strict'
import {
  DEFAULT_TASK_RANGE,
  TASK_RANGE_OPTIONS,
  localDateString,
  shiftLocalDays,
  taskDateRange
} from '../src/utils/taskDateRange.js'

const reference = new Date(2026, 10, 23, 12, 0, 0) // 23 Nov 2026, local noon

assert.equal(DEFAULT_TASK_RANGE, 'week')
assert.deepEqual(
  TASK_RANGE_OPTIONS.map(option => option.value),
  ['today', 'week', 'month', 'custom'],
  'the review offers today, the rolling week, the rolling month and a custom range'
)
assert.ok(
  !TASK_RANGE_OPTIONS.some(option => option.value === 'this_week'),
  'there is no calendar-week option'
)

assert.equal(localDateString(reference), '2026-11-23')
assert.equal(shiftLocalDays(-7, reference), '2026-11-16')

assert.deepEqual(
  taskDateRange('week', { now: reference }),
  { from_date: '2026-11-16', to_date: '2026-11-23' },
  'the default range is the rolling last 7 days'
)
assert.deepEqual(
  taskDateRange('today', { now: reference }),
  { from_date: '2026-11-23', to_date: '2026-11-23' }
)
assert.deepEqual(
  taskDateRange('month', { now: reference }),
  { from_date: '2026-10-24', to_date: '2026-11-23' }
)
assert.deepEqual(
  taskDateRange('custom', {
    from: '2026-11-01',
    to: '2026-11-10',
    now: reference
  }),
  { from_date: '2026-11-01', to_date: '2026-11-10' }
)
assert.deepEqual(
  taskDateRange('custom', { from: '2026-11-01', now: reference }),
  { from_date: '2026-11-01', to_date: '2026-11-23' },
  'an open custom end is today'
)
