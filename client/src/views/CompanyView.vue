<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { API } from '@/api/api'
import { $storex } from '@/store'

import CompanyHome from '@/components/company/CompanyHome.vue'
import CompanyWork from '@/components/company/CompanyWork.vue'
import CompanyTeams from '@/components/company/CompanyTeams.vue'
import CompanyPeople from '@/components/company/CompanyPeople.vue'
import CompanyBusiness from '@/components/company/CompanyBusiness.vue'
import CompanyActivity from '@/components/company/CompanyActivity.vue'
import CompanySecurity from '@/components/company/CompanySecurity.vue'
import CompanySettings from '@/components/company/CompanySettings.vue'
import CompanyAssistant from '@/components/company/CompanyAssistant.vue'
import CompanyChatPanel from '@/components/company/CompanyChatPanel.vue'
import CompanyGoalDialog from '@/components/company/CompanyGoalDialog.vue'
import GenerateTeamDialog from '@/components/teams/GenerateTeamDialog.vue'
import { COMPANY_TEMPLATES } from '@/components/company/companyMeta'
import '@/assets/company.css'

/**
 * Company shell — the AI company operating system.
 *
 * A business owner gets one place to see the truth, decide, talk to the team
 * and grow revenue. The assistant is always one click away; every workspace is
 * one click away and every number is live.
 *
 * Presentation only: the data flow, routing and actions are unchanged. The
 * shell owns a small design system (see assets/company.css) with its own
 * light/dark palette so the company workspace keeps its identity even when the
 * rest of the app uses another daisyUI theme.
 */
const route = useRoute()
const router = useRouter()
const api = API.companies

const STORAGE_KEY = 'codx.company.id'
const SECTION_KEY = 'codx.company.section'
const THEME_KEY = 'codx.company.theme'
const SIDEBAR_KEY = 'codx.company.sidebar'

const loading = ref(true)
const busy = ref(false)
const companies = ref([])
const activeId = ref('')
const snapshot = ref(null)
const references = ref({ projects: [], users: [], profiles: [], templates: [] })
const companyActors = ref({ actors: [], counts: {} })
const section = ref('home')
const navOpen = ref(false)
const exitOpen = ref(false)
const askText = ref('')

const createOpen = ref(false)
const createAdvanced = ref(false)
const chatsOpen = ref(false)
const chatTeamId = ref('')
const generateOpen = ref(false)
const goalOpen = ref(false)
const goalTeamId = ref('')
const goalMission = ref(null)
const workFocusId = ref('')
const teamFocusId = ref('')

// ── Workspace chrome ───────────────────────────────────────────────────────
// The company workspace is a dedicated surface with its own palette (see
// assets/company.css). It defaults to the design's dark theme and remembers
// the user's explicit light/dark choice locally; it deliberately does not follow
// the app-wide daisyUI theme, which would flip the design mid-session.

function initialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* storage may be unavailable */ }
  return 'dark'
}

const companyTheme = ref(initialTheme())
const isDarkTheme = computed(() => companyTheme.value === 'dark')
const sidebarCollapsed = ref(false)
try { sidebarCollapsed.value = localStorage.getItem(SIDEBAR_KEY) === '1' } catch { /* noop */ }

function toggleCompanyTheme() {
  companyTheme.value = isDarkTheme.value ? 'light' : 'dark'
  try { localStorage.setItem(THEME_KEY, companyTheme.value) } catch { /* noop */ }
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try { localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed.value ? '1' : '0') } catch { /* noop */ }
}

const isMobile = computed(() => $storex.ui.isMobile)
const currentUser = computed(() => $storex.users.user)
const userLabel = computed(() => currentUser.value?.name || currentUser.value?.username || 'Owner')
const userInitials = computed(() => initials(userLabel.value))
const userAvatar = computed(() => currentUser.value?.avatar || '')

const createForm = reactive({
  name: '',
  description: '',
  mission: '',
  template: 'blank',
  project_ids: []
})

const company = computed(() => snapshot.value?.company || null)
const counts = computed(() => snapshot.value?.counts || {})
const attention = computed(() => snapshot.value?.attention || [])
const reviewCount = computed(() => (snapshot.value?.missions || []).filter(mission => mission.status === 'review').length)
const activeActors = computed(() => (companyActors.value.actors || []).filter(actor => actor?.config?.enabled))
const aiCount = computed(() => counts.value.ai || 0)

const SECTIONS = [
  { id: 'home', label: 'Overview', icon: 'fa-solid fa-border-all', hint: 'The state of the company' },
  { id: 'work', label: 'Goals & work', icon: 'fa-solid fa-flag', hint: 'Goals, delivery, review' },
  { id: 'teams', label: 'Teams', icon: 'fa-solid fa-people-group', hint: 'Independent units' },
  { id: 'people', label: 'People', icon: 'fa-solid fa-user-group', hint: 'Humans & AI' },
  { id: 'business', label: 'Business', icon: 'fa-solid fa-chart-line', hint: 'Revenue, margin, pipeline' },
  { id: 'activity', label: 'Activity', icon: 'fa-solid fa-wave-square', hint: 'What happened' },
  { id: 'security', label: 'Security', icon: 'fa-solid fa-shield-halved', hint: 'CISO posture' },
  { id: 'settings', label: 'Settings', icon: 'fa-solid fa-sliders', hint: 'Company & controls' }
]

const NAV_GROUPS = [
  { id: 'workspace', label: 'Workspace', ids: ['home', 'work', 'teams', 'people'] },
  { id: 'business', label: 'Business', ids: ['business', 'activity'] },
  { id: 'governance', label: 'Governance', ids: ['security', 'settings'] }
]

const activeSection = computed(() => SECTIONS.find(item => item.id === section.value) || SECTIONS[0])
const myCompanyRole = computed(() => {
  if ($storex.users.isAdmin) return 'admin'
  if (!company.value?.owner) return 'admin'
  if (company.value.owner === $storex.api.user?.username) return 'admin'
  return $storex.users.companyRole(activeId.value) || ''
})
const canAdmin = computed(() => myCompanyRole.value === 'admin')
const canManage = computed(() => ['admin', 'manager'].includes(myCompanyRole.value))
const ownerMember = computed(() => (snapshot.value?.members || []).find(member => member.username === company.value?.owner))
const ownerLabel = computed(() => ownerMember.value?.title || company.value?.owner || 'Owner')
const ownerAvatar = computed(() => ownerMember.value?.avatar || userAvatar.value)
const roleLabel = computed(() => {
  if (canAdmin.value) return 'Owner'
  if (myCompanyRole.value === 'manager') return 'Manager'
  return 'Member'
})
const workingProject = computed(() => $storex.projects.activeProject || null)
const companyInitials = computed(() => initials(company.value?.name || 'Company'))

function initials(name) {
  return String(name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0].toUpperCase())
    .join('')
}

function hideBrokenImage(event) {
  if (event?.target) event.target.style.display = 'none'
}

const navItems = computed(() => SECTIONS.map(item => ({
  ...item,
  badge: item.id === 'work'
    ? attention.value.length
    : item.id === 'teams'
      ? counts.value.teams || 0
      : item.id === 'people'
        ? (counts.value.human || counts.value.humans || 0) + aiCount.value
        : ''
})))

const navGroups = computed(() => NAV_GROUPS
  .map(group => ({ ...group, items: navItems.value.filter(item => group.ids.includes(item.id)) }))
  .filter(group => group.items.length))

function notify(text, type = 'info') {
  try { $storex.ui.addNotification({ text, type }) } catch { /* best effort */ }
}

function errorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || String(error)
}

function notifyError(error) {
  notify(errorMessage(error), 'error')
}

// ── Loading ────────────────────────────────────────────────────────────────

async function loadCompanies() {
  companies.value = await api.list()
  if (!companies.value.length) {
    activeId.value = ''
    snapshot.value = null
    return
  }
  const routeCompany = String(route.query.company || route.params.companyId || '')
  const stored = localStorage.getItem(STORAGE_KEY) || ''
  const candidate = companies.value.find(item => item.id === routeCompany)
    || companies.value.find(item => item.id === stored)
    || companies.value[0]
  await selectCompany(candidate.id, { skipList: true })
}

async function loadReferences() {
  try {
    references.value = await api.references()
  } catch (error) {
    notifyError(error)
  }
}

async function loadCompanyActors(companyId) {
  if (!companyId) {
    companyActors.value = { actors: [], counts: {} }
    return
  }
  try {
    companyActors.value = await api.actors(companyId)
  } catch (error) {
    console.warn('[CompanyView] cannot load company actors', error)
    companyActors.value = { actors: [], counts: {} }
  }
}

async function refreshSnapshot() {
  if (!activeId.value) return
  try {
    snapshot.value = await api.snapshot(activeId.value)
    await loadCompanyActors(activeId.value)
  } catch (error) {
    notifyError(error)
  }
}

async function selectCompany(companyId, { skipList = false } = {}) {
  if (!companyId) return
  activeId.value = companyId
  localStorage.setItem(STORAGE_KEY, companyId)
  busy.value = true
  try {
    if (!skipList) companies.value = await api.list()
    snapshot.value = await api.snapshot(companyId)
    await loadCompanyActors(companyId)
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

function setSection(id, { updateRoute = true } = {}) {
  const next = SECTIONS.some(item => item.id === id) ? id : 'home'
  section.value = next
  localStorage.setItem(SECTION_KEY, next)
  navOpen.value = false
  if (updateRoute) {
    const query = { ...route.query, section: next }
    delete query.tab
    router.replace({ query }).catch(() => {})
  }
}

// ── Assistant ──────────────────────────────────────────────────────────────

function askAssistant(prompt = '') {
  window.dispatchEvent(new CustomEvent('company-assistant:open', { detail: { prompt } }))
}

function sendAsk() {
  const text = askText.value.trim()
  if (!text) return
  askText.value = ''
  askAssistant(text)
}

function openChat(teamId = '') {
  chatTeamId.value = teamId || ''
  chatsOpen.value = true
}

function openGoal(teamId = '', mission = null) {
  goalTeamId.value = teamId || ''
  goalMission.value = mission || null
  goalOpen.value = true
}

function onGoalSaved() {
  refreshSnapshot()
  notify('Goal saved ✓', 'success')
}

function onWorkFocus(id) {
  workFocusId.value = id || ''
  setSection('work')
}

function onTeamFocus(id) {
  teamFocusId.value = id || ''
  setSection('teams')
}

function openAssistantFor(prompt) {
  askAssistant(prompt)
}

// ── Company onboarding ─────────────────────────────────────────────────────

function openCreate() {
  Object.assign(createForm, {
    name: '',
    description: '',
    mission: '',
    template: 'blank',
    project_ids: []
  })
  createAdvanced.value = false
  createOpen.value = true
}

function toggleCreateProject(projectId) {
  const index = createForm.project_ids.indexOf(projectId)
  if (index === -1) createForm.project_ids.push(projectId)
  else createForm.project_ids.splice(index, 1)
}

async function submitCreate() {
  if (!createForm.name.trim()) {
    notify('Please give the company a name', 'error')
    return
  }
  busy.value = true
  try {
    const created = await api.create({
      name: createForm.name.trim(),
      description: createForm.description,
      mission: createForm.mission,
      template: createForm.template,
      project_ids: createForm.project_ids
    })
    createOpen.value = false
    companies.value = await api.list()
    await selectCompany(created.id, { skipList: true })
    setSection('home')
    notify(`Company "${created.name}" created ✓`, 'success')
    nextTick(() => askAssistant('What should we do first to get this company moving?'))
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function onCompanyDeleted() {
  snapshot.value = null
  activeId.value = ''
  notify('Company deleted', 'info')
  await loadCompanies()
}

async function leaveCompany(mode) {
  exitOpen.value = false
  try {
    // Leave the company shell explicitly. Without this preference the app
    // would route the next page back into company mode.
    await $storex.users.setWorkspaceMode('work')
  } catch (error) {
    // The destination is still useful even if the preference cannot persist.
  }
  if (mode === 'simple') {
    router.push({ name: 'solopreneur' }).catch(() => {})
    return
  }
  const project = workingProject.value
  router.push(project ? `/${project.project_name || project.project_id}` : '/').catch(() => {})
}

// ── Lifecycle ──────────────────────────────────────────────────────────────

onMounted(async () => {
  loading.value = true
  try {
    await loadReferences()
    await loadCompanies()
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
    const requested = String(route.query.section || route.query.tab || localStorage.getItem(SECTION_KEY) || 'home')
    setSection(legacySection(requested), { updateRoute: false })
    const intent = route.query.assistant
    if (intent) {
      nextTick(() => askAssistant(String(intent)))
      const rest = { ...route.query }
      delete rest.assistant
      delete rest.tab
      router.replace({ query: rest }).catch(() => {})
    }
  }
})

function legacySection(value) {
  const map = {
    teams: 'teams',
    people: 'people',
    organization: 'teams',
    projects: 'home',
    activity: 'activity',
    missions: 'work',
    overview: 'home',
    work: 'work',
    business: 'business',
    settings: 'settings',
    home: 'home'
  }
  return map[String(value || '')] || 'home'
}

watch(() => route.query.section, value => {
  if (value && value !== section.value) setSection(legacySection(value), { updateRoute: false })
})

watch(() => route.query.assistant, value => {
  if (value) {
    askAssistant(String(value))
    const rest = { ...route.query }
    delete rest.assistant
    router.replace({ query: rest }).catch(() => {})
  }
})
</script>

<template>
  <div class="company-shell flex h-full w-full overflow-hidden" :data-company-theme="companyTheme">
    <div class="flex h-full w-full gap-0 p-0 md:gap-3 md:p-3 xl:gap-4 xl:p-4">
      <!-- ── Sidebar ──────────────────────────────────────────────────────── -->
      <aside
        v-if="!isMobile"
        class="cp-sidebar cp-scroll flex shrink-0 flex-col overflow-y-auto overflow-x-hidden transition-[width] duration-200 ease-out"
        :style="{ width: sidebarCollapsed ? '76px' : '236px' }"
        aria-label="Company navigation"
      >
        <div class="flex items-center gap-2.5 px-3.5 pb-2 pt-4">
          <button type="button" class="shrink-0 transition hover:scale-[1.03]" title="Company overview" @click="setSection('home')">
            <span class="grid h-9 w-9 place-items-center rounded-[11px] bg-[var(--cp-lime)] text-[13px] font-black text-[var(--cp-lime-ink)] shadow-sm">
              <i v-if="company?.logo" :class="company.logo"></i>
              <span v-else>{{ companyInitials }}</span>
            </span>
          </button>
          <div v-if="!sidebarCollapsed" class="min-w-0 flex-1">
            <p class="truncate text-[13px] font-extrabold leading-tight tracking-tight">{{ company?.name || 'Your company' }}</p>
            <p class="mt-0.5 truncate text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">AI company OS</p>
          </div>
          <button
            v-if="!sidebarCollapsed"
            class="cp-icon-btn !h-7 !w-7"
            title="New company"
            aria-label="New company"
            @click="openCreate"
          >
            <i class="fa-solid fa-plus text-[10px]"></i>
          </button>
        </div>

        <div v-if="companies.length > 1 && !sidebarCollapsed" class="px-3 pb-1.5">
          <label class="relative block">
            <select
              class="w-full appearance-none rounded-[9px] border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] py-1.5 pl-2.5 pr-7 text-[10.5px] font-semibold text-[var(--cp-text)] outline-none transition hover:border-[var(--cp-border-strong)]"
              :value="activeId"
              aria-label="Select company"
              @change="selectCompany($event.target.value)"
            >
              <option v-for="item in companies" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
            <i class="fa-solid fa-chevron-down pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[8px] text-[var(--cp-muted-soft)]"></i>
          </label>
        </div>

        <nav class="cp-scroll flex-1 overflow-y-auto px-2.5 pb-1 pt-1">
          <template v-for="group in navGroups" :key="group.id">
            <p
              v-if="!sidebarCollapsed"
              class="px-2 pb-1.5 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]"
            >
              {{ group.label }}
            </p>
            <div v-else class="mx-2 my-2 border-t border-[var(--cp-border)] first:mt-1"></div>
            <button
              v-for="item in group.items"
              :key="item.id"
              type="button"
              class="cp-nav-item mb-0.5"
              :class="[section === item.id ? 'is-active' : '', sidebarCollapsed ? 'justify-center' : '']"
              :title="sidebarCollapsed ? item.label : item.hint"
              :aria-current="section === item.id ? 'page' : undefined"
              @click="setSection(item.id)"
            >
              <span class="cp-nav-icon"><i :class="item.icon" class="text-[11px]"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
              <span
                v-if="item.badge"
                class="cp-nav-badge"
                :class="[
                  item.id === 'work' && attention.length ? '!bg-[var(--cp-yellow-soft)] !text-[var(--cp-secondary-ink)]' : '',
                  sidebarCollapsed ? '!absolute !right-0.5 !top-0.5 !h-4 !min-w-4 !px-1 !text-[7px]' : ''
                ]"
              >
                {{ item.badge }}
              </span>
            </button>
          </template>
        </nav>

        <div class="mt-auto space-y-2 px-2.5 pb-3 pt-2">
          <div v-if="!sidebarCollapsed" class="cp-promo">
            <div class="flex items-start justify-between">
              <span class="cp-promo-badge"><i class="fa-solid fa-bolt text-[9px]"></i></span>
              <span class="rounded-full border border-white/10 px-1.5 py-0.5 text-[7.5px] font-bold uppercase tracking-[0.14em] text-white/45">Company AI</span>
            </div>
            <p class="mt-2.5 text-[12px] font-bold leading-snug">Put your AI team to work</p>
            <p class="mt-1 text-[9.5px] leading-relaxed text-white/45">
              {{ activeActors.length }} teammate{{ activeActors.length === 1 ? '' : 's' }} on duty · {{ counts.teams || 0 }} teams
            </p>
            <button type="button" class="cp-promo-action mt-2.5" @click="askAssistant('Give me today\'s executive brief')">
              <i class="fa-solid fa-wand-magic-sparkles text-[9px]"></i>
              Ask codx-junior
            </button>
          </div>
          <button
            v-else
            type="button"
            class="cp-promo-badge mx-auto !h-8 !w-8"
            title="Ask codx-junior"
            aria-label="Ask codx-junior"
            @click="askAssistant('Give me today\'s executive brief')"
          >
            <i class="fa-solid fa-bolt text-[10px]"></i>
          </button>

          <button
            type="button"
            class="cp-nav-item !py-1.5"
            :class="sidebarCollapsed ? 'justify-center' : ''"
            :title="sidebarCollapsed ? 'Exit company' : 'Exit company'"
            @click="exitOpen = true"
          >
            <i class="fa-solid fa-arrow-right-from-bracket w-4 text-center text-[10px]"></i>
            <span v-if="!sidebarCollapsed" class="text-[10px]">Exit company</span>
          </button>

          <div class="flex items-center gap-2 rounded-xl px-1 py-1">
            <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-[var(--cp-lime-soft)]">
              <span class="absolute inset-0 grid place-items-center text-[9px] font-extrabold text-[var(--cp-accent-ink)]">{{ initials(ownerLabel) }}</span>
              <img v-if="ownerAvatar" :src="ownerAvatar" :alt="ownerLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
            </span>
            <div v-if="!sidebarCollapsed" class="min-w-0 flex-1">
              <p class="truncate text-[10.5px] font-bold leading-tight">{{ ownerLabel }}</p>
              <p class="mt-0.5 text-[8.5px] text-[var(--cp-muted-soft)]">{{ roleLabel }} · full control</p>
            </div>
          </div>

          <button
            type="button"
            class="cp-nav-item !py-1.5"
            :class="sidebarCollapsed ? 'justify-center' : ''"
            :title="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            @click="toggleSidebar"
          >
            <i :class="sidebarCollapsed ? 'fa-solid fa-angles-right' : 'fa-solid fa-angles-left'" class="w-4 text-center text-[10px]"></i>
            <span v-if="!sidebarCollapsed" class="text-[10px]">Collapse sidebar</span>
          </button>
        </div>
      </aside>

      <!-- Mobile nav overlay -->
      <div v-if="navOpen && isMobile" class="fixed inset-0 z-[60]">
        <div class="absolute inset-0 bg-black/45 backdrop-blur-sm" @click="navOpen = false"></div>
        <aside class="relative flex h-full w-[86%] max-w-[310px] flex-col border-r border-[var(--cp-border)] bg-[var(--cp-sidebar)] p-3 shadow-2xl">
          <div class="flex items-center gap-2.5 pb-2">
            <span class="grid h-9 w-9 place-items-center rounded-[11px] bg-[var(--cp-lime)] text-[13px] font-black text-[var(--cp-lime-ink)]">
              <i v-if="company?.logo" :class="company.logo"></i>
              <span v-else>{{ companyInitials }}</span>
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-[13px] font-extrabold">{{ company?.name || 'Company' }}</p>
              <p class="text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">AI company OS</p>
            </div>
            <button class="cp-icon-btn" aria-label="Close menu" @click="navOpen = false"><i class="fa-solid fa-xmark text-[13px]"></i></button>
          </div>
          <div v-if="companies.length > 1" class="pb-2">
            <select
              class="w-full rounded-[9px] border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] px-2.5 py-1.5 text-[11px] font-semibold outline-none"
              :value="activeId"
              aria-label="Select company"
              @change="selectCompany($event.target.value)"
            >
              <option v-for="item in companies" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
          </div>
          <nav class="cp-scroll flex-1 overflow-y-auto">
            <template v-for="group in navGroups" :key="group.id">
              <p class="px-2 pb-1 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">{{ group.label }}</p>
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="cp-nav-item mb-0.5"
                :class="section === item.id ? 'is-active' : ''"
                @click="setSection(item.id)"
              >
                <span class="cp-nav-icon"><i :class="item.icon" class="text-[11px]"></i></span>
                <span class="flex-1 text-[12px]">{{ item.label }}</span>
                <span v-if="item.badge" class="cp-nav-badge">{{ item.badge }}</span>
              </button>
            </template>
          </nav>
          <div class="mt-2 space-y-1 border-t border-[var(--cp-border)] pt-3">
            <button type="button" class="cp-nav-item" @click="toggleCompanyTheme">
              <span class="cp-nav-icon"><i :class="isDarkTheme ? 'fa-solid fa-sun' : 'fa-solid fa-moon'" class="text-[11px]"></i></span>
              <span class="flex-1 text-[12px]">{{ isDarkTheme ? 'Light mode' : 'Dark mode' }}</span>
            </button>
            <button type="button" class="cp-nav-item" @click="exitOpen = true; navOpen = false">
              <span class="cp-nav-icon"><i class="fa-solid fa-arrow-right-from-bracket text-[11px]"></i></span>
              <span class="flex-1 text-[12px]">Exit company</span>
            </button>
            <button type="button" class="cp-nav-item" @click="navOpen = false; openCreate()">
              <span class="cp-nav-icon"><i class="fa-solid fa-plus text-[11px]"></i></span>
              <span class="flex-1 text-[12px]">New company</span>
            </button>
            <button type="button" class="cp-cta-dark mt-2 w-full" @click="askAssistant('Give me today\'s executive brief')">
              <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i> Ask codx-junior
            </button>
          </div>
        </aside>
      </div>

      <!-- ── Main column ──────────────────────────────────────────────────── -->
      <div class="cp-canvas flex min-w-0 flex-1 flex-col overflow-hidden max-md:rounded-none md:rounded-[22px] md:border md:border-[var(--cp-border)]">
        <header class="cp-topbar shrink-0">
          <div class="flex h-[60px] items-center gap-2 px-3 md:h-[64px] md:px-5">
            <button v-if="isMobile" class="cp-icon-btn" aria-label="Open menu" @click="navOpen = true">
              <i class="fa-solid fa-bars-staggered text-[13px]"></i>
            </button>
            <div class="flex min-w-0 flex-1 items-center">
              <label class="cp-search w-full" :class="isMobile ? 'max-w-none' : 'max-w-[360px]'">
                <i class="fa-solid fa-magnifying-glass text-[10px]"></i>
                <input
                  v-model="askText"
                  type="text"
                  :placeholder="isMobile ? 'Ask AI…' : 'Quick search or ask anything…'"
                  aria-label="Search or ask the company assistant"
                  @keydown.enter="sendAsk"
                />
                <span v-if="!isMobile" class="cp-kbd">⌘K</span>
              </label>
            </div>

            <div class="flex shrink-0 items-center gap-0.5 md:gap-1">
              <button
                class="cp-icon-btn"
                :title="isDarkTheme ? 'Switch to light mode' : 'Switch to dark mode'"
                :aria-label="isDarkTheme ? 'Switch to light mode' : 'Switch to dark mode'"
                :aria-pressed="isDarkTheme"
                @click="toggleCompanyTheme"
              >
                <i :class="isDarkTheme ? 'fa-solid fa-sun' : 'fa-solid fa-moon'" class="text-[12px]"></i>
              </button>
              <span v-if="!isMobile">
                <button class="cp-icon-btn" :disabled="busy" title="Refresh company data" aria-label="Refresh company data" @click="selectCompany(activeId)">
                  <i class="fa-solid fa-rotate text-[11px]" :class="busy ? 'animate-spin' : ''"></i>
                </button>
              </span>
              <span v-if="!isMobile">
                <button class="cp-icon-btn" title="Company chats" aria-label="Company chats" @click="openChat()">
                  <span class="relative">
                    <i class="fa-regular fa-comments text-[13px]"></i>
                    <span v-if="counts.chats" class="absolute -right-1.5 -top-1.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-[var(--cp-lime)] px-0.5 text-[7px] font-bold text-[var(--cp-lime-ink)]">{{ counts.chats }}</span>
                  </span>
                </button>
              </span>
              <button class="cp-icon-btn relative" title="Needs your attention" aria-label="Needs your attention" @click="setSection('work')">
                <i class="fa-regular fa-bell text-[13px]"></i>
                <span v-if="attention.length" class="absolute right-1 top-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-[var(--cp-yellow)] px-0.5 text-[7px] font-bold text-[#201b04]">{{ attention.length }}</span>
              </button>
              <span v-if="!isMobile">
                <button class="cp-icon-btn" title="Company settings" aria-label="Company settings" @click="setSection('settings')">
                  <i class="fa-solid fa-gear text-[12px]"></i>
                </button>
              </span>

              <div v-if="!isMobile" class="mx-1.5 flex items-center gap-2">
                <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-[var(--cp-lime-soft)] ring-2 ring-[var(--cp-surface)]">
                  <span class="absolute inset-0 grid place-items-center text-[9px] font-extrabold text-[var(--cp-accent-ink)]">{{ userInitials }}</span>
                  <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                </span>
                <div class="min-w-0">
                  <p class="max-w-[110px] truncate text-[10.5px] font-bold leading-tight">{{ userLabel }}</p>
                  <p class="max-w-[110px] truncate text-[8.5px] text-[var(--cp-muted-soft)]">{{ roleLabel }} · {{ company?.name || 'Company' }}</p>
                </div>
              </div>

              <button v-if="canManage" class="cp-cta-dark ml-1" aria-label="New goal" @click="openGoal()">
                <i class="fa-solid fa-plus text-[10px]"></i>
                <span v-if="!isMobile">New goal</span>
              </button>
            </div>
          </div>

          <!-- Mobile section pills -->
          <div v-if="isMobile" class="cp-scroll flex gap-1 overflow-x-auto border-t border-[var(--cp-border)] px-2 py-2">
            <button
              v-for="item in navItems"
              :key="item.id"
              class="cp-chip shrink-0"
              :class="section === item.id ? '!border-transparent !bg-[var(--cp-lime)] !text-[var(--cp-lime-ink)]' : ''"
              @click="setSection(item.id)"
            >
              {{ item.label }}<span v-if="item.badge" class="opacity-60">{{ item.badge }}</span>
            </button>
          </div>
        </header>

        <main class="cp-scroll min-h-0 flex-1 overflow-y-auto">
          <div class="w-full px-3 py-3 md:px-5 md:py-5">
            <div v-if="loading" class="grid h-[60vh] place-items-center">
              <div class="text-center text-[var(--cp-muted)]">
                <span class="loading loading-spinner loading-lg text-primary"></span>
                <p class="mt-2 text-sm">Opening your company…</p>
              </div>
            </div>

            <div v-else-if="!companies.length" class="grid min-h-[70vh] place-items-center">
              <div class="cp-card w-full max-w-md p-6 text-center md:p-8">
                <div class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-[var(--cp-lime)] text-[var(--cp-lime-ink)] shadow-lg shadow-[0_12px_28px_rgba(204,0,153,0.30)]">
                  <i class="fa-solid fa-building-columns text-xl"></i>
                </div>
                <h2 class="mt-4 text-xl font-extrabold tracking-tight">Build your AI company</h2>
                <p class="mx-auto mt-2 max-w-sm text-[12px] leading-relaxed text-[var(--cp-muted)]">
                  A company where human judgment and AI execution work together. Start with a name; the assistant will help with the org, the teams, the goals and the money.
                </p>
                <button class="cp-cta mx-auto mt-5 h-10 px-4" @click="openCreate">
                  <i class="fa-solid fa-plus text-[10px]"></i> Create my company
                </button>
              </div>
            </div>

            <template v-else-if="company">
              <Transition name="cp-fade" mode="out-in">
                <div :key="section">
                  <CompanyHome
                    v-if="section === 'home'"
                    :snapshot="snapshot"
                    :actors="companyActors.actors"
                    :can-manage="canManage"
                    :can-admin="canAdmin"
                    @assistant="openAssistantFor"
                    @open-work="onWorkFocus"
                    @open-team="onTeamFocus"
                    @open-business="setSection('business')"
                    @open-activity="setSection('activity')"
                    @open-settings="setSection('settings')"
                    @new-goal="openGoal()"
                    @open-chat="openChat()"
                  />
                  <CompanyWork
                    v-else-if="section === 'work'"
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :can-manage="canManage"
                    :focus-id="workFocusId"
                    @assistant="openAssistantFor"
                    @new-goal="openGoal()"
                    @edit-goal="(mission) => openGoal('', mission)"
                    @refresh="refreshSnapshot"
                  />
                  <CompanyTeams
                    v-else-if="section === 'teams'"
                    :snapshot="snapshot"
                    :actors="companyActors.actors"
                    :can-manage="canManage"
                    :focus-id="teamFocusId"
                    @assistant="openAssistantFor"
                    @open-chat="openChat"
                    @new-goal="(teamId) => openGoal(teamId)"
                    @new-team="generateOpen = true"
                  />
                  <CompanyPeople
                    v-else-if="section === 'people'"
                    :snapshot="snapshot"
                    :actors="companyActors.actors"
                    :can-manage="canManage"
                    :can-admin="canAdmin"
                    @assistant="openAssistantFor"
                    @new-team="generateOpen = true"
                  />
                  <CompanyBusiness
                    v-else-if="section === 'business'"
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :can-manage="canManage"
                    @assistant="openAssistantFor"
                    @refresh="refreshSnapshot"
                  />
                  <CompanyActivity
                    v-else-if="section === 'activity'"
                    :snapshot="snapshot"
                    @assistant="openAssistantFor"
                    @open-work="onWorkFocus"
                  />
                  <CompanySecurity
                    v-else-if="section === 'security'"
                    :company-id="activeId"
                  />
                  <CompanySettings
                    v-else-if="section === 'settings'"
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :can-manage="canManage"
                    :can-admin="canAdmin"
                    @refresh="refreshSnapshot"
                    @assistant="openAssistantFor"
                    @deleted="onCompanyDeleted"
                  />
                </div>
              </Transition>
            </template>
          </div>
        </main>
      </div>
    </div>

    <!-- ── Always-present assistant (drawer) ────────────────────────────── -->
    <CompanyAssistant
      v-if="company"
      :company-id="activeId"
      :company-name="company.name"
      :view="section"
      :can-manage="canManage"
      :can-admin="canAdmin"
      :snapshot="snapshot"
      :actors="companyActors.actors"
      :docked="false"
      @refresh="refreshSnapshot"
      @deleted="onCompanyDeleted"
    />

    <!-- ── Goal dialog ────────────────────────────────────────────────────── -->
    <CompanyGoalDialog
      v-if="goalOpen && company"
      :company-id="activeId"
      :snapshot="snapshot"
      :team-id="goalTeamId"
      :mission="goalMission"
      @close="goalOpen = false"
      @saved="onGoalSaved"
    />

    <!-- ── Team builder ───────────────────────────────────────────────────── -->
    <GenerateTeamDialog
      v-if="generateOpen && company"
      :company-id="activeId"
      :project="$storex.projects.activeProject"
      @created="refreshSnapshot"
      @close="generateOpen = false"
    />

    <!-- ── Team rooms / chats ─────────────────────────────────────────────── -->
    <CompanyChatPanel
      v-if="chatsOpen && company"
      :company-id="activeId"
      :snapshot="snapshot"
      :can-manage="canManage"
      :team-id="chatTeamId"
      @close="chatsOpen = false; chatTeamId = ''"
      @changed="refreshSnapshot"
    />

    <!-- ── Exit company ─────────────────────────────────────────────────── -->
    <div v-if="exitOpen" class="fixed inset-0 z-[95] grid place-items-center bg-black/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Leave company">
      <div class="cp-card w-full max-w-[390px] p-4 md:p-5">
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="cp-eyebrow">Leave company</p>
            <h2 class="mt-1 text-base font-extrabold">Where do you want to go?</h2>
            <p class="mt-1 text-[10px] leading-relaxed cp-muted">The company keeps running. You can come back from the workspace picker.</p>
          </div>
          <button class="cp-icon-btn !h-7 !w-7" aria-label="Close" @click="exitOpen = false"><i class="fa-solid fa-xmark text-[11px]"></i></button>
        </div>
        <div class="mt-3 grid gap-2">
          <button class="flex items-start gap-3 rounded-xl border border-[var(--cp-border)] p-3 text-left transition hover:border-[var(--cp-border-strong)] hover:bg-[var(--cp-surface-soft)]" @click="leaveCompany('simple')">
            <span class="cp-icon-tile cp-icon-tile--lime !h-8 !w-8"><i class="fa-solid fa-rocket text-[10px]"></i></span>
            <span class="min-w-0">
              <span class="block text-[11px] font-bold">Simple mode</span>
              <span class="mt-0.5 block text-[9px] cp-muted">One screen. Chat, tasks and approvals.</span>
            </span>
            <i class="fa-solid fa-arrow-right ml-auto mt-1 text-[9px] cp-muted-soft"></i>
          </button>
          <button class="flex items-start gap-3 rounded-xl border border-[var(--cp-border)] p-3 text-left transition hover:border-[var(--cp-border-strong)] hover:bg-[var(--cp-surface-soft)]" @click="leaveCompany('desktop')">
            <span class="cp-icon-tile !h-8 !w-8"><i class="fa-solid fa-desktop text-[10px]"></i></span>
            <span class="min-w-0">
              <span class="block text-[11px] font-bold">Desktop workspace</span>
              <span class="mt-0.5 block text-[9px] cp-muted">The full project workspace and tools.</span>
            </span>
            <i class="fa-solid fa-arrow-right ml-auto mt-1 text-[9px] cp-muted-soft"></i>
          </button>
        </div>
        <button class="cp-chip mt-3 w-full justify-center" @click="exitOpen = false">Stay in the company</button>
      </div>
    </div>

    <!-- ── Create company ────────────────────────────────────────────────── -->
    <div v-if="createOpen" class="modal modal-open z-[90]" role="dialog" aria-modal="true">
      <div class="modal-box max-w-lg overflow-hidden border border-[var(--cp-border)] bg-[var(--cp-surface)] p-0">
        <div class="border-b border-[var(--cp-border)] bg-[var(--cp-surface-soft)] px-5 py-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <span class="grid h-9 w-9 place-items-center rounded-[10px] bg-[var(--cp-lime)] text-[var(--cp-lime-ink)]">
                <i class="fa-solid fa-building-columns text-[13px]"></i>
              </span>
              <div>
                <h3 class="text-base font-extrabold tracking-tight">Create your company</h3>
                <p class="text-[11px] text-[var(--cp-muted)]">You can change everything later just by asking.</p>
              </div>
            </div>
            <button class="cp-icon-btn" aria-label="Close" @click="createOpen = false"><i class="fa-solid fa-xmark text-[13px]"></i></button>
          </div>
        </div>
        <div class="max-h-[68vh] overflow-y-auto p-5">
          <label class="form-control">
            <span class="label-text text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--cp-muted)]">Company name</span>
            <input v-model="createForm.name" class="input input-bordered mt-1" placeholder="For example: Acme Studio" autofocus @keyup.enter="submitCreate" />
          </label>
          <label class="form-control mt-3">
            <span class="label-text text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--cp-muted)]">What does it do? <span class="font-normal text-[var(--cp-muted-soft)]">(optional)</span></span>
            <textarea v-model="createForm.description" class="textarea textarea-bordered mt-1" rows="2" placeholder="We design and sell furniture online"></textarea>
          </label>

          <details class="mt-3 rounded-xl border border-[var(--cp-border)] p-3" :open="createAdvanced" @toggle="createAdvanced = $event.target.open">
            <summary class="cursor-pointer text-xs font-semibold text-[var(--cp-accent-ink)]">More options</summary>
            <div class="mt-3 flex flex-col gap-3">
              <label class="form-control">
                <span class="label-text text-[10px] uppercase text-[var(--cp-muted)]">Mission — why it exists?</span>
                <textarea v-model="createForm.mission" class="textarea textarea-sm textarea-bordered mt-1" rows="2"></textarea>
              </label>
              <div>
                <div class="text-[10px] font-bold uppercase tracking-wide text-[var(--cp-muted)]">Start with a template</div>
                <div class="mt-1 flex flex-col gap-1.5">
                  <label v-for="template in COMPANY_TEMPLATES" :key="template.id"
                         class="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--cp-border)] p-2"
                         :class="createForm.template === template.id ? 'border-[var(--cp-lime-deep)] bg-[var(--cp-lime-soft)]' : ''">
                    <input v-model="createForm.template" type="radio" class="radio radio-xs radio-primary mt-0.5" :value="template.id" />
                    <span>
                      <span class="block text-[11px] font-semibold">{{ template.label }}</span>
                      <span class="block text-[10px] text-[var(--cp-muted)]">{{ template.hint }}</span>
                    </span>
                  </label>
                </div>
              </div>
              <div>
                <div class="text-[10px] font-bold uppercase tracking-wide text-[var(--cp-muted)]">Link projects</div>
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <label v-for="project in (references.projects || [])" :key="project.project_id"
                         class="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[var(--cp-border)] px-2 py-1 text-[10px]">
                    <input type="checkbox" class="checkbox checkbox-xs checkbox-primary"
                           :checked="createForm.project_ids.includes(project.project_id)"
                           @change="toggleCreateProject(project.project_id)" />
                    <span class="max-w-40 truncate">{{ project.project_name }}</span>
                  </label>
                  <span v-if="!(references.projects || []).length" class="text-[10px] text-[var(--cp-muted-soft)]">No projects found — add them later.</span>
                </div>
              </div>
            </div>
          </details>
        </div>
        <div class="flex items-center justify-between border-t border-[var(--cp-border)] bg-[var(--cp-surface-soft)] px-5 py-3">
          <p class="text-[10px] text-[var(--cp-muted-soft)]"><i class="fa-solid fa-lock mr-1"></i>You are the owner.</p>
          <div class="flex gap-2">
            <button class="cp-chip !h-8" @click="createOpen = false">Cancel</button>
            <button class="cp-cta h-8" :disabled="busy || !createForm.name.trim()" @click="submitCreate">
              <i class="fa-solid fa-arrow-right text-[9px]"></i> Create company
            </button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" @click="createOpen = false"></div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->

<style scoped>
/* Company forms are labelled above their control, everywhere. DaisyUI 5's
   form-control is not a column by default in this theme, so every company
   workspace gets the same predictable field rhythm. */
:deep(.form-control) {
  display: flex;
  flex-direction: column;
  align-items: stretch;
}
:deep(.form-control > .label-text) {
  margin-bottom: 0.25rem;
}
:deep(.form-control > input),
:deep(.form-control > textarea),
:deep(.form-control > select) {
  width: 100%;
}
</style>
