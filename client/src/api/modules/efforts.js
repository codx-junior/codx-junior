/**
 * Efforts API module
 *
 * An *effort* is a named agent budget (model rounds, tool calls, course
 * corrections, wall clock, validation) that profiles are linked to:
 *
 *   - the **team view** only picks a name (`profile.effort_name`) and shows its
 *     description;
 *   - **Global Settings → Efforts** edits the definition and explains what each
 *     value changes in the profile's work.
 *
 * Mirrors the backend router `codx/junior/api/efforts.py` (auto-discovered and
 * mounted under `/api`). Every call is scoped by `codx_path`, which the
 * connection layer appends automatically (`connection.prepareUrl`), so the
 * module must be built over the API instance of the project:
 *
 * @example
 * const efforts = await $storex.api.project(project)
 * const list = await efforts.efforts.list()
 *
 * Convention (same as the backend):
 *   - `POST` creates, `PUT` replaces an existing effort
 *   - a rename is `POST` + `DELETE` (the folder name must match the name)
 */

/**
 * Build the path of one effort.
 * @param {string} name - Effort name.
 * @returns {string} `/api/efforts/{name}`
 */
const effortPath = (name) =>
  `/api/efforts/${encodeURIComponent((name || '').trim())}`

/**
 * Build the efforts API bound to one API instance.
 * @param {Object} API - API instance (active project or a project-scoped one).
 * @returns {Object} Efforts API.
 */
export const effortsModule = (API) => ({
  /**
   * List the efforts of the project (the built-ins are seeded on first read).
   * @returns {Promise<Object[]>} Definitions, each with `profiles` linked to it.
   */
  list() {
    return API.get('/api/efforts')
  },

  /**
   * Documentation of every value the advanced editor can set: label, group,
   * control type, summary and the detailed `effect` on the profile's work.
   * @returns {Promise<Object>} `{ groups, fields, tiers }`
   */
  fields() {
    return API.get('/api/efforts/fields')
  },

  /**
   * Read one effort definition.
   * @param {string} name - Effort name.
   * @returns {Promise<Object>}
   */
  load(name) {
    return API.get(effortPath(name))
  },

  /**
   * Create an effort.
   * @param {Object} effort - `{ name, description, tier, values }`
   * @returns {Promise<Object>}
   */
  create(effort) {
    return API.post('/api/efforts', effort)
  },

  /**
   * Replace an existing effort. Its values are pushed to the linked profiles.
   * @param {string} name - Effort name.
   * @param {Object} effort - `{ description, tier, values }`
   * @returns {Promise<Object>}
   */
  save(name, effort) {
    return API.put(effortPath(name), effort)
  },

  /**
   * Delete an effort. The server refuses built-in efforts and any effort still
   * linked to a profile (409 with the list of profiles).
   * @param {string} name - Effort name.
   * @returns {Promise<void>}
   */
  async delete(name) {
    await API.delete(effortPath(name))
  }
})

// Made with ❤️ by codx-junior
