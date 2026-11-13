/**
 * Teams API module
 *
 * Covers all team-related API operations for the *active project*:
 *   - CRUD (list, get, create, update, delete)
 *   - Membership (add / remove AI profiles and human users)
 *
 * Team data is stored per project (exactly like chats) and managed by the
 * backend `TeamManager`. This module mirrors the backend router:
 *   codx/junior/api/teams.py
 */

export const teamsModule = (API) => ({
  // ── CRUD ─────────────────────────────────────────────────────────────────

  /**
   * List all teams for the active project.
   * @returns {Promise<Object[]>}
   */
  list() {
    return API.get('/api/teams')
  },

  /**
   * Fetch a single team by id.
   * @param {string} teamId
   * @returns {Promise<Object>}
   */
  get(teamId) {
    return API.get(`/api/teams/${encodeURIComponent(teamId)}`)
  },

  /**
   * Create a team. `id` is always assigned server-side (any supplied value is
   * ignored) and the team is bound to the active project. `name` is required.
   * @param {Object} team
   * @returns {Promise<Object>}
   */
  create(team) {
    return API.post('/api/teams', team)
  },

  /**
   * Update an existing team (partial update applied over the stored team).
   * @param {Object} team - Must carry a valid `id`.
   * @returns {Promise<Object>}
   */
  update(team) {
    return API.put(`/api/teams/${encodeURIComponent(team.id)}`, team)
  },

  /**
   * Delete a team by id.
   * @param {string} teamId
   * @returns {Promise<{ message: string }>}
   */
  delete(teamId) {
    return API.delete(`/api/teams/${encodeURIComponent(teamId)}`)
  },

  // ── Generation from a plain-language need ───────────────────────────────

  /**
   * Turn a need into a team **proposal** (nothing is written).
   *
   * The backend maps the sentence onto known roles (reusing the profiles that
   * already exist) and returns the `actor` block every new profile would get,
   * so an autonomous team can be previewed with its availability window.
   * @param {{ need: string, autonomous?: boolean, board?: string, schedule?: Object }} payload
   * @returns {Promise<{ mode: string, team_name: string, roles: Object[], members: Object[], summary: string }>}
   */
  suggestGeneration({ need, autonomous = false, board = '', schedule = null } = {}) {
    return API.post('/api/teams/generate/suggest', { need, autonomous, board, schedule })
  },

  /**
   * Materialize a proposal: creates the missing profiles and the team (with its
   * charter). Existing profiles are never modified.
   * @param {{ suggestion: Object, team_name?: string }} payload
   * @returns {Promise<{ team: Object, created_profiles: string[], reused_profiles: string[], errors: string[] }>}
   */
  applyGeneration({ suggestion, team_name = '' } = {}) {
    return API.post('/api/teams/generate/apply', { suggestion, team_name })
  },

  // ── Membership ─────────────────────────────────────────────────────────────

  /**
   * Add AI profiles and/or human users to a team (duplicates are ignored).
   * @param {string} teamId
   * @param {{ profiles?: string[], users?: string[] }} members
   * @returns {Promise<Object>} The updated team.
   */
  addMembers(teamId, { profiles = [], users = [] } = {}) {
    return API.post(`/api/teams/${encodeURIComponent(teamId)}/members`, {
      profiles,
      users
    })
  },

  /**
   * Remove AI profiles and/or human users from a team.
   *
   * Members are sent as comma-separated query parameters because a DELETE body
   * is not reliably forwarded by proxies/interceptors. The backend accepts a
   * body OR query params.
   * @param {string} teamId
   * @param {{ profiles?: string[], users?: string[] }} members
   * @returns {Promise<Object>} The updated team.
   */
  removeMembers(teamId, { profiles = [], users = [] } = {}) {
    const params = new URLSearchParams()
    if (profiles.length) params.append('profiles', profiles.join(','))
    if (users.length) params.append('users', users.join(','))
    const qs = params.toString()
    return API.delete(
      `/api/teams/${encodeURIComponent(teamId)}/members${qs ? `?${qs}` : ''}`
    )
  }
})