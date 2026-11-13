<template>
  <!-- ── Turn timeline strip ──
       The WATERFALL of one assistant turn compressed into a single soft bar that
       now lives IN the message header, so it stays pinned while the message body
       scrolls (the header is sticky, this bar is one of its rows).
       It answers "where did the time go?" without opening anything:
         · one segment per tool call, interleaved with the model time before each
           call (the bar always covers the whole turn, so a long thought is a
           long segment, not empty space);
         · EVERY EVENT TYPE HAS ITS OWN COLOUR — read / write / execute / web /
           agent / model / error — see `utils/toolVisuals.js`, shared with the
           events table of the details panel so the two views never disagree;
         · the tools counter and the turn total on the right;
         · a floating detail card on hover (family, status, duration, arguments);
         · the read-receipt countdown painted OVER the bar in soft success: the
           entry still waits `seenDuration` ms after the turn finished before
           marking the message as read.
       Clicking the strip toggles the details panel, which expands right under it
       with two tabs (events table + tools).
       Status is never conveyed by colour alone: every segment has a tooltip, and
       the counter names the error count in words. -->
  <div v-if="visible" class="w-full min-w-0">
    <!-- The WHOLE pill is the toggle: the bar, the counter and the chevron. It
         used to be a button wrapping only the bar, so clicking the chevron (the
         expected affordance) or the counter did nothing. The drawer button keeps
         its own handler with `.stop`. -->
    <div
      class="relative flex items-center gap-2 rounded-full border border-base-content/10 bg-base-200/40 pl-1.5 pr-1 py-0.5 min-w-0 cursor-pointer outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-primary/40"
      :class="eventsOpen ? 'border-primary/30' : 'hover:border-base-content/20'"
      role="button"
      tabindex="0"
      :aria-expanded="eventsOpen ? 'true' : 'false'"
      :aria-controls="panelId"
      :title="barTip"
      @click.stop="$emit('toggle')"
      @keydown.enter.prevent.stop="$emit('toggle')"
      @keydown.space.prevent.stop="$emit('toggle')"
      @mouseleave="hoveredIndex = -1"
    >
      <div class="relative flex-1 min-w-0 py-1 text-left">
        <!-- ── Floating detail of the hovered segment ── -->
        <div
          v-if="hoveredSegment"
          class="pointer-events-none absolute bottom-full z-40 mb-1 w-max max-w-[250px] rounded-lg border border-base-300/70 bg-base-100/95 px-2 py-1.5 shadow-lg backdrop-blur-sm"
          :style="tooltipStyle"
        >
          <div class="flex items-center gap-1.5 text-[11px] font-semibold text-base-content">
            <span class="w-3 flex justify-center shrink-0" :class="hoveredSegment.textClass">
              <span v-if="hoveredSegment.running" class="loading loading-spinner loading-xs"></span>
              <i v-else :class="hoveredSegment.icon" class="text-[10px]"></i>
            </span>
            <span class="truncate">{{ hoveredSegment.label }}</span>
            <!-- The family, always in words next to its colour -->
            <span class="shrink-0 rounded px-1 py-px text-[9px] uppercase tracking-wide" :class="hoveredSegment.chipClass">
              {{ hoveredSegment.categoryLabel }}
            </span>
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
      </div>

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

      <!-- Processing details (side drawer). Inherited from the tool summary strip
           this bar replaced: the drawer only means something while the details
           panel is open, so the trigger only exists then. -->
      <button
        v-if="eventsOpen"
        type="button"
        class="shrink-0 rounded-full px-1.5 py-0.5 text-base-content/40 hover:text-base-content hover:bg-base-content/10 transition-colors duration-150"
        :class="drawerOpen && 'text-warning bg-warning/15'"
        aria-label="Processing details"
        title="Processing details"
        @click.stop="$emit('show-events-drawer')"
      >
        <i class="fa-solid fa-ellipsis text-[10px]" aria-hidden="true"></i>
      </button>

      <i
        class="fa-solid fa-chevron-down text-[9px] text-base-content/40 shrink-0 transition-transform duration-150"
        :class="eventsOpen && 'rotate-180'"
        aria-hidden="true"
      ></i>
    </div>
  </div>
</template>

<script>
import { categoryVisual, phaseVisual, toolVisual } from '../../utils/toolVisuals.js'

export default {
  props: {
    // Assistant message owning the turn (`tool_events` + `meta_data.time_taken`)
    message: { type: Object, default: null },
    // Parent-owned state of the details panel (same flag as the header wrench)
    eventsOpen: { type: Boolean, default: false },
    // Side drawer of the processing details (trigger hidden while closed)
    drawerOpen: { type: Boolean, default: false },
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
  emits: ['toggle', 'show-events-drawer'],
  data() {
    return {
      hoveredIndex: -1,
      // Arguments rendered in the hover card before the "+N more" hint
      maxTipArgs: 2,
      // Wall clock of the bar: a waiting phase or a running call has no measured
      // duration yet, so its slice grows from its real start to "now" while the
      // turn is still open.
      now: Date.now(),
      clockTimer: null
    }
  },
  watch: {
    // The clock only exists while the turn is open.
    streaming() {
      this.startClock()
    }
  },
  mounted() {
    this.startClock()
  },
  beforeUnmount() {
    this.stopClock()
  },
  computed: {
    tools() {
      return this.message?.tool_events || []
    },
    // PHASES: the waiting that is not a tool call — request sent, tokens
    // streaming, runtime notices, interruptions. The runtime stamps them with
    // real timestamps, so the bar can PAINT the wait instead of guessing it.
    phases() {
      return this.message?.phase_events || []
    },
    // The slice still growing (at most one: the runtime closes the wait when the
    // first token arrives).
    openPhase() {
      return this.phases.find(phase => phase.status === 'running') || null
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
    // Entries placed by their REAL timestamps, merged from the phases of the
    // turn and its tool calls. Empty for a message recorded before the runtime
    // stamped its events: those fall back to the reconstructed layout below.
    timedEntries() {
      const entries = []
      this.phases.forEach((phase, index) => {
        const start = this.toMs(phase.started_at)
        if (start == null) return
        const measured = Number(phase.duration_ms) || 0
        const finished = this.toMs(phase.finished_at)
        const end = finished != null
          ? finished
          : (phase.status === 'running' ? this.now : start + measured)
        entries.push({ kind: 'phase', ref: phase, phase, at: start, end: Math.max(end, start), index })
      })
      this.tools.forEach((tool, index) => {
        const measured = Number(tool.duration_ms) || 0
        const finished = this.toMs(tool.finished_at)
        const start = this.toMs(tool.started_at)
        const fromStart = start != null ? start : (finished != null ? finished - measured : null)
        if (fromStart == null) return
        const end = finished != null
          ? finished
          : (tool.status === 'running' ? this.now : fromStart + measured)
        entries.push({ kind: 'tool', ref: tool, tool, at: fromStart, end: Math.max(end, fromStart), index })
      })
      return entries.sort((a, b) => a.at - b.at)
    },
    // One entry per slice of the turn, in chronological order: the model time
    // before each tool call (and before the final answer) interleaved with the
    // tool calls themselves.
    segments() {
      // Real timestamps first: every phase and tool placed where it ACTUALLY
      // happened, with the silence between them drawn as idle slices. That is
      // what makes "waiting 2 minutes for the model" a visible block and "no
      // event for 2 minutes" a visible hole.
      const timed = this.timedEntries
      if (timed.length) return this.timedSegments(timed)

      // Nothing stamped yet (no event recorded): the strip shows a soft pulse
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
      return this.tools.length > 0 || this.phases.length > 0
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
      // What the turn is doing RIGHT NOW, in one word: "waiting" next to a bar
      // that is not moving is worth more than silence.
      if (this.openPhase) {
        parts.push(this.openPhase.kind === 'llm_response' ? 'streaming' : 'waiting')
      } else if (this.tools.some(tool => tool.status === 'running')) {
        parts.push('running')
      }
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
      if (this.openPhase) {
        const started = this.toMs(this.openPhase.started_at)
        const waited = started != null ? Math.max(0, this.now - started) : 0
        parts.push(`${this.openPhase.label || 'Waiting for the model'} · ${this.formatMs(waited)}`)
      }
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
    // Waterfall built from REAL timestamps: every phase and every call occupies
    // the time it actually took, and the silence between two events becomes an
    // IDLE slice. Those holes are the point — they are what "nothing happened
    // for two minutes" looks like, and they are distinguishable from a long wait
    // precisely because a wait is a recorded phase with its own colour.
    timedSegments(entries) {
      const first = entries[0].at
      const lastEntry = entries[entries.length - 1]
      const last = this.streaming ? Math.max(this.now, lastEntry.end) : lastEntry.end
      const span = Math.max(last - first, 1)
      // A hole thinner than 2‰ of the turn is noise, not a stall
      const minGap = Math.max(span * 0.002, 1)

      const slices = []
      let cursor = first
      entries.forEach(entry => {
        if (entry.at - cursor > minGap) {
          slices.push({ kind: 'idle', at: cursor, end: entry.at, ms: entry.at - cursor })
        }
        const end = Math.max(entry.end, entry.at + 1)
        slices.push({ ...entry, ms: end - entry.at })
        cursor = Math.max(cursor, end)
      })
      if (this.streaming && last - cursor > minGap) {
        slices.push({ kind: 'idle', at: cursor, end: last, ms: last - cursor })
      }

      return slices.map((slice, index) => {
        const width = (slice.ms / span) * 100
        return {
          ...slice,
          key: `${slice.kind}-${index}`,
          width: width + '%',
          leftPct: ((slice.at - first) / span) * 100,
          spanPct: width,
          ...this.describeSegment(slice)
        }
      })
    },
    // Timestamps arrive as ISO strings, epoch seconds or epoch milliseconds
    toMs(value) {
      if (value == null || value === '') return null
      if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
      const asNumber = Number(value)
      if (Number.isFinite(asNumber) && String(value).trim() !== '') {
        return asNumber < 1e12 ? asNumber * 1000 : asNumber
      }
      const date = new Date(value)
      return isNaN(date.getTime()) ? null : date.getTime()
    },
    // Live clock: a waiting phase grows while the turn is open. Stopped as soon
    // as the turn finishes, because nothing is left to grow.
    startClock() {
      this.stopClock()
      this.now = Date.now()
      if (!this.streaming) return
      this.clockTimer = setInterval(() => {
        this.now = Date.now()
      }, 1000)
    },
    stopClock() {
      if (this.clockTimer) clearInterval(this.clockTimer)
      this.clockTimer = null
    },
    // What a phase was waiting for, in the hover card
    phaseHint(phase) {
      const meta = phase?.meta || {}
      const kind = phase?.kind || ''
      if (kind === 'llm_request') {
        const parts = []
        if (meta.model) parts.push(`model ${meta.model}`)
        if (meta.message_count) parts.push(`${meta.message_count} messages`)
        if (!parts.length) parts.push(phase?.detail || 'Request sent')
        return parts.join(' · ')
      }
      if (kind === 'llm_response') {
        const parts = []
        if (meta.ttft_ms) parts.push(`first token after ${(Number(meta.ttft_ms) / 1000).toFixed(1)}s`)
        if (meta.completion_tokens) parts.push(`${meta.completion_tokens} tokens out`)
        if (!parts.length) parts.push(phase?.detail || 'Tokens arriving')
        return parts.join(' · ')
      }
      return phase?.detail || ''
    },
    // Phase metrics as hover-card arguments
    phaseMeta(phase) {
      const meta = phase?.meta || {}
      return Object.entries(meta)
        .filter(([, value]) => value != null && value !== '')
        .slice(0, this.maxTipArgs)
        .map(([key, value]) => {
          const text = String(value)
          return { key, value: text.length > 60 ? text.slice(0, 59) + '…' : text }
        })
    },
    // Everything the hover card and the colours need for one slice. The family
    // (and therefore the colour) comes from the shared vocabulary of
    // `utils/toolVisuals.js`, the same one the events table uses.
    describeSegment(entry) {
      // Silence: no event was recorded between two events. Neutral-warning and
      // named "No events" — this is the slice that means "something may be
      // wrong", and it must never be confused with a phase that is waiting.
      if (entry.kind === 'idle') {
        const visual = categoryVisual('idle')
        return {
          ...visual,
          categoryLabel: visual.label,
          label: 'No events',
          hint: 'Nothing was recorded during this time',
          status: 'idle',
          running: false,
          statusWord: 'Silent',
          durationText: this.formatMs(entry.ms),
          chipClass: 'bg-warning/15 text-warning',
          args: [],
          error: ''
        }
      }

      // Waiting recorded by the runtime: model request, streaming, notice,
      // interruption. It keeps its own colour per phase kind.
      if (entry.kind === 'phase') {
        const phase = entry.phase || {}
        const visual = phaseVisual(phase)
        const running = phase.status === 'running'
        const failed = phase.status === 'error'
        return {
          ...visual,
          categoryLabel: visual.label,
          label: phase.label || phase.kind || 'phase',
          hint: this.phaseHint(phase),
          status: phase.status || 'unknown',
          running,
          statusWord: failed ? 'Failed' : (running ? 'Running' : 'Done'),
          durationText: running ? 'in progress' : this.formatMs(Number(phase.duration_ms) || entry.ms),
          classes: running ? `${visual.classes} animate-pulse` : visual.classes,
          hoverClasses: visual.hoverClasses,
          chipClass: this.chipClass(visual),
          args: this.phaseMeta(phase),
          error: failed ? (phase.detail || '') : ''
        }
      }

      if (entry.kind === 'think') {
        const visual = categoryVisual('think')
        return {
          ...visual,
          categoryLabel: visual.label,
          label: this.streaming && !this.tools.length ? 'Starting…' : 'Thinking',
          hint: this.tools.length
            ? 'Model round'
            : (this.streaming ? 'Waiting for the first token' : 'Thinking & writing'),
          status: 'done',
          running: this.streaming,
          statusWord: this.streaming ? 'Running' : 'Done',
          durationText: this.formatMs(entry.ms),
          icon: visual.icon,
          textClass: visual.textClass,
          classes: visual.classes,
          hoverClasses: visual.hoverClasses,
          chipClass: 'bg-base-content/10 text-base-content/50',
          args: [],
          error: ''
        }
      }

      const tool = entry.tool || {}
      const status = tool.status || 'unknown'
      const visual = toolVisual(tool)
      return {
        ...visual,
        categoryLabel: visual.label,
        label: tool.tool || 'tool',
        hint: status === 'running' ? 'Running…' : '',
        status,
        running: status === 'running',
        statusWord: { running: 'Running', done: 'Done', error: 'Failed' }[status] || 'Unknown',
        // A running call has no measured duration yet — never invent one
        durationText: status === 'running' ? 'in progress' : this.formatMs(Number(tool.duration_ms) || entry.ms),
        // The family keeps its colour while the call runs; only the pulse says
        // "in progress", so a running execute never looks like a read.
        classes: status === 'running' ? `${visual.classes} animate-pulse` : visual.classes,
        hoverClasses: visual.hoverClasses,
        chipClass: this.chipClass(visual),
        args: this.toolArgs(tool).slice(0, this.maxTipArgs),
        error: tool.error || ''
      }
    },
    // Soft background for the family chip of the hover card
    chipClass(visual) {
      const key = visual?.key || 'other'
      if (key === 'error') return 'bg-error/15 text-error'
      if (key === 'read') return 'bg-info/15 text-info'
      if (key === 'edit') return 'bg-success/15 text-success'
      if (key === 'run') return 'bg-warning/15 text-warning'
      if (key === 'web') return 'bg-secondary/15 text-secondary'
      if (key === 'agent') return 'bg-primary/15 text-primary'
      return 'bg-base-content/10 text-base-content/50'
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
