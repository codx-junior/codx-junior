/**
 * Date ranges shared by the task lists.
 *
 * "Last 7 days" is the rolling week the follow-up review opens with. There is
 * deliberately no "this week" option: a calendar week (Monday→Sunday) is a
 * different question from "what moved in the last 7 days", and mixing the two
 * made the default view disagree with the filters.
 *
 * Dates are local calendar days (YYYY-MM-DD). Using `toISOString()` here would
 * shift the bounds by a day in negative timezones.
 */

export const TASK_RANGE_OPTIONS = [
  { label: 'Today', value: 'today' },
  { label: 'Last 7 days', value: 'week' },
  { label: 'Last 30 days', value: 'month' },
  { label: 'Custom', value: 'custom' }
]

export const DEFAULT_TASK_RANGE = 'week'

/** Local calendar date of `date` as YYYY-MM-DD. */
export function localDateString(date = new Date()) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

/** Local calendar date `days` before `base` (negative values go forward). */
export function shiftLocalDays(days, base = new Date()) {
  const date = new Date(base)
  date.setDate(date.getDate() + days)
  return localDateString(date)
}

/**
 * Resolve a range id to the `{ from_date, to_date }` pair `/api/tasks` expects.
 *
 * @param {string} range - today | week | month | custom.
 * @param {Object} [options]
 * @param {string|null} [options.from] - custom start day.
 * @param {string|null} [options.to] - custom end day.
 * @param {Date} [options.now] - reference instant (tests pass a fixed date).
 * @returns {{from_date: string, to_date: string}}
 */
export function taskDateRange(range, { from = null, to = null, now = new Date() } = {}) {
  const today = localDateString(now)
  if (range === 'today') {
    return { from_date: today, to_date: today }
  }
  if (range === 'week') {
    return { from_date: shiftLocalDays(-7, now), to_date: today }
  }
  if (range === 'custom') {
    return {
      from_date: from || shiftLocalDays(-30, now),
      to_date: to || today
    }
  }
  // month (the default for the planning selector) is the rolling 30 days.
  return { from_date: shiftLocalDays(-30, now), to_date: today }
}
