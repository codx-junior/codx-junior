<template>
  <div class="card bg-base-100 shadow">
    <div class="card-body p-3 gap-3">

      <!-- Row 0: default views for API analysis (query + levels + duration) -->
      <div v-if="quickFilters.length" class="flex flex-wrap items-center gap-1">
        <span class="mr-1 font-mono text-[10px] uppercase tracking-wide opacity-50">view</span>
        <button
          v-for="preset in quickFilters"
          :key="preset.id"
          class="btn btn-xs gap-1"
          :class="preset.id === activeQuickFilter ? 'btn-primary' : 'btn-ghost'"
          :title="preset.query ? `q: ${preset.query}` : preset.label"
          @click="$emit('select-quick-filter', preset.id)"
        >
          <i :class="preset.icon" class="text-[10px]"></i>
          {{ preset.label }}
          <span v-if="preset.minMs" class="badge badge-xs badge-ghost">≥{{ preset.minMs }}ms</span>
        </button>
      </div>

      <!-- Row 1: source, time range, order, refresh, live -->
      <div class="flex flex-wrap items-center gap-2">

        <!-- Source multi-select (native <details> dropdown: no JS needed) -->
        <details class="dropdown">
          <summary class="btn btn-sm btn-outline gap-2">
            <i class="fa-solid fa-database text-xs opacity-70"></i>
            <span class="max-w-40 truncate">{{ sourcesLabel }}</span>
            <i class="fa-solid fa-caret-down text-[10px] opacity-60"></i>
          </summary>
          <div class="dropdown-content z-30 mt-1 max-h-96 w-80 overflow-auto rounded-box bg-base-100 p-2 shadow-lg">
            <p v-if="!sources.length" class="px-2 py-1 text-xs opacity-60">Now log sources</p>

            <!-- Running containers (read with `docker logs`) -->
            <template v-if="containerSources.length">
              <p class="px-2 py-1 font-mono text-[10px] uppercase tracking-wide opacity-50">
                <i class="fa-brands fa-docker mr-1"></i> containers ({{ containerSources.length }})
              </p>
              <label
                v-for="source in containerSources"
                :key="source.id"
                class="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-base-200"
              >
                <input
                  type="checkbox"
                  class="checkbox checkbox-xs"
                  :checked="selectedSources.includes(source.id)"
                  @change="toggleSource(source.id)"
                />
                <span class="flex-1 truncate text-xs" :title="source.id">{{ source.name || source.id }}</span>
                <i class="fa-brands fa-docker text-[10px] opacity-40"></i>
              </label>
            </template>

            <!-- Log files on disk -->
            <template v-if="fileSources.length">
              <p class="px-2 py-1 font-mono text-[10px] uppercase tracking-wide opacity-50">
                <i class="fa-regular fa-file-lines mr-1"></i> files ({{ fileSources.length }})
              </p>
              <label
                v-for="source in fileSources"
                :key="source.id"
                class="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-base-200"
              >
                <input
                  type="checkbox"
                  class="checkbox checkbox-xs"
                  :checked="selectedSources.includes(source.id)"
                  @change="toggleSource(source.id)"
                />
                <span class="flex-1 truncate text-xs" :title="source.path">{{ source.name || source.id }}</span>
                <span class="font-mono text-[10px] opacity-50">{{ formatBytes(source.size_bytes) }}</span>
              </label>
            </template>

            <button
              v-if="selectedSources.length"
              class="btn btn-xs btn-ghost mt-1 w-full justify-start"
              @click="$emit('select-sources', [])"
            >
              <i class="fa-solid fa-xmark text-xs"></i>
              All sources
            </button>
          </div>
        </details>

        <!-- Time-range presets -->
        <div class="join">
          <button
            v-for="preset in presets"
            :key="preset.id"
            class="join-item btn btn-xs"
            :class="activePreset === preset.id ? 'btn-primary' : 'btn-ghost'"
            @click="applyPreset(preset)"
          >{{ preset.label }}</button>
        </div>

        <!-- Custom start / end (only when the custom preset is selected) -->
        <div v-if="activePreset === 'custom'" class="flex items-center gap-1">
          <input
            type="datetime-local"
            class="input input-bordered input-xs"
            :value="customStartValue"
            @change="setCustom('start', $event.target.value)"
          />
          <span class="text-xs opacity-50">→</span>
          <input
            type="datetime-local"
            class="input input-bordered input-xs"
            :value="customEndValue"
            @change="setCustom('end', $event.target.value)"
          />
        </div>

        <!-- Order -->
        <div class="join">
          <button
            class="join-item btn btn-xs gap-1"
            :class="order === 'desc' ? 'btn-primary' : 'btn-ghost'"
            @click="$emit('select-order', 'desc')"
          >
            <i class="fa-solid fa-arrow-down-wide-short text-[10px]"></i>
            Newest
          </button>
          <button
            class="join-item btn btn-xs gap-1"
            :class="order === 'asc' ? 'btn-primary' : 'btn-ghost'"
            @click="$emit('select-order', 'asc')"
          >
            <i class="fa-solid fa-arrow-up-short-wide text-[10px]"></i>
            Oldest
          </button>
        </div>

        <div class="ml-auto flex items-center gap-2">
          <button class="btn btn-sm btn-ghost gap-2" :disabled="loading" @click="$emit('refresh')">
            <i class="fa-solid fa-rotate-right text-xs" :class="{ 'animate-spin': loading }"></i>
            Refresh
          </button>

          <button
            class="btn btn-sm gap-2"
            :class="liveTail ? 'btn-error' : 'btn-ghost'"
            :title="liveTail ? 'Stop streaming new entries' : 'Stream new entries every 2 s'"
            @click="$emit('toggle-live')"
          >
            <span class="h-2 w-2 rounded-full" :class="liveTail ? 'animate-pulse bg-white' : 'bg-base-content/40'"></span>
            Live
          </button>
        </div>
      </div>

      <!-- Row 2: query cost and completeness -->
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-base-content/60">
        <span class="flex items-center gap-1">
          <i class="fa-solid fa-list-ul text-[10px]"></i>
          {{ formatNumber(stats.matched) }} matched
        </span>
        <span class="flex items-center gap-1">
          <i class="fa-solid fa-hard-drive text-[10px]"></i>
          {{ formatNumber(stats.scanned_lines) }} lines scanned
        </span>
        <span class="flex items-center gap-1">
          <i class="fa-solid fa-stopwatch text-[10px]"></i>
          {{ formatDuration(stats.duration_ms) }}
        </span>
        <span v-if="stats.truncated" class="flex items-center gap-1 text-warning">
          <i class="fa-solid fa-triangle-exclamation text-[10px]"></i>
          partial window (only the tail of the file was scanned)
        </span>
        <span v-if="liveTail" class="badge badge-error badge-sm gap-1 animate-pulse">
          <span class="h-1.5 w-1.5 rounded-full bg-white"></span>
          Live
        </span>
        <span class="ml-auto opacity-70">{{ windowLabel }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { TIME_PRESETS } from '@/composables/useLogExplorer'

export default {
  name: 'LogExplorerToolbar',
  emits: ['select-sources', 'select-time-range', 'select-order', 'refresh', 'toggle-live', 'select-quick-filter'],
  props: {
    sources: { type: Array, default: () => [] },
    selectedSources: { type: Array, default: () => [] },
    timeRange: { type: Object, default: () => ({ preset: '24h', start: null, end: null }) },
    order: { type: String, default: 'desc' },
    loading: { type: Boolean, default: false },
    liveTail: { type: Boolean, default: false },
    stats: { type: Object, default: () => ({}) },
    quickFilters: { type: Array, default: () => [] },
    activeQuickFilter: { type: String, default: '' },
  },
  data() {
    return {
      presets: TIME_PRESETS,
    }
  },
  computed: {
    /** Running containers (`kind="docker"`), read with `docker logs`. */
    containerSources() {
      return this.sources.filter((source) => source.kind === 'docker')
    },
    /** Log files discovered in LOGS_FOLDER. */
    fileSources() {
      return this.sources.filter((source) => source.kind !== 'docker')
    },
    activePreset() {
      return this.timeRange?.preset || 'all'
    },
    sourcesLabel() {
      if (!this.selectedSources.length) return 'All sources'
      if (this.selectedSources.length === 1) {
        const found = this.sources.find((source) => source.id === this.selectedSources[0])
        return found ? (found.name || found.id) : this.selectedSources[0]
      }
      return `${this.selectedSources.length} sources`
    },
    customStartValue() {
      return this.toInputValue(this.timeRange?.start)
    },
    customEndValue() {
      return this.toInputValue(this.timeRange?.end)
    },
    windowLabel() {
      const start = this.timeRange?.start
      const end = this.timeRange?.end
      if (start && end) {
        return `${this.formatTime(start)} → ${this.formatTime(end)}`
      }
      const preset = this.presets.find((item) => item.id === this.activePreset)
      return preset && preset.ms ? `last ${preset.label}` : 'all time'
    },
  },
  methods: {
    toggleSource(sourceId) {
      const next = new Set(this.selectedSources)
      if (next.has(sourceId)) next.delete(sourceId)
      else next.add(sourceId)
      this.$emit('select-sources', [...next])
    },
    applyPreset(preset) {
      if (preset.id === 'custom') {
        const end = this.timeRange?.end ?? Date.now()
        const start = this.timeRange?.start ?? (end - 60 * 60 * 1000)
        this.$emit('select-time-range', { preset: 'custom', start, end })
        return
      }
      this.$emit('select-time-range', { preset: preset.id, start: null, end: null })
    },
    setCustom(which, value) {
      const ms = this.fromInputValue(value)
      this.$emit('select-time-range', { ...this.timeRange, preset: 'custom', [which]: ms })
    },
    /** Epoch ms -> value accepted by `<input type="datetime-local">` (local time). */
    toInputValue(ms) {
      if (ms == null || ms === '') return ''
      const date = new Date(Number(ms))
      if (Number.isNaN(date.getTime())) return ''
      const pad = (value) => String(value).padStart(2, '0')
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
    },
    /** `datetime-local` value -> epoch ms. */
    fromInputValue(value) {
      if (!value) return null
      const parsed = new Date(value).getTime()
      return Number.isNaN(parsed) ? null : parsed
    },
    formatBytes(bytes) {
      const size = Number(bytes || 0)
      if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(1)}G`
      if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)}M`
      if (size >= 1024) return `${(size / 1024).toFixed(1)}K`
      return `${size}B`
    },
    formatNumber(value) {
      const number = Number(value || 0)
      if (number >= 1000000) return `${(number / 1000000).toFixed(1)}M`
      if (number >= 1000) return `${(number / 1000).toFixed(1)}K`
      return String(number)
    },
    formatDuration(ms) {
      const value = Number(ms || 0)
      if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
      return `${value.toFixed(value < 10 ? 2 : 0)} ms`
    },
    formatTime(ms) {
      const date = new Date(Number(ms))
      if (Number.isNaN(date.getTime())) return '—'
      return date.toLocaleString()
    },
  },
}
</script>
