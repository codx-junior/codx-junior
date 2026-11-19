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

// Creation time of the task's newest message — the STABLE activity signal.
//
// A message is created ONCE, while its `updated_at` (and the chat's) moves on
// every streamed chunk. Sorting on the creation date therefore keeps the list
// still while an answer is being generated, instead of reshuffling it per token.
export function lastMessageCreatedAt(task) {
  if (!task) return null

  const messages = Array.isArray(task.messages) ? task.messages : []
  let newest = null
  let newestTime = 0
  messages.forEach((message) => {
    // `created_at` / `timestamp` only: `updated_at` moves while streaming.
    const candidate = message?.created_at || message?.timestamp || null
    if (!candidate) return
    const time = new Date(candidate).getTime()
    if (!Number.isNaN(time) && time > newestTime) {
      newestTime = time
      newest = candidate
    }
  })
  if (newest) return newest

  // Chat summary without its messages loaded: the store's own bookkeeping.
  return task.lastMessageAt || task.created_at || null
}

// Bucketed last-message activity of a task: `null` when the task has none.
// Sorting on this value is what keeps the task lists from flickering.
export function taskActivityTime(task) {
  return bucketTime(lastMessageCreatedAt(task))
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
