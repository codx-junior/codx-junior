/**
 * Harness API module.
 *
 * Observability and audit surface of the native codx-junior harness:
 * the append-only audit journal (`.codx/harness/audit.jsonl`) and the
 * worker/watchdog health of the project.
 *
 * Read-only: the journal is written by the orchestration runtime, the chat
 * engine and the harness watchdog, never through HTTP.
 */

export const harnessModule = (API) => ({
  /**
   * Read the harness audit journal (newest first).
   *
   * Entries register every piece of harness work: `task_requested`, run
   * lifecycle (`run_created` ... `run_completed`), step dispatches and
   * finishes, notifications and worker supervision (`worker_stalled` /
   * `worker_restarted` / `worker_recovered`).
   *
   * @param {object} [params] - { limit, offset, event, run_id, chat_id }
   *   `run_id` / `chat_id` scope the journal to one harness task.
   */
  audit(params = {}) {
    const query = new URLSearchParams()
    if (params.limit) query.set('limit', String(params.limit))
    if (params.offset) query.set('offset', String(params.offset))
    if (params.event) query.set('event', params.event)
    if (params.run_id) query.set('run_id', params.run_id)
    if (params.chat_id) query.set('chat_id', params.chat_id)
    const qs = query.toString()
    return API.get(`/api/harness/audit${qs ? `?${qs}` : ''}`)
  },

  /**
   * Harness health: worker presence, active runs and watchdog state.
   */
  status() {
    return API.get('/api/harness/status')
  },

  // ── Harness mode: sessions ───────────────────────────────────────────────
  // A harness session is a normal task chat executed by the codx-junior
  // profile in task mode. The API returns the DSH-like view model directly
  // (phase, timings, preview, worktree diff) and schedules the turn in the
  // background, so streaming and cancellation are the normal chat ones.

  /**
   * List the project's harness sessions (newest first).
   *
   * @param {object} [params] - { limit, phase }
   */
  sessions(params = {}) {
    const query = new URLSearchParams()
    if (params.limit) query.set('limit', String(params.limit))
    if (params.phase) query.set('phase', params.phase)
    const qs = query.toString()
    return API.get(`/api/harness/sessions${qs ? `?${qs}` : ''}`)
  },

  /**
   * Create a harness session from a ticket.
   *
   * @param {object} payload - { prompt, name, profiles, model, budget_cxjcoins }
   */
  createSession(payload = {}) {
    return API.post('/api/harness/sessions', payload)
  },

  /** One session: chat, metrics, worktree, changed files, run and audit. */
  session(sessionId) {
    return API.get(`/api/harness/sessions/${encodeURIComponent(sessionId)}`)
  },

  /** Append a follow-up message; the API schedules its turn. */
  promptSession(sessionId, payload = {}) {
    return API.post(
      `/api/harness/sessions/${encodeURIComponent(sessionId)}/prompt`,
      payload
    )
  }
})
