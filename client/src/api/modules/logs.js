/**
 * Logs API module
 * Handles AI request/response log retrieval and management
 */

/**
 * Build a query string from logs filter params.
 * Omits undefined/null values and converts camelCase to snake_case.
 */
function _buildLogsQS(params) {
  const keyMap = {
    startDate: 'start_date',
    endDate: 'end_date',
    username: 'username',
    project: 'project',
    model: 'model',
    provider: 'provider',
    direction: 'direction',
    sessionId: 'session_id',
    page: 'page',
    pageSize: 'page_size',
  }
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${keyMap[k] || k}=${encodeURIComponent(v)}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

/**
 * Build a query string for the log explorer (repeatable params).
 *
 * Array values (source, level) are emitted as repeated params —
 * `source=a&source=b` — which is what FastAPI expects for `List[str] = Query()`.
 * Undefined/null/empty values are omitted.
 */
function _buildExplorerQS(params) {
  const parts = []
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    if (Array.isArray(value)) {
      value
        .filter((item) => item !== undefined && item !== null && item !== '')
        .forEach((item) => parts.push(`${key}=${encodeURIComponent(item)}`))
      return
    }
    parts.push(`${key}=${encodeURIComponent(value)}`)
  })
  return parts.length ? `?${parts.join('&')}` : ''
}

export const logsModule = (API) => ({
  // ── Application logs ───────────────────────────────────────────────────

  /**
   * Read a specific application log file.
   * @param {string} logName - The log file name
   * @param {number} size - Number of bytes to read
   * @returns {Promise<object>} Log file content
   */
  read(logName, size) {
    return API.get(`/api/logs/${logName}?log_size=${size}`)
  },

  /**
   * List all available application log files.
   * @returns {Promise<object>} List of log files
   */
  list() {
    return API.get('/api/logs')
  },

  // ── System logs ────────────────────────────────────────────────────────

  system: {
    /**
     * Read a specific system log file.
     * @param {string} logName - The log file name
     * @param {number} size - Number of bytes to read
     * @returns {Promise<object>} Log file content
     */
    read(logName, size) {
      return API.get(`/api/system/logs/${logName}?log_size=${size}`)
    },

    /**
     * List all available system log files.
     * @returns {Promise<object>} List of log files
     */
    list() {
      return API.get('/api/system/logs')
    },
  },

  // ── AI logs (request/response) ─────────────────────────────────────────

  ai: {
    /**
     * Get recent AI request/response log entries for the authenticated user.
     * @param {number} [limit=10] - Number of most recent logs to return (max 100)
     * @returns {Promise<object>} Object with username, recent_logs array, and count
     */
    me(limit = 10) {
      return API.get(`/api/logs/me?limit=${Math.min(limit, 100)}`)
    },

    /**
     * Get paginated list of AI request/response log entries for the authenticated user.
     * Results are sorted by timestamp descending (most recent first).
     * @param {object} opts
     * @param {string}  [opts.startDate]   - Inclusive start date YYYY-MM-DD
     * @param {string}  [opts.endDate]     - Inclusive end date YYYY-MM-DD
     * @param {string}  [opts.project]     - Filter by project name
     * @param {string}  [opts.model]       - Filter by model name
     * @param {string}  [opts.provider]    - Filter by provider name
     * @param {string}  [opts.direction]   - Filter by direction ('request' or 'response')
     * @param {string}  [opts.sessionId]   - Filter by session id
     * @param {number}  [opts.page=1]      - Page number (1-based)
     * @param {number}  [opts.pageSize=50] - Items per page (max 500)
     * @returns {Promise<object>} RawLogListResponse with paginated results
     */
    list({ startDate, endDate, project, model, provider, direction, sessionId, page = 1, pageSize = 50 } = {}) {
      const qs = _buildLogsQS({ startDate, endDate, project, model, provider, direction, sessionId, page, pageSize })
      return API.get(`/api/logs/list${qs}`)
    },

    /**
     * Get a specific log entry with full payload.
     * @param {string} logId - The synthetic log id (YYYY-MM-DD:line_index)
     * @returns {Promise<object>} Full RawLogRecord with complete payload
     */
    get(logId) {
      return API.get(`/api/logs/${encodeURIComponent(logId)}`)
    },

    // ── Admin endpoints ────────────────────────────────────────────────────

    admin: {
      /**
       * Get paginated list of AI request/response log entries across all users (admin only).
       * Results are sorted by timestamp descending (most recent first).
       * @param {object} opts
       * @param {string}  [opts.startDate]   - Inclusive start date YYYY-MM-DD
       * @param {string}  [opts.endDate]     - Inclusive end date YYYY-MM-DD
       * @param {string}  [opts.username]    - Filter by username
       * @param {string}  [opts.project]     - Filter by project name
       * @param {string}  [opts.model]       - Filter by model name
       * @param {string}  [opts.provider]    - Filter by provider name
       * @param {string}  [opts.direction]   - Filter by direction ('request' or 'response')
       * @param {string}  [opts.sessionId]   - Filter by session id
       * @param {number}  [opts.page=1]      - Page number (1-based)
       * @param {number}  [opts.pageSize=50] - Items per page (max 500)
       * @returns {Promise<object>} RawLogListResponse with paginated results
       */
      list({ startDate, endDate, username, project, model, provider, direction, sessionId, page = 1, pageSize = 50 } = {}) {
        const qs = _buildLogsQS({ startDate, endDate, username, project, model, provider, direction, sessionId, page, pageSize })
        return API.get(`/api/logs/admin/list${qs}`)
      },

      /**
       * Get any specific log entry with full payload (admin only).
       * @param {string} logId - The synthetic log id (YYYY-MM-DD:line_index)
       * @returns {Promise<object>} Full RawLogRecord with complete payload
       */
      get(logId) {
        return API.get(`/api/logs/admin/${encodeURIComponent(logId)}`)
      },

      /**
       * Delete a specific log entry (admin only).
       * @param {string} logId - The synthetic log id (YYYY-MM-DD:line_index)
       * @returns {Promise<object>} {ok: true, deleted: 1}
       */
      delete(logId) {
        return API.delete(`/api/logs/admin/${encodeURIComponent(logId)}`)
      },

      /**
       * Bulk delete log entries matching filters (admin only).
       * At least one filter must be provided to prevent accidental deletion of all logs.
       * @param {object} opts
       * @param {string}  [opts.startDate] - Start date filter YYYY-MM-DD
       * @param {string}  [opts.endDate]   - End date filter YYYY-MM-DD
       * @param {string}  [opts.username]  - Username filter
       * @param {string}  [opts.project]   - Project name filter
       * @param {string}  [opts.model]     - Model name filter
       * @param {string}  [opts.provider]  - Provider name filter
       * @returns {Promise<object>} {ok: true, deleted: <count>}
       */
      purge({ startDate, endDate, username, project, model, provider } = {}) {
        return API.post('/api/logs/admin/purge', {
          start_date: startDate || null,
          end_date: endDate || null,
          username: username || null,
          project: project || null,
          model: model || null,
          provider: provider || null,
        })
      }
    }
  },

  // ── Application log explorer (Loki/Graylog style) ──────────────────────
  //
  // Server-side exploration of the application log files
  // (`api/codx/junior/api/log_explorer.py`). The browser sends filters and
  // receives at most ONE page of entries plus the aggregations it renders,
  // never a whole log file.
  explorer: {
    /**
     * List the log files the explorer can query (newest first).
     * @returns {Promise<object>} { sources: [{ id, name, path, size_bytes, mtime, kind, entries_estimate }] }
     */
    sources() {
      return API.get('/api/logs/explorer/sources')
    },

    /**
     * Filtered, paged query. Everything (filtering, sorting, pagination,
     * histogram and facets) happens server-side.
     * @param {object} opts
     * @param {string[]} [opts.source]           - Source ids (repeatable; defaults to the newest file)
     * @param {string}   [opts.q]                - Query text (free text, "phrase", level=error,warn, logger~"re", -negation)
     * @param {number|string} [opts.start]       - Inclusive start, epoch ms or ISO-8601
     * @param {number|string} [opts.end]         - Inclusive end, epoch ms or ISO-8601
     * @param {string[]} [opts.level]            - Level filter (repeatable)
     * @param {number}   [opts.minMs]            - Keep only entries slower than this (ms)
     * @param {number}   [opts.page=1]           - Page number (1-based)
     * @param {number}   [opts.pageSize=100]     - Entries per page (backend caps at 1000)
     * @param {string}   [opts.order='desc']     - 'desc' (newest first) or 'asc'
     * @param {number}   [opts.histogramBuckets] - Number of histogram buckets (max 200)
     * @returns {Promise<object>} { entries, total, page, page_size, has_more, histogram, facets, stats }
     */
    query({ source, q, start, end, level, minMs, page, pageSize, order, histogramBuckets } = {}) {
      const qs = _buildExplorerQS({
        source,
        q,
        start,
        end,
        level,
        min_ms: minMs,
        page,
        page_size: pageSize,
        order,
        histogram_buckets: histogramBuckets,
      })
      return API.get(`/api/logs/explorer/query${qs}`)
    },

    /**
     * One entry with its verbatim raw text.
     * @param {string} id - Entry id (`<source>#<line index>`)
     * @param {string} [source] - Source id, when the id lost its source part
     * @returns {Promise<object>} Full entry including `raw`
     */
    entry(id, source) {
      const qs = _buildExplorerQS({ source })
      return API.get(`/api/logs/explorer/entry/${encodeURIComponent(id)}${qs}`)
    },

    /**
     * Entries surrounding an entry (ascending file order).
     * @param {string} id - Anchor entry id
     * @param {object} [opts]
     * @param {string} [opts.source] - Source id, when the id lost its source part
     * @param {number} [opts.before=10] - Preceding entries
     * @param {number} [opts.after=10]  - Following entries
     * @returns {Promise<object>} { entries: [...] }
     */
    context(id, { source, before = 10, after = 10 } = {}) {
      const qs = _buildExplorerQS({ source, before, after })
      return API.get(`/api/logs/explorer/context/${encodeURIComponent(id)}${qs}`)
    },

    /**
     * Poll a source for new entries.
     * @param {object} [opts]
     * @param {string} [opts.source]      - Source id (defaults to the newest file)
     * @param {string} [opts.sinceCursor] - Cursor returned by the previous poll
     * @returns {Promise<object>} { cursor, entries: [...] }
     */
    tail({ source, sinceCursor } = {}) {
      const qs = _buildExplorerQS({ source, since_cursor: sinceCursor })
      return API.get(`/api/logs/explorer/tail${qs}`)
    },

    /**
     * API-analysis aggregation over the same window and filters as `query`.
     * Returns request counts, error rate, latency percentiles, the busiest and
     * slowest endpoints, the status distribution and the profiler summary.
     * @param {object} [opts]
     * @param {string[]} [opts.source]           - Source ids (repeatable)
     * @param {string}   [opts.q]                - Query text
     * @param {number|string} [opts.start]       - Inclusive start (epoch ms or ISO-8601)
     * @param {number|string} [opts.end]         - Inclusive end (epoch ms or ISO-8601)
     * @param {string[]} [opts.level]            - Level filter (repeatable)
     * @param {number}   [opts.minMs]            - Keep only entries slower than this (ms)
     * @param {number}   [opts.top=10]           - Rows in the ranked lists (max 100)
     * @param {number}   [opts.histogramBuckets] - Number of histogram buckets
     * @returns {Promise<object>} { summary, histogram, endpoints, slowest, status, profiler, stats }
     */
    insights({ source, q, start, end, level, minMs, top, histogramBuckets } = {}) {
      const qs = _buildExplorerQS({
        source,
        q,
        start,
        end,
        level,
        min_ms: minMs,
        top,
        histogram_buckets: histogramBuckets,
      })
      return API.get(`/api/logs/explorer/insights${qs}`)
    },
  }
})