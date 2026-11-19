<script>
/**
 * BrowserRecorder — record a coworking flow once, replay it forever.
 *
 * The live view already sends the human's clicks/typing to the page recorder.
 * This dialog starts/stops a named recording, previews the generated steps,
 * and saves them into the project's recipe library
 * (`.codx/browser_scripts`). Re-recording over an existing recipe bumps its
 * version instead of creating a duplicate.
 *
 * It is intentionally a plain Options API component: the surrounding browser
 * panel is Options API and the component is also mounted in meeting panels.
 */
export default {
  name: 'BrowserRecorder',
  props: {
    session: { type: Object, default: null },
    recording: { type: Object, default: null },
    preview: { type: Object, default: null },
    analyzing: { type: Boolean, default: false },
    initialTab: { type: String, default: 'record' }
  },
  emits: ['close', 'started', 'stopped', 'saved', 'cancelled', 'changed'],
  data() {
    return {
      tab: this.initialTab === 'scripts' ? 'scripts' : 'record',
      mode: 'setup',
      loading: false,
      saving: false,
      runningId: null,
      scripts: [],
      scriptsLoading: false,
      title: '',
      description: '',
      site: '',
      tagsText: '',
      selectedScriptId: '',
      stepsText: '',
      savedStepsText: '',
      warnings: [],
      meta: null,
      error: null,
      runResult: null,
      tick: 0,
      timer: null
    }
  },
  computed: {
    sessionId() {
      return this.session?.id || null
    },
    currentUrl() {
      return this.session?.current_url || ''
    },
    isActive() {
      return !!this.recording?.active
    },
    hasPreview() {
      return !!this.preview
    },
    showEditor() {
      return this.mode === 'preview' || this.mode === 'editScript'
    },
    elapsedLabel() {
      const started = this.recording?.started_at
      if (!started) return ''
      // `tick` makes the label reactive once per second.
      void this.tick
      const seconds = Math.max(0, Math.floor((Date.now() - new Date(started).getTime()) / 1000))
      const minutes = Math.floor(seconds / 60)
      return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    },
    eventCount() {
      return Number(this.recording?.event_count || 0)
    },
    selectedScript() {
      return this.scripts.find((script) => script.id === this.selectedScriptId) || null
    },
    formTitle() {
      if (this.isActive) return 'Recording in progress'
      if (this.hasPreview) return 'Review and save'
      if (this.selectedScriptId) return 'Update recipe'
      return 'Record a flow'
    },
    stepCount() {
      try {
        const steps = JSON.parse(this.stepsText || '[]')
        return Array.isArray(steps) ? steps.length : 0
      } catch (error) {
        return 0
      }
    },
    stepsDirty() {
      if (!this.savedStepsText) return true
      try {
        return JSON.stringify(JSON.parse(this.stepsText || '[]')) !== this.savedStepsText
      } catch (error) {
        return true
      }
    },
    canSave() {
      return !!this.title.trim() && this.stepCount > 0 && !this.saving && this.stepsDirty
    },
    variablesLabel() {
      return (this.meta?.variables || []).map((name) => `{{${name}}}`).join(', ')
    }
  },
  watch: {
    preview: {
      immediate: true,
      handler(value) {
        if (value) this.applyPreview(value)
      }
    },
    recording: {
      immediate: true,
      handler(value) {
        if (!value) return
        if (value.active) {
          this.tab = 'record'
          this.mode = 'recording'
          if (!this.title) this.title = value.title || ''
          if (!this.site) this.site = value.site || ''
        }
        this.syncTimer()
      }
    },
    initialTab(value) {
      if (value) this.tab = value
    },
    sessionId(value, previous) {
      if (value === previous) return
      this.reset()
      this.loadScripts()
    }
  },
  mounted() {
    this.seedFromSession()
    this.loadScripts()
    if (this.preview) this.applyPreview(this.preview)
    this.syncTimer()
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer)
  },
  methods: {
    api() {
      return this.$storex.api.computers
    },
    notify(type, text) {
      try {
        this.$storex.ui.addNotification({ text, type })
      } catch (e) {
        // Notifications are best effort (headless tests).
      }
    },
    reset() {
      this.title = ''
      this.description = ''
      this.site = ''
      this.tagsText = ''
      this.selectedScriptId = ''
      this.stepsText = ''
      this.warnings = []
      this.meta = null
      this.error = null
      this.scripts = []
      this.mode = 'setup'
      this.seedFromSession()
    },
    seedFromSession() {
      if (!this.title) this.title = this.recording?.title || ''
      if (!this.site) {
        this.site = this.recording?.site || this.hostOf(this.currentUrl) || ''
      }
    },
    hostOf(url) {
      try {
        return new URL(url).hostname.replace(/^www\./, '')
      } catch (e) {
        return ''
      }
    },
    syncTimer() {
      if (this.timer) {
        clearInterval(this.timer)
        this.timer = null
      }
      if (!this.isActive) return
      this.timer = setInterval(() => { this.tick += 1 }, 1000)
    },
    applyPreview(preview) {
      const recording = preview.recording || {}
      this.tab = 'record'
      this.mode = 'preview'
      this.title = recording.title || preview.suggested_title || this.title
      this.description = recording.description || ''
      this.site = recording.site || this.site
      this.tagsText = (recording.tags || []).join(', ')
      this.selectedScriptId = recording.script_id || ''
      this.stepsText = JSON.stringify(preview.steps || [], null, 2)
      this.savedStepsText = preview.analysis?.saved
        ? JSON.stringify(preview.steps || [])
        : ''
      this.warnings = preview.warnings || []
      this.meta = {
        events: preview.event_count || 0,
        skipped: preview.skipped || 0,
        variables: preview.variables || [],
        truncated: !!preview.truncated
      }
      this.error = null
      this.runResult = null
    },
    applyScript(script) {
      if (!script) return
      this.tab = 'record'
      this.mode = 'editScript'
      this.selectedScriptId = script.id || ''
      this.title = script.title || ''
      this.description = script.description || ''
      this.site = script.site || this.site
      this.tagsText = (script.tags || []).join(', ')
      this.stepsText = JSON.stringify(script.steps || [], null, 2)
      this.savedStepsText = ''
      this.warnings = []
      this.meta = {
        events: script.step_count || (script.steps || []).length,
        skipped: 0,
        variables: [],
        truncated: false,
        existing: true
      }
      this.runResult = null
      this.error = null
    },
    parseTags() {
      return String(this.tagsText || '')
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean)
    },
    parseSteps() {
      let data
      try {
        data = JSON.parse(this.stepsText || '[]')
      } catch (error) {
        throw new Error(`Steps must be valid JSON: ${error.message}`)
      }
      if (!Array.isArray(data) || !data.length) {
        throw new Error('The recording has no steps to save')
      }
      data.forEach((step, index) => {
        if (!step || typeof step !== 'object' || !step.action) {
          throw new Error(`Step ${index + 1} must be an object with an action`)
        }
      })
      return data
    },
    async loadScripts() {
      if (!this.sessionId) return
      this.scriptsLoading = true
      try {
        const response = await this.api().scripts(this.sessionId, { site: this.site || null })
        this.scripts = response?.scripts || []
        if (!this.site && response?.site) this.site = response.site
      } catch (error) {
        console.warn('[BrowserRecorder] Could not load recipes', error)
      } finally {
        this.scriptsLoading = false
      }
    },
    /**
     * Choose an existing recipe as the update target without loading its steps:
     * the user can re-record the flow (the backend bumps the same version).
     */
    selectExisting() {
      const script = this.selectedScript
      if (!script) return
      this.mode = 'setup'
      this.title = script.title || this.title
      this.description = script.description || ''
      this.site = script.site || this.site
      this.tagsText = (script.tags || []).join(', ')
      this.stepsText = ''
      this.warnings = []
      this.meta = { existing: true, version: script.version }
    },
    async startRecording() {
      if (!this.sessionId) return
      this.error = null
      this.loading = true
      try {
        const payload = {
          title: this.title.trim() || null,
          description: this.description || '',
          site: this.site || null,
          tags: this.parseTags(),
          script_id: this.selectedScriptId || null
        }
        const response = await this.api().startRecording(this.sessionId, payload)
        this.mode = 'recording'
        this.$emit('started', response?.recording || null)
        this.$emit('changed')
        this.notify('success', 'Recording started — interact with the live browser, then press Stop')
      } catch (error) {
        this.error = error.message || 'Could not start the recording'
      } finally {
        this.loading = false
      }
    },
    async stopRecording() {
      if (!this.sessionId) return
      this.error = null
      this.loading = true
      try {
        const preview = await this.api().stopRecording(this.sessionId)
        this.applyPreview(preview)
        this.$emit('stopped', preview)
        this.$emit('changed')
      } catch (error) {
        this.error = error.message || 'Could not stop the recording'
      } finally {
        this.loading = false
      }
    },
    async save() {
      if (!this.sessionId) return
      this.error = null
      let steps
      try {
        steps = this.parseSteps()
      } catch (error) {
        this.error = error.message
        return
      }
      this.saving = true
      try {
        const response = await this.api().saveRecording(this.sessionId, {
          title: this.title.trim(),
          steps,
          site: this.site || null,
          description: this.description || '',
          tags: this.parseTags(),
          script_id: this.selectedScriptId || null
        })
        const script = response?.script
        if (script?.id) {
          this.selectedScriptId = script.id
          this.stepsText = JSON.stringify(script.steps || steps, null, 2)
        }
        this.$emit('saved', script)
        this.$emit('changed')
        this.notify(
          'success',
          script
            ? `Recipe "${script.title}" saved (v${script.version}) — the AI can now repeat it.`
            : 'Recipe saved'
        )
        await this.loadScripts()
      } catch (error) {
        this.error = error.message || 'Could not save the recipe'
      } finally {
        this.saving = false
      }
    },
    async discard() {
      if (!this.sessionId) return
      try {
        await this.api().cancelRecording(this.sessionId)
      } catch (error) {
        console.warn('[BrowserRecorder] Could not discard recording', error)
      }
      this.stepsText = ''
      this.meta = null
      this.warnings = []
      this.mode = 'setup'
      this.selectedScriptId = ''
      this.$emit('cancelled')
      this.$emit('changed')
      this.$emit('close')
    },
    async cancelActive() {
      if (!this.sessionId) return
      this.loading = true
      try {
        await this.api().cancelRecording(this.sessionId)
        this.$emit('cancelled')
        this.$emit('changed')
        this.notify('info', 'Recording discarded')
      } catch (error) {
        this.error = error.message || 'Could not discard the recording'
      } finally {
        this.loading = false
      }
    },
    /**
     * One-click "record over this recipe" from its card.
     *
     * No setup dialog: the recipe already carries the title/site/description.
     * The parent closes this modal, shows the floating recording control over
     * the browser, and the stop action sends the trace to codx-junior.
     */
    async recordScript(script) {
      if (!this.sessionId || !script) return
      this.error = null
      this.loading = true
      try {
        const response = await this.api().startRecording(this.sessionId, {
          script_id: script.id,
          title: script.title,
          description: script.description || '',
          site: script.site || this.site || null,
          tags: script.tags || []
        })
        this.$emit('started', response?.recording || null)
        this.$emit('changed')
        this.notify('info', `Recording over "${script.title}" — do the flow in the browser, then press Stop`)
      } catch (error) {
        this.error = error.message || 'Could not start recording'
      } finally {
        this.loading = false
      }
    },
    async run(script) {
      if (!this.sessionId || !script) return
      this.error = null
      this.runningId = script.id
      try {
        const response = await this.api().runScript(this.sessionId, script.id, {})
        this.runResult = response
        if (response?.ok) {
          this.notify('success', `Ran "${script.title}" in ${Math.round(response.duration_ms || 0)} ms`)
        } else {
          this.notify('error', `Recipe stopped at step ${response?.result?.failed_step}: ${response?.result?.error || 'unknown error'}`)
        }
        this.$emit('changed')
      } catch (error) {
        this.error = error.message || 'Could not run the recipe'
      } finally {
        this.runningId = null
      }
    },
    async remove(script) {
      if (!script || !this.sessionId) return
      const confirmed = typeof window === 'undefined' || !window.confirm
        || window.confirm(`Delete the recipe "${script.title}"?`)
      if (!confirmed) return
      try {
        await this.api().deleteScript(this.sessionId, script.id)
        this.notify('success', 'Recipe moved to .trash')
        if (this.selectedScriptId === script.id) {
          this.selectedScriptId = ''
          this.stepsText = ''
        }
        await this.loadScripts()
        this.$emit('changed')
      } catch (error) {
        this.error = error.message || 'Could not delete the recipe'
      }
    },
    newRecording() {
      this.tab = 'record'
      this.mode = 'setup'
      this.selectedScriptId = ''
      this.title = ''
      this.description = ''
      this.tagsText = ''
      this.stepsText = ''
      this.warnings = []
      this.meta = null
      this.error = null
      this.seedFromSession()
    },
    editScript(script) {
      this.applyScript(script)
    },
    close() {
      this.$emit('close')
    }
  }
}
</script>

<template>
  <div class="flex flex-col gap-3 md:w-[56rem] max-w-full">
    <div
      v-if="analyzing"
      class="flex flex-col items-center justify-center gap-3 rounded-xl border border-primary/30 bg-primary/5 p-8 text-center"
      role="status"
      aria-live="polite"
    >
      <span class="loading loading-spinner loading-lg text-primary"></span>
      <p class="text-sm font-semibold">codx-junior is reviewing the recorded actions…</p>
      <p class="text-xs opacity-60 max-w-md">
        It is converting the Selenium/Playwright-like trace into recipe steps,
        removing recorder noise and checking selectors.
      </p>
    </div>

    <div v-if="!analyzing" role="tablist" class="tabs tabs-boxed tabs-sm flex-shrink-0 self-start">
      <button
        role="tab"
        class="tab gap-1"
        :class="{ 'tab-active': tab === 'record' }"
        @click="tab = 'record'"
      >
        <i class="fa-solid fa-circle-dot"></i>
        Record
      </button>
      <button
        role="tab"
        class="tab gap-1"
        :class="{ 'tab-active': tab === 'scripts' }"
        @click="tab = 'scripts'; loadScripts()"
      >
        <i class="fa-solid fa-book"></i>
        Recipes
        <span v-if="scripts.length" class="badge badge-xs">{{ scripts.length }}</span>
      </button>
    </div>

    <p
      v-if="error && !analyzing"
      role="alert"
      class="flex items-start gap-1.5 rounded-lg bg-error/10 px-2.5 py-2 text-[11px] text-error"
    >
      <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
      <span class="min-w-0">{{ error }}</span>
    </p>

    <!-- ── Record tab ─────────────────────────────────────────────────── -->
    <template v-if="tab === 'record' && !analyzing">
      <div
        v-if="isActive"
        class="rounded-xl border border-error/40 bg-error/5 p-3 flex flex-col gap-2"
      >
        <div class="flex items-center gap-2">
          <span class="relative flex h-2.5 w-2.5">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-error opacity-60"></span>
            <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-error"></span>
          </span>
          <p class="text-sm font-semibold flex-1">Recording “{{ recording?.title || title || 'Untitled flow' }}”</p>
          <span class="text-xs font-mono opacity-70">{{ elapsedLabel }}</span>
        </div>
        <p class="text-xs opacity-70">
          Use the live browser on the left. Click, type and submit the flow you want the AI to repeat.
          <span v-if="eventCount">{{ eventCount }} action(s) captured.</span>
        </p>
        <div class="flex flex-wrap gap-2 justify-end">
          <button class="btn btn-xs btn-ghost" :disabled="loading" @click="cancelActive">
            <i class="fa-solid fa-trash"></i> Discard
          </button>
          <button class="btn btn-sm btn-error gap-1" :disabled="loading" @click="stopRecording">
            <span v-if="loading" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-stop"></i>
            Stop &amp; review
          </button>
        </div>
      </div>

      <div v-else class="flex flex-col gap-3">
        <p class="text-xs opacity-60">
          Start a recording, do the flow once in the live browser, then stop.
          The generated recipes are stored per site and can be replayed from
          the <strong>Recipes</strong> tab or asked for by the AI.
        </p>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
          <label class="form-control">
            <span class="label-text text-xs font-semibold">Title *</span>
            <input
              v-model="title"
              type="text"
              class="input input-sm input-bordered w-full"
              placeholder="Create a post"
              @keyup.enter="hasPreview ? save() : startRecording()"
            />
          </label>
          <label class="form-control">
            <span class="label-text text-xs font-semibold">Site</span>
            <input
              v-model="site"
              type="text"
              class="input input-sm input-bordered w-full font-mono"
              placeholder="example.com"
              @blur="loadScripts"
            />
          </label>
        </div>

        <label class="form-control">
          <span class="label-text text-xs font-semibold">Description</span>
          <textarea
            v-model="description"
            rows="2"
            class="textarea textarea-bordered textarea-sm w-full"
            placeholder="What does this flow do? The AI reads this to choose it."
          ></textarea>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
          <label class="form-control">
            <span class="label-text text-xs font-semibold">Tags</span>
            <input
              v-model="tagsText"
              type="text"
              class="input input-sm input-bordered w-full"
              placeholder="post, cms, publish"
            />
          </label>
          <label class="form-control">
            <span class="label-text text-xs font-semibold">Update an existing recipe</span>
            <select v-model="selectedScriptId" class="select select-sm select-bordered w-full" @change="selectExisting">
              <option value="">Create a new recipe</option>
              <option v-for="script in scripts" :key="script.id" :value="script.id">
                {{ script.title }} · v{{ script.version }} · {{ script.site }}
              </option>
            </select>
          </label>
        </div>

        <p v-if="selectedScriptId && mode === 'setup'" class="text-[11px] rounded-lg bg-info/10 text-info px-2.5 py-2">
          <i class="fa-solid fa-circle-info mr-1"></i>
          Recording now will update <strong>{{ selectedScript?.title }}</strong>
          (currently v{{ selectedScript?.version }}) and keep the previous version archived.
        </p>

        <!-- Preview of the generated steps, editable before saving. -->
        <div v-if="showEditor" class="flex flex-col gap-2">
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <span class="badge badge-sm badge-primary">{{ stepCount }} step(s)</span>
            <span v-if="meta?.events" class="badge badge-sm badge-ghost">{{ meta.events }} action(s)</span>
            <span v-if="meta?.skipped" class="badge badge-sm badge-ghost">{{ meta.skipped }} ignored</span>
            <span v-if="variablesLabel" class="badge badge-sm badge-warning">
              fill {{ variablesLabel }} when running
            </span>
            <span v-if="selectedScriptId" class="badge badge-sm badge-info">updates recipe</span>
            <span class="text-base-content/50">Edit the JSON if a selector needs a small fix.</span>
          </div>

          <div
            v-if="preview?.analysis"
            class="rounded-lg px-2.5 py-2 text-[11px]"
            :class="preview.analysis.used_ai ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'"
          >
            <p class="flex items-start gap-1.5">
              <i class="mt-0.5" :class="preview.analysis.used_ai ? 'fa-solid fa-wand-magic-sparkles' : 'fa-solid fa-triangle-exclamation'"></i>
              <span class="min-w-0">
                <template v-if="preview.analysis.used_ai">
                  codx-junior reviewed {{ preview.event_count || 0 }} recorded action(s)
                  <span v-if="preview.analysis.model"> with {{ preview.analysis.model }}</span>.
                  <span v-if="preview.analysis.notes" class="block opacity-80">{{ preview.analysis.notes }}</span>
                </template>
                <template v-else>
                  AI review was unavailable; the deterministic recorder steps are shown.
                  <span v-if="preview.analysis.error" class="block opacity-80">{{ preview.analysis.error }}</span>
                </template>
                <span v-if="preview.analysis.saved" class="block font-semibold mt-0.5">
                  Recipe updated to v{{ preview.analysis.version }} — the AI can replay it now.
                </span>
              </span>
            </p>
          </div>

          <ul v-if="warnings.length" class="text-[11px] list-disc pl-5 opacity-70">
            <li v-for="(warning, index) in warnings" :key="index">{{ warning }}</li>
          </ul>

          <textarea
            v-model="stepsText"
            rows="12"
            class="textarea textarea-bordered w-full font-mono text-[11px] leading-relaxed"
            spellcheck="false"
          ></textarea>
        </div>

        <div class="flex flex-wrap justify-end gap-2 pt-1">
          <button class="btn btn-sm btn-ghost" @click="close">{{ preview?.analysis?.saved ? 'Close' : 'Cancel' }}</button>
          <button
            v-if="mode === 'preview'"
            class="btn btn-sm btn-ghost text-error"
            :disabled="saving"
            @click="discard"
          >
            <i class="fa-solid fa-trash"></i> Discard
          </button>
          <button
            v-if="mode === 'editScript'"
            class="btn btn-sm btn-ghost"
            @click="newRecording"
          >
            <i class="fa-solid fa-rotate-left"></i> Stop editing
          </button>
          <button
            v-if="mode === 'setup'"
            class="btn btn-sm btn-primary gap-1"
            :disabled="loading"
            @click="startRecording"
          >
            <span v-if="loading" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-circle-dot"></i>
            Start recording
          </button>
          <button
            v-if="showEditor"
            class="btn btn-sm btn-success gap-1"
            :disabled="!canSave"
            @click="save"
          >
            <span v-if="saving" class="loading loading-spinner loading-xs"></span>
            <i v-else :class="preview?.analysis?.saved && !stepsDirty ? 'fa-solid fa-check' : 'fa-solid fa-floppy-disk'"></i>
            <template v-if="preview?.analysis?.saved">
              {{ stepsDirty ? 'Save changes' : 'Updated' }}
            </template>
            <template v-else>
              {{ selectedScriptId ? 'Update recipe' : 'Save recipe' }}
            </template>
          </button>
        </div>
      </div>
    </template>

    <!-- ── Recipes tab ────────────────────────────────────────────────── -->
    <template v-else-if="tab === 'scripts' && !analyzing">
      <div class="flex items-center gap-2">
        <p class="text-xs opacity-60 flex-1">
          Recipes recorded for <code class="text-[11px]">{{ site || 'this site' }}</code>.
          The AI finds them automatically with <code class="text-[11px]">list_browser_scripts</code>.
        </p>
        <button class="btn btn-xs btn-ghost" :disabled="scriptsLoading" @click="loadScripts">
          <span v-if="scriptsLoading" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-rotate"></i>
          Refresh
        </button>
        <button class="btn btn-xs btn-primary gap-1" @click="newRecording">
          <i class="fa-solid fa-plus"></i> New recording
        </button>
      </div>

      <div v-if="scriptsLoading" class="p-6 flex items-center justify-center gap-2 text-sm opacity-70">
        <span class="loading loading-spinner loading-sm"></span> Loading recipes...
      </div>

      <div v-else-if="!scripts.length" class="rounded-xl border border-base-content/10 p-6 text-center">
        <i class="fa-solid fa-book-open text-3xl opacity-20 mb-2 block"></i>
        <p class="text-sm font-semibold">No recipes yet</p>
        <p class="text-xs opacity-60 mt-1">
          Record a flow once; it will appear here and in the AI's browser tools.
        </p>
      </div>

      <div v-else class="flex flex-col gap-2 max-h-[55vh] overflow-y-auto pr-1">
        <div
          v-for="script in scripts"
          :key="script.id"
          class="rounded-xl border border-base-content/10 p-2.5 flex flex-col gap-1.5"
        >
          <div class="flex items-center gap-2 min-w-0">
            <i class="fa-solid fa-scroll text-primary/70 flex-shrink-0"></i>
            <p class="text-sm font-semibold truncate flex-1">{{ script.title }}</p>
            <span class="badge badge-xs badge-ghost">{{ script.site }}</span>
            <span class="badge badge-xs">v{{ script.version }}</span>
            <span class="badge badge-xs badge-outline">{{ script.step_count || 0 }} steps</span>
          </div>
          <p v-if="script.description" class="text-[11px] opacity-60 truncate">{{ script.description }}</p>
          <div class="flex items-center gap-1.5">
            <span class="text-[10px] opacity-40 mr-auto">
              <i class="fa-solid fa-play mr-1"></i>{{ script.uses || 0 }} uses
              <span v-if="script.tags?.length"> · {{ script.tags.join(', ') }}</span>
            </span>
            <button
              class="btn btn-xs btn-error gap-1"
              :disabled="loading"
              title="Record this flow again and let codx-junior update it"
              @click="recordScript(script)"
            >
              <span v-if="loading" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-circle-dot"></i>
              Record
            </button>
            <button
              class="btn btn-xs btn-success gap-1"
              :disabled="runningId === script.id"
              @click="run(script)"
            >
              <span v-if="runningId === script.id" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-play"></i>
              Run
            </button>
            <button class="btn btn-xs btn-ghost gap-1" @click="editScript(script)">
              <i class="fa-solid fa-pen"></i>
              Edit
            </button>
            <button class="btn btn-xs btn-ghost text-error" @click="remove(script)">
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
        </div>
      </div>

      <div
        v-if="runResult"
        class="rounded-lg p-2 text-[11px]"
        :class="runResult.ok ? 'bg-success/10 text-success' : 'bg-error/10 text-error'"
      >
        {{ runResult.ok ? 'Recipe completed' : 'Recipe failed' }}
        in {{ Math.round(runResult.duration_ms || runResult.result?.ms || 0) }} ms
        <span v-if="runResult.result?.error"> — {{ runResult.result.error }}</span>
        <span v-if="runResult.result?.failed_step !== undefined && runResult.result?.failed_step !== null">
          (step {{ runResult.result.failed_step }})
        </span>
      </div>
    </template>
  </div>
</template>
