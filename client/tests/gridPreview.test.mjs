/**
 * Tests for the CoWorking wall tile rendering decision.
 *
 *   node tests/gridPreview.test.mjs
 *
 * The rule under test: a session that is already live on the stage must not
 * have a second live viewer (Selkies iframe) in the filmstrip/side rail,
 * because every viewer resizes the shared remote display to its own size and
 * the stage's pointer position then no longer matches the stream.
 */
import assert from 'node:assert/strict'
import { tilePreviewMode } from '../src/utils/gridPreview.js'

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

const browser = { isBrowser: true, running: true, embedUrl: '/browser/s-1/' }

await test('the stage renders the interactive viewer', () => {
  assert.equal(tilePreviewMode({ active: true, ...browser }), 'live')
})

await test('a preview-only thumbnail of the live session is a snapshot', () => {
  assert.equal(tilePreviewMode({ ...browser, active: true, previewOnly: true }), 'snapshot')
})

await test('a preview-only thumbnail of another browser session is a snapshot too', () => {
  assert.equal(tilePreviewMode({ ...browser, active: false, previewOnly: true }), 'snapshot')
})

await test('a browser wall tile may embed its live page when it is the only viewer', () => {
  assert.equal(tilePreviewMode(browser), 'iframe')
})

await test('a stopped browser session previews as a snapshot', () => {
  assert.equal(tilePreviewMode({ ...browser, running: false }), 'snapshot')
})

await test('a browser session without a live view previews as a snapshot', () => {
  assert.equal(tilePreviewMode({ ...browser, embedUrl: '' }), 'snapshot')
})

await test('desktop sessions preview as a snapshot', () => {
  assert.equal(tilePreviewMode({ isBrowser: false, running: true, embedUrl: '/computer/s-1/' }), 'snapshot')
})

await test('no arguments degrade to a snapshot', () => {
  assert.equal(tilePreviewMode(), 'snapshot')
})

if (failed) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall grid preview tests passed')
process.exit(0)
