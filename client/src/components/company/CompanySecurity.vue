<script setup>
/**
 * Company security panel.
 *
 * CISO is project-scoped, but a company owns several projects: this panel gives
 * the company shell one cross-project view of their security posture. It reads
 * the same `/api/ciso/overview` data the CISO screen uses, aggregated by
 * `GET /api/companies/{id}/security`.
 *
 * Actions stay explicit:
 *  - Open CISO → the full project view (findings, scans, rules, activity),
 *  - Scan project / changes → start a background scan for that project.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { API } from '@/api/api'
import { formatDate, formatAge, severityClass } from '@/components/ciso/format'

const props = defineProps({
  companyId: { type: String, default: '' }
})

const api = API.companies
const loading = ref(false)
const error = ref('')
const payload = ref(null)
const scanning = ref('')
const notice = ref('')
let pollTimer = null

const rows = computed(() => payload.value?.projects || [])
const totals = computed(() => payload.value?.totals || {})
const hasRows = computed(() => rows.value.length > 0)

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

function scoreClass(row) {
  const score = row?.score
  if (score == null) return 'badge-ghost'
  if (score >= 90) return 'badge-success'
  if (score >= 75) return 'badge-info'
  if (score >= 60) return 'badge-warning'
  return 'badge-error'
}

function cisoUrl(row, extra = '') {
  const name = encodeURIComponent(row?.project_name || row?.name || '')
  return `/${name}/ciso${extra}`
}

async function load() {
  if (!props.companyId) {
    payload.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    payload.value = await api.security(props.companyId)
  } catch (ex) {
    error.value = `Could not load company security: ${errorMessage(ex)}`
  } finally {
    loading.value = false
  }
}

function openCiso(row) {
  window.location.href = cisoUrl(row)
}

function openFindings(row) {
  if (!row?.scan_id) return
  window.location.href = cisoUrl(row, `?tab=findings&scan=${encodeURIComponent(row.scan_id)}`)
}

async function scan(row, scope) {
  if (!row?.project_id || scanning.value) return
  scanning.value = row.project_id
  error.value = ''
  notice.value = ''
  try {
    const started = await api.scanSecurity(props.companyId, {
      projectId: row.project_id,
      scope,
      maxFiles: 300
    })
    notice.value = `Scan started for ${row.name || row.project_name} (${scope}).`
    await load()
    schedulePoll(8)
  } catch (ex) {
    error.value = `Could not start the scan: ${errorMessage(ex)}`
  } finally {
    scanning.value = ''
  }
}

function schedulePoll(attempts = 5) {
  if (pollTimer) clearTimeout(pollTimer)
  let remaining = attempts
  const tick = async () => {
    await load()
    remaining -= 1
    if (remaining > 0) {
      pollTimer = setTimeout(tick, 4000)
    }
  }
  pollTimer = setTimeout(tick, 4000)
}

watch(() => props.companyId, () => load())
onMounted(load)
onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="flex items-center gap-2 text-lg font-black">
          <i class="fa-solid fa-shield-halved text-primary"></i> Security
        </h2>
        <p class="text-xs text-base-content/50">
          CISO posture of the company's linked projects. One row per project; open
          the project CISO to review findings, rules and scans.
        </p>
      </div>
      <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
        <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i> Refresh
      </button>
    </header>

    <div v-if="error" class="alert alert-error text-sm py-2">
      <i class="fa-solid fa-triangle-exclamation"></i> {{ error }}
    </div>
    <div v-if="notice" class="alert alert-success text-sm py-2">
      <i class="fa-solid fa-circle-check"></i> {{ notice }}
    </div>

    <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-3">
        <div class="text-[10px] uppercase tracking-wide text-base-content/40">Projects</div>
        <div class="text-xl font-black tabular-nums">{{ totals.projects || 0 }}</div>
        <div class="text-[10px] text-base-content/40">{{ totals.available || 0 }} readable</div>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-3">
        <div class="text-[10px] uppercase tracking-wide text-base-content/40">Critical</div>
        <div class="text-xl font-black tabular-nums" :class="totals.critical ? 'text-error' : ''">
          {{ totals.critical || 0 }}
        </div>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-3">
        <div class="text-[10px] uppercase tracking-wide text-base-content/40">High</div>
        <div class="text-xl font-black tabular-nums" :class="totals.high ? 'text-warning' : ''">
          {{ totals.high || 0 }}
        </div>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-3">
        <div class="text-[10px] uppercase tracking-wide text-base-content/40">Gates blocked</div>
        <div class="text-xl font-black tabular-nums" :class="totals.gates_blocked ? 'text-error' : ''">
          {{ totals.gates_blocked || 0 }}
        </div>
        <div class="text-[10px] text-base-content/40">last 7 days</div>
      </div>
    </div>

    <div v-if="loading && !hasRows" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Reading project security…
    </div>

    <div v-else-if="!hasRows" class="rounded-2xl border border-dashed border-base-content/20 p-8 text-center">
      <i class="fa-solid fa-cubes text-3xl text-base-content/20 block mb-2"></i>
      <p class="text-sm text-base-content/50">No projects are linked to this company yet.</p>
      <p class="mt-1 text-xs text-base-content/40">Link a project in <strong>Work → Projects</strong> to see its CISO posture.</p>
    </div>

    <div v-else class="grid gap-3 md:grid-cols-2">
      <article
        v-for="row in rows"
        :key="row.project_id"
        class="rounded-2xl border border-base-content/10 bg-base-100 p-4 flex flex-col gap-3"
      >
        <div class="flex items-start gap-2">
          <div class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
            <i class="fa-solid fa-cube"></i>
          </div>
          <div class="min-w-0 flex-1">
            <h3 class="truncate font-bold">{{ row.name || row.project_name }}</h3>
            <p class="truncate font-mono text-[10px] text-base-content/40" :title="row.codx_path">
              {{ row.codx_path || 'not discovered' }}
            </p>
          </div>
          <span v-if="row.available" class="badge badge-sm" :class="scoreClass(row)" :title="`risk ${row.risk_score ?? '—'}`">
            {{ row.score ?? '—' }} · {{ row.grade || '—' }}
          </span>
          <span v-else class="badge badge-sm badge-ghost">unavailable</span>
        </div>

        <div v-if="row.available" class="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span class="badge badge-xs" :class="severityClass('critical')">Critical {{ row.critical }}</span>
          <span class="badge badge-xs" :class="severityClass('high')">High {{ row.high }}</span>
          <span class="badge badge-xs badge-ghost">Medium {{ row.medium }}</span>
          <span v-if="row.blocked" class="badge badge-xs badge-error">policy would block</span>
          <span class="badge badge-xs" :class="row.policy_enabled ? 'badge-success badge-outline' : 'badge-warning'">
            policy {{ row.policy_enabled ? 'on' : 'off' }}
          </span>
        </div>
        <div v-if="row.available" class="text-[11px] text-base-content/50">
          <template v-if="row.scan_id">
            Last scan: {{ formatDate(row.scan_created_at) }}
            <span class="badge badge-xs badge-ghost ml-1">{{ row.scan_status }}</span>
            <span class="ml-1">{{ row.total_findings }} finding(s)</span>
          </template>
          <template v-else>No completed scan yet.</template>
          <span class="ml-1">· gates {{ row.gates_total }} ({{ row.gates_blocked }} blocked)</span>
        </div>
        <div v-if="row.error" class="text-[11px] text-warning">{{ row.error }}</div>

        <div class="mt-auto flex flex-wrap items-center gap-1.5">
          <button class="btn btn-xs btn-primary" :disabled="!row.available" @click="openCiso(row)">
            <i class="fa-solid fa-arrow-up-right-from-square"></i> Open CISO
          </button>
          <button
            class="btn btn-xs btn-outline"
            :disabled="!row.available || !!scanning"
            @click="scan(row, 'project')"
          >
            <span v-if="scanning === row.project_id" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-magnifying-glass"></i> Scan project
          </button>
          <button
            class="btn btn-xs btn-ghost"
            :disabled="!row.available || !!scanning"
            @click="scan(row, 'changes')"
          >
            <i class="fa-solid fa-code-compare"></i> Scan changes
          </button>
          <button
            v-if="row.scan_id"
            class="btn btn-xs btn-ghost"
            @click="openFindings(row)"
          >
            Findings
          </button>
        </div>
      </article>
    </div>

    <p class="text-[10px] text-base-content/40">
      Data as of {{ formatAge(payload?.generated_at) }} · scans are deterministic and run on the project API.
    </p>
  </div>
</template>
