/**
 * File references inside markdown messages.
 *
 * An answer writes file names in prose — `./coolify-install.sh`,
 * `src/components/Markdown.vue`, `install.sh` — and rendered as plain text they
 * are dead ends. This module turns them into links that the message renderers
 * wire to the in-app FileViewer (see `$ui.openFileViewer`):
 *
 *   • desktop mode → a dockview panel, one tab per file;
 *   • single mode  → the global FileViewer dialog over the current screen.
 *
 * Two layers live here:
 *
 *  - the rule itself (`findFileReferences` / `splitFileReferences`), dependency
 *    free and covered by tests/markdownFileLinks.test.mjs;
 *  - `markdownFileLinks(md)`, the markdown-it plugin registered by
 *    components/Markdown.vue and components/MarkdownViewer.vue.
 */

// Extensions that turn a BARE word into a file reference ("install.sh",
// "README.md"). A candidate that carries a folder ("src/App.vue") does not need
// this list: the slash is already unambiguous, so any short extension is taken.
const BARE_EXTENSIONS = [
  // shell / scripts
  'sh', 'bash', 'zsh', 'fish', 'ps1', 'bat', 'cmd',
  // source
  'js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'vue', 'svelte', 'py', 'rb', 'go',
  'rs', 'java', 'kt', 'kts', 'c', 'h', 'cc', 'cpp', 'hpp', 'cs', 'php', 'swift',
  'scala', 'pl', 'lua', 'sql', 'graphql', 'proto', 'ipynb',
  // web / docs
  'html', 'htm', 'css', 'scss', 'sass', 'less', 'md', 'mdx', 'txt', 'rst',
  'adoc', 'pdf',
  // data / config
  'json', 'jsonc', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'env',
  'properties', 'lock', 'csv', 'tsv', 'xml', 'log',
  // media / archives
  'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'mp4', 'mov', 'mp3',
  'wav', 'zip', 'tar', 'gz', 'tgz', '7z',
  // infra
  'tf', 'hcl', 'gradle', 'mk'
]

// Well known dotfiles: they have no extension to match against.
const DOT_FILES = [
  '.gitattributes', '.dockerignore', '.prettierrc', '.eslintrc', '.gitignore',
  '.env.local', '.env.example', '.bashrc', '.zshrc', '.npmrc', '.nvmrc', '.env'
]

// A candidate with a folder: "./x.sh", "../lib/x.py", "/app/main.py" or the
// plain relative "src/components/Markdown.vue". The optional ":line[:column]"
// suffix is part of the reference (it is dropped when opening the file).
const PATH_SOURCE = String.raw`(?:(?:\.{1,2}\/|\/)(?:[\w.@+-]+\/)*|(?:[\w.@+-]+\/)+)[\w.@+-]+\.[A-Za-z0-9]{1,10}(?::\d+(?::\d+)?)?`
// A bare filename whose extension is a known one.
const BARE_SOURCE = String.raw`[\w@+-][\w.@+-]*\.(?:${BARE_EXTENSIONS.join('|')})(?::\d+(?::\d+)?)?`
// A known dotfile.
const DOT_SOURCE = DOT_FILES
  .map(name => name.replace(/\./g, '\\.'))
  .join('|')

/**
 * Source of the file-reference pattern.
 *
 * The look-behind keeps the match out of a bigger token (a URL segment such as
 * `https://host/a/b.md`, a `$HOME/x.sh` variable, a Windows path) and the
 * look-ahead keeps it from stopping in the middle of a path.
 */
export const FILE_REFERENCE_SOURCE = String.raw`(?<![\w./\\@$%~-])(?:${PATH_SOURCE}|${BARE_SOURCE}|(?:${DOT_SOURCE}))(?![\w/-])`

/**
 * Drop the decoration that must not reach the file system: the ":line:column"
 * suffix and a leading "./" (both spellings resolve to the same project path).
 *
 * @param {string} reference - Raw reference as written in the message.
 * @returns {string} Project relative path.
 */
export function normalizeReference(reference) {
  return String(reference ?? '')
    .replace(/:\d+(?::\d+)?$/, '')
    .replace(/^\.\//, '')
    .trim()
}

// A host-like first segment ("www.example.com/install.sh",
// "github.com/user/repo.py"): a URL without the scheme, never a project path.
const HOST_LIKE = /^[\w-]+(?:\.[\w-]+)+$/
const TLD_LIKE = /^[a-z]{2,24}$/i

/**
 * Reject a candidate whose FIRST folder is a host name: without this,
 * "see www.example.com/install.sh" would be linked while "https://…" is not.
 * A leading dot is not a host (".github/workflows/ci.yml" stays a path).
 *
 * @param {string} reference - Raw candidate.
 * @returns {boolean} Whether it can be a project file.
 */
function isFileCandidate(reference) {
  const segments = normalizeReference(reference).split('/').filter(Boolean)
  if (segments.length < 2) return true
  const first = segments[0]
  if (!HOST_LIKE.test(first)) return true
  return !TLD_LIKE.test(first.split('.').pop())
}

/**
 * Find every file reference of a text.
 *
 * @param {string} text
 * @returns {Array<{value: string, path: string, index: number}>} Every match,
 *   `value` as written (kept as the link label) and `path` normalised.
 */
export function findFileReferences(text) {
  const matches = []
  const source = String(text ?? '')
  if (!source) return matches

  const regex = new RegExp(FILE_REFERENCE_SOURCE, 'gi')
  let match
  while ((match = regex.exec(source)) !== null) {
    if (!isFileCandidate(match[0])) continue
    matches.push({
      value: match[0],
      path: normalizeReference(match[0]),
      index: match.index
    })
  }
  return matches
}

/**
 * Split a text into plain-text and file-reference segments, ready to be turned
 * into markdown-it tokens.
 *
 * @param {string} text
 * @returns {Array<{type: 'text'|'file', value: string, path?: string}>}
 */
export function splitFileReferences(text) {
  const source = String(text ?? '')
  const segments = []
  let lastIndex = 0

  for (const reference of findFileReferences(source)) {
    if (reference.index > lastIndex) {
      segments.push({ type: 'text', value: source.slice(lastIndex, reference.index) })
    }
    segments.push({ type: 'file', value: reference.value, path: reference.path })
    lastIndex = reference.index + reference.value.length
  }

  if (!segments.length) return [{ type: 'text', value: source }]
  if (lastIndex < source.length) {
    segments.push({ type: 'text', value: source.slice(lastIndex) })
  }
  return segments
}

/**
 * Match only when the WHOLE string is one file reference. Inline code is
 * linkified with this rule ("`src/App.vue`"), never partially ("`npm run
 * ./x.sh`"), so a code span is never chopped in two.
 *
 * @param {string} text
 * @returns {{value: string, path: string}|null}
 */
export function getFileReference(text) {
  const value = String(text ?? '').trim()
  if (!value) return null
  const regex = new RegExp(`^(?:${FILE_REFERENCE_SOURCE})$`, 'i')
  return regex.test(value) && isFileCandidate(value)
    ? { value, path: normalizeReference(value) }
    : null
}

/**
 * Create the three tokens that render one file reference as a link.
 *
 * @param {object} Token - markdown-it Token class (`state.Token`).
 * @param {string} label - Text shown in the message (as written).
 * @param {string} path - Normalised path handed to the FileViewer.
 * @param {boolean} code - Wrap the label in <code> (inline code span).
 */
function createLinkTokens(Token, label, path, code) {
  const open = new Token('link_open', 'a', 1)
  open.attrSet('class', 'file-link')
  // The anchor never navigates: the click is intercepted and the file opens in
  // the viewer, so the fragment keeps a normal link affordance without a URL.
  open.attrSet('href', '#')
  open.attrSet('data-file', path)
  open.attrSet('title', `Open ${path} in the file viewer`)

  const labelToken = code
    ? new Token('code_inline', 'code', 0)
    : new Token('text', '', 0)
  labelToken.content = label
  if (code) labelToken.markup = '`'

  const close = new Token('link_close', 'a', -1)
  return [open, labelToken, close]
}

/**
 * Replace the linkable children of an inline token with link / text tokens.
 *
 * Links already present in the source ([label](url)), images and raw HTML are
 * left untouched: the walk tracks the `link_open` / `link_close` depth and only
 * rewrites `text` and `code_inline` children outside of it.
 */
function linkifyChildren(Token, children) {
  const output = []
  let linkDepth = 0

  for (const child of children) {
    if (child.type === 'link_open') linkDepth++
    if (child.type === 'link_close') linkDepth = Math.max(0, linkDepth - 1)

    if (linkDepth > 0 || (child.type !== 'text' && child.type !== 'code_inline')) {
      output.push(child)
      continue
    }

    if (child.type === 'code_inline') {
      const reference = getFileReference(child.content)
      if (!reference) {
        output.push(child)
        continue
      }
      output.push(...createLinkTokens(Token, reference.value, reference.path, true))
      continue
    }

    const segments = splitFileReferences(child.content)
    if (segments.length === 1 && segments[0].type === 'text') {
      output.push(child)
      continue
    }
    for (const segment of segments) {
      if (segment.type === 'text') {
        const text = new Token('text', '', 0)
        text.content = segment.value
        output.push(text)
      } else {
        output.push(...createLinkTokens(Token, segment.value, segment.path, false))
      }
    }
  }

  return output
}

/**
 * markdown-it plugin: turn file references into `.file-link` anchors.
 *
 * It runs as a CORE rule, i.e. after `linkify` and after inline parsing, so a
 * URL is never re-processed and code fences are never touched (they are block
 * tokens, not inline children).
 *
 * @param {object} md - markdown-it instance.
 * @returns {object} The same instance, for chaining.
 */
export function markdownFileLinks(md) {
  md.core.ruler.push('file_links', state => {
    for (const token of state.tokens || []) {
      if (token.type !== 'inline' || !Array.isArray(token.children)) continue
      token.children = linkifyChildren(state.Token, token.children)
    }
  })
  return md
}
