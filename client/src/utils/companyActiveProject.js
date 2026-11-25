/**
 * Company-scoped active-project resolution.
 *
 * The active project is a workspace-mode concept, but it is stored globally
 * while the company owns the projects. Switching companies must therefore
 * revalidate it: a project that belonged to the previous company is not a
 * valid active project for the new one.
 *
 * These helpers are pure so the rule can be tested without a browser. The
 * project store owns the side effects (activating/clearing the project).
 */

/** localStorage key of the last active project of one company. */
export function companyActiveProjectKey(companyId) {
  return `lastActiveProject:${companyId || 'default'}`
}

/**
 * Resolve which project should be active for a company.
 *
 * Order:
 *  1. the current active project, when the company still sees it;
 *  2. the project this company used last (per-company memory);
 *  3. the installation's last active project, when it is valid here;
 *  4. the default `codx-junior` project;
 *  5. the first linked project, then the first visible project.
 *
 * @param {Array} projects Every discovered project (full project objects).
 * @param {Object} options
 * @param {String} options.activeProjectId Currently active project id.
 * @param {Array} options.visibleProjectIds Projects the company can see.
 * @param {Array} options.linkedProjectIds Projects the company defines.
 * @param {String} options.rememberedProjectId Last project of this company.
 * @param {String} options.lastProjectId Installation-wide last project.
 * @param {String} options.defaultProjectName Preferred default project name.
 * @returns {{project: Object|null, changed: Boolean, valid: Boolean}}
 */
export function resolveCompanyActiveProject(projects, options = {}) {
  const all = (projects || []).filter(project => project?.project_id)
  const byId = new Map(all.map(project => [String(project.project_id), project]))
  const visibleIds = [...new Set(
    (options.visibleProjectIds || [])
      .map(id => String(id || ''))
      .filter(Boolean)
  )]
  const visible = new Set(visibleIds)
  const currentId = String(options.activeProjectId || '')
  const rememberedId = String(options.rememberedProjectId || '')
  const lastProjectId = String(options.lastProjectId || '')

  // A cold load (restore) prefers this company's remembered project/local
  // history over whatever project the server happened to leave active. A
  // company switch, on the other hand, keeps a project both companies share.
  if (options.preferRemembered) {
    for (const id of [rememberedId, lastProjectId]) {
      if (id && visible.has(id) && byId.has(id)) {
        return {
          project: byId.get(id),
          changed: id !== currentId,
          valid: true
        }
      }
    }
  }

  if (currentId && visible.has(currentId)) {
    const project = byId.get(currentId) || null
    if (project) return { project, changed: false, valid: true }
  }

  for (const id of [rememberedId, lastProjectId]) {
    if (id && visible.has(id) && byId.has(id)) {
      return { project: byId.get(id), changed: true, valid: true }
    }
  }

  const defaultName = String(options.defaultProjectName || 'codx-junior')
  const defaultProject = all.find(project =>
    String(project.project_name || '') === defaultName
    && visible.has(String(project.project_id))
  )
  if (defaultProject) {
    return { project: defaultProject, changed: true, valid: true }
  }

  for (const id of (options.linkedProjectIds || []).map(item => String(item))) {
    if (visible.has(id) && byId.has(id)) {
      return { project: byId.get(id), changed: true, valid: true }
    }
  }
  for (const id of visibleIds) {
    if (byId.has(id)) {
      return { project: byId.get(id), changed: true, valid: true }
    }
  }

  // The company has no project, or none of its projects is discovered.
  return {
    project: null,
    changed: Boolean(currentId) || all.length > 0,
    valid: false
  }
}
