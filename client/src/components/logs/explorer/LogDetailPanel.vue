<template>
  <aside class="flex h-full w-full flex-col border-l border-base-300 bg-base-100 shadow-2xl">
    <!-- Header -->
    <header class="flex shrink-0 items-start gap-2 border-b border-base-300 px-3 py-2">
      <span class="badge badge-sm border-0 font-mono uppercase" :class="levelStyle.badge">{{ levelLabel }}</span>
      <div class="min-w-0 flex-1">
        <p class="truncate font-mono text-[11px] opacity-70" :title="timeLabel">{{ timeLabel }}</p>
        <p class="truncate font-mono text-[10px] opacity-50" :title="loggerLabel">{{ loggerLabel }}</p>
      </div>
      <button class="btn btn-xs btn-circle btn-ghost shrink-0" title="Close (Esc)" @click="$emit('close')">
        <i class="fa-solid fa-xmark text-xs"></i>
      </button>
    </header>

    <div class="min-h-0 flex-1 space-y-3 overflow-auto p-3">
      <div v-if="loading" class="flex items-center gap-2 text-xs opacity-60">
        <i class="fa-solid fa-circle-notch animate-spin text-xs"></i>
        Loading the entry…
      </div>

      <!-- Full message -->
      <section>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-50">Message</p>
        <p class="whitespace-pre-wrap break-words rounded-box bg-base-200/60 p-2 font-mono text-[11px] leading-5">{{ message }}</p>
      </section>

      <!-- Actions -->
      <div class="flex flex-wrap items-center gap-1">
        <button class="btn btn-xs gap-1" title="Copy the raw entry to the clipboard" @click="copyEntry">
          <i class="fa-solid text-[10px]" :class="copied ? 'fa-check text-success' : 'fa-copy'"></i>
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button
          v-if="loggerValue"
          class="btn btn-xs gap-1"
          title="Filter the query by this logger"
          @click="filterBy('logger', loggerValue)"
        >
          <i class="fa-solid fa-filter text-[10px]"></i>
          Filter logger
        </button>
        <button class="btn btn-xs gap-1" :disabled="contextLoading" title="Load the entries around this one" @click="$emit('context')">
          <i class="fa-solid text-[10px]" :class="contextLoading ? 'fa-circle-notch animate-spin' : 'fa-arrows-up-down'"></i>
          Surrounding
        </button>
      </div>

      <!-- Parsed fields -->
      <section v-if="fieldRows.length">
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-50">Fields</p>
        <table class="table table-xs">
          <tbody>
            <tr v-for="field in fieldRows" :key="field.key">
              <td class="w-1/3 whitespace-nowrap align-top font-mono text-[11px] text-secondary">{{ field.key }}</td>
              <td class="break-all font-mono text-[11px] opacity-80">{{ field.value }}</td>
              <td class="w-6 align-top">
                <button
                  v-if="field.filterable"
                  class="opacity-40 hover:opacity-100"
                  title="Filter by this value"
                  @click="filterBy(field.key, field.rawValue)"
                >
                  <i class="fa-solid fa-filter text-[10px]"></i>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Pretty JSON of the structured fields -->
      <section v-if="prettyFields">
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-50">Fields as JSON</p>
        <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-box bg-base-200/60 p-2 font-mono text-[10px] leading-4">{{ prettyFields }}</pre>
      </section>

      <!-- Verbatim raw text (`GET /entry/{id}`) -->
      <section v-if="rawText">
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-50">Raw</p>
        <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-box bg-base-200/60 p-2 font-mono text-[10px] leading-4">{{ rawText }}</pre>
      </section>

      <!-- Surrounding entries (`GET /context/{id}`) -->
      <section v-if="contextEntries.length || contextLoading">
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-50">
          Surrounding entries
          <span class="opacity-60">({{ contextEntries.length }})</span>
        </p>
        <div class="max-h-72 overflow-auto rounded-box border border-base-300/60">
          <button
            v-for="item in contextEntries"
            :key="item.id"
            class="flex w-full items-start gap-2 border-b border-base-300/40 px-2 py-1 text-left font-mono text-[10px] leading-4 last:border-b-0 hover:bg-base-200/60"
            :title="`Go to ${item.id}`"
            @click="$emit('select-entry', item)"
          >
            <span class="w-10 shrink-0 uppercase opacity-50">{{ item.level }}</span>
            <span class="w-40 shrink-0 truncate opacity-50">{{ item.timestamp }}</span>
            <span class="min-w-0 flex-1 truncate">{{ item.message }}</span>
          </button>
        </div>
      </section>
    </div>
  </aside>
</template>

<script>
/** Longest value rendered as-is in the fields table. */
const FIELD_MAX_CHARS = 2000

export default {
  name: 'LogDetailPanel',
  emits: ['close', 'filter', 'context', 'select-entry'],
  props: {
    /** Selected entry (`/query` shape, enriched by `/entry/{id}` when loaded). */
    entry: { type: Object, default: null },
    /** Verbatim raw text returned by `/entry/{id}`. */
    raw: { type: String, default: '' },
    /** True while the full entry is being fetched. */
    loading: { type: Boolean, default: false },
    /** Entries returned by `/context/{id}`. */
    contextEntries: { type: Array, default: () => [] },
    contextLoading: { type: Boolean, default: false },
  },
  data() {
    return {
      copied: false,
      copyTimer: null,
      levelBadges: {
        critical: 'bg-fuchsia-600 text-white',
        error: 'badge-error',
        warn: 'badge-warning',
        info: 'badge-info',
        debug: 'badge-success',
        trace: 'badge-neutral',
        unknown: 'badge-ghost',
      },
    }
  },
  computed: {
    level() {
      const value = String(this.entry?.level || 'unknown').toLowerCase()
      return this.levelBadges[value] ? value : 'unknown'
    },
    levelLabel() {
      return this.level === 'critical' ? 'crit' : this.level
    },
    levelStyle() {
      return { badge: this.levelBadges[this.level] }
    },
    message() {
      return String(this.entry?.message || '')
    },
    timeLabel() {
      const raw = this.entry?.timestamp
      if (raw) return raw
      const ms = Number(this.entry?.ts_ms)
      if (!Number.isFinite(ms)) return '—'
      return new Date(ms).toISOString()
    },
    loggerLabel() {
      const name = this.entry?.module || this.entry?.logger || ''
      const line = this.entry?.line
      if (line && name) return `${name}:${line}`
      if (line) return `line ${line}`
      return name || '—'
    },
    loggerValue() {
      return String(this.entry?.logger || this.entry?.module || '')
    },
    fieldRows() {
      const fields = this.entry?.fields || {}
      return Object.entries(fields).map(([key, value]) => ({
        key,
        rawValue: value,
        value: this.stringifyValue(value),
        filterable: value === null || typeof value !== 'object',
      }))
    },
    prettyFields() {
      const fields = this.entry?.fields || {}
      if (!Object.keys(fields).length) return ''
      try {
        return JSON.stringify(fields, null, 2)
      } catch (err) {
        return ''
      }
    },
    rawText() {
      return this.raw || String(this.entry?.raw || this.entry?.raw_preview || '')
    },
  },
  beforeUnmount() {
    if (this.copyTimer) clearTimeout(this.copyTimer)
  },
  methods: {
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
    /** Copy the verbatim entry (falls back to the message when raw is absent). */
    async copyEntry() {
      const text = this.rawText || this.message
      if (!text) return
      try {
        await navigator.clipboard.writeText(text)
        this.copied = true
        if (this.copyTimer) clearTimeout(this.copyTimer)
        this.copyTimer = setTimeout(() => {
          this.copied = false
          this.copyTimer = null
        }, 1500)
      } catch (err) {
        this.copied = false
      }
    },
    /** Emit a ready-to-use query term, e.g. `logger="codx.junior.api"`. */
    filterBy(key, value) {
      const clean = String(value ?? '').replace(/"/g, '').slice(0, 200).trim()
      if (!clean) return
      this.$emit('filter', `${key}="${clean}"`)
    },
  },
}
</script>
