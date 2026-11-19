<script setup>
/**
 * CISO overview: the security posture of the active project at a glance.
 *
 * Answers the three questions a CISO opens the screen for:
 *   - how exposed are we today (score, severity mix, trend),
 *   - what is the platform gate doing (decisions of the last 7 days),
 *   - is the repository hygiene acceptable (tracked data plane, secret vault).
 *
 * The component is presentational: the parent owns the request and passes the
 * `/api/ciso/overview` payload. Actions are emitted, never performed here, so
 * the view stays a single source of navigation.
 */
import { computed } from 'vue'
import {
  SEVERITIES,
  actionClass,
  actionLabel,
  categoryLabel,
  formatAge,
  formatDate,
  formatDuration,
  severityClass,
  severityLabel,
  statusClass,
  scopeLabel
} from './format'

const props = defineProps({
  overview: { type: Object, default: null },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['run-scan', 'open-tab', 'refresh'])

const posture = computed(() => props.overview?.posture || null)
const latest = computed(() => props.overview?.latest_completed_scan || props.overview?.latest_scan || null)
const gates = computed(() => props.overview?.gates || {})
const secrets = computed(() => props.overview?.secrets || {})
const repo = computed(() => props.overview?.repo || {})
const policy = computed(() => props.overview?.policy || {})
const trend = computed(() => props.overview?.trend || [])

/** Counts keyed by severity, with every level present. */
const severityRows = computed(() => {
  const counts = posture.value?.counts || {}
  const max = Math.max(1, ...SEVERITIES.map(level => Number(counts[level] || 0)))
  return SEVERITIES.map(level => {
    const count = Number(counts[level] || 0)
    return {
      level,
      count,
      percent: Math.round((count * 100) / max)
    }
  })
})

/** Categories with at least one finding, highest first. */
const categoryRows = computed(() => {
  const counts = posture.value?.categories || {}
  return Object.entries(counts)
    .filter(([, count]) => Number(count) > 0)
    .sort((first, second) => Number(second[1]) - Number(first[1]))
    .map(([category, count]) => ({ category, count }))
})

const scoreClass = computed(() => {
  const score = posture.value?.score
  if (score == null) return 'text-base-content/40'
  if (score >= 90) return 'text-success'
  if (score >= 75) return 'text-info'
  if (score >= 60) return 'text-warning'
  return 'text-error'
})

const repoHealthy = computed(
  () => repo.value?.checked === true && repo.value?.ignore_rules_ok !== false &&
    !(repo.value?.tracked_data_plane_count > 0)
)

const repoTitle = computed(() => {
  if (!repo.value?.checked) return repo.value?.errors?.[0] || 'Not checked'
  if (repo.value?.tracked_data_plane_count > 0) {
    return `${repo.value.tracked_data_plane_count} data-plane file(s) tracked by git`
  }
  if (repo.value?.ignore_rules_ok === false) {
    return `${repo.value.missing_rules?.length || 0} .gitignore rule(s) missing`
  }
  return 'Data plane ignored and untracked'
})

/** Gate actions in the order they matter to a human. */
const gateRows = computed(() => {
  const actions = gates.value.actions || {}
  return ['block', 'require_approval', 'redact', 'warn', 'allow']
    .map(action => ({ action, count: Number(actions[action] || 0) }))
    .filter(row => row.count > 0)
})

/** Sparkline of the last completed scans (0..100). */
const trendPoints = computed(() => {
  const points = trend.value.filter(point => point.score != null)
  if (!points.length) return ''
  const width = 100
  const height = 28
  const pad = 2
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0
  return points
    .map((point, index) => {
      const x = pad + index * step
      const y = height - pad - (Math.min(100, Math.max(0, Number(point.score))) / 100) * (height - pad * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

const latestCounts = computed(() => latest.value?.summary?.counts || {})

const secretStatuses = computed(() =>
  Object.entries(secrets.value?.statuses || {}).map(([status, count]) => ({ status, count }))
)

function scanFindings(scanId) {
  emit('open-tab', 'findings', { scanId })
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div v-if="loading && !overview" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span>
      Loading security posture…
    </div>

    <div v-else-if="!overview" class="alert alert-warning text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>
      No security data available for this project.
    </div>

    <template v-else>
      <!-- Top row: posture + latest scan + quick actions -------------------->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div class="card bg-base-200/60 border border-base-300 lg:col-span-1">
          <div class="card-body p-4 gap-3">
            <div class="flex items-center justify-between gap-2">
              <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
                Posture
              </h3>
              <span
                v-if="posture?.available"
                class="badge badge-outline"
                :class="actionClass(posture.action)"
                :title="`Last decision: ${actionLabel(posture.action)}`"
              >{{ actionLabel(posture.action) }}</span>
            </div>
            <div class="flex items-center gap-4">
              <div
                class="radial-progress shrink-0"
                :class="scoreClass"
                :style="{ '--value': posture?.available ? posture.score : 0, '--size': '6rem', '--thickness': '0.45rem' }"
                role="progressbar"
              >
                <span class="text-2xl font-bold">{{ posture?.available ? posture.score : '—' }}</span>
                <span class="text-[10px] uppercase opacity-70">
                  {{ posture?.available ? `grade ${posture.grade}` : 'no scan' }}
                </span>
              </div>
              <div class="min-w-0 flex flex-col gap-1 text-xs">
                <div v-if="posture?.available" class="flex items-center gap-1.5">
                  <span class="badge badge-sm" :class="severityClass(posture.highest_severity)">
                    {{ severityLabel(posture.highest_severity) }}
                  </span>
                  <span class="text-base-content/60">highest severity</span>
                </div>
                <div class="text-base-content/70">
                  <strong class="text-base-content">{{ posture?.total_findings || 0 }}</strong>
                  finding(s) in the latest completed scan
                </div>
                <div class="text-base-content/60">
                  Risk score {{ posture?.risk_score ?? '—' }} · policy
                  <span :class="policy.enabled ? 'text-success' : 'text-error'">
                    {{ policy.enabled ? 'enabled' : 'disabled' }}
                  </span>
                </div>
              </div>
            </div>
            <div v-if="!posture?.available" class="text-xs text-base-content/60">
              Run the first analysis to compute a posture score.
            </div>
            <div v-else class="flex flex-wrap gap-1.5">
              <button class="btn btn-xs btn-primary" @click="emit('run-scan', 'project')">
                <i class="fa-solid fa-magnifying-glass"></i> Scan project
              </button>
              <button class="btn btn-xs btn-ghost" @click="emit('run-scan', 'changes')">
                <i class="fa-solid fa-code-compare"></i> Scan changes
              </button>
            </div>
          </div>
        </div>

        <div class="card bg-base-200/60 border border-base-300 lg:col-span-2">
          <div class="card-body p-4 gap-3">
            <div class="flex items-center justify-between gap-2">
              <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
                Latest scan
              </h3>
              <button class="btn btn-xs btn-ghost" :disabled="loading" title="Refresh" @click="emit('refresh')">
                <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
              </button>
            </div>
            <div v-if="latest">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="badge badge-sm" :class="statusClass(latest.status)">
                  {{ latest.status }}
                </span>
                <span class="text-sm font-medium">{{ scopeLabel(latest.scope) }}</span>
                <span class="text-xs text-base-content/60">
                  {{ formatDate(latest.created_at) }} · {{ formatDuration(latest.duration_seconds) }}
                </span>
                <span v-if="latest.findings_truncated" class="badge badge-xs badge-warning">
                  findings list truncated
                </span>
              </div>
              <div class="mt-2 flex items-center gap-2 flex-wrap text-xs">
                <span
                  v-for="level in SEVERITIES"
                  :key="level"
                  class="badge badge-sm"
                  :class="severityClass(level)"
                >{{ severityLabel(level) }} {{ latestCounts[level] || 0 }}</span>
                <span class="text-base-content/60">{{ latest.summary?.files_scanned || 0 }} file(s) scanned</span>
              </div>
              <div class="mt-3 flex flex-wrap gap-1.5">
                <button class="btn btn-xs btn-outline" @click="scanFindings(latest.scan_id)">
                  <i class="fa-solid fa-list-check"></i> Review findings
                </button>
                <button class="btn btn-xs btn-ghost" @click="emit('open-tab', 'scans')">
                  All scans
                </button>
              </div>
            </div>
            <div v-else class="text-sm text-base-content/60">
              No scan has run yet.
              <button class="btn btn-xs btn-primary ml-2" @click="emit('run-scan', 'project')">
                Run the first scan
              </button>
            </div>

            <!-- Trend -->
            <div v-if="trend.length > 1" class="mt-1">
              <div class="text-[11px] uppercase tracking-wide text-base-content/50 mb-1">
                Score trend (last {{ trend.length }} scans)
              </div>
              <svg class="w-full h-10" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
                <line x1="0" y1="26" x2="100" y2="26" class="stroke-base-300" stroke-width="0.3" />
                <polyline
                  :points="trendPoints"
                  fill="none"
                  class="stroke-primary"
                  stroke-width="1.2"
                  stroke-linejoin="round"
                  stroke-linecap="round"
                />
              </svg>
            </div>
          </div>
        </div>
      </div>

      <!-- Severity + categories --------------------------------------------->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div class="card bg-base-200/60 border border-base-300">
          <div class="card-body p-4 gap-3">
            <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
              Findings by severity
            </h3>
            <div v-for="row in severityRows" :key="row.level" class="flex items-center gap-2 text-xs">
              <span class="w-16 shrink-0">
                <span class="badge badge-xs" :class="severityClass(row.level)">{{ severityLabel(row.level) }}</span>
              </span>
              <div class="flex-1 h-2 rounded bg-base-300/60 overflow-hidden">
                <div
                  class="h-full"
                  :class="{
                    'bg-error': row.level === 'critical',
                    'bg-warning': row.level === 'high',
                    'bg-info': row.level === 'medium',
                    'bg-base-content/40': row.level === 'low',
                    'bg-base-content/20': row.level === 'info'
                  }"
                  :style="{ width: `${row.percent}%` }"
                ></div>
              </div>
              <span class="w-8 text-right tabular-nums">{{ row.count }}</span>
            </div>

            <div v-if="categoryRows.length" class="mt-1">
              <div class="text-[11px] uppercase tracking-wide text-base-content/50 mb-1">By category</div>
              <div class="flex flex-wrap gap-1.5">
                <span v-for="row in categoryRows" :key="row.category" class="badge badge-outline badge-sm">
                  {{ categoryLabel(row.category) }} <span class="opacity-60">{{ row.count }}</span>
                </span>
              </div>
            </div>
            <div v-else class="text-xs text-base-content/60">
              No findings recorded in the latest scan.
            </div>
          </div>
        </div>

        <!-- Gates -->
        <div class="card bg-base-200/60 border border-base-300">
          <div class="card-body p-4 gap-3">
            <div class="flex items-center justify-between gap-2">
              <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
                Gates · last {{ gates.window_hours ? Math.round(gates.window_hours / 24) : 7 }} days
              </h3>
              <button class="btn btn-xs btn-ghost" @click="emit('open-tab', 'activity')">
                Activity
              </button>
            </div>
            <div class="grid grid-cols-3 gap-2 text-center">
              <div class="rounded bg-base-300/40 p-2">
                <div class="text-lg font-semibold tabular-nums">{{ gates.total || 0 }}</div>
                <div class="text-[10px] uppercase text-base-content/50">decisions</div>
              </div>
              <div class="rounded bg-base-300/40 p-2">
                <div class="text-lg font-semibold tabular-nums" :class="gates.blocked ? 'text-error' : ''">
                  {{ gates.blocked || 0 }}
                </div>
                <div class="text-[10px] uppercase text-base-content/50">blocked</div>
              </div>
              <div class="rounded bg-base-300/40 p-2">
                <div class="text-lg font-semibold tabular-nums">{{ policy.rules?.ignored || 0 }}</div>
                <div class="text-[10px] uppercase text-base-content/50">rules silenced</div>
              </div>
            </div>
            <div v-if="gateRows.length" class="flex flex-wrap gap-1.5">
              <span v-for="row in gateRows" :key="row.action" class="badge badge-sm" :class="actionClass(row.action)">
                {{ actionLabel(row.action) }} {{ row.count }}
              </span>
            </div>
            <div v-else class="text-xs text-base-content/60">
              No gate decision recorded in the window. Gates run on writes, commits, commands and model output.
            </div>
            <div v-if="gates.top_rules?.length" class="text-xs">
              <div class="text-[11px] uppercase tracking-wide text-base-content/50 mb-1">Most triggered rules</div>
              <ul class="space-y-0.5">
                <li v-for="row in gates.top_rules" :key="row.rule_id" class="flex justify-between gap-2">
                  <code class="truncate text-[11px]">{{ row.rule_id }}</code>
                  <span class="tabular-nums text-base-content/60">{{ row.count }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      <!-- Secrets + repository hygiene -------------------------------------->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div class="card bg-base-200/60 border border-base-300">
          <div class="card-body p-4 gap-2">
            <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
              Secret vault
            </h3>
            <div v-if="secrets.available" class="flex items-center gap-3 flex-wrap text-xs">
              <span class="badge badge-sm badge-ghost">{{ secrets.total }} declared</span>
              <span class="badge badge-sm" :class="secrets.ready ? 'badge-success' : 'badge-ghost'">
                {{ secrets.ready }} ready
              </span>
              <span v-if="secrets.pending" class="badge badge-sm badge-warning">
                {{ secrets.pending }} pending
              </span>
              <span v-for="row in secretStatuses" :key="row.status" class="text-base-content/60">
                {{ row.status }}: {{ row.count }}
              </span>
            </div>
            <div v-else class="text-xs text-warning">
              The secret inventory is unavailable: {{ secrets.error || 'unknown error' }}
            </div>
            <div class="text-[11px] text-base-content/50">
              Only names and availability are reported: the CISO view never reads a secret value.
            </div>
          </div>
        </div>

        <div class="card bg-base-200/60 border border-base-300">
          <div class="card-body p-4 gap-2">
            <div class="flex items-center justify-between gap-2">
              <h3 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">
                Repository hygiene
              </h3>
              <span class="badge badge-sm" :class="repoHealthy ? 'badge-success' : 'badge-warning'">
                {{ repoHealthy ? 'clean' : 'needs attention' }}
              </span>
            </div>
            <div class="text-xs text-base-content/70">{{ repoTitle }}</div>
            <div v-if="repo.tracked_data_plane_count" class="text-xs text-error">
              Tracked: {{ (repo.tracked_data_plane || []).slice(0, 5).join(', ') }}
              <span v-if="repo.tracked_data_plane_count > 5">…</span>
            </div>
            <div v-if="repo.missing_rules?.length" class="text-xs text-warning">
              Missing ignore rules: {{ repo.missing_rules.slice(0, 5).join(', ') }}
              <span v-if="repo.missing_rules.length > 5">…</span>
            </div>
            <div v-if="repo.errors?.length" class="text-xs text-base-content/50">
              {{ repo.errors[0] }}
            </div>
          </div>
        </div>
      </div>

      <div class="text-[11px] text-base-content/40 text-right">
        Generated {{ formatAge(overview.generated_at) }} · thresholds:
        block ≥ {{ policy.block_above }}, approval ≥ {{ policy.approval_above }},
        warn ≥ {{ policy.warn_above }}
      </div>
    </template>
  </div>
</template>
