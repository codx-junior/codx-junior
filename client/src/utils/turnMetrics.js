/**
 * Parallelism metrics of one chat turn.
 *
 * The turn timeline paints WHAT happened and the events table lists WHEN, but
 * both read the turn as a flat sequence: a turn that ran three tool calls at
 * the same time looks exactly like one that ran them one after another. That is
 * the gap this module fills — how much of the tool work actually OVERLAPPED.
 *
 * The arithmetic only uses what the runtime already persists on every tool
 * event (`started_at`, `finished_at`, `duration_ms`):
 *
 *   toolMs  Σ of the time the calls were busy   (the work)
 *   wallMs  the union of their intervals        (how long it took)
 *   ratio   toolMs / wallMs                     (the parallelism)
 *
 * `ratio` is two readings of the same number, and both are useful:
 *   · the speed-up against a strictly sequential run ("would have taken 2.5×
 *     longer one at a time"), and
 *   · the average number of calls in flight.
 * 1.0× therefore means "never more than one at a time" — which is a finding in
 * itself, not an absence of data.
 *
 * The functions are pure on purpose: the components only format them.
 */
import { toolVisual } from './toolVisuals.js'

/** Timestamps arrive as ISO strings, epoch seconds or epoch milliseconds. */
export function parseTime(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
  const asNumber = Number(value)
  if (Number.isFinite(asNumber) && String(value).trim() !== '') {
    return asNumber < 1e12 ? asNumber * 1000 : asNumber
  }
  const date = new Date(value)
  return isNaN(date.getTime()) ? null : date.getTime()
}

/**
 * Intervals of every tool call of the message, chronological.
 *
 * A call is placed by its real `started_at`; without one it is derived from
 * `finished_at - duration_ms`. A call with neither cannot be placed at all, and
 * it is reported instead of being packed after the previous one: inventing a
 * position would invent parallelism the runtime never recorded.
 *
 * @param {object} message assistant message (`tool_events`)
 * @param {number} [now] clock for the calls still running (defaults to Date.now())
 * @returns {{intervals: Array, events: Array, measured: boolean, unplaced: number}}
 */
export function toolIntervals(message, now = null) {
  const events = message?.tool_events || []
  const clock = Number(now) > 0 ? Number(now) : Date.now()
  const intervals = []
  let measured = true
  let unplaced = 0

  events.forEach((event, index) => {
    const durationMs = Number(event?.duration_ms) || 0
    const started = parseTime(event?.started_at)
    const finished = parseTime(event?.finished_at)
    let start = started
    if (start == null && finished != null) start = finished - durationMs
    if (start == null) {
      unplaced += 1
      return
    }
    if (started == null) measured = false
    const running = event?.status === 'running'
    let end
    if (running) end = Math.max(clock, start)
    else if (finished != null) end = Math.max(finished, start)
    else end = start + durationMs
    intervals.push({ index, event, start, end: Math.max(end, start), ms: Math.max(end - start, 1) })
  })

  intervals.sort((a, b) => a.start - b.start || a.index - b.index)
  return { intervals, events, measured, unplaced }
}

/**
 * How many calls were in flight over time (sweep line).
 *
 * Every slice covers `[start, end)` with a constant `count`, gaps included
 * (`count === 0`): the silence between two calls is part of the picture. At an
 * identical timestamp a call ENDING sorts before a call STARTING, so two calls
 * that merely touch are not counted as overlapping.
 *
 * @param {Array} intervals sorted intervals of {@link toolIntervals}
 * @returns {Array} `[{ start, end, count }]`
 */
export function concurrencySlices(intervals) {
  if (!intervals?.length) return []
  const points = []
  intervals.forEach(interval => {
    points.push({ at: interval.start, delta: 1 })
    points.push({ at: interval.end, delta: -1 })
  })
  points.sort((a, b) => a.at - b.at || a.delta - b.delta)

  const slices = []
  let count = 0
  let previous = points[0].at
  points.forEach(point => {
    if (point.at > previous) slices.push({ start: previous, end: point.at, count })
    count += point.delta
    previous = point.at
  })
  return slices
}

/**
 * The numbers behind the visual.
 *
 * @param {Array} intervals sorted intervals of {@link toolIntervals}
 * @param {Array} slices slices of {@link concurrencySlices}
 */
export function parallelismMetrics(intervals, slices) {
  const toolMs = intervals.reduce((sum, interval) => sum + interval.ms, 0)
  const wallMs = slices.reduce((sum, slice) => sum + (slice.count > 0 ? slice.end - slice.start : 0), 0)
  const peak = slices.reduce((max, slice) => Math.max(max, slice.count), 0)
  const ratio = wallMs > 0 ? toolMs / wallMs : 0
  return { toolMs, wallMs, peak, ratio, savedMs: Math.max(0, toolMs - wallMs) }
}

/**
 * Lane (row) of every call in the waterfall: the greedy interval colouring, so
 * two calls share a lane only when they can never overlap.
 *
 * @param {Array} intervals sorted intervals of {@link toolIntervals}
 * @returns {{bars: Array, laneCount: number}}
 */
export function assignLanes(intervals) {
  const laneEnds = []
  const bars = intervals.map(interval => {
    let lane = laneEnds.findIndex(end => end <= interval.start)
    if (lane === -1) {
      lane = laneEnds.length
      laneEnds.push(0)
    }
    laneEnds[lane] = interval.end
    return { ...interval, lane }
  })
  return { bars, laneCount: laneEnds.length }
}

/** Colour of a concurrency level. Never the only channel: every slice is titled. */
export function concurrencyClass(count) {
  if (count <= 0) return 'bg-base-content/10'
  if (count === 1) return 'bg-base-content/30'
  if (count === 2) return 'bg-primary/40'
  if (count === 3) return 'bg-primary/60'
  return 'bg-primary/85'
}

/** `2.5×` — two decimals below 10, none above (the unit is the message). */
export function formatRatio(ratio) {
  const value = Number(ratio) || 0
  if (value <= 0) return '—'
  const text = value < 10 ? value.toFixed(2).replace(/0$/, '') : value.toFixed(0)
  return text + '×'
}

/** The ratio in words: colour and the number are never the whole story. */
export function ratioWords(ratio) {
  const value = Number(ratio) || 0
  if (value <= 0) return 'no data'
  if (value < 1.05) return 'sequential'
  if (value < 1.5) return 'a little parallel'
  if (value < 2.5) return 'parallel'
  return 'highly parallel'
}

/**
 * Everything the UI needs about the turn, resolved once.
 *
 * @param {object} message assistant message
 * @param {number} [now] clock for the calls still running
 * @returns {object} metrics with `%` positions ready for the templates
 */
export function turnMetrics(message, now = null) {
  const { intervals, events, measured, unplaced } = toolIntervals(message, now)
  const { bars, laneCount } = assignLanes(intervals)
  const slices = concurrencySlices(intervals)
  const numbers = parallelismMetrics(intervals, slices)

  const start = intervals.length ? intervals[0].start : null
  const end = intervals.reduce((max, interval) => Math.max(max, interval.end), start || 0)
  const span = start != null ? Math.max(end - start, 1) : 0
  const pct = ms => (span > 0 ? (Math.max(0, ms) / span) * 100 : 0)

  return {
    ...numbers,
    tools: events.length,
    placed: intervals.length,
    unplaced,
    // False when a call had no real start: the ratio is then a lower bound
    measured: measured && !unplaced,
    laneCount,
    start,
    end,
    span,
    hasData: intervals.length > 0,
    parallel: numbers.peak > 1,
    slices: slices.map((slice, index) => ({
      key: 'slice-' + index,
      count: slice.count,
      durationMs: slice.end - slice.start,
      classes: concurrencyClass(slice.count),
      left: pct(slice.start - start) + '%',
      width: pct(slice.end - slice.start) + '%'
    })),
    bars: bars.map((bar, index) => {
      const visual = toolVisual(bar.event)
      const running = bar.event?.status === 'running'
      return {
        key: bar.event?.tool_call_id || 'bar-' + index,
        lane: bar.lane,
        tool: bar.event?.tool || 'tool',
        status: bar.event?.status || 'unknown',
        running,
        failed: bar.event?.status === 'error',
        error: bar.event?.error || '',
        durationMs: bar.ms,
        offsetMs: bar.start - start,
        visual,
        classes: running ? visual.classes + ' animate-pulse' : visual.classes,
        left: pct(bar.start - start) + '%',
        width: pct(bar.ms) + '%'
      }
    })
  }
}
