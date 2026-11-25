<script setup>
import { computed, ref } from 'vue'
import { $storex } from '@/store'
import GlobalTasksView from '@/views/GlobalTasksView.vue'
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
// The Activity tab holds two faces of the same question: the company journal
// (what happened) and the Follow-up / GeneralView (what needs doing). The
// button switch keeps both one click apart instead of adding another nav item.
// Activity opens on the tasks (the Follow-up review): the owner comes here to
// see what needs doing, and the audit journal is one click away.
const view = ref('followup')

const views = computed(() => [
  { id: 'activity', label: t('activity.viewActivity'), icon: 'fa-solid fa-wave-square' },
  { id: 'followup', label: t('activity.viewFollowup'), icon: 'fa-solid fa-list-check' }
])

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
  return EVENT_META[event.type] || { icon: 'fa-solid fa-circle-info', tone: 'text-primary-ink' }
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

// Open the chat of a task. A task is a chat of its project, so the task chat is
// the destination; the company channels panel is only for company chats.
function openTask(task) {
  const id = task?.id || task?.chat_id
  if (!id) return
  $storex.ui.openApp({ appKey: 'chat', payload: { chat: { id } } })
}

function openEvent(event) {
  const data = event?.data || {}
  const taskId = data.task_id || data.chat_id || data.chatId || ''
  // A company journal entry about a task opens that task's chat, not the
  // channel of the team that happened to run it.
  if (taskId && !data.team_id) {
    openTask({ id: taskId })
    return
  }
  emit('open-chat', event)
}

function createTask() {
  emit('new-task')
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="text-xs font-bold uppercase tracking-[0.2em] text-info-ink">Audit &amp; review</p>
        <h1 class="mt-1 text-2xl font-black tracking-tight md:text-3xl">{{ t('activity.title') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          {{ t('activity.subtitle') }}
        </p>
      </div>
      <div class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <div class="flex w-full items-center gap-1 rounded-xl border border-base-content/10 bg-base-100 p-1 sm:w-auto">
          <button v-for="item in views" :key="item.id" type="button"
                  class="btn btn-xs gap-1.5 rounded-lg border normal-case"
                  :class="view === item.id ? 'border-primary bg-primary text-primary-content' : 'border-transparent bg-transparent text-base-content/55 hover:bg-base-content/5'"
                  @click="view = item.id">
            <i :class="item.icon" class="text-xs"></i> {{ item.label }}
          </button>
        </div>
        <button class="btn btn-xs" @click="createTask">
          <i class="fa-solid fa-plus text-[11px]"></i> {{ t('activity.newTask') }}
        </button>
        <button class="btn btn-sm w-full gap-1.5 rounded-xl border-base-content/15 sm:w-auto" @click="ask('Write a weekly review of the company: what was delivered, what is blocked, what we learned and what to do next.')">
          <i class="fa-solid fa-wand-magic-sparkles"></i> {{ t('activity.weeklyReview') }}
        </button>
      </div>
    </header>

    <!-- Follow-up reuses the workspace Tasks view: the same detailed rows
         (last message, status and unread badge), the same task side panel and
         the same filters. The only company-specific part is the rolling
         date filter, which opens on the last 7 days. -->
    <div v-if="view === 'followup'" class="h-[70dvh] min-h-[420px] overflow-hidden rounded-2xl border border-base-content/10 bg-base-100 sm:h-[75vh] sm:min-h-[520px]">
      <GlobalTasksView show-date-filter />
    </div>

    <section v-if="view === 'activity' && completed.length" class="rounded-2xl border border-base-content/10 bg-base-100 p-3 sm:p-4 md:p-5">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="flex items-center gap-2 text-sm font-black">
            <span class="grid h-7 w-7 place-items-center rounded-lg bg-success/15 text-success-ink"><i class="fa-solid fa-circle-check text-xs"></i></span>
            {{ t('activity.delivered') }}
          </h2>
          <p class="mt-1 text-xs text-base-content/45">What the team shipped and the result the company recorded.</p>
        </div>
        <span class="badge badge-sm badge-success badge-outline">{{ completed.length }}</span>
      </div>
      <div class="mt-3 grid gap-2 sm:grid-cols-2">
        <button v-for="mission in completed" :key="mission.id"
                class="rounded-xl border border-base-content/10 p-3 text-left transition hover:border-success/40 hover:bg-success/5"
                @click="openMission(mission)">
          <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span class="badge badge-xs badge-success">{{ mission.status }}</span>
            <span class="min-w-0 flex-1 truncate text-xs font-bold">{{ mission.title }}</span>
            <span class="shrink-0 text-[11px] text-base-content/35">{{ mission.completed_at ? formatDate(mission.completed_at) : '' }}</span>
          </div>
          <p class="mt-1 line-clamp-2 text-xs leading-snug text-base-content/50">{{ mission.result || mission.acceptance || 'No result recorded.' }}</p>
        </button>
      </div>
    </section>

    <section v-if="view === 'activity'" class="rounded-2xl border border-base-content/10 bg-base-100 p-3 sm:p-4 md:p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex w-full flex-wrap gap-1.5 sm:w-auto">
          <button v-for="item in filters" :key="item.id"
                  class="btn btn-xs rounded-full border normal-case"
                  :class="filter === item.id ? 'border-primary bg-primary text-primary-content' : 'border-base-content/15 bg-base-100 text-base-content/55'"
                  @click="filter = item.id">{{ item.label }}</button>
        </div>
        <label class="input input-sm input-bordered flex w-full items-center gap-2 rounded-xl sm:w-64">
          <i class="fa-solid fa-magnifying-glass text-xs text-base-content/35"></i>
          <input v-model="query" type="text" class="grow bg-transparent text-xs" :placeholder="t('activity.search')" />
        </label>
      </div>

      <div v-if="!grouped.length" class="mt-6 rounded-xl border border-dashed border-base-content/15 p-8 text-center">
        <i class="fa-solid fa-wave-square text-2xl text-base-content/20"></i>
        <p class="mt-2 text-sm font-bold">{{ t('activity.empty') }}</p>
        <p class="text-xs text-base-content/45">Activity appears as the team works: goals, decisions, deliveries and changes.</p>
      </div>

      <div v-else class="mt-4 space-y-5">
        <div v-for="group in grouped" :key="group.day">
          <div class="mb-2 flex items-center gap-2">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">{{ dayLabel(group.day) }}</h3>
            <span class="text-[11px] text-base-content/25">{{ group.events.length }} events</span>
            <div class="h-px flex-1 bg-base-content/5"></div>
          </div>
          <div class="space-y-1.5">
            <button
              v-for="event in group.events"
              :key="event.id"
              type="button"
              class="flex w-full items-start gap-2 rounded-xl px-2 py-2.5 text-left transition hover:bg-base-200/40 sm:gap-2.5 sm:p-2"
              :title="t('activity.openChat')"
              @click="openEvent(event)"
            >
              <span class="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-base-200/70 sm:h-7 sm:w-7" :class="meta(event).tone">
                <i :class="meta(event).icon" class="text-xs"></i>
              </span>
              <div class="min-w-0 flex-1">
                <p class="break-words text-[13px] leading-relaxed text-base-content/70 sm:text-xs">{{ event.message }}</p>
                <div class="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-base-content/35">
                  <span class="whitespace-nowrap">{{ relativeTime(event.at) }}</span>
                  <span>·</span>
                  <span class="max-w-[10rem] truncate sm:max-w-none">{{ event.actor || 'system' }}</span>
                  <span class="hidden sm:inline">·</span>
                  <span class="hidden truncate font-mono text-[10px] sm:inline">{{ event.type }}</span>
                  <span class="max-w-full truncate rounded bg-base-200/70 px-1.5 py-0.5 font-mono text-[9px] text-base-content/45 sm:hidden">{{ event.type }}</span>
                </div>
              </div>
              <span class="hidden shrink-0 text-[11px] text-base-content/25 sm:block">{{ formatDateTime(event.at) }}</span>
              <i class="fa-regular fa-comments mt-1.5 shrink-0 text-[11px] text-base-content/25 sm:mt-1"></i>
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
