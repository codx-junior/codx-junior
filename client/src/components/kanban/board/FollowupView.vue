<template>
  <div ref="scrollEl" class="followup-view h-full overflow-y-auto bg-base-100" @scroll="onScroll">
    <div class="p-4 lg:p-6 w-full">

      <!-- Header + filters -->
      <div class="flex flex-col gap-3 mb-5">
        <div>
          <div class="flex items-center gap-2">
            <h1 class="font-semibold text-lg">Follow-up</h1>
            <span
              v-if="totalCount"
              class="badge badge-sm badge-ghost text-base-content/50 font-normal"
            >
              Showing {{ loadedCount }} of {{ totalCount }}
            </span>
            <span class="badge badge-sm badge-outline gap-1">
              <i class="fa-solid fa-layer-group text-[9px]"></i>{{ scopeLabel }}
            </span>
          </div>
          <p class="text-xs text-base-content/50 mt-0.5">Sprint health & attention queue</p>
        </div>

        <div class="flex flex-wrap items-center justify-center gap-2 w-full">
          <PlanningRangeFilter
            :local="true"
            :range="rangeFilter"
            :from-date="customFromDate"
            :to-date="customToDate"
            @update:range="setRangeFilter"
            @update:from-date="setCustomFrom"
            @update:to-date="setCustomTo"
          />

          <button
            type="button"
            class="btn btn-sm btn-outline gap-1.5"
            title="See every board and assign its responsible"
            @click="$ui.openApp('board-management')"
          >
            <i class="fa-solid fa-table-columns text-xs"></i>
            <span class="text-xs">Boards</span>
          </button>

          <select
            class="select select-sm select-bordered text-xs"
            :value="boardFilter"
            @change="$storex.planning.setBoardFilter($event.target.value)"
          >
            <option value="all">All boards</option>
            <option v-for="board in boards" :key="board" :value="board">{{ board }}</option>
          </select>

          <select
            class="select select-sm select-bordered text-xs"
            :value="assigneeFilter"
            @change="$storex.planning.setAssigneeFilter($event.target.value)"
          >
            <option value="all">All assignees</option>
            <option v-for="user in assignees" :key="user" :value="user">{{ user }}</option>
          </select>
        </div>
      </div>

      <p v-if="error" class="text-xs text-error mb-4">
        <i class="fa-solid fa-circle-exclamation mr-1"></i>{{ error }}
      </p>

      <!-- Metric cards -->
      <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        <div class="bg-base-200 border border-base-100 rounded-lg p-4">
          <div class="text-xs text-base-content/40 uppercase tracking-wide">Total</div>
          <div class="text-2xl font-bold mt-1">{{ metrics.total }}</div>
          <div class="text-xs text-base-content/50 mt-1"><i class="fa-solid fa-list-ul mr-1"></i>all boards</div>
        </div>

        <div class="bg-base-200 border border-base-100 rounded-lg p-4">
          <div class="text-xs text-base-content/40 uppercase tracking-wide">Active</div>
          <div class="text-2xl font-bold mt-1 text-info">{{ metrics.active }}</div>
          <div class="text-xs text-base-content/50 mt-1">in progress / todo</div>
        </div>

        <div class="bg-base-200 border border-base-100 rounded-lg p-4">
          <div class="text-xs text-base-content/40 uppercase tracking-wide">Done</div>
          <div class="text-2xl font-bold mt-1 text-success">{{ metrics.done }}</div>
          <div class="text-xs text-base-content/50 mt-1">{{ metrics.completion }}% completion</div>
        </div>

        <div class="bg-base-200 rounded-lg p-4" :class="metrics.overdue ? 'border border-error/30' : 'border border-base-100'">
          <div class="text-xs text-base-content/40 uppercase tracking-wide">Overdue</div>
          <div class="text-2xl font-bold mt-1 text-error">{{ metrics.overdue }}</div>
          <div class="text-xs mt-1" :class="metrics.overdue ? 'text-error/70' : 'text-base-content/50'">
            <i class="fa-solid fa-triangle-exclamation mr-1" v-if="metrics.overdue"></i>
            {{ metrics.overdue ? 'needs attention' : 'clear' }}
          </div>
        </div>

        <div class="bg-base-200 border border-base-100 rounded-lg p-4">
          <div class="text-xs text-base-content/40 uppercase tracking-wide">Due today</div>
          <div class="text-2xl font-bold mt-1 text-warning">{{ metrics.dueToday }}</div>
          <div class="text-xs text-base-content/50 mt-1">focus today</div>
        </div>
      </div>

      <!-- Lower grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <!-- Attention + board progress -->
        <section class="lg:col-span-2">
          <h2 class="text-sm font-semibold flex items-center gap-2 mb-3">
            <i class="fa-solid fa-triangle-exclamation text-warning"></i> Needs attention
          </h2>

          <div class="space-y-2">
            <div
              v-for="task in needsAttention"
              :key="task.id"
              class="flex items-center gap-3 p-3 rounded-lg bg-base-200 border border-base-100 transition-colors"
              :class="task.priority === 'high' ? 'hover:border-error/40' : 'hover:border-warning/40'"
            >
              <span class="badge badge-sm gap-1 border shrink-0" :class="priorityMeta(task.priority).badge">
                <i :class="`${priorityMeta(task.priority).icon} text-[10px]`"></i>
                {{ priorityMeta(task.priority).label }}
              </span>

              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium truncate">{{ task.name }}</div>
                <div class="text-xs text-base-content/50">
                  {{ task.board }} ·
                  <span :class="dueState(task).class">{{ dueState(task).label }}</span>
                </div>
              </div>

              <span v-if="!task.assignees?.length" class="badge badge-sm badge-ghost shrink-0 text-base-content/40">unassigned</span>
            </div>

            <div v-if="!needsAttention.length" class="text-sm text-base-content/30 py-8 text-center">
              Nothing needs attention 🎉
            </div>
          </div>

          <!-- Board progress -->
          <h2 class="text-sm font-semibold flex items-center gap-2 mt-7 mb-3">
            <i class="fa-solid fa-bars-progress text-info"></i> Board progress
          </h2>
          <div class="space-y-3">
            <div v-for="board in boardProgress" :key="board.board">
              <div class="flex justify-between text-xs mb-1">
                <span class="text-base-content/60">{{ board.board }}</span>
                <span class="text-base-content/40">{{ board.done }}/{{ board.total }} · {{ board.percentage }}%</span>
              </div>
              <progress
                class="progress w-full h-2"
                :class="progressClass(board.percentage)"
                :value="board.percentage"
                max="100"
              ></progress>
            </div>

            <div v-if="!boardProgress.length" class="text-sm text-base-content/30 py-8 text-center">
              No board data
            </div>
          </div>
        </section>

        <!-- Activity feed -->
        <aside>
          <h2 class="text-sm font-semibold flex items-center gap-2 mb-3">
            <i class="fa-solid fa-satellite-dish text-base-content/50"></i> Activity
          </h2>
          <ol class="relative border-s border-base-300 ms-3 space-y-4">
            <li v-for="event in activityFeed" :key="event.id" class="ms-6">
              <span class="absolute -start-1.5 mt-1 h-3 w-3 rounded-full" :class="toneDot(event.tone)"></span>
              <p class="text-xs text-base-content/70">
                <span class="font-semibold">{{ event.actor }}</span> {{ event.text }}
              </p>
              <time class="text-xs text-base-content/40">{{ event.timeLabel }}</time>
            </li>
          </ol>
        </aside>
      </div>

      <!-- Pagination footer -->
      <div class="flex flex-col items-center justify-center gap-2 py-6 text-xs text-base-content/40">
        <template v-if="loading && !loadedCount">
          <span class="loading loading-spinner loading-xs text-primary"></span>
          <span>Loading tasks…</span>
        </template>
        <template v-else-if="loadingMore">
          <span class="loading loading-spinner loading-xs text-primary"></span>
          <span>Loading more…</span>
        </template>
        <template v-else-if="hasMore">
          <button type="button" class="btn btn-xs btn-outline gap-1.5" @click="loadNextPage">
            <i class="fa-solid fa-chevron-down text-[9px]"></i>
            Load more
          </button>
          <span class="text-base-content/30">Showing {{ loadedCount }} of {{ totalCount }}</span>
        </template>
        <template v-else-if="loadedCount">
          <span class="text-base-content/30">End of list</span>
        </template>
        <template v-else>
          <span class="text-base-content/30">No tasks match the current filters</span>
        </template>
      </div>
    </div>
  </div>
</template>

<script>
import { API } from '@/api/api'
import PlanningRangeFilter from './PlanningRangeFilter.vue'

// Distance (px) from the bottom that triggers loading the next page.
const SCROLL_THRESHOLD = 120
// `/api/tasks` caps page_size at 100. The view loads pages on demand: the first
// one on mount/filter change and the next ones while the user scrolls.
const PAGE_SIZE = 100

const PRIORITY_WEIGHT = { high: 0, medium: 1, low: 2 }

function today() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgo(days) {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return date.toISOString().slice(0, 10)
}

function relativeTime(value) {
  if (!value) return ''
  const then = new Date(String(value).replace(' ', 'T')).getTime()
  if (Number.isNaN(then)) return ''
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return `${Math.round(days / 30)}mo ago`
}

// Map one GlobalTask of `/api/tasks` to the shape this view renders.
function toTask(row) {
  return {
    id: row.id,
    name: row.name,
    board: row.board,
    column: row.column,
    status: row.status || 'todo',
    priority: row.priority || 'low',
    dueDate: row.due_date || null,
    progress: Number(row.progress) || 0,
    assignees: row.assignees || [],
    updatedAt: row.last_activity_at || row.updated_at || row.created_at || null,
    users: []
  }
}

function taskToActivity(task) {
  const actor = task.assignees?.[0] || 'unknown'
  const name = task.name || task.column || task.board || task.id
  let text = `updated "${name}"`
  let tone = 'primary'

  if (task.status === 'done') {
    text = `completed "${name}"`
    tone = 'success'
  } else if (task.status === 'doing') {
    text = `moved "${name}" to ${task.column || 'In Progress'}`
    tone = 'info'
  } else if (task.status === 'onhold') {
    text = `put "${name}" on hold`
    tone = 'warning'
  }

  return {
    id: `${task.id}-${task.updatedAt}`,
    actor,
    text,
    target: task.id,
    timeLabel: relativeTime(task.updatedAt),
    time: task.updatedAt,
    tone
  }
}

function byPriorityAndDue(a, b) {
  const pa = PRIORITY_WEIGHT[a.priority] ?? 2
  const pb = PRIORITY_WEIGHT[b.priority] ?? 2
  if (pa !== pb) return pa - pb

  const aDue = a.dueDate || '9999-12-31'
  const bDue = b.dueDate || '9999-12-31'
  if (aDue !== bDue) return aDue < bDue ? -1 : 1
  return (a.name || '').localeCompare(b.name || '')
}

export default {
  name: 'FollowupView',
  components: { PlanningRangeFilter },
  data() {
    return {
      // Tasks of every page loaded so far for the current filters.
      tasks: [],
      // Local range filter (the view owns it so changing it triggers exactly
      // one `/api/tasks` request, not the planning store's dataset reload).
      rangeFilter: 'month',
      customFromDate: null,
      customToDate: null,
      // Server-side page/counters of the `/api/tasks` response.
      page: 0,
      total: 0,
      hasMore: false,
      serverMetrics: {},
      loading: false,
      loadingMore: false,
      error: null,
      // Guards against stale responses after a filter/project/date reset.
      loadToken: 0
    }
  },
  computed: {
    boardFilter() {
      return this.$storex.planning.boardFilter
    },
    assigneeFilter() {
      return this.$storex.planning.assigneeFilter
    },
    // The paged review is scoped exactly like the Tasks table: the active
    // project plus its children.
    projectIds() {
      const ids = []
      const active = this.$storex.projects.activeProject
      if (active?.project_id) ids.push(active.project_id)
      ;(this.$storex.projects.childProjects || []).forEach(project => {
        if (project?.project_id && !ids.includes(project.project_id)) {
          ids.push(project.project_id)
        }
      })
      return ids
    },
    scopeLabel() {
      const count = this.projectIds.length
      if (!count) return 'All projects'
      if (count === 1) {
        return this.$storex.projects.activeProject?.project_name || '1 project'
      }
      return `${count} projects`
    },
    dateRange() {
      const now = today()
      if (this.rangeFilter === 'today') {
        return { from_date: now, to_date: now }
      }
      if (this.rangeFilter === 'week') {
        return { from_date: daysAgo(7), to_date: now }
      }
      if (this.rangeFilter === 'custom') {
        return {
          from_date: this.customFromDate || daysAgo(30),
          to_date: this.customToDate || now
        }
      }
      return { from_date: daysAgo(30), to_date: now }
    },
    filteredTasks() {
      return this.tasks.filter(task => {
        if (this.boardFilter !== 'all' && task.board !== this.boardFilter) return false
        if (this.assigneeFilter !== 'all' && !(task.assignees || []).includes(this.assigneeFilter)) return false
        return true
      })
    },
    boards() {
      return [...new Set(this.tasks.map(task => task.board).filter(Boolean))].sort()
    },
    assignees() {
      return [...new Set(this.tasks.flatMap(task => task.assignees || []))].sort()
    },
    // Headline numbers come from the server over the whole filtered dataset,
    // not from the page currently loaded.
    metrics() {
      const metrics = this.serverMetrics || {}
      return {
        total: Number(metrics.total) || 0,
        active: Number(metrics.active) || 0,
        done: Number(metrics.done) || 0,
        completion: Number(metrics.completion) || 0,
        overdue: Number(metrics.overdue) || 0,
        dueToday: Number(metrics.due_today) || 0
      }
    },
    needsAttention() {
      const now = new Date().toISOString().slice(0, 10)
      return this.filteredTasks
        .filter(task => task.status !== 'done')
        .filter(task => {
          const overdue = task.dueDate && task.dueDate < now
          const dueToday = task.dueDate === now
          const unassignedHigh = task.priority === 'high' && !(task.assignees || []).length
          return overdue || dueToday || unassignedHigh
        })
        .sort(byPriorityAndDue)
    },
    boardProgress() {
      const map = {}
      this.filteredTasks.forEach(task => {
        const board = task.board || '--none--'
        if (!map[board]) map[board] = { board, total: 0, done: 0 }
        map[board].total += 1
        if (task.status === 'done') map[board].done += 1
      })

      return Object.values(map)
        .map(board => ({
          ...board,
          percentage: board.total ? Math.round((board.done / board.total) * 100) : 0
        }))
        .sort((a, b) => a.board.localeCompare(b.board))
    },
    activityFeed() {
      return this.filteredTasks
        .filter(task => task.updatedAt)
        .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
        .slice(0, 30)
        .map(taskToActivity)
    },
    loadedCount() {
      return this.tasks.length
    },
    totalCount() {
      return this.total
    }
  },
  watch: {
    // The date range and the project scope belong to the request: reset the
    // paged list when either changes.
    dateRange: {
      deep: true,
      handler() {
        this.load({ reset: true })
      }
    },
    projectIds: {
      deep: true,
      handler() {
        this.load({ reset: true })
      }
    }
  },
  created() {
    this.load({ reset: true })
  },
  methods: {
    setRangeFilter(range) {
      const allowed = ['today', 'week', 'month', 'custom']
      this.rangeFilter = allowed.includes(range) ? range : 'month'
      if (this.rangeFilter === 'custom') {
        if (!this.customFromDate) this.customFromDate = daysAgo(30)
        if (!this.customToDate) this.customToDate = today()
      }
    },
    setCustomFrom(value) {
      this.customFromDate = value || null
      this.rangeFilter = 'custom'
    },
    setCustomTo(value) {
      this.customToDate = value || null
      this.rangeFilter = 'custom'
    },
    requestParams(page) {
      return {
        project_ids: this.projectIds.join(','),
        from_date: this.dateRange.from_date,
        to_date: this.dateRange.to_date,
        page,
        page_size: PAGE_SIZE,
        sort: 'updated_at',
        direction: 'desc',
        include_done: true
      }
    },
    applyPage(response, page, append = false) {
      const rows = (response?.tasks || []).map(toTask)
      if (append) {
        const existing = new Set(this.tasks.map(task => task.id))
        this.tasks = [...this.tasks, ...rows.filter(task => !existing.has(task.id))]
      } else {
        this.tasks = rows
      }
      this.page = Number(response?.page) || page
      this.total = Number(response?.total) || 0
      this.hasMore = !!response?.has_next
      this.serverMetrics = response?.metrics || {}
    },
    async load({ reset = false } = {}) {
      if (reset) {
        this.page = 0
        this.tasks = []
        this.total = 0
        this.hasMore = false
        this.serverMetrics = {}
        this.loadingMore = false
      }
      const token = ++this.loadToken
      this.loading = true
      this.error = null
      try {
        const response = await API.tasks.list(this.requestParams(1))
        if (token !== this.loadToken) return
        this.applyPage(response, 1)
      } catch (error) {
        if (token !== this.loadToken) return
        this.error = error?.response?.data?.detail || error?.message || String(error)
      } finally {
        if (token === this.loadToken) this.loading = false
      }
    },
    async loadNextPage() {
      if (this.loading || this.loadingMore || !this.hasMore) return
      const token = this.loadToken
      const nextPage = this.page + 1
      this.loadingMore = true
      this.error = null
      try {
        const response = await API.tasks.list(this.requestParams(nextPage))
        if (token !== this.loadToken) return
        this.applyPage(response, nextPage, true)
      } catch (error) {
        if (token !== this.loadToken) return
        this.error = error?.response?.data?.detail || error?.message || String(error)
      } finally {
        if (token === this.loadToken) this.loadingMore = false
      }
    },
    // Infinite scroll: load the next page when close to the bottom.
    onScroll(event) {
      const el = event.target
      if (!el) return
      const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distanceToBottom <= SCROLL_THRESHOLD) {
        this.loadNextPage()
      }
    },
    priorityMeta(priority) {
      return this.$storex.planning.priorityConfig(priority)
    },
    dueState(task) {
      if (!task.dueDate) return { label: 'no due date', class: 'text-base-content/40' }
      const today = new Date().toISOString().slice(0, 10)
      if (task.dueDate < today) return { label: `${this.daysLate(task.dueDate)} overdue`, class: 'text-error' }
      if (task.dueDate === today) return { label: 'due today', class: 'text-warning' }
      return { label: this.formatDueDate(task.dueDate), class: 'text-base-content/50' }
    },
    daysLate(dateStr) {
      const diff = Math.round((new Date(dateStr) - new Date(new Date().toISOString().slice(0, 10))) / 86400000)
      return Math.abs(diff) === 1 ? '1 day' : `${Math.abs(diff)} days`
    },
    formatDueDate(dateStr) {
      return new Date(dateStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    },
    progressClass(percentage) {
      if (percentage >= 100) return 'progress-success'
      if (percentage >= 60) return 'progress-primary'
      if (percentage >= 30) return 'progress-info'
      return 'progress-warning'
    },
    toneDot(tone) {
      return {
        info: 'bg-info',
        success: 'bg-success',
        primary: 'bg-primary',
        warning: 'bg-warning',
        error: 'bg-error'
      }[tone] || 'bg-base-content/30'
    }
  }
}
</script>
