<script setup>
/**
 * Collapsible DSH-style activity list of one harness session.
 *
 * It shows exactly what the agent did (read, searched, edited, ran tests,
 * waited for the model) in chronological order, with the request arguments as
 * the visible detail and the raw output behind a click. The component is pure:
 * the view model comes from `utils/harnessSteps.js`.
 */
import { computed, ref } from 'vue'
import { formatDuration } from '@/utils/harnessSteps.js'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  activity: { type: String, default: 'Worked' },
  live: { type: Boolean, default: false },
  defaultOpen: { type: Boolean, default: false }
})

const open = ref(props.defaultOpen)
const expanded = ref({})

const duration = computed(() =>
  formatDuration(
    props.steps.reduce((total, step) => total + (Number(step.durationMs) || 0), 0) / 1000
  )
)
const errorCount = computed(() =>
  props.steps.filter(step => step.status === 'error').length
)
const toolCount = computed(() =>
  props.steps.filter(step => step.kind === 'tool').length
)
const visibleSteps = computed(() => props.steps.filter(step => !step.hidden))

function toggleRow(id) {
  expanded.value = { ...expanded.value, [id]: !expanded.value[id] }
}

function statusIcon(step) {
  if (step.status === 'error') return 'fa-solid fa-circle-xmark text-error'
  if (step.status === 'running') return 'fa-solid fa-circle-notch fa-spin text-info'
  return 'fa-solid fa-circle-check text-success/70'
}

function rowTitle(step) {
  if (step.kind === 'tool') return `${step.label} ${step.detail || ''}`.trim()
  return step.detail || step.label
}
</script>

<template>
  <div class="rounded-xl border border-base-content/10 bg-base-200/40 overflow-hidden">
    <button
      type="button"
      class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-base-content/5 transition-colors"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="w-4 flex justify-center shrink-0">
        <span v-if="live" class="loading loading-spinner loading-xs text-info"></span>
        <i v-else-if="errorCount" class="fa-solid fa-circle-xmark text-error text-xs"></i>
        <i v-else class="fa-solid fa-list-check text-base-content/50 text-xs"></i>
      </span>
      <span class="text-xs font-semibold truncate">{{ activity }}</span>
      <span class="text-[11px] text-base-content/40 shrink-0">
        {{ toolCount }} tool{{ toolCount === 1 ? '' : 's' }}
        <template v-if="duration !== '0s'"> · {{ duration }}</template>
      </span>
      <span v-if="errorCount" class="badge badge-error badge-xs shrink-0">
        {{ errorCount }} error{{ errorCount === 1 ? '' : 's' }}
      </span>
      <i
        class="fa-solid fa-chevron-down text-[10px] text-base-content/40 ml-auto shrink-0 transition-transform"
        :class="open && 'rotate-180'"
      ></i>
    </button>

    <div v-if="open" class="border-t border-base-content/10 divide-y divide-base-content/5">
      <div v-for="step in visibleSteps" :key="step.id" class="text-xs">
        <button
          type="button"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-base-content/5 transition-colors"
          @click="toggleRow(step.id)"
        >
          <i :class="[step.icon, 'w-4 text-center text-base-content/40 shrink-0']"></i>
          <span class="font-medium shrink-0" :class="step.kind === 'tool' ? 'text-base-content/80' : 'text-base-content/50'">
            {{ step.label }}
          </span>
          <code
            v-if="step.detail"
            class="font-mono text-[11px] text-base-content/45 truncate min-w-0"
          >{{ step.detail }}</code>
          <span class="grow"></span>
          <span v-if="step.cached" class="badge badge-ghost badge-xs shrink-0">cached</span>
          <span
            v-if="step.durationMs"
            class="text-[10px] tabular-nums text-base-content/30 shrink-0"
          >{{ (step.durationMs / 1000).toFixed(1) }}s</span>
          <i :class="statusIcon(step)" class="text-[11px] shrink-0"></i>
          <i
            v-if="step.output || step.error"
            class="fa-solid fa-chevron-down text-[9px] text-base-content/30 shrink-0 transition-transform"
            :class="expanded[step.id] && 'rotate-180'"
          ></i>
        </button>
        <div
          v-if="expanded[step.id] && (step.output || step.error)"
          class="px-9 pb-2"
        >
          <pre
            v-if="step.error"
            class="max-h-48 overflow-auto rounded-lg bg-error/10 p-2 text-[11px] text-error whitespace-pre-wrap break-words"
          >{{ step.error }}</pre>
          <pre
            v-if="step.output"
            class="max-h-48 overflow-auto rounded-lg bg-base-300/60 p-2 text-[11px] text-base-content/60 whitespace-pre-wrap break-words"
          >{{ step.output }}</pre>
        </div>
      </div>

      <p v-if="!visibleSteps.length" class="px-3 py-2 text-xs text-base-content/40">
        No activity recorded yet.
      </p>
    </div>
  </div>
</template>
