/**
 * Date layer of the Agenda / Calendar manager.
 *
 * The SERVER returns occurrences already timezone-shifted and recurrence-
 * expanded (analysis §8.2), so the client never does calendar arithmetic
 * beyond **formatting** and grid placement. What lives here is exactly that:
 * formatters plus the range boundaries sent to `POST /api/calendar/events/query`.
 *
 * Storage / API format is UTC ISO-8601 (`...Z`). `new Date(iso)` therefore
 * yields the right local wall-clock instant to render, and the values of
 * `<input type="datetime-local">` are converted back to UTC on save.
 */

export const MS_DAY = 86400000

/** Monday-first, like the prototype (`design/06-calendar/02-prototype`). */
export const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
export const DOW_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

export const pad2 = (n) => String(n).padStart(2, '0')

/**
 * Parse an API timestamp into a local `Date`.
 * An unparseable / missing value returns `null` instead of `Invalid Date`.
 */
export function parseDate(value) {
  if (!value) return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Local `YYYY-MM-DD` key (the bucket id of a day cell). */
export function dateKey(value) {
  const date = parseDate(value)
  if (!date) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/** Key of a `YYYY-MM-DD` string back to the same day at 00:00 local. */
export function dateFromKey(key) {
  if (!key) return null
  const [y, m, d] = String(key).split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

export function startOfDay(value) {
  const date = parseDate(value) || new Date()
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

export function addDays(value, amount) {
  const date = parseDate(value) || new Date()
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + amount,
    date.getHours(), date.getMinutes(), date.getSeconds())
}

export function addMonths(value, amount) {
  const date = parseDate(value) || new Date()
  return new Date(date.getFullYear(), date.getMonth() + amount, date.getDate())
}

/** Monday 00:00 of the week containing `value`. */
export function startOfWeek(value) {
  const day = startOfDay(value)
  const offset = (day.getDay() + 6) % 7 // 0 = Monday
  return addDays(day, -offset)
}

export function startOfMonth(value) {
  const date = parseDate(value) || new Date()
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

export function sameDay(a, b) {
  const da = parseDate(a)
  const db = parseDate(b)
  if (!da || !db) return false
  return da.getFullYear() === db.getFullYear() &&
    da.getMonth() === db.getMonth() &&
    da.getDate() === db.getDate()
}

export function isToday(value) {
  return sameDay(value, new Date())
}

export function daysBetween(a, b) {
  const da = startOfDay(a)
  const db = startOfDay(b)
  if (!da || !db) return 0
  return Math.round((db.getTime() - da.getTime()) / MS_DAY)
}

/** `09:30` */
export function formatTime(value) {
  const date = parseDate(value)
  return date ? `${pad2(date.getHours())}:${pad2(date.getMinutes())}` : ''
}

/** `09:30 – 10:00` */
export function formatTimeRange(start, end) {
  const from = formatTime(start)
  const to = formatTime(end)
  return from && to ? `${from} – ${to}` : from
}

/** `February 2026` */
export function formatMonthTitle(value) {
  const date = parseDate(value)
  return date ? `${MONTHS[date.getMonth()]} ${date.getFullYear()}` : ''
}

/** `Monday, 9 February 2026` */
export function formatDayLong(value) {
  const date = parseDate(value)
  if (!date) return ''
  return `${DOW_LONG[(date.getDay() + 6) % 7]}, ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

/** `Mon 9 Feb` */
export function formatDayShort(value) {
  const date = parseDate(value)
  if (!date) return ''
  return `${DOW[(date.getDay() + 6) % 7]} ${date.getDate()} ${MONTHS[date.getMonth()].slice(0, 3)}`
}

/** UTC ISO-8601 (`...Z`), the transport format of every timestamp. */
export function toIsoDate(value) {
  const date = parseDate(value)
  return date ? date.toISOString() : ''
}

/** `YYYY-MM-DDTHH:mm` for `<input type="datetime-local">` (local wall clock). */
export function toLocalInput(value) {
  const date = parseDate(value)
  if (!date) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** Read `<input type="datetime-local">` back into a local `Date`. */
export function fromLocalInput(value) {
  if (!value) return null
  const [dayPart, timePart = '00:00'] = String(value).split('T')
  const [y, m, d] = dayPart.split('-').map(Number)
  const [hh, mm] = timePart.split(':').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d, hh || 0, mm || 0)
}

/** The viewer's IANA timezone, sent with every write so the server can render it. */
export function localTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/**
 * Query window of a view, in UTC ISO-8601.
 *
 * Month = the full 6×7 grid (the leading/trailing days of the neighbouring
 * months must render), week = 7 days from Monday, day = the cursor day, agenda
 * = 30 days from the cursor.
 *
 * @param {string} view - `month | week | day | agenda`.
 * @param {Date|string} cursor - Any date inside the period.
 * @returns {{ from: string, to: string }}
 */
export function rangeFor(view, cursor) {
  const base = startOfDay(cursor)

  if (view === 'week') {
    const start = startOfWeek(base)
    return { from: toIsoDate(start), to: toIsoDate(addDays(start, 7)) }
  }

  if (view === 'day') {
    return { from: toIsoDate(base), to: toIsoDate(addDays(base, 1)) }
  }

  if (view === 'agenda') {
    return { from: toIsoDate(base), to: toIsoDate(addDays(base, 30)) }
  }

  const gridStart = startOfWeek(startOfMonth(base))
  return { from: toIsoDate(gridStart), to: toIsoDate(addDays(gridStart, 42)) }
}

/** Label of the period shown by the toolbar for a view + cursor. */
export function formatRangeLabel(view, cursor) {
  const base = startOfDay(cursor)

  if (view === 'day') return formatDayLong(base)

  if (view === 'week') {
    const start = startOfWeek(base)
    const end = addDays(start, 6)
    return `${formatDayShort(start)} – ${formatDayShort(end)} ${end.getFullYear()}`
  }

  if (view === 'agenda') {
    return `Agenda · ${formatDayShort(base)} → ${formatDayShort(addDays(base, 29))}`
  }

  return formatMonthTitle(base)
}
