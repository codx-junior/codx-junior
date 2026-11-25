/**
 * Install-time feature profile API module.
 *
 * Thin client over `GET /api/features` (see `codx/junior/api/features.py`).
 * The payload says which feature ids this installation exposes and, in
 * particular, whether the company shell (and its first-run wizard) is on.
 *
 * The app image is the same for every profile; only the exposed surface
 * changes. The 0.0.1 installer writes `core` by default, so a fresh install
 * starts in the project workspace with the company OS hidden.
 */

const BASE = '/api/features'

export const featuresModule = (API) => ({
  /**
   * Resolved feature profile.
   *
   * @returns {Promise<{
   *   version: string,
   *   profile: 'core'|'all'|'custom',
   *   active: string[],
   *   hidden: string[],
   *   company: boolean,
   *   company_wizard: boolean,
   *   manifest_hash: string
   * }>}
   */
  get() {
    return API.get(BASE)
  }
})
