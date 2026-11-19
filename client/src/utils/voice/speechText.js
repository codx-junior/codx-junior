/**
 * Turn an assistant markdown answer into something that is pleasant to hear.
 *
 * A voice call must never read code fences, tables, URLs, HTML or the endless
 * tool logs of a long answer. The helpers are pure (no DOM) so they are unit
 * tested in `tests/voiceSpeech.test.mjs`.
 */

import { parseResponseOptions } from '../responseOptions.js'

const MAX_PREVIEW_CHARS = 1400
const MAX_CHUNK_CHARS = 220
const CODE_PLACEHOLDER = 'The code is in the chat.'

/**
 * Remove markdown/HTML noise and keep the spoken content.
 *
 * Suggested replies arrive as an `options` JSON block. It must never be read
 * as code: the prose is kept and the option labels are announced at the end,
 * so a voice user hears the choices (the buttons stay available in the chat).
 *
 * @param {string} markdown
 * @returns {string}
 */
export function toSpeechText(markdown) {
  const parsed = parseResponseOptions(String(markdown || ''), { streaming: true })
  let text = parsed.content

  // Fenced code blocks: reading code aloud is useless; keep a short cue.
  text = text.replace(/```[\s\S]*?```/g, ` ${CODE_PLACEHOLDER} `)
  // HTML comments / tags.
  text = text.replace(/<!--[\s\S]*?-->/g, ' ')
  text = text.replace(/<[^>]+>/g, ' ')
  // Images: prefer the alt text.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  // Links: keep the label, drop the URL unless it is the only text.
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  text = text.replace(/https?:\/\/\S+/g, ' link ')
  // Inline code: read its content (usually a command/name).
  text = text.replace(/`([^`]+)`/g, '$1')
  // Markdown emphasis / strikethrough / headings / list markers.
  text = text.replace(/^#{1,6}\s+/gm, '')
  text = text.replace(/^>\s?/gm, '')
  text = text.replace(/^\s*[-*+]\s+/gm, '')
  text = text.replace(/^\s*\d+[.)]\s+/gm, '')
  text = text.replace(/[*_~]{1,3}/g, '')
  // Tables: drop separator rows, turn cells into short pauses.
  text = text
    .split('\n')
    .filter(line => !/^\s*\|?[\s:|-]+\|?\s*$/.test(line))
    .map(line => line.replace(/\s*\|\s*/g, ', '))
    .join('\n')
  // Paths and hashes are not words: leave them but avoid punctuation noise.
  text = text.replace(/[ \t]+/g, ' ')
  text = text.replace(/\n{3,}/g, '\n\n')

  // Announce the choices instead of the JSON block they came in. The buttons
  // remain in the chat for users who tap instead of speak.
  const optionLabels = parsed.options.map(option => option.label).filter(Boolean)
  if (optionLabels.length) {
    text = `${text}\n\nOptions: ${optionLabels.join(', ')}.`
  }

  return text.trim()
}

/**
 * Split a long speech text into engine-friendly chunks.
 *
 * Most `SpeechSynthesis` implementations cut long utterances (or stop after
 * ~15s on some mobile browsers), so the player speaks a sequence of short
 * sentences instead.
 *
 * @param {string} text
 * @param {number} [maxChars]
 * @returns {string[]}
 */
export function splitForSpeech(text, maxChars = MAX_CHUNK_CHARS) {
  const value = String(text || '').replace(/\s+/g, ' ').trim()
  if (!value) return []
  const limit = Math.max(40, Number(maxChars) || MAX_CHUNK_CHARS)
  if (value.length <= limit) return [value]

  const sentences = value.match(/[^.!?…]+[.!?…]*\s*/g) || [value]
  const chunks = []
  let current = ''
  const flush = () => {
    if (current.trim()) chunks.push(current.trim())
    current = ''
  }
  for (const sentence of sentences) {
    if (sentence.length > limit) {
      flush()
      // Very long sentence: split on commas/spaces, never mid-word.
      const words = sentence.split(/\s+/)
      let piece = ''
      for (const word of words) {
        const candidate = piece ? `${piece} ${word}` : word
        if (candidate.length > limit && piece) {
          chunks.push(piece)
          piece = word
        } else {
          piece = candidate
        }
      }
      if (piece) chunks.push(piece)
      continue
    }
    const candidate = current ? `${current} ${sentence.trim()}` : sentence.trim()
    if (candidate.length > limit) {
      flush()
      current = sentence.trim()
    } else {
      current = candidate
    }
  }
  flush()
  return chunks
}

/**
 * Build the spoken version of a (possibly long) assistant answer.
 *
 * Long answers are cut at a sentence boundary and close with a pointer to the
 * chat, so the user can always read the rest.
 *
 * @param {string} markdown
 * @param {number} [maxChars]
 * @returns {string}
 */
export function speechPreview(markdown, maxChars = MAX_PREVIEW_CHARS) {
  const plain = toSpeechText(markdown)
  if (!plain) return ''
  if (plain.length <= maxChars) return plain
  const cut = plain.slice(0, maxChars)
  const boundary = Math.max(
    cut.lastIndexOf('. '),
    cut.lastIndexOf('! '),
    cut.lastIndexOf('? '),
    cut.lastIndexOf('\n')
  )
  const head = (boundary > maxChars * 0.5 ? cut.slice(0, boundary + 1) : cut).trim()
  return `${head} The rest is in the chat.`
}

export default { speechPreview, splitForSpeech, toSpeechText }
