<script setup>
/**
 * CISO activity: the gate journal.
 *
 * Every CISO gate appends a `ciso_verdict` entry to the harness audit journal
 * (`.codx/harness/audit.jsonl`): file write, commit, command, container,
 * knowledge ingestion, model output… The evidence is stored redacted, so this
 * panel can show *what* was detected and *where* without reproducing a secret.
 *
 * Read-only by design: a verdict is a fact, not something the UI can edit.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  SEVERITIES,
  actionClass,
  actionLabel,
  domainLabel,
  formatDate,
  formatAge,
  severityClass,
  severityLabel
} from './format'

const props = defineProps({
  api: { type: Object, default: null }
})

const entries = ref([])
const stats = ref({})
const loading = ref(false)
const error = ref('')
const limit = ref(100)
let timer = null

const actionRows = computed(() => {
  const actions = stats.value?.actions || {}
  return Object.entries(actions)
    .filter(([, count]) => Number(count) > 0)
    .sort((first, second) => Number(second[1]) - Number(first[1]))
    .map(([action, count]) => ({ action, count }))
})

const severityRows = computed(() => {
  const counts = stats.value?.severities || {}
  return SEVERITIES.map(level => ({ level, count: Number(counts[level] || 0) }))
})

const domainRows = computed(() =>
  Object.entries(stats.value?.domains || {})
    .sort((first, second) => Number(second[1]) - Number(first[1]))
    .map(([domain, count]) => ({ domain, count }))
)

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

async function load({ silent = false } = {}) {
  if (!props.api) return
  if (!silent) loading.value = true
  error.value = ''
  try {
    const payload = await props.api.ciso.activity({ limit: limit.value })
    entries.value = payload?.entries || []
    stats.value = payload?.stats || {}
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    if (!silent) loading.value = false
  }
  schedule()
}

function schedule() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => load({ silent: true }), 15000)
}

onMounted(load)
onUnmounted(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="flex flex-col gap-3">
    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>{{ error }}
    </div>

    <div class="flex items-center justify-between gap-2">
      <div class="text-xs text-base-content/60">
        Window: last {{ stats.window_hours ? Math.round(stats.window_hours / 24) : 7 }} days
        <span v-if="stats.last_event_at"> · last decision {{ formatAge(stats.last_event_at) }}</span>
      </div>
      <div class="flex items-center gap-1">
        <select v-model.number="limit" class="select select-xs select-bordered" @change="load()">
          <option :value="50">50</option>
          <option :value="100">100</option>
          <option :value="200">200</option>
          <option :value="500">500</option>
        </select>
        <button class="btn btn-xs btn-ghost" :disabled="loading" @click="load()">
          <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
        </button>
      </div>
    </div>

    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
      <div class="rounded bg-base-200/60 border border-base-300 p-2">
        <div class="text-lg font-semibold tabular-nums">{{ stats.total || 0 }}</div>
        <div class="text-[10px] uppercase text-base-content/50">decisions</div>
      </div>
      <div class="rounded bg-base-200/60 border border-base-300 p-2">
        <div class="text-lg font-semibold tabular-nums" :class="stats.blocked ? 'text-error' : 'text-success'">
          {{ stats.blocked || 0 }}
        </div>
        <div class="text-[10px] uppercase text-base-content/50">blocked</div>
      </div>
      <div class="rounded bg-base-200/60 border border-base-300 p-2">
        <div class="text-lg font-semibold tabular-nums">
          {{ stats.actions?.require_approval || 0 }}
        </div>
        <div class="text-[10px] uppercase text-base-content/50">need approval</div>
      </div>
      <div class="rounded bg-base-200/60 border border-base-300 p-2">
        <div class="text-lg font-semibold tabular-nums">{{ stats.actions?.redact || 0 }}</div>
        <div class="text-[10px] uppercase text-base-content/50">redacted</div>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div class="card bg-base-200/60 border border-base-300">
        <div class="card-body p-3 gap-2">
          <h3 class="font-semibold text-xs uppercase tracking-wide text-base-content/60">Decisions by action</h3>
          <div v-if="actionRows.length" class="flex flex-wrap gap-1.5">
            <span
              v-for="row in actionRows"
              :key="row.action"
              class="badge badge-sm"
              :class="actionClass(row.action)"
            >{{ actionLabel(row.action) }} {{ row.count }}</span>
          </div>
          <div v-else class="text-xs text-base-content/50">No decisions in the window.</div>
          <div class="flex flex-wrap gap-1.5">
            <span
              v-for="row in severityRows.filter(item => item.count)"
              :key="row.level"
              class="badge badge-xs"
              :class="severityClass(row.level)"
            >{{ severityLabel(row.level) }} {{ row.count }}</span>
          </div>
        </div>
      </div>

      <div class="card bg-base-200/60 border border-base-300">
        <div class="card-body p-3 gap-2">
          <h3 class="font-semibold text-xs uppercase tracking-wide text-base-content/60">Where gates fire</h3>
          <div v-if="domainRows.length" class="flex flex-wrap gap-1.5">
            <span v-for="row in domainRows" :key="row.domain" class="badge badge-ghost badge-sm">
              {{ domainLabel(row.domain) }} {{ row.count }}
            </span>
          </div>
          <div v-else class="text-xs text-base-content/50">No gate activity recorded yet.</div>
          <div v-if="stats.top_rules?.length" class="text-xs">
            <div class="text-[10px] uppercase text-base-content/50 mb-1">Top rules</div>
            <div class="flex flex-wrap gap-1">
              <code v-for="row in stats.top_rules" :key="row.rule_id" class="text-[10px] bg-base-300/40 rounded px-1">
                {{ row.rule_id }} ({{ row.count }})
              </code>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loading && !entries.length" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Loading gate journal…
    </div>

    <div v-else-if="!entries.length" class="alert alert-info text-sm">
      <i class="fa-solid fa-circle-info"></i>
      No gate decision recorded. Gates run automatically on agent file writes, commits, shell commands,
      container operations, knowledge ingestion and model output.
    </div>

    <div v-else class="overflow-x-auto rounded border border-base-300">
      <table class="table table-xs">
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Gate</th>
            <th>Subject</th>
            <th class="hidden md:table-cell">Findings</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(entry, index) in entries" :key="`${entry.ts}-${index}`">
            <td class="whitespace-nowrap text-[11px]">{{ formatDate(entry.ts) }}</td>
            <td>
              <span class="badge badge-xs" :class="actionClass(entry.action)">{{ actionLabel(entry.action) }}</span>
            </td>
            <td class="text-[11px]">{{ domainLabel(entry.domain) }}</td>
            <td class="text-[11px] font-mono max-w-64 truncate" :title="entry.subject">{{ entry.subject || '—' }}</td>
            <td class="hidden md:table-cell">
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="finding in (entry.findings || []).slice(0, 4)"
                  :key="finding.rule"
                  class="badge badge-xs"
                  :class="severityClass(finding.severity)"
                  :title="`${finding.category}: ${finding.path || ''}${finding.line ? ':' + finding.line : ''}`"
                >{{ finding.rule }}</span>
                <span v-if="(entry.findings || []).length > 4" class="text-[10px] text-base-content/50">
                  +{{ entry.findings.length - 4 }}
                </span>
                <span v-if="!(entry.findings || []).length" class="text-[10px] text-base-content/40">clean</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
