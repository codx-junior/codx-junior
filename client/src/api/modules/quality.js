/**
 * Quality guardian API module.
 *
 * Mirrors the backend router `codx/junior/api/quality.py`:
 *
 *   GET    /api/quality/state                      guardians + runs + policy
 *   POST   /api/quality/guardians/bootstrap        create/refresh guardians
 *   POST   /api/quality/guardians/{name}/adopt     adopt an existing profile
 *   POST   /api/quality/guardians/{name}/enable    enable/disable
 *   PATCH  /api/quality/guardians/{name}           edit a guardian
 *   POST   /api/quality/guardians/{name}/learn     add a durable rule
 *   POST   /api/quality/review                     run a review
 *   GET    /api/quality/runs                       history (summaries)
 *   GET    /api/quality/runs/{run_id}              one full run
 *   POST   /api/quality/fix                        fix/propose fixes
 *   POST   /api/quality/feedback                   dismiss/suppress/learn/reopen
 *   GET    /api/quality/policy                     effective policy
 *   PUT    /api/quality/policy                     update the policy
 *
 * Every call is project-scoped: the connection layer appends `codx_path`, so
 * the module must be built over the API instance of the active project.
 *
 * @example
 * const api = await $storex.api.project(project)
 * const quality = qualityModule(api)
 * const state = await quality.state()
 */

const qualityPath = (suffix = '') =>
  `/api/quality${suffix.startsWith('/') ? suffix : `/${suffix}`}`

const guardianPath = (name, suffix = '') =>
  qualityPath(
    `/guardians/${encodeURIComponent((name || '').trim())}${
      suffix ? `/${suffix}` : ''
    }`
  )

/**
 * Build the quality API bound to one project API instance.
 * @param {Object} API - Project API instance.
 * @returns {Object} Quality API.
 */
export const qualityModule = (API) => ({
  /** Full panel state: guardians, candidates, runs, policy and trigger status. */
  state({ autoBootstrap = true } = {}) {
    return API.get(qualityPath(`/state?auto_bootstrap=${autoBootstrap}`))
  },

  /** Guardian list (without the run history). */
  guardians({ includeDisabled = true, includeCandidates = true } = {}) {
    return API.get(
      qualityPath(
        `/guardians?include_disabled=${includeDisabled}&include_candidates=${includeCandidates}`
      )
    )
  },

  /** Create/refresh the guardians the detected stack needs. */
  bootstrap({ adopt = false, force = false } = {}) {
    return API.post(
      qualityPath(`/guardians/bootstrap?adopt=${adopt}&force=${force}`),
      {}
    )
  },

  /** Adopt an existing file_match profile as a guardian. */
  adopt(name, { enabled = true } = {}) {
    return API.post(guardianPath(name, `adopt?enabled=${enabled}`), {})
  },

  /** Enable/disable a guardian without touching its rules. */
  enable(name, enabled = true) {
    return API.post(guardianPath(name, 'enable'), { enabled })
  },

  /** Update a guardian (merge semantics). */
  update(name, patch = {}) {
    return API.request('PATCH', guardianPath(name), patch)
  },

  /** Add a durable rule (`enrich.md`) to a guardian. */
  learn(name, { title = '', content = '' } = {}) {
    return API.post(guardianPath(name, 'learn'), { title, content })
  },

  /** Run a quality review. */
  review({
    trigger = 'manual',
    scope = 'changed',
    mode = 'auto',
    paths = null,
    file_type = null,
    guardians = null,
    base = 'HEAD',
    change_scope = null,
    max_files = null
  } = {}) {
    return API.post(qualityPath('/review'), {
      trigger,
      scope,
      mode,
      paths,
      file_type,
      guardians,
      base,
      change_scope,
      max_files
    })
  },

  /** Newest run summaries. */
  runs(limit = 20) {
    return API.get(qualityPath(`/runs?limit=${limit}`))
  },

  /** One full run. */
  run(runId) {
    return API.get(qualityPath(`/runs/${encodeURIComponent(runId)}`))
  },

  /** Fix/propose fixes for the open findings of a run. */
  fix({
    run_id = '',
    finding_ids = null,
    paths = null,
    guardians = null,
    apply = false,
    use_ai = true
  } = {}) {
    return API.post(qualityPath('/fix'), {
      run_id,
      finding_ids,
      paths,
      guardians,
      apply,
      use_ai
    })
  },

  /** Dismiss, suppress, learn from or reopen a finding. */
  feedback({
    run_id = '',
    finding_id,
    action = 'dismiss',
    reason = '',
    title = '',
    content = '',
    scope = 'finding',
    learn = false,
    suppress = false,
    expires_in_days = null
  }) {
    return API.post(qualityPath('/feedback'), {
      run_id,
      finding_id,
      action,
      reason,
      title,
      content,
      scope,
      learn,
      suppress,
      expires_in_days
    })
  },

  /** Read the effective policy. */
  policy() {
    return API.get(qualityPath('/policy'))
  },

  /** Merge updates into the policy. */
  updatePolicy(updates = {}) {
    return API.put(qualityPath('/policy'), { updates })
  }
})

// Made with ❤️ by codx-junior
