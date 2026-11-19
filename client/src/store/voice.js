import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'
import { resolveSpeechLanguage } from '@/config/languages'
import { createMicLevelMonitor } from '@/utils/audioLevel'
import { BARGE_HOLD_MS, BARGE_WARMUP_MS, STRONG_BARGE_HOLD_MS, isAssistantEcho, isBargeInLevel, isStrongBargeInLevel } from '@/utils/voice/bargeIn'
import {
  createVoiceCapture,
  detectVoiceSupport
} from '@/utils/voice/capture'
import {
  parseEndCommand,
  parseSwitchCommand,
  resolveChatQuery
} from '@/utils/voice/commands'
import { nextProgressPlan } from '@/utils/voice/progress'
import { speechPreview } from '@/utils/voice/speechText'
import { createVoicePlayer } from '@/utils/voice/player'
import { waitForAssistantReply } from '@/utils/voice/waitForReply'

/**
 * Voice conversation store.
 *
 * The transcript is the chat: this store only owns the call UI and the speech
 * loop. Every final utterance is posted with the normal chat API, the turn is
 * triggered with the same socket event used by the composer, and the finished
 * answer is read aloud.
 *
 * Browser objects (MediaRecorder, SpeechRecognition, Audio players) live in
 * module variables, never in Vuex state: they are not serializable and must not
 * be proxied.
 */

let capture = null
let player = null
let timerInterval = null
let turnAbort = null
let initialized = false
// Monotonic token that cancels an in-flight startCall when the panel is closed.
let startAttempt = 0
// Cancels the "restore listening" step of a speech that was superseded.
let speechGeneration = 0
// Spoken progress while the agent thinks / runs tools.
let progressTimer = null
let progressState = null
let progressPlayer = null
let progressGeneration = 0
// Barge-in: a microphone monitor that runs only while the assistant speaks.
let bargeMonitor = null
let bargeStream = null
let bargeArmedAt = 0
let bargeTriggered = false
let bargeRecognition = null
let bargeRecognitionTimer = null
let bargeWatchdog = null
let bargeLevelStarting = false
let currentSpeechText = ''

const STATUS_LABELS = {
  idle: 'Ready',
  connecting: 'Connecting…',
  listening: 'Listening',
  transcribing: 'Transcribing…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  switching: 'Switching chat…',
  ended: 'Call ended',
  error: 'Needs attention'
}

export const namespaced = true

export const state = () => ({
  open: false,
  minimized: false,
  setupChatId: null,
  // Backend session (see codx/junior/voice/model.py) and its chat.
  session: null,
  chatId: null,
  // idle | connecting | listening | transcribing | thinking | speaking |
  // switching | ended | error
  status: 'idle',
  error: null,
  muted: false,
  captions: true,
  interim: '',
  level: 0,
  elapsed: 0,
  startedAt: null,
  language: 'en-US',
  // A manual choice in the call setup wins over the user/browser preference
  // for the rest of this call; a new call resolves it again.
  languageManual: false,
  // Optional server voices are used when available; browser TTS is the fallback.
  useServerVoice: true,
  capabilities: null,
  support: {
    speechRecognition: false,
    speechSynthesis: false,
    microphone: false,
    mediaRecorder: false
  },
  captureMode: null,
  recentSessions: [],
  loadingSessions: false,
  switching: false,
  pendingSwitch: null,
  lastReply: null,
  narrateProgress: true,
  progressText: ''
})

export const getters = getterTree(state, {
  chat: state => {
    return state.chatId ? $storex.chats.chats?.[state.chatId] || null : null
  },
  setupChat: state => {
    return state.setupChatId ? $storex.chats.chats?.[state.setupChatId] || null : null
  },
  isActive: state => {
    return Boolean(
      state.session &&
      state.session.status === 'active' &&
      !['ended', 'idle'].includes(state.status)
    )
  },
  isOnCallChat: state => {
    return Boolean(state.chatId && $storex.chats.activeChatId === state.chatId)
  },
  statusLabel: state => STATUS_LABELS[state.status] || state.status,
  statusTone: state => {
    if (state.status === 'listening') return 'success'
    if (state.status === 'speaking') return 'info'
    if (state.status === 'thinking' || state.status === 'transcribing') return 'warning'
    if (state.status === 'error') return 'error'
    if (state.status === 'switching') return 'info'
    return 'neutral'
  },
  canSpeak: state => {
    if (!state.session || state.session.status !== 'active') return false
    if (!state.capabilities) return true
    return Boolean(
      state.support.speechSynthesis ||
      state.capabilities?.synthesis?.available
    )
  },
  canListen: state => {
    return Boolean(
      !state.muted &&
      state.session?.status === 'active' &&
      (state.support.speechRecognition ||
        (state.support.microphone &&
          state.support.mediaRecorder &&
          state.capabilities?.transcription?.available))
    )
  },
  // Chats available as a destination, excluding the current one and templates.
  switchChats: state => {
    return ($storex.chats.allChats || [])
      .filter(chat => chat.id && chat.id !== state.chatId && !chat.is_template)
      .sort((a, b) => {
        const timeA = new Date(a.updated_at || a.lastMessageAt || 0).getTime()
        const timeB = new Date(b.updated_at || b.lastMessageAt || 0).getTime()
        return timeB - timeA
      })
  },
  callMessages: state => {
    const chat = state.chatId ? $storex.chats.chats?.[state.chatId] : null
    return (chat?.messages || [])
      .filter(message => !message.unsent)
      .slice(-12)
  }
})

export const mutations = mutationTree(state, {
  setOpen(state, open) {
    state.open = Boolean(open)
  },
  setMinimized(state, minimized) {
    state.minimized = Boolean(minimized)
  },
  setSetupChatId(state, chatId) {
    state.setupChatId = chatId || null
  },
  setSession(state, session) {
    state.session = session || null
    if (session?.chat_id) state.chatId = session.chat_id
  },
  setChatId(state, chatId) {
    state.chatId = chatId || null
  },
  setStatus(state, status) {
    state.status = status || 'idle'
  },
  setError(state, error) {
    state.error = error ? String(error.message || error) : null
  },
  setMuted(state, muted) {
    state.muted = Boolean(muted)
  },
  setCaptions(state, captions) {
    state.captions = Boolean(captions)
  },
  setInterim(state, interim) {
    state.interim = interim || ''
  },
  setLevel(state, level) {
    state.level = Number(level) || 0
  },
  setElapsed(state, elapsed) {
    state.elapsed = Math.max(0, Number(elapsed) || 0)
  },
  setStartedAt(state, startedAt) {
    state.startedAt = startedAt || null
  },
  setLanguage(state, language) {
    state.language = language || 'en-US'
  },
  setLanguageManual(state, value) {
    state.languageManual = Boolean(value)
  },
  setUseServerVoice(state, value) {
    state.useServerVoice = Boolean(value)
  },
  setCapabilities(state, capabilities) {
    state.capabilities = capabilities || null
  },
  setSupport(state, support) {
    state.support = { ...state.support, ...(support || {}) }
  },
  setCaptureMode(state, mode) {
    state.captureMode = mode || null
  },
  setRecentSessions(state, sessions) {
    state.recentSessions = Array.isArray(sessions) ? sessions : []
  },
  setLoadingSessions(state, loading) {
    state.loadingSessions = Boolean(loading)
  },
  setSwitching(state, switching) {
    state.switching = Boolean(switching)
  },
  setPendingSwitch(state, pendingSwitch) {
    state.pendingSwitch = pendingSwitch || null
  },
  setLastReply(state, message) {
    state.lastReply = message || null
  },
  setNarrateProgress(state, value) {
    state.narrateProgress = Boolean(value)
  },
  setProgressText(state, value) {
    state.progressText = value || ''
  }
})

function newMessageId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

function clearTimer() {
  if (timerInterval) clearInterval(timerInterval)
  timerInterval = null
}

function stopBargeMonitor() {
  bargeArmedAt = 0
  if (bargeRecognitionTimer) {
    clearTimeout(bargeRecognitionTimer)
    bargeRecognitionTimer = null
  }
  if (bargeWatchdog) {
    clearTimeout(bargeWatchdog)
    bargeWatchdog = null
  }
  if (bargeRecognition) {
    const recognition = bargeRecognition
    bargeRecognition = null
    try {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      recognition.abort()
    } catch (error) { /* already stopped */ }
  }
  if (bargeMonitor) {
    try { bargeMonitor.stop() } catch (error) { /* already stopped */ }
    bargeMonitor = null
  }
  if (bargeStream) {
    try { bargeStream.getTracks().forEach(track => track.stop()) }
    catch (error) { /* already stopped */ }
    bargeStream = null
  }
}

function stopProgressAudio() {
  progressGeneration++
  if (progressPlayer) {
    try { progressPlayer.stop() } catch (error) { /* already stopped */ }
  }
}

function disposeCapture() {
  if (capture) {
    try { capture.stop() } catch (error) { /* already gone */ }
    capture = null
  }
}

function disposePlayer() {
  speechGeneration++
  stopBargeMonitor()
  if (player) {
    try { player.stop() } catch (error) { /* already gone */ }
  }
}

function abortTurn() {
  if (turnAbort) {
    try { turnAbort.abort() } catch (error) { /* already aborted */ }
    turnAbort = null
  }
}

function ensurePlayer() {
  if (!player) {
    // The store owns the status; speak() restores the listening state itself.
    player = createVoicePlayer()
  }
  return player
}

/**
 * Barge-in detector #1: a temporary SpeechRecognition instance.
 *
 * This is the same engine used for input and therefore the most reliable way
 * to notice the user's voice on every browser that supports it. Its results
 * are compared with the text being spoken so the assistant's own voice
 * (speaker leak) does not interrupt itself.
 */
function startBargeRecognition({ state, commit, dispatch }) {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SpeechRecognition) return false
  let stopped = false

  const start = () => {
    if (stopped || bargeTriggered || state.status !== 'speaking') return false
    let recognition
    try {
      recognition = new SpeechRecognition()
    } catch (error) {
      stopped = true
      return false
    }
    recognition.lang = state.language
    recognition.continuous = true
    recognition.interimResults = true
    recognition.maxAlternatives = 1

    recognition.onresult = event => {
      if (bargeTriggered || state.status !== 'speaking') return
      if (Date.now() - bargeArmedAt < 500) return
      let heard = ''
      for (let index = event.resultIndex || 0; index < event.results.length; index++) {
        heard += event.results[index]?.[0]?.transcript || ''
      }
      heard = heard.trim()
      if (!heard) return
      if (isAssistantEcho(heard, currentSpeechText)) {
        // It is the assistant's own voice coming back from the speakers.
        return
      }
      console.debug('[voice] barge-in detected by recognition:', heard)
      bargeTriggered = true
      stopBargeMonitor()
      dispatch('handleBargeIn')
    }

    recognition.onerror = event => {
      const reason = event?.error || ''
      console.debug('[voice] barge recognition error:', reason)
      if (
        reason === 'not-allowed' ||
        reason === 'service-not-allowed' ||
        reason === 'audio-capture' ||
        reason === 'network'
      ) {
        stopped = true
        // The speech engine cannot run while the answer plays: use the
        // microphone-level fallback instead.
        setTimeout(() => {
          if (!bargeTriggered && state.status === 'speaking') {
            startLevelBargeMonitor({ state, commit, dispatch })
          }
        }, 100)
      }
      // Transient `no-speech`/`aborted` are handled by onend restarting.
    }

    recognition.onend = () => {
      if (bargeRecognition === recognition) bargeRecognition = null
      if (stopped || bargeTriggered || state.status !== 'speaking') return
      bargeRecognitionTimer = setTimeout(start, 200)
    }

    try {
      recognition.start()
      bargeRecognition = recognition
      console.debug('[voice] barge recognition started')
      // If the recognizer produces nothing (some engines refuse to listen
      // while TTS plays), add the level monitor as a second chance.
      if (bargeWatchdog) clearTimeout(bargeWatchdog)
      bargeWatchdog = setTimeout(() => {
        bargeWatchdog = null
        if (!bargeTriggered && state.status === 'speaking' && !bargeMonitor) {
          startLevelBargeMonitor({ state, commit, dispatch })
        }
      }, 1800)
      return true
    } catch (error) {
      stopped = true
      return false
    }
  }

  return start()
}

/**
 * Barge-in detector #2: microphone level monitor.
 *
 * Fallback for browsers without SpeechRecognition (and second chance when the
 * recognizer refuses to listen while TTS plays). It learns the ambient level
 * during a short warm-up and requires either a sustained level above it or a
 * clearly loud voice.
 */
async function startLevelBargeMonitor({ state, commit, dispatch }) {
  if (bargeTriggered || state.status !== 'speaking' || state.muted) return
  if (!navigator.mediaDevices?.getUserMedia) return
  if (bargeMonitor || bargeStream || bargeLevelStarting) return
  bargeLevelStarting = true
  const armedAt = Date.now()
  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      },
      video: false
    })
    if (
      state.status !== 'speaking' ||
      bargeTriggered ||
      state.muted ||
      state.session?.status !== 'active'
    ) {
      stream.getTracks().forEach(track => track.stop())
      return
    }
    bargeStream = stream
    let loudSince = 0
    let strongSince = 0
    let ambientSum = 0
    let ambientCount = 0
    let ambientLevel = 0
    bargeMonitor = createMicLevelMonitor({
      threshold: 9,
      pollMs: 80,
      onLevel: level => {
        if (!armedAt || state.status !== 'speaking' || bargeTriggered) return
        const elapsed = Date.now() - armedAt
        if (elapsed < BARGE_WARMUP_MS) {
          ambientSum += level
          ambientCount += 1
          ambientLevel = ambientCount ? ambientSum / ambientCount : 0
          return
        }
        if (isStrongBargeInLevel(level)) {
          if (!strongSince) strongSince = Date.now()
          if (
            Date.now() - strongSince >= STRONG_BARGE_HOLD_MS &&
            !bargeTriggered
          ) {
            bargeTriggered = true
            stopBargeMonitor()
            dispatch('handleBargeIn')
            return
          }
        } else {
          strongSince = 0
        }
        if (isBargeInLevel(level, ambientLevel)) {
          if (!loudSince) loudSince = Date.now()
          else if (Date.now() - loudSince >= BARGE_HOLD_MS && !bargeTriggered) {
            bargeTriggered = true
            stopBargeMonitor()
            dispatch('handleBargeIn')
          }
        } else {
          loudSince = 0
        }
      }
    })
    bargeMonitor.start(bargeStream)
    console.debug('[voice] barge level monitor started')
  } catch (error) {
    // A monitor that cannot open must never break playback (or the working
    // SpeechRecognition detector, which may be holding the microphone).
    if (stream) {
      try { stream.getTracks().forEach(track => track.stop()) } catch (e) { /* ignore */ }
    }
    if (bargeStream === stream) bargeStream = null
    if (bargeMonitor) {
      try { bargeMonitor.stop() } catch (e) { /* already stopped */ }
      bargeMonitor = null
    }
  } finally {
    bargeLevelStarting = false
  }
}

export const actions = actionTree(
  { state, getters, mutations },
  {
    async init({ commit, dispatch }) {
      if (initialized) return
      initialized = true
      commit('setSupport', detectVoiceSupport(window, navigator))
      return dispatch('loadCapabilities')
    },

    async loadCapabilities({ commit }) {
      try {
        const capabilities = await $storex.api.voice.capabilities()
        commit('setCapabilities', capabilities)
        return capabilities
      } catch (error) {
        console.warn('[voice] capabilities unavailable', error)
        commit('setCapabilities', {
          transcription: { available: false },
          synthesis: { available: false }
        })
        return null
      }
    },

    async loadRecentSessions({ commit }) {
      commit('setLoadingSessions', true)
      try {
        const response = await $storex.api.voice.listSessions({ limit: 20 })
        commit('setRecentSessions', response?.sessions || [])
        return response?.sessions || []
      } catch (error) {
        console.warn('[voice] could not list sessions', error)
        commit('setRecentSessions', [])
        return []
      } finally {
        commit('setLoadingSessions', false)
      }
    },

    /** Open the call panel (setup screen when no call is running). */
    async openSetup({ state, commit, dispatch }, { chat = null } = {}) {
      // A finished call is history: the phone button always opens the setup
      // panel again, not the previous "call ended" summary.
      if (state.session?.status === 'ended') {
        disposeCapture()
        disposePlayer()
        player = null
        capture = null
        clearTimer()
        commit('setSession', null)
        commit('setChatId', null)
        commit('setStatus', 'idle')
        commit('setStartedAt', null)
        commit('setElapsed', 0)
      }
      commit('setOpen', true)
      commit('setMinimized', false)
      const chatId =
        chat?.id ||
        $storex.chats.activeChatId ||
        state.chatId ||
        null
      commit('setSetupChatId', chatId)
      commit('setError', null)
      if (!state.session) {
        await dispatch('resolveLanguage')
      }
      if (!initialized) {
        initialized = true
        commit('setSupport', detectVoiceSupport(window, navigator))
      }
      await Promise.all([
        dispatch('loadCapabilities'),
        dispatch('loadRecentSessions'),
        dispatch('loadSwitchChats')
      ])
    },

    /**
     * Resolve the speech language from the codx user preference, falling back
     * to the browser locale (and finally the legacy UI language).
     */
    async resolveLanguage({ commit }) {
      const userLanguage =
        $storex.users?.user?.language ||
        $storex.api.user?.language ||
        ''
      const browserLanguage =
        navigator.language ||
        navigator.languages?.[0] ||
        ''
      const fallback = $storex.ui.voiceLanguage || 'en-US'
      commit(
        'setLanguage',
        resolveSpeechLanguage({ userLanguage, browserLanguage, fallback })
      )
      commit('setLanguageManual', false)
      return true
    },

    async loadSwitchChats() {
      try {
        await $storex.chats.loadRecentChats({ page: 1, pageSize: 30 })
      } catch (error) {
        console.warn('[voice] could not load chats', error)
      }
    },

    /** Start a call in the active / setup chat. */
    async startCall({ state, commit, dispatch }, { chat = null, sessionId = null, announce = true } = {}) {
      const target =
        chat ||
        (state.setupChatId && $storex.chats.chats?.[state.setupChatId]) ||
        $storex.chats.activeChat
      if (!target?.id) {
        return dispatch('startNewConversation')
      }
      if (state.status === 'connecting' && !state.session) {
        // A double click must not create two sessions.
        commit('setOpen', true)
        commit('setMinimized', false)
        return null
      }
      if (target.harness || target.external_source) {
        const message = 'This task is executed by an external harness: its transcript is read-only, so a voice call cannot run here.'
        commit('setStatus', 'error')
        commit('setError', message)
        $storex.ui.addNotification({ text: message, type: 'warning' })
        return null
      }
      if (state.session?.status === 'active') {
        if (state.session.chat_id === target.id) {
          // The call is already here: the phone button just re-opens it.
          commit('setOpen', true)
          commit('setMinimized', false)
          return state.session
        }
        // A call is already running in another chat: move it instead of
        // opening a second call. This is the "continue this chat" gesture.
        commit('setOpen', true)
        commit('setMinimized', false)
        return dispatch('switchToChat', { chat: target })
      }
      if (!state.languageManual) await dispatch('resolveLanguage')
      commit('setSupport', detectVoiceSupport(window, navigator))
      const attempt = ++startAttempt
      commit('setOpen', true)
      commit('setMinimized', false)
      commit('setStatus', 'connecting')
      commit('setError', null)
      commit('setInterim', '')
      commit('setSetupChatId', target.id)
      if (!state.capabilities) await dispatch('loadCapabilities')
      if (attempt !== startAttempt) return null

      try {
        const response = await $storex.api.voice.createSession({
          chat_id: target.id,
          session_id: sessionId || undefined,
          owner_project_id: target.owner_project_id || undefined,
          language: state.language,
          announce,
          meta: { client: 'web', source: 'voice-call' }
        })
        const session = response?.session
        if (!session) throw new Error('the server did not return a voice session')
        if (attempt !== startAttempt) {
          // The user cancelled while the session was being created: do not
          // leave an invisible active call behind.
          try {
            await $storex.api.voice.endSession(session.session_id, {
              reason: 'cancelled while starting'
            })
          } catch (cancelError) {
            console.warn('[voice] could not cancel the abandoned call', cancelError)
          }
          return null
        }
        commit('setSession', session)
        commit('setChatId', session.chat_id)
        commit('setStartedAt', new Date().toISOString())
        commit('setElapsed', 0)
        commit('setMuted', false)
        commit('setPendingSwitch', session.pending_switch || null)
        clearTimer()
        timerInterval = setInterval(() => {
          const started = state.startedAt ? new Date(state.startedAt).getTime() : Date.now()
          commit('setElapsed', Math.floor((Date.now() - started) / 1000))
        }, 1000)

        // Show the conversation: the call is also a chat.
        try {
          if (response?.chat?.id) {
            await $storex.chats.loadChat({
              id: session.chat_id,
              owner_project_id: session.owner_project_id
            })
          } else {
            await $storex.chats.loadChat({ id: session.chat_id })
          }
          $storex.ui.openChat(response?.chat || { id: session.chat_id, name: session.title })
        } catch (openError) {
          console.warn('[voice] could not open the call chat', openError)
        }

        await dispatch('primeMicrophone')
        await dispatch('startListening')
        if (attempt !== startAttempt) {
          disposeCapture()
          clearTimer()
          try {
            await $storex.api.voice.endSession(session.session_id, {
              reason: 'cancelled while starting'
            })
          } catch (cancelError) {
            console.warn('[voice] could not cancel the abandoned call', cancelError)
          }
          commit('setSession', null)
          commit('setChatId', null)
          commit('setStatus', 'idle')
          commit('setMinimized', false)
          return null
        }
        if (response?.notice) {
          commit('setLastReply', { content: response.notice, role: 'assistant' })
        }
        const canSpeak =
          state.support.speechSynthesis ||
          state.capabilities?.synthesis?.available
        if ((response?.created || response?.resumed) && canSpeak) {
          // Audible confirmation also unlocks SpeechSynthesis on browsers that
          // require a user gesture before the first utterance.
          await dispatch('speak', {
            text: response?.resumed ? "I'm listening again." : "I'm listening.",
            force: true,
            allowBargeIn: false
          })
        }
        // A previous assistant-requested switch may still be pending (the page
        // was closed before it was applied): honour it now.
        if (session.pending_switch?.chat_id) {
          const pending = session.pending_switch
          commit('setPendingSwitch', null)
          await dispatch('switchToChat', {
            chat: { id: pending.chat_id, name: pending.chat_name },
            reason: pending.reason || 'assistant requested'
          })
        }
        return session
      } catch (error) {
        console.error('[voice] could not start the call', error)
        clearTimer()
        commit('setStatus', 'error')
        commit('setError', error)
        $storex.ui.addNotification({
          text: `Could not start the voice call: ${error.message || error}`,
          type: 'error'
        })
        return null
      }
    },

    /** Start a call in a brand new chat (from scratch). */
    async startNewConversation({ dispatch, commit }) {
      try {
        const stamp = new Date().toLocaleString()
        const chat = await $storex.chats.createNewChat({
          name: `Voice call ${stamp}`,
          description: 'Voice conversation started from a call.',
          mode: 'chat',
          profiles: []
        })
        if (!chat?.id) throw new Error('could not create the chat')
        await $storex.chats.setActiveChat(chat)
        return await dispatch('startCall', { chat })
      } catch (error) {
        commit('setStatus', 'error')
        commit('setError', error)
        $storex.ui.addNotification({
          text: `Could not create a voice conversation: ${error.message || error}`,
          type: 'error'
        })
        return null
      }
    },

    /** Resume a call listed in the setup panel. */
    async resumeSession({ state, commit, dispatch }, session) {
      if (!session?.session_id || !session?.chat_id) return
      // Another call may be running: the user explicitly chose this session,
      // so end the current one instead of silently moving it.
      if (
        state.session?.status === 'active' &&
        state.session.session_id !== session.session_id
      ) {
        await dispatch('endCall', {
          reason: 'another voice session was resumed',
          announce: false,
          keepOpen: true
        })
      }
      // Keep the language stored on the session being resumed.
      if (session.language) {
        commit('setLanguage', session.language)
        commit('setLanguageManual', true)
      }
      try {
        await $storex.chats.loadChat({
          id: session.chat_id,
          owner_project_id: session.owner_project_id
        })
      } catch (error) {
        console.warn('[voice] could not load the resumed chat', error)
      }
      return dispatch('startCall', {
        chat: { id: session.chat_id },
        sessionId: session.session_id
      })
    },

    /**
     * Request microphone permission once at call start.
     *
     * SpeechRecognition asks for it implicitly, but the barge-in monitor uses
     * getUserMedia later (while the assistant speaks), where a first-time
     * permission prompt would be both surprising and possibly blocked.
     */
    async primeMicrophone({ state }) {
      if (!state.support.speechRecognition) return
      if (!navigator.mediaDevices?.getUserMedia) return
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: false
        })
        stream.getTracks().forEach(track => track.stop())
      } catch (error) {
        // The capture path reports the real permission error.
      }
    },

    /** Create/recreate the microphone capture and start listening. */
    async startListening({ state, commit, dispatch }) {
      if (!state.session || state.session.status !== 'active') return
      if (state.muted) {
        commit('setStatus', 'idle')
        return
      }
      if (['thinking', 'transcribing', 'speaking', 'switching', 'connecting'].includes(state.status)) {
        return
      }
      const support = state.support?.speechRecognition
        ? state.support
        : detectVoiceSupport(window, navigator)
      commit('setSupport', support)
      const serverStt = Boolean(state.capabilities?.transcription?.available)
      const canCapture =
        support.speechRecognition ||
        (support.microphone && support.mediaRecorder && serverStt)
      if (!canCapture) {
        commit('setStatus', 'idle')
        commit(
          'setError',
          serverStt || support.mediaRecorder
            ? 'Voice input is not available in this browser. You can type your instructions below.'
            : 'Voice input needs a microphone and SpeechRecognition, or a configured server transcription backend. You can type below.'
        )
        return
      }

      if (!capture) {
        capture = createVoiceCapture({
          language: state.language,
          serverTranscribe: serverStt
            ? async blob => {
                const response = await $storex.api.voice.transcribe(blob, state.language)
                return response?.text || ''
              }
            : null,
          onFinal: text => dispatch('handleFinalTranscript', text),
          onInterim: text => commit('setInterim', text),
          onLevel: level => commit('setLevel', level),
          onError: error => dispatch('handleCaptureError', error),
          onStateChange: captureState => {
            if (captureState === 'transcribing') commit('setStatus', 'transcribing')
          }
        })
        commit('setCaptureMode', capture.getMode())
      }
      commit('setError', null)
      commit('setStatus', 'listening')
      try {
        await capture.start()
      } catch (error) {
        dispatch('handleCaptureError', error)
      }
    },

    async stopListening({ state, commit }) {
      if (capture) {
        try { capture.pause() } catch (error) { /* already paused */ }
      }
      commit('setInterim', '')
      commit('setLevel', 0)
      if (state.status === 'listening') commit('setStatus', 'idle')
    },

    async toggleMute({ state, commit, dispatch }) {
      const muted = !state.muted
      commit('setMuted', muted)
      if (muted) {
        stopBargeMonitor()
        commit('setError', null)
        await dispatch('stopListening')
      } else {
        commit('setError', null)
        if (state.status === 'speaking') dispatch('startBargeMonitor')
        await dispatch('startListening')
      }
    },

    async retryMicrophone({ commit, dispatch }) {
      commit('setMuted', false)
      commit('setError', null)
      disposeCapture()
      commit('setCaptureMode', null)
      await dispatch('startListening')
    },

    handleCaptureError({ commit, state }, error) {
      const message = error?.message || String(error)
      console.warn('[voice] capture error', error)
      commit('setStatus', state.session?.status === 'active' ? 'error' : 'idle')
      commit('setError', message)
      $storex.ui.addNotification({ text: message, type: 'error' })
      if (capture) {
        try { capture.stop() } catch (stopError) { /* already stopped */ }
        capture = null
      }
    },

    /** Final transcript: command, or a normal chat message. */
    async handleFinalTranscript({ state, commit, dispatch }, text) {
      const value = String(text || '').trim()
      if (!value) return
      if (!state.session || state.session.status !== 'active') return
      if (['thinking', 'transcribing', 'switching', 'speaking'].includes(state.status)) return

      if (parseEndCommand(value)) {
        return dispatch('endCall', { reason: 'voice command', announce: true })
      }
      const command = parseSwitchCommand(value)
      if (command.matched) {
        const handled = await dispatch('switchByQuery', command.query)
        if (handled !== false) return handled
        // No chat matches the name: it may be a normal instruction ("switch
        // to the develop branch"), so let the agent answer it.
      }
      return dispatch('sendTranscript', value)
    },

    /** Resolve a spoken chat name and switch the call. */
    async switchByQuery({ state, dispatch, commit }, query) {
      const chats = $storex.chats.allChats || []
      if (!chats.length) await dispatch('loadSwitchChats')
      const result = resolveChatQuery($storex.chats.allChats || [], query)
      if (result.ambiguous) {
        const names = result.candidates.slice(0, 4).map(chat => `“${chat.name}”`).join(', ')
        await dispatch('announce', `I found more than one match: ${names}. Please say the full chat name.`)
        return
      }
      const target = result.chat
      if (!target?.id) {
        // Let the caller fall back to the normal chat pipeline; the assistant
        // may still resolve the destination with the voice_switch_chat tool or
        // answer a different "switch ..." instruction.
        return false
      }
      if (target.id === state.chatId) {
        await dispatch('announce', `We are already in “${target.name}”.`)
        return true
      }
      await dispatch('switchToChat', { chat: target, reason: `voice command: ${query}` })
      return true
    },

    /** Move the call to another chat; backend stores and announces the move. */
    async switchToChat({ state, commit, dispatch }, { chat, reason = '' } = {}) {
      if (!state.session || state.session.status !== 'active') return
      if (!chat?.id) return
      if (['thinking', 'transcribing'].includes(state.status)) {
        // Moving mid-turn would leave the answer in the old chat while the
        // call listens in the new one.
        commit('setError', 'Wait for the current answer before switching chats.')
        $storex.ui.addNotification({
          text: 'Wait for the current answer before switching chats.',
          type: 'warning'
        })
        return null
      }
      if (chat.id === state.chatId) {
        return dispatch('announce', `We are already in “${chat.name || 'this chat'}”.`)
      }
      await dispatch('stopProgressNarration')
      commit('setSwitching', true)
      commit('setStatus', 'switching')
      commit('setError', null)
      disposePlayer()
      await dispatch('stopListening')
      try {
        const response = await $storex.api.voice.switchSession(
          state.session.session_id,
          {
            chat_id: chat.id,
            reason,
            requested_by: 'user',
            announce: true
          }
        )
        commit('setSession', response.session)
        commit('setChatId', response.session.chat_id)
        commit('setPendingSwitch', null)
        commit('setInterim', '')
        // Open the destination chat so the transcript follows the call.
        try {
          await $storex.chats.loadChat({
            id: response.session.chat_id,
            owner_project_id: response.session.owner_project_id
          })
          $storex.ui.openChat(
            response.chat || {
              id: response.session.chat_id,
              name: response.session.title
            }
          )
        } catch (openError) {
          console.warn('[voice] could not open the destination chat', openError)
        }
        commit('setSwitching', false)
        await dispatch('announce', response.notice || `Switching to “${chat.name || 'the new chat'}”.`)
        return response
      } catch (error) {
        console.error('[voice] could not switch the call', error)
        commit('setSwitching', false)
        commit('setError', error)
        commit('setStatus', 'listening')
        await dispatch('announce', `I could not switch the conversation: ${error.message || error}`)
        await dispatch('startListening')
        return null
      }
    },

    /** Speak one short progress phrase without changing the call status. */
    async speakProgress({ state }, text) {
      const value = String(text || '').trim()
      if (!value) return
      if (!state.narrateProgress) return
      const canSpeak =
        state.support.speechSynthesis ||
        state.capabilities?.synthesis?.available
      if (!canSpeak) return
      const generation = ++progressGeneration
      if (!progressPlayer) progressPlayer = createVoicePlayer()
      const serverAudio =
        state.useServerVoice && state.capabilities?.synthesis?.available
          ? async speech => {
              const blob = await $storex.api.voice.speech({
                text: speech,
                language: state.language,
                speed: 1
              })
              return blob
            }
          : null
      try {
        await progressPlayer.speak(value, {
          language: state.language,
          serverAudio
        })
      } catch (error) {
        // Progress is optional feedback: never break the call for it.
        console.warn('[voice] progress narration failed', error)
      } finally {
        if (generation !== progressGeneration) return
      }
    },

    /**
     * Narrate thinking/tool progress for the current turn.
     *
     * Reads the streamed tool events of the assistant message and speaks a
     * short phrase with a conservative throttle (see utils/voice/progress.js).
     */
    async startProgressNarration({ state, commit, dispatch }, { chatId, userMessageId } = {}) {
      await dispatch('stopProgressNarration')
      if (!chatId || !userMessageId || !state.narrateProgress) return
      const canSpeak =
        state.support.speechSynthesis ||
        state.capabilities?.synthesis?.available
      if (!canSpeak) return
      progressState = {
        chatId,
        userMessageId,
        startedAt: Date.now(),
        lastSpokenAt: 0,
        count: 0,
        announcedToolKeys: [],
        speaking: false
      }
      progressTimer = setInterval(async () => {
        const current = progressState
        if (!current || state.status !== 'thinking' || current.speaking) return
        if (!state.narrateProgress) return
        const chat = $storex.chats.chats?.[current.chatId]
        if (!chat) return
        const plan = nextProgressPlan({
          messages: chat.messages,
          userMessageId: current.userMessageId,
          announcedToolKeys: current.announcedToolKeys,
          startedAt: current.startedAt,
          lastSpokenAt: current.lastSpokenAt,
          count: current.count
        })
        if (!plan) return
        current.count += 1
        current.lastSpokenAt = Date.now()
        current.announcedToolKeys = [
          ...new Set([...current.announcedToolKeys, ...(plan.keys || [])])
        ]
        current.speaking = true
        commit('setProgressText', plan.text)
        try {
          await dispatch('speakProgress', plan.text)
        } finally {
          if (progressState === current) current.speaking = false
        }
      }, 1000)
    },

    async stopProgressNarration({ commit }) {
      if (progressTimer) clearInterval(progressTimer)
      progressTimer = null
      progressState = null
      stopProgressAudio()
      progressPlayer = null
      commit('setProgressText', '')
    },

    async toggleNarration({ state, commit, dispatch }) {
      commit('setNarrateProgress', !state.narrateProgress)
      if (!state.narrateProgress) await dispatch('stopProgressNarration')
    },

    /**
     * Watch the microphone while the assistant speaks. A sustained voice above
     * the threshold is a barge-in: stop the answer and start listening again.
     */
    async startBargeMonitor({ state, commit, dispatch }) {
      if (state.muted || state.status !== 'speaking') return
      if (!state.session || state.session.status !== 'active') return
      const canListen =
        state.support.speechRecognition ||
        (state.support.microphone &&
          state.support.mediaRecorder &&
          state.capabilities?.transcription?.available)
      if (!canListen) return
      stopBargeMonitor()
      bargeTriggered = false
      bargeArmedAt = Date.now()
      // Give the normal recognizer a moment to release the device before the
      // barge detector asks for it.
      await new Promise(resolve => setTimeout(resolve, 60))
      if (state.status !== 'speaking' || bargeTriggered) return
      // Prefer the speech recognizer: it is the engine that already works for
      // this user, and it does not depend on a second microphone stream (which
      // can be refused while SpeechRecognition holds the device).
      if (state.support.speechRecognition && startBargeRecognition({ state, commit, dispatch })) {
        return
      }
      await startLevelBargeMonitor({ state, commit, dispatch })
    },

    async handleBargeIn({ state, commit, dispatch }) {
      if (state.status !== 'speaking' || state.muted) return
      speechGeneration++
      stopBargeMonitor()
      if (player) {
        try { player.stop() } catch (error) { /* already stopped */ }
      }
      commit('setInterim', '')
      commit('setStatus', 'listening')
      commit('setError', null)
      // Let the recognizer release the microphone before the normal capture
      // starts again (otherwise the two engines can race for the device).
      await new Promise(resolve => setTimeout(resolve, 120))
      await dispatch('startListening')
    },

    /** Speak a short system phrase without treating it as an AI turn. */
    async announce({ state, commit, dispatch }, text) {
      const value = String(text || '').trim()
      if (!value) return
      if (state.session?.status === 'active') {
        await dispatch('stopListening')
      }
      await dispatch('speak', { text: value, allowBargeIn: false })
    },

    /** Speak an assistant answer (browser or server voice) with captions. */
    async speak({ state, commit, dispatch }, { text, force = false, allowBargeIn = true } = {}) {
      const value = String(text || '').trim()
      if (!value) return
      if (!force && state.session?.status !== 'active') return
      const canSpeak =
        state.support.speechSynthesis ||
        state.capabilities?.synthesis?.available
      if (!canSpeak) {
        commit('setError', 'Voice output is not available; the answer is in the chat.')
        if (state.session?.status === 'active') {
          if (!state.muted) {
            commit('setStatus', 'listening')
            await dispatch('startListening')
          } else {
            commit('setStatus', 'idle')
          }
        }
        return
      }
      const preview = speechPreview(value)
      if (!preview) return
      // Never keep the normal recognizer running while the barge detector
      // starts its own: two SpeechRecognition instances fight for the mic.
      await dispatch('stopListening')
      const speechToken = ++speechGeneration
      currentSpeechText = preview
      const currentPlayer = ensurePlayer()
      commit('setStatus', 'speaking')
      if (allowBargeIn && !state.muted) {
        // Fire and forget: opening the monitor must never delay the answer.
        dispatch('startBargeMonitor')
      }
      try {
        const serverAudio =
          state.useServerVoice && state.capabilities?.synthesis?.available
            ? async speech => {
                const blob = await $storex.api.voice.speech({
                  text: speech,
                  language: state.language,
                  speed: 1
                })
                return blob
              }
            : null
        await currentPlayer.speak(preview, {
          language: state.language,
          serverAudio
        })
      } catch (error) {
        console.warn('[voice] could not speak', error)
        commit('setError', `Voice output failed: ${error.message || error}`)
        $storex.ui.addNotification({
          text: 'The assistant answer could not be spoken; read it in the chat.',
          type: 'warning'
        })
      } finally {
        stopBargeMonitor()
        if (speechToken === speechGeneration) currentSpeechText = ''
        if (speechToken !== speechGeneration) return
        if (state.session?.status === 'active' && !state.muted) {
          commit('setStatus', 'listening')
          await dispatch('startListening')
        } else if (state.session?.status === 'active') {
          commit('setStatus', 'idle')
        }
      }
    },

    async stopSpeaking({ state, commit, dispatch }) {
      speechGeneration++
      disposePlayer()
      player = null
      if (state.session?.status === 'active' && !state.muted) {
        commit('setStatus', 'listening')
        await dispatch('startListening')
      }
    },

    /** Post one spoken/typed instruction and wait for the answer. */
    async sendTranscript({ state, commit, dispatch }, text) {
      const value = String(text || '').trim()
      if (!value) return
      if (!state.session || state.session.status !== 'active') return
      if (['thinking', 'transcribing', 'switching'].includes(state.status)) return
      const chat = $storex.chats.chats?.[state.chatId]
      if (!chat) {
        commit('setError', 'The call chat is not loaded')
        return
      }

      // A typed instruction may arrive while the previous answer is still
      // being spoken: stop the audio before starting the new turn.
      disposePlayer()
      await dispatch('stopListening')
      commit('setStatus', 'thinking')
      commit('setInterim', '')
      commit('setError', null)
      commit('setLevel', 0)
      abortTurn()
      turnAbort = new AbortController()

      const userMessage = {
        role: 'user',
        content: value,
        doc_id: newMessageId(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        user: $storex.api.user?.username,
        done: true,
        meta_data: {
          voice: {
            session_id: state.session.session_id,
            language: state.language,
            source: 'voice'
          }
        }
      }

      try {
        await $storex.chats.addMessage({ chat, message: userMessage })
        $storex.chats.startChatProcessing({ chatId: chat.id })
        await $storex.projects.chatWihProject(chat)
        dispatch('startProgressNarration', {
          chatId: chat.id,
          userMessageId: userMessage.doc_id
        })
        const reply = await waitForAssistantReply({
          store: $storex,
          chatId: chat.id,
          userMessageId: userMessage.doc_id,
          signal: turnAbort.signal
        })
        await dispatch('stopProgressNarration')
        commit('setLastReply', reply)
        if (reply?.error) {
          await dispatch('announce', `The task failed: ${reply.error}`)
        } else {
          await dispatch('speak', { text: reply?.content || 'Done. The answer is in the chat.' })
        }
        try {
          await $storex.api.voice.recordTurn(state.session.session_id, {
            user_message_id: userMessage.doc_id,
            assistant_message_id: reply?.doc_id || null,
            user_text: value,
            assistant_text: reply?.content || ''
          })
        } catch (recordError) {
          console.warn('[voice] could not record the turn', recordError)
        }
        // Always refresh the session: a tool may have requested a switch even
        // when recording the turn failed.
        try {
          const refreshed = await $storex.api.voice.getSession(state.session.session_id)
          if (refreshed?.session) {
            commit('setSession', refreshed.session)
            commit('setPendingSwitch', refreshed.session.pending_switch || null)
          }
        } catch (sessionError) {
          console.warn('[voice] could not refresh the call session', sessionError)
        }
        // A tool may have asked to move the call; apply it after the answer.
        if (state.pendingSwitch?.chat_id) {
          const target = state.pendingSwitch
          commit('setPendingSwitch', null)
          await dispatch('switchToChat', {
            chat: { id: target.chat_id, name: target.chat_name },
            reason: target.reason || 'assistant requested'
          })
        }
        return reply
      } catch (error) {
        if (error?.name === 'AbortError') return null
        console.error('[voice] turn failed', error)
        commit('setError', error)
        commit('setStatus', 'error')
        if (state.session?.status === 'active') {
          await dispatch('announce', `Something went wrong: ${error.message || error}`)
          await dispatch('startListening')
        }
        return null
      } finally {
        await dispatch('stopProgressNarration')
        turnAbort = null
      }
    },

    /** Typed fallback for browsers without a working microphone. */
    async sendTyped({ dispatch }, text) {
      return dispatch('sendTranscript', text)
    },

    async setLanguage({ commit, state, dispatch }, language) {
      const previous = state.language
      commit('setLanguage', language)
      commit('setLanguageManual', true)
      if (state.session?.status === 'active') {
        try {
          const response = await $storex.api.voice.setLanguage(
            state.session.session_id,
            language
          )
          if (response?.session) commit('setSession', response.session)
        } catch (error) {
          commit('setLanguage', previous)
          $storex.ui.addNotification({
            text: `Could not change the call language: ${error.message || error}`,
            type: 'error'
          })
          return false
        }
        if (state.status === 'speaking') {
          stopBargeMonitor()
          dispatch('startBargeMonitor')
        }
        if (capture) {
          disposeCapture()
          commit('setCaptureMode', null)
          await dispatch('startListening')
        }
      }
      return true
    },

    async toggleCaptions({ state, commit }) {
      commit('setCaptions', !state.captions)
    },

    async minimize({ commit }) {
      commit('setMinimized', true)
      commit('setOpen', false)
    },

    async restore({ commit }) {
      commit('setMinimized', false)
      commit('setOpen', true)
    },

    /** Close the panel; an active call keeps running in the floating pill. */
    close({ state, commit, dispatch }) {
      if (state.status === 'connecting' && state.session?.status !== 'active') {
        // Cancel the in-flight start; startCall ends the abandoned session.
        startAttempt++
        commit('setOpen', false)
        commit('setMinimized', false)
        commit('setStatus', 'idle')
        commit('setError', null)
        commit('setLanguageManual', false)
        return
      }
      if (state.session?.status === 'active') {
        commit('setMinimized', true)
        commit('setOpen', false)
        return
      }
      commit('setOpen', false)
      commit('setMinimized', false)
      commit('setStatus', 'idle')
      commit('setLanguageManual', false)
    },

    /** End the call, optionally reading a short goodbye. */
    async endCall({ state, commit, dispatch }, { reason = '', announce = false, keepOpen = false } = {}) {
      const session = state.session
      if (!session || session.status !== 'active') {
        startAttempt++
        clearTimer()
        commit('setOpen', keepOpen)
        commit('setMinimized', false)
        commit('setStatus', 'idle')
        commit('setLanguageManual', false)
        return
      }
      startAttempt++
      await dispatch('stopProgressNarration')
      abortTurn()
      disposeCapture()
      disposePlayer()
      player = null
      capture = null
      clearTimer()
      commit('setStatus', 'ended')
      commit('setLanguageManual', false)
      commit('setMuted', false)
      commit('setInterim', '')
      commit('setLevel', 0)
      let response = null
      try {
        response = await $storex.api.voice.endSession(session.session_id, {
          reason,
          announce: true
        })
        if (response?.session) commit('setSession', response.session)
      } catch (error) {
        console.warn('[voice] could not end the call cleanly', error)
        $storex.ui.addNotification({
          text: `The call was stopped locally but the server did not acknowledge it: ${error.message || error}`,
          type: 'warning'
        })
      }
      if (announce) {
        try {
          const currentPlayer = createVoicePlayer()
          await currentPlayer.speak('Call ended.')
          currentPlayer.stop()
        } catch (error) {
          /* A missing voice engine must not keep the overlay open. */
        }
      }
      if (!keepOpen) {
        commit('setOpen', false)
        commit('setMinimized', false)
      }
      return response
    }
  }
)
