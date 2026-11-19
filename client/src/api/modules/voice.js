/**
 * Voice conversation API.
 *
 * The conversation itself uses the normal chat endpoints. This module only
 * covers call lifecycle, chat switching and the optional server speech
 * backends (`/api/voice/*`, see `codx/junior/api/voice.py`).
 */

export const voiceModule = (API) => ({
  /** Server speech capabilities (transcription / synthesis). */
  capabilities() {
    return API.get('/api/voice/capabilities')
  },

  /** Recent calls, newest first. */
  listSessions({ status, chatId, limit = 20 } = {}) {
    const params = new URLSearchParams()
    if (status) params.append('status', status)
    if (chatId) params.append('chat_id', chatId)
    if (limit) params.append('limit', limit)
    const qs = params.toString()
    return API.get(`/api/voice/sessions${qs ? `?${qs}` : ''}`)
  },

  /** Start, continue or resume a call on `chat_id`. */
  createSession(payload) {
    return API.post('/api/voice/sessions', payload)
  },

  getSession(sessionId) {
    return API.get(`/api/voice/sessions/${encodeURIComponent(sessionId)}`)
  },

  /** Move an active call to another chat (backend adds the notices). */
  switchSession(sessionId, payload) {
    return API.post(
      `/api/voice/sessions/${encodeURIComponent(sessionId)}/switch`,
      payload
    )
  },

  /** Change the speech language of a running call. */
  setLanguage(sessionId, language) {
    return API.post(
      `/api/voice/sessions/${encodeURIComponent(sessionId)}/language`,
      { language }
    )
  },

  endSession(sessionId, payload = {}) {
    return API.post(
      `/api/voice/sessions/${encodeURIComponent(sessionId)}/end`,
      payload
    )
  },

  /** Record which chat messages belong to a completed speech turn. */
  recordTurn(sessionId, payload) {
    return API.post(
      `/api/voice/sessions/${encodeURIComponent(sessionId)}/turns`,
      payload
    )
  },

  /**
   * Upload one recorded utterance to the optional local transcription backend.
   * @param {Blob} blob
   * @param {string} language
   * @returns {Promise<{text: string}>}
   */
  transcribe(blob, language = 'en-US') {
    const form = new FormData()
    const type = String(blob.type || 'audio/webm').split(';')[0]
    const extension = type.includes('ogg')
      ? 'ogg'
      : type.includes('mp4')
        ? 'm4a'
        : type.includes('wav')
          ? 'wav'
          : 'webm'
    form.append('file', blob, `utterance.${extension}`)
    form.append('language', language)
    return API.post('/api/voice/transcribe', form)
  },

  /**
   * Synthesize text with the configured server TTS provider.
   * @returns {Promise<Blob>}
   */
  speech({ text, provider = '', voiceId = '', language = null, speed = 1 }) {
    return API.connection.postBlob('/api/voice/speech', {
      text,
      provider,
      voice_id: voiceId,
      language,
      speed
    })
  }
})

export default voiceModule
