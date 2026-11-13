<script setup>
/**
 * Event editor — the M0 create/edit surface (analysis §9.2, §5.2).
 *
 * Scope of M0: one event, several calendar memberships (R6), a project, the
 * time block, all-day, reminders and guests. Recurrence is **preserved** when
 * editing but not edited here: the recurrence editor belongs to M3, where the
 * server can expand and validate the rule (`invalid_recurrence`).
 *
 * The component is dumb: it validates the form and emits `save` with an API
 * payload. The store owns the request, the optimistic locking (`revision`) and
 * the range reload.
 */
import { computed, ref } from 'vue'
import {
  addDays,
  fromLocalInput,
  localTimezone,
  parseDate,
  startOfDay,
  toIsoDate,
  toLocalInput
} from '@/utils/calendarDate'

const props = defineProps({
  /** Raw event being edited (`null` = create). */
  event: { type: Object, default: null },
  /** Prefilled start of a new event (clicked slot / day cell). */
  defaultStart: { type: Date, default: null },
  /** Writable calendars (read-only ones are never offered). */
  calendars: { type: Array, default: () => [] },
  /** `[{ id, name, role }]` project options, `personal` included. */
  projects: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false }
})

const emit = defineEmits(['save', 'cancel', 'delete'])

const REMINDER_OPTIONS = [
  { value: -1, label: 'Use calendar default' },
  { value: 0, label: 'At start' },
  { value: 5, label: '5 min before' },
  { value: 10, label: '10 min before' },
  { value: 15, label: '15 min before' },
  { value: 30, label: '30 min before' },
  { value: 60, label: '1 hour before' },
  { value: 180, label: '3 hours before' }
]

const SECOND_REMINDER_OPTIONS = REMINDER_OPTIONS.filter(option => [0, 5, 10, 60, -1].includes(option.value))

const isNew = computed(() => !props.event?.id)
// `can_write` already resolves scope + role on the server; fall back to the
// stored flag for payloads that do not carry it.
const editableCalendars = computed(() =>
  (props.calendars || []).filter(calendar => (calendar.can_write ?? !calendar.read_only))
)

function initialStart() {
  const raw = props.event?.start
  const start = parseDate(raw) || props.defaultStart || new Date()
  return props.event ? start : new Date(start.getFullYear(), start.getMonth(), start.getDate(), start.getHours(), start.getMinutes())
}

function initialEnd(start) {
  const raw = props.event?.end
  return parseDate(raw) || new Date(start.getTime() + 60 * 60000)
}

const startDate = initialStart()
const endDate = initialEnd(startDate)

const form = ref({
  title: props.event?.title || '',
  calendarIds: (props.event?.calendar_ids || []).filter(id =>
    editableCalendars.value.some(calendar => calendar.id === id)
  ).length
    ? (props.event.calendar_ids || []).filter(id => editableCalendars.value.some(calendar => calendar.id === id))
    : (editableCalendars.value[0]?.id ? [editableCalendars.value[0].id] : []),
  projectId: props.event?.project_id || '',
  allDay: !!props.event?.all_day,
  start: toLocalInput(startDate),
  end: toLocalInput(endDate),
  location: props.event?.location || '',
  description: props.event?.description || '',
  guests: (props.event?.guests || []).join(', '),
  reminder1: props.event?.reminders?.length ? props.event.reminders[0].minutes_before : -1,
  reminder2: props.event?.reminders?.[1]?.minutes_before ?? -1
})

const error = ref('')

function toggleCalendar(id) {
  const ids = form.value.calendarIds || []
  form.value.calendarIds = ids.includes(id) ? ids.filter(item => item !== id) : [...ids, id]
}

function onAllDayChange() {
  if (!form.value.allDay) return
  const start = fromLocalInput(form.value.start) || new Date()
  const day = startOfDay(start)
  form.value.start = toLocalInput(day)
  form.value.end = toLocalInput(addDays(day, 1))
}

function submit() {
  const title = form.value.title.trim()
  if (!title) {
    error.value = 'A title is required.'
    return
  }
  const calendarIds = [...new Set(form.value.calendarIds || [])]
  if (!calendarIds.length) {
    error.value = 'Pick at least one calendar.'
    return
  }
  const start = fromLocalInput(form.value.start)
  const end = fromLocalInput(form.value.end)
  if (!start || !end) {
    error.value = 'Start and end are required.'
    return
  }
  if (end.getTime() <= start.getTime()) {
    error.value = 'The end must be after the start.'
    return
  }

  const reminders = []
  if (form.value.reminder1 >= 0) reminders.push({ channel: 'popup', minutes_before: Number(form.value.reminder1) })
  if (form.value.reminder2 >= 0) reminders.push({ channel: 'popup', minutes_before: Number(form.value.reminder2) })

  const payload = {
    title,
    calendar_ids: calendarIds,
    project_id: form.value.projectId || null,
    description: form.value.description.trim(),
    location: form.value.location.trim(),
    start: toIsoDate(start),
    end: toIsoDate(end),
    all_day: !!form.value.allDay,
    timezone: localTimezone(),
    reminders,
    guests: form.value.guests.split(',').map(value => value.trim()).filter(Boolean),
    status: props.event?.status || 'confirmed',
    visibility: props.event?.visibility || 'default',
    // Recurrence is preserved, never edited in M0 (the editor belongs to M3).
    recurrence: props.event?.recurrence || null,
    source: props.event?.source || 'native',
    refs: props.event?.refs || {}
  }

  error.value = ''
  emit('save', { payload, revision: props.event?.revision })
}
</script>

<template>
  <div class="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/55">
    <form
      class="w-full max-w-xl max-h-[90vh] overflow-y-auto rounded-box bg-base-200 border border-base-300 shadow-2xl flex flex-col"
      role="dialog"
      aria-modal="true"
      @submit.prevent="submit"
    >
      <header class="flex items-center justify-between px-4 py-3 border-b border-base-300">
        <h2 class="text-[15px] font-semibold">{{ isNew ? 'New event' : 'Edit event' }}</h2>
        <button type="button" class="btn btn-xs btn-ghost btn-circle" title="Close" @click="emit('cancel')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </header>

      <div class="px-4 py-3 flex flex-col gap-3">
        <label class="form-control w-full">
          <span class="label-text text-xs text-base-content/60 font-semibold">Title</span>
          <input
            v-model="form.title"
            class="input input-sm input-bordered w-full"
            placeholder="Spec review…"
            autofocus
          />
        </label>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Project</span>
            <select v-model="form.projectId" class="select select-sm select-bordered w-full">
              <option value="">Personal · no project</option>
              <option v-for="project in projects" :key="project.id" :value="project.id">
                {{ project.name }}
              </option>
            </select>
          </label>

          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Reminder</span>
            <select v-model.number="form.reminder1" class="select select-sm select-bordered w-full">
              <option v-for="option in REMINDER_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
        </div>

        <label class="label cursor-pointer gap-2 justify-start py-0">
          <input v-model="form.allDay" type="checkbox" class="checkbox checkbox-xs" @change="onAllDayChange" />
          <span class="label-text text-xs">All day</span>
        </label>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Starts</span>
            <input v-model="form.start" type="datetime-local" class="input input-sm input-bordered w-full" />
          </label>
          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Ends</span>
            <input v-model="form.end" type="datetime-local" class="input input-sm input-bordered w-full" />
          </label>
        </div>

        <!-- Calendar memberships: one event, N calendars (R6) -->
        <div class="flex flex-col gap-1">
          <span class="text-xs text-base-content/60 font-semibold">
            Calendars
            <span class="font-normal text-base-content/40">(an event can live in several)</span>
          </span>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="calendar in editableCalendars"
              :key="calendar.id"
              type="button"
              class="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-full border transition-colors"
              :class="form.calendarIds.includes(calendar.id)
                ? 'border-primary bg-primary/15 text-base-content'
                : 'border-base-300 text-base-content/60'"
              @click="toggleCalendar(calendar.id)"
            >
              <i class="inline-block w-1.5 h-1.5 rounded-full" :style="{ background: calendar.color }"></i>
              {{ calendar.name }}
            </button>
          </div>
          <p v-if="!editableCalendars.length" class="text-[11px] text-warning">
            No writable calendar: create one in the sidebar first.
          </p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Second reminder</span>
            <select v-model.number="form.reminder2" class="select select-sm select-bordered w-full">
              <option v-for="option in SECOND_REMINDER_OPTIONS" :key="option.value" :value="option.value">
                {{ option.value === -1 ? 'None' : option.label }}
              </option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-xs text-base-content/60 font-semibold">Location</span>
            <input v-model="form.location" class="input input-sm input-bordered w-full" placeholder="Meet · platform" />
          </label>
        </div>

        <label class="form-control">
          <span class="label-text text-xs text-base-content/60 font-semibold">Description</span>
          <textarea v-model="form.description" class="textarea textarea-sm textarea-bordered w-full" rows="2"></textarea>
        </label>

        <label class="form-control">
          <span class="label-text text-xs text-base-content/60 font-semibold">Guests</span>
          <input v-model="form.guests" class="input input-sm input-bordered w-full" placeholder="marta, jordi" />
        </label>

        <p v-if="error" class="text-xs text-error">{{ error }}</p>
        <p v-if="!isNew && event?.recurrence" class="text-[11px] text-base-content/40">
          Recurring event ({{ event.recurrence.rrule }}) — the recurrence rule is edited in a later milestone.
        </p>
      </div>

      <footer class="flex items-center gap-2 px-4 py-3 border-t border-base-300 bg-base-300/40">
        <button
          v-if="!isNew"
          type="button"
          class="btn btn-sm btn-outline btn-error mr-auto"
          @click="emit('delete', event)"
        >
          Delete
        </button>
        <button type="button" class="btn btn-sm btn-ghost" @click="emit('cancel')">Cancel</button>
        <button type="submit" class="btn btn-sm btn-primary" :disabled="busy">
          <i v-if="busy" class="fa-solid fa-spinner animate-spin"></i>
          {{ isNew ? 'Create' : 'Save' }}
        </button>
      </footer>
    </form>
  </div>
</template>
