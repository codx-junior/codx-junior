<script setup>
import { computed, nextTick, ref } from 'vue'
import { API } from '@/api/api'

/**
 * Always-available setup assistant.
 *
 * The assistant is intentionally useful before an AI provider exists: the
 * backend answers with a preconfigured QA guide, and this component has the
 * same guide locally so a network/API failure still helps the user. Once a
 * provider and model are configured the backend upgrades the same panel to a
 * real AI chat.
 */
const props = defineProps({
  /** Wizard step / project count / AI state, sent with every question. */
  context: { type: Object, default: () => ({}) },
  /** Compact mode for the floating panel. */
  compact: { type: Boolean, default: false }
})

const messages = ref([
  {
    role: 'assistant',
    text:
      "Hi — I'm the setup assistant. I can help you add a project, connect a model, " +
      'or troubleshoot a fresh install. No AI key yet? I still work: these answers ' +
      'are preconfigured until you connect a provider.',
    source: 'qa'
  }
])
const text = ref('')
const loading = ref(false)
const error = ref('')
const scrollBox = ref(null)

const quickPrompts = [
  'How do I add a project?',
  'How do I connect a model?',
  'The project list is empty',
  'What should I do next?'
]

const projectCount = computed(() => Number(props.context?.projects || 0))
const hasAi = computed(() => Boolean(props.context?.hasAi))

function localQa(message) {
  const value = String(message || '').toLowerCase()
  const has = (...words) => words.some((word) => value.includes(word))
  if (has('ai', 'model', 'provider', 'api key', 'key', 'llm')) {
    return (
      'To connect a model:\n' +
      '1. Pick a provider preset (DeepSeek, OpenAI, OpenRouter, Mistral, Groq) or Custom.\n' +
      '2. Paste the API key. It stays on this server.\n' +
      '3. Keep the suggested chat model or type the provider id.\n' +
      '4. Save. The model becomes the default for chats and knowledge.\n\n' +
      'You can skip it now and use this guide without a model; configure AI later in Settings → AI Models.'
    )
  }
  if (has('project', 'repo', 'repository', 'folder', 'clone')) {
    return (
      'To add a project:\n' +
      '1. Enter a Git URL (for example https://github.com/you/repo.git) or an absolute folder path on this server.\n' +
      '2. Press Add project. codx-junior creates the .codx workspace and initialises Git if needed.\n' +
      '3. Select it and continue.\n\n' +
      'The list refreshes immediately after creation. A project without a model can still be browsed and configured.'
    )
  }
  if (has('empty', '500', 'undefined', 'error', 'broken', 'not working')) {
    return (
      'If a fresh install looks empty or an API returns 500:\n' +
      '1. Check /api/health and /api/setup/status.\n' +
      '2. Add project again and confirm the folder exists and is writable.\n' +
      '3. Settings need a project; without one the API now returns a clear 422.\n' +
      '4. Still blocked? Check docker logs <instance> and the request id.'
    )
  }
  if (has('install', 'start', 'next', 'begin')) {
    return (
      'The first-run flow is: add a project → optionally connect a model → open the project workspace.\n\n' +
      'A project is required. AI is optional: without it you can browse files, review Git and keep using this guide.'
    )
  }
  return (
    'I can help with three things:\n' +
    '• Add a project — Git URL or server folder.\n' +
    '• Connect a model — provider, API key and chat model.\n' +
    '• Troubleshoot — empty lists, settings 500s, first-run issues.\n\n' +
    'Ask in your own words, or use a quick prompt below.'
  )
}

async function scrollToBottom() {
  await nextTick()
  if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight
}

async function send(message) {
  const content = String(message ?? text.value ?? '').trim()
  if (!content || loading.value) return
  text.value = ''
  error.value = ''
  messages.value.push({ role: 'user', text: content })
  loading.value = true
  await scrollToBottom()
  const history = messages.value
    .slice(0, -1)
    .slice(-8)
    .map((item) => ({ role: item.role, content: item.text }))
  try {
    const response = await API.post('/api/setup/assistant', {
      message: content,
      context: { ...props.context },
      history
    })
    messages.value.push({
      role: 'assistant',
      text: response?.answer || localQa(content),
      source: response?.source || 'qa'
    })
  } catch (ex) {
    error.value = 'The setup service is unavailable; using the offline guide.'
    messages.value.push({
      role: 'assistant',
      text: localQa(content),
      source: 'offline'
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

defineExpose({ send })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-base-100">
    <div class="flex items-center gap-2 border-b border-base-300 px-4 py-3">
      <i class="fa-solid fa-robot text-primary"></i>
      <div class="min-w-0">
        <div class="text-sm font-bold">Setup assistant</div>
        <div class="text-[11px] text-base-content/60">
          {{ hasAi ? 'AI chat is ready' : 'Preconfigured guide — no AI key needed' }}
        </div>
      </div>
      <span class="badge badge-sm ml-auto" :class="hasAi ? 'badge-success' : 'badge-ghost'">
        {{ hasAi ? 'AI' : 'QA' }}
      </span>
    </div>

    <div ref="scrollBox" class="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <div
        v-for="(message, index) in messages"
        :key="index"
        class="flex flex-col gap-1"
        :class="message.role === 'user' ? 'items-end' : 'items-start'"
      >
        <div class="flex items-center gap-2 text-[10px] uppercase tracking-wide text-base-content/50">
          <span>{{ message.role === 'user' ? 'You' : 'Setup assistant' }}</span>
          <span v-if="message.source === 'ai'" class="badge badge-xs badge-success">AI</span>
          <span v-else-if="message.role === 'assistant'" class="badge badge-xs badge-ghost">Guide</span>
        </div>
        <div
          class="max-w-[92%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed"
          :class="message.role === 'user'
            ? 'bg-primary text-primary-content rounded-br-sm'
            : 'bg-base-200 text-base-content rounded-bl-sm'"
        >
          {{ message.text }}
        </div>
      </div>
      <div v-if="loading" class="flex items-center gap-2 text-xs text-base-content/60">
        <span class="loading loading-dots loading-sm"></span> Thinking…
      </div>
    </div>

    <div class="border-t border-base-300 p-3">
      <div v-if="!compact" class="mb-2 flex flex-wrap gap-1">
        <button
          v-for="prompt in quickPrompts"
          :key="prompt"
          type="button"
          class="btn btn-xs btn-ghost border border-base-300"
          :disabled="loading"
          @click="send(prompt)"
        >
          {{ prompt }}
        </button>
      </div>
      <form class="flex items-center gap-2" @submit.prevent="send()">
        <input
          v-model="text"
          type="text"
          class="input input-bordered input-sm min-w-0 flex-1"
          placeholder="Ask about projects, models or setup…"
          :disabled="loading"
        />
        <button type="submit" class="btn btn-primary btn-sm" :disabled="loading || !text.trim()">
          <i class="fa-solid fa-paper-plane"></i>
          <span class="sr-only">Send</span>
        </button>
      </form>
      <p v-if="error" class="mt-1 text-[11px] text-warning-ink">{{ error }}</p>
      <p class="mt-1 text-[10px] text-base-content/50">
        {{ projectCount }} project(s) · {{ hasAi ? 'model connected' : 'no model configured' }}
      </p>
    </div>
  </div>
</template>
