/**
 * Tests for the speech text helpers used by the voice call.
 *
 *   node tests/voiceSpeech.test.mjs
 */
import assert from 'node:assert/strict'
import {
  speechPreview,
  splitForSpeech,
  toSpeechText
} from '../src/utils/voice/speechText.js'

let failed = 0

async function test(name, fn) {
  try {
    await fn()
    console.log(`ok - ${name}`)
  } catch (err) {
    failed++
    console.error(`not ok - ${name}`)
    console.error(`  ${err.message}`)
  }
}

await test('removes code fences and says the code is in the chat', () => {
  const text = toSpeechText('Here is the fix:\n```js\nconst a = 1\n```\nDone.')
  assert.match(text, /Here is the fix/)
  assert.match(text, /The code is in the chat/)
  assert.equal(text.includes('const a'), false)
})

await test('announces suggested replies instead of reading the options JSON', () => {
  const text = toSpeechText(
    'Pick one.\n\n```options\n[{"label": "Postgres"}, {"label": "SQLite"}]\n```'
  )
  assert.match(text, /Pick one/)
  assert.equal(text.includes('"label"'), false)
  assert.equal(text.includes('The code is in the chat'), false)
  assert.match(text, /Options: Postgres, SQLite\./)
})

await test('keeps link labels and drops urls', () => {
  const text = toSpeechText('See [the docs](https://example.com/a/b) and https://x.test')
  assert.match(text, /the docs/)
  assert.equal(text.includes('https://example.com'), false)
  assert.equal(text.includes('https://x.test'), false)
})

await test('removes headings, list markers and emphasis', () => {
  const text = toSpeechText('# Title\n\n- **one**\n- *two*')
  assert.equal(text.includes('#'), false)
  assert.equal(text.includes('**'), false)
  assert.equal(text.includes('*'), false)
  assert.match(text, /Title/)
  assert.match(text, /one/)
})

await test('turns table rows into comma separated text', () => {
  const text = toSpeechText('| name | value |\n|---|---|\n| a | 1 |')
  assert.equal(text.includes('|'), false)
  assert.match(text, /name, value/)
  assert.match(text, /a, 1/)
})

await test('splits a long text into engine-friendly chunks', () => {
  const chunks = splitForSpeech(
    'First sentence. Second sentence! Third sentence? Fourth sentence.',
    40
  )
  assert.ok(chunks.length >= 2)
  assert.ok(chunks.every(chunk => chunk.length <= 45))
  assert.equal(chunks.join(' ').includes('Fourth'), true)
})

await test('never splits a word', () => {
  const word = 'a'.repeat(60)
  const chunks = splitForSpeech(`intro ${word} outro`, 30)
  assert.ok(chunks.includes(word))
})

await test('returns no chunks for empty text', () => {
  assert.deepEqual(splitForSpeech(''), [])
  assert.deepEqual(splitForSpeech('   '), [])
})

await test('cuts a very long answer at a sentence boundary', () => {
  const answer = `${'Sentence one. '.repeat(120)}`
  const preview = speechPreview(answer, 200)
  assert.ok(preview.length < 300)
  assert.match(preview, /The rest is in the chat\.$/)
  assert.match(preview, /Sentence one\./)
})

await test('keeps a short answer intact', () => {
  assert.equal(speechPreview('All good.'), 'All good.')
})

if (failed) {
  console.error(`\n${failed} voice speech test(s) failed`)
  process.exit(1)
}
