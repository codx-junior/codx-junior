<script setup>
/**
 * Append-only harness audit journal (presentational).
 *
 * The journal is the machine-readable trail of every harness action: requests,
 * run events, worker incidents and — since the timing instrumentation — one
 * `tool_call` entry per tool invocation with its wall-clock window and its
 * duration. That is what makes a time frame possible for a slow run.
 *
 * The component owns NO data: fetching, paging and filters live in the view
 * (views/HarnessView.vue) so the same journal can be rendered next to the run
 * detail and beside an open task panel without duplicating request logic.
 */
import { formatTime } from './harnessStatus'

defineProps({
  entries: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  event: { type: String, default: '' },
  scope: { type: String, default: 'run' }
})

const emit = defineEmits(['update:event', 'update:scope', 'load', 'load-more'])

// Event types offered by the filter. `tool_call` is the tool timing record
// emitted by SmolAgent for harness turns (start, end and duration).
const AUDIT_EVENTS = [
  { value: '', label: 'All events' },
  { value: 'task_requested', label: 'task_requested' },
  { value: 'run_event', label: 'run_event (run lifecycle)' },
  { value: 'tool_call', label: 'tool_call (tool timing)' },
  { value: 'notification_sent', label: 'notification_sent' },
  { value: 'worker_stalled', label: 'worker_stalled' },
  { value: 'worker_restarted', label: 'worker_restarted' },
  { value: 'worker_recovered', label: 'worker_recovered' },
  { value: 'error', label: 'error' }
]

/** One-line human summary of an audit entry. */
function auditText(entry) {
  if (entry.event === 'task_requested') {
    return `@codx-junior requested by ${entry.user || '?'} in chat ${entry.chat_id || '?'}`
  }
  if (entry.event === 'run_event') {
    const runEvent = entry.run_event || ''
    const extra = ['step_finished', 'step_started', 'step_dispatched', 'step_retried'].includes(runEvent)
      ? ` step ${entry.step_id || '?'}`
      : ''
    return `${runEvent} · run ${entry.run_id || '?'}${extra} · ${entry.message || ''}`
  }
  if (entry.event === 'tool_call') {
    const duration = entry.duration_ms != null ? `${Math.round(entry.duration_ms)}ms` : '?'
    const status = entry.status === 'ok' ? '' : ` · ${entry.status}${entry.timed_out ? ' (timeout)' : ''}`
    return `${entry.tool || '?'} · ${duration}${status} · chat ${entry.chat_id || '?'}`
  }
  if (entry.event === 'notification_sent') {
    return `user notified · run ${entry.run_id || '?'} (${entry.status || ''})`
  }
  if (entry.event === 'worker_stalled') {
    return `worker stalled · project ${entry.project || '?'} · ${entry.active_runs?.length || 0} active run(s)`
  }
  if (entry.event === 'worker_restarted') {
    return `worker restarted · ${entry.method || ''} · ${entry.project || ''}`
  }
  if (entry.event === 'worker_recovered') {
    return `worker recovered · pid ${entry.pid ?? '?'}`
  }
  if (entry.event === 'error') {
    return `${entry.message || entry.error || JSON.stringify(entry)}`
  }
  return JSON.stringify(entry)
}

function onEventChange(event) {
  emit('update:event', event.target.value)
  emit('load')
}

function onScopeChange(event) {
  emit('update:scope', event.target.value)
  emit('load')
}
</script>

<template>
  <section class="flex flex-col gap-2 pb-4">
    <div class="flex items-center justify-between gap-2 flex-wrap">
      <h2 class="font-semibold text-sm uppercase tracking-wide text-base-content/60">Audit journal</h2>
      <div class="flex items-center gap-2">
        <select :value="scope" class="select select-bordered select-xs" @change="onScopeChange">
          <option value="run">This task</option>
          <option value="all">Whole project</option>
        </select>
        <select :value="event" class="select select-bordered select-xs" @change="onEventChange">
          <option v-for="option in AUDIT_EVENTS" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
    </div>
    <div v-if="!entries.length" class="text-sm text-base-content/50 bg-base-200 rounded-box p-4">
      No audited events yet.
    </div>
    <div v-else class="flex flex-col gap-1 bg-base-200 rounded-box p-2 max-h-96 overflow-y-auto">
      <div
        v-for="(entry, index) in entries"
        :key="`${entry.ts}-${index}`"
        class="flex items-start gap-2 text-sm px-2 py-1 rounded hover:bg-base-300"
      >
        <span class="badge badge-xs badge-outline shrink-0 mt-0.5">{{ entry.event }}</span>
        <span class="text-xs shrink-0 text-base-content/50 mt-0.5">{{ formatTime(entry.ts) }}</span>
        <span class="text-xs break-all">{{ auditText(entry) }}</span>
      </div>
      <button
        v-if="entries.length >= 50"
        class="btn btn-xs btn-ghost"
        :disabled="loading"
        @click="$emit('load-more')"
      >
        <i v-if="loading" class="fa-solid fa-spinner animate-spin"></i>
        Load more
      </button>
    </div>
  </section>
</template>
