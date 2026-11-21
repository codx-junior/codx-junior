<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import VerticalSplitter from '@/components/layout/VerticalSplitter.vue'
import ProjectMultiSelect from '@/components/common/ProjectMultiSelect.vue'
import MultiSelectFilter from '@/components/common/MultiSelectFilter.vue'
import ChatView from '@/views/ChatView.vue'

/**
 * Tasks — the main follow-up style task list.
 *
 * This screen keeps the visual language of the kanban "Follow up" view:
 * headline cards, a flat attention-oriented list and an activity-like reading
 * order. It is NOT a dashboard clone: the list is the working surface, and a
 * task opens in the right pane of a VerticalSplitter (the same interaction the
 * board has for its inline task detail).
 *
 * Scope
 * -----
 * The default project filter is the ACTIVE project **and its children**, so a
 * parent project's task list includes every sub-project. The user can switch
 * to "All projects" without leaving the screen.
 *
 * Scheduling
 * ----------
 * Scheduled tasks carry `wake_at`; the row shows the scheduled instant and the
 * autonomy worker wakes them later. The New chat modal is the entry point to
 * create one of those tasks.
 */

const PAGE_SIZE = 25

const PRIORITY_META = {
  high: { label: 'High', icon: 'fa-solid fa-flag', badge: 'border-error/30 bg-error/10 text-error', dot: 'bg-error' },
  medium: { label: 'Medium', icon: 'fa-solid fa-flag', badge: 'border-warning/30 bg-warning/10 text-warning', dot: 'bg-warning' },
  low: { label: 'Low', icon: 'fa-solid fa-flag', badge: 'border-success/30 bg-success/10 text-success', dot: 'bg-success' }
}

const STATUS_META = {
  todo: { label: 'To do', tone: 'badge-ghost' },
  doing: { label: 'In progress', tone: 'badge-info' },
  onhold: { label: 'On hold', tone: 'badge-warning' },
  done: { label: 'Done', tone: 'badge-success' },
  rejected: { label: 'Rejected', tone: 'badge-error' }
}

const loading = ref(false)
const error = ref('')
const result = ref({
  tasks: [],
  metrics: {},
  total: 0,
  page: 1,
  page_size: PAGE_SIZE,
  total_pages: 1,
  has_next: false,
  has_prev: false,
  projects: [],
  boards: [],
  columns: [],
  assignees: [],
  statuses: [],
  priorities: []
})

const filters = reactive({
  q: '',
  // Multi-select: ids of the projects shown in the table. Empty = every
  // visible project. Initialized to the active project + its children.
  project_ids: [],
  board_ids: [],
  columns: [],
  statuses: [],
  priorities: [],
  assignees: [],
  include_done: true
})

const page = ref(1)
const pageSize = ref(PAGE_SIZE)
const sort = ref('updated_at')
const direction = ref('desc')

const selectedTaskId = ref(null)
const sideTask = ref(null)
const sideLoading = ref(false)

let searchTimer = null

// ── Project scope ──────────────────────────────────────────────────────────
const activeProject = computed(() => $storex.projects.activeProject || null)

// The active project plus its children: the default Follow-up scope.
function defaultProjectIds() {
  const ids = []
  if (activeProject.value?.project_id) ids.push(activeProject.value.project_id)
  return ids
}

const scopeProjectIds = computed(() => (filters.project_ids || []).map(String))
const selectedProjectCount = computed(() => scopeProjectIds.value.length)
const scopeLabel = computed(() => {
  if (!scopeProjectIds.value.length) return 'All projects'
  if (scopeProjectIds.value.length === 1) {
    const project = projects.value.find(item => String(item.id) === scopeProjectIds.value[0])
    return project?.name || activeProject.value?.project_name || '1 project'
  }
  return `${scopeProjectIds.value.length} projects`
})
const defaultScopeIds = computed(() => defaultProjectIds().map(String))

// ── Result ─────────────────────────────────────────────────────────────────
const tasks = computed(() => result.value.tasks || [])
const metrics = computed(() => result.value.metrics || {})
const projects = computed(() => result.value.projects || [])
const allBoards = computed(() => result.value.boards || [])
const boards = computed(() => {
  const selected = new Set(scopeProjectIds.value)
  if (!selected.size) return allBoards.value
  return allBoards.value.filter(board => selected.has(String(board.project_id)))
})
const boardOptions = computed(() => boards.value.map(board => ({
  value: String(board.id),
  label: selectedProjectCount.value > 1 || !selectedProjectCount.value
    ? `${board.project_name || board.project_id} · ${board.title}`
    : board.title,
  color: board.project_color || '#888'
})))
const columns = computed(() => result.value.columns || [])
const assignees = computed(() => result.value.assignees || [])
const statuses = computed(() => (result.value.statuses || []).filter(Boolean))
const priorities = computed(() => (result.value.priorities || []).filter(Boolean))
const columnOptions = computed(() => columns.value.map(column => ({ value: column, label: column })))
const assigneeOptions = computed(() => assignees.value.map(assignee => ({ value: assignee, label: assignee })))
const statusOptions = computed(() => statuses.value.map(status => ({
  value: status,
  label: statusMeta(status).label
})))
const priorityOptions = computed(() => priorities.value.map(priority => ({
  value: priority,
  label: priorityMeta(priority).label
})))
const rangeLabel = computed(() => {
  if (!result.value.total) return 'No tasks'
  const first = (result.value.page - 1) * result.value.page_size + 1
  const last = Math.min(result.value.page * result.value.page_size, result.value.total)
  return `Showing ${first}–${last} of ${result.value.total}`
})
const hasFilters = computed(() =>
  Boolean(
    filters.q || filters.board_ids.length || filters.columns.length ||
    filters.statuses.length || filters.priorities.length ||
    filters.assignees.length || !filters.include_done ||
    scopeProjectIds.value.join(',') !== defaultScopeIds.value.join(',')
  )
)

function queryParams() {
  return {
    q: filters.q.trim(),
    project_ids: scopeProjectIds.value.join(','),
    board_ids: filters.board_ids.join(','),
    columns: filters.columns.join(','),
    statuses: filters.statuses.join(','),
    priorities: filters.priorities.join(','),
    assignees: filters.assignees.join(','),
    include_done: filters.include_done,
    page: page.value,
    page_size: pageSize.value,
    sort: sort.value,
    direction: direction.value
  }
}

async function load({ resetPage = false } = {}) {
  if (resetPage) page.value = 1
  loading.value = true
  error.value = ''
  try {
    result.value = await API.tasks.list(queryParams())
    // A selected task that no longer matches the filters is closed.
    if (sideTask.value && !tasks.value.some(task => task.id === sideTask.value.id)) {
      closeSideTask()
    }
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || String(ex)
  } finally {
    loading.value = false
  }
}

function scheduleSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => load({ resetPage: true }), 350)
}

function clearFilters() {
  Object.assign(filters, {
    q: '',
    project_ids: defaultScopeIds.value,
    board_ids: [],
    columns: [],
    statuses: [],
    priorities: [],
    assignees: [],
    include_done: true
  })
  load({ resetPage: true })
}

function goToPage(next) {
  if (next < 1 || next > result.value.total_pages || next === page.value) return
  page.value = next
  load()
}

function changeSort(field) {
  if (sort.value === field) {
    direction.value = direction.value === 'desc' ? 'asc' : 'desc'
  } else {
    sort.value = field
    direction.value = 'desc'
  }
  load({ resetPage: true })
}

// ── Formatting ─────────────────────────────────────────────────────────────
function priorityMeta(priority) {
  return PRIORITY_META[String(priority || '').toLowerCase()] || PRIORITY_META.low
}

function statusMeta(status) {
  return STATUS_META[String(status || '').toLowerCase()] || STATUS_META.todo
}

function initials(name) {
  return String(name || '?').slice(0, 1).toUpperCase()
}

function relativeTime(value) {
  if (!value) return ''
  const then = new Date(String(value).replace(' ', 'T')).getTime()
  if (Number.isNaN(then)) return value
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(then).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function taskAgeOpacity(task) {
  if (!task) return 1
  if (task.is_old) {
    const days = Number(task.age_days || 7)
    if (days >= 30) return 0.45
    if (days >= 14) return 0.6
    return 0.75
  }
  return 1
}

function dueState(task) {
  if (task.wake_at) {
    const wake = new Date(task.wake_at)
    if (!Number.isNaN(wake.getTime())) {
      return {
        label: `wakes ${wake.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`,
        class: 'text-info',
        icon: 'fa-regular fa-moon'
      }
    }
  }
  if (!task.due_date) {
    return { label: 'no due date', class: 'text-base-content/30', icon: 'fa-regular fa-calendar' }
  }
  const today = new Date().toISOString().slice(0, 10)
  if (task.status === 'done') {
    return { label: formatDate(task.due_date), class: 'text-success', icon: 'fa-solid fa-circle-check' }
  }
  if (task.due_date < today) {
    return { label: `Overdue · ${formatDate(task.due_date)}`, class: 'text-error', icon: 'fa-regular fa-clock' }
  }
  if (task.due_date === today) {
    return { label: 'Due today', class: 'text-warning', icon: 'fa-regular fa-clock' }
  }
  return { label: formatDate(task.due_date), class: 'text-base-content/50', icon: 'fa-regular fa-calendar' }
}

function formatDate(value) {
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function projectFor(task) {
  return ($storex.projects.allProjects || []).find(project =>
    project.project_id === task.project_id
  )
}

// ── Task selection / right pane ────────────────────────────────────────────
function isMobile() {
  return !!$storex.ui?.isMobile
}

async function selectTask(task) {
  if (!task?.id) return
  if (selectedTaskId.value === task.id) {
    closeSideTask()
    return
  }
  selectedTaskId.value = task.id
  if (isMobile()) {
    $storex.$router?.$navigation?.chats.open(task.id, task.name)
    return
  }
  sideTask.value = task
  sideLoading.value = true
  try {
    await $storex.chats.loadChat({
      id: task.id,
      owner_project_id: task.owner_project_id || task.project_id
    })
  } catch (ex) {
    $storex.ui.addNotification?.({
      text: ex?.message || 'Could not load the task',
      type: 'error'
    })
  } finally {
    sideLoading.value = false
  }
}

function closeSideTask() {
  selectedTaskId.value = null
  sideTask.value = null
  sideLoading.value = false
}

async function openBoard(task) {
  const title = String(task?.board || '').trim()
  if (!title) return
  const project = projectFor(task)
  if (project && activeProject.value?.project_id !== project.project_id) {
    try {
      await $storex.projects.activeProjectChanged(project)
    } catch (ex) {
      $storex.ui.addNotification?.({
        text: ex?.message || 'Could not switch to the task project',
        type: 'error'
      })
      return
    }
  }
  $storex.ui.openApp('tasks', { board: title, boardName: title })
}

// ── Splitter ───────────────────────────────────────────────────────────────
const splitterPanels = computed(() => ({
  left: { defaultSize: sideTask.value ? 62 : 100, minSize: 40 },
  right: { defaultSize: 38, minSize: 25 }
}))

watch(
  () => [
    filters.project_ids.join(','),
    filters.board_ids.join(','),
    filters.columns.join(','),
    filters.statuses.join(','),
    filters.priorities.join(','),
    filters.assignees.join(','),
    filters.include_done
  ],
  () => {
    // Boards that no longer belong to the selected projects are removed from
    // the selection instead of silently filtering the table to nothing.
    const validBoardIds = new Set(boards.value.map(board => String(board.id)))
    const nextBoardIds = filters.board_ids.filter(id => validBoardIds.has(String(id)))
    if (nextBoardIds.length !== filters.board_ids.length) {
      filters.board_ids = nextBoardIds
      return
    }
    load({ resetPage: true })
  }
)

watch(
  () => activeProject.value?.project_id,
  () => {
    closeSideTask()
    filters.project_ids = defaultScopeIds.value
    load({ resetPage: true })
  }
)

// ── Live updates ───────────────────────────────────────────────────────────
// There is no manual sync any more: the list loads on mount and then follows
// the project's socket events. Every card here is a projection of the chat /
// board state, so any of the events below means the list may be stale.
const TASK_SOCKET_EVENTS = [
  'codx-junior-tasks',
  'codx-junior-task',
  'codx-junior-board',
  'codx-junior-chat-turns',
  'codx-junior-orchestration'
]

const SOCKET_RELOAD_DELAY = 1200
let socketReloadTimer = null
let socketRetryTimer = null
let subscribedEvents = []

function scheduleSocketReload() {
  clearTimeout(socketReloadTimer)
  socketReloadTimer = setTimeout(() => {
    socketReloadTimer = null
    // A request is already in flight: its response is the fresh state.
    if (loading.value) return
    load()
  }, SOCKET_RELOAD_DELAY)
}

function subscribeTaskEvents() {
  const manager = API.socket
  if (!manager || subscribedEvents.length) return !!manager
  subscribedEvents = TASK_SOCKET_EVENTS
  subscribedEvents.forEach(event => manager.on(event, scheduleSocketReload))
  return true
}

function unsubscribeTaskEvents() {
  const manager = API.socket
  if (manager) subscribedEvents.forEach(event => manager.off(event))
  subscribedEvents = []
}

onMounted(() => {
  filters.project_ids = defaultScopeIds.value
  load()
  // The socket singleton may still be connecting: retry briefly until it exists.
  if (!subscribeTaskEvents()) {
    let attempts = 0
    socketRetryTimer = setInterval(() => {
      attempts += 1
      if (subscribeTaskEvents() || attempts >= 10) {
        clearInterval(socketRetryTimer)
        socketRetryTimer = null
      }
    }, 1000)
  }
})

onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  clearTimeout(socketReloadTimer)
  if (socketRetryTimer) clearInterval(socketRetryTimer)
  unsubscribeTaskEvents()
})
</script>

<template>
  <VerticalSplitter
    class="flex-1 min-h-0 h-full"
    :panels="splitterPanels"
    :split="!isMobile()"
    :show-handle="!isMobile()"
    persist-key="global-tasks"
  >
    <!-- ── Left: follow-up style task list, full width until a task opens ── -->
    <template #left>
      <div class="followup-view h-full w-full overflow-y-auto bg-base-100">
        <div class="p-4 lg:p-6 w-full">

          <!-- Header + filters -->
          <div class="flex flex-col gap-4 mb-5 xl:flex-row xl:items-center xl:justify-between">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h1 class="font-semibold text-lg">Follow-up</h1>
                <span class="badge badge-sm badge-ghost text-base-content/50 font-normal">
                  {{ rangeLabel }}
                </span>
                <span class="badge badge-sm badge-outline gap-1">
                  <i class="fa-solid fa-layer-group text-[9px]"></i>{{ scopeLabel }}
                </span>
              </div>
              <p class="text-xs text-base-content/50 mt-0.5">
                Everyone's latest work · unread, stopped tasks and old entries
              </p>
            </div>

            <div class="flex flex-nowrap items-center gap-2 overflow-x-auto pb-1">
              <label class="input input-sm input-bordered flex min-w-52 shrink-0 items-center gap-2">
                <i class="fa-solid fa-magnifying-glass text-xs opacity-40"></i>
                <input
                  v-model="filters.q"
                  type="search"
                  class="grow bg-transparent text-xs"
                  placeholder="Search tasks, boards, people…"
                  aria-label="Search tasks"
                  @input="scheduleSearch"
                />
              </label>

              <ProjectMultiSelect
                v-model="filters.project_ids"
                :projects="projects"
                placeholder="All projects"
                class="shrink-0"
              />

              <MultiSelectFilter
                v-model="filters.board_ids"
                :options="boardOptions"
                placeholder="All boards"
                all-label="All boards"
                icon="fa-solid fa-table-columns"
                search-placeholder="Search boards…"
                empty-text="No boards in the selected projects"
              />

              <MultiSelectFilter
                v-model="filters.columns"
                :options="columnOptions"
                placeholder="All columns"
                all-label="All columns"
                icon="fa-solid fa-table-list"
                search-placeholder="Search columns…"
                empty-text="No columns"
              />

              <MultiSelectFilter
                v-model="filters.statuses"
                :options="statusOptions"
                placeholder="All statuses"
                all-label="All statuses"
                icon="fa-solid fa-circle-dot"
                search-placeholder="Search statuses…"
                empty-text="No statuses"
              />

              <MultiSelectFilter
                v-model="filters.priorities"
                :options="priorityOptions"
                placeholder="All priorities"
                all-label="All priorities"
                icon="fa-solid fa-flag"
                search-placeholder="Search priorities…"
                empty-text="No priorities"
              />

              <MultiSelectFilter
                v-model="filters.assignees"
                :options="assigneeOptions"
                placeholder="All assignees"
                all-label="All assignees"
                icon="fa-solid fa-user"
                search-placeholder="Search people…"
                empty-text="No assignees"
              />

              <label class="flex cursor-pointer items-center gap-1.5 text-xs opacity-70">
                <input v-model="filters.include_done" type="checkbox" class="checkbox checkbox-xs" />
                Done
              </label>

              <button
                v-if="hasFilters"
                class="btn btn-ghost btn-sm btn-square"
                title="Clear filters"
                aria-label="Clear filters"
                @click="clearFilters"
              >
                <i class="fa-solid fa-eraser text-xs"></i>
              </button>

              <button
                class="btn btn-sm btn-outline shrink-0 gap-1.5"
                title="See every board and assign its responsible"
                @click="$ui.openApp('board-management')"
              >
                <i class="fa-solid fa-table-columns text-xs"></i>
                <span class="text-xs">Boards</span>
              </button>

              <button
                class="btn btn-primary btn-sm gap-1.5"
                title="New chat with codx-junior"
                @click="$storex.ui.showNewAssistantChat(true)"
              >
                <i class="fa-solid fa-plus text-xs"></i>
                <span class="hidden sm:inline">New chat</span>
              </button>
            </div>
          </div>

          <p v-if="error" class="text-xs text-error mb-4">
            <i class="fa-solid fa-circle-exclamation mr-1"></i>{{ error }}
          </p>

          <!-- Metric cards: the Follow-up view's headline numbers -->
          <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
            <div class="bg-base-200 border border-base-100 rounded-lg p-4">
              <div class="text-xs text-base-content/40 uppercase tracking-wide">Total</div>
              <div class="text-2xl font-bold mt-1">{{ metrics.total || 0 }}</div>
              <div class="text-xs text-base-content/50 mt-1">
                <i class="fa-solid fa-list-ul mr-1"></i>{{ scopeLabel }}
              </div>
            </div>
            <div class="bg-base-200 border border-base-100 rounded-lg p-4">
              <div class="text-xs text-base-content/40 uppercase tracking-wide">Active</div>
              <div class="text-2xl font-bold mt-1 text-info">{{ metrics.active || 0 }}</div>
              <div class="text-xs text-base-content/50 mt-1">in progress / todo</div>
            </div>
            <div class="bg-base-200 border border-base-100 rounded-lg p-4">
              <div class="text-xs text-base-content/40 uppercase tracking-wide">Done</div>
              <div class="text-2xl font-bold mt-1 text-success">{{ metrics.done || 0 }}</div>
              <div class="text-xs text-base-content/50 mt-1">{{ metrics.completion || 0 }}% completion</div>
            </div>
            <div class="bg-base-200 rounded-lg p-4" :class="metrics.overdue ? 'border border-error/30' : 'border border-base-100'">
              <div class="text-xs text-base-content/40 uppercase tracking-wide">Overdue</div>
              <div class="text-2xl font-bold mt-1 text-error">{{ metrics.overdue || 0 }}</div>
              <div class="text-xs mt-1" :class="metrics.overdue ? 'text-error/70' : 'text-base-content/50'">
                <i class="fa-solid fa-triangle-exclamation mr-1" v-if="metrics.overdue"></i>
                {{ metrics.overdue ? 'needs attention' : 'clear' }}
              </div>
            </div>
            <div class="bg-base-200 border border-base-100 rounded-lg p-4">
              <div class="text-xs text-base-content/40 uppercase tracking-wide">Due today</div>
              <div class="text-2xl font-bold mt-1 text-warning">{{ metrics.due_today || 0 }}</div>
              <div class="text-xs text-base-content/50 mt-1">focus today</div>
            </div>
          </div>

          <!-- Task list -->
          <section>
            <div class="flex items-center justify-between gap-2 mb-3">
              <h2 class="text-sm font-semibold flex items-center gap-2">
                <i class="fa-solid fa-list-check text-primary"></i> Latest tasks
              </h2>
              <span class="text-[11px] text-base-content/40">
                <i class="fa-solid fa-arrow-pointer mr-1"></i>click a task to open it on the right
              </span>
            </div>

            <div v-if="loading && !tasks.length" class="flex flex-col items-center gap-2 py-16 text-base-content/30">
              <span class="loading loading-spinner loading-sm text-primary"></span>
              <span class="text-sm">Loading tasks…</span>
            </div>

            <div v-else-if="!tasks.length" class="flex flex-col items-center gap-2 py-16 text-base-content/30">
              <i class="fa-solid fa-inbox text-3xl"></i>
              <span class="text-sm">No tasks match your filters</span>
              <button v-if="hasFilters" class="btn btn-sm mt-2" @click="clearFilters">Clear filters</button>
            </div>

            <div v-else class="space-y-1.5">
              <div
                v-for="task in tasks"
                :key="task.id"
                class="flex items-center gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors duration-150"
                :class="selectedTaskId === task.id
                  ? 'bg-primary/10 border-primary/30'
                  : task.is_old
                    ? 'bg-base-200/60 border-warning/20 hover:bg-base-200'
                    : 'bg-base-200 border-transparent hover:bg-base-300/60'"
                :style="{ opacity: selectedTaskId === task.id ? 1 : taskAgeOpacity(task) }"
                tabindex="0"
                role="button"
                :title="task.is_old ? `No activity for ${task.age_days} days` : ''"
                @click="selectTask(task)"
                @keydown.enter="selectTask(task)"
              >
                <span class="badge badge-sm gap-1 border shrink-0" :class="priorityMeta(task.priority).badge">
                  <i :class="`${priorityMeta(task.priority).icon} text-[10px]`"></i>
                  {{ priorityMeta(task.priority).label }}
                </span>

                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-sm font-medium truncate" :title="task.name">{{ task.name || 'Untitled task' }}</span>
                    <span class="badge badge-xs shrink-0" :class="statusMeta(task.status).tone">
                      {{ statusMeta(task.status).label }}
                    </span>
                    <span
                      v-if="task.process_state === 'error' || task.process_state === 'stopped'"
                      class="badge badge-xs gap-1 shrink-0"
                      :class="task.process_state === 'error' ? 'badge-error badge-outline' : 'badge-warning badge-outline'"
                      :title="task.process_error || (task.process_state === 'error' ? 'Process ended with an error' : 'Process was stopped')"
                    >
                      <i :class="task.process_state === 'error' ? 'fa-solid fa-triangle-exclamation' : 'fa-solid fa-stop'"></i>
                      {{ task.process_state === 'error' ? 'error' : 'stopped' }}
                    </span>
                    <span
                      v-if="task.unread_count > 0"
                      class="badge badge-xs badge-warning gap-1 shrink-0"
                      :title="`${task.unread_count} unread message(s)`"
                    >
                      <i class="fa-solid fa-envelope"></i>
                      {{ task.unread_count > 9 ? '9+' : task.unread_count }}
                    </span>
                    <span v-if="task.is_old" class="badge badge-xs badge-outline shrink-0" :title="`Last activity ${task.age_days} days ago`">
                      <i class="fa-regular fa-hourglass-half"></i>
                      {{ task.age_days }}d
                    </span>
                  </div>
                  <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-base-content/50 mt-1">
                    <span class="truncate">
                      <i class="fa-solid fa-cubes mr-1 text-[10px]"></i>{{ task.project_name }}
                    </span>
                    <span class="truncate">
                      <i class="fa-solid fa-table-columns mr-1 text-[10px]"></i>
                      <button
                        type="button"
                        class="link link-hover"
                        :title="task.board ? `Open board ${task.board}` : 'No board'"
                        @click.stop="openBoard(task)"
                      >{{ task.board || 'No board' }}</button>
                      <span class="opacity-40"> · </span>{{ task.column || '—' }}
                    </span>
                    <span v-if="task.responsible" class="truncate" :title="`${task.responsible}${task.responsible_source === 'parent' ? ' (inherited from parent board)' : ''}`">
                      <i class="fa-solid fa-user-shield mr-1 text-[10px]"></i>{{ task.responsible }}
                      <span v-if="task.responsible_source === 'parent'" class="opacity-50">· parent</span>
                    </span>
                  </div>
                  <div v-if="task.latest_message?.content" class="mt-1 truncate text-[11px] text-base-content/50" :title="task.latest_message.content">
                    <i
                      class="mr-1 text-[9px]"
                      :class="task.latest_message.role === 'assistant' ? 'fa-solid fa-robot' : 'fa-solid fa-user'"
                    ></i>
                    <span class="font-medium text-base-content/60">{{ task.latest_message.user || task.latest_message.role || 'system' }}</span>
                    <span>· {{ task.latest_message.content }}</span>
                    <span class="ml-1 opacity-50">{{ relativeTime(task.latest_message.created_at || task.latest_message.updated_at) }}</span>
                  </div>
                </div>

                <span class="flex items-center gap-1 shrink-0 text-xs" :class="dueState(task).class">
                  <i :class="dueState(task).icon"></i>{{ dueState(task).label }}
                </span>

                <div v-if="task.assignees?.length" class="avatar-group -space-x-2 shrink-0">
                  <div v-for="assignee in task.assignees.slice(0, 2)" :key="assignee" class="avatar">
                    <div class="w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-semibold flex items-center justify-center">
                      {{ initials(assignee) }}
                    </div>
                  </div>
                  <span v-if="task.assignees.length > 2" class="text-[10px] text-base-content/40 ml-1">
                    +{{ task.assignees.length - 2 }}
                  </span>
                </div>
                <span v-else class="badge badge-sm badge-ghost shrink-0 text-base-content/40">unassigned</span>

                <span class="text-[10px] text-base-content/30 shrink-0 w-14 text-right" :title="task.updated_at">
                  {{ relativeTime(task.updated_at) }}
                </span>

                <i class="fa-solid fa-chevron-right text-[9px] text-base-content/20 shrink-0"></i>
              </div>
            </div>

            <!-- Pagination footer -->
            <div class="flex flex-wrap items-center justify-between gap-2 py-6 text-xs text-base-content/40">
              <span>{{ rangeLabel }}</span>
              <div class="flex items-center gap-1">
                <select
                  v-model.number="pageSize"
                  class="select select-xs select-bordered"
                  aria-label="Rows per page"
                  @change="load({ resetPage: true })"
                >
                  <option :value="10">10</option>
                  <option :value="25">25</option>
                  <option :value="50">50</option>
                  <option :value="100">100</option>
                </select>
                <div class="join">
                  <button class="btn btn-xs join-item" :disabled="!result.has_prev || loading"
                          aria-label="Previous page" @click="goToPage(page - 1)">
                    <i class="fa-solid fa-chevron-left"></i>
                  </button>
                  <span class="join-item border border-base-300 px-2 py-1">
                    {{ result.page }} / {{ result.total_pages }}
                  </span>
                  <button class="btn btn-xs join-item" :disabled="!result.has_next || loading"
                          aria-label="Next page" @click="goToPage(page + 1)">
                    <i class="fa-solid fa-chevron-right"></i>
                  </button>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </template>

    <!-- ── Right: the selected task, same interaction as the board detail ── -->
    <template #right v-if="sideTask && !isMobile()">
      <div class="flex flex-col h-full min-h-0 w-full bg-base-100">
        <div class="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-base-100">
          <i class="fa-solid fa-list-check text-xs shrink-0 text-primary"></i>
          <span class="flex-1 min-w-0 text-sm font-semibold truncate" :title="sideTask.name">
            {{ sideTask.name || 'Task' }}
          </span>

          <button
            v-if="sideTask.board"
            class="btn btn-ghost btn-xs gap-1.5 text-base-content/50 hover:text-primary"
            title="Open the task board"
            aria-label="Open the task board"
            @click="openBoard(sideTask)"
          >
            <i class="fa-solid fa-up-right-from-square text-xs"></i>
          </button>

          <button
            class="btn btn-ghost btn-xs text-base-content/50 hover:text-error"
            title="Close"
            aria-label="Close task detail"
            @click="closeSideTask"
          >
            <i class="fa-solid fa-xmark text-xs"></i>
          </button>
        </div>

        <div class="flex-1 min-h-0 overflow-hidden">
          <div v-if="sideLoading" class="flex h-full items-center justify-center gap-2 text-sm text-base-content/40">
            <span class="loading loading-spinner loading-sm"></span>
            Loading task…
          </div>
          <ChatView
            v-else
            :key="sideTask.id"
            :chat="sideTask"
            class="h-full"
            @chats="closeSideTask"
          />
        </div>
      </div>
    </template>
  </VerticalSplitter>
</template>

<style scoped>
.followup-view {
  scrollbar-width: thin;
}
</style>

<!-- Made with ❤️ by codx-junior -->
