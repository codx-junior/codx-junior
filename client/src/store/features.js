import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'
import { API } from '../api/api'

export const namespaced = true

// Shared in-flight load so the router guard and the shell do not race.
let loadPromise = null

/**
 * Install-time feature profile.
 *
 * The image is the same for every deployment; the installer decides which
 * feature ids are exposed through `CODX_JUNIOR_ACTIVE_FEATURES` and the API
 * publishes the resolved set at `/api/features`.
 *
 * The 0.0.1 release profile is `core`: the company OS (and its first-run
 * wizard) is hidden, so the app starts in the project workspace. `all` is the
 * full development/CI product. Before the profile is known the state keeps the
 * legacy product (`company: true`, `companyWizard: true`) so an older API that
 * predates `/api/features` cannot break the company shell.
 */
export const state = () => ({
  loaded: false,
  loading: false,
  profile: '',
  version: '',
  active: [],
  hidden: [],
  company: true,
  companyWizard: true,
  manifestHash: '',
  error: null
})

export const getters = getterTree(state, {
  /**
   * Whether a feature id is exposed by this installation.
   *
   * Fails OPEN when the profile could not be read: an older API (no
   * `/api/features`) or a transient error must keep the legacy product instead
   * of hiding every gated surface.
   */
  isActive: (state) => (featureId) => {
    const id = String(featureId || '')
    if (!id) return true
    if (!state.loaded || state.error) return true
    return (state.active || []).includes(id)
  },
  /** True when the company shell should be used. */
  companyEnabled: (state) => !!state.company,
  /** True when the company first-run wizard should gate the product. */
  companyWizardEnabled: (state) => !!state.company && !!state.companyWizard,
  /** Ids that exist in the manifest but are hidden here. */
  hiddenFeatures: (state) => state.hidden || []
})

export const mutations = mutationTree(state, {
  setProfile(state, payload) {
    const data = payload || {}
    state.loaded = true
    state.loading = false
    state.error = null
    state.profile = data.profile || ''
    state.version = data.version || ''
    state.active = Array.isArray(data.active) ? data.active : []
    state.hidden = Array.isArray(data.hidden) ? data.hidden : []
    // `company` is the shell switch. If an older API omits it, fall back to
    // the historical company-first product so an upgrade never locks a user
    // out of their company.
    state.company = data.company === undefined ? true : !!data.company
    state.companyWizard = data.company_wizard === undefined
      ? state.company
      : !!data.company_wizard
    state.manifestHash = data.manifest_hash || ''
  },
  setLoading(state, value) {
    state.loading = !!value
  },
  setError(state, error) {
    state.error = error || null
  },
  setFailure(state, error) {
    state.loaded = true
    state.loading = false
    state.error = error || null
  }
})

export const actions = actionTree(
  { state, getters, mutations },
  {
    async init() {
      // The profile is loaded by the router guard / App shell before the first
      // route resolves (see features.ensureLoaded).
    },

    /**
     * Load the profile once per session.
     *
     * @param {Object} [options]
     * @param {boolean} [options.force] Reload even when already loaded.
     * @returns {Promise<Object>} The feature state.
     */
    async ensureLoaded({ state }, { force = false } = {}) {
      if (!$storex.api.user) return state
      if (state.loaded && !force) return state
      if (loadPromise && !force) return loadPromise
      mutations.setLoading(state, true)
      loadPromise = (async () => {
        try {
          const payload = await API.features.get()
          mutations.setProfile(state, payload)
        } catch (error) {
          // Fail safe to the legacy product: a missing /api/features (older
          // image) or a transient error must not hide a configured company.
          mutations.setFailure(state, error)
          console.warn('[features] cannot read /api/features; keeping company mode', error)
        } finally {
          loadPromise = null
        }
        return state
      })()
      return loadPromise
    }
  }
)
