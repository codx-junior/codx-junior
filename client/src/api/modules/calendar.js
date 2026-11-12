/**
 * Calendar API module — the global Agenda / Calendar manager.
 *
 * Contract: `design/06-calendar/01-analysis.md` §5 ("Backend API").
 *
 * D1 — the calendar store is **global**: the routes are NOT scoped by the
 * active project (the connection still appends `codx_path`, the router simply
 * ignores it) and the projects are only a FILTER of the query.
 * D2 — calendars carry their own scope (`private | team | workspace | system |
 * external`) and the server resolves the ACL per request, so the client never
 * decides who can read a calendar: it renders what the responses contain.
 *
 * Every call returns promises resolved by `API.request` (connection unwraps
 * `data`), so a caller can `try/catch` a 4xx and read `error.response.data`.
 */

const enc = (value) => encodeURIComponent(value == null ? '' : value)

export const calendarModule = (API) => ({
  // ─── Calendars (§5.1) ─────────────────────────────────────────────────────

  calendars: {
    /**
     * Calendars the viewer may see (scope resolution + materialised `system` /
     * `external` entries).
     *
     * @returns {Promise<Array|Object>} `[{...}]` or `{ calendars: [...] }`.
     */
    list() {
      return API.get('/api/calendar/calendars')
    },

    /**
     * Create a calendar.
     *
     * `scope: 'workspace'` requires an admin on the server side.
     *
     * @param {object} payload `{ name, color, scope, audience?, timezone?, default_reminders? }`
     */
    create(payload) {
      return API.post('/api/calendar/calendars', payload)
    },

    /** Partial update (name, color, scope, visible, order, reminders, audience). */
    update(id, payload) {
      return API.put(`/api/calendar/calendars/${enc(id)}`, payload)
    },

    /**
     * Delete a calendar. The server answers 409 `calendar_in_use` when it still
     * holds events and `move_to` was not given, and 409 `read_only_calendar`
     * for `system` calendars.
     *
     * @param {string} id - Calendar id.
     * @param {object} [options] `{ moveTo }` — calendar receiving the events.
     */
    remove(id, { moveTo } = {}) {
      const qs = moveTo ? `?move_to=${enc(moveTo)}` : ''
      return API.delete(`/api/calendar/calendars/${enc(id)}${qs}`)
    },

    /** `{ scope, owner, audience, members[] }` for the sharing panel. */
    share(id) {
      return API.get(`/api/calendar/calendars/${enc(id)}/share`)
    }
  },

  /** Projects the viewer can put events in: `[{ project_id, name, role }]`. */
  projects() {
    return API.get('/api/calendar/projects')
  },

  // ─── Per-user preferences (§5.1) ──────────────────────────────────────────

  prefs: {
    /** Hidden calendars, order and week start of the caller. */
    read() {
      return API.get('/api/calendar/prefs')
    },

    save(prefs) {
      return API.put('/api/calendar/prefs', prefs || {})
    }
  },

  // ─── Events and occurrences (§5.2) ────────────────────────────────────────

  events: {
    /**
     * **The main read.** Recurrences are expanded server-side, so the client
     * receives occurrences ready to render.
     *
     * @param {object} query `{ calendar_ids?, from, to, projects?, q?, sources?, include_hidden? }`
     *   `projects` is OR-ed and `[]`/absent means "every project".
     * @returns {Promise<object>} `{ range, calendars, occurrences, counts, projects, warnings }`
     */
    query(query) {
      return API.post('/api/calendar/events/query', query || {})
    },

    /** Raw event, for the editor (it needs the rule, not the instances). */
    get(id) {
      return API.get(`/api/calendar/events/${enc(id)}`)
    },

    /** Create. `calendar_ids[]` is required by the server. */
    create(payload) {
      return API.post('/api/calendar/events', payload)
    },

    /**
     * Update with optimistic locking.
     *
     * The contract uses `If-Match: <revision>`; the shared connection layer
     * does not expose per-request headers, so the revision travels in the body
     * (`revision`) — the server answers 409 `revision_mismatch` either way.
     *
     * @param {string} id - Event id.
     * @param {object} payload - Event fields.
     * @param {object} [options] `{ revision }`.
     */
    update(id, payload, { revision } = {}) {
      const body = revision == null ? payload : { ...payload, revision }
      return API.put(`/api/calendar/events/${enc(id)}`, body)
    },

    /**
     * Delete (`scope=single|following|all` for a recurring event).
     *
     * @param {string} id - Event id.
     * @param {object} [options] `{ scope }`.
     */
    remove(id, { scope } = {}) {
      const qs = scope ? `?scope=${enc(scope)}` : ''
      return API.delete(`/api/calendar/events/${enc(id)}${qs}`)
    },

    /** Reschedule one reminder of an occurrence (used by the notification centre). */
    snooze(id, { minutes, occurrence_start } = {}) {
      return API.post(`/api/calendar/events/${enc(id)}/snooze`, { minutes, occurrence_start })
    },

    /**
     * Parse a natural-language draft (`"Lunch Ana tom 13:00 1h @Café"`).
     * Nothing is persisted: the editor is filled with the returned draft.
     */
    quick(text) {
      return API.post('/api/calendar/events/quick', { text })
    }
  }
})
