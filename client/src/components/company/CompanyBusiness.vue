<script setup>
import { computed, reactive, ref } from 'vue'
import { useCompanyI18n } from './i18n'
import { API } from '@/api/api'
import { $storex } from '@/store'
import { DEAL_STAGES, dealStageMeta, formatDate, formatMoney } from './companyMeta'

/**
 * Business — the owner's commercial cockpit.
 *
 * Revenue, margin and pipeline are first-class company data, editable by the
 * owner. The view is deliberately a board, not an accounting table: every deal
 * has a next step and a probability, and the assistant can turn the gap into a
 * plan.
 */
const { t } = useCompanyI18n()

const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false }
})

const emit = defineEmits(['assistant', 'refresh'])

const api = API.companies
const saving = ref(false)
const targetOpen = ref(false)
const dealOpen = ref(false)
const metricOpen = ref(false)
const editingDealId = ref('')
const confirmDeal = ref(null)

const company = computed(() => props.snapshot?.company || {})
const members = computed(() => props.snapshot?.members || [])
const business = computed(() => company.value.business || {})
const currency = computed(() => business.value.currency || 'EUR')
const deals = computed(() => business.value.pipeline || [])
const metrics = computed(() => business.value.metrics || [])
const openStages = computed(() => DEAL_STAGES.filter(stage => !['won', 'lost'].includes(stage.id)))
const closedStages = computed(() => DEAL_STAGES.filter(stage => ['won', 'lost'].includes(stage.id)))

const revenue = computed(() => Number(business.value.revenue_actual || 0))
const target = computed(() => Number(business.value.revenue_target || 0))
const costs = computed(() => Number(business.value.costs_actual || 0))
const grossProfit = computed(() => revenue.value - costs.value)
const gap = computed(() => Math.max(0, target.value - revenue.value))
const progress = computed(() => target.value > 0 ? Math.min(100, Math.round((revenue.value / target.value) * 100)) : 0)
const pipelineValue = computed(() => deals.value.filter(deal => !['won', 'lost'].includes(deal.stage)).reduce((sum, deal) => sum + Number(deal.value || 0), 0))
const weightedPipeline = computed(() => deals.value.filter(deal => !['won', 'lost'].includes(deal.stage)).reduce((sum, deal) => sum + (Number(deal.value || 0) * Number(deal.probability || 0) / 100), 0))
const wonValue = computed(() => deals.value.filter(deal => deal.stage === 'won').reduce((sum, deal) => sum + Number(deal.value || 0), 0))

const overview = reactive({ revenue_target: 0, revenue_actual: 0, costs_actual: 0, currency: 'EUR', period: 'month', notes: '' })
const dealForm = reactive({ id: '', name: '', account: '', value: 0, currency: 'EUR', stage: 'lead', probability: 20, owner_kind: 'user', owner_name: '', next_step: '', due_date: '', notes: '' })
const metricForm = reactive({ id: '', label: '', value: 0, target: 0, unit: '', period: 'month' })

function newId(prefix = 'item') {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  } catch { /* fall through */ }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function notify(text, type = 'info') {
  try { $storex.ui.addNotification({ text, type }) } catch { /* best effort */ }
}

function errorMessage(error) {
  return error?.response?.data?.detail || error?.message || String(error)
}

function stageDeals(stage) {
  return deals.value.filter(deal => deal.stage === stage)
}

function ask(prompt) {
  emit('assistant', prompt)
}

function openTarget() {
  Object.assign(overview, {
    revenue_target: Number(business.value.revenue_target || 0),
    revenue_actual: Number(business.value.revenue_actual || 0),
    costs_actual: Number(business.value.costs_actual || 0),
    currency: business.value.currency || 'EUR',
    period: business.value.period || 'month',
    notes: business.value.notes || ''
  })
  targetOpen.value = true
}

function openDeal(deal = null, stage = '') {
  editingDealId.value = deal?.id || ''
  Object.assign(dealForm, {
    id: deal?.id || '',
    name: deal?.name || '',
    account: deal?.account || '',
    value: Number(deal?.value || 0),
    currency: deal?.currency || currency.value,
    stage: deal?.stage || stage || 'lead',
    probability: Number(deal?.probability ?? 20),
    owner_kind: deal?.owner_kind || 'user',
    owner_name: deal?.owner_name || members.value[0]?.username || '',
    next_step: deal?.next_step || '',
    due_date: (deal?.due_date || '').slice(0, 10),
    notes: deal?.notes || ''
  })
  dealOpen.value = true
}

function openMetric(metric = null) {
  Object.assign(metricForm, {
    id: metric?.id || '',
    label: metric?.label || '',
    value: Number(metric?.value || 0),
    target: Number(metric?.target || 0),
    unit: metric?.unit || '',
    period: metric?.period || 'month'
  })
  metricOpen.value = true
}

function businessPayload(patch = {}) {
  const base = {
    currency: business.value.currency || 'EUR',
    period: business.value.period || 'month',
    revenue_target: Number(business.value.revenue_target || 0),
    revenue_actual: Number(business.value.revenue_actual || 0),
    costs_actual: Number(business.value.costs_actual || 0),
    pipeline: deals.value.map(deal => ({ ...deal })),
    metrics: metrics.value.map(metric => ({ ...metric })),
    notes: business.value.notes || '',
    updated_at: new Date().toISOString()
  }
  return { ...base, ...patch }
}

async function persist(payload) {
  saving.value = true
  try {
    await api.update(props.companyId, { business: payload })
    emit('refresh')
    return true
  } catch (error) {
    notify(errorMessage(error), 'error')
    return false
  } finally {
    saving.value = false
  }
}

async function saveOverview() {
  const ok = await persist(businessPayload({
    revenue_target: Number(overview.revenue_target || 0),
    revenue_actual: Number(overview.revenue_actual || 0),
    costs_actual: Number(overview.costs_actual || 0),
    currency: (overview.currency || 'EUR').toUpperCase().slice(0, 3),
    period: overview.period,
    notes: overview.notes
  }))
  if (ok) {
    targetOpen.value = false
    notify('Business numbers updated ✓', 'success')
  }
}

async function saveDeal() {
  if (!dealForm.name.trim()) return
  const existing = deals.value.find(deal => deal.id === dealForm.id)
  const next = {
    ...(existing || {}),
    id: dealForm.id || newId('deal'),
    name: dealForm.name.trim(),
    account: dealForm.account.trim(),
    value: Number(dealForm.value || 0),
    currency: (dealForm.currency || currency.value).toUpperCase().slice(0, 3),
    stage: dealForm.stage,
    probability: Number(dealForm.probability || 0),
    owner_kind: dealForm.owner_kind,
    owner_name: dealForm.owner_name,
    next_step: dealForm.next_step.trim(),
    due_date: dealForm.due_date,
    notes: dealForm.notes.trim(),
    updated_at: new Date().toISOString()
  }
  const pipeline = existing
    ? deals.value.map(deal => deal.id === next.id ? next : deal)
    : [...deals.value, next]
  const ok = await persist(businessPayload({ pipeline }))
  if (ok) {
    dealOpen.value = false
    notify(existing ? 'Opportunity updated ✓' : 'Opportunity added ✓', 'success')
  }
}

function removeDeal(deal) {
  confirmDeal.value = deal
}

async function confirmRemoveDeal() {
  const deal = confirmDeal.value
  if (!deal) return
  const ok = await persist(businessPayload({ pipeline: deals.value.filter(item => item.id !== deal.id) }))
  if (ok) {
    notify('Opportunity removed', 'info')
    confirmDeal.value = null
  }
}

async function moveDeal(deal, stage) {
  const pipeline = deals.value.map(item => item.id === deal.id
    ? { ...item, stage, probability: stage === 'won' ? 100 : stage === 'lost' ? 0 : item.probability, updated_at: new Date().toISOString() }
    : item)
  await persist(businessPayload({ pipeline }))
}

async function saveMetric() {
  if (!metricForm.label.trim()) return
  const existing = metrics.value.find(metric => metric.id === metricForm.id)
  const next = {
    ...(existing || {}),
    id: metricForm.id || newId('metric'),
    label: metricForm.label.trim(),
    value: Number(metricForm.value || 0),
    target: Number(metricForm.target || 0),
    unit: metricForm.unit.trim(),
    period: metricForm.period,
    updated_at: new Date().toISOString()
  }
  const nextMetrics = existing
    ? metrics.value.map(metric => metric.id === next.id ? next : metric)
    : [...metrics.value, next]
  const ok = await persist(businessPayload({ metrics: nextMetrics }))
  if (ok) {
    metricOpen.value = false
    notify('Metric saved ✓', 'success')
  }
}

async function removeMetric(metric) {
  if (!metric?.id) return
  const ok = await persist(businessPayload({ metrics: metrics.value.filter(item => item.id !== metric.id) }))
  if (ok) notify('Metric removed', 'info')
}

async function removeCurrentMetric() {
  const metric = metrics.value.find(item => item.id === metricForm.id)
  await removeMetric(metric)
  metricOpen.value = false
}

function dealOwner(deal) {
  return deal.owner_name || 'Unassigned'
}

function metricProgress(metric) {
  if (!Number(metric.target || 0)) return 0
  return Math.min(100, Math.max(0, Math.round((Number(metric.value || 0) / Number(metric.target)) * 100)))
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="cp-eyebrow">Commercial</p>
        <h1 class="mt-1 text-2xl font-extrabold tracking-tight md:text-3xl">{{ t('nav.business') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          Revenue, margin and pipeline in one place. Ask the assistant to turn the gap into a plan the team can execute.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="cp-chip" :disabled="saving" @click="openTarget">
          <i class="fa-solid fa-bullseye text-[9px]"></i> {{ t('business.edit') }}
        </button>
        <button v-if="canManage" class="cp-cta" @click="openDeal()">
          <i class="fa-solid fa-handshake text-[9px]"></i> {{ t('business.add') }}
        </button>
      </div>
    </header>

    <!-- ── Revenue hero ──────────────────────────────────────────────────── -->
    <section class="grid gap-4 lg:grid-cols-12">
      <div class="cp-card relative overflow-hidden p-5 lg:col-span-8">
        <div class="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-[var(--cp-lime-soft)] blur-3xl"></div>
        <div class="relative">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p class="cp-eyebrow">Revenue this {{ business.period || 'month' }}</p>
              <p class="mt-1 text-3xl font-black tracking-tight md:text-4xl">{{ formatMoney(revenue, currency) }}</p>
              <p class="mt-1 text-xs text-base-content/50">
                of {{ formatMoney(target, currency) }} target
                <span v-if="gap > 0" class="text-warning"> · {{ formatMoney(gap, currency) }} to go</span>
                <span v-else class="text-success"> · target reached</span>
              </p>
            </div>
            <div class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] px-3 py-2 text-right">
              <p class="text-[9px] uppercase tracking-wide text-base-content/40">Attainment</p>
              <p class="text-2xl font-black" :class="progress >= 100 ? 'text-success' : progress >= 60 ? 'text-primary' : 'text-warning'">{{ progress }}%</p>
            </div>
          </div>

          <div class="mt-4 h-2.5 overflow-hidden rounded-full bg-[var(--cp-track)]">
            <div class="h-full rounded-full bg-[var(--cp-lime)] transition-all" :style="{ width: `${progress}%` }"></div>
          </div>

          <div class="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3">
              <p class="text-[9px] uppercase tracking-wide text-base-content/40">Gross profit</p>
              <p class="mt-1 text-sm font-black" :class="grossProfit >= 0 ? 'text-success' : 'text-error'">{{ formatMoney(grossProfit, currency, true) }}</p>
            </div>
            <div class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3">
              <p class="text-[9px] uppercase tracking-wide text-base-content/40">Costs</p>
              <p class="mt-1 text-sm font-black">{{ formatMoney(costs, currency, true) }}</p>
            </div>
            <div class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3">
              <p class="text-[9px] uppercase tracking-wide text-base-content/40">Open pipeline</p>
              <p class="mt-1 text-sm font-black">{{ formatMoney(pipelineValue, currency, true) }}</p>
            </div>
            <div class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3">
              <p class="text-[9px] uppercase tracking-wide text-base-content/40">Weighted pipeline</p>
              <p class="mt-1 text-sm font-black">{{ formatMoney(weightedPipeline, currency, true) }}</p>
            </div>
          </div>
        </div>
      </div>

      <div class="cp-card relative overflow-hidden p-5 lg:col-span-4">
        <div class="grid h-9 w-9 place-items-center rounded-xl bg-[var(--cp-lime)] text-[var(--cp-lime-ink)]">
          <i class="fa-solid fa-wand-magic-sparkles"></i>
        </div>
        <h2 class="mt-2.5 cp-title">Turn the gap into a plan</h2>
        <p class="mt-1 text-[11px] leading-relaxed text-base-content/55">
          The assistant sees revenue, margin, pipeline, team load and open work. Ask for a concrete path to the target — and assign it as goals.
        </p>
        <button class="cp-cta mt-3 w-full" @click="ask('We have a revenue gap of ' + gap + ' ' + currency + '. Build a concrete plan to close it this period using the current team and pipeline. Give me 3 actions with owners and deadlines.')">
          <i class="fa-solid fa-bolt"></i> Build the growth plan
        </button>
        <button class="cp-chip mt-1.5 w-full justify-center" @click="ask('Where are we losing money and what should I fix first?')">Where are we losing money?</button>
      </div>
    </section>

    <!-- ── Metrics ───────────────────────────────────────────────────────── -->
    <section class="cp-card cp-card-pad">
      <div class="flex items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-black">Business metrics</h2>
          <p class="mt-0.5 text-[11px] text-base-content/45">The numbers that tell you whether the company is healthy beyond revenue.</p>
        </div>
        <button class="btn btn-ghost btn-xs gap-1" @click="openMetric()"><i class="fa-solid fa-plus"></i> Add metric</button>
      </div>
      <div v-if="metrics.length" class="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div v-for="metric in metrics" :key="metric.id" class="group rounded-xl border border-base-content/10 p-3">
          <div class="flex items-start justify-between gap-2">
            <p class="truncate text-[10px] font-bold uppercase tracking-wide text-base-content/45">{{ metric.label }}</p>
            <button class="opacity-0 transition group-hover:opacity-100" @click="openMetric(metric)"><i class="fa-solid fa-pen text-[9px] text-base-content/40"></i></button>
          </div>
          <p class="mt-1 text-lg font-black">{{ Number(metric.value || 0).toLocaleString() }}<span class="ml-1 text-[10px] font-normal text-base-content/40">{{ metric.unit }}</span></p>
          <p v-if="Number(metric.target || 0)" class="text-[10px] text-base-content/40">target {{ Number(metric.target).toLocaleString() }} {{ metric.unit }}</p>
          <div class="mt-2 h-1 overflow-hidden rounded-full bg-base-content/10">
            <div class="h-full rounded-full bg-primary" :style="{ width: `${metricProgress(metric)}%` }"></div>
          </div>
        </div>
      </div>
      <div v-else class="mt-3 rounded-xl border border-dashed border-base-content/15 p-4 text-center">
        <p class="text-[11px] text-base-content/45">No metrics yet. Add the 3 numbers you check every week (MRR, customers, churn, margin…).</p>
      </div>
    </section>

    <!-- ── Pipeline board ────────────────────────────────────────────────── -->
    <section class="cp-card cp-card-pad">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-black">Pipeline</h2>
          <p class="mt-0.5 text-[11px] text-base-content/45">Every opportunity has a value, a probability and a next step. The weighted value is what you can plan with.</p>
        </div>
        <div class="flex items-center gap-2">
          <span class="badge badge-sm badge-ghost">{{ deals.length }} opportunities</span>
          <button class="btn btn-ghost btn-xs gap-1" @click="ask('Which deals deserve attention this week and why?')"><i class="fa-solid fa-wand-magic-sparkles"></i> Ask AI</button>
        </div>
      </div>

      <div class="mt-4">
        <div class="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          <div v-for="stage in openStages" :key="stage.id" class="rounded-xl bg-base-200/35 p-2">
            <div class="mb-2 flex items-center justify-between px-1">
              <span class="text-[10px] font-bold uppercase tracking-[0.14em]" :class="stage.tone">{{ stage.label }}</span>
              <span class="text-[10px] text-base-content/35">{{ stageDeals(stage.id).length }}</span>
            </div>
            <div class="space-y-2">
              <div v-for="deal in stageDeals(stage.id)" :key="deal.id"
                   class="group rounded-xl border border-base-content/10 bg-base-100 p-2.5 shadow-sm transition hover:border-primary/40">
                <div class="flex items-start justify-between gap-1">
                  <button class="min-w-0 text-left" @click="openDeal(deal)">
                    <p class="truncate text-[11px] font-bold">{{ deal.name }}</p>
                    <p class="truncate text-[9px] text-base-content/40">{{ deal.account || 'No account' }}</p>
                  </button>
                  <button class="opacity-0 transition group-hover:opacity-100" title="Remove" @click="removeDeal(deal)"><i class="fa-solid fa-xmark text-[9px] text-base-content/30"></i></button>
                </div>
                <p class="mt-1.5 text-xs font-black">{{ formatMoney(deal.value, deal.currency || currency, true) }}</p>
                <div class="mt-1 flex items-center gap-1 text-[9px] text-base-content/40">
                  <span class="badge badge-xs badge-ghost">{{ deal.probability }}%</span>
                  <span class="truncate">{{ dealOwner(deal) }}</span>
                </div>
                <p v-if="deal.next_step" class="mt-1.5 line-clamp-2 rounded-md bg-base-200/50 px-1.5 py-1 text-[9px] leading-snug text-base-content/55">{{ deal.next_step }}</p>
                <div class="mt-1.5 flex items-center justify-between">
                  <span class="text-[8px] text-base-content/35">{{ deal.due_date ? formatDate(deal.due_date) : 'No date' }}</span>
                  <div class="flex gap-0.5 opacity-0 transition group-hover:opacity-100">
                    <button class="btn btn-ghost btn-xs btn-square" title="Mark won" @click="moveDeal(deal, 'won')"><i class="fa-solid fa-check text-[8px] text-success"></i></button>
                    <button class="btn btn-ghost btn-xs btn-square" title="Mark lost" @click="moveDeal(deal, 'lost')"><i class="fa-solid fa-xmark text-[8px] text-error"></i></button>
                  </div>
                </div>
              </div>
              <button class="w-full rounded-lg border border-dashed border-base-content/15 p-2 text-[9px] text-base-content/35 hover:border-primary/40 hover:text-primary" @click="openDeal(null, stage.id)">+ Opportunity</button>
            </div>
          </div>
        </div>
      </div>

      <div class="mt-3 grid gap-2 sm:grid-cols-2">
        <div v-for="stage in closedStages" :key="stage.id" class="rounded-xl border border-base-content/10 p-3">
          <div class="flex items-center justify-between">
            <span class="text-[10px] font-bold uppercase tracking-wide" :class="stage.tone">{{ stage.label }}</span>
            <span class="text-xs font-black">{{ formatMoney(stageDeals(stage.id).reduce((sum, deal) => sum + Number(deal.value || 0), 0), currency, true) }}</span>
          </div>
          <p class="mt-1 text-[10px] text-base-content/40">{{ stageDeals(stage.id).length }} deal(s)</p>
        </div>
      </div>
    </section>

    <!-- ── Target modal ──────────────────────────────────────────────────── -->
    <div v-if="targetOpen" class="modal modal-open z-[90]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-lg border border-base-content/10 bg-base-100">
        <h3 class="text-base font-black">Business numbers</h3>
        <p class="mt-1 text-[11px] text-base-content/50">The owner's cockpit, not the accounting ledger. Update it whenever you have a new number.</p>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Currency</span>
            <input v-model="overview.currency" class="input input-sm input-bordered mt-1" maxlength="3" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Period</span>
            <select v-model="overview.period" class="select select-sm select-bordered mt-1">
              <option value="month">Month</option>
              <option value="quarter">Quarter</option>
              <option value="year">Year</option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Revenue target</span>
            <input v-model.number="overview.revenue_target" type="number" min="0" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Revenue booked</span>
            <input v-model.number="overview.revenue_actual" type="number" min="0" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Costs booked</span>
            <input v-model.number="overview.costs_actual" type="number" min="0" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control sm:col-span-2">
            <span class="label-text text-[10px] uppercase text-base-content/45">Owner notes / strategy</span>
            <textarea v-model="overview.notes" class="textarea textarea-sm textarea-bordered mt-1" rows="3" placeholder="What is the commercial focus this period?"></textarea>
          </label>
        </div>
        <div class="modal-action">
          <button class="btn btn-ghost btn-sm" @click="targetOpen = false">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="saving" @click="saveOverview"><span v-if="saving" class="loading loading-spinner loading-xs"></span>Save</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="targetOpen = false"></div>
    </div>

    <!-- ── Deal modal ────────────────────────────────────────────────────── -->
    <div v-if="dealOpen" class="modal modal-open z-[90]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-xl border border-base-content/10 bg-base-100">
        <h3 class="text-base font-black">{{ dealForm.id ? 'Edit opportunity' : 'New opportunity' }}</h3>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="form-control sm:col-span-2">
            <span class="label-text text-[10px] uppercase text-base-content/45">Opportunity</span>
            <input v-model="dealForm.name" class="input input-sm input-bordered mt-1" placeholder="Website redesign for Acme" autofocus />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Account</span>
            <input v-model="dealForm.account" class="input input-sm input-bordered mt-1" placeholder="Acme" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Owner</span>
            <select v-model="dealForm.owner_name" class="select select-sm select-bordered mt-1">
              <option value="">Unassigned</option>
              <option v-for="member in members" :key="member.username" :value="member.username">{{ member.username }}</option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Value</span>
            <input v-model.number="dealForm.value" type="number" min="0" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Currency</span>
            <input v-model="dealForm.currency" class="input input-sm input-bordered mt-1" maxlength="3" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Stage</span>
            <select v-model="dealForm.stage" class="select select-sm select-bordered mt-1">
              <option v-for="stage in DEAL_STAGES" :key="stage.id" :value="stage.id">{{ stage.label }}</option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Win probability %</span>
            <input v-model.number="dealForm.probability" type="number" min="0" max="100" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control sm:col-span-2">
            <span class="label-text text-[10px] uppercase text-base-content/45">Next step</span>
            <input v-model="dealForm.next_step" class="input input-sm input-bordered mt-1" placeholder="Send the proposal and book a call" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Expected close</span>
            <input v-model="dealForm.due_date" type="date" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control sm:col-span-2">
            <span class="label-text text-[10px] uppercase text-base-content/45">Notes</span>
            <textarea v-model="dealForm.notes" class="textarea textarea-sm textarea-bordered mt-1" rows="2"></textarea>
          </label>
        </div>
        <div class="modal-action">
          <button class="btn btn-ghost btn-sm" @click="dealOpen = false">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="saving" @click="saveDeal"><span v-if="saving" class="loading loading-spinner loading-xs"></span>Save opportunity</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="dealOpen = false"></div>
    </div>

    <!-- ── Remove opportunity confirmation ─────────────────────────────── -->
    <div v-if="confirmDeal" class="modal modal-open z-[95]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-sm border border-base-content/10 bg-base-100">
        <h3 class="text-base font-black">Remove opportunity?</h3>
        <p class="mt-1 text-[11px] text-base-content/55">
          <strong>{{ confirmDeal.name }}</strong> will be removed from the pipeline. The rest of the business data is kept.
        </p>
        <div class="modal-action">
          <button class="btn btn-ghost btn-sm" @click="confirmDeal = null">Keep it</button>
          <button class="btn btn-error btn-sm" :disabled="saving" @click="confirmRemoveDeal">Remove</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="confirmDeal = null"></div>
    </div>

    <!-- ── Metric modal ──────────────────────────────────────────────────── -->
    <div v-if="metricOpen" class="modal modal-open z-[90]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-md border border-base-content/10 bg-base-100">
        <h3 class="text-base font-black">{{ metricForm.id ? 'Edit metric' : 'New metric' }}</h3>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="form-control sm:col-span-2">
            <span class="label-text text-[10px] uppercase text-base-content/45">Name</span>
            <input v-model="metricForm.label" class="input input-sm input-bordered mt-1" placeholder="Recurring revenue" autofocus />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Value</span>
            <input v-model.number="metricForm.value" type="number" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Target</span>
            <input v-model.number="metricForm.target" type="number" class="input input-sm input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Unit</span>
            <input v-model="metricForm.unit" class="input input-sm input-bordered mt-1" placeholder="€, %, customers" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px] uppercase text-base-content/45">Period</span>
            <select v-model="metricForm.period" class="select select-sm select-bordered mt-1">
              <option value="month">Month</option>
              <option value="quarter">Quarter</option>
              <option value="year">Year</option>
            </select>
          </label>
        </div>
        <div class="modal-action">
          <button v-if="metricForm.id" class="btn btn-ghost btn-sm text-error mr-auto" @click="removeCurrentMetric">Delete</button>
          <button class="btn btn-ghost btn-sm" @click="metricOpen = false">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="saving" @click="saveMetric">Save</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="metricOpen = false"></div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
