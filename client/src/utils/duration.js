/**
 * Chat message generation time.
 *
 * Assistant messages report how long the AI took to produce the response in
 * their metadata (`meta_data.time_taken`, in seconds). A response can take a
 * fraction of a second or well over an hour, so the minutes field is never
 * folded into hours: the value is always rendered as minutes and seconds
 * (`000:07s`, `125:03s` / `00m 07s`, `125m 03s`).
 */

/** Width of the minutes and seconds fields. */
const MINUTES_WIDTH = 3
const SECONDS_WIDTH = 2
/** Minutes/seconds width of the compact `MMm SSs` label. */
const LABEL_WIDTH = 2

/**
 * Format a duration in seconds as `MMM:SSs` (minutes and seconds).
 *
 * Minutes are zero padded to three digits so the value stays tabular, and they
 * keep growing instead of carrying into an hours field — a response may run
 * longer than an hour and must still be readable.
 *
 * @param {number|string} seconds duration reported in the message metadata
 * @returns {string} `MMM:SSs`, or `000:00s` when the value is missing
 */
export function formatGenerationTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  const remainder = total % 60
  const mm = String(minutes).padStart(MINUTES_WIDTH, '0')
  const ss = String(remainder).padStart(SECONDS_WIDTH, '0')
  return `${mm}:${ss}s`
}

/**
 * Format a duration in seconds as `MMm SSs` (minutes and seconds).
 *
 * Compact label used by the message header: the live counter of a running
 * response and the reported total of a finished one share the same format, so
 * the value does not jump when streaming ends (`00m 07s`, `12m 45s`,
 * `125m 03s`). Minutes keep growing — never an hours field.
 *
 * @param {number|string} seconds elapsed or reported duration, in seconds
 * @returns {string} `MMm SSs`, or `00m 00s` when the value is missing
 */
export function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  const remainder = total % 60
  const mm = String(minutes).padStart(LABEL_WIDTH, '0')
  const ss = String(remainder).padStart(LABEL_WIDTH, '0')
  return `${mm}m ${ss}s`
}
