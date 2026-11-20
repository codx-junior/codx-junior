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
  'assistant', 'open-work', 'open-team', 'open-business', 'open-activity',
  'open-settings', 'open-calendar', 'new-goal', 'open-chat'
])

const company = computed(() => props.snapshot?.company || {})
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
    color: 'var(--cp-lime)',
    tone: 'var(--cp-success-ink)'
  },
  {
    id: 'costs',
    label: t('home.totalExpenses'),
    value: formatMoney(costs.value, currency.value),
    hint: t('home.ofRevenue', { value: costRatio.value }),
    icon: 'fa-solid fa-arrow-trend-down',
    color: 'var(--cp-yellow)',
    tone: 'var(--cp-warning-ink)'
  },
  {
    id: 'saved',
    label: t('home.grossProfit'),
    value: formatMoney(grossProfit.value, currency.value),
    hint: t('home.grossMargin', { value: margin.value }),
    icon: grossProfit.value >= 0 ? 'fa-solid fa-arrow-trend-up' : 'fa-solid fa-arrow-trend-down',
    color: 'var(--cp-green)',
    tone: grossProfit.value >= 0 ? 'var(--cp-success-ink)' : 'var(--cp-error-ink)'
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
const healthTone = computed(() => health.value >= 75 ? 'var(--cp-success-ink)' : health.value >= 50 ? 'var(--cp-warning-ink)' : 'var(--cp-error-ink)')

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
  { id: 'done', labelKey: 'home.delivered', color: 'var(--cp-lime)' },
  { id: 'active', labelKey: 'home.inMotion', color: 'var(--cp-yellow)' },
  { id: 'attention', labelKey: 'home.attention', color: 'var(--cp-peach)' }
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
      { id: 'done', value: day.done, color: 'var(--cp-lime)' },
      { id: 'active', value: day.active, color: 'var(--cp-yellow)' },
      { id: 'attention', value: day.attention, color: 'var(--cp-peach)' }
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
  'var(--cp-lime)',
  'var(--cp-green)',
  'var(--cp-yellow)',
  'var(--cp-peach)',
  'var(--cp-muted-soft)'
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
      tile: 'cp-icon-tile--yellow'
    })
  }
  if (reviewMissions.value.length) {
    list.push({
      id: 'review',
      target: 'work',
      title: `${reviewMissions.value.length} goal${reviewMissions.value.length === 1 ? '' : 's'} ready to review`,
      body: 'Accept the outcome or send it back with notes.',
      icon: 'fa-solid fa-magnifying-glass',
      tile: 'cp-icon-tile--lime'
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
      tile: 'cp-icon-tile--red'
    })
  } else if (revenueGap.value > 0 && revenueTarget.value > 0) {
    list.push({
      id: 'revenue',
      target: 'business',
      title: `${formatMoney(revenueGap.value, currency.value)} to the revenue target`,
      body: 'Review the pipeline and the next best deal.',
      icon: 'fa-solid fa-arrow-trend-up',
      tile: 'cp-icon-tile--green'
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
      tile: 'cp-icon-tile--lime'
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
    return { label: 'Review', color: 'var(--cp-error-ink)', tile: 'cp-icon-tile--red' }
  }
  if (type.includes('accepted') || type.includes('done') || type.includes('closed')) {
    return { label: 'Done', color: 'var(--cp-success-ink)', tile: 'cp-icon-tile--lime' }
  }
  if (type.includes('created') || type.includes('dispatched')) {
    return { label: 'New', color: 'var(--cp-accent-ink)', tile: 'cp-icon-tile--green' }
  }
  if (type.includes('updated') || type.includes('transitioned') || type.includes('synced')) {
    return { label: 'Update', color: 'var(--cp-info-ink)', tile: 'cp-icon-tile' }
  }
  return { label: 'Info', color: 'var(--cp-muted)', tile: 'cp-icon-tile--yellow' }
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
  <div class="company-dashboard">
    <h1 class="sr-only">Company overview</h1>

    <div class="grid gap-4 min-[1180px]:grid-cols-12 min-[1180px]:gap-5">
      <!-- ── Main column ─────────────────────────────────────────────────── -->
      <div class="space-y-4 min-[1180px]:col-span-8 min-[1180px]:space-y-5">
        <div class="grid gap-4 md:grid-cols-12">
          <!-- Delivery activity -->
          <section class="cp-card cp-card-pad flex flex-col md:col-span-8" aria-label="Company activity">
            <header class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="cp-eyebrow">{{ t('home.companyActivity') }}</p>
                <p class="cp-display mt-2">{{ weekTotal }}<span class="ml-1.5 text-[13px] font-bold cp-muted">{{ t('home.events') }}</span></p>
                <p class="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span class="cp-muted">{{ t('home.last7') }}</span>
                  <span class="text-[var(--cp-muted-soft)]">·</span>
                  <span class="inline-flex items-center gap-1 font-semibold" :style="{ color: 'var(--cp-success-ink)' }">
                    <i class="fa-solid fa-wave-square text-[8px]"></i>
                    {{ t('home.deliveryDecisions') }}
                  </span>
                </p>
              </div>
              <div class="flex items-center gap-1.5">
                <button class="cp-icon-btn !h-7 !w-7 !rounded-md" title="Open activity" aria-label="Open activity" @click="emit('open-activity')">
                  <i class="fa-solid fa-list-ul text-[10px]"></i>
                </button>
                <button class="cp-icon-btn !h-7 !w-7 !rounded-md bg-[var(--cp-surface-soft)]" title="Weekly chart" aria-label="Weekly chart" @click="emit('open-activity')">
                  <i class="fa-solid fa-chart-column text-[10px]"></i>
                </button>
              </div>
            </header>

            <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-3">
                <span v-for="item in legend" :key="item.id" class="inline-flex items-center gap-1.5 text-[9px] font-semibold cp-muted">
                  <span class="h-1.5 w-1.5 rounded-full" :style="{ background: item.color }"></span>
                  {{ t(item.labelKey) }}
                </span>
              </div>
              <span class="text-[9px] cp-muted-soft">{{ activity.length }} {{ t('home.recentEntries') }}</span>
            </div>

            <div class="relative mt-auto pt-4">
              <div v-if="!weekTotal" class="pointer-events-none absolute inset-x-0 top-8 z-[2] flex justify-center">
                <span class="cp-chip !h-6 !border-transparent !bg-[var(--cp-surface-soft)] !text-[8.5px]">No activity in the last 7 days</span>
              </div>
              <div class="cp-chart">
                <div class="cp-chart-y">
                  <span v-for="tick in axisTicks" :key="tick">{{ tick }}</span>
                </div>
                <div class="cp-chart-plot" @mouseleave="hoverBar = -1">
                  <div class="cp-chart-lines">
                    <span v-for="tick in axisTicks" :key="`line-${tick}`"></span>
                  </div>
                  <div class="cp-chart-cols">
                    <div
                      v-for="bar in weekBars"
                      :key="bar.date.getTime()"
                      class="cp-chart-col"
                      :class="barActive(bar.index) ? 'is-active' : ''"
                      @mouseenter="hoverBar = bar.index"
                      @focusin="hoverBar = bar.index"
                      tabindex="0"
                      :aria-label="`${bar.dateLabel}: ${bar.count} activity events`"
                    >
                      <div class="cp-chart-stack" :style="{ height: `${bar.height}%` }">
                        <template v-if="barActive(bar.index)">
                          <i
                            v-for="part in bar.segments"
                            :key="part.id"
                            :style="{ flex: `${part.value} 1 0`, background: part.color }"
                          ></i>
                        </template>
                      </div>
                      <span class="cp-chart-label">{{ bar.label }}</span>
                    </div>
                  </div>

                  <div v-if="activeBar && weekTotal" class="cp-chart-tooltip" :style="{ left: `${tooltipLeft}%` }">
                    <p class="cp-chart-tooltip-title">{{ activeBar.dateLabel }}</p>
                    <ul class="mt-1 space-y-0.5">
                      <li class="cp-chart-tooltip-row"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--cp-lime)"></span> Delivered <b>{{ activeBar.done }}</b></li>
                      <li class="cp-chart-tooltip-row"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--cp-yellow)"></span> In motion <b>{{ activeBar.active }}</b></li>
                      <li class="cp-chart-tooltip-row"><span class="h-1.5 w-1.5 rounded-full" style="background: var(--cp-peach)"></span> Attention <b>{{ activeBar.attention }}</b></li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- Financial pulse -->
          <section class="cp-card cp-card-pad flex flex-col md:col-span-4" aria-label="Financial pulse">
            <header class="flex items-start justify-between gap-2">
              <div>
                <p class="cp-eyebrow">{{ t('home.financials') }}</p>
                <p class="mt-1.5 text-[10px] cp-muted">{{ t('home.snapshot') }}</p>
              </div>
              <span class="cp-icon-tile cp-icon-tile--lime !h-7 !w-7">
                <i class="fa-solid fa-wallet text-[10px]"></i>
              </span>
            </header>

            <div class="mt-3 flex-1 divide-y divide-[var(--cp-border)]">
              <div v-for="kpi in kpis" :key="kpi.id" class="py-3 first:pt-1 last:pb-1">
                <div class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full" :style="{ background: kpi.color }"></span>
                  <p class="text-[9.5px] cp-muted">{{ kpi.label }}</p>
                  <i :class="kpi.icon" class="ml-auto text-[9px]" :style="{ color: kpi.tone }"></i>
                </div>
                <p class="mt-1 text-[19px] font-extrabold leading-none tracking-tight tabular-nums">{{ kpi.value }}</p>
                <p class="mt-1 inline-flex items-center gap-1 text-[8.5px] font-semibold" :style="{ color: kpi.tone }">
                  {{ kpi.hint }}
                </p>
              </div>
            </div>

            <button
              type="button"
              class="mt-3 flex items-center justify-between rounded-[10px] bg-[var(--cp-surface-soft)] px-2.5 py-2 text-[9px] font-semibold cp-muted transition hover:text-[var(--cp-text)]"
              @click="emit('open-business')"
            >
              Business cockpit
              <i class="fa-solid fa-arrow-right text-[8px]"></i>
            </button>
          </section>
        </div>

        <!-- Budget + quick wins -->
        <div class="grid gap-4 md:grid-cols-2">
          <section class="cp-card cp-card-pad flex flex-col" aria-label="AI spending limit">
            <header class="flex items-start justify-between gap-2">
              <div>
                <h2 class="cp-title">{{ limitTitle }}</h2>
                <p class="mt-1 text-[9.5px] leading-snug cp-muted">{{ limitSubtitle }}</p>
              </div>
              <button
                class="cp-icon-btn !h-7 !w-7 !rounded-md border border-[var(--cp-border)]"
                title="Open AI budget settings"
                aria-label="Open AI budget settings"
                @click="emit('open-settings')"
              >
                <i class="fa-solid fa-pen text-[9px]"></i>
              </button>
            </header>

            <div class="mt-4 flex flex-1 flex-col">
              <div class="flex items-center justify-between text-[9px] font-semibold">
                <span :style="{ color: budgetOver ? 'var(--cp-error-ink)' : 'var(--cp-text)' }">
                  {{ budgetProgressRaw }}% used
                </span>
                <span class="cp-muted">
                  <template v-if="budgetLimit > 0">{{ formatCxj(remainingBudget) }} cxj left</template>
                  <template v-else>No limit set</template>
                </span>
              </div>
              <div class="cp-progress mt-1.5" :class="budgetOver ? 'cp-progress--danger' : ''">
                <span :style="{ width: `${budgetProgress}%` }"></span>
              </div>
              <div class="mt-2 flex items-end justify-between gap-2">
                <div class="min-w-0">
                  <p class="text-[18px] font-extrabold leading-none tracking-tight tabular-nums">{{ formatCxj(budgetSpent) }} <span class="text-[9px] font-semibold cp-muted">cxj spent</span></p>
                  <p class="mt-1 text-[8.5px] cp-muted-soft">{{ hasDailyLimit ? 'Today' : 'Total' }} · {{ activeActors.length }} AI on duty</p>
                </div>
                <p class="shrink-0 text-[10px] font-semibold cp-muted">
                  {{ budgetLimit > 0 ? formatCxj(budgetLimit) : '—' }}
                </p>
              </div>
              <div class="mt-auto flex items-center justify-between rounded-lg bg-[var(--cp-surface-soft)] px-2.5 py-1.5">
                <span class="flex items-center gap-1.5 text-[8.5px] font-semibold cp-muted">
                  <i class="fa-solid fa-shield-halved text-[8px] text-success"></i>
                  Autopilot enforced
                </span>
                <span class="text-[8px] cp-muted-soft">Resets in {{ resetLabel }}</span>
              </div>
            </div>
          </section>

          <section class="cp-card cp-card-pad relative flex flex-col overflow-hidden" aria-label="Quick wins">
            <header>
              <h2 class="cp-title">{{ t('home.nextActions') }}</h2>
              <p class="mt-1 text-[9.5px] cp-muted">The three most useful moves from today's signals.</p>
            </header>

            <div class="mt-2.5 flex items-start gap-2">
              <div class="min-w-0 flex-1">
                <button
                  v-for="tip in tips"
                  :key="tip.id"
                  class="cp-list-row group !px-0.5"
                  @click="runTip(tip)"
                >
                  <span class="cp-icon-tile !h-7 !w-7" :class="tip.tile">
                    <i :class="tip.icon" class="text-[9px]"></i>
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-[10px] font-bold">{{ tip.title }}</span>
                    <span class="mt-0.5 block truncate text-[8.5px] cp-muted">{{ tip.body }}</span>
                  </span>
                  <i class="fa-solid fa-chevron-right text-[7px] cp-muted-soft transition group-hover:text-[var(--cp-text)]"></i>
                </button>
              </div>
              <div class="pointer-events-none absolute right-3 top-10 hidden sm:block">
                <div class="cp-mosaic">
                  <span v-for="cell in 16" :key="cell"></span>
                </div>
              </div>
            </div>

            <button
              type="button"
              class="mt-auto inline-flex items-center gap-1 pt-2 text-[9px] font-bold"
              :style="{ color: 'var(--cp-accent-ink)' }"
              @click="ask('Give me three ways to optimize spending and delivery this week')"
            >
              Read more <i class="fa-solid fa-chevron-right text-[7px]"></i>
            </button>
          </section>
        </div>

        <!-- Analysis row -->
        <div class="company-analysis-grid grid gap-4 sm:grid-cols-2">
          <!-- AI spend -->
          <section class="cp-card cp-card-pad flex flex-col" aria-label="AI spend">
            <header class="flex items-center justify-between gap-2">
              <h2 class="cp-title whitespace-nowrap">{{ t('home.aiSpend') }}</h2>
              <span class="cp-chip !h-6 !text-[8.5px]">All time</span>
            </header>
            <p class="mt-2.5 text-[20px] font-extrabold leading-none tracking-tight tabular-nums">
              {{ formatCxj(costTotal) }}<span class="ml-1 text-[9px] font-semibold cp-muted">cxj</span>
            </p>
            <p class="mt-1 text-[8.5px] cp-muted">Autopilot and teammate usage</p>

            <div v-if="costRows.length" class="mt-3">
              <div class="flex h-2 overflow-hidden rounded-full bg-[var(--cp-track)]">
                <span
                  v-for="row in costRows"
                  :key="`bar-${row.key}`"
                  :style="{ width: `${row.share}%`, background: row.color }"
                ></span>
              </div>
              <div class="mt-2.5 space-y-1.5">
                <div v-for="row in costRows" :key="row.key" class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 shrink-0 rounded-full" :style="{ background: row.color }"></span>
                  <span class="min-w-0 flex-1 truncate text-[9px] cp-muted">{{ row.label }}</span>
                  <span class="text-[9px] font-semibold">{{ row.share }}%</span>
                </div>
              </div>
            </div>
            <div v-else class="mt-4 flex flex-1 flex-col items-start justify-center rounded-xl border border-dashed border-[var(--cp-border)] p-3">
              <p class="text-[9.5px] font-semibold">{{ t('home.noSpend') }}</p>
              <p class="mt-0.5 text-[8.5px] cp-muted">Dispatch a goal and the cost shows up here.</p>
            </div>

            <button
              type="button"
              class="mt-auto flex items-center justify-between border-t border-[var(--cp-border)] pt-2.5 text-[8.5px] font-semibold cp-muted transition hover:text-[var(--cp-text)]"
              @click="emit('open-settings')"
            >
              {{ t('home.budgetSettings') }}
              <i class="fa-solid fa-arrow-right text-[7px]"></i>
            </button>
          </section>

          <!-- Operating health -->
          <section class="cp-card cp-card-pad flex flex-col" aria-label="Operating health">
            <header class="flex items-center justify-between gap-2">
              <h2 class="cp-title whitespace-nowrap">{{ t('home.operatingHealth') }}</h2>
              <span class="cp-chip !h-6 !text-[8.5px]">Now</span>
            </header>
            <div class="mt-2.5 flex items-end justify-between gap-2">
              <div class="min-w-0">
                <p class="text-[19px] font-extrabold leading-none tracking-tight tabular-nums">{{ formatMoney(grossProfit, currency) }}</p>
                <p class="mt-1 text-[8.5px] cp-muted">Gross profit · {{ margin }}% margin</p>
              </div>
              <span class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[8px] font-bold" :style="{ color: healthTone, background: 'var(--cp-surface-soft)' }">
                <span class="h-1 w-1 rounded-full" :style="{ background: healthTone }"></span>
                {{ healthLabel }}
              </span>
            </div>

            <div class="relative mx-auto mt-2 h-[132px] w-[168px]" role="img" :aria-label="`Operating health ${health} percent`">
              <svg viewBox="0 0 200 200" class="h-full w-full" aria-hidden="true">
                <defs>
                  <linearGradient id="cp-health-gradient" x1="0%" y1="100%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="var(--cp-yellow)" />
                    <stop offset="55%" stop-color="var(--cp-lime)" />
                    <stop offset="100%" stop-color="var(--cp-green)" />
                  </linearGradient>
                </defs>
                <path d="M 46 154 A 76 76 0 1 1 154 154" fill="none" stroke="var(--cp-track)" stroke-width="13" stroke-linecap="round" />
                <path
                  d="M 46 154 A 76 76 0 1 1 154 154"
                  fill="none"
                  stroke="url(#cp-health-gradient)"
                  stroke-width="13"
                  stroke-linecap="round"
                  pathLength="100"
                  stroke-dasharray="100"
                  :stroke-dashoffset="100 - health"
                />
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center pt-3">
                <span class="text-[24px] font-extrabold leading-none tracking-tight">{{ health }}<span class="text-[11px] font-bold cp-muted">%</span></span>
                <span class="mt-1 text-[8px] font-bold uppercase tracking-[0.14em]" :style="{ color: healthTone }">{{ healthLabel }}</span>
              </div>
            </div>

            <p class="mt-auto text-center text-[8px] leading-relaxed cp-muted">
              Based on open decisions, blocked work, budget pressure and team coverage.
            </p>
          </section>

          <!-- Goal tracker -->
          <section class="company-goal-card cp-card cp-card-pad flex flex-col sm:col-span-2" aria-label="Goal tracker">
            <header class="flex items-center justify-between gap-2">
              <div>
                <h2 class="cp-title whitespace-nowrap">{{ t('home.goalTracker') }}</h2>
                <p class="mt-0.5 text-[8.5px] cp-muted">In motion right now</p>
              </div>
              <button v-if="canManage" class="cp-chip !h-6 !text-[8.5px]" @click="emit('new-goal')">
                <i class="fa-solid fa-plus text-[7px]"></i> {{ t('home.addGoal') }}
              </button>
            </header>

            <div v-if="goalRows.length" class="mt-2 flex-1 space-y-0.5">
              <div
                v-for="goal in goalRows"
                :key="goal.id"
                class="group -mx-1 w-[calc(100%+0.5rem)] cursor-pointer rounded-[10px] px-1 py-1.5 text-left transition hover:bg-[var(--cp-surface-soft)]"
                role="button"
                tabindex="0"
                @click="openMission(goal)"
                @keydown.enter.prevent="openMission(goal)"
                @keydown.space.prevent="openMission(goal)"
              >
                <div class="flex items-start gap-2">
                  <span class="cp-icon-tile !h-7 !w-7" :class="goal.status === 'blocked' ? 'cp-icon-tile--red' : goal.status === 'review' ? 'cp-icon-tile--yellow' : 'cp-icon-tile--lime'">
                    <i :class="goal.meta.icon" class="text-[9px]"></i>
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="flex items-center gap-1">
                      <span class="min-w-0 flex-1 truncate text-[9.5px] font-bold">{{ goal.title }}</span>
                      <span class="shrink-0 text-[8.5px] font-bold tabular-nums">{{ goal.progress }}%</span>
                    </span>
                    <span class="mt-0.5 block truncate text-[8px] cp-muted">{{ goal.team }} · {{ goal.due }}</span>
                    <span class="cp-progress cp-progress--slim mt-1 block">
                      <span :style="{ width: `${Math.max(3, goal.progress)}%`, background: goal.status === 'blocked' ? 'var(--cp-red)' : goal.status === 'review' ? 'var(--cp-yellow)' : 'var(--cp-lime)' }"></span>
                    </span>
                  </span>
                  <button class="grid h-5 w-5 shrink-0 place-items-center rounded-md cp-muted-soft opacity-0 transition group-hover:opacity-100" title="Ask about this goal" aria-label="Ask about this goal" @click.stop="askGoal(goal)">
                    <i class="fa-solid fa-wand-magic-sparkles text-[8px]"></i>
                  </button>
                </div>
              </div>
            </div>

            <div v-else class="mt-3 flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--cp-border)] p-3 text-center">
              <i class="fa-solid fa-flag-checkered text-lg text-[var(--cp-muted-soft)]"></i>
              <p class="mt-1 text-[9.5px] font-bold">{{ t('home.noGoals') }}</p>
              <p class="mt-0.5 text-[8.5px] cp-muted">Turn an outcome into work the team can run.</p>
              <button v-if="canManage" class="cp-cta mt-2 !h-7 !text-[9px]" @click="emit('new-goal')">Set a goal</button>
            </div>

            <button
              type="button"
              class="mt-2 flex items-center justify-between border-t border-[var(--cp-border)] pt-2.5 text-[8.5px] font-semibold cp-muted transition hover:text-[var(--cp-text)]"
              @click="emit('open-work')"
            >
              See all goals
              <i class="fa-solid fa-arrow-right text-[7px]"></i>
            </button>
          </section>
        </div>
      </div>

      <!-- ── Right rail ──────────────────────────────────────────────────── -->
      <div class="space-y-4 min-[1180px]:col-span-4 min-[1180px]:space-y-5">
        <!-- Quick actions + team shortcuts -->
        <section class="cp-card cp-card-pad" aria-label="Quick actions">
          <header class="flex items-center justify-between gap-2">
            <div>
              <h2 class="cp-title">{{ t('home.quickActions') }}</h2>
              <p class="mt-0.5 text-[8.5px] cp-muted">One click to the work that matters.</p>
            </div>
            <span class="cp-chip !h-6 !text-[8.5px]">{{ counts.teams || teams.length || 0 }} teams</span>
          </header>

          <div class="mt-2.5 grid grid-cols-5 gap-0.5 border-b border-[var(--cp-border)] pb-2">
            <button
              v-for="action in visibleQuickActions"
              :key="action.id"
              type="button"
              class="cp-quick-btn"
              :title="t(action.labelKey)"
              @click="action.run"
            >
              <i :class="action.icon"></i>
              <span>{{ t(action.labelKey) }}</span>
            </button>
          </div>

          <div class="mt-2.5 grid grid-cols-2 gap-2">
            <button class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5 text-left transition hover:bg-[var(--cp-surface-strong)]" @click="emit('open-business')">
              <span class="cp-eyebrow !text-[7.5px]">{{ t('home.openPipeline') }}</span>
              <span class="mt-1 block text-[14px] font-extrabold leading-tight tabular-nums">{{ formatMoney(pipelineValue, currency, true) }}</span>
              <span class="mt-0.5 block text-[8px] cp-muted">{{ openDealCount }} opportunit{{ openDealCount === 1 ? 'y' : 'ies' }}</span>
            </button>
            <button class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5 text-left transition hover:bg-[var(--cp-surface-strong)]" @click="emit('open-team')">
              <span class="cp-eyebrow !text-[7.5px]">{{ t('home.people') }}</span>
              <span class="mt-1 block text-[14px] font-extrabold leading-tight tabular-nums">{{ peopleCount }}</span>
              <span class="mt-0.5 block text-[8px] cp-muted">humans + AI</span>
            </button>
          </div>

          <div class="mt-2.5 border-t border-[var(--cp-border)] pt-2.5">
            <div class="flex items-center justify-between">
              <p class="cp-eyebrow !text-[8px]">{{ t('home.aiTeammates') }}</p>
              <button class="grid h-5 w-5 place-items-center rounded-md cp-muted transition hover:bg-[var(--cp-surface-soft)] hover:text-[var(--cp-text)]" title="Manage the team" aria-label="Manage the team" @click="emit('open-team')">
                <i class="fa-solid fa-plus text-[8px]"></i>
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
                  <span class="relative grid h-[31px] w-[31px] place-items-center overflow-hidden rounded-full border-2 border-[var(--cp-surface)] bg-[var(--cp-lime-soft)]">
                    <span class="absolute inset-0 grid place-items-center text-[8px] font-extrabold text-[var(--cp-accent-ink)]">{{ initials(person.name) }}</span>
                    <img :src="person.avatar" :alt="person.name" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                  </span>
                  <span class="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border-2 border-[var(--cp-surface)]" :class="person.kind === 'ai' ? 'bg-[var(--cp-lime)]' : 'bg-[var(--cp-green)]'"></span>
                </button>
              </div>
              <span v-else class="text-[8.5px] cp-muted">{{ t('home.noTeammates') }}</span>
              <button
                class="ml-auto grid h-7 w-7 place-items-center rounded-full border border-[var(--cp-border)] cp-muted transition hover:bg-[var(--cp-surface-soft)] hover:text-[var(--cp-text)]"
                title="Ask the team"
                aria-label="Ask the team"
                @click="ask('Give me a status update of the whole team')"
              >
                <i class="fa-solid fa-ellipsis text-[9px]"></i>
              </button>
            </div>
          </div>
        </section>

        <CompanyCalendar :snapshot="snapshot" @open="emit('open-calendar')" />

        <!-- Activity history -->
        <section class="cp-card cp-card-pad flex flex-col" aria-label="Company activity">
          <header class="flex items-center justify-between gap-2">
            <h2 class="cp-title">{{ t('home.activityHistory') }}</h2>
            <span class="cp-chip !h-6 !text-[8.5px]">7d</span>
          </header>

          <div class="-mx-1 mt-2 flex-1">
            <button
              v-for="event in activity"
              :key="event.id || `${event.at}-${event.type}`"
              type="button"
              class="cp-list-row"
              :title="event.message"
              @click="openEvent(event)"
            >
              <span class="cp-icon-tile !h-8 !w-8" :class="eventLook(event).tile">
                <i :class="eventIcon(event)" class="text-[9px]"></i>
              </span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-[9.5px] font-semibold">{{ event.message || event.type }}</span>
                <span class="mt-0.5 block truncate text-[8px] cp-muted">{{ event.actor || 'system' }}</span>
              </span>
              <span class="shrink-0 text-right">
                <span class="block text-[8px] font-bold" :style="{ color: eventLook(event).color }">{{ eventLook(event).label }}</span>
                <span class="mt-0.5 block text-[7px] cp-muted-soft">{{ relativeTime(event.at) }}</span>
              </span>
            </button>

            <div v-if="!activity.length" class="flex flex-col items-center justify-center px-3 py-8 text-center">
              <span class="cp-icon-tile !h-9 !w-9"><i class="fa-solid fa-wave-square text-[11px]"></i></span>
              <p class="mt-2 text-[9.5px] font-semibold">Nothing has happened yet.</p>
              <p class="mt-0.5 text-[8.5px] cp-muted">Create a goal and the company journal starts here.</p>
            </div>
          </div>

          <button
            type="button"
            class="mt-2 flex items-center justify-between border-t border-[var(--cp-border)] pt-2.5 text-[9px] font-semibold cp-muted transition hover:text-[var(--cp-text)]"
            @click="emit('open-activity')"
          >
            {{ t('home.allActivity') }}
            <i class="fa-solid fa-arrow-right text-[8px]"></i>
          </button>
        </section>

        <!-- One decision, surfaced -->
        <section v-if="attentionFocus" class="cp-card cp-card-pad" aria-label="Next decision">
          <div class="flex items-start gap-2.5">
            <span class="cp-icon-tile cp-icon-tile--yellow !h-8 !w-8">
              <i class="fa-solid fa-stamp text-[10px]"></i>
            </span>
            <div class="min-w-0 flex-1">
              <p class="cp-eyebrow !text-[8px]">Next decision</p>
              <p class="mt-1 truncate text-[10px] font-bold">{{ attentionFocus.title }}</p>
              <p class="mt-0.5 truncate text-[8.5px] cp-muted">{{ attentionLabel(attentionFocus.reason) }} · {{ attentionFocus.status }}</p>
            </div>
          </div>
          <div class="mt-2.5 flex gap-1.5">
            <button class="cp-cta flex-1 !h-8 !text-[9px]" @click="openAttention(attentionFocus)">Review now</button>
            <button class="cp-chip !h-8" title="Ask about this decision" aria-label="Ask about this decision" @click="ask(`Help me decide about ${attentionFocus.title}`)">
              <i class="fa-solid fa-wand-magic-sparkles text-[8px]"></i>
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
