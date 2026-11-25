<template>
  <div class="w-full space-y-4">
    <!-- ── Version / installation ───────────────────────────────────────── -->
    <div class="card bg-base-200 border border-base-300 shadow-sm">
      <div class="card-body p-4 gap-3">
        <div class="flex flex-wrap items-start gap-3">
          <div class="w-10 h-10 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0">
            <i class="fa-solid fa-cloud-arrow-down"></i>
          </div>
          <div class="min-w-0 grow">
            <h2 class="font-bold flex flex-wrap items-center gap-2">
              Updates
              <span class="badge badge-sm" :class="versionBadgeClass">{{ versionLabel }}</span>
              <span v-if="current.mode" class="badge badge-sm badge-ghost">{{ current.mode }}</span>
            </h2>
            <p class="text-xs opacity-70 mt-1">
              The update is applied by <span class="font-semibold">codx-junior-installer</span>:
              it pulls the image and recreates the container. Settings, projects and
              data are kept — only the running container is replaced.
            </p>
          </div>
          <button class="btn btn-sm btn-ghost" :disabled="loading" @click="refresh" title="Refresh">
            <i class="fa-solid fa-arrows-rotate" :class="{ 'animate-spin': loading }"></i>
          </button>
        </div>

        <div v-if="error" class="alert alert-error py-2">
          <i class="fa-solid fa-circle-exclamation"></i>
          <span class="text-xs break-all">{{ error }}</span>
        </div>

        <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-xs">
          <div class="rounded-lg bg-base-300/40 p-2">
            <div class="opacity-60">Version</div>
            <div class="font-mono break-all">{{ current.version || 'unknown' }}</div>
          </div>
          <div class="rounded-lg bg-base-300/40 p-2">
            <div class="opacity-60">Commit</div>
            <div class="font-mono break-all">{{ shortCommit }}</div>
          </div>
          <div class="rounded-lg bg-base-300/40 p-2">
            <div class="opacity-60">Instance</div>
            <div class="font-mono break-all">{{ current.instance || '-' }}</div>
          </div>
          <div class="rounded-lg bg-base-300/40 p-2 sm:col-span-2">
            <div class="opacity-60">Image</div>
            <div class="font-mono break-all">{{ current.image || '-' }}</div>
          </div>
          <div class="rounded-lg bg-base-300/40 p-2">
            <div class="opacity-60">Container</div>
            <div class="font-mono break-all">{{ current.container_name || '-' }}</div>
          </div>
        </div>

        <div v-if="!supported && reason" class="alert alert-warning items-start py-2">
          <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
          <div class="min-w-0 text-xs space-y-2">
            <p>{{ reason }}</p>
            <div v-if="manualCommand" class="flex flex-wrap items-center gap-2">
              <code class="bg-base-300 px-1 rounded break-all">{{ manualCommand }}</code>
              <button class="btn btn-xs btn-ghost" @click="copy(manualCommand)">
                <i class="fa-regular fa-copy"></i> Copy
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Update action ────────────────────────────────────────────────── -->
    <div class="card bg-base-200 border border-base-300 shadow-sm">
      <div class="card-body p-4 gap-3">
        <h3 class="font-semibold flex flex-wrap items-center gap-2">
          <i class="fa-solid fa-arrows-rotate text-primary"></i>
          Install the latest image
          <span v-if="state.status !== 'idle'" class="badge badge-sm" :class="stateBadgeClass">
            {{ stateLabel }}
          </span>
        </h3>

        <p class="text-xs opacity-70">
          Pulls <code class="bg-base-300 px-1 rounded">{{ targetLabel }}</code> and runs
          <code class="bg-base-300 px-1 rounded">codx-junior upgrade</code>. The page
          disconnects for a few seconds while the container is recreated.
        </p>

        <div class="flex flex-wrap items-end gap-3">
          <label class="form-control grow min-w-[16rem]">
            <div class="label py-1">
              <span class="label-text text-xs font-semibold">Target image</span>
            </div>
            <input
              v-model.trim="targetImage"
              type="text"
              class="input input-bordered input-sm font-mono text-xs"
              :placeholder="current.image || 'codxjuniorai/codx-junior:latest'"
              :disabled="busy"
            />
          </label>
          <label class="label cursor-pointer gap-2 py-0">
            <span class="label-text text-xs">Recreate even if the image is unchanged</span>
            <input v-model="force" type="checkbox" class="checkbox checkbox-xs" :disabled="busy" />
          </label>
        </div>

        <div v-if="!confirming" class="flex flex-wrap items-center gap-2">
          <button class="btn btn-sm btn-primary" :disabled="!canStart" @click="confirming = true">
            <i class="fa-solid fa-cloud-arrow-down"></i>
            <span>{{ busy ? 'Updating…' : 'Check &amp; update' }}</span>
          </button>
          <span v-if="follow" class="text-xs opacity-70 flex items-center gap-1">
            <span class="loading loading-spinner loading-xs"></span>
            {{ waiting ? 'Waiting for the new container…' : 'Following the update…' }}
          </span>
        </div>

        <div v-else class="alert alert-warning items-start py-2">
          <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
          <div class="min-w-0 text-xs space-y-2">
            <p>
              The container will be recreated and this page will reconnect. Running
              chats, terminals and background jobs may be interrupted for a few
              seconds. Settings, projects and data are kept.
            </p>
            <div class="flex flex-wrap gap-2">
              <button class="btn btn-xs btn-warning" :disabled="starting" @click="startUpdate">
                <span v-if="starting" class="loading loading-spinner loading-xs"></span>
                <span v-else>Yes, update now</span>
              </button>
              <button class="btn btn-xs btn-ghost" :disabled="starting" @click="confirming = false">
                Cancel
              </button>
            </div>
          </div>
        </div>

        <div v-if="state.message" class="alert py-2 text-xs" :class="messageClass">
          <i class="fa-solid" :class="messageIcon"></i>
          <span class="break-words">{{ state.message }}</span>
        </div>
      </div>
    </div>

    <!-- ── Progress / result ────────────────────────────────────────────── -->
    <div v-if="showProgress" class="card bg-base-200 border border-base-300 shadow-sm">
      <div class="card-body p-4 gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-semibold flex items-center gap-2">
            <i class="fa-solid fa-terminal text-primary"></i>
            Installer log
          </h3>
          <span class="badge badge-sm" :class="stateBadgeClass">{{ stateLabel }}</span>
          <span v-if="state.exit_code !== null && state.exit_code !== undefined" class="badge badge-sm badge-ghost">
            exit {{ state.exit_code }}
          </span>
          <button v-if="finished" class="btn btn-xs btn-primary ml-auto" @click="reloadPage">
            <i class="fa-solid fa-rotate-right"></i> Reload page
          </button>
        </div>
        <pre class="max-h-80 overflow-auto rounded-lg bg-base-300/60 p-3 text-[11px] leading-relaxed whitespace-pre-wrap break-words">{{ state.log || 'Waiting for the first log lines…' }}</pre>
        <p v-if="finished && state.status === 'completed'" class="text-xs opacity-70">
          The new container is serving this page again. Reload to pick up the new
          client bundle.
        </p>
      </div>
    </div>
  </div>
</template>

<script>
const POLL_MS = 3000
const ACTIVE = ['pulling', 'updating']

const STATUS_LABELS = {
  idle: 'not updated',
  pulling: 'pulling image',
  updating: 'updating',
  up_to_date: 'up to date',
  completed: 'updated',
  failed: 'failed',
  unknown: 'unknown'
}

export default {
  data() {
    return {
      info: null,
      loading: false,
      error: '',
      confirming: false,
      starting: false,
      targetImage: '',
      force: false,
      // True after the user starts an update: keep polling through the restart.
      follow: false,
      waiting: false,
      baselineCommit: '',
      timer: null
    }
  },
  computed: {
    current() {
      return this.info?.current || {}
    },
    installation() {
      return this.info?.installation || {}
    },
    state() {
      return this.info?.update || {}
    },
    supported() {
      return this.info?.supported === true
    },
    reason() {
      return this.info?.reason || ''
    },
    manualCommand() {
      return this.info?.manual_command || ''
    },
    shortCommit() {
      const commit = String(this.current.commit || '')
      return commit && commit !== 'unknown' ? commit.slice(0, 12) : 'unknown'
    },
    versionLabel() {
      const version = this.current.version
      if (!version || version === 'unknown') return 'version unknown'
      return `v${version.replace(/^v/, '')}`
    },
    versionBadgeClass() {
      return this.versionLabel === 'version unknown' ? 'badge-ghost' : 'badge-primary'
    },
    stateLabel() {
      return STATUS_LABELS[this.state.status] || this.state.status || 'unknown'
    },
    stateBadgeClass() {
      const status = this.state.status
      if (status === 'completed') return 'badge-success'
      if (status === 'up_to_date') return 'badge-info'
      if (status === 'failed' || status === 'unknown') return 'badge-error'
      if (ACTIVE.includes(status)) return 'badge-warning'
      return 'badge-ghost'
    },
    messageClass() {
      const status = this.state.status
      if (status === 'completed') return 'alert-success'
      if (status === 'failed' || status === 'unknown') return 'alert-error'
      if (status === 'up_to_date') return 'alert-info'
      return 'alert-info'
    },
    messageIcon() {
      const status = this.state.status
      if (status === 'completed') return 'fa-circle-check'
      if (status === 'failed' || status === 'unknown') return 'fa-circle-exclamation'
      if (status === 'up_to_date') return 'fa-circle-info'
      return 'fa-circle-info'
    },
    busy() {
      return this.starting || ACTIVE.includes(this.state.status)
    },
    canStart() {
      return this.supported && !this.busy && !this.loading
    },
    finished() {
      const status = this.state.status
      return status === 'completed' || status === 'failed' || status === 'unknown'
    },
    showProgress() {
      return Boolean(this.state.log) || ACTIVE.includes(this.state.status)
    },
    targetLabel() {
      return this.targetImage || this.current.image || 'codxjuniorai/codx-junior:latest'
    }
  },
  created() {
    this.refresh()
    this.timer = setInterval(this.refresh, POLL_MS)
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  },
  methods: {
    getApi() {
      return this.$project?.$api || this.$storex?.api
    },
    errorText(error) {
      return error?.response?.data?.detail || error?.message || String(error)
    },
    notify(text, type = 'info') {
      this.$ui?.addNotification?.({ text, type })
    },
    async refresh() {
      if (this.loading) return
      const api = this.getApi()
      if (!api?.update) {
        this.error = 'The update API is not available in this build.'
        return
      }
      this.loading = true
      try {
        const info = await api.update.info()
        this.info = info
        this.error = ''
        const status = info?.update?.status
        if (this.follow) {
          // Keep following through the restart: reads fail for a few seconds
          // and resume when the new container answers.
          this.waiting = false
          if (!ACTIVE.includes(status)) {
            this.follow = false
            const commit = String(info?.current?.commit || '')
            if (status === 'completed' && commit && commit !== this.baselineCommit) {
              this.notify('codx-junior was updated. Reload the page to use the new client.', 'success')
            } else if (status === 'up_to_date') {
              this.notify('The image was already up to date.', 'info')
            }
          }
        }
      } catch (error) {
        // While following, a failed read is expected: the API is restarting.
        if (this.follow) {
          this.waiting = true
          this.error = ''
        } else {
          this.error = this.errorText(error)
        }
      } finally {
        this.loading = false
      }
    },
    async startUpdate() {
      const api = this.getApi()
      if (!api?.update) return
      this.starting = true
      this.error = ''
      this.baselineCommit = String(this.current.commit || '')
      try {
        const state = await api.update.start({
          image: this.targetImage || '',
          force: this.force
        })
        this.info = { ...this.info, update: { ...(this.info?.update || {}), ...state } }
        this.confirming = false
        this.follow = true
        this.notify('Update started. The page reconnects when the new container is ready.', 'info')
      } catch (error) {
        this.error = this.errorText(error)
        this.follow = false
        this.notify('The update could not be started', 'error')
      } finally {
        this.starting = false
      }
    },
    reloadPage() {
      window.location.reload()
    },
    async copy(text) {
      try {
        await navigator.clipboard.writeText(text)
        this.notify('Copied to the clipboard')
      } catch (error) {
        this.notify('Could not copy to the clipboard', 'error')
      }
    }
  }
}
</script>
