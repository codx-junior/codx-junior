/**
 * Voice command parsing for the call UI.
 *
 * The commands are deliberately deterministic and tiny: they run on the final
 * transcript BEFORE the message is sent to the model, so "switch to the
 * payments task" changes the call immediately instead of waiting for a full
 * AI turn. Everything that is not a recognised command goes to the normal chat
 * pipeline.
 *
 * Pure module (no browser/DOM): unit tested in `tests/voiceCommands.test.mjs`.
 */

const SWITCH_PREFIXES = [
  'switch',
  'change',
  'move',
  'take',
  'bring',
  'go',
  'cambia',
  'cambiar',
  'mueve',
  'mover',
  'lleva',
  'llevame',
  've'
]

// Prepositions that introduce the destination. `go to` and `switch over to`
// are the common spoken forms; Spanish uses `a`.
const SWITCH_PREPOSITIONS = ['over to', 'back to', 'into', 'to', 'a']

const END_PATTERNS = [
  /^(?:please\s+)?(?:end|finish|stop|close|hang\s*up|hangup)\s+(?:the\s+)?(?:voice\s+)?(?:call|conversation|chat)?$/i,
  /^(?:please\s+)?hang\s*up$/i,
  /^(?:please\s+)?(?:termina|terminar|finaliza|finalizar|cuelga|colgar)\s+(?:la\s+)?(?:llamada|conversacion|conversación)?$/i,
  /^(?:adios|adiós|bye|goodbye)$/i
]

const NOISE = /[“”"¿?¡!.,;:]+/g

/**
 * Normalise a transcript for command matching.
 * @param {string} text
 * @returns {string}
 */
export function normalizeTranscript(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(NOISE, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Remove conversational filler from the destination.
 * @param {string} value
 * @returns {string}
 */
export function cleanChatQuery(value) {
  return String(value || '')
    .replace(/^(?:the|el|la|los|las)\s+/i, '')
    .replace(/^(?:voice\s+)?(?:chat|conversation|task|call)\s+/i, '')
    .replace(/\s+(?:chat|conversation|please|por favor)$/i, '')
    .replace(/[“”"']/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Parse a "switch the conversation to <chat>" instruction.
 * @param {string} transcript
 * @returns {{matched: boolean, query: string}}
 */
export function parseSwitchCommand(transcript) {
  const text = normalizeTranscript(transcript)
  if (!text) return { matched: false, query: '' }

  // Drop an optional politeness/verb prefix, then require a destination.
  for (const prefix of SWITCH_PREFIXES) {
    if (!text.startsWith(`${prefix} `) && text !== prefix) continue
    let rest = text.slice(prefix.length).trim()
    // Optional indirect object: "take me to", "bring me to".
    rest = rest.replace(/^me\s+/i, '')
    // Optional object: "the conversation", "the voice call", "the chat".
    rest = rest.replace(
      /^(?:the\s+|la\s+|el\s+)?(?:voice\s+)?(?:conversation|call|chat|conversacion|llamada|charla)\s*/i,
      ''
    )
    // The verb `go` requires a preposition; `switch`/`change` also accept one.
    let foundPreposition = ''
    for (const preposition of SWITCH_PREPOSITIONS) {
      const marker = `${preposition} `
      if (rest.startsWith(marker)) {
        foundPreposition = preposition
        rest = rest.slice(marker.length).trim()
        break
      }
    }
    if (!foundPreposition) {
      // "go payments task" is not a switch; "switch payments task" is
      // ambiguous in speech, so require a preposition for every verb.
      continue
    }
    const query = cleanChatQuery(rest)
    if (query) return { matched: true, query }
  }
  return { matched: false, query: '' }
}

/**
 * Parse "end the call" / "hang up" commands.
 * @param {string} transcript
 * @returns {boolean}
 */
export function parseEndCommand(transcript) {
  const text = normalizeTranscript(transcript)
    .replace(/\s+(?:please|por favor)$/i, '')
    .trim()
  return END_PATTERNS.some(pattern => pattern.test(text))
}

/**
 * Score a chat against a spoken query. Higher is better; 0 means no match.
 * @param {string} query
 * @param {{id?: string, name?: string}} chat
 * @returns {number}
 */
export function scoreChatMatch(query, chat) {
  const value = normalizeTranscript(query)
  if (!value || !chat) return 0
  const id = String(chat.id || '').toLowerCase()
  const name = normalizeTranscript(chat.name || '')
  if (!name && !id) return 0
  if (value === id) return 100
  if (value === name) return 90
  if (name.startsWith(value)) return 70
  if (name.includes(value)) return 55
  const words = value.split(' ').filter(Boolean)
  if (words.length > 1 && words.every(word => name.includes(word))) return 45
  return 0
}

/**
 * Resolve a spoken query to one chat.
 *
 * - exact id / exact name wins,
 * - otherwise the best prefix/substring match wins,
 * - an ambiguous set of equally good matches is reported, never guessed.
 *
 * @param {Array<{id?: string, name?: string}>} chats
 * @param {string} query
 * @returns {{chat: object|null, candidates: object[], ambiguous: boolean}}
 */
export function resolveChatQuery(chats, query) {
  const scored = (Array.isArray(chats) ? chats : [])
    .map(chat => ({ chat, score: scoreChatMatch(query, chat) }))
    .filter(entry => entry.score > 0)
    .sort((a, b) => b.score - a.score)
  if (!scored.length) return { chat: null, candidates: [], ambiguous: false }
  const best = scored[0]
  const candidates = scored.map(entry => entry.chat)
  const ties = scored.filter(entry => entry.score === best.score)
  return {
    chat: ties.length === 1 ? best.chat : null,
    candidates,
    ambiguous: ties.length > 1
  }
}

export default {
  cleanChatQuery,
  normalizeTranscript,
  parseEndCommand,
  parseSwitchCommand,
  resolveChatQuery,
  scoreChatMatch
}
