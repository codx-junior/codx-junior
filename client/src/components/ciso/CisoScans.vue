<script setup>
/**
 * CISO scans: start an analysis and follow its lifecycle.
 *
 * Scans run in a background thread on the API, so this panel polls the history
 * while a record is queued/running and stops as soon as everything is terminal.
 * Starting a scan never blocks the screen: the row appears immediately with
 * its progress and the list refreshes every two seconds.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  SEVERITIES,
  formatDate,
  formatDuration,
  scopeLabel,
  severityClass,
  severityLabel,
  statusClass,
  STATUS_LABELS
} from './format'

const props = defineProps({
  api: { type: Object, default: null }
})

const emit = defineEmits(['open-findings', 'changed'])

const scans = ref([])
const total = ref(0)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let pollTimer = null

const form = ref({
  scope: 'project',
  paths: '',
  max_files: 500,
  min_severity: ''
})

const running = computed(() =>
  scans.value.some(scan => ['queued', 'running'].includes(scan.status))
)

const statusOptions = ['project', 'changes', 'staged', 'paths']

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

async function load({ silent = false } = {}) {
  if (!props.api) return
  if (!silent) loading.value = true
  error.value = ''
  try {
    const payload = await props.api.ciso.scans({ limit: 30 })
    scans.value = payload?.scans || []
    total.value = payload?.total || 0
  } catch (ex) {
    if (!silent) error.value = errorMessage(ex)
  } finally {
    if (!silent) loading.value = false
  }
  schedulePoll()
}

function schedulePoll() {
  stopPoll()
  if (running.value) {
    pollTimer = setTimeout(() => load({ silent: true }), 2000)
  }
}

function stopPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function startScan() {
  if (!props.api || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const payload = {
      scope: form.value.scope,
      max_files: Number(form.value.max_files) || 500
    }
    if (form.value.scope === 'paths') {
      payload.paths = form.value.paths
        .split('\n')
        .map(path => path.trim())
        .filter(Boolean)
      if (!payload.paths.length) {
        throw new Error('Enter at least one path for a "selected paths" scan')
      }
    }
    if (form.value.min_severity) payload.min_severity = form.value.min_severity
    const started = await props.api.ciso.startScan(payload)
    notice.value = `Scan ${started.scan_id} queued`
    await load({ silent: true })
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

async function cancelScan(scan) {
  if (!props.api || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await props.api.ciso.cancelScan(scan.scan_id)
    notice.value = `Cancellation requested for ${scan.scan_id}`
    await load({ silent: true })
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

async function deleteScan(scan) {
  if (!props.api || busy.value) return
  if (!window.confirm(`Delete scan ${scan.scan_id}? Its findings will be removed.`)) return
  busy.value = true
  error.value = ''
  try {
    await props.api.ciso.deleteScan(scan.scan_id)
    notice.value = `Scan ${scan.scan_id} deleted`
    await load({ silent: true })
    emit('changed')
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

function countsOf(scan, level) {
  return scan?.summary?.counts?.[level] || 0
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status
}

watch(() => props.api, () => load(), { immediate: false })

onMounted(load)
onUnmounted(stopPoll)
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="card bg-base-200/60 border border-base-300">
      <div class="card-body p-4 gap-3">
        <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
          New analysis
        </h3>
        <div class="grid grid-cols-1 sm:grid-cols-4 gap-2 items-end">
          <label class="flex flex-col gap-1">
            <span class="label-text text-xs mb-1">What to scan</span>
            <select v-model="form.scope" class="select select-sm select-bordered w-full">
              <option v-for="scope in statusOptions" :key="scope" :value="scope">
                {{ scopeLabel(scope) }}
              </option>
            </select>
          </label>
          <label class="flex flex-col gap-1">
            <span class="label-text text-xs mb-1">Max files</span>
            <input v-model.number="form.max_files" type="number" min="1" max="5000" class="input input-sm input-bordered w-full" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="label-text text-xs mb-1">Severity floor</span>
            <select v-model="form.min_severity" class="select select-sm select-bordered w-full">
              <option value="">All severities</option>
              <option v-for="level in SEVERITIES" :key="level" :value="level">
                {{ severityLabel(level) }}
              </option>
            </select>
          </label>
          <button class="btn btn-sm btn-primary" :disabled="busy || !api" @click="startScan">
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-play"></i>
            Run scan
          </button>
        </div>
        <label v-if="form.scope === 'paths'" class="flex flex-col gap-1">
          <span class="label-text text-xs mb-1">Paths (one per line, relative to the project)</span>
          <textarea
            v-model="form.paths"
            rows="3"
            class="textarea textarea-bordered textarea-sm w-full font-mono text-xs"
            placeholder="src/&#10;scripts/"
          ></textarea>
        </label>
        <p class="text-[11px] text-base-content/50">
          <strong>Working tree + untracked</strong> includes files no diff would show — the ones an agent
          creates and forgets. The scan is bounded by <em>max files</em> and runs in the background.
        </p>
      </div>
    </div>

    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>{{ error }}
    </div>
    <div v-if="notice" class="alert alert-success text-sm py-2">
      <i class="fa-solid fa-circle-check"></i>{{ notice }}
    </div>

    <div class="flex items-center justify-between gap-2">
      <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
        History <span class="text-base-content/40">({{ total }})</span>
      </h3>
      <button class="btn btn-xs btn-ghost" :disabled="loading" @click="load()">
        <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i> Refresh
      </button>
    </div>

    <div v-if="loading && !scans.length" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Loading scans…
    </div>

    <div v-else-if="!scans.length" class="text-sm text-base-content/60">
      No scans yet. Run the first analysis above.
    </div>

    <div v-else class="flex flex-col gap-2">
      <div
        v-for="scan in scans"
        :key="scan.scan_id"
        class="card bg-base-200/50 border border-base-300"
      >
        <div class="card-body p-3 gap-2">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="badge badge-sm" :class="statusClass(scan.status)">
              <span v-if="['queued', 'running'].includes(scan.status)" class="loading loading-spinner loading-xs mr-1"></span>
              {{ statusLabel(scan.status) }}
            </span>
            <span class="text-sm font-medium">{{ scopeLabel(scan.scope) }}</span>
            <code class="text-[11px] text-base-content/50">{{ scan.scan_id }}</code>
            <span class="text-xs text-base-content/50 ml-auto">
              {{ formatDate(scan.created_at) }} · {{ formatDuration(scan.duration_seconds) }}
            </span>
          </div>

          <div v-if="scan.summary" class="flex items-center gap-1.5 flex-wrap text-xs">
            <span
              v-for="level in SEVERITIES"
              :key="level"
              class="badge badge-xs"
              :class="severityClass(level)"
              :title="`${severityLabel(level)} findings`"
            >{{ severityLabel(level) }} {{ countsOf(scan, level) }}</span>
            <span class="text-base-content/60">
              {{ scan.summary.total_findings }} finding(s) · risk {{ scan.summary.risk_score }}
            </span>
            <span v-if="scan.summary.blocked" class="badge badge-xs badge-error">would block</span>
          </div>
          <div v-else-if="['queued', 'running'].includes(scan.status)" class="text-xs text-base-content/60">
            <progress class="progress progress-info w-full h-1.5" :value="scan.progress?.percent || 0" max="100"></progress>
            {{ scan.progress?.done || 0 }} / {{ scan.progress?.total || '…' }} files
          </div>
          <div v-else-if="scan.error" class="text-xs text-error">{{ scan.error }}</div>

          <div class="flex items-center gap-1.5 flex-wrap">
            <button
              v-if="scan.summary"
              class="btn btn-xs btn-outline"
              @click="emit('open-findings', scan.scan_id)"
            >
              <i class="fa-solid fa-list-check"></i> Findings
            </button>
            <button
              v-if="['queued', 'running'].includes(scan.status)"
              class="btn btn-xs btn-ghost text-warning"
              :disabled="busy"
              @click="cancelScan(scan)"
            >
              <i class="fa-solid fa-stop"></i> Cancel
            </button>
            <button
              v-else
              class="btn btn-xs btn-ghost text-error"
              :disabled="busy"
              @click="deleteScan(scan)"
            >
              <i class="fa-solid fa-trash-can"></i> Delete
            </button>
            <span v-if="scan.paths?.length" class="text-[11px] text-base-content/50 truncate">
              {{ scan.paths.slice(0, 3).join(', ') }}<span v-if="scan.paths.length > 3">…</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
