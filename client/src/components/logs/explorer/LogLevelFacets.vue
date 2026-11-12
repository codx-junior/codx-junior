<template>
  <div class="card bg-base-100 shadow">
    <div class="card-body p-3 gap-3">

      <div class="flex items-center justify-between">
        <h3 class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide opacity-60">
          <i class="fa-solid fa-layer-group text-[10px]"></i>
          Levels
        </h3>
        <button
          v-if="levels.length"
          class="btn btn-xs btn-ghost gap-1"
          @click="$emit('clear-levels')"
        >
          <i class="fa-solid fa-filter-circle-xmark text-[10px]"></i>
          clear
        </button>
      </div>

      <!-- Level chips with counts (multi-select) -->
      <div v-if="levelRows.length" class="flex flex-wrap gap-1.5">
        <button
          v-for="row in levelRows"
          :key="row.value"
          class="badge badge-lg gap-2 cursor-pointer select-none transition-colors"
          :class="isSelected(row.value) ? row.style.active : 'badge-ghost opacity-80 hover:opacity-100'"
          :title="`${row.count} entries · click to filter`"
          @click="$emit('toggle-level', row.value)"
        >
          <span class="h-2 w-2 rounded-full" :class="row.style.dot"></span>
          <span class="font-mono text-[11px] uppercase">{{ row.value }}</span>
          <span class="font-mono text-[10px] opacity-70">{{ row.count }}</span>
        </button>
      </div>
      <p v-else class="text-[11px] opacity-40">No levels in the matched window.</p>

      <!-- Collapsed logger / module facets -->
      <div
        v-for="group in facetGroups"
        :key="group.key"
        class="collapse collapse-arrow rounded-box bg-base-200/50"
      >
        <input type="checkbox" />
        <div class="collapse-title flex min-h-0 items-center gap-2 py-2 text-xs font-medium">
          <i :class="[group.icon, 'text-[10px] opacity-60']"></i>
          {{ group.label }}
          <span class="badge badge-xs badge-ghost">{{ group.values.length }}</span>
        </div>
        <div class="collapse-content px-2">
          <button
            v-for="value in group.values"
            :key="value.value"
            class="flex w-full items-center gap-2 rounded py-1 text-left hover:bg-base-300/60"
            :title="`Add ${group.key}=${value.value} to the query`"
            @click="applyFacet(group.key, value.value)"
          >
            <span class="flex-1 truncate font-mono text-[11px]">{{ value.value }}</span>
            <span class="font-mono text-[10px] opacity-60">{{ value.count }}</span>
            <span class="h-1.5 w-10 overflow-hidden rounded bg-base-300">
              <span
                class="block h-full"
                :class="barClass(group.key)"
                :style="{ width: barWidth(group, value.count) }"
              ></span>
            </span>
          </button>
          <p v-if="!group.values.length" class="py-1 text-[11px] opacity-40">No values</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { LOG_LEVELS } from '@/composables/useLogExplorer'

/** How many values each logger/module facet lists. */
const FACET_VALUES = 12

export default {
  name: 'LogLevelFacets',
  emits: ['toggle-level', 'clear-levels', 'filter'],
  props: {
    facets: { type: Object, default: () => ({ level: [], logger: [], module: [] }) },
    levels: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
  },
  data() {
    return {
      levelStyles: {
        critical: { dot: 'bg-fuchsia-500', active: 'badge-error text-error-content' },
        error: { dot: 'bg-error', active: 'badge-error' },
        warn: { dot: 'bg-warning', active: 'badge-warning' },
        info: { dot: 'bg-info', active: 'badge-info' },
        debug: { dot: 'bg-success', active: 'badge-success' },
        trace: { dot: 'bg-base-content/40', active: 'badge-neutral' },
        unknown: { dot: 'bg-base-content/20', active: 'badge-neutral' },
      },
    }
  },
  computed: {
    /** Level chips: known levels sorted by severity, plus any extra value. */
    levelRows() {
      const counts = {}
      for (const facet of this.facets?.level || []) {
        counts[String(facet.value).toLowerCase()] = Number(facet.count || 0)
      }
      const known = LOG_LEVELS.map((level) => ({ value: level, count: counts[level] || 0 }))
      const extras = Object.keys(counts)
        .filter((level) => !LOG_LEVELS.includes(level))
        .sort()
        .map((level) => ({ value: level, count: counts[level] || 0 }))
      return [...known, ...extras]
        .filter((row) => row.count > 0 || this.isSelected(row.value))
        .map((row) => ({ ...row, style: this.levelStyles[row.value] || this.levelStyles.unknown }))
    },
    facetGroups() {
      return [
        { key: 'logger', label: 'Loggers', icon: 'fa-solid fa-tag', values: this.topValues(this.facets?.logger) },
        { key: 'module', label: 'Modules', icon: 'fa-solid fa-cubes', values: this.topValues(this.facets?.module) },
      ]
    },
  },
  methods: {
    isSelected(level) {
      return this.levels.includes(level)
    },
    topValues(values) {
      return (values || []).slice(0, FACET_VALUES)
    },
    /** Largest count of the group (used to scale the little bars). */
    groupMax(group) {
      return group.values.reduce((max, item) => Math.max(max, Number(item.count || 0)), 0) || 1
    },
    barWidth(group, count) {
      return `${Math.max(4, Math.round((Number(count || 0) / this.groupMax(group)) * 100))}%`
    },
    barClass(key) {
      return key === 'logger' ? 'bg-secondary' : 'bg-accent'
    },
    /** Emit a ready-to-use query term, e.g. `logger="codx.junior.api"`. */
    applyFacet(key, value) {
      const clean = String(value || '').replace(/"/g, '')
      this.$emit('filter', `${key}="${clean}"`)
    },
  },
}
</script>
