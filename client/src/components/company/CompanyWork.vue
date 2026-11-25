<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useCompanyI18n } from './i18n'
import { API } from '@/api/api'
import { $storex } from '@/store'
import {
  MISSION_COLUMNS,
  MISSION_OTHER_STATUSES,
  formatDate,
  formatDateTime,
  priorityMeta,
  statusMeta
} from './companyMeta'

/**
 * Work — goals and the execution board.
 *
 * A mission is the company-level goal. This view shows where every goal is,
 * lets the owner dispatch, approve and review it, and surfaces the team's
 * evidence instead of a raw task list.
 */
const { t } = useCompanyI18n()

const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false },
  focusId: { type: String, default: '' }
})

const emit = defineEmits(['assistant', 'new-goal', 'edit-goal', 'refresh'])

const api = API.companies
const busy = ref(false)
const filter = ref('all')
const selectedId = ref('')
const action = reactive({ open: false, type: '', mission: null, title: '', hint: '', value: '', error: '' })

const company = computed(() => props.snapshot?.company || {})
const teams = computed(() => props.snapshot?.teams || [])
const projects = computed(() => props.snapshot?.projects || [])
const missions = computed(() => props.snapshot?.missions || [])
const journal = computed(() => props.snapshot?.journal || [])

const selected = computed(() => missions.value.find(mission => mission.id === selectedId.value) || null)
const selectedTimeline = computed(() => journal.value.filter(event => event.mission_id === selectedId.value).slice(-12).reverse())

watch(() => [props.focusId, props.snapshot], ([id]) => {
  if (id && missions.value.some(mission => mission.id === id)) selectedId.value = id
}, { immediate: true })

const attention = computed(() => missions.value.filter(mission =>
  mission.status === 'blocked'
  || mission.status === 'review'
  || (['ready', 'blocked'].includes(mission.status) && ['pending', 'denied'].includes(mission.approval_status))
  || Boolean(mission.last_error)
))

const filtered = computed(() => {
  if (filter.value === 'attention') return attention.value
  if (filter.value === 'all') return missions.value
  if (filter.value === 'done') return missions.value.filter(mission => ['done', 'cancelled'].includes(mission.status))
  return missions.value.filter(mission => mission.status === filter.value)
})

const otherMissions = computed(() => filtered.value.filter(mission => MISSION_OTHER_STATUSES.includes(mission.status)))
const columnMissions = computed(() => Object.fromEntries(
  MISSION_COLUMNS.map(column => [column.id, filtered.value.filter(mission => mission.status === column.id)])
))

const counts = computed(() => ({
  all: missions.value.length,
  attention: attention.value.length,
  running: missions.value.filter(m => m.status === 'running').length,
  review: missions.value.filter(m => m.status === 'review').length,
  done: missions.value.filter(m => m.status === 'done').length
}))

const filters = computed(() => [
  { id: 'all', label: t('work.allGoals'), count: counts.value.all },
  { id: 'attention', label: t('work.needsMe'), count: counts.value.attention },
  { id: 'running', label: t('status.running'), count: counts.value.running },
  { id: 'review', label: t('work.toReview'), count: counts.value.review },
  { id: 'done', label: t('work.done'), count: counts.value.done }
])

function notify(text, type = 'info') {
  try { $storex.ui.addNotification({ text, type }) } catch { /* best effort */ }
}

function errorMessage(error) {
  return error?.response?.data?.detail || error?.message || String(error)
}

function missionTeam(mission) {
  return teams.value.find(team => team.id === mission.team_id)?.name || 'No team'
}

function missionProject(mission) {
  return projects.value.find(project => project.project_id === mission.project_id)?.name
    || mission.project_id
    || 'No project'
}

function openMission(mission) {
  if (!mission) return
  selectedId.value = mission.id
}

function closeDrawer() {
  selectedId.value = ''
}

function ask(prompt) {
  emit('assistant', prompt)
}

function startEdit(mission) {
  emit('edit-goal', mission)
}

function openAction(type, mission) {
  action.open = true
  action.type = type
  action.mission = mission
  action.value = ''
  action.error = ''
  const copy = {
    approve: { title: 'Approve this goal', hint: 'Optional note for the team. The goal can start as soon as it is approved.' },
    deny: { title: 'Send the goal back', hint: 'Tell the team what must change before it can start.' },
    accept: { title: 'Accept the delivery', hint: 'What was the outcome? This is stored as the company result.' },
    reject: { title: 'Request changes', hint: 'What is missing or wrong? The team will see this and rework it.' },
    cancel: { title: 'Cancel this goal', hint: 'Why is it no longer needed? The history is kept.' }
  }[type] || { title: 'Add a note', hint: '' }
  action.title = copy.title
  action.hint = copy.hint
}

function closeAction() {
  action.open = false
  action.mission = null
  action.error = ''
}

async function submitAction() {
  if (!action.mission) return
  const mission = action.mission
  busy.value = true
  action.error = ''
  try {
    if (action.type === 'approve') await api.approveMission(props.companyId, mission.id, action.value)
    else if (action.type === 'deny') await api.denyMission(props.companyId, mission.id, action.value)
    else if (action.type === 'accept') await api.acceptMission(props.companyId, mission.id, action.value)
    else if (action.type === 'reject') await api.rejectMission(props.companyId, mission.id, action.value)
    else if (action.type === 'cancel') await api.transitionMission(props.companyId, mission.id, 'cancelled')
    closeAction()
    emit('refresh')
    notify('Goal updated ✓', 'success')
  } catch (error) {
    action.error = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function dispatch(mission) {
  busy.value = true
  try {
    await api.dispatchMission(props.companyId, mission.id)
    emit('refresh')
    notify('Goal dispatched to the team ✓', 'success')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function transition(mission, status) {
  busy.value = true
  try {
    await api.transitionMission(props.companyId, mission.id, status)
    emit('refresh')
    notify('Goal moved ✓', 'success')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function sync() {
  busy.value = true
  try {
    await api.syncMissions(props.companyId)
    emit('refresh')
    notify('Progress synced from the project runs ✓', 'success')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

function eventTone(event) {
  return String(event.type || '').includes('failed') ? 'text-error-ink' : 'text-primary-ink'
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-xs font-bold uppercase tracking-[0.2em] text-primary-ink">Execution</p>
        <h1 class="mt-1 text-2xl font-black tracking-tight md:text-3xl">{{ t('nav.work') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          Every goal has an owner, a deadline and evidence. Start it, review it, accept it — or ask the assistant why it is stuck.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-sm gap-1.5 rounded-xl border-base-content/15" :disabled="busy" @click="sync">
          <i class="fa-solid fa-rotate" :class="busy ? 'animate-spin' : ''"></i> {{ t('work.sync') }}
        </button>
        <button class="btn btn-sm gap-1.5 rounded-xl border-base-content/15" @click="ask('Review the work in progress and tell me what needs my attention')">
          <i class="fa-solid fa-wand-magic-sparkles"></i> {{ t('work.askAi') }}
        </button>
        <button v-if="canManage" class="btn btn-primary btn-sm gap-1.5 rounded-xl shadow-lg shadow-primary/20" @click="emit('new-goal')">
          <i class="fa-solid fa-plus"></i> {{ t('shell.newGoal') }}
        </button>
      </div>
    </header>

    <div class="flex flex-wrap items-center gap-1.5">
      <button v-for="item in filters" :key="item.id"
              class="btn btn-xs rounded-full border normal-case"
              :class="filter === item.id ? 'border-primary bg-primary text-primary-content' : 'border-base-content/15 bg-base-100 text-base-content/60'"
              @click="filter = item.id">
        {{ item.label }}
        <span class="badge badge-xs border-0" :class="filter === item.id ? 'bg-primary-content/20' : 'bg-base-content/10'">{{ item.count }}</span>
      </button>
    </div>

    <!-- Blocked / paused row -->
    <section v-if="otherMissions.length" class="rounded-2xl border border-warning/20 bg-warning/5 p-3">
      <div class="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] text-warning-ink">
        <i class="fa-solid fa-triangle-exclamation"></i> Outside the main flow
      </div>
      <div class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <button v-for="mission in otherMissions" :key="mission.id"
                class="rounded-xl border border-warning/20 bg-base-100 p-3 text-left hover:border-warning/50"
                @click="openMission(mission)">
          <div class="flex items-center gap-2">
            <span class="badge badge-xs" :class="statusMeta(mission.status).badge">{{ statusMeta(mission.status).label }}</span>
            <span class="truncate text-xs font-bold">{{ mission.title }}</span>
          </div>
          <p class="mt-1 truncate text-xs text-base-content/45">{{ mission.last_error || missionTeam(mission) }}</p>
        </button>
      </div>
    </section>

    <!-- Board -->
    <section class="rounded-2xl border border-base-content/10 bg-base-100 p-3 md:overflow-x-auto">
      <div class="grid grid-cols-1 gap-2.5 md:min-w-[1000px] md:grid-cols-5">
        <div v-for="column in MISSION_COLUMNS" :key="column.id" class="rounded-xl bg-base-200/35 p-2">
          <div class="mb-2 flex items-center justify-between px-1">
            <div class="flex items-center gap-1.5">
              <span class="h-1.5 w-1.5 rounded-full" :class="column.id === 'running' ? 'bg-primary' : column.id === 'review' ? 'bg-warning' : column.id === 'done' ? 'bg-success' : 'bg-base-content/30'"></span>
              <span class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/50">{{ t(`status.${column.id}`) }}</span>
            </div>
            <span class="text-xs text-base-content/35">{{ (columnMissions[column.id] || []).length }}</span>
          </div>
          <p class="mb-2 px-1 text-[11px] text-base-content/30">{{ column.hint }}</p>

          <div class="space-y-2">
            <button v-for="mission in columnMissions[column.id]" :key="mission.id"
                    class="group w-full rounded-xl border border-base-content/10 bg-base-100 p-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
                    @click="openMission(mission)">
              <div class="flex items-start gap-1.5">
                <span class="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="mission.priority === 'critical' ? 'bg-error' : mission.priority === 'high' ? 'bg-warning' : mission.priority === 'low' ? 'bg-base-content/30' : 'bg-info'"></span>
                <span class="line-clamp-2 text-xs font-bold leading-snug">{{ mission.title }}</span>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-1">
                <span class="badge badge-xs border-0 bg-base-200 text-[10px]">{{ missionTeam(mission) }}</span>
                <span v-if="mission.approval_status === 'pending'" class="badge badge-xs badge-warning gap-0.5"><i class="fa-solid fa-stamp text-[10px]"></i>approve</span>
                <span v-if="mission.run_status" class="badge badge-xs badge-ghost">{{ mission.run_status }}</span>
              </div>
              <div class="mt-2 flex items-center justify-between text-[11px] text-base-content/40">
                <span>{{ mission.due_date ? formatDate(mission.due_date) : 'No deadline' }}</span>
                <span>{{ mission.progress || 0 }}%</span>
              </div>
              <div class="mt-1.5 h-1 overflow-hidden rounded-full bg-base-content/10">
                <div class="h-full rounded-full" :class="mission.status === 'blocked' ? 'bg-error' : mission.status === 'review' ? 'bg-warning' : 'bg-primary'" :style="{ width: `${Math.max(4, mission.progress || 0)}%` }"></div>
              </div>
            </button>

            <div v-if="!(columnMissions[column.id] || []).length" class="rounded-lg border border-dashed border-base-content/15 p-4 text-center text-[11px] text-base-content/30">
              {{ t('work.empty') }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <p class="text-xs text-base-content/35">
      <i class="fa-solid fa-circle-info mr-1"></i>
      A goal in <strong>Running</strong> is being executed by the team. <strong>Review</strong> means the AI team has delivered and is waiting for a human decision.
    </p>

    <!-- ── Mission drawer ─────────────────────────────────────────────── -->
    <div v-if="selected" class="fixed inset-0 z-[85] flex justify-end">
      <div class="absolute inset-0 bg-base-300/60 backdrop-blur-sm" @click="closeDrawer"></div>
      <aside class="relative flex h-full w-full max-w-2xl flex-col border-l border-base-content/10 bg-base-100 shadow-2xl">
        <header class="shrink-0 border-b border-base-content/10 bg-gradient-to-r from-primary/10 to-transparent p-4 md:p-5">
          <div class="flex items-start gap-2">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="badge badge-sm gap-1" :class="statusMeta(selected.status).badge">
                  <i :class="statusMeta(selected.status).icon" class="text-[10px]"></i>{{ statusMeta(selected.status).label }}
                </span>
                <span class="badge badge-sm" :class="priorityMeta(selected.priority).badge">{{ priorityMeta(selected.priority).label }}</span>
                <span v-if="selected.approval_status === 'pending'" class="badge badge-sm badge-warning">approval needed</span>
              </div>
              <h2 class="mt-2 text-lg font-black leading-tight">{{ selected.title }}</h2>
              <p class="mt-1 text-xs text-base-content/45">{{ missionTeam(selected) }} · {{ missionProject(selected) }}</p>
            </div>
            <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="closeDrawer"><i class="fa-solid fa-xmark"></i></button>
          </div>

          <div class="mt-3 flex flex-wrap gap-1.5">
            <button v-if="canManage && selected.status === 'draft'" class="btn btn-primary btn-xs gap-1" :disabled="busy"
                    @click="transition(selected, 'ready')">
              <i class="fa-solid fa-circle-check"></i> Mark ready
            </button>
            <button v-if="canManage && ['ready', 'blocked'].includes(selected.status) && selected.approval_status !== 'pending'"
                    class="btn btn-primary btn-xs gap-1" :disabled="busy" @click="dispatch(selected)">
              <i class="fa-solid fa-play"></i> Start work
            </button>
            <button v-if="canManage && selected.approval_status === 'pending'" class="btn btn-warning btn-xs gap-1" @click="openAction('approve', selected)">
              <i class="fa-solid fa-stamp"></i> Approve
            </button>
            <button v-if="canManage && selected.approval_status === 'pending'" class="btn btn-ghost btn-xs gap-1" @click="openAction('deny', selected)">
              <i class="fa-solid fa-rotate-left"></i> Send back
            </button>
            <button v-if="canManage && selected.status === 'running'" class="btn btn-ghost btn-xs gap-1" :disabled="busy" @click="transition(selected, 'paused')">
              <i class="fa-solid fa-pause"></i> Pause
            </button>
            <button v-if="canManage && selected.status === 'paused'" class="btn btn-ghost btn-xs gap-1" :disabled="busy" @click="transition(selected, 'running')">
              <i class="fa-solid fa-play"></i> Resume
            </button>
            <button v-if="canManage && selected.status === 'review'" class="btn btn-success btn-xs gap-1" @click="openAction('accept', selected)">
              <i class="fa-solid fa-circle-check"></i> Accept delivery
            </button>
            <button v-if="canManage && selected.status === 'review'" class="btn btn-ghost btn-xs gap-1" @click="openAction('reject', selected)">
              <i class="fa-solid fa-rotate-left"></i> Request changes
            </button>
            <button v-if="canManage" class="btn btn-ghost btn-xs gap-1" @click="startEdit(selected)">
              <i class="fa-solid fa-pen"></i> Edit
            </button>
            <button class="btn btn-ghost btn-xs gap-1" @click="ask(`Review the goal ${selected.title} and tell me what needs my attention`)">
              <i class="fa-solid fa-wand-magic-sparkles"></i> {{ t('work.askAi') }}
            </button>
          </div>
        </header>

        <div class="flex-1 overflow-y-auto p-4 md:p-5">
          <div class="grid gap-3 sm:grid-cols-3">
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-3">
              <p class="text-[11px] uppercase tracking-wide text-base-content/40">Progress</p>
              <p class="mt-1 text-lg font-black">{{ selected.progress || 0 }}%</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-3">
              <p class="text-[11px] uppercase tracking-wide text-base-content/40">Budget</p>
              <p class="mt-1 text-lg font-black">{{ Number(selected.spent_cxjcoins || 0).toFixed(2) }}<span class="text-xs font-normal text-base-content/40"> / {{ selected.budget_cxjcoins || 0 }} cxj</span></p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-3">
              <p class="text-[11px] uppercase tracking-wide text-base-content/40">Deadline</p>
              <p class="mt-1 text-sm font-black">{{ selected.due_date ? formatDate(selected.due_date) : 'None' }}</p>
            </div>
          </div>

          <section class="mt-4">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">The outcome</h3>
            <p class="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-base-content/70">{{ selected.objective || selected.title }}</p>
          </section>

          <section v-if="selected.acceptance" class="mt-4">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Done means</h3>
            <p class="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-base-content/70">{{ selected.acceptance }}</p>
          </section>

          <section v-if="selected.result" class="mt-4 rounded-xl border border-success/25 bg-success/5 p-3">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-success-ink">Delivered result</h3>
            <p class="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-base-content/75">{{ selected.result }}</p>
          </section>

          <section v-if="selected.last_error" class="mt-4 rounded-xl border border-error/25 bg-error/5 p-3">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-error-ink">Needs attention</h3>
            <p class="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-base-content/75">{{ selected.last_error }}</p>
          </section>

          <section class="mt-4 grid gap-2 sm:grid-cols-2">
            <div class="rounded-xl border border-base-content/10 p-3">
              <p class="text-[11px] uppercase tracking-wide text-base-content/40">Approval</p>
              <p class="mt-1 text-xs font-semibold">{{ selected.approval_status }}<template v-if="selected.approved_by"> · {{ selected.approved_by }}</template></p>
            </div>
            <div class="rounded-xl border border-base-content/10 p-3">
              <p class="text-[11px] uppercase tracking-wide text-base-content/40">Run</p>
              <p class="mt-1 text-xs font-semibold">{{ selected.run_status || 'Not started' }}<template v-if="selected.run_id"> · {{ selected.run_id.slice(-6) }}</template></p>
            </div>
          </section>

          <section v-if="selectedTimeline.length" class="mt-5">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Timeline</h3>
            <div class="mt-2 space-y-2">
              <div v-for="event in selectedTimeline" :key="event.id" class="flex gap-2">
                <span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" :class="eventTone(event).replace('text-', 'bg-')"></span>
                <div class="min-w-0 flex-1">
                  <p class="text-xs leading-snug text-base-content/70">{{ event.message }}</p>
                  <p class="text-[11px] text-base-content/35">{{ formatDateTime(event.at) }} · {{ event.actor }}</p>
                </div>
              </div>
            </div>
          </section>
        </div>
      </aside>
    </div>

    <!-- ── Action modal ────────────────────────────────────────────────── -->
    <div v-if="action.open" class="modal modal-open z-[95]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-md border border-base-content/10 bg-base-100">
        <h3 class="text-base font-black">{{ action.title }}</h3>
        <p class="mt-1 text-xs text-base-content/50">{{ action.hint }}</p>
        <textarea v-model="action.value" class="textarea textarea-bordered mt-3 w-full" rows="4"
                  :placeholder="action.type === 'accept' ? 'Describe the outcome and the evidence…' : 'Add a note for the team…'"></textarea>
        <p v-if="action.error" class="mt-2 text-xs text-error-ink">{{ action.error }}</p>
        <div class="modal-action">
          <button class="btn btn-ghost btn-sm" @click="closeAction">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="submitAction">
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            Confirm
          </button>
        </div>
      </div>
      <div class="modal-backdrop" @click="closeAction"></div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
