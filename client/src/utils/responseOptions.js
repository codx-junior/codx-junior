/**
 * Suggested replies ("response options") protocol.
 *
 * When the model needs the user to take a decision it can end its answer with
 * a fenced block tagged `options` whose body is a JSON array. The chat renders
 * that block as buttons instead of raw markdown, and clicking one sends the
 * option's `value` as the next user message.
 *
 * The parser is deliberately forgiving because the block is produced by an
 * LLM: JSON is accepted with trailing commas / curly quotes, a plain bullet
 * list is accepted as a fallback (one reply per line), and an unterminated
 * fence is hidden while the answer is still streaming.
 *
 * Shape returned by {@link parseResponseOptions}:
 *
 *   {
 *     options:     [{ label, value, description, style, icon }],
 *     content:     markdown with the options blocks removed,
 *     rawBlock:    exact source of the valid options block(s),
 *     blocks:      [{ raw, complete, invalid }],
 *     incomplete:  true while a trailing block has no closing fence,
 *     invalid:     true when a block could not be parsed (it stays visible)
 *   }
 */

/** Fence languages understood as a suggested-replies block. */
export const RESPONSE_OPTION_FENCE_LANGUAGES = Object.freeze([
  'options',
  'suggestions',
  'suggested-replies',
  'suggested_replies',
  'quick-replies',
  'quick_replies',
  'choices',
  'replies'
])

const LANGUAGE_SET = new Set(RESPONSE_OPTION_FENCE_LANGUAGES)

/** Safety cap: a runaway model must not render an endless wall of buttons. */
export const MAX_RESPONSE_OPTIONS = 12

const MAX_LABEL_LENGTH = 160
const MAX_DESCRIPTION_LENGTH = 320
const MAX_VALUE_LENGTH = 4000
const MAX_ICON_LENGTH = 80

const ALLOWED_STYLES = new Set([
  'primary',
  'secondary',
  'accent',
  'danger',
  'warning',
  'success',
  'info',
  'ghost'
])

const STYLE_ALIASES = {
  error: 'danger',
  destructive: 'danger',
  negative: 'danger',
  remove: 'danger',
  positive: 'success',
  ok: 'success',
  confirm: 'primary',
  main: 'primary',
  default: '',
  normal: '',
  neutral: '',
  plain: ''
}

const OPEN_FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)(?:[\s,].*)?$/
const CLOSE_FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*$/
const PARTIAL_OPEN_FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z_-]*)$/

function emptyResult(source) {
  return {
    options: [],
    content: typeof source === 'string' ? source : '',
    rawBlock: '',
    blocks: [],
    incomplete: false,
    invalid: false
  }
}

function matchOpenFence(line) {
  const match = String(line || '').match(OPEN_FENCE_RE)
  if (!match) return null
  return {
    marker: match[1],
    length: match[1].length,
    lang: (match[2] || '').toLowerCase()
  }
}

function isClosingFence(line, fence) {
  const match = String(line || '').match(CLOSE_FENCE_RE)
  if (!match) return false
  return match[1][0] === fence.marker[0] && match[1].length >= fence.length
}

/**
 * A fence line that is still being streamed, e.g. `` ```opt ``.
 * Only used to hide the tail of a streaming message; a finished message keeps
 * a line like that as ordinary text.
 */
function matchPartialOptionFence(line) {
  const match = String(line || '').match(PARTIAL_OPEN_FENCE_RE)
  if (!match) return null
  const lang = (match[2] || '').toLowerCase()
  if (!lang) return null
  if (!RESPONSE_OPTION_FENCE_LANGUAGES.some(candidate => candidate.startsWith(lang))) {
    return null
  }
  return { marker: match[1], length: match[1].length, lang }
}

function parseJsonLoose(text) {
  const attempts = [text, text.replace(/,\s*([}\]])/g, '$1')]
  for (const attempt of attempts) {
    try {
      return JSON.parse(attempt)
    } catch {
      // try the next variant
    }
  }
  return undefined
}

function looksLikeJson(text) {
  const first = text.trimStart()[0]
  return first === '[' || first === '{'
}

function cleanInlineText(value, maxLength) {
  if (value === null || value === undefined) return ''
  const text = String(value)
    .replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return text.slice(0, maxLength)
}

function cleanMessageText(value, maxLength) {
  if (value === null || value === undefined) return ''
  return String(value)
    .replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, maxLength)
}

function stripLabelDecoration(text) {
  return text
    .replace(/^[\s*_`"']+/, '')
    .replace(/[\s*_`"']+$/, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .trim()
}

function pick(source, keys) {
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return value
    }
  }
  return ''
}

function normalizeStyle(value) {
  const raw = cleanInlineText(value, 40).toLowerCase()
  if (!raw) return ''
  if (ALLOWED_STYLES.has(raw)) return raw
  return STYLE_ALIASES[raw] ?? ''
}

const FA_STYLE_CLASS_RE = /^(fa[srb]?|fa-(solid|regular|brands|light|thin|duotone))$/

function normalizeIcon(value) {
  const raw = cleanInlineText(value, MAX_ICON_LENGTH)
  if (!raw) return ''
  const classes = raw
    .split(/\s+/)
    .filter(token => FA_STYLE_CLASS_RE.test(token) || /^fa-[a-z0-9-]+$/.test(token))
  const iconClasses = classes.filter(token => !FA_STYLE_CLASS_RE.test(token))
  if (!iconClasses.length) return ''
  if (!classes.some(token => FA_STYLE_CLASS_RE.test(token))) classes.unshift('fa-solid')
  return classes.join(' ')
}

/**
 * Normalize one entry of the block. A plain string is both label and value.
 * Unknown/empty entries are dropped.
 */
export function normalizeResponseOption(item) {
  let label = ''
  let value = ''
  let description = ''
  let style = ''
  let icon = ''

  if (typeof item === 'string' || typeof item === 'number') {
    label = cleanInlineText(item, MAX_LABEL_LENGTH)
    value = cleanMessageText(item, MAX_VALUE_LENGTH)
  } else if (item && typeof item === 'object') {
    label = stripLabelDecoration(cleanInlineText(
      pick(item, ['label', 'title', 'text', 'name', 'prompt', 'value']),
      MAX_LABEL_LENGTH
    ))
    value = cleanMessageText(
      pick(item, ['value', 'prompt', 'message', 'text', 'label', 'title']),
      MAX_VALUE_LENGTH
    )
    description = cleanInlineText(
      pick(item, ['description', 'desc', 'hint', 'detail', 'tooltip', 'note']),
      MAX_DESCRIPTION_LENGTH
    )
    style = normalizeStyle(pick(item, ['style', 'variant', 'type', 'color', 'tone']))
    icon = normalizeIcon(pick(item, ['icon', 'fa_icon', 'faIcon']))
  } else {
    return null
  }

  label = stripLabelDecoration(label)
  if (!label && value) label = cleanInlineText(value, MAX_LABEL_LENGTH)
  if (!label) return null
  if (!value) value = label

  return {
    label,
    value,
    description,
    style,
    icon
  }
}

function normalizeList(list) {
  return (Array.isArray(list) ? list : [])
    .map(normalizeResponseOption)
    .filter(Boolean)
}

function parseFenceBody(body) {
  const text = String(body || '').trim()
  if (!text) return []

  if (looksLikeJson(text)) {
    const parsed = parseJsonLoose(text)
    let list = null
    if (Array.isArray(parsed)) {
      list = parsed
    } else if (parsed && typeof parsed === 'object') {
      list = parsed.options
        ?? parsed.choices
        ?? parsed.replies
        ?? parsed.suggestions
        ?? parsed.buttons
        ?? null
    }

    // JSON-lines: one object per line, no wrapping array.
    if (!Array.isArray(list)) {
      const lines = text.split('\n').map(line => line.trim()).filter(Boolean)
      if (lines.length > 1 && lines.every(line => line.startsWith('{'))) {
        const objects = lines.map(parseJsonLoose)
        if (objects.every(object => object && typeof object === 'object')) {
          list = objects
        }
      }
    }

    if (!Array.isArray(list)) return null
    return normalizeList(list)
  }

  // Plain bullet/numbered list fallback: one reply per line.
  const items = []
  for (const line of text.split('\n')) {
    const raw = line.trim()
    if (!raw || raw.startsWith('```') || raw.startsWith('~~~')) continue
    const item = raw
      .replace(/^[-*+]\s+/, '')
      .replace(/^\d+[.)]\s+/, '')
      .replace(/^\[[ xX]\]\s+/, '')
      .trim()
    if (!item) continue

    const arrow = item.indexOf('=>')
    if (arrow > 0) {
      items.push({
        label: item.slice(0, arrow).trim(),
        value: item.slice(arrow + 2).trim()
      })
    } else {
      items.push(item)
    }
  }
  return normalizeList(items)
}

function dedupeOptions(options) {
  const seen = new Set()
  const result = []
  for (const option of options) {
    const key = `${option.label}\u0000${option.value}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push(option)
  }
  return result
}

function normalizeContent(lines) {
  return lines
    .join('\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/\s+$/, '')
    .replace(/^\n+/, '')
}

/**
 * Extract suggested replies from an assistant message.
 *
 * @param {string} rawContent Markdown source of the message.
 * @param {{ streaming?: boolean }} [options] `streaming` hides a trailing
 *        unterminated options block (and a partially typed fence language).
 * @returns {{
 *   options: Array<{label: string, value: string, description: string, style: string, icon: string}>,
 *   content: string,
 *   rawBlock: string,
 *   blocks: Array<{raw: string, complete: boolean, invalid: boolean}>,
 *   incomplete: boolean,
 *   invalid: boolean
 * }}
 */
export function parseResponseOptions(rawContent, options = {}) {
  const source = typeof rawContent === 'string' ? rawContent : ''
  if (!source) return emptyResult(source)

  const streaming = options.streaming === true
  const lines = source.split('\n')
  const kept = []
  const collected = []
  const blocks = []
  let incomplete = false
  let invalid = false
  let genericFence = null

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]

    // Inside a regular code fence: leave the content untouched. Without this
    // an example of the protocol inside ```markdown would be rendered as
    // buttons and stripped from the code sample.
    if (genericFence) {
      kept.push(line)
      if (isClosingFence(line, genericFence)) genericFence = null
      continue
    }

    const open = matchOpenFence(line)
    if (!open) {
      kept.push(line)
      continue
    }

    if (!LANGUAGE_SET.has(open.lang)) {
      // Track every other fence: its body may contain a line that looks like
      // an options fence (a code sample of this very protocol, for instance).
      genericFence = open
      kept.push(line)
      continue
    }

    let closeIndex = -1
    for (let cursor = index + 1; cursor < lines.length; cursor++) {
      if (isClosingFence(lines[cursor], open)) {
        closeIndex = cursor
        break
      }
    }

    if (closeIndex < 0) {
      blocks.push({ raw: lines.slice(index).join('\n'), complete: false, invalid: false })
      incomplete = true
      break
    }

    const raw = lines.slice(index, closeIndex + 1).join('\n')
    const body = lines.slice(index + 1, closeIndex).join('\n')
    const parsed = parseFenceBody(body)

    if (parsed === null) {
      // Malformed output is kept visible: hiding it would silently eat the
      // explanation the model tried to give.
      invalid = true
      blocks.push({ raw, complete: true, invalid: true })
      for (let cursor = index; cursor <= closeIndex; cursor++) kept.push(lines[cursor])
    } else {
      blocks.push({ raw, complete: true, invalid: false })
      collected.push(...parsed)
    }

    index = closeIndex
  }

  if (streaming && !incomplete && kept.length) {
    const last = kept[kept.length - 1]
    if (matchPartialOptionFence(last)) {
      blocks.push({ raw: last, complete: false, invalid: false })
      kept.pop()
      incomplete = true
    }
  }

  const normalized = dedupeOptions(collected).slice(0, MAX_RESPONSE_OPTIONS)

  return {
    options: normalized,
    content: normalizeContent(kept),
    rawBlock: blocks
      .filter(block => block.complete && !block.invalid)
      .map(block => block.raw)
      .join('\n\n'),
    blocks,
    incomplete,
    invalid
  }
}

/**
 * Append back the options block preserved by the parser.
 *
 * Editing a message renders only the clean markdown, so saving the edited
 * blocks would otherwise drop the buttons. The raw block is re-attached at the
 * end (where the protocol requires it).
 */
export function withResponseOptions(content, rawBlock) {
  const body = typeof content === 'string' ? content.trimEnd() : ''
  const block = typeof rawBlock === 'string' ? rawBlock.trim() : ''
  if (!block) return body
  if (!body) return block
  if (body.includes(block)) return body
  return `${body}\n\n${block}`
}
