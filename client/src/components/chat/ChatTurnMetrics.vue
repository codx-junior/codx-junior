<template>
  <!-- ── Parallelism of a turn ──
       The one reading neither the timeline nor the events table can give: how
       many tool calls were running at the SAME TIME. Three variants, one source
       of truth (`utils/turnMetrics.js`):
         · chip    — the header of the message: just the ratio, no room for more;
         · summary — one line of facts above the details tabs;
         · lanes   — the waterfall: one row per concurrent slot, so two calls
                     drawn on the same row never overlap in time and two calls
                     drawn on different rows visibly do.
       A ratio below 1.05× is reported as SEQUENTIAL rather than hidden: a turn
       that never overlapped is a finding, not a missing number. When the
       runtime did not stamp the calls the component says so instead of drawing
       a parallelism it cannot prove. -->
  <div v-if="visible" :class="rootClass" :title="tip">
    <!-- ─────────── chip: the ratio, next to the tools counter ─────────── -->
    <template v-if="variant === 'chip'">
      <i class="fa-solid fa-code-fork text-[8px]" aria-hidden="true"></i>
      <span class="whitespace-nowrap">{{ ratioText }}</span>
    </template>

    <!-- ─────────── summary: the numbers of the turn ─────────── -->
    <template v-else-if="variant === 'summary'">
      <span class="inline-flex items-center gap-1 font-semibold tabular-nums" :class="ratioClass">
        <i class="fa-solid fa-code-fork text-[9px]" aria-hidden="true"></i>
        {{ ratioText }}
      </span>
      <span class="text-base-content/45">{{ ratioWords }}</span>
      <span class="text-base-content/40">
        peak <span class="font-semibold text-base-content/70 tabular-nums">{{ metrics.peak }}</span>
      </span>
      <span class="text-base-content/40">
        lanes <span class="font-semibold text-base-content/70 tabular-nums">{{ metrics.laneCount }}</span>
      </span>
      <span class="text-base-content/40 tabular-nums">
        {{ toolTimeText }} busy in {{ wallTimeText }}
      </span>
      <span v-if="metrics.savedMs >= 1" class="text-success/80 tabular-nums" :title="savedTip">
        −{{ savedText }} saved
      </span>
      <span class="grow"></span>
      <span v-if="!metrics.measured" class="text-warning/80" :title="unmeasuredTip">
        <i class="fa-solid fa-triangle-exclamation text-[9px]" aria-hidden="true"></i>
        unstamped calls · ratio is a lower bound
      </span>
    </template>

    <!-- ─────────── lanes: the concurrency strip and the waterfall ─────────── -->
    <template v-else>
      <div class="flex items-center justify-between text-[9px] uppercase tracking-wide text-base-content/35">
        <span>Concurrency</span>
        <span class="normal-case tabular-nums">{{ concurrencyText }}</span>
      </div>

      <!-- The strip: the level of concurrency over the tool window. A gap
           between two calls is painted as an empty slice, never as overlap. -->
      <div class="relative h-2 w-full rounded-sm bg-base-300/30 overflow-hidden" :title="stripTip">
        <div
          v-for="slice in metrics.slices"
          :key="slice.key"
          class="absolute inset-y-0"
          :class="slice.classes"
        ></div>
      </div>

      <!-- The waterfall: one row per concurrent slot, bars placed on the real
           clock. Same colours as the timeline and the events table. -->
      <div class="space-y-[2px]">
        <div v-for="(lane, laneIndex) in laneRows" :key="'lane-' + laneIndex" class="relative h-1.5">
          <div
            v-for="bar in lane"
            :key="bar.key"
            class="absolute inset-y-0 rounded-sm cursor-default"
            :class="bar.classes"
            :style="{ left: bar.left, width: bar.width }"
            :title="barTip(bar)"
          ></div>
        </div>
      </div>

      <div class="flex items-center justify-between text-[9px] text-base-content/30 tabular-nums">
        <span>{{ metrics.tools }} tool{{ metrics.tools === 1 ? '' : 's' }} · {{ metrics.laneCount }} lane{{ metrics.laneCount === 1 ? '' : 's' }}</span>
        <span>{{ toolTimeText }} busy in {{ wallTimeText }}</span>
      </div>
    </template>
  </div>
</template>

<script>
import { formatRatio, ratioWords as ratioWordsOf, turnMetrics } from '../../utils/turnMetrics.js'

export default {
  props: {
    // Assistant message owning the turn (`tool_events`)
    message: { type: Object, default: null },
    // chip (header) | summary (one line) | lanes (waterfall)
    variant: { type: String, default: 'chip' },
    // Clock of the parent, so a running call grows with the turn instead of
    // carrying a second timer. Falls back to the wall clock.
    now: { type: Number, default: 0 }
  },
  computed: {
    // `now` first: while a turn streams the parent ticks it every second, which
    // is what makes a running call's bar grow.
    metrics() {
      return turnMetrics(this.message, this.now)
    },
    // Nothing to draw without at least two placed calls: one call can never
    // overlap, and the chip would be a permanent "1×" next to every message.
    visible() {
      if (!this.metrics.hasData) return false
      if (this.variant === 'chip' || this.variant === 'lanes') return this.metrics.tools > 1
      return true
    },
    ratioText() {
      return formatRatio(this.metrics.ratio)
    },
    ratioWords() {
      return ratioWordsOf(this.metrics.ratio)
    },
    ratioClass() {
      return this.metrics.parallel ? 'text-primary' : 'text-base-content/50'
    },
    rootClass() {
      if (this.variant === 'chip') {
        const tone = this.metrics.parallel
          ? 'bg-primary/15 text-primary'
          : 'bg-base-content/10 text-base-content/50'
        return 'inline-flex items-center gap-1 rounded-full px-1 py-px text-[9px] tabular-nums shrink-0 ' + tone
      }
      if (this.variant === 'summary') {
        return 'flex flex-wrap items-center gap-x-2 gap-y-0.5 px-2 py-1 text-[10px] shrink-0'
      }
      return 'px-2 py-1.5 space-y-1'
    },
    toolTimeText() {
      return this.formatMs(this.metrics.toolMs)
    },
    wallTimeText() {
      return this.formatMs(this.metrics.wallMs)
    },
    savedText() {
      return this.formatMs(this.metrics.savedMs)
    },
    // Bars grouped by lane: one row per concurrent slot
    laneRows() {
      const rows = []
      for (let lane = 0; lane < this.metrics.laneCount; lane += 1) {
        rows.push(this.metrics.bars.filter(bar => bar.lane === lane))
      }
      return rows
    },
    concurrencyText() {
      if (!this.metrics.peak) return 'no calls'
      return `avg ${this.ratioText} · peak ${this.metrics.peak}`
    },
    // Full state in words — the bar is a picture, the tooltip is the fact
    tip() {
      if (!this.metrics.hasData) return 'No timed tool call in this turn'
      const parts = [
        `${this.metrics.tools} tool call${this.metrics.tools === 1 ? '' : 's'}`,
        `parallelism ${this.ratioText} (${this.ratioWords})`,
        `peak ${this.metrics.peak} at once`,
        `${this.toolTimeText} of tool work in ${this.wallTimeText}`
      ]
      if (this.metrics.savedMs >= 1) parts.push(`${this.savedText} saved vs sequential`)
      if (!this.metrics.measured) parts.push(this.unmeasuredTip)
      return parts.join(' · ')
    },
    stripTip() {
      return `Concurrency over the tool window · ${this.concurrencyText}`
    },
    savedTip() {
      return `A sequential run would have needed ${this.formatMs(this.metrics.toolMs)} instead of ${this.wallTimeText}`
    },
    unmeasuredTip() {
      const missing = this.metrics.unplaced
      return missing
        ? `${missing} call(s) without a start timestamp could not be placed`
        : 'Some calls have no start timestamp: the position is derived from their duration'
    }
  },
  methods: {
    barTip(bar) {
      const status = { running: 'Running', done: 'Done', error: 'Failed' }[bar.status] || 'Unknown'
      const parts = [bar.tool, status, this.formatMs(bar.durationMs)]
      parts.push(`starts +${this.formatMs(bar.offsetMs)}`)
      parts.push(`lane ${bar.lane + 1}`)
      if (bar.error) parts.push(bar.error)
      return parts.join(' · ')
    },
    formatMs(ms) {
      const value = Math.max(0, Number(ms) || 0)
      if (value < 1000) return Math.round(value) + ' ms'
      if (value < 60000) return (value / 1000).toFixed(1) + ' s'
      const minutes = Math.floor(value / 60000)
      const seconds = Math.round((value % 60000) / 1000)
      return `${minutes}m ${String(seconds).padStart(2, '0')}s`
    }
  }
}
</script>
