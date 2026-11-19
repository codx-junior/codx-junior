/**
 * Speech output for the voice call.
 *
 * Default: the browser's `SpeechSynthesis` (free, no configuration, works
 * offline). When the server has a configured TTS provider the store can pass a
 * `serverAudio` function; if it fails for any reason the player falls back to
 * the browser voice instead of breaking the call.
 *
 * The player speaks one short chunk at a time because several mobile engines
 * cut long utterances, and it exposes a synchronous `stop()` so a switch/hang
 * up can silence the assistant immediately.
 */

import { splitForSpeech } from '@/utils/voice/speechText'

const VOICE_LOAD_TIMEOUT_MS = 1200

function browserSynth(win = globalThis) {
  return win?.speechSynthesis || null
}

/**
 * Wait for the voice list to be populated (`voiceschanged` is async on Chrome).
 * @param {SpeechSynthesis} synth
 * @returns {Promise<SpeechSynthesisVoice[]>}
 */
function loadVoices(synth) {
  if (!synth) return Promise.resolve([])
  const current = synth.getVoices?.() || []
  if (current.length) return Promise.resolve(current)
  return new Promise(resolve => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      try { synth.removeEventListener?.('voiceschanged', finish) } catch (error) { /* ignore */ }
      resolve(synth.getVoices?.() || [])
    }
    try { synth.addEventListener?.('voiceschanged', finish) } catch (error) { /* ignore */ }
    setTimeout(finish, VOICE_LOAD_TIMEOUT_MS)
  })
}

/**
 * Pick the voice that best matches the requested language / name.
 * @param {SpeechSynthesisVoice[]} voices
 * @param {string} language
 * @param {string} voiceName
 * @returns {SpeechSynthesisVoice|null}
 */
export function selectVoice(voices, language = '', voiceName = '') {
  const list = Array.isArray(voices) ? voices : []
  if (!list.length) return null
  if (voiceName) {
    const exact = list.find(voice => voice.name === voiceName)
    if (exact) return exact
  }
  const wanted = String(language || '').toLowerCase()
  if (wanted) {
    const exactLang = list.find(voice => (voice.lang || '').toLowerCase() === wanted)
    if (exactLang) return exactLang
    const base = wanted.split('-')[0]
    const sameBase = list.find(voice =>
      (voice.lang || '').toLowerCase().startsWith(`${base}-`) ||
      (voice.lang || '').toLowerCase() === base
    )
    if (sameBase) return sameBase
  }
  return list.find(voice => voice.default) || list[0]
}

/**
 * Create a speech player.
 *
 * @param {object} options
 * @param {(state: string) => void} [options.onStateChange]
 * @returns {object} `{speak, stop, isSpeaking, isSupported}`
 */
export function createVoicePlayer({ onStateChange = () => {} } = {}) {
  const synth = browserSynth()
  let generation = 0
  let speaking = false
  let audio = null
  let audioUrl = null
  let audioResolve = null

  const setState = state => {
    try { onStateChange(state) } catch (error) { /* listener error */ }
  }

  function cleanupAudio() {
    if (audio) {
      try { audio.pause() } catch (error) { /* ignore */ }
      audio = null
    }
    if (audioUrl) {
      try { URL.revokeObjectURL(audioUrl) } catch (error) { /* ignore */ }
      audioUrl = null
    }
  }

  function stop() {
    generation++
    // A paused <audio> never fires `ended`: resolve the pending play promise so
    // `speak()` can finish and the call returns to listening.
    if (audioResolve) {
      const resolve = audioResolve
      audioResolve = null
      resolve()
    }
    cleanupAudio()
    if (synth) {
      try { synth.cancel() } catch (error) { /* ignore */ }
    }
    if (speaking) {
      speaking = false
      setState('idle')
    }
  }

  async function playBlob(blob) {
    cleanupAudio()
    if (!blob || !blob.size) throw new Error('empty audio')
    audioUrl = URL.createObjectURL(blob)
    audio = new Audio(audioUrl)
    await new Promise((resolve, reject) => {
      audioResolve = resolve
      audio.onended = () => {
        audioResolve = null
        resolve()
      }
      audio.onerror = () => {
        audioResolve = null
        reject(new Error('could not play the synthesized audio'))
      }
      const promise = audio.play()
      if (promise?.catch) {
        promise.catch(error => {
          audioResolve = null
          reject(error)
        })
      }
    })
  }

  async function playBrowser(text, { language, voiceName, rate }, isCancelled) {
    if (!synth) throw new Error('Speech synthesis is not available in this browser')
    const chunks = splitForSpeech(text)
    if (!chunks.length) return
    const voices = await loadVoices(synth)
    const voice = selectVoice(voices, language, voiceName)
    for (const chunk of chunks) {
      if (isCancelled()) return
      await new Promise((resolve, reject) => {
        const utterance = new SpeechSynthesisUtterance(chunk)
        utterance.lang = language || voice?.lang || 'en-US'
        utterance.rate = Number(rate) > 0 ? Number(rate) : 1
        if (voice) utterance.voice = voice
        utterance.onend = resolve
        utterance.onerror = event => {
          // `canceled`/`interrupted` happen on stop(); not an error.
          const reason = event?.error || ''
          if (reason === 'canceled' || reason === 'interrupted' || isCancelled()) {
            resolve()
          } else {
            reject(new Error(`speech synthesis failed: ${reason || 'unknown'}`))
          }
        }
        try { synth.speak(utterance) } catch (error) { reject(error) }
      })
    }
  }

  return {
    isSupported() {
      return Boolean(synth)
    },
    isSpeaking() {
      return speaking
    },
    stop,
    /**
     * Speak `text`.
     *
     * @param {string} text - already-sanitised speech text.
     * @param {object} [options]
     * @param {string} [options.language]
     * @param {string} [options.voiceName]
     * @param {number} [options.rate]
     * @param {() => Promise<Blob|null>} [options.serverAudio] - optional server TTS.
     * @returns {Promise<void>}
     */
    async speak(text, options = {}) {
      const value = String(text || '').trim()
      if (!value) return
      stop()
      const myGeneration = ++generation
      const isCancelled = () => myGeneration !== generation
      speaking = true
      setState('speaking')
      try {
        if (typeof options.serverAudio === 'function') {
          try {
            const blob = await options.serverAudio(value)
            if (isCancelled()) return
            if (blob) {
              await playBlob(blob)
              return
            }
          } catch (error) {
            // A server voice is optional: fall through to the browser one.
            console.warn('[voice] server TTS unavailable, using browser voice', error)
          }
        }
        if (isCancelled()) return
        await playBrowser(value, options, isCancelled)
      } finally {
        cleanupAudio()
        if (!isCancelled()) {
          speaking = false
          setState('idle')
        }
      }
    }
  }
}

export default { createVoicePlayer, selectVoice }
