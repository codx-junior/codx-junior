// Task activity + error helpers shared by the board and the task lists.
//
// `updated_at` moves on EVERY streamed message, so sorting by it makes cards
// and boards jump around while a task is being answered. `bucketTime` rounds a
// timestamp DOWN to a 10 minute slice: two tasks touched inside the same slice
// compare equal, so the sort keeps their previous relative order instead of
// flipping on every token. The bucket containing "now" is always the newest
// one, so a freshly touched task still goes to the top — once per 10 minutes,
// not once per message.

export const ACTIVITY_BUCKET_MINUTES = 10

const BUCKET_MS = ACTIVITY_BUCKET_MINUTES * 60 * 1000

// Floor a timestamp (ISO string, Date or epoch) to the start of its bucket.
// Missing / invalid values return null so callers treat them as "no activity".
export function bucketTime(value, sizeMs = BUCKET_MS) {
  if (!value) return null
  const time = value instanceof Date ? value.getTime() : new Date(value).getTime()
  if (Number.isNaN(time)) return null
  return Math.floor(time / sizeMs) * sizeMs
}

// "Most recent first" comparator over bucketed timestamps. Returns 0 when both
// values fall in the same bucket, which keeps the sort stable (no flickering).
export function compareActivity(a, b) {
  const aTs = bucketTime(a)
  const bTs = bucketTime(b)
  if (aTs === bTs) return 0
  if (aTs === null) return 1
  if (bTs === null) return -1
  return aTs > bTs ? -1 : 1
}

// Harness / generation states that mean the task did NOT finish cleanly.
const ERROR_HARNESS_STATUSES = ['failed', 'stale', 'error']

// Human readable reason when a task ended in error ("Stopped by caller", model
// failure, stale harness...). Returns null when the task looks healthy, so the
// same helper doubles as a `v-if` flag.
export function taskError(task) {
  if (!task) return null

  const raw = task.error || task.last_error || task.error_message || task.harness_error || null
  if (raw) {
    if (typeof raw === 'string') return raw
    return raw.message || raw.detail || 'Task failed'
  }

  const harness = String(task.harness_status || '').toLowerCase()
  if (ERROR_HARNESS_STATUSES.includes(harness)) return `Task ${harness}`

  return null
}

export function hasTaskError(task) {
  return !!taskError(task)
}
