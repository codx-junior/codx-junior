/**
 * CISO API module.
 *
 * Backend of the security view: posture, background scans, findings, the
 * detector rule catalogue and the project `ciso_policy` block.
 * See `codx/junior/api/ciso.py` and `codx/junior/security/ciso/service.py`.
 *
 * Every call is project-scoped through the API instance (the connection adds
 * `codx_path`), so the view always shows the active project.
 */

export const cisoModule = (API) => ({
  /** Security posture: latest scan, trend, gates, secrets and repo hygiene. */
  overview() {
    return API.get('/api/ciso/overview')
  },

  /** Scan history, newest first. */
  scans({ limit = 20, offset = 0 } = {}) {
    const query = new URLSearchParams()
    query.set('limit', String(limit))
    query.set('offset', String(offset))
    return API.get(`/api/ciso/scans?${query.toString()}`)
  },

  /** One scan record. `includeFindings=false` keeps the payload small. */
  scan(scanId, { includeFindings = true } = {}) {
    return API.get(
      `/api/ciso/scans/${encodeURIComponent(scanId)}?include_findings=${includeFindings ? 1 : 0}`
    )
  },

  /**
   * Start a background analysis.
   *
   * @param {object} payload - { scope, paths, max_files, min_severity }
   *   `scope`: `project` | `changes` | `staged` | `paths`.
   */
  startScan(payload = {}) {
    return API.post('/api/ciso/scans', payload)
  },

  /** Request cancellation of a running scan. */
  cancelScan(scanId) {
    return API.post(`/api/ciso/scans/${encodeURIComponent(scanId)}/cancel`, {})
  },

  /** Delete a scan record (a running scan is cancelled first). */
  deleteScan(scanId) {
    return API.del(`/api/ciso/scans/${encodeURIComponent(scanId)}`)
  },

  /**
   * Filtered and paged findings of one scan.
   *
   * @param {string} scanId
   * @param {object} params - { severity, category, query, limit, offset }
   */
  findings(scanId, params = {}) {
    const query = new URLSearchParams()
    if (params.severity) query.set('severity', params.severity)
    if (params.category) query.set('category', params.category)
    if (params.query) query.set('query', params.query)
    if (params.limit) query.set('limit', String(params.limit))
    if (params.offset) query.set('offset', String(params.offset))
    const suffix = query.toString()
    return API.get(
      `/api/ciso/scans/${encodeURIComponent(scanId)}/findings${suffix ? `?${suffix}` : ''}`
    )
  },

  /** Detector catalogue with the enabled/ignored state of every rule. */
  rules({ includeDisabled = true } = {}) {
    return API.get(`/api/ciso/rules?include_disabled=${includeDisabled ? 1 : 0}`)
  },

  /** Enable or silence one rule (persisted in the project policy). */
  setRule(ruleId, enabled) {
    return API.put(`/api/ciso/rules/${encodeURIComponent(ruleId)}`, { enabled })
  },

  /** Effective policy, stored overrides, defaults and editable options. */
  policy() {
    return API.get('/api/ciso/policy')
  },

  /** Merge a partial policy into `project.json` (validated server-side). */
  updatePolicy(changes) {
    return API.put('/api/ciso/policy', changes)
  },

  /** Remove the project policy overrides. */
  resetPolicy() {
    return API.post('/api/ciso/policy/reset', {})
  },

  /**
   * Open a conversation with the `ciso` profile.
   *
   * The backend creates a real chat card seeded with the selected findings
   * (already redacted) and, by default, starts the first CISO assessment.
   *
   * @param {object} payload - { scan_id, finding_keys, question, auto_analyze }
   * @returns {Promise<{chat: object}>}
   */
  chat(payload = {}) {
    return API.post('/api/ciso/chat', payload)
  },

  /** Recent CISO gate verdicts (ciso_verdict journal entries). */
  activity({ limit = 100 } = {}) {
    return API.get(`/api/ciso/activity?limit=${limit}`)
  }
})
