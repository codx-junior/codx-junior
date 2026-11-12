<template>
  <div class="card bg-base-100 shadow">
    <div class="card-body gap-3 p-3">

      <!-- Header: what this panel is + the size of the analysed window -->
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
          <i class="fa-solid fa-chart-simple text-[11px] opacity-60"></i>
          API analysis
        </h3>
        <span class="badge badge-sm badge-ghost font-mono">
          {{ formatNumber(summary.requests) }} requests
        </span>
        <span
          class="badge badge-sm font-mono"
          :class="summary.errors ? 'badge-error' : 'badge-ghost'"
        >
          {{ formatNumber(summary.errors) }} errors
          <template v-if="summary.requests">· {{ errorRateLabel }}</template>
        </span>
        <span class="badge badge-sm badge-ghost font-mono" title="p95 latency">
          p95 {{ formatMs(summary.p95_ms) }}
        </span>
        <span class="badge badge-sm badge-ghost font-mono" title="slowest request">
          max {{ formatMs(summary.max_ms) }}
        </span>
        <span
          v-if="insights.stats && insights.stats.truncated"
          class="badge badge-sm badge-warning gap-1"
          title="Only the tail window of the log was scanned"
        >
          <i class="fa-solid fa-triangle-exclamation text-[10px]"></i>
          partial window
        </span>
        <span class="ml-auto font-mono text-[10px] opacity-50">
          {{ formatNumber(insights.stats && insights.stats.matched) }} entries scanned
        </span>
      </div>

      <!-- Failure state -->
      <div v-if="error" class="flex items-center gap-2 rounded-box bg-error/10 px-2 py-1 text-xs text-error">
        <i class="fa-solid fa-triangle-exclamation text-[11px]"></i>
        <span class="truncate">{{ error }}</span>
      </div>

      <!-- Latency over time (same click-to-zoom as the volume histogram) -->
      <LogHistogram :histogram="histogram" :loading="loading" @zoom="onZoom" />

      <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">

        <!-- Top endpoints -->
        <section class="rounded-box border border-base-300">
          <header class="flex items-center justify-between border-b border-base-300 bg-base-200/60 px-2 py-1">
            <span class="font-mono text-[10px] uppercase tracking-wide opacity-60">top endpoints</span>
            <span class="font-mono text-[10px] opacity-40">click to filter</span>
          </header>
          <p v-if="!endpoints.length" class="px-2 py-3 text-center text-xs opacity-50">
            No API request entries in this window.
          </p>
          <table v-else class="table table-xs table-pin-rows">
            <tbody>
              <tr
                v-for="endpoint in endpoints"
                :key="endpoint.endpoint"
                class="cursor-pointer hover:bg-base-200"
                :title="`Filter the log by ${endpoint.endpoint}`"
                @click="onFilterEndpoint(endpoint)"
              >
                <td class="w-14">
                  <span class="badge badge-xs font-mono" :class="methodClass(endpoint.method)">
                    {{ endpoint.method || '·' }}
                  </span>
                </td>
                <td class="max-w-[16rem] truncate font-mono text-[11px]" :title="endpoint.path">
                  {{ endpoint.path || endpoint.endpoint }}
                </td>
                <td class="w-12 text-right font-mono text-[11px]">{{ formatNumber(endpoint.count) }}</td>
                <td class="w-10 text-right font-mono text-[11px]" :class="endpoint.errors ? 'text-error' : 'opacity-40'">
                  {{ formatNumber(endpoint.errors) }}
                </td>
                <td class="w-16 text-right font-mono text-[11px] opacity-70">{{ formatMs(endpoint.p95_ms) }}</td>
                <td class="w-20">
                  <div class="h-1.5 w-full rounded bg-base-300">
                    <div class="h-1.5 rounded bg-primary" :style="{ width: barWidth(endpoint.p95_ms) }"></div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- Slowest requests -->
        <section class="rounded-box border border-base-300">
          <header class="flex items-center justify-between border-b border-base-300 bg-base-200/60 px-2 py-1">
            <span class="font-mono text-[10px] uppercase tracking-wide opacity-60">slowest requests</span>
            <span class="font-mono text-[10px] opacity-40">click to inspect</span>
          </header>
          <p v-if="!slowest.length" class="px-2 py-3 text-center text-xs opacity-50">
            No timed request found.
          </p>
          <ul v-else class="divide-y divide-base-200">
            <li
              v-for="item in slowest"
              :key="item.id"
              class="flex cursor-pointer items-center gap-2 px-2 py-1 hover:bg-base-200"
              @click="$emit('open-entry', item.id)"
            >
              <span class="badge badge-xs font-mono" :class="methodClass(item.method)">{{ item.method || '·' }}</span>
              <span class="min-w-0 flex-1 truncate font-mono text-[11px]" :title="item.url">{{ item.path }}</span>
              <span
                v-if="item.status"
                class="badge badge-xs font-mono"
                :class="item.status >= 500 ? 'badge-error' : (item.status >= 400 ? 'badge-warning' : 'badge-ghost')"
              >{{ item.status }}</span>
              <span class="w-20 text-right font-mono text-[11px]" :class="item.duration_ms >= 500 ? 'text-error' : ''">
                {{ formatMs(item.duration_ms) }}
              </span>
              <span class="w-14 text-right font-mono text-[10px] opacity-40">{{ shortTime(item.ts_ms) }}</span>
            </li>
          </ul>
        </section>
      </div>

      <!-- Status codes + profiler -->
      <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <section class="rounded-box border border-base-300 p-2">
          <p class="mb-1 font-mono text-[10px] uppercase tracking-wide opacity-60">status codes</p>
          <p v-if="!status.length" class="text-xs opacity-50">No HTTP status in this window.</p>
          <div v-else class="flex flex-wrap gap-1">
            <span
              v-for="entry in status"
              :key="entry.value"
              class="badge badge-sm gap-1 font-mono"
              :class="statusClass(entry.value)"
            >
              {{ entry.value }}
              <span class="opacity-60">{{ formatNumber(entry.count) }}</span>
            </span>
          </div>
        </section>

        <section class="rounded-box border border-base-300 p-2">
          <p class="mb-1 font-mono text-[10px] uppercase tracking-wide opacity-60">profiler · top methods</p>
          <p v-if="!profiler.length" class="text-xs opacity-50">No profiler entry in this window.</p>
          <ul v-else class="space-y-0.5">
            <li
              v-for="item in profiler"
              :key="item.method"
              class="flex items-center gap-2 font-mono text-[11px]"
            >
              <span class="min-w-0 flex-1 truncate" :title="item.method">{{ item.method }}</span>
              <span class="opacity-50">{{ formatNumber(item.count) }}x</span>
              <span class="w-16 text-right">{{ formatMs(item.avg_ms) }}</span>
              <span class="w-16 text-right opacity-60">p95 {{ formatMs(item.p95_ms) }}</span>
            </li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<script>
import LogHistogram from './LogHistogram.vue'

/** Zeroed summary, used when the payload is still empty. */
const EMPTY_SUMMARY = {
  requests: 0,
  errors: 0,
  error_rate: 0,
  avg_ms: 0,
  p50_ms: 0,
  p95_ms: 0,
  p99_ms: 0,
  max_ms: 0,
}

/** Tailwind badge class per HTTP method. */
const METHOD_CLASSES = {
  GET: 'badge-info',
  POST: 'badge-success',
  PUT: 'badge-warning',
  PATCH: 'badge-warning',
  DELETE: 'badge-error',
}

/**
 * API analysis panel.
 *
 * Renders the `/api/logs/explorer/insights` payload: request/error KPIs,
 * latency percentiles, the latency histogram (clicking a bucket zooms the time
 * range, exactly like the volume histogram), the busiest endpoints, the slowest
 * requests, the status distribution and the profiler summary.
 *
 * Presentational only: props in, intent out. Log-derived strings are always
 * interpolated (`{{ }}`), never rendered as HTML.
 */
export default {
  name: 'LogApiInsights',
  components: { LogHistogram },
  emits: ['open-entry', 'filter-endpoint', 'zoom'],
  props: {
    insights: { type: Object, default: () => ({}) },
    loading: { type: Boolean, default: false },
    error: { type: String, default: '' },
  },
  computed: {
    summary() {
      return { ...EMPTY_SUMMARY, ...(this.insights.summary || {}) }
    },
    histogram() {
      return this.insights.histogram || []
    },
    endpoints() {
      return this.insights.endpoints || []
    },
    slowest() {
      return this.insights.slowest || []
    },
    status() {
      return this.insights.status || []
    },
    profiler() {
      return this.insights.profiler || []
    },
    errorRateLabel() {
      return `${(this.summary.error_rate * 100).toFixed(1)}%`
    },
    /** Slowest p95 of the table: the 100% width of the per-row bars. */
    maxP95() {
      return Math.max(1, ...this.endpoints.map((item) => Number(item.p95_ms) || 0))
    },
  },
  methods: {
    onZoom(ts, bucketMs) {
      this.$emit('zoom', ts, bucketMs)
    },
    /** Emit an exact-phrase term that narrows the list to this endpoint. */
    onFilterEndpoint(endpoint) {
      if (endpoint && endpoint.endpoint) this.$emit('filter-endpoint', `"${endpoint.endpoint}"`)
    },
    barWidth(value) {
      const ratio = Math.max(0, Math.min(1, (Number(value) || 0) / this.maxP95))
      return `${Math.max(2, Math.round(ratio * 100))}%`
    },
    methodClass(method) {
      return METHOD_CLASSES[String(method || '').toUpperCase()] || 'badge-ghost'
    },
    statusClass(value) {
      const status = Number(value)
      if (status >= 500) return 'badge-error'
      if (status >= 400) return 'badge-warning'
      return 'badge-ghost'
    },
    formatNumber(value) {
      const number = Number(value || 0)
      if (number >= 1000000) return `${(number / 1000000).toFixed(1)}M`
      if (number >= 1000) return `${(number / 1000).toFixed(1)}K`
      return String(number)
    },
    formatMs(value) {
      const ms = Number(value || 0)
      if (!ms) return '—'
      if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`
      return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`
    },
    shortTime(tsMs) {
      if (!tsMs) return ''
      const date = new Date(Number(tsMs))
      if (Number.isNaN(date.getTime())) return ''
      const pad = (value) => String(value).padStart(2, '0')
      return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    },
  },
}
</script>
