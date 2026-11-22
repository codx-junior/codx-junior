/**
 * Regression tests for the file-reference rule of markdown messages
 * (src/utils/markdownFileLinks.js).
 *
 * Dependency free on purpose (no test runner is installed), run with:
 *
 *   node --test tests/markdownFileLinks.test.mjs
 *   node tests/markdownFileLinks.test.mjs
 */
import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'
import {
  findFileReferences,
  getFileReference,
  markdownFileLinks,
  normalizeReference,
  splitFileReferences
} from '../src/utils/markdownFileLinks.js'

let failed = 0

function test(name, fn) {
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (err) {
    failed++
    console.error(`not ok - ${name}`)
    console.error(`  ${err.message}`)
  }
}

function render(text) {
  const md = new MarkdownIt({ html: true, linkify: true, typographer: true })
  md.use(markdownFileLinks)
  return md.render(text)
}

// 1. The reference of the feature request: a relative script in plain prose.
test('detects "./coolify-install.sh" in prose', () => {
  const matches = findFileReferences('Simply run ./coolify-install.sh to start')
  assert.equal(matches.length, 1)
  assert.equal(matches[0].value, './coolify-install.sh')
  // "./x" and "x" are the same project path for the file API.
  assert.equal(matches[0].path, 'coolify-install.sh')
})

// 2. A project path with folders keeps them, the ":line" suffix does not.
test('detects a folder path with a line number', () => {
  const [match] = findFileReferences('look at src/components/Markdown.vue:120')
  assert.equal(match.value, 'src/components/Markdown.vue:120')
  assert.equal(match.path, 'src/components/Markdown.vue')
})

// 3. A bare, well-known filename is a reference too.
test('detects a bare filename with a known extension', () => {
  const paths = findFileReferences('edit install.sh and docker-compose.yml').map(m => m.path)
  assert.deepEqual(paths, ['install.sh', 'docker-compose.yml'])
})

// 4. Absolute paths are accepted (the file API resolves them).
test('detects an absolute path', () => {
  const [match] = findFileReferences('log: /var/log/codx-junior.log')
  assert.equal(match.path, '/var/log/codx-junior.log')
})

// 5. A URL is not a file reference, neither is the path of its segments.
test('does not match a URL', () => {
  assert.deepEqual(findFileReferences('https://example.com/docs/readme.md'), [])
  assert.deepEqual(findFileReferences('see www.example.com/install.sh'), [])
})

// 6. Versions, decimals and abbreviations stay plain text.
test('does not match versions, decimals or abbreviations', () => {
  assert.deepEqual(findFileReferences('upgrade to v1.4.0 and wait 1.5 seconds'), [])
  assert.deepEqual(findFileReferences('many languages, e.g. python'), [])
  assert.deepEqual(findFileReferences('today is 01.02.2024'), [])
})

// 7. Well known dotfiles have no extension to match against.
test('detects a dotfile', () => {
  assert.deepEqual(findFileReferences('.env').map(m => m.path), ['.env'])
  assert.deepEqual(findFileReferences('copy .env.example to .env').map(m => m.path), ['.env.example', '.env'])
})

// 8. Trailing punctuation belongs to the sentence, not to the path.
test('ignores trailing sentence punctuation', () => {
  const [match] = findFileReferences('then run ./setup.sh.')
  assert.equal(match.path, 'setup.sh')
})

// 9. The whole-string rule used for inline code.
test('getFileReference only matches a complete reference', () => {
  assert.equal(getFileReference('src/App.vue').path, 'src/App.vue')
  assert.equal(getFileReference('fails when the config is missing'), null)
  assert.equal(getFileReference('npm run ./build.sh'), null)
})

// 10. Splitting keeps the text around the reference intact.
test('splitFileReferences returns text and file segments', () => {
  const segments = splitFileReferences('run ./a.sh, then check src/b.py')
  assert.deepEqual(segments.map(s => s.type), ['text', 'file', 'text', 'file'])
  assert.equal(segments[1].path, 'a.sh')
  assert.equal(segments[3].path, 'src/b.py')
})

// 11. normalizeReference drops what the file system must not receive.
test('normalizeReference strips "./" and ":line"', () => {
  assert.equal(normalizeReference('./src/a.js'), 'src/a.js')
  assert.equal(normalizeReference('src/a.js:12:3'), 'src/a.js')
})

// 12. Rendered prose carries a .file-link anchor with the resolved path.
test('renders a file reference as a .file-link anchor', () => {
  const html = render('Run ./coolify-install.sh to install')
  assert.match(html, /<a class="file-link"[^>]*href="#"/)
  assert.match(html, /data-file="coolify-install\.sh"/)
  assert.match(html, />\.\/coolify-install\.sh</)
})

// 13. Inline code that is exactly a reference becomes a link; a command does not.
test('inline code is linkified only when it is the whole reference', () => {
  const linked = render('open `src/App.vue` please')
  assert.match(linked, /<a class="file-link"[^>]*><code>src\/App\.vue<\/code><\/a>/)

  const command = render('run `npm install` first')
  assert.doesNotMatch(command, /file-link/)
})

// 14. Fenced code is content, not a reference.
test('does not touch fenced code blocks', () => {
  const html = render('```bash\n./coolify-install.sh\n```')
  assert.doesNotMatch(html, /file-link/)
})

// 15. A markdown link is never re-wrapped.
test('keeps an existing markdown link untouched', () => {
  const html = render('[the installer](./coolify-install.sh)')
  assert.doesNotMatch(html, /file-link/)
  assert.match(html, /<a href="\.\/coolify-install\.sh">the installer<\/a>/)
})

// 16. URLs produced by linkify stay URLs.
test('keeps linkified URLs untouched', () => {
  const html = render('see https://example.com/docs/readme.md')
  assert.doesNotMatch(html, /file-link/)
  assert.match(html, /href="https:\/\/example\.com\/docs\/readme\.md"/)
})

// 17. A real assistant answer (Dockerfile review) keeps every reference it
//     writes, including the ones inside inline code.
test('links the references of a real answer', () => {
  const answer = [
    '**Dockerfile** — self-contained: context is now its own folder, COPYs',
    '`./entrypoint-wrapper.sh`, `./codx-junior-dind-init.sh` and ./coolify-install.sh.',
    'The stack lives in codx-junior-installer/dind/docker-compose.yml and the',
    'entrypoint is scripts/entrypoint.sh:42.'
  ].join('\n')

  const paths = findFileReferences(answer).map(m => m.path)
  assert.deepEqual(paths, [
    'entrypoint-wrapper.sh',
    'codx-junior-dind-init.sh',
    'coolify-install.sh',
    'codx-junior-installer/dind/docker-compose.yml',
    'scripts/entrypoint.sh'
  ])
})

// 18. What must NOT become a link in a mixed paragraph.
test('leaves shell variables, ports and flags alone', () => {
  const text = 'run $HOME/bin/codx.sh, expose 8080/tcp, use --debug and v2.1.0'
  assert.deepEqual(findFileReferences(text), [])
})

if (failed) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall file link tests passed')
