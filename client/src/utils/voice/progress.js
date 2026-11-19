/**
 * Spoken progress feedback for a voice turn.
 *
 * While the agent is thinking or running tools, the call says one short phrase
 * every few seconds ("Checking the project files.") instead of staying silent
 * for minutes. The rules are deliberately conservative:
 *
 * - nothing during the first ~5 seconds (most answers are faster than that),
 * - one phrase per new tool group, never one per parallel tool call,
 * - at least 7 seconds between phrases and at most 5 per turn,
 * - generic "Working on it." only when no tool has started.
 *
 * Pure module (no DOM) so the timing rules are unit tested in
 * `tests/voiceProgress.test.mjs`.
 */

const TOOL_GROUPS = [
  {
    match: /^(project_search|project_grep|project_glob|project_read_file|project_structure|project_locate|project_map|project_trace|project_impact|project_health|read_folder|search_codx|repository_|get_file_last_version)/,
    phrase: 'Checking the project files.'
  },
  {
    match: /^(fetch_webpage|web_|search_web|ddgs)/,
    phrase: 'Looking that up online.'
  },
  {
    match: /^(create_task|schedule_task|process_task|project_manager|task_|kanban|planning)/,
    phrase: 'Updating the task board.'
  },
  {
    match: /^(browser|computer|desktop|screenshot|explain_image|generate_image)/,
    phrase: 'Working in the browser.'
  },
  {
    match: /^(docker|console|infra|shell|traefik)/,
    phrase: 'Checking the containers.'
  },
  {
    match: /^(code_writer|apply_file_changes|git_|repository_commit|create_pr|close_pr|restore_changes)/,
    phrase: 'Applying the code changes.'
  }
]

const GENERIC_PHRASE = 'Working on it.'
const STILL_PHRASE = 'Still working on it.'

/**
 * Human friendly phrase for a tool name.
 * @param {string} toolName
 * @returns {string}
 */
export function toolProgressPhrase(toolName) {
  const value = String(toolName || '').toLowerCase()
  if (!value) return GENERIC_PHRASE
  const group = TOOL_GROUPS.find(entry => entry.match.test(value))
  return group ? group.phrase : GENERIC_PHRASE
}

/**
 * Stable identity of a tool call inside one turn.
 * @param {object} event
 * @returns {string}
 */
export function toolEventKey(event) {
  if (!event) return ''
  return String(
    event.tool_call_id ||
      event.tool_call ||
      `${event.tool || 'tool'}:${event.started_at || event.tool || ''}`
  )
}

/**
 * Find the assistant message that belongs to this spoken turn.
 * @param {Array} messages
 * @param {string} userMessageId
 * @returns {object|null}
 */
export function assistantMessageForTurn(messages, userMessageId) {
  const list = Array.isArray(messages) ? messages : []
  const key = String(userMessageId || '')
  const userIndex = list.findIndex(message =>
    String(message?.doc_id || message?.id || '') === key
  )
  if (userIndex < 0) return null
  const after = list.slice(userIndex + 1)
    .filter(message => message?.role === 'assistant' && !message?.meta_data?.voice_notice)
  if (!after.length) return null
  // Prefer the message that carries the tool stream; otherwise the last one.
  return [...after].reverse().find(message => message?.tool_events?.length) || after[0]
}

/**
 * All tool events of the turn, de-duplicated by call id.
 * @param {Array} messages
 * @param {string} userMessageId
 * @returns {Array<object>}
 */
export function toolEventsForTurn(messages, userMessageId) {
  const reply = assistantMessageForTurn(messages, userMessageId)
  const events = Array.isArray(reply?.tool_events) ? reply.tool_events : []
  const seen = new Set()
  const result = []
  for (const event of events) {
    const key = toolEventKey(event)
    if (!key || seen.has(key)) continue
    seen.add(key)
    result.push(event)
  }
  return result
}

/**
 * Decide the next spoken progress phrase.
 *
 * @param {object} options
 * @param {Array} options.messages - Chat messages.
 * @param {string} options.userMessageId - The spoken user message.
 * @param {string[]} [options.announcedToolKeys] - Tool calls already announced.
 * @param {number} [options.startedAt] - Turn start (ms).
 * @param {number} [options.lastSpokenAt] - Last progress phrase (ms).
 * @param {number} [options.now] - Clock (ms), for tests.
 * @param {number} [options.count] - Progress phrases already spoken this turn.
 * @returns {{text: string, keys: string[], kind: string}|null}
 */
export function nextProgressPlan({
  messages,
  userMessageId,
  announcedToolKeys = [],
  startedAt = 0,
  lastSpokenAt = 0,
  now = Date.now(),
  count = 0
} = {}) {
  if (count >= 5) return null
  if (!startedAt) return null
  const elapsed = now - startedAt
  const sinceLast = lastSpokenAt ? now - lastSpokenAt : elapsed
  if (elapsed < 5000) return null
  // Never speak twice in quick succession, even when many tools start.
  if (count > 0 && sinceLast < 7000) return null

  const announced = new Set((announcedToolKeys || []).map(String))
  const events = toolEventsForTurn(messages, userMessageId)
  const running = events.filter(event => event.status === 'running')
  const newRunning = running.filter(event => !announced.has(toolEventKey(event)))
  const runningKeys = running.map(toolEventKey).filter(Boolean)

  if (newRunning.length) {
    return {
      kind: 'tool',
      text: toolProgressPhrase(newRunning[0].tool),
      keys: newRunning.map(toolEventKey).filter(Boolean)
    }
  }

  if (!running.length && count === 0 && elapsed >= 7000) {
    return { kind: 'start', text: GENERIC_PHRASE, keys: [] }
  }

  // Long turns with no new tool call: keep the human company, sparsely.
  if (count > 0 && sinceLast >= 20000) {
    if (running.length) {
      const phrase = toolProgressPhrase(running[0].tool)
      const still = `Still ${phrase.charAt(0).toLowerCase()}${phrase.slice(1)}`
      return { kind: 'still', text: still, keys: runningKeys }
    }
    return { kind: 'still', text: STILL_PHRASE, keys: runningKeys }
  }

  return null
}

export default {
  assistantMessageForTurn,
  nextProgressPlan,
  toolEventKey,
  toolEventsForTurn,
  toolProgressPhrase
}
