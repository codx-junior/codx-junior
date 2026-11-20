/**
 * Mixed meeting recorder.
 *
 * The host records the meeting from its own browser: the local microphone, the
 * remote WebRTC streams and the synthesized profile voices are mixed into ONE
 * audio track through the Web Audio API. The resulting blob is uploaded when
 * the meeting ends, so the summary page can replay the actual call.
 *
 * The mixer only sends audio to a `MediaStreamDestination` (the recorder): it
 * never connects the microphone/remote streams to the speakers, which would
 * duplicate the call audio or create feedback. A media element (the profile
 * TTS player) is connected to both the recorder and the speakers because that
 * element would otherwise be muted by `createMediaElementSource`.
 *
 * Everything degrades gracefully: a browser without `AudioContext` or
 * `MediaRecorder` simply yields no recording; the meeting still works.
 */

import { pickRecordingMimeType } from '@/utils/voice/capture'

export function createMeetingRecorder() {
  let context = null
  let destination = null
  let recorder = null
  let chunks = []
  let startedAt = 0
  let mimeType = ''
  const sources = new Map() // id -> MediaStreamAudioSourceNode
  const elements = new Map() // id -> { source, element }

  function supported() {
    return Boolean(
      (globalThis.AudioContext || globalThis.webkitAudioContext) &&
      globalThis.MediaRecorder &&
      globalThis.MediaStream
    )
  }

  async function start() {
    if (!supported()) return false
    if (recorder) return true
    const AudioContextCtor =
      globalThis.AudioContext || globalThis.webkitAudioContext
    context = new AudioContextCtor()
    try {
      await context.resume()
    } catch (error) {
      // resume() may need a user gesture; recording still starts suspended.
    }
    destination = context.createMediaStreamDestination()
    mimeType = pickRecordingMimeType(globalThis) || ''
    try {
      recorder = new MediaRecorder(
        destination.stream,
        mimeType ? { mimeType } : undefined
      )
    } catch (error) {
      console.warn('[meet] MediaRecorder unavailable', error)
      await close()
      return false
    }
    chunks = []
    recorder.ondataavailable = event => {
      if (event.data?.size) chunks.push(event.data)
    }
    recorder.start(1000)
    startedAt = Date.now()
    return true
  }

  function attachStream(id, stream) {
    if (!context || !destination || !stream || !id) return
    if (sources.has(id)) detachStream(id)
    try {
      const source = context.createMediaStreamSource(stream)
      source.connect(destination)
      sources.set(id, source)
    } catch (error) {
      console.warn('[meet] could not record stream', id, error)
    }
  }

  function detachStream(id) {
    const source = sources.get(id)
    if (!source) return
    try { source.disconnect() } catch (error) { /* ignore */ }
    sources.delete(id)
  }

  /**
   * Route a TTS `<audio>` element into the recording AND the speakers.
   * `createMediaElementSource` mutes the element, so both connections are
   * required.
   */
  function attachAudioElement(id, element) {
    if (!context || !destination || !element || !id) return
    detachAudioElement(id)
    try {
      const source = context.createMediaElementSource(element)
      source.connect(destination)
      source.connect(context.destination)
      elements.set(id, { source, element })
    } catch (error) {
      console.warn('[meet] could not record the profile voice', error)
    }
  }

  function detachAudioElement(id) {
    const entry = elements.get(id)
    if (!entry) return
    try { entry.source.disconnect() } catch (error) { /* ignore */ }
    elements.delete(id)
  }

  async function close() {
    sources.forEach(source => { try { source.disconnect() } catch (e) { /* ignore */ } })
    sources.clear()
    elements.forEach(entry => { try { entry.source.disconnect() } catch (e) { /* ignore */ } })
    elements.clear()
    if (context) {
      try { await context.close() } catch (error) { /* ignore */ }
    }
    context = null
    destination = null
    recorder = null
  }

  function stop() {
    if (!recorder) return Promise.resolve(null)
    const current = recorder
    const duration = startedAt ? (Date.now() - startedAt) / 1000 : 0
    const type = current.mimeType || mimeType || 'audio/webm'
    return new Promise(resolve => {
      current.onstop = async () => {
        const blob = new Blob(chunks, { type })
        chunks = []
        await close()
        resolve({ blob, mime: type, duration })
      }
      try {
        current.stop()
      } catch (error) {
        close()
        resolve(null)
      }
    })
  }

  async function cancel() {
    if (recorder) {
      try { recorder.stop() } catch (error) { /* ignore */ }
    }
    chunks = []
    await close()
  }

  return {
    supported,
    start,
    attachStream,
    detachStream,
    attachAudioElement,
    detachAudioElement,
    stop,
    cancel,
    isRecording: () => Boolean(recorder)
  }
}

export default { createMeetingRecorder }
