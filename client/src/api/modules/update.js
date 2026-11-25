/**
 * ============================================================================
 * codx-junior — Self-update API module
 * File: src/api/modules/update.js
 * ----------------------------------------------------------------------------
 * BACKEND CONTRACT
 * ----------------
 * Thin HTTP client for `/api/system/update`, implemented by
 * `api/codx/junior/api/system.py` over `api/codx/junior/update.py`.
 *
 * How an update works
 * -------------------
 * `POST` pulls the target image and starts a detached helper container that
 * runs the installer's `upgrade` (`<install-home>/bin/codx-junior upgrade`) —
 * the only supported update path. The installer recreates THIS container, so
 * the API cannot answer the original request: the endpoint returns `202` with
 * the bookkeeping state and the UI polls `info()` until the new container
 * answers. The log/state live in the settings folder, so they survive the
 * restart and both containers see them.
 *
 * State statuses
 * --------------
 * `idle`        never updated (or the state file was lost)
 * `pulling`     the API is pulling the image through the Docker Engine API
 * `updating`    the helper is running the installer (the container will restart)
 * `up_to_date`  the image did not change; nothing was restarted
 * `completed`   the installer exited 0 and the new container is healthy
 * `failed`      the installer (or the pull) failed; the container is still up
 * `unknown`     the container was restarted before the helper reported
 *
 * Both routes are admin-only. A non-admin caller receives `403`; `409` means
 * "this instance cannot update itself" (`reason`/`manual_command` say what to
 * do instead) or "an update is already running"; `503` is a docker/registry
 * failure.
 */

export const updateModule = (API) => ({
  /**
   * Current build, discovered installation, support decision and last update
   * state (including a log tail). Always resolves to the full shape.
   *
   * @returns {Promise<object>} Update status payload.
   */
  info() {
    return API.get('/api/system/update')
  },

  /**
   * Pull the target image in the background and report the version it
   * contains. Nothing is recreated: safe to call whenever the tab opens.
   * Follow ``info()`` until ``check.status`` is ``done``/``failed``.
   *
   * @param {object} [options]
   * @param {string} [options.image] Target image (default: the running one).
   * @returns {Promise<object>} Initial update state.
   */
  check({ image = '' } = {}) {
    return API.post('/api/system/update/check', { image })
  },

  /**
   * Start an update (pull + recreate). Returns `202` with the initial state;
   * follow it with `info()` — the connection drops when the container is
   * recreated.
   *
   * @param {object} [options]
   * @param {string} [options.image] Target image (default: the running one).
   * @param {boolean} [options.force] Recreate even when the image is unchanged.
   * @returns {Promise<object>} Initial update state.
   */
  start({ image = '', force = false } = {}) {
    return API.post('/api/system/update', { image, force })
  },

  /**
   * Tail of the current/last update log (it survives the container restart).
   *
   * @param {number} [lines] Number of lines (10..2000).
   * @returns {Promise<{ status: string, log: string }>} Status and log text.
   */
  log(lines = 200) {
    return API.get(`/api/system/update/log?lines=${encodeURIComponent(lines)}`)
  },
})
