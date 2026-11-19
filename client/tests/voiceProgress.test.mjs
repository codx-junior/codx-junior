/**
 * Tests for the spoken progress narrator rules.
 *
 *   node tests/voiceProgress.test.mjs
 */
import assert from 'node:assert/strict'
import {
  nextProgressPlan,
  toolEventsForTurn,
  toolProgressPhrase
} from '../src/utils/voice/progress.js'

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

const user = { doc_id: 'u1', role: 'user', content: 'status?' }
const notice = {
  doc_id: 'n1',
  role: 'assistant',
  content: 'Voice call started.',
  meta_data: { voice_notice: 'started' }
}
const reply = {
  doc_id: 'a1',
  role: 'assistant',
  reply_to: 'u1',
  content: '',
  done: false,
  tool_events: [
    { tool: 'project_read_file', tool_call_id: 't1', status: 'running' },
    { tool: 'project_grep', tool_call_id: 't2', status: 'running' }
  ]
}
const messages = [notice, user, reply]
const plan = overrides => nextProgressPlan({
  messages,
  userMessageId: 'u1',
  startedAt: 1_000,
  now: 1_000 + 6_000,
  ...overrides
})

await test('maps tool names to short human phrases', () => {
  assert.equal(toolProgressPhrase('project_read_file'), 'Checking the project files.')
  assert.equal(toolProgressPhrase('fetch_webpage'), 'Looking that up online.')
  assert.equal(toolProgressPhrase('create_task'), 'Updating the task board.')
  assert.equal(toolProgressPhrase('docker_ps'), 'Checking the containers.')
  assert.equal(toolProgressPhrase('something_unknown'), 'Working on it.')
})

await test('finds the tool stream of the turn, skipping notices', () => {
  const events = toolEventsForTurn(messages, 'u1')
  assert.equal(events.length, 2)
  assert.equal(events[0].tool, 'project_read_file')
})

await test('stays silent during the first five seconds', () => {
  assert.equal(plan({ now: 1_000 + 4_000 }), null)
})

await test('announces a new running tool group once', () => {
  const first = plan({ now: 1_000 + 6_000 })
  assert.equal(first.kind, 'tool')
  assert.equal(first.text, 'Checking the project files.')
  assert.deepEqual(first.keys, ['t1', 't2'])

  const again = plan({
    now: 1_000 + 20_000,
    announcedToolKeys: first.keys
  })
  assert.equal(again, null)
})

await test('says "working on it" when no tool has started', () => {
  const noTools = [notice, user, { ...reply, tool_events: [] }]
  const result = plan({ messages: noTools, now: 1_000 + 7_500 })
  assert.equal(result.kind, 'start')
  assert.equal(result.text, 'Working on it.')
})

await test('still gives feedback when tools finish before the first phrase', () => {
  const done = {
    ...reply,
    tool_events: [
      { tool: 'project_read_file', tool_call_id: 't1', status: 'done' }
    ]
  }
  const result = plan({ messages: [notice, user, done], now: 1_000 + 8_000 })
  assert.equal(result.kind, 'start')
  assert.equal(result.text, 'Working on it.')
})

await test('keeps company on long tool runs, sparsely', () => {
  const first = plan({ now: 1_000 + 6_000 })
  const still = plan({
    now: 1_000 + 27_000,
    announcedToolKeys: first.keys,
    lastSpokenAt: 1_000 + 6_000,
    count: 1
  })
  assert.equal(still.kind, 'still')
  assert.equal(still.text, 'Still checking the project files.')
})

await test('stops after five phrases per turn', () => {
  const result = plan({ now: 1_000 + 60_000, count: 5 })
  assert.equal(result, null)
})

await test('requires the user message before narrating', () => {
  const result = plan({ messages: [notice], userMessageId: 'u1' })
  assert.equal(result, null)
})

if (failed) {
  console.error(`\n${failed} voice progress test(s) failed`)
  process.exit(1)
}
