<template>
  <!-- ── Turn timeline strip ──
       The WATERFALL of one assistant turn, compressed into a single soft bar that
       lives at the END of the message (where the old read-receipt bar was).
       It answers "where did the time go?" without opening anything:
         · a segment per tool call, plus a leading "thinking" segment for the time
           spent before the first call (the bar always covers the whole turn, so
           a long thought is visible as a long segment, not as empty space);
         · the tools counter and the turn total on the right;
         · a floating detail card on hover (status, duration, arguments, error);
         · the read-receipt countdown painted OVER the bar in soft success — the
           entry still waits `seenDuration` ms after the turn finished before
           marking the message as read, and that wait is now visible here instead
           of on a bar that only appeared 2.5s before it disappeared.
       Clicking the strip toggles the tool details, which expand right below it.
       Status is never conveyed by colour alone: every segment has a tooltip, and
       the counter names the error count in words. -->
  <div v-if="visible" class="min-w-0">
    <div class="relative flex items-center gap-2 rounded-full border border-base-content/10 bg-base-200/40 pl-1.5 pr-1.5 py-0.5 min-w-0 transition-colors duration-150" :class="eventsOpen ? 'border-primary/30' : 'hover:border-base-content/20'">
      <button
        type="button"
        class="relative flex-1 min-w-0 py-1 text-left cursor-pointer"
        :aria-expanded="eventsOpen ? 'true' : 'false'"
        :aria-controls="panelId"
        :title="barTip"
        @click.stop="$emit('toggle')"
        @mouseleave="hoveredIndex = -1"
      >
        <!-- ── Floating detail of the hovered segment ── -->
        <div
          v-if="hoveredSegment"
          class="pointer-events-none absolute bottom-full z-40 mb-1 w-max max-w-[250px] rounded-lg border border-base-300/70 bg-base-100/95 px-2 py-1.5 shadow-lg backdrop-blur-sm"
          :style="tooltipStyle"
        >
          <div class="flex items-center gap-1.5 text-[11px] font-semibold text-base-content">
            <span class="w-3 flex justify-center shrink-0" :class="hoveredSegment.textClass">
              <span v-if="hoveredSegment.status === 'running'" class="loading loading-spinner loading-xs"></span>
              <i v-else :class="hoveredSegment.icon" class="text-[10px]"></i>
            </span>
            <span class="truncate">{{ hoveredSegment.label }}</span>
          </div>
          <div class="mt-0.5 text-[10px] text-base-content/60 tabular-nums">
            {{ hoveredSegment.statusWord }} · {{ hoveredSegment.durationText }}
          </div>
          <p v-if="hoveredSegment.hint" class="mt-0.5 text-[10px] text-base-content/40">
            {{ hoveredSegment.hint }}
          </p>
          <div
            v-for="arg in hoveredSegment.args"
            :key="arg.key"
            class="mt-0.5 flex gap-1 text-[10px] min-w-0"
          >
            <span class="uppercase tracking-wide text-base-content/35 shrink-0">{{ arg.key }}</span>
            <span class="font-mono text-base-content/60 truncate">{{ arg.value }}</span>
          </div>
          <p v-if="hoveredSegment.error" class="mt-1 text-[10px] text-error line-clamp-2 break-all">
            {{ hoveredSegment.error }}
          </p>
        </div>

        <!-- ── The bar ── -->
        <div class="relative h-2 w-full rounded-full bg-base-200/70 overflow-hidden">
          <div class="absolute inset-0 flex">
            <div
              v-for="(segment, index) in segments"
              :key="segment.key"
              class="h-full transition-colors duration-150"
              :class="[segment.classes, hoveredIndex === index && segment.hoverClasses]"
              :style="{ width: segment.width }"
              @mouseenter="hoveredIndex = index"
            ></div>
          </div>

          <!-- Read-receipt countdown: the entry marks the message as read after
               `seenDuration` ms, painted in soft success over the turn. -->
          <div
            v-if="seenPending"
            class="absolute inset-y-0 left-0 bg-success/30 transition-[width] duration-100 ease-linear"
            :style="{ width: seenProgress + '%' }"
          ></div>

          <!-- Nothing recorded yet (first token pending): a soft pulse, never an
               empty bar, so the strip is meaningful from the very beginning. -->
          <div v-if="!segments.length" class="absolute inset-0 bg-info/25 animate-pulse"></div>
        </div>
      </button>

      <!-- ── Counter: tools + total (+ errors named in words) ── -->
      <span
        class="shrink-0 inline-flex items-center gap-1 text-[10px] tabular-nums text-base-content/50"
        :title="barTip"
      >
        <i class="fa-solid fa-wrench text-[9px] text-base-content/40" aria-hidden="true"></i>
        <span class="whitespace-nowrap">{{ counterText }}</span>
      </span>

      <!-- Read-receipt state: double check while the countdown runs -->
      <i
        v-if="seenPending"
        class="fa-solid fa-check-double text-[11px] text-success/70 shrink-0"
        aria-hidden="true"
      ></i>

      <i
        class="fa-solid fa-chevron-down text-[9px] text-base-content/40 shrink-0 transition-transform duration-150"
        :class="eventsOpen && 'rotate-180'"
        aria-hidden="true"
      ></i>
    </div>
  </div>
</template>

<script>
export default {
  props: {
    // Assistant message owning the turn (`tool_events` + `meta_data.time_taken`)
    message: { type: Object, default: null },
    // Parent-owned state of the details panel (same flag as the header wrench)
    eventsOpen: { type: Boolean, default: false },
    // Id of the details panel the strip points at with aria-controls
    panelId: { type: String, default: null },
    // Elapsed milliseconds of a response that is still streaming: the entry
    // refreshes it once per second so the bar grows while the turn runs.
    liveMs: { type: Number, default: 0 },
    // Read-receipt countdown, 0-100
    seenProgress: { type: Number, default: 0 },
    // True while the entry waits before marking the message as read
    seenPending: { type: Boolean, default: false },
    // That wait, in milliseconds (shown in the tooltip)
    seenDuration: { type: Number, default: 2500 }
  },
  emits: ['toggle'],
  data() {
    return {
      hoveredIndex: -1,
      // Arguments rendered in the hover card before the "+N more" hint
      maxTipArgs: 2
    }
  },
  computed: {
    tools() {
      return this.message?.tool_events || []
    },
    streaming() {
      return !this.message?.done
    },
    toolErrorCount() {
      return this.tools.filter(tool => tool.status === 'error').length
    },
    // Length of the whole turn: the duration the server reported, the live
    // elapsed time while it streams, and the sum of the tool durations as the
    // last resort (messages recorded without metadata).
    totalMs() {
      const reported = Number(this.message?.meta_data?.time_taken)
      if (Number.isFinite(reported) && reported > 0) return reported * 1000
      if (this.liveMs > 0) return this.liveMs
      return this.tools.reduce((total, tool) => total + (Number(tool.duration_ms) || 0), 0)
    },
    // One entry per slice of the turn, in chronological order: the model time
    // before each tool call (and before the final answer) interleaved with the
    // tool calls themselves.
    segments() {
      // Nothing to slice yet (first token pending): the strip shows a soft pulse
      // instead of a bar that would look frozen at 100%.
      if (this.streaming && !this.tools.length) return []

      const total = Math.max(this.totalMs, 1)
      const completedMs = this.tools.reduce((sum, tool) => {
        if (tool.status === 'running') return sum
        return sum + (Number(tool.duration_ms) || 0)
      }, 0)
      const remaining = Math.max(total - completedMs, 0)

      // The backend records every tool duration but no per-round timestamp, so
      // the turn's model time (total − tools) is spread EVENLY over the rounds:
      // one gap before each call and one before the final answer. It is an
      // approximation, but it keeps the bar alternating like a real waterfall
      // instead of stacking every model second at the front.
      const gapMs = remaining / (this.tools.length + 1)

      const entries = []
      if (!this.tools.length) {
        entries.push({ kind: 'think', ms: total })
      } else {
        this.tools.forEach((tool, index) => {
          if (gapMs > 0) entries.push({ kind: 'think', ms: gapMs })
          const running = tool.status === 'running'
          entries.push({
            kind: 'tool',
            ms: running ? (gapMs || 1) : (Number(tool.duration_ms) || gapMs || 1),
            tool,
            index
          })
        })
        if (gapMs > 0) entries.push({ kind: 'think', ms: gapMs })
      }

      const span = entries.reduce((sum, entry) => sum + entry.ms, 0) || 1
      let left = 0
      return entries.map((entry, index) => {
        const widthPct = (entry.ms / span) * 100
        const segment = {
          ...entry,
          key: `${entry.kind}-${index}`,
          width: widthPct + '%',
          leftPct: left,
          spanPct: widthPct,
          ...this.describeSegment(entry)
        }
        left += widthPct
        return segment
      })
    },
    hoveredSegment() {
      return this.hoveredIndex >= 0 ? this.segments[this.hoveredIndex] || null : null
    },
    // Centred on the hovered slice, clamped so the card never leaves the strip
    tooltipStyle() {
      const segment = this.hoveredSegment
      if (!segment) return {}
      const center = Math.min(85, Math.max(15, segment.leftPct + segment.spanPct / 2))
      return { left: center + '%', transform: 'translateX(-50%)' }
    },
    // Visible from the very beginning of the turn. Once it finished, the strip
    // stays while it has something to say (a recorded tool or the read-receipt
    // countdown); a plain answer with no tools fades out after being read.
    visible() {
      if (!this.message) return false
      if (this.streaming) return true
      if (this.seenPending) return true
      return this.tools.length > 0
    },
    counterText() {
      const parts = []
      if (this.tools.length) {
        parts.push(`${this.tools.length} tool${this.tools.length > 1 ? 's' : ''}`)
      }
      if (this.toolErrorCount) {
        parts.push(`${this.toolErrorCount} err${this.toolErrorCount > 1 ? 's' : ''}`)
      }
      const total = this.totalMs
      if (total >= 1000) parts.push(this.formatMs(total))
      if (parts.length) return parts.join(' · ')
      return this.streaming ? 'Starting…' : 'No tools'
    },
    // Full state in words — the bar is a picture, the tooltip is the fact
    barTip() {
      if (this.seenPending) {
        return `Marking as read in ${((this.seenDuration || 0) / 1000).toFixed(1)}s`
      }
      const parts = []
      if (this.streaming) parts.push('Running')
      if (this.tools.length) {
        parts.push(`${this.tools.length} tool${this.tools.length > 1 ? 's' : ''}`)
      }
      if (this.toolErrorCount) {
        parts.push(`${this.toolErrorCount} error${this.toolErrorCount > 1 ? 's' : ''}`)
      }
      if (this.totalMs >= 1000) parts.push(this.formatMs(this.totalMs))
      parts.push(this.eventsOpen ? 'Hide details' : 'Show details')
      return parts.join(' · ')
    }
  },
  methods: {
    // Everything the hover card and the colours need for one slice. Tints stay
    // soft (30-35% alpha): the strip is a background trace, not an alert.
    describeSegment(entry) {
      if (entry.kind === 'think') {
        return {
          label: this.streaming && !this.tools.length ? 'Starting…' : 'Thinking',
          hint: this.tools.length
            ? 'Model round'
            : (this.streaming ? 'Waiting for the first token' : 'Thinking & writing'),
          status: 'done',
          statusWord: this.streaming ? 'Running' : 'Done',
          durationText: this.formatMs(entry.ms),
          icon: 'fa-solid fa-brain',
          textClass: 'text-base-content/50',
          classes: 'bg-base-content/15',
          hoverClasses: 'bg-base-content/30'
        }
      }
      const tool = entry.tool || {}
      const status = tool.status || 'unknown'
      const args = this.toolArgs(tool)
      return {
        label: tool.tool || 'tool',
        hint: status === 'running' ? 'Running…' : '',
        status,
        statusWord: { running: 'Running', done: 'Done', error: 'Failed' }[status] || 'Unknown',
        // A running call has no measured duration yet — never invent one
        durationText: status === 'running' ? 'in progress' : this.formatMs(Number(tool.duration_ms) || entry.ms),
        icon: status === 'running'
          ? 'fa-solid fa-spinner fa-spin'
          : status === 'error'
            ? 'fa-solid fa-circle-xmark'
            : 'fa-solid fa-circle-check',
        textClass: status === 'error' ? 'text-error' : status === 'running' ? 'text-info' : 'text-success',
        classes: status === 'error'
          ? 'bg-error/35'
          : status === 'running'
            ? 'bg-info/35 animate-pulse'
            : 'bg-success/30',
        hoverClasses: status === 'error' ? 'bg-error/55' : status === 'running' ? 'bg-info/55' : 'bg-success/50',
        args: args.slice(0, this.maxTipArgs),
        error: tool.error || ''
      }
    },
    // Request arguments arrive parsed (objects) or serialized (JSON strings)
    toolRequest(tool) {
      const request = tool?.request
      if (!request) return null
      if (typeof request === 'string') {
        try {
          return JSON.parse(request)
        } catch {
          return { input: request }
        }
      }
      return typeof request === 'object' ? request : { input: request }
    },
    toolArgs(tool) {
      const request = this.toolRequest(tool)
      if (!request) return []
      return Object.entries(request).map(([key, value]) => {
        const simple = value === null || ['string', 'number', 'boolean'].includes(typeof value)
        const text = simple ? String(value ?? '') : this.stringifyValue(value)
        return { key, value: text.length > 60 ? text.slice(0, 59) + '…' : text }
      })
    },
    stringifyValue(value) {
      try {
        return JSON.stringify(value)
      } catch {
        return String(value)
      }
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
