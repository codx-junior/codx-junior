<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { API } from '@/api/api'

/**
 * The always-present codx-junior assistant of the company shell.
 *
 * This is the product: managers talk to it instead of opening screens. It asks
 * ONE question at a time (`state` from the backend), offers big quick-reply
 * buttons and only shows a settings form when the user explicitly asks for the
 * full settings view.
 *
 * It posts to `POST /api/companies/<id>/assistant` (see company/assistant.py).
 */
const props = defineProps({
  companyId: { type: String, default: '' },
  companyName: { type: String, default: '' },
  view: { type: String, default: 'home' },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false },
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  // Docked panel on desktop; drawer/floating on phones.
  docked: { type: Boolean, default: false },
  // The company shell opens the assistant from its Ask bar, so the floating
  // launcher is optional.
  showLauncher: { type: Boolean, default: true }
})

const emit = defineEmits(['refresh', 'navigate', 'deleted'])

const api = API.companies

const messages = ref([])
const question = ref('')
const thinking = ref(false)
const error = ref('')
const form = ref(null)
const formValues = reactive({})
const formError = ref('')
const submitting = ref(false)
const collapsed = ref(false)
const mobileOpen = ref(false)
const rootEl = ref(null)
const messagesEl = ref(null)
const inputEl = ref(null)
// Multi-turn slot filling: the backend returns the next question's state, the
// next message is the answer and the flow continues without any form.
const conversation = ref(null)
const quickOptions = ref([])
const remoteSuggestions = ref([])

const canManage = computed(() => props.canManage)
const canAdmin = computed(() => props.canAdmin)

const suggestions = computed(() => {
  if (conversation.value) return []
  if (remoteSuggestions.value.length) return remoteSuggestions.value.slice(0, 6)
  const byView = {
    home: ["Today's brief", 'Review the work waiting for me', 'How is the business doing?', 'What should we focus on this week?'],
    work: ['What is blocked and why?', 'Review the work waiting for me', 'Create a goal for this month', 'What should we ship next?'],
    teams: ['Give me the status of every team', 'Who is overloaded?', 'What needs a manager decision?', 'Build a team'],
    people: ['Who is on duty right now?', 'Give me a performance review of the team', 'Who is overloaded?', 'Add a person'],
    business: ['How can we increase revenue this month?', 'Which deals deserve attention?', 'Where are we losing money?', 'Turn the revenue gap into a plan'],
    activity: ['Write a weekly review', 'What did we deliver this week?', 'What decisions are pending?', 'What changed?'],
    settings: ['How much autonomy do we allow?', 'What is our daily AI budget?', 'Open company settings', 'Add a person']
  }
  return (byView[props.view] || byView.home).slice(0, 4)
})

const placeholder = computed(() => (
  conversation.value ? 'Type your answer…' : 'Tell me what you need…'
))

// ── Live signals the assistant is watching ──────────────────────────────────
const missions = computed(() => props.snapshot?.missions || [])
const attention = computed(() => props.snapshot?.attention || [])
const reviewWork = computed(() => missions.value.filter(mission => mission.status === 'review'))
const blockedWork = computed(() => missions.value.filter(mission => mission.status === 'blocked'))
const business = computed(() => props.snapshot?.company?.business || {})
const revenueTarget = computed(() => Number(business.value.revenue_target || 0))
const revenueActual = computed(() => Number(business.value.revenue_actual || 0))
const revenueProgress = computed(() => revenueTarget.value > 0
  ? Math.min(100, Math.round((revenueActual.value / revenueTarget.value) * 100))
  : 0)
const onDuty = computed(() => (props.actors || []).filter(actor => actor?.config?.enabled).length)
const showSignals = computed(() => messages.value.length <= 1 && !thinking.value)

const signals = computed(() => {
  const items = []
  if (attention.value.length) {
    items.push({
      icon: 'fa-solid fa-bell',
      tone: 'text-warning',
      bg: 'bg-warning/10',
      label: `${attention.value.length} decision${attention.value.length === 1 ? '' : 's'} waiting for you`,
      prompt: 'What needs my attention and what do you recommend?'
    })
  }
  if (reviewWork.value.length) {
    items.push({
      icon: 'fa-solid fa-magnifying-glass',
      tone: 'text-accent',
      bg: 'bg-accent/10',
      label: `${reviewWork.value.length} deliver${reviewWork.value.length === 1 ? 'y' : 'ies'} to review`,
      prompt: 'Review the work waiting for me and tell me what to accept or send back.'
    })
  }
  if (revenueTarget.value > 0 && revenueProgress.value < 100) {
    items.push({
      icon: 'fa-solid fa-arrow-trend-up',
      tone: 'text-success',
      bg: 'bg-success/10',
      label: `${100 - revenueProgress.value}% left to hit the revenue target`,
      prompt: 'How do we close the revenue gap this period? Give me a concrete plan.'
    })
  }
  if (blockedWork.value.length) {
    items.push({
      icon: 'fa-solid fa-ban',
      tone: 'text-error',
      bg: 'bg-error/10',
      label: `${blockedWork.value.length} goal${blockedWork.value.length === 1 ? '' : 's'} blocked`,
      prompt: 'What is blocked, why, and what do you need from me to unblock it?'
    })
  }
  if (!items.length) {
    items.push({
      icon: 'fa-solid fa-circle-check',
      tone: 'text-success',
      bg: 'bg-success/10',
      label: 'Everything is moving. Ask for the next best move.',
      prompt: 'Give me today\'s executive brief and the single most important thing to do next.'
    })
  }
  return items.slice(0, 3)
})

const quickActions = computed(() => [
  { icon: 'fa-solid fa-bolt', label: "Today's brief", prompt: 'Give me today\'s executive brief' },
  { icon: 'fa-solid fa-magnifying-glass', label: 'Review work', prompt: 'Review the work in progress and tell me what needs my attention' },
  { icon: 'fa-solid fa-arrow-trend-up', label: 'Grow revenue', prompt: 'How can we increase revenue this month? Use our pipeline and team.' },
  { icon: 'fa-solid fa-people-group', label: 'Team status', prompt: 'Give me the status of every team, who is blocked and why.' }
])

function browserLanguage() {
  try {
    return navigator.languages?.[0] || navigator.language || ''
  } catch {
    return ''
  }
}

function optionLabel(option) {
  if (typeof option === 'string') return option
  return option?.label || option?.value || ''
}

function optionValue(option) {
  if (typeof option === 'string') return option
  return option?.value ?? option?.label ?? ''
}

// ── Rendering helpers ───────────────────────────────────────────────────────

function scrollToEnd() {
  nextTick(() => {
    const el = messagesEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pushMessage(role, text, extra = {}) {
  messages.value.push({ role, text, ...extra })
  messages.value = messages.value.slice(-40)
  scrollToEnd()
}

function history() {
  return messages.value
    .filter(message => ['user', 'assistant'].includes(message.role))
    .map(message => ({
      role: message.role === 'assistant' ? 'assistant' : 'user',
      content: message.text
    }))
    .slice(-12)
}

/** Very small markdown renderer: keeps the panel dependency-free. */
function render(text) {
  if (!text) return ''
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^### (.+)$/gm, '<div class="font-bold text-sm mt-2">$1</div>')
    .replace(/^- (.+)$/gm, '<div class="pl-2">• $1</div>')
    .replace(/\n/g, '<br/>')
}

// A local, instant greeting. It avoids a race between the first API turn and
// a one-shot intent ("Add a person") the shell may open right after mounting.
function bootstrapMessage() {
  messages.value = []
  question.value = ''
  conversation.value = null
  quickOptions.value = []
  remoteSuggestions.value = []
  applyForm(null)
  pushMessage(
    'assistant',
    `Hi — I'm **codx-junior**, your AI chief of staff for **${props.companyName || 'your company'}**. `
    + 'I can see the teams, the work, the pipeline and the money. Ask me what matters, '
    + 'what to do next, or tell me to get something moving.'
    + (canManage.value ? '' : ' Your role is read-only here, so I can answer questions but changes need a manager.')
  )
}

// ── Assistant turns ─────────────────────────────────────────────────────────

async function ask(text, { silent = false, display = '' } = {}) {
  const companyId = props.companyId
  const message = String(text || '').trim()
  if (!companyId || thinking.value) return
  if (!message && !silent) return
  error.value = ''
  if (!silent) {
    pushMessage('user', display || message)
    question.value = ''
  }
  thinking.value = true
  try {
    const response = await api.assistant(companyId, {
      message,
      view: props.view,
      history: history(),
      state: conversation.value || {},
      language: browserLanguage()
    })
    if (props.companyId !== companyId) return
    pushMessage('assistant', response?.reply || 'I have no answer for that yet.')
    conversation.value = response?.state || null
    quickOptions.value = response?.options || []
    remoteSuggestions.value = response?.suggestions || []
    applyForm(response?.form || null)
    if (response?.deleted) emit('deleted', response)
    else if (response?.changed) emit('refresh', { source: 'assistant' })
  } catch (ex) {
    const detail = ex?.response?.data?.detail || ex?.message || String(ex)
    error.value = detail
    if (!silent) pushMessage('assistant', `I could not do that: ${detail}`)
  } finally {
    thinking.value = false
  }
}

function send() {
  const text = question.value.trim()
  if (!text || thinking.value) return
  ask(text)
}

function submitForm() {
  if (!form.value || submitting.value) return
  formError.value = ''
  const values = { ...formValues }
  // Required-field validation is client-side too, so a form never posts half
  // an answer to the assistant.
  const missing = (form.value.fields || [])
    .filter(field => {
      if (!field.required) return false
      const value = values[field.name]
      return Array.isArray(value) ? !value.length : !String(value ?? '').trim()
    })
    .map(field => field.label || field.name)
  if (missing.length) {
    formError.value = `Required: ${missing.join(', ')}`
    return
  }
  submitting.value = true
  const companyId = props.companyId
  pushMessage('user', `Submit “${form.value.title}”`)
  api.assistant(companyId, {
    form_id: form.value.id,
    values,
    view: props.view,
    history: history(),
    state: conversation.value || {},
    language: browserLanguage()
  }).then(response => {
    if (props.companyId !== companyId) return
    pushMessage('assistant', response?.reply || 'Done.')
    conversation.value = response?.state || null
    quickOptions.value = response?.options || []
    remoteSuggestions.value = response?.suggestions || []
    applyForm(response?.form || null)
    if (response?.deleted) emit('deleted', response)
    else if (response?.changed) emit('refresh', { source: 'assistant' })
  }).catch(ex => {
    formError.value = ex?.response?.data?.detail || ex?.message || String(ex)
  }).finally(() => {
    submitting.value = false
  })
}

function cancelForm() {
  applyForm(null)
  formError.value = ''
}

function applyForm(next) {
  form.value = next || null
  formError.value = ''
  Object.keys(formValues).forEach(key => delete formValues[key])
  if (next) {
    Object.assign(formValues, next.values || {})
    // Multiselects always start as arrays (the backend accepts both).
    ;(next.fields || []).forEach(field => {
      if (field.type === 'multiselect' && !Array.isArray(formValues[field.name])) {
        formValues[field.name] = formValues[field.name]
          ? [formValues[field.name]]
          : []
      }
    })
    nextTick(() => {
      const first = rootEl.value?.querySelector('[data-assistant-form] input, [data-assistant-form] select, [data-assistant-form] textarea')
      first?.focus?.()
    })
  }
}

function toggleMulti(field, value) {
  const current = Array.isArray(formValues[field.name]) ? formValues[field.name] : []
  const index = current.indexOf(value)
  if (index === -1) current.push(value)
  else current.splice(index, 1)
  formValues[field.name] = current
}

function isMultiSelected(field, value) {
  return Array.isArray(formValues[field.name]) && formValues[field.name].includes(value)
}

// ── Window controls ─────────────────────────────────────────────────────────

function collapse() {
  if (props.docked) collapsed.value = true
  else mobileOpen.value = false
}

function expand() {
  collapsed.value = false
  mobileOpen.value = true
  nextTick(() => inputEl.value?.focus?.())
}

function openWithPrompt(prompt = '') {
  expand()
  if (prompt) {
    question.value = prompt
    ask(prompt)
  }
}

function resetChat() {
  messages.value = []
  question.value = ''
  conversation.value = null
  quickOptions.value = []
  remoteSuggestions.value = []
  error.value = ''
  applyForm(null)
  bootstrapMessage()
  nextTick(() => inputEl.value?.focus?.())
}

function onGlobalShortcut(event) {
  if (!(event.metaKey || event.ctrlKey) || String(event.key).toLowerCase() !== 'k') return
  const target = event.target
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
  event.preventDefault()
  expand()
}

function onExternalOpen(event) {
  const prompt = event?.detail?.prompt || ''
  openWithPrompt(prompt)
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

// ── Lifecycle ───────────────────────────────────────────────────────────────

onMounted(() => {
  window.addEventListener('company-assistant:open', onExternalOpen)
  window.addEventListener('keydown', onGlobalShortcut)
  if (props.companyId) bootstrapMessage()
})

onBeforeUnmount(() => {
  window.removeEventListener('company-assistant:open', onExternalOpen)
  window.removeEventListener('keydown', onGlobalShortcut)
})

watch(() => props.companyId, companyId => {
  if (!companyId) return
  bootstrapMessage()
})
</script>

<template>
  <!-- Dockable column (project shells) or a right-hand drawer (company shell).
       One panel, one state; the drawer falls back to full width on phones. -->
  <div
    v-if="!docked && mobileOpen"
    class="fixed inset-0 z-[79] bg-black/35 backdrop-blur-[2px] transition-opacity"
    aria-hidden="true"
    @click="collapse"
  ></div>
  <aside
    v-if="docked || mobileOpen"
    ref="rootEl"
    class="company-assistant flex flex-col bg-base-100"
    :class="docked
      ? ['shrink-0 h-full border-l border-base-content/10 transition-all duration-200', collapsed ? 'w-12' : 'w-[22rem] xl:w-[25rem]']
      : 'fixed inset-y-0 right-0 z-[80] h-full w-full max-w-[27rem] border-l border-base-content/10 shadow-2xl max-sm:max-w-none'"
    aria-label="codx-junior company assistant"
  >
    <!-- Collapsed rail (desktop only) -->
    <template v-if="docked && collapsed">
      <button class="flex flex-1 flex-col items-center gap-3 py-4 text-primary hover:bg-base-200/60"
              title="Open the codx-junior assistant" aria-label="Open the assistant" @click="expand">
        <span class="relative">
          <img src="/only_icon.png" alt="codx-junior" class="h-8 w-8 rounded-full bg-base-300" />
          <span class="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-base-100 bg-success"></span>
        </span>
        <i class="fa-solid fa-chevron-left text-[10px]"></i>
        <span v-if="signals.length" class="grid h-5 w-5 place-items-center rounded-full bg-warning text-[9px] font-bold text-warning-content">{{ signals.length }}</span>
      </button>
    </template>

    <template v-else>
      <!-- Header -->
      <header class="relative shrink-0 overflow-hidden border-b border-base-content/10 px-3.5 py-3">
        <div class="pointer-events-none absolute -right-12 -top-16 h-40 w-40 rounded-full bg-primary/20 blur-3xl"></div>
        <div class="relative flex items-start gap-2.5">
          <span class="relative shrink-0">
            <img src="/only_icon.png" alt="codx-junior" class="h-9 w-9 rounded-xl bg-base-300 shadow-md" />
            <span class="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-base-100 bg-success"></span>
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5">
              <h2 class="text-sm font-black leading-tight">codx-junior</h2>
              <span class="badge badge-xs border-primary/20 bg-primary/10 text-primary">AI Chief of Staff</span>
            </div>
            <p class="mt-0.5 truncate text-[10px] text-base-content/45">
              {{ companyName || 'Your company' }} · live data
              <span v-if="onDuty"> · {{ onDuty }} AI on duty</span>
            </p>
          </div>
          <button class="btn btn-ghost btn-xs btn-square" title="New conversation" aria-label="New conversation" @click="resetChat">
            <i class="fa-solid fa-pen-to-square text-[10px]"></i>
          </button>
          <button v-if="docked" class="btn btn-ghost btn-xs btn-square" title="Collapse" aria-label="Collapse assistant" @click="collapse">
            <i class="fa-solid fa-chevron-right text-[10px]"></i>
          </button>
          <button v-else class="btn btn-ghost btn-xs btn-square" title="Close" aria-label="Close assistant" @click="collapse">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </header>

      <!-- Conversation -->
      <div ref="messagesEl" class="flex-1 min-h-0 overflow-y-auto px-3 py-3" aria-live="polite">
        <!-- Proactive signals + quick actions before the conversation starts -->
        <div v-if="showSignals" class="mb-3 space-y-2.5">
          <div class="rounded-2xl border border-primary/15 bg-gradient-to-br from-primary/10 via-base-100 to-secondary/10 p-3">
            <div class="flex items-center gap-1.5">
              <span class="relative flex h-2 w-2">
                <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60"></span>
                <span class="relative inline-flex h-2 w-2 rounded-full bg-success"></span>
              </span>
              <p class="text-[10px] font-bold uppercase tracking-[0.16em] text-base-content/50">Watching your company</p>
            </div>
            <div class="mt-2 space-y-1.5">
              <button v-for="signal in signals" :key="signal.label" type="button"
                      class="flex w-full items-center gap-2 rounded-xl border border-base-content/10 bg-base-100/70 p-2 text-left transition hover:border-primary/30"
                      :disabled="thinking" @click="ask(signal.prompt)">
                <span class="grid h-7 w-7 shrink-0 place-items-center rounded-lg" :class="[signal.bg, signal.tone]">
                  <i :class="signal.icon" class="text-[10px]"></i>
                </span>
                <span class="min-w-0 flex-1 text-[10px] font-medium leading-snug">{{ signal.label }}</span>
                <i class="fa-solid fa-arrow-right text-[8px] text-base-content/25"></i>
              </button>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-1.5">
            <button v-for="action in quickActions" :key="action.label" type="button"
                    class="flex items-center gap-1.5 rounded-xl border border-base-content/10 bg-base-100 p-2 text-left text-[10px] font-semibold text-base-content/65 transition hover:border-primary/30 hover:text-primary"
                    :disabled="thinking" @click="ask(action.prompt)">
              <i :class="action.icon" class="text-[9px] text-primary"></i>{{ action.label }}
            </button>
          </div>
        </div>

        <div v-if="!messages.length" class="flex h-full items-center justify-center text-xs text-base-content/50">
          <span class="loading loading-dots loading-xs"></span>
          <span class="ml-2">Connecting to your company…</span>
        </div>

        <div v-for="(message, index) in messages" :key="index" class="chat"
             :class="message.role === 'user' ? 'chat-end' : 'chat-start'">
          <div v-if="message.role !== 'user'" class="chat-image">
            <img src="/only_icon.png" alt="" class="h-6 w-6 rounded-full bg-base-300" />
          </div>
          <div class="chat-bubble max-w-[92%] text-[11px] leading-relaxed whitespace-normal"
               :class="message.role === 'user'
                 ? 'chat-bubble-primary'
                 : 'border border-base-content/10 bg-base-200/70 text-base-content'"
               v-html="render(message.text)"></div>
        </div>

        <div v-if="thinking" class="chat chat-start">
          <div class="chat-image"><img src="/only_icon.png" alt="" class="h-6 w-6 rounded-full bg-base-300" /></div>
          <div class="chat-bubble border border-base-content/10 bg-base-200/70 text-base-content">
            <span class="flex items-center gap-2 text-[10px] text-base-content/55">
              <span class="loading loading-dots loading-xs"></span>
              Reasoning over your live company data…
            </span>
          </div>
        </div>

        <!-- Forms are rendered HERE and nowhere else. -->
        <form v-if="form" data-assistant-form
              class="mt-2 rounded-2xl border border-primary/30 bg-primary/5 p-3"
              @submit.prevent="submitForm">
          <div class="flex items-start justify-between gap-2">
            <div>
              <div class="text-xs font-bold">{{ form.title }}</div>
              <p v-if="form.description" class="mt-0.5 text-[10px] text-base-content/50">{{ form.description }}</p>
            </div>
            <button type="button" class="btn btn-ghost btn-xs btn-square" aria-label="Cancel" @click="cancelForm">
              <i class="fa-solid fa-xmark text-[9px]"></i>
            </button>
          </div>
          <label v-for="field in form.fields" :key="field.name" class="form-control mt-2">
            <span class="label-text text-[9px] font-bold uppercase tracking-wide text-base-content/45">
              {{ field.label }}<span v-if="field.required" class="text-error"> *</span>
            </span>
            <textarea v-if="field.type === 'textarea'" v-model="formValues[field.name]"
                      class="textarea textarea-xs textarea-bordered mt-0.5" rows="2"
                      :placeholder="field.placeholder || ''"></textarea>
            <select v-else-if="field.type === 'select'" v-model="formValues[field.name]"
                    class="select select-xs select-bordered mt-0.5">
              <option value="">—</option>
              <option v-for="option in (field.options || [])" :key="option.value || option" :value="option.value || option">
                {{ option.label || option }}
              </option>
            </select>
            <div v-else-if="field.type === 'multiselect'" class="mt-1 flex flex-wrap gap-1">
              <label v-for="option in (field.options || [])" :key="option.value || option"
                     class="badge badge-sm cursor-pointer gap-1"
                     :class="isMultiSelected(field, option.value || option) ? 'badge-primary' : 'badge-ghost'">
                <input type="checkbox" class="checkbox checkbox-xs"
                       :checked="isMultiSelected(field, option.value || option)"
                       @change="toggleMulti(field, option.value || option)" />
                {{ option.label || option }}
              </label>
            </div>
            <input v-else v-model="formValues[field.name]" class="input input-xs input-bordered mt-0.5"
                   :type="field.type === 'password' ? 'password' : field.type === 'email' ? 'email' : 'text'"
                   :placeholder="field.placeholder || ''" />
          </label>
          <p v-if="formError" class="mt-1 text-[10px] text-error">{{ formError }}</p>
          <div class="mt-2 flex items-center gap-1.5">
            <button type="button" class="btn btn-ghost btn-xs" @click="cancelForm">Cancel</button>
            <button type="submit" class="btn btn-primary btn-xs flex-1" :disabled="submitting">
              <span v-if="submitting" class="loading loading-spinner loading-xs"></span>
              {{ form.submit_label || 'Save' }}
            </button>
          </div>
        </form>
      </div>

      <p v-if="error" class="shrink-0 px-3 pb-1 text-[10px] text-error">{{ error }}</p>

      <!-- Structured answers / follow-up options -->
      <div v-if="quickOptions.length" class="shrink-0 flex flex-wrap gap-1 px-2 pb-1">
        <button v-for="(option, index) in quickOptions" :key="index" type="button"
                class="btn btn-xs btn-primary btn-outline normal-case"
                :disabled="thinking" @click="ask(optionValue(option), { display: optionLabel(option) })">
          {{ optionLabel(option) }}
        </button>
      </div>

      <!-- Suggestions -->
      <div v-if="suggestions.length && !conversation" class="shrink-0 flex flex-wrap gap-1 px-2 pb-1">
        <button v-for="suggestion in suggestions" :key="suggestion" type="button"
                class="btn btn-xs btn-ghost border border-base-content/10 normal-case"
                :disabled="thinking" @click="ask(suggestion)">{{ suggestion }}</button>
      </div>

      <!-- Composer -->
      <form class="shrink-0 border-t border-base-content/10 p-2.5" @submit.prevent="send">
        <div class="flex items-end gap-1.5 rounded-2xl border border-base-content/15 bg-base-200/40 p-1.5 focus-within:border-primary/50">
          <textarea ref="inputEl" v-model="question" rows="1"
                    class="max-h-28 min-h-8 flex-1 resize-none bg-transparent px-2 py-1 text-xs outline-none"
                    :placeholder="placeholder" aria-label="Message the company assistant"
                    @keydown.enter.exact.prevent="send"></textarea>
          <button type="submit" class="btn btn-primary btn-sm btn-square shrink-0 rounded-xl"
                  :disabled="!question.trim() || thinking" aria-label="Send">
            <i class="fa-solid fa-arrow-up text-xs"></i>
          </button>
        </div>
        <div class="mt-1 flex items-center justify-between px-1">
          <span class="text-[9px] text-base-content/35">
            <i class="fa-solid fa-shield-halved mr-1 text-success"></i>Grounded in live company data
          </span>
          <span class="hidden text-[9px] text-base-content/25 sm:inline">⌘K</span>
        </div>
      </form>

      <p v-if="!canManage" class="shrink-0 px-3 pb-2 text-[9px] text-base-content/35">
        Read-only role · ask a manager for changes
      </p>
    </template>
  </aside>

  <!-- Floating launcher (drawer mode) -->
  <button v-if="!docked && !mobileOpen && showLauncher"
          class="fixed bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] right-3 z-40 grid h-12 w-12 place-items-center rounded-full bg-[var(--cp-cta-bg,#111419)] text-[var(--cp-cta-ink,#fff)] shadow-xl ring-1 ring-black/5 transition hover:scale-105 active:scale-95"
          aria-label="Open the codx-junior assistant" @click="expand">
    <i class="fa-solid fa-wand-magic-sparkles"></i>
    <span v-if="signals.length" class="absolute -right-0.5 -top-0.5 grid h-5 w-5 place-items-center rounded-full bg-warning text-[9px] font-bold text-warning-content">{{ signals.length }}</span>
  </button>
</template>

<style scoped>
.chat-bubble :deep(code) {
  font-size: 0.72rem;
  padding: 0 0.2rem;
  border-radius: 0.25rem;
  background: rgba(0, 0, 0, 0.18);
}
</style>

<!-- Made with ❤️ by codx-junior -->
