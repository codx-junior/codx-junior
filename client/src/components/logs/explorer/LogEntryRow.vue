<template>
  <div class="w-full border-b border-base-300/30">
    <div
      class="group flex w-full cursor-pointer items-start gap-2 px-2 py-[2px] font-mono text-[11px] leading-[18px] transition-colors"
      :class="rowClass"
      @click="$emit('select', entry)"
      @dblclick="$emit('open', entry)"
    >
      <!-- Level badge -->
      <span class="mt-[1px] w-14 shrink-0">
        <span class="badge badge-xs border-0 font-mono uppercase" :class="levelStyle.badge">{{ levelLabel }}</span>
      </span>

      <!-- Timestamp (ms precision, rendered as plain text) -->
      <span class="mt-[1px] w-44 shrink-0 truncate opacity-60" :title="timeLabel">{{ timeLabel }}</span>

      <!-- Logger:line, dimmed -->
      <span class="mt-[1px] w-48 shrink-0 truncate opacity-50" :title="loggerLabel">{{ loggerLabel }}</span>

      <!-- Message: plain text segments, highlighted matches wrapped in spans -->
      <span class="min-w-0 flex-1" :class="expanded ? 'whitespace-pre-wrap break-words' : 'truncate'">
        <span
          v-for="(segment, index) in messageSegments"
          :key="index"
          :class="segment.match ? 'rounded-sm bg-warning/40' : ''"
        >{{ segment.text }}</span>
      </span>

      <!-- Inline affordances -->
      <span class="flex shrink-0 items-center gap-1 opacity-50 group-hover:opacity-100">
        <button
          v-if="hasContinuations"
          class="badge badge-xs badge-ghost gap-1 px-1"
          :title="expanded ? 'Collapse the continuation lines' : `Show ${continuationLabel} continuation lines`"
          @click.stop="expanded = !expanded"
        >
          <i class="fa-solid text-[9px]" :class="expanded ? 'fa-minus' : 'fa-plus'"></i>
          {{ continuationLabel }}
        </button>
        <button
          v-if="fieldRows.length"
          class="badge badge-xs badge-ghost gap-1 px-1"
          :title="fieldsOpen ? 'Hide the structured fields' : 'Show the structured fields'"
          @click.stop="fieldsOpen = !fieldsOpen"
        >
          <i class="fa-solid fa-brackets-curly text-[9px]"></i>
          {{ fieldRows.length }}
        </button>
      </span>
    </div>

    <!-- Structured fields (request / profiler / JSON keys) -->
    <div
      v-if="fieldsOpen"
      class="grid grid-cols-1 gap-x-4 gap-y-0.5 border-t border-dashed border-base-300/50 bg-base-200/40 px-2 py-1 font-mono text-[10px] sm:grid-cols-2"
    >
      <div v-for="field in fieldRows" :key="field.key" class="flex min-w-0 items-center gap-1">
        <span class="shrink-0 text-secondary">{{ field.key }}:</span>
        <span class="min-w-0 truncate opacity-70" :title="field.value">{{ field.value }}</span>
        <button
          v-if="field.filterable"
          class="shrink-0 opacity-40 hover:opacity-100"
          title="Filter by this field value"
          @click.stop="filterField(field)"
        >
          <i class="fa-solid fa-filter text-[9px]"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<script>
/** Level -> badge classes (daisyUI semantic colours). */
const LEVEL_BADGES = {
  critical: 'bg-fuchsia-600 text-white',
  error: 'badge-error',
  warn: 'badge-warning',
  info: 'badge-info',
  debug: 'badge-success',
  trace: 'badge-neutral',
  unknown: 'badge-ghost',
}

/** Level -> short label shown in the badge. */
const LEVEL_LABELS = {
  critical: 'crit',
  error: 'error',
  warn: 'warn',
  info: 'info',
  debug: 'debug',
  trace: 'trace',
  unknown: '?',
}

/** Longest value rendered inside the fields grid. */
const FIELD_MAX_CHARS = 320

export default {
  name: 'LogEntryRow',
  emits: ['select', 'open', 'filter'],
  props: {
    /** Entry as returned by `/api/logs/explorer/query`. */
    entry: { type: Object, required: true },
    /** Free-text needles highlighted inside the message (never HTML). */
    highlights: { type: Array, default: () => [] },
    /** Whether the entry is the current selection (keyboard / detail). */
    selected: { type: Boolean, default: false },
  },
  data() {
    return {
      expanded: false,
      fieldsOpen: false,
    }
  },
  computed: {
    level() {
      const value = String(this.entry?.level || 'unknown').toLowerCase()
      return LEVEL_BADGES[value] ? value : 'unknown'
    },
    levelLabel() {
      return LEVEL_LABELS[this.level]
    },
    levelStyle() {
      return { badge: LEVEL_BADGES[this.level] }
    },
    rowClass() {
      if (this.selected) return 'bg-primary/10 ring-1 ring-inset ring-primary/40'
      if (this.level === 'error' || this.level === 'critical') return 'bg-error/5 hover:bg-base-200/70'
      if (this.level === 'warn') return 'bg-warning/5 hover:bg-base-200/70'
      return 'hover:bg-base-200/70'
    },
    timeLabel() {
      const raw = this.entry?.timestamp
      if (raw) return raw
      const ms = Number(this.entry?.ts_ms)
      if (!Number.isFinite(ms)) return '—'
      const date = new Date(ms)
      const pad = (value, size = 2) => String(value).padStart(size, '0')
      return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`
    },
    loggerLabel() {
      const name = this.entry?.module || this.entry?.logger || ''
      const line = this.entry?.line
      if (line && name) return `${name}:${line}`
      if (line) return `line ${line}`
      return name || '—'
    },
    /** Whether the message owns continuation lines that can be expanded. */
    hasContinuations() {
      const message = String(this.entry?.message || '')
      return Number(this.entry?.continuation_lines || 0) > 0 || message.includes('\n')
    },
    continuationLabel() {
      const count = Number(this.entry?.continuation_lines || 0)
      if (count > 0) return `+${count}`
      const lines = String(this.entry?.message || '').split('\n').length - 1
      return `+${Math.max(lines, 1)}`
    },
    /**
     * Message split into plain-text segments. Matches are marked with
     * `match: true` and rendered inside `<span class="bg-warning/40">` by the
     * template — log content is never injected as raw HTML.
     */
    messageSegments() {
      const text = String(this.entry?.message || '')
      return this.splitByNeedles(text, this.highlights)
    },
    fieldRows() {
      const fields = this.entry?.fields || {}
      return Object.entries(fields).map(([key, value]) => ({
        key,
        value: this.stringifyValue(value),
        filterable: value === null || typeof value !== 'object',
      }))
    },
  },
  methods: {
    /** Case-insensitive, overlapping-safe split of `text` around `needles`. */
    splitByNeedles(text, needles) {
      const list = (needles || [])
        .map((needle) => String(needle || '').trim())
        .filter(Boolean)
        .slice(0, 12)
      if (!text) return [{ text: '', match: false }]
      if (!list.length) return [{ text, match: false }]

      const haystack = text.toLowerCase()
      const ranges = []
      list.forEach((needle) => {
        const target = needle.toLowerCase()
        let from = 0
        while (from <= haystack.length - target.length) {
          const at = haystack.indexOf(target, from)
          if (at === -1) break
          ranges.push([at, at + target.length])
          from = at + target.length
        }
      })
      if (!ranges.length) return [{ text, match: false }]

      ranges.sort((left, right) => left[0] - right[0])
      const merged = []
      ranges.forEach((range) => {
        const last = merged[merged.length - 1]
        if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1])
        else merged.push([range[0], range[1]])
      })

      const segments = []
      let cursor = 0
      merged.forEach(([start, end]) => {
        if (start > cursor) segments.push({ text: text.slice(cursor, start), match: false })
        segments.push({ text: text.slice(start, end), match: true })
        cursor = end
      })
      if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false })
      return segments
    },
    /** One-line, length-capped rendering of a structured field value. */
    stringifyValue(value) {
      if (value === null || value === undefined) return String(value)
      let text = ''
      if (typeof value === 'object') {
        try {
          text = JSON.stringify(value)
        } catch (err) {
          text = '[unserialisable]'
        }
      } else {
        text = String(value)
      }
      return text.length > FIELD_MAX_CHARS ? `${text.slice(0, FIELD_MAX_CHARS)}…` : text
    },
    /** Emit a ready-to-use query term for the backend query language. */
    filterField(field) {
      const value = String(field.value || '').replace(/"/g, '').slice(0, 200)
      if (!value) return
      this.$emit('filter', `${field.key}="${value}"`)
    },
  },
}
</script>
