<script setup>
/**
 * Week / day view — `days` columns × 24 hours, an all-day row on top,
 * side-by-side layout for overlapping items and a "now" line (analysis §9.2).
 * The same component renders the Day view with `days = 1`.
 *
 * Positions come from the occurrence start/end of the *current* view range;
 * nothing is expanded here (the server already did it).
 *
 * Not in M0: drag-to-move and edge-resize (they need `PUT` round-trips with
 * optimistic reconciliation, §9.3.4). Clicking an empty slot creates an event
 * with the time prefilled, which is the M0 interaction.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  DOW,
  addDays,
  dateKey,
  formatTimeRange,
  startOfDay,
  startOfWeek
} from '@/utils/calendarDate'

const HOUR_HEIGHT = 44
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)

const props = defineProps({
  cursorDate: { type: Date, default: () => new Date() },
  /** `{ 'YYYY-MM-DD': [occurrence] }` */
  occurrencesByDay: { type: Object, default: () => ({}) },
  calendars: { type: Array, default: () => [] },
  /** 7 = week view, 1 = day view. */
  days: { type: Number, default: 7 }
})

const emit = defineEmits(['select', 'create'])

const scrollHost = ref(null)
const now = ref(new Date())
let ticker = null

const calendarsById = computed(() => {
  const map = {}
  for (const calendar of props.calendars || []) map[calendar.id] = calendar
  return map
})

const columns = computed(() => {
  const start = props.days === 1 ? startOfDay(props.cursorDate) : startOfWeek(props.cursorDate)
  const cols = []
  for (let i = 0; i < props.days; i++) {
    const date = addDays(start, i)
    const key = dateKey(date)
    const items = props.occurrencesByDay?.[key] || []
    cols.push({
      key,
      date,
      label: DOW[(date.getDay() + 6) % 7],
      number: date.getDate(),
      isToday: dateKey(new Date()) === key,
      weekend: date.getDay() === 0 || date.getDay() === 6,
      allDay: items.filter(item => item.all_day),
      timed: layoutLanes(items.filter(item => !item.all_day), date)
    })
  }
  return cols
})

/**
 * Greedy lane assignment: items overlapping in time share the column width.
 * Returns `[{ occurrence, top, height, left, width }]` in percentages/pixels.
 */
function layoutLanes(items, date) {
  const dayStart = startOfDay(date).getTime()
  const prepared = items
    .map(occurrence => {
      const start = new Date(occurrence.start).getTime()
      const end = new Date(occurrence.end).getTime()
      return { occurrence, start, end: Number.isFinite(end) ? end : start + 30 * 60000 }
    })
    .filter(item => Number.isFinite(item.start))
    .sort((a, b) => a.start - b.start || b.end - a.end)

  const laneEnds = []
  for (const item of prepared) {
    let lane = laneEnds.findIndex(end => end <= item.start)
    if (lane === -1) {
      lane = laneEnds.length
      laneEnds.push(item.end)
    } else {
      laneEnds[lane] = item.end
    }
    item.lane = lane
  }

  const lanes = Math.max(1, laneEnds.length)
  return prepared.map(item => {
    const startMinutes = (item.start - dayStart) / 60000
    const minutes = Math.max(15, (item.end - item.start) / 60000)
    return {
      occurrence: item.occurrence,
      top: (startMinutes / 60) * HOUR_HEIGHT,
      height: Math.max(18, (minutes / 60) * HOUR_HEIGHT - 2),
      left: (item.lane * 100) / lanes,
      width: 100 / lanes
    }
  })
}

const nowTop = computed(() => {
  const date = now.value
  return ((date.getHours() * 60 + date.getMinutes()) / 60) * HOUR_HEIGHT
})

function calendarOf(occurrence) {
  const id = occurrence.calendar_id || (occurrence.calendar_ids || [])[0]
  return calendarsById.value[id] || null
}

function blockStyle(block) {
  const color = calendarOf(block.occurrence)?.color || '#cc0099'
  return {
    top: `${block.top}px`,
    height: `${block.height}px`,
    left: `calc(${block.left}% + 2px)`,
    width: `calc(${block.width}% - 4px)`,
    borderLeft: `3px solid ${color}`,
    background: `color-mix(in oklab, ${color} 26%, var(--color-base-100, #16161a))`
  }
}

function allDayStyle(occurrence) {
  const color = calendarOf(occurrence)?.color || '#cc0099'
  return {
    borderLeft: `3px solid ${color}`,
    background: `color-mix(in oklab, ${color} 22%, var(--color-base-100, #16161a))`
  }
}

function createFromSlot(column, hour, event) {
  // Keep the clicked half-hour: the slot is 1h tall, so its middle is :30.
  const rect = event?.currentTarget?.getBoundingClientRect?.()
  let minutes = 0
  if (rect && rect.height) {
    const ratio = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))
    minutes = Math.floor((ratio * 60) / 15) * 15
  }
  const date = new Date(column.date.getFullYear(), column.date.getMonth(), column.date.getDate(), hour, minutes)
  emit('create', date)
}

onMounted(() => {
  ticker = setInterval(() => { now.value = new Date() }, 60000)
  if (scrollHost.value) scrollHost.value.scrollTop = 7.5 * HOUR_HEIGHT
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<template>
  <div class="flex flex-col flex-1 min-h-0">
    <!-- Day headers -->
    <div class="flex border-b border-base-300 bg-base-200/60 shrink-0">
      <div class="w-14 shrink-0 border-r border-base-300"></div>
      <div
        v-for="column in columns"
        :key="column.key"
        class="flex-1 min-w-0 py-1 text-center border-r border-base-300 last:border-r-0"
      >
        <div class="text-[10px] uppercase tracking-wider text-base-content/40">{{ column.label }}</div>
        <div
          class="mx-auto mt-0.5 text-[15px] font-semibold w-7 h-7 rounded-full flex items-center justify-center tabular-nums"
          :class="column.isToday ? 'bg-codx-primary text-white' : ''"
        >
          {{ column.number }}
        </div>
      </div>
    </div>

    <!-- All-day row -->
    <div class="flex border-b border-base-300 shrink-0 max-h-24 overflow-y-auto">
      <div class="w-14 shrink-0 border-r border-base-300 text-[10px] text-base-content/40 text-right pr-1.5 py-1">
        all day
      </div>
      <div
        v-for="column in columns"
        :key="`allday-${column.key}`"
        class="flex-1 min-w-0 border-r border-base-300 last:border-r-0 p-0.5 flex flex-col gap-0.5 min-h-6"
      >
        <button
          v-for="occurrence in column.allDay"
          :key="`${occurrence.event_id}-${occurrence.start}`"
          class="text-left rounded-[5px] pl-1 pr-1 py-0.5 text-[11px] truncate hover:brightness-125"
          :style="allDayStyle(occurrence)"
          :title="occurrence.title"
          @click="emit('select', occurrence)"
        >
          {{ occurrence.title }}
        </button>
      </div>
    </div>

    <!-- Hour grid -->
    <div ref="scrollHost" class="flex-1 min-h-0 overflow-y-auto">
      <div class="flex">
        <div class="w-14 shrink-0 border-r border-base-300">
          <div
            v-for="hour in HOURS"
            :key="`label-${hour}`"
            class="text-[10px] text-base-content/40 text-right pr-1.5 font-mono -translate-y-1.5"
            :style="{ height: `${HOUR_HEIGHT}px` }"
          >
            {{ String(hour).padStart(2, '0') }}:00
          </div>
        </div>

        <div
          v-for="column in columns"
          :key="`col-${column.key}`"
          class="relative flex-1 min-w-0 border-r border-base-300 last:border-r-0"
          :class="column.weekend ? 'bg-base-content/[0.025]' : ''"
          :style="{ height: `${24 * HOUR_HEIGHT}px` }"
        >
          <div
            v-for="hour in HOURS"
            :key="`slot-${column.key}-${hour}`"
            class="absolute left-0 right-0 border-t border-base-content/[0.07] hover:bg-base-content/[0.04]"
            :style="{ top: `${hour * HOUR_HEIGHT}px`, height: `${HOUR_HEIGHT}px` }"
            :title="`Create an event at ${String(hour).padStart(2, '0')}:00`"
            @click="createFromSlot(column, hour, $event)"
          ></div>

          <button
            v-for="block in column.timed"
            :key="`${block.occurrence.event_id}-${block.occurrence.start}`"
            class="absolute rounded-[5px] px-1 py-0.5 text-[11px] leading-tight text-left overflow-hidden hover:brightness-125 hover:z-10"
            :style="blockStyle(block)"
            :title="`${block.occurrence.title} · ${formatTimeRange(block.occurrence.start, block.occurrence.end)}`"
            @click.stop="emit('select', block.occurrence)"
          >
            <div class="font-semibold truncate">{{ block.occurrence.title }}</div>
            <div class="text-base-content/60 tabular-nums text-[10px]">
              {{ formatTimeRange(block.occurrence.start, block.occurrence.end) }}
            </div>
            <i
              v-if="block.occurrence.read_only"
              class="fa-solid fa-lock text-[8px] text-base-content/40"
              title="Managed elsewhere — read-only"
            ></i>
          </button>

          <div
            v-if="column.isToday"
            class="absolute left-0 right-0 border-t-2 border-error z-20 pointer-events-none"
            :style="{ top: `${nowTop}px` }"
          >
            <span class="absolute -left-1 -top-1 w-2 h-2 rounded-full bg-error"></span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
