<template>
  <div class="card bg-base-100 shadow">
    <div class="card-body p-3 gap-2">

      <!-- Query input -->
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative min-w-60 flex-1">
          <i class="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-xs opacity-40"></i>
          <input
            ref="input"
            :value="modelValue"
            type="text"
            spellcheck="false"
            autocomplete="off"
            class="input input-bordered input-sm w-full pl-8 font-mono text-xs"
            :placeholder="placeholder"
            @input="$emit('update:modelValue', $event.target.value)"
            @keydown.enter.prevent="run"
          />
        </div>

        <button class="btn btn-sm btn-primary gap-1" :disabled="loading" @click="run">
          <i class="fa-solid fa-play text-xs"></i>
          Run
        </button>

        <button class="btn btn-sm btn-ghost gap-1" :disabled="!modelValue" @click="clear">
          <i class="fa-solid fa-xmark text-xs"></i>
          Clear
        </button>

        <!-- Help popover with the operator grammar -->
        <details class="dropdown dropdown-end">
          <summary class="btn btn-sm btn-ghost gap-1" title="Query language">
            <i class="fa-solid fa-circle-question text-xs"></i>
            <span class="hidden sm:inline">Syntax</span>
          </summary>
          <div class="dropdown-content z-30 mt-1 w-96 rounded-box bg-base-100 p-3 shadow-lg">
            <p class="mb-2 text-xs font-semibold uppercase tracking-wide opacity-60">Query language</p>
            <p class="mb-2 text-[11px] opacity-70">
              Terms are AND-joined. Values are matched case-insensitive.
            </p>
            <table class="table table-xs">
              <tbody>
                <tr v-for="row in operators" :key="row.syntax">
                  <td class="whitespace-nowrap font-mono text-[11px] text-primary">{{ row.syntax }}</td>
                  <td class="text-[11px] opacity-70">{{ row.description }}</td>
                </tr>
              </tbody>
            </table>
            <p class="mt-2 text-[11px] opacity-50">
              Press <kbd class="kbd kbd-xs">Enter</kbd> to run, <kbd class="kbd kbd-xs">/</kbd> to focus this box.
            </p>
          </div>
        </details>
      </div>

      <!-- Parsed terms -->
      <div v-if="terms.length" class="flex flex-wrap items-center gap-1">
        <span class="mr-1 text-[10px] uppercase tracking-wide opacity-40">terms</span>
        <button
          v-for="(term, index) in terms"
          :key="`${term}-${index}`"
          class="badge badge-sm badge-neutral gap-1 font-mono cursor-pointer hover:badge-error"
          title="Remove this term"
          @click="removeTerm(index)"
        >
          <span class="max-w-60 truncate">{{ term }}</span>
          <i class="fa-solid fa-xmark text-[9px] opacity-60"></i>
        </button>
      </div>
      <div v-else class="font-mono text-[11px] opacity-40">
        <span class="text-info">level=error,warn</span> ·
        <span class="text-info">logger~"chat_.*"</span> ·
        <span class="text-info">request.url=/api</span> ·
        <span class="text-info">-"no such table"</span> ·
        <span class="text-info">-level=debug</span>
      </div>
    </div>
  </div>
</template>

<script>
import { removeQueryTerm, tokenizeQuery } from '@/composables/useLogExplorer'

export default {
  name: 'LogQueryBar',
  emits: ['update:modelValue', 'run', 'clear'],
  props: {
    modelValue: { type: String, default: '' },
    loading: { type: Boolean, default: false },
    placeholder: {
      type: String,
      default: 'free text  "exact phrase"  level=error,warn  logger~"regex"  field=value  -negation',
    },
  },
  data() {
    return {
      operators: [
        { syntax: 'text', description: 'Case-insensitive substring on the whole entry.' },
        { syntax: '"exact phrase"', description: 'Substring with spaces, quotes included.' },
        { syntax: 'level=error,warn', description: 'Level set membership (critical…trace).' },
        { syntax: 'logger~"chat_.*"', description: 'Case-insensitive regex on the logger name.' },
        { syntax: 'module=codx.junior.api', description: 'Exact match on a field or top-level attribute.' },
        { syntax: 'field~"re"', description: 'Regex match on a structured field.' },
        { syntax: '-term', description: 'Negate any of the terms above.' },
      ],
    }
  },
  computed: {
    terms() {
      return tokenizeQuery(this.modelValue)
    },
  },
  mounted() {
    window.addEventListener('keydown', this.onGlobalKeydown)
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onGlobalKeydown)
  },
  methods: {
    run() {
      this.$emit('run')
    },
    clear() {
      this.$emit('update:modelValue', '')
      this.$emit('clear')
    },
    removeTerm(index) {
      this.$emit('update:modelValue', removeQueryTerm(this.modelValue, index))
      this.$emit('run')
    },
    /** `/` focuses the query box, unless the user is typing somewhere else. */
    onGlobalKeydown(event) {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target
      const tagName = (target?.tagName || '').toLowerCase()
      if (tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target?.isContentEditable) return
      event.preventDefault()
      this.$refs.input?.focus()
    },
  },
}
</script>
