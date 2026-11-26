<script>
import { qualityModule } from '@/api/modules/quality'

/**
 * Project quality guardians.
 *
 * The panel is the human side of the guardian subsystem:
 * - it lists the guardian profiles (file scope, kind, checks, enabled state);
 * - it runs a review on changed files, the whole project, one file type or an
 *   explicit path set;
 * - it shows the structured findings of the latest run, the deterministic
 *   checks and the AI reviews;
 * - it lets the user fix, dismiss, suppress or learn from a finding, which is
 *   how a guardian is polished over time;
 * - it exposes the cost/trigger policy so an expensive lane is explicit.
 *
 * Everything is project-scoped through `api` (the project API instance). The
 * backend stores the runs under `.codx/quality`, so the panel can be reopened
 * after a restart and still show the last verdict.
 */
export default {
  name: 'QualityPanel',
  props: {
    project: { type: Object, default: null },
    api: { type: Object, default: null },
    // Expensive data loads only when the tab is visible.
    active: { type: Boolean, default: false }
  },
  data() {
    return {
      loading: false,
      error: null,
      notice: null,
      state: null,
      run: null,
      // Review form
      scope: 'changed',
      trigger: 'manual',
      mode: 'auto',
      fileType: '',
      pathsText: '',
      maxFiles: null,
      running: false,
      // Fix / feedback
      fixing: false,
      fixResult: null,
      feedbackOpen: false,
      feedbackSaving: false,
      feedback: {
        finding: null,
        action: 'dismiss',
        reason: '',
        title: '',
        content: '',
        learn: false,
        suppress: false,
        scope: 'finding'
      },
      showChecks: false,
      showRuns: false,
      severityOrder: ['critical', 'high', 'medium', 'low', 'info']
    }
  },
  computed: {
    qualityApi() {
      return this.api ? qualityModule(this.api) : null
    },
    policy() {
      return this.state?.policy || {}
    },
    guardians() {
      return this.state?.guardians || []
    },
    candidates() {
      return this.state?.candidates || []
    },
    runs() {
      return this.state?.runs || []
    },
    findings() {
      return (this.run?.findings || []).slice().sort((a, b) => {
        const openA = (a.status || 'open') === 'open' ? 0 : 1
        const openB = (b.status || 'open') === 'open' ? 0 : 1
        if (openA !== openB) return openA - openB
        return this.severityOrder.indexOf(a.severity) - this.severityOrder.indexOf(b.severity)
      })
    },
    openFindings() {
      return this.findings.filter(finding => (finding.status || 'open') === 'open')
    },
    verdict() {
      return this.run?.verdict || {}
    },
    verdictClass() {
      return {
        allow: 'badge-success',
        warn: 'badge-warning',
        require_approval: 'badge-info',
        block: 'badge-error'
      }[this.verdict.action] || 'badge-ghost'
    },
    canRun() {
      return !!this.qualityApi && !this.running && !this.loading
    },
    triggerStatus() {
      return this.state?.trigger || {}
    },
    detectedStack() {
      const signals = this.state?.bootstrap?.signals || {}
      return {
        languages: signals.languages || [],
        frameworks: signals.frameworks || [],
        tests: signals.test_runners || [],
        docs: signals.has_docs,
        docker: signals.has_docker,
        ci: signals.has_ci
      }
    }
  },
  watch: {
    active(active) {
      if (active) this.loadState({ autoBootstrap: !this.state })
    }
  },
  mounted() {
    if (this.active) this.loadState({ autoBootstrap: true })
    this.bindQualityEvents()
  },
  beforeUnmount() {
    this.unbindQualityEvents()
    clearTimeout(this._noticeTimer)
  },
  methods: {
    // ── Live events ────────────────────────────────────────────────────
    /**
     * The background worker emits `quality-event` after persisting a run.
     * Refresh the panel in place so a file-change review appears without a
     * manual reload. The event only carries a summary; the panel fetches the
     * stored run, which is the source of truth.
     */
    bindQualityEvents() {
      const socket = this.api?.socket
      if (!socket?.on || this._qualityEventHandler) return
      this._qualityEventHandler = (payload) => {
        if (!this.active) return
        const projectPath = this.project?.codx_path || ''
        if (projectPath && payload?.codx_path && payload.codx_path !== projectPath) return
        this.refresh()
      }
      socket.on('quality-event', this._qualityEventHandler)
    },

    unbindQualityEvents() {
      if (!this._qualityEventHandler) return
      this.api?.socket?.off?.('quality-event')
      this._qualityEventHandler = null
    },

    // ── Data loading ────────────────────────────────────────────────────
    async loadState({ autoBootstrap = false } = {}) {
      if (!this.qualityApi) return
      this.loading = true
      this.error = null
      try {
        const state = await this.qualityApi.state({ autoBootstrap })
        this.state = state
        const currentId = this.run?.id || state.latest_run_id || ''
        if (currentId && state.latest?.id === currentId) {
          this.run = state.latest
        } else if (currentId) {
          this.run = await this.qualityApi.run(currentId)
        } else {
          this.run = null
        }
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not load the quality guardians')
      } finally {
        this.loading = false
      }
    },

    refresh() {
      return this.loadState({ autoBootstrap: false })
    },

    errorText(ex, fallback) {
      return ex?.response?.data?.detail || ex?.message || fallback
    },

    notify(message) {
      this.notice = message
      clearTimeout(this._noticeTimer)
      this._noticeTimer = setTimeout(() => { this.notice = null }, 5000)
    },

    // ── Guardians ───────────────────────────────────────────────────────
    async bootstrap() {
      this.running = true
      this.error = null
      try {
        const report = await this.qualityApi.bootstrap({ adopt: true })
        this.notify(
          `Guardians refreshed: ${report.created?.length || 0} created, `
          + `${report.updated?.length || 0} updated, ${report.adopted?.length || 0} adopted.`
        )
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not create the guardians')
      } finally {
        this.running = false
      }
    },

    async toggleGuardian(guardian) {
      try {
        await this.qualityApi.enable(guardian.name, !guardian.enabled)
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, `Could not change ${guardian.name}`)
      }
    },

    async adopt(candidate) {
      try {
        await this.qualityApi.adopt(candidate.name)
        this.notify(`Profile ${candidate.name} is now a guardian.`)
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, `Could not adopt ${candidate.name}`)
      }
    },

    async runGuardian(guardian) {
      this.scope = 'changed'
      await this.runReview({ guardians: [guardian.name] })
    },

    // ── Review ──────────────────────────────────────────────────────────
    buildPaths() {
      return (this.pathsText || '')
        .split(/[\n,]/)
        .map(path => path.trim())
        .filter(Boolean)
    },

    async runReview({ guardians = null } = {}) {
      if (!this.qualityApi) return
      this.running = true
      this.error = null
      try {
        const result = await this.qualityApi.review({
          trigger: this.trigger,
          scope: this.scope,
          mode: this.mode,
          paths: this.scope === 'files' ? this.buildPaths() : null,
          file_type: this.scope === 'file_type' ? (this.fileType || null) : null,
          guardians,
          max_files: this.maxFiles ? Number(this.maxFiles) : null
        })
        this.run = result
        this.state = {
          ...(this.state || {}),
          latest: result,
          latest_run_id: result.id,
          open_findings: (result.findings || []).filter(f => (f.status || 'open') === 'open').length
        }
        this.notify(`Review finished: ${result.verdict?.action || 'allow'}.`)
        // Refresh the panels that depend on the stored summary.
        this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, 'The review failed')
      } finally {
        this.running = false
      }
    },

    async openRun(runId) {
      if (!this.qualityApi || !runId) return
      this.loading = true
      try {
        this.run = await this.qualityApi.run(runId)
        this.showRuns = false
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not load the run')
      } finally {
        this.loading = false
      }
    },

    // ── Policy ──────────────────────────────────────────────────────────
    async togglePolicy(key) {
      if (!this.qualityApi || !this.policy) return
      try {
        await this.qualityApi.updatePolicy({ [key]: !this.policy[key] })
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not update the policy')
      }
    },

    // ── Findings: feedback ──────────────────────────────────────────────
    openFeedback(finding, action) {
      const isLearn = action === 'learn'
      const isSuppress = action === 'suppress'
      this.feedback = {
        finding,
        action: isLearn ? 'learn' : (isSuppress ? 'suppress' : 'dismiss'),
        reason: '',
        title: finding.title ? `Quality: ${finding.title}`.slice(0, 110) : '',
        content: finding.remediation || '',
        learn: isLearn,
        suppress: isSuppress,
        scope: 'finding'
      }
      this.feedbackOpen = true
    },

    async submitFeedback() {
      if (!this.qualityApi || !this.feedback.finding) return
      if (!this.feedback.reason.trim()) {
        this.error = 'A short reason is required: it is the evidence for the decision.'
        return
      }
      this.feedbackSaving = true
      this.error = null
      try {
        const result = await this.qualityApi.feedback({
          run_id: this.run?.id || '',
          finding_id: this.feedback.finding.id,
          action: this.feedback.action,
          reason: this.feedback.reason.trim(),
          title: this.feedback.title.trim(),
          content: this.feedback.content.trim(),
          scope: this.feedback.scope,
          learn: this.feedback.learn,
          suppress: this.feedback.suppress
        })
        this.feedbackOpen = false
        this.notify(`Finding ${result.finding?.status || 'updated'}.`)
        await this.openRun(result.run?.id || this.run?.id)
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not record the feedback')
      } finally {
        this.feedbackSaving = false
      }
    },

    // ── Findings: fixes ─────────────────────────────────────────────────
    async proposeFix(finding) {
      if (!this.qualityApi) return
      this.fixing = true
      this.error = null
      try {
        this.fixResult = await this.qualityApi.fix({
          run_id: this.run?.id || '',
          finding_ids: finding ? [finding.id] : null,
          apply: false,
          use_ai: true
        })
        this.notify('Fix proposal ready. Review it before applying.')
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not propose a fix')
      } finally {
        this.fixing = false
      }
    },

    async applyFix() {
      if (!this.qualityApi || !this.fixResult) return
      this.fixing = true
      this.error = null
      try {
        const result = await this.qualityApi.fix({
          run_id: this.fixResult.run_id,
          finding_ids: (this.fixResult.findings || []).map(finding => finding.id),
          apply: true,
          use_ai: true
        })
        this.fixResult = result
        this.notify(`Applied ${result.applied || 0} fix(es).`)
        // The post-fix verification is a NEW run. Show it instead of the
        // original run whose findings are still recorded as open.
        const verificationId = result.verification?.run_id
        if (verificationId) {
          this.run = await this.qualityApi.run(verificationId)
        } else {
          this.run = null
        }
        await this.loadState({ autoBootstrap: false })
      } catch (ex) {
        this.error = this.errorText(ex, 'Could not apply the fixes')
      } finally {
        this.fixing = false
      }
    },

    // ── Presentation ────────────────────────────────────────────────────
    severityClass(severity) {
      return {
        critical: 'badge-error',
        high: 'badge-warning',
        medium: 'badge-info',
        low: 'badge-ghost',
        info: 'badge-ghost'
      }[severity] || 'badge-ghost'
    },

    statusClass(status) {
      return {
        open: 'badge-ghost',
        dismissed: 'badge-neutral',
        suppressed: 'badge-neutral',
        fixed: 'badge-success'
      }[status || 'open'] || 'badge-ghost'
    },

    findingLocation(finding) {
      if (!finding?.path) return '-'
      return finding.line ? `${finding.path}:${finding.line}` : finding.path
    },

    guardianChecks(guardian) {
      return guardian?.checks || []
    },

    checkStatusClass(status) {
      return {
        passed: 'text-success',
        failed: 'text-error',
        error: 'text-error',
        blocked: 'text-error',
        timeout: 'text-warning',
        skipped: 'text-base-content/40'
      }[status] || 'text-base-content/50'
    },

    formatCost(value) {
      const number = Number(value || 0)
      return number ? number.toFixed(4) : '0'
    },

    formatTime(value) {
      if (!value) return ''
      try {
        return new Date(value).toLocaleString()
      } catch {
        return value
      }
    }
  }
}
</script>

<template>
  <div class="h-full w-full flex flex-col bg-base-100 overflow-hidden text-sm">
    <!-- Header -->
    <header class="shrink-0 border-b border-base-content/10 px-4 py-3 flex items-center gap-3 flex-wrap">
      <div class="w-9 h-9 rounded-xl grid place-items-center bg-success/15 text-success shrink-0">
        <i class="fa-solid fa-shield-heart"></i>
      </div>
      <div class="min-w-0 flex-1">
        <h2 class="font-bold leading-tight truncate">Quality guardians</h2>
        <p class="text-[11px] text-base-content/50 hidden sm:block">
          File-scoped guardians. Fast checks on every change; AI review when the policy allows it.
        </p>
      </div>
      <div class="flex items-center gap-1.5 flex-wrap">
        <span class="badge badge-sm badge-outline" :title="`${guardians.length} guardian profiles`">
          <i class="fa-solid fa-user-shield mr-1"></i>{{ guardians.length }}
        </span>
        <span class="badge badge-sm" :class="openFindings.length ? 'badge-warning' : 'badge-success'">
          {{ openFindings.length }} finding{{ openFindings.length === 1 ? '' : 's' }}
        </span>
        <button class="btn btn-xs btn-ghost gap-1" :disabled="loading" title="Reload" @click="refresh">
          <i class="fa-solid fa-rotate" :class="loading ? 'animate-spin' : ''"></i>
        </button>
        <button class="btn btn-xs btn-ghost gap-1" :disabled="running" title="Create/refresh the guardians from the detected stack" @click="bootstrap">
          <i class="fa-solid fa-wand-magic-sparkles"></i>
          <span class="hidden md:inline">Bootstrap</span>
        </button>
        <button class="btn btn-xs btn-primary gap-1" :disabled="!canRun" @click="runReview()">
          <span v-if="running" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-play"></i>
          Run review
        </button>
      </div>
    </header>

    <!-- Feedback -->
    <div v-if="error" class="alert alert-error rounded-none py-1 px-3 text-xs flex items-center gap-2 shrink-0">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span class="grow">{{ error }}</span>
      <button class="btn btn-ghost btn-xs" @click="error = null"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div v-if="notice" class="alert alert-success rounded-none py-1 px-3 text-xs flex items-center gap-2 shrink-0">
      <i class="fa-solid fa-circle-check"></i>
      <span class="grow">{{ notice }}</span>
      <button class="btn btn-ghost btn-xs" @click="notice = null"><i class="fa-solid fa-xmark"></i></button>
    </div>

    <!-- Loading -->
    <div v-if="loading && !state" class="flex-1 p-4 flex flex-col gap-3 overflow-hidden">
      <div class="skeleton h-24 w-full rounded-xl"></div>
      <div class="skeleton h-40 w-full rounded-xl"></div>
      <div class="skeleton h-40 w-full rounded-xl"></div>
    </div>

    <div v-else class="flex-1 min-h-0 overflow-y-auto">
      <div class="grid grid-cols-1 xl:grid-cols-[20rem_minmax(0,1fr)] gap-4 p-4">
        <!-- Left: guardians + policy -->
        <aside class="flex flex-col gap-3 min-w-0">
          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3">
            <div class="flex items-center gap-2 mb-2">
              <i class="fa-solid fa-sliders text-primary text-xs"></i>
              <h3 class="text-xs font-bold uppercase tracking-wide">Policy</h3>
              <span v-if="policy.enabled" class="badge badge-xs badge-success ml-auto">on</span>
              <span v-else class="badge badge-xs badge-error ml-auto">off</span>
            </div>
            <div class="flex flex-col gap-1.5">
              <label class="flex items-center justify-between gap-2 cursor-pointer" title="Create the guardians automatically from the detected stack">
                <span class="text-[11px]">Auto-create guardians</span>
                <input type="checkbox" class="toggle toggle-xs toggle-success" :checked="policy.auto_bootstrap" @change="togglePolicy('auto_bootstrap')" />
              </label>
              <label class="flex items-center justify-between gap-2 cursor-pointer" title="Deterministic checks run after a file-change burst">
                <span class="text-[11px]">Checks on file change</span>
                <input type="checkbox" class="toggle toggle-xs toggle-success" :checked="policy.run_on_change" @change="togglePolicy('run_on_change')" />
              </label>
              <label class="flex items-center justify-between gap-2 cursor-pointer" title="LLM review on every file-change burst (costs tokens)">
                <span class="text-[11px]">AI on file change</span>
                <input type="checkbox" class="toggle toggle-xs toggle-warning" :checked="policy.ai_on_change" @change="togglePolicy('ai_on_change')" />
              </label>
              <label class="flex items-center justify-between gap-2 cursor-pointer" title="LLM review of staged changes (the next commit)">
                <span class="text-[11px]">AI on staged</span>
                <input type="checkbox" class="toggle toggle-xs toggle-success" :checked="policy.ai_on_staged" @change="togglePolicy('ai_on_staged')" />
              </label>
              <label class="flex items-center justify-between gap-2 cursor-pointer" title="LLM review of manual runs">
                <span class="text-[11px]">AI on manual</span>
                <input type="checkbox" class="toggle toggle-xs toggle-success" :checked="policy.ai_on_manual" @change="togglePolicy('ai_on_manual')" />
              </label>
            </div>
            <div class="mt-2 pt-2 border-t border-base-content/10 text-[10px] text-base-content/50 flex flex-col gap-0.5">
              <span>Debounce {{ policy.debounce_seconds }}s · cooldown {{ policy.file_cooldown_seconds }}s</span>
              <span>AI budget {{ policy.max_ai_files_per_run }} files / {{ policy.max_ai_calls_per_run }} calls</span>
              <span>Gate: block ≥ {{ policy.block_above }} · approval ≥ {{ policy.approval_above }}</span>
            </div>
          </section>

          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3">
            <div class="flex items-center gap-2 mb-2">
              <i class="fa-solid fa-user-shield text-success text-xs"></i>
              <h3 class="text-xs font-bold uppercase tracking-wide">Guardians</h3>
              <span class="badge badge-xs badge-ghost ml-auto">{{ guardians.length }}</span>
            </div>
            <div v-if="!guardians.length" class="text-[11px] text-base-content/50">
              No guardian yet. Use <b>Bootstrap</b> to create them from the detected stack.
            </div>
            <div v-else class="flex flex-col gap-1.5">
              <div
                v-for="guardian in guardians"
                :key="guardian.name"
                class="rounded-lg bg-base-100 border border-base-content/10 p-2"
              >
                <div class="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    class="toggle toggle-xs toggle-success"
                    :checked="guardian.enabled"
                    :title="guardian.enabled ? 'Disable guardian' : 'Enable guardian'"
                    @change="toggleGuardian(guardian)"
                  />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1">
                      <span class="font-mono text-[11px] font-semibold truncate">{{ guardian.name }}</span>
                      <span v-if="guardian.auto_generated" class="badge badge-xs" :class="guardian.polished ? 'badge-warning' : 'badge-ghost'">
                        {{ guardian.polished ? 'polished' : 'auto' }}
                      </span>
                    </div>
                    <div class="text-[10px] text-base-content/50 truncate" :title="guardian.file_match">
                      {{ guardian.kind || 'custom' }} · {{ guardian.file_match }}
                    </div>
                  </div>
                  <button
                    class="btn btn-ghost btn-xs btn-square"
                    title="Run this guardian now"
                    :disabled="running"
                    @click="runGuardian(guardian)"
                  >
                    <i class="fa-solid fa-play text-[10px]"></i>
                  </button>
                </div>
                <div class="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-base-content/45">
                  <span class="badge badge-xs badge-outline">{{ guardian.check_count }} check(s)</span>
                  <span v-for="trigger in guardian.triggers" :key="trigger" class="badge badge-xs badge-ghost">{{ trigger }}</span>
                </div>
              </div>
            </div>

            <div v-if="candidates.length" class="mt-3 pt-3 border-t border-base-content/10">
              <div class="text-[10px] font-semibold uppercase tracking-wide text-base-content/40 mb-1.5">
                Adopt an existing profile
              </div>
              <div class="flex flex-col gap-1">
                <div v-for="candidate in candidates.slice(0, 8)" :key="candidate.name" class="flex items-center gap-1">
                  <span class="font-mono text-[10px] truncate flex-1" :title="candidate.file_match">{{ candidate.name }}</span>
                  <button class="btn btn-xs btn-ghost" title="Adopt as guardian" @click="adopt(candidate)">
                    <i class="fa-solid fa-plus"></i>
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 text-[10px] text-base-content/50">
            <div class="flex items-center gap-2 mb-1">
              <i class="fa-solid fa-magnifying-glass-chart text-info"></i>
              <span class="text-xs font-bold uppercase tracking-wide text-base-content/70">Detected stack</span>
            </div>
            <div class="flex flex-wrap gap-1">
              <span v-for="language in detectedStack.languages" :key="language" class="badge badge-xs badge-outline">{{ language }}</span>
              <span v-for="framework in detectedStack.frameworks" :key="framework" class="badge badge-xs badge-outline">{{ framework }}</span>
              <span v-if="detectedStack.docs" class="badge badge-xs badge-ghost">docs</span>
              <span v-if="detectedStack.docker" class="badge badge-xs badge-ghost">docker</span>
              <span v-if="detectedStack.ci" class="badge badge-xs badge-ghost">ci</span>
            </div>
            <div v-if="triggerStatus.pending || triggerStatus.running" class="mt-2 text-info">
              <i class="fa-solid fa-circle-notch animate-spin mr-1"></i>
              {{ triggerStatus.running ? 'Running' : 'Queued' }}
              {{ triggerStatus.file_count || 0 }} file(s)
            </div>
          </section>
        </aside>

        <!-- Right: review + findings -->
        <main class="flex flex-col gap-3 min-w-0">
          <!-- Run form -->
          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3">
            <div class="flex flex-wrap items-end gap-2">
              <div>
                <label class="label-text text-[10px] font-semibold block pb-1">Scope</label>
                <select v-model="scope" class="select select-xs select-bordered">
                  <option value="changed">Changed files</option>
                  <option value="project">Whole project</option>
                  <option value="file_type">By file type</option>
                  <option value="files">Specific files</option>
                </select>
              </div>
              <div>
                <label class="label-text text-[10px] font-semibold block pb-1">Trigger</label>
                <select v-model="trigger" class="select select-xs select-bordered">
                  <option value="manual">Manual (all guardians)</option>
                  <option value="change">File change</option>
                  <option value="staged">Staged</option>
                  <option value="commit">Commit</option>
                </select>
              </div>
              <div>
                <label class="label-text text-[10px] font-semibold block pb-1">Mode</label>
                <select v-model="mode" class="select select-xs select-bordered">
                  <option value="auto">Checks + AI</option>
                  <option value="checks">Checks only</option>
                  <option value="review">AI only</option>
                </select>
              </div>
              <div v-if="scope === 'file_type'" class="flex-1 min-w-[9rem]">
                <label class="label-text text-[10px] font-semibold block pb-1">File type</label>
                <input v-model="fileType" type="text" class="input input-xs input-bordered w-full" placeholder="python, vue, docs, tests, .ts" />
              </div>
              <div class="w-24">
                <label class="label-text text-[10px] font-semibold block pb-1">Max files</label>
                <input v-model="maxFiles" type="number" min="1" max="500" class="input input-xs input-bordered w-full" placeholder="policy" />
              </div>
              <button class="btn btn-sm btn-primary gap-1" :disabled="!canRun" @click="runReview()">
                <span v-if="running" class="loading loading-spinner loading-xs"></span>
                <i v-else class="fa-solid fa-shield-heart"></i>
                Review
              </button>
            </div>
            <div v-if="scope === 'files'" class="mt-2">
              <label class="label-text text-[10px] font-semibold block pb-1">Paths (one per line or comma separated)</label>
              <textarea v-model="pathsText" rows="2" class="textarea textarea-bordered textarea-xs w-full font-mono" placeholder="src/app.py, docs/index.md"></textarea>
            </div>
          </section>

          <!-- Verdict + stats -->
          <section v-if="run" class="rounded-xl border border-base-content/10 bg-base-200/40 p-3">
            <div class="flex flex-wrap items-center gap-2">
              <span class="badge badge-sm" :class="verdictClass">{{ (verdict.action || 'allow').replace('_', ' ') }}</span>
              <span class="font-semibold text-sm truncate">{{ run.id }}</span>
              <span class="text-[10px] text-base-content/45">{{ formatTime(run.created_at) }}</span>
              <div class="ml-auto flex items-center gap-1.5 flex-wrap">
                <span class="badge badge-xs badge-outline">{{ run.files?.length || 0 }} file(s)</span>
                <span class="badge badge-xs badge-outline">{{ run.checks?.length || 0 }} check(s)</span>
                <span class="badge badge-xs badge-outline">{{ run.ai_calls || 0 }} AI call(s)</span>
                <span class="badge badge-xs badge-outline">{{ formatCost(run.ai_cost) }}</span>
                <button
                  class="btn btn-xs"
                  :disabled="fixing || !openFindings.length"
                  title="Ask for fixes of every open finding (proposal, then apply)"
                  @click="proposeFix(null)"
                >
                  <span v-if="fixing" class="loading loading-spinner loading-xs"></span>
                  <i v-else class="fa-solid fa-screwdriver-wrench"></i>
                  Propose fixes
                </button>
              </div>
            </div>
            <p class="mt-2 text-xs text-base-content/70">{{ verdict.summary || run.summary || 'No summary.' }}</p>
            <div v-if="run.errors?.length" class="mt-2 text-[10px] text-warning">
              <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ run.errors.join(' · ') }}
            </div>
          </section>

          <!-- Findings -->
          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3">
            <div class="flex items-center gap-2 mb-2">
              <i class="fa-solid fa-list-check text-warning text-xs"></i>
              <h3 class="text-xs font-bold uppercase tracking-wide">Findings</h3>
              <span class="badge badge-xs badge-ghost">{{ findings.length }}</span>
              <div class="ml-auto flex items-center gap-1">
                <button class="btn btn-ghost btn-xs gap-1" @click="showChecks = !showChecks">
                  <i class="fa-solid fa-terminal text-[10px]"></i>
                  Checks
                </button>
                <button class="btn btn-ghost btn-xs gap-1" @click="showRuns = !showRuns">
                  <i class="fa-solid fa-clock-rotate-left text-[10px]"></i>
                  Runs
                  <span class="badge badge-xs">{{ runs.length }}</span>
                </button>
              </div>
            </div>

            <!-- Checks table -->
            <div v-if="showChecks" class="mb-3 overflow-x-auto rounded-lg border border-base-content/10">
              <table class="table table-xs">
                <thead>
                  <tr><th>Guardian</th><th>Check</th><th>Status</th><th>Files</th><th>Message</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(check, index) in (run?.checks || [])" :key="`${check.guardian}-${check.check_id}-${index}`">
                    <td class="font-mono text-[10px]">{{ check.guardian }}</td>
                    <td class="font-mono text-[10px]">{{ check.check_id }}</td>
                    <td class="text-[10px]" :class="checkStatusClass(check.status)">{{ check.status }}</td>
                    <td class="text-[10px]">{{ check.files?.length || 0 }}</td>
                    <td class="text-[10px] max-w-[20rem] truncate" :title="check.message">{{ check.message }}</td>
                  </tr>
                  <tr v-if="!(run?.checks || []).length">
                    <td colspan="5" class="text-center text-[11px] text-base-content/40">No deterministic check ran.</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Runs -->
            <div v-if="showRuns" class="mb-3 flex flex-col gap-1">
              <button
                v-for="item in runs"
                :key="item.id"
                class="text-left rounded-lg border border-base-content/10 bg-base-100 px-2 py-1.5 hover:bg-base-200 flex items-center gap-2"
                @click="openRun(item.id)"
              >
                <span class="badge badge-xs" :class="item.action === 'allow' ? 'badge-success' : item.action === 'block' ? 'badge-error' : 'badge-warning'">
                  {{ item.action }}
                </span>
                <span class="font-mono text-[10px] truncate flex-1">{{ item.id }}</span>
                <span class="text-[10px] text-base-content/45">{{ item.finding_count }} finding(s)</span>
              </button>
              <div v-if="!runs.length" class="text-[11px] text-base-content/40">No run yet.</div>
            </div>

            <!-- Finding cards -->
            <div v-if="!findings.length" class="text-[11px] text-base-content/50 flex items-center gap-2 py-2">
              <i class="fa-solid fa-circle-check text-success"></i>
              No finding in this run.
            </div>
            <div v-else class="flex flex-col gap-2">
              <article
                v-for="finding in findings"
                :key="finding.id"
                class="rounded-lg border border-base-content/10 bg-base-100 p-2.5"
                :class="{ 'opacity-60': (finding.status || 'open') !== 'open' }"
              >
                <div class="flex items-start gap-2 flex-wrap">
                  <span class="badge badge-xs" :class="severityClass(finding.severity)">{{ finding.severity }}</span>
                  <span class="badge badge-xs badge-outline font-mono">{{ finding.guardian }}</span>
                  <span class="font-mono text-[10px] text-base-content/55">{{ findingLocation(finding) }}</span>
                  <span class="badge badge-xs" :class="statusClass(finding.status)">{{ finding.status || 'open' }}</span>
                  <span class="text-[10px] text-base-content/40 ml-auto font-mono">{{ finding.rule_id }}</span>
                </div>
                <h4 class="mt-1 text-xs font-semibold">{{ finding.title }}</h4>
                <p v-if="finding.evidence" class="mt-1 text-[11px] text-base-content/60 font-mono whitespace-pre-wrap break-words">{{ finding.evidence }}</p>
                <p v-if="finding.remediation" class="mt-1 text-[11px] text-base-content/70">
                  <i class="fa-solid fa-lightbulb text-warning mr-1"></i>{{ finding.remediation }}
                </p>
                <div class="mt-2 flex items-center gap-1 flex-wrap">
                  <button
                    class="btn btn-xs btn-ghost gap-1"
                    :disabled="fixing"
                    title="Ask the model for an exact fix of this finding"
                    @click="proposeFix(finding)"
                  >
                    <i class="fa-solid fa-screwdriver-wrench text-[10px]"></i> Fix
                  </button>
                  <button class="btn btn-xs btn-ghost gap-1" title="Teach the guardian this is an accepted convention" @click="openFeedback(finding, 'learn')">
                    <i class="fa-solid fa-graduation-cap text-[10px]"></i> Learn
                  </button>
                  <button class="btn btn-xs btn-ghost gap-1" title="Close the finding with a reason" @click="openFeedback(finding, 'dismiss')">
                    <i class="fa-solid fa-check text-[10px]"></i> Dismiss
                  </button>
                  <button class="btn btn-xs btn-ghost gap-1" title="Do not report this again" @click="openFeedback(finding, 'suppress')">
                    <i class="fa-solid fa-ban text-[10px]"></i> Suppress
                  </button>
                  <span v-if="finding.status_reason" class="text-[10px] text-base-content/40 ml-auto truncate max-w-[18rem]" :title="finding.status_reason">
                    {{ finding.status_reason }}
                  </span>
                </div>
              </article>
            </div>
          </section>
        </main>
      </div>
    </div>

    <!-- Feedback modal -->
    <div v-if="feedbackOpen" class="modal modal-open" role="dialog">
      <div class="modal-box max-w-xl">
        <h3 class="font-bold text-sm">
          {{ feedback.action === 'learn' ? 'Teach the guardian' : feedback.action === 'suppress' ? 'Suppress the finding' : 'Dismiss the finding' }}
        </h3>
        <p class="text-[11px] text-base-content/50 mt-1">
          {{ feedback.finding?.guardian }} · {{ findingLocation(feedback.finding) }}
        </p>
        <p class="text-xs mt-2">{{ feedback.finding?.title }}</p>

        <label class="label-text text-xs font-semibold block mt-3 pb-1">Reason *</label>
        <textarea
          v-model="feedback.reason"
          rows="3"
          class="textarea textarea-bordered textarea-sm w-full"
          placeholder="Why is this a false positive, an accepted convention or a real fix?"
        ></textarea>
        <p class="text-[10px] text-base-content/40 mt-1">
          The reason is stored in the run history. "Learn" also writes a durable rule to the guardian profile.
        </p>

        <template v-if="feedback.action === 'learn'">
          <label class="label-text text-xs font-semibold block mt-2 pb-1">Rule title</label>
          <input v-model="feedback.title" type="text" class="input input-sm input-bordered w-full" />
          <label class="label-text text-xs font-semibold block mt-2 pb-1">Rule body</label>
          <textarea v-model="feedback.content" rows="3" class="textarea textarea-bordered textarea-sm w-full"></textarea>
        </template>

        <div class="flex flex-wrap items-center gap-3 mt-3">
          <label class="flex items-center gap-2 text-[11px] cursor-pointer">
            <input v-model="feedback.learn" type="checkbox" class="checkbox checkbox-xs checkbox-success" />
            Learn a durable rule
          </label>
          <label class="flex items-center gap-2 text-[11px] cursor-pointer">
            <input v-model="feedback.suppress" type="checkbox" class="checkbox checkbox-xs" />
            Suppress this finding
          </label>
          <select v-if="feedback.suppress" v-model="feedback.scope" class="select select-xs select-bordered ml-auto">
            <option value="finding">This finding</option>
            <option value="rule">This rule on this file</option>
            <option value="path">Any rule on this path</option>
          </select>
        </div>

        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" :disabled="feedbackSaving" @click="feedbackOpen = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="feedbackSaving" @click="submitFeedback">
            <span v-if="feedbackSaving" class="loading loading-spinner loading-xs"></span>
            Save decision
          </button>
        </div>
      </div>
    </div>

    <!-- Fix proposal modal -->
    <div v-if="fixResult" class="modal modal-open" role="dialog">
      <div class="modal-box max-w-4xl">
        <div class="flex items-center gap-2">
          <h3 class="font-bold text-sm">Quality fixes</h3>
          <span class="badge badge-xs badge-ghost">{{ fixResult.findings?.length || 0 }} finding(s)</span>
          <span class="badge badge-xs" :class="(fixResult.applied || 0) ? 'badge-success' : 'badge-warning'">
            {{ fixResult.applied || 0 }} applied
          </span>
          <button class="btn btn-sm btn-ghost btn-square ml-auto" aria-label="Close" @click="fixResult = null">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="mt-3 max-h-[60vh] overflow-y-auto flex flex-col gap-3">
          <div v-for="(execution, index) in (fixResult.deterministic || [])" :key="`det-${index}`" class="rounded-lg border border-base-content/10 p-2">
            <div class="text-[11px] font-semibold">Deterministic · {{ execution.guardian }} / {{ execution.check_id }}</div>
            <pre class="text-[10px] whitespace-pre-wrap break-words bg-base-200 rounded p-2 mt-1">{{ (execution.command || []).join(' ') || '(not run)' }}</pre>
            <p v-if="execution.reason" class="text-[10px] text-warning mt-1">
              <i class="fa-solid fa-circle-info mr-1"></i>{{ execution.reason }}
            </p>
          </div>
          <div v-for="proposal in (fixResult.proposals || [])" :key="proposal.path" class="rounded-lg border border-base-content/10 p-2">
            <div class="text-[11px] font-semibold">{{ proposal.path }}</div>
            <p class="text-[11px] text-base-content/60">{{ proposal.summary }}</p>
            <pre v-if="proposal.preview" class="text-[10px] whitespace-pre-wrap break-words bg-base-200 rounded p-2 mt-1 max-h-60 overflow-y-auto">{{ proposal.preview }}</pre>
            <ul v-if="proposal.changes?.length" class="text-[10px] list-disc ml-4 mt-1 text-base-content/60">
              <li v-for="(change, changeIndex) in proposal.changes" :key="changeIndex">{{ change.reason || 'change' }}</li>
            </ul>
          </div>
          <div v-if="(fixResult.deterministic || []).some(item => item.reason === 'auto_fix=false')" class="alert alert-warning py-1 px-2 text-[11px]">
            Some deterministic fixes are disabled by the guardian's <b>Allow deterministic fixes</b>
            flag. Enable it in Team → member → Quality, or apply only the AI changes.
          </div>
          <div v-if="fixResult.verification" class="rounded-lg border border-base-content/10 p-2">
            <div class="text-[11px] font-semibold">Verification</div>
            <div class="text-[11px] text-base-content/60">
              Run {{ fixResult.verification.run_id }} · {{ fixResult.verification.action }} ·
              {{ fixResult.verification.findings }} open finding(s)
            </div>
          </div>
          <div v-if="fixResult.errors?.length" class="text-[11px] text-warning">
            <div v-for="(item, index) in fixResult.errors" :key="index">• {{ item }}</div>
          </div>
        </div>
        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="fixResult = null">Close</button>
          <button class="btn btn-sm btn-primary gap-1" :disabled="fixing || !((fixResult.proposals || []).length || (fixResult.deterministic || []).length)" @click="applyFix">
            <span v-if="fixing" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-check"></i>
            Apply fixes
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
