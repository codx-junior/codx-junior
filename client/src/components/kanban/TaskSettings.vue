<script setup>
import ProfileSelector from '../profile/ProfileSelector.vue'
</script>

<template>
  <!-- ── Properties dialog ──
       The fields of a chat/task, grouped in the three decisions the user actually
       makes: WHAT it is (identity), WHERE it lives (organization) and WHO works
       on it (assistant). One column, one label style, one control height — the
       previous flat list of nine identical blocks gave every field the same
       weight, so nothing guided the eye.
       `isEmbedded` keeps the panel usable inside a host pane: no header, no
       footer, no danger zone (the host owns those). -->
  <div class="flex flex-col gap-4 w-full">

    <!-- Header (dialog mode) — the modal can render its own (`hideHeader`) -->
    <header v-if="!isEmbedded && !hideHeader" class="flex items-start gap-3 pb-3 border-b border-base-content/10">
      <div class="w-9 h-9 rounded-lg bg-primary/15 text-primary grid place-items-center shrink-0">
        <i class="fa-solid fa-sliders" aria-hidden="true"></i>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="text-sm font-semibold text-base-content truncate">{{ title }}</h3>
        <p class="text-[11px] text-base-content/50 truncate font-mono">
          {{ taskData.board || '—' }}
          <span class="mx-1 opacity-50">/</span>{{ taskData.column || 'unassigned' }}
          <template v-if="shortId">
            <span class="mx-1 opacity-50">·</span>{{ shortId }}
          </template>
        </p>
      </div>
      <button
        type="button"
        class="btn btn-xs btn-ghost shrink-0"
        aria-label="Close properties"
        title="Close"
        @click="$emit('close')"
      >
        <i class="fa-solid fa-xmark" aria-hidden="true"></i>
      </button>
    </header>

    <div class="space-y-5" :class="!isEmbedded && 'py-1'">

      <!-- ── Identity ── -->
      <section>
        <h4 class="text-[10px] font-semibold uppercase tracking-[0.08em] text-base-content/45 mb-2">Identity</h4>
        <div class="space-y-3">

          <label class="block">
            <span class="block text-[11px] font-medium text-base-content/60 mb-1">Name</span>
            <input
              v-model="taskData.name"
              type="text"
              placeholder="Chat name"
              class="input input-sm input-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
              :class="!hasName && 'input-error'"
            />
            <span v-if="!hasName" class="block mt-1 text-[11px] text-error">
              A name is required to save.
            </span>
          </label>

          <label class="block">
            <span class="block text-[11px] font-medium text-base-content/60 mb-1">Project</span>
            <select
              v-model="taskData.project_id"
              class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
            >
              <option :value="null">{{ $project.project_name }}</option>
              <option v-for="project in projects" :key="project.project_id" :value="project.project_id">
                {{ project.project_name }}
              </option>
            </select>
          </label>

          <label class="block">
            <span class="block text-[11px] font-medium text-base-content/60 mb-1">Mode</span>
            <select
              v-model="taskData.mode"
              class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
            >
              <option v-for="mode in modeOptions" :key="mode.value" :value="mode.value">
                {{ mode.label }}
              </option>
            </select>
          </label>
        </div>
      </section>

      <!-- ── Organization ── -->
      <section>
        <h4 class="text-[10px] font-semibold uppercase tracking-[0.08em] text-base-content/45 mb-2">Organization</h4>
        <div class="space-y-3">

          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="block text-[11px] font-medium text-base-content/60 mb-1">Board</span>
              <select
                v-model="taskData.board"
                class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
                :class="!hasBoard && 'select-error'"
              >
                <option v-for="board in boards" :key="board" :value="board">{{ board }}</option>
              </select>
            </label>

            <label class="block">
              <span class="block text-[11px] font-medium text-base-content/60 mb-1">Column</span>
              <select
                v-model="taskData.column"
                class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
                :class="!hasColumn && 'select-error'"
              >
                <option v-for="column in columns" :key="column.title" :value="column.title">
                  {{ column.title }}
                </option>
              </select>
            </label>
          </div>
          <p v-if="!hasBoard || !hasColumn" class="text-[11px] text-error">
            Pick a board and a column to save.
          </p>

          <label class="block">
            <span class="block text-[11px] font-medium text-base-content/60 mb-1">Parent chat</span>
            <select
              v-model="taskData.parent_id"
              class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
            >
              <option :value="null">None — top level</option>
              <option v-for="chat in orderedChats" :key="chat.id" :value="chat.id">{{ chat.name }}</option>
            </select>
          </label>

          <!-- Schedule: `wake_at` is the alarm clock of the autonomy worker, not a
               deadline (that is `due_date`). Setting it moves the task to the
               after-hours shift and draws it in the calendar. -->
          <div>
            <div class="flex items-center gap-2 mb-1">
              <span class="text-[11px] font-medium text-base-content/60">Schedule</span>
              <span
                v-if="taskData.wake_at"
                class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border border-warning/40 bg-warning/10 text-warning text-[10px] font-medium"
                :title="wakeAtLabel"
              >
                <i class="fa-regular fa-clock text-[9px]" aria-hidden="true"></i>
                {{ wakeAtLabel }}
              </span>
              <div class="grow"></div>
              <button
                v-if="taskData.wake_at"
                type="button"
                class="btn btn-xs btn-ghost gap-1"
                title="Remove the schedule"
                @click="clearWakeAt"
              >
                <i class="fa-solid fa-xmark text-[10px]" aria-hidden="true"></i> Clear
              </button>
            </div>

            <div class="flex flex-wrap gap-1 mb-2">
              <button
                v-for="preset in wakePresetOptions"
                :key="preset.label"
                type="button"
                class="btn btn-xs btn-outline gap-1"
                :title="preset.hint"
                @click="applyWakePreset(preset)"
              >
                {{ preset.label }}
              </button>
            </div>

            <input
              v-model="wakeAtLocal"
              type="datetime-local"
              class="input input-sm input-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
            />
            <p class="mt-1 text-[10px] text-base-content/40">
              When the agent picks this task up on its own. Use an after-hours time (e.g. 01:00) so it can change code while nobody is working.
            </p>

            <div class="mt-3 rounded-lg border border-base-300/60 bg-base-200/30 p-2 space-y-2">
              <div class="flex items-center gap-2">
                <span class="text-[11px] font-medium text-base-content/60">Repeat</span>
                <span
                  v-if="taskData.schedule"
                  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border border-primary/40 bg-primary/10 text-primary text-[10px] font-medium"
                  :title="taskData.schedule"
                >
                  <i class="fa-solid fa-rotate text-[9px]" aria-hidden="true"></i>
                  {{ taskData.schedule }}
                </span>
                <div class="grow"></div>
                <button
                  v-if="taskData.schedule"
                  type="button"
                  class="btn btn-xs btn-ghost gap-1"
                  title="Stop repeating"
                  @click="clearRepeat"
                >
                  <i class="fa-solid fa-xmark text-[10px]" aria-hidden="true"></i> Clear
                </button>
              </div>

              <input
                v-model="repeatText"
                type="text"
                placeholder="every friday at noon"
                class="input input-sm input-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
                @keyup.enter="applyRepeat()"
              />

              <div class="flex flex-wrap gap-1">
                <button
                  v-for="preset in repeatPresets"
                  :key="preset"
                  type="button"
                  class="btn btn-xs btn-outline"
                  @click="applyRepeat(preset)"
                >
                  {{ preset }}
                </button>
              </div>

              <label class="flex items-center gap-2 text-[11px] text-base-content/60">
                <span>Each run</span>
                <select
                  v-model="repeatContext"
                  class="select select-xs select-bordered bg-base-100"
                >
                  <option value="history">sees the previous runs (same card)</option>
                  <option value="clean">starts clean (new card)</option>
                </select>
              </label>

              <p v-if="repeatError" class="text-[10px] text-error">{{ repeatError }}</p>
              <p v-else-if="repeatInfo" class="text-[10px] text-success">{{ repeatInfo }}</p>
              <p v-else-if="taskData.schedule && taskData.wake_at" class="text-[10px] text-base-content/40">
                Next run: {{ wakeAtLabel }}
              </p>
            </div>
          </div>
        </div>
      </section>

      <!-- ── Assistant ── -->
      <section>
        <h4 class="text-[10px] font-semibold uppercase tracking-[0.08em] text-base-content/45 mb-2">Assistant</h4>
        <div class="space-y-3">

          <!-- Profiles: removable chips, add lives next to the label -->
          <div>
            <div class="flex items-center gap-2 mb-1">
              <span class="text-[11px] font-medium text-base-content/60">Profiles</span>
              <span class="text-[10px] text-base-content/40 tabular-nums">{{ profileNames.length }}</span>
              <div class="grow"></div>
              <button
                type="button"
                class="btn btn-xs btn-ghost gap-1"
                title="Add profile"
                @click="showProfileSelector = true"
              >
                <i class="fa-solid fa-plus text-[10px]" aria-hidden="true"></i> Add
              </button>
            </div>

            <div v-if="profileNames.length" class="flex flex-wrap gap-1">
              <span
                v-for="profile in profileNames"
                :key="profile"
                class="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md border border-primary/30 bg-primary/10 text-primary text-[11px] font-medium"
              >
                {{ profile }}
                <button
                  type="button"
                  class="w-4 h-4 grid place-items-center rounded text-primary/60 hover:text-error hover:bg-error/10 transition-colors duration-150"
                  :aria-label="`Remove profile ${profile}`"
                  :title="`Remove ${profile}`"
                  @click="removeProfile(profile)"
                >
                  <i class="fa-solid fa-xmark text-[9px]" aria-hidden="true"></i>
                </button>
              </span>
            </div>
            <p v-else class="text-[11px] text-base-content/40">No profile: the chat uses the project defaults.</p>
          </div>

          <label class="block">
            <span class="block text-[11px] font-medium text-base-content/60 mb-1">AI model</span>
            <select
              v-model="taskData.llm_model"
              class="select select-sm select-bordered w-full bg-base-100 focus:border-primary/50 focus:ring-2 focus:ring-primary/40"
            >
              <option value="">Default</option>
              <option v-for="model in aiModels" :key="model.name" :value="model.name">
                {{ model.name }}{{ model.ai_model ? ` (${model.ai_model})` : '' }}
              </option>
            </select>
          </label>
        </div>
      </section>

      <!-- ── Danger zone (dialog mode only) ──
           Two steps instead of a collapsed section: the destructive action is
           visible, but a single misclick cannot delete the conversation. -->
      <section v-if="!isEmbedded" class="rounded-lg border border-error/25 bg-error/5 px-2.5 py-2">
        <div class="flex items-center gap-2">
          <div class="min-w-0 flex-1">
            <p class="text-xs font-medium text-error">Delete this chat</p>
            <p class="text-[11px] text-base-content/50">Removed with its messages. This cannot be undone.</p>
          </div>
          <template v-if="!confirmingDelete">
            <button type="button" class="btn btn-xs btn-outline btn-error shrink-0" @click="confirmingDelete = true">
              Delete
            </button>
          </template>
          <template v-else>
            <button type="button" class="btn btn-xs btn-ghost shrink-0" @click="confirmingDelete = false">Cancel</button>
            <button type="button" class="btn btn-xs btn-error shrink-0" @click="deleteTask">Confirm</button>
          </template>
        </div>
      </section>
    </div>

    <!-- Footer (dialog mode only) — one primary action, unsaved state spelled out -->
    <footer v-if="!isEmbedded" class="flex items-center gap-2 pt-3 border-t border-base-content/10">
      <span v-if="isDirty" class="inline-flex items-center gap-1.5 text-[11px] text-warning">
        <i class="fa-solid fa-circle text-[6px]" aria-hidden="true"></i>
        Unsaved changes
      </span>
      <div class="grow"></div>
      <button type="button" class="btn btn-sm btn-ghost" @click="$emit('close')">Discard</button>
      <button
        type="button"
        class="btn btn-sm btn-primary gap-1"
        :disabled="!canSave"
        @click="saveChanges"
      >
        <i class="fa-solid fa-floppy-disk text-[11px]" aria-hidden="true"></i>
        Save
      </button>
    </footer>

    <modal close="true" @close="showProfileSelector = false" v-if="showProfileSelector">
      <ProfileSelector @select="addProfile($event)" :project="taskProject" />
    </modal>
  </div>
</template>

<script>
export default {
  props: {
    taskData: {
      type: Object,
      required: true
    },
    isEmbedded: {
      type: Boolean,
      default: false
    },
    // Heading used when this component renders its own header
    title: {
      type: String,
      default: 'Task settings'
    },
    // The host dialog already prints a title + close button (ChatView's modal
    // header slot): drop the internal one to avoid a duplicated header bar.
    hideHeader: {
      type: Boolean,
      default: false
    }
  },
  data() {
    return {
      showProfileSelector: false,
      confirmingDelete: false,
      // After-hours presets: the hour is fixed, the day is resolved on click.
      wakePresetOptions: [
        { label: 'Tonight 01:00', hour: 1, days: 0, hint: 'Next 01:00 (tonight, or tomorrow if it already passed)' },
        { label: 'Tomorrow 01:00', hour: 1, days: 1, hint: 'Tomorrow at 01:00 local time' },
        { label: '+2 days 01:00', hour: 1, days: 2, hint: 'In two days at 01:00 local time' }
      ],
      // Recurrence presets: the text is parsed by the API and stored canonical.
      repeatPresets: [
        'every friday at noon',
        'every monday at 09:00',
        'every day at 09:00',
        'every weekday at 09:00'
      ],
      repeatText: '',
      repeatContext: 'history',
      repeatError: '',
      repeatInfo: '',
      // Snapshot of the editable fields, to tell the user there is nothing to save
      snapshot: null
    }
  },
  created() {
    this.snapshot = this.fieldsSnapshot()
  },
  computed: {
    taskAIModel() {
      return this.aiModels.find(m => m.name === this.taskData.llm_model)
    },
    aiModels() {
      return this.$projects.ai.models
    },
    // Boards are plain strings: the previous comparator read `.title` off a
    // string, so the list was never sorted.
    boards() {
      return Object.keys(this.$projects.kanban.boards || {}).sort((a, b) => a.localeCompare(b))
    },
    columns() {
      return this.$projects.kanban.boards?.[this.taskData.board]?.columns || []
    },
    hasName() {
      return !!this.taskData.name?.trim()
    },
    hasBoard() {
      return !!this.taskData.board
    },
    hasColumn() {
      return !!this.taskData.column
    },
    canSave() {
      return this.hasName && this.hasBoard && this.hasColumn
    },
    // `allProjects` is a store array: sorting it in place reordered every other
    // view that reads it. Sort a copy.
    projects() {
      return [...this.$projects.allProjects]
        .sort((a, b) => a.project_name.toLowerCase().localeCompare(b.project_name.toLowerCase()))
    },
    taskProject() {
      if (this.taskData.project_id) {
        return this.projects.find(p => p.project_id === this.taskData.project_id)
      }
      return this.$project
    },
    allChats() {
      return this.$projects.allChats
    },
    // Chats of the current board first, then the rest
    orderedChats() {
      const boardOf = chat => chat.board || chat.board_id
      return this.allChats
        .filter(chat => boardOf(chat) === this.taskData.board)
        .concat(this.allChats.filter(chat => boardOf(chat) !== this.taskData.board))
    },
    // The store owns the list of modes ('chat', 'task', 'agent', …). Using it
    // keeps this dialog from silently rewriting an unsupported mode to ''
    modeOptions() {
      const modes = this.$storex?.chats?.availableChatModes || [
        { value: 'chat', label: 'Chat' },
        { value: 'task', label: 'Task' }
      ]
      const options = modes.map(mode => ({ value: mode.value, label: mode.label || mode.value }))
      // Never hide the mode the chat already has
      if (this.taskData.mode && !options.some(mode => mode.value === this.taskData.mode)) {
        options.unshift({ value: this.taskData.mode, label: this.taskData.mode })
      }
      return options
    },
    profileNames() {
      return this.taskData.profiles || []
    },
    // `wake_at` is stored as an ISO instant and edited as a local
    // `datetime-local` string, so both directions are converted here.
    wakeAtLocal: {
      get() {
        const iso = this.taskData.wake_at
        if (!iso) return ''
        const date = new Date(iso)
        if (Number.isNaN(date.getTime())) return ''
        const pad = value => String(value).padStart(2, '0')
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
      },
      set(value) {
        this.taskData.wake_at = value ? this.toIsoInstant(value) : null
      }
    },
    wakeAtLabel() {
      const iso = this.taskData.wake_at
      if (!iso) return ''
      const date = new Date(iso)
      if (Number.isNaN(date.getTime())) return ''
      return date.toLocaleString(undefined, {
        weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
      })
    },
    shortId() {
      return this.taskData.id ? String(this.taskData.id).slice(0, 8) : ''
    },
    isDirty() {
      if (!this.snapshot) return false
      return this.fieldsSnapshot() !== this.snapshot
    }
  },
  watch: {
    taskData: {
      handler() {
        this.confirmingDelete = false
        this.snapshot = this.fieldsSnapshot()
        this.ensureValidColumn()
      }
    },
    // Changing the board swaps the column list: keep a column that exists
    'taskData.board'() {
      this.ensureValidColumn()
    }
  },
  methods: {
    fieldsSnapshot() {
      const data = this.taskData || {}
      return JSON.stringify({
        name: data.name || '',
        project_id: data.project_id || null,
        parent_id: data.parent_id || null,
        board: data.board || '',
        column: data.column || '',
        mode: data.mode || '',
        llm_model: data.llm_model || '',
        wake_at: data.wake_at || '',
        profiles: [...(data.profiles || [])].sort()
      })
    },
    ensureValidColumn() {
      const columns = this.columns
      if (!columns.length) return
      if (!columns.find(column => column.title === this.taskData.column)) {
        this.taskData.column = ''
      }
    },
    saveChanges() {
      if (!this.canSave) return
      this.$chats.saveChatInfo(this.taskData)
      this.snapshot = this.fieldsSnapshot()
      this.$emit('close')
    },
    deleteTask() {
      this.confirmingDelete = false
      this.$chats.deleteChat(this.taskData)
      this.$emit('close')
    },
    addProfile(profile) {
      if (!profile?.name) return
      if (!this.taskData.profiles) this.taskData.profiles = []
      if (!this.taskData.profiles.includes(profile.name)) {
        this.taskData.profiles.push(profile.name)
      }
      this.showProfileSelector = false
    },
    removeProfile(profile) {
      this.taskData.profiles = (this.taskData.profiles || []).filter(name => name !== profile)
    },
    // Resolve a preset to the next occurrence of its hour, never the past.
    nextWakeInstant(hour, daysAhead) {
      const date = new Date()
      date.setDate(date.getDate() + (daysAhead || 0))
      date.setHours(hour, 0, 0, 0)
      if (date.getTime() <= Date.now()) {
        date.setDate(date.getDate() + 1)
        date.setHours(hour, 0, 0, 0)
      }
      return date.toISOString()
    },
    toIsoInstant(value) {
      const date = value instanceof Date ? value : new Date(value)
      return Number.isNaN(date.getTime()) ? null : date.toISOString()
    },
    applyWakePreset(preset) {
      this.taskData.wake_at = this.nextWakeInstant(preset.hour, preset.days)
    },
    clearWakeAt() {
      this.taskData.wake_at = null
    },

    // ── Repeat (recurrence) ──────────────────────────────────────────────
    // A recurrence is NOT part of the metadata save: arming `wake_at` and
    // remembering the home column is the scheduler's job, so it goes through
    // /api/chats/schedule, which returns the canonical rule and the next run.
    applyRepeat(text) {
      const value = String(text === undefined ? this.repeatText : text || '').trim()
      if (!value) return
      return this.sendSchedule(value)
    },
    clearRepeat() {
      return this.sendSchedule('')
    },
    async sendSchedule(schedule) {
      const chatId = this.taskData && this.taskData.id
      if (!chatId) return
      this.repeatError = ''
      this.repeatInfo = ''
      try {
        const result = await this.$chats.scheduleChat({
          chat_id: chatId,
          schedule,
          context: this.repeatContext,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
        })
        if (result && result.error) {
          this.repeatError = result.error
          return
        }
        this.taskData.schedule = result.schedule || null
        this.taskData.schedule_context = result.context || this.repeatContext
        this.taskData.wake_at = result.wake_at || null
        this.repeatInfo = result.next_run
          ? `Repeats ${result.schedule} — next run ${this.formatInstant(result.next_run)}`
          : 'Repeat cleared'
        this.repeatText = ''
        this.snapshot = this.fieldsSnapshot()
      } catch (error) {
        this.repeatError = (error && error.message) || 'Could not save the repeat rule'
      }
    },
    formatInstant(iso) {
      const date = new Date(iso)
      if (Number.isNaN(date.getTime())) return iso
      return date.toLocaleString(undefined, {
        weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
      })
    }
  }
}
</script>