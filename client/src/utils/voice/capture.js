/**
 * Microphone capture for the voice call.
 *
 * Two engines, one interface:
 *
 * 1. **SpeechRecognition** (Chrome/Edge): live transcription with interim
 *    results; the call feels instant and needs no server round trip.
 * 2. **MediaRecorder + server transcription** (Firefox/Safari or browsers
 *    without SpeechRecognition): records one utterance at a time, detects the
 *    end of speech with the mic level, uploads the clip to
 *    `POST /api/voice/transcribe` and emits the resulting text.
 *
 * The interface is intentionally small: `start`, `pause`, `resume`, `stop`.
 * `pause` is used while the assistant speaks so the microphone does not listen
 * to the speaker (echo) and so the browser does not transcribe codx-junior's
 * own voice.
 */

import { createMicLevelMonitor } from '@/utils/audioLevel'

const RECOGNITION_RESTART_MS = 250
const FINAL_DEBOUNCE_MS = 500
const SILENCE_MS = 1100
const MIN_UTTERANCE_MS = 600
const MAX_UTTERANCE_MS = 30000
const SPEECH_LEVEL = 10
const MIN_BLOB_BYTES = 1000

/**
 * Detect the browser engines available for a call.
 * @param {Window} [win]
 * @param {Navigator} [nav]
 * @returns {{speechRecognition: boolean, speechSynthesis: boolean, microphone: boolean, mediaRecorder: boolean}}
 */
export function detectVoiceSupport(win = globalThis, nav = globalThis?.navigator) {
  const SpeechRecognition = win?.SpeechRecognition || win?.webkitSpeechRecognition
  return {
    speechRecognition: Boolean(SpeechRecognition),
    speechSynthesis: Boolean(win?.speechSynthesis),
    microphone: Boolean(nav?.mediaDevices?.getUserMedia),
    mediaRecorder: Boolean(win?.MediaRecorder)
  }
}

/**
 * Best recording mime type the browser supports.
 * @returns {string}
 */
export function pickRecordingMimeType(win = globalThis) {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4'
  ]
  return (
    candidates.find(type => win?.MediaRecorder?.isTypeSupported?.(type)) || ''
  )
}

/**
 * Create the microphone capture used by the call overlay.
 *
 * @param {object} options
 * @param {string} [options.language]
 * @param {(blob: Blob) => Promise<string>} [options.serverTranscribe]
 * @param {(text: string) => void} [options.onFinal]
 * @param {(text: string) => void} [options.onInterim]
 * @param {(level: number) => void} [options.onLevel]
 * @param {(error: Error) => void} [options.onError]
 * @param {(state: string) => void} [options.onStateChange]
 * @returns {{start: Function, pause: Function, resume: Function, stop: Function, getMode: Function, isRunning: Function}}
 */
export function createVoiceCapture({
  language = 'en-US',
  serverTranscribe = null,
  onFinal = () => {},
  onInterim = () => {},
  onLevel = () => {},
  onError = () => {},
  onStateChange = () => {}
} = {}) {
  const win = globalThis
  const nav = win?.navigator
  const support = detectVoiceSupport(win, nav)
  const mode = support.speechRecognition ? 'recognition' : 'recorder'

  let running = false
  let paused = false

  // Recognition state
  let recognition = null
  let restartTimer = null
  let finalTimer = null
  let pendingFinal = ''
  let lastRecognitionError = ''

  // Recorder state
  let stream = null
  let recorder = null
  let recorderMimeType = ''
  let chunks = []
  let levelMonitor = null
  let utteranceStartedAt = 0
  let lastVoiceAt = 0
  let speechDetected = false
  let discarding = false
  let maxUtteranceTimer = null
  let transcribing = false

  const setState = state => {
    try { onStateChange(state) } catch (error) { /* listener error */ }
  }

  const fail = error => {
    try { onError(error instanceof Error ? error : new Error(String(error))) }
    catch (listenerError) { /* listener error */ }
  }

  // ── SpeechRecognition ────────────────────────────────────────────────

  function flushFinal() {
    if (finalTimer) clearTimeout(finalTimer)
    finalTimer = null
    const text = pendingFinal.trim()
    pendingFinal = ''
    if (text) onFinal(text)
  }

  function scheduleFinalFlush() {
    if (finalTimer) clearTimeout(finalTimer)
    finalTimer = setTimeout(flushFinal, FINAL_DEBOUNCE_MS)
  }

  function startRecognition() {
    const SpeechRecognition =
      win?.SpeechRecognition || win?.webkitSpeechRecognition
    if (!SpeechRecognition) {
      fail(new Error('Speech recognition is not available in this browser'))
      return
    }
    try {
      recognition = new SpeechRecognition()
      recognition.lang = language
      recognition.continuous = true
      recognition.interimResults = true
      recognition.maxAlternatives = 1

      recognition.onresult = event => {
        let interim = ''
        let final = ''
        for (let index = event.resultIndex; index < event.results.length; index++) {
          const result = event.results[index]
          const text = result?.[0]?.transcript || ''
          if (result.isFinal) final += `${text} `
          else interim += text
        }
        if (interim) onInterim(interim.trim())
        if (final.trim()) {
          pendingFinal += `${final.trim()} `
          onInterim('')
          scheduleFinalFlush()
        }
      }

      recognition.onerror = event => {
        const reason = event?.error || 'unknown'
        lastRecognitionError = reason
        if (reason === 'not-allowed' || reason === 'service-not-allowed') {
          running = false
          fail(new Error('Microphone permission was denied'))
          return
        }
        // `no-speech`, `aborted` and transient `network` errors are normal:
        // `onend` restarts recognition.
        if (reason === 'network' && !paused) {
          fail(new Error('Speech recognition network error'))
        }
      }

      recognition.onend = () => {
        recognition = null
        if (!running || paused) return
        if (restartTimer) clearTimeout(restartTimer)
        restartTimer = setTimeout(() => {
          restartTimer = null
          if (running && !paused) startRecognition()
        }, RECOGNITION_RESTART_MS)
      }

      recognition.start()
      setState('listening')
    } catch (error) {
      recognition = null
      fail(error)
    }
  }

  function stopRecognition({ abort = false } = {}) {
    if (restartTimer) {
      clearTimeout(restartTimer)
      restartTimer = null
    }
    if (finalTimer) {
      clearTimeout(finalTimer)
      finalTimer = null
    }
    if (abort) pendingFinal = ''
    const current = recognition
    recognition = null
    if (current) {
      try { current.abort() } catch (error) { /* already stopped */ }
    }
  }

  // ── MediaRecorder fallback ───────────────────────────────────────────

  function clearRecorderTimers() {
    if (maxUtteranceTimer) {
      clearTimeout(maxUtteranceTimer)
      maxUtteranceTimer = null
    }
  }

  function stopStream() {
    if (stream) {
      try { stream.getTracks().forEach(track => track.stop()) } catch (error) { /* ignore */ }
      stream = null
    }
    if (levelMonitor) {
      try { levelMonitor.stop() } catch (error) { /* ignore */ }
      levelMonitor = null
    }
    onLevel(0)
  }

  function restartRecorder(delay = 200) {
    if (!running || paused) return
    setTimeout(() => {
      if (running && !paused) recordOnce()
    }, delay)
  }

  async function handleRecorderStop() {
    const currentChunks = chunks
    chunks = []
    const wasDiscarding = discarding
    discarding = false
    const hadSpeech = speechDetected
    speechDetected = false
    clearRecorderTimers()

    if (!running || paused || wasDiscarding) return
    if (!hadSpeech) {
      restartRecorder(50)
      return
    }
    const blob = new Blob(currentChunks, {
      type: recorderMimeType || 'audio/webm'
    })
    if (blob.size < MIN_BLOB_BYTES) {
      restartRecorder(50)
      return
    }
    if (!serverTranscribe) {
      fail(
        new Error(
          'This browser cannot transcribe speech and no server transcription ' +
            'backend is configured. You can type instead.'
        )
      )
      setState('error')
      return
    }
    transcribing = true
    setState('transcribing')
    try {
      const text = await serverTranscribe(blob)
      if (text && text.trim()) onFinal(text.trim())
    } catch (error) {
      fail(error)
    } finally {
      transcribing = false
    }
    restartRecorder(50)
  }

  function stopRecorder({ discard = false } = {}) {
    clearRecorderTimers()
    discarding = discard
    const current = recorder
    recorder = null
    if (current && current.state !== 'inactive') {
      try { current.stop() } catch (error) { /* already stopped */ }
    }
  }

  function recordOnce() {
    if (!running || paused) return
    if (!stream) {
      openStream().then(recordOnce).catch(error => fail(error))
      return
    }
    if (!win?.MediaRecorder) {
      fail(new Error('Audio recording is not available in this browser'))
      return
    }
    chunks = []
    speechDetected = false
    utteranceStartedAt = Date.now()
    lastVoiceAt = Date.now()
    const mimeType = pickRecordingMimeType(win)
    try {
      recorder = new win.MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined
      )
      recorderMimeType = recorder.mimeType || mimeType || 'audio/webm'
    } catch (error) {
      fail(error)
      return
    }
    recorder.ondataavailable = event => {
      if (event.data?.size) chunks.push(event.data)
    }
    recorder.onstop = () => handleRecorderStop()
    try {
      recorder.start(250)
      setState('listening')
    } catch (error) {
      fail(error)
      return
    }
    maxUtteranceTimer = setTimeout(() => {
      if (recorder) stopRecorder()
    }, MAX_UTTERANCE_MS)
  }

  function handleLevel(level) {
    onLevel(level)
    if (!running || paused || !recorder) return
    const now = Date.now()
    if (level >= SPEECH_LEVEL) {
      speechDetected = true
      lastVoiceAt = now
    }
    if (
      speechDetected &&
      now - lastVoiceAt > SILENCE_MS &&
      now - utteranceStartedAt > MIN_UTTERANCE_MS
    ) {
      stopRecorder()
    }
  }

  async function openStream() {
    if (stream) return stream
    if (!nav?.mediaDevices?.getUserMedia) {
      throw new Error('Microphone access is not available in this browser')
    }
    stream = await nav.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      },
      video: false
    })
    levelMonitor = createMicLevelMonitor({
      onLevel: handleLevel,
      threshold: SPEECH_LEVEL
    })
    levelMonitor.start(stream)
    return stream
  }

  async function startRecorder() {
    if (transcribing) return
    try {
      await openStream()
      if (!running || paused) {
        stopStream()
        return
      }
      recordOnce()
    } catch (error) {
      const denied =
        error?.name === 'NotAllowedError' || error?.name === 'SecurityError'
      fail(
        new Error(
          denied
            ? 'Microphone permission was denied'
            : `Could not open the microphone: ${error?.message || error}`
        )
      )
    }
  }

  // ── public interface ─────────────────────────────────────────────────

  return {
    getMode() {
      return mode
    },
    isRunning() {
      return running && !paused
    },
    async start() {
      if (running && !paused) return
      running = true
      paused = false
      if (mode === 'recognition') startRecognition()
      else await startRecorder()
    },
    pause() {
      if (!running || paused) return
      paused = true
      onInterim('')
      if (mode === 'recognition') stopRecognition({ abort: false })
      else {
        stopRecorder({ discard: true })
        stopStream()
      }
      setState('paused')
    },
    async resume() {
      if (!running || !paused) return
      paused = false
      if (mode === 'recognition') startRecognition()
      else await startRecorder()
    },
    stop() {
      running = false
      paused = false
      if (mode === 'recognition') stopRecognition({ abort: true })
      else {
        stopRecorder({ discard: true })
        stopStream()
      }
      onInterim('')
      setState('idle')
    }
  }
}

export default { createVoiceCapture, detectVoiceSupport, pickRecordingMimeType }
