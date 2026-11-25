<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { API } from '@/api/api'
import { useCompanyI18n } from './i18n'

/**
 * Compact company calendar for the overview.
 *
 * The calendar API is project-scoped; this widget asks for **every project
 * linked to the company** and shows the next few events across all of them.
 */
const props = defineProps({
  snapshot: { type: Object, default: null }
})

const emit = defineEmits(['open'])

const { t, locale } = useCompanyI18n()
const loading = ref(false)
const error = ref('')
const occurrences = ref([])

const projectIds = computed(() => [...new Set(
  (props.snapshot?.projects || [])
    .filter(project => project?.available && project?.project_id)
    .map(project => String(project.project_id))
)])

const upcoming = computed(() => occurrences.value
  .filter(item => item?.start)
  .slice()
  .sort((a, b) => String(a.start).localeCompare(String(b.start)))
  .slice(0, 4))

const localeCode = computed(() => ({
  en: 'en-GB', es: 'es-ES', fr: 'fr-FR', de: 'de-DE', hi: 'hi-IN'
}[locale.value] || 'en-GB'))

async function load() {
  if (!projectIds.value.length) {
    occurrences.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const from = new Date()
    const to = new Date(from.getTime() + 14 * 24 * 60 * 60 * 1000)
    const response = await API.calendar.events.query({
      from: from.toISOString(),
      to: to.toISOString(),
      projects: projectIds.value
    })
    const now = Date.now()
    occurrences.value = (response?.occurrences || [])
      .filter(item => new Date(item.start).getTime() >= now - 60_000)
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || 'Calendar unavailable'
    occurrences.value = []
  } finally {
    loading.value = false
  }
}

function dayLabel(value) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const today = new Date()
  const tomorrow = new Date()
  tomorrow.setDate(today.getDate() + 1)
  if (date.toDateString() === today.toDateString()) return t('calendar.today')
  if (date.toDateString() === tomorrow.toDateString()) return t('calendar.tomorrow')
  return date.toLocaleDateString(localeCode.value, { day: 'numeric', month: 'short' })
}

function timeLabel(item) {
  if (item.all_day) return '—'
  const date = new Date(item.start)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString(localeCode.value, { hour: '2-digit', minute: '2-digit' })
}

function openCalendar() {
  emit('open')
}

watch(() => projectIds.value.join(','), load)
onMounted(load)
</script>

<template>
  <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4" aria-label="Calendar">
    <header class="flex items-center justify-between gap-2">
      <div>
        <h2 class="text-base font-bold tracking-tight">{{ t('calendar.title') }}</h2>
        <p class="mt-0.5 text-[10px] text-base-content/60">{{ t('calendar.upcoming') }}</p>
      </div>
      <button class="btn btn-xs !h-6 !text-[10px]" @click="openCalendar">
        {{ t('calendar.open') }} <i class="fa-solid fa-arrow-right text-[10px]"></i>
      </button>
    </header>

    <div v-if="loading" class="mt-3 flex items-center gap-2 py-2 text-[11px] text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span>
    </div>
    <div v-else-if="error" class="mt-2 rounded-lg bg-error/10 px-2 py-1.5 text-[11px] text-error-ink">
      {{ error }}
    </div>
    <div v-else-if="!upcoming.length" class="mt-2 rounded-xl border border-dashed border-base-300 px-3 py-4 text-center">
      <p class="text-[11px] text-base-content/60">{{ t('calendar.noEvents') }}</p>
    </div>
    <div v-else class="mt-2 divide-y divide-base-300">
      <button
        v-for="item in upcoming"
        :key="`${item.event_id || item.id}-${item.start}`"
        type="button"
        class="flex w-full items-center gap-2 py-2 text-left transition hover:text-accent-ink"
        @click="openCalendar"
      >
        <span class="grid h-7 w-9 shrink-0 place-items-center rounded-lg bg-accent/10 text-[10px] font-bold text-accent-ink">
          {{ dayLabel(item.start) }}
        </span>
        <span class="min-w-0 flex-1">
          <span class="block truncate text-xs font-bold">{{ item.title || 'Event' }}</span>
          <span class="mt-0.5 block truncate text-[10px] text-base-content/60">{{ timeLabel(item) }}</span>
        </span>
        <i class="fa-solid fa-chevron-right text-[10px] text-base-content/40"></i>
      </button>
    </div>
  </section>
</template>

<!-- Made with ❤️ by codx-junior -->
