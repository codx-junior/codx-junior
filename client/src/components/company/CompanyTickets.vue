<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import { useCompanyI18n } from './i18n'

/**
 * Company Tickets — the DSH-like workbench of the company.
 *
 * A ticket is a request placed on ONE project. The company owns both ends, so
 * the workbench can enable the unattended run in the same action: the target
 * accepts the request, the autonomous worker implements it on its own branch,
 * runs the validation gate and closes the ticket only when the gate is green.
 *
 * This screen is the review surface that was missing:
 *
 * * left rail: the company projects and their ticket queues, with the live
 *   run state (queued / running / review / blocked / done);
 * * main pane: the selected ticket, its delivery report, validation evidence,
 *   conversation and the actions (run now, cancel, accept delivery, request
 *   changes and queue another run);
 * * composer: place a new ticket with acceptance criteria and a validation
 *   gate, or comment on an existing one.
 *
 * It polls the company endpoint while visible; every mutation returns the
 * canonical detail so the screen never drifts from the ticket chat.
 */
const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false },
  focusId: { type: String, default: '' }
})

const emit = defineEmits(['assistant', 'refresh', 'open-project'])

const api = API.companies
const { t } = useCompanyI18n()

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const data = ref({
  projects: [],
  tickets: [],
  counts: {},
  worker_present: false,
  autonomy_enabled: true
})
const projectFilter = ref('')
const filter = ref('all')
const selectedId = ref('')
const detail = ref(null)
const detailLoading = ref(false)
const messagesEl = ref(null)
const mobileDetail = ref(false)
const polling = ref(false)

const comment = ref('')
const reviewNote = ref('')

const createOpen = ref(false)
const createError = ref('')
const form = reactive({
  project_id: '',
  name: '',
  description: '',
  kind: 'feature',
  priority: 'medium',
  autonomous: true,
  profile: '',
  validation_source: 'auto',
  commands: ''
})

const FILTERS = computed(() => [
  { id: 'all', label: t('tickets.filter.all'), count: counts.value.all || 0 },
  { id: 'active', label: t('tickets.filter.active'), count: (counts.value.running || 0) + (counts.value.queued || 0) },
  { id: 'review', label: t('tickets.filter.review'), count: counts.value.review || 0 },
  { id: 'blocked', label: t('tickets.filter.blocked'), count: counts.value.blocked || 0 },
  { id: 'done', label: t('tickets.filter.done'), count: counts.value.done || 0 }
])

const projects = computed(() => data.value.projects || [])
const tickets = computed(() => data.value.tickets || [])
const counts = computed(() => data.value.counts || {})
const availableProjects = computed(() => projects.value.filter(project => project.available !== false))
const workerOnline = computed(() => Boolean(data.value.worker_present))
const autonomyOn = computed(() => data.value.autonomy_enabled !== false)

// The detail payload is the canonical view after an action: the list endpoint
// can still be served from the short-lived GET cache while a review/run has
// just changed the ticket.
const selectedTicket = computed(() => (
  detail.value?.ticket
  || tickets.value.find(ticket => ticket.ticket_id === selectedId.value)
  || null
))
const selectedAutonomy = computed(() => (
  detail.value?.autonomy
  || selectedTicket.value?.autonomy
  || {}
))
const selectedProject = computed(() => (
  projects.value.find(project => project.project_id === selectedTicket.value?.project_id)
  || detail.value?.project
  || null
))
const messages = computed(() => detail.value?.messages || [])
const reviewPending = computed(() => Boolean(selectedTicket.value?.review_pending))
const isRunning = computed(() => ['running', 'queued'].includes(selectedAutonomy.value?.state))
const isClosed = computed(() => ['done', 'abandoned'].includes(selectedTicket.value?.status))
const canAct = computed(() => props.canManage && !isClosed.value)

const projectCounts = computed(() => {
  const result = {}
  tickets.value.forEach(ticket => {
    const key = ticket.project_id
    result[key] = result[key] || { total: 0, review: 0, active: 0 }
    result[key].total += 1
    if (ticket.review_pending) result[key].review += 1
    if (['running', 'queued'].includes(ticket.autonomy?.state)) result[key].active += 1
  })
  return result
})

const filteredTickets = computed(() => {
  let result = tickets.value
  if (projectFilter.value) {
    result = result.filter(ticket => ticket.project_id === projectFilter.value)
  }
  if (filter.value === 'review') result = result.filter(ticket => ticket.review_pending)
  else if (filter.value === 'active') result = result.filter(ticket => ['running', 'queued'].includes(ticket.autonomy?.state))
  else if (filter.value === 'blocked') result = result.filter(ticket => ['failed', 'blocked'].includes(ticket.autonomy?.state))
  else if (filter.value === 'done') result = result.filter(ticket => ticket.status === 'done')
  return result
})

const groups = computed(() => {
  const byProject = new Map()
  projects.value.forEach(project => {
    if (projectFilter.value && project.project_id !== projectFilter.value) return
    byProject.set(project.project_id, { project, tickets: [] })
  })
  filteredTickets.value.forEach(ticket => {
    if (!byProject.has(ticket.project_id)) {
      byProject.set(ticket.project_id, {
        project: { project_id: ticket.project_id, name: ticket.project_name || ticket.project_id },
        tickets: []
      })
    }
    byProject.get(ticket.project_id).tickets.push(ticket)
  })
  return [...byProject.values()]
    .filter(group => group.tickets.length)
    .sort((a, b) => String(a.project.name || '').localeCompare(String(b.project.name || '')))
})

const showEmptyHero = computed(() => !loading.value && !tickets.value.length)

const validationLabel = computed(() => {
  const validation = selectedAutonomy.value?.validation || {}
  if (!validation.source || validation.source === 'auto') return t('tickets.gate.auto')
  if (validation.source === 'commands') return t('tickets.gate.commands')
  return t('tickets.gate.none')
})

const deliveryText = computed(() => selectedAutonomy.value?.delivery || '')
const validationText = computed(() => selectedAutonomy.value?.last_validation || '')

// ── helpers ────────────────────────────────────────────────────────────────

function notify(text, type = 'info') {
  try { $storex.ui.addNotification({ text, type }) } catch { /* best effort */ }
}

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

function escapeText(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** Compact markdown renderer for deliveries and comments. */
function renderMarkdown(value) {
  if (!value) return ''
  return escapeText(value)
    .replace(/```([\s\S]*?)```/g, '<pre class="ticket-pre">$1</pre>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^### (.+)$/gm, '<div class="ticket-h3">$1</div>')
    .replace(/^## (.+)$/gm, '<div class="ticket-h2">$1</div>')
    .replace(/^- (.+)$/gm, '<div class="ticket-li">• $1</div>')
    .replace(/\n/g, '<br/>')
}

function relativeTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return t('tickets.time.now')
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d`
  return date.toLocaleDateString()
}

function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString()
}

function stateMeta(ticket) {
  const autonomy = ticket?.autonomy || {}
  const state = autonomy.state || 'off'
  if (ticket?.review_pending) {
    return { label: t('tickets.state.review'), tone: 'badge-warning', dot: 'bg-warning', icon: 'fa-solid fa-magnifying-glass' }
  }
  if (state === 'running') return { label: t('tickets.state.running'), tone: 'badge-info', dot: 'bg-info animate-pulse', icon: 'fa-solid fa-person-running' }
  if (state === 'queued') return { label: t('tickets.state.queued'), tone: 'badge-ghost', dot: 'bg-warning', icon: 'fa-solid fa-clock' }
  if (state === 'failed' || state === 'blocked') return { label: t('tickets.state.blocked'), tone: 'badge-error', dot: 'bg-error', icon: 'fa-solid fa-triangle-exclamation' }
  if (state === 'cancelled') return { label: t('tickets.state.cancelled'), tone: 'badge-ghost', dot: 'bg-base-content/30', icon: 'fa-solid fa-stop' }
  if (ticket?.status === 'done') return { label: t('tickets.state.done'), tone: 'badge-success', dot: 'bg-success', icon: 'fa-solid fa-check' }
  if (ticket?.status === 'accepted') return { label: t('tickets.state.accepted'), tone: 'badge-info', dot: 'bg-info', icon: 'fa-solid fa-inbox' }
  if (ticket?.status === 'declined') return { label: t('tickets.state.declined'), tone: 'badge-ghost', dot: 'bg-base-content/30', icon: 'fa-solid fa-ban' }
  return { label: t('tickets.state.open'), tone: 'badge-ghost', dot: 'bg-base-content/30', icon: 'fa-solid fa-inbox' }
}

function kindIcon(kind) {
  return {
    bug: 'fa-solid fa-bug',
    feature: 'fa-solid fa-wand-magic-sparkles',
    change: 'fa-solid fa-pen',
    question: 'fa-solid fa-circle-question'
  }[kind] || 'fa-solid fa-ticket'
}

function projectName(projectId) {
  return projects.value.find(project => project.project_id === projectId)?.name
    || tickets.value.find(ticket => ticket.project_id === projectId)?.project_name
    || projectId
}

// ── loading ────────────────────────────────────────────────────────────────

async function load({ silent = false } = {}) {
  if (!props.companyId) return
  if (!silent) loading.value = true
  try {
    const listing = await api.tickets(props.companyId, { limit: 200 })
    if (listing) data.value = listing
    error.value = ''
    if (selectedId.value && !tickets.value.some(ticket => ticket.ticket_id === selectedId.value)) {
      selectedId.value = ''
      detail.value = null
    }
  } catch (ex) {
    error.value = errorMessage(ex)
    if (!silent) notify(error.value, 'error')
  } finally {
    loading.value = false
  }
}

async function loadDetail(id = selectedId.value, { silent = false } = {}) {
  if (!id) {
    detail.value = null
    return
  }
  if (!silent) detailLoading.value = true
  try {
    const payload = await api.ticket(props.companyId, id)
    if (selectedId.value === id) {
      detail.value = payload
      if (!silent) nextTick(scrollMessages)
    }
  } catch (ex) {
    if (!silent) notify(errorMessage(ex), 'error')
  } finally {
    detailLoading.value = false
  }
}

async function refreshAll() {
  await load({ silent: true })
  if (selectedId.value) await loadDetail(selectedId.value, { silent: true })
}

async function pollTick() {
  if (polling.value || busy.value) return
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
  polling.value = true
  try {
    await refreshAll()
  } finally {
    polling.value = false
  }
}

let timer = null

function selectTicket(ticket) {
  const id = ticket?.ticket_id || ticket
  if (!id) return
  selectedId.value = id
  mobileDetail.value = true
  detail.value = null
  reviewNote.value = ''
  comment.value = ''
  loadDetail(id)
}

function selectProject(projectId) {
  projectFilter.value = projectFilter.value === projectId ? '' : projectId
  const first = filteredTickets.value[0]
  if (first) selectTicket(first)
}

function scrollMessages() {
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

// ── actions ────────────────────────────────────────────────────────────────

function openCreate() {
  Object.assign(form, {
    project_id: availableProjects.value[0]?.project_id || '',
    name: '',
    description: '',
    kind: 'feature',
    priority: 'medium',
    autonomous: true,
    profile: '',
    validation_source: 'auto',
    commands: ''
  })
  createError.value = ''
  createOpen.value = true
}

function validationPayload() {
  if (form.validation_source === 'none') {
    return { source: 'none', required: false }
  }
  if (form.validation_source === 'commands') {
    return {
      source: 'commands',
      required: true,
      commands: form.commands.split('\n').map(line => line.trim()).filter(Boolean)
    }
  }
  return { source: 'auto', required: true }
}

async function createTicket() {
  createError.value = ''
  if (!form.project_id) {
    createError.value = t('tickets.create.projectRequired')
    return
  }
  if (!form.name.trim()) {
    createError.value = t('tickets.create.nameRequired')
    return
  }
  if (!form.description.trim()) {
    createError.value = t('tickets.create.descriptionRequired')
    return
  }
  if (form.autonomous && form.validation_source === 'commands' && !form.commands.trim()) {
    createError.value = t('tickets.create.commandsRequired')
    return
  }
  busy.value = true
  try {
    const payload = await api.createTicket(props.companyId, {
      project_id: form.project_id,
      name: form.name.trim(),
      description: form.description.trim(),
      kind: form.kind,
      priority: form.priority,
      autonomous: form.autonomous,
      auto_accept: form.autonomous,
      profile: form.profile.trim() || null,
      validation: form.autonomous ? validationPayload() : null
    })
    createOpen.value = false
    const created = payload?.ticket
    await load({ silent: true })
    if (created?.ticket_id) {
      selectedId.value = created.ticket_id
      detail.value = payload
      mobileDetail.value = true
    }
    emit('refresh')
    notify(t('tickets.created'), 'success')
  } catch (ex) {
    createError.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

async function addComment() {
  const text = comment.value.trim()
  if (!text || !selectedId.value) return
  busy.value = true
  try {
    detail.value = await api.commentTicket(props.companyId, selectedId.value, text)
    comment.value = ''
    await load({ silent: true })
    notify(t('tickets.commentSaved'), 'success')
  } catch (ex) {
    notify(errorMessage(ex), 'error')
  } finally {
    busy.value = false
  }
}

async function runNow() {
  if (!selectedId.value) return
  busy.value = true
  try {
    await api.runTicket(props.companyId, selectedId.value)
    notify(t('tickets.runStarted'), 'success')
    await load({ silent: true })
    await loadDetail(selectedId.value, { silent: true })
  } catch (ex) {
    notify(errorMessage(ex), 'error')
  } finally {
    busy.value = false
  }
}

async function queueTicket() {
  if (!selectedId.value) return
  busy.value = true
  try {
    detail.value = await api.queueTicket(props.companyId, selectedId.value)
    notify(t('tickets.queued'), 'success')
    await load({ silent: true })
  } catch (ex) {
    notify(errorMessage(ex), 'error')
  } finally {
    busy.value = false
  }
}

async function cancelRun() {
  if (!selectedId.value) return
  busy.value = true
  try {
    detail.value = await api.cancelTicket(props.companyId, selectedId.value)
    notify(t('tickets.cancelled'), 'info')
    await load({ silent: true })
  } catch (ex) {
    notify(errorMessage(ex), 'error')
  } finally {
    busy.value = false
  }
}

async function review(decision) {
  if (!selectedId.value) return
  if (decision === 'changes' && !reviewNote.value.trim()) {
    notify(t('tickets.review.noteRequired'), 'error')
    return
  }
  busy.value = true
  try {
    detail.value = await api.reviewTicket(props.companyId, selectedId.value, {
      decision,
      note: reviewNote.value.trim(),
      queue: decision === 'changes'
    })
    reviewNote.value = ''
    notify(
      decision === 'accept' ? t('tickets.review.accepted') : t('tickets.review.sentBack'),
      'success'
    )
    await load({ silent: true })
    emit('refresh')
  } catch (ex) {
    notify(errorMessage(ex), 'error')
  } finally {
    busy.value = false
  }
}

function askAssistant() {
  emit('assistant', t('tickets.askPrompt'))
}

function openProject() {
  if (selectedTicket.value?.project_id) emit('open-project', selectedTicket.value.project_id)
}

// ── lifecycle ──────────────────────────────────────────────────────────────

watch(() => props.companyId, () => {
  selectedId.value = ''
  detail.value = null
  projectFilter.value = ''
  filter.value = 'all'
  mobileDetail.value = false
  load()
}, { immediate: true })

watch(() => props.focusId, value => {
  if (value && tickets.value.some(ticket => ticket.ticket_id === value)) {
    selectTicket(value)
  }
}, { immediate: true })

onMounted(() => {
  timer = setInterval(pollTick, 6000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-base-100">
    <!-- ── Header ─────────────────────────────────────────────────────── -->
    <header class="shrink-0 border-b border-base-content/10 px-4 py-3">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="flex min-w-0 items-start gap-3">
          <span class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent">
            <i class="fa-solid fa-ticket text-base"></i>
          </span>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-base font-black tracking-tight">{{ t('tickets.title') }}</h1>
              <span class="badge badge-sm badge-ghost">{{ tickets.length }} {{ t('tickets.total') }}</span>
              <span
                class="badge badge-sm"
                :class="workerOnline ? 'badge-success' : 'badge-ghost'"
                :title="workerOnline ? t('tickets.workerOnlineHint') : t('tickets.workerOfflineHint')"
              >
                <i class="fa-solid fa-robot mr-1 text-[10px]"></i>
                {{ workerOnline ? t('tickets.workerOnline') : t('tickets.workerOffline') }}
              </span>
              <span v-if="!autonomyOn" class="badge badge-sm badge-warning">{{ t('tickets.disabled') }}</span>
            </div>
            <p class="mt-0.5 max-w-2xl text-xs text-base-content/55">
              {{ t('tickets.subtitle') }}
            </p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn btn-ghost btn-sm gap-1.5" :disabled="loading" @click="refreshAll">
            <i class="fa-solid fa-rotate text-xs" :class="{ 'fa-spin': loading }"></i>
            <span class="hidden sm:inline">{{ t('tickets.refresh') }}</span>
          </button>
          <button class="btn btn-ghost btn-sm btn-square" :title="t('tickets.ask')" @click="askAssistant">
            <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
          </button>
          <button v-if="canManage" class="btn btn-primary btn-sm gap-1.5" :disabled="!availableProjects.length" @click="openCreate">
            <i class="fa-solid fa-plus text-xs"></i>
            {{ t('tickets.new') }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="error" class="mx-4 mt-3 alert alert-error py-2 text-xs">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span>{{ error }}</span>
    </div>

    <!-- ── Body ───────────────────────────────────────────────────────── -->
    <div class="flex min-h-0 flex-1">
      <!-- Left rail: queues per project -->
      <aside
        class="flex w-full shrink-0 flex-col border-r border-base-content/10 bg-base-200/25 md:w-[21rem]"
        :class="mobileDetail ? 'hidden md:flex' : 'flex'"
      >
        <div class="shrink-0 border-b border-base-content/5 p-3">
          <div class="flex flex-wrap gap-1">
            <button
              v-for="item in FILTERS"
              :key="item.id"
              class="btn btn-xs gap-1 normal-case"
              :class="filter === item.id ? 'btn-primary' : 'btn-ghost border border-base-content/10'"
              @click="filter = item.id"
            >
              {{ item.label }}
              <span v-if="item.count" class="badge badge-xs" :class="filter === item.id ? 'badge-neutral' : 'badge-ghost'">{{ item.count }}</span>
            </button>
          </div>
          <label class="mt-3 flex items-center gap-2 rounded-xl border border-base-content/10 bg-base-100 px-2 py-1.5">
            <i class="fa-solid fa-filter text-[11px] text-base-content/40"></i>
            <select v-model="projectFilter" class="w-full bg-transparent text-xs outline-none">
              <option value="">{{ t('tickets.allProjects') }}</option>
              <option v-for="project in projects" :key="project.project_id" :value="project.project_id">
                {{ project.name }}{{ project.available === false ? ' (' + t('tickets.unavailable') + ')' : '' }}
              </option>
            </select>
          </label>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          <div v-if="loading && !tickets.length" class="space-y-2 p-2">
            <div v-for="n in 4" :key="n" class="h-16 animate-pulse rounded-xl bg-base-300/40"></div>
          </div>

          <div v-if="showEmptyHero" class="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
            <i class="fa-solid fa-ticket text-4xl text-base-content/20"></i>
            <p class="text-sm font-semibold">{{ t('tickets.emptyTitle') }}</p>
            <p class="text-xs text-base-content/50">{{ t('tickets.emptyHint') }}</p>
            <button v-if="canManage" class="btn btn-primary btn-sm mt-1" :disabled="!availableProjects.length" @click="openCreate">
              <i class="fa-solid fa-plus text-xs"></i> {{ t('tickets.new') }}
            </button>
          </div>

          <div v-else-if="!filteredTickets.length && !loading" class="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-base-content/45">
            <i class="fa-regular fa-circle-check text-3xl"></i>
            <p>{{ t('tickets.noMatches') }}</p>
          </div>

          <div v-for="group in groups" :key="group.project.project_id" class="mb-2">
            <div class="flex items-center gap-2 px-2 py-1.5">
              <i class="fa-regular fa-folder-open text-[11px] text-base-content/35"></i>
              <span class="min-w-0 flex-1 truncate text-[11px] font-bold uppercase tracking-wide text-base-content/45">
                {{ group.project.name }}
              </span>
              <span class="badge badge-xs badge-ghost">{{ projectCounts[group.project.project_id]?.total || 0 }}</span>
              <span v-if="projectCounts[group.project.project_id]?.review" class="badge badge-xs badge-warning">
                {{ projectCounts[group.project.project_id].review }} {{ t('tickets.toReview') }}
              </span>
            </div>

            <button
              v-for="ticket in group.tickets"
              :key="ticket.ticket_id"
              class="mb-1 flex w-full items-start gap-2 rounded-xl border px-2.5 py-2 text-left transition"
              :class="selectedId === ticket.ticket_id
                ? 'border-primary/40 bg-primary/10'
                : 'border-transparent hover:border-base-content/10 hover:bg-base-100'"
              @click="selectTicket(ticket)"
            >
              <span class="mt-1.5 h-2 w-2 shrink-0 rounded-full" :class="stateMeta(ticket).dot"></span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-xs font-semibold">{{ ticket.name }}</span>
                <span class="mt-0.5 flex items-center gap-1.5 text-[11px] text-base-content/45">
                  <i :class="kindIcon(ticket.kind)" class="text-[10px]"></i>
                  <span>{{ stateMeta(ticket).label }}</span>
                  <span v-if="ticket.autonomy?.rounds">· {{ ticket.autonomy.rounds }} {{ t('tickets.rounds') }}</span>
                  <span v-if="ticket.updated_at" class="ml-auto shrink-0">{{ relativeTime(ticket.updated_at) }}</span>
                </span>
              </span>
              <i v-if="selectedId === ticket.ticket_id" class="fa-solid fa-chevron-right mt-1 text-[9px] text-primary"></i>
            </button>
          </div>
        </div>

        <div class="shrink-0 border-t border-base-content/5 px-3 py-2 text-[11px] text-base-content/35">
          <i class="fa-solid fa-circle-info mr-1"></i>{{ t('tickets.railHint') }}
        </div>
      </aside>

      <!-- Main pane: ticket detail / empty -->
      <main class="flex min-w-0 flex-1 flex-col" :class="mobileDetail ? 'flex' : 'hidden md:flex'">
        <div v-if="!selectedId" class="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <div class="grid h-16 w-16 place-items-center rounded-2xl bg-accent/10 text-accent">
            <i class="fa-solid fa-diagram-project text-2xl"></i>
          </div>
          <div class="max-w-md">
            <h2 class="text-base font-bold">{{ t('tickets.pickTitle') }}</h2>
            <p class="mt-1 text-xs leading-relaxed text-base-content/55">{{ t('tickets.pickHint') }}</p>
          </div>
          <div class="flex flex-wrap justify-center gap-2">
            <button v-if="canManage" class="btn btn-primary btn-sm" :disabled="!availableProjects.length" @click="openCreate">
              <i class="fa-solid fa-plus text-xs"></i> {{ t('tickets.new') }}
            </button>
            <button class="btn btn-ghost btn-sm" @click="askAssistant">
              <i class="fa-solid fa-wand-magic-sparkles text-xs"></i> {{ t('tickets.ask') }}
            </button>
          </div>
          <div class="mt-2 grid max-w-2xl grid-cols-1 gap-2 sm:grid-cols-3">
            <div class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 text-left">
              <i class="fa-solid fa-pen-to-square text-xs text-primary"></i>
              <p class="mt-1 text-xs font-semibold">{{ t('tickets.how.place') }}</p>
              <p class="mt-0.5 text-[11px] text-base-content/50">{{ t('tickets.how.placeHint') }}</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 text-left">
              <i class="fa-solid fa-robot text-xs text-success"></i>
              <p class="mt-1 text-xs font-semibold">{{ t('tickets.how.run') }}</p>
              <p class="mt-0.5 text-[11px] text-base-content/50">{{ t('tickets.how.runHint') }}</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 text-left">
              <i class="fa-solid fa-magnifying-glass text-xs text-warning"></i>
              <p class="mt-1 text-xs font-semibold">{{ t('tickets.how.review') }}</p>
              <p class="mt-0.5 text-[11px] text-base-content/50">{{ t('tickets.how.reviewHint') }}</p>
            </div>
          </div>
        </div>

        <template v-else>
          <div class="flex shrink-0 items-center gap-2 border-b border-base-content/10 px-3 py-2">
            <button class="btn btn-ghost btn-xs md:hidden" :title="t('tickets.back')" @click="mobileDetail = false">
              <i class="fa-solid fa-arrow-left"></i>
            </button>
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-2">
                <i :class="kindIcon(selectedTicket?.kind)" class="text-xs text-base-content/40"></i>
                <span class="truncate text-sm font-bold">{{ selectedTicket?.name }}</span>
              </span>
              <span class="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-base-content/45">
                <button class="link link-hover text-left" @click="openProject">{{ projectName(selectedTicket?.project_id) }}</button>
                <span>·</span>
                <span>{{ selectedTicket?.kind || 'feature' }}</span>
                <span>·</span>
                <span>{{ t('tickets.updated') }} {{ relativeTime(selectedTicket?.updated_at) }}</span>
              </span>
            </span>
            <span class="badge badge-sm" :class="stateMeta(selectedTicket).tone">{{ stateMeta(selectedTicket).label }}</span>
            <button class="btn btn-ghost btn-xs btn-square" :title="t('tickets.refresh')" :disabled="detailLoading" @click="loadDetail(selectedId)">
              <i class="fa-solid fa-rotate text-[11px]" :class="{ 'fa-spin': detailLoading }"></i>
            </button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto">
            <div v-if="detailLoading && !detail" class="p-6 text-center text-xs text-base-content/50">
              <span class="loading loading-spinner loading-sm"></span>
              <p class="mt-2">{{ t('tickets.loading') }}</p>
            </div>

            <template v-else>
              <!-- Accepted delivery -->
              <div v-if="selectedAutonomy.reviewed_at && !reviewPending" class="m-3 rounded-2xl border border-success/30 bg-success/10 p-3">
                <div class="flex items-center gap-2 text-xs">
                  <i class="fa-solid fa-circle-check text-success"></i>
                  <span class="font-bold">{{ t('tickets.review.acceptedTitle') }}</span>
                  <span class="text-base-content/50">
                    {{ selectedAutonomy.reviewed_by || t('tickets.you') }} · {{ formatDateTime(selectedAutonomy.reviewed_at) }}
                  </span>
                </div>
                <p v-if="selectedAutonomy.review_note" class="mt-1 text-[11px] text-base-content/60">{{ selectedAutonomy.review_note }}</p>
              </div>

              <!-- Review banner -->
              <div v-if="reviewPending" class="m-3 rounded-2xl border border-warning/40 bg-warning/10 p-3">
                <div class="flex flex-wrap items-start gap-2">
                  <span class="grid h-9 w-9 place-items-center rounded-xl bg-warning/20 text-warning">
                    <i class="fa-solid fa-magnifying-glass"></i>
                  </span>
                  <div class="min-w-0 flex-1">
                    <p class="text-sm font-bold">{{ t('tickets.review.title') }}</p>
                    <p class="mt-0.5 text-xs text-base-content/60">{{ t('tickets.review.hint') }}</p>
                  </div>
                  <div v-if="canManage" class="flex flex-wrap items-center gap-1.5">
                    <button class="btn btn-success btn-sm" :disabled="busy" @click="review('accept')">
                      <i class="fa-solid fa-check text-xs"></i> {{ t('tickets.review.accept') }}
                    </button>
                    <button class="btn btn-outline btn-sm" :disabled="busy" @click="review('changes')">
                      <i class="fa-solid fa-rotate-left text-xs"></i> {{ t('tickets.review.changes') }}
                    </button>
                  </div>
                </div>
                <textarea
                  v-if="canManage"
                  v-model="reviewNote"
                  rows="2"
                  class="textarea textarea-bordered mt-2 w-full text-xs"
                  :placeholder="t('tickets.review.notePlaceholder')"
                ></textarea>
              </div>

              <!-- Meta -->
              <div class="grid grid-cols-2 gap-2 px-3 pt-3 sm:grid-cols-4">
                <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
                  <div class="text-[10px] uppercase tracking-wide text-base-content/40">{{ t('tickets.meta.state') }}</div>
                  <div class="mt-0.5 text-xs font-bold">{{ stateMeta(selectedTicket).label }}</div>
                </div>
                <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
                  <div class="text-[10px] uppercase tracking-wide text-base-content/40">{{ t('tickets.meta.rounds') }}</div>
                  <div class="mt-0.5 text-xs font-bold">{{ selectedAutonomy.rounds || 0 }} / {{ selectedAutonomy.max_rounds || '—' }}</div>
                </div>
                <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
                  <div class="text-[10px] uppercase tracking-wide text-base-content/40">{{ t('tickets.meta.validation') }}</div>
                  <div class="mt-0.5 text-xs font-bold" :class="selectedAutonomy.validation_passed ? 'text-success' : ''">
                    {{ selectedAutonomy.validation_passed ? t('tickets.meta.passed') : t('tickets.meta.notGreen') }}
                  </div>
                </div>
                <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
                  <div class="text-[10px] uppercase tracking-wide text-base-content/40">{{ t('tickets.meta.gate') }}</div>
                  <div class="mt-0.5 truncate text-xs font-bold" :title="validationLabel">{{ validationLabel }}</div>
                </div>
              </div>

              <!-- Controls -->
              <div v-if="canAct" class="flex flex-wrap items-center gap-2 px-3 pt-3">
                <button class="btn btn-primary btn-sm gap-1" :disabled="busy || isRunning" @click="runNow">
                  <i class="fa-solid fa-bolt text-xs"></i> {{ t('tickets.run') }}
                </button>
                <button class="btn btn-ghost btn-sm gap-1" :disabled="busy || isRunning" @click="queueTicket">
                  <i class="fa-solid fa-clock text-xs"></i> {{ t('tickets.queue') }}
                </button>
                <button v-if="isRunning" class="btn btn-ghost btn-sm gap-1 text-error" :disabled="busy" @click="cancelRun">
                  <i class="fa-solid fa-stop text-xs"></i> {{ t('tickets.cancel') }}
                </button>
                <span class="ml-auto text-[11px] text-base-content/40">
                  <i class="fa-solid fa-code-branch mr-1"></i>{{ selectedAutonomy.workspace_branch || t('tickets.mainCheckout') }}
                </span>
              </div>

              <!-- Delivery -->
              <section v-if="deliveryText || validationText || selectedAutonomy.last_error" class="px-3 pt-3">
                <div class="rounded-2xl border border-base-content/10 bg-base-100">
                  <div class="flex items-center gap-2 border-b border-base-content/5 px-3 py-2">
                    <i class="fa-solid fa-box-open text-xs text-success"></i>
                    <h3 class="text-xs font-bold">{{ t('tickets.delivery') }}</h3>
                    <span v-if="selectedAutonomy.finished_at" class="ml-auto text-[11px] text-base-content/40">{{ formatDateTime(selectedAutonomy.finished_at) }}</span>
                  </div>
                  <div v-if="selectedAutonomy.last_error" class="m-3 rounded-xl border border-error/30 bg-error/10 p-2.5 text-xs text-error">
                    <div class="mb-1 font-bold">{{ t('tickets.lastError') }}</div>
                    <div class="whitespace-pre-wrap">{{ selectedAutonomy.last_error }}</div>
                  </div>
                  <div v-if="deliveryText" class="ticket-markdown px-3 py-2 text-xs leading-relaxed" v-html="renderMarkdown(deliveryText)"></div>
                  <div v-if="validationText" class="border-t border-base-content/5 px-3 py-2">
                    <div class="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wide text-base-content/40">
                      <i class="fa-solid fa-vial-circle-check"></i> {{ t('tickets.validationEvidence') }}
                    </div>
                    <pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-base-200 p-2 text-[11px]">{{ validationText }}</pre>
                  </div>
                </div>
              </section>

              <!-- Description -->
              <section class="px-3 pt-3">
                <div class="rounded-2xl border border-base-content/10 bg-base-100">
                  <div class="flex items-center gap-2 border-b border-base-content/5 px-3 py-2">
                    <i class="fa-solid fa-file-lines text-xs text-base-content/40"></i>
                    <h3 class="text-xs font-bold">{{ t('tickets.request') }}</h3>
                    <span class="ml-auto text-[11px] text-base-content/40">{{ selectedTicket?.source_project_name || 'company' }}</span>
                  </div>
                  <div class="ticket-markdown px-3 py-2 text-xs leading-relaxed" v-html="renderMarkdown(selectedTicket?.description)"></div>
                </div>
              </section>

              <!-- Conversation -->
              <section class="px-3 py-3">
                <div class="rounded-2xl border border-base-content/10 bg-base-100">
                  <div class="flex items-center gap-2 border-b border-base-content/5 px-3 py-2">
                    <i class="fa-regular fa-comments text-xs text-base-content/40"></i>
                    <h3 class="text-xs font-bold">{{ t('tickets.conversation') }}</h3>
                    <span class="ml-auto text-[11px] text-base-content/40">{{ messages.length }}</span>
                  </div>
                  <div ref="messagesEl" class="max-h-[28rem] space-y-3 overflow-y-auto px-3 py-3">
                    <div v-if="!messages.length" class="py-4 text-center text-[11px] text-base-content/40">
                      {{ t('tickets.noMessages') }}
                    </div>
                    <div
                      v-for="message in messages"
                      :key="message.doc_id || message.created_at"
                      class="flex gap-2"
                      :class="message.role === 'user' ? 'flex-row-reverse' : ''"
                    >
                      <span class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px]" :class="message.role === 'user' ? 'bg-primary text-primary-content' : 'bg-base-300 text-base-content/60'">
                        <i :class="message.role === 'user' ? 'fa-solid fa-user' : 'fa-solid fa-robot'"></i>
                      </span>
                      <div class="min-w-0 max-w-[88%]">
                        <div class="mb-0.5 flex items-center gap-2 text-[10px] text-base-content/40" :class="message.role === 'user' ? 'justify-end' : ''">
                          <span>{{ message.role === 'user' ? (message.user || t('tickets.you')) : 'codx-junior' }}</span>
                          <span>{{ formatDateTime(message.created_at) }}</span>
                        </div>
                        <div class="ticket-markdown rounded-xl px-2.5 py-2 text-xs leading-relaxed" :class="message.role === 'user' ? 'bg-primary/10' : 'bg-base-200/60'" v-html="renderMarkdown(message.content)"></div>
                      </div>
                    </div>
                  </div>
                  <div v-if="canManage" class="border-t border-base-content/5 p-2">
                    <div class="flex items-end gap-1.5 rounded-xl border border-base-content/10 bg-base-200/30 p-1.5">
                      <textarea
                        v-model="comment"
                        rows="1"
                        class="max-h-28 min-h-8 flex-1 resize-none bg-transparent px-2 py-1 text-xs outline-none"
                        :placeholder="t('tickets.commentPlaceholder')"
                        @keydown.enter.exact.prevent="addComment"
                      ></textarea>
                      <button class="btn btn-primary btn-sm btn-square" :disabled="busy || !comment.trim()" @click="addComment">
                        <i class="fa-solid fa-arrow-up text-xs"></i>
                      </button>
                    </div>
                  </div>
                </div>
              </section>
            </template>
          </div>
        </template>
      </main>
    </div>

    <!-- ── New ticket ─────────────────────────────────────────────────── -->
    <div v-if="createOpen" class="fixed inset-0 z-[85] grid place-items-center bg-black/50 p-3" role="dialog" aria-modal="true" @click.self="createOpen = false">
      <div class="flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-base-300 bg-base-100 shadow-2xl">
        <header class="flex items-start justify-between gap-3 border-b border-base-content/10 px-4 py-3">
          <div>
            <h2 class="text-sm font-bold">{{ t('tickets.create.title') }}</h2>
            <p class="mt-0.5 text-xs text-base-content/50">{{ t('tickets.create.hint') }}</p>
          </div>
          <button class="btn btn-ghost btn-sm btn-square" :aria-label="t('tickets.close')" @click="createOpen = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="form-control">
              <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/45">{{ t('tickets.create.project') }}</span>
              <select v-model="form.project_id" class="select select-sm select-bordered">
                <option v-for="project in projects" :key="project.project_id" :value="project.project_id" :disabled="project.available === false">
                  {{ project.name }}{{ project.available === false ? ' (' + t('tickets.unavailable') + ')' : '' }}
                </option>
              </select>
            </label>
            <div class="grid grid-cols-2 gap-2">
              <label class="form-control">
                <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/45">{{ t('tickets.create.kind') }}</span>
                <select v-model="form.kind" class="select select-sm select-bordered">
                  <option value="feature">feature</option>
                  <option value="bug">bug</option>
                  <option value="change">change</option>
                  <option value="question">question</option>
                </select>
              </label>
              <label class="form-control">
                <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/45">{{ t('tickets.create.priority') }}</span>
                <select v-model="form.priority" class="select select-sm select-bordered">
                  <option value="high">high</option>
                  <option value="medium">medium</option>
                  <option value="low">low</option>
                </select>
              </label>
            </div>
          </div>

          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/45">{{ t('tickets.create.name') }}</span>
            <input v-model="form.name" class="input input-sm input-bordered" :placeholder="t('tickets.create.namePlaceholder')" autofocus />
          </label>

          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/45">{{ t('tickets.create.description') }}</span>
            <textarea v-model="form.description" rows="5" class="textarea textarea-bordered text-sm" :placeholder="t('tickets.create.descriptionPlaceholder')"></textarea>
            <span class="mt-1 text-[11px] text-base-content/40">{{ t('tickets.create.descriptionHint') }}</span>
          </label>

          <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-3">
            <label class="flex cursor-pointer items-center gap-3">
              <input v-model="form.autonomous" type="checkbox" class="toggle toggle-sm toggle-primary" />
              <span>
                <span class="block text-xs font-semibold">{{ t('tickets.create.autonomous') }}</span>
                <span class="block text-[11px] text-base-content/50">{{ t('tickets.create.autonomousHint') }}</span>
              </span>
            </label>

            <template v-if="form.autonomous">
              <div class="mt-3 grid gap-2 sm:grid-cols-2">
                <label class="form-control">
                  <span class="label-text text-[11px]">{{ t('tickets.create.profile') }}</span>
                  <input v-model="form.profile" class="input input-sm input-bordered" :placeholder="t('tickets.create.profilePlaceholder')" />
                </label>
                <label class="form-control">
                  <span class="label-text text-[11px]">{{ t('tickets.create.gate') }}</span>
                  <select v-model="form.validation_source" class="select select-sm select-bordered">
                    <option value="auto">{{ t('tickets.gate.auto') }}</option>
                    <option value="commands">{{ t('tickets.gate.commands') }}</option>
                    <option value="none">{{ t('tickets.gate.none') }}</option>
                  </select>
                </label>
              </div>
              <label v-if="form.validation_source === 'commands'" class="form-control mt-2">
                <span class="label-text text-[11px]">{{ t('tickets.create.commands') }}</span>
                <textarea v-model="form.commands" rows="3" class="textarea textarea-bordered textarea-sm font-mono text-xs" :placeholder="t('tickets.create.commandsPlaceholder')"></textarea>
              </label>
              <p v-else-if="form.validation_source === 'none'" class="mt-2 text-[11px] text-warning">
                <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ t('tickets.create.noGateWarning') }}
              </p>
            </template>
          </div>

          <p v-if="createError" class="text-xs text-error">{{ createError }}</p>
        </div>

        <footer class="flex items-center justify-end gap-2 border-t border-base-content/10 px-4 py-3">
          <button class="btn btn-ghost btn-sm" @click="createOpen = false">{{ t('tickets.cancel') }}</button>
          <button class="btn btn-primary btn-sm gap-1" :disabled="busy" @click="createTicket">
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-paper-plane text-xs"></i>
            {{ t('tickets.create.submit') }}
          </button>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ticket-markdown :deep(code) {
  font-size: 0.72rem;
  padding: 0 0.2rem;
  border-radius: 0.25rem;
  background: color-mix(in oklab, currentColor 8%, transparent);
}
.ticket-markdown :deep(pre),
.ticket-markdown :deep(.ticket-pre) {
  margin: 0.35rem 0;
  padding: 0.5rem;
  border-radius: 0.5rem;
  background: color-mix(in oklab, currentColor 6%, transparent);
  overflow-x: auto;
  font-size: 0.7rem;
}
.ticket-markdown :deep(.ticket-h2) {
  margin-top: 0.5rem;
  font-weight: 800;
  font-size: 0.8rem;
}
.ticket-markdown :deep(.ticket-h3) {
  margin-top: 0.4rem;
  font-weight: 700;
}
.ticket-markdown :deep(.ticket-li) {
  padding-left: 0.4rem;
}
</style>

<!-- Made with ❤️ by codx-junior -->
