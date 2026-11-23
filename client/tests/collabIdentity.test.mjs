/**
 * Tests for the per-tab participant identity.
 *
 *   node tests/collabIdentity.test.mjs
 */
import assert from 'node:assert/strict'
import {
  getParticipantId,
  getParticipantIdentity,
  resetParticipantId
} from '../src/utils/collabIdentity.js'

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

await test('the participant id is stable for the tab', () => {
  resetParticipantId()
  const first = getParticipantId()
  const second = getParticipantId()
  assert.ok(first)
  assert.equal(first, second)
})

await test('the identity carries the authenticated display name and avatar', () => {
  resetParticipantId()
  const identity = getParticipantIdentity({
    username: 'gbrian',
    name: 'Guillermo',
    avatar: 'https://example.test/avatar.png'
  })
  assert.equal(identity.participant_id, getParticipantId())
  assert.equal(identity.display_name, 'Guillermo')
  assert.equal(identity.avatar, 'https://example.test/avatar.png')
})

await test('guests get a display name and no avatar', () => {
  resetParticipantId()
  const identity = getParticipantIdentity(null)
  assert.equal(identity.display_name, 'Guest')
  assert.equal(identity.avatar, null)
})

if (failed) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall participant identity tests passed')
process.exit(0)
