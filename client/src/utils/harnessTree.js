/**
 * Harness tree rows: the chats of a project grouped by the TICKET BRANCH.
 *
 * The branch is saved in the chat model by the backend (`Chat.branch`) when the
 * ticket is created, so the tree groups by a value that was recorded, never
 * guessed from the checkout. A chat without it lands in the "Unknown" node.
 *
 * The rows are sorted with the checkout's branch first, the other branches
 * alphabetically and "Unknown" last.
 *
 * Kept out of the SFC so the grouping (and the Unknown node) is unit tested:
 * `cd client && node --test tests/harnessTree.test.mjs`.
 */

/** Label of the node holding the chats without a recorded ticket branch. */
export const UNKNOWN_BRANCH = 'Unknown'
/** Group key of that node (never collides with a real branch name). */
export const UNKNOWN_BRANCH_KEY = '__unknown__'

/**
 * Group key of a session: its ticket branch, or the unknown marker.
 *
 * @param {object} session - Session summary from the harness API.
 * @returns {string} The branch name or :data:`UNKNOWN_BRANCH_KEY`.
 */
export function branchKey(session) {
  return String(session?.branch || '').trim() || UNKNOWN_BRANCH_KEY
}

/**
 * Build the flat row list of the sidebar tree.
 *
 * @param {Array<object>} [sessions] - Session summaries.
 * @param {object} [options]
 * @param {string} [options.currentBranch] - Checked out branch (sorted first).
 * @param {string} [options.search] - Term matched against name/description/preview/model.
 * @param {string[]} [options.collapsed] - Branch keys rendered collapsed.
 * @returns {Array<object>} A branch header row followed by its session rows.
 */
export function harnessTreeRows(
  sessions = [],
  { currentBranch = '', search = '', collapsed = [] } = {}
) {
  const term = String(search || '').trim().toLowerCase()
  const matches = session => [
    session?.name,
    session?.description,
    session?.preview,
    session?.model
  ].some(value => String(value || '').toLowerCase().includes(term))

  const groups = new Map()
  for (const session of sessions || []) {
    const key = branchKey(session)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(session)
  }

  const active = String(currentBranch || '').trim()
  const order = [...groups.keys()].sort((a, b) => {
    if (a === active) return -1
    if (b === active) return 1
    // Tickets without a ticket branch are last: the "Unknown" node.
    if (a === UNKNOWN_BRANCH_KEY) return 1
    if (b === UNKNOWN_BRANCH_KEY) return -1
    return a.localeCompare(b)
  })

  const rows = []
  for (const key of order) {
    const all = groups.get(key)
    const visible = term ? all.filter(matches) : all
    if (term && !visible.length) continue
    const isCollapsed = (collapsed || []).includes(key)
    const unknown = key === UNKNOWN_BRANCH_KEY
    rows.push({
      session_id: `branch:${key}`,
      __branchHeader: true,
      __branchKey: key,
      // No branch name on the unknown node: diff/merge are not offered there.
      __branch: unknown ? '' : key,
      __branchLabel: unknown ? UNKNOWN_BRANCH : key,
      __branchUnknown: unknown,
      __branchCount: all.length,
      __branchRunning: all.filter(item => item.phase === 'running').length,
      __branchCurrent: !unknown && key === active,
      __branchCollapsed: isCollapsed
    })
    if (isCollapsed) continue
    for (const session of visible) rows.push({ ...session, __branchKey: key })
  }
  return rows
}
