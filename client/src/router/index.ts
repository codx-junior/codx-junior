import { createRouter, createWebHistory } from 'vue-router'
import { $storex } from '../store'
import { API } from '@/api/api'
import Navigate from './navigate'

// ─────────────────────────────────────────────────────────────────────────────
// LAZY ROUTE COMPONENTS
// Each screen is its own chunk: navigating fetches only what the route needs
// instead of evaluating the whole application on first paint.
// ─────────────────────────────────────────────────────────────────────────────
const AppRouteView = () => import('@/views/AppRouteView.vue')
const QuickChatView = () => import('@/views/QuickChatView.vue')
const ChatsView = () => import('@/views/ChatsView.vue')
const WorkspaceView = () => import('@/views/WorkspaceView.vue')
const MessengerView = () => import('@/views/MessengerView.vue')
const ChannelView = () => import('@/views/ChannelView.vue')
const MeetView = () => import('@/views/MeetView.vue')
const MeetSummaryView = () => import('@/views/MeetSummaryView.vue')
const BoardManagementView = () => import('@/views/BoardManagementView.vue')
const TeamView = () => import('@/views/TeamView.vue')
const CompanyView = () => import('@/views/CompanyView.vue')
const LegacyRedirectView = () => import('@/views/LegacyRedirectView.vue')
const MetricsDashboard = () => import('@/components/analytics/MetricsDashboard.vue')
const Desktop = () => import('@/components/desktop/Desktop.vue')
const KanbanBoardView = () => import('@/components/kanban/board/KanbanBoardView.vue')
const BrowsersSessionManager = () => import('@/components/browsers/BrowsersSessionManager.vue')
const ProfileEditorView = () => import('@/views/ProfileEditorView.vue')

// ─────────────────────────────────────────────────────────────────────────────
// COMPANY-FIRST URL MODEL
//
//   /                                    → /<default-company-slug>
//   /<companySlug>                       → company home
//   /<companySlug>/<section>             → company section
//   /<companySlug>/workspace/<project>…  → advanced (project) view
//
// The company is the root. Legacy project URLs are resolved by the guard and
// redirected to the canonical company/workspace path.
// ─────────────────────────────────────────────────────────────────────────────
const WELCOME_APP = 'task-list'

const COMPANY_SECTIONS = [
  'home',
  'work',
  'tickets',
  'team',
  'projects',
  'files',
  'business',
  'activity',
  'security',
  'settings',
  'meet',
  'coworking',
  'calendar'
]

const COMPANY_SECTION_RE = COMPANY_SECTIONS.join('|')

// Mobile metadata helper (same convention as before).
function mobile(title: string, tab = 'more', extra: Record<string, any> = {}) {
  return { mobile: { title, tab, ...extra } }
}

// Project-scoped screens. Each path below is relative to the project root and
// is mounted under /:companySlug/workspace/:projectName.
const PROJECT_ROUTES = [
  {
    path: '',
    name: 'home',
    component: AppRouteView,
    meta: { app: WELCOME_APP, ...mobile('Home', 'home', { hideTopBar: true }) }
  },
  {
    path: '/chats',
    name: 'chats',
    component: ChatsView,
    meta: { ...mobile('Chats', 'chats', { hideTopBar: true }) }
  },
  {
    path: '/new-chat',
    name: 'new-chat',
    component: QuickChatView,
    meta: { ...mobile('New chat', 'chats') }
  },
  {
    path: '/chats/:chatId/:chatName',
    name: 'chat',
    component: QuickChatView,
    meta: { ...mobile('Chat', 'chats', { hideTopBar: true, hideTabs: true }) }
  },
  {
    path: '/chats/:chatId/:chatName/workspace/:workspaceId/:workspaceName',
    name: 'chat-workspace',
    component: QuickChatView,
    meta: { ...mobile('Chat', 'chats', { hideTopBar: true, hideTabs: true }) }
  },
  { path: '/meet', name: 'meet', component: MeetView },
  {
    path: '/meet/:meetId',
    name: 'meet-session',
    component: MeetView,
    meta: { ...mobile('Meeting', 'more', { hideTabs: true }) }
  },
  {
    path: '/meet/:meetId/summary',
    name: 'meet-summary',
    component: MeetSummaryView,
    meta: { ...mobile('Meeting summary', 'more', { hideTabs: true }) }
  },
  { path: '/workspaces', name: 'workspaces', component: WorkspaceView },
  {
    path: '/workspaces/:workspaceId/:workspaceName',
    name: 'workspace',
    component: WorkspaceView,
    meta: { ...mobile('Workspace', 'more', { hideTabs: true }) }
  },
  { path: '/browsers', name: 'browsers', component: BrowsersSessionManager },
  {
    path: '/browsers/:sessionId/:sessionName',
    name: 'browser-session',
    component: BrowsersSessionManager,
    meta: { ...mobile('Computer', 'desktop', { hideTabs: true }) }
  },
  { path: '/computers', name: 'computers', component: BrowsersSessionManager },
  {
    path: '/computers/:sessionId/:sessionName',
    name: 'computer-session',
    component: BrowsersSessionManager,
    meta: { ...mobile('Computer', 'desktop', { hideTabs: true }) }
  },
  {
    path: '/board-management',
    name: 'board-management',
    component: BoardManagementView,
    meta: { ...mobile('Board management', 'tasks') }
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: AppRouteView,
    meta: { app: 'task-list', ...mobile('Tasks', 'tasks', { hideTopBar: true }) }
  },
  {
    path: '/kanban/:board?/:boardName?',
    name: 'kanban',
    component: KanbanBoardView,
    meta: { ...mobile('Board', 'tasks', { hideTopBar: true }) }
  },
  {
    path: '/messenger',
    name: 'messenger',
    component: MessengerView,
    meta: { ...mobile('Messenger', 'chats', { hideTopBar: true, hideTabs: true }) }
  },
  {
    path: '/team/:teamId?',
    name: 'team',
    component: TeamView
  },
  {
    path: '/profiles/:name/edit',
    name: 'profile-edit',
    component: ProfileEditorView,
    meta: { ...mobile('Edit profile', 'more', { hideTabs: true }) }
  },

  // ── Generic app routes ────────────────────────────────────────────────
  { path: '/vibe-coding', name: 'vibe-coding', component: AppRouteView, meta: { app: 'vibe-coding' } },
  { path: '/wiki', name: 'wiki', component: AppRouteView, meta: { app: 'wiki' } },
  {
    path: '/channel',
    name: 'channel',
    component: ChannelView,
    meta: { ...mobile('Channel', 'more', { hideTopBar: true, hideTabs: true }) }
  },
  { path: '/docs', name: 'docs', component: AppRouteView, meta: { app: 'docs' } },
  { path: '/calendar', name: 'calendar', component: AppRouteView, meta: { app: 'calendar', ...mobile('Calendar', 'more') } },
  { path: '/harness', name: 'harness', component: AppRouteView, meta: { app: 'harness', ...mobile('Harness', 'more') } },
  { path: '/ciso', name: 'ciso', component: AppRouteView, meta: { app: 'ciso', ...mobile('CISO', 'more') } },
  { path: '/knowledge', name: 'knowledge', component: AppRouteView, meta: { app: 'knowledge' } },
  { path: '/knowledge-settings', name: 'knowledge-settings', component: AppRouteView, meta: { app: 'knowledge_settings' } },
  { path: '/projects', name: 'projects', component: AppRouteView, meta: { app: 'projects' } },
  { path: '/settings', name: 'settings', component: AppRouteView, meta: { app: 'settings' } },
  { path: '/global-settings', name: 'global-settings', component: AppRouteView, meta: { app: 'global-settings' } },
  { path: '/account', name: 'account', component: AppRouteView, meta: { app: 'account' } },
  { path: '/file-explorer', name: 'file-explorer', component: AppRouteView, meta: { app: 'file-explorer' } },
  { path: '/file-viewer/:filePath(.*)*', name: 'file-viewer', component: AppRouteView, meta: { app: 'file-viewer', ...mobile('File', 'more', { hideTabs: true }) } },
  { path: '/logs', name: 'log-viewer', component: AppRouteView, meta: { app: 'log-viewer' } },
  { path: '/chat-logs', name: 'chat-logs', component: AppRouteView, meta: { app: 'chat-logs' } },
  { path: '/decision-model-logs', name: 'decision-model-logs', component: AppRouteView, meta: { app: 'decision-model-logs' } },
  { path: '/metrics', name: 'metrics', component: AppRouteView, meta: { app: 'metrics' } },
  {
    path: '/management-dashboard',
    redirect: (to: any) => ({
      name: 'tasks',
      params: { companySlug: to.params.companySlug, projectName: to.params.projectName }
    })
  },
  { path: '/api-monitoring', name: 'api-monitoring', component: AppRouteView, meta: { app: 'api-monitoring' } },
  { path: '/infra', name: 'infra', component: AppRouteView, meta: { app: 'infra' } },
  { path: '/git-view', name: 'git-view', component: AppRouteView, meta: { app: 'git-view' } },
  { path: '/team-channel/:channelId', name: 'team-channel', component: AppRouteView, meta: { app: 'team-channel', ...mobile('Channel', 'chats', { hideTabs: true }) } },
  { path: '/team-dm/:dmId', name: 'team-dm', component: AppRouteView, meta: { app: 'team-dm', ...mobile('Direct message', 'chats', { hideTabs: true }) } },
  { path: '/team-media-library/:teamId', name: 'team-media-library', component: AppRouteView, meta: { app: 'team-media-library' } },
  {
    path: '/desktop/:pathMatch(.*)*',
    name: 'codx-junior-split',
    component: Desktop,
    meta: { ...mobile('Desktop', 'more', { hideTopBar: true, hideTabs: true }) }
  },
  { path: '/analytics', name: 'analytics', component: MetricsDashboard }
]

// The same route names, mounted under the company workspace prefix.
const ADVANCED_ROUTES = PROJECT_ROUTES.map(route => ({
  ...route,
  path: `/:companySlug/workspace/:projectName${route.path}`
}))

// Return the RAW active project/company segments. vue-router encodes params
// when it builds the URL, so pre-encoding would double-encode names.
function activeProjectSegment() {
  return $storex.projects.activeProject?.project_name || ''
}

function activeCompanySlug() {
  return $storex.company?.activeSlug || ''
}

// ─────────────────────────────────────────────────────────────────────────────
// LEGACY REDIRECTS
//
// A legacy project URL is /<projectName>/<tail>. The guard resolves the project
// to a company and redirects to /<companySlug>/workspace/<projectName>/<tail>.
// ─────────────────────────────────────────────────────────────────────────────
function defaultCompanyHome() {
  const company = $storex.company?.defaultCompany
  return company?.slug ? `/${company.slug}` : '/'
}

function withQuery(path: string, query: Record<string, any>) {
  const params = new URLSearchParams()
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    if (Array.isArray(value)) value.forEach(item => params.append(key, String(item)))
    else params.set(key, String(value))
  })
  const search = params.toString()
  return search ? `${path}?${search}` : path
}

async function ensureProjectsLoaded() {
  if (($storex.projects.allProjects || []).length) return
  try {
    await $storex.projects.loadAllProjects?.()
  } catch {
    // The screens report their own loading errors.
  }
}

async function resolveLegacy(to: any) {
  if (!$storex.api.user) return true
  await $storex.company.ensureLoaded().catch(() => {})
  await ensureProjectsLoaded()
  const segments = String(to.path || '')
    .split('/')
    .filter(Boolean)
    .map(segment => decodeURIComponent(segment))

  if (!segments.length) return defaultCompanyHome()

  // Old /company[/<id>] routes.
  if (segments[0] === 'company') {
    const byId = $storex.company.companies.find(item => item.id === segments[1])
    if (byId) return withQuery(`/${byId.slug}`, to.query)
    return withQuery(defaultCompanyHome(), to.query)
  }

  const first = segments[0]
  const company = $storex.company.companies.find(item => item.slug === first)
  if (company) {
    // A company slug with an unknown tail: go to its home rather than 404.
    return withQuery(`/${company.slug}`, to.query)
  }

  const project = ($storex.projects.allProjects || []).find(item =>
    item.project_name === first || item.project_id === first
  )
  if (!project) return withQuery(defaultCompanyHome(), to.query)

  let target = null
  try {
    const owners = await API.companies.lookup(project.project_id)
    target = (owners || []).find(item => item.is_default) || (owners || [])[0] || null
  } catch {
    target = null
  }
  target = target || $storex.company.defaultCompany
  if (!target?.slug) return withQuery(defaultCompanyHome(), to.query)

  try {
    if ($storex.projects.activeProject?.project_id !== project.project_id) {
      await $storex.projects.activeProjectChanged(project)
    }
  } catch {
    // The redirect is still useful; project loading reports its own error.
  }

  // /<project>/company is the old company entry point.
  if (segments[1] === 'company') {
    return withQuery(`/${target.slug}`, to.query)
  }

  let tail = segments.slice(1).join('/')
  if (tail.startsWith('management-dashboard')) tail = 'tasks'
  const path = `/${target.slug}/workspace/${encodeURIComponent(project.project_name)}${
    tail ? `/${tail}` : ''
  }`
  return withQuery(path, to.query)
}

// ─────────────────────────────────────────────────────────────────────────────
// ROUTES
// ─────────────────────────────────────────────────────────────────────────────
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      // Bootstrap home. The guard replaces it with /<default-company-slug> when
      // a company exists; when there is none yet the COMPANY SHELL renders here
      // so codx-junior is company-first from the very first visit: the shell
      // opens the onboarding wizard instead of the old project welcome screen.
      path: '/',
      name: 'root',
      component: CompanyView,
      meta: { ...mobile('Company', 'more', { hideTopBar: true }), company: true }
    },
    { path: '/company', redirect: () => withQuery(defaultCompanyHome(), {}) },
    { path: '/solopreneur/:pathMatch(.*)*', redirect: () => defaultCompanyHome() },
    {
      path: '/channel',
      redirect: () => {
        const companySlug = activeCompanySlug()
        const projectName = activeProjectSegment()
        return companySlug && projectName
          ? { name: 'channel', params: { companySlug, projectName } }
          : defaultCompanyHome()
      }
    },

    {
      // Company shell and sections: /<slug> and /<slug>/<section>.
      path: `/:companySlug/:section(${COMPANY_SECTION_RE})?`,
      name: 'company-home',
      component: CompanyView,
      meta: { ...mobile('Company', 'more', { hideTopBar: true }), company: true }
    },

    ...ADVANCED_ROUTES,

    {
      // Legacy / unknown URL: the guard redirects to the canonical path.
      path: '/:pathMatch(.*)*',
      name: 'legacy',
      component: LegacyRedirectView
    }
  ]
})

// ─────────────────────────────────────────────────────────────────────────────
// BASE-PATH ENFORCEMENT
// Any navigation by *name* to an advanced route gets the active company slug
// and project name injected. Company shell routes are not injected.
// ─────────────────────────────────────────────────────────────────────────────
let scopedRouteNames = new Set<string>()

function refreshScopedRouteNames() {
  scopedRouteNames = new Set(
    router.getRoutes()
      .filter(route => typeof route.name === 'string' && route.path.includes(':projectName'))
      .map(route => route.name as string)
  )
}

function withContext(location: any): any {
  if (!location || typeof location === 'string') return location
  const loc = { ...location }
  if (scopedRouteNames.has(loc.name)) {
    const params = { ...(loc.params || {}) }
    if (!params.companySlug) params.companySlug = activeCompanySlug()
    if (!params.projectName) params.projectName = activeProjectSegment()
    loc.params = params
  }
  return loc
}

refreshScopedRouteNames()

const _push = router.push.bind(router)
const _replace = router.replace.bind(router)
;(router as any).push = (to: any) => _push(withContext(to))
;(router as any).replace = (to: any) => _replace(withContext(to))

router.$navigation = Navigate({ $router: router, $storex })
router.$navigation.$storex = $storex
router.$navigation.init()

router.beforeEach(async (to, _from) => {
  if (to.path.startsWith('/auth')) return true
  if (to.path.startsWith('/solopreneur')) return defaultCompanyHome()
  if (to.name === 'legacy') return resolveLegacy(to)
  if (!$storex.api.user) return true

  if (to.name === 'root') {
    await $storex.company.ensureLoaded().catch(() => {})
    const home = defaultCompanyHome()
    // No company yet (fresh bootstrap / API unavailable): let the shell render
    // instead of redirecting to "/" in a loop.
    return home === '/' ? true : home
  }

  if (to.params.companySlug) {
    await $storex.company.ensureLoaded().catch(() => {})
    const slug = String(to.params.companySlug)
    const company = $storex.company.companies.find(item => item.slug === slug)
    if (!company) return resolveLegacy(to)

    if ($storex.company.activeId !== company.id) {
      await $storex.company.select(company.id, { skipList: true })
    }

    if (to.params.projectName) {
      await ensureProjectsLoaded()
      const projectName = decodeURIComponent(String(to.params.projectName))
      const project = ($storex.projects.allProjects || []).find(item =>
        item.project_name === projectName || item.project_id === projectName
      )
      const visible = new Set(
        ($storex.company.visibleProjectIds || []).map((id: any) => String(id))
      )
      if (!project || !visible.has(String(project.project_id))) {
        $storex.ui?.addNotification?.({
          text: `Project "${projectName}" is not available in this company`,
          type: 'error'
        })
        return `/${company.slug}`
      }
      if ($storex.projects.activeProject?.project_id !== project.project_id) {
        try {
          await $storex.projects.activeProjectChanged(project)
        } catch {
          // The screen reports its own loading error.
        }
      }
    }
  }

  return true
})

// The dockview desktop is not a mobile screen; keep the existing fallback.
router.beforeEach((to) => {
  if ($storex.ui.isMobile && to.name === 'codx-junior-split') {
    const companySlug = activeCompanySlug()
    const projectName = activeProjectSegment()
    return companySlug && projectName
      ? { name: 'home', params: { companySlug, projectName }, replace: true }
      : defaultCompanyHome()
  }
  return true
})

router.beforeEach((to, from) => {
  return router.$navigation.onRouteChanged({ from, to })
})

$storex.$router = router
$storex.$navigation = router.$navigation

export default router
