<script setup>
/**
 * One harness session, in the DSH conversation shape:
 *
 *   ticket → collapsible "what the agent did" → delivery report → files changed
 *
 * The messages are the SAME `Chat` object the chats store streams (registered
 * by the harness store), so tool events and content update live while the turn
 * runs. The view model is built by `utils/harnessSteps.js`; this component only
 * lays it out and reuses the standard composer for follow-ups.
 */
import { computed, nextTick, ref, watch } from 'vue'
import Markdown from '@/components/Markdown.vue'
import ChatInputBox from '@/components/chat/ChatInputBox.vue'
import HarnessActivity from './HarnessActivity.vue'
import HarnessFiles from './HarnessFiles.vue'
import { $storex } from '@/store'
import {
  attachmentFromPaste,
  attachmentPayload,
  attachmentPayloads,
  attachmentsFromFiles
} from '@/utils/composerAttachments'
import {
  activitySummary,
  buildHarnessView,
  formatDuration,
  formatTokens
} from '@/utils/harnessSteps.js'

const props = defineProps({
  session: { type: Object, default: null },
  detail: { type: Object, default: null },
  chat: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  models: { type: Array, default: () => [] }
})

const emit = defineEmits([
  'back',
  'open-chat',
  'open-git',
  'open-diagnostics',
  'open-project',
  'stop',
  'send',
  'refresh'
])

const scroller = ref(null)
const composer = ref(null)
const followModel = ref('')

const messages = computed(() => {
  if (props.chat?.messages?.length) return props.chat.messages
  return props.detail?.chat?.messages || []
})

const summary = computed(() => props.session || props.detail?.session || null)
const metrics = computed(() => props.detail?.metrics || {})
const view = computed(() =>
  buildHarnessView(messages.value, {
    diff: props.detail?.diff || null,
    session: summary.value
  })
)

const phase = computed(() => String(summary.value?.phase || '').toLowerCase())
const live = computed(() => view.value.live || phase.value === 'running')

const status = computed(() => {
  if (live.value) {
    return { label: 'Working', icon: 'fa-solid fa-circle-notch fa-spin', badge: 'badge-info' }
  }
  if (phase.value === 'failed') {
    return { label: 'Failed', icon: 'fa-solid fa-circle-xmark', badge: 'badge-error' }
  }
  if (phase.value === 'done') {
    return { label: 'Completed', icon: 'fa-solid fa-circle-check', badge: 'badge-success' }
  }
  return { label: 'Ready', icon: 'fa-regular fa-clock', badge: 'badge-ghost' }
})

const durationSeconds = computed(() => {
  const value = summary.value?.duration_seconds ?? metrics.value?.duration_seconds
  return Number(value) || 0
})

const durationLabel = computed(() => {
  if (!durationSeconds.value) return ''
  if (live.value) return `Working for ${formatDuration(durationSeconds.value)}`
  if (phase.value === 'failed') return `Failed after ${formatDuration(durationSeconds.value)}`
  if (phase.value === 'done') return `Completed in ${formatDuration(durationSeconds.value)}`
  return formatDuration(durationSeconds.value)
})

const tokenLabel = computed(() => {
  const total = (Number(metrics.value?.input_tokens) || 0) + (Number(metrics.value?.output_tokens) || 0)
  return total ? `${formatTokens(total)} tokens` : ''
})

const projectName = computed(() => {
  const current = $storex.projects?.activeProject
  return current?.project_name || ''
})

const exchanges = computed(() =>
  view.value.exchanges.filter(exchange =>
    exchange.request ||
    exchange.assistant?.content ||
    exchange.assistant?.steps?.length
  )
)

function scrollToBottom() {
  nextTick(() => {
    const element = scroller.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

// A completed session opens at the top (the ticket + the report); only a live
// session follows the stream to the bottom. The follow-up composer calls the
// exposed `scrollToBottom` explicitly after sending.
watch(() => messages.value.length, () => {
  if (live.value) scrollToBottom()
})
watch(
  () => messages.value[messages.value.length - 1]?.content,
  () => {
    if (live.value) scrollToBottom()
  }
)

// Pending attachments of the follow-up composer. `ChatInputBox` only emits
// `paste` / `upload-files`: converting them into chat attachments is the host's
// job (a full chat does it inside Chat.vue), so harness mode does it here and
// posts them with the message.
const pendingAttachments = ref([])
const attachmentPreviews = computed(() => pendingAttachments.value.map(attachmentPayload))

function notifyError(text) {
  $storex.ui?.addNotification?.({ text, type: 'error' })
}

async function onUploadFiles(files) {
  const prepared = await attachmentsFromFiles(files, notifyError)
  if (!prepared.length) return
  pendingAttachments.value = [...pendingAttachments.value, ...prepared]
}

async function onPaste(event) {
  const attachment = await attachmentFromPaste(event, notifyError)
  if (!attachment) return
  // The image IS the message: do not let the paste also dump markup.
  event?.preventDefault?.()
  event?.stopPropagation?.()
  pendingAttachments.value = [...pendingAttachments.value, attachment]
}

function removeAttachment(index) {
  pendingAttachments.value = pendingAttachments.value.filter((_, ix) => ix !== index)
}

function sendFollowUp() {
  const text = String(composer.value?.getEditorText?.() || '').trim()
  const attachments = attachmentPayloads(pendingAttachments.value)
  if ((!text && !attachments.length) || props.busy || live.value) return
  emit('send', { text, model: followModel.value, attachments })
  pendingAttachments.value = []
}

function clearComposer() {
  composer.value?.setEditorText?.('')
  pendingAttachments.value = []
}

// Enter sends, Shift+Enter adds a newline.
function onKeyDown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  sendFollowUp()
}

defineExpose({ clearComposer, scrollToBottom })

function copyReport(text) {
  if (!text) return
  // The UI store already notifies "Text copied".
  $storex.ui.copyTextToClipboard(text)
}

</script>

<template>
  <div class="h-full min-h-0 flex flex-col">
    <!-- Header -->
    <header class="shrink-0 flex flex-wrap items-center gap-2 px-3 sm:px-5 py-2.5 border-b border-base-content/10 bg-base-200/30">
      <button
        v-if="$storex.ui.isMobile"
        type="button"
        class="btn btn-ghost btn-xs btn-square"
        title="Back to sessions"
        @click="emit('back')"
      >
        <i class="fa-solid fa-arrow-left"></i>
      </button>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 min-w-0">
          <h2 class="text-sm font-semibold truncate">{{ summary?.name || 'Harness session' }}</h2>
          <span class="badge badge-sm gap-1 shrink-0" :class="status.badge">
            <i :class="status.icon" class="text-[9px]"></i>
            {{ status.label }}
          </span>
        </div>
        <div class="flex items-center gap-2 text-[11px] text-base-content/40 mt-0.5 flex-wrap">
          <button
            v-if="projectName"
            type="button"
            class="inline-flex items-center gap-1 hover:text-base-content/70"
            @click="emit('open-project')"
          >
            <i class="fa-regular fa-folder"></i>{{ projectName }}
          </button>
          <span v-if="durationLabel">{{ durationLabel }}</span>
          <span v-if="tokenLabel">· {{ tokenLabel }}</span>
          <span v-if="view.toolCount">· {{ view.toolCount }} tools</span>
          <span v-if="summary?.profiles?.length" class="font-mono">· {{ summary.profiles.join(', ') }}</span>
          <span v-if="summary?.model" class="font-mono truncate max-w-40" :title="summary.model">· {{ summary.model }}</span>
          <span v-if="detail?.workspace?.branch" class="font-mono truncate max-w-56" :title="detail.workspace.branch">
            · {{ detail.workspace.branch }}
          </span>
        </div>
      </div>

      <div class="flex items-center gap-1 shrink-0">
        <button
          v-if="live"
          type="button"
          class="btn btn-xs btn-error btn-outline gap-1"
          :disabled="busy"
          title="Stop the running turn"
          @click="emit('stop')"
        >
          <span v-if="busy" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-stop text-[9px]"></i>
          Stop
        </button>
        <button
          v-if="chat"
          type="button"
          class="btn btn-xs btn-ghost gap-1"
          title="Open the task card with its full chat UI"
          @click="emit('open-chat')"
        >
          <i class="fa-solid fa-up-right-from-square text-[10px]"></i>
          <span class="hidden sm:inline">Task</span>
        </button>
        <button
          type="button"
          class="btn btn-xs btn-ghost gap-1"
          title="Review the branch in the Git view"
          @click="emit('open-git')"
        >
          <i class="fa-solid fa-code-branch text-[10px]"></i>
          <span class="hidden sm:inline">Git</span>
        </button>
        <button
          type="button"
          class="btn btn-xs btn-ghost btn-square"
          title="Refresh the session"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <i class="fa-solid fa-rotate text-[10px]" :class="{ 'animate-spin': loading }"></i>
        </button>
        <button
          type="button"
          class="btn btn-xs btn-ghost btn-square"
          title="Harness diagnostics"
          @click="emit('open-diagnostics')"
        >
          <i class="fa-solid fa-heart-pulse text-[10px]"></i>
        </button>
      </div>
    </header>

    <!-- Transcript -->
    <div ref="scroller" class="flex-1 min-h-0 overflow-y-auto">
      <div class="max-w-3xl mx-auto px-3 sm:px-6 py-4 flex flex-col gap-4">
        <div v-if="error" class="alert alert-error text-xs py-2">
          <i class="fa-solid fa-triangle-exclamation"></i>
          {{ error }}
        </div>

        <div v-if="view.modelWarning?.message" class="alert alert-warning text-xs py-2 items-start">
          <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
          <div class="min-w-0">
            <div class="font-semibold">Using the default model</div>
            <div class="opacity-80 break-words">{{ view.modelWarning.message }}</div>
          </div>
        </div>

        <div v-if="loading && !messages.length" class="flex items-center gap-2 text-xs text-base-content/40 py-10 justify-center">
          <span class="loading loading-spinner loading-sm"></span>
          Loading session…
        </div>

        <!-- One block per exchange: ticket/follow-up → activity → answer -->
        <section
          v-for="(exchange, index) in exchanges"
          :key="exchange.id"
          class="flex flex-col gap-2"
        >
          <div v-if="exchange.request" class="flex flex-col gap-1">
            <span class="text-[10px] font-bold uppercase tracking-widest text-base-content/35">
              {{ index === 0 ? 'Ticket' : 'Follow-up' }}
            </span>
            <div class="rounded-xl border border-base-content/10 bg-base-100/50 px-3 py-2.5">
              <p class="text-sm whitespace-pre-wrap break-words text-base-content/85">{{ exchange.request }}</p>
            </div>
          </div>

          <HarnessActivity
            v-if="exchange.assistant?.steps?.length"
            :steps="exchange.assistant.steps"
            :activity="activitySummary(exchange.assistant.steps)"
            :live="live && index === exchanges.length - 1"
          />

          <div v-if="exchange.assistant?.content" class="relative group/report">
            <button
              type="button"
              class="absolute right-0 -top-1 btn btn-ghost btn-xs btn-square opacity-0 group-hover/report:opacity-100 transition-opacity z-10"
              title="Copy the report"
              @click="copyReport(exchange.assistant.content)"
            >
              <i class="fa-regular fa-copy text-[10px]"></i>
            </button>
            <Markdown :text="exchange.assistant.content" />
          </div>

          <div
            v-else-if="live && index === exchanges.length - 1"
            class="flex items-center gap-2 rounded-xl border border-base-content/10 bg-base-200/30 px-3 py-2 text-xs text-base-content/50"
          >
            <span class="loading loading-spinner loading-xs"></span>
            Thinking…
          </div>

          <div
            v-else-if="exchange.assistant?.error"
            class="alert alert-error text-xs py-2"
          >
            <i class="fa-solid fa-circle-xmark"></i>
            {{ exchange.assistant.error }}
          </div>
        </section>

        <div v-if="!exchanges.length && !loading" class="py-10 text-center text-sm text-base-content/40">
          This session has no messages yet.
        </div>

        <!-- Files changed -->
        <HarnessFiles
          v-if="detail"
          :files="view.files"
          :workspace="detail.workspace"
          :diff="detail.diff"
        />

        <!-- Orchestration run (when the session promoted itself to a run) -->
        <div v-if="detail?.run" class="rounded-xl border border-base-content/10 bg-base-200/30 px-3 py-2">
          <div class="flex items-center gap-2 text-xs">
            <i class="fa-solid fa-diagram-project text-base-content/40"></i>
            <span class="font-semibold">Run</span>
            <span class="badge badge-ghost badge-xs">{{ detail.run.status }}</span>
            <span class="text-[11px] text-base-content/40">{{ detail.run.progress }}%</span>
          </div>
          <div class="mt-2 flex flex-col gap-1">
            <div
              v-for="step in detail.run.steps"
              :key="step.id"
              class="flex items-center gap-2 text-[11px] text-base-content/60"
            >
              <i
                class="w-3 text-center text-[9px]"
                :class="step.status === 'done'
                  ? 'fa-solid fa-circle-check text-success'
                  : step.status === 'failed'
                    ? 'fa-solid fa-circle-xmark text-error'
                    : 'fa-regular fa-clock text-base-content/30'"
              ></i>
              <span class="truncate">{{ step.title }}</span>
            </div>
          </div>
        </div>

        <!-- Audit trail of this session -->
        <details v-if="detail?.audit?.length" class="rounded-xl border border-base-content/10 bg-base-200/20 px-3 py-2">
          <summary class="text-[11px] text-base-content/45 cursor-pointer select-none">
            Session log ({{ detail.audit.length }})
          </summary>
          <div class="mt-2 flex flex-col gap-1 max-h-56 overflow-y-auto">
            <div
              v-for="(entry, index) in detail.audit.slice(0, 60)"
              :key="`${entry.ts}-${index}`"
              class="flex items-start gap-2 text-[10px] text-base-content/45"
            >
              <span class="font-mono shrink-0">{{ String(entry.ts || '').slice(11, 19) }}</span>
              <span class="font-semibold shrink-0">{{ entry.event }}</span>
              <span class="truncate">{{ entry.message || entry.request || '' }}</span>
            </div>
          </div>
        </details>
      </div>
    </div>

    <!-- Follow-up composer -->
    <div class="shrink-0 border-t border-base-content/10 bg-base-200/20 px-3 sm:px-5 py-2">
      <div class="max-w-3xl mx-auto">
        <ChatInputBox
          ref="composer"
          :waiting="busy || live || loading"
          :ai-models="models"
          :profiles="[]"
          :selected-model="followModel"
          :project="null"
          :chat-project="null"
          :attachment-count="pendingAttachments.length"
          placeholder="Message or run a task…"
          @send="sendFollowUp"
          @keydown="onKeyDown"
          @paste="onPaste"
          @upload-files="onUploadFiles"
          @model-changed="followModel = $event"
        />
        <!-- Pasted / uploaded attachments waiting for the follow-up -->
        <div v-if="attachmentPreviews.length" class="flex flex-wrap gap-1.5 px-1 pb-2">
          <div
            v-for="(item, index) in attachmentPreviews"
            :key="`${item.name}-${index}`"
            class="flex items-center gap-1.5 rounded-lg border border-base-content/15 bg-base-200/60 px-2 py-1 text-[11px]"
          >
            <img
              v-if="String(item.type).startsWith('image/')"
              :src="item.data"
              class="h-6 w-6 rounded object-cover"
              alt=""
            />
            <i v-else class="fa-solid fa-paperclip text-base-content/50"></i>
            <span class="max-w-32 truncate">{{ item.name }}</span>
            <button
              class="btn btn-ghost btn-xs px-1"
              title="Remove attachment"
              @click="removeAttachment(index)"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
