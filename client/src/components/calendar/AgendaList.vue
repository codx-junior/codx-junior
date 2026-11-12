<script setup>
/**
 * Agenda view — a flat list grouped by day, 30 days from the cursor
 * (analysis §9.2). It is the most forgiving view on a small screen and the one
 * that shows *why* an item is there: the project badge and the source badges
 * (`task` / `run` / `meet` / `ics`) are rendered per row.
 */
import { computed } from 'vue'
import {
  addDays,
  dateKey,
  formatDayLong,
  formatTimeRange,
  sameDay,
  startOfDay
} from '@/utils/calendarDate'

const DAYS = 30

const props = defineProps({
  cursorDate: { type: Date, default: () => new Date() },
  /** `{ 'YYYY-MM-DD': [occurrence] }` */
  occurrencesByDay: { type: Object, default: () => ({}) },
  calendars: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['select'])

const calendarsById = computed(() => {
  const map = {}
  for (const calendar of props.calendars || []) map[calendar.id] = calendar
  return map
})

const groups = computed(() => {
  const start = startOfDay(props.cursorDate)
  const result = []
  for (let i = 0; i < DAYS; i++) {
    const date = addDays(start, i)
    const key = dateKey(date)
    const items = props.occurrencesByDay?.[key] || []
    if (!items.length) continue
    result.push({
      key,
      date,
      label: formatDayLong(date),
      today: sameDay(date, new Date()),
      items: items.slice().sort((a, b) => {
        if (a.all_day !== b.all_day) return a.all_day ? -1 : 1
        return new Date(a.start) - new Date(b.start)
      })
    })
  }
  return result
})

function calendarOf(occurrence) {
  const id = occurrence.calendar_id || (occurrence.calendar_ids || [])[0]
  return calendarsById.value[id] || null
}

function memberships(occurrence) {
  return (occurrence.calendar_ids || [])
    .map(id => calendarsById.value[id])
    .filter(Boolean)
}

function sourceBadge(occurrence) {
  return {
    task: 'task',
    orchestration: 'run',
    meet: 'meet',
    external: 'ics'
  }[occurrence.source] || ''
}

function whenLabel(occurrence) {
  return occurrence.all_day ? 'All day' : formatTimeRange(occurrence.start, occurrence.end)
}
</script>

<template>
  <div class="flex-1 min-h-0 overflow-y-auto px-4 pb-16 pt-2">
    <div v-if="!groups.length" class="flex flex-col items-center justify-center gap-2 py-16 text-base-content/40">
      <i class="fa-regular fa-calendar text-3xl"></i>
      <p v-if="loading" class="text-sm">Loading the agenda…</p>
      <p v-else class="text-sm">Nothing scheduled in the next 30 days.</p>
    </div>

    <section v-for="group in groups" :key="group.key" class="mt-3">
      <h4
        class="sticky top-0 z-10 bg-base-100 py-1.5 flex items-center gap-2 text-[11px] uppercase tracking-wider"
        :class="group.today ? 'text-codx-primary' : 'text-base-content/40'"
      >
        {{ group.today ? 'Today · ' : '' }}{{ group.label }}
        <span class="flex-1 h-px bg-base-300"></span>
      </h4>

      <button
        v-for="occurrence in group.items"
        :key="`${occurrence.event_id}-${occurrence.start}`"
        class="w-full text-left flex items-start gap-3 rounded-box px-2.5 py-2 border border-transparent hover:bg-base-200 hover:border-base-300 transition-colors"
        @click="emit('select', occurrence)"
      >
        <div class="w-24 shrink-0 text-[11px] font-mono text-base-content/60 pt-0.5 tabular-nums">
          {{ whenLabel(occurrence) }}
        </div>

        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-1.5 flex-wrap">
            <span
              class="inline-block w-[3px] h-3.5 rounded-sm shrink-0"
              :style="{ background: calendarOf(occurrence)?.color || '#cc0099' }"
            ></span>
            <span class="font-semibold text-sm">{{ occurrence.title }}</span>
            <span class="badge badge-xs badge-outline">{{ occurrence.project_id || 'personal' }}</span>
            <span v-if="sourceBadge(occurrence)" class="badge badge-xs badge-ghost uppercase text-[9px]">
              {{ sourceBadge(occurrence) }}
            </span>
            <span v-if="occurrence.read_only" class="badge badge-xs badge-warning text-[9px]">read-only</span>
            <span v-if="occurrence.overdue" class="badge badge-xs badge-error text-[9px]">overdue</span>
          </div>

          <div
            v-if="occurrence.location || occurrence.description"
            class="text-[11px] text-base-content/50 truncate mt-0.5"
          >
            {{ occurrence.location || occurrence.description }}
          </div>

          <div class="flex items-center gap-2 text-[10px] text-base-content/50 mt-0.5 flex-wrap">
            <span v-for="calendar in memberships(occurrence)" :key="calendar.id" class="inline-flex items-center gap-1">
              <i class="inline-block w-1.5 h-1.5 rounded-full" :style="{ background: calendar.color }"></i>
              {{ calendar.name }}
            </span>
          </div>
        </div>
      </button>
    </section>
  </div>
</template>
