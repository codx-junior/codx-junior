<script setup>
/**
 * Calendar view — the Agenda / Calendar manager screen.
 *
 * Behaviour is ported from the verified prototype
 * (`design/06-calendar/02-prototype/index.html`); data comes from the backend
 * contract of `design/06-calendar/01-analysis.md` §5.
 *
 * Two rules drive the whole screen and both come from the locked decisions:
 *
 * 1. **The project filter is the only thing that narrows the agenda** (D1).
 *    The store is global, so selecting a project never reloads a project nor
 *    changes the URL — it re-queries the same store with `projects[]` (OR).
 * 2. **The sidebar groups by SCOPE, not by project** (D2): private calendars,
 *    shared ones (team + workspace), the `codx-junior` projection and the
 *    external (ICS) feeds.
 *
 * The server returns occurrences already expanded and timezone-shifted, so the
 * view does no date arithmetic beyond formatting and grid placement.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { $storex } from '@/store'
import { PERSONAL_PROJECT } from '@/store/calendar'
import { formatDayLong } from '@/utils/calendarDate'
import CalendarSidebar from '@/components/calendar/CalendarSidebar.vue'
import MonthGrid from '@/components/calendar/MonthGrid.vue'
import WeekGrid from '@/components/calendar/WeekGrid.vue'
import AgendaList from '@/components/calendar/AgendaList.vue'
import EventEditor from '@/components/calendar/EventEditor.vue'
import EventPopover from '@/components/calendar/EventPopover.vue'

const props = defineProps({
  /** Route params handed over by <AppRouteView> (deep-links). */
  params: { type: Object, default: () => ({}) }
})

const VIEW_OPTIONS = [
  { value: 'month', label: 'Month', key: 'm' },
  { value: 'week', label: 'Week', key: 'w' },
  { value: 'day', label: 'Day', key: 'd' },
  { value: 'agenda', label: 'Agenda', key: 'a' }
]

const store = $storex.calendar

// ─── Store projections ──────────────────────────────────────────────────────

const view = computed(() => store.activeView)
const cursorDate = computed(() => store.cursorDate)
const rangeLabel = computed(() => store.rangeLabel)
const calendars = computed(() => store.calendarList)
const calendarGroups = computed(() => store.calendarGroups)
const visibleIds = computed(() => store.visibleCalendarIds)
const occurrencesByDay = computed(() => store.occurrencesByDay)
const projectOptions = computed(() => store.projectOptions)
const activeProjects = computed(() => store.activeProjects)
const isAllProjects = computed(() => store.isAllProjects)
const loading = computed(() => store.isLoading)
const error = computed(() => store.loadError)
const filteredCount = computed(() => store.filteredCount)
const totalCount = computed(() => store.totalCount)
const writableCalendars = computed(() => calendars.value.filter(calendar => !calendar.read_only))

/** Project selector of the editor: `personal` is the empty option. */
const editorProjects = computed(() =>
  projectOptions.value.filter(option => option.id !== PERSONAL_PROJECT)
)

/** Mini-month dots: one colour per calendar present on that day (max 3). */
const dotsByDay = computed(() => {
  const colorsByCalendar = {}
  for (const calendar of calendars.value) colorsByCalendar[calendar.id] = calendar.color
  const dots = {}
  for (const [key, items] of Object.entries(occurrencesByDay.value || {})) {
    const colors = []
    for (const occurrence of items) {
      const color = colorsByCalendar[occurrence.calendar_id] || colorsByCalendar[(occurrence.calendar_ids || [])[0]]
      if (color && !colors.includes(color)) colors.push(color)
    }
    dots[key] = colors.slice(0, 3)
  }
  return dots
})

const selectedProjectNames = computed(() => {
  if (isAllProjects.value) return 'All projects'
  return projectOptions.value
    .filter(option => activeProjects.value.includes(option.id))
    .map(option => option.name)
    .join(', ')
})

// ─── Local UI state ─────────────────────────────────────────────────────────

const popover = ref(null)          // { occurrence, position }
const editor = ref(null)           // { event, defaultStart, key }
const editorBusy = ref(false)
const actionError = ref('')

// A chip click and the document listener see the SAME event: this guard keeps
// the popover from being closed by the very click that opened it.
let openingClick = false

async function selectView(nextView) {
  await store.setView(nextView)
}

async function goToday() {
  await store.goToday()
}

async function shift(direction) {
  await store.shift(direction)
}

function openPopover(occurrence) {
  openingClick = true
  const position = { x: window.innerWidth / 2, y: window.innerHeight / 2 }
  popover.value = { occurrence, position }
}

function closePopover() {
  popover.value = null
}

/** Open the day of a "+N more" click (month view → day view, §9.2). */
async function openDay(date) {
  await store.setCursor(date)
  await store.setView('day')
}

function openCreate(defaultStart) {
  closePopover()
  editor.value = { event: null, defaultStart: defaultStart || cursorDate.value, key: `new-${Date.now()}` }
}

/**
 * Edit an existing occurrence. The occurrence only carries what a grid needs,
 * so the RAW event is fetched first (the editor needs the rule and the full
 * description); the occurrence is the fallback when the read fails.
 */
async function openEdit(occurrence) {
  closePopover()
  actionError.value = ''
  const raw = await store.loadEvent(occurrence.event_id)
  editor.value = {
    event: raw || {
      id: occurrence.event_id,
      calendar_ids: occurrence.calendar_ids,
      project_id: occurrence.project_id,
      title: occurrence.title,
      start: occurrence.start,
      end: occurrence.end,
      all_day: occurrence.all_day,
      source: occurrence.source,
      refs: occurrence.refs,
      revision: occurrence.revision
    },
    defaultStart: null,
    key: `edit-${occurrence.event_id}-${occurrence.start}`
  }
}

function closeEditor() {
  editor.value = null
  editorBusy.value = false
}

/** Store `save` event from the editor: create or update, then reload. */
async function saveEvent({ payload, revision }) {
  if (!editor.value) return
  editorBusy.value = true
  actionError.value = ''
  try {
    if (editor.value.event?.id) {
      await store.updateEvent({ id: editor.value.event.id, payload, revision })
    } else {
      await store.createEvent(payload)
    }
    closeEditor()
  } catch (exception) {
    actionError.value = exception?.response?.data?.detail || exception?.message || 'Could not save the event'
  } finally {
    editorBusy.value = false
  }
}

async function deleteEvent(occurrenceOrEvent) {
  const id = occurrenceOrEvent?.event_id || occurrenceOrEvent?.id
  if (!id) return
  const title = occurrenceOrEvent.title || 'this event'
  if (!window.confirm(`Delete "${title}"?`)) return
  actionError.value = ''
  try {
    await store.deleteEvent({ id })
    closePopover()
    closeEditor()
  } catch (exception) {
    actionError.value = exception?.response?.data?.detail || exception?.message || 'Could not delete the event'
  }
}

/** Read-only projection item: open the source instead of editing. */
function openSource(occurrence) {
  if (occurrence.source === 'task' && occurrence.refs?.chat_id) {
    $storex.ui.openChat?.({ id: occurrence.refs.chat_id })
    return
  }
  actionError.value = 'This item is managed by the source that produced it.'
}

async function toggleCalendar(calendarId) {
  await store.toggleCalendar(calendarId)
}

async function jumpTo(date) {
  await store.setCursor(date)
}

/** Multi-select project filter: an empty list means "All projects". */
async function toggleProject(projectId) {
  const current = activeProjects.value || []
  const next = current.includes(projectId)
    ? current.filter(id => id !== projectId)
    : [...current, projectId]
  await store.setProjectFilter(next)
}

async function selectAllProjects() {
  await store.setProjectFilter([])
}

async function createCalendar(payload) {
  actionError.value = ''
  try {
    await store.createCalendar(payload)
  } catch (exception) {
    actionError.value = exception?.response?.data?.detail || exception?.message || 'Could not create the calendar'
  }
}

// ─── Keyboard (§9.3.5) ──────────────────────────────────────────────────────

function isTyping(target) {
  if (!target) return false
  const tag = (target.tagName || '').toLowerCase()
  return ['input', 'textarea', 'select'].includes(tag) || target.isContentEditable
}

function onKeydown(event) {
  if (isTyping(event.target)) return
  const key = String(event.key || '').toLowerCase()

  if (key === 'escape') {
    closePopover()
    closeEditor()
    return
  }
  if (key === 'n') {
    event.preventDefault()
    openCreate()
    return
  }
  if (key === 't') {
    event.preventDefault()
    goToday()
    return
  }
  if (['m', 'w', 'd', 'a'].includes(key)) {
    event.preventDefault()
    selectView(VIEW_OPTIONS.find(option => option.key === key)?.value || 'month')
    return
  }
  if (key === 'arrowleft') {
    event.preventDefault()
    shift(-1)
    return
  }
  if (key === 'arrowright') {
    event.preventDefault()
    shift(1)
  }
}

function onDocumentClick(event) {
  if (openingClick) {
    openingClick = false
    return
  }
  if (!popover.value) return
  if (event.target?.closest?.('[data-calendar-popover]')) return
  closePopover()
}

onMounted(async () => {
  // Deep-link: `?view=week` (or the app payload) opens that view.
  const initialView = props.params?.view
  if (initialView && initialView !== store.activeView) await store.setView(initialView)
  const initialDate = props.params?.date
  if (initialDate) await store.setCursor(initialDate)
  await store.ensureLoaded()
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('click', onDocumentClick)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <div class="h-full flex flex-col bg-base-100 text-base-content overflow-hidden">
    <!-- Toolbar -->
    <header class="flex items-center gap-2 px-3 py-2 border-b border-base-300 bg-base-200/60 shrink-0 flex-wrap">
      <button class="btn btn-sm btn-outline" title="Go to today (t)" @click="goToday">Today</button>
      <button class="btn btn-sm btn-ghost btn-square" title="Previous (←)" @click="shift(-1)">‹</button>
      <button class="btn btn-sm btn-ghost btn-square" title="Next (→)" @click="shift(1)">›</button>
      <div class="text-[15px] font-semibold whitespace-nowrap min-w-44">{{ rangeLabel }}</div>

      <span v-if="loading" class="loading loading-spinner loading-xs text-primary"></span>

      <div class="flex-1"></div>

      <div class="join" role="tablist">
        <button
          v-for="option in VIEW_OPTIONS"
          :key="option.value"
          class="btn btn-sm join-item"
          :class="view === option.value ? 'btn-primary' : 'btn-ghost'"
          role="tab"
          :aria-selected="view === option.value"
          :title="`${option.label} (${option.key})`"
          @click="selectView(option.value)"
        >
          {{ option.label }}
        </button>
      </div>

      <button class="btn btn-sm btn-primary gap-1.5" title="New event (n)" @click="openCreate()">
        <i class="fa-solid fa-plus text-[11px]"></i>
        New event
      </button>
    </header>

    <!-- Project filter: the ONLY thing that narrows a global agenda (D1) -->
    <div class="flex items-center gap-1.5 px-3 py-1.5 border-b border-base-300 shrink-0 overflow-x-auto">
      <span class="text-[10px] uppercase tracking-wider text-base-content/40 whitespace-nowrap mr-0.5">Projects</span>
      <button
        class="px-2.5 py-0.5 rounded-full text-[11px] border whitespace-nowrap transition-colors"
        :class="isAllProjects ? 'border-primary bg-primary/20 font-semibold' : 'border-base-300 text-base-content/60 hover:border-base-content/30'"
        :aria-pressed="isAllProjects"
        @click="selectAllProjects"
      >
        All
      </button>
      <button
        v-for="project in projectOptions"
        :key="project.id"
        class="px-2.5 py-0.5 rounded-full text-[11px] border whitespace-nowrap transition-colors"
        :class="activeProjects.includes(project.id)
          ? 'border-primary bg-primary/20 font-semibold'
          : 'border-base-300 text-base-content/60 hover:border-base-content/30'"
        :aria-pressed="activeProjects.includes(project.id)"
        :title="project.role ? `${project.name} · ${project.role}` : project.name"
        @click="toggleProject(project.id)"
      >
        {{ project.name }}
      </button>
      <span class="ml-auto text-[10px] font-mono text-base-content/40 whitespace-nowrap">
        {{ filteredCount }}/{{ totalCount }} events · {{ visibleIds.length }}/{{ calendars.length }} calendars
      </span>
    </div>

    <div v-if="error || actionError" class="px-3 py-1.5 text-xs bg-error/10 text-error border-b border-error/30 shrink-0">
      <i class="fa-solid fa-triangle-exclamation mr-1"></i>
      {{ actionError || error }}
    </div>

    <!-- Body -->
    <div class="flex flex-1 min-h-0">
      <CalendarSidebar
        :groups="calendarGroups"
        :visible-ids="visibleIds"
        :dots-by-day="dotsByDay"
        :cursor-date="cursorDate"
        :loading="loading"
        @toggle="toggleCalendar"
        @jump="jumpTo"
        @create="openCreate()"
        @create-calendar="createCalendar"
      />

      <main class="flex-1 min-w-0 flex flex-col relative">
        <MonthGrid
          v-if="view === 'month'"
          :cursor-date="cursorDate"
          :occurrences-by-day="occurrencesByDay"
          :calendars="calendars"
          @select="openPopover"
          @more="openDay"
          @create="openCreate"
        />
        <WeekGrid
          v-else-if="view === 'week'"
          :cursor-date="cursorDate"
          :occurrences-by-day="occurrencesByDay"
          :calendars="calendars"
          :days="7"
          @select="openPopover"
          @create="openCreate"
        />
        <WeekGrid
          v-else-if="view === 'day'"
          :cursor-date="cursorDate"
          :occurrences-by-day="occurrencesByDay"
          :calendars="calendars"
          :days="1"
          @select="openPopover"
          @create="openCreate"
        />
        <AgendaList
          v-else
          :cursor-date="cursorDate"
          :occurrences-by-day="occurrencesByDay"
          :calendars="calendars"
          :loading="loading"
          @select="openPopover"
        />

        <p v-if="!isAllProjects" class="text-[10px] text-base-content/40 px-3 py-1 border-t border-base-300">
          Filtered by {{ selectedProjectNames }} — <button class="link" @click="selectAllProjects">show all projects</button>
        </p>
        <p v-else-if="!calendars.length && !loading" class="text-[10px] text-base-content/40 px-3 py-1 border-t border-base-300">
          No calendar yet. Create one in the sidebar, or run the backend migration for
          <code class="font-mono">/api/calendar</code>.
        </p>
      </main>
    </div>

    <!-- Detail popover -->
    <div data-calendar-popover>
      <EventPopover
        v-if="popover"
        :occurrence="popover.occurrence"
        :calendars="calendars"
        :position="popover.position"
        @close="closePopover"
        @edit="openEdit"
        @delete="deleteEvent"
        @open-source="openSource"
      />
    </div>

    <!-- Event editor -->
    <EventEditor
      v-if="editor"
      :key="editor.key"
      :event="editor.event"
      :default-start="editor.defaultStart"
      :calendars="writableCalendars"
      :projects="editorProjects"
      :busy="editorBusy"
      @save="saveEvent"
      @cancel="closeEditor"
      @delete="deleteEvent"
    />

    <div class="sr-only" aria-live="polite">
      {{ formatDayLong(cursorDate) }} · {{ filteredCount }} events in view
    </div>
  </div>
</template>
