/**
 * Cross-project task table API module.
 *
 * Thin client over `GET /api/tasks` (paged latest tasks of every board of every
 * visible project) and `GET /api/tasks/boards` (boards with the owning team and
 * its lead). See `codx/junior/api/tasks.py` and `task_board.py`.
 *
 * The table is read-only: card mutations keep using the project endpoints.
 */

const BASE = '/api/tasks'

/**
 * Build a query string, skipping empty values.
 * @param {Object} params
 * @returns {string} `?a=1&b=2` or `''`.
 */
function toQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

export const tasksModule = (API) => ({
  /**
   * One page of the global task table.
   * @param {Object} params - q, project_id, project_ids, board, board_ids,
   *   column, columns, status, statuses, priority, priorities, team_id,
   *   assignee, assignees, from_date, to_date, include_done, page, page_size,
   *   sort, direction.
   */
  list(params = {}) {
    return API.get(`${BASE}${toQuery(params)}`)
  },

  /** Boards (with team/lead, flow and effective responsible) and teams. */
  boards() {
    return API.get(`${BASE}/boards`)
  },

  /**
   * Assign the responsible/flow of one board.
   * @param {Object} payload - board_id, flow, responsible_kind,
   *   responsible_name, propagate_children.
   */
  updateBoard(payload = {}) {
    return API.put(`${BASE}/boards`, {
      board_id: payload.board_id || payload.boardId,
      flow: payload.flow,
      responsible_kind: payload.responsible_kind ?? payload.responsibleKind,
      responsible_name: payload.responsible_name ?? payload.responsibleName,
      propagate_children: payload.propagate_children ?? payload.propagateChildren
    })
  },

  /**
   * Resolve a schedule expression to the instant stored in `Chat.wake_at`.
   * `when="non-working-hours"` uses the installation's human working window,
   * so the client never duplicates the timezone/window logic.
   */
  resolveSchedule({ when, timezone = '' } = {}) {
    return API.get(`${BASE}/schedule${toQuery({ when, timezone })}`)
  }
})

// Made with ❤️ by codx-junior
