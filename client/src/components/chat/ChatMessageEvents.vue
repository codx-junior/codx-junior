<script setup>
import Markdown from '../Markdown.vue'
</script>

<template>
  <!-- ── Processing details panel ──
       Lives INSIDE the sticky message header, directly under the turn timeline,
       so the bar that opens it and the detail it opens stay pinned together while
       the message body scrolls. Two tabs, because the run has two very different
       readings:
         · Events — the CHRONOLOGICAL table of everything that happened in the
           turn (message sent → run events → every tool call → answer), one row
           per event with its offset, family, status and duration. This is the
           DevTools-timeline reading: it is where a stall or a retry is spotted.
         · Tools — the same table applied to the CALLS: identity, arguments at a
           glance and a duration bar relative to the slowest one, expandable into
           request, result and output. Both tabs share the legend, the header and
           the family colours, so switching tab does not re-teach the reading.
       Families and colours come from `utils/toolVisuals.js`, the same vocabulary
       the timeline bar paints with, so a colour means the same thing in both.
       Opaque surface + border: it must not let content show through while stuck. -->
  <div
    :id="panelId"
    class="@container/panel flex flex-col max-h-[55vh] rounded-lg border border-base-300/70 bg-base-100 shadow-sm overflow-hidden"
  >

    <!-- ── State of the turn, in one line ──
         The whole reason this panel exists: is the turn WORKING or DEAD? It
         answers with what the runtime actually recorded — the phase in flight
         and how long ago the last event was written — so "waiting 2 minutes for
         the model" and "died two minutes ago" never look the same.
         It sits ABOVE the tabs because it describes the turn, not one tab. -->
    <div
      v-if="healthBanner"
      class="flex items-start gap-1.5 px-2 py-1 border-b border-base-300/40 text-[10px] leading-snug shrink-0"
      :class="healthBanner.class"
    >
      <i :class="[healthBanner.icon, 'text-[10px] mt-px shrink-0']" aria-hidden="true"></i>
      <span class="min-w-0 break-words">
        <span class="font-semibold">{{ healthBanner.title }}</span>
        <span class="opacity-75"> · {{ healthBanner.detail }}</span>
      </span>
    </div>

    <!-- ── Tabs ── -->
    <div class="flex items-center gap-1 px-1.5 pt-1 border-b border-base-300/50 shrink-0 bg-base-100">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="inline-flex items-center gap-1.5 px-2 py-1 -mb-px text-[11px] font-medium border-b-2 transition-colors duration-150"
        :class="activeTab === tab.key
          ? 'border-primary text-primary'
          : 'border-transparent text-base-content/50 hover:text-base-content'"
        :aria-selected="activeTab === tab.key ? 'true' : 'false'"
        role="tab"
        @click.stop="activeTab = tab.key"
      >
        <i :class="[tab.icon, 'text-[10px]']" aria-hidden="true"></i>
        <span>{{ tab.label }}</span>
        <span class="tabular-nums text-[10px] text-base-content/35">{{ tab.count }}</span>
      </button>

      <span class="grow"></span>

      <span v-if="errorCount" class="inline-flex items-center gap-1 text-[10px] text-error">
        <i class="fa-solid fa-circle-xmark text-[9px]" aria-hidden="true"></i>
        {{ errorCount }} failed
      </span>
    </div>

    <!-- ── Scrollable content: a long run never outgrows the viewport ── -->
    <div class="min-h-0 overflow-y-auto">

      <!-- ═════════════════════ TABS: Events (table) ═════════════════════ -->
      <div v-if="activeTab === 'events'" role="tabpanel">
        <!-- Legend: what the turn DID (tool families) and what it WAITED for
             (phases: model request, streaming, notices, interruptions) in the
             same row, so the wait can never hide behind the tool counts. -->
        <div v-if="eventFamilies.length" class="flex flex-wrap items-center gap-1 px-2 py-1.5 border-b border-base-300/40 bg-base-200/30">
          <span
            v-for="family in eventFamilies"
            :key="family.key"
            class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]"
            :class="family.chipClass"
            :title="`${family.label}: ${family.count} call(s), ${formatDuration(family.ms) || 'no duration'}`"
          >
            <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="family.visual.dotClass"></span>
            {{ family.label }}
            <span class="tabular-nums opacity-70">{{ family.count }}</span>
          </span>
        </div>

        <table v-if="eventRows.length" class="w-full text-[11px] border-collapse">
          <thead>
            <tr class="text-[9px] uppercase tracking-wide text-base-content/35">
              <th class="text-right font-medium pl-2 pr-1 py-1 w-8">#</th>
              <th class="text-right font-medium px-1 py-1 w-14">Offset</th>
              <th class="text-left font-medium px-1 py-1">Event</th>
              <th class="text-right font-medium px-1 py-1 w-16">Duration</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-base-300/40">
            <tr
              v-for="row in eventRows"
              :key="row.key"
              class="transition-colors duration-150 hover:bg-base-200/60"
            >
              <td class="text-right pl-2 pr-1 py-1 tabular-nums text-base-content/30">{{ row.index }}</td>
              <td class="text-right px-1 py-1 tabular-nums text-base-content/40">{{ row.offset || '—' }}</td>
              <td class="px-1 py-1 min-w-0">
                <div class="flex items-center gap-1.5 min-w-0">
                  <!-- Colour dot + icon: the family, the same as in the bar -->
                  <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="row.visual.dotClass" aria-hidden="true"></span>
                  <span class="w-3 flex justify-center shrink-0">
                    <span v-if="row.running" class="loading loading-spinner loading-xs" :class="row.visual.textClass"></span>
                    <i v-else :class="[row.visual.icon, row.visual.textClass, 'text-[10px]']" aria-hidden="true"></i>
                  </span>
                  <span class="font-mono font-semibold text-base-content truncate">{{ row.title }}</span>
                  <!-- Family in words, never colour alone -->
                  <span class="shrink-0 text-[9px] uppercase tracking-wide text-base-content/35">{{ row.visual.label }}</span>
                </div>
                <!-- The detail line is what turns the table into an explanation:
                     "First token after 2.4 s", the failing tool error, the reason
                     the application stopped. -->
                <p
                  v-if="row.detail"
                  class="pl-[1.375rem] text-[10px] leading-snug line-clamp-2 break-words"
                  :class="row.failed ? 'text-error/80' : 'text-base-content/40'"
                  :title="row.detail"
                >{{ row.detail }}</p>
              </td>
              <td class="text-right px-1 pr-2 py-1 tabular-nums whitespace-nowrap" :class="row.failed ? 'text-error' : 'text-base-content/40'">
                {{ row.durationText || '—' }}
              </td>
            </tr>
          </tbody>
        </table>

        <div v-else class="px-3 py-2 text-[11px] text-base-content/40">
          No events recorded for this message.
        </div>
      </div>

      <!-- ═════════════════════ TABS: Tools (execution wall) ═════════════════════ -->
      <div v-else role="tabpanel">
        <!-- ── Tools: the same table reading as Events ──
             One line per call — identity (family dot + status + name), the
             arguments at a glance and a duration with a bar relative to the
             slowest call — so the expensive or failed one is spotted without
             opening anything. The row expands into request, result and output. -->
        <div v-if="toolEvents.length" class="divide-y divide-base-300/40">
          <!-- Family legend: the same chips the Events tab uses -->
          <div class="flex flex-wrap items-center gap-1 px-2 py-1.5 bg-base-200/30">
            <span
              v-for="family in familySummary"
              :key="family.key"
              class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]"
              :class="family.chipClass"
              :title="`${family.label}: ${family.count} call(s), ${formatDuration(family.ms) || 'no duration'}`"
            >
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="family.visual.dotClass"></span>
              {{ family.label }}
              <span class="tabular-nums opacity-70">{{ family.count }}</span>
            </span>
            <span class="grow"></span>
            <span v-if="maxToolMs" class="text-[10px] tabular-nums text-base-content/35">
              slowest {{ formatDuration(maxToolMs) }}
            </span>
          </div>

          <!-- Column labels, matching the Events table -->
          <div class="grid grid-cols-[1.75rem_1fr_5.5rem_0.75rem] items-center gap-2 px-2 py-1 text-[9px] uppercase tracking-wide text-base-content/35">
            <span class="text-right">#</span>
            <span>Tool · arguments</span>
            <span class="text-right">Duration</span>
            <span></span>
          </div>

          <div
            v-for="tool in tools"
            :key="tool.event.tool_call_id || ('tool-' + tool.index)"
            :class="tool.failed && 'bg-error/5'"
          >
            <!-- Row: the whole line is the target, mouse or keyboard -->
            <div
              class="grid grid-cols-[1.75rem_1fr_5.5rem_0.75rem] items-center gap-2 px-2 py-1 cursor-pointer transition-colors duration-150 hover:bg-base-200/60 focus:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
              role="button"
              tabindex="0"
              :aria-expanded="isToolOpen(tool.index) ? 'true' : 'false'"
              :title="(isToolOpen(tool.index) ? 'Hide details' : 'Show details') + ' · ' + (tool.event.status || 'unknown')"
              @click="toggleTool(tool.index)"
              @keydown.enter.prevent="toggleTool(tool.index)"
              @keydown.space.prevent="toggleTool(tool.index)"
            >
              <span class="text-right text-[10px] tabular-nums text-base-content/30">{{ tool.index + 1 }}</span>

              <div class="flex items-center gap-1.5 min-w-0">
                <!-- Family dot + status icon: colour is never the only channel -->
                <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="tool.visual.dotClass" aria-hidden="true"></span>
                <span class="w-3 flex justify-center shrink-0">
                  <span v-if="tool.running" class="loading loading-spinner loading-xs text-info"></span>
                  <i v-else-if="tool.event.status === 'done'" class="fa-solid fa-circle-check text-success text-[10px]"></i>
                  <i v-else-if="tool.failed" class="fa-solid fa-circle-xmark text-error text-[10px]"></i>
                  <i v-else class="fa-solid fa-circle text-base-content/30 text-[10px]"></i>
                </span>
                <i :class="[tool.visual.icon, tool.visual.textClass, 'text-[10px] shrink-0']" aria-hidden="true"></i>
                <span class="font-mono text-xs font-semibold text-base-content truncate">{{ tool.event.tool }}</span>
                <!-- Family in words, never colour alone -->
                <span class="shrink-0 text-[9px] uppercase tracking-wide text-base-content/35">{{ tool.visual.label }}</span>
                <span v-if="tool.detail" class="@max-sm/panel:hidden truncate font-mono text-[10px] text-base-content/45" :title="tool.detail">{{ tool.detail }}</span>
              </div>

              <!-- Duration + bar relative to the slowest call of the turn -->
              <div class="min-w-0">
                <div
                  class="text-right text-[10px] tabular-nums whitespace-nowrap"
                  :class="tool.failed ? 'text-error' : (tool.slow ? 'text-base-content/70' : 'text-base-content/40')"
                >
                  {{ tool.durationText || '—' }}
                </div>
                <div class="mt-0.5 h-[3px] rounded-full bg-base-300/50 overflow-hidden" aria-hidden="true">
                  <div class="h-full rounded-full" :class="tool.barClass" :style="{ width: tool.barWidth }"></div>
                </div>
              </div>

              <i
                class="fa-solid fa-chevron-down text-[10px] text-base-content/40 transition-transform duration-150"
                :class="isToolOpen(tool.index) && 'rotate-180'"
              ></i>
            </div>

            <!-- The failure stays in the summary: no need to expand to see it -->
            <div
              v-if="tool.failed && tool.event.error"
              class="pl-[2.75rem] pr-2 pb-1 flex items-start gap-1 text-[10px] text-error"
            >
              <i class="fa-solid fa-triangle-exclamation text-[9px] mt-0.5 shrink-0"></i>
              <span class="line-clamp-1 break-all" :title="tool.event.error">{{ tool.event.error }}</span>
            </div>

            <!-- ── Details: the raw material of the call ── -->
            <div v-if="isToolOpen(tool.index)" class="pl-[2.75rem] pr-2 pb-2 space-y-2 bg-base-200/30">
              <!-- One meta line: status, wall clock, weight, arity -->
              <div class="flex flex-wrap items-center gap-x-2 text-[10px] text-base-content/40">
                <span class="uppercase tracking-wide">{{ tool.event.status || 'unknown' }}</span>
                <span v-if="tool.time" class="tabular-nums">· {{ tool.time }}</span>
                <span v-if="tool.durationText" class="tabular-nums">· {{ tool.durationText }}</span>
                <span v-if="tool.args.length" class="tabular-nums">· {{ tool.args.length }} args</span>
              </div>

              <!-- All arguments: the row only carries the first two -->
              <div class="grid grid-cols-[minmax(4.5rem,7rem)_1fr] gap-x-3 gap-y-0.5 items-baseline">
                <template v-for="arg in tool.args" :key="arg.key">
                  <span class="text-[10px] font-medium uppercase tracking-wide text-base-content/40 truncate" :title="arg.key">
                    {{ arg.key }}
                  </span>
                  <span class="font-mono text-[11px] text-base-content/60 truncate" :title="arg.full">
                    {{ arg.display }}
                  </span>
                </template>
              </div>

              <!-- Raw request -->
              <div v-if="hasRequest(tool.event)">
                <p class="text-[10px] uppercase tracking-wide text-base-content/40 mb-1">Request</p>
                <pre class="text-[11px] leading-snug bg-base-300/40 rounded-md p-2 overflow-auto max-h-40">{{ formatRequest(tool.event.request) }}</pre>
              </div>

              <!-- Result preview -->
              <div v-if="tool.event.response">
                <p class="text-[10px] uppercase tracking-wide text-base-content/40 mb-1">Result</p>
                <pre class="text-[11px] leading-snug bg-base-300/40 rounded-md p-2 overflow-auto max-h-40">{{ tool.event.response }}</pre>
              </div>

              <!-- Full error: the row only clamps it to one line -->
              <div v-if="tool.event.error">
                <p class="text-[10px] uppercase tracking-wide text-error/70 mb-1">Error</p>
                <pre class="text-[11px] leading-snug bg-error/10 text-error rounded-md p-2 overflow-auto max-h-32 whitespace-pre-wrap break-all">{{ tool.event.error }}</pre>
              </div>

              <!-- User-facing output (kept OUT of the message body — opt-in) -->
              <div v-if="tool.event.output">
                <button class="btn btn-xs btn-ghost gap-1" @click="toggleOutput(tool.index)">
                  <i class="fa-solid fa-eye"></i>
                  {{ isOutputOpen(tool.index) ? 'Hide output' : 'Show output' }}
                </button>
                <div
                  v-if="isOutputOpen(tool.index)"
                  class="mt-1 rounded-md border border-base-300/50 bg-base-100/40 p-2 max-h-96 overflow-auto"
                >
                  <Markdown :text="tool.event.output"></Markdown>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="px-3 py-2 text-[11px] text-base-content/40">
          No tool ran for this message.
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { categoryVisual, phaseVisual, toolVisual } from '../../utils/toolVisuals.js'

export default {
  // `panelId` is the id the timeline strip points at with aria-controls
  props: ['message', 'panelId'],
  data() {
    return {
      // The wall answers "what is it doing now" (the usual reason to open the
      // panel); the events table is one click away for the forensic reading.
      activeTab: 'tools',
      openTools: {},
      openOutputs: {},
      // Wall clock for the live "waiting 2m 36s" counters. It ticks ONLY while
      // the turn is open: a finished message has nothing left to count, and a
      // dead one must stop counting too (see `healthBanner`).
      now: Date.now(),
      clockTimer: null
    }
  },
  watch: {
    // The panel can be opened before the answer starts (open phase) and stay
    // open when it ends: re-arm or stop the clock with the state of the turn.
    'message.done'() {
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
    toolEvents() {
      return this.message?.tool_events || []
    },
    lifecycleEvents() {
      return this.message?.lifecycle_events || []
    },
    // PHASES: the WAITING of the turn — request sent, tokens streaming, runtime
    // notices, budget check, and the interruption stamped by the recovery worker
    // when the process died. They are the events that explain a turn which is
    // slow but alive; without them the table showed only tools and a two-minute
    // wait for the model looked exactly like a dead turn.
    phaseEvents() {
      return this.message?.phase_events || []
    },
    // The most recent thing the runtime recorded, whatever its kind — the
    // denominator of "how long since anything happened". `created_at` is the
    // floor: a turn that died before writing anything still counts its silence
    // from the moment it started (never from `updated_at`, which moves when the
    // message is read or edited).
    latestEventAt() {
      const stamps = []
      const push = (value) => {
        const time = this.parseTime(value)
        if (time != null) stamps.push(time)
      }
      this.phaseEvents.forEach(phase => {
        push(phase.finished_at)
        push(phase.started_at)
      })
      this.toolEvents.forEach(tool => {
        push(tool.finished_at)
        push(tool.started_at)
      })
      this.lifecycleEvents.forEach(event => {
        push(event.finished_at)
        push(event.started_at)
      })
      push(this.message?.created_at)
      return stamps.length ? Math.max(...stamps) : null
    },
    // The phase currently in flight (at most one: the runtime closes the wait
    // when the stream starts).
    openPhase() {
      return this.phaseEvents.find(phase => phase.status === 'running') || null
    },
    openTool() {
      return this.toolEvents.find(tool => tool.status === 'running') || null
    },
    errorCount() {
      return this.toolEvents.filter(tool => tool.status === 'error').length
    },
    tabs() {
      return [
        { key: 'events', label: 'Events', icon: 'fa-solid fa-table-list', count: this.eventRows.length },
        { key: 'tools', label: 'Tools', icon: 'fa-solid fa-wrench', count: this.toolEvents.length }
      ]
    },
    // One row per call, resolved once per render: identity, the arguments at a
    // glance and the weight of the call relative to the rest of the turn.
    tools() {
      const slowest = this.maxToolMs || 1
      return this.toolEvents.map((event, index) => {
        const args = this.toolArgs(event)
        const visual = toolVisual(event)
        const failed = event.status === 'error'
        const running = event.status === 'running'
        const ms = Number(event.duration_ms) || 0
        return {
          index,
          event,
          args,
          visual,
          failed,
          running,
          time: this.eventTime(event),
          // Two arguments are enough for the row; all of them live in the details
          detail: args.slice(0, 2).map(arg => `${arg.key}=${arg.display}`).join(' · '),
          durationText: this.formatDuration(event.duration_ms),
          // Slow is a threshold, not a comparison: 5s of a 6 minute turn is
          // still a stall the user wants to see.
          slow: ms >= 5000,
          // Bar width against the slowest call. The 2% floor keeps a fast call
          // visible: an empty bar would read as "no data".
          barWidth: (running ? 100 : Math.max(2, Math.round((ms / slowest) * 100))) + '%',
          barClass: failed ? 'bg-error/70' : (running ? 'bg-info/60 animate-pulse' : visual.classes)
        }
      })
    },
    // Slowest call of the turn: the 100% of the duration bars
    maxToolMs() {
      return this.toolEvents.reduce((max, tool) => Math.max(max, Number(tool.duration_ms) || 0), 0)
    },
    // ── Events table ──
    // One row per thing that happened in the turn, in chronological order, with
    // its offset from the start: message sent → run lifecycle → every tool call
    // → answer received. Rows are only REORDERED when every row carries a
    // timestamp: mixing measured and unmeasured rows would invent an order the
    // data does not have. The offset itself is still rendered for the rows that
    // do have one, measured against the earliest known timestamp, because a
    // timeline without offsets cannot be read.
    eventRows() {
      const message = this.message || {}
      const rows = []
      const turnStart = this.parseTime(message?.meta_data?.start_time) || this.parseTime(message.created_at)

      // The backend stores a duration per tool call but NO timestamp, so a row
      // without one gets a RECONSTRUCTED offset: the model time that no tool
      // accounts for is spread over the gaps exactly like the timeline bar does
      // (one gap before every call plus one before the answer). A reconstructed
      // offset therefore points where the matching bar segment sits.
      const totalMs = this.totalDurationMs
      const toolsMs = this.toolEvents.reduce((sum, tool) => sum + (Number(tool.duration_ms) || 0), 0)
      const gapMs = this.toolEvents.length ? Math.max(totalMs - toolsMs, 0) / (this.toolEvents.length + 1) : 0
      let cursor = gapMs

      if (turnStart != null) {
        rows.push({
          key: 'sent',
          visual: categoryVisual('send'),
          title: 'Message sent',
          detail: 'Turn handed to the model',
          duration: null,
          failed: false,
          at: turnStart,
          derived: 0
        })
      }

      this.lifecycleEvents.forEach((event, index) => {
        const failed = !!event.error || event.status === 'error'
        rows.push({
          key: 'run-' + (event.run_id || index),
          visual: categoryVisual(failed ? 'error' : 'lifecycle'),
          title: `Run ${event.status || 'event'}`,
          detail: event.error || `run ${this.shortRunId(event.run_id)}`,
          duration: event.duration_ms,
          failed,
          at: this.parseTime(event.started_at || event.created_at || event.timestamp)
        })
      })


      // A turn can fail WITHOUT any tool failing: the model died, the budget
      // ran out, the provider refused. The message carries that error itself and
      // it used to be invisible in the table.
      const lifecycleError = this.lifecycleEvents.some(event => !!event.error)
      if (message.error && !message.done && !lifecycleError) {
        rows.push({
          key: 'turn-error',
          visual: categoryVisual('error'),
          title: 'Turn error',
          detail: message.error,
          duration: null,
          failed: true,
          at: this.parseTime(message?.meta_data?.interrupted_at)
        })
      }

      // PHASES: the waiting that is NOT a tool call — "waiting for the model",
      // "reading the response", runtime notices, bounces, interruptions. They
      // carry REAL timestamps from the runtime, so they are placed by time and
      // never reconstructed. Without them the table showed only tools and hid
      // the minutes the turn spent waiting for the model.
      this.phaseEvents.forEach((phase, index) => {
        rows.push({
          key: 'phase-' + index + '-' + (phase.kind || 'phase'),
          visual: phaseVisual(phase),
          title: phase.label || phase.kind || 'phase',
          detail: this.phaseDetail(phase),
          duration: phase.duration_ms,
          failed: phase.status === 'error' || phase.kind === 'interrupted',
          running: phase.status === 'running',
          at: this.parseTime(phase.started_at)
        })
      })

      this.toolEvents.forEach((event, index) => {
        const visual = toolVisual(event)
        const args = this.toolArgs(event)
        rows.push({
          key: event.tool_call_id || 'tool-' + index,
          visual,
          title: event.tool || 'tool',
          detail: args.length ? `${args[0].key}=${args[0].display}` : '—',
          duration: event.duration_ms,
          failed: event.status === 'error',
          running: event.status === 'running',
          at: this.parseTime(event.started_at || event.created_at || event.timestamp),
          derived: cursor
        })
        cursor += (Number(event.duration_ms) || 0) + gapMs
      })
      if (message.done) {
        const failed = !!message.error
        rows.push({
          key: 'answer',
          visual: categoryVisual(failed ? 'error' : 'answer'),
          title: failed ? 'Turn ended with an error' : 'Answer received',
          detail: failed
            ? message.error
            : (this.totalDurationText ? `Turn finished in ${this.totalDurationText}` : 'Turn finished'),
          duration: this.totalDurationMs,
          failed,
          // No `at`: `updated_at` also moves when the message is read or edited,
          // which would place the answer far beyond the end of its own turn.
          // The turn duration is the only honest position.
          at: null,
          derived: totalMs
        })
      }

      // ── Order ──
      // Real timestamps win whenever the runtime recorded them; the rows that
      // carry none (an old message, a tool row without `started_at`) fall back
      // to their reconstructed offset. Both are expressed relative to the START
      // of the turn, so a mixed list still sorts into one coherent chronology
      // instead of collapsing to insertion order the moment one row is untimed.
      const base = turnStart != null
        ? turnStart
        : rows.reduce(
          (earliest, row) => (row.at != null && (earliest == null || row.at < earliest) ? row.at : earliest),
          null
        )
      const offsetOf = (row) => (row.at != null && base != null ? Math.max(0, row.at - base) : (row.derived || 0))
      const timed = rows.some(row => row.at != null)
      if (timed) rows.sort((a, b) => offsetOf(a) - offsetOf(b))

      return rows.map((row, index) => ({
        ...row,
        index: index + 1,
        offset: this.formatOffset(offsetOf(row)),
        durationText: this.formatDuration(row.duration)
      }))
    },
    // What the turn spent its time on, by family — the legend of the bar
    familySummary() {
      const groups = {}
      this.toolEvents.forEach(event => {
        const visual = toolVisual(event)
        if (!groups[visual.key]) groups[visual.key] = { key: visual.key, visual, count: 0, ms: 0 }
        groups[visual.key].count += 1
        groups[visual.key].ms += Number(event.duration_ms) || 0
      })
      return Object.values(groups)
        .sort((a, b) => b.ms - a.ms || b.count - a.count)
        .map(group => ({
          ...group,
          label: group.visual.label,
          chipClass: this.chipClass(group.visual)
        }))
    },
    // Phases grouped by family, for the legend of the Events tab: the time the
    // turn spent WAITING has to be as visible in the summary as the tool
    // families are, otherwise the legend still reads "it only called tools".
    phaseFamilies() {
      const groups = {}
      this.phaseEvents.forEach(phase => {
        const visual = phaseVisual(phase)
        if (!groups[visual.key]) groups[visual.key] = { key: visual.key, visual, count: 0, ms: 0 }
        groups[visual.key].count += 1
        groups[visual.key].ms += Number(phase.duration_ms) || 0
      })
      return Object.values(groups)
        .sort((a, b) => b.ms - a.ms || b.count - a.count)
        .map(group => ({
          ...group,
          label: group.visual.label,
          chipClass: this.chipClass(group.visual)
        }))
    },
    // Legend of the Events tab: what the turn DID (tool families) followed by
    // what it WAITED for (phases), merged by family so a family never appears
    // twice. The Tools tab keeps the tool-only summary.
    eventFamilies() {
      const merged = new Map()
      const add = (family) => {
        const current = merged.get(family.key)
        if (current) {
          current.count += family.count
          current.ms += family.ms
        } else {
          merged.set(family.key, { ...family })
        }
      }
      this.familySummary.forEach(add)
      this.phaseFamilies.forEach(add)
      return [...merged.values()].sort((a, b) => b.ms - a.ms || b.count - a.count)
    },
    // ── Is the turn WORKING or DEAD? ──
    // The single question this panel exists for, answered from what the runtime
    // actually recorded — never guessed:
    //   · a turn with an open phase is WAITING, and says for how long;
    //   · a turn with an open tool is RUNNING it;
    //   · a turn with nothing in flight and nothing new is what DEAD looks like;
    //   · a turn cut by a restart is reported as interrupted instead of showing
    //     a spinner that would spin for ever.
    // The clock (`now`) ticks only while the turn is open, so these counters are
    // live while it matters and frozen once it is over.
    healthBanner() {
      const message = this.message || {}
      const silentMs = this.latestEventAt != null ? Math.max(0, this.now - this.latestEventAt) : null

      // 1. The process stopped while this turn was running (power off, restart).
      if (message?.meta_data?.interrupted) {
        const at = this.parseTime(message?.meta_data?.interrupted_at)
        const facts = [message.error || 'The application stopped']
        if (at != null) {
          facts.push(`detected at ${new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`)
        }
        if (silentMs != null) facts.push(`${this.formatDuration(silentMs)} without a new event`)
        return {
          class: 'bg-error/10 text-error',
          icon: 'fa-solid fa-plug-circle-xmark',
          title: 'Interrupted',
          detail: facts.join(' · ')
        }
      }

      // 2. The turn is over: report HOW it ended, errors first.
      if (message.done) {
        if (message.error || this.errorCount) {
          const facts = []
          if (message.error) facts.push(message.error)
          if (this.errorCount) {
            facts.push(`${this.errorCount} tool call${this.errorCount > 1 ? 's' : ''} failed`)
          }
          facts.push(this.totalDurationText || 'finished')
          return {
            class: 'bg-error/10 text-error',
            icon: 'fa-solid fa-circle-xmark',
            title: 'Finished with errors',
            detail: facts.join(' · ')
          }
        }
        const done = [
          this.totalDurationText,
          `${this.toolEvents.length} tool${this.toolEvents.length === 1 ? '' : 's'}`,
          `${this.phaseEvents.length} phase${this.phaseEvents.length === 1 ? '' : 's'}`
        ]
        return {
          class: 'bg-success/10 text-success/90',
          icon: 'fa-solid fa-flag-checkered',
          title: 'Completed',
          detail: done.filter(Boolean).join(' · ')
        }
      }

      // 3. Waiting: the whole point of recording phases.
      const phase = this.openPhase
      if (phase) {
        const started = this.parseTime(phase.started_at)
        const waited = started != null ? Math.max(0, this.now - started) : null
        const streaming = phase.kind === 'llm_response'
        const facts = []
        if (waited != null) {
          facts.push(streaming
            ? `streaming for ${this.formatDuration(waited)}`
            : `${this.formatDuration(waited)} without a first token`)
        }
        if (phase.detail) facts.push(phase.detail)
        // A wait is normal work; a VERY long one is the thing worth flagging.
        // Three minutes with no token is past any healthy time-to-first-token.
        const level = waited == null ? 'info' : (waited >= 180000 ? 'error' : (waited >= 60000 ? 'warning' : 'info'))
        if (level === 'error') facts.push('possibly stuck')
        return {
          class: this.bannerClass(level),
          icon: level === 'error'
            ? 'fa-solid fa-triangle-exclamation'
            : (streaming ? 'fa-solid fa-brain' : 'fa-solid fa-hourglass-half'),
          title: phase.label || (streaming ? 'Reading the response' : 'Waiting for the model'),
          detail: facts.join(' · ')
        }
      }

      // 4. A tool is in flight: also working, also measurable.
      const tool = this.openTool
      if (tool) {
        const started = this.parseTime(tool.started_at)
        const running = started != null ? Math.max(0, this.now - started) : null
        return {
          class: this.bannerClass('info'),
          icon: 'fa-solid fa-wrench',
          title: `Running ${tool.tool || 'a tool'}`,
          detail: [
            running != null ? `${this.formatDuration(running)} elapsed` : null,
            'waiting for it to return'
          ].filter(Boolean).join(' · ')
        }
      }

      // 5. Nothing in flight and nothing new: this is what dead looks like.
      const rows = this.eventRows
      const last = rows.length ? rows[rows.length - 1] : null
      const facts = []
      if (silentMs != null) facts.push(`${this.formatDuration(silentMs)} since the last recorded event`)
      if (last) facts.push(`last: ${last.title}${last.offset ? ` ${last.offset}` : ''}`)
      const level = silentMs == null ? 'info' : (silentMs >= 120000 ? 'error' : (silentMs >= 30000 ? 'warning' : 'info'))
      return {
        class: this.bannerClass(level),
        icon: level === 'error'
          ? 'fa-solid fa-plug-circle-xmark'
          : (level === 'warning' ? 'fa-solid fa-hourglass-end' : 'fa-solid fa-circle-notch'),
        title: level === 'error'
          ? 'No activity — the turn may be dead'
          : (level === 'warning' ? 'No activity for a while' : 'Working'),
        detail: facts.join(' · ') || 'Waiting for the first event of the turn'
      }
    },
    // Duration of the whole turn (metadata first, tool sum as fallback)
    totalDurationMs() {
      const reported = Number(this.message?.meta_data?.time_taken)
      if (Number.isFinite(reported) && reported > 0) return reported * 1000
      return this.toolEvents.reduce((sum, tool) => sum + (Number(tool.duration_ms) || 0), 0)
    },
    totalDurationText() {
      return this.formatDuration(this.totalDurationMs)
    }
  },
  methods: {
    // Soft chip of a family (colour + words, never colour alone)
    chipClass(visual) {
      const key = visual?.key || 'other'
      if (key === 'error') return 'bg-error/15 text-error'
      if (key === 'interrupted') return 'bg-error/15 text-error'
      if (key === 'read') return 'bg-info/15 text-info'
      if (key === 'edit') return 'bg-success/15 text-success'
      if (key === 'run' || key === 'idle') return 'bg-warning/15 text-warning'
      if (key === 'web') return 'bg-secondary/15 text-secondary'
      if (key === 'agent' || key === 'answer') return 'bg-primary/15 text-primary'
      if (key === 'llm' || key === 'lifecycle') return 'bg-accent/15 text-accent'
      return 'bg-base-content/10 text-base-content/50'
    },
    // Background of the health banner, by severity
    bannerClass(level) {
      if (level === 'error') return 'bg-error/10 text-error'
      if (level === 'warning') return 'bg-warning/10 text-warning'
      return 'bg-info/10 text-info/90'
    },
    // ── Live clock ──
    // "Waiting 2m 36s" is only useful if it ticks. The interval exists ONLY
    // while the turn is open: a finished turn has nothing left to count, and
    // re-rendering a 100-row table every second for nothing is a waste.
    startClock() {
      this.stopClock()
      this.now = Date.now()
      if (this.message?.done) return
      this.clockTimer = setInterval(() => {
        this.now = Date.now()
      }, 1000)
    },
    stopClock() {
      if (this.clockTimer) clearInterval(this.clockTimer)
      this.clockTimer = null
    },
    // One line per phase: what it was waiting for and what it measured. Every
    // number comes from the runtime (TTFT, tokens, duration) — the client never
    // invents a timing.
    phaseDetail(phase) {
      const meta = phase?.meta || {}
      const parts = []
      const kind = phase?.kind || ''
      if (kind === 'llm_request') {
        if (meta.model) parts.push(`model ${meta.model}`)
        if (meta.message_count) parts.push(`${meta.message_count} messages`)
        if (phase.status === 'running') parts.push('no first token yet')
      } else if (kind === 'llm_response') {
        if (meta.ttft_ms) parts.push(`first token after ${(Number(meta.ttft_ms) / 1000).toFixed(1)}s`)
        if (meta.completion_tokens) parts.push(`${meta.completion_tokens} tokens out`)
        if (meta.total_tokens) parts.push(`${meta.total_tokens} total`)
        if (phase.status === 'running') parts.push('streaming')
      } else if (kind === 'interrupted') {
        parts.push(meta.reason || phase.detail || 'The process stopped while this was running')
      }
      // The backend's own sentence is always worth keeping when it adds something
      const detail = String(phase?.detail || '').trim()
      if (detail && !parts.some(part => detail.includes(part) || part.includes(detail))) {
        parts.unshift(detail)
      }
      if (!parts.length) {
        parts.push(phase?.label || 'phase')
      }
      return parts.join(' · ')
    },
    formatDuration(ms) {
      if (ms == null) return ''
      if (ms < 1000) return Math.round(ms) + ' ms'
      if (ms < 60000) return (ms / 1000).toFixed(2) + ' s'
      const minutes = Math.floor(ms / 60000)
      const seconds = Math.round((ms % 60000) / 1000)
      return `${minutes}m ${String(seconds).padStart(2, '0')}s`
    },
    // Offset from the start of the turn, `+0.00s` style
    formatOffset(ms) {
      const value = Math.max(0, Number(ms) || 0)
      if (value < 1000) return '+' + Math.round(value) + 'ms'
      if (value < 60000) return '+' + (value / 1000).toFixed(2) + 's'
      const minutes = Math.floor(value / 60000)
      const seconds = ((value % 60000) / 1000).toFixed(0).padStart(2, '0')
      return `+${minutes}m${seconds}s`
    },
    // Timestamps arrive as epoch seconds, epoch milliseconds or ISO strings
    parseTime(value) {
      if (value == null || value === '') return null
      if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
      const asNumber = Number(value)
      if (Number.isFinite(asNumber) && String(value).trim() !== '') {
        return asNumber < 1e12 ? asNumber * 1000 : asNumber
      }
      const date = new Date(value)
      return isNaN(date.getTime()) ? null : date.getTime()
    },
    formatRequest(request) {
      if (!request || Object.keys(request).length === 0) return '{}'
      try {
        return JSON.stringify(request, null, 2)
      } catch {
        return String(request)
      }
    },
    hasRequest(toolEvent) {
      return toolEvent.request && Object.keys(toolEvent.request).length > 0
    },
    shortRunId(runId) {
      return runId ? String(runId).slice(0, 8) : '—'
    },
    // Tool arguments as flat { key, full, display } entries for the summary grid
    toolArgs(toolEvent) {
      const request = this.toolRequest(toolEvent)
      if (!request) return []
      return Object.entries(request).map(([key, value]) => {
        const isSimple = value === null || ['string', 'number', 'boolean'].includes(typeof value)
        const full = isSimple ? String(value) : this.stringifyValue(value)
        return { key, full, display: this.truncateValue(full) }
      })
    },
    toolRequest(toolEvent) {
      const request = toolEvent?.request
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
    truncateValue(value) {
      const text = String(value ?? '').replace(/\s+/g, ' ').trim()
      return text.length > 72 ? text.slice(0, 71) + '…' : text
    },
    stringifyValue(value) {
      try {
        return JSON.stringify(value)
      } catch {
        return String(value)
      }
    },
    // Wall rows show the wall-clock time of the execution when the backend provides it
    eventTime(event) {
      const timestamp = this.parseTime(event?.started_at || event?.created_at || event?.timestamp)
      if (timestamp == null) return null
      return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    },
    isToolOpen(index) {
      return !!this.openTools[index]
    },
    toggleTool(index) {
      this.openTools[index] = !this.openTools[index]
    },
    isOutputOpen(index) {
      return !!this.openOutputs[index]
    },
    toggleOutput(index) {
      this.openOutputs[index] = !this.openOutputs[index]
    }
  }
}
</script>
