<script setup>
/**
 * DSH-like harness sidebar: workspaces (projects) + sessions + New Session.
 *
 * It is a lens over existing stores: `projects` comes from the projects store
 * and `sessions` from the harness store. Nothing is duplicated here — the
 * sidebar only groups and presents them.
 */
import { computed } from 'vue'
import moment from 'moment'

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
  'refresh'
])

const visibleProjects = computed(() => props.projects || [])
const sortedSessions = computed(() =>
  [...(props.sessions || [])].sort((a, b) => {
    const aLive = a.phase === 'running' ? 1 : 0
    const bLive = b.phase === 'running' ? 1 : 0
    if (aLive !== bLive) return bLive - aLive
    return String(b.last_activity_at || b.updated_at || '')
      .localeCompare(String(a.last_activity_at || a.updated_at || ''))
  })
)

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
      </template>
    </div>

    <!-- New Session -->
    <div class="shrink-0 px-2 pb-2">
      <button
        type="button"
        class="w-full flex items-center justify-center gap-2 rounded-lg border border-base-content/15 bg-base-100/70 hover:bg-base-content/10 transition-colors py-2 text-xs font-medium"
        :title="collapsed ? 'New session' : ''"
        @click="emit('new-session')"
      >
        <i class="fa-solid fa-plus text-[10px]"></i>
        <span v-if="!collapsed">New Session</span>
      </button>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto px-1.5 pb-2">
      <!-- Workspaces -->
      <div class="flex items-center gap-1 px-1.5 pt-2 pb-1">
        <span v-if="!collapsed" class="text-[10px] font-bold uppercase tracking-widest text-base-content/40">Workspaces</span>
        <span class="grow"></span>
        <button
          v-if="!collapsed"
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Refresh sessions"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <i class="fa-solid fa-rotate text-[10px]" :class="{ 'animate-spin': loading }"></i>
        </button>
      </div>
      <button
        v-for="project in visibleProjects"
        :key="project.project_id || project.project_name"
        type="button"
        class="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
        :class="project.project_id === activeProject?.project_id
          ? 'bg-base-content/10 text-base-content'
          : 'text-base-content/60 hover:bg-base-content/5 hover:text-base-content'"
        :title="project.project_name"
        @click="emit('select-project', project)"
      >
        <img
          v-if="!collapsed && projectIcon(project)"
          :src="projectIcon(project)"
          alt=""
          class="w-4 h-4 rounded shrink-0 object-cover"
        />
        <i v-else class="fa-regular fa-folder text-[11px] shrink-0"></i>
        <span v-if="!collapsed" class="text-xs truncate">{{ project.project_name }}</span>
      </button>

      <!-- Sessions -->
      <div class="flex items-center gap-1 px-1.5 pt-4 pb-1">
        <span v-if="!collapsed" class="text-[10px] font-bold uppercase tracking-widest text-base-content/40">
          Sessions
        </span>
        <span v-if="runningCount" class="badge badge-success badge-xs border-0 text-[9px]">{{ runningCount }}</span>
        <span class="grow"></span>
        <span v-if="!collapsed" class="text-[10px] text-base-content/30 tabular-nums">{{ sessions.length }}</span>
      </div>

      <template v-if="!collapsed">
        <button
          v-for="session in sortedSessions"
          :key="session.session_id"
          type="button"
          class="w-full flex items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
          :class="session.session_id === activeSessionId
            ? 'bg-base-content/10'
            : 'hover:bg-base-content/5'"
          :title="session.name"
          @click="emit('select-session', session.session_id)"
        >
          <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0" :class="phaseDotClass(session.phase)"></span>
          <span class="min-w-0 flex-1">
            <span class="block text-xs truncate" :class="session.phase === 'failed' ? 'text-error/90' : 'text-base-content/80'">
              {{ session.name }}
            </span>
            <span class="block text-[10px] text-base-content/35 truncate">
              {{ phaseLabel(session.phase) }}
              <template v-if="relativeTime(session.last_activity_at || session.updated_at)">
                · {{ relativeTime(session.last_activity_at || session.updated_at) }}
              </template>
            </span>
          </span>
        </button>

        <div v-if="!sessions.length && !loading" class="px-2 py-3 text-[11px] text-base-content/35 leading-relaxed">
          No sessions yet. Write a ticket to start one.
        </div>
        <div v-if="loading && !sessions.length" class="px-2 py-3">
          <span class="loading loading-spinner loading-xs"></span>
        </div>
      </template>

      <!-- Collapsed: one dot per session -->
      <template v-else>
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

    <!-- Footer: diagnostics + settings -->
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
    </div>
  </aside>
</template>
