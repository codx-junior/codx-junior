// ─── Find in chat ───────────────────────────────────────────────────────────
// Matching helpers shared by the in-chat search control (ChatFindBar).
//
// The same matcher is used twice:
//   * over `message.content`  → to count and navigate the occurrences, and
//   * over the rendered text nodes of a message → to paint the highlight.
// Keeping both behind these helpers guarantees the counter and the painted
// ranges always agree on what a "match" is.

// Safety valve: a pathological regex on a huge conversation must not lock the UI.
const MAX_MATCHES = 2000

export function escapeRegExp(value) {
  return String(value ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Build the RegExp used by the find control.
 *
 * @param {{query?: string, caseSensitive?: boolean, wholeWord?: boolean, regex?: boolean}} options
 * @returns {{matcher: RegExp|null, error: string}} `matcher` is null when there
 *   is no query (or the expression is invalid) and `error` carries the reason.
 */
export function buildFindMatcher({ query = '', caseSensitive = false, wholeWord = false, regex = false } = {}) {
  const raw = String(query ?? '')
  if (!raw) return { matcher: null, error: '' }

  let source = regex ? raw : escapeRegExp(raw)
  if (wholeWord) {
    // Word boundaries only make sense next to a word character; wrapping a
    // punctuation query in \b would make it match nothing at all.
    const prefix = /^[\w\u00C0-\u024F]/.test(raw) ? '\\b' : ''
    const suffix = /[\w\u00C0-\u024F]$/.test(raw) ? '\\b' : ''
    source = `${prefix}(?:${source})${suffix}`
  }

  try {
    return { matcher: new RegExp(source, caseSensitive ? 'g' : 'gi'), error: '' }
  } catch {
    return { matcher: null, error: 'Invalid regular expression' }
  }
}

/**
 * All the non-overlapping occurrences of `matcher` inside a plain string.
 * @returns {Array<{start: number, end: number, text: string}>}
 */
export function findMatchesInText(text, matcher) {
  const value = String(text ?? '')
  if (!matcher || !value) return []

  const found = []
  matcher.lastIndex = 0
  let match
  while ((match = matcher.exec(value)) !== null) {
    // A zero-length match (e.g. `a*`) would loop forever: step over it.
    if (match[0] === '') {
      matcher.lastIndex += 1
      continue
    }
    found.push({ start: match.index, end: match.index + match[0].length, text: match[0] })
    if (found.length >= MAX_MATCHES) break
  }
  return found
}

/**
 * Occurrences of the query across a conversation, in reading order.
 * @returns {{matcher: RegExp|null, error: string, matches: Array<{start: number, end: number, text: string, messageIndex: number}>}}
 */
export function findMatchesInMessages(messages, options = {}) {
  const { matcher, error } = buildFindMatcher(options)
  const matches = []
  if (!matcher) return { matcher: null, error, matches }

  ;(messages || []).forEach((message, messageIndex) => {
    if (matches.length >= MAX_MATCHES) return
    findMatchesInText(message?.content, matcher).forEach(hit => {
      if (matches.length >= MAX_MATCHES) return
      matches.push({ ...hit, messageIndex })
    })
  })

  return { matcher, error, matches }
}
