/**
 * Harness-mode view model helpers (pure, no Vue dependency).
 *
 * A harness session is a normal codx-junior task chat. This module turns its
 * messages into the shape the DSH-like view renders:
 *
 * * the user's request with the appended harness brief stripped,
 * * one activity block per assistant turn (tool calls + phases),
 * * the final delivery report (the last assistant content),
 * * the files changed by the session and their line counters.
 *
 * Keeping the rules here (instead of inside a component) is what makes them
 * testable with `node --test`, and keeps the Vue components focused on layout.
 */

/** Marker appended to the first user message by the backend harness brief. */
export const HARNESS_BRIEF_MARKER = '\n\n---\n\n## Harness mode\n'

/** Tool presentation: label shown in the activity row, icon and category. */
export const TOOL_META = {
  project_read_file: { label: 'Read', icon: 'fa-solid fa-file-lines', category: 'read' },
  read_folder: { label: 'Listed', icon: 'fa-solid fa-folder-open', category: 'read' },
  project_structure: { label: 'Mapped project', icon: 'fa-solid fa-sitemap', category: 'read' },
  project_map: { label: 'Mapped project', icon: 'fa-solid fa-sitemap', category: 'read' },
  project_search: { label: 'Searched', icon: 'fa-solid fa-magnifying-glass', category: 'search' },
  project_grep: { label: 'Searched', icon: 'fa-solid fa-magnifying-glass', category: 'search' },
  project_glob: { label: 'Found files', icon: 'fa-solid fa-magnifying-glass', category: 'search' },
  project_changes: { label: 'Read changes', icon: 'fa-solid fa-code-compare', category: 'read' },
  project_write_file: { label: 'Edited', icon: 'fa-solid fa-pen-to-square', category: 'edit' },
  apply_file_changes: { label: 'Edited', icon: 'fa-solid fa-pen-to-square', category: 'edit' },
  project_apply_file_changes: { label: 'Edited', icon: 'fa-solid fa-pen-to-square', category: 'edit' },
  restore_changes: { label: 'Restored', icon: 'fa-solid fa-rotate-left', category: 'edit' },
  code_writer: { label: 'Wrote code', icon: 'fa-solid fa-pen-to-square', category: 'edit' },
  qa_run_tests: { label: 'Ran tests', icon: 'fa-solid fa-flask-vial', category: 'test' },
  qa_review_changes: { label: 'Reviewed change', icon: 'fa-solid fa-clipboard-check', category: 'test' },
  qa_plan: { label: 'Planned tests', icon: 'fa-solid fa-list-check', category: 'test' },
  qa_coverage: { label: 'Measured coverage', icon: 'fa-solid fa-chart-pie', category: 'test' },
  qa_flaky_check: { label: 'Checked flakiness', icon: 'fa-solid fa-repeat', category: 'test' },
  security_review_changes: { label: 'Security review', icon: 'fa-solid fa-shield-halved', category: 'test' },
  security_check_command: { label: 'Checked command', icon: 'fa-solid fa-shield-halved', category: 'test' },
  repository_status: { label: 'Git status', icon: 'fa-solid fa-code-branch', category: 'git' },
  repository_diff: { label: 'Diff', icon: 'fa-solid fa-code-compare', category: 'git' },
  repository_commit: { label: 'Committed', icon: 'fa-solid fa-code-commit', category: 'git' },
  repository_stage: { label: 'Staged', icon: 'fa-solid fa-code-commit', category: 'git' },
  create_pr_branch: { label: 'Created branch', icon: 'fa-solid fa-code-branch', category: 'git' },
  docker_exec: { label: 'Ran in container', icon: 'fa-brands fa-docker', category: 'command' },
  docker_run: { label: 'Started container', icon: 'fa-brands fa-docker', category: 'command' },
  docker_compose_run: { label: 'Started service', icon: 'fa-brands fa-docker', category: 'command' },
  docker_logs: { label: 'Read container logs', icon: 'fa-brands fa-docker', category: 'command' },
  fetch_webpage: { label: 'Fetched page', icon: 'fa-solid fa-globe', category: 'web' },
  duckduckgo_search: { label: 'Searched the web', icon: 'fa-solid fa-globe', category: 'web' },
  browser_agent: { label: 'Browsed', icon: 'fa-solid fa-window-maximize', category: 'web' }
}

const DEFAULT_TOOL_META = {
  label: 'Used tool',
  icon: 'fa-solid fa-wrench',
  category: 'tool'
}

const DETAIL_KEYS = [
  'file_path',
  'path',
  'command',
  'cmd',
  'query',
  'pattern',
  'suite',
  'url',
  'name',
  'title'
]

const CATEGORY_LABELS = {
  read: 'read files',
  search: 'searched the code',
  edit: 'edited files',
  test: 'ran tests',
  git: 'used git',
  command: 'ran commands',
  web: 'browsed the web',
  tool: 'used tools'
}

/**
 * Remove the appended harness brief from a user message.
 * @param {string} content Stored message content.
 * @returns {string} The user's original request.
 */
export function stripHarnessBrief(content) {
  const text = String(content || '')
  const index = text.indexOf(HARNESS_BRIEF_MARKER)
  return index === -1 ? text : text.slice(0, index).trimEnd()
}

/**
 * Metadata of a tool call.
 * @param {string} tool Tool name.
 * @returns {{label: string, icon: string, category: string}}
 */
export function toolMeta(tool) {
  return TOOL_META[tool] || DEFAULT_TOOL_META
}

/**
 * Human-readable argument of a tool call (one line).
 * @param {object|null} request Parsed tool arguments.
 * @returns {string}
 */
export function toolDetail(request) {
  if (!request || typeof request !== 'object') return ''
  // Batch write tools name their files inside `changes`; show the first one
  // instead of a JSON blob in the activity row.
  const change = Array.isArray(request.changes) ? request.changes[0] : null
  if (change && typeof change === 'object') {
    const changePath = change.file_path || change.path || change.file
    if (changePath) return String(changePath)
  }
  for (const key of DETAIL_KEYS) {
    const value = request[key]
    if (value === null || value === undefined || value === '') continue
    if (typeof value === 'string' || typeof value === 'number') return String(value)
    if (Array.isArray(value)) return value.slice(0, 3).join(', ')
  }
  const [key, value] = Object.entries(request)[0] || []
  if (!key) return ''
  if (value === null || value === undefined) return key
  if (typeof value === 'string' || typeof value === 'number') return `${key}=${value}`
  try {
    return `${key}=${JSON.stringify(value)}`
  } catch {
    return key
  }
}

/** Format a duration in seconds as the compact DSH label (`1m 3s`). */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  if (!total) return '0s'
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    return `${hours}h ${minutes % 60}m`
  }
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`
}

/** Format token counts: 1200 -> 1.2k. */
export function formatTokens(value) {
  const total = Number(value) || 0
  if (!total) return '0'
  if (total >= 1000000) return `${(total / 1000000).toFixed(1)}m`
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`
  return String(total)
}

/**
 * Convert one tool event into an activity row.
 * @param {object} event ToolEvent.
 * @param {string} messageId Owner message id.
 * @returns {object}
 */
export function toolStep(event, messageId = '') {
  const meta = toolMeta(event?.tool)
  return {
    id: `${messageId}:tool:${event?.tool_call_id || event?.tool || Math.random()}`,
    kind: 'tool',
    tool: event?.tool || '',
    label: meta.label,
    icon: meta.icon,
    category: meta.category,
    detail: toolDetail(event?.request),
    status: event?.status || 'done',
    durationMs: Number(event?.duration_ms || 0),
    output: event?.output || event?.response || '',
    error: event?.error || '',
    timestamp: event?.created_at || event?.started_at || event?.finished_at || '',
    cached: event?.cached === true
  }
}

/**
 * Convert one phase event into an activity row.
 *
 * The phase events are the invisible waits of a turn (request, streaming,
 * notices), which is exactly what the DSH "Think" rows make visible.
 *
 * @param {object} event PhaseEvent.
 * @param {string} messageId Owner message id.
 * @returns {object}
 */
export function phaseStep(event, messageId = '') {
  const kind = String(event?.kind || 'notice')
  const labels = {
    llm_request: 'Thinking',
    llm_response: 'Writing',
    notice: 'Notice',
    wallet_check: 'Budget check',
    interrupted: 'Interrupted'
  }
  return {
    id: `${messageId}:phase:${kind}:${event?.started_at || Math.random()}`,
    kind: 'phase',
    tool: '',
    label: event?.label || labels[kind] || 'Step',
    icon: kind === 'llm_response'
      ? 'fa-solid fa-pen-nib'
      : (kind === 'interrupted' ? 'fa-solid fa-triangle-exclamation' : 'fa-solid fa-brain'),
    category: 'think',
    detail: event?.detail || '',
    status: event?.status || 'done',
    durationMs: Number(event?.duration_ms || 0),
    output: '',
    error: '',
    timestamp: event?.started_at || event?.finished_at || ''
  }
}

/**
 * Convert one lifecycle event into an activity row (agent run boundaries).
 * @param {object} event LifeCycleEvent.
 * @param {string} messageId Owner message id.
 * @returns {object}
 */
export function lifecycleStep(event, messageId = '') {
  return {
    id: `${messageId}:lifecycle:${event?.run_id || Math.random()}`,
    kind: 'lifecycle',
    tool: '',
    label: 'Agent run',
    icon: 'fa-solid fa-robot',
    category: 'think',
    detail: '',
    status: event?.status || 'done',
    durationMs: Number(event?.duration_ms || 0),
    output: '',
    error: event?.error || '',
    timestamp: event?.started_at || event?.finished_at || '',
    // The agent run is the WHOLE turn: showing it next to its own tools would
    // duplicate the "Completed in…" header. Its duration is still summed by
    // the activity strip, which is what makes the strip report the turn time.
    hidden: true
  }
}

/**
 * Build the activity rows of one assistant message.
 * @param {object} message Assistant message.
 * @returns {object[]}
 */
export function messageSteps(message) {
  const messageId = message?.doc_id || message?.id || ''
  const steps = []
  for (const event of message?.tool_events || []) steps.push(toolStep(event, messageId))
  for (const event of message?.phase_events || []) steps.push(phaseStep(event, messageId))
  for (const event of message?.lifecycle_events || []) steps.push(lifecycleStep(event, messageId))
  // Tool/phase events are stored in separate arrays; the transcript must read
  // in real order (thinking/writing are interleaved with the tool calls), so
  // sort by timestamp. Events without a timestamp keep their array order.
  const time = step => {
    const parsed = Date.parse(step.timestamp || '')
    return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed
  }
  return steps
    .map((step, index) => ({ step, index, time: time(step) }))
    .sort((a, b) => (a.time === b.time ? a.index - b.index : a.time - b.time))
    .map(entry => entry.step)
}

/**
 * Summarize the categories present in a set of steps.
 * @param {object[]} steps Activity rows.
 * @returns {string} e.g. "Read files, searched the code, edited files, ran tests".
 */
export function activitySummary(steps) {
  const categories = []
  for (const step of steps || []) {
    if (!categories.includes(step.category)) categories.push(step.category)
  }
  const labels = categories
    .filter(category => CATEGORY_LABELS[category])
    .map(category => CATEGORY_LABELS[category])
  if (!labels.length) return 'Worked'
  const unique = labels.filter((label, index) => labels.indexOf(label) === index)
  if (unique.length === 1) return unique[0].charAt(0).toUpperCase() + unique[0].slice(1)
  return unique
    .map((label, index) => (index === 0 ? label.charAt(0).toUpperCase() + label.slice(1) : label))
    .join(', ')
}

/**
 * Count the steps that failed.
 * @param {object[]} steps Activity rows.
 * @returns {number}
 */
export function failedSteps(steps) {
  return (steps || []).filter(step => step.status === 'error').length
}

/**
 * Sum the measured duration of the activity rows (ms).
 * @param {object[]} steps Activity rows.
 * @returns {number}
 */
export function stepsDurationMs(steps) {
  return (steps || []).reduce((total, step) => total + (Number(step.durationMs) || 0), 0)
}

/**
 * Files touched according to the diff block of the session API.
 * @param {object|null} diff `{ files: [{path, additions, deletions, status}] }`.
 * @returns {object[]}
 */
export function diffFiles(diff) {
  const files = Array.isArray(diff?.files) ? diff.files : []
  return files
    .filter(file => file && file.path)
    .map(file => ({
      path: String(file.path),
      additions: Number(file.additions) || 0,
      deletions: Number(file.deletions) || 0,
      status: file.status || 'modified'
    }))
}

/**
 * Short label for the changed-files block (`7 files +540 -21`).
 * @param {object[]} files Files from {@link diffFiles}.
 * @returns {string}
 */
export function filesSummary(files) {
  const list = Array.isArray(files) ? files : []
  if (!list.length) return 'No files changed'
  const additions = list.reduce((total, file) => total + (Number(file.additions) || 0), 0)
  const deletions = list.reduce((total, file) => total + (Number(file.deletions) || 0), 0)
  const fileLabel = `${list.length} file${list.length === 1 ? '' : 's'}`
  if (!additions && !deletions) return fileLabel
  return `${fileLabel} +${additions} -${deletions}`
}

/**
 * Keep the newest assistant content that actually says something.
 * @param {object[]} assistants Assistant messages.
 * @returns {object|null}
 */
export function deliveryReport(assistants) {
  const list = (assistants || []).filter(message => String(message?.content || '').trim())
  return list.length ? list[list.length - 1] : null
}

/**
 * Build the complete harness view model.
 *
 * @param {object[]} messages Chat messages.
 * @param {object} [options]
 * @param {object|null} [options.diff] Diff block from the session API.
 * @param {object|null} [options.session] Session summary (phase/timings).
 * @returns {object} `{ request, exchanges, turns, steps, report, files, activity, ... }`.
 */
export function buildHarnessView(messages, { diff = null, session = null } = {}) {
  const visible = (Array.isArray(messages) ? messages : [])
    .filter(message => message && !message.unsent && !message.hide)
  const exchanges = []
  let current = null

  for (const message of visible) {
    if (message.role === 'user') {
      current = {
        id: message.doc_id || message.id || `user-${exchanges.length}`,
        request: stripHarnessBrief(message.content),
        createdAt: message.created_at || message.updated_at || '',
        assistant: null
      }
      exchanges.push(current)
      continue
    }
    if (message.role !== 'assistant') continue
    if (!current) {
      current = { id: `assistant-${exchanges.length}`, request: '', createdAt: '', assistant: null }
      exchanges.push(current)
    }
    current.assistant = {
      id: message.doc_id || message.id || `assistant-${exchanges.length}`,
      content: String(message.content || ''),
      done: message.done !== false,
      error: message.error || '',
      createdAt: message.created_at || message.updated_at || '',
      model: message.meta_data?.model || '',
      steps: messageSteps(message),
      toolCount: (message.tool_events || []).length
    }
  }

  const assistants = visible.filter(message => message.role === 'assistant')
  const steps = assistants.flatMap(message => messageSteps(message))
  const reportMessage = deliveryReport(assistants)
  const report = reportMessage
    ? {
        id: reportMessage.doc_id || reportMessage.id,
        content: String(reportMessage.content || ''),
        createdAt: reportMessage.created_at || '',
        model: reportMessage.meta_data?.model || ''
      }
    : null

  const requestMessage = visible.find(message => message.role === 'user') || null
  const request = requestMessage ? stripHarnessBrief(requestMessage.content) : ''
  const files = diffFiles(diff)
  const phase = String(session?.phase || '').toLowerCase()
  // A session is pending while the last visible message is the user's (a fresh
  // ticket or a just-sent follow-up): the turn is scheduled but has not produced
  // its response yet. A failed session is not pending.
  const lastVisible = visible[visible.length - 1] || null
  const pending = lastVisible?.role === 'user' && phase !== 'failed'

  return {
    request,
    requestMessageId: requestMessage?.doc_id || requestMessage?.id || '',
    exchanges,
    assistants,
    steps,
    report,
    files,
    activity: activitySummary(steps),
    toolCount: steps.filter(step => step.kind === 'tool').length,
    failedCount: failedSteps(steps),
    durationMs: stepsDurationMs(steps),
    // The session is "live" while the backend reports it running, the last
    // assistant message is still streaming, or the ticket has not produced an
    // assistant message yet.
    pending,
    live: phase === 'running' || pending || !!(current?.assistant && !current.assistant.done),
    empty: !visible.length
  }
}
