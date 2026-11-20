<script setup>
import { computed, onMounted, ref } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import ProjectMultiSelect from '@/components/common/ProjectMultiSelect.vue'
import BoardResponsibilityModal from '@/components/kanban/BoardResponsibilityModal.vue'

const FLOWS = [
  { value: 'agile', label: 'Agile', hint: 'todo · doing · done · rejected · onhold' },
  { value: 'delivery', label: 'Delivery', hint: 'backlog · todo · doing · review · done' },
  { value: 'support', label: 'Support', hint: 'new · triage · waiting · resolved · closed' },
  { value: 'custom', label: 'Custom', hint: 'The board defines its own columns and statuses.' }
]

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const boards = ref([])
const teams = ref([])
const query = ref('')
const selectedProjects = ref([])
const onlyUnassigned = ref(false)
const flowFilter = ref('')
const editing = ref(null)

const activeProject = computed(() => $storex.projects.activeProject || null)
const childProjects = computed(() => $storex.projects.childProjects || [])
const hasActiveProject = computed(() => !!activeProject.value?.project_id)

function defaultProjectIds() {
  const ids = []
  if (activeProject.value?.project_id) ids.push(String(activeProject.value.project_id))
  childProjects.value.forEach(project => {
    if (project?.project_id) ids.push(String(project.project_id))
  })
  return ids
}

const projects = computed(() => {
  const seen = new Map()
  boards.value.forEach(board => {
    const id = String(board.project_id || '')
    if (!id || seen.has(id)) return
    seen.set(id, {
      id,
      name: board.project_name || id,
      color: board.project_color || '#888'
    })
  })
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name))
})

const scopedBoards = computed(() => {
  const selected = new Set(selectedProjects.value.map(String))
  if (!selected.size) return boards.value
  return boards.value.filter(board => selected.has(String(board.project_id)))
})

const filteredBoards = computed(() => {
  const term = query.value.trim().toLowerCase()
  return scopedBoards.value.filter(board => {
    if (onlyUnassigned.value && board.responsible_source === 'board') return false
    if (flowFilter.value && String(board.flow || 'agile') !== flowFilter.value) return false
    if (!term) return true
    return [
      board.title,
      board.project_name,
      board.parent_title,
      board.responsible_name,
      board.lead_name,
      board.flow
    ].filter(Boolean).join(' ').toLowerCase().includes(term)
  })
})

const sortedBoards = computed(() =>
  [...filteredBoards.value].sort((a, b) => {
    const project = String(a.project_name || '').localeCompare(String(b.project_name || ''))
    if (project) return project
    return String(a.title || '').localeCompare(String(b.title || ''))
  })
)

const summary = computed(() => ({
  total: scopedBoards.value.length,
  inherited: scopedBoards.value.filter(board => board.responsible_source === 'parent').length,
  explicit: scopedBoards.value.filter(board => board.responsible_source === 'board').length,
  defaulted: scopedBoards.value.filter(board => board.responsible_source === 'default').length,
  overdue: scopedBoards.value.reduce((sum, board) => sum + Number(board.overdue_tasks || 0), 0)
}))

const users = computed(() => ($storex.users.users || []).filter(user => user?.username))
const profiles = computed(() => ($storex.profiles.allProfiles || []).filter(profile => profile?.name))

function flowMeta(value) {
  return FLOWS.find(item => item.value === value) || FLOWS[0]
}

function boardProject(board) {
  return ($storex.projects.allProjects || []).find(project => project.project_id === board.project_id) || null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await API.tasks.boards()
    boards.value = result?.boards || []
    teams.value = result?.teams || []
    if (!selectedProjects.value.length) selectedProjects.value = defaultProjectIds()
    await loadProfilesForBoards()
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || 'Could not load the boards'
  } finally {
    loading.value = false
  }
}

async function loadProfilesForBoards() {
  const projectIds = [...new Set(boards.value.map(board => board.project_id).filter(Boolean))]
  await Promise.allSettled(projectIds.map(projectId => {
    const project = ($storex.projects.allProjects || []).find(item => item.project_id === projectId)
    return project ? $storex.profiles.loadProjectProfiles(project) : Promise.resolve([])
  }))
  if (!(($storex.users.users || []).length)) {
    await $storex.users.loadUsers().catch(() => {})
  }
}

function openEditor(board) {
  editing.value = board
}

async function saveResponsibility(payload) {
  if (!editing.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const result = await API.tasks.updateBoard(payload)
    const updated = result?.board
    if (updated) {
      boards.value = boards.value.map(board =>
        board.id === updated.id ? { ...board, ...updated } : board
      )
    }
    editing.value = null
    $storex.session.onInfo('Board responsibility saved')
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || 'Could not save the board'
  } finally {
    saving.value = false
  }
}

async function openBoard(board) {
  const project = boardProject(board)
  if (project && activeProject.value?.project_id !== project.project_id) {
    try {
      await $storex.projects.activeProjectChanged(project)
    } catch (ex) {
      $storex.session.onError?.('Could not switch to the board project')
      return
    }
  }
  $storex.ui.openApp('tasks', { board: board.title, boardName: board.title })
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto bg-base-100">
    <div class="mx-auto w-full max-w-7xl px-4 py-5 md:px-6">
      <header class="mb-4 flex flex-wrap items-start gap-3">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <h1 class="text-lg font-bold">Board management</h1>
            <span class="badge badge-sm badge-ghost">{{ summary.total }} boards</span>
            <span v-if="summary.overdue" class="badge badge-sm badge-error badge-outline">{{ summary.overdue }} overdue tasks</span>
          </div>
          <p class="mt-0.5 text-xs text-base-content/50">
            Assign the leader responsible for each board and its flow. Children without an explicit responsible inherit it.
          </p>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn btn-ghost btn-sm gap-1.5" @click="$ui.openApp('task-list')">
            <i class="fa-solid fa-list-check text-xs"></i>
            Follow-up
          </button>
          <button class="btn btn-ghost btn-sm btn-square" :disabled="loading" title="Refresh" @click="load">
            <i class="fa-solid fa-arrows-rotate text-xs" :class="{ 'animate-spin': loading }"></i>
          </button>
        </div>
      </header>

      <p v-if="error" class="mb-3 rounded-lg bg-error/10 p-2 text-xs text-error">{{ error }}</p>

      <!-- Filters: one row, like Follow-up -->
      <div class="mb-4 flex flex-nowrap items-center gap-2 overflow-x-auto pb-1">
        <ProjectMultiSelect v-model="selectedProjects" :projects="projects" placeholder="All projects" />
        <label class="input input-sm input-bordered flex min-w-52 shrink-0 items-center gap-2">
          <i class="fa-solid fa-magnifying-glass text-xs opacity-40"></i>
          <input v-model="query" type="search" class="grow bg-transparent text-xs" placeholder="Search board, responsible…" />
        </label>
        <select v-model="flowFilter" class="select select-sm select-bordered text-xs shrink-0">
          <option value="">All flows</option>
          <option v-for="flow in FLOWS" :key="flow.value" :value="flow.value">{{ flow.label }}</option>
        </select>
        <label class="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs opacity-70">
          <input v-model="onlyUnassigned" type="checkbox" class="checkbox checkbox-xs" />
          Inherited / no explicit responsible
        </label>
        <div class="ml-auto flex shrink-0 items-center gap-1.5 text-[11px] text-base-content/40">
          <span class="badge badge-xs badge-ghost">own {{ summary.explicit }}</span>
          <span class="badge badge-xs badge-info badge-outline">inherited {{ summary.inherited }}</span>
          <span v-if="summary.defaulted" class="badge badge-xs badge-warning badge-outline">default {{ summary.defaulted }}</span>
        </div>
      </div>

      <div v-if="loading" class="grid place-items-center py-20">
        <span class="loading loading-spinner loading-lg text-primary"></span>
      </div>

      <div v-else-if="!sortedBoards.length" class="rounded-xl border border-dashed border-base-content/20 py-16 text-center text-sm text-base-content/50">
        No boards match the filters.
      </div>

      <div v-else class="overflow-x-auto rounded-xl border border-base-content/10 bg-base-100">
        <table class="table table-sm">
          <thead>
            <tr class="text-[11px] uppercase tracking-wide text-base-content/40">
              <th>Board</th>
              <th>Flow</th>
              <th>Responsible</th>
              <th class="text-right">Tasks</th>
              <th class="text-right">Overdue</th>
              <th class="text-right">Unassigned</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="board in sortedBoards" :key="board.id" class="hover:bg-base-content/5">
              <td class="min-w-64">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full shrink-0" :style="{ backgroundColor: board.project_color || '#888' }"></span>
                  <div class="min-w-0">
                    <div class="flex items-center gap-1.5">
                      <span class="truncate text-sm font-semibold">{{ board.title }}</span>
                      <span v-if="board.parent_title" class="badge badge-xs badge-ghost" :title="`Child of ${board.parent_title}`">
                        <i class="fa-solid fa-sitemap text-[8px]"></i> {{ board.parent_title }}
                      </span>
                    </div>
                    <div class="truncate text-[11px] text-base-content/40">{{ board.project_name }}</div>
                  </div>
                </div>
              </td>
              <td>
                <span class="badge badge-sm badge-outline gap-1" :title="flowMeta(board.flow).hint">
                  <i class="fa-solid fa-route text-[9px]"></i>{{ flowMeta(board.flow).label }}
                </span>
              </td>
              <td class="min-w-56">
                <div class="flex items-center gap-2">
                  <i
                    class="text-xs"
                    :class="board.responsible_kind === 'user' ? 'fa-solid fa-user' : 'fa-solid fa-robot'"
                  ></i>
                  <span class="text-sm font-medium">{{ board.responsible_name || '—' }}</span>
                  <span
                    class="badge badge-xs"
                    :class="board.responsible_source === 'board'
                      ? 'badge-success badge-outline'
                      : (board.responsible_source === 'parent' ? 'badge-info badge-outline' : 'badge-ghost')"
                    :title="board.responsible_source === 'parent' ? 'Inherited from the parent board' : `Source: ${board.responsible_source || 'none'}`"
                  >
                    {{ board.responsible_source || 'none' }}
                  </span>
                </div>
                <div v-if="board.team_name" class="text-[11px] text-base-content/40">team · {{ board.team_name }}</div>
              </td>
              <td class="text-right text-sm">{{ board.task_count || 0 }}</td>
              <td class="text-right">
                <span class="text-sm" :class="board.overdue_tasks ? 'text-error font-semibold' : 'text-base-content/40'">
                  {{ board.overdue_tasks || 0 }}
                </span>
              </td>
              <td class="text-right">
                <span class="text-sm" :class="board.unassigned_tasks ? 'text-warning' : 'text-base-content/40'">
                  {{ board.unassigned_tasks || 0 }}
                </span>
              </td>
              <td class="text-right whitespace-nowrap">
                <button class="btn btn-ghost btn-xs gap-1" title="Assign responsible and flow" @click="openEditor(board)">
                  <i class="fa-solid fa-user-shield text-xs"></i>
                  Manage
                </button>
                <button class="btn btn-ghost btn-xs gap-1" title="Open the board" @click="openBoard(board)">
                  <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <BoardResponsibilityModal
        v-if="editing"
        :board="editing"
        :flows="FLOWS"
        :users="users"
        :profiles="profiles"
        :saving="saving"
        @close="editing = null"
        @save="saveResponsibility"
      />
    </div>
  </div>
</template>
