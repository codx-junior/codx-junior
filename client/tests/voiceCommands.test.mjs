/**
 * Tests for the deterministic voice commands.
 *
 *   node tests/voiceCommands.test.mjs
 */
import assert from 'node:assert/strict'
import {
  cleanChatQuery,
  parseEndCommand,
  parseSwitchCommand,
  resolveChatQuery,
  scoreChatMatch
} from '../src/utils/voice/commands.js'

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

const chats = [
  { id: 'chat-1', name: 'Voice UI' },
  { id: 'chat-2', name: 'Payments task' },
  { id: 'chat-3', name: 'Payments design' },
  { id: 'chat-4', name: 'Roadmap' }
]

await test('parses "switch to <chat>" without the conversational filler', () => {
  assert.deepEqual(parseSwitchCommand('switch to the payments task'), {
    matched: true,
    query: 'payments task'
  })
})

await test('parses "change the conversation to <chat>"', () => {
  assert.deepEqual(parseSwitchCommand('change the conversation to roadmap'), {
    matched: true,
    query: 'roadmap'
  })
})

await test('parses "move the call to <chat>"', () => {
  assert.deepEqual(parseSwitchCommand('move the call to payments design'), {
    matched: true,
    query: 'payments design'
  })
})

await test('parses "go back to <chat>" and "take me to <chat>"', () => {
  assert.deepEqual(parseSwitchCommand('go back to roadmap'), {
    matched: true,
    query: 'roadmap'
  })
  assert.deepEqual(parseSwitchCommand('take me to the roadmap chat'), {
    matched: true,
    query: 'roadmap'
  })
})

await test('parses the Spanish form', () => {
  assert.deepEqual(parseSwitchCommand('cambia la conversación a pagos'), {
    matched: true,
    query: 'pagos'
  })
  assert.deepEqual(parseSwitchCommand('llévame a la tarea de pagos'), {
    matched: true,
    query: 'tarea de pagos'
  })
})

await test('does not treat normal instructions as switch commands', () => {
  assert.equal(parseSwitchCommand('show me the payments task').matched, false)
  assert.equal(parseSwitchCommand('switch the tests to pytest').matched, false)
  assert.equal(parseSwitchCommand('go home').matched, false)
  assert.equal(parseSwitchCommand('').matched, false)
})

await test('cleans a destination fragment', () => {
  assert.equal(cleanChatQuery('the "Payments task" chat'), 'Payments task')
})

await test('parses end-call commands in both languages', () => {
  assert.equal(parseEndCommand('end the call'), true)
  assert.equal(parseEndCommand('hang up please'), true)
  assert.equal(parseEndCommand('termina la llamada'), true)
  assert.equal(parseEndCommand('what is the status?'), false)
})

await test('scores a chat by id, exact name and substring', () => {
  assert.equal(scoreChatMatch('chat-2', chats[1]), 100)
  assert.equal(scoreChatMatch('payments task', chats[1]), 90)
  assert.ok(scoreChatMatch('payments', chats[1]) > 0)
  assert.equal(scoreChatMatch('nothing here', chats[1]), 0)
})

await test('resolves an exact chat name to one chat', () => {
  const result = resolveChatQuery(chats, 'roadmap')
  assert.equal(result.chat?.id, 'chat-4')
  assert.equal(result.ambiguous, false)
})

await test('reports ambiguity instead of guessing', () => {
  const result = resolveChatQuery(chats, 'payments')
  assert.equal(result.chat, null)
  assert.equal(result.ambiguous, true)
  assert.equal(result.candidates.length, 2)
})

await test('returns an empty result when nothing matches', () => {
  const result = resolveChatQuery(chats, 'does not exist')
  assert.equal(result.chat, null)
  assert.equal(result.ambiguous, false)
  assert.deepEqual(result.candidates, [])
})

if (failed) {
  console.error(`\n${failed} voice command test(s) failed`)
  process.exit(1)
}
