/**
 * Tests for the barge-in level rules.
 *
 *   node tests/voiceBargeIn.test.mjs
 */
import assert from 'node:assert/strict'
import {
  BARGE_HOLD_MS,
  BARGE_WARMUP_MS,
  bargeInThreshold,
  isAssistantEcho,
  isBargeInLevel,
  isStrongBargeInLevel
} from '../src/utils/voice/bargeIn.js'

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

await test('uses a low minimum threshold in a quiet room', () => {
  assert.equal(bargeInThreshold(0), 9)
  assert.equal(isBargeInLevel(9, 0), true)
  assert.equal(isBargeInLevel(8, 0), false)
})

await test('raises the threshold above the ambient level, with a cap', () => {
  assert.equal(bargeInThreshold(10), 15)
  assert.equal(isBargeInLevel(14, 10), false)
  assert.equal(isBargeInLevel(15, 10), true)
  // A loud speaker must not lock barge-in out forever.
  assert.equal(bargeInThreshold(80), 24)
})

await test('recognises a loud voice regardless of the learned ambient level', () => {
  assert.equal(isStrongBargeInLevel(32), true)
  assert.equal(isStrongBargeInLevel(31), false)
})

await test('requires a sustained hold and an arming delay', () => {
  assert.ok(BARGE_HOLD_MS >= 300)
  assert.ok(BARGE_WARMUP_MS >= 500)
})

await test('handles missing values as silence', () => {
  assert.equal(bargeInThreshold(), 9)
  assert.equal(isBargeInLevel(undefined, 10), false)
})

if (failed) {
  console.error(`\n${failed} barge-in test(s) failed`)
  process.exit(1)
}


await test("ignores the assistant's own words picked up by the mic", () => {
  assert.equal(
    isAssistantEcho('checking the project files', 'Checking the project files.'),
    true
  )
  assert.equal(
    isAssistantEcho('I am listening again', "I'm listening again."),
    true
  )
  // Recognition interims often start with one word.
  assert.equal(isAssistantEcho('checking', 'Checking the project files.'), true)
})

await test('does not treat a short user command as echo', () => {
  assert.equal(isAssistantEcho('stop', 'Checking the project files.'), false)
  assert.equal(isAssistantEcho('wait', 'Writing the answer.'), false)
})

await test('detects unrelated user speech as barge-in', () => {
  assert.equal(
    isAssistantEcho('no show me the kanban board instead', 'Checking the project files.'),
    false
  )
})
