<script setup>
/**
 * Month view — a 6×7 grid with up to three chips per day and a "+N more"
 * button that opens the day (analysis §9.2).
 *
 * A click anywhere on a day cell creates an event on that day; the chips and
 * the "+N more" button stop the propagation because they open something else.
 *
 * The occurrences arrive already expanded by the server and bucketed by the
 * store (`occurrencesByDay`), so this component only decides *where* each chip
 * goes. Colour = calendar (§9.3.1): a chip bar uses the primary calendar
 * colour and a second dot marks an extra membership, which is how "one
 * occurrence, N memberships" (R6) stays visible without clutter.
 */
import { computed } from 'vue'
import {
  DOW,
  MS_DAY,
  addDays,
  dateKey,
  formatTime,
  sameDay,
  startOfMonth,
  startOfWeek
} from '@/utils/calendarDate'

const props = defineProps({
  cursorDate: { type: Date, default: () => new Date() },
  /** `{ 'YYYY-MM-DD': [occurrence] }` */
  occurrencesByDay: { type: Object, default: () => ({}) },
  calendars: { type: Array, default: () => [] }
})

const emit = defineEmits(['select', 'more', 'create'])

const MAX_CHIPS = 3

const calendarsById = computed(() => {
  const map = {}
  for (const calendar of props.calendars || []) map[calendar.id] = calendar
  return map
})

const days = computed(() => {
  const gridStart = startOfWeek(startOfMonth(props.cursorDate))
  const month = props.cursorDate.getMonth()
  const cells = []
  for (let i = 0; i < 42; i++) {
    const date = addDays(gridStart, i)
    const key = dateKey(date)
    const items = (props.occurrencesByDay?.[key] || []).slice()
    cells.push({
      key,
      date,
      number: date.getDate(),
      other: date.getMonth() !== month,
      today: sameDay(date, new Date()),
      items,
      shown: items.slice(0, MAX_CHIPS),
      extra: Math.max(0, items.length - MAX_CHIPS)
    })
  }
  return cells
})

/**
 * Day-cell click → create. The cell carries no time, so a new event defaults
 * to 09:00, or to the next full hour when the clicked day is today.
 */
function createOn(date) {
  const now = new Date()
  const hour = sameDay(date, now) ? Math.min(23, now.getHours() + 1) : 9
  emit('create', new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour, 0))
}

function calendarOf(occurrence) {
  const id = occurrence.calendar_id || (occurrence.calendar_ids || [])[0]
  return calendarsById.value[id] || null
}

/**
 * Chip colour. The left bar is the primary calendar; a multi-calendar item
 * tints the chip with a two-tone gradient (§9.3.1).
 */
function chipStyle(occurrence) {
  const primary = calendarOf(occurrence)?.color || '#cc0099'
  const secondary = calendarsById.value[(occurrence.calendar_ids || [])[1]]?.color
  const base = 'var(--color-base-100, #16161a)'
  const style = {
    borderLeft: `3px solid ${primary}`,
    background: `color-mix(in oklab, ${primary} 22%, ${base})`
  }
  if (secondary) {
    style.backgroundImage =
      `linear-gradient(to right, ${primary} 0 3px, ${secondary} 3px 6px, transparent 6px)`
  }
  return style
}

function chipTitle(occurrence) {
  const names = (occurrence.calendar_ids || [])
    .map(id => calendarsById.value[id]?.name)
    .filter(Boolean)
  const when = occurrence.all_day ? 'all day' : formatTime(occurrence.start)
  return `${occurrence.title} · ${when}${names.length > 1 ? ` · ${names.join(' + ')}` : ''}`
}

function isMulti(occurrence) {
  return (occurrence.calendar_ids || []).length > 1
}

function sourceBadge(occurrence) {
  return {
    task: 'task',
    orchestration: 'run',
    meet: 'meet',
    external: 'ics'
  }[occurrence.source] || ''
}

/** Multi-day items read better with an arrow on their first visible day. */
function spanSuffix(occurrence, cellDate) {
  if (!occurrence.all_day) return ''
  const start = new Date(occurrence.start)
  const end = new Date(occurrence.end)
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) return ''
  const startDay = new Date(start.getFullYear(), start.getMonth(), start.getDate())
  const endDay = new Date(end.getFullYear(), end.getMonth(), end.getDate())
  if (Math.round((endDay - startDay) / MS_DAY) <= 0) return ''
  return dateKey(start) === dateKey(cellDate) ? '→' : ''
}
</script>

<template>
  <div class="flex flex-col flex-1 min-h-0">
    <div class="grid grid-cols-7 border-b border-base-300 bg-base-200/60 shrink-0">
      <div
        v-for="day in DOW"
        :key="day"
        class="py-1.5 text-center text-[10px] uppercase tracking-wider text-base-content/40 border-r border-base-300 last:border-r-0"
      >
        {{ day }}
      </div>
    </div>

    <div class="grid grid-cols-7 grid-rows-6 flex-1 min-h-0 overflow-y-auto">
      <div
        v-for="cell in days"
        :key="cell.key"
        class="relative border-r border-b border-base-300 last:border-r-0 p-1 flex flex-col gap-0.5 min-h-0 overflow-hidden cursor-pointer transition-colors hover:bg-base-content/[0.04]"
        :class="[cell.other ? 'bg-base-content/[0.02]' : '', cell.today ? 'bg-codx-primary/10' : '']"
        :title="`Create an event on ${cell.key}`"
        @click="createOn(cell.date)"
      >
        <button
          class="self-center text-[11px] w-5 h-5 rounded-full flex items-center justify-center tabular-nums transition-colors"
          :class="cell.today
            ? 'bg-codx-primary text-white font-bold'
            : 'text-base-content/60 hover:bg-base-300'"
          :title="`Create an event on ${cell.key}`"
          @click.stop="createOn(cell.date)"
        >
          {{ cell.number }}
        </button>

        <div class="flex flex-col gap-0.5 min-h-0">
          <button
            v-for="occurrence in cell.shown"
            :key="`${occurrence.event_id}-${occurrence.start}`"
            class="text-left rounded-[5px] pl-1 pr-1 py-0.5 text-[11px] leading-tight truncate transition-opacity hover:brightness-125"
            :class="occurrence.overdue ? 'opacity-70' : ''"
            :style="chipStyle(occurrence)"
            :title="chipTitle(occurrence)"
            @click.stop="emit('select', occurrence)"
          >
            <span v-if="!occurrence.all_day" class="text-base-content/60 tabular-nums">
              {{ formatTime(occurrence.start) }}
            </span>
            <span class="font-medium">{{ occurrence.title }}</span>
            <span
              v-if="sourceBadge(occurrence)"
              class="ml-1 text-[8px] uppercase tracking-wide px-1 rounded bg-base-content/15"
            >{{ sourceBadge(occurrence) }}</span>
            <i
              v-if="isMulti(occurrence)"
              class="inline-block w-1 h-1 rounded-full ml-1 align-middle opacity-80"
              :style="{ background: calendarsById[(occurrence.calendar_ids || [])[1]]?.color || 'currentColor' }"
            ></i>
            <span class="text-base-content/50">{{ spanSuffix(occurrence, cell.date) }}</span>
          </button>

          <button
            v-if="cell.extra"
            class="text-[10px] text-left pl-1 text-base-content/50 hover:text-base-content"
            @click.stop="emit('more', cell.date)"
          >
            +{{ cell.extra }} more…
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
