<template>
  <div v-if="isTicket" class="shrink-0">
    <button
      class="tooltip tooltip-bottom rounded p-1 transition-colors duration-150 hover:bg-codx-primary/10 focus:ring-2 focus:ring-primary/50 outline-none"
      :class="buttonClass"
      :data-tip="tooltip"
      aria-label="Autonomous ticket"
      @click="openPanel"
    >
      <i class="fa-solid fa-robot text-sm"></i>
      <span v-if="stateLabel" class="badge badge-xs ml-0.5" :class="badgeClass">
        {{ stateLabel }}
      </span>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        class="fixed inset-0 z-[150] flex items-center justify-center bg-black/50 p-4"
        @click.self="closePanel"
      >
        <div class="bg-base-100 rounded-xl shadow-2xl border border-base-300 w-full max-w-2xl max-h-[90vh] overflow-y-auto">
          <div class="flex items-start justify-between gap-3 px-5 py-4 border-b border-base-300">
            <div class="min-w-0">
              <h3 class="font-semibold text-base flex items-center gap-2">
                <i class="fa-solid fa-robot text-codx-primary"></i>
                Autonomous ticket
              </h3>
              <p class="text-xs text-base-content/60 truncate">{{ ticket?.name }}</p>
            </div>
            <button class="btn btn-ghost btn-sm btn-circle" @click="closePanel" aria-label="Close">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>

          <div v-if="loading" class="p-8 text-center text-sm text-base-content/60">
            <span class="loading loading-spinner loading-md"></span>
            <p class="mt-2">Reading the ticket…</p>
          </div>

          <template v-else>
            <div v-if="error" class="m-4 alert alert-error text-sm py-2">
              <i class="fa-solid fa-triangle-exclamation"></i>
              <span>{{ error }}</span>
            </div>

            <div class="px-5 py-3 grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
              <div>
                <div class="text-base-content/50">Ticket</div>
                <div class="font-semibold">{{ ticket?.status || '—' }}</div>
              </div>
              <div>
                <div class="text-base-content/50">Autonomy</div>
                <div class="font-semibold" :class="stateColor">{{ state || 'off' }}</div>
              </div>
              <div>
                <div class="text-base-content/50">Rounds</div>
                <div class="font-semibold">{{ autonomy?.rounds || 0 }} / {{ autonomy?.max_rounds || '—' }}</div>
              </div>
              <div>
                <div class="text-base-content/50">Attempts</div>
                <div class="font-semibold">{{ autonomy?.attempts || 0 }}</div>
              </div>
              <div>
                <div class="text-base-content/50">Validation</div>
                <div class="font-semibold" :class="autonomy?.validation_passed ? 'text-success' : 'text-base-content/70'">
                  {{ autonomy?.validation_passed ? 'passed' : (autonomy?.state === 'running' ? 'checking' : 'not green') }}
                </div>
              </div>
              <div>
                <div class="text-base-content/50">Workspace</div>
                <div class="font-semibold truncate" :title="autonomy?.workspace_branch || ''">
                  {{ autonomy?.workspace_branch || 'main checkout' }}
                </div>
              </div>
            </div>

            <div v-if="autonomy?.last_error" class="mx-5 mb-3 rounded-lg bg-warning/10 border border-warning/30 p-3 text-xs">
              <div class="font-semibold text-warning mb-1">Last stop</div>
              <div class="whitespace-pre-wrap">{{ autonomy.last_error }}</div>
            </div>

            <div v-if="autonomy?.last_validation" class="mx-5 mb-3 rounded-lg bg-base-200 p-3">
              <div class="font-semibold text-xs mb-1">Validation evidence</div>
              <pre class="text-[11px] whitespace-pre-wrap max-h-40 overflow-y-auto">{{ autonomy.last_validation }}</pre>
            </div>

            <!-- Target project controls -->
            <div v-if="canConfigure" class="px-5 pb-4 space-y-3">
              <div class="divider my-1 text-xs text-base-content/50">Configure the run</div>

              <label class="flex items-center gap-3 cursor-pointer">
                <input v-model="form.enabled" type="checkbox" class="toggle toggle-sm toggle-primary" />
                <span class="text-sm">Work this ticket autonomously</span>
              </label>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label class="form-control">
                  <span class="label-text text-xs">Profile (agent)</span>
                  <input v-model="form.profile" class="input input-sm input-bordered" placeholder="project default" />
                </label>
                <label class="form-control">
                  <span class="label-text text-xs">Max rounds</span>
                  <input v-model.number="form.max_rounds" type="number" min="1" max="40" class="input input-sm input-bordered" />
                </label>
                <label class="form-control">
                  <span class="label-text text-xs">Max runtime (minutes)</span>
                  <input v-model.number="form.max_runtime_minutes" type="number" min="1" max="180" class="input input-sm input-bordered" />
                </label>
                <label class="form-control">
                  <span class="label-text text-xs">Budget (cxjcoins, 0 = default)</span>
                  <input v-model.number="form.budget_cxjcoins" type="number" min="0" step="0.5" class="input input-sm input-bordered" />
                </label>
              </div>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label class="form-control">
                  <span class="label-text text-xs">Validation gate</span>
                  <select v-model="form.validation.source" class="select select-sm select-bordered">
                    <option value="auto">Project QA suite</option>
                    <option value="commands">Explicit commands</option>
                    <option value="none">None (not recommended)</option>
                  </select>
                </label>
                <label class="form-control">
                  <span class="label-text text-xs">QA suite (auto)</span>
                  <input v-model="form.validation.suite" class="input input-sm input-bordered" placeholder="auto" />
                </label>
              </div>

              <label v-if="form.validation.source === 'commands'" class="form-control">
                <span class="label-text text-xs">Commands, one per line (shell disabled)</span>
                <textarea
                  v-model="commandsText"
                  class="textarea textarea-bordered textarea-sm font-mono text-xs"
                  rows="3"
                  placeholder="pytest tests/test_export.py -q"
                ></textarea>
              </label>

              <p class="text-[11px] text-base-content/60">
                The ticket must be accepted before code runs. <strong>Queue</strong>
                accepts it and lets the worker run; moving the card to
                <strong>Accepted</strong> does the same.
              </p>

              <div class="flex flex-wrap items-center gap-2 pt-1">
                <button class="btn btn-sm btn-primary" :disabled="busy" @click="save">
                  <i class="fa-solid fa-floppy-disk"></i> Save
                </button>
                <button class="btn btn-sm btn-outline" :disabled="busy" @click="queue">
                  <i class="fa-solid fa-clock"></i> Queue
                </button>
                <button class="btn btn-sm btn-secondary" :disabled="busy" @click="runNow">
                  <i class="fa-solid fa-bolt"></i> Run now
                </button>
                <button class="btn btn-sm btn-ghost text-error" :disabled="busy" @click="cancelRun">
                  <i class="fa-solid fa-stop"></i> Cancel
                </button>
              </div>
            </div>

            <div v-else class="px-5 pb-4">
              <p class="text-xs text-base-content/60">
                This ticket belongs to <strong>{{ ticket?.target_project_name }}</strong>.
                Only the target project can configure or run its autonomous work;
                you can follow its state here.
              </p>
            </div>

            <div v-if="autonomy?.delivery" class="px-5 pb-5">
              <div class="divider my-1 text-xs text-base-content/50">Last delivery</div>
              <pre class="text-[11px] whitespace-pre-wrap max-h-60 overflow-y-auto bg-base-200 rounded-lg p-3">{{ autonomy.delivery }}</pre>
            </div>
          </template>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script>
import { API } from '@/api/api'

export default {
  name: 'TicketAutonomyControl',
  props: {
    chat: { type: Object, required: true }
  },
  data() {
    return {
      open: false,
      loading: false,
      busy: false,
      error: '',
      ticket: null,
      form: {
        enabled: false,
        profile: '',
        auto_accept: true,
        max_rounds: 6,
        max_runtime_minutes: 30,
        budget_cxjcoins: 0,
        validation: { source: 'auto', suite: '', commands: [], required: true }
      },
      commandsText: '',
      pollTimer: null
    }
  },
  computed: {
    isTicket() {
      return this.chat?.mode === 'ticket'
    },
    ticketId() {
      return this.ticket?.ticket_id || this.chat?.ticket?.id || this.chat?.id
    },
    autonomy() {
      return this.ticket?.autonomy || this.chat?.ticket?.autonomy || null
    },
    state() {
      return this.autonomy?.state || 'off'
    },
    canConfigure() {
      return this.ticket?.direction === 'incoming'
    },
    stateLabel() {
      const map = {
        queued: 'queued', running: 'running', passed: 'done',
        failed: 'failed', blocked: 'blocked', cancelled: 'cancelled'
      }
      return this.autonomy?.enabled ? (map[this.state] || '') : ''
    },
    badgeClass() {
      const map = {
        queued: 'badge-info', running: 'badge-warning animate-pulse',
        passed: 'badge-success', failed: 'badge-error', blocked: 'badge-warning',
        cancelled: 'badge-ghost'
      }
      return this.autonomy?.enabled ? (map[this.state] || 'badge-ghost') : 'badge-ghost'
    },
    buttonClass() {
      if (this.state === 'running') return 'text-warning animate-pulse'
      if (this.state === 'passed') return 'text-success'
      if (this.state === 'failed' || this.state === 'blocked') return 'text-error'
      return this.autonomy?.enabled ? 'text-codx-primary' : 'text-base-content/40'
    },
    stateColor() {
      if (this.state === 'passed') return 'text-success'
      if (this.state === 'failed') return 'text-error'
      if (this.state === 'running' || this.state === 'queued') return 'text-warning'
      return 'text-base-content/70'
    },
    tooltip() {
      if (!this.autonomy?.enabled) return 'Autonomous ticket (off)'
      return `Autonomous ticket: ${this.state}`
    }
  },
  mounted() {
    // Load the live state so the header badge is meaningful before opening.
    if (this.isTicket) this.refresh()
  },
  beforeUnmount() {
    this.stopPolling()
  },
  methods: {
    async openPanel() {
      this.open = true
      await this.refresh()
      if (this.open) this.startPolling()
    },
    closePanel() {
      this.open = false
      this.stopPolling()
    },
    startPolling() {
      this.stopPolling()
      this.pollTimer = setInterval(() => this.pollStatus(), 5000)
    },
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },
    async pollStatus() {
      if (!this.open || this.busy || this.loading) return
      try {
        const payload = await API.get(`/api/tickets/${this.chat.id}/autonomy`)
        this.ticket = { ...(this.ticket || {}), ...payload }
      } catch (ex) {
        // Polling is best effort; the next refresh reports the real error.
      }
    },
    async refresh() {
      if (!this.isTicket) return
      this.loading = true
      this.error = ''
      try {
        this.ticket = await API.get(`/api/tickets/${this.chat.id}`)
        const autonomy = this.ticket?.autonomy || {}
        this.form.enabled = Boolean(autonomy.enabled)
        this.form.profile = autonomy.profile || ''
        this.form.auto_accept = autonomy.auto_accept !== false
        this.form.max_rounds = autonomy.max_rounds || 6
        this.form.max_runtime_minutes = autonomy.max_runtime_minutes || 30
        this.form.budget_cxjcoins = autonomy.budget_cxjcoins || 0
        this.form.validation = {
          source: autonomy.validation?.source || 'auto',
          suite: autonomy.validation?.suite || '',
          commands: autonomy.validation?.commands || [],
          required: autonomy.validation?.required !== false
        }
        this.commandsText = (autonomy.validation?.commands || []).join('\n')
      } catch (ex) {
        this.error = ex?.message || 'Could not read the ticket autonomy state'
      } finally {
        this.loading = false
      }
    },
    payload() {
      const commands = this.commandsText
        .split('\n')
        .map(line => line.trim())
        .filter(Boolean)
      return {
        enabled: this.form.enabled,
        profile: this.form.profile || '',
        auto_accept: this.form.auto_accept,
        max_rounds: this.form.max_rounds,
        max_runtime_minutes: this.form.max_runtime_minutes,
        budget_cxjcoins: this.form.budget_cxjcoins,
        validation: {
          source: this.form.validation.source,
          suite: this.form.validation.suite || null,
          commands,
          required: this.form.validation.source === 'none' ? false : this.form.validation.required
        }
      }
    },
    async save() {
      this.busy = true
      this.error = ''
      try {
        await API.post(`/api/tickets/${this.chat.id}/autonomy`, this.payload())
        await this.refresh()
      } catch (ex) {
        this.error = ex?.message || 'Could not save the autonomy configuration'
      } finally {
        this.busy = false
      }
    },
    async queue() {
      this.busy = true
      this.error = ''
      try {
        await API.post(`/api/tickets/${this.chat.id}/autonomy`, this.payload())
        await API.post(`/api/tickets/${this.chat.id}/autonomy/queue`)
        await this.refresh()
      } catch (ex) {
        this.error = ex?.message || 'Could not queue the ticket'
      } finally {
        this.busy = false
      }
    },
    async runNow() {
      this.busy = true
      this.error = ''
      try {
        await API.post(
          `/api/tickets/${this.chat.id}/autonomy`,
          { ...this.payload(), accept: true }
        )
        await API.post(`/api/tickets/${this.chat.id}/autonomy/run`)
        await this.refresh()
      } catch (ex) {
        this.error = ex?.message || 'Could not start the autonomous run'
      } finally {
        this.busy = false
      }
    },
    async cancelRun() {
      this.busy = true
      this.error = ''
      try {
        await API.post(`/api/tickets/${this.chat.id}/autonomy/cancel`)
        await this.refresh()
      } catch (ex) {
        this.error = ex?.message || 'Could not cancel the run'
      } finally {
        this.busy = false
      }
    }
  }
}
</script>
