<script setup>
/**
 * One harness session, rendered with the SAME transcript as every chat.
 *
 * The earlier version reimplemented the conversation as a list of "exchanges"
 * (ticket → activity → last assistant answer). That hid messages: a task chat
 * routinely has several assistant turns and the task-mode cleanup archived the
 * previous ones, so the harness showed only the last reply and the agent lost
 * its own history on the next turn.
 *
 * A harness session is a normal codx-junior task chat, so the view now uses
 * `ChatMessageList` + `ChatEntry` (the components `Chat.vue` renders) with a
 * harness lens:
 *
 *   • every persisted message is visible (never "archived"),
 *   • the appended harness contract is stripped from the ticket,
 *   • suggested replies stay clickable on every assistant turn,
 *   • attachments, images, tool timelines and the composer are the chat ones.
 *
 * Only the harness extras stay local: the effort selector, the changed-files
 * block, the optional orchestration run and the session audit log.
 */
import { computed, nextTick, ref, watch } from 'vue'
import ChatMessageList from '@/components/chat/ChatMessageList.vue'
import ChatInputBox from '@/components/chat/ChatInputBox.vue'
import ChatAttachmentPreview from '@/components/chat/ChatAttachmentPreview.vue'
import HarnessFiles from './HarnessFiles.vue'
import { $storex } from '@/store'
import {
  attachmentFromPaste,
  attachmentPayloads,
  attachmentsFromFiles
} from '@/utils/composerAttachments'
import {
  diffFiles,
  formatDuration,
  formatTokens,
  harnessTranscript
} from '@/utils/harnessSteps.js'
import { HARNESS_TIERS, tierHint } from '@/utils/harnessMode.js'

const props = defineProps({
  session: { type: Object, default: null },
  detail: { type: Object, default: null },
  chat: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  models: { type: Array, default: () => [] },
  // Effort tier selected for the NEXT turn (`standard` / `deep`). It is
  // reachable from the session header, not only from the new-session composer.
  mode: { type: String, default: 'standard' }
})

const emit = defineEmits([
  'back',
  'open-chat',
  'open-git',
  'open-diagnostics',
  'open-project',
  'stop',
  'send',
  'refresh',
  'update:mode'
])

const messageList = ref(null)
const composer = ref(null)
const followModel = ref('')

/**
 * Messages of the chat with the harness contract removed and archived flags
 * cleared. The chats store owns the original objects (streaming updates keep
 * arriving); this list is only the transcript projection.
 */
const messages = computed(() =>
  harnessTranscript(props.chat?.messages?.length
    ? props.chat.messages
    : (props.detail?.chat?.messages || []))
)

const summary = computed(() => props.session || props.detail?.session || null)
const metrics = computed(() => props.detail?.metrics || {})
const files = computed(() => diffFiles(props.detail?.diff))

const phase = computed(() => String(summary.value?.phase || '').toLowerCase())
const live = computed(() => {
  if (phase.value === 'running') return true
  const last = messages.value[messages.value.length - 1] || null
  // A just-sent ticket/follow-up is pending until its assistant turn starts; a
  // streaming assistant is live; an archived/old unfinished message is not.
  if (last?.role === 'user') return phase.value !== 'failed'
  return last?.role === 'assistant' && last.done === false
})
const processing = computed(() => props.busy || live.value)

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

const toolCount = computed(() => Number(metrics.value?.tool_calls) || 0)

const projectName = computed(() => {
  const current = $storex.projects?.activeProject
  return current?.project_name || ''
})

// The model fallback warning is stamped on the assistant response by the turn
// engine. Surface it once, at the top of the transcript.
const modelWarning = computed(() => {
  const assistants = [...messages.value].reverse().filter(message => message.role === 'assistant')
  return assistants
    .map(message => message?.meta_data?.model_warning)
    .find(warning => warning && warning.message) || null
})

// ── Composer (follow-up turn) ─────────────────────────────────────────────

const pendingAttachments = ref([])

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

async function onDrop(event) {
  const files = Array.from(event?.dataTransfer?.files || [])
  if (files.length) await onUploadFiles(files)
}

function removeAttachment(index) {
  pendingAttachments.value = pendingAttachments.value.filter((_, ix) => ix !== index)
}

function onAttachmentUpdated({ index, attachment }) {
  if (!attachment || index < 0) return
  pendingAttachments.value = pendingAttachments.value.map((item, ix) =>
    ix === index ? attachment : item
  )
}

/**
 * Send the follow-up currently in the composer (or the payload of a clicked
 * suggested reply).
 *
 * @param {{text?: string, attachments?: object[]}|null} [explicit] Reply to
 *   send instead of the composer content.
 */
function sendFollowUp(explicit = null) {
  const text = String(
    explicit?.text ?? composer.value?.getEditorText?.() ?? ''
  ).trim()
  const attachments = explicit?.attachments ?? attachmentPayloads(pendingAttachments.value)
  if ((!text && !attachments.length) || props.busy || live.value) return
  emit('send', { text, model: followModel.value, attachments })
  if (!explicit) pendingAttachments.value = []
}

function onSend() {
  sendFollowUp()
}

/**
 * A suggested reply was clicked. It is sent as the next user message when the
 * composer is idle; with a draft or pending attachment it is appended to the
 * composer instead, so nothing typed by the user is lost.
 */
function onAnswerOption({ option } = {}) {
  const value = String(option?.value ?? option?.label ?? '').trim()
  if (!value || props.busy || live.value) return
  const draft = String(composer.value?.getEditorText?.() || '').trim()
  if (draft || pendingAttachments.value.length) {
    composer.value?.setEditorText?.(draft ? `${draft} ${value}` : value)
    return
  }
  sendFollowUp({ text: value, attachments: [] })
}

function clearComposer() {
  composer.value?.setEditorText?.('')
  pendingAttachments.value = []
}

// Enter sends, Shift+Enter adds a newline.
function onKeyDown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  onSend()
}

function scrollToBottom() {
  nextTick(() => messageList.value?.scrollToBottom?.(true))
}

defineExpose({ clearComposer, scrollToBottom })

// A completed session opens at the top (the ticket + the report); only a live
// session follows the stream to the bottom.
watch(
  () => messages.value.length,
  () => {
    if (live.value) scrollToBottom()
  }
)
watch(
  () => messages.value[messages.value.length - 1]?.content,
  () => {
    if (live.value) scrollToBottom()
  }
)
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
          <span v-if="toolCount">· {{ toolCount }} tools</span>
          <span v-if="summary?.profiles?.length" class="font-mono">· {{ summary.profiles.join(', ') }}</span>
          <span class="font-mono">· effort {{ tierHint(mode) }}</span>
          <span v-if="summary?.model" class="font-mono truncate max-w-40" :title="summary.model">· {{ summary.model }}</span>
          <span v-if="detail?.session?.branch || detail?.workspace?.branch" class="font-mono truncate max-w-56" :title="detail.session?.branch || detail.workspace.branch">
            · {{ detail.session?.branch || detail.workspace.branch }}
          </span>
        </div>
      </div>

      <div class="flex items-center gap-1 shrink-0">
        <!-- Effort: always reachable, also while continuing a session. -->
        <div class="join" role="group" aria-label="Effort">
          <button
            v-for="tier in HARNESS_TIERS"
            :key="tier.id"
            type="button"
            class="join-item btn btn-xs gap-1"
            :class="mode === tier.id ? 'btn-primary' : 'btn-ghost'"
            :title="tier.hint"
            :aria-pressed="mode === tier.id"
            @click="emit('update:mode', tier.id)"
          >
            <i :class="tier.icon" class="text-[9px]"></i>
            <span class="hidden sm:inline">{{ tier.shortLabel }}</span>
          </button>
        </div>
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
          :disabled="!(detail?.session?.branch || detail?.workspace?.branch)"
          :title="(detail?.session?.branch || detail?.workspace?.branch)
            ? `Review ${detail.session?.branch || detail.workspace.branch} in the Git view`
            : 'This session has no task branch to review'"
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

    <div v-if="error" class="shrink-0 alert alert-error text-xs py-2 mx-3 mt-2">
      <i class="fa-solid fa-triangle-exclamation"></i>
      {{ error }}
    </div>

    <!-- The transcript is the standard chat list + entry components. -->
    <ChatMessageList
      v-if="chat"
      ref="messageList"
      class="flex-1 min-h-0"
      :chat="chat"
      :messages="messages"
      :harness="true"
      :processing="processing"
      @answer-option="onAnswerOption"
    >
      <template #transcript-header>
        <div v-if="modelWarning?.message" class="alert alert-warning text-xs py-2 mb-3 items-start">
          <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
          <div class="min-w-0">
            <div class="font-semibold">Using the default model</div>
            <div class="opacity-80 break-words">{{ modelWarning.message }}</div>
          </div>
        </div>

        <div
          v-if="loading && !messages.length"
          class="flex items-center gap-2 text-xs text-base-content/40 py-10 justify-center"
        >
          <span class="loading loading-spinner loading-sm"></span>
          Loading session…
        </div>
      </template>

      <template #transcript-footer>
        <!-- Files changed on the session branch. -->
        <HarnessFiles
          v-if="detail"
          :files="files"
          :workspace="detail.workspace"
          :diff="detail.diff"
          class="mt-4"
        />

        <!-- Orchestration run (when the session promoted itself to a run). -->
        <div v-if="detail?.run" class="mt-3 rounded-xl border border-base-content/10 bg-base-200/30 px-3 py-2">
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

        <!-- Audit trail of this session. -->
        <details v-if="detail?.audit?.length" class="mt-3 rounded-xl border border-base-content/10 bg-base-200/20 px-3 py-2">
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
      </template>

      <template #input>
        <ChatInputBox
          ref="composer"
          :waiting="processing || loading"
          :ai-models="models"
          :profiles="[]"
          :selected-model="followModel"
          :project="null"
          :chat-project="null"
          :attachment-count="pendingAttachments.length"
          placeholder="Message or run a task…"
          @send="onSend"
          @keydown="onKeyDown"
          @paste="onPaste"
          @drop="onDrop"
          @upload-files="onUploadFiles"
          @model-changed="followModel = $event"
        />
        <!-- Pending attachments use the chat preview: image thumbnails, the
             full-size modal and the remove action are the standard ones. -->
        <ChatAttachmentPreview
          v-if="pendingAttachments.length"
          :attachments="pendingAttachments"
          size="sm"
          @remove-attachment="removeAttachment"
          @update-attachment="onAttachmentUpdated"
        />
      </template>
    </ChatMessageList>

    <div v-else class="flex-1 flex items-center justify-center gap-2 text-sm text-base-content/50">
      <span class="loading loading-spinner loading-sm"></span>
      Loading session…
    </div>
  </div>
</template>
