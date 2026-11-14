<template>
  <section class="flex flex-col gap-4 px-4 py-4">
    <!-- ── Who can create ─────────────────────────────────────────────────── -->
    <div
      v-if="!isAdmin"
      class="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-xs leading-relaxed"
    >
      <p class="font-bold">Admins only</p>
      <p class="mt-1 opacity-80">
        Creating and building content is restricted to project admins. You can read
        the formats below, but saving, uploading and building are disabled.
      </p>
    </div>

    <p v-if="error" class="rounded-xl border border-error/40 bg-error/10 px-4 py-3 text-xs">
      {{ error }}
    </p>
    <p v-if="notice" class="rounded-xl border border-success/40 bg-success/10 px-4 py-3 text-xs">
      {{ notice }}
    </p>

    <!-- ── 1. Intent ──────────────────────────────────────────────────────── -->
    <div>
      <label :for="intentId" class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">
        What do you want to create?
      </label>
      <textarea
        :id="intentId"
        v-model="draft.intent"
        rows="3"
        :disabled="!canEdit"
        class="mt-2 w-full resize-y rounded-xl border border-base-content/15 bg-base-200/40 px-3 py-2 text-sm outline-none focus:border-primary"
        placeholder="Describe the content you want to create"
      ></textarea>
    </div>

    <!-- ── 2. Outputs ─────────────────────────────────────────────────────── -->
    <div>
      <div class="flex items-baseline justify-between">
        <span class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">
          Outputs
        </span>
        <span class="text-[0.65rem] opacity-40">pick one or more</span>
      </div>
      <div class="mt-2 flex flex-wrap gap-2">
        <button
          v-for="format in formats"
          :key="format.id"
          type="button"
          class="flex min-h-[44px] items-center gap-2 rounded-full border px-3 text-xs font-semibold transition-colors"
          :class="isSelected(format.id)
            ? 'border-primary bg-primary/10 text-primary'
            : 'border-base-content/15 text-base-content/70 hover:bg-base-content/5'"
          :disabled="!canEdit"
          :aria-pressed="isSelected(format.id) ? 'true' : 'false'"
          :title="format.description"
          @click="toggleFormat(format.id)"
        >
          <i :class="format.icon" aria-hidden="true"></i>
          <span>{{ format.label }}</span>
          <i
            v-if="isSelected(format.id)"
            class="fa-solid fa-check text-[0.6rem]"
            aria-hidden="true"
          ></i>
        </button>
      </div>
      <p v-if="selectedHint" class="mt-2 text-xs leading-relaxed opacity-60">
        {{ selectedHint }}
      </p>
    </div>

    <!-- ── 3. Details (collapsed: the intent alone already builds) ────────── -->
    <details class="rounded-xl border border-base-content/10 bg-base-200/30 px-3 py-2">
      <summary class="cursor-pointer select-none text-xs font-bold uppercase tracking-[0.18em] opacity-50">
        Details
      </summary>
      <div class="mt-3 flex flex-col gap-3">
        <label class="flex flex-col gap-1 text-xs">
          <span class="font-semibold opacity-70">Title</span>
          <input
            v-model="draft.title"
            :disabled="!canEdit"
            class="min-h-[44px] rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
            placeholder="Becomes the task and the output file names"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs">
          <span class="font-semibold opacity-70">Audience</span>
          <input
            v-model="draft.audience"
            :disabled="!canEdit"
            class="min-h-[44px] rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
            placeholder="Who watches this and what do they know"
          />
        </label>
        <div class="flex gap-3">
          <label class="flex flex-1 flex-col gap-1 text-xs">
            <span class="font-semibold opacity-70">Tone</span>
            <input
              v-model="draft.tone"
              :disabled="!canEdit"
              class="min-h-[44px] rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
              placeholder="dry, friendly"
            />
          </label>
          <label class="flex w-24 flex-col gap-1 text-xs">
            <span class="font-semibold opacity-70">Language</span>
            <input
              v-model="draft.language"
              :disabled="!canEdit"
              class="min-h-[44px] rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
              placeholder="en"
            />
          </label>
        </div>
        <label class="flex flex-col gap-1 text-xs">
          <span class="font-semibold opacity-70">Target length (seconds, 0 = decide for me)</span>
          <input
            v-model.number="draft.duration_seconds"
            type="number"
            min="0"
            step="30"
            :disabled="!canEdit"
            class="min-h-[44px] rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs">
          <span class="font-semibold opacity-70">Notes for the builder</span>
          <textarea
            v-model="draft.notes"
            rows="2"
            :disabled="!canEdit"
            class="rounded-lg border border-base-content/15 bg-base-100 px-3 py-2 text-sm"
            placeholder="Anything the builder must know or avoid"
          ></textarea>
        </label>
      </div>
    </details>

    <!-- ── 4. Sources ─────────────────────────────────────────────────────── -->
    <div>
      <div class="flex items-baseline justify-between">
        <span class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Sources</span>
        <span class="text-[0.65rem] opacity-40">
          links, images and videos, one folder per content
        </span>
      </div>

      <div class="mt-2 flex gap-2">
        <input
          v-model="linkValue"
          :disabled="!canEdit"
          class="min-h-[44px] min-w-0 flex-1 rounded-lg border border-base-content/15 bg-base-100 px-3 text-sm"
          placeholder="Paste a link"
          @keyup.enter="addLink"
        />
        <button
          type="button"
          class="min-h-[44px] shrink-0 rounded-lg border border-base-content/15 px-3 text-xs font-semibold disabled:opacity-40"
          :disabled="!canEdit || !linkValue.trim() || busy"
          @click="addLink"
        >
          Add
        </button>
      </div>

      <div class="mt-2 flex flex-wrap items-center gap-2">
        <label
          class="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border border-dashed border-base-content/25 px-3 text-xs font-semibold"
          :class="canEdit ? '' : 'pointer-events-none opacity-40'"
        >
          <i class="fa-solid fa-upload" aria-hidden="true"></i>
          <span>{{ uploading ? 'Uploading…' : 'Upload files' }}</span>
          <input
            ref="fileInput"
            type="file"
            multiple
            class="hidden"
            accept="image/*,video/*,audio/*,.pdf,.md,.txt,.csv,.json"
            :disabled="!canEdit || uploading"
            @change="onFiles"
          />
        </label>
        <span v-if="limitLabel" class="text-[0.65rem] opacity-40">{{ limitLabel }}</span>
      </div>

      <ul v-if="sources.length" class="mt-3 flex flex-col gap-2">
        <li
          v-for="source in sources"
          :key="source.source_id"
          class="flex items-center gap-2 rounded-lg border border-base-content/10 bg-base-200/30 px-3 py-2"
        >
          <i :class="sourceIcon(source)" class="w-4 shrink-0 text-xs opacity-60" aria-hidden="true"></i>
          <a
            v-if="source.kind === 'image'"
            :href="fileUrl(source)"
            target="_blank"
            rel="noopener"
            class="min-w-0 flex-1 truncate text-xs underline decoration-dotted"
          >{{ source.label || source.value }}</a>
          <a
            v-else-if="source.kind === 'link'"
            :href="source.value"
            target="_blank"
            rel="noopener"
            class="min-w-0 flex-1 truncate text-xs underline decoration-dotted"
          >{{ source.label || source.value }}</a>
          <span v-else class="min-w-0 flex-1 truncate text-xs">{{ source.label || source.value }}</span>
          <button
            v-if="canEdit"
            type="button"
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-base-content/50 hover:bg-base-content/10"
            :aria-label="`Remove ${source.label || source.value}`"
            @click="removeSource(source.source_id)"
          >
            <i class="fa-solid fa-xmark text-xs" aria-hidden="true"></i>
          </button>
        </li>
      </ul>
    </div>

    <!-- ── Missing pieces ─────────────────────────────────────────────────── -->
    <ul v-if="missing.length" class="flex flex-col gap-1 text-xs opacity-70">
      <li v-for="item in missing" :key="item" class="flex gap-2">
        <i class="fa-solid fa-circle-info mt-0.5 text-[0.6rem]" aria-hidden="true"></i>
        <span>{{ item }}</span>
      </li>
    </ul>

    <!-- ── Actions ────────────────────────────────────────────────────────── -->
    <div class="flex flex-wrap gap-2">
      <button
        type="button"
        class="min-h-[44px] rounded-lg border border-base-content/15 px-3 text-xs font-semibold disabled:opacity-40"
        :disabled="!canEdit || busy"
        @click="save"
      >
        {{ draft.content_id ? 'Save' : 'Save draft' }}
      </button>
      <button
        type="button"
        class="min-h-[44px] rounded-lg border border-base-content/15 px-3 text-xs font-semibold disabled:opacity-40"
        :disabled="!canEdit || busy || !draft.intent.trim()"
        @click="askCreator"
      >
        <i class="fa-solid fa-wand-magic-sparkles mr-1" aria-hidden="true"></i>
        Ask the content creator
      </button>
      <button
        type="button"
        class="min-h-[44px] rounded-lg bg-primary px-4 text-xs font-bold text-primary-content disabled:opacity-40"
        :disabled="!canBuild"
        @click="build"
      >
        <i class="fa-solid fa-hammer mr-1" aria-hidden="true"></i>
        {{ building ? 'Building…' : 'Build' }}
      </button>
    </div>

    <!-- ── Build result ───────────────────────────────────────────────────── -->
    <div
      v-if="buildResult"
      class="rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-xs leading-relaxed"
    >
      <p class="font-bold">Build queued</p>
      <p class="mt-1 opacity-80">{{ buildResult.summary }}</p>
      <p v-if="buildResult.folder" class="mt-1 break-all opacity-60">
        {{ buildResult.folder }}
      </p>
      <p v-if="buildResult.jobs" class="mt-1 opacity-60">{{ buildResult.jobs }}</p>
      <button
        v-if="buildResult.taskOpened"
        type="button"
        class="mt-2 min-h-[36px] rounded-lg border border-base-content/20 px-3 text-[0.7rem] font-semibold"
        @click="openBuilderChat"
      >
        Open the builder task
      </button>
    </div>

    <!-- ── Saved content ──────────────────────────────────────────────────── -->
    <div v-if="items.length">
      <span class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Content</span>
      <ul class="mt-2 flex flex-col gap-2">
        <li v-for="entry in items" :key="entry.content_id">
          <button
            type="button"
            class="flex w-full items-center gap-3 rounded-xl border border-base-content/10 bg-base-200/30 px-3 py-2 text-left hover:bg-base-200/60"
            @click="openItem(entry.content_id)"
          >
            <div class="min-w-0 flex-1">
              <p class="truncate text-xs font-semibold">
                {{ entry.title || entry.content_id }}
              </p>
              <p class="truncate text-[0.65rem] opacity-50">
                {{ formatLabels(entry.formats) }} · {{ entry.sources }} sources · {{ entry.state }}
              </p>
            </div>
            <i class="fa-solid fa-chevron-right text-[0.6rem] opacity-40" aria-hidden="true"></i>
          </button>
        </li>
      </ul>
    </div>

    <p class="text-[0.65rem] leading-relaxed opacity-40">
      The content creator profile turns this into a brief; the
      <strong>codx-junior</strong> profile builds it and the media worker renders it.
    </p>
  </section>
</template>

<script>
// Admin-only creator panel of the Project's Channel.
//
// It is a form, not an editor: the source of truth is `brief.json` on the
// server (`.codx/media/content/<slug>/`), and every action re-reads the item so
// the panel never drifts from what was stored. Nothing here reads a value that
// may be null while rendering: an exception during render aborts the whole app
// mount (see ChannelView).
export default {
  name: 'ContentCreatorPanel',
  props: {},
  data() {
    return {
      intentId: `creator-intent-${Math.random().toString(36).slice(2, 8)}`,
      formats: [],
      limits: { max_upload_bytes: 0 },
      draft: this.emptyDraft(),
      sources: [],
      missing: [],
      items: [],
      linkValue: '',
      uploading: false,
      busy: false,
      building: false,
      error: '',
      notice: '',
      buildResult: null,
      builderChat: null
    }
  },
  computed: {
    content() {
      // Service that talks to /api/content (registered in service/index.js).
      return (this.$service && this.$service.content) || null
    },
    activeProjectId() {
      const project = this.$storex.projects.activeProject
      return (project && (project.project_id || project.id)) || ''
    },
    isAdmin() {
      try {
        return Boolean(this.$storex.users.isAdmin)
      } catch (ex) {
        return false
      }
    },
    canEdit() {
      return this.isAdmin
    },
    canBuild() {
      return this.isAdmin && !this.busy && !this.building &&
        Boolean(this.draft.formats.length) && Boolean(this.draft.intent.trim())
    },
    selectedHint() {
      const chosen = this.formats.filter(fmt => this.draft.formats.includes(fmt.id))
      if (!chosen.length) {
        return ''
      }
      return chosen.map(fmt => `${fmt.label}: ${fmt.description}`).join(' ')
    },
    limitLabel() {
      const bytes = Number(this.limits.max_upload_bytes || 0)
      if (!bytes) {
        return ''
      }
      return `max ${Math.round(bytes / (1024 * 1024))} MB per file`
    }
  },
  watch: {
    // Switching project must not keep another project's draft on screen.
    activeProjectId() {
      this.reset()
    }
  },
  mounted() {
    this.load()
  },
  methods: {
    emptyDraft() {
      return {
        content_id: '',
        title: '',
        intent: '',
        formats: [],
        audience: '',
        tone: '',
        language: '',
        duration_seconds: 0,
        notes: ''
      }
    },
    reset() {
      this.draft = this.emptyDraft()
      this.sources = []
      this.missing = []
      this.buildResult = null
      this.builderChat = null
    },
    fail(error) {
      const detail = (error && error.response && error.response.data) || null
      const raw =
        (detail && (detail.detail?.message || detail.detail || detail.message)) ||
        (detail && typeof detail === 'string' ? detail : '') ||
        (error && error.message) ||
        'Unexpected error'
      const message = typeof raw === 'string' ? raw : JSON.stringify(raw)
      // A bare "cannot destructure" message means the request never got a usable
      // response (transport error or an unmounted router): tell the admin what
      // to do instead of showing a javascript error.
      this.error = /destructure/i.test(message)
        ? 'The content API is not reachable. Restart the server so /api/content is mounted.'
        : message
      this.notice = ''
    },
    okay(message) {
      this.notice = message
      this.error = ''
    },
    async load() {
      if (!this.content) {
        this.error = 'The content service is not available.'
        return
      }
      try {
        const [formats, items] = await Promise.all([
          this.content.formats(),
          this.content.items()
        ])
        this.formats = (formats && formats.formats) || []
        this.limits = (formats && formats.limits) || this.limits
        this.items = (items && items.items) || []
      } catch (error) {
        this.fail(error)
      }
    },
    isSelected(formatId) {
      return this.draft.formats.includes(formatId)
    },
    toggleFormat(formatId) {
      if (!this.canEdit) {
        return
      }
      const formats = this.draft.formats.filter(id => id !== formatId)
      if (!this.draft.formats.includes(formatId)) {
        formats.push(formatId)
      }
      this.draft.formats = formats
    },
    formatLabels(formats) {
      const labels = (formats || []).map(id => {
        const found = this.formats.find(fmt => fmt.id === id)
        return found ? found.label : id
      })
      return labels.length ? labels.join(', ') : 'no outputs yet'
    },
    sourceIcon(source) {
      const kind = (source && source.kind) || 'document'
      if (kind === 'link') return 'fa-solid fa-link'
      if (kind === 'image') return 'fa-solid fa-image'
      if (kind === 'video') return 'fa-solid fa-video'
      if (kind === 'audio') return 'fa-solid fa-waveform'
      if (kind === 'note') return 'fa-solid fa-note-sticky'
      return 'fa-solid fa-file'
    },
    fileUrl(source) {
      if (!this.content || !this.draft.content_id) {
        return '#'
      }
      return this.content.fileUrl(this.draft.content_id, source.value)
    },
    applyItem(payload) {
      const brief = (payload && payload.brief) || {}
      this.draft = {
        content_id: brief.content_id || '',
        title: brief.title || '',
        intent: brief.intent || '',
        formats: [...(brief.formats || [])],
        audience: brief.audience || '',
        tone: brief.tone || '',
        language: brief.language || '',
        duration_seconds: Number(brief.duration_seconds || 0),
        notes: brief.notes || ''
      }
      this.sources = brief.sources || []
      this.missing = (payload && payload.missing) || []
    },
    payload() {
      return {
        title: this.draft.title,
        intent: this.draft.intent,
        formats: this.draft.formats,
        audience: this.draft.audience,
        tone: this.draft.tone,
        language: this.draft.language,
        duration_seconds: Number(this.draft.duration_seconds || 0),
        notes: this.draft.notes
      }
    },
    // Creates the item the first time, updates it afterwards. Returns the id.
    async ensureSaved() {
      if (!this.content) {
        this.error = 'The content service is not available.'
        return ''
      }
      try {
        const saved = this.draft.content_id
          ? await this.content.update(this.draft.content_id, this.payload())
          : await this.content.create(this.payload())
        this.applyItem(saved)
        return this.draft.content_id
      } catch (error) {
        this.fail(error)
        return ''
      }
    },
    async save() {
      this.busy = true
      const contentId = await this.ensureSaved()
      this.busy = false
      if (contentId) {
        this.okay(`Saved to .codx/media/content/${contentId}/`)
        await this.load()
      }
    },
    async openItem(contentId) {
      if (!this.content || !contentId) {
        return
      }
      try {
        this.applyItem(await this.content.item(contentId))
        this.buildResult = null
        this.builderChat = null
        this.error = ''
        this.notice = ''
      } catch (error) {
        this.fail(error)
      }
    },
    async addLink() {
      const value = this.linkValue.trim()
      if (!value) {
        return
      }
      this.busy = true
      const contentId = await this.ensureSaved()
      if (contentId) {
        try {
          this.applyItem(
            await this.content.addSource(contentId, { kind: 'link', value, label: value })
          )
          this.linkValue = ''
          this.okay('Link attached.')
        } catch (error) {
          this.fail(error)
        }
      }
      this.busy = false
    },
    async onFiles(event) {
      const input = event && event.target
      const files = input && input.files ? [...input.files] : []
      if (!files.length || !this.content) {
        return
      }
      this.uploading = true
      const contentId = await this.ensureSaved()
      if (contentId) {
        try {
          let payload = null
          for (const file of files) {
            payload = await this.content.upload(contentId, file)
          }
          if (payload) {
            this.applyItem(payload)
            this.okay(`${files.length} file(s) stored in .codx/media/content/${contentId}/sources/`)
          }
        } catch (error) {
          this.fail(error)
        }
      }
      this.uploading = false
      if (input) {
        input.value = ''
      }
    },
    async removeSource(sourceId) {
      if (!this.content || !this.draft.content_id) {
        return
      }
      try {
        this.applyItem(await this.content.removeSource(this.draft.content_id, sourceId))
      } catch (error) {
        this.fail(error)
      }
    },
    briefMessage() {
      const lines = [
        'Help me finish the brief for a new piece of content of this project.',
        '',
        `Intent: ${this.draft.intent.trim()}`
      ]
      if (this.draft.title.trim()) lines.push(`Title: ${this.draft.title.trim()}`)
      lines.push(`Outputs: ${this.draft.formats.join(', ') || '(not selected yet)'}`)
      if (this.draft.audience.trim()) lines.push(`Audience: ${this.draft.audience.trim()}`)
      if (this.draft.tone.trim()) lines.push(`Tone: ${this.draft.tone.trim()}`)
      if (this.draft.language.trim()) lines.push(`Language: ${this.draft.language.trim()}`)
      if (Number(this.draft.duration_seconds || 0)) {
        lines.push(`Target length: ${this.draft.duration_seconds} seconds`)
      }
      if (this.sources.length) {
        lines.push('', 'Sources:')
        this.sources.forEach(source => lines.push(`- [${source.kind}] ${source.value}`))
      }
      if (this.draft.content_id) {
        lines.push('', `Brief folder: .codx/media/content/${this.draft.content_id}/`)
      }
      lines.push('', 'Reply with the finished brief, ready to build.')
      return lines.join('\n')
    },
    async askCreator() {
      if (!this.canEdit) {
        return
      }
      this.busy = true
      const contentId = await this.ensureSaved()
      this.busy = false
      if (!contentId) {
        return
      }
      try {
        const chat = await this.$service.chat.createChat({
          name: `Content: ${this.draft.title || this.draft.intent.slice(0, 40)}`,
          mode: 'chat',
          message: this.briefMessage(),
          profiles: ['content_creator'],
          files: [`${this.folderPath()}/brief.md`]
        })
        if (chat) {
          this.$storex.ui.openChat(chat)
        }
      } catch (error) {
        this.fail(error)
      }
    },
    folderPath() {
      return this.draft.content_id ? `.codx/media/content/${this.draft.content_id}` : ''
    },
    async build() {
      if (!this.canBuild) {
        return
      }
      this.building = true
      this.error = ''
      const contentId = await this.ensureSaved()
      if (!contentId) {
        this.building = false
        return
      }
      try {
        const result = await this.content.build(contentId, {})
        const plan = (result && result.plan) || {}
        const task = (result && result.task) || {}
        const jobIds = (result && result.job_ids) || []
        this.applyItem(result)
        this.buildResult = {
          summary: `${plan.steps ? plan.steps.length : 0} step(s) for ${this.formatLabels(this.draft.formats)}.`,
          folder: `.codx/media/content/${contentId}/`,
          jobs: jobIds.length ? `Media jobs: ${jobIds.join(', ')}` : 'No media job needed for these formats.',
          taskOpened: false
        }
        await this.load()
        if (task.name) {
          const chat = await this.$service.chat.createChat({
            name: task.name,
            mode: 'task',
            message: task.prompt,
            files: task.files || [],
            profiles: task.profiles || ['codx-junior'],
            metadata: task.metadata || {}
          })
          if (chat) {
            this.builderChat = chat
            this.buildResult.taskOpened = true
            this.$storex.ui.openChat(chat)
            this.okay('Build queued. The codx-junior profile is starting now.')
            this.$storex.projects.chatWihProject(chat).catch(error => this.fail(error))
          }
        }
      } catch (error) {
        this.fail(error)
      }
      this.building = false
    },
    openBuilderChat() {
      if (this.builderChat) {
        this.$storex.ui.openChat(this.builderChat)
      }
    }
  }
}
</script>
