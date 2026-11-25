<template>
  <div class="flex flex-wrap items-center gap-2">
    <div class="join">
      <button
        v-for="range in rangeOptions"
        :key="range.value"
        @click="setRange(range.value)"
        class="btn btn-sm join-item"
        :class="rangeFilter === range.value ? 'btn-primary' : 'btn-ghost'"
      >
        {{ range.label }}
      </button>
    </div>

    <div v-if="rangeFilter === 'custom'" class="flex items-center gap-1">
      <input
        type="date"
        :value="customFromDate"
        @change="setFrom($event.target.value)"
        class="input input-sm input-bordered text-xs"
      />
      <span class="text-xs text-base-content/40">→</span>
      <input
        type="date"
        :value="customToDate"
        @change="setTo($event.target.value)"
        class="input input-sm input-bordered text-xs"
      />
    </div>

    <span v-if="loading" class="loading loading-spinner loading-xs text-primary"></span>
  </div>
</template>

<script>
/**
 * Range selector for the planning and follow-up views.
 *
 * By default it edits the planning store (the Planning view reads it). Pass
 * `local` to own the value in the parent instead: the component then emits
 * `update:range`, `update:from-date` and `update:to-date` without triggering
 * the planning store's dataset reload.
 */
export default {
  name: 'PlanningRangeFilter',
  props: {
    local: { type: Boolean, default: false },
    range: { type: String, default: 'month' },
    fromDate: { type: String, default: null },
    toDate: { type: String, default: null },
    // Optional override of the visible ranges. Callers that need the exact
    // same set as the Follow-up review pass `TASK_RANGE_OPTIONS`.
    ranges: { type: Array, default: null }
  },
  emits: ['update:range', 'update:from-date', 'update:to-date'],
  data() {
    return {
      defaultRanges: [
        { label: 'Today', value: 'today' },
        { label: 'Last 7 days', value: 'week' },
        { label: 'Last 30 days', value: 'month' },
        { label: 'Custom', value: 'custom' }
      ]
    }
  },
  computed: {
    // A parent may pass its own range list; without it the selector keeps the
    // historical four options.
    rangeOptions() {
      return (this.ranges && this.ranges.length) ? this.ranges : this.defaultRanges
    },
    rangeFilter() {
      return this.local ? this.range : this.$storex.planning.rangeFilter
    },
    customFromDate() {
      return this.local ? this.fromDate : this.$storex.planning.customFromDate
    },
    customToDate() {
      return this.local ? this.toDate : this.$storex.planning.customToDate
    },
    loading() {
      return this.local ? false : this.$storex.planning.loading
    }
  },
  methods: {
    setRange(value) {
      if (this.local) {
        this.$emit('update:range', value)
        return
      }
      this.$storex.planning.setRangeFilter(value)
    },
    setFrom(value) {
      if (this.local) {
        this.$emit('update:from-date', value)
        return
      }
      this.$storex.planning.setCustomRange({ fromDate: value, toDate: this.customToDate })
    },
    setTo(value) {
      if (this.local) {
        this.$emit('update:to-date', value)
        return
      }
      this.$storex.planning.setCustomRange({ fromDate: this.customFromDate, toDate: value })
    }
  }
}
</script>
