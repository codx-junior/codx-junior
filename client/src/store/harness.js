import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'

/**
 * Harness mode store: the DSH-like session list and the selected session.
 *
 * A session is a normal codx-junior task chat (the backend creates it on the
 * project's Harness board and appends the harness contract). The chat document
 * returned by the session endpoint is registered in the regular chats store, so
 * streamed message events keep updating it in place — harness mode is a lens,
 * not a second conversation store.
 *
 * `codx-junior-harness` is intentionally a REST store: the run state that needs
 * low latency (messages, tool events) already travels on the chat socket.
 */

export const namespaced = true

const inflightSessions = {}
const inflightDetails = {}

function harnessApi() {
  const project = $storex.projects?.activeProject
  return project?.$api || $storex.api
}

function errorMessage(error) {
  return error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
}

export const state = () => ({
  sessions: [],
  // Session detail by id: `{ session, chat, metrics, workspace, diff, files, run, audit }`.
  details: {},
  activeSessionId: '',
  loadingSessions: false,
  loadingSession: false,
  error: null,
  loaded: false,
  lastLoadedAt: 0
})

export const getters = getterTree(state, {
  /** Session summary by id (from the list or from a loaded detail). */
  sessionById: state => (id) => {
    if (!id) return null
    return state.details[id]?.session ||
      state.sessions.find(session => session.session_id === id) ||
      null
  },
  /** Detail of the selected session, when loaded. */
  activeSession: state => (
    state.activeSessionId ? state.details[state.activeSessionId] || null : null
  ),
  /** Chat of the selected session, when loaded. */
  activeChat: (state, getters) => getters.activeSession?.chat || null,
  /** Number of sessions currently running (sidebar badge). */
  runningCount: state => state.sessions.filter(session => session.phase === 'running').length
})

export const mutations = mutationTree(state, {
  setSessions(state, sessions) {
    state.sessions = Array.isArray(sessions) ? sessions : []
    state.loaded = true
    state.lastLoadedAt = Date.now()
  },
  upsertSession(state, session) {
    if (!session?.session_id) return
    const index = state.sessions.findIndex(item => item.session_id === session.session_id)
    if (index === -1) {
      state.sessions = [session, ...state.sessions]
      return
    }
    state.sessions.splice(index, 1, { ...state.sessions[index], ...session })
  },
  setDetail(state, detail) {
    if (!detail?.session?.session_id) return
    state.details = {
      ...state.details,
      [detail.session.session_id]: detail
    }
    const index = state.sessions.findIndex(item => item.session_id === detail.session.session_id)
    if (index === -1) state.sessions = [detail.session, ...state.sessions]
    else state.sessions.splice(index, 1, { ...state.sessions[index], ...detail.session })
  },
  setActiveSessionId(state, sessionId) {
    state.activeSessionId = sessionId || ''
  },
  setLoadingSessions(state, loading) {
    state.loadingSessions = !!loading
  },
  setLoadingSession(state, loading) {
    state.loadingSession = !!loading
  },
  setError(state, error) {
    state.error = error || null
  },
  reset(state) {
    state.sessions = []
    state.details = {}
    state.activeSessionId = ''
    state.error = null
    state.loaded = false
    state.lastLoadedAt = 0
  }
})

export const actions = actionTree(
  { state, getters, mutations },
  {
    /**
     * Load the harness sessions of the active project.
     *
     * @param {object} payload
     * @param {number} [payload.limit] - Max sessions (default 50).
     * @param {boolean} [payload.silent] - Do not toggle the loading flag (poll).
     */
    async loadSessions({ state, commit }, { limit = 50, silent = false } = {}) {
      const api = harnessApi()
      if (!api?.harness?.sessions) return state.sessions
      if (inflightSessions.request) return inflightSessions.request
      if (!silent) commit('setLoadingSessions', true)
      commit('setError', null)
      const request = (async () => {
        try {
          const payload = await api.harness.sessions({ limit })
          commit('setSessions', payload?.sessions || [])
          return state.sessions
        } catch (error) {
          commit('setError', errorMessage(error))
          return state.sessions
        } finally {
          commit('setLoadingSessions', false)
          delete inflightSessions.request
        }
      })()
      inflightSessions.request = request
      return request
    },

    /**
     * Load one session and register its chat document in the chats store.
     *
     * @param {object} payload
     * @param {string} payload.id - Session/chat id.
     * @param {boolean} [payload.silent] - Poll refresh.
     */
    async loadSession({ state, commit }, { id, silent = false } = {}) {
      if (!id) return null
      const api = harnessApi()
      if (!api?.harness?.session) return null
      if (inflightDetails[id]) return inflightDetails[id]
      if (!silent) commit('setLoadingSession', true)
      const request = (async () => {
        try {
          const detail = await api.harness.session(id)
          commit('setError', null)
          commit('setDetail', detail)
          if (detail?.chat?.id && $storex.chats?.registerChatDocument) {
            $storex.chats.registerChatDocument(detail.chat)
          }
          return detail
        } catch (error) {
          commit('setError', errorMessage(error))
          return state.details[id] || null
        } finally {
          commit('setLoadingSession', false)
          delete inflightDetails[id]
        }
      })()
      inflightDetails[id] = request
      return request
    },

    /**
     * Create a session and select it.
     *
     * The backend persists the chat and the harness brief; the caller starts
     * the AI turn through the normal chat socket (`$storex.projects.chatWihProject`).
     */
    async createSession({ commit }, payload = {}) {
      const api = harnessApi()
      if (!api?.harness?.createSession) throw new Error('Harness API is not available')
      const session = await api.harness.createSession(payload)
      commit('setError', null)
      commit('upsertSession', session)
      commit('setActiveSessionId', session.session_id)
      return session
    },

    /**
     * Append a follow-up message to a session, then start the turn.
     *
     * @returns {Promise<object| null>} The updated detail.
     */
    async promptSession(
      { commit },
      { id, content, profiles = [], model = null, attachments = [] } = {}
    ) {
      const api = harnessApi()
      if (!api?.harness?.promptSession) throw new Error('Harness API is not available')
      const detail = await api.harness.promptSession(id, {
        content,
        profiles,
        model,
        attachments
      })
      commit('setError', null)
      commit('setDetail', detail)
      if (detail?.chat?.id && $storex.chats?.registerChatDocument) {
        $storex.chats.registerChatDocument(detail.chat)
      }
      return detail
    },

    /** Select a session (without loading it). */
    selectSession({ commit }, sessionId) {
      commit('setActiveSessionId', sessionId || '')
    },

    /** Clear the store (project switch / logout). */
    reset({ commit }) {
      commit('reset')
    }
  }
)
