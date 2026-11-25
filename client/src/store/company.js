import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'
import { API } from '../api/api'

export const namespaced = true

// Shared in-flight load so the router guard and the shell do not race.
let loadPromise = null

const STORAGE_KEY = 'codx.company.slug'

/**
 * Company-first state.
 *
 * The company is the root of the product: the URL carries its slug, and every
 * company-scoped surface (home, settings, Follow-up, advanced project lists)
 * reads from here. The store deliberately keeps only the active snapshot; the
 * list is the lightweight summary returned by `/api/companies`.
 */
export const state = () => ({
  companies: [],
  activeId: '',
  snapshot: null,
  loaded: false,
  loading: false,
  error: null
})

export const getters = getterTree(state, {
  activeCompany: (state) =>
    state.companies.find(company => company.id === state.activeId) ||
    state.companies.find(company => company.is_default) ||
    state.companies[0] ||
    null,

  defaultCompany: (state) =>
    state.companies.find(company => company.is_default) || state.companies[0] || null,

  activeSlug: (state) => {
    const company = state.companies.find(item => item.id === state.activeId) ||
      state.companies.find(item => item.is_default) ||
      state.companies[0]
    return company?.slug || ''
  },

  activeRole: (state) => {
    if ($storex.users?.isAdmin) return 'admin'
    const company = state.companies.find(item => item.id === state.activeId)
    return (API.user?.companies || [])
      .find(item => item.company_id === (company?.id || state.activeId))?.role || ''
  },

  canManage: (state, getters) =>
    ['admin', 'manager'].includes(getters.activeRole),

  canAdmin: (state, getters) => getters.activeRole === 'admin',

  /**
   * Effective project visibility. Prefers the server's computed set; falls
   * back to direct links plus the `all_projects` wildcard while the snapshot
   * is loading.
   */
  visibleProjectIds: (state) => {
    if (Array.isArray(state.snapshot?.visible_project_ids)) {
      return state.snapshot.visible_project_ids.map(String)
    }
    const allProjects = $storex.projects?.allProjects || []
    const company = state.companies.find(item => item.id === state.activeId) ||
      state.companies.find(item => item.is_default) ||
      state.companies[0]
    if (company?.all_projects) return allProjects.map(project => String(project.project_id))
    const linked = (company?.project_ids || []).map(String)
    const children = (state.snapshot?.child_projects || []).map(item => String(item.project_id))
    return [...new Set([...linked, ...children])]
  },

  rootProjectIds: (state) => {
    if (Array.isArray(state.snapshot?.root_project_ids)) {
      return state.snapshot.root_project_ids.map(String)
    }
    const company = state.companies.find(item => item.id === state.activeId) ||
      state.companies.find(item => item.is_default) ||
      state.companies[0]
    if (company?.all_projects) {
      return ($storex.projects?.allProjects || [])
        .filter(project => !project.parentProject)
        .map(project => String(project.project_id))
    }
    return (company?.project_ids || []).map(String)
  },

  /**
   * Projects the advanced workspace may list. Falls back to every discovered
   * project only while no company is loaded (login/bootstrap).
   */
  companyProjects: (state, getters) => {
    const allProjects = $storex.projects?.allProjects || []
    if (!state.loaded && !state.companies.length) return allProjects
    const visible = new Set(getters.visibleProjectIds)
    return allProjects.filter(project => visible.has(String(project.project_id)))
  },

  /**
   * Projects *defined* by the company: the direct links shown in the Projects
   * section (or the whole catalogue in `all_projects` mode). Child projects
   * inherited from a linked root are accessible through the advanced workspace
   * but are not separate company projects, so company-wide pickers never list
   * them as if the owner had linked them.
   *
   * Falls back to the company summary while the snapshot is loading.
   */
  linkedProjects: (state, getters) => {
    const snapshotProjects = state.snapshot?.projects
    if (Array.isArray(snapshotProjects)) {
      const seen = new Set()
      return snapshotProjects
        .filter(project => {
          const id = String(project?.project_id || '')
          if (!id || seen.has(id)) return false
          seen.add(id)
          return true
        })
        .map(project => ({
          ...project,
          project_id: String(project.project_id),
          project_name: project.name || project.discovered_name || String(project.project_id)
        }))
    }
    const allProjects = $storex.projects?.allProjects || []
    const company = state.companies.find(item => item.id === state.activeId) ||
      state.companies.find(item => item.is_default) ||
      state.companies[0]
    if (company?.all_projects) return allProjects
    const linked = new Set((company?.project_ids || []).map(String))
    return allProjects.filter(project => linked.has(String(project.project_id)))
  },

  linkedProjectIds: (state, getters) =>
    (getters.linkedProjects || []).map(project => String(project.project_id)),

  hasProject: (state, getters) => (projectId) =>
    getters.visibleProjectIds.includes(String(projectId || ''))
})

export const mutations = mutationTree(state, {
  setCompanies(state, companies) {
    state.companies = Array.isArray(companies) ? companies : []
  },
  setActiveId(state, companyId) {
    state.activeId = companyId || ''
  },
  setSnapshot(state, snapshot) {
    state.snapshot = snapshot || null
  },
  setLoaded(state, value) {
    state.loaded = !!value
  },
  setLoading(state, value) {
    state.loading = !!value
  },
  setError(state, error) {
    state.error = error || null
  }
})

export const actions = actionTree(
  { state, getters, mutations },
  {
    async init() {
      // Companies are loaded after the user is known (see ensureLoaded).
    },

    async ensureLoaded({ state }) {
      if (state.loaded) return state.companies
      if (!loadPromise) {
        loadPromise = $storex.company.loadCompanies().finally(() => {
          loadPromise = null
        })
      }
      return loadPromise
    },

    async loadCompanies({ state }) {
      if (!$storex.api.user) return []
      mutations.setLoading(state, true)
      mutations.setError(state, null)
      try {
        const companies = await API.companies.list()
        mutations.setCompanies(state, companies)
        const remembered = (() => {
          try { return localStorage.getItem(STORAGE_KEY) || '' } catch { return '' }
        })()
        const candidate =
          companies.find(company => company.id === state.activeId) ||
          companies.find(company => company.slug === remembered) ||
          companies.find(company => company.is_default) ||
          companies[0]
        if (candidate && candidate.id !== state.activeId) {
          await $storex.company.select({ companyId: candidate.id, skipList: true })
        }
        mutations.setLoaded(state, true)
        return companies
      } catch (error) {
        mutations.setError(state, error?.message || String(error))
        return state.companies
      } finally {
        mutations.setLoading(state, false)
      }
    },

    async select({ state }, payload) {
      const companyId = typeof payload === 'string' ? payload : payload?.companyId
      const skipList = typeof payload === 'object' && payload !== null
        ? Boolean(payload.skipList)
        : false
      if (!companyId) return null
      if (!skipList) {
        const companies = await API.companies.list()
        mutations.setCompanies(state, companies)
      }
      mutations.setActiveId(state, companyId)
      const company = state.companies.find(item => item.id === companyId)
      if (company?.slug) {
        try { localStorage.setItem(STORAGE_KEY, company.slug) } catch { /* noop */ }
      }
      try {
        const snapshot = await API.companies.snapshot(companyId)
        mutations.setSnapshot(state, snapshot)
        // A company switch must not leak the previous company's Follow-up data.
        $storex.planning?.reset?.()
        return snapshot
      } catch (error) {
        mutations.setError(state, error?.message || String(error))
        return null
      }
    },

    async selectBySlug({ state }, payload) {
      const slug = typeof payload === 'string' ? payload : payload?.slug
      const skipList = typeof payload === 'object' && payload !== null
        ? Boolean(payload.skipList)
        : false
      if (!slug) return null
      if (!state.loaded || skipList) {
        const companies = await API.companies.list()
        mutations.setCompanies(state, companies)
      }
      const company = state.companies.find(item => item.slug === slug)
      if (!company) return null
      return $storex.company.select({ companyId: company.id, skipList: true })
    },

    async refresh({ state }) {
      if (!state.activeId) return
      // A company-level edit (name, slug, logo…) changes the summary list, not
      // only the snapshot: reload it too or the switcher, the sidebar header and
      // the remembered slug keep showing the previous company name until a full
      // page reload.
      const companies = await API.companies.list().catch(() => state.companies)
      if (Array.isArray(companies)) mutations.setCompanies(state, companies)
      await $storex.company.select({ companyId: state.activeId, skipList: true })
    },

    async setAllProjects({ state }, value) {
      if (!state.activeId) return null
      await API.companies.update(state.activeId, { all_projects: !!value })
      await $storex.company.refresh()
      return state.snapshot
    },

    reset({ state }) {
      mutations.setActiveId(state, '')
      mutations.setSnapshot(state, null)
      mutations.setLoaded(state, false)
    }
  }
)
