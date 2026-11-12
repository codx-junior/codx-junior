import { getterTree, mutationTree, actionTree } from 'typed-vuex'
import { $storex } from '.'
import {
  MS_DAY,
  addDays,
  addMonths,
  dateFromKey,
  dateKey,
  formatRangeLabel,
  parseDate,
  rangeFor,
  startOfDay
} from '@/utils/calendarDate'

/**
 * Agenda / Calendar store (design/06-calendar/01-analysis.md).
 *
 * Follows the project convention: a namespaced Vuex module built with
 * `typed-vuex` (`planning.js`, `orchestration.js`), not a Pinia store — the
 * module is registered in `store/index.js` like every other one.
 *
 * D1 — the store is GLOBAL: `state.calendars` / `state.occurrences` hold the
 * *whole* agenda the viewer may see. Projects never re-scope it; they are a
 * multi-select FILTER (`activeProjects`, OR semantics, empty = All).
 * D2 — calendars are grouped by SCOPE, not by project. The sidebar only ever
 * needs `calendarGroups`.
 *
 * The server expands recurrences and shifts timezones, so this module never
 * computes an occurrence: it asks for a range and keeps the answer.
 */

export const namespaced = true

export const VIEWS = ['month', 'week', 'day', 'agenda']
const DEFAULT_VIEW = 'month'

// How many days a view moves when navigating (‹ / ›). The agenda shows a
// 30-day window (§9.2), so navigating it moves a window, not a single day.
const SHIFT_DAYS = { week: 7, day: 1, agenda: 30 }

// `personal` is not a project: it is the token of the events whose
// `project_id` is null (a private calendar is not a project — §3.1).
export const PERSONAL_PROJECT = 'personal'

export const DEFAULT_PREFS = {
  hidden_calendars: [],
  order: [],
  week_start: 1,
  // Not part of the §5.1 sketch, additive: lets the project filter survive a
  // reload ("persist the choice via prefs").
  active_projects: []
}

/** The API client of the active project (global endpoints, project headers). */
function apiClient() {
  return $storex.projects.activeProject?.$api || $storex.api
}

function errorMessage(error) {
  return error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
}

export const state = () => ({
  // Server data
  calendars: [],
  occurrences: [],
  counts: {},
  warnings: [],
  projects: [],

  // Project filter: [] === All (OR of the selected ids otherwise)
  activeProjects: [],

  // Navigation
  view: DEFAULT_VIEW,
  cursor: dateKey(new Date()),
  range: null,

  // Per-user preferences (hidden calendars, order, week start, filter)
  prefs: { ...DEFAULT_PREFS },
  prefsLoaded: false,

  loading: false,
  error: null,
  loadToken: 0,
  loaded: false
})

// ─── Getters ──────────────────────────────────────────────────────────────────

export const getters = getterTree(state, {
  /** Every calendar the viewer may see, in display order. */
  calendarList: state => {
    const order = state.prefs.order || []
    const weight = cal => {
      const index = order.indexOf(cal.id)
      return index === -1 ? Number.MAX_SAFE_INTEGER : index
    }
    return [...(state.calendars || [])].sort((a, b) => {
      const wa = weight(a)
      const wb = weight(b)
      if (wa !== wb) return wa - wb
      return (a.order ?? 0) - (b.order ?? 0)
    })
  },

  calendarsById: (state, getters) => (id) =>
    (getters.calendarList || []).find(cal => cal.id === id) || null,

  /** Calendars the viewer has not hidden (prefs + the calendar's own flag). */
  visibleCalendarIds: (state, getters) => {
    const hidden = state.prefs.hidden_calendars || []
    return (getters.calendarList || [])
      .filter(cal => cal.visible !== false && !hidden.includes(cal.id))
      .map(cal => cal.id)
  },

  visibleCalendars: (state, getters) => {
    const ids = getters.visibleCalendarIds
    return (getters.calendarList || []).filter(cal => ids.includes(cal.id))
  },

  /** Sidebar sections, grouped by SCOPE (§9.1) — never by project. */
  calendarGroups: (state, getters) => {
    const list = getters.calendarList || []
    const groups = [
      { key: 'private', title: 'My calendars', items: list.filter(cal => cal.scope === 'private') },
      { key: 'shared', title: 'Shared with me', items: list.filter(cal => cal.scope === 'team' || cal.scope === 'workspace') },
      { key: 'system', title: 'codx-junior', items: list.filter(cal => cal.scope === 'system') },
      { key: 'other', title: 'Other', items: list.filter(cal => !['private', 'team', 'workspace', 'system'].includes(cal.scope)) }
    ]
    return groups.filter(group => group.items.length)
  },

  /** Project filter options (plus the `personal` pseudo-project). */
  projectOptions: (state) => {
    const options = (state.projects || [])
      .map(project => ({
        id: project.project_id || project.id || project.codx_path || '',
        name: project.name || project.project_name || project.project_id || '',
        role: project.role || ''
      }))
      .filter(option => option.id)
    if (!options.some(option => option.id === PERSONAL_PROJECT)) {
      options.push({ id: PERSONAL_PROJECT, name: 'Personal · no project', role: '' })
    }
    return options
  },

  isAllProjects: state => !(state.activeProjects || []).length,

  activeView: state => (VIEWS.includes(state.view) ? state.view : DEFAULT_VIEW),

  cursorKey: state => state.cursor,

  cursorDate: state => dateFromKey(state.cursor) || startOfDay(new Date()),

  rangeLabel: (state, getters) => formatRangeLabel(getters.activeView, getters.cursorDate),

  /** Query window of the current view, regenerated from view + cursor. */
  queryRange: (state, getters) => rangeFor(getters.activeView, getters.cursorDate),

  /** Occurrences to render: hidden calendars and the project filter applied. */
  filteredOccurrences: (state, getters) => {
    const visibleIds = getters.visibleCalendarIds || []
    const filtered = (state.occurrences || []).filter(occ => {
      const memberships = occ.calendar_ids || (occ.calendar_id ? [occ.calendar_id] : [])
      if (memberships.length && !memberships.some(id => visibleIds.includes(id))) return false
      if (getters.isAllProjects) return true
      return (state.activeProjects || []).includes(occ.project_id || PERSONAL_PROJECT)
    })
    return filtered.sort((a, b) => {
      if (a.all_day !== b.all_day) return a.all_day ? -1 : 1
      return (parseDate(a.start)?.getTime() || 0) - (parseDate(b.start)?.getTime() || 0)
    })
  },

  /** Occurrences bucketed by local day key — the month grid and agenda read this. */
  occurrencesByDay: (state, getters) => {
    const buckets = {}
    for (const occ of getters.filteredOccurrences) {
      const start = parseDate(occ.start)
      if (!start) continue
      const end = parseDate(occ.end) || start
      // Multi-day (all-day) items appear on every day they cover. An end at
      // exactly 00:00 is an exclusive bound, so it does not add a day.
      let days = Math.max(1, Math.round((startOfDay(end) - startOfDay(start)) / MS_DAY) + 1)
      if (days > 1 && end.getHours() === 0 && end.getMinutes() === 0) days -= 1
      for (let i = 0; i < days; i++) {
        const key = dateKey(addDays(start, i))
        if (!buckets[key]) buckets[key] = []
        buckets[key].push(occ)
      }
    }
    return buckets
  },

  occurrenceList: state => state.occurrences || [],

  countsByCalendar: state => state.counts?.by_calendar || {},

  filteredCount: (state, getters) => getters.filteredOccurrences.length,

  totalCount: state => Number(state.counts?.total ?? (state.occurrences || []).length),

  isLoading: state => state.loading,

  loadError: state => state.error,

  preferences: state => state.prefs
})

// ─── Mutations ────────────────────────────────────────────────────────────────

export const mutations = mutationTree(state, {
  setCalendars(state, calendars) {
    state.calendars = calendars || []
  },

  setOccurrences(state, occurrences) {
    state.occurrences = occurrences || []
  },

  setCounts(state, counts) {
    state.counts = counts || {}
  },

  setWarnings(state, warnings) {
    state.warnings = warnings || []
  },

  setProjects(state, projects) {
    state.projects = projects || []
  },

  setActiveProjects(state, projects) {
    state.activeProjects = projects || []
  },

  applyView(state, view) {
    state.view = VIEWS.includes(view) ? view : DEFAULT_VIEW
  },

  applyCursor(state, cursor) {
    state.cursor = cursor || dateKey(new Date())
  },

  setRange(state, range) {
    state.range = range || null
  },

  setPrefs(state, prefs) {
    state.prefs = { ...DEFAULT_PREFS, ...(prefs || {}) }
  },

  patchPrefs(state, patch) {
    state.prefs = { ...state.prefs, ...(patch || {}) }
  },

  setPrefsLoaded(state, loaded) {
    state.prefsLoaded = !!loaded
  },

  setLoading(state, loading) {
    state.loading = !!loading
  },

  setError(state, error) {
    state.error = error || null
  },

  setLoaded(state, loaded) {
    state.loaded = !!loaded
  },

  setLoadToken(state, token) {
    state.loadToken = Number(token) || 0
  }
})

// ─── Actions ──────────────────────────────────────────────────────────────────

export const actions = actionTree(
  { state, getters, mutations },
  {
    /**
     * Nothing is fetched at boot: the calendar is a global surface and the view
     * asks for its own range (`ensureLoaded`), so opening another app never
     * pays for a calendar query.
     */
    async init() {},

    /** Load calendars, projects and prefs — once per session. */
    async ensureLoaded({ state, dispatch }) {
      if (state.loaded || state.loading) return
      await Promise.all([
        dispatch('loadPrefs'),
        dispatch('loadCalendars'),
        dispatch('loadProjects')
      ])
      await dispatch('loadRange')
    },

    /** Reload everything the current screen shows (after a write, on refresh). */
    async reload({ dispatch }) {
      await Promise.all([dispatch('loadCalendars'), dispatch('loadRange')])
    },

    /** Calendars the viewer may see (§5.1). */
    async loadCalendars({ commit }) {
      const api = apiClient()
      if (!api?.calendar) return []
      try {
        const response = await api.calendar.calendars.list()
        const calendars = Array.isArray(response) ? response : (response?.calendars || [])
        commit('setCalendars', calendars)
        return calendars
      } catch (error) {
        commit('setError', errorMessage(error))
        return []
      }
    },

    /** Projects the viewer can put events in (feeds the filter + the editor). */
    async loadProjects({ commit }) {
      const api = apiClient()
      if (!api?.calendar) return []
      try {
        const response = await api.calendar.projects()
        const projects = Array.isArray(response) ? response : (response?.projects || [])
        commit('setProjects', projects)
        return projects
      } catch (error) {
        // The project filter is not critical: the agenda still renders "All".
        commit('setError', errorMessage(error))
        return []
      }
    },

    /**
     * Ask the server for the occurrences of the current view.
     *
     * The response is authoritative: projections (`codx-junior`), recurrence
     * expansion and the ACL are all resolved there. A stale response (the user
     * navigated while it was in flight) is discarded via the load token.
     */
    async loadRange({ state, getters, commit }, overrides = {}) {
      const api = apiClient()
      if (!api?.calendar) return null

      const token = (state.loadToken || 0) + 1
      commit('setLoadToken', token)
      commit('setLoading', true)
      commit('setError', null)

      const range = overrides.range || getters.queryRange
      const query = { from: range.from, to: range.to }
      const projects = overrides.projects ?? state.activeProjects
      if ((projects || []).length) query.projects = projects

      try {
        const response = await api.calendar.events.query(query)
        if (token !== state.loadToken) return null

        commit('setOccurrences', response?.occurrences || [])
        commit('setCounts', response?.counts || {})
        commit('setWarnings', response?.warnings || [])
        commit('setRange', response?.range || range)
        // The query response carries the calendars visible for this request:
        // use it until `GET /calendars` answers, so the sidebar is never blank.
        if (!(state.calendars || []).length && Array.isArray(response?.calendars)) {
          commit('setCalendars', response.calendars)
        }
        commit('setLoaded', true)
        return response
      } catch (error) {
        if (token !== state.loadToken) return null
        commit('setError', errorMessage(error))
        commit('setOccurrences', [])
        return null
      } finally {
        if (token === state.loadToken) commit('setLoading', false)
      }
    },

    /** Switch view (month / week / day / agenda) and load its window. */
    async setView({ commit, dispatch }, view) {
      commit('applyView', view)
      await dispatch('loadRange')
    },

    /**
     * Navigate the period: month → a month, week → 7 days, day → 1 day,
     * agenda → 30 days (its window).
     */
    async shift({ getters, commit, dispatch }, direction = 1) {
      const step = Number(direction) || 1
      const view = getters.activeView
      if (view === 'month') {
        commit('applyCursor', dateKey(addMonths(getters.cursorDate, step)))
      } else {
        commit('applyCursor', dateKey(addDays(getters.cursorDate, step * (SHIFT_DAYS[view] || 1))))
      }
      await dispatch('loadRange')
    },

    /** Jump to a day (`YYYY-MM-DD` or a Date). */
    async setCursor({ commit, dispatch }, value) {
      const date = value instanceof Date ? value : (dateFromKey(value) || parseDate(value))
      commit('applyCursor', dateKey(date || new Date()))
      await dispatch('loadRange')
    },

    async goToday({ commit, dispatch }) {
      commit('applyCursor', dateKey(new Date()))
      await dispatch('loadRange')
    },

    /**
     * Show/hide a calendar for this viewer (per-user preference, never a global
     * mutation of a shared calendar).
     */
    async toggleCalendar({ state, commit, dispatch }, calendarId) {
      if (!calendarId) return
      const hidden = state.prefs.hidden_calendars || []
      const next = hidden.includes(calendarId)
        ? hidden.filter(id => id !== calendarId)
        : [...hidden, calendarId]
      commit('patchPrefs', { hidden_calendars: next })
      await dispatch('savePrefs', { hidden_calendars: next })
    },

    /**
     * Multi-select project filter (OR semantics). An empty list means **All**.
     * The choice is persisted in the caller's prefs so it survives a reload.
     */
    async setProjectFilter({ commit, dispatch }, projects) {
      const next = [...new Set((projects || []).filter(Boolean))]
      commit('setActiveProjects', next)
      dispatch('savePrefs', { active_projects: next })
      await dispatch('loadRange', { projects: next })
    },

    // ─── Preferences ────────────────────────────────────────────────────────

    async loadPrefs({ state, commit }) {
      if (state.prefsLoaded) return state.prefs
      const api = apiClient()
      if (!api?.calendar) return state.prefs
      try {
        const response = await api.calendar.prefs.read()
        const prefs = { ...DEFAULT_PREFS, ...(response?.prefs || response || {}) }
        commit('setPrefs', prefs)
        commit('setActiveProjects', prefs.active_projects || [])
        commit('setPrefsLoaded', true)
        return prefs
      } catch (error) {
        // Preferences are a comfort, not a requirement: keep the defaults.
        commit('setPrefsLoaded', true)
        return state.prefs
      }
    },

    async savePrefs({ state, commit }, patch = {}) {
      const prefs = { ...state.prefs, ...(patch || {}) }
      commit('setPrefs', prefs)
      const api = apiClient()
      if (!api?.calendar) return prefs
      try {
        await api.calendar.prefs.save(prefs)
      } catch (error) {
        // A failed preference write must not break the calendar.
        console.warn('[calendar store] prefs not saved:', errorMessage(error))
      }
      return prefs
    },

    // ─── Events ─────────────────────────────────────────────────────────────

    /** Raw event (the editor needs the recurrence rule, not the instances). */
    async loadEvent({ commit }, eventId) {
      const api = apiClient()
      if (!api?.calendar || !eventId) return null
      try {
        return await api.calendar.events.get(eventId)
      } catch (error) {
        commit('setError', errorMessage(error))
        return null
      }
    },

    /**
     * Create an event and reload the visible range.
     *
     * @param {object} payload `{ calendar_ids, title, start, end, ... }`
     */
    async createEvent({ dispatch }, payload) {
      const api = apiClient()
      if (!api?.calendar) throw new Error('Calendar API is not available')
      const created = await api.calendar.events.create(payload)
      await dispatch('loadRange')
      return created
    },

    /**
     * Update an event (optimistic locking: the current `revision` is sent).
     * On `409 revision_mismatch` the range is reloaded so the UI snaps back to
     * the server state instead of keeping a stale edit.
     */
    async updateEvent({ dispatch }, { id, payload, revision } = {}) {
      const api = apiClient()
      if (!api?.calendar) throw new Error('Calendar API is not available')
      try {
        const updated = await api.calendar.events.update(id, payload, { revision })
        await dispatch('loadRange')
        return updated
      } catch (error) {
        if (error?.response?.status === 409) await dispatch('loadRange')
        throw error
      }
    },

    /** Delete an event (`scope` for a recurring series) and reload the range. */
    async deleteEvent({ dispatch }, { id, scope } = {}) {
      const api = apiClient()
      if (!api?.calendar) throw new Error('Calendar API is not available')
      await api.calendar.events.remove(id, { scope })
      await dispatch('loadRange')
    },

    // ─── Calendars ──────────────────────────────────────────────────────────

    /** Create a calendar (`scope: workspace` needs an admin on the server). */
    async createCalendar({ dispatch }, payload) {
      const api = apiClient()
      if (!api?.calendar) throw new Error('Calendar API is not available')
      const created = await api.calendar.calendars.create(payload)
      await dispatch('loadCalendars')
      return created
    }
  }
)
