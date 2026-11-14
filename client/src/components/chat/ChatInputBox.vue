<script setup>
import ChatInputToolbar from './ChatInputToolbar.vue'
import EmojiPicker from './EmojiPicker.vue'
import FindReplaceBox from './FindReplaceBox.vue'
</script>

<template>
  <div
    ref="inputContainer"
    class="notion-composer flex flex-col transition-all duration-200 text-sm md:text-base"
    :class="[
      isDraggingOver
        ? 'bg-primary/10 ring-2 ring-primary rounded-lg md:rounded-xl'
        : isFocused
          ? 'ring-1 ring-base-300 shadow-md rounded-lg md:rounded-xl'
          : 'border border-base-content/50 rounded-lg md:rounded-xl hover:bg-base-200 hover:ring-1 hover:ring-base-300'
    ]"
    @dragover.prevent="isDraggingOver = true"
    @dragleave.prevent="isDraggingOver = false"
    @drop.prevent="onDrop"        
  >
    <!-- ── Collapsed pill ── -->
    <!-- Shown on every viewport: the composer collapses when the user activates
         the message section (reading intent), not only on mobile.
         `.stop`: the pill owns its own toggle, the click-outside listener must
         not duplicate it. -->
    <button
      v-if="collapsed"
      type="button"
      class="w-full flex items-center gap-3 px-4 py-3 rounded-lg md:rounded-xl text-left cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      aria-label="Expand the message composer"
      title="Expand the message composer"
      @click.stop="$emit('toggle-collapsed')"
    >
      <i class="fa-solid fa-pen text-base-content/40 text-sm shrink-0"></i>
      <span class="text-base-content/40 text-sm flex-1 truncate">Write a message...</span>
      <!-- Pending attachments / files indicator (previews are hidden while collapsed) -->
      <span
        v-if="attachmentCount || fileCount"
        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary/20 text-primary text-xs font-medium shrink-0"
      >
        <i class="fa-solid fa-paperclip text-[10px]"></i>
        {{ (attachmentCount || 0) + (fileCount || 0) }}
      </span>
      <i class="fa-solid fa-chevron-up text-base-content/30 text-xs"></i>
    </button>

    <!-- ── Full input (hidden while collapsed) ── -->
    <template v-if="!collapsed">
      <!-- Slot: extra content above the textarea (e.g. project selector) -->
      <div v-if="$slots['before-textarea']" class="px-2 md:px-3 pt-2 md:pt-3 pb-1">
        <slot name="before-textarea" />
      </div>

      <!-- Find/Replace Box -->
      <FindReplaceBox
        v-if="showFindReplace"
        :editor-text="getEditorText()"
        @close="showFindReplace = false"
        @find-replace="onFindReplace"
      />

      <!-- Emoji Picker Popup -->
      <EmojiPicker
        v-if="cursorWord.word?.startsWith(':')"
        class="px-2 md:px-3 py-2 md:py-2 border-b border-base-100/50"
        :emoji-name="cursorWord.word"
        @emoji="onEmojiSelected"
      />

      <!-- Textarea — ghost style, expands naturally -->
      <div class="relative px-2 md:px-3 pt-2 md:pt-3">
        <textarea
          ref="editor"
          rows="3"
          class="w-full bg-transparent resize-none outline-none text-sm md:text-base text-base-content placeholder:text-base-content/30 leading-relaxed"
          :placeholder="isEditing ? 'Edit your message...' : 'Write something, or @ to mention a file...'"
          @keydown="onKeyDown"
          @paste="$emit('paste', $event)"
          @focus="onFocus"
          @blur="onBlur"
          @input="onInput"
        ></textarea>
      </div>

      <!-- Divider -->
      <div class="mx-2 md:mx-3 border-t border-base-100/50 mt-1"></div>

      <!-- Toolbar -->
      <!-- The voice note recorder lives in the toolbar: it only renders/emits,
           the MediaRecorder lifecycle stays here so the mic is released whenever
           the composer is collapsed, unmounted or the recording is cancelled. -->
      <ChatInputToolbar
        :waiting="waiting"
        :is-editing="isEditing"
        :is-voice-session="isVoiceSession"
        :searching="searching"
        :read-only="readOnly"
        :selected-model="selectedModel"
        :ai-models="aiModels"
        :images="images"
        :profiles="profiles"
        :selected-profiles="selectedProfiles"
        :project="project"
        :chat-project="chatProject"
        :voice-language-label="voiceLanguageLabel"
        :is-recording="isRecording"
        :recording-label="recordingLabel"
        @send="onSend"
        @cancel-edit="$emit('cancel-edit')"
        @model-changed="$emit('model-changed', $event)"
        @toggle-voice="$emit('toggle-voice')"
        @remove-image="$emit('remove-image', $event)"
        @preview-image="$emit('preview-image', $event)"
        @profiles-selected="$emit('profiles-selected', $event)"
        @project-changed="$emit('project-changed', $event)"
        @upload-files="$emit('upload-files', $event)"
        @start-voice-note="startVoiceNoteRecording"
        @stop-voice-note="stopVoiceNoteRecording(false)"
        @cancel-voice-note="stopVoiceNoteRecording(true)"
      />
    </template>
  </div>
</template>

<script>
export default {
  props: {
    waiting: Boolean,
    isEditing: Boolean,
    isVoiceSession: Boolean,
    searching: Boolean,
    readOnly: Boolean,
    selectedModel: String,
    aiModels: { type: Array, default: () => [] },
    images: { type: Array, default: () => [] },
    voiceLanguageLabel: String,
    profiles: { type: Array, default: () => [] },
    selectedProfiles: { type: Array, default: () => [] },
    // Project the message is addressed to (message.project_id) and the chat's
    // project used as default / override detection.
    project: { type: Object, default: null },
    chatProject: { type: Object, default: null },
    // Chat that owns the composer's unsent draft. It is OPTIONAL and only an
    // explicit value enables the draft feature: a NEW-CHAT composer (quick chat
    // dialogs, "New Task") has no chat yet, so it must never persist its text as
    // the draft of the currently active — and unrelated — chat.
    chatId: { type: [String, Number], default: null },
    cursorWord: { type: Object, default: () => ({}) },
    // Collapsed composer: only the pill is rendered (mobile reading mode, or any
    // viewport after the user activates the message section).
    collapsed: { type: Boolean, default: false },
    // Counters shown on the collapsed pill (previews are hidden while collapsed)
    attachmentCount: { type: Number, default: 0 },
    fileCount: { type: Number, default: 0 }
  },
  emits: [
    'send', 'add-message', 'cancel-edit', 'model-changed',
    'toggle-voice', 'remove-image', 'preview-image', 'keydown', 'paste',
    'drop', 'profiles-selected', 'project-changed', 'upload-files',
    'focus', 'blur', 'toggle-collapsed', 'update:cursor-word'
  ],
  data() {
    return {
      isFocused: false,
      isDraggingOver: false,
      showFindReplace: false,
      // Draft kept in memory while the composer is collapsed
      collapsedDraft: '',
      // ── Server draft (unsent message) ──
      // Text last synchronised with the server, so identical content is not
      // re-saved on every keystroke.
      draftSavedContent: '',
      // True once the user typed: hydration never overwrites in-progress text.
      draftDirty: false,
      draftTimer: null,
      draftDebounceMs: 700,
      // ── Voice note recorder ──
      isRecording: false,
      recordingSeconds: 0,
      recordingMaxSeconds: 300,
      recordingMimeType: '',
      recordingChunks: [],
      recordingTimer: null,
      discardVoiceNote: false,
      mediaRecorder: null,
      recordingStream: null
    }
  },
  computed: {
    isMobile() {
      return this.$ui.isMobile
    },
    // Chat whose unsent draft the composer owns. ONLY the explicit `chatId` prop
    // counts: never the active chat of the store. Falling back to the active chat
    // leaked the text typed while creating a chat/task into an unrelated
    // conversation, which could then hydrate it or overwrite it.
    draftChatId() {
      return this.chatId || null
    },
    // Label rendered by the toolbar while recording: "0:12 / 5:00"
    recordingLabel() {
      return `${this.formatRecordingTime(this.recordingSeconds)} / ${this.formatRecordingTime(this.recordingMaxSeconds)}`
    }
  },
  watch: {
    collapsed(collapsed) {
      if (collapsed) {
        // The editor is unmounted while collapsed, keep the draft in memory
        this.collapsedDraft = this.$refs.editor?.value || ''
        // The textarea disappears without firing blur: drop the focus styling
        // so the pill never renders with the editor's focus ring
        this.isFocused = false
        // The recorder controls disappear with the toolbar, never keep the microphone open
        if (this.isRecording) this.stopVoiceNoteRecording(true)
        // Keep the server draft in sync while the textarea is unmounted
        this.scheduleDraftSync()
      } else {
        // Put the draft back as soon as the editor is mounted again
        this.$nextTick(() => this.restoreCollapsedDraft())
      }
    },
    // Chat switched: persist what is still pending for the chat being left, then
    // hydrate the composer with the draft of the chat that is opening.
    draftChatId(chatId, previousChatId) {
      const hadPending = !!this.draftTimer
      const pendingContent = this.getEditorText()
      this.cancelDraftTimer()
      if (hadPending && previousChatId && previousChatId !== chatId) {
        // The pending write belonged to the chat we are leaving
        this.draftWrite(previousChatId, pendingContent)
      }
      this.draftDirty = false
      this.draftSavedContent = ''
      this.hydrateChatDraft()
    }
  },
  mounted() {
    // Mobile: toggle the composer when the user taps inside (expand) or outside (collapse).
    // Desktop collapses on demand (see the `collapsed` prop), so no global listener there.
    document.addEventListener('click', this.onDocumentClick)
    // Recover the draft the user left unsent in this chat
    this.hydrateChatDraft()
  },
  unmounted() {
    document.removeEventListener('click', this.onDocumentClick)
    // Never leave a debounce timer writing for a destroyed composer
    this.cancelDraftTimer()
    // Release the microphone if the composer is destroyed mid-recording
    this.cleanupVoiceNoteRecorder()
  },
  methods: {
    // ── Mobile collapse ─────────────────────────────────────

    // Single authority for the inside/outside transitions.
    // The pill handles its own toggle with `.stop`, so a pill tap never reaches here.
    onDocumentClick(event) {
      if (!this.isMobile || this.isEditing) return
      const insideComposer = this.isInsideComposer(event.target)
      if (this.collapsed) {
        // Tapping the composer area (pill, attachment or files preview) expands it
        if (insideComposer) this.$emit('toggle-collapsed')
      } else if (!insideComposer) {
        // Tapping outside while reading the messages collapses it
        this.$emit('toggle-collapsed')
      }
    },
    isInsideComposer(target) {
      const container = this.$refs.inputContainer
      if (!container || !target) return false
      // The composer also hosts parent rendered items (attachments, message files,
      // intellisense) inside the wrapper marked with data-chat-composer
      const composer = container.closest('[data-chat-composer]') || container
      return composer.contains(target)
    },
    restoreCollapsedDraft() {
      const editor = this.$refs.editor
      if (!editor) return
      editor.value = this.collapsedDraft
      this.autoResize()
    },

    // ── Server draft (unsent message) ──────────────────────

    // Fill the composer with the draft the user left unsent in this chat.
    // It is loaded on its own and NEVER pushed into the message list.
    hydrateChatDraft() {
      const chatId = this.draftChatId
      if (!chatId || this.isEditing || this.readOnly) return
      this.$chats.getChatDraft({ chatId }).then(draft => {
        // The chat may have changed while the request was in flight
        if (this.draftChatId !== chatId) return
        const content = (draft && draft.content) || ''
        this.draftSavedContent = content
        if (!content) return
        // Never overwrite what the user already typed
        if (this.draftDirty) return
        this.$nextTick(() => {
          if (this.draftChatId !== chatId || this.draftDirty) return
          if (this.getEditorText()) return
          this.setEditorText(content)
        })
      }).catch(() => {})
    },
    // Debounced write: at most one request per `draftDebounceMs` while typing.
    scheduleDraftSync() {
      // No owning chat (new-chat composer) ⇒ there is no draft to persist
      if (!this.draftChatId || this.isEditing || this.readOnly) return
      this.cancelDraftTimer()
      this.draftTimer = setTimeout(() => {
        this.draftTimer = null
        this.syncDraftNow()
      }, this.draftDebounceMs)
    },
    cancelDraftTimer() {
      if (this.draftTimer) clearTimeout(this.draftTimer)
      this.draftTimer = null
    },
    // Persist the current composer text when it differs from the last saved one.
    // The store wrapper never throws, so typing is never blocked by a failure.
    syncDraftNow() {
      const chatId = this.draftChatId
      // Without an explicit chatId nothing is read, written or deleted
      if (!chatId || this.isEditing || this.readOnly) return
      const content = this.getEditorText()
      if (content === this.draftSavedContent) return
      this.draftSavedContent = content
      this.draftWrite(chatId, content)
    },
    // Empty text removes the draft, anything else upserts it.
    draftWrite(chatId, content) {
      if (!chatId) return
      if (content) {
        this.$chats.saveChatDraft({ chatId, content })
      } else {
        this.$chats.deleteChatDraft({ chatId })
      }
    },

    // ── Focus handling ──────────────────────────────────────

    onFocus() {
      this.isFocused = true
      this.$emit('focus')
    },
    onBlur(event) {
      const relatedTarget = event.relatedTarget
      const inputContainer = this.$refs.inputContainer

      if (!relatedTarget || !inputContainer?.contains(relatedTarget)) {
        this.isFocused = false
        this.$emit('blur')
      }
    },
    onKeyDown(event) {
      // Check for Ctrl+H to toggle find/replace
      if (event.key === 'h' && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
        event.preventDefault()
        this.showFindReplace = !this.showFindReplace
        if (this.showFindReplace) {
          this.$nextTick(() => this.$refs.findReplace?.focusFindInput())
        }
        return
      }

      // Check for Ctrl+Shift+Enter to add message without sending to AI
      if (event.key === 'Enter' && event.ctrlKey && event.shiftKey) {
        event.preventDefault()
        event.stopPropagation()
        this.$emit('add-message')
        return
      }

      // Pass other keydown events to parent
      this.$emit('keydown', event)
    },
    onSend() {
      // The API deletes the draft atomically when the message is sent: cancel the
      // pending write so it never re-creates a draft right after sending.
      this.cancelDraftTimer()
      this.draftSavedContent = ''
      this.draftDirty = false
      this.$emit('send')
    },
    onDrop(event) {
      this.isDraggingOver = false
      this.$emit('drop', event)
    },
    onInput() {
      this.autoResize()
      this.updateCursorWord()
      // The user is typing: never let hydration overwrite the composer
      this.draftDirty = true
      this.scheduleDraftSync()
    },
    getEditorText() {
      // While collapsed the editor is unmounted, return the kept draft
      if (!this.$refs.editor) return this.collapsedDraft
      return this.$refs.editor.value || ''
    },
    setEditorText(text) {
      if (this.$refs.editor) {
        this.$refs.editor.value = text
        this.autoResize()
      } else {
        // Composer is collapsed: keep the text so it is restored when expanded
        this.collapsedDraft = text || ''
      }
      // Programmatic change from the parent (send / clear / mention): keep the
      // server draft in sync as well.
      this.scheduleDraftSync()
    },
    appendEditorText(text) {
      if (!this.$refs.editor) {
        // Composer is collapsed: keep the text so it is restored when expanded
        this.collapsedDraft += text || ''
      } else {
        this.$refs.editor.value += text
        this.autoResize()
      }
      this.draftDirty = true
      this.scheduleDraftSync()
    },
    focusEditor() {
      this.$refs.editor?.focus()
    },
    autoResize() {
      const el = this.$refs.editor
      if (!el) return
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 320) + 'px'
    },
    getCaretWordInfo() {
      const textarea = this.$refs.editor
      if (!textarea) return {}
      const caretIndex = textarea.selectionStart
      const text = textarea.value
      let wordStart = caretIndex
      while (wordStart > 0 && /\S/.test(text[wordStart - 1])) {
        wordStart--
      }
      return {
        word: text.slice(wordStart, caretIndex),
        caretIndex
      }
    },
    updateCursorWord() {
      const wordInfo = this.getCaretWordInfo()
      this.$emit('update:cursor-word', wordInfo)
    },
    onEmojiSelected({ emoji }) {
      const textarea = this.$refs.editor
      if (!textarea) return

      const text = textarea.value
      const caretIndex = textarea.selectionStart
      
      let emojiStart = caretIndex
      while (emojiStart > 0 && /\S/.test(text[emojiStart - 1])) {
        emojiStart--
      }
      
      const newText = text.slice(0, emojiStart) + emoji + text.slice(caretIndex)
      textarea.value = newText
      
      const newCursorPos = emojiStart + emoji.length
      textarea.selectionStart = newCursorPos
      textarea.selectionEnd = newCursorPos
      
      this.autoResize()
      this.updateCursorWord()
      textarea.focus()
    },
    onFindReplace(payload) {
      const textarea = this.$refs.editor
      if (!textarea) return

      const { type, match, replaceText, findText } = payload

      if (type === 'replace-one' && match) {
        const text = textarea.value
        const newText = text.slice(0, match.index) + replaceText + text.slice(match.index + match.length)
        textarea.value = newText
        textarea.selectionStart = match.index
        textarea.selectionEnd = match.index + replaceText.length
        this.autoResize()
      } else if (type === 'replace-all') {
        const text = textarea.value
        const regex = new RegExp(this.escapeRegex(findText), 'g')
        const newText = text.replace(regex, replaceText)
        textarea.value = newText
        this.autoResize()
      } else if (type === 'scroll' && payload.index !== undefined) {
        textarea.selectionStart = payload.index
        textarea.selectionEnd = payload.index + payload.length
        textarea.focus()
      }
    },
    escapeRegex(str) {
      return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    },

    // ── Voice notes ─────────────────────────────────────────

    isVoiceNoteSupported() {
      return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
    },
    // First codec container the browser can actually record
    pickVoiceNoteMimeType() {
      const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4'
      ]
      return candidates.find(type => window.MediaRecorder?.isTypeSupported?.(type)) || ''
    },
    voiceNoteExtension(mimeType) {
      if (mimeType.includes('mp4')) return 'm4a'
      if (mimeType.includes('ogg')) return 'ogg'
      if (mimeType.includes('wav')) return 'wav'
      return 'webm'
    },
    formatRecordingTime(seconds) {
      const total = Math.max(0, Math.floor(seconds || 0))
      const minutes = Math.floor(total / 60)
      const secs = `${total % 60}`.padStart(2, '0')
      return `${minutes}:${secs}`
    },
    async startVoiceNoteRecording() {
      if (this.isRecording || this.readOnly) return
      if (!this.isVoiceNoteSupported()) {
        this.$ui?.addNotification?.({ text: 'Voice notes are not supported in this browser', type: 'warning' })
        return
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const mimeType = this.pickVoiceNoteMimeType()
        const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)

        this.recordingChunks = []
        this.recordingStream = stream
        this.recordingMimeType = recorder.mimeType || mimeType || 'audio/webm'
        this.discardVoiceNote = false
        this.recordingSeconds = 0

        recorder.ondataavailable = (event) => {
          if (event.data?.size) this.recordingChunks.push(event.data)
        }
        recorder.onstop = () => this.onVoiceNoteRecorderStop()

        this.mediaRecorder = recorder
        recorder.start()
        this.isRecording = true
        this.startRecordingTimer()
        // Escape cancels the recording, matching the rest of the composer shortcuts
        document.addEventListener('keydown', this.onRecordingKeyDown)
      } catch (error) {
        console.error('[VoiceNote] Unable to start recording', error)
        this.cleanupVoiceNoteRecorder()
        this.$ui?.addNotification?.({ text: 'Microphone access denied', type: 'error' })
      }
    },
    stopVoiceNoteRecording(cancel = false) {
      if (!this.mediaRecorder) {
        this.cleanupVoiceNoteRecorder()
        return
      }
      this.discardVoiceNote = cancel
      this.isRecording = false
      this.stopRecordingTimer()
      document.removeEventListener('keydown', this.onRecordingKeyDown)
      if (this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop()
      else this.cleanupVoiceNoteRecorder()
    },
    onVoiceNoteRecorderStop() {
      const chunks = this.recordingChunks
      const mimeType = this.recordingMimeType
      const discard = this.discardVoiceNote

      this.cleanupVoiceNoteRecorder()

      if (discard || !chunks.length) return

      // `MediaRecorder.mimeType` carries the codec list ("audio/webm;codecs=opus").
      // The attachment stores the container type only: a bare mime type keeps the
      // `data:` URL used by the preview player valid in every browser and avoids
      // the attachment type being rejected when the message is persisted.
      const fileType = (mimeType || 'audio/webm').split(';')[0].trim()
      const extension = this.voiceNoteExtension(fileType)
      const blob = new Blob(chunks, { type: fileType })
      const stamp = new Date().toISOString().replace(/[:.]/g, '-')
      const file = new File([blob], `voice-note-${stamp}.${extension}`, {
        type: fileType,
        lastModified: Date.now()
      })

      // Reuse the composer upload pipeline: the parent turns the file into a
      // ChatAttachment, which is stored, previewed and removable like any other
      this.$emit('upload-files', [file])
    },
    onRecordingKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        this.stopVoiceNoteRecording(true)
      }
    },
    startRecordingTimer() {
      this.stopRecordingTimer()
      this.recordingTimer = setInterval(() => {
        this.recordingSeconds++
        // Hard cap so a forgotten recording can never grow unbounded
        if (this.recordingSeconds >= this.recordingMaxSeconds) {
          this.stopVoiceNoteRecording(false)
        }
      }, 1000)
    },
    stopRecordingTimer() {
      if (this.recordingTimer) clearInterval(this.recordingTimer)
      this.recordingTimer = null
    },
    cleanupVoiceNoteRecorder() {
      this.stopRecordingTimer()
      document.removeEventListener('keydown', this.onRecordingKeyDown)

      // Stopping the tracks releases the microphone indicator
      this.recordingStream?.getTracks().forEach(track => track.stop())
      this.recordingStream = null

      if (this.mediaRecorder) {
        this.mediaRecorder.ondataavailable = null
        this.mediaRecorder.onstop = null
      }
      this.mediaRecorder = null
      this.recordingChunks = []
      this.recordingSeconds = 0
      this.isRecording = false
      this.discardVoiceNote = false
    }
  },
  expose: [
    'getEditorText',
    'setEditorText',
    'appendEditorText',
    'focusEditor',
    'getCaretWordInfo'
  ]
}
</script>
