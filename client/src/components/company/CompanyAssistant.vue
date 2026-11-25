<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import ChatView from '@/views/ChatView.vue'

/**
 * Company assistant — a real ChatView conversation, not a custom panel.
 *
 * The company shell provisions (once) a normal Chat hosted by one of the
 * company's projects. That chat runs with the `codx-junior` profile, so it has
 * the same streaming, tools, mentions, attachments and voice support as every
 * other conversation. This component only owns the drawer chrome and the
 * starter prompts.
 *
 * Starter prompts are sent through ChatView/Chat, so they use the exact same
 * optimistic message + store + streaming path as typed input.
 */
const props = defineProps({
  companyId: { type: String, default: '' },
  companyName: { type: String, default: '' },
  view: { type: String, default: 'home' },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false },
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  docked: { type: Boolean, default: false },
  showLauncher: { type: Boolean, default: true }
})

const emit = defineEmits(['refresh', 'navigate', 'deleted'])

const open = ref(false)
const loading = ref(false)
const error = ref('')
const chatId = ref('')
const chatView = ref(null)
const promptsOpen = ref(false)
const pendingPrompt = ref('')
let opening = null

// The store is the live source of truth: ChatView streams into it, so reading
// the chat from the store keeps the starter-prompt visibility in sync with the
// conversation without copying message arrays.
const chat = computed(() => (
  chatId.value ? $storex.chats.chats[chatId.value] : null
))
const hasUserMessages = computed(() => (
  (chat.value?.messages || []).some(message => message.role === 'user')
))
const processing = computed(() => (
  chatId.value ? $storex.chats.isChatUpdating(chatId.value) : false
))

const PROMPT_SETS = {
  home: [
    { icon: 'fa-solid fa-wand-magic-sparkles', label: "Today's brief", prompt: "Give me today's executive brief for the company: what matters now, what is moving, and the single most important decision." },
    { icon: 'fa-solid fa-bell', label: 'What needs me', prompt: 'What needs my attention today? Review goals, blocked work, approvals and project tickets, and tell me what to decide first.' },
    { icon: 'fa-solid fa-ticket', label: 'Review deliveries', prompt: 'Which project tickets are waiting for my review? Summarise each delivery and tell me whether to accept it or request changes.' },
    { icon: 'fa-solid fa-chart-line', label: 'Business status', prompt: 'How is the business doing? Cover revenue vs target, pipeline, margin and the biggest commercial risk.' }
  ],
  work: [
    { icon: 'fa-solid fa-ban', label: 'What is blocked', prompt: 'What goals or tickets are blocked, why, and what do you need from me to unblock them?' },
    { icon: 'fa-solid fa-forward', label: 'What to ship next', prompt: 'What should we ship next? Look at the open goals and the project ticket queues and recommend the next delivery.' },
    { icon: 'fa-solid fa-magnifying-glass', label: 'Review work', prompt: 'Review the work waiting for me and tell me what to accept or send back.' },
    { icon: 'fa-solid fa-rotate', label: 'Weekly review', prompt: 'Write a weekly review: what was delivered, what is blocked, what we learned and what to do next week.' }
  ],
  tickets: [
    { icon: 'fa-solid fa-magnifying-glass', label: 'To review', prompt: 'Which project tickets are waiting for my review? Summarise the delivery and validation evidence, and recommend accept or changes for each.' },
    { icon: 'fa-solid fa-triangle-exclamation', label: 'Blocked tickets', prompt: 'Show the tickets that are blocked or failed and explain the concrete blocker for each.' },
    { icon: 'fa-solid fa-person-running', label: 'What is running', prompt: 'What project tickets are queued or running right now? Summarise the state, rounds and expected next step.' },
    { icon: 'fa-solid fa-ticket', label: 'Place a ticket', prompt: 'Help me place a ticket: inspect the company projects, propose the target project, and draft the request with acceptance criteria and a validation gate.' }
  ],
  team: [
    { icon: 'fa-solid fa-people-group', label: 'Team status', prompt: 'Give me the status of every team, who is overloaded and what needs a manager decision.' },
    { icon: 'fa-solid fa-bolt', label: 'Who is on duty', prompt: 'Who is on duty right now, human and AI, and what are they working on?' },
    { icon: 'fa-solid fa-balance-scale', label: 'Load balance', prompt: 'Where is the workload unbalanced, and how should we redistribute it?' }
  ],
  people: [
    { icon: 'fa-solid fa-user-check', label: 'Who is on duty', prompt: 'Who is on duty right now and what are they working on?' },
    { icon: 'fa-solid fa-chart-simple', label: 'Performance', prompt: 'Give me a performance review of the team: delivery, blockers and where I should coach.' },
    { icon: 'fa-solid fa-user-plus', label: 'Add a person', prompt: 'Help me add a person to the company and place them in the right team.' }
  ],
  business: [
    { icon: 'fa-solid fa-arrow-trend-up', label: 'Close the gap', prompt: 'How do we close the revenue gap this period? Give me a concrete, sequenced plan.' },
    { icon: 'fa-solid fa-handshake', label: 'Pipeline', prompt: 'Which deals deserve attention this week, and why? Rank them by expected value and next action.' },
    { icon: 'fa-solid fa-coins', label: 'Where we lose money', prompt: 'Where are we losing money or margin, and what should we change first?' }
  ],
  activity: [
    { icon: 'fa-solid fa-rotate', label: 'Weekly review', prompt: 'Write a weekly review of the company: what was delivered, what is blocked, what we learned and what to do next.' },
    { icon: 'fa-solid fa-check', label: 'What we delivered', prompt: 'What did we deliver this week and what result did the company record?' },
    { icon: 'fa-solid fa-scale-balanced', label: 'Pending decisions', prompt: 'What decisions are pending and what do you recommend?' }
  ],
  settings: [
    { icon: 'fa-solid fa-sliders', label: 'Autonomy', prompt: 'How much autonomy do we allow, and which limits should we change for the next month?' },
    { icon: 'fa-solid fa-coins', label: 'AI budget', prompt: 'How is our AI budget behaving this month and where should we spend the next cxjcoins?' },
    { icon: 'fa-solid fa-people-group', label: 'People', prompt: 'Review the company people and teams: who is missing, who is overloaded and what should I change?' }
  ]
}

const prompts = computed(() => PROMPT_SETS[props.view] || PROMPT_SETS.home)
const showPrompts = computed(() => (
  !loading.value && !!chat.value && (promptsOpen.value || !hasUserMessages.value)
))

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

async function ensureChat() {
  if (!props.companyId) return null
  if (chat.value) return chat.value
  if (opening) return opening
  opening = (async () => {
    loading.value = true
    error.value = ''
    try {
      const locator = await API.companies.assistantChat(props.companyId)
      const id = locator?.chat_id || locator?.id
      if (!id) throw new Error('The assistant chat was not provisioned')
      const loaded = await $storex.chats.loadChat({
        id,
        owner_project_id: locator.owner_project_id || locator.project_id,
        project_id: locator.project_id
      })
      if (!loaded) throw new Error('The assistant chat could not be loaded')
      chatId.value = id
      await nextTick()
      if (pendingPrompt.value) {
        const prompt = pendingPrompt.value
        pendingPrompt.value = ''
        await sendPrompt(prompt)
      }
      return loaded
    } catch (ex) {
      error.value = errorMessage(ex)
      chatId.value = ''
      return null
    } finally {
      loading.value = false
      opening = null
    }
  })()
  return opening
}

async function sendPrompt(prompt) {
  const text = String(prompt || '').trim()
  if (!text) return false
  open.value = true
  promptsOpen.value = false
  if (!chat.value) {
    pendingPrompt.value = text
    open.value = true
    await ensureChat()
    return true
  }
  // The ChatView mounts its Chat child asynchronously (load hierarchy, profiles,
  // run state). Wait briefly for it before giving up on the start prompt.
  let component = null
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await nextTick()
    component = chatView.value?.$refs?.chatBox || null
    if (component) break
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  if (!component || typeof chatView.value?.sendPrompt !== 'function') {
    error.value = 'The chat is not ready yet. Try the prompt again in a moment.'
    return false
  }
  try {
    const sent = await chatView.value.sendPrompt(text)
    if (!sent) {
      error.value = 'The assistant is already answering. Wait for the current turn to finish.'
      return false
    }
    error.value = ''
    return true
  } catch (ex) {
    error.value = errorMessage(ex)
    return false
  }
}

async function openAssistant(prompt = '') {
  open.value = true
  if (prompt) {
    if (chat.value) await sendPrompt(prompt)
    else {
      pendingPrompt.value = prompt
      await ensureChat()
    }
    return
  }
  if (!chat.value) await ensureChat()
}

function close() {
  open.value = false
  promptsOpen.value = false
}

function togglePrompts() {
  promptsOpen.value = !promptsOpen.value
}

function onExternalOpen(event) {
  openAssistant(event?.detail?.prompt || '')
}

// A completed assistant turn can change the company (ticket placed, mission
// created...). Refresh the shell snapshot when a new assistant message lands.
watch(() => {
  const messages = chat.value?.messages || []
  const last = messages[messages.length - 1]
  return `${messages.length}:${last?.doc_id || ''}:${last?.done}:${last?.updated_at || ''}:${String(last?.content || '').length}`
}, () => {
  const messages = chat.value?.messages || []
  const last = messages[messages.length - 1]
  if (last?.role === 'assistant' && last?.done !== false) emit('refresh')
})

watch(() => props.companyId, (companyId, previous) => {
  if (!companyId || companyId === previous) return
  chatId.value = ''
  pendingPrompt.value = ''
  error.value = ''
  if (open.value || props.docked) ensureChat()
})

onMounted(() => {
  window.addEventListener('company-assistant:open', onExternalOpen)
  if (props.docked) ensureChat()
})

onBeforeUnmount(() => {
  window.removeEventListener('company-assistant:open', onExternalOpen)
})

defineExpose({ openAssistant, sendPrompt })
</script>

<template>
  <div
    v-if="!docked && open"
    class="fixed inset-0 z-[79] bg-black/35 backdrop-blur-[2px] transition-opacity"
    aria-hidden="true"
    @click="close"
  ></div>

  <aside
    v-if="docked || open"
    class="company-assistant flex flex-col bg-base-100"
    :class="docked
      ? 'relative h-full w-[min(46rem,100%)] shrink-0 border-l border-base-content/10'
      : 'fixed inset-y-0 right-0 z-[80] h-full w-full max-w-[46rem] border-l border-base-content/10 shadow-2xl max-sm:max-w-none'"
    aria-label="codx-junior company assistant"
  >
    <!-- Compact drawer chrome. ChatView owns the conversation header below. -->
    <header class="flex shrink-0 items-center gap-2.5 border-b border-base-content/10 px-3 py-2.5">
      <span class="relative shrink-0">
        <img src="/only_icon.png" alt="codx-junior" class="h-8 w-8 rounded-xl bg-base-300" />
        <span class="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-base-100 bg-success"></span>
      </span>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-1.5">
          <h2 class="text-sm font-black leading-tight">codx-junior</h2>
          <span class="badge badge-xs border-primary/20 bg-primary/10 text-primary-ink">codx-junior profile</span>
        </div>
        <p class="mt-0.5 truncate text-[11px] text-base-content/45">
          {{ companyName || 'Your company' }} · full agent chat
        </p>
      </div>
      <button
        class="btn btn-ghost btn-xs btn-square"
        :class="promptsOpen ? 'text-primary-ink' : 'text-base-content/45'"
        title="Starter prompts"
        aria-label="Starter prompts"
        @click="togglePrompts"
      >
        <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
      </button>
      <button
        v-if="!docked"
        class="btn btn-ghost btn-xs btn-square"
        title="Close assistant"
        aria-label="Close assistant"
        @click="close"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </header>

    <!-- Predefined queries. They disappear after the first user turn; the
         wand button brings them back. -->
    <div v-if="showPrompts" class="shrink-0 border-b border-base-content/5 bg-base-200/30 px-2 py-2">
      <div class="flex gap-1.5 overflow-x-auto pb-0.5">
        <button
          v-for="prompt in prompts"
          :key="prompt.label"
          type="button"
          class="btn btn-xs shrink-0 gap-1.5 rounded-full border-base-content/15 bg-base-100 normal-case"
          :title="prompt.prompt"
          :disabled="loading || processing"
          @click="sendPrompt(prompt.prompt)"
        >
          <i :class="prompt.icon" class="text-[10px] text-primary-ink"></i>
          {{ prompt.label }}
        </button>
      </div>
    </div>

    <div v-if="error" class="shrink-0 border-b border-error/20 bg-error/10 px-3 py-1.5 text-[11px] text-error">
      <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ error }}
    </div>

    <div class="relative min-h-0 flex-1">
      <ChatView
        v-if="chat"
        ref="chatView"
        :chat="chat"
        hide-chat-sidebar
        class="h-full"
      />
      <div v-else-if="loading" class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <span class="loading loading-dots loading-md text-primary"></span>
        <div>
          <p class="text-sm font-bold">Opening your company assistant…</p>
          <p class="mt-1 text-xs text-base-content/50">Loading the codx-junior conversation.</p>
        </div>
      </div>
      <div v-else class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <i class="fa-solid fa-comments text-3xl text-base-content/20"></i>
        <p class="text-sm font-bold">The assistant conversation is not ready</p>
        <button class="btn btn-primary btn-sm" @click="ensureChat">
          <i class="fa-solid fa-rotate text-xs"></i> Retry
        </button>
      </div>
    </div>
  </aside>

  <button
    v-if="!docked && !open && showLauncher"
    class="fixed bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] right-3 z-40 grid h-12 w-12 place-items-center rounded-full bg-secondary text-secondary-content shadow-xl ring-1 ring-black/5 transition hover:scale-105 active:scale-95"
    aria-label="Open the codx-junior assistant"
    @click="openAssistant()"
  >
    <i class="fa-solid fa-wand-magic-sparkles"></i>
  </button>
</template>

<!-- Made with ❤️ by codx-junior -->
