<script setup>
import ChatInputBox from '@/components/chat/ChatInputBox.vue'
import ChatAttachmentPreview from '@/components/chat/ChatAttachmentPreview.vue'
import ProjectDetailt from '@/components/ProjectDetailt.vue'
</script>

<template>
  <modal close :fullscreen="isMobile" @close="$emit('close')">
    <template #header>
      <h2 class="min-w-0 flex items-center gap-2 font-semibold text-sm md:text-base">
        <!-- The avatar identifies who answers the chat: the profile that
             assists managers (ProjectManager), same icon as the entry button -->
        <div class="avatar shrink-0" v-if="assistantAvatar">
          <div class="w-6 rounded-full bg-base-300">
            <img :src="assistantAvatar" :alt="assistantName" />
          </div>
        </div>
        <i v-else class="fa-solid fa-comment-dots text-primary text-xs"></i>

        <span class="shrink-0">{{ scheduleEnabled ? 'New chat' : 'Quick chat' }}</span>

        <span
          v-if="assistantName"
          class="badge badge-xs badge-primary gap-1 min-w-0"
          :title="`Assistant: ${assistantName}`"
        >
          <span class="truncate max-w-[9rem]">{{ assistantName }}</span>
        </span>

        <span
          v-if="boardName"
          class="badge badge-xs badge-ghost gap-1 min-w-0 hidden sm:inline-flex"
        >
          <i class="fa-solid fa-table-columns text-[8px]"></i>
          <span class="truncate max-w-[10rem]">{{ boardName }}</span>
        </span>
      </h2>
    </template>

    <div class="p-1 flex flex-col gap-3">
      <!-- Assistant context: the New entry opens a conversation with the
           codx-junior profile, not a generic board card. -->
      <div v-if="scheduleEnabled" class="flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 p-2.5">
        <div class="avatar shrink-0" v-if="assistantAvatar">
          <div class="w-7 rounded-full bg-base-300">
            <img :src="assistantAvatar" :alt="assistantName" />
          </div>
        </div>
        <i v-else class="fa-solid fa-wand-magic-sparkles text-primary text-xs mt-1"></i>
        <div class="min-w-0">
          <p class="text-xs font-semibold">{{ assistantName }} assistant</p>
          <p class="text-[11px] text-base-content/50">
            Describe the work. It becomes a task on the <b>codx-junior</b> board;
            choose whether it starts now, outside human working hours, or at a
            concrete instant.
          </p>
        </div>
      </div>

      <!-- ── Project the chat is registered in ─────────────────────────────────
           The project is OPTIONAL: it starts on the current ACTIVE project (a
           sensible default) and can be changed or cleared. It is never a
           requirement — the chat always belongs to the board, the project only
           decides which profiles, files and AI model it can use. -->
      <div class="flex flex-col gap-1.5">
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-semibold uppercase tracking-wider text-base-content/50">
            Project
            <span class="normal-case font-normal tracking-normal opacity-50">(optional)</span>
          </span>

          <!-- Clearing / restoring the project: nothing blocks the chat -->
          <button
            v-if="hasProject"
            type="button"
            class="text-[10px] text-base-content/40 hover:text-primary transition-colors"
            title="Create the chat without a project"
            aria-label="Create the chat without a project"
            @click="clearProject"
          >
            <i class="fa-solid fa-xmark text-[9px]"></i> None
          </button>
          <button
            v-else
            type="button"
            class="text-[10px] text-primary/80 hover:text-primary transition-colors disabled:opacity-40 disabled:pointer-events-none"
            title="Use the active project"
            aria-label="Use the active project"
            :disabled="!activeProject"
            @click="useActiveProject"
          >
            <i class="fa-solid fa-rotate-left text-[9px]"></i> Use active
          </button>
        </div>

        <!-- Same project tree selector used by the top bar: shows the project
             in use and opens the hierarchy (parent / children / others) to
             change it. -->
        <ProjectDetailt
          :model-value="targetProject"
          placeholder="No project (optional)"
          @update:model-value="onProjectSelected"
        />

        <p class="text-[11px] flex items-start gap-1 text-base-content/40">
          <i class="fa-solid fa-circle-info text-[10px] mt-0.5 shrink-0"></i>
          <span>{{ projectHint }}</span>
        </p>
      </div>

      <!-- ── Schedule ──────────────────────────────────────────────────────────
           A new chat/task does not have to start immediately. "Non-working
           hours" uses the installation's human working window, so autonomous
           work happens at night/weekends instead of colliding with the team. -->
      <div v-if="scheduleEnabled" class="flex flex-col gap-1.5">
        <span class="text-xs font-semibold uppercase tracking-wider text-base-content/50">
          Schedule
        </span>
        <div class="flex flex-wrap items-center gap-2">
          <label
            v-for="option in scheduleOptions"
            :key="option.value"
            class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs cursor-pointer transition-colors"
            :class="scheduleMode === option.value
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-base-100 text-base-content/50 hover:border-base-content/20'"
          >
            <input v-model="scheduleMode" type="radio" :value="option.value" class="hidden" />
            <i :class="option.icon + ' text-[10px]'"></i>
            {{ option.label }}
          </label>
        </div>
        <input
          v-if="scheduleMode === 'at'"
          v-model="scheduleAt"
          type="datetime-local"
          class="input input-sm input-bordered w-full"
        />
        <p class="text-[11px] flex items-start gap-1 text-base-content/40">
          <i class="fa-solid fa-circle-info text-[10px] mt-0.5 shrink-0"></i>
          <span>{{ scheduleHint }}</span>
        </p>
      </div>

      <!-- ── Message ─────────────────────────────────────────────────────────── -->
      <ChatInputBox
        ref="composer"
        :waiting="creating"
        :ai-models="aiModels"
        :profiles="profiles"
        :selected-profiles="selectedProfiles"
        :cursor-word="cursorWord"
        @send="submit(true)"
        @keydown="onKeyDown"
        @paste="onPaste"
        @drop="onDrop"
        @model-changed="model = $event"
        @profiles-selected="onProfilesSelected"
        @upload-files="onUploadFiles"
        @update:cursor-word="cursorWord = $event"
      />

      <!-- ── Attachments ─────────────────────────────────────────────────────── -->
      <ChatAttachmentPreview
        v-if="attachments.length"
        :attachments="attachments"
        @remove-attachment="removeAttachment"
      />

      <!-- ── Actions ─────────────────────────────────────────────────────────── -->
      <div class="flex items-center gap-2">
        <span class="mr-auto min-w-0 text-[11px] flex items-center gap-1 text-base-content/40">
          <i class="fa-solid fa-circle-info text-[10px] shrink-0"></i>
          <span class="truncate">{{ hint }}</span>
        </span>

        <button type="button" class="btn btn-ghost btn-sm" @click="$emit('close')">
          Cancel
        </button>

        <button
          type="button"
          class="btn btn-outline btn-sm gap-1.5"
          :disabled="!canSubmit"
          title="Create the chat without starting it"
          @click="submit(false)"
        >
          <i class="fa-solid fa-plus text-xs"></i> Add
        </button>

        <button
          type="button"
          class="btn btn-primary btn-sm gap-1.5"
          :disabled="!canSubmit"
          title="Create the chat and start it now"
          @click="submit(true)"
        >
          <span v-if="creating" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-paper-plane text-xs"></i>
          {{ primaryActionLabel }}
        </button>
      </div>
    </div>
  </modal>
</template>

<script>
import ChatAttachment from '@/api/model/ChatAttachment.js'
import { API } from '@/api/api'

// Attachments travel inline as base64 — keep them reasonably small
const MAX_ATTACHMENT_SIZE_MB = 50

export default {
  name: 'BoardQuickChat',
  props: {
    // Board the chat is created on (column / sprint membership)
    boardId: { type: String, required: true },
    // 'kanban' | 'sprint' — decides where the new card lands
    boardType: { type: String, default: 'kanban' },
    // Display name of the board (shown in the dialog header)
    boardName: { type: String, default: null },
    // Columns of the board (kanban only): the first real one receives the chat
    columns: { type: Array, default: () => [] },
    // Profiles pre-selected on the new chat. Managers are assisted by the
    // ProjectManager profile, so that is the default.
    defaultProfiles: { type: Array, default: () => ['ProjectManager'] },
    // Prompt the composer starts with (e.g. the board assistant's
    // "Summarize the status of this board…"). Editable before sending.
    initialMessage: { type: String, default: null },
    // Show the schedule selector. The New-chat entry enables it so a task can
    // run now, outside the human working hours, or at a concrete instant.
    scheduleEnabled: { type: Boolean, default: false },
    // Chat mode of the created card ('chat' or 'task').
    mode: { type: String, default: 'chat' }
  },
  emits: ['close', 'created'],
  data() {
    return {
      // Project the chat is registered in. OPTIONAL: it starts on the active
      // project and `null` means "create the chat without a project".
      selectedProject: null,
      // Profile that answers the chat (avatar in the header, preselected)
      assistantProfile: null,
      profiles: [],
      selectedProfiles: [],
      model: null,
      attachments: [],
      cursorWord: {},
      creating: false,
      maxAttachmentSizeMB: MAX_ATTACHMENT_SIZE_MB,
      // Scheduling: now (default), outside humans' working hours, or a fixed
      // instant. `wake_at` is what the autonomy worker reads.
      scheduleMode: 'now',
      scheduleAt: '',
      resolvingSchedule: false,
      scheduleOptions: [
        { value: 'now', label: 'Start now', icon: 'fa-solid fa-bolt' },
        { value: 'non-working', label: 'Non-working hours', icon: 'fa-solid fa-moon' },
        { value: 'at', label: 'At…', icon: 'fa-regular fa-clock' }
      ]
    }
  },
  created() {
    // Profiles already fetched per project id (the assistant and the composer
    // share them, so a project is never listed twice)
    this.profilesCache = {}
    // The project is a default, not a requirement: the chat starts on the
    // current ACTIVE project and can be changed or cleared from the dialog.
    this.selectedProject = this.activeProject
    this.loadProfiles()
    this.loadAssistantProfile()
  },
  mounted() {
    // Seed the composer with the prompt the caller asked for (board assistant
    // quick asks). The text stays editable and nothing is sent automatically.
    if (this.initialMessage) {
      this.$refs.composer?.setEditorText(this.initialMessage)
    }
  },
  computed: {
    isMobile() {
      return this.$ui.isMobile
    },
    // Project the dialog defaults to (the one currently open in the app)
    activeProject() {
      return this.$project || null
    },
    // Project the chat is finally registered in (optional — null ⇒ no project)
    targetProject() {
      return this.selectedProject || null
    },
    hasProject() {
      return !!this.targetProject?.project_id
    },
    // Profile the chat talks to (its avatar is the assistant icon)
    wantedAssistant() {
      return (this.defaultProfiles || [])[0] || 'ProjectManager'
    },
    assistant() {
      return this.assistantProfile || this.selectedProfiles[0] || null
    },
    assistantAvatar() {
      return this.assistant?.avatar_url || this.assistant?.avatar || null
    },
    assistantName() {
      return this.assistant?.name || this.wantedAssistant
    },
    aiModels() {
      return this.$storex.projects.ai?.models || []
    },
    // Column the chat lands in (kanban). Sprint boards resolve it themselves.
    destinationColumn() {
      const column = (this.columns || []).find(col => !col.virtual)
      return column?.value || column?.title || null
    },
    // Explains which project the chat goes to (and that it is optional)
    projectHint() {
      if (!this.hasProject) {
        return 'No project — the chat is created on this board without one. '
          + 'Pick a project to give it its profiles, files and AI model.'
      }
      return `Registered in ${this.targetProject.project_name} — its profiles, files and AI model. `
        + 'Optional: clear it to create the chat without a project.'
    },
    hint() {
      if (this.creating) return 'Creating the chat…'
      if (!this.hasProject) return 'Added to this board (no project)'
      return 'Added to this board'
    },
    // Only the message is required — the project never blocks the chat
    canSubmit() {
      return !this.creating && !this.resolvingSchedule
    },
    // The action button explains what will happen with the card.
    primaryActionLabel() {
      if (!this.scheduleEnabled || this.scheduleMode === 'now') return 'Chat'
      return 'Schedule'
    },
    scheduleHint() {
      if (this.scheduleMode === 'non-working') {
        return 'Runs outside the human working hours configured in Global Settings, so profiles do not collide with the team.'
      }
      if (this.scheduleMode === 'at') {
        return 'The task is created now and woken by the autonomy worker at that instant.'
      }
      return 'The chat starts immediately.'
    }
  },
  watch: {
    // A different project brings different profiles (and its ProjectManager)
    targetProject() {
      this.loadProfiles()
      this.loadAssistantProfile()
    }
  },
  methods: {
    // ── Project (optional) ───────────────────────────────────────────────────
    // The project can be changed (a project object or its id) or cleared: an
    // empty / unknown value means "no project", never an error.
    onProjectSelected(project) {
      const selected = typeof project === 'string'
        ? this.$storex.projects.allProjectsById[project]
        : project
      this.selectedProject = selected?.project_id ? selected : null
    },
    // Create the chat on the board only, without a project
    clearProject() {
      this.selectedProject = null
    },
    // Restore the active project as the chat project
    useActiveProject() {
      this.selectedProject = this.activeProject
    },
    // Profiles of a project, fetched once and shared by the composer and the
    // assistant avatar.
    async profilesFor(project) {
      const id = project?.project_id
      if (!id) return []
      if (this.profilesCache[id]) return this.profilesCache[id]
      const api = project.$api || project.api
      let list = []
      try {
        list = await api?.profiles?.list?.() || []
      } catch (error) {
        console.error('[BoardQuickChat] Could not load profiles:', error)
      }
      this.profilesCache[id] = [...list].sort((a, b) => (a.name > b.name ? 1 : -1))
      return this.profilesCache[id]
    },
    async loadProfiles() {
      const project = this.targetProject
      // No project → no project profiles to offer: the chat still keeps the
      // assistant it was opened for (see `createChat`)
      if (!project?.project_id) {
        this.profiles = []
        this.selectedProfiles = []
        return
      }
      const list = await this.profilesFor(project)
      // A slower response for a previous project must not overwrite the current
      if (project.project_id !== this.targetProject?.project_id) return
      this.profiles = list
      // Preselect the profiles that assist this kind of user
      this.selectedProfiles = (this.defaultProfiles || [])
        .map(name => list.find(profile => profile.name === name))
        .filter(Boolean)
    },
    // Avatar of the profile that answers: taken from the project the chat is
    // registered in, or from the active project while no project is chosen.
    async loadAssistantProfile() {
      const project = this.targetProject || this.activeProject
      const list = await this.profilesFor(project)
      this.assistantProfile = list.find(profile => profile.name === this.wantedAssistant) || null
    },
    onProfilesSelected(profiles) {
      this.selectedProfiles = profiles || []
    },

    // ── Composer ──────────────────────────────────────────────────────────────
    // Enter sends (shift+Enter adds a newline), like the chat composer
    onKeyDown(event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        this.submit(true)
      }
    },
    async onUploadFiles(files) {
      await this.addAttachments(files)
    },
    async onPaste(event) {
      const imageFile = await this.$service.chat.parseImageFromPaste(event)
      if (!imageFile) return
      event.preventDefault()
      await this.addAttachments([imageFile])
    },
    async onDrop(event) {
      const files = Array.from(event.dataTransfer?.files || [])
      if (files.length) await this.addAttachments(files)
    },
    async addAttachments(files) {
      for (const file of files || []) {
        const attachment = await this.prepareAttachment(file)
        if (attachment) this.attachments.push(attachment)
      }
    },
    async prepareAttachment(file) {
      if ((file?.size || 0) > this.maxAttachmentSizeMB * 1024 * 1024) {
        this.$ui?.addNotification?.({
          text: `File exceeds maximum size of ${this.maxAttachmentSizeMB}MB`,
          type: 'error'
        })
        return null
      }
      try {
        return await ChatAttachment.fromFile(file)
      } catch (error) {
        console.error('[BoardQuickChat] Failed to process attachment:', error)
        this.$ui?.addNotification?.({ text: 'Failed to process file', type: 'error' })
        return null
      }
    },
    removeAttachment(index) {
      this.attachments.splice(index, 1)
    },

    // ── Create ────────────────────────────────────────────────────────────────
    async submit(run) {
      if (!this.canSubmit) return
      const message = (this.$refs.composer?.getEditorText() || '').trim()
      if (!message) {
        this.$ui?.addNotification?.({ text: 'Write a message to start the chat', type: 'warning' })
        return
      }
      let wakeAt = ''
      this.resolvingSchedule = true
      try {
        wakeAt = await this.resolveWakeAt()
      } catch (error) {
        this.$ui?.addNotification?.({
          text: error?.response?.data?.detail || 'Could not resolve the schedule',
          type: 'error'
        })
        this.resolvingSchedule = false
        return
      } finally {
        this.resolvingSchedule = false
      }
      // A scheduled card is not processed now: the autonomy worker wakes it.
      this.createChat({ message, run: run && !wakeAt, wakeAt })
    },
    async resolveWakeAt() {
      if (!this.scheduleEnabled || this.scheduleMode === 'now') return ''
      if (this.scheduleMode === 'non-working') {
        const resolved = await API.tasks.resolveSchedule({ when: 'non-working-hours' })
        return resolved?.wake_at || ''
      }
      if (!this.scheduleAt) {
        throw new Error('Choose the instant when the task must run')
      }
      const date = new Date(this.scheduleAt)
      if (Number.isNaN(date.getTime())) {
        throw new Error('Invalid schedule instant')
      }
      return date.toISOString()
    },
    // The chat is created ON the board and only then tagged with its project:
    // the chat stays owned by the current project (it lives in this board) while
    // `project_id` records the project the work belongs to — the same contract
    // the board already uses for tasks assigned to another project. When the
    // project was cleared the chat keeps whatever project the board/column gave
    // it, so creating a board chat never requires a project.
    async createChat({ message, run, wakeAt = '' }) {
      this.creating = true
      try {
        const picked = this.selectedProfiles.map(profile => profile.name || profile)
        // The chat keeps the assistant it was opened for when no project
        // profile was picked (New chat → codx-junior).
        const profiles = picked.length ? picked : [...(this.defaultProfiles || [])]
        const name = this.chatName(message)

        let chat = null
        if (this.boardType === 'sprint') {
          chat = await this.$storex.kanban.newSprintTask({
            name,
            mode: this.mode,
            profiles,
            sprintId: this.boardId
          })
        } else {
          chat = await this.$storex.kanban.newTask({
            name,
            mode: this.mode,
            profiles,
            board: this.boardId,
            column: this.destinationColumn || 'tasks'
          })
        }

        if (!chat?.id) {
          this.$ui?.addNotification?.({ text: 'Could not create the chat', type: 'error' })
          return
        }

        const updates = {}
        if (this.hasProject) updates.project_id = this.targetProject.project_id
        if (this.model) updates.llm_model = this.model
        if (wakeAt) updates.wake_at = wakeAt
        if (Object.keys(updates).length) {
          await this.$storex.chats.updateChatInfo({ chat, updates })
        }

        const userMessage = this.$service.chat.getUserMessage({
          message,
          files: [],
          profiles,
          // `getUserMessage` does not forward attachments: they travel in the
          // message metadata so nothing the user attached is lost.
          metadata: this.attachmentMetadata(),
          user: this.$user?.username
        })
        await this.$storex.chats.addMessage({ chat, message: userMessage })

        this.$refs.composer?.setEditorText('')
        this.attachments = []
        if (wakeAt) {
          this.$ui?.addNotification?.({
            text: `Chat scheduled for ${new Date(wakeAt).toLocaleString()}`,
            type: 'success'
          })
        }

        // Close the dialog right away and let the work continue in the
        // background: the current view is never switched to the new chat.
        this.$emit('close')

        // "Chat" starts the agent right away; "Add" only files the card.
        // The run is NOT awaited: closing the modal must stay instant.
        if (run) {
          this.$storex.projects
            .chatWihProject(chat)
            .catch(error => console.error('[BoardQuickChat] Background run failed:', error))
        }
      } catch (error) {
        console.error('[BoardQuickChat] Could not create the chat:', error)
        this.$ui?.addNotification?.({ text: 'Could not create the chat', type: 'error' })
      } finally {
        this.creating = false
      }
    },
    attachmentMetadata() {
      if (!this.attachments.length) return undefined
      return {
        attachments: this.attachments.map(attachment => (
          attachment.toJSON ? attachment.toJSON() : attachment
        ))
      }
    },
    // Card name: the beginning of the message, on a single line
    chatName(message) {
      const text = `${message || ''}`.replace(/\s+/g, ' ').trim()
      return text.length > 60 ? `${text.slice(0, 60)}…` : (text || 'Quick chat')
    }
  }
}
</script>