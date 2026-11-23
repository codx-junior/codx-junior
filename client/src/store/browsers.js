import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'
import { API } from '../api/api'
import { desktopSizeOptions } from '../utils/desktopSizes'
import { SESSION_LABELS } from '../utils/sessionLabels'
import { getParticipantId } from '../utils/collabIdentity'
import { normalizeControlState } from '../utils/sessionControl'

export const namespaced = true

/**
 * Coalescing/TTL for the session list.
 *
 * The co-working screen mounts the sidebar, the panel and (after a reload) the
 * deep-linked viewer, each of which used to fire its own `GET /api/computers`.
 * Sharing the in-flight promise and keeping the result for a moment removes
 * that burst without making the list stale (explicit refresh uses `force`).
 */
const LOAD_SESSIONS_TTL_MS = 1500
let loadSessionsPromise = null
let loadSessionsAt = 0

// Control requests already announced to the owner (one toast per request).
const notifiedControlRequests = new Set()

/** Tell the current control owner that somebody is asking for the screen. */
function notifyIncomingControlRequest(session) {
  const request = session?.control_request
  const user = $storex.users?.user || null
  if (!request || !user) return
  const participantId = getParticipantId()
  const isOwner = session.controller === 'user' && session.control_owner && (
    session.control_owner === user.username ||
    session.control_owner === participantId
  )
  if (!isOwner || notifiedControlRequests.has(request.id)) return
  notifiedControlRequests.add(request.id)
  notify('info', `${request.name || request.username || 'A participant'} requests control`)
}

/** Identity payload shared by presence and control calls. */
function participantPayload() {
  const user = $storex.users?.user || null
  return {
    participant_id: getParticipantId(),
    display_name: user?.name || user?.username || 'Guest'
  }
}

export const state = () => ({
  sessions: [],
  selectedSessionId: null,
  loading: false,
  error: null,
  editingSessionId: null,
  showNewSessionForm: false,
  // Image chosen from the standalone library browser, handed to the
  // new-session dialog that opens right after it.
  pendingLibraryImage: null,
  activityBySession: {},
  costBySession: {},
  loadingActivity: false,
  controlLoading: false,
  realtimeReady: false,
  // Options of the desktop-size selector returned by
  // `/api/computers/desktop-sizes`. `null` means "not loaded (yet)": the UI
  // then falls back to the built-in presets, so it also works against a
  // backend without that endpoint.
  desktopSizes: null,

  // ── Multi-session "grid view" ───────────────────────────────────────────
  // The screen shows either the single focused machine (`single`) or a wall
  // with several machines at once (`grid`).
  //
  // `gridSessionIds` is the explicit selection (its order is the tile order)
  // and `gridActiveId` is the tile that carries the live, interactive viewer
  // while the rest render cheap previews. `gridLayout` only decides how those
  // tiles are arranged, never which sessions are watched.
  viewMode: 'single',
  gridSessionIds: [],
  gridActiveId: null,
  gridLayout: 'grid'
})

export const getters = getterTree(state, {
  allSessions: state => state.sessions,

  selectedSession: state => {
    if (!state.selectedSessionId) return null
    return state.sessions.find(s => s.id === state.selectedSessionId)
  },

  sessionById: (state) => (id) => {
    return state.sessions.find(s => s.id === id)
  },

  activityFor: (state) => (sessionId) => {
    return state.activityBySession[sessionId] || []
  },

  costFor: (state) => (sessionId) => {
    return state.costBySession[sessionId] || null
  },

  pendingLibraryImage: state => state.pendingLibraryImage,
  isLoading: state => state.loading,
  isLoadingActivity: state => state.loadingActivity,
  isControlling: state => state.controlLoading,
  hasError: state => !!state.error,
  errorMessage: state => state.error,

  /** Selector options for the desktop size: auto, the fixed presets and custom. */
  desktopSizeOptions: state => desktopSizeOptions(state.desktopSizes),

  // ── Grid view ───────────────────────────────────────────────────────────

  isGridView: state => state.viewMode === 'grid',
  gridLayout: state => state.gridLayout,
  gridSessionIds: state => state.gridSessionIds,
  gridActiveId: state => state.gridActiveId,

  /**
   * Sessions on the wall, in the order the user added them.
   *
   * Ids that do not resolve (session deleted, another project, list not loaded
   * yet) are dropped so the grid never renders an empty tile.
   */
  gridSessions: (state) => {
    return state.gridSessionIds
      .map(id => state.sessions.find(session => session.id === id))
      .filter(Boolean)
  }
})

export const mutations = mutationTree(state, {
  setSessions(state, sessions) {
    state.sessions = sessions
  },

  addSession(state, session) {
    state.sessions.push(normalizeControlState(session))
  },

  updateSession(state, session) {
    const index = state.sessions.findIndex(s => s.id === session.id)
    if (index !== -1) {
      state.sessions[index] = normalizeControlState(session)
    }
  },

  deleteSession(state, sessionId) {
    state.sessions = state.sessions.filter(s => s.id !== sessionId)
    if (state.selectedSessionId === sessionId) {
      state.selectedSessionId = null
    }
    // A deleted machine cannot stay on the wall.
    state.gridSessionIds = state.gridSessionIds.filter(id => id !== sessionId)
    if (state.gridActiveId === sessionId) {
      state.gridActiveId = state.gridSessionIds[0] || null
    }
    delete state.activityBySession[sessionId]
    delete state.costBySession[sessionId]
  },

  selectSession(state, sessionId) {
    state.selectedSessionId = sessionId
  },

  clearSelection(state) {
    state.selectedSessionId = null
  },

  setLoading(state, loading) {
    state.loading = loading
  },

  setLoadingActivity(state, loading) {
    state.loadingActivity = loading
  },

  setError(state, error) {
    state.error = error
  },

  clearError(state) {
    state.error = null
  },

  setEditingSession(state, sessionId) {
    state.editingSessionId = sessionId
  },

  clearEditingSession(state) {
    state.editingSessionId = null
  },

  toggleNewSessionForm(state, show) {
    state.showNewSessionForm = show !== undefined ? show : !state.showNewSessionForm
  },

  setPendingLibraryImage(state, image) {
    state.pendingLibraryImage = image || null
  },

  setActivity(state, { sessionId, activity }) {
    state.activityBySession = {
      ...state.activityBySession,
      [sessionId]: activity || []
    }
  },

  appendActivity(state, { sessionId, entry }) {
    if (!sessionId || !entry) return
    const current = state.activityBySession[sessionId] || []
    // Socket.IO can deliver the same entry the REST refresh already returned.
    const key = `${entry.ts || ''}|${entry.actor || ''}|${entry.action || ''}|${entry.url || ''}`
    if (current.some(item => `${item.ts || ''}|${item.actor || ''}|${item.action || ''}|${item.url || ''}` === key)) {
      return
    }
    const next = [...current, entry]
    state.activityBySession = {
      ...state.activityBySession,
      [sessionId]: next.slice(-300)
    }
  },

  setControlLoading(state, loading) {
    state.controlLoading = loading
  },

  setRealtimeReady(state, ready) {
    state.realtimeReady = ready
  },

  setCost(state, { sessionId, cost }) {
    state.costBySession = {
      ...state.costBySession,
      [sessionId]: cost || null
    }
  },

  setDesktopSizes(state, presets) {
    state.desktopSizes = Array.isArray(presets) && presets.length ? presets : null
  },

  // ── Grid view ───────────────────────────────────────────────────────────

  setViewMode(state, mode) {
    state.viewMode = mode === 'grid' ? 'grid' : 'single'
  },

  setGridLayout(state, layout) {
    state.gridLayout = ['grid', 'spotlight', 'sidebar'].includes(layout) ? layout : 'grid'
  },

  setGridSessions(state, sessionIds) {
    state.gridSessionIds = [...new Set((sessionIds || []).filter(Boolean))]
    if (!state.gridSessionIds.includes(state.gridActiveId)) {
      state.gridActiveId = state.gridSessionIds[0] || null
    }
  },

  addGridSession(state, sessionId) {
    if (!sessionId || state.gridSessionIds.includes(sessionId)) return
    state.gridSessionIds = [...state.gridSessionIds, sessionId]
    if (!state.gridActiveId) state.gridActiveId = sessionId
  },

  removeGridSession(state, sessionId) {
    state.gridSessionIds = state.gridSessionIds.filter(id => id !== sessionId)
    if (state.gridActiveId === sessionId) {
      state.gridActiveId = state.gridSessionIds[0] || null
    }
  },

  toggleGridSession(state, sessionId) {
    if (!sessionId) return
    if (state.gridSessionIds.includes(sessionId)) {
      state.gridSessionIds = state.gridSessionIds.filter(id => id !== sessionId)
      if (state.gridActiveId === sessionId) {
        state.gridActiveId = state.gridSessionIds[0] || null
      }
      return
    }
    state.gridSessionIds = [...state.gridSessionIds, sessionId]
    if (!state.gridActiveId) state.gridActiveId = sessionId
  },

  setGridActive(state, sessionId) {
    state.gridActiveId = sessionId || null
  }
})

function notify(type, text) {
  try {
    $storex.ui.addNotification({ text, type })
  } catch (e) {
    // Notifications are best-effort (e.g. when the store boots headless)
  }
}

/**
 * Advertise a session that was just created to the active meet.
 *
 * Requirement: when the meet owner starts a browser session it must be started
 * for everyone in the room — not only for the browser tab it was created from.
 * The meet store persists the association on the meet and broadcasts it, so
 * participants that join later (or reload) land in the same session too.
 *
 * Never throws: creating a session must not fail because the meet is gone.
 */
function shareNewSessionWithMeet(session) {
  if (!session?.id) return
  try {
    const meets = $storex.meets
    const meetId = meets?.currentMeetId
    if (!meetId) return
    const meet = meets.meetForId(meetId)
    if (!meet) return
    const me = $storex.users?.user?.username || 'guest'
    // Only the meet owner auto-shares; a participant can still share manually
    // from the meet browser panel.
    if (meet.initiator && meet.initiator !== me) return
    Promise.resolve(meets.shareBrowserSession({ meetId, session })).catch((error) => {
      console.warn('[Browsers] Could not share the new session with the meet', error)
    })
  } catch (error) {
    console.warn('[Browsers] Meet sharing unavailable', error)
  }
}

// How many machines the first switch to the wall picks up on its own.
const DEFAULT_GRID_SIZE = 6

/**
 * Normalise the list response of the computer-session endpoint.
 *
 * The canonical `/api/computers` route answers `{ sessions: [...] }`, the
 * legacy `/api/browsers` route has also answered a bare array (and some
 * versions wrap it as `{ computers: [...] }`). Reading only `response.sessions`
 * turned any of those shapes into an EMPTY list — the screen showed "no
 * computer sessions" (or, with a failed request, nothing at all) while the
 * sessions existed on the server.
 */
function normalizeSessionList(response) {
  const list = Array.isArray(response)
    ? response
    : (Array.isArray(response?.sessions)
        ? response.sessions
        : (Array.isArray(response?.computers) ? response.computers : []))
  return list.map(normalizeControlState)
}

export const actions = actionTree(
  { state, getters, mutations },
  {
    /**
     * Load the shared machines.
     *
     * `/api/computers` is the canonical endpoint, but older installations only
     * expose the legacy `/api/browsers` routes. A 404 there used to end up as an
     * empty, unexplained session list (the error was stored in `state.error` and
     * nobody rendered it), which looks exactly like "the screen shows nothing".
     * The legacy route is tried before giving up, and a real failure is kept in
     * `state.error` so the list can show it with a retry.
     */
    async loadSessions({ state, commit }, options = {}) {
      const force = typeof options === 'boolean' ? options : !!options?.force
      const ttl = typeof options === 'object' && options?.ttl != null
        ? Number(options.ttl)
        : LOAD_SESSIONS_TTL_MS
      if (!force && loadSessionsPromise) return loadSessionsPromise
      if (!force && state.sessions.length && Date.now() - loadSessionsAt < ttl) {
        return state.sessions
      }
      const task = (async () => {
        commit('setLoading', true)
        commit('clearError')
        try {
          const response = await API.computers.list()
          commit('setSessions', normalizeSessionList(response))
        } catch (error) {
          try {
            const legacy = await API.browsers.list()
            commit('setSessions', normalizeSessionList(legacy))
            console.warn('[Browsers] /api/computers failed, using the legacy /api/browsers endpoint:', error?.message)
          } catch (legacyError) {
            commit('setError', error.message || 'Failed to load sessions')
            console.error('[Browsers] Error loading sessions:', error, legacyError)
          }
        } finally {
          commit('setLoading', false)
        }
        return state.sessions
      })()
      loadSessionsPromise = task
      try {
        return await task
      } finally {
        loadSessionsPromise = null
        loadSessionsAt = Date.now()
      }
    },

    /**
     * Create a browser session.
     *
     * Besides the session identity, the payload carries the **project folder
     * mapping**, decided by the new-session dialog because the folders become
     * bind mounts and cannot change while the container lives:
     *
     *  - `share_project`    mount the whole project at /home/<project_name>
     *  - `share_folders`    project-relative folders to mount instead
     *  - `readonly`         mount those folders read-only
     *  - `downloads_folder` project folder receiving the browser downloads
     *  - `desktop_auto_resize` let the desktop follow the viewer (default) or
     *    pin `desktop_width`/`desktop_height`
     */
    async createSession({ commit, dispatch }, sessionData) {
      commit('clearError')
      try {
        const payload = {
          name: sessionData.name,
          description: sessionData.description || null,
          // The order is accepted and the machine boots in background: the
          // screen keeps working while the image is pulled and the container
          // starts, and the card follows the session status.
          background: sessionData.background ?? true,
          chat_id: sessionData.chat_id || null,
          initial_url: sessionData.initial_url || sessionData.url || null,
          share_project: sessionData.share_project ?? null,
          share_folders: sessionData.share_folders || null,
          readonly: sessionData.readonly ?? null,
          downloads_folder: sessionData.downloads_folder || null,
          kind: sessionData.kind || 'browser',
          os_version: sessionData.os_version || null,
          image: sessionData.image || null,
          // The image library sends the provider and, for saved computers, the
          // library item id. Without them the backend cannot apply the KasmVNC
          // live-view profile or restore the saved computer/profile.
          image_provider: sessionData.image_provider || null,
          library_item_id: sessionData.library_item_id || null,
          cpu_cores: sessionData.cpu_cores || null,
          ram_size: sessionData.ram_size || null,
          disk_size: sessionData.disk_size || null,
          shm_size: sessionData.shm_size || null,
          extra_env: sessionData.extra_env || null,
          desktop_width: sessionData.desktop_width || null,
          desktop_height: sessionData.desktop_height || null,
          // Default on the backend too: the desktop follows the client viewport
          // (KasmVNC remote resize) instead of a fixed desktop size.
          desktop_auto_resize: sessionData.desktop_auto_resize ?? null,
          control_mode: sessionData.control_mode || null,
          vnc_host: sessionData.vnc_host || null,
          vnc_port: sessionData.vnc_port || null,
          vnc_password: sessionData.vnc_password || null,
          vnc_public_url: sessionData.vnc_public_url || null,
          ssh_host: sessionData.ssh_host || null,
          ssh_port: sessionData.ssh_port || null,
          ssh_user: sessionData.ssh_user || null,
          ssh_key_path: sessionData.ssh_key_path || null,
          ssh_password: sessionData.ssh_password || null
        }
        const session = await API.computers.create(payload)
        commit('addSession', session)
        commit('selectSession', session.id)
        commit('toggleNewSessionForm', false)
        notify('success', `${session.kind_label || "Computer"} session "${session.name}" created`)
        // A meet owner starting a browser session starts it for the whole room:
        // advertise it on the meet (the backend persists it so late joiners and
        // page reloads land in the same session).
        shareNewSessionWithMeet(session)
        // The POST only accepted the order: the machine is still booting, so
        // the card is followed until it reports a final status.
        if (session.status === 'creating') {
          dispatch('followSessionCreation', session.id)
        }
        return session
      } catch (error) {
        commit('setError', error.message || 'Failed to create session')
        notify('error', error.message || 'Failed to create session')
        throw error
      }
    },

    /**
     * Follow a co-desktop whose creation is still running in background.
     *
     * The POST only accepted the order: the record exists with status
     * "creating" and the backend starts the machine (image pull + docker run)
     * behind the request, pushing every status change as a collection event.
     * The record is polled here so the card leaves "creating" even when the
     * socket is not connected; polling stops on the first final status.
     */
    async followSessionCreation({ commit }, sessionId) {
      if (!sessionId) return null
      // Generous for an image pull on a cold host; after this the card simply
      // keeps whatever status the backend last reported.
      const deadline = Date.now() + 5 * 60 * 1000
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        let session = null
        try {
          session = await API.computers.get(sessionId)
        } catch (error) {
          console.warn('[Browsers] Could not refresh the creating session', sessionId, error)
          return null
        }
        if (!session?.id) return null
        commit('updateSession', session)
        if (session.status !== 'creating' && session.status !== 'starting') {
          if (session.status === 'error') {
            notify('error', `Could not start "${session.name}"`)
          } else {
            notify('success', `"${session.name}" is ready`)
          }
          return session
        }
      }
      return null
    },

    /**
     * Make sure a session is available locally so a panel can open it.
     *
     * Used when another participant's browser session is shared with a meet:
     * the viewer may be on a screen whose list was loaded before the session
     * existed (or may never have loaded it at all).
     */
    async ensureSession({ state, commit }, sessionOrId) {
      const sessionId = typeof sessionOrId === 'string' ? sessionOrId : sessionOrId?.id
      if (!sessionId) return null
      const existing = state.sessions.find(s => s.id === sessionId)
      if (existing) return existing
      try {
        const session = await API.computers.get(sessionId)
        if (session?.id) {
          commit('addSession', session)
          return session
        }
      } catch (error) {
        console.warn('[Browsers] Could not load shared session', sessionId, error)
      }
      return null
    },

    async updateSession({ commit }, sessionData) {
      commit('clearError')
      try {
        const payload = { name: sessionData.name }
        if (sessionData.description !== undefined) {
          payload.description = sessionData.description || null
        }
        // Advanced settings: only send fields the caller actually edited. An
        // empty size/"" image clears the override; extra_env must be an object.
        // `desktop_auto_resize` is the only one applied immediately (the
        // live-view URL carries it); the rest need a restart.
        const advancedKeys = [
          'image', 'cpu_cores', 'ram_size', 'disk_size', 'shm_size',
          'extra_env', 'desktop_width', 'desktop_height', 'desktop_auto_resize'
        ]
        advancedKeys.forEach((key) => {
          if (Object.prototype.hasOwnProperty.call(sessionData, key)) {
            payload[key] = sessionData[key]
          }
        })
        const session = await API.computers.update(sessionData.id, payload)
        commit('updateSession', session)
        commit('clearEditingSession')
        notify('success', `${session.kind_label || "Computer"} session "${session.name}" updated`)
        return session
      } catch (error) {
        commit('setError', error.message || 'Failed to update session')
        notify('error', error.message || 'Failed to update session')
        throw error
      }
    },

    /**
     * Load the desktop-size selector options (auto + presets + custom).
     *
     * The endpoint is optional: when it is missing the built-in presets are
     * used, so the settings UI never renders an empty select.
     */
    async loadDesktopSizes({ state, commit }) {
      if (Array.isArray(state.desktopSizes)) return state.desktopSizes
      try {
        const response = await API.computers.desktopSizes()
        const presets = response?.presets || []
        commit('setDesktopSizes', presets)
        return presets
      } catch (error) {
        console.warn('[Browsers] /api/computers/desktop-sizes unavailable, using the built-in sizes:', error?.message)
        commit('setDesktopSizes', null)
        return []
      }
    },

    async stopSession({ commit }, sessionId) {
      const session = await API.computers.stop(sessionId)
      commit('updateSession', session)
      notify('success', SESSION_LABELS.noticeStopped)
      return session
    },

    async restartSession({ commit }, sessionId) {
      const session = await API.computers.restart(sessionId)
      commit('updateSession', session)
      notify('success', SESSION_LABELS.noticeRestarted)
      return session
    },

    async deleteSession({ commit }, sessionId) {
      commit('clearError')
      try {
        // Fire and forget: the backend stops the container and moves the
        // profile to trash behind the response, so the list is usable at once.
        await API.computers.delete(sessionId, true)
        commit('deleteSession', sessionId)
        notify('success', SESSION_LABELS.noticeDeleted)
      } catch (error) {
        commit('setError', error.message || 'Failed to delete session')
        notify('error', error.message || 'Failed to delete session')
        throw error
      }
    },

    selectSession({ commit }, sessionId) {
      commit('selectSession', sessionId)
    },

    clearSelection({ commit }) {
      commit('clearSelection')
    },

    /**
     * Switch the stage between one machine (`single`) and the wall (`grid`).
     *
     * The first switch to the wall seeds the selection with the machine being
     * watched plus the running ones, so the grid never opens completely empty.
     * `{ mode, seed: false }` restores a stored state without seeding it.
     */
    setViewMode({ state, commit }, payload) {
      const options = typeof payload === 'string' ? { mode: payload } : (payload || {})
      const mode = options.mode === 'grid' ? 'grid' : 'single'
      if (mode === 'grid' && options.seed !== false && !state.gridSessionIds.length) {
        const running = state.sessions
          .filter(session => session.status === 'running')
          .slice(0, options.seedLimit || DEFAULT_GRID_SIZE)
          .map(session => session.id)
        const ids = state.selectedSessionId
          ? [state.selectedSessionId, ...running]
          : running
        commit('setGridSessions', [...new Set(ids)])
      }
      commit('setViewMode', mode)
    },

    setGridLayout({ commit }, layout) {
      commit('setGridLayout', layout)
    },

    setGridSessions({ commit }, sessionIds) {
      commit('setGridSessions', sessionIds)
    },

    addGridSession({ commit }, sessionId) {
      commit('addGridSession', sessionId)
    },

    removeGridSession({ commit }, sessionId) {
      commit('removeGridSession', sessionId)
    },

    toggleGridSession({ commit }, sessionId) {
      commit('toggleGridSession', sessionId)
    },

    setGridActive({ commit }, sessionId) {
      commit('setGridActive', sessionId)
    },

    /** Leave the wall and show one machine full screen again. */
    openSingleSession({ commit }, sessionId) {
      if (sessionId) {
        commit('selectSession', sessionId)
        commit('setGridActive', sessionId)
      }
      commit('setViewMode', 'single')
    },

    toggleNewSessionForm({ commit }, show) {
      commit('toggleNewSessionForm', show)
      if (show) {
        commit('clearEditingSession')
      }
    },

    /** Open the new-session dialog with an image already chosen. */
    openNewSessionFromLibrary({ commit }, image) {
      commit('setPendingLibraryImage', image || null)
      commit('toggleNewSessionForm', true)
      commit('clearEditingSession')
    },

    /** The create dialog consumed the pending library image. */
    clearPendingLibraryImage({ commit }) {
      commit('setPendingLibraryImage', null)
    },

    startEditingSession({ commit }, sessionId) {
      commit('setEditingSession', sessionId)
    },

    stopEditingSession({ commit }) {
      commit('clearEditingSession')
    },

    async runSession({ commit }, sessionId) {
      try {
        const session = await API.computers.get(sessionId)
        if (!session) throw new Error('Session not found')
        if (!session.vnc_path && session.vnc_url) {
          // Best-effort same-origin path for older persisted sessions.
          try {
            session.vnc_path = new URL(session.vnc_url, window.location.origin).pathname
          } catch (e) {
            session.vnc_path = session.vnc_url
          }
        }

        // Central navigation knows whether we are in desktop mode (dockview
        // panel) or single/mobile mode (route) and does the right thing.
        $storex.$navigation?.browsers?.open(sessionId, session.name)
        return session
      } catch (error) {
        commit('setError', error.message || 'Failed to run session')
        notify('error', error.message || 'Failed to run session')
        throw error
      }
    },

    async loadActivity({ commit }, { sessionId, limit = 100, since = null, actor = null } = {}) {
      if (!sessionId) return []
      commit('setLoadingActivity', true)
      try {
        const response = await API.computers.activity(sessionId, limit, { since, actor })
        commit('setActivity', { sessionId, activity: response?.activity || [] })
        return response?.activity || []
      } catch (error) {
        console.error('[Browsers] Error loading activity:', error)
        return []
      } finally {
        commit('setLoadingActivity', false)
      }
    },

    async loadCost({ commit }, sessionId) {
      if (!sessionId) return null
      try {
        const cost = await API.computers.cost(sessionId)
        commit('setCost', { sessionId, cost })
        return cost
      } catch (error) {
        console.error('[Browsers] Error loading cost:', error)
        return null
      }
    },

    /**
     * Subscribe once to the browser co-browsing events.
     *
     * Activity and control changes are pushed by the backend, so the live view
     * no longer polls every 7 seconds.  The REST refresh remains the fallback
     * when the socket is not connected.
     */
    initRealtime({ state, commit, dispatch }) {
      if (state.realtimeReady) return
      const socket = $storex.api?.socket
      if (!socket) return
      commit('setRealtimeReady', true)
      socket.on('codx-junior-browser-activity', (payload) => {
        const sessionId = payload?.session_id
        if (!sessionId || !payload?.entry) return
        commit('appendActivity', { sessionId, entry: payload.entry })
        const entry = payload.entry
        const session = state.sessions.find(item => item.id === sessionId)
        if (session) {
          if (entry.url) session.current_url = entry.url
          if (entry.title) session.current_title = entry.title
        }
      })
      socket.on('codx-junior-browser-control', (payload) => {
        if (payload?.session) {
          commit('updateSession', payload.session)
          notifyIncomingControlRequest(payload.session)
        } else if (payload?.session_id) {
          const session = state.sessions.find(item => item.id === payload.session_id)
          if (session) {
            session.controller = payload.controller
            session.control_mode = payload.control_mode
            session.control_owner = payload.control_owner
            session.control_owner_name = payload.control_owner_name
            session.control_request = payload.control_request
            session.handoff_reason = payload.handoff_reason
            session.user_active_until = payload.user_active_until
          }
        }
      })
      socket.on('codx-junior-browser-participants', (payload) => {
        if (payload?.session) {
          commit('updateSession', payload.session)
          notifyIncomingControlRequest(payload.session)
          return
        }
        if (!payload?.session_id) return
        const session = state.sessions.find(item => item.id === payload.session_id)
        if (!session) return
        session.participants = payload.participants || []
        session.control_mode = payload.control_mode
        session.control_owner = payload.control_owner
        session.control_owner_name = payload.control_owner_name
        session.control_request = payload.control_request
      })
    },

    /**
     * Join a shared session and keep its presence fresh.
     *
     * Safe to call more than once: the backend upserts the participant. The
     * interval lives in the caller so switching sessions can leave the old one.
     */
    async joinSession({ commit }, { sessionId, controlMode = null } = {}) {
      if (!sessionId) return null
      try {
        const response = await API.computers.join(sessionId, {
          ...participantPayload(),
          ...(controlMode ? { control_mode: controlMode } : {})
        })
        if (response?.id) commit('updateSession', response)
        return response
      } catch (error) {
        console.warn('[Browsers] Could not join session presence:', sessionId, error?.message)
        return null
      }
    },

    /** Remove this tab from a session's participant list. */
    async leaveSession({ commit }, sessionId) {
      if (!sessionId) return null
      try {
        const response = await API.computers.leave(sessionId, { ...participantPayload() })
        if (response?.id) commit('updateSession', response)
        return response
      } catch (error) {
        console.warn('[Browsers] Could not leave session presence:', sessionId, error?.message)
        return null
      }
    },

    /**
     * Ask for the control lease.
     *
     * The backend decides: in `auto` mode (or while the AI drives) it is
     * granted immediately; in `request` mode it creates a pending request the
     * current owner must approve.
     */
    async takeControl({ commit }, { sessionId, reason = 'User took control from the live view' } = {}) {
      if (!sessionId) return null
      commit('setControlLoading', true)
      try {
        const response = await API.computers.control(sessionId, 'take_control', {
          ...participantPayload(),
          reason
        })
        if (response?.session) commit('updateSession', response.session)
        const pending = !!response?.session?.control_request
        notify(pending ? 'info' : 'success', response?.message || (pending ? 'Control request sent' : 'You have control'))
        return response
      } catch (error) {
        notify('error', error.message || 'Could not take control')
        throw error
      } finally {
        commit('setControlLoading', false)
      }
    },

    /** Explicit alias for request mode. */
    async requestControl({ dispatch }, { sessionId, reason } = {}) {
      return dispatch('takeControl', {
        sessionId,
        reason: reason || 'User requested control from the live view'
      })
    },

    /** Give the human lease back to the AI. */
    async giveBackControl({ commit }, sessionId) {
      if (!sessionId) return null
      commit('setControlLoading', true)
      try {
        const response = await API.computers.control(sessionId, 'give_back', {
          ...participantPayload()
        })
        if (response?.session) commit('updateSession', response.session)
        notify('success', response?.message || 'Control returned to the AI')
        return response
      } catch (error) {
        notify('error', error.message || 'Could not return control')
        throw error
      } finally {
        commit('setControlLoading', false)
      }
    },

    /** Approve the pending request (control owner or admin only). */
    async approveControlRequest({ commit }, { sessionId, requestId = null } = {}) {
      if (!sessionId) return null
      commit('setControlLoading', true)
      try {
        const response = await API.computers.control(sessionId, 'approve_request', {
          ...participantPayload(),
          request_id: requestId
        })
        if (response?.session) commit('updateSession', response.session)
        notify('success', response?.message || 'Control request approved')
        return response
      } catch (error) {
        notify('error', error.message || 'Could not approve the request')
        throw error
      } finally {
        commit('setControlLoading', false)
      }
    },

    /** Deny the pending request, keeping the current lease. */
    async denyControlRequest({ commit }, { sessionId, requestId = null } = {}) {
      if (!sessionId) return null
      commit('setControlLoading', true)
      try {
        const response = await API.computers.control(sessionId, 'deny_request', {
          ...participantPayload(),
          request_id: requestId
        })
        if (response?.session) commit('updateSession', response.session)
        notify('info', response?.message || 'Control request denied')
        return response
      } catch (error) {
        notify('error', error.message || 'Could not deny the request')
        throw error
      } finally {
        commit('setControlLoading', false)
      }
    },

    /** Change how humans share control (`auto` / `request`). */
    async setControlMode({ commit }, { sessionId, mode } = {}) {
      if (!sessionId || !mode) return null
      commit('setControlLoading', true)
      try {
        const response = await API.computers.control(sessionId, 'set_mode', {
          ...participantPayload(),
          mode
        })
        if (response?.session) commit('updateSession', response.session)
        notify('success', response?.message || `Control mode: ${mode}`)
        return response
      } catch (error) {
        notify('error', error.message || 'Could not change the control mode')
        throw error
      } finally {
        commit('setControlLoading', false)
      }
    },

    async requestHandoff({ commit }, { sessionId, reason }) {
      if (!sessionId) return null
      const response = await API.computers.control(sessionId, 'handoff', { reason })
      if (response?.session) commit('updateSession', response.session)
      return response
    },

    /**
     * Get (or create) the chat linked to a browser session.
     *
     * The endpoint reuses the session's chat when it already has one, otherwise
     * creates it and activates the `browser` profile so the AI can drive the
     * shared page from the browser-sessions view.
     */
    async ensureChat({ commit }, { sessionId, chatId = null, createNew = false } = {}) {
      if (!sessionId) return null
      const response = await API.computers.createChat(sessionId, chatId, createNew)
      if (response?.session) {
        commit('updateSession', response.session)
      }
      const chat = response?.chat || null
      if (chat?.id) {
        await $storex.chats.loadChat(chat)
      }
      return chat
    },

    async navigate({ dispatch }, { sessionId, url }) {
      const result = await API.computers.navigate(sessionId, url)
      await dispatch('loadActivity', { sessionId, limit: 50 })
      return result
    },

    async pageAction({ dispatch }, { sessionId, action, payload = {} }) {
      const result = await API.computers.action(sessionId, { action, ...payload })
      await dispatch('loadActivity', { sessionId, limit: 50 })
      return result
    },

    /**
     * Return a compact context of the current page for "Ask AI about this page".
     *
     * The selection is what the human highlighted in the live view; the active
     * element tells the AI which field the human was editing.
     */
    async pageContext({ state }, sessionId) {
      if (!sessionId) return null
      const session = state.sessions.find(item => item.id === sessionId)
      if (session && session.kind && session.kind !== 'browser') {
        // Desktops have no DOM/snapshot: send the OCR text plus the focused
        // window title so "Ask AI" has the same context a page would give.
        const response = await API.computers.screenshot(sessionId, { include_text: true, max_width: 1280 })
        return {
          url: null,
          title: session.current_title || session.name,
          selection: (response?.data?.text || '').slice(0, 4000),
          active: null,
          kind: session.kind
        }
      }
      const response = await API.computers.action(sessionId, { action: 'snapshot' })
      const snapshot = response?.data?.snapshot || {}
      return {
        url: snapshot.url,
        title: snapshot.title,
        selection: snapshot.selection || '',
        active: snapshot.active || null,
        kind: session?.kind || 'browser'
      }
    },

    async screenshot({ dispatch }, { sessionId, fullPage = true } = {}) {
      const result = await API.computers.screenshot(sessionId, { full_page: fullPage })
      await dispatch('loadActivity', { sessionId, limit: 50 })
      return result
    }
  }
)