/**
 * Tests for the suggested-replies (`options`) protocol parser.
 *
 * Dependency free; run with:
 *
 *   node tests/responseOptions.test.mjs
 *   node --test tests/
 */
import assert from 'node:assert/strict'
import {
  MAX_RESPONSE_OPTIONS,
  parseResponseOptions,
  withResponseOptions
} from '../src/utils/responseOptions.js'

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

const jsonBlock = `Should I apply the migration?

\`\`\`options
[
  {"label": "Yes, apply it", "value": "Yes, apply the changes", "description": "Run it now", "style": "primary", "icon": "fa-solid fa-database"},
  {"label": "No", "value": "No, leave it as is", "style": "danger"}
]
\`\`\``

test('parses a JSON options block and strips it from the markdown', () => {
  const result = parseResponseOptions(jsonBlock)
  assert.equal(result.content, 'Should I apply the migration?')
  assert.equal(result.options.length, 2)
  assert.deepEqual(result.options[0], {
    label: 'Yes, apply it',
    value: 'Yes, apply the changes',
    description: 'Run it now',
    style: 'primary',
    icon: 'fa-solid fa-database'
  })
  assert.equal(result.options[1].label, 'No')
  assert.equal(result.options[1].description, '')
  assert.equal(result.options[1].style, 'danger')
  assert.equal(result.incomplete, false)
  assert.equal(result.invalid, false)
  assert.ok(result.rawBlock.includes('```options'))
  assert.ok(result.rawBlock.includes('"Yes, apply it"'))
})

test('accepts the object wrapper, trailing commas and curly quotes', () => {
  const result = parseResponseOptions(`Pick one:

\`\`\`suggestions
{"options": [{"label": "Alpha",}, {"label": "Beta",},],}
\`\`\``)
  assert.deepEqual(result.options.map(option => option.label), ['Alpha', 'Beta'])
  assert.equal(result.content, 'Pick one:')
})

test('accepts a plain bullet list and "label => value" lines', () => {
  const result = parseResponseOptions(`Choose:

\`\`\`options
- Yes
- No
- Tell me more => Explain the trade-offs first
\`\`\``)
  assert.deepEqual(result.options.map(option => option.value), [
    'Yes',
    'No',
    'Explain the trade-offs first'
  ])
  assert.equal(result.content, 'Choose:')
})

test('normalizes styles, labels and icons', () => {
  const result = parseResponseOptions(`\`\`\`options
[
  {"label": "**Delete**", "style": "error", "icon": "fa-trash"},
  {"text": "Keep", "variant": "positive"},
  {"label": "Weird", "style": "rainbow", "icon": "javascript:alert(1)"}
]
\`\`\``)
  assert.equal(result.options[0].label, 'Delete')
  assert.equal(result.options[0].style, 'danger')
  assert.equal(result.options[0].icon, 'fa-solid fa-trash')
  assert.equal(result.options[1].label, 'Keep')
  assert.equal(result.options[1].style, 'success')
  assert.equal(result.options[2].style, '')
  assert.equal(result.options[2].icon, '')
})

test('hides an unterminated block (streaming) without losing the prose', () => {
  const result = parseResponseOptions(
    `Working on it.\n\n\`\`\`options\n[{"label": "Yes"}`,
    { streaming: true }
  )
  assert.equal(result.content, 'Working on it.')
  assert.deepEqual(result.options, [])
  assert.equal(result.incomplete, true)
})

test('hides a partially typed fence language while streaming', () => {
  const result = parseResponseOptions('Working on it.\n\n```opt', { streaming: true })
  assert.equal(result.content, 'Working on it.')
  assert.equal(result.incomplete, true)
})

test('keeps a malformed block visible instead of eating the answer', () => {
  const source = 'Oops\n\n```options\n[{"label": "Yes" // broken\n```'
  const result = parseResponseOptions(source)
  assert.equal(result.invalid, true)
  assert.equal(result.options.length, 0)
  assert.equal(result.content, source.trim())
})

test('ignores fences that are not the options protocol', () => {
  const source = 'Example:\n\n```python\nprint("hi")\n```'
  const result = parseResponseOptions(source)
  assert.equal(result.content, source)
  assert.deepEqual(result.options, [])
})

test('does not parse an options example nested in a code fence', () => {
  const source = [
    'The format is:',
    '',
    '````markdown',
    '```options',
    '[{"label": "Yes"}]',
    '```',
    '````'
  ].join('\n')
  const result = parseResponseOptions(source)
  assert.equal(result.options.length, 0)
  assert.ok(result.content.includes('```options'))
})

test('supports JSON-lines entries and deduplicates them', () => {
  const result = parseResponseOptions(`\`\`\`options
{"label": "Yes", "value": "Yes"}
{"label": "Yes", "value": "Yes"}
{"label": "No", "value": "No"}
\`\`\``)
  assert.deepEqual(result.options.map(option => option.label), ['Yes', 'No'])
})

test('caps the number of rendered options', () => {
  const list = Array.from({ length: MAX_RESPONSE_OPTIONS + 5 }, (_, index) => ({
    label: `Option ${index}`,
    value: `Option ${index}`
  }))
  const result = parseResponseOptions(
    'Pick:\n\n```options\n' + JSON.stringify(list) + '\n```'
  )
  assert.equal(result.options.length, MAX_RESPONSE_OPTIONS)
})

test('withResponseOptions re-attaches the block after a block edit', () => {
  const edited = 'Updated prose'
  assert.equal(
    withResponseOptions(edited, '```options\n[{"label": "Yes"}]\n```'),
    'Updated prose\n\n```options\n[{"label": "Yes"}]\n```'
  )
  assert.equal(withResponseOptions(edited, ''), 'Updated prose')
  const already = 'Prose\n\n```options\n[{"label": "Yes"}]\n```'
  assert.equal(withResponseOptions(already, '```options\n[{"label": "Yes"}]\n```'), already)
})

if (failed > 0) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall responseOptions tests passed')
