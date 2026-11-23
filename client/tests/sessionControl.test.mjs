/**
 * Tests for the shared-control helpers used by both live viewers.
 *
 *   node tests/sessionControl.test.mjs
 */
import assert from 'node:assert/strict'
import {
  controlActionFor,
  controlSummary,
  hasPendingControlRequest,
  isControlOwner,
  mayControlSession,
  overlayControlFor,
  participantInitials,
  participantLabel,
  sessionParticipants,
  viewerIdentity
} from '../src/utils/sessionControl.js'

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

const alice = viewerIdentity({ username: 'alice', role: 'user' }, 'tab-a')
const bob = viewerIdentity({ username: 'bob', role: 'user' }, 'tab-b')
const admin = viewerIdentity({ username: 'root', role: 'admin' }, 'tab-r')

function session(overrides = {}) {
  return {
    controller: 'ai',
    control_mode: 'auto',
    control_owner: null,
    control_owner_name: null,
    control_request: null,
    participants: [],
    ...overrides
  }
}

await test('the AI lease lets a human take control', () => {
  const state = session()
  assert.equal(isControlOwner(state, alice), false)
  assert.equal(mayControlSession(state, alice), true)
  const action = controlActionFor(state, alice)
  assert.equal(action.action, 'take_control')
  assert.equal(action.disabled, false)
  assert.equal(controlSummary(state, alice).label, 'AI drives')
})

await test('the control owner can give back and observers need to ask', () => {
  const owned = session({
    controller: 'user',
    control_mode: 'request',
    control_owner: 'alice',
    control_owner_name: 'Alice'
  })
  assert.equal(isControlOwner(owned, alice), true)
  assert.equal(controlActionFor(owned, alice).action, 'release')
  assert.equal(controlSummary(owned, alice).label, 'You drive')

  assert.equal(mayControlSession(owned, bob), false)
  const bobAction = controlActionFor(owned, bob)
  assert.equal(bobAction.action, 'request_control')
  assert.match(bobAction.hint, /Alice is driving/)
  assert.equal(controlSummary(owned, bob).label, 'Alice drives')
})

await test('auto mode takes directly even while another human drives', () => {
  const owned = session({
    controller: 'user',
    control_mode: 'auto',
    control_owner: 'alice',
    control_owner_name: 'Alice'
  })
  assert.equal(controlActionFor(owned, bob).action, 'take_control')
  assert.equal(overlayControlFor(owned, bob).action, 'take_control')
})

await test('request mode keeps the observer behind the overlay', () => {
  const owned = session({
    controller: 'user',
    control_mode: 'request',
    control_owner: 'alice',
    control_owner_name: 'Alice'
  })
  const overlay = overlayControlFor(owned, bob)
  assert.equal(overlay.interactive, true)
  assert.equal(overlay.action, 'request_control')
  assert.match(overlay.hint, /click to ask/)
  // The owner sees no overlay: it can type directly.
  assert.equal(overlayControlFor(owned, alice).interactive, false)
})

await test('a pending request disables the button and shows the wait', () => {
  const pending = session({
    controller: 'user',
    control_mode: 'request',
    control_owner: 'alice',
    control_owner_name: 'Alice',
    control_request: {
      id: 'req-1',
      participant_id: 'tab-b',
      username: 'bob',
      name: 'Bob'
    }
  })
  assert.equal(hasPendingControlRequest(pending, bob), true)
  assert.equal(hasPendingControlRequest(pending, alice), false)
  assert.equal(controlActionFor(pending, bob).label, 'Request pending…')
  assert.equal(controlActionFor(pending, bob).disabled, true)
  assert.match(overlayControlFor(pending, bob).hint, /Waiting for Alice/)
})

await test('a tab id cannot impersonate another account', () => {
  const owned = session({ controller: 'user', control_owner: 'alice' })
  const impostor = viewerIdentity({ username: 'bob', role: 'user' }, 'tab-a')
  assert.equal(isControlOwner(owned, impostor), false)
  assert.equal(mayControlSession(owned, impostor), false)
  assert.equal(overlayControlFor(owned, impostor).interactive, true)
})

await test('admin may act and resolve another human lease', () => {
  const owned = session({ controller: 'user', control_owner: 'alice' })
  assert.equal(mayControlSession(owned, admin), true)
  assert.equal(overlayControlFor(owned, admin).interactive, false)
})

await test('participants render with names and initials', () => {
  const list = sessionParticipants({
    participants: [
      { id: '1', username: 'alice', name: 'Alice A.' },
      { id: '2', username: 'bob' },
      { id: '' }
    ]
  })
  assert.equal(list.length, 2)
  assert.equal(participantLabel(list[0]), 'Alice A.')
  assert.equal(participantLabel(list[1]), 'bob')
  assert.equal(participantInitials(list[0]), 'AA')
  assert.equal(participantInitials(list[1]), 'BO')
})

await test('request mode with AI driving is granted at once by the backend', () => {
  // UI still offers "take control"; the backend sees no human owner to approve.
  const state = session({ control_mode: 'request' })
  assert.equal(controlActionFor(state, bob).action, 'take_control')
})

if (failed) {
  console.error(`\n${failed} test(s) failed`)
  process.exit(1)
}
console.log('\nall session control tests passed')
process.exit(0)
