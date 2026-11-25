<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { API } from '@/api/api'
import { $storex } from '@/store'

import CompanyHome from '@/components/company/CompanyHome.vue'
import CompanyWork from '@/components/company/CompanyWork.vue'
import CompanyTickets from '@/components/company/CompanyTickets.vue'
import CompanyTeam from '@/components/company/CompanyTeam.vue'
import CompanyBusiness from '@/components/company/CompanyBusiness.vue'
import CompanyActivity from '@/components/company/CompanyActivity.vue'
import CompanySecurity from '@/components/company/CompanySecurity.vue'
import CompanySettings from '@/components/company/CompanySettings.vue'
import CompanyProjectsPanel from '@/components/company/CompanyProjectsPanel.vue'
import CompanyFiles from '@/components/company/CompanyFiles.vue'
import CompanyAssistant from '@/components/company/CompanyAssistant.vue'
import CompanyWorkplace from '@/components/company/CompanyWorkplace.vue'
import CompanyChatPanel from '@/components/company/CompanyChatPanel.vue'
import CompanyGoalDialog from '@/components/company/CompanyGoalDialog.vue'
import CompanyWizard from '@/components/company/CompanyWizard.vue'
import GenerateTeamDialog from '@/components/teams/GenerateTeamDialog.vue'
import { useCompanyI18n, setCompanyLanguage } from '@/components/company/i18n'

/**
 * Company shell — the AI company operating system.
 *
 * A business owner gets one place to see the truth, decide, talk to the team
 * and grow revenue. The assistant is always one click away; every workspace is
 * one click away and every number is live.
 *
 * Presentation only: the data flow, routing and actions are unchanged. The
 * shell is styled with the app's Tailwind/daisyUI theme (semantic tokens and
 * utilities), so the company workspace and the project workspace always share
 * one palette and follow the user's theme.
 */
const route = useRoute()
const router = useRouter()
const api = API.companies

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
// Company switcher of the left bar: the company name is the dropdown.
const companyMenuOpen = ref(false)
const askText = ref('')
const newCompanyOpen = ref(false)
const newCompanySaving = ref(false)
const newCompanyError = ref('')
const newCompanyForm = ref({ name: '', description: '' })
// Setup assistant (the "New company" wizard). The first-run gate lives in
// App.vue; this modal is the optional re-run entry point from the user menu (and
// the assistant of a company that was just created).
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
// The company shell is not a separate visual universe: it consumes the same
// daisyUI theme as the project workspace. The quick toggle writes the app-wide
// theme instead of a local one, so switching views never changes the palette.
const DARK_THEMES = new Set([
  'dark', 'codx-junior-dark', 'synthwave', 'halloween', 'forest', 'black',
  'luxury', 'dracula', 'night', 'coffee', 'dim', 'sunset', 'abyss'
])
const isDarkTheme = computed(() => DARK_THEMES.has($storex.ui.theme))
const { t, locale, languages, setLanguage } = useCompanyI18n()
const sidebarCollapsed = ref(false)
try { sidebarCollapsed.value = localStorage.getItem(SIDEBAR_KEY) === '1' } catch { /* noop */ }

function toggleCompanyTheme() {
  $storex.ui.setTheme(isDarkTheme.value ? 'codx-junior-light' : 'codx-junior-dark')
  $storex.ui.saveState()
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
  { id: 'tickets', labelKey: 'nav.tickets', icon: 'fa-solid fa-ticket', hintKey: 'Project tickets, unattended runs and delivery review' },
  { id: 'team', labelKey: 'nav.team', icon: 'fa-solid fa-people-group', hintKey: 'Teams and people' },
  { id: 'projects', labelKey: 'nav.projects', icon: 'fa-solid fa-cubes', hintKey: 'Projects this company runs' },
  { id: 'files', labelKey: 'nav.files', icon: 'fa-solid fa-folder-tree', hintKey: 'Company files and project folders' },
  { id: 'business', labelKey: 'nav.business', icon: 'fa-solid fa-chart-line', hintKey: 'Revenue, margin, pipeline' },
  { id: 'activity', labelKey: 'nav.activity', icon: 'fa-solid fa-wave-square', hintKey: 'What happened' },
  { id: 'security', labelKey: 'nav.security', icon: 'fa-solid fa-shield-halved', hintKey: 'CISO posture' },
  { id: 'settings', labelKey: 'nav.settings', icon: 'fa-solid fa-sliders', hintKey: 'Company & controls' },
  { id: 'meet', labelKey: 'nav.meet', icon: 'fa-solid fa-video', hintKey: 'Meet', workplace: true },
  { id: 'coworking', labelKey: 'nav.coworking', icon: 'fa-regular fa-window-maximize', hintKey: 'CoWorking', workplace: true },
  { id: 'calendar', labelKey: 'nav.calendar', icon: 'fa-regular fa-calendar-days', hintKey: 'Calendar', workplace: true }
]

const NAV_GROUPS = [
  { id: 'workspace', labelKey: 'group.workspace', ids: ['home', 'work', 'tickets', 'team', 'projects', 'files'] },
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
// A company owns MANY projects. Every company surface works with the projects
// the company actually links — the same list the Projects section shows — and
// never with an "active project" or with a child project inherited from a
// linked root. The store getter also falls back cleanly while the snapshot is
// still loading, and updates as soon as a project is linked.
const companyProjects = computed(() => $storex.company.linkedProjects || [])
const companyProjectIds = computed(() => $storex.company.linkedProjectIds || [])
// Full discovered project of the first available company project. Company
// components that need a project API client use this instead of the globally
// active project, which may belong to another company after a switch.
const primaryCompanyProject = computed(() => {
  const linked = companyProjects.value.find(item => item.available !== false) || companyProjects.value[0]
  if (!linked?.project_id) return null
  return ($storex.projects.allProjects || []).find(item => String(item.project_id) === linked.project_id) || null
})
const workingProjectInCompany = computed(() => {
  const active = workingProject.value
  if (!active) return null
  return companyProjectIds.value.includes(String(active.project_id)) ? active : null
})
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
    // The company shell is project-independent: it aggregates every project the
    // company links. Never change the global active project just by opening a
    // company; only an explicit "open project/workspace" action does that.
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
    // No project scope: the pickers must offer the profiles of every project
    // the company runs, not only the one that happened to be active.
    companyReferences.value = await api.references()
  } catch (error) {
    // Pickers are optional context; the rest of the shell keeps working.
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
  const catalog = $storex.projects.allProjects || []
  const owned = companyProjects.value.find(item =>
    item.project_name === projectName
    || item.discovered_name === projectName
    || item.project_id === projectName
  )
  // A ticket can target a child project that the company sees through an
  // inherited link. It is not a company link of its own, so fall back to the
  // discovered catalog instead of refusing to open a ticket's project.
  const project = owned
    ? catalog.find(item => String(item.project_id) === owned.project_id)
    : catalog.find(item => String(item.project_id) === String(projectName)
      || item.project_name === projectName)
  if (!owned && !project) {
    notify('That project is not linked to this company.', 'error')
    return
  }
  const slug = companySlug()
  if (!slug) return
  if (project) {
    await $storex.projects.activeProjectChanged(project).catch(() => {})
  }
  const discoveredName = owned?.discovered_name
    || project?.project_name
    || projectName
  router.push({
    name: 'home',
    params: { companySlug: slug, projectName: discoveredName }
  }).catch(() => {})
}

async function openAdvancedView() {
  const slug = companySlug()
  if (!slug) return
  exitOpen.value = false

  // Resolve the target from the company's linked projects only. The advanced
  // workspace may open any of them (a manually added child is a company project
  // too), but never a project that belongs to another company.
  const allProjects = $storex.projects.allProjects || []
  const owned = companyProjects.value
    .map(option => allProjects.find(project =>
      String(project.project_id) === String(option.project_id)
    ) || option)
    .filter(project => project?.project_id && project?.project_name)
  const activeOwned = workingProjectInCompany.value
  const selected = (activeOwned && owned.find(project =>
    String(project.project_id) === String(activeOwned.project_id)
  )) || owned[0]
  if (!selected) {
    setSection('projects')
    notify('Link or create a project before switching to the workspace view.', 'error')
    return
  }

  // Make the workspace project the active one BEFORE navigating: the route
  // guard, the desktop shell and every panel read the project from the store.
  if (String(workingProject.value?.project_id || '') !== String(selected.project_id)) {
    try {
      await $storex.projects.activeProjectChanged(selected)
    } catch (error) {
      notifyError(error)
      return
    }
  }

  // Phones cannot render the dockview desktop (the router keeps sending that
  // route back to the company). Open the project home instead: it is the same
  // workspace on a mobile-safe screen, so the switch is never a no-op.
  const routeName = $storex.ui.isMobile ? 'home' : 'codx-junior-split'
  try {
    await router.push({
      name: routeName,
      params: {
        companySlug: slug,
        projectName: selected.project_name
      }
    })
  } catch (error) {
    notify(`Could not switch to the workspace view: ${errorMessage(error)}`, 'error')
  }
}

function onSelectCompany(companyId) {
  companyMenuOpen.value = false
  if (!companyId || companyId === activeId.value) return
  selectCompany(companyId)
}

function onOpenNewCompany() {
  companyMenuOpen.value = false
  openNewCompany()
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
// The wizard state lives on the server (`/api/companies/onboarding`). The shell
// only renders after the first-run gate in App.vue is satisfied; from here the
// assistant is re-runnable at any time (and configures a newly created company).
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
  // The destination project must be one the company links. Prefer the active
  // project when it is part of the company, otherwise fall back to the first
  // available linked project; the task dialog itself lists every company
  // project so the owner can change it.
  const available = companyProjects.value.find(project => project.available !== false)
  const linked = workingProjectInCompany.value || available || companyProjects.value[0]
  const project = linked?.project_id
    ? ($storex.projects.allProjects || []).find(item => String(item.project_id) === linked.project_id)
    : null
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
    files: 'files',
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
  <div class="flex h-full w-full overflow-hidden bg-base-100 text-base-content">
    <div class="flex h-full w-full gap-0 p-0 md:gap-3 md:p-3 xl:gap-4 xl:p-4">
      <!-- ── Sidebar ──────────────────────────────────────────────────────── -->
      <aside
        v-if="!isMobile"
        class="rounded-box border border-base-300 bg-base-100 flex shrink-0 flex-col overflow-y-auto overflow-x-hidden transition-[width] duration-200 ease-out"
        :style="{ width: sidebarCollapsed ? '76px' : '236px' }"
        aria-label="Company navigation"
      >
        <!-- Company switcher: the name itself is the dropdown. It lists every
             company the user can open and offers creating a new one, so
             switching context never needs a second control. -->
        <div class="relative px-3.5 pb-2 pt-4">
          <button
            type="button"
            class="flex w-full items-center gap-2.5 rounded-[11px] px-1 py-0.5 text-left transition hover:bg-base-200"
            :class="sidebarCollapsed ? 'justify-center' : ''"
            :title="company?.name || 'Select company'"
            aria-haspopup="listbox"
            :aria-expanded="companyMenuOpen"
            @click="companyMenuOpen = !companyMenuOpen"
          >
            <span class="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-[11px] bg-accent text-base font-black text-accent-content shadow-sm">
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
            <template v-if="!sidebarCollapsed">
              <div class="min-w-0 flex-1">
                <p class="truncate text-base font-extrabold leading-tight tracking-tight">{{ company?.name || 'Your company' }}</p>
                <p class="mt-0.5 truncate text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40">AI company OS</p>
              </div>
              <i
                class="fa-solid fa-chevron-down shrink-0 text-[11px] text-base-content/40 transition-transform"
                :class="companyMenuOpen ? 'rotate-180' : ''"
              ></i>
            </template>
            <i v-else class="fa-solid fa-chevron-down text-[10px] text-base-content/40"></i>
          </button>

          <div v-if="companyMenuOpen" class="fixed inset-0 z-[70]" @click="companyMenuOpen = false"></div>
          <div
            v-if="companyMenuOpen"
            class="absolute left-2 right-2 top-[calc(100%-0.25rem)] z-[71] overflow-hidden rounded-[12px] border border-base-300 bg-base-100 p-1 shadow-2xl"
            role="listbox"
            aria-label="Companies"
          >
            <button
              v-for="item in companies"
              :key="item.id"
              type="button"
              class="flex w-full items-center gap-2 rounded-[8px] px-2 py-1.5 text-left text-xs font-semibold transition hover:bg-base-200"
              :class="item.id === activeId ? 'text-base-content' : 'text-base-content/60'"
              role="option"
              :aria-selected="item.id === activeId"
              @click="onSelectCompany(item.id)"
            >
              <i class="fa-solid w-3 text-center text-[10px]" :class="item.id === activeId ? 'fa-circle-check text-accent-ink' : 'fa-circle opacity-30'"></i>
              <span class="min-w-0 flex-1 truncate">{{ item.name }}</span>
            </button>
            <div class="my-1 border-t border-base-300"></div>
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-[8px] px-2 py-1.5 text-left text-xs font-semibold text-base-content transition hover:bg-base-200"
              @click="onOpenNewCompany"
            >
              <i class="fa-solid fa-plus w-3 text-center text-[11px]"></i>
              <span class="min-w-0 flex-1 truncate">New company</span>
            </button>
          </div>
        </div>

        <nav class="flex-1 overflow-y-auto px-2.5 pb-1 pt-1">
          <template v-for="group in navGroups" :key="group.id">
            <p
              v-if="!sidebarCollapsed"
              class="px-2 pb-1.5 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40"
            >
              {{ group.label }}
            </p>
            <div v-else class="mx-2 my-2 border-t border-base-300 first:mt-1"></div>
            <button
              v-for="item in group.items"
              :key="item.id"
              type="button"
              class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
              :class="[section === item.id ? 'bg-primary/10 font-bold text-primary-ink' : '', sidebarCollapsed ? 'justify-center' : '']"
              :title="sidebarCollapsed ? item.label : item.hint"
              :aria-current="section === item.id ? 'page' : undefined"
              @click="setSection(item.id)"
            >
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition" :class="section === item.id ? 'border-transparent bg-accent text-accent-content' : ''"><i :class="item.icon" class="text-xs"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
              <span
                v-if="item.badge"
                class="inline-grid h-5 min-w-5 place-items-center rounded-full bg-base-300 px-1.5 text-xs font-bold text-base-content/60"
                :class="[
                  item.id === 'work' && attention.length ? '!bg-warning/10 !text-primary-ink' : '',
                  sidebarCollapsed ? '!absolute !right-0.5 !top-0.5 !h-4 !min-w-4 !px-1 !text-[10px]' : ''
                ]"
              >
                {{ item.badge }}
              </span>
            </button>
          </template>

          <p
            v-if="!sidebarCollapsed"
            class="px-2 pb-1.5 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40"
          >{{ t('group.workplace') }}</p>
          <div v-else class="mx-2 my-2 border-t border-base-300"></div>
          <button
            v-for="link in workplaceLinks"
            :key="link.id"
            type="button"
            class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
            :class="[section === link.id ? 'bg-primary/10 font-bold text-primary-ink' : '', sidebarCollapsed ? 'justify-center' : '']"
            :title="link.label"
            @click="openWorkplace(link.id)"
          >
            <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition" :class="section === link.id ? 'border-transparent bg-accent text-accent-content' : ''"><i :class="link.icon" class="text-xs"></i></span>
            <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ link.label }}</span>
          </button>

          <!-- Messenger: the rail reaches the company conversations in one
               click. It opens the same chats panel as the top bar, so there is
               one source of truth for channels. -->
          <button
            type="button"
            class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
            :class="[chatsOpen ? 'bg-primary/10 font-bold text-primary-ink' : '', sidebarCollapsed ? 'justify-center' : '']"
            :title="t('nav.messenger')"
            @click="openChat()"
          >
            <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition" :class="chatsOpen ? 'border-transparent bg-accent text-accent-content' : ''"><i class="fa-regular fa-comments text-xs"></i></span>
            <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ t('nav.messenger') }}</span>
            <span
              v-if="counts.chats"
              class="inline-grid h-5 min-w-5 place-items-center rounded-full bg-base-300 px-1.5 text-xs font-bold text-base-content/60"
              :class="sidebarCollapsed ? '!absolute !right-0.5 !top-0.5 !h-4 !min-w-4 !px-1 !text-[10px]' : ''"
            >
              {{ counts.chats }}
            </span>
          </button>

          <template v-if="recentChats.length">
            <p
              v-if="!sidebarCollapsed"
              class="px-2 pb-1.5 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40"
            >{{ t('group.channels') }}</p>
            <div v-else class="mx-2 my-2 border-t border-base-300"></div>
            <button
              v-for="chat in recentChats"
              :key="chat.chat_id"
              type="button"
              class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
              :class="sidebarCollapsed ? 'justify-center' : ''"
              :title="chatName(chat)"
              @click="openChatChannel(chat)"
            >
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition"><i class="fa-solid fa-hashtag text-xs"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate">{{ chatName(chat) }}</span>
            </button>
            <button
              type="button"
              class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
              :class="sidebarCollapsed ? 'justify-center' : ''"
              :title="t('nav.allChannels')"
              @click="openChat()"
            >
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition"><i class="fa-solid fa-ellipsis text-xs"></i></span>
              <span v-if="!sidebarCollapsed" class="min-w-0 flex-1 truncate text-xs">{{ t('nav.allChannels') }}</span>
            </button>
          </template>
        </nav>

        <div class="mt-auto space-y-2 px-2.5 pb-3 pt-2">
          <div v-if="!sidebarCollapsed" class="relative overflow-hidden rounded-box border border-white/10 bg-neutral p-3.5 text-neutral-content">
            <div class="flex items-start justify-between">
              <span class="grid h-6 w-6 place-items-center rounded-field bg-accent text-accent-content"><i class="fa-solid fa-bolt text-[11px]"></i></span>
              <span class="rounded-full border border-neutral-content/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.14em] text-neutral-content/50">Company AI</span>
            </div>
            <p class="mt-2.5 text-sm font-bold leading-snug">Put your AI team to work</p>
            <p class="mt-1 text-[11px] leading-relaxed text-neutral-content/60">
              {{ activeActors.length }} teammate{{ activeActors.length === 1 ? '' : 's' }} on duty · {{ counts.teams || 0 }} teams
            </p>
            <button type="button" class="flex h-8 w-full items-center justify-center gap-1.5 rounded-field bg-accent text-xs font-bold text-accent-content transition hover:bg-accent/85 mt-2.5" @click="askAssistant('Give me today\'s executive brief')">
              <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
              Ask codx-junior
            </button>
          </div>
          <button
            v-else
            type="button"
            class="grid h-6 w-6 place-items-center rounded-field bg-accent text-accent-content mx-auto !h-8 !w-8"
            title="Ask codx-junior"
            aria-label="Ask codx-junior"
            @click="askAssistant('Give me today\'s executive brief')"
          >
            <i class="fa-solid fa-bolt text-xs"></i>
          </button>

          <button
            type="button"
            class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content !py-1.5"
            :class="sidebarCollapsed ? 'justify-center' : ''"
            :title="'Switch to workspace view'"
            aria-label="Switch to workspace view"
            @click="exitOpen = true"
          >
            <i class="fa-solid fa-desktop w-4 text-center text-xs"></i>
            <span v-if="!sidebarCollapsed" class="text-xs">Workspace view</span>
          </button>

          <button
            type="button"
            class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content !py-1.5"
            :class="sidebarCollapsed ? 'justify-center' : ''"
            :title="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            @click="toggleSidebar"
          >
            <i :class="sidebarCollapsed ? 'fa-solid fa-angles-right' : 'fa-solid fa-angles-left'" class="w-4 text-center text-xs"></i>
            <span v-if="!sidebarCollapsed" class="text-xs">Collapse sidebar</span>
          </button>
        </div>
      </aside>

      <!-- Mobile nav overlay -->
      <div v-if="navOpen && isMobile" class="fixed inset-0 z-[60]">
        <div class="absolute inset-0 bg-black/45 backdrop-blur-sm" @click="navOpen = false"></div>
        <aside class="relative flex h-full w-[86%] max-w-[310px] flex-col border-r border-base-300 bg-base-100 p-3 shadow-2xl">
          <div class="flex items-center gap-2.5 pb-2">
            <span class="grid h-9 w-9 place-items-center overflow-hidden rounded-[11px] bg-accent text-base font-black text-accent-content">
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
              <p class="truncate text-base font-extrabold">{{ company?.name || 'Company' }}</p>
              <p class="text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40">AI company OS</p>
            </div>
            <button class="btn btn-ghost btn-sm btn-square" aria-label="Close menu" @click="navOpen = false"><i class="fa-solid fa-xmark text-base"></i></button>
          </div>
          <div v-if="companies.length" class="pb-2">
            <select
              class="select select-sm select-bordered w-full"
              :value="activeId"
              aria-label="Select company"
              @change="selectCompany($event.target.value)"
            >
              <option v-for="item in companies" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
          </div>
          <nav class="flex-1 overflow-y-auto">
            <template v-for="group in navGroups" :key="group.id">
              <p class="px-2 pb-1 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40">{{ group.label }}</p>
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5"
                :class="section === item.id ? 'bg-primary/10 font-bold text-primary-ink' : ''"
                @click="setSection(item.id)"
              >
                <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition" :class="section === item.id ? 'border-transparent bg-accent text-accent-content' : ''"><i :class="item.icon" class="text-xs"></i></span>
                <span class="flex-1 text-sm">{{ item.label }}</span>
                <span v-if="item.badge" class="inline-grid h-5 min-w-5 place-items-center rounded-full bg-base-300 px-1.5 text-xs font-bold text-base-content/60">{{ item.badge }}</span>
              </button>
            </template>
            <p class="px-2 pb-1 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40">{{ t('group.workplace') }}</p>
            <div class="grid grid-cols-3 gap-1 px-1">
              <button v-for="link in workplaceLinks" :key="link.id" type="button" class="btn btn-xs !h-8 justify-center !px-1 !text-[10px]" :class="section === link.id ? '!border-transparent !bg-accent !text-accent-content' : ''" @click="openWorkplace(link.id); navOpen = false">
                <i :class="link.icon" class="text-[10px]"></i>
                <span class="truncate">{{ link.label }}</span>
              </button>
            </div>
            <button type="button" class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5 mt-1" @click="openChat(); navOpen = false">
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition" :class="chatsOpen ? 'border-transparent bg-accent text-accent-content' : ''"><i class="fa-regular fa-comments text-xs"></i></span>
              <span class="flex-1 text-sm">{{ t('nav.messenger') }}</span>
              <span v-if="counts.chats" class="inline-grid h-5 min-w-5 place-items-center rounded-full bg-base-300 px-1.5 text-xs font-bold text-base-content/60">{{ counts.chats }}</span>
            </button>
            <template v-if="recentChats.length">
              <p class="px-2 pb-1 pt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-base-content/40">{{ t('group.channels') }}</p>
              <button v-for="chat in recentChats" :key="chat.chat_id" type="button" class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content mb-0.5" @click="openChatChannel(chat); navOpen = false">
                <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition"><i class="fa-solid fa-hashtag text-xs"></i></span>
                <span class="flex-1 truncate text-sm">{{ chatName(chat) }}</span>
              </button>
            </template>
          </nav>
          <div class="mt-2 space-y-1 border-t border-base-300 pt-3">
            <div class="rounded-xl bg-base-200 p-2">
              <div class="flex items-center gap-2">
                <span class="relative grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full bg-accent/10">
                  <span class="absolute inset-0 grid place-items-center text-[10px] font-extrabold text-accent-ink">{{ userInitials }}</span>
                  <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-xs font-bold">{{ userLabel }}</p>
                  <p class="truncate text-[10px] text-base-content/40">{{ roleLabel }} · {{ company?.name || 'Company' }}</p>
                </div>
                <button class="btn btn-ghost btn-sm btn-square !h-7 !w-7" :aria-label="isDarkTheme ? 'Light mode' : 'Dark mode'" @click="toggleCompanyTheme">
                  <i :class="isDarkTheme ? 'fa-solid fa-sun' : 'fa-solid fa-moon'" class="text-xs"></i>
                </button>
              </div>
              <label class="flex min-w-0 flex-col gap-1 mt-2">
                <span class="text-xs font-semibold text-base-content/60">{{ t('shell.language') }}</span>
                <select class="select select-sm select-bordered w-full" :value="locale" @change="changeLanguage($event.target.value)">
                  <option v-for="language in languages" :key="language.code" :value="language.code">{{ language.label }}</option>
                </select>
              </label>
              <div class="mt-1.5 flex gap-1.5">
                <button class="btn btn-xs flex-1 justify-center !h-7 !text-[10px]" @click="openUserSettings">{{ t('shell.settings') }}</button>
                <button class="btn btn-xs flex-1 justify-center !h-7 !text-[10px] !text-error-ink" @click="logoutCompany">{{ t('shell.logout') }}</button>
              </div>
            </div>
            <button type="button" class="relative flex w-full items-center gap-2.5 rounded-selector px-2.5 py-2 text-left text-sm font-semibold text-base-content/60 transition-colors hover:bg-base-200 hover:text-base-content" @click="exitOpen = true; navOpen = false">
              <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector border border-base-300 bg-base-100 text-base-content/60 transition"><i class="fa-solid fa-arrow-right-from-bracket text-xs"></i></span>
              <span class="flex-1 text-sm">Switch to workspace view</span>
            </button>
          </div>
        </aside>
      </div>

      <!-- ── Main column ──────────────────────────────────────────────────── -->
      <div class="bg-base-100 flex min-w-0 flex-1 flex-col overflow-hidden">
        <header class="border-b border-base-300 shrink-0">
          <div class="flex h-[60px] items-center gap-2 px-3 md:h-[64px] md:px-5">
            <button v-if="isMobile" class="btn btn-ghost btn-sm btn-square" aria-label="Open menu" @click="navOpen = true">
              <i class="fa-solid fa-bars-staggered text-base"></i>
            </button>
            <div class="flex min-w-0 flex-1 items-center">
              <label class="flex h-10 w-full items-center gap-2 rounded-field border border-base-300 bg-base-200 px-3" :class="isMobile ? 'max-w-none' : 'max-w-[360px]'">
                <i class="fa-solid fa-wand-magic-sparkles text-xs text-accent-ink"></i>
                <input
                  v-model="askText"
                  type="text"
                  class="min-w-0 flex-1 border-0 bg-transparent text-sm outline-none placeholder:text-base-content/40"
                  :placeholder="t('shell.ask')"
                  aria-label="Ask codx-junior"
                  @click="askAssistant()"
                  @keydown.enter="sendAsk"
                />
                <span v-if="!isMobile" class="kbd kbd-sm">⌘K</span>
              </label>
            </div>

            <div class="flex shrink-0 items-center gap-0.5 md:gap-1">
              <!-- No active-project selector: the company shell aggregates every
                   project linked to the company. Project choice belongs to the
                   action that needs it (goal, task, advanced workspace). -->
              <button
                v-if="!isMobile"
                class="btn btn-ghost btn-sm btn-square"
                :title="isDarkTheme ? 'Switch to light mode' : 'Switch to dark mode'"
                :aria-label="isDarkTheme ? 'Switch to light mode' : 'Switch to dark mode'"
                :aria-pressed="isDarkTheme"
                @click="toggleCompanyTheme"
              >
                <i :class="isDarkTheme ? 'fa-solid fa-sun' : 'fa-solid fa-moon'" class="text-sm"></i>
              </button>
              <span v-if="!isMobile">
                <button class="btn btn-ghost btn-sm btn-square" :disabled="busy" title="Refresh company data" aria-label="Refresh company data" @click="selectCompany(activeId)">
                  <i class="fa-solid fa-rotate text-xs" :class="busy ? 'animate-spin' : ''"></i>
                </button>
              </span>
              <span v-if="!isMobile">
                <button class="btn btn-ghost btn-sm btn-square" title="Company chats" aria-label="Company chats" @click="openChat()">
                  <span class="relative">
                    <i class="fa-regular fa-comments text-base"></i>
                    <span v-if="counts.chats" class="absolute -right-1.5 -top-1.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-accent px-0.5 text-[10px] font-bold text-accent-content">{{ counts.chats }}</span>
                  </span>
                </button>
              </span>
              <button class="btn btn-ghost btn-sm btn-square relative" title="Needs your attention" aria-label="Needs your attention" @click="setSection('work')">
                <i class="fa-regular fa-bell text-base"></i>
                <span v-if="attention.length" class="absolute right-1 top-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-warning px-0.5 text-[10px] font-bold text-warning-content">{{ attention.length }}</span>
              </button>
              <span v-if="!isMobile">
                <button class="btn btn-ghost btn-sm btn-square" title="Company settings" aria-label="Company settings" @click="setSection('settings')">
                  <i class="fa-solid fa-gear text-sm"></i>
                </button>
              </span>

              <div v-if="!isMobile" class="relative ml-0.5">
                <button
                  type="button"
                  class="flex items-center gap-1 rounded-full p-0.5 transition hover:bg-base-200"
                  :title="userLabel"
                  aria-label="User menu"
                  :aria-expanded="userMenuOpen"
                  @click.stop="userMenuOpen = !userMenuOpen"
                >
                  <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-accent/10 ring-2 ring-base-100">
                    <span class="absolute inset-0 grid place-items-center text-[11px] font-extrabold text-accent-ink">{{ userInitials }}</span>
                    <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                  </span>
                  <i class="fa-solid fa-chevron-down pr-1 text-[10px] text-base-content/40"></i>
                </button>
                <div v-if="userMenuOpen" class="fixed inset-0 z-40" @click="userMenuOpen = false"></div>
                <div v-if="userMenuOpen" class="absolute right-0 top-full z-50 mt-2 w-[242px] rounded-2xl border border-base-300 bg-base-100 p-1.5 shadow-2xl" role="menu">
                  <div class="flex items-center gap-2 rounded-xl bg-base-200 p-2">
                    <span class="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-accent/10">
                      <span class="absolute inset-0 grid place-items-center text-[11px] font-extrabold text-accent-ink">{{ userInitials }}</span>
                      <img v-if="userAvatar" :src="userAvatar" :alt="userLabel" class="relative h-full w-full object-cover" @error="hideBrokenImage" />
                    </span>
                    <div class="min-w-0">
                      <p class="truncate text-xs font-bold">{{ userLabel }}</p>
                      <p class="truncate text-[10px] text-base-content/40">{{ roleLabel }} · {{ company?.name || 'Company' }}</p>
                    </div>
                  </div>
                  <p class="px-2 pb-1 pt-2 text-xs font-bold uppercase tracking-[0.14em] text-base-content/40 !text-[10px]">{{ t('shell.language') }}</p>
                  <div class="grid grid-cols-2 gap-1">
                    <button
                      v-for="language in languages"
                      :key="language.code"
                      class="flex items-center justify-between rounded-lg px-2 py-1.5 text-left text-xs transition hover:bg-base-200"
                      :class="locale === language.code ? 'bg-accent/10 font-bold text-accent-ink' : 'text-base-content'"
                      @click="changeLanguage(language.code)"
                    >
                      {{ language.label }}
                      <i v-if="locale === language.code" class="fa-solid fa-check text-[10px]"></i>
                    </button>
                  </div>
                  <div class="my-1 border-t border-base-300"></div>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs font-semibold transition hover:bg-base-200" @click="openUserSettings">
                    <i class="fa-solid fa-sliders w-4 text-center text-xs text-base-content/60"></i>{{ t('shell.settings') }}
                  </button>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs font-semibold transition hover:bg-base-200" @click="openSetupAssistant">
                    <i class="fa-solid fa-wand-magic-sparkles w-4 text-center text-xs text-base-content/60"></i>Setup assistant
                  </button>
                  <button class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs font-semibold text-error-ink transition hover:bg-error/10" @click="logoutCompany">
                    <i class="fa-solid fa-arrow-right-from-bracket w-4 text-center text-xs"></i>{{ t('shell.logout') }}
                  </button>
                </div>
              </div>

              <button v-if="canManage" class="btn btn-sm btn-secondary ml-1" aria-label="New goal" @click="openGoal()">
                <i class="fa-solid fa-plus text-xs"></i>
                <span v-if="!isMobile">{{ t('shell.newGoal') }}</span>
              </button>
            </div>
          </div>

          <!-- Mobile section pills -->
          <div v-if="isMobile" class="flex gap-1 overflow-x-auto border-t border-base-300 px-2 py-2">
            <button
              v-for="item in navItems"
              :key="item.id"
              class="btn btn-xs shrink-0"
              :class="section === item.id ? '!border-transparent !bg-accent !text-accent-content' : ''"
              @click="setSection(item.id)"
            >
              {{ item.label }}<span v-if="item.badge" class="opacity-60">{{ item.badge }}</span>
            </button>
          </div>
        </header>

        <main class="min-h-0 flex-1 overflow-y-auto">
          <div class="w-full px-3 py-3 md:px-5 md:py-5">
            <div v-if="loading" class="grid h-[60vh] place-items-center">
              <div class="text-center text-base-content/60">
                <span class="loading loading-spinner loading-lg text-primary-ink"></span>
                <p class="mt-2 text-sm">Opening your company…</p>
              </div>
            </div>

            <div v-else-if="!companies.length" class="grid min-h-[70vh] place-items-center">
              <div class="rounded-box border border-base-300 bg-base-100 shadow-sm w-full max-w-md p-6 text-center md:p-8">
                <div class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent text-accent-content shadow-lg shadow-primary/25">
                  <i class="fa-solid fa-building-columns text-xl"></i>
                </div>
                <h2 class="mt-4 text-xl font-extrabold tracking-tight">{{ t('shell.noCompany') }}</h2>
                <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-base-content/60">
                  {{ t('shell.createWorkspace') }}
                </p>
                <button class="btn btn-sm btn-primary mx-auto mt-5 h-10 px-4" @click="exitOpen = true">
                  <i class="fa-solid fa-desktop text-xs"></i> Workspace view
                </button>
              </div>
            </div>

            <template v-else-if="company">
              <div :key="section">
                  <CompanyHome
                    v-if="section === 'home'"
                    :snapshot="snapshot"
                    :actors="companyActors.actors"
                    :can-manage="canManage"
                    :can-admin="canAdmin"
                    @assistant="openAssistantFor"
                    @open-work="onWorkFocus"
                    @open-tickets="setSection('tickets')"
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
                  <CompanyTickets
                    v-else-if="section === 'tickets'"
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :can-manage="canManage"
                    @assistant="openAssistantFor"
                    @refresh="refreshSnapshot"
                    @open-project="openProjectByName"
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
                  <CompanyFiles
                    v-else-if="section === 'files'"
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :can-manage="canManage"
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
                    :company-id="activeId"
                    :snapshot="snapshot"
                    :project-ids="companyProjectIds"
                    :can-manage="canManage"
                    @close="setSection('home')"
                  />
                  <CompanySecurity
                    v-else-if="section === 'security'"
                    :company-id="activeId"
                    :project-ids="companyProjectIds"
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
        <label class="mb-2 flex flex-col gap-1">
          <span class="text-xs font-semibold text-base-content/60">Company name</span>
          <input v-model="newCompanyForm.name" class="input input-sm input-bordered w-full" placeholder="Acme Studio"
                 autofocus @keydown.enter="createCompany" />
        </label>
        <label class="mb-2 flex flex-col gap-1">
          <span class="text-xs font-semibold text-base-content/60">Description <span class="opacity-50">(optional)</span></span>
          <input v-model="newCompanyForm.description" class="input input-sm input-bordered w-full" placeholder="What does it do?" />
        </label>
        <p v-if="newCompanyError" class="mb-2 text-xs text-error-ink">{{ newCompanyError }}</p>
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
      :project="primaryCompanyProject"
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
      <div class="rounded-box border border-base-300 bg-base-100 shadow-sm w-full max-w-[390px] p-4 md:p-5">
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">Workspace view</p>
            <h2 class="mt-1 text-base font-extrabold">Switch to the project workspace</h2>
            <p class="mt-1 text-xs leading-relaxed text-base-content/60">
              The company keeps running. Use the workspace view for projects, files,
              boards and technical settings; come back here any time.
            </p>
          </div>
          <button class="btn btn-ghost btn-sm btn-square !h-7 !w-7" aria-label="Close" @click="exitOpen = false"><i class="fa-solid fa-xmark text-xs"></i></button>
        </div>
        <button class="mt-3 flex w-full items-start gap-3 rounded-xl border border-base-300 p-3 text-left transition hover:border-base-content/20 hover:bg-base-200" @click="openAdvancedView">
          <span class="grid h-8 w-8 shrink-0 place-items-center rounded-selector bg-base-300 text-base-content/60 !h-8 !w-8"><i class="fa-solid fa-desktop text-xs"></i></span>
          <span class="min-w-0">
            <span class="block text-xs font-bold">Switch to workspace view</span>
            <span class="mt-0.5 block text-[11px] text-base-content/60">Projects, files, boards and tools.</span>
          </span>
          <i class="fa-solid fa-arrow-right ml-auto mt-1 text-[11px] text-base-content/40"></i>
        </button>
        <button class="btn btn-xs mt-3 w-full justify-center" @click="exitOpen = false">Stay in the company</button>
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
