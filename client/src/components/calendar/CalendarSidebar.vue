<script setup>
/**
 * Calendar sidebar — the left rail of the Agenda screen.
 *
 * Two rules from the design (analysis §9.1), both consequences of D1/D2:
 *
 * 1. Calendars are grouped by **scope**, never by project:
 *    *My calendars* (private), *Shared with me* (team + workspace),
 *    *codx-junior* (system), *Other* (external / unknown).
 * 2. Toggling a calendar is a **per-viewer** preference (a hidden calendar is
 *    remembered in `prefs.hidden_calendars`), never a global mutation of a
 *    shared calendar.
 *
 * The mini month is a jump target: dots summarise the days that have items.
 */
import { computed, ref, watch } from 'vue'
import {
  DOW,
  MONTHS,
  addDays,
  addMonths,
  dateKey,
  formatDayLong,
  sameDay,
  startOfMonth,
  startOfWeek
} from '@/utils/calendarDate'

const props = defineProps({
  /** `[{ key, title, items: [calendar] }]` from the store. */
  groups: { type: Array, default: () => [] },
  /** Ids of the calendars currently visible. */
  visibleIds: { type: Array, default: () => [] },
  /** `{ 'YYYY-MM-DD': ['#cc0099', ...] }` — mini-month dots. */
  dotsByDay: { type: Object, default: () => ({}) },
  cursorDate: { type: Date, default: () => new Date() },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['toggle', 'jump', 'create', 'create-calendar'])

// Mini month can page away from the cursor without moving it: it is just a
// picker. It re-aligns to the cursor whenever the cursor moves.
const miniBase = ref(startOfMonth(props.cursorDate))

// The mini month is a picker, but it must follow the toolbar: jumping to
// another month (or clicking "Today") realigns it to the cursor.
watch(() => props.cursorDate, (date) => {
  miniBase.value = startOfMonth(date)
})

const creating = ref(false)
const draft = ref({ name: '', color: '#cc0099', scope: 'private' })

const SCOPE_HINT = {
  private: { label: 'me', icon: 'fa-solid fa-lock' },
  team: { label: 'team', icon: 'fa-solid fa-people-group' },
  workspace: { label: 'all', icon: 'fa-solid fa-globe' },
  system: { label: 'ro', icon: 'fa-solid fa-robot' },
  external: { label: 'ics', icon: 'fa-solid fa-rss' }
}

function scopeHint(calendar) {
  return SCOPE_HINT[calendar?.scope] || { label: '', icon: 'fa-solid fa-calendar' }
}

function isVisible(calendar) {
  return props.visibleIds.includes(calendar.id)
}

const miniDays = computed(() => {
  const first = startOfMonth(miniBase.value)
  const gridStart = startOfWeek(first)
  const days = []
  for (let i = 0; i < 42; i++) {
    const date = addDays(gridStart, i)
    days.push({
      date,
      key: dateKey(date),
      number: date.getDate(),
      other: date.getMonth() !== first.getMonth(),
      today: sameDay(date, new Date()),
      cursor: sameDay(date, props.cursorDate),
      dots: props.dotsByDay?.[dateKey(date)] || []
    })
  }
  return days
})

function shiftMini(months) {
  miniBase.value = addMonths(miniBase.value, months)
}

function jumpTo(date) {
  emit('jump', date)
}

function startCreate() {
  creating.value = true
  draft.value = { name: '', color: '#cc0099', scope: 'private' }
}

function cancelCreate() {
  creating.value = false
}

function submitCreate() {
  const name = draft.value.name.trim()
  if (!name) return
  emit('create-calendar', { name, color: draft.value.color, scope: draft.value.scope })
  creating.value = false
}
</script>

<template>
  <aside class="w-60 shrink-0 flex flex-col gap-3 p-3 overflow-y-auto border-r border-base-300 bg-base-200/40">
    <button class="btn btn-sm btn-primary gap-2" @click="emit('create')">
      <i class="fa-solid fa-plus"></i>
      Create
    </button>

    <!-- Mini month -->
    <section class="select-none">
      <div class="flex items-center justify-between mb-1">
        <span class="text-sm font-semibold">
          {{ MONTHS[miniBase.getMonth()] }} {{ miniBase.getFullYear() }}
        </span>
        <span class="flex items-center">
          <button class="btn btn-xs btn-ghost px-1" title="Previous month" @click="shiftMini(-1)">‹</button>
          <button class="btn btn-xs btn-ghost px-1" title="Next month" @click="shiftMini(1)">›</button>
        </span>
      </div>
      <div class="grid grid-cols-7 text-center gap-y-0.5">
        <span v-for="day in DOW" :key="day" class="text-[10px] text-base-content/40">{{ day[0] }}</span>
        <button
          v-for="day in miniDays"
          :key="day.key"
          class="relative aspect-square rounded-full text-[11px] flex items-center justify-center transition-colors"
          :class="[
            day.other ? 'opacity-35' : '',
            day.today ? 'bg-codx-primary text-white font-bold' : 'hover:bg-base-300 text-base-content/70',
            day.cursor && !day.today ? 'ring-1 ring-primary' : ''
          ]"
          :title="formatDayLong(day.date)"
          @click="jumpTo(day.date)"
        >
          {{ day.number }}
          <span class="absolute bottom-0.5 left-0 right-0 flex justify-center gap-0.5">
            <i
              v-for="(color, index) in day.dots.slice(0, 3)"
              :key="index"
              class="block w-[3px] h-[3px] rounded-full"
              :style="{ background: color }"
            ></i>
          </span>
        </button>
      </div>
    </section>

    <!-- Scope-grouped calendars -->
    <section v-for="group in groups" :key="group.key" class="flex flex-col gap-0.5">
      <h3 class="text-[10px] uppercase tracking-wider font-bold text-base-content/40 mb-0.5">
        {{ group.title }}
      </h3>
      <button
        v-for="calendar in group.items"
        :key="calendar.id"
        class="flex items-center gap-2 px-1.5 py-1 rounded-md text-left hover:bg-base-300 transition-colors"
        :title="`${calendar.name} · ${calendar.scope}`"
        @click="emit('toggle', calendar.id)"
      >
        <span
          class="w-3.5 h-3.5 rounded-[4px] flex items-center justify-center shrink-0 border"
          :class="isVisible(calendar) ? '' : 'bg-transparent'"
          :style="{
            background: isVisible(calendar) ? calendar.color : 'transparent',
            borderColor: calendar.color
          }"
        >
          <i v-if="isVisible(calendar)" class="fa-solid fa-check text-[7px] text-black/70"></i>
        </span>
        <span class="flex-1 truncate text-xs" :class="calendar.read_only ? 'text-base-content/60' : ''">
          {{ calendar.name }}
        </span>
        <i
          v-if="scopeHint(calendar).icon"
          :class="[scopeHint(calendar).icon, calendar.scope === 'private' ? 'text-base-content/30' : 'text-base-content/40']"
          class="text-[9px]"
          :title="calendar.scope"
        ></i>
        <span
          v-if="calendar.read_only"
          class="badge badge-xs badge-outline text-[9px] py-0 px-1 text-warning border-warning/40"
        >ro</span>
      </button>
      <p v-if="!group.items.length" class="text-[11px] text-base-content/30 px-1.5">None</p>
    </section>

    <div v-if="!groups.length && !loading" class="text-[11px] text-base-content/40 px-1">
      No calendar yet — create one below.
    </div>

    <!-- New calendar (POST /api/calendar/calendars) -->
    <form v-if="creating" class="flex flex-col gap-1.5 bg-base-300/60 rounded-box p-2" @submit.prevent="submitCreate">
      <input
        v-model="draft.name"
        class="input input-xs input-bordered w-full"
        placeholder="Calendar name"
        autofocus
      />
      <div class="flex items-center gap-1.5">
        <input v-model="draft.color" type="color" class="w-6 h-6 rounded border border-base-300 bg-transparent p-0" />
        <select v-model="draft.scope" class="select select-xs select-bordered flex-1">
          <option value="private">Private</option>
          <option value="team">Team</option>
          <option value="workspace">Workspace</option>
        </select>
      </div>
      <div class="flex items-center gap-1.5">
        <button type="button" class="btn btn-xs btn-ghost flex-1" @click="cancelCreate">Cancel</button>
        <button type="submit" class="btn btn-xs btn-primary flex-1" :disabled="!draft.name.trim()">Create</button>
      </div>
    </form>
    <button
      v-else
      class="flex items-center gap-2 px-1.5 py-1 rounded-md text-xs text-primary font-semibold hover:bg-base-300"
      @click="startCreate"
    >
      <i class="fa-solid fa-plus text-[10px] w-3.5 text-center"></i>
      Add calendar
    </button>

    <div class="flex-1"></div>
    <p class="text-[10px] leading-relaxed text-base-content/35">
      The calendar is <b>global</b>: one agenda for every project. Use the project filter above to narrow it.
    </p>
  </aside>
</template>
