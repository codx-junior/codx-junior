<template>
  <div class="card bg-base-100 shadow">
    <div class="card-body p-3 gap-2">

      <div class="flex items-center justify-between">
        <h3 class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide opacity-60">
          <i class="fa-solid fa-chart-column text-[10px]"></i>
          Volume
        </h3>
        <div class="flex items-center gap-3 font-mono text-[10px] opacity-50">
          <span v-if="bucketLabel">{{ bucketLabel }}</span>
          <span>{{ formattedTotal }} hits</span>
        </div>
      </div>

      <!-- Empty / loading state -->
      <div v-if="!buckets.length" class="flex h-24 items-center justify-center text-xs opacity-40">
        <span v-if="loading">
          <i class="fa-solid fa-circle-notch mr-2 animate-spin"></i>Loading…
        </span>
        <span v-else>No entries in the selected window</span>
      </div>

      <div v-else class="relative">
        <!-- Stacked bars (pure CSS, one column per bucket) -->
        <div class="flex h-24 w-full items-end gap-px" @mouseleave="hoveredIndex = null">
          <div
            v-for="(bucket, index) in buckets"
            :key="bucket.ts"
            class="flex h-full flex-1 cursor-pointer flex-col-reverse transition-opacity"
            :class="hoveredIndex === null || hoveredIndex === index ? 'opacity-100' : 'opacity-50'"
            role="button"
            :title="`${formatTooltipTime(bucket.ts)} · ${bucket.total} entries`"
            @mouseenter="hoveredIndex = index"
            @click="onBucketClick(bucket)"
          >
            <span
              v-for="segment in bucket.segments"
              :key="segment.level"
              class="w-full"
              :class="segment.classes"
              :style="{ height: segment.height }"
            ></span>
            <!-- Empty bucket: thin baseline so the axis stays readable -->
            <span v-if="!bucket.segments.length" class="w-full bg-base-content/10" :style="{ height: '2px' }"></span>
          </div>
        </div>

        <!-- Hover tooltip with total + per-level counts -->
        <div
          v-if="hoveredBucket"
          class="pointer-events-none absolute z-20 -translate-x-1/2 whitespace-nowrap rounded-box bg-neutral px-2 py-1 font-mono text-[10px] text-neutral-content shadow-lg"
          :style="{ left: tooltipLeft, bottom: 'calc(100% + 4px)' }"
        >
          <p class="font-semibold">{{ formatTooltipTime(hoveredBucket.ts) }}</p>
          <p class="opacity-80">total {{ hoveredBucket.total }}</p>
          <p v-for="segment in hoveredBucket.segments" :key="segment.level" class="flex items-center gap-1">
            <span class="h-1.5 w-1.5 rounded-full" :class="segment.classes"></span>
            <span class="flex-1">{{ segment.level }}</span>
            <span class="opacity-80">{{ segment.count }}</span>
          </p>
        </div>

        <!-- Time axis -->
        <div class="relative mt-1 h-4">
          <span
            v-for="tick in axisTicks"
            :key="tick.index"
            class="absolute -translate-x-1/2 font-mono text-[9px] opacity-40"
            :style="{ left: tick.left }"
          >{{ tick.label }}</span>
        </div>
      </div>

      <!-- Legend -->
      <div v-if="usedLevels.length" class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] opacity-60">
        <span v-for="level in usedLevels" :key="level" class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :class="bgClass(level)"></span>
          {{ level }}
        </span>
        <span class="ml-auto opacity-60">click a bar to zoom</span>
      </div>
    </div>
  </div>
</template>

<script>
import { LEVEL_BG_CLASSES, LOG_LEVELS } from '@/composables/useLogExplorer'

/** Maximum number of labels printed under the histogram. */
const MAX_TICKS = 6

export default {
  name: 'LogHistogram',
  emits: ['zoom'],
  props: {
    histogram: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
  },
  data() {
    return {
      hoveredIndex: null,
    }
  },
  computed: {
    /** Level order used by the stack: canonical levels first, then any extra one. */
    levelOrder() {
      const list = Array.isArray(this.histogram) ? this.histogram : []
      const extras = new Set()
      for (const bucket of list) {
        for (const [level, count] of Object.entries(bucket.by_level || {})) {
          if (!LOG_LEVELS.includes(level) && Number(count) > 0) extras.add(level)
        }
      }
      return [...LOG_LEVELS, ...extras]
    },
    /** Normalised buckets: `{ ts, total, segments: [{ level, count, classes, height }] }`. */
    buckets() {
      const list = Array.isArray(this.histogram) ? this.histogram : []
      const maxTotal = list.reduce((max, bucket) => Math.max(max, Number(bucket.total || 0)), 0) || 1
      return list.map((bucket) => {
        const byLevel = bucket.by_level || {}
        const total = Number(bucket.total || 0)
        const segments = this.levelOrder
          .map((level) => ({ level, count: Number(byLevel[level] || 0) }))
          .filter((segment) => segment.count > 0)
          .map((segment) => ({
            level: segment.level,
            count: segment.count,
            classes: this.bgClass(segment.level),
            // Relative to the global maximum so bar HEIGHTS carry the volume.
            height: `${Math.max(2, (segment.count / maxTotal) * 100)}%`,
          }))
        return { ts: Number(bucket.ts_ms || 0), total, segments }
      })
    },
    hoveredBucket() {
      if (this.hoveredIndex === null) return null
      return this.buckets[this.hoveredIndex] || null
    },
    tooltipLeft() {
      if (this.hoveredIndex === null || !this.buckets.length) return '0%'
      const ratio = (this.hoveredIndex + 0.5) / this.buckets.length
      return `${Math.min(96, Math.max(4, ratio * 100))}%`
    },
    /** Width of one bucket in ms, used to zoom when a bar is clicked. */
    bucketMs() {
      if (this.buckets.length < 2) return 60 * 1000
      const delta = this.buckets[1].ts - this.buckets[0].ts
      return delta > 0 ? delta : 60 * 1000
    },
    spanMs() {
      if (this.buckets.length < 2) return 0
      return this.buckets[this.buckets.length - 1].ts - this.buckets[0].ts
    },
    bucketLabel() {
      if (!this.bucketMs) return ''
      const minutes = this.bucketMs / 60000
      if (minutes < 1) return `per ${Math.round(this.bucketMs / 1000)}s`
      if (minutes < 90) return `per ${Math.round(minutes)}m`
      const hours = minutes / 60
      if (hours < 48) return `per ${Math.round(hours)}h`
      return `per ${Math.round(hours / 24)}d`
    },
    formattedTotal() {
      const total = this.buckets.reduce((sum, bucket) => sum + bucket.total, 0)
      if (total >= 1000000) return `${(total / 1000000).toFixed(1)}M`
      if (total >= 1000) return `${(total / 1000).toFixed(1)}K`
      return String(total)
    },
    usedLevels() {
      const seen = new Set()
      for (const bucket of this.buckets) {
        for (const segment of bucket.segments) seen.add(segment.level)
      }
      return this.levelOrder.filter((level) => seen.has(level))
    },
    axisTicks() {
      const count = this.buckets.length
      if (!count) return []
      const step = Math.max(1, Math.ceil(count / MAX_TICKS))
      const ticks = []
      for (let index = 0; index < count; index += step) {
        ticks.push({
          index,
          left: `${((index + 0.5) / count) * 100}%`,
          label: this.formatTick(this.buckets[index].ts),
        })
      }
      return ticks
    },
  },
  methods: {
    bgClass(level) {
      return LEVEL_BG_CLASSES[level] || LEVEL_BG_CLASSES.unknown
    },
    /** Click a bar -> ask the parent to zoom the time range to that bucket. */
    onBucketClick(bucket) {
      this.$emit('zoom', bucket.ts, this.bucketMs)
    },
    formatTick(ts) {
      const date = new Date(Number(ts))
      if (Number.isNaN(date.getTime())) return ''
      const pad = (value) => String(value).padStart(2, '0')
      const day = `${pad(date.getDate())}/${pad(date.getMonth() + 1)}`
      const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`
      // Long ranges need the day, short ones only need the clock.
      if (this.spanMs > 24 * 60 * 60 * 1000) return `${day} ${time}`
      if (this.bucketMs < 60 * 1000) return `${time}:${pad(date.getSeconds())}`
      return time
    },
    formatTooltipTime(ts) {
      const date = new Date(Number(ts))
      if (Number.isNaN(date.getTime())) return '—'
      const pad = (value) => String(value).padStart(2, '0')
      return `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    },
  },
}
</script>
