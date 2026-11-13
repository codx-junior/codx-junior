/**
 * Visual identity of the events of a chat turn.
 *
 * The turn timeline (the bar in the message header) and the events table (in
 * the details panel) must agree on one thing: WHAT a colour means. They are two
 * views of the same events, so the mapping lives here once instead of being
 * copied in both components.
 *
 * A tool is classified by its name into a family — read / write / execute / web
 * / agent — because that is what the user needs to spot at a glance: "the run
 * spent 40% of its time executing commands" says more than forty identical green
 * slices. Status still wins over family: a failed call is always error red.
 *
 * Tints stay soft (40-45% alpha): the bar is a background trace, not an alert.
 * Colour is never the only channel — every segment has a tooltip naming its
 * family and status, and the events table repeats it in words.
 */

/** Families, in the order the legend and the summary list them. */
export const CATEGORY_ORDER = ['read', 'edit', 'run', 'web', 'agent', 'other']

/**
 * How a tool name maps to a family.
 * The FIRST matching rule wins, so the specific families (agent, web) are
 * tested before the generic verbs (read, write) they would otherwise swallow
 * (`web_search` is a web call, not a file read).
 */
const RULES = [
  {
    key: 'agent',
    match: /delegate|orchestrator|process_task|create_task|project_manager|task_manager|team_|profile_|company_|mission/i
  },
  {
    key: 'web',
    match: /web_search|fetch_webpage|browser|browse|screenshot|computer_|navigate|_url|http/i
  },
  {
    key: 'run',
    match: /terminal|shell|exec|docker|compose|build|command|run_|git_|deploy|restart|logs?$/i
  },
  {
    key: 'edit',
    match: /write_file|apply_file|create_file|delete_file|save_|edit|patch|replace|restore|commit|close_pr|abandon/i
  },
  {
    key: 'read',
    match: /read|get_|list_|glob|grep|locate|search|find|trace|impact|health|map|outline/i
  }
]

/**
 * Visual definitions of every kind of slice the timeline can draw.
 *
 * - `classes` / `hoverClasses` → the bar segment
 * - `dotClass` → legend and table bullets
 * - `textClass` → the icon of the row / hover card
 * - `badgeClass` → daisyUI badge in the events table
 */
export const CATEGORIES = {
  // Model time: the turn's own "thinking". Deliberately neutral — it is the
  // background against which the tool families have to stand out.
  think: {
    label: 'Model',
    icon: 'fa-solid fa-brain',
    classes: 'bg-base-content/20',
    hoverClasses: 'bg-base-content/35',
    dotClass: 'bg-base-content/40',
    textClass: 'text-base-content/50',
    badgeClass: 'badge-ghost'
  },
  read: {
    label: 'Read',
    icon: 'fa-solid fa-magnifying-glass',
    classes: 'bg-info/40',
    hoverClasses: 'bg-info/60',
    dotClass: 'bg-info',
    textClass: 'text-info',
    badgeClass: 'badge-info'
  },
  edit: {
    label: 'Write',
    icon: 'fa-solid fa-pen-to-square',
    classes: 'bg-success/40',
    hoverClasses: 'bg-success/60',
    dotClass: 'bg-success',
    textClass: 'text-success',
    badgeClass: 'badge-success'
  },
  run: {
    label: 'Execute',
    icon: 'fa-solid fa-terminal',
    classes: 'bg-warning/45',
    hoverClasses: 'bg-warning/65',
    dotClass: 'bg-warning',
    textClass: 'text-warning',
    badgeClass: 'badge-warning'
  },
  web: {
    label: 'Web',
    icon: 'fa-solid fa-globe',
    classes: 'bg-secondary/40',
    hoverClasses: 'bg-secondary/60',
    dotClass: 'bg-secondary',
    textClass: 'text-secondary',
    badgeClass: 'badge-secondary'
  },
  agent: {
    label: 'Agent',
    icon: 'fa-solid fa-diagram-project',
    classes: 'bg-primary/40',
    hoverClasses: 'bg-primary/60',
    dotClass: 'bg-primary',
    textClass: 'text-primary',
    badgeClass: 'badge-primary'
  },
  // Lifecycle of the run itself (started / finished / cancelled)
  lifecycle: {
    label: 'Run',
    icon: 'fa-solid fa-circle-play',
    classes: 'bg-accent/40',
    hoverClasses: 'bg-accent/60',
    dotClass: 'bg-accent',
    textClass: 'text-accent',
    badgeClass: 'badge-accent'
  },
  // Waiting for the model to answer. This is the phase that used to be
  // invisible: a turn with no tool running is usually NOT idle, it is waiting
  // for the AI — the two must never look the same.
  llm: {
    label: 'LLM wait',
    icon: 'fa-solid fa-hourglass-half',
    classes: 'bg-accent/45',
    hoverClasses: 'bg-accent/65',
    dotClass: 'bg-accent',
    textClass: 'text-accent',
    badgeClass: 'badge-accent'
  },
  // The model is writing: tokens are arriving.
  stream: {
    label: 'Model',
    icon: 'fa-solid fa-brain',
    classes: 'bg-base-content/25',
    hoverClasses: 'bg-base-content/45',
    dotClass: 'bg-base-content/50',
    textClass: 'text-base-content/60',
    badgeClass: 'badge-ghost'
  },
  // No event recorded at all between two events: dead time.
  idle: {
    label: 'Idle',
    icon: 'fa-solid fa-hourglass-end',
    classes: 'bg-warning/25',
    hoverClasses: 'bg-warning/45',
    dotClass: 'bg-warning',
    textClass: 'text-warning',
    badgeClass: 'badge-warning'
  },
  // The process stopped while this was running (power off, restart, kill).
  interrupted: {
    label: 'Interrupted',
    icon: 'fa-solid fa-plug-circle-xmark',
    classes: 'bg-error/50',
    hoverClasses: 'bg-error/70',
    dotClass: 'bg-error',
    textClass: 'text-error',
    badgeClass: 'badge-error'
  },
  // The boundaries of the turn, only ever drawn as table rows
  send: {
    label: 'Sent',
    icon: 'fa-solid fa-paper-plane',
    classes: 'bg-base-content/25',
    hoverClasses: 'bg-base-content/40',
    dotClass: 'bg-base-content/50',
    textClass: 'text-base-content/50',
    badgeClass: 'badge-ghost'
  },
  answer: {
    label: 'Answer',
    icon: 'fa-solid fa-flag-checkered',
    classes: 'bg-primary/35',
    hoverClasses: 'bg-primary/55',
    dotClass: 'bg-primary',
    textClass: 'text-primary',
    badgeClass: 'badge-primary'
  },
  // Status overrides family: a failure is always the same colour everywhere
  error: {
    label: 'Error',
    icon: 'fa-solid fa-circle-xmark',
    classes: 'bg-error/45',
    hoverClasses: 'bg-error/70',
    dotClass: 'bg-error',
    textClass: 'text-error',
    badgeClass: 'badge-error'
  },
  other: {
    label: 'Tool',
    icon: 'fa-solid fa-wrench',
    classes: 'bg-base-content/25',
    hoverClasses: 'bg-base-content/40',
    dotClass: 'bg-base-content/40',
    textClass: 'text-base-content/60',
    badgeClass: 'badge-ghost'
  }
}

/**
 * Family of a tool by name.
 *
 * @param {string} name tool name (`project_read_file`, `docker_build`, …)
 * @returns {string} key of {@link CATEGORIES}: read | edit | run | web | agent | other
 */
export function toolCategory(name) {
  const value = String(name || '')
  if (!value) return 'other'
  const rule = RULES.find(candidate => candidate.match.test(value))
  return rule ? rule.key : 'other'
}

/**
 * Complete visual of a category, with its key attached so a row can compare it.
 *
 * @param {string} key key of {@link CATEGORIES}
 * @returns {object} `{ key, label, icon, classes, hoverClasses, dotClass, textClass, badgeClass }`
 */
export function categoryVisual(key) {
  const found = CATEGORIES[key] || CATEGORIES.other
  return { key: CATEGORIES[key] ? key : 'other', ...found }
}

/**
 * Visual of one tool call: its family, or error red when the call failed.
 * A running call keeps its family colour (the timeline pulses it instead of
 * recolouring it), so a long execute never looks like a different kind of work.
 *
 * @param {object} tool tool event of the message
 * @returns {object} visual of {@link CATEGORIES}
 */
export function toolVisual(tool) {
  if (tool?.status === 'error') return categoryVisual('error')
  return categoryVisual(toolCategory(tool?.tool))
}

/**
 * Visual of one PHASE of the turn: the waiting, not the calling.
 *
 * A turn spends most of its time waiting — for the model to accept the request,
 * for its first token, for the answer to stream — and none of that is a tool.
 * Waiting for the model and being idle must never share a colour: one is normal
 * work, the other is the turn possibly being dead.
 *
 * @param {object} phase phase event of the message (`kind`, `status`)
 * @returns {object} visual of {@link CATEGORIES}
 */
export function phaseVisual(phase) {
  const kind = String(phase?.kind || '')
  if (kind === 'interrupted') return categoryVisual('interrupted')
  if (phase?.status === 'error') return categoryVisual('error')
  if (kind === 'llm_request') return categoryVisual('llm')
  if (kind === 'llm_response') return categoryVisual('stream')
  if (kind === 'wallet_check') return categoryVisual('lifecycle')
  return categoryVisual('other')
}
