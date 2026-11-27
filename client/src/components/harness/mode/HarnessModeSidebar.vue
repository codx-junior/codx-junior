<script setup>
/**
 * DSH-like harness sidebar: ONE project root + the chats of that project.
 *
 * The selected project is the root of the tree (`ProjectDetailt` selector in the
 * composer switches it) and every harness session is a chat under that root, so
 * the sidebar never shows the flat list of every project again: a project is
 * only added as a new root when the user picks one to create a chat in.
 *
 * Everything is a lens over existing stores:
 *   • sessions → `store/harness.js` (GET /api/harness/sessions)
 *   • chat documents → the regular chats store (unread counts, previews)
 *   • project delete → `api.chats.delete` (the same call the chat UI uses)
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import moment from 'moment'
import { $storex } from '@/store'
import { countUnreadMessages } from '@/utils/chatUnread'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeSessionId: { type: String, default: '' },
  projects: { type: Array, default: () => [] },
  activeProject: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  runningCount: { type: Number, default: 0 }
})

const emit = defineEmits([
  'select-session',
  'new-session',
  'select-project',
  'toggle-collapse',
  'diagnostics',
  'settings',
  'refresh',
  'remove-session',
  'exit'
])

const router = useRouter()

// Search over the chats of the current root.
const search = ref('')
// The project root is collapsible: the tree is two levels (project → chats).
const rootCollapsed = ref(false)
// Project picker: `null` = closed, otherwise every project that is not the root.
const pickerOpen = ref(false)
// Chat pending deletion confirmation, and the one currently being deleted.
const confirmId = ref('')
const deletingId = ref('')

const rootProject = computed(() => props.activeProject || null)
const rootName = computed(() => rootProject.value?.project_name || 'No project')
const otherProjects = computed(() => {
  const rootId = rootProject.value?.project_id
  return (props.projects || []).filter(item => item.project_id !== rootId)
})

const username = computed(() => {
  const user = $storex.users?.user
  return user?.username || user?.id || ''
})

const sortedSessions = computed(() =>
  [...(props.sessions || [])].sort((a, b) => {
    const aLive = a.phase === 'running' ? 1 : 0
    const bLive = b.phase === 'running' ? 1 : 0
    if (aLive !== bLive) return bLive - aLive
    return String(b.last_activity_at || b.updated_at || '')
      .localeCompare(String(a.last_activity_at || b.last_activity_at || ''))
  })
)

const filteredSessions = computed(() => {
  const term = search.value.trim().toLowerCase()
  if (!term) return sortedSessions.value
  return sortedSessions.value.filter(session => [
    session.name,
    session.description,
    session.preview,
    session.model
  ].some(value => String(value || '').toLowerCase().includes(term)))
})

// ── Branch tree (project → branch → tasks) ──────────────────────────────────
// A session is bound to a branch (no worktree: it runs in the checkout on that
// branch), so the left bar groups the tasks per branch and several tickets can
// run per branch. A collapsed branch renders only its header row.
const branches = ref([])
const currentBranch = ref('')
const collapsedBranches = ref([])
const menuBranch = ref('')
const projectMenuOpen = ref(false)
const branchBusy = ref('')

function notify(text, type = 'error') {
  $storex.ui?.addNotification?.({ text: String(text || ''), type })
}

function apiError(error) {
  return error?.response?.data?.detail || error?.message || String(error)
}

function projectApi() {
  return rootProject.value?.$api || $storex.api
}

/** Branches of the project and the one checked out (the merge target). */
async function loadBranches() {
  const api = projectApi()
  if (!api?.repo?.branches) return
  try {
    const payload = await api.repo.branches()
    const list = (payload?.branches || payload || [])
      .filter(item => typeof item === 'string' || !item?.type || item.type === 'local')
      .map(item => (typeof item === 'string' ? item : item?.name))
      .filter(Boolean)
    branches.value = [...new Set(list)]
    currentBranch.value = String(payload?.active_branch || '').trim() || currentBranch.value
  } catch (error) {
    branches.value = []
  }
}

onMounted(() => { loadBranches() })

const treeRows = computed(() => {
  const term = search.value.trim().toLowerCase()
  const matches = session => [
    session.name,
    session.description,
    session.preview,
    session.model
  ].some(value => String(value || '').toLowerCase().includes(term))

  const groups = new Map()
  for (const session of sortedSessions.value) {
    const branch = String(session.branch || currentBranch.value || '').trim() || 'Unknown branch'
    if (!groups.has(branch)) groups.set(branch, [])
    groups.get(branch).push(session)
  }
  const active = String(currentBranch.value || '').trim()
  const order = [...groups.keys()].sort((a, b) => {
    if (a === active) return -1
    if (b === active) return 1
    return a.localeCompare(b)
  })

  const rows = []
  for (const branch of order) {
    const all = groups.get(branch)
    const visible = term ? all.filter(matches) : all
    if (term && !visible.length) continue
    const collapsed = collapsedBranches.value.includes(branch)
    rows.push({
      session_id: `branch:${branch}`,
      __branchHeader: true,
      __branch: branch,
      __branchCount: all.length,
      __branchRunning: all.filter(item => item.phase === 'running').length,
      __branchCurrent: branch === active,
      __branchCollapsed: collapsed
    })
    if (collapsed) continue
    for (const session of visible) rows.push({ ...session, __branch: branch })
  }
  return rows
})

// ── Branch node actions ─────────────────────────────────────────────────────

function toggleBranch(branch) {
  collapsedBranches.value = collapsedBranches.value.includes(branch)
    ? collapsedBranches.value.filter(item => item !== branch)
    : [...collapsedBranches.value, branch]
}

function toggleBranchMenu(branch) {
  projectMenuOpen.value = false
  menuBranch.value = menuBranch.value === branch ? '' : branch
}

function toggleProjectMenu() {
  menuBranch.value = ''
  projectMenuOpen.value = !projectMenuOpen.value
}

function closeMenus() {
  menuBranch.value = ''
  projectMenuOpen.value = false
}

/** View diff: the Git view, Review (compare) tab, branch vs the current one. */
function viewBranchDiff(branch) {
  closeMenus()
  $storex.ui.openGitView({
    tab: 'review',
    compareBranch: String(branch || ''),
    compareTo: currentBranch.value || ''
  })
}

/** Merge a branch into the checked out one. No worktree to delete. */
async function mergeBranch(branch, { close = false } = {}) {
  closeMenus()
  const source = String(branch || '').trim()
  const target = String(currentBranch.value || '').trim()
  if (!source || !target) return
  if (source === target) {
    notify(`'${source}' is already the current branch.`, 'warning')
    return
  }
  branchBusy.value = source
  try {
    const result = await projectApi().repo.merge({
      from_branch: source,
      to_branch: target,
      delete_branch: !!close,
      delete_worktree: false
    })
    if (result?.conflicted) {
      notify(`Merge of '${source}' has conflicts: resolve them in the Git view.`, 'warning')
    }
    await loadBranches()
    await $storex.harness?.loadSessions?.({ limit: 50 })
  } catch (error) {
    notify(apiError(error))
  } finally {
    branchBusy.value = ''
  }
}

/** Archive every task of a branch. */
async function archiveBranch(branch) {
  closeMenus()
  const name = String(branch || '').trim()
  if (!name) return
  branchBusy.value = name
  try {
    await $storex.harness.archiveSessions({ branch: name })
  } catch (error) {
    notify(apiError(error))
  } finally {
    branchBusy.value = ''
  }
}

/** Project menu: archive the done tasks, or every task. */
async function archiveProject({ all = false } = {}) {
  closeMenus()
  try {
    await $storex.harness.archiveSessions(all ? { allSessions: true } : { phase: 'done' })
  } catch (error) {
    notify(apiError(error))
  }
}

function chatDocument(sessionId) {
  return $storex.chats?.chats?.[sessionId] || null
}

/** Unread messages of one chat, using the shared unread rule. */
function unreadCount(session) {
  const doc = chatDocument(session.session_id)
  const messages = doc?.messages || []
  if (!messages.length || !username.value) return 0
  const count = countUnreadMessages(messages, username.value)
  return Number(count) || 0
}

/** Time taken by the session (backend `duration_seconds`, live while running). */
function durationSeconds(session) {
  const stored = Number(session.duration_seconds)
  if (Number.isFinite(stored) && stored > 0) return stored
  if (session.phase !== 'running') return 0
  const start = moment(session.created_at || session.updated_at)
  if (!start.isValid()) return 0
  return Math.max(0, moment().diff(start, 'seconds'))
}

function durationText(session) {
  const total = durationSeconds(session)
  if (!total) return ''
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  if (minutes < 60) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function relativeTime(value) {
  if (!value) return ''
  const time = moment(value)
  if (!time.isValid()) return ''
  return time.fromNow(true)
}

function phaseDotClass(phase) {
  if (phase === 'running') return 'bg-success animate-pulse'
  if (phase === 'failed') return 'bg-error'
  if (phase === 'done') return 'bg-base-content/30'
  return 'bg-base-content/20'
}

function phaseLabel(phase) {
  if (phase === 'running') return 'Running'
  if (phase === 'failed') return 'Failed'
  if (phase === 'done') return 'Done'
  return 'Idle'
}

function projectIcon(project) {
  return project?.project_icon || project?.icon || ''
}

function projectInitial(project) {
  return String(project?.project_name || '?').trim().charAt(0).toUpperCase()
}

// ── Actions ──────────────────────────────────────────────────────────────────

function toggleRoot() {
  rootCollapsed.value = !rootCollapsed.value
}

/** Add a project as a new root (and start a chat in it). */
function addProject() {
  if (!otherProjects.value.length) {
    pickerOpen.value = !pickerOpen.value
    return
  }
  pickerOpen.value = !pickerOpen.value
}

function pickProject(project) {
  pickerOpen.value = false
  emit('select-project', project)
  emit('new-session')
}

function askRemove(session) {
  confirmId.value = session.session_id
}

function cancelRemove() {
  confirmId.value = ''
}

/**
 * Remove a chat (session) through the regular chats API: a harness session *is*
 * a chat, so the same call the chat UI uses deletes it. The list is reloaded so
 * the root stops showing it immediately.
 */
async function removeSession(session) {
  const id = session.session_id
  const project = rootProject.value
  if (!id || deletingId.value) return
  deletingId.value = id
  confirmId.value = ''
  try {
    await project?.$api?.chats?.delete({ id })
    emit('remove-session', id)
    await $storex.harness?.loadSessions?.({ limit: 50 })
  } catch (error) {
    console.error('[harness sidebar] Failed to remove chat:', error)
  } finally {
    deletingId.value = ''
  }
}

/** Leave harness mode and go back to the workspace. */
function exitHarness() {
  emit('exit')
  try {
    router.push('/')
  } catch (error) {
    window.location.assign('/')
  }
}
</script>

<template>
  <aside
    class="h-full flex flex-col shrink-0 border-r border-base-content/10 bg-base-200/60 transition-all duration-200"
    :class="collapsed ? 'w-16' : 'w-[264px]'"
  >
    <!-- Header: logo + HARNESS badge + collapse -->
    <div class="shrink-0 px-2 py-3 flex items-center gap-2">
      <button
        type="button"
        class="btn btn-ghost btn-xs btn-square shrink-0"
        :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        @click="emit('toggle-collapse')"
      >
        <i :class="collapsed ? 'fa-solid fa-angles-right' : 'fa-solid fa-angles-left'" class="text-[11px] text-base-content/50"></i>
      </button>
      <template v-if="!collapsed">
        <span class="font-semibold text-sm tracking-tight truncate">codx-junior</span>
        <span class="badge badge-xs bg-base-content/15 border-0 text-[9px] font-bold tracking-widest">HARNESS</span>
        <span class="grow"></span>
        <button
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Back to workspace"
          @click="exitHarness"
        >
          <i class="fa-solid fa-right-from-bracket text-[10px] text-base-content/50"></i>
        </button>
      </template>
    </div>

    <!-- New chat -->
    <div class="shrink-0 px-2 pb-2">
      <button
        type="button"
        class="w-full flex items-center justify-center gap-2 rounded-lg border border-base-content/15 bg-base-100/70 hover:bg-base-content/10 transition-colors py-2 text-xs font-medium"
        :title="collapsed ? 'New chat' : ''"
        @click="emit('new-session')"
      >
        <i class="fa-solid fa-plus text-[10px]"></i>
        <span v-if="!collapsed">New chat</span>
      </button>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto px-1.5 pb-2">
      <!-- Project root header -->
      <div class="flex items-center gap-1 px-1.5 pt-2 pb-1">
        <button
          v-if="!collapsed"
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          :title="rootCollapsed ? 'Expand project' : 'Collapse project'"
          @click="toggleRoot"
        >
          <i :class="rootCollapsed ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'" class="text-[9px] text-base-content/40"></i>
        </button>
        <span v-if="!collapsed" class="text-[10px] font-bold uppercase tracking-widest text-base-content/40">Project</span>
        <span class="grow"></span>
        <button
          v-if="!collapsed"
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Add a project to create a chat in"
          @click="addProject"
        >
          <i class="fa-solid fa-folder-plus text-[10px]"></i>
        </button>
        <button
          v-if="!collapsed"
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Refresh chats"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <i class="fa-solid fa-rotate text-[10px]" :class="{ 'animate-spin': loading }"></i>
        </button>
        <button
          v-if="!collapsed"
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Project actions: archive done / archive all"
          @click="toggleProjectMenu"
        >
          <i class="fa-solid fa-ellipsis-vertical text-[10px]"></i>
        </button>
      </div>

      <!-- Project menu: archive the tasks of the whole tree -->
      <div
        v-if="!collapsed && projectMenuOpen"
        class="mb-1 rounded-md border border-base-content/10 bg-base-100/70 p-1"
      >
        <button
          type="button"
          class="w-full flex items-center gap-2 rounded px-1.5 py-1 text-left text-[11px] text-base-content/70 hover:bg-base-content/10"
          @click="archiveProject()"
        >
          <i class="fa-solid fa-box-archive text-[9px]"></i> Archive done
        </button>
        <button
          type="button"
          class="w-full flex items-center gap-2 rounded px-1.5 py-1 text-left text-[11px] text-base-content/70 hover:bg-base-content/10"
          @click="archiveProject({ all: true })"
        >
          <i class="fa-solid fa-boxes-stacked text-[9px]"></i> Archive all
        </button>
      </div>

      <!-- Project picker (add a root) -->
      <div v-if="!collapsed && pickerOpen" class="mb-1 rounded-md border border-base-content/10 bg-base-100/70 p-1">
        <button
          v-for="project in otherProjects"
          :key="project.project_id || project.project_name"
          type="button"
          class="w-full flex items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-base-content/70 hover:bg-base-content/10"
          :title="project.project_name"
          @click="pickProject(project)"
        >
          <img v-if="projectIcon(project)" :src="projectIcon(project)" alt="" class="w-3.5 h-3.5 rounded shrink-0 object-cover" />
          <span v-else class="w-3.5 h-3.5 rounded bg-base-content/15 text-[8px] flex items-center justify-center shrink-0">{{ projectInitial(project) }}</span>
          <span class="truncate">{{ project.project_name }}</span>
        </button>
        <div v-if="!otherProjects.length" class="px-1.5 py-1 text-[11px] text-base-content/40">
          No other project available
        </div>
      </div>

      <!-- Root (the selected project) -->
      <button
        type="button"
        class="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left bg-base-content/10 text-base-content"
        :title="rootName"
        @click="toggleRoot"
      >
        <img
          v-if="!collapsed && projectIcon(rootProject)"
          :src="projectIcon(rootProject)"
          alt=""
          class="w-4 h-4 rounded shrink-0 object-cover"
        />
        <i v-else class="fa-solid fa-folder-open text-[11px] shrink-0"></i>
        <span v-if="!collapsed" class="text-xs truncate grow">{{ rootName }}</span>
        <span v-if="!collapsed && sessions.length" class="text-[10px] text-base-content/40 tabular-nums">{{ sessions.length }}</span>
        <span v-if="runningCount" class="badge badge-success badge-xs border-0 text-[9px]">{{ runningCount }}</span>
      </button>

      <template v-if="!collapsed && !rootCollapsed">
        <!-- Chat search -->
        <div class="px-1.5 pt-2">
          <input
            v-model="search"
            type="search"
            class="input input-xs w-full bg-base-100/70 border border-base-content/10 rounded-md text-[11px]"
            placeholder="Search chats"
            aria-label="Search chats"
          />
        </div>

        <!-- Chats under the project root -->
        <div class="mt-1 flex flex-col">
          <div
            v-for="session in treeRows"
            :key="session.session_id"
            class="group rounded-md transition-colors"
            :class="session.__branchHeader
              ? ''
              : (session.session_id === activeSessionId ? 'bg-base-content/10' : 'hover:bg-base-content/5')"
          >
            <!-- Branch node: collapsible, with its own submenu -->
            <template v-if="session.__branchHeader">
              <div class="flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-base-content/5">
                <button
                  type="button"
                  class="flex items-center gap-1.5 min-w-0 flex-1 text-left"
                  :title="session.__branch"
                  @click="toggleBranch(session.__branch)"
                >
                  <i
                    :class="session.__branchCollapsed ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"
                    class="text-[8px] text-base-content/35 shrink-0"
                  ></i>
                  <i class="fa-solid fa-code-branch text-[10px] text-base-content/40 shrink-0"></i>
                  <span
                    class="text-[11px] font-medium truncate"
                    :class="session.__branchCurrent ? 'text-base-content' : 'text-base-content/70'"
                  >{{ session.__branch }}</span>
                  <span class="text-[10px] text-base-content/35 tabular-nums">{{ session.__branchCount }}</span>
                  <span v-if="session.__branchRunning" class="badge badge-success badge-xs border-0 text-[9px]">{{ session.__branchRunning }}</span>
                </button>
                <button
                  type="button"
                  class="btn btn-ghost btn-xs btn-square opacity-0 group-hover:opacity-100 focus:opacity-100"
                  :title="`Branch actions: ${session.__branch}`"
                  @click.stop="toggleBranchMenu(session.__branch)"
                >
                  <i class="fa-solid fa-ellipsis text-[10px] text-base-content/40"></i>
                </button>
              </div>

              <!-- Branch submenu -->
              <div
                v-if="menuBranch === session.__branch"
                class="mx-1.5 mb-1 rounded-md border border-base-content/10 bg-base-100/90 py-0.5 shadow-sm"
              >
                <button type="button" class="w-full flex items-center gap-2 px-2 py-1 text-left text-[11px] text-base-content/70 hover:bg-base-content/10" @click="viewBranchDiff(session.__branch)">
                  <i class="fa-solid fa-code-compare text-[9px]"></i> View diff
                </button>
                <button type="button" class="w-full flex items-center gap-2 px-2 py-1 text-left text-[11px] text-base-content/70 hover:bg-base-content/10" :disabled="!!branchBusy" @click="mergeBranch(session.__branch)">
                  <i class="fa-solid fa-code-merge text-[9px]"></i> Merge
                </button>
                <button type="button" class="w-full flex items-center gap-2 px-2 py-1 text-left text-[11px] text-base-content/70 hover:bg-base-content/10" :disabled="!!branchBusy" @click="mergeBranch(session.__branch, { close: true })">
                  <i class="fa-solid fa-code-merge text-[9px]"></i> Merge and close
                </button>
                <div class="my-0.5 border-t border-base-content/10"></div>
                <button type="button" class="w-full flex items-center gap-2 px-2 py-1 text-left text-[11px] text-base-content/50 hover:bg-base-content/10" :disabled="!!branchBusy" @click="archiveBranch(session.__branch)">
                  <i class="fa-solid fa-box-archive text-[9px]"></i> Archive
                </button>
              </div>
            </template>

            <template v-else>
            <div class="flex items-start gap-2 px-2 py-1.5">
              <button
                type="button"
                class="flex items-start gap-2 min-w-0 flex-1 text-left"
                :title="session.name"
                @click="emit('select-session', session.session_id)"
              >
                <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0" :class="phaseDotClass(session.phase)"></span>
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-1 min-w-0">
                    <span class="block text-xs truncate" :class="session.phase === 'failed' ? 'text-error/90' : 'text-base-content/80'">
                      {{ session.name }}
                    </span>
                    <span
                      v-if="unreadCount(session)"
                      class="badge badge-warning badge-xs border-0 text-[9px] shrink-0"
                      :title="`${unreadCount(session)} unread message(s)`"
                    >{{ unreadCount(session) }}</span>
                  </span>
                  <span class="block text-[10px] text-base-content/35 truncate">
                    {{ phaseLabel(session.phase) }}
                    <template v-if="durationText(session)"> · {{ durationText(session) }}</template>
                    <template v-if="relativeTime(session.last_activity_at || session.updated_at)">
                      · {{ relativeTime(session.last_activity_at || session.updated_at) }}
                    </template>
                  </span>
                </span>
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs btn-square opacity-0 group-hover:opacity-100 focus:opacity-100"
                title="Remove chat"
                :disabled="deletingId === session.session_id"
                @click.stop="askRemove(session)"
              >
                <i class="fa-solid fa-trash-can text-[9px] text-base-content/40"></i>
              </button>
            </div>

            <!-- Confirm removal -->
            <div
              v-if="confirmId === session.session_id"
              class="mx-2 mb-1.5 rounded-md border border-error/30 bg-error/5 px-2 py-1.5"
            >
              <div class="text-[10px] text-base-content/60 mb-1">Remove this chat?</div>
              <div class="flex items-center gap-1 justify-end">
                <button type="button" class="btn btn-ghost btn-xs text-[10px]" @click="cancelRemove">Cancel</button>
                <button type="button" class="btn btn-error btn-xs text-[10px]" @click="removeSession(session)">Remove</button>
              </div>
            </div>
            </template>
          </div>

          <div v-if="!filteredSessions.length && !loading" class="px-2 py-3 text-[11px] text-base-content/35 leading-relaxed">
            {{ search ? 'No chat matches the search.' : 'No chats yet. Write a ticket to start one.' }}
          </div>
          <div v-if="loading && !sessions.length" class="px-2 py-3">
            <span class="loading loading-spinner loading-xs"></span>
          </div>
        </div>
      </template>

      <!-- Collapsed: one dot per chat -->
      <template v-else-if="!collapsed">
        <button
          v-for="session in sortedSessions.slice(0, 14)"
          :key="session.session_id"
          type="button"
          class="w-full flex justify-center py-1.5"
          :title="session.name"
          @click="emit('select-session', session.session_id)"
        >
          <span class="w-2 h-2 rounded-full" :class="phaseDotClass(session.phase)"></span>
        </button>
      </template>
    </div>

    <!-- Footer: diagnostics + settings + workspace -->
    <div class="shrink-0 border-t border-base-content/10 px-2 py-2 flex flex-col gap-1">
      <button
        type="button"
        class="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-base-content/50 hover:bg-base-content/5 hover:text-base-content transition-colors"
        title="Harness diagnostics (runs, worker, audit)"
        @click="emit('diagnostics')"
      >
        <i class="fa-solid fa-heart-pulse text-[11px] shrink-0"></i>
        <span v-if="!collapsed" class="text-[11px]">Diagnostics</span>
      </button>
      <button
        type="button"
        class="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-base-content/50 hover:bg-base-content/5 hover:text-base-content transition-colors"
        title="Project settings"
        @click="emit('settings')"
      >
        <i class="fa-solid fa-sliders text-[11px] shrink-0"></i>
        <span v-if="!collapsed" class="text-[11px]">Settings</span>
      </button>
      <button
        type="button"
        class="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-base-content/50 hover:bg-base-content/5 hover:text-base-content transition-colors"
        title="Leave harness and return to the workspace"
        @click="exitHarness"
      >
        <i class="fa-solid fa-table-columns text-[11px] shrink-0"></i>
        <span v-if="!collapsed" class="text-[11px]">Workspace mode</span>
      </button>
    </div>
  </aside>
</template>
