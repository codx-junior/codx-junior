<script setup>
/**
 * Harness-mode home: the "Into the Unknown" composer.
 *
 * It reuses the standard chat composer (ChatInputBox + its model/profile
 * selectors and attachment handling) and adds only what harness mode needs:
 * a workspace picker and the two execution tiers (Standard / Deep).
 */
import { ref } from 'vue'
import ChatInputBox from '@/components/chat/ChatInputBox.vue'
import ChatAttachmentPreview from '@/components/chat/ChatAttachmentPreview.vue'
import ProjectDetailt from '@/components/ProjectDetailt.vue'
import { $storex } from '@/store'
import {
  attachmentFromPaste,
  attachmentPayloads,
  attachmentsFromFiles
} from '@/utils/composerAttachments'
import { HARNESS_TIERS, tierHint } from '@/utils/harnessMode.js'

const props = defineProps({
  project: { type: Object, default: null },
  mode: { type: String, default: 'standard' },
  profiles: { type: Array, default: () => [] },
  selectedProfiles: { type: Array, default: () => [] },
  models: { type: Array, default: () => [] },
  selectedModel: { type: String, default: '' },
  creating: { type: Boolean, default: false },
  error: { type: String, default: '' }
})

const emit = defineEmits([
  'update:project',
  'update:mode',
  'update:profiles',
  'update:model',
  'submit'
])

const composer = ref(null)

function onSend() {
  const text = String(composer.value?.getEditorText?.() || '').trim()
  const attachments = attachmentPayloads(pendingAttachments.value)
  if ((!text && !attachments.length) || props.creating) return
  emit('submit', { text, attachments })
  pendingAttachments.value = []
}

function clear() {
  composer.value?.setEditorText?.('')
  pendingAttachments.value = []
}

// Pending attachments of the ticket (pasted screenshot / uploaded file).
// `ChatInputBox` only EMITS `paste` and `upload-files`; converting them into
// chat attachments is the host's job, like Chat.vue does for a full chat.
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
  event?.preventDefault?.()
  event?.stopPropagation?.()
  pendingAttachments.value = [...pendingAttachments.value, attachment]
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

async function onDrop(event) {
  const files = Array.from(event?.dataTransfer?.files || [])
  if (files.length) await onUploadFiles(files)
}

// Enter sends, Shift+Enter adds a newline (the chat composer convention).
function onKeyDown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  onSend()
}

defineExpose({ clear })
</script>

<template>
  <div class="h-full overflow-y-auto flex flex-col">
    <div class="flex-1 flex flex-col items-center justify-center px-4 py-10 sm:py-16">
      <div class="w-full max-w-3xl flex flex-col gap-6">
        <!-- Title -->
        <div class="flex items-center justify-center gap-2 flex-wrap">
          <h1 class="text-2xl sm:text-4xl font-semibold tracking-tight text-center">Into the Unknown</h1>
          <span class="badge badge-xs bg-primary/15 border-0 text-primary text-[9px] font-bold uppercase tracking-widest">
            Preview
          </span>
        </div>
        <p class="text-center text-xs sm:text-sm text-base-content/50 -mt-3">
          Give the harness a ticket. It investigates, fixes, tests and writes the report.
        </p>

        <!-- Composer (standard chat composer + harness controls) -->
        <ChatInputBox
          ref="composer"
          :waiting="creating"
          :ai-models="models"
          :profiles="profiles"
          :selected-profiles="selectedProfiles"
          :selected-model="selectedModel"
          :project="null"
          :chat-project="null"
          placeholder="Describe the ticket — what is broken, expected behaviour, acceptance…"
          :attachment-count="pendingAttachments.length"
          @send="onSend"
          @keydown="onKeyDown"
          @paste="onPaste"
          @drop="onDrop"
          @upload-files="onUploadFiles"
          @model-changed="emit('update:model', $event)"
          @profiles-selected="emit('update:profiles', $event)"
        >
          <template #before-textarea>
            <div class="flex flex-wrap items-center gap-2">
              <!-- Workspace (the project the session belongs to) -->
              <div class="min-w-0 max-w-64">
                <ProjectDetailt
                  :model-value="project"
                  :options="{ showIcon: true, showFolders: false, showSelector: true }"
                  @select="emit('update:project', $event)"
                />
              </div>

              <!-- Execution tier -->
              <div class="flex items-center rounded-lg border border-base-content/15 bg-base-100/40 p-0.5" role="group" aria-label="Harness tier">
                <button
                  v-for="tier in HARNESS_TIERS"
                  :key="tier.id"
                  type="button"
                  class="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors"
                  :class="mode === tier.id
                    ? 'bg-base-content/10 text-base-content font-medium'
                    : 'text-base-content/45 hover:text-base-content/80'"
                  :title="tier.hint"
                  :aria-pressed="mode === tier.id"
                  @click="emit('update:mode', tier.id)"
                >
                  <i :class="tier.icon" class="text-[10px]"></i>
                  {{ tier.label }}
                </button>
              </div>

              <span class="text-[10px] text-base-content/30 truncate">{{ tierHint(mode) }}</span>
            </div>
          </template>
        </ChatInputBox>

        <!-- Pasted / uploaded attachments of the ticket. The standard preview
             renders real image thumbnails (data: URL) and the full-size modal. -->
        <ChatAttachmentPreview
          v-if="pendingAttachments.length"
          :attachments="pendingAttachments"
          size="sm"
          @remove-attachment="removeAttachment"
          @update-attachment="onAttachmentUpdated"
        />

        <div v-if="error" class="alert alert-error text-xs py-2">
          <i class="fa-solid fa-triangle-exclamation"></i>
          {{ error }}
        </div>

        <!-- What the harness does, in one line each -->
        <div class="flex flex-wrap items-center justify-center gap-1.5 text-[10px] text-base-content/40">
          <span class="inline-flex items-center gap-1 rounded-full border border-base-content/10 px-2 py-0.5">
            <i class="fa-solid fa-magnifying-glass"></i> investigates
          </span>
          <span class="inline-flex items-center gap-1 rounded-full border border-base-content/10 px-2 py-0.5">
            <i class="fa-solid fa-pen-to-square"></i> implements
          </span>
          <span class="inline-flex items-center gap-1 rounded-full border border-base-content/10 px-2 py-0.5">
            <i class="fa-solid fa-flask-vial"></i> tests
          </span>
          <span class="inline-flex items-center gap-1 rounded-full border border-base-content/10 px-2 py-0.5">
            <i class="fa-solid fa-file-lines"></i> reports
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
