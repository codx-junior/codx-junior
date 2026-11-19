/**
 * Barge-in level rules.
 *
 * The microphone monitor runs while the assistant speaks. It learns the
 * ambient level (usually the assistant's own output surviving echo
 * cancellation) and requires the user to speak clearly above it, for a
 * sustained period, before stopping playback.
 *
 * Pure module so the thresholds are unit tested.
 */

export const MIN_BARGE_LEVEL = 9
export const AMBIENT_MARGIN = 5
export const MAX_BARGE_LEVEL = 24
export const BARGE_HOLD_MS = 400
export const BARGE_WARMUP_MS = 600
export const STRONG_BARGE_LEVEL = 32
export const STRONG_BARGE_HOLD_MS = 250

/**
 * Level that counts as a barge-in for a known ambient level.
 * @param {number} ambientLevel
 * @returns {number}
 */
export function bargeInThreshold(ambientLevel) {
  return Math.min(
    MAX_BARGE_LEVEL,
    Math.max(MIN_BARGE_LEVEL, Number(ambientLevel || 0) + AMBIENT_MARGIN)
  )
}

/**
 * Whether a level is above the barge-in threshold.
 * @param {number} level
 * @param {number} ambientLevel
 * @returns {boolean}
 */
export function isBargeInLevel(level, ambientLevel) {
  return Number(level || 0) >= bargeInThreshold(ambientLevel)
}

/**
 * A loud, unmistakable voice, independent of the ambient threshold.
 * @param {number} level
 * @returns {boolean}
 */
export function isStrongBargeInLevel(level) {
  return Number(level || 0) >= STRONG_BARGE_LEVEL
}

export default {
  bargeInThreshold,
  isAssistantEcho,
  isBargeInLevel,
  isStrongBargeInLevel,
  speechWords
}

/**
 * Words that matter for echo comparison (case/accent/punctuation insensitive).
 * @param {string} text
 * @returns {string[]}
 */
export function speechWords(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9\s']+/g, ' ')
    .split(/\s+/)
    .map(word => word.replace(/^'+|'+$/g, ''))
    .filter(word => word.length > 2)
}

/**
 * Whether a recognition transcript is most likely the assistant's own voice
 * leaking back through the speakers (so it must NOT trigger barge-in).
 *
 * A short command ("stop", "wait") is never considered echo: a one-word
 * utterance is exactly what a barge-in sounds like.
 *
 * @param {string} transcript
 * @param {string} spokenText
 * @returns {boolean}
 */
export function isAssistantEcho(transcript, spokenText) {
  const heard = speechWords(transcript)
  const spokenWords = speechWords(spokenText)
  if (!spokenWords.length) return false
  const spoken = new Set(spokenWords)
  if (!heard.length) {
    // Short interims ("I am") produce no significant words: compare the raw
    // normalized strings instead of treating them as a user command.
    const normalize = value => String(value || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s']+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    const normalizedHeard = normalize(transcript)
    return Boolean(normalizedHeard && normalize(spokenText).includes(normalizedHeard))
  }
  if (heard.length === 1) {
    // SpeechRecognition interims often start with a single word. If that word
    // belongs to the answer being spoken, it is almost certainly speaker echo.
    return spoken.has(heard[0])
  }
  const overlap = heard.filter(word => spoken.has(word)).length / heard.length
  return overlap >= 0.6
}
