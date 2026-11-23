<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { API } from '@/api/api'
import { $storex } from '@/store'

import CompanyHome from '@/components/company/CompanyHome.vue'
import CompanyWork from '@/components/company/CompanyWork.vue'
import CompanyTeam from '@/components/company/CompanyTeam.vue'
import CompanyBusiness from '@/components/company/CompanyBusiness.vue'
import CompanyActivity from '@/components/company/CompanyActivity.vue'
import CompanySecurity from '@/components/company/CompanySecurity.vue'
import CompanySettings from '@/components/company/CompanySettings.vue'
import CompanyProjectsPanel from '@/components/company/CompanyProjectsPanel.vue'
import CompanyAssistant from '@/components/company/CompanyAssistant.vue'
import CompanyWorkplace from '@/components/company/CompanyWorkplace.vue'
import CompanyChatPanel from '@/components/company/CompanyChatPanel.vue'
import CompanyGoalDialog from '@/components/company/CompanyGoalDialog.vue'
import CompanyWizard from '@/components/company/CompanyWizard.vue'
import GenerateTeamDialog from '@/components/teams/GenerateTeamDialog.vue'
import { useCompanyI18n, setCompanyLanguage } from '@/components/company/i18n'
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

const THEME_KEY = 'codx.company.theme'
const SIDEBAR_KEY = 'codx.company.sidebar'

const loading = ref(true)
const busy = ref(false)
// The company store is the single source of truth for the active company and
// its snapshot; the URL slug is resolved by the router guard.
const companies = computed(() => $storex.company.companies)
const activeId = computed(() => $storex.company.activeId)
const snapshot = computed(() => $storex.company.snapshot)
const companyActors = ref({ actors: [], counts: {} })
const companyReferences = ref({ projects: [], users: [], profiles: [] })
const companyChats = ref([])
const section = ref('home')
const navOpen = ref(false)
const exitOpen = ref(false)
const userMenuOpen = ref(false)
const askText = ref('')
const newCompanyOpen = ref(false)
const newCompanySaving = ref(false)
const newCompanyError = ref('')
const newCompanyForm = ref({ name: '', description: '' })
// Setup assistant (the "New company" wizard). It opens by itself while the
// installation onboarding is still pending and can be re-run from the user menu.
const wizardOpen = ref(false)
// Company the wizard configures. Empty runs the installation onboarding (new
// company); set when a company was just created, so its own wizard opens.
const wizardCompanyId = ref('')

const chatsOpen = ref(false)
const chatTeamId = ref('')
const chatInitialId = ref('')
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
const { t, locale, languages, setLanguage } = useCompanyI18n()
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

const company = computed(() => snapshot.value?.company || null)
const counts = computed(() => snapshot.value?.counts || {})
const attention = computed(() => snapshot.value?.attention || [])
const reviewCount = computed(() => (snapshot.value?.missions || []).filter(mission => mission.status === 'review').length)
const activeActors = computed(() => (companyActors.value.actors || []).filter(actor => actor?.config?.enabled))
const aiCount = computed(() => counts.value.ai || 0)

const SECTIONS = [
  { id: 'home', labelKey: 'nav.overview', icon: 'fa-solid fa-border-all', hintKey: 'The state of the company' },
  { id: 'work', labelKey: 'nav.work', icon: 'fa-solid fa-flag', hintKey: 'Goals, delivery, review' },
  { id: 'team', labelKey: 'nav.team', icon: 'fa-solid fa-people-group', hintKey: 'Teams and people' },
  { id: 'projects', labelKey: 'nav.projects', icon: 'fa-solid fa-cubes', hintKey: 'Projects this company runs' },
  { id: 'business', labelKey: 'nav.business', icon: 'fa-solid fa-chart-line', hintKey: 'Revenue, margin, pipeline' },
  { id: 'activity', labelKey: 'nav.activity', icon: 'fa-solid fa-wave-square', hintKey: 'What happened' },
  { id: 'security', labelKey: 'nav.security', icon: 'fa-solid fa-shield-halved', hintKey: 'CISO posture' },
  { id: 'settings', labelKey: 'nav.settings', icon: 'fa-solid fa-sliders', hintKey: 'Company & controls' },
  { id: 'meet', labelKey: 'nav.meet', icon: 'fa-solid fa-video', hintKey: 'Meet', workplace: true },
  { id: 'coworking', labelKey: 'nav.coworking', icon: 'fa-regular fa-window-maximize', hintKey: 'CoWorking', workplace: true },
  { id: 'calendar', labelKey: 'nav.calendar', icon: 'fa-regular fa-calendar-days', hintKey: 'Calendar', workplace: true }
]

const NAV_GROUPS = [
  { id: 'workspace', labelKey: 'group.workspace', ids: ['home', 'work', 'team', 'projects'] },
  { id: 'business', labelKey: 'group.business', ids: ['business', 'activity'] },
  { id: 'governance', labelKey: 'group.governance', ids: ['security', 'settings'] }
]

const WORKPLACE_LINKS = [
  { id: 'meet', labelKey: 'nav.meet', icon: 'fa-solid fa-video' },
  { id: 'coworking', labelKey: 'nav.coworking', icon: 'fa-regular fa-window-maximize' },
  { id: 'calendar', labelKey: 'nav.calendar', icon: 'fa-regular fa-calendar-days' }
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
  if (canAdmin.value) return t('shell.owner')
  if (myCompanyRole.value === 'manager') return t('shell.manager')
  return t('shell.member')
})
const workingProject = computed(() => $storex.projects.activeProject || null)
// A company owns MANY projects. The selector in the top bar lists the ones
// linked to this company and switches the active project without leaving the
// current company section, so every company screen follows the same context.
const companyProjectOptions = computed(() =>
  ($storex.company.companyProjects || [])
    .filter(item => item && item.project_id)
    .map(item => ({
      project_id: item.project_id,
      project_name: item.project_name || item.project_id
    }))
)
const activeProjectId = computed(() => {
  const active = workingProject.value?.project_id
  if (active && companyProjectOptions.value.some(option => option.project_id === active)) return active
  return companyProjectOptions.value[0]?.project_id || ''
})

async function onSelectProject(event) {
  const projectId = event?.target?.value
  if (!projectId || projectId === workingProject.value?.project_id) return
  const project = ($storex.projects.allProjects || []).find(item => item.project_id === projectId)
  if (!project) return
  try {
    await $storex.projects.activeProjectChanged(project)
    await refreshSnapshot()
  } catch (error) {
    notifyError(error)
  }
}
const companyInitials = computed(() => initials(company.value?.name || 'Company'))
// The logo is stored as an image source (an http(s) URL or an inline data URL).
// Older documents keep a FontAwesome class there, so only real image sources are
// rendered as an <img>.
const companyLogo = computed(() => String(company.value?.logo || '').trim())
const companyLogoIsImage = computed(() => /^(data:image\/|https?:\/\/|\/)/i.test(companyLogo.value))

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

const navItems = computed(() => SECTIONS.filter(item => !item.workplace).map(item => ({
  ...item,
  label: t(item.labelKey),
  hint: item.hintKey,
  badge: item.id === 'work'
    ? attention.value.length
    : item.id === 'team'
      ? counts.value.teams || 0
      : ''
})))

const navGroups = computed(() => NAV_GROUPS
  .map(group => ({
    ...group,
    label: t(group.labelKey),
    items: navItems.value.filter(item => group.ids.includes(item.id))
  }))
  .filter(group => group.items.length))

const workplaceLinks = computed(() => WORKPLACE_LINKS.map(item => ({
  ...item,
  label: t(item.labelKey)
})))

const recentChats = computed(() => [...companyChats.value]
  .sort((a, b) => String(chatUpdatedAt(b)).localeCompare(String(chatUpdatedAt(a))))
  .slice(0, 3))

function chatUpdatedAt(chat) {
  return chat.chat?.updated_at || chat.updated_at || ''
}

function chatName(chat) {
  return chat.name || chat.chat?.name || chat.chat_id
}

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

async function loadActiveCompany() {
  loading.value = true
  try {
    await $storex.company.ensureLoaded()
    const slug = String(route.params.companySlug || '')
    const company = companies.value.find(item => item.slug === slug) ||
      $storex.company.defaultCompany
    if (!company) return
    if (slug && company.slug !== slug) {
      router.replace({ name: 'company-home', params: { companySlug: company.slug } }).catch(() => {})
      return
    }
    if ($storex.company.activeId !== company.id || !$storex.company.snapshot) {
      await $storex.company.select({ companyId: company.id, skipList: true })
    }
    // Never keep a project from another company active in the company shell.
    const active = $storex.projects.activeProject
    if (active && !$storex.company.hasProject(active.project_id)) {
      const first = $storex.company.companyProjects[0]
      if (first) {
        await $storex.projects.activeProjectChanged(first).catch(() => {})
      }
    }
    await loadCompanyActors(company.id)
    await loadCompanyChats()
    await loadCompanyReferences()
    openNewCompanyFromMenu()
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
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

async function loadCompanyReferences() {
  try {
    companyReferences.value = await api.references({
      projectId: workingProject.value?.project_id || ''
    })
  } catch (error) {
    // The project picker is optional context; the rest of the shell keeps working.
    console.warn('[CompanyView] cannot load company references', error)
  }
}

async function refreshSnapshot() {
  if (!activeId.value) return
  try {
    await $storex.company.refresh()
    await loadCompanyActors(activeId.value)
    await loadCompanyChats()
    await loadCompanyReferences()
  } catch (error) {
    notifyError(error)
  }
}

// A project created from the company workspace (Projects → "New project")
// is only linkable once the company references are recomputed from the
// discovered projects. The project store is the signal: as soon as it grows,
// the picker and the snapshot are refreshed — no reload of the page.
watch(() => ($storex.projects?.allProjects || []).length, (now, before) => {
  if (now > before && activeId.value) {
    loadCompanyReferences()
    refreshSnapshot()
  }
})

// ── Projects ──────────────────────────────────────────────────────────────
// The project list is a company setting: it is what the company "can work
// with". The default company starts with every project (`all_projects`), and
// the user can change the list here or create another company with its own list.
async function linkCompanyProject({ project_id, payload } = {}) {
  if (!activeId.value || !project_id) return
  busy.value = true
  try {
    await api.linkProject(activeId.value, { project_id, ...(payload || {}) })
    await refreshSnapshot()
    notify('Project linked ✓', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function unlinkCompanyProject(projectId) {
  if (!activeId.value || !projectId) return
  busy.value = true
  try {
    await api.unlinkProject(activeId.value, projectId)
    await refreshSnapshot()
    notify('Project unlinked', 'info')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function setAllCompanyProjects(value) {
  if (!activeId.value) return
  busy.value = true
  try {
    await $storex.company.setAllProjects(value)
    await loadCompanyReferences()
    notify(value ? 'Company sees all projects' : 'Company sees the selected projects', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

function companySlug() {
  return $storex.company.activeCompany?.slug || $storex.company.activeSlug || String(route.params.companySlug || '')
}

async function openProjectByName(projectName) {
  if (!projectName) return
  const project = ($storex.projects.allProjects || []).find(item =>
    item.project_name === projectName || item.project_id === projectName
  )
  const slug = companySlug()
  if (!slug) return
  if (project) {
    await $storex.projects.activeProjectChanged(project).catch(() => {})
  }
  router.push({
    name: 'home',
    params: { companySlug: slug, projectName }
  }).catch(() => {})
}

function openAdvancedView() {
  const slug = companySlug()
  if (!slug) return
  exitOpen.value = false
  const selected = ($storex.projects.activeProject &&
    $storex.company.hasProject($storex.projects.activeProject.project_id))
    ? $storex.projects.activeProject
    : $storex.company.companyProjects[0]
  if (!selected) {
    setSection('projects')
    notify('Link or create a project before opening the advanced view.', 'error')
    return
  }
  router.push(
    `/${encodeURIComponent(slug)}/workspace/${encodeURIComponent(selected.project_name)}/desktop`
  ).catch(() => {})
}

function openNewCompany() {
  newCompanyForm.value = { name: '', description: '' }
  newCompanyError.value = ''
  newCompanyOpen.value = true
}

async function createCompany() {
  const name = newCompanyForm.value.name.trim()
  if (!name) {
    newCompanyError.value = 'Give the company a name.'
    return
  }
  newCompanySaving.value = true
  newCompanyError.value = ''
  try {
    const created = await api.create({
      name,
      description: newCompanyForm.value.description.trim()
    })
    newCompanyOpen.value = false
    await $storex.company.loadCompanies()
    const company = $storex.company.companies.find(item => item.id === created.id) || created
    await router.push({
      name: 'company-home',
      params: { companySlug: company.slug }
    }).catch(() => {})
    notify('Company created ✓', 'success')
    // A brand new company is not configured yet: run ITS wizard (the company
    // step first) instead of leaving the owner with an empty workspace.
    openCompanyWizard(created.id)
  } catch (error) {
    newCompanyError.value = errorMessage(error)
  } finally {
    newCompanySaving.value = false
  }
}

// ── Setup assistant (New company wizard) ──────────────────────────────────
// The wizard is the first thing a fresh installation runs. The state lives on
// the server (`/api/companies/onboarding`), so it is offered once per install,
// never re-opened after a decision, and re-runnable at any time from the menu.
async function maybeOpenWizard({ force = false } = {}) {
  try {
    const onboarding = await api.onboarding()
    if (force || onboarding?.needs_setup) wizardOpen.value = true
    return onboarding
  } catch (error) {
    console.warn('[CompanyView] cannot read the setup state', error)
    return null
  }
}

function openSetupAssistant() {
  userMenuOpen.value = false
  wizardCompanyId.value = ''
  wizardOpen.value = true
}

/**
 * Open the wizard bound to one company (used right after creating it).
 *
 * @param {String} companyId Company to configure; empty runs the installation
 *   onboarding, which may create a company.
 */
function openCompanyWizard(companyId = '') {
  wizardCompanyId.value = companyId || ''
  wizardOpen.value = true
}

async function onWizardDone() {
  wizardOpen.value = false
  wizardCompanyId.value = ''
  await loadActiveCompany().catch(() => {})
  await refreshSnapshot().catch(() => {})
}

// The main menu (left bar → "New company") reaches this shell with `?new=1`.
// The dialog is owned here, so the entry point never duplicates it; the flag
// is consumed and removed from the URL so a refresh does not reopen it.
async function openNewCompanyFromMenu() {
  if (route.query.new !== '1') return
  openNewCompany()
  const query = { ...route.query }
  delete query.new
  await router.replace({ query }).catch(() => {})
}

async function selectCompany(companyId, { skipList = false } = {}) {
  if (!companyId) return
  const company = companies.value.find(item => item.id === companyId)
  if (!company) return
  const targetSection = section.value && section.value !== 'home' ? section.value : undefined
  await router.push({
    name: 'company-home',
    params: {
      companySlug: company.slug,
      ...(targetSection ? { section: targetSection } : {})
    }
  }).catch(() => {})
}

function setSection(id, { updateRoute = true } = {}) {
  const next = SECTIONS.some(item => item.id === id) ? id : 'home'
  section.value = next
  navOpen.value = false
  if (updateRoute) {
    const slug = companySlug()
    if (!slug) return
    router.push({
      name: 'company-home',
      params: {
        companySlug: slug,
        ...(next !== 'home' ? { section: next } : {})
      }
    }).catch(() => {})
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

async function loadCompanyChats() {
  if (!activeId.value) {
    companyChats.value = []
    return
  }
  try {
    companyChats.value = await api.listChats(activeId.value)
  } catch {
    companyChats.value = []
  }
}

function openChat(teamId = '', chatId = '') {
  chatTeamId.value = teamId || ''
  chatInitialId.value = chatId || ''
  chatsOpen.value = true
}

function openChatChannel(chat) {
  openChat(chat.team_id || '', chat.chat_id || '')
}

async function onChatsChanged() {
  await loadCompanyChats()
  await refreshSnapshot()
}

function openActivityChat(event) {
  const data = event?.data || {}
  openChat(data.team_id || '', data.chat_id || data.chatId || '')
}

function openWorkplace(kind) {
  setSection(kind)
  navOpen.value = false
}

async function openNewTask() {
  const linked = (snapshot.value?.projects || []).find(project => project.available)
  let project = $storex.projects.activeProject
  if (!project && linked) {
    project = ($storex.projects.allProjects || []).find(item => item.project_id === linked.project_id)
  }
  if (!project) {
    notify('Link a project to the company before creating a task.', 'error')
    return
  }
  if ($storex.projects.activeProject?.project_id !== project.project_id) {
    try {
      await $storex.projects.activeProjectChanged(project)
    } catch {
      // The global task dialog can still open with the project metadata.
    }
  }
  $storex.ui.showNewTask(true)
}

async function changeLanguage(code) {
  const next = setLanguage(code)
  userMenuOpen.value = false
  try {
    await $storex.users.saveUser({ ...$storex.api.user, language: next })
  } catch {
    // Keep the local company language even if the profile cannot be saved.
  }
}

function openUserSettings() {
  userMenuOpen.value = false
  navOpen.value = false
  setSection('settings')
}

async function logoutCompany() {
  userMenuOpen.value = false
  await $storex.users.logout()
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
  setSection('team')
}

function openAssistantFor(prompt) {
  askAssistant(prompt)
}

async function onCompanyDeleted() {
  notify('Company deleted', 'info')
  $storex.company.reset()
  await $storex.company.loadCompanies()
  const company = $storex.company.defaultCompany
  if (company?.slug) {
    router.replace({ name: 'company-home', params: { companySlug: company.slug } }).catch(() => {})
  } else {
    router.replace('/').catch(() => {})
  }
}


// ── Lifecycle ──────────────────────────────────────────────────────────────

onMounted(async () => {
  const requested = String(route.params.section || route.query.section || route.query.tab || 'home')
  section.value = legacySection(requested)
  await loadActiveCompany()
  await maybeOpenWizard()
  const preferredLanguage = $storex.api.user?.language
  if (preferredLanguage && preferredLanguage !== 'auto') setCompanyLanguage(preferredLanguage)
  const intent = route.query.assistant
  if (intent) {
    nextTick(() => askAssistant(String(intent)))
    const rest = { ...route.query }
    delete rest.assistant
    delete rest.tab
    router.replace({ query: rest }).catch(() => {})
  }
})

watch(
  () => [route.params.companySlug, route.params.section],
  async ([slug, requested]) => {
    if (!slug) return
    section.value = legacySection(requested || 'home')
    await loadActiveCompany()
  }
)

function legacySection(value) {
  const map = {
    teams: 'team',
    people: 'team',
    organization: 'team',
    projects: 'projects',
    activity: 'activity',
    missions: 'work',
    overview: 'home',
    work: 'work',
    team: 'team',
    meet: 'meet',
    coworking: 'coworking',
    calendar: 'calendar',
    business: 'business',
    settings: 'settings',
    home: 'home'
  }
  return map[String(value || '')] || 'home'
}

watch(() => route.query.section, value => {
  if (value) setSection(legacySection(value))
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
            <span class="grid h-9 w-9 place-items-center overflow-hidden rounded-[11px] bg-[var(--cp-lime)] text-[13px] font-black text-[var(--cp-lime-ink)] shadow-sm">
              <img
                v-if="companyLogoIsImage"
                :src="companyLogo"
                :alt="company?.name || 'Company logo'"
                class="h-full w-full object-cover"
                @error="hideBrokenImage"
              />
              <i v-else-if="companyLogo" :class="companyLogo"></i>
              <span v-else>{{ companyInitials }}</span>
            </span>
          </button>
          <div v-if="!sidebarCollapsed" class="min-w-0 flex-1">
            <p class="truncate text-[13px] font-extrabold leading-tight tracking-tight">{{ company?.name || 'Your company' }}</p>
            <p class="mt-0.5 truncate text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">AI company OS</p>
          </div>
        </div>

        <div v-if="!sidebarCollapsed" class="flex items-center gap-1 px-3 pb-1.5">
          <label v-if="companies.length" class="relative block min-w-0 flex-1">
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
          <button
            type="button"
            class="ml-auto grid h-7 w-7 shrink-0 place-items-center rounded-[8px] border border-[var(--cp-border)] text-[10px] text-[var(--cp-muted)] transition hover:border-[var(--cp-border-strong)] hover:text-[var(--cp-text)]"
            title="Create a new company"
            aria-label="Create a new company"
            @click="openNewCompany"
          >
            <i class="fa-solid fa-plus"></i>
          </button>
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

          <p
            v-if="!sidebarCollapsed"
            class="px-2 pb-1.5 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]"
          >{{ t('group.workplace') }}</p>
          <div v-else class="mx-2 my-2 border-t border-[var(--cp-border)]"></div>
          <button
            v-for="link in workplaceLinks"
            :key="link.id"
            type="button"
            class="cp-nav-item mb-0.5"
            :class="[section === link.id ? 'is-active' : '', sidebarCollapsed ? 'justify-center' : '']"
            :title="link.label"
            @click="openWorkplace(link.id)"
          >
            <span class="cp-nav-icon"><i :class="link.icon" class="text-[11px]"></i></span>
            <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ link.label }}</span>
          </button>

          <template v-if="recentChats.length">
            <p
              v-if="!sidebarCollapsed"
              class="px-2 pb-1.5 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]"
            >{{ t('group.channels') }}</p>
            <div v-else class="mx-2 my-2 border-t border-[var(--cp-border)]"></div>
            <button
              v-for="chat in recentChats"
              :key="chat.chat_id"
              type="button"
              class="cp-nav-item mb-0.5"
              :class="sidebarCollapsed ? 'justify-center' : ''"
              :title="chatName(chat)"
              @click="openChatChannel(chat)"
            >
              <span class="cp-nav-icon"><i class="fa-solid fa-hashtag text-[10px]"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ chatName(chat) }}</span>
            </button>
            <button
              type="button"
              class="cp-nav-item mb-0.5"
              :class="sidebarCollapsed ? 'justify-center' : ''"
              :title="t('nav.allChannels')"
              @click="openChat()"
            >
              <span class="cp-nav-icon"><i class="fa-solid fa-ellipsis text-[10px]"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate text-[10px]">{{ t('nav.allChannels') }}</span>
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
            :title="'Advanced view'"
            @click="exitOpen = true"
          >
            <i class="fa-solid fa-desktop w-4 text-center text-[10px]"></i>
            <span v-if="!sidebarCollapsed" class="text-[10px]">Advanced view</span>
          </button>

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
            <span class="grid h-9 w-9 place-items-center overflow-hidden rounded-[11px] bg-[var(--cp-lime)] text-[13px] font-black text-[var(--cp-lime-ink)]">
              <img
                v-if="companyLogoIsImage"
                :src="companyLogo"
                :alt="company?.name || 'Company logo'"
                class="h-full w-full object-cover"
                @error="hideBrokenImage"
              />
              <i v-else-if="companyLogo" :class="companyLogo"></i>
              <span v-else>{{ companyInitials }}</span>
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-[13px] font-extrabold">{{ company?.name || 'Company' }}</p>
              <p class="text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">AI company OS</p>
            </div>
            <button class="cp-icon-btn" aria-label="Close menu" @click="navOpen = false"><i class="fa-solid fa-xmark text-[13px]"></i></button>
          </div>
          <div v-if="companies.length" class="pb-2">
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
            <p class="px-2 pb-1 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">{{ t('group.workplace') }}</p>
            <div class="grid grid-cols-3 gap-1 px-1">
              <button v-for="link in workplaceLinks" :key="link.id" type="button" class="cp-chip !h-8 justify-center !px-1 !text-[8.5px]" :class="section === link.id ? '!border-transparent !bg-[var(--cp-lime)] !text-[var(--cp-lime-ink)]' : ''" @click="openWorkplace(link.id); navOpen = false">
                <i :class="link.icon" class="text-[8px]"></i>
                <span class="truncate">{{ link.label }}</span>
              </button>
            </div>
            <template v-if="recentChats.length">
              <p class="px-2 pb-1 pt-3 text-[8px] font-bold uppercase tracking-[0.18em] text-[var(--cp-muted-soft)]">{{ t('group.channels') }}</p>
              <button v-for="chat in recentChats" :key="chat.chat_id" type="button" class="cp-nav-item mb-0.5" @click="openChatChannel(chat); navOpen = false">
                <span class="cp-nav-icon"><i class="fa-solid fa-hashtag text-[10px]"></i></span>
                <span class="flex-1 truncate text-[12px]">{{ chatName(chat) }}</span>
              </button>
            </template>
          </nav>
          <div class="mt-2 space-y-1 border-t border-[var(--cp-border)] pt-3">
            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2">
              <div class="flex items-center gap-2">
                <span class="relative grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full bg-[var(--cp-lime-soft)]">
                  <span class="absolute inset-0 grid place-items-center text-[8px] font-extrabold text-[var(--cp-accent-ink)]">{{ userInitials }}</span>
                  <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-[10.5px] font-bold">{{ userLabel }}</p>
                  <p class="truncate text-[8px] cp-muted-soft">{{ roleLabel }} · {{ company?.name || 'Company' }}</p>
                </div>
                <button class="cp-icon-btn !h-7 !w-7" :aria-label="isDarkTheme ? 'Light mode' : 'Dark mode'" @click="toggleCompanyTheme">
                  <i :class="isDarkTheme ? 'fa-solid fa-sun' : 'fa-solid fa-moon'" class="text-[10px]"></i>
                </button>
              </div>
              <label class="cp-field mt-2">
                <span>{{ t('shell.language') }}</span>
                <select class="cp-select" :value="locale" @change="changeLanguage($event.target.value)">
                  <option v-for="language in languages" :key="language.code" :value="language.code">{{ language.label }}</option>
                </select>
              </label>
              <div class="mt-1.5 flex gap-1.5">
                <button class="cp-chip flex-1 justify-center !h-7 !text-[8.5px]" @click="openUserSettings">{{ t('shell.settings') }}</button>
                <button class="cp-chip flex-1 justify-center !h-7 !text-[8.5px] !text-[var(--cp-error-ink)]" @click="logoutCompany">{{ t('shell.logout') }}</button>
              </div>
            </div>
            <button type="button" class="cp-nav-item" @click="exitOpen = true; navOpen = false">
              <span class="cp-nav-icon"><i class="fa-solid fa-arrow-right-from-bracket text-[11px]"></i></span>
              <span class="flex-1 text-[12px]">Advanced view</span>
            </button>
          </div>
        </aside>
      </div>

      <!-- ── Main column ──────────────────────────────────────────────────── -->
      <div class="cp-canvas flex min-w-0 flex-1 flex-col overflow-hidden">
        <header class="cp-topbar shrink-0">
          <div class="flex h-[60px] items-center gap-2 px-3 md:h-[64px] md:px-5">
            <button v-if="isMobile" class="cp-icon-btn" aria-label="Open menu" @click="navOpen = true">
              <i class="fa-solid fa-bars-staggered text-[13px]"></i>
            </button>
            <div class="flex min-w-0 flex-1 items-center">
              <label class="cp-search w-full" :class="isMobile ? 'max-w-none' : 'max-w-[360px]'">
                <i class="fa-solid fa-wand-magic-sparkles text-[10px] text-[var(--cp-accent-ink)]"></i>
                <input
                  v-model="askText"
                  type="text"
                  :placeholder="t('shell.ask')"
                  aria-label="Ask codx-junior"
                  @click="askAssistant()"
                  @keydown.enter="sendAsk"
                />
                <span v-if="!isMobile" class="cp-kbd">⌘K</span>
              </label>
            </div>

            <div class="flex shrink-0 items-center gap-0.5 md:gap-1">
              <!-- Project selector: the company runs several projects and every
                   company screen reads the active one. Hidden when the company
                   has a single project (nothing to choose). -->
              <label
                v-if="!isMobile && companyProjectOptions.length > 1"
                class="cp-project-select"
                title="Active project"
              >
                <i class="fa-solid fa-cube text-[10px] text-[var(--cp-accent-ink)]"></i>
                <select
                  :value="activeProjectId"
                  aria-label="Active project"
                  @change="onSelectProject"
                >
                  <option
                    v-for="option in companyProjectOptions"
                    :key="option.project_id"
                    :value="option.project_id"
                  >
                    {{ option.project_name }}
                  </option>
                </select>
              </label>
              <button
                v-if="!isMobile"
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

              <div v-if="!isMobile" class="relative ml-0.5">
                <button
                  type="button"
                  class="flex items-center gap-1 rounded-full p-0.5 transition hover:bg-[var(--cp-surface-soft)]"
                  :title="userLabel"
                  aria-label="User menu"
                  :aria-expanded="userMenuOpen"
                  @click.stop="userMenuOpen = !userMenuOpen"
                >
                  <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-[var(--cp-lime-soft)] ring-2 ring-[var(--cp-surface)]">
                    <span class="absolute inset-0 grid place-items-center text-[9px] font-extrabold text-[var(--cp-accent-ink)]">{{ userInitials }}</span>
                    <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                  </span>
                  <i class="fa-solid fa-chevron-down pr-1 text-[7px] cp-muted-soft"></i>
                </button>
                <div v-if="userMenuOpen" class="fixed inset-0 z-40" @click="userMenuOpen = false"></div>
                <div v-if="userMenuOpen" class="absolute right-0 top-full z-50 mt-2 w-[242px] rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-surface)] p-1.5 shadow-2xl" role="menu">
                  <div class="flex items-center gap-2 rounded-xl bg-[var(--cp-surface-soft)] p-2">
                    <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-[var(--cp-lime-soft)]">
                      <span class="absolute inset-0 grid place-items-center text-[9px] font-extrabold text-[var(--cp-accent-ink)]">{{ userInitials }}</span>
                      <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                    </span>
                    <div class="min-w-0">
                      <p class="truncate text-[11px] font-bold">{{ userLabel }}</p>
                      <p class="truncate text-[8.5px] cp-muted-soft">{{ roleLabel }} · {{ company?.name || 'Company' }}</p>
                    </div>
                  </div>
                  <p class="px-2 pb-1 pt-2 cp-eyebrow !text-[7.5px]">{{ t('shell.language') }}</p>
                  <div class="grid grid-cols-2 gap-1">
                    <button
                      v-for="language in languages"
                      :key="language.code"
                      class="flex items-center justify-between rounded-lg px-2 py-1.5 text-left text-[10px] transition hover:bg-[var(--cp-surface-soft)]"
                      :class="locale === language.code ? 'bg-[var(--cp-lime-soft)] font-bold text-[var(--cp-accent-ink)]' : 'text-[var(--cp-text)]'"
                      @click="changeLanguage(language.code)"
                    >
                      {{ language.label }}
                      <i v-if="locale === language.code" class="fa-solid fa-check text-[8px]"></i>
                    </button>
                  </div>
                  <div class="my-1 border-t border-[var(--cp-border)]"></div>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[10.5px] font-semibold transition hover:bg-[var(--cp-surface-soft)]" @click="openUserSettings">
                    <i class="fa-solid fa-sliders w-4 text-center text-[10px] cp-muted"></i>{{ t('shell.settings') }}
                  </button>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[10.5px] font-semibold transition hover:bg-[var(--cp-surface-soft)]" @click="openSetupAssistant">
                    <i class="fa-solid fa-wand-magic-sparkles w-4 text-center text-[10px] cp-muted"></i>Setup assistant
                  </button>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[10.5px] font-semibold text-[var(--cp-error-ink)] transition hover:bg-[var(--cp-red-soft)]" @click="logoutCompany">
                    <i class="fa-solid fa-arrow-right-from-bracket w-4 text-center text-[10px]"></i>{{ t('shell.logout') }}
                  </button>
                </div>
              </div>

              <button v-if="canManage" class="cp-cta-dark ml-1" aria-label="New goal" @click="openGoal()">
                <i class="fa-solid fa-plus text-[10px]"></i>
                <span v-if="!isMobile">{{ t('shell.newGoal') }}</span>
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
                <h2 class="mt-4 text-xl font-extrabold tracking-tight">{{ t('shell.noCompany') }}</h2>
                <p class="mx-auto mt-2 max-w-sm text-[12px] leading-relaxed text-[var(--cp-muted)]">
                  {{ t('shell.createWorkspace') }}
                </p>
                <button class="cp-cta mx-auto mt-5 h-10 px-4" @click="exitOpen = true">
                  <i class="fa-solid fa-desktop text-[10px]"></i> Advanced view
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
                    @open-calendar="openWorkplace('calendar')"
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
                  <CompanyTeam
                    v-else-if="section === 'team'"
                    :snapshot="snapshot"
                    :actors="companyActors.actors"
                    :can-manage="canManage"
                    :can-admin="canAdmin"
                    :focus-id="teamFocusId"
                    @assistant="openAssistantFor"
                    @open-chat="openChat"
                    @new-goal="(teamId) => openGoal(teamId)"
                    @new-team="generateOpen = true"
                  />
                  <CompanyProjectsPanel
                    v-else-if="section === 'projects'"
                    :snapshot="snapshot"
                    :references="companyReferences"
                    :busy="busy"
                    @save="linkCompanyProject"
                    @delete="unlinkCompanyProject"
                    @open-project="openProjectByName"
                    @set-all-projects="setAllCompanyProjects"
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
                    @open-chat="openActivityChat"
                    @new-task="openNewTask"
                  />
                  <CompanyWorkplace
                    v-else-if="['meet', 'coworking', 'calendar'].includes(section)"
                    :kind="section"
                    @close="setSection('home')"
                  />
                  <CompanySecurity
                    v-else-if="section === 'security'"
                    :company-id="activeId"
                    :project-id="workingProject?.project_id || snapshot?.projects?.find(project => project.available)?.project_id || ''"
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
                    @open-projects="setSection('projects')"
                    @new-company="openNewCompany"
                  />
                </div>
              </Transition>
            </template>
          </div>
        </main>
      </div>
    </div>

    <!-- ── New company ───────────────────────────────────────────────────── -->
    <div v-if="newCompanyOpen" class="fixed inset-0 z-[80] grid place-items-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label="Create a company">
      <div class="w-full max-w-md rounded-2xl border border-base-300 bg-base-100 p-4 shadow-2xl">
        <div class="mb-3 flex items-center justify-between">
          <h3 class="text-base font-bold">Create a company</h3>
          <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="newCompanyOpen = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <p class="mb-3 text-xs opacity-60">
          A company owns projects, teams and boards. The default company already covers every project;
          create another one to separate another business.
        </p>
        <label class="form-control mb-2">
          <span class="label-text text-xs">Company name</span>
          <input v-model="newCompanyForm.name" class="input input-sm input-bordered" placeholder="Acme Studio"
                 autofocus @keydown.enter="createCompany" />
        </label>
        <label class="form-control mb-2">
          <span class="label-text text-xs">Description <span class="opacity-50">(optional)</span></span>
          <input v-model="newCompanyForm.description" class="input input-sm input-bordered" placeholder="What does it do?" />
        </label>
        <p v-if="newCompanyError" class="mb-2 text-xs text-error">{{ newCompanyError }}</p>
        <div class="flex justify-end gap-2">
          <button class="btn btn-ghost btn-sm" @click="newCompanyOpen = false">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="newCompanySaving || !newCompanyForm.name.trim()" @click="createCompany">
            <span v-if="newCompanySaving" class="loading loading-spinner loading-xs"></span>
            Create company
          </button>
        </div>
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
      :show-launcher="false"
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
      :chat-id="chatInitialId"
      @close="chatsOpen = false; chatTeamId = ''; chatInitialId = ''"
      @changed="onChatsChanged"
    />

    <!-- ── Advanced view ────────────────────────────────────────────────── -->
    <div v-if="exitOpen" class="fixed inset-0 z-[95] grid place-items-center bg-black/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Open advanced view">
      <div class="cp-card w-full max-w-[390px] p-4 md:p-5">
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="cp-eyebrow">Advanced view</p>
            <h2 class="mt-1 text-base font-extrabold">Open the project workspace</h2>
            <p class="mt-1 text-[10px] leading-relaxed cp-muted">
              The company keeps running. Use the advanced view for projects, files,
              boards and technical settings; come back here any time.
            </p>
          </div>
          <button class="cp-icon-btn !h-7 !w-7" aria-label="Close" @click="exitOpen = false"><i class="fa-solid fa-xmark text-[11px]"></i></button>
        </div>
        <button class="mt-3 flex w-full items-start gap-3 rounded-xl border border-[var(--cp-border)] p-3 text-left transition hover:border-[var(--cp-border-strong)] hover:bg-[var(--cp-surface-soft)]" @click="openAdvancedView">
          <span class="cp-icon-tile !h-8 !w-8"><i class="fa-solid fa-desktop text-[10px]"></i></span>
          <span class="min-w-0">
            <span class="block text-[11px] font-bold">Advanced view</span>
            <span class="mt-0.5 block text-[9px] cp-muted">Projects, files, boards and tools.</span>
          </span>
          <i class="fa-solid fa-arrow-right ml-auto mt-1 text-[9px] cp-muted-soft"></i>
        </button>
        <button class="cp-chip mt-3 w-full justify-center" @click="exitOpen = false">Stay in the company</button>
      </div>
    </div>

    <!-- ── Setup assistant (New company wizard) ──────────────────────────── -->
    <CompanyWizard
      :open="wizardOpen"
      :company-id="wizardCompanyId"
      :projects="companyReferences.projects || []"
      :references="companyReferences"
      @close="wizardOpen = false; wizardCompanyId = ''"
      @done="onWizardDone"
    />

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
