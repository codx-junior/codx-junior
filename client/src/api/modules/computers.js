/**
 * Computers API module.
 *
 * Computer sessions generalize browser sessions: a session is a shared machine
 * (Chromium page, Linux desktop, Windows/macOS VM or a remote host).  The
 * backend exposes the canonical routes under `/api/computers`; the legacy
 * `/api/browsers` routes remain available.
 */

export const computersModule = (API) => ({
  // ── List & Retrieval ───────────────────────────────────────────────────

  async list(filters) {
    let qs = ''
    if (filters) {
      const params = Object.entries(filters)
        .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
        .join('&')
      qs = `?${params}`
    }
    return API.get(`/api/computers${qs}`)
  },

  async get(sessionId) {
    return API.get(`/api/computers/${sessionId}`)
  },

  // ── Image library ──────────────────────────────────────────────────────

  /**
   * Browse the desktop image catalog.
   *
   * @param {Object} [filters] - provider, q, category, kind, refresh.
   */
  async images(filters = {}) {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    })
    const qs = params.toString()
    return API.get(`/api/computers/images${qs ? `?${qs}` : ''}`)
  },

  /** List selectable tags for a repository. */
  async imageTags(provider, repository, refresh = false) {
    const params = new URLSearchParams({ provider, repository })
    if (refresh) params.set('refresh', 'true')
    return API.get(`/api/computers/images/tags?${params.toString()}`)
  },

  /**
   * One feed behind the image grid.
   *
   * Merges the provider catalog (linuxserver / kasmweb / browseruse), the saved
   * computers and the custom coworking gallery into a single list of cards, so
   * the grid needs one request instead of four.
   *
   * @param {Object} [filters] - provider, q, type, tag, visibility, status,
   *   source, refresh.
   */
  async imageCatalog(filters = {}) {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    })
    const qs = params.toString()
    return API.get(`/api/computers/images/catalog${qs ? `?${qs}` : ''}`)
  },

  /** Credentials for the embedded KasmVNC live view. */
  async viewerCredentials(sessionId) {
    return API.post(`/api/computers/${sessionId}/viewer-credentials`, {})
  },

  // ── Desktop size ───────────────────────────────────────────────────────

  /**
   * Options of the desktop-size selector.
   *
   * `auto` (first entry, the default) makes the remote desktop follow the
   * viewer size; the fixed presets carry `width`/`height`, and `custom` tells
   * the UI to ask for the size manually.
   */
  async desktopSizes() {
    return API.get('/api/computers/desktop-sizes')
  },

  /**
   * Ask the machine to resize its desktop.
   *
   * `mode='client'` treats width/height as the viewer size in CSS pixels
   * (`scale` = device pixel ratio); `mode='manual'` applies an explicit size
   * (`{ resolution: '1920x1080' }` is also accepted). Sessions with auto resize
   * on answer without touching the machine.
   */
  async resizeDesktop(sessionId, payload = {}) {
    return API.post(`/api/computers/${sessionId}/resize`, payload)
  },

  // ── Saved computers ────────────────────────────────────────────────────

  /** Commit a running Linux/Kasmweb desktop into the project library. */
  async saveComputer(sessionId, payload) {
    return API.post(`/api/computers/${sessionId}/save`, payload)
  },

  /** List the project's reusable computers. */
  async savedComputers() {
    return API.get('/api/computers/library')
  },

  /** Delete a saved computer and its local Docker image. */
  async deleteSavedComputer(itemId) {
    return API.delete(`/api/computers/library/${itemId}`)
  },

  /** Authenticated preview blob for a saved computer. */
  async savedComputerPreview(itemId) {
    return API.connection.getBlob(`/api/computers/library/${itemId}/preview`)
  },

  // ── Custom coworking images (.codx/coworking/images) ──────────────────

  /**
   * Browse the team's custom coworking images.
   *
   * Visibility is resolved by the backend (`user`, `project`, `custom` project
   * list, `global`); admins see every definition.
   *
   * @param {Object} [filters] - q, visibility, tag.
   */
  async coworkingImages(filters = {}) {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    })
    const qs = params.toString()
    return API.get(`/api/computers/library/custom${qs ? `?${qs}` : ''}`)
  },

  /** Create the definition folder (manifest + Dockerfile/README scaffold). */
  async createCoworkingImage(payload) {
    return API.post('/api/computers/library/custom', payload)
  },

  /** Import a Docker Hub image or a git repository that ships a Dockerfile. */
  async importCoworkingImage(payload) {
    return API.post('/api/computers/library/custom/import', payload)
  },

  async coworkingImage(imageId) {
    return API.get(`/api/computers/library/custom/${imageId}`)
  },

  /** Change name, tags, media or visibility of an image. */
  async updateCoworkingImage(imageId, payload) {
    return API.put(`/api/computers/library/custom/${imageId}`, payload)
  },

  async deleteCoworkingImage(imageId) {
    return API.delete(`/api/computers/library/custom/${imageId}`)
  },

  /** Files of the definition folder (manifest, Dockerfile, support, media). */
  async coworkingImageFiles(imageId) {
    return API.get(`/api/computers/library/custom/${imageId}/files`)
  },

  /** Read one file of the definition folder. */
  async coworkingImageFile(imageId, path) {
    const params = new URLSearchParams({ path })
    return API.get(`/api/computers/library/custom/${imageId}/file?${params.toString()}`)
  },

  /**
   * Authenticated blob of an icon/preview of the definition folder.
   *
   * The media route sits behind the API auth, so cards cannot use it as a plain
   * `<img src>`: the blob is turned into an object URL by the caller.
   */
  async coworkingImageMedia(imageId, path) {
    const params = new URLSearchParams({ path })
    return API.connection.getBlob(`/api/computers/library/custom/${imageId}/media?${params.toString()}`)
  },

  /** Write one file of the definition folder (Dockerfile, support files...). */
  async writeCoworkingImageFile(imageId, path, content) {
    return API.put(`/api/computers/library/custom/${imageId}/file`, { path, content })
  },

  // ── CRUD & lifecycle ────────────────────────────────────────────────────

  /**
   * Create a co-desktop.
   *
   * With `background: true` the backend accepts the order and starts the
   * machine (image pull + `docker run`) behind the response: the session comes
   * back with `status: 'creating'` and the progress arrives as a
   * `computer_sessions` collection event, so the caller can keep going.
   */
  async create(payload) {
    return API.post('/api/computers', payload)
  },

  async update(sessionId, payload) {
    return API.put(`/api/computers/${sessionId}`, payload)
  },

  /**
   * Delete a co-desktop.
   *
   * With `background: true` the order is accepted and answered at once: the
   * container is stopped and the profile moved to trash by the backend, while
   * the screen keeps working.
   */
  async delete(sessionId, background = false) {
    const qs = background ? '?background=true' : ''
    return API.delete(`/api/computers/${sessionId}${qs}`)
  },

  async restart(sessionId) {
    return API.post(`/api/computers/${sessionId}/restart`, {})
  },

  async createChat(sessionId, chatId = null, createNew = false) {
    const payload = {}
    if (chatId) payload.chat_id = chatId
    if (createNew) payload.create_new = true
    return API.post(`/api/computers/${sessionId}/chat`, payload)
  },

  async stop(sessionId) {
    return API.post(`/api/computers/${sessionId}/stop`, {})
  },

  async control(sessionId, action, { chatId = null, reason = null } = {}) {
    const payload = { action }
    if (chatId) payload.chat_id = chatId
    if (reason) payload.reason = reason
    return API.post(`/api/computers/${sessionId}/control`, payload)
  },

  // ── Actions (browser page or desktop) ──────────────────────────────────

  async action(sessionId, payload) {
    return API.post(`/api/computers/${sessionId}/action`, payload)
  },

  navigate(sessionId, url) {
    return this.action(sessionId, { action: 'navigate', url })
  },

  back(sessionId) {
    return this.action(sessionId, { action: 'back' })
  },

  forward(sessionId) {
    return this.action(sessionId, { action: 'forward' })
  },

  refresh(sessionId) {
    return this.action(sessionId, { action: 'refresh' })
  },

  click(sessionId, selector) {
    return this.action(sessionId, { action: 'click', selector })
  },

  fill(sessionId, selector, value) {
    return this.action(sessionId, { action: 'fill', selector, value })
  },

  execute(sessionId, script) {
    return this.action(sessionId, { action: 'execute', script })
  },

  text(sessionId) {
    return this.action(sessionId, { action: 'text' })
  },

  links(sessionId) {
    return this.action(sessionId, { action: 'links' })
  },

  /** Desktop helpers. */
  desktop(sessionId, action, payload = {}) {
    return this.action(sessionId, { action, ...payload })
  },

  clickAt(sessionId, x, y, button = 'left', clicks = 1) {
    return this.action(sessionId, { action: 'click', x, y, button, clicks })
  },

  clickText(sessionId, text, options = {}) {
    return this.action(sessionId, { action: 'click_text', text, ...options })
  },

  typeText(sessionId, text) {
    return this.action(sessionId, { action: 'type', text })
  },

  key(sessionId, key) {
    return this.action(sessionId, { action: 'key', key })
  },

  runCommand(sessionId, command, timeout = 60) {
    return this.action(sessionId, { action: 'run_command', command, timeout })
  },

  async screenshot(sessionId, options = {}) {
    const payload = typeof options === 'boolean'
      ? { full_page: options }
      : { include_text: true, max_width: 1600, ...options }
    return API.post(`/api/computers/${sessionId}/screenshot`, payload)
  },

  /** One-time WebSocket path for the built-in noVNC viewer. */
  async vncTicket(sessionId) {
    return API.post(`/api/computers/${sessionId}/vnc-ticket`, {})
  },

  async source(sessionId) {
    return API.get(`/api/computers/${sessionId}/source`)
  },

  // ── Recording + reusable recipes ───────────────────────────────────────

  /**
   * Current recording state and the recipes for the session's site.
   *
   * The recording is stored server-side next to the activity log, so a page
   * reload (or another chat) still sees an in-flight recording.
   */
  async recording(sessionId) {
    return API.get(`/api/computers/${sessionId}/recording`)
  },

  /** Start capturing the human's clicks/typing into replayable steps. */
  async startRecording(sessionId, payload = {}) {
    return API.post(`/api/computers/${sessionId}/recording/start`, payload)
  },

  /** Stop and get the generated step preview. */
  async stopRecording(sessionId) {
    return API.post(`/api/computers/${sessionId}/recording/stop`, {})
  },

  /**
   * Save the preview (optionally edited) as a recipe.
   *
   * Passing `script_id` updates that recipe in place: the previous body is
   * archived and the version is bumped by the backend.
   */
  async saveRecording(sessionId, payload) {
    return API.post(`/api/computers/${sessionId}/recording/save`, payload)
  },

  async cancelRecording(sessionId) {
    return API.delete(`/api/computers/${sessionId}/recording`)
  },

  /**
   * Review the stopped recording with codx-junior and convert it into steps.
   *
   * `save=true` updates the targeted recipe in place (or creates one). The
   * deterministic recorder steps are the fallback when the AI is unavailable.
   */
  async analyzeRecording(sessionId, payload = {}) {
    return API.post(`/api/computers/${sessionId}/recording/analyze`, {
      save: true,
      use_ai: true,
      ...payload
    })
  },

  async scripts(sessionId, { site = null, query = null, limit = 50 } = {}) {
    const params = new URLSearchParams()
    if (site) params.set('site', site)
    if (query) params.set('query', query)
    if (limit) params.set('limit', String(limit))
    const qs = params.toString()
    return API.get(`/api/computers/${sessionId}/scripts${qs ? `?${qs}` : ''}`)
  },

  async script(sessionId, scriptId) {
    return API.get(`/api/computers/${sessionId}/scripts/${scriptId}`)
  },

  async updateScript(sessionId, scriptId, payload) {
    return API.put(`/api/computers/${sessionId}/scripts/${scriptId}`, payload)
  },

  async deleteScript(sessionId, scriptId, hard = false) {
    const qs = hard ? '?hard=true' : ''
    return API.delete(`/api/computers/${sessionId}/scripts/${scriptId}${qs}`)
  },

  /** Run the whole recipe on the shared browser (one round-trip). */
  async runScript(sessionId, scriptId, variables = {}) {
    return API.post(`/api/computers/${sessionId}/scripts/${scriptId}/run`, {
      variables,
      force: true
    })
  },

  // ── Co-driving audit + cost ────────────────────────────────────────────

  async activity(sessionId, limit = 100, { since = null, actor = null } = {}) {
    const params = new URLSearchParams({ limit: String(limit) })
    if (since) params.set('since', since)
    if (actor) params.set('actor', actor)
    return API.get(`/api/computers/${sessionId}/activity?${params.toString()}`)
  },

  async cost(sessionId) {
    return API.get(`/api/computers/${sessionId}/cost`)
  }
})