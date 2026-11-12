/**
 * ============================================================================
 * Log Explorer — state and actions for the application-log explorer.
 * ----------------------------------------------------------------------------
 * The explorer replaces the raw log viewer (`LogViewer.vue`), which used to
 * download the whole log file as a `string[]` and render every line in the DOM.
 * Here ALL the work happens server-side: `/api/logs/explorer/query` returns at
 * most one page of entries plus the aggregations the UI needs (histogram and
 * facets), so the browser never holds — nor renders — a whole log file.
 *
 * This module owns every piece of explorer state (sources, filters, page,
 * entries, histogram, facets, stats, live tail) so the components stay
 * presentational: state in through props, intent out through emits.
 *
 * STALE RESPONSES
 * ---------------
 * Free-text typing re-runs the query on a 300 ms debounce, so several requests
 * can be on the wire at once. Every query takes a sequence number and a result
 * whose sequence is no longer the latest is DROPPED: fast typing can never
 * paint an older page over a newer one.
 *
 * LIVE TAIL
 * ---------
 * `startLiveTail()` polls `/tail` every 2 s with the opaque cursor returned by
 * the previous poll and merges the new entries into the list (deduplicated by
 * entry id). The interval is cleared by `stopLiveTail()` and automatically on
 * component unmount.
 *
 * Diagram
 * -------
 * ```mermaid
 * graph LR
 *   UI --> runQuery
 *   runQuery -->|sequence guard| API["logs/explorer/query"]
 *   UI --> startLiveTail
 *   startLiveTail -->|every 2s| tail["logs/explorer/tail"]
 * ```
 * ============================================================================
 */
import { computed, getCurrentInstance, onBeforeUnmount, ref } from 'vue'
import { API } from '@/api/api'

/** Canonical level names, most severe first (mirrors `logs/parsers.py`). */
export const LOG_LEVELS = ['critical', 'error', 'warn', 'info', 'debug', 'trace', 'unknown']

/** Tailwind background class per level (stacked histogram + legends). */
export const LEVEL_BG_CLASSES = {
  critical: 'bg-fuchsia-500',
  error: 'bg-error',
  warn: 'bg-warning',
  info: 'bg-info',
  debug: 'bg-success',
  trace: 'bg-base-content/40',
  unknown: 'bg-base-content/20',
}

/** Default entries per page (the backend caps it at 1000). */
export const DEFAULT_PAGE_SIZE = 100

/** Histogram buckets requested by default (the backend caps it at 200). */
export const DEFAULT_HISTOGRAM_BUCKETS = 24

/** Live-tail polling period, in milliseconds. */
export const LIVE_TAIL_MS = 2000

/** Debounce applied to free-text query edits, in milliseconds. */
export const QUERY_DEBOUNCE_MS = 300

/**
 * Time-range presets offered by the toolbar.
 * `ms: null` means "no lower bound" (open range / all time).
 */
export const TIME_PRESETS = [
  { id: '5m', label: '5m', ms: 5 * 60 * 1000 },
  { id: '15m', label: '15m', ms: 15 * 60 * 1000 },
  { id: '1h', label: '1h', ms: 60 * 60 * 1000 },
  { id: '6h', label: '6h', ms: 6 * 60 * 60 * 1000 },
  { id: '24h', label: '24h', ms: 24 * 60 * 60 * 1000 },
  { id: '7d', label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { id: 'custom', label: 'Custom', ms: null },
  { id: 'all', label: 'All', ms: null },
]

/** Preset selected when the explorer opens. */
export const DEFAULT_TIME_PRESET = '24h'

/**
 * Default set of filters for API analysis.
 *
 * The application log is a mix of chat, worker, profiler and HTTP traffic; a
 * raw tail is unreadable. These presets reproduce what the API dashboards show,
 * as one-click query+level+duration filters:
 *
 * - `query`   free-text term sent to the backend (`Request` matches the
 *             ``Request <url> - <ms> ms "GET /api/x HTTP/1.1" 200`` lines the
 *             parser turns into `fields.request`)
 * - `levels`  level filter
 * - `minMs`   keep only entries slower than this (backend `min_ms`)
 * - `insights` show the API analysis panel (latency/endpoints/errors)
 * - `timePreset` time range to force when the preset is applied; without it the
 *             current time range is kept (`all` = no time bound at all, so the
 *             "All logs" view really shows the newest N entries of the source)
 */
export const QUICK_FILTERS = [
  {
    id: 'api',
    label: 'API requests',
    icon: 'fa-solid fa-bolt',
    query: 'Request',
    levels: [],
    minMs: 0,
    insights: true,
  },
  {
    id: 'api-errors',
    label: 'API errors',
    icon: 'fa-solid fa-triangle-exclamation',
    query: 'Request',
    levels: ['error', 'critical'],
    minMs: 0,
    insights: true,
  },
  {
    id: 'api-slow',
    label: 'Slow API',
    icon: 'fa-solid fa-stopwatch',
    query: 'Request',
    levels: [],
    minMs: 500,
    insights: true,
  },
  {
    id: 'profiler',
    label: 'Profiler',
    icon: 'fa-solid fa-gauge-high',
    query: 'Profiler:',
    levels: [],
    minMs: 0,
    insights: true,
  },
  {
    id: 'errors',
    label: 'Errors & warnings',
    icon: 'fa-solid fa-bug',
    query: '',
    levels: ['error', 'warn', 'critical'],
    minMs: 0,
    insights: false,
  },
  {
    id: 'all',
    label: 'All logs',
    icon: 'fa-solid fa-list',
    query: '',
    levels: [],
    minMs: 0,
    insights: false,
    // "No filter, last N lines": the 24h default would silently hide older
    // entries, so this preset opens the time range up.
    timePreset: 'all',
  },
]

/** Quick filter applied when the explorer opens. */
export const DEFAULT_QUICK_FILTER = 'api'

/**
 * Split a query into terms, keeping quoted sections (and their quotes) whole.
 *
 * Mirrors the backend tokenizer (`logs/query.py::tokenize`) so the chips the
 * user clicks match exactly the terms the server compiles.
 *
 * @param {string} text - Raw query text.
 * @returns {string[]} Terms in the order they were written.
 */
export function tokenizeQuery(text) {
  const source = text || ''
  const terms = []
  let current = ''
  let inQuotes = false
  let escaped = false

  for (const char of source) {
    if (escaped) {
      current += char
      escaped = false
      continue
    }
    if (char === '\\' && inQuotes) {
      current += char
      escaped = true
      continue
    }
    if (char === '"') {
      inQuotes = !inQuotes
      current += char
      continue
    }
    if (/\s/.test(char) && !inQuotes) {
      if (current) {
        terms.push(current)
        current = ''
      }
      continue
    }
    current += char
  }
  if (current) terms.push(current)
  return terms
}

/**
 * Remove one term from a query, keeping the remaining terms untouched.
 *
 * @param {string} text - Raw query text.
 * @param {number} index - Index of the term to drop (0-based).
 * @returns {string} The new query text.
 */
export function removeQueryTerm(text, index) {
  const terms = tokenizeQuery(text)
  if (index < 0 || index >= terms.length) return text || ''
  return terms.filter((_, position) => position !== index).join(' ')
}

/**
 * Turn an unknown error into a short human-readable message.
 *
 * @param {*} err - Thrown value (axios error, Error, string, …).
 * @returns {string} Message suitable for the UI.
 */
function describeError(err) {
  return err?.response?.data?.detail || err?.message || String(err)
}

/** Empty stats block, so the toolbar always has something to render. */
function emptyStats() {
  return { scanned_lines: 0, scanned_bytes: 0, matched: 0, duration_ms: 0, truncated: false }
}

/** Build a time-range object (`preset` is a `TIME_PRESETS` id). */
function createTimeRange(preset = DEFAULT_TIME_PRESET, start = null, end = null) {
  return { preset, start, end }
}

/** Zeroed API-insights payload, so the panel always has something to render. */
function emptyInsights() {
  return {
    summary: {
      requests: 0,
      errors: 0,
      error_rate: 0,
      avg_ms: 0,
      p50_ms: 0,
      p95_ms: 0,
      p99_ms: 0,
      max_ms: 0,
    },
    histogram: [],
    endpoints: [],
    slowest: [],
    status: [],
    profiler: [],
    stats: emptyStats(),
  }
}

/**
 * Log explorer state and actions.
 *
 * @param {object} [options]
 * @param {number} [options.pageSize] - Initial page size.
 * @param {number} [options.histogramBuckets] - Initial histogram bucket count.
 * @param {string} [options.query] - Initial query text.
 * @param {string[]} [options.levels] - Initial level filter.
 * @param {string} [options.timeRangePreset] - Initial time-range preset id.
 * @returns {object} Refs and actions (see the destructuring in the explorer view).
 */
export function useLogExplorer(options = {}) {
  // ── Filters ───────────────────────────────────────────────────────────────
  const sources = ref([])
  const selectedSources = ref([])
  const query = ref(options.query || '')
  const levels = ref([...(options.levels || [])])
  const timeRange = ref(createTimeRange(options.timeRangePreset || DEFAULT_TIME_PRESET))
  const order = ref(options.order === 'asc' ? 'asc' : 'desc')
  const pageSize = ref(options.pageSize || DEFAULT_PAGE_SIZE)
  const histogramBuckets = ref(options.histogramBuckets || DEFAULT_HISTOGRAM_BUCKETS)

  // ── Default API-analysis preset ───────────────────────────────────────────
  // The explorer opens on "API requests": the first screen is API traffic with
  // its latency/error analysis, not a raw tail of every logger in the container.
  const activeQuickFilter = ref(
    options.quickFilter === undefined ? DEFAULT_QUICK_FILTER : options.quickFilter,
  )
  const initialPreset = QUICK_FILTERS.find((item) => item.id === activeQuickFilter.value) || null
  if (options.query === undefined && initialPreset) query.value = initialPreset.query || ''
  if (options.levels === undefined && initialPreset) levels.value = [...(initialPreset.levels || [])]
  const minMs = ref(
    options.minMs === undefined ? Number(initialPreset?.minMs || 0) : Number(options.minMs),
  )

  // ── Results ───────────────────────────────────────────────────────────────
  const entries = ref([])
  const total = ref(0)
  const page = ref(1)
  const hasMore = ref(false)
  const histogram = ref([])
  const facets = ref({ level: [], logger: [], module: [] })
  const stats = ref(emptyStats())

    // ── API analysis (insights) ───────────────────────────────────────────────
  const insights = ref(emptyInsights())
  const insightsLoading = ref(false)
  const insightsError = ref(null)

  // ── Transport state ───────────────────────────────────────────────────────
  const loading = ref(false)
  const error = ref(null)

  // ── Live tail ─────────────────────────────────────────────────────────────
  const liveTail = ref(false)
  const cursor = ref(null)

  let requestSeq = 0
  let insightsSeq = 0
  let tailTimer = null
  let debounceTimer = null
  let destroyed = false

  /**
   * Resolve the selected time range into explicit epoch-ms bounds.
   * Relative presets are resolved at query time so a dashboard left open keeps
   * showing "the last hour" instead of freezing on an old window.
   */
  function resolveWindow() {
    const range = timeRange.value || {}
    if (range.preset === 'custom') {
      return {
        start: range.start == null ? null : Number(range.start),
        end: range.end == null ? null : Number(range.end),
      }
    }
    const preset = TIME_PRESETS.find((item) => item.id === range.preset)
    if (!preset || !preset.ms) return { start: null, end: null }
    const now = Date.now()
    return { start: now - preset.ms, end: now }
  }

  /** Cancel a pending debounced query (an explicit action wins over typing). */
  function cancelDebounce() {
    if (debounceTimer) {
      clearTimeout(debounceTimer)
      debounceTimer = null
    }
  }

  /**
   * Load the queryable log files.
   * Keeps the current selection, dropping ids that no longer exist.
   *
   * @returns {Promise<object[]>} The available sources.
   */
  async function loadSources() {
    error.value = null
    try {
      const data = await API.logs.explorer.sources()
      const list = data?.sources || []
      sources.value = list
      const known = new Set(list.map((source) => source.id))
      selectedSources.value = selectedSources.value.filter((id) => known.has(id))
      return list
    } catch (err) {
      error.value = describeError(err)
      sources.value = []
      return []
    }
  }

  /**
   * Run the query against the backend.
   *
   * @param {object} [opts]
   * @param {boolean} [opts.reset=false] - Go back to page 1 before querying.
   * @returns {Promise<object|null>} The response, or null when it failed/was stale.
   */
  async function runQuery({ reset = false } = {}) {
    cancelDebounce()
    if (reset) page.value = 1
    const sequence = ++requestSeq
    loading.value = true
    error.value = null
    const window = resolveWindow()
    try {
      const result = await API.logs.explorer.query({
        source: selectedSources.value,
        q: query.value,
        start: window.start,
        end: window.end,
        level: levels.value,
        minMs: minMs.value || undefined,
        page: page.value,
        pageSize: pageSize.value,
        order: order.value,
        histogramBuckets: histogramBuckets.value,
      })
      // A newer query started meanwhile: this answer is stale, drop it.
      if (sequence !== requestSeq || destroyed) return null
      entries.value = result?.entries || []
      total.value = result?.total || 0
      page.value = result?.page || page.value
      hasMore.value = Boolean(result?.has_more)
      histogram.value = result?.histogram || []
      facets.value = result?.facets || { level: [], logger: [], module: [] }
      stats.value = { ...emptyStats(), ...(result?.stats || {}) }
      if (showInsights.value) loadInsights()
      return result
    } catch (err) {
      if (sequence !== requestSeq || destroyed) return null
      error.value = describeError(err)
      entries.value = []
      total.value = 0
      hasMore.value = false
      histogram.value = []
      facets.value = { level: [], logger: [], module: [] }
      stats.value = emptyStats()
      return null
    } finally {
      if (sequence === requestSeq) loading.value = false
    }
  }

  /** Go to the next page (no-op when there is none). */
  async function nextPage() {
    if (!hasMore.value || loading.value) return null
    page.value += 1
    const result = await runQuery()
    if (!result) page.value = Math.max(1, page.value - 1)
    return result
  }

  /** Go to the previous page (no-op on the first page). */
  async function prevPage() {
    if (page.value <= 1 || loading.value) return null
    page.value -= 1
    const result = await runQuery()
    if (!result) page.value += 1
    return result
  }

  /** Add/remove a level from the filter and re-run from page 1. */
  function toggleLevel(level) {
    const next = new Set(levels.value)
    if (next.has(level)) next.delete(level)
    else next.add(level)
    levels.value = [...next]
    return runQuery({ reset: true })
  }

  /** Clear the level filter. */
  function clearLevels() {
    if (!levels.value.length) return Promise.resolve(null)
    levels.value = []
    return runQuery({ reset: true })
  }

  /** Replace the time range (partial objects are merged) and re-run. */
  function setTimeRange(range = {}) {
    timeRange.value = { ...timeRange.value, ...range }
    return runQuery({ reset: true })
  }

  /** Change the sort order and re-run from page 1. */
  function setOrder(nextOrder) {
    order.value = nextOrder === 'asc' ? 'asc' : 'desc'
    return runQuery({ reset: true })
  }

  /** Change the page size and re-run from page 1. */
  function setPageSize(size) {
    const value = Number(size)
    if (!Number.isFinite(value) || value <= 0) return Promise.resolve(null)
    pageSize.value = Math.min(Math.round(value), 1000)
    return runQuery({ reset: true })
  }

  /** Width of one histogram bucket, in ms (falls back to one minute). */
  function estimateBucketMs() {
    const list = histogram.value || []
    if (list.length < 2) return 60 * 1000
    const delta = Number(list[1].ts_ms) - Number(list[0].ts_ms)
    return delta > 0 ? delta : 60 * 1000
  }

  /**
   * Zoom the time range to the bucket the user clicked in the histogram.
   *
   * @param {number} ts - Bucket start, epoch milliseconds.
   * @param {number} [bucketMs] - Bucket width; estimated from the histogram when omitted.
   */
  function zoomToBucket(ts, bucketMs) {
    const start = Number(ts)
    if (!Number.isFinite(start)) return Promise.resolve(null)
    const width = Number(bucketMs) > 0 ? Number(bucketMs) : estimateBucketMs()
    timeRange.value = createTimeRange('custom', start, start + width - 1)
    return runQuery({ reset: true })
  }

  /** Update the query text and re-run after a short debounce. */
  function setQuery(text) {
    query.value = text ?? ''
    cancelDebounce()
    debounceTimer = setTimeout(() => {
      debounceTimer = null
      if (!destroyed) runQuery({ reset: true })
    }, QUERY_DEBOUNCE_MS)
  }

  /** Add one term to the query (used by the facet chips) and re-run now. */
  function addFilterTerm(term) {
    const clean = String(term || '').trim()
    if (!clean) return Promise.resolve(null)
    query.value = `${query.value.trim()} ${clean}`.trim()
    return runQuery({ reset: true })
  }

  /** Drop one term of the query (by index) and re-run now. */
  function removeTermByIndex(index) {
    query.value = removeQueryTerm(query.value, index)
    return runQuery({ reset: true })
  }

  /** Reset the query text and re-run. */
  function clearQuery() {
    query.value = ''
    return runQuery({ reset: true })
  }

  /** Select (or deselect) one source and re-run. */
  function toggleSource(sourceId) {
    const next = new Set(selectedSources.value)
    if (next.has(sourceId)) next.delete(sourceId)
    else next.add(sourceId)
    selectedSources.value = [...next]
    return runQuery({ reset: true })
  }

  /** Replace the whole source selection and re-run. */
  function setSources(sourceIds) {
    selectedSources.value = [...(sourceIds || [])]
    return runQuery({ reset: true })
  }

  /**
   * Apply one of the `QUICK_FILTERS` presets (query + levels + min duration).
   *
   * A preset may also force its own time range (`timePreset`): "All logs" sets
   * it to `all`, otherwise the 24h default would hide everything older.
   *
   * @param {string} id - Preset id.
   * @returns {Promise<object|null>} The query response, or null.
   */
  function applyQuickFilter(id) {
    const preset = QUICK_FILTERS.find((item) => item.id === id)
    if (!preset) return Promise.resolve(null)
    activeQuickFilter.value = preset.id
    query.value = preset.query || ''
    levels.value = [...(preset.levels || [])]
    minMs.value = Number(preset.minMs || 0)
    if (preset.timePreset) timeRange.value = createTimeRange(preset.timePreset)
    cancelDebounce()
    return runQuery({ reset: true })
  }

  /**
   * Load the API-analysis aggregation for the current filters.
   *
   * Uses its own sequence guard, so a slow insights answer can never overwrite
   * a newer one (the same protection the paged query uses).
   *
   * @returns {Promise<object|null>} The insights payload, or null.
   */
  async function loadInsights() {
    if (!showInsights.value) {
      insights.value = emptyInsights()
      return null
    }
    const sequence = ++insightsSeq
    const window = resolveWindow()
    insightsLoading.value = true
    insightsError.value = null
    try {
      const result = await API.logs.explorer.insights({
        source: selectedSources.value,
        q: query.value,
        start: window.start,
        end: window.end,
        level: levels.value,
        minMs: minMs.value || undefined,
        histogramBuckets: histogramBuckets.value,
      })
      if (sequence !== insightsSeq || destroyed) return null
      insights.value = { ...emptyInsights(), ...(result || {}) }
      return result
    } catch (err) {
      if (sequence !== insightsSeq || destroyed) return null
      insightsError.value = describeError(err)
      insights.value = emptyInsights()
      return null
    } finally {
      if (sequence === insightsSeq) insightsLoading.value = false
    }
  }

  /** Clear every filter (query, levels, sources, time range) and re-run. */
  function reset() {
    activeQuickFilter.value = 'all'
    query.value = ''
    levels.value = []
    minMs.value = 0
    selectedSources.value = []
    timeRange.value = createTimeRange(DEFAULT_TIME_PRESET)
    return runQuery({ reset: true })
  }

  /** Poll `/tail` once and merge the new entries into the current list. */
  async function pollTail() {
    try {
      const result = await API.logs.explorer.tail({
        source: selectedSources.value[0] || null,
        sinceCursor: cursor.value,
      })
      if (destroyed || !result) return
      cursor.value = result.cursor == null ? cursor.value : result.cursor
      const incoming = result.entries || []
      if (!incoming.length) return
      const known = new Set(entries.value.map((entry) => entry.id))
      const fresh = incoming.filter((entry) => !known.has(entry.id))
      if (!fresh.length) return
      entries.value = order.value === 'asc'
        ? [...entries.value, ...fresh]
        : [...fresh.slice().reverse(), ...entries.value]
      total.value += fresh.length
    } catch (err) {
      if (destroyed) return
      error.value = describeError(err)
      stopLiveTail()
    }
  }

  /** Start polling the selected source for new entries (every 2 s). */
  function startLiveTail() {
    if (tailTimer) return
    liveTail.value = true
    cursor.value = null
    pollTail()
    tailTimer = setInterval(pollTail, LIVE_TAIL_MS)
  }

  /** Stop the live tail and release the interval. */
  function stopLiveTail() {
    liveTail.value = false
    if (tailTimer) {
      clearInterval(tailTimer)
      tailTimer = null
    }
  }

  /** Toggle the live tail. */
  function toggleLiveTail() {
    if (liveTail.value) stopLiveTail()
    else startLiveTail()
  }

  /**
   * Fetch one entry with its verbatim raw text.
   *
   * @param {string} id - Entry id.
   * @param {string} [source] - Source id (defaults to the first selected one).
   * @returns {Promise<object|null>} The entry, or null on failure.
   */
  async function fetchEntry(id, source) {
    try {
      return await API.logs.explorer.entry(id, source || selectedSources.value[0] || null)
    } catch (err) {
      error.value = describeError(err)
      return null
    }
  }

  /**
   * Fetch the entries surrounding one entry.
   *
   * @param {string} id - Anchor entry id.
   * @param {object} [opts] - `{ source, before, after }`.
   * @returns {Promise<object[]>} The surrounding entries (empty on failure).
   */
  async function fetchContext(id, { source = null, before = 10, after = 10 } = {}) {
    try {
      const data = await API.logs.explorer.context(id, {
        source: source || selectedSources.value[0] || null,
        before,
        after,
      })
      return data?.entries || []
    } catch (err) {
      error.value = describeError(err)
      return []
    }
  }

  /** Number of active filters (sources, levels, time range, query terms). */
  const activeFilterCount = computed(() => {
    let count = selectedSources.value.length + levels.value.length
    if (timeRange.value?.preset && timeRange.value.preset !== 'all') count += 1
    count += tokenizeQuery(query.value).length
    return count
  })

  /** True when the active quick filter renders the API analysis panel. */
  const showInsights = computed(() => {
    const preset = QUICK_FILTERS.find((item) => item.id === activeQuickFilter.value)
    return Boolean(preset && preset.insights)
  })

  const isLive = computed(() => liveTail.value)

  // The interval and the debounce must never outlive the component that owns
  // them, otherwise a background tab keeps polling.
  if (getCurrentInstance()) {
    onBeforeUnmount(() => {
      destroyed = true
      stopLiveTail()
      cancelDebounce()
    })
  }

  return {
    // Filters
    sources,
    selectedSources,
    query,
    levels,
    minMs,
    activeQuickFilter,
    timeRange,
    order,
    pageSize,
    histogramBuckets,
    // Results
    entries,
    total,
    page,
    hasMore,
    histogram,
    facets,
    stats,
    // API analysis
    insights,
    insightsLoading,
    insightsError,
    // Transport state
    loading,
    error,
    // Live tail
    liveTail,
    cursor,
    // Derived
    activeFilterCount,
    isLive,
    showInsights,
    // Actions
    loadSources,
    runQuery,
    loadInsights,
    applyQuickFilter,
    nextPage,
    prevPage,
    toggleLevel,
    clearLevels,
    setTimeRange,
    setOrder,
    setPageSize,
    zoomToBucket,
    setQuery,
    addFilterTerm,
    removeTermByIndex,
    clearQuery,
    toggleSource,
    setSources,
    reset,
    startLiveTail,
    stopLiveTail,
    toggleLiveTail,
    fetchEntry,
    fetchContext,
  }
}

export default useLogExplorer
