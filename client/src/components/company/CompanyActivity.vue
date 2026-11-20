<script setup>
import { computed, ref } from 'vue'
import { EVENT_META, formatDate, formatDateTime, relativeTime } from './companyMeta'
import { useCompanyI18n } from './i18n'

/**
 * Activity — the company's memory.
 *
 * A human-readable audit trail grouped by day, plus the completed work and its
 * delivered result. This is where the owner reviews what actually happened.
 */
const props = defineProps({
  snapshot: { type: Object, default: null }
})

const emit = defineEmits(['assistant', 'open-work', 'open-chat', 'new-task'])

const { t } = useCompanyI18n()

const filter = ref('all')
const query = ref('')

const journal = computed(() => (props.snapshot?.journal || []).slice().reverse())
const missions = computed(() => props.snapshot?.missions || [])
const completed = computed(() => missions.value.filter(mission => ['done', 'cancelled'].includes(mission.status)).slice(0, 6))

const filters = computed(() => [
  { id: 'all', label: t('activity.all') },
  { id: 'work', label: t('activity.work') },
  { id: 'team', label: t('activity.team') },
  { id: 'company', label: t('activity.company') },
  { id: 'people', label: t('activity.people') }
])

function groupOf(event) {
  const type = String(event.type || '')
  if (type.startsWith('mission') || type.startsWith('autopilot')) return 'work'
  if (type.startsWith('team') || type.startsWith('org_unit')) return 'team'
  if (type.startsWith('person') || type.startsWith('company.members')) return 'people'
  return 'company'
}

const filtered = computed(() => journal.value.filter(event => {
  if (filter.value !== 'all' && groupOf(event) !== filter.value) return false
  const needle = query.value.trim().toLowerCase()
  if (!needle) return true
  return `${event.type} ${event.message} ${event.actor}`.toLowerCase().includes(needle)
}))

const grouped = computed(() => {
  const groups = new Map()
  for (const event of filtered.value) {
    const day = String(event.at || '').slice(0, 10) || 'Unknown'
    if (!groups.has(day)) groups.set(day, [])
    groups.get(day).push(event)
  }
  return [...groups.entries()].map(([day, events]) => ({ day, events }))
})

function meta(event) {
  return EVENT_META[event.type] || { icon: 'fa-solid fa-circle-info', tone: 'text-primary' }
}

function dayLabel(day) {
  if (!day || day === 'Unknown') return 'Unknown date'
  const date = new Date(`${day}T00:00:00`)
  if (Number.isNaN(date.getTime())) return day
  const today = new Date()
  const yesterday = new Date()
  yesterday.setDate(today.getDate() - 1)
  if (date.toDateString() === today.toDateString()) return 'Today'
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
}

function ask(prompt) {
  emit('assistant', prompt)
}

function openMission(mission) {
  emit('open-work', mission.id)
}

function openEvent(event) {
  emit('open-chat', event)
}

function createTask() {
  emit('new-task')
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-[10px] font-bold uppercase tracking-[0.2em] text-info">Audit &amp; review</p>
        <h1 class="mt-1 text-2xl font-black tracking-tight md:text-3xl">{{ t('activity.title') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          {{ t('activity.subtitle') }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="cp-chip" @click="createTask">
          <i class="fa-solid fa-plus text-[9px]"></i> {{ t('activity.newTask') }}
        </button>
        <button class="btn btn-sm gap-1.5 rounded-xl border-base-content/15" @click="ask('Write a weekly review of the company: what was delivered, what is blocked, what we learned and what to do next.')">
          <i class="fa-solid fa-wand-magic-sparkles"></i> {{ t('activity.weeklyReview') }}
        </button>
      </div>
    </header>

    <section v-if="completed.length" class="rounded-2xl border border-base-content/10 bg-base-100 p-4 md:p-5">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="flex items-center gap-2 text-sm font-black">
            <span class="grid h-7 w-7 place-items-center rounded-lg bg-success/15 text-success"><i class="fa-solid fa-circle-check text-[11px]"></i></span>
            {{ t('activity.delivered') }}
          </h2>
          <p class="mt-1 text-[11px] text-base-content/45">What the team shipped and the result the company recorded.</p>
        </div>
        <span class="badge badge-sm badge-success badge-outline">{{ completed.length }}</span>
      </div>
      <div class="mt-3 grid gap-2 md:grid-cols-2">
        <button v-for="mission in completed" :key="mission.id"
                class="rounded-xl border border-base-content/10 p-3 text-left transition hover:border-success/40 hover:bg-success/5"
                @click="openMission(mission)">
          <div class="flex items-center gap-2">
            <span class="badge badge-xs badge-success">{{ mission.status }}</span>
            <span class="truncate text-[11px] font-bold">{{ mission.title }}</span>
            <span class="ml-auto text-[9px] text-base-content/35">{{ mission.completed_at ? formatDate(mission.completed_at) : '' }}</span>
          </div>
          <p class="mt-1 line-clamp-2 text-[10px] leading-snug text-base-content/50">{{ mission.result || mission.acceptance || 'No result recorded.' }}</p>
        </button>
      </div>
    </section>

    <section class="rounded-2xl border border-base-content/10 bg-base-100 p-4 md:p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex flex-wrap gap-1.5">
          <button v-for="item in filters" :key="item.id"
                  class="btn btn-xs rounded-full border normal-case"
                  :class="filter === item.id ? 'border-primary bg-primary text-primary-content' : 'border-base-content/15 bg-base-100 text-base-content/55'"
                  @click="filter = item.id">{{ item.label }}</button>
        </div>
        <label class="input input-sm input-bordered flex items-center gap-2 rounded-xl">
          <i class="fa-solid fa-magnifying-glass text-[10px] text-base-content/35"></i>
          <input v-model="query" type="text" class="grow bg-transparent text-xs" :placeholder="t('activity.search')" />
        </label>
      </div>

      <div v-if="!grouped.length" class="mt-6 rounded-xl border border-dashed border-base-content/15 p-8 text-center">
        <i class="fa-solid fa-wave-square text-2xl text-base-content/20"></i>
        <p class="mt-2 text-sm font-bold">{{ t('activity.empty') }}</p>
        <p class="text-[11px] text-base-content/45">Activity appears as the team works: goals, decisions, deliveries and changes.</p>
      </div>

      <div v-else class="mt-4 space-y-5">
        <div v-for="group in grouped" :key="group.day">
          <div class="mb-2 flex items-center gap-2">
            <h3 class="text-[10px] font-bold uppercase tracking-[0.16em] text-base-content/40">{{ dayLabel(group.day) }}</h3>
            <span class="text-[9px] text-base-content/25">{{ group.events.length }} events</span>
            <div class="h-px flex-1 bg-base-content/5"></div>
          </div>
          <div class="space-y-1.5">
            <button
              v-for="event in group.events"
              :key="event.id"
              type="button"
              class="flex w-full items-start gap-2.5 rounded-xl p-2 text-left transition hover:bg-base-200/40"
              :title="t('activity.openChat')"
              @click="openEvent(event)"
            >
              <span class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-base-200/70" :class="meta(event).tone">
                <i :class="meta(event).icon" class="text-[10px]"></i>
              </span>
              <div class="min-w-0 flex-1">
                <p class="text-[11px] leading-relaxed text-base-content/70">{{ event.message }}</p>
                <p class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[9px] text-base-content/35">
                  <span>{{ relativeTime(event.at) }}</span>
                  <span>·</span>
                  <span>{{ event.actor || 'system' }}</span>
                  <span>·</span>
                  <span class="font-mono text-[8px]">{{ event.type }}</span>
                </p>
              </div>
              <span class="hidden shrink-0 text-[9px] text-base-content/25 sm:block">{{ formatDateTime(event.at) }}</span>
              <i class="fa-regular fa-comments mt-1 shrink-0 text-[9px] text-base-content/25"></i>
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
