<script setup>
/**
 * Detail popover of one occurrence (analysis §9.2 / §9.3.3).
 *
 * It deliberately describes the **occurrence**, not the event: the same event
 * may be shown in several calendars (R6) and only the read-only projection
 * items (`cal_codx`, `external`) carry `read_only`. Editing is therefore
 * offered only when the occurrence allows it.
 */
import { computed } from 'vue'
import { formatDayLong, formatTimeRange } from '@/utils/calendarDate'

const props = defineProps({
  occurrence: { type: Object, required: true },
  calendars: { type: Array, default: () => [] },
  /** Viewport coordinates of the click that opened it. */
  position: { type: Object, default: () => ({ x: 0, y: 0 }) }
})

const emit = defineEmits(['close', 'edit', 'delete', 'open-source'])

const WIDTH = 330

const calendarsById = computed(() => {
  const map = {}
  for (const calendar of props.calendars || []) map[calendar.id] = calendar
  return map
})

const memberships = computed(() =>
  (props.occurrence.calendar_ids || [])
    .map(id => calendarsById.value[id])
    .filter(Boolean)
)

const primary = computed(() => memberships.value[0] || null)

const when = computed(() => {
  const day = formatDayLong(props.occurrence.start)
  return props.occurrence.all_day ? `${day} · all day` : `${day} · ${formatTimeRange(props.occurrence.start, props.occurrence.end)}`
})

const sourceLabel = computed(() => ({
  task: 'Task',
  orchestration: 'Orchestration run',
  meet: 'Meet',
  external: 'Imported (ICS)',
  native: 'Event'
}[props.occurrence.source] || 'Event'))

const reminderLabel = computed(() => {
  const reminders = props.occurrence.reminders || []
  if (reminders.length) {
    return reminders
      .map(reminder => (reminder.minutes_before ? `${reminder.minutes_before} min before` : 'at start'))
      .join(' · ')
  }
  const defaults = primary.value?.default_reminders || []
  if (defaults.length) {
    return `calendar default (${defaults.map(reminder => reminder.minutes_before).join('/')} min)`
  }
  return 'no reminders'
})

const style = computed(() => {
  const width = WIDTH
  const maxX = typeof window === 'undefined' ? props.position.x : window.innerWidth - width - 12
  const maxY = typeof window === 'undefined' ? props.position.y : window.innerHeight - 340
  const left = Math.min(Math.max(12, (props.position.x || 0) - width / 2), Math.max(12, maxX))
  const top = Math.min(Math.max(64, (props.position.y || 0) - 20), Math.max(64, maxY))
  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    borderColor: primary.value?.color || 'var(--color-base-300)'
  }
})
</script>

<template>
  <div
    class="fixed z-[60] rounded-box shadow-2xl bg-base-200 border overflow-hidden"
    :style="style"
    role="dialog"
    aria-label="Event details"
  >
    <div class="h-1.5" :style="{ background: primary?.color || '#cc0099' }"></div>

    <div class="p-3.5 flex flex-col gap-2">
      <div>
        <h3 class="font-semibold text-[15px] leading-tight">{{ occurrence.title }}</h3>
        <p class="text-xs text-base-content/60 mt-0.5">{{ when }}</p>
      </div>

      <div class="flex items-center gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-folder text-[10px] opacity-60"></i>
        {{ occurrence.project_id || 'Personal · no project' }}
      </div>

      <div v-if="occurrence.location" class="flex items-center gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-location-dot text-[10px] opacity-60"></i>
        {{ occurrence.location }}
      </div>

      <div v-if="occurrence.description" class="flex items-start gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-align-left text-[10px] opacity-60 mt-0.5"></i>
        <span>{{ occurrence.description }}</span>
      </div>

      <div v-if="(occurrence.guests || []).length" class="flex items-start gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-user-group text-[10px] opacity-60 mt-0.5"></i>
        <span>{{ (occurrence.guests || []).join(', ') }}</span>
      </div>

      <div class="flex items-start gap-1.5 text-xs text-base-content/70">
        <i class="fa-regular fa-bell text-[10px] opacity-60 mt-0.5"></i>
        <span>{{ reminderLabel }}</span>
      </div>

      <div class="flex items-center gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-tag text-[10px] opacity-60"></i>
        <span>{{ sourceLabel }}<span v-if="occurrence.read_only"> · read-only</span></span>
      </div>

      <div v-if="occurrence.is_recurring_instance" class="flex items-center gap-1.5 text-xs text-base-content/70">
        <i class="fa-solid fa-repeat text-[10px] opacity-60"></i>
        Recurring instance
      </div>

      <div class="flex flex-wrap gap-1 pt-0.5">
        <span
          v-for="calendar in memberships"
          :key="calendar.id"
          class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-base-300 text-base-content/70"
        >
          <i class="inline-block w-1.5 h-1.5 rounded-full" :style="{ background: calendar.color }"></i>
          {{ calendar.name }}
        </span>
      </div>

      <p v-if="memberships.length > 1" class="text-[10px] text-base-content/40">
        One item, {{ memberships.length }} calendar memberships.
      </p>
    </div>

    <div class="flex items-center justify-end gap-1.5 px-3.5 py-2 border-t border-base-300 bg-base-300/40">
      <button
        v-if="occurrence.read_only"
        class="btn btn-xs"
        @click="emit('open-source', occurrence)"
      >
        {{ occurrence.source === 'task' ? 'Open task' : 'Open item' }}
      </button>
      <template v-else>
        <button class="btn btn-xs" @click="emit('edit', occurrence)">Edit</button>
        <button class="btn btn-xs btn-error btn-outline" @click="emit('delete', occurrence)">Delete</button>
      </template>
      <button class="btn btn-xs btn-ghost" @click="emit('close')">Close</button>
    </div>
  </div>
</template>
