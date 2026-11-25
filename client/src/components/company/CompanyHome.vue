<script setup>
import { computed, ref } from 'vue'
import CompanyCalendar from './CompanyCalendar.vue'
import { useCompanyI18n } from './i18n'
import {
  attentionLabel,
  companyHealth,
  formatDate,
  formatMoney,
  memberAvatar,
  percent,
  relativeTime,
  statusMeta
} from './companyMeta'

/**
 * Overview — the owner's dashboard.
 *
 * One screen that answers: how much money is moving, what is the team doing,
 * what needs a decision and where is the budget going. Every tile is a link to
 * the workspace that lets the owner act on it; the data is the company snapshot
 * (nothing is invented when the company is empty).
 *
 * Presentation only: the emits mirror the previous home, plus `open-activity`
 * for the activity rail.
 */
const props = defineProps({
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false }
})

const { t } = useCompanyI18n()

const emit = defineEmits([
  'assistant', 'open-work', 'open-tickets', 'open-team', 'open-business', 'open-activity',
  'open-settings', 'open-calendar', 'new-goal', 'open-chat'
])

const company = computed(() => props.snapshot?.company || {})
// Identity of the company that is open: the logo is an image source (http(s)
// URL or inline data URL); older documents keep a FontAwesome class there.
const logo = computed(() => String(company.value.logo || '').trim())
const logoIsImage = computed(() => /^(data:image\/|https?:\/\/|\/)/i.test(logo.value))
const companyInitials = computed(() => initials(company.value.name || 'Company'))
const counts = computed(() => props.snapshot?.counts || {})
const teams = computed(() => props.snapshot?.teams || [])
const missions = computed(() => props.snapshot?.missions || [])
const members = computed(() => props.snapshot?.members || [])
const attention = computed(() => props.snapshot?.attention || [])
const journal = computed(() => props.snapshot?.journal || [])
const budget = computed(() => props.snapshot?.budget || {})
const business = computed(() => company.value.business || {})
const currency = computed(() => business.value.currency || 'EUR')

const hoverBar = ref(-1)


// ── Money ──────────────────────────────────────────────────────────────────

const revenue = computed(() => Number(business.value.revenue_actual || 0))
const costs = computed(() => Number(business.value.costs_actual || 0))
const grossProfit = computed(() => revenue.value - costs.value)
const revenueTarget = computed(() => Number(business.value.revenue_target || 0))
const revenueProgress = computed(() => Number(business.value.revenue_progress ?? percent(revenue.value, revenueTarget.value)))
const revenueGap = computed(() => Math.max(0, revenueTarget.value - revenue.value))
const pipelineValue = computed(() => (business.value.pipeline || [])
  .filter(deal => !['won', 'lost'].includes(deal.stage))
  .reduce((sum, deal) => sum + Number(deal.value || 0), 0))
const costRatio = computed(() => revenue.value > 0 ? Math.round((costs.value / revenue.value) * 100) : 0)
const margin = computed(() => revenue.value > 0 ? Math.round((grossProfit.value / revenue.value) * 100) : 0)
const peopleCount = computed(() => Number(counts.value.human ?? counts.value.humans ?? 0) + Number(counts.value.ai || 0))
const openDealCount = computed(() => (business.value.pipeline || []).filter(deal => !['won', 'lost'].includes(deal.stage)).length)

const kpis = computed(() => [
  {
    id: 'target',
    label: t('home.revenueTarget'),
    value: formatMoney(revenueTarget.value, currency.value),
    hint: t('home.attained', { value: Math.min(999, revenueProgress.value) }),
    icon: 'fa-solid fa-bullseye',
    color: 'var(--color-accent)',
    tone: 'var(--color-success-ink)'
  },
  {
    id: 'costs',
    label: t('home.totalExpenses'),
    value: formatMoney(costs.value, currency.value),
    hint: t('home.ofRevenue', { value: costRatio.value }),
    icon: 'fa-solid fa-arrow-trend-down',
    color: 'var(--color-warning)',
    tone: 'var(--color-warning-ink)'
  },
  {
    id: 'saved',
    label: t('home.grossProfit'),
    value: formatMoney(grossProfit.value, currency.value),
    hint: t('home.grossMargin', { value: margin.value }),
    icon: grossProfit.value >= 0 ? 'fa-solid fa-arrow-trend-up' : 'fa-solid fa-arrow-trend-down',
    color: 'var(--color-success)',
    tone: grossProfit.value >= 0 ? 'var(--color-success-ink)' : 'var(--color-error-ink)'
  }
])

// ── Work ───────────────────────────────────────────────────────────────────

const openMissions = computed(() => missions.value.filter(mission => ['ready', 'running', 'blocked', 'review', 'paused'].includes(mission.status)))
const reviewMissions = computed(() => missions.value.filter(mission => mission.status === 'review'))
const blockedMissions = computed(() => missions.value.filter(mission => mission.status === 'blocked'))
const activeActors = computed(() => (props.actors || []).filter(actor => actor?.config?.enabled))
const aiProfiles = computed(() => {
  const names = new Set()
  teams.value.forEach(team => (team.members || []).forEach(member => {
    if (member.kind === 'profile') names.add(member.name)
  }))
  return [...names]
})

const health = computed(() => companyHealth({
  attention: attention.value.length,
  blocked: blockedMissions.value.length,
  review: reviewMissions.value.length,
  budgetOver: Boolean(budget.value.over_daily_budget),
  teamsWithoutManager: teams.value.filter(team => !team.lead_name).length
}))
const healthLabel = computed(() => health.value >= 75 ? 'Healthy' : health.value >= 50 ? 'Watch' : 'At risk')
const healthTone = computed(() => health.value >= 75 ? 'var(--color-success-ink)' : health.value >= 50 ? 'var(--color-warning-ink)' : 'var(--color-error-ink)')

const goalRows = computed(() => {
  const weight = { blocked: 0, review: 1, running: 2, ready: 3, paused: 4 }
  return [...openMissions.value]
    .sort((a, b) => (weight[a.status] ?? 9) - (weight[b.status] ?? 9))
    .slice(0, 4)
    .map(goal => ({
      ...goal,
      progress: Math.max(0, Math.min(100, Number(goal.progress || 0))),
      due: goal.due_date ? formatDate(goal.due_date) : 'No deadline',
      team: teams.value.find(team => team.id === goal.team_id)?.name || 'Unassigned',
      meta: statusMeta(goal.status)
    }))
})

const roster = computed(() => {
  const list = []
  const seen = new Set()
  const push = (name, avatar, kind) => {
    const key = String(name || '')
    if (!key || seen.has(key)) return
    seen.add(key)
    list.push({ name: key, avatar, kind })
  }
  members.value.forEach(member => push(member.name || member.username, member.avatar || memberAvatar(member), 'human'))
  activeActors.value.forEach(actor => push(actor.actor, actor.avatar || memberAvatar({ name: actor.actor }), 'ai'))
  if (!list.length) aiProfiles.value.forEach(name => push(name, memberAvatar({ name }), 'ai'))
  return list.slice(0, 5)
})

const quickActions = [
  { id: 'brief', labelKey: 'home.brief', icon: 'fa-solid fa-wand-magic-sparkles', run: () => ask("Give me today's executive brief") },
  { id: 'tickets', labelKey: 'nav.tickets', icon: 'fa-solid fa-ticket', run: () => emit('open-tickets') },
  { id: 'goal', labelKey: 'shell.newGoal', icon: 'fa-solid fa-flag', run: () => emit('new-goal') },
  { id: 'teams', labelKey: 'nav.team', icon: 'fa-solid fa-people-group', run: () => emit('open-team') },
  { id: 'chat', labelKey: 'nav.channels', icon: 'fa-regular fa-comments', run: () => emit('open-chat') },
  { id: 'activity', labelKey: 'nav.activity', icon: 'fa-solid fa-wave-square', run: () => emit('open-activity') }
]
const visibleQuickActions = computed(() => quickActions.filter(action => action.id !== 'goal' || props.canManage))

// ── Weekly activity ────────────────────────────────────────────────────────
// The reference dashboard charts a week. The company snapshot has no daily
// financial rollup, but its journal is timestamped, so the chart uses the real
// operating activity of the last seven days (never random demo data).

const legend = [
  { id: 'done', labelKey: 'home.delivered', color: 'var(--color-accent)' },
  { id: 'active', labelKey: 'home.inMotion', color: 'var(--color-warning)' },
  { id: 'attention', labelKey: 'home.attention', color: 'var(--color-error)' }
]

const weekBars = computed(() => {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const days = []
  for (let index = 6; index >= 0; index -= 1) {
    const date = new Date(today)
    date.setDate(today.getDate() - index)
    days.push({
      date,
      label: date.toLocaleDateString(undefined, { weekday: 'short' }),
      dateLabel: date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }),
      count: 0,
      done: 0,
      active: 0,
      attention: 0
    })
  }
  const byDay = new Map(days.map(day => [day.date.getTime(), day]))
  journal.value.forEach(event => {
    const at = new Date(event.at)
    if (Number.isNaN(at.getTime())) return
    at.setHours(0, 0, 0, 0)
    const day = byDay.get(at.getTime())
    if (!day) return
    day.count += 1
    const type = String(event.type || '')
    if (type.includes('accepted') || type.includes('closed') || type.includes('done')) day.done += 1
    else if (type.includes('failed') || type.includes('blocked') || type.includes('rejected') || type.includes('error')) day.attention += 1
    else day.active += 1
  })
  const peak = Math.max(4, ...days.map(day => day.count))
  const axisMax = Math.ceil(peak / 4) * 4
  return days.map((day, index) => ({
    ...day,
    index,
    isToday: index === 6,
    height: day.count ? Math.max(7, Math.round((day.count / axisMax) * 100)) : 0,
    segments: [
      { id: 'done', value: day.done, color: 'var(--color-accent)' },
      { id: 'active', value: day.active, color: 'var(--color-warning)' },
      { id: 'attention', value: day.attention, color: 'var(--color-error)' }
    ].filter(part => part.value > 0),
    axisMax
  }))
})

const weekTotal = computed(() => weekBars.value.reduce((sum, day) => sum + day.count, 0))
const axisTicks = computed(() => {
  const max = weekBars.value[0]?.axisMax || 4
  return [max, Math.round(max * 0.75), Math.round(max * 0.5), Math.round(max * 0.25), 0]
})
const defaultBarIndex = computed(() => {
  for (let index = weekBars.value.length - 1; index >= 0; index -= 1) {
    if (weekBars.value[index].count) return index
  }
  return Math.max(0, weekBars.value.findIndex(day => day.isToday))
})
const activeBarIndex = computed(() => hoverBar.value >= 0 ? hoverBar.value : defaultBarIndex.value)
const activeBar = computed(() => weekBars.value[activeBarIndex.value] || null)
const tooltipLeft = computed(() => {
  const raw = ((activeBarIndex.value + 0.5) / 7) * 100
  return Math.min(84, Math.max(16, raw))
})

function barActive(index) {
  return index === activeBarIndex.value
}

// ── Budget ─────────────────────────────────────────────────────────────────

const dailyLimit = computed(() => Number(budget.value.daily_limit_cxjcoins || 0))
const dailySpent = computed(() => Number(budget.value.spent_today_cxjcoins || 0))
const projectBudget = computed(() => Number(budget.value.total_project_budget_cxjcoins || 0))
const totalSpent = computed(() => Number(budget.value.spent_total_cxjcoins || 0))
const hasDailyLimit = computed(() => dailyLimit.value > 0)
const budgetLimit = computed(() => hasDailyLimit.value ? dailyLimit.value : projectBudget.value)
const budgetSpent = computed(() => hasDailyLimit.value ? dailySpent.value : totalSpent.value)
const budgetProgressRaw = computed(() => budgetLimit.value > 0 ? Math.round((budgetSpent.value / budgetLimit.value) * 100) : 0)
const budgetProgress = computed(() => Math.min(100, Math.max(0, budgetProgressRaw.value)))
const budgetOver = computed(() => budgetProgressRaw.value >= 100)
const limitTitle = computed(() => hasDailyLimit.value ? 'Daily spending limit' : projectBudget.value > 0 ? 'Project spending limit' : 'AI spending limit')
const limitSubtitle = computed(() => hasDailyLimit.value
  ? 'Autopilot checks this budget on every cycle.'
  : projectBudget.value > 0
    ? 'Shared budget across the linked projects.'
    : t('home.noLimitConfigured'))
const remainingBudget = computed(() => Math.max(0, budgetLimit.value - budgetSpent.value))
const resetLabel = computed(() => {
  const now = new Date()
  const next = new Date(now)
  next.setHours(24, 0, 0, 0)
  return `${Math.max(1, Math.ceil((next.getTime() - now.getTime()) / 3600000))}h`
})

// ── Cost split ─────────────────────────────────────────────────────────────

const costPalette = [
  'var(--color-accent)',
  'var(--color-success)',
  'var(--color-warning)',
  'var(--color-error)',
  'var(--color-base-content)'
]

const costRows = computed(() => {
  const projectRows = (budget.value.projects || [])
    .map(row => ({ id: `project:${row.project_id}`, label: row.name || row.project_id, value: Number(row.spent_cxjcoins || 0) }))
    .filter(row => row.value > 0)
  const teamRows = (budget.value.teams || [])
    .map(row => ({ id: `team:${row.team_id}`, label: row.name, value: Number(row.spent_cxjcoins || 0) }))
    .filter(row => row.value > 0)
  const missionRows = missions.value
    .map(mission => ({ id: `mission:${mission.id}`, label: mission.title, value: Number(mission.spent_cxjcoins || 0) }))
    .filter(row => row.value > 0)
  let rows = projectRows.length ? projectRows : teamRows.length ? teamRows : missionRows
  rows = [...rows].sort((a, b) => b.value - a.value).slice(0, 4)
  const subtotal = rows.reduce((sum, row) => sum + row.value, 0)
  const total = Math.max(totalSpent.value, subtotal)
  if (total > subtotal && subtotal > 0) {
    rows.push({ id: 'other', label: 'Everything else', value: total - subtotal })
  }
  return rows.map((row, index) => ({
    ...row,
    key: row.id || `cost:${index}`,
    share: total > 0 ? Math.round((row.value / total) * 100) : 0,
    color: costPalette[index % costPalette.length]
  }))
})
const costTotal = computed(() => totalSpent.value || costRows.value.reduce((sum, row) => sum + row.value, 0))

// ── Activities & tips ──────────────────────────────────────────────────────

const activity = computed(() => [...journal.value].reverse().slice(0, 6))

const tips = computed(() => {
  const list = []
  if (attention.value.length) {
    list.push({
      id: 'attention',
      target: 'work',
      title: `${attention.value.length} decision${attention.value.length === 1 ? '' : 's'} need you`,
      body: attentionLabel(attention.value[0]?.reason) || 'Unblock the team',
      icon: 'fa-solid fa-bell',
      tile: 'bg-warning/10 text-warning-ink'
    })
  }
  if (reviewMissions.value.length) {
    list.push({
      id: 'review',
      target: 'work',
      title: `${reviewMissions.value.length} goal${reviewMissions.value.length === 1 ? '' : 's'} ready to review`,
      body: 'Accept the outcome or send it back with notes.',
      icon: 'fa-solid fa-magnifying-glass',
      tile: 'bg-accent/10 text-accent-ink'
    })
  }
  if (budgetOver.value) {
    list.push({
      id: 'budget',
      target: 'assistant',
      prompt: 'The daily AI budget is exhausted. What should we do?',
      title: 'AI budget exhausted',
      body: 'Ask for a plan before the next autopilot cycle.',
      icon: 'fa-solid fa-coins',
      tile: 'bg-error/10 text-error-ink'
    })
  } else if (revenueGap.value > 0 && revenueTarget.value > 0) {
    list.push({
      id: 'revenue',
      target: 'business',
      title: `${formatMoney(revenueGap.value, currency.value)} to the revenue target`,
      body: 'Review the pipeline and the next best deal.',
      icon: 'fa-solid fa-arrow-trend-up',
      tile: 'bg-success/10 text-success-ink'
    })
  }
  if (!list.length) {
    list.push({
      id: 'plan',
      target: 'assistant',
      prompt: 'What are the three highest-leverage things to do this week?',
      title: "Ask for this week's plan",
      body: 'Turn the current numbers into three concrete moves.',
      icon: 'fa-solid fa-wand-magic-sparkles',
      tile: 'bg-accent/10 text-accent-ink'
    })
  }
  return list.slice(0, 3)
})

const attentionFocus = computed(() => attention.value[0] || null)

function ask(prompt) {
  emit('assistant', prompt)
}

function initials(name) {
  return String(name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0].toUpperCase())
    .join('')
}

function formatCxj(value) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Number(value || 0))
}

function hideBrokenImage(event) {
  if (event?.target) event.target.style.display = 'none'
}

function openMission(missionOrId) {
  const id = typeof missionOrId === 'string' ? missionOrId : missionOrId?.id || missionOrId?.mission_id
  if (id) emit('open-work', id)
  else emit('open-work')
}

function openAttention(item) {
  if (item?.mission_id) {
    openMission(item.mission_id)
    return
  }
  ask(`The project ${item?.title || ''} is unavailable in this installation. Help me fix it.`)
}

function runTip(tip) {
  if (tip.target === 'work') emit('open-work')
  else if (tip.target === 'business') emit('open-business')
  else ask(tip.prompt || 'How can I improve the company this week?')
}

function askGoal(goal) {
  ask(`What is the status of "${goal.title}" and what should we do next?`)
}

function eventIcon(event) {
  const type = String(event.type || '')
  if (type.startsWith('mission')) return 'fa-solid fa-flag'
  if (type.startsWith('team')) return 'fa-solid fa-people-group'
  if (type.startsWith('company')) return 'fa-solid fa-building'
  if (type.includes('project')) return 'fa-solid fa-cube'
  if (type.includes('autopilot') || type.includes('autonomy')) return 'fa-solid fa-robot'
  return 'fa-solid fa-circle-info'
}

function eventLook(event) {
  const type = String(event.type || '')
  if (type.includes('failed') || type.includes('deleted') || type.includes('rejected')) {
    return { label: 'Review', color: 'var(--color-error-ink)', tile: 'bg-error/10 text-error-ink' }
  }
  if (type.includes('accepted') || type.includes('done') || type.includes('closed')) {
    return { label: 'Done', color: 'var(--color-success-ink)', tile: 'bg-accent/10 text-accent-ink' }
  }
  if (type.includes('created') || type.includes('dispatched')) {
    return { label: 'New', color: 'var(--color-accent-ink)', tile: 'bg-success/10 text-success-ink' }
  }
  if (type.includes('updated') || type.includes('transitioned') || type.includes('synced')) {
    return { label: 'Update', color: 'var(--color-info-ink)', tile: '' }
  }
  return { label: 'Info', color: 'var(--color-base-content)', tile: 'bg-warning/10 text-warning-ink' }
}

function openEvent(event) {
  if (event?.mission_id) {
    openMission(event.mission_id)
    return
  }
  ask(`Explain this company activity: ${event?.message || event?.type}`)
}
</script>

<template>
  <div>
    <!-- Company identity: the owner always sees which company is open. -->
    <header class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 mb-4 flex flex-wrap items-center gap-3" aria-label="Company">
      <span class="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-[14px] bg-accent text-lg font-black text-accent-content shadow-sm">
        <img
          v-if="logoIsImage"
          :src="logo"
          :alt="company.name || 'Company logo'"
          class="h-full w-full object-cover"
          @error="hideBrokenImage"
        />
        <i v-else-if="logo" :class="logo"></i>
        <span v-else>{{ companyInitials }}</span>
      </span>
      <div class="min-w-0 flex-1">
        <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">Company</p>
        <h1 class="mt-0.5 truncate text-xl font-extrabold tracking-tight md:text-2xl">{{ company.name || 'Your company' }}</h1>
        <p class="mt-0.5 line-clamp-2 text-xs leading-relaxed text-base-content/60">{{ company.mission || company.description || 'Add the company purpose in Settings.' }}</p>
      </div>
      <div class="flex shrink-0 items-center gap-1.5">
        <button class="btn btn-xs" title="Edit this company" @click="emit('open-settings')">
          <i class="fa-solid fa-pen text-[11px]"></i> Edit
        </button>
      </div>
    </header>

    <div class="grid gap-4 min-[1180px]:grid-cols-12 min-[1180px]:gap-5">
      <!-- ── Main column ─────────────────────────────────────────────────── -->
      <div class="space-y-4 min-[1180px]:col-span-8 min-[1180px]:space-y-5">
        <div class="grid gap-4 md:grid-cols-12">
          <!-- Delivery activity -->
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col md:col-span-8" aria-label="Company activity">
            <header class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">{{ t('home.companyActivity') }}</p>
                <p class="text-3xl font-extrabold leading-none tracking-tight tabular-nums md:text-4xl mt-2">{{ weekTotal }}<span class="ml-1.5 text-base font-bold text-base-content/60">{{ t('home.events') }}</span></p>
                <p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                  <span class="text-base-content/60">{{ t('home.last7') }}</span>
                  <span class="text-base-content/40">·</span>
                  <span class="inline-flex items-center gap-1 font-semibold" :style="{ color: 'var(--color-success-ink)' }">
                    <i class="fa-solid fa-wave-square text-[10px]"></i>
                    {{ t('home.deliveryDecisions') }}
                  </span>
                </p>
              </div>
              <div class="flex items-center gap-1.5">
                <button class="btn btn-ghost btn-sm btn-square !h-7 !w-7 !rounded-md" title="Open activity" aria-label="Open activity" @click="emit('open-activity')">
                  <i class="fa-solid fa-list-ul text-xs"></i>
                </button>
                <button class="btn btn-ghost btn-sm btn-square !h-7 !w-7 !rounded-md bg-base-200" title="Weekly chart" aria-label="Weekly chart" @click="emit('open-activity')">
                  <i class="fa-solid fa-chart-column text-xs"></i>
                </button>
              </div>
            </header>

            <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-3">
                <span v-for="item in legend" :key="item.id" class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-base-content/60">
                  <span class="h-1.5 w-1.5 rounded-full" :style="{ background: item.color }"></span>
                  {{ t(item.labelKey) }}
                </span>
              </div>
              <span class="text-[11px] text-base-content/40">{{ activity.length }} {{ t('home.recentEntries') }}</span>
            </div>

            <div class="relative mt-auto pt-4">
              <div v-if="!weekTotal" class="pointer-events-none absolute inset-x-0 top-8 z-[2] flex justify-center">
                <span class="btn btn-xs !h-6 !border-transparent !bg-base-200 !text-[10px]">No activity in the last 7 days</span>
              </div>
              <div class="grid h-[194px] grid-cols-[26px_minmax(0,1fr)] gap-2.5">
                <div class="flex flex-col justify-between pb-6 text-xs tabular-nums text-base-content/40">
                  <span v-for="tick in axisTicks" :key="tick">{{ tick }}</span>
                </div>
                <div class="relative min-w-0" @mouseleave="hoverBar = -1">
                  <div class="pointer-events-none absolute inset-x-0 bottom-6 top-0 flex flex-col justify-between">
                    <span v-for="tick in axisTicks" :key="`line-${tick}`" class="block w-full border-t border-dashed border-base-300"></span>
                  </div>
                  <div class="absolute inset-0 flex items-stretch justify-between gap-1">
                    <div
                      v-for="bar in weekBars"
                      :key="bar.date.getTime()"
                      class="relative flex h-full min-w-0 flex-1 flex-col items-center justify-end pb-6"

                      @mouseenter="hoverBar = bar.index"
                      @focusin="hoverBar = bar.index"
                      tabindex="0"
                      :aria-label="`${bar.dateLabel}: ${bar.count} activity events`"
                    >
                      <div class="w-full max-w-[44px] min-h-[2px] overflow-hidden rounded-[7px] bg-base-300 transition-all" :class="barActive(bar.index) ? 'bg-base-300/80' : ''" :style="{ height: `${bar.height}%` }">
                        <template v-if="barActive(bar.index)">
                          <i
                            v-for="part in bar.segments"
                            :key="part.id"
                            :style="{ flex: `${part.value} 1 0`, background: part.color }"
                          ></i>
                        </template>
                      </div>
                      <span class="absolute bottom-0 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs font-semibold" :class="barActive(bar.index) ? 'text-base-content' : 'text-base-content/40'">{{ bar.label }}</span>
                    </div>
                  </div>

                  <div v-if="activeBar && weekTotal" class="absolute top-1 z-[5] w-32 -translate-x-1/2 rounded-field bg-neutral p-2.5 text-neutral-content shadow-xl max-[520px]:hidden [@media(hover:none)]:hidden" :style="{ left: `${tooltipLeft}%` }">
                    <p class="text-xs font-semibold uppercase tracking-wide opacity-60">{{ activeBar.dateLabel }}</p>
                    <ul class="mt-1 space-y-0.5">
                      <li class="flex items-center gap-1 text-xs"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--color-accent)"></span> Delivered <b>{{ activeBar.done }}</b></li>
                      <li class="flex items-center gap-1 text-xs"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--color-warning)"></span> In motion <b>{{ activeBar.active }}</b></li>
                      <li class="flex items-center gap-1 text-xs"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--color-error)"></span> Attention <b>{{ activeBar.attention }}</b></li>
                    </ul>
                    <span class="absolute -bottom-1 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 bg-neutral"></span>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- Financial pulse -->
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col md:col-span-4" aria-label="Financial pulse">
            <header class="flex items-start justify-between gap-2">
              <div>
                <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">{{ t('home.financials') }}</p>
                <p class="mt-1.5 text-xs text-base-content/60">{{ t('home.snapshot') }}</p>
              </div>
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 bg-accent/10 text-accent-ink !h-7 !w-7">
                <i class="fa-solid fa-wallet text-xs"></i>
              </span>
            </header>

            <div class="mt-3 flex-1 divide-y divide-base-300">
              <div v-for="kpi in kpis" :key="kpi.id" class="py-3 first:pt-1 last:pb-1">
                <div class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full" :style="{ background: kpi.color }"></span>
                  <p class="text-[11px] text-base-content/60">{{ kpi.label }}</p>
                  <i :class="kpi.icon" class="ml-auto text-[11px]" :style="{ color: kpi.tone }"></i>
                </div>
                <p class="mt-1 text-2xl font-extrabold leading-none tracking-tight tabular-nums">{{ kpi.value }}</p>
                <p class="mt-1 inline-flex items-center gap-1 text-[10px] font-semibold" :style="{ color: kpi.tone }">
                  {{ kpi.hint }}
                </p>
              </div>
            </div>

            <button
              type="button"
              class="mt-3 flex items-center justify-between rounded-[10px] bg-base-200 px-2.5 py-2 text-[11px] font-semibold text-base-content/60 transition hover:text-base-content"
              @click="emit('open-business')"
            >
              Business cockpit
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </section>
        </div>

        <!-- Budget + quick wins -->
        <div class="grid gap-4 md:grid-cols-2">
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col" aria-label="AI spending limit">
            <header class="flex items-start justify-between gap-2">
              <div>
                <h2 class="text-base font-bold tracking-tight">{{ limitTitle }}</h2>
                <p class="mt-1 text-[11px] leading-snug text-base-content/60">{{ limitSubtitle }}</p>
              </div>
              <button
                class="btn btn-ghost btn-sm btn-square !h-7 !w-7 !rounded-md border border-base-300"
                title="Open AI budget settings"
                aria-label="Open AI budget settings"
                @click="emit('open-settings')"
              >
                <i class="fa-solid fa-pen text-[11px]"></i>
              </button>
            </header>

            <div class="mt-4 flex flex-1 flex-col">
              <div class="flex items-center justify-between text-[11px] font-semibold">
                <span :style="{ color: budgetOver ? 'var(--color-error-ink)' : 'var(--color-base-content)' }">
                  {{ budgetProgressRaw }}% used
                </span>
                <span class="text-base-content/60">
                  <template v-if="budgetLimit > 0">{{ formatCxj(remainingBudget) }} cxj left</template>
                  <template v-else>No limit set</template>
                </span>
              </div>
              <div class="relative mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-base-300">
                <span class="block h-full rounded-full bg-accent" :class="budgetOver ? 'bg-error' : ''" :style="{ width: `${budgetProgress}%` }"></span>
              </div>
              <div class="mt-2 flex items-end justify-between gap-2">
                <div class="min-w-0">
                  <p class="text-2xl font-extrabold leading-none tracking-tight tabular-nums">{{ formatCxj(budgetSpent) }} <span class="text-[11px] font-semibold text-base-content/60">cxj spent</span></p>
                  <p class="mt-1 text-[10px] text-base-content/40">{{ hasDailyLimit ? 'Today' : 'Total' }} · {{ activeActors.length }} AI on duty</p>
                </div>
                <p class="shrink-0 text-xs font-semibold text-base-content/60">
                  {{ budgetLimit > 0 ? formatCxj(budgetLimit) : '—' }}
                </p>
              </div>
              <div class="mt-auto flex items-center justify-between rounded-lg bg-base-200 px-2.5 py-1.5">
                <span class="flex items-center gap-1.5 text-[10px] font-semibold text-base-content/60">
                  <i class="fa-solid fa-shield-halved text-[10px] text-success-ink"></i>
                  Autopilot enforced
                </span>
                <span class="text-[10px] text-base-content/40">Resets in {{ resetLabel }}</span>
              </div>
            </div>
          </section>

          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 relative flex flex-col overflow-hidden" aria-label="Quick wins">
            <header>
              <h2 class="text-base font-bold tracking-tight">{{ t('home.nextActions') }}</h2>
              <p class="mt-1 text-[11px] text-base-content/60">The three most useful moves from today's signals.</p>
            </header>

            <div class="mt-2.5 flex items-start gap-2">
              <div class="min-w-0 flex-1">
                <button
                  v-for="tip in tips"
                  :key="tip.id"
                  class="flex w-full min-w-0 items-center gap-2.5 rounded-field px-1.5 py-1.5 text-left transition-colors hover:bg-base-200 group !px-0.5"
                  @click="runTip(tip)"
                >
                  <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 !h-7 !w-7" :class="tip.tile">
                    <i :class="tip.icon" class="text-[11px]"></i>
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-xs font-bold">{{ tip.title }}</span>
                    <span class="mt-0.5 block truncate text-[10px] text-base-content/60">{{ tip.body }}</span>
                  </span>
                  <i class="fa-solid fa-chevron-right text-[10px] text-base-content/40 transition group-hover:text-base-content"></i>
                </button>
              </div>
              <div class="pointer-events-none absolute right-3 top-10 hidden sm:block">
                <div class="grid w-24 rotate-[-6deg] grid-cols-4 gap-1">
                  <span v-for="(cell, index) in 16" :key="cell" class="aspect-square rounded-[4px]" :class="(index + 1) % 5 === 0 ? 'bg-accent' : (index + 1) % 4 === 0 ? 'bg-success/10' : (index + 1) % 3 === 0 ? 'bg-warning/10' : 'bg-accent/10'"></span>
                </div>
              </div>
            </div>

            <button
              type="button"
              class="mt-auto inline-flex items-center gap-1 pt-2 text-[11px] font-bold"
              :style="{ color: 'var(--color-accent-ink)' }"
              @click="ask('Give me three ways to optimize spending and delivery this week')"
            >
              Read more <i class="fa-solid fa-chevron-right text-[10px]"></i>
            </button>
          </section>
        </div>

        <!-- Analysis row -->
        <div class="grid gap-4 sm:grid-cols-2 min-[1180px]:grid-cols-3">
          <!-- AI spend -->
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col" aria-label="AI spend">
            <header class="flex items-center justify-between gap-2">
              <h2 class="text-base font-bold tracking-tight whitespace-nowrap">{{ t('home.aiSpend') }}</h2>
              <span class="btn btn-xs !h-6 !text-[10px]">All time</span>
            </header>
            <p class="mt-2.5 text-2xl font-extrabold leading-none tracking-tight tabular-nums">
              {{ formatCxj(costTotal) }}<span class="ml-1 text-[11px] font-semibold text-base-content/60">cxj</span>
            </p>
            <p class="mt-1 text-[10px] text-base-content/60">Autopilot and teammate usage</p>

            <div v-if="costRows.length" class="mt-3">
              <div class="flex h-2 overflow-hidden rounded-full bg-base-300">
                <span
                  v-for="row in costRows"
                  :key="`bar-${row.key}`"
                  :style="{ width: `${row.share}%`, background: row.color }"
                ></span>
              </div>
              <div class="mt-2.5 space-y-1.5">
                <div v-for="row in costRows" :key="row.key" class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 shrink-0 rounded-full" :style="{ background: row.color }"></span>
                  <span class="min-w-0 flex-1 truncate text-[11px] text-base-content/60">{{ row.label }}</span>
                  <span class="text-[11px] font-semibold">{{ row.share }}%</span>
                </div>
              </div>
            </div>
            <div v-else class="mt-4 flex flex-1 flex-col items-start justify-center rounded-xl border border-dashed border-base-300 p-3">
              <p class="text-[11px] font-semibold">{{ t('home.noSpend') }}</p>
              <p class="mt-0.5 text-[10px] text-base-content/60">Dispatch a goal and the cost shows up here.</p>
            </div>

            <button
              type="button"
              class="mt-auto flex items-center justify-between border-t border-base-300 pt-2.5 text-[10px] font-semibold text-base-content/60 transition hover:text-base-content"
              @click="emit('open-settings')"
            >
              {{ t('home.budgetSettings') }}
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </section>

          <!-- Operating health -->
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col" aria-label="Operating health">
            <header class="flex items-center justify-between gap-2">
              <h2 class="text-base font-bold tracking-tight whitespace-nowrap">{{ t('home.operatingHealth') }}</h2>
              <span class="btn btn-xs !h-6 !text-[10px]">Now</span>
            </header>
            <div class="mt-2.5 flex items-end justify-between gap-2">
              <div class="min-w-0">
                <p class="text-2xl font-extrabold leading-none tracking-tight tabular-nums">{{ formatMoney(grossProfit, currency) }}</p>
                <p class="mt-1 text-[10px] text-base-content/60">Gross profit · {{ margin }}% margin</p>
              </div>
              <span class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-bold" :style="{ color: healthTone, background: 'var(--color-base-200)' }">
                <span class="h-1 w-1 rounded-full" :style="{ background: healthTone }"></span>
                {{ healthLabel }}
              </span>
            </div>

            <div class="relative mx-auto mt-2 h-[132px] w-[168px]" role="img" :aria-label="`Operating health ${health} percent`">
              <svg viewBox="0 0 200 200" class="h-full w-full" aria-hidden="true">
                <defs>
                  <linearGradient id="company-health-gradient" x1="0%" y1="100%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="var(--color-warning)" />
                    <stop offset="55%" stop-color="var(--color-accent)" />
                    <stop offset="100%" stop-color="var(--color-success)" />
                  </linearGradient>
                </defs>
                <path d="M 46 154 A 76 76 0 1 1 154 154" fill="none" stroke="var(--color-base-300)" stroke-width="13" stroke-linecap="round" />
                <path
                  d="M 46 154 A 76 76 0 1 1 154 154"
                  fill="none"
                  stroke="url(#company-health-gradient)"
                  stroke-width="13"
                  stroke-linecap="round"
                  pathLength="100"
                  stroke-dasharray="100"
                  :stroke-dashoffset="100 - health"
                />
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center pt-3">
                <span class="text-3xl font-extrabold leading-none tracking-tight">{{ health }}<span class="text-xs font-bold text-base-content/60">%</span></span>
                <span class="mt-1 text-[10px] font-bold uppercase tracking-[0.14em]" :style="{ color: healthTone }">{{ healthLabel }}</span>
              </div>
            </div>

            <p class="mt-auto text-center text-[10px] leading-relaxed text-base-content/60">
              Based on open decisions, blocked work, budget pressure and team coverage.
            </p>
          </section>

          <!-- Goal tracker -->
          <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col sm:col-span-2 min-[1180px]:col-span-1" aria-label="Goal tracker">
            <header class="flex items-center justify-between gap-2">
              <div>
                <h2 class="text-base font-bold tracking-tight whitespace-nowrap">{{ t('home.goalTracker') }}</h2>
                <p class="mt-0.5 text-[10px] text-base-content/60">In motion right now</p>
              </div>
              <button v-if="canManage" class="btn btn-xs !h-6 !text-[10px]" @click="emit('new-goal')">
                <i class="fa-solid fa-plus text-[10px]"></i> {{ t('home.addGoal') }}
              </button>
            </header>

            <div v-if="goalRows.length" class="mt-2 flex-1 space-y-0.5">
              <div
                v-for="goal in goalRows"
                :key="goal.id"
                class="group -mx-1 w-[calc(100%+0.5rem)] cursor-pointer rounded-[10px] px-1 py-1.5 text-left transition hover:bg-base-200"
                role="button"
                tabindex="0"
                @click="openMission(goal)"
                @keydown.enter.prevent="openMission(goal)"
                @keydown.space.prevent="openMission(goal)"
              >
                <div class="flex items-start gap-2">
                  <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 !h-7 !w-7" :class="goal.status === 'blocked' ? 'bg-error/10 text-error-ink' : goal.status === 'review' ? 'bg-warning/10 text-warning-ink' : 'bg-accent/10 text-accent-ink'">
                    <i :class="goal.meta.icon" class="text-[11px]"></i>
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="flex items-center gap-1">
                      <span class="min-w-0 flex-1 truncate text-[11px] font-bold">{{ goal.title }}</span>
                      <span class="shrink-0 text-[10px] font-bold tabular-nums">{{ goal.progress }}%</span>
                    </span>
                    <span class="mt-0.5 block truncate text-[10px] text-base-content/60">{{ goal.team }} · {{ goal.due }}</span>
                    <span class="mt-1 block h-1.5 w-full overflow-hidden rounded-full bg-base-300">
                      <span class="block h-full rounded-full bg-accent" :class="goal.status === 'blocked' ? 'bg-error' : goal.status === 'review' ? 'bg-warning' : ''" :style="{ width: `${Math.max(3, goal.progress)}%` }"></span>
                    </span>
                  </span>
                  <button class="grid h-5 w-5 shrink-0 place-items-center rounded-md text-base-content/40 opacity-0 transition group-hover:opacity-100" title="Ask about this goal" aria-label="Ask about this goal" @click.stop="askGoal(goal)">
                    <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                  </button>
                </div>
              </div>
            </div>

            <div v-else class="mt-3 flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-base-300 p-3 text-center">
              <i class="fa-solid fa-flag-checkered text-lg text-base-content/40"></i>
              <p class="mt-1 text-[11px] font-bold">{{ t('home.noGoals') }}</p>
              <p class="mt-0.5 text-[10px] text-base-content/60">Turn an outcome into work the team can run.</p>
              <button v-if="canManage" class="btn btn-sm btn-primary mt-2 !h-7 !text-[11px]" @click="emit('new-goal')">Set a goal</button>
            </div>

            <button
              type="button"
              class="mt-2 flex items-center justify-between border-t border-base-300 pt-2.5 text-[10px] font-semibold text-base-content/60 transition hover:text-base-content"
              @click="emit('open-work')"
            >
              See all goals
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </section>
        </div>
      </div>

      <!-- ── Right rail ──────────────────────────────────────────────────── -->
      <div class="space-y-4 min-[1180px]:col-span-4 min-[1180px]:space-y-5">
        <!-- Quick actions + team shortcuts -->
        <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4" aria-label="Quick actions">
          <header class="flex items-center justify-between gap-2">
            <div>
              <h2 class="text-base font-bold tracking-tight">{{ t('home.quickActions') }}</h2>
              <p class="mt-0.5 text-[10px] text-base-content/60">One click to the work that matters.</p>
            </div>
            <span class="btn btn-xs !h-6 !text-[10px]">{{ counts.teams || teams.length || 0 }} teams</span>
          </header>

          <div class="mt-2.5 grid grid-cols-3 gap-0.5 border-b border-base-300 pb-2 sm:grid-cols-6">
            <button
              v-for="action in visibleQuickActions"
              :key="action.id"
              type="button"
              class="flex min-w-0 flex-col items-center justify-center gap-1 rounded-selector px-0.5 py-1 text-xs font-medium text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content [&>i]:text-base [&>i]:text-base-content"
              :title="t(action.labelKey)"
              @click="action.run"
            >
              <i :class="action.icon"></i>
              <span>{{ t(action.labelKey) }}</span>
            </button>
          </div>

          <div class="mt-2.5 grid grid-cols-2 gap-2">
            <button class="rounded-xl bg-base-200 p-2.5 text-left transition hover:bg-base-300" @click="emit('open-business')">
              <span class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40 !text-[10px]">{{ t('home.openPipeline') }}</span>
              <span class="mt-1 block text-base font-extrabold leading-tight tabular-nums">{{ formatMoney(pipelineValue, currency, true) }}</span>
              <span class="mt-0.5 block text-[10px] text-base-content/60">{{ openDealCount }} opportunit{{ openDealCount === 1 ? 'y' : 'ies' }}</span>
            </button>
            <button class="rounded-xl bg-base-200 p-2.5 text-left transition hover:bg-base-300" @click="emit('open-team')">
              <span class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40 !text-[10px]">{{ t('home.people') }}</span>
              <span class="mt-1 block text-base font-extrabold leading-tight tabular-nums">{{ peopleCount }}</span>
              <span class="mt-0.5 block text-[10px] text-base-content/60">humans + AI</span>
            </button>
          </div>

          <div class="mt-2.5 border-t border-base-300 pt-2.5">
            <div class="flex items-center justify-between">
              <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40 !text-[10px]">{{ t('home.aiTeammates') }}</p>
              <button class="grid h-5 w-5 place-items-center rounded-md text-base-content/60 transition hover:bg-base-200 hover:text-base-content" title="Manage the team" aria-label="Manage the team" @click="emit('open-team')">
                <i class="fa-solid fa-plus text-[10px]"></i>
              </button>
            </div>
            <div class="mt-2 flex items-center">
              <div v-if="roster.length" class="flex items-center -space-x-1.5">
                <button
                  v-for="person in roster"
                  :key="person.name"
                  type="button"
                  class="relative shrink-0 rounded-full transition hover:-translate-y-0.5"
                  :title="person.name"
                  :aria-label="`Ask about ${person.name}`"
                  @click="ask(`What is ${person.name} working on and how is it performing?`)"
                >
                  <span class="relative grid h-[31px] w-[31px] place-items-center overflow-hidden rounded-full border-2 border-[var(--color-base-100)] bg-accent/10">
                    <span class="absolute inset-0 grid place-items-center text-[10px] font-extrabold text-accent-ink">{{ initials(person.name) }}</span>
                    <img :src="person.avatar" :alt="person.name" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                  </span>
                  <span class="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border-2 border-[var(--color-base-100)]" :class="person.kind === 'ai' ? 'bg-accent' : 'bg-success'"></span>
                </button>
              </div>
              <span v-else class="text-[10px] text-base-content/60">{{ t('home.noTeammates') }}</span>
              <button
                class="ml-auto grid h-7 w-7 place-items-center rounded-full border border-base-300 text-base-content/60 transition hover:bg-base-200 hover:text-base-content"
                title="Ask the team"
                aria-label="Ask the team"
                @click="ask('Give me a status update of the whole team')"
              >
                <i class="fa-solid fa-ellipsis text-[11px]"></i>
              </button>
            </div>
          </div>
        </section>

        <CompanyCalendar :snapshot="snapshot" @open="emit('open-calendar')" />

        <!-- Activity history -->
        <section class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4 flex flex-col" aria-label="Company activity">
          <header class="flex items-center justify-between gap-2">
            <h2 class="text-base font-bold tracking-tight">{{ t('home.activityHistory') }}</h2>
            <span class="btn btn-xs !h-6 !text-[10px]">7d</span>
          </header>

          <div class="-mx-1 mt-2 flex-1">
            <button
              v-for="event in activity"
              :key="event.id || `${event.at}-${event.type}`"
              type="button"
              class="flex w-full min-w-0 items-center gap-2.5 rounded-field px-1.5 py-1.5 text-left transition-colors hover:bg-base-200"
              :title="event.message"
              @click="openEvent(event)"
            >
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 !h-8 !w-8" :class="eventLook(event).tile">
                <i :class="eventIcon(event)" class="text-[11px]"></i>
              </span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-[11px] font-semibold">{{ event.message || event.type }}</span>
                <span class="mt-0.5 block truncate text-[10px] text-base-content/60">{{ event.actor || 'system' }}</span>
              </span>
              <span class="shrink-0 text-right">
                <span class="block text-[10px] font-bold" :style="{ color: eventLook(event).color }">{{ eventLook(event).label }}</span>
                <span class="mt-0.5 block text-[10px] text-base-content/40">{{ relativeTime(event.at) }}</span>
              </span>
            </button>

            <div v-if="!activity.length" class="flex flex-col items-center justify-center px-3 py-8 text-center">
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 !h-9 !w-9"><i class="fa-solid fa-wave-square text-xs"></i></span>
              <p class="mt-2 text-[11px] font-semibold">Nothing has happened yet.</p>
              <p class="mt-0.5 text-[10px] text-base-content/60">Create a goal and the company journal starts here.</p>
            </div>
          </div>

          <button
            type="button"
            class="mt-2 flex items-center justify-between border-t border-base-300 pt-2.5 text-[11px] font-semibold text-base-content/60 transition hover:text-base-content"
            @click="emit('open-activity')"
          >
            {{ t('home.allActivity') }}
            <i class="fa-solid fa-arrow-right text-[10px]"></i>
          </button>
        </section>

        <!-- One decision, surfaced -->
        <section v-if="attentionFocus" class="rounded-box border border-base-300 bg-base-100 shadow-sm p-4" aria-label="Next decision">
          <div class="flex items-start gap-2.5">
            <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 bg-warning/10 text-warning-ink !h-8 !w-8">
              <i class="fa-solid fa-stamp text-xs"></i>
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40 !text-[10px]">Next decision</p>
              <p class="mt-1 truncate text-xs font-bold">{{ attentionFocus.title }}</p>
              <p class="mt-0.5 truncate text-[10px] text-base-content/60">{{ attentionLabel(attentionFocus.reason) }} · {{ attentionFocus.status }}</p>
            </div>
          </div>
          <div class="mt-2.5 flex gap-1.5">
            <button class="btn btn-sm btn-primary flex-1 !h-8 !text-[11px]" @click="openAttention(attentionFocus)">Review now</button>
            <button class="btn btn-xs !h-8" title="Ask about this decision" aria-label="Ask about this decision" @click="ask(`Help me decide about ${attentionFocus.title}`)">
              <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
