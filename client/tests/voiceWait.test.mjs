/**
 * Tests for the "wait for the assistant answer" helper.
 *
 *   node tests/voiceWait.test.mjs
 */
import assert from 'node:assert/strict'
import { findAssistantReply } from '../src/utils/voice/waitForReply.js'

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

const user = { doc_id: 'u1', role: 'user', content: 'hi' }
const notice = {
  doc_id: 'n1',
  role: 'assistant',
  content: 'Voice call started.',
  done: true,
  meta_data: { voice_notice: 'started' }
}

await test('returns null while the answer is still streaming', () => {
  const messages = [
    user,
    { doc_id: 'a1', role: 'assistant', reply_to: 'u1', content: 'Hel', done: false }
  ]
  assert.equal(findAssistantReply(messages, 'u1'), null)
})

await test('returns the finished answer linked by reply_to', () => {
  const reply = { doc_id: 'a1', role: 'assistant', reply_to: 'u1', content: 'Hello', done: true }
  assert.equal(findAssistantReply([user, reply], 'u1'), reply)
})

await test('skips voice notices and picks the real answer', () => {
  const reply = { doc_id: 'a2', role: 'assistant', reply_to: 'u1', content: 'Answer', done: true }
  assert.equal(findAssistantReply([user, notice, reply], 'u1'), reply)
})

await test('falls back to the next assistant message without reply_to', () => {
  const reply = { doc_id: 'a3', role: 'assistant', content: 'Answer', done: true }
  assert.equal(findAssistantReply([user, reply], 'u1'), reply)
})

await test('never returns a previous assistant answer', () => {
  const oldReply = { doc_id: 'old', role: 'assistant', content: 'Old', done: true }
  const messages = [oldReply, user]
  assert.equal(findAssistantReply(messages, 'u1'), null)
})

await test('returns null for unknown messages', () => {
  assert.equal(findAssistantReply([], 'u1'), null)
  assert.equal(findAssistantReply(null, 'u1'), null)
  assert.equal(findAssistantReply([user], ''), null)
})

if (failed) {
  console.error(`\n${failed} voice wait test(s) failed`)
  process.exit(1)
}
