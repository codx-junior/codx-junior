/**
 * Companies API module.
 *
 * Thin client over `/api/companies/*` (see `codx/junior/api/company.py`).
 *
 * A company is the top-level entity above projects: it owns an organization
 * (departments/functions), hybrid teams of humans **and** AI profiles, the
 * linked projects and the missions being executed. The server owns validation,
 * journaling, dispatch and autonomy; this module only shapes the requests.
 *
 * Notes
 * -----
 * • Company endpoints are global (not project-scoped). The API layer still
 *   appends `codx_path` to every request, which the backend ignores for these
 *   routes.
 * • `DELETE` membership is sent as a comma separated `members` query parameter
 *   because some proxies drop DELETE bodies; the router accepts that form.
 * • No caching: every mutation is followed by a snapshot reload, and a short TTL
 *   would serve stale counters back.
 */

const BASE = '/api/companies'

/**
 * Build a query string, skipping empty values.
 * @param {Object} params
 * @returns {string} `?a=1&b=2` or `''`.
 */
function toQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

/** Join a member reference the backend understands. */
export function memberRef(member) {
  if (!member) return ''
  if (typeof member === 'string') return member
  return `${member.kind || 'profile'}:${member.name || ''}`
}

export const companiesModule = (API) => ({
  // ── Companies ────────────────────────────────────────────────────────────

  /** List every company (summary payload). */
  list({ includeArchived = false } = {}) {
    return API.get(`${BASE}${toQuery({ include_archived: includeArchived })}`)
  },

  /** Discovered projects, users and AI profiles for the pickers. */
  references({ projectId } = {}) {
    return API.get(`${BASE}/references${toQuery({ project_id: projectId })}`)
  },

  /**
   * Installation onboarding (the "New company" wizard): stored state, the
   * step catalog, the companies that can be configured and the first-run gate
   * (`setup_required`, also exposed as `needs_setup`).
   */
  onboarding() {
    return API.get(`${BASE}/onboarding`)
  },

  /**
   * Advance a step or finish the wizard (the only action that opens the
   * first-run gate), or `{ reset: true }` to run it again. Settings collected in
   * the wizard (`default_model`…) are merged server side.
   */
  saveOnboarding(payload = {}) {
    return API.post(`${BASE}/onboarding`, payload)
  },

  /** One company document. */
  get(companyId) {
    return API.get(`${BASE}/${encodeURIComponent(companyId)}`)
  },

  /**
   * Companies that can see a project. Used to resolve legacy
   * `/<project>/...` URLs to the canonical company workspace URL.
   */
  lookup(projectId) {
    return API.get(`${BASE}/lookup${toQuery({ project_id: projectId })}`)
  },

  /** Create a company (optionally from the `saas` template). */
  create(payload) {
    return API.post(BASE, payload)
  },

  /** Update company-level fields. */
  update(companyId, payload) {
    return API.put(`${BASE}/${encodeURIComponent(companyId)}`, payload)
  },

  /**
   * Upload the company logo (multipart, field `file`).
   *
   * The image is stored as a file next to the company document and the endpoint
   * answers with the updated company, whose `logo` is the URL that serves it
   * (`GET /api/companies/{id}/logo`).
   *
   * @param {string} companyId
   * @param {Blob|File} file Image bytes picked (or downscaled) by the browser.
   * @returns {Promise<Object>} The updated company.
   */
  uploadLogo(companyId, file) {
    const form = new FormData()
    form.append('file', file, file.name || 'logo.png')
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/logo`, form)
  },

  /** Remove the stored logo (file and document field). */
  removeLogo(companyId) {
    return API.delete(`${BASE}/${encodeURIComponent(companyId)}/logo`)
  },

  /**
   * Replace the company's project list in one request. This is what the
   * settings → Projects editor saves; existing project metadata is preserved.
   */
  setProjects(companyId, projectIds = []) {
    return API.put(`${BASE}/${encodeURIComponent(companyId)}/projects`, {
      project_ids: projectIds
    })
  },

  /** Delete a company and its journal. */
  remove(companyId) {
    return API.delete(`${BASE}/${encodeURIComponent(companyId)}`)
  },

  /** Dashboard payload: counts, teams, projects, missions, attention, journal. */
  snapshot(companyId, { journalLimit = 30 } = {}) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/snapshot${toQuery({ journal_limit: journalLimit })}`
    )
  },

  // ── Coworking spaces (company files + virtual project folders) ───────────

  /**
   * List the company-owned coworking spaces.
   *
   * A company that predates spaces gets its default "Company files" space on
   * the first call, so the file browser is never empty for a missing config.
   */
  spaces(companyId, { includeStats = false } = {}) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/spaces${toQuery({ include_stats: includeStats })}`
    )
  },

  /** One space with its mapped projects and folders. */
  space(companyId, spaceId) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}`
    )
  },

  /** Create a company-owned coworking space. */
  createSpace(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/spaces`, payload)
  },

  /** Update the name, mappings or presentation of a space. */
  updateSpace(companyId, spaceId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}`,
      payload
    )
  },

  /** Delete a space (its company files are moved to the company trash). */
  deleteSpace(companyId, spaceId) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}`
    )
  },

  /**
   * List one folder of the space browser.
   *
   * The root shows the company files plus the virtual `projects` and `folders`
   * roots; below a project id every folder is the project's real files
   * (read-only), and below a mapped folder id it is the host folder.
   */
  spaceFiles(companyId, spaceId, path = '') {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/files${toQuery({ path })}`
    )
  },

  /** Direct URL of one file of a space (inline preview or attachment). */
  spaceFileUrl(companyId, spaceId, path, { download = false } = {}) {
    return API.connection.prepareUrl(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/file` +
      toQuery({ path, download: download ? 'true' : '' })
    )
  },

  /** Create a folder in the company files of a space. */
  createSpaceFolder(companyId, spaceId, path, name) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/folders`,
      { path, name }
    )
  },

  /** Upload one file into the company files of a space. */
  uploadSpaceFile(companyId, spaceId, path, file) {
    const form = new FormData()
    form.append('file', file, file.name || 'file')
    form.append('path', path || '')
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/upload`,
      form
    )
  },

  /** Create or replace a text file in the company files of a space. */
  writeSpaceFile(companyId, spaceId, path, content) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/file`,
      { path, content }
    )
  },

  /** Delete a company file or an empty company folder from a space. */
  deleteSpaceEntry(companyId, spaceId, path) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/spaces/${encodeURIComponent(spaceId)}/file${toQuery({ path })}`
    )
  },

  /**
   * Cross-project CISO posture: one row per linked project (score, severity
   * counters, latest scan and gate activity).
   */
  security(companyId, { projectId = '' } = {}) {
    return API.get(`${BASE}/${encodeURIComponent(companyId)}/security${toQuery({ project_id: projectId })}`)
  },

  /** Start a CISO scan for one linked project (company admin/manager). */
  scanSecurity(companyId, { projectId, scope = 'project', maxFiles = 300 } = {}) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/security/scan`, {
      project_id: projectId,
      scope,
      max_files: maxFiles
    })
  },

  /** Autonomous actors of the company's linked projects (read-only). */
  actors(companyId) {
    return API.get(`${BASE}/${encodeURIComponent(companyId)}/actors`)
  },

  /** Audit journal (newest last). */
  journal(companyId, { limit = 100 } = {}) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/journal${toQuery({ limit })}`
    )
  },

  // ── Company chats ────────────────────────────────────────────────────────

  /** List the company channels (with their hosted chat summary). */
  listChats(companyId) {
    return API.get(`${BASE}/${encodeURIComponent(companyId)}/chats`)
  },

  /** Create a company channel hosted by a linked project. */
  createChat(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/chats`, payload)
  },

  /** Update a company channel. */
  updateChat(companyId, chatId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/chats/${encodeURIComponent(chatId)}`,
      payload
    )
  },

  /** Copy the owning team roster into the hosted chat. */
  syncChatMembers(companyId, chatId) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/chats/${encodeURIComponent(chatId)}/sync-members`,
      {}
    )
  },

  /** Delete a company channel and its hosted chat. */
  deleteChat(companyId, chatId) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/chats/${encodeURIComponent(chatId)}`
    )
  },

  // ── Organization ─────────────────────────────────────────────────────────

  addOrgUnit(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/org-units`, payload)
  },

  updateOrgUnit(companyId, unitId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/org-units/${encodeURIComponent(unitId)}`,
      payload
    )
  },

  deleteOrgUnit(companyId, unitId) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/org-units/${encodeURIComponent(unitId)}`
    )
  },

  // ── Teams ────────────────────────────────────────────────────────────────

  addTeam(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/teams`, payload)
  },

  updateTeam(companyId, teamId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}`,
      payload
    )
  },

  deleteTeam(companyId, teamId, { force = false } = {}) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}${toQuery({ force })}`
    )
  },

  resolveTeam(companyId, teamId) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}`
    )
  },

  addTeamMembers(companyId, teamId, members) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}/members`,
      { members }
    )
  },

  removeTeamMembers(companyId, teamId, members) {
    const refs = (members || []).map(memberRef).filter(Boolean).join(',')
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}/members${toQuery({ members: refs })}`
    )
  },

  setTeamLead(companyId, teamId, { kind = 'profile', name = '' } = {}) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/teams/${encodeURIComponent(teamId)}/lead`,
      { kind, name }
    )
  },

  // ── Projects ─────────────────────────────────────────────────────────────

  linkProject(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/projects`, payload)
  },

  unlinkProject(companyId, projectId, { force = false } = {}) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/projects/${encodeURIComponent(projectId)}${toQuery({ force })}`
    )
  },

  // ── Missions ─────────────────────────────────────────────────────────────

  addMission(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/missions`, payload)
  },

  updateMission(companyId, missionId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}`,
      payload
    )
  },

  deleteMission(companyId, missionId, { force = false } = {}) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}${toQuery({ force })}`
    )
  },

  dispatchMission(companyId, missionId, { force = false } = {}) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/dispatch${toQuery({ force })}`,
      {}
    )
  },

  acceptMission(companyId, missionId, result = '') {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/accept`,
      { result }
    )
  },

  rejectMission(companyId, missionId, reason = '') {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/reject`,
      { reason }
    )
  },

  approveMission(companyId, missionId, note = '') {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/approve`,
      { note }
    )
  },

  denyMission(companyId, missionId, note = '') {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/deny`,
      { note }
    )
  },

  transitionMission(companyId, missionId, status, { force = false } = {}) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/missions/${encodeURIComponent(missionId)}/transition`,
      { status, force }
    )
  },

  syncMissions(companyId) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/missions/sync`, {})
  },

  // ── Autonomy ─────────────────────────────────────────────────────────────

  updateAutonomy(companyId, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/autonomy`,
      payload
    )
  },

  runCycle(companyId, { force = true } = {}) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/cycle`,
      { force }
    )
  },

  // ── People / company users ───────────────────────────────────────────────
  // Only company admins may write these; the server enforces the role.

  members(companyId) {
    return API.get(`${BASE}/${encodeURIComponent(companyId)}/members`)
  },

  addMember(companyId, payload) {
    return API.post(`${BASE}/${encodeURIComponent(companyId)}/members`, payload)
  },

  updateMember(companyId, username, payload) {
    return API.put(
      `${BASE}/${encodeURIComponent(companyId)}/members/${encodeURIComponent(username)}`,
      payload
    )
  },

  removeMember(companyId, username) {
    return API.delete(
      `${BASE}/${encodeURIComponent(companyId)}/members/${encodeURIComponent(username)}`
    )
  },

  // ── Company assistant chat ───────────────────────────────────────────────
  // The shell opens a real ChatView on this chat; the endpoint provisions it
  // once and returns its locator (chat id + hosting project).
  assistantChat(companyId) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/assistant/chat`,
      {}
    )
  },

  // ── Company ticket workbench ─────────────────────────────────────────────
  // Tickets are project-owned chats; the company layer lists the queue of every
  // linked project, places a request on one project and reviews the delivery of
  // the unattended run. See codx/junior/api/company_tickets.py.
  tickets(companyId, params = {}) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/tickets${toQuery(params)}`
    )
  },

  ticketSummary(companyId) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/summary`
    )
  },

  ticket(companyId, ticketId) {
    return API.get(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}`
    )
  },

  createTicket(companyId, payload) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets`,
      payload
    )
  },

  commentTicket(companyId, ticketId, comment) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/comment`,
      { comment }
    )
  },

  configureTicket(companyId, ticketId, payload) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/configure`,
      payload
    )
  },

  queueTicket(companyId, ticketId) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/queue`,
      {}
    )
  },

  runTicket(companyId, ticketId) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/run`,
      {}
    )
  },

  cancelTicket(companyId, ticketId) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/cancel`,
      {}
    )
  },

  reviewTicket(companyId, ticketId, payload) {
    return API.post(
      `${BASE}/${encodeURIComponent(companyId)}/tickets/${encodeURIComponent(ticketId)}/review`,
      payload
    )
  }
})

export default companiesModule

// Made with ❤️ by codx-junior
