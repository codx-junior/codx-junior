/**
 * Actors API module
 *
 * An **actor** is a profile that works on its own: a role (`coworker` |
 * `manager`), the boards it is responsible for, a cadence, a **weekly
 * availability window** and budgets. This module mirrors the backend router
 * `codx/junior/api/autonomy.py`, which is mounted under `/api`:
 *
 *   GET    /api/actors/status                 actors + schedule + state
 *   GET    /api/actors/boards                 boards that can be watched
 *   PUT    /api/actors/{profile}              partial update of the actor block
 *   POST   /api/actors/{profile}/enable       switch the actor on
 *   POST   /api/actors/{profile}/disable      switch the actor off
 *   POST   /api/actors/{profile}/wake         take a shift now (queued)
 *   POST   /api/actors/{profile}/schedule     set the next shift instant
 *   POST   /api/actors/{profile}/run          one shift synchronously (debug)
 *
 * The API process never runs a shift inline (`run` excepted): `wake` records the
 * intent and the background worker executes it, so the request stays short.
 *
 * @example
 * const actors = await $storex.api.project(project).actors
 * await actors.save('support', {
 *   enabled: true,
 *   role: 'coworker',
 *   session: 'until_done',
 *   schedule: {
 *     enabled: true,
 *     timezone: 'Europe/Madrid',
 *     windows: [{ days: ['mon', 'tue', 'wed', 'thu', 'fri'], start: '09:00', end: '18:00' }]
 *   }
 * })
 */

/**
 * Build the base path of an actor.
 * @param {string} profileName - Profile name of the actor.
 * @returns {string} `/api/actors/{profile}`
 */
const actorPath = (profileName) =>
  `/api/actors/${encodeURIComponent((profileName || '').trim())}`

export const actorsModule = (API) => ({
  /**
   * Read every actor of the project (configuration, state, schedule, cost).
   * @returns {Promise<{actors: Object[], agents: Object[], defaults: Object}>}
   */
  status() {
    return API.get('/api/actors/status')
  },

  /**
   * Read only the actor list.
   * @returns {Promise<Object[]>}
   */
  list() {
    return API.get('/api/actors')
  },

  /**
   * Boards that can be watched by an actor (title, id, columns).
   * @returns {Promise<Object[]>}
   */
  boards() {
    return API.get('/api/actors/boards')
  },

  /**
   * Update the actor block of a profile.
   *
   * Only the provided fields change: the instructions, tools, knowledge and
   * linked profiles of the profile are preserved.
   * @param {string} profileName - Profile name.
   * @param {Object} patch - Fields to change (enabled, role, policy, session,
   *   board, boards, interval_minutes, mode, budget_cxjcoins,
   *   daily_budget_cxjcoins, max_runtime_minutes, max_turns, max_agenda,
   *   max_tasks, include_done, instructions, schedule).
   * @returns {Promise<Object>} The updated actor entry.
   */
  save(profileName, patch) {
    return API.put(actorPath(profileName), patch)
  },

  /**
   * Switch the profile into an actor.
   * @param {string} profileName
   * @returns {Promise<Object>} The full status payload.
   */
  enable(profileName) {
    return API.post(`${actorPath(profileName)}/enable`, {})
  },

  /**
   * Stop the profile from taking shifts (boards and history are kept).
   * @param {string} profileName
   * @returns {Promise<Object>} The full status payload.
   */
  disable(profileName) {
    return API.post(`${actorPath(profileName)}/disable`, {})
  },

  /**
   * Ask for a shift now (or at `when`). A manual wake ignores the schedule.
   * @param {string} profileName
   * @param {string} [when] - `+2d`, `3 hours`, an ISO date or instant.
   * @returns {Promise<{queued: boolean, actor: string, state: Object}>}
   */
  wake(profileName, when = '') {
    return API.post(`${actorPath(profileName)}/wake`, { when })
  },

  /**
   * Set the next planned shift of an actor.
   * @param {string} profileName
   * @param {string} when - Schedule expression (`+2d`, `2026-04-01`, ISO).
   * @returns {Promise<Object>}
   */
  schedule(profileName, when) {
    return API.post(
      `${actorPath(profileName)}/schedule?when=${encodeURIComponent(when)}`,
      {}
    )
  },

  /**
   * Run one shift immediately and return its report (blocking, for debugging).
   * @param {string} profileName
   * @returns {Promise<Object>}
   */
  run(profileName) {
    return API.post(`${actorPath(profileName)}/run`, {})
  }
})
