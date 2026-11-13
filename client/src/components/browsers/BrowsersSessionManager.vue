<script setup>
import BrowserSessionList from './BrowserSessionList.vue'
import BrowserSessionPanel from './BrowserSessionPanel.vue'
import ComputerGridView from './ComputerGridView.vue'
import ComputerGridPicker from './ComputerGridPicker.vue'
</script>

<template>
  <div class="w-full h-full flex flex-col bg-base-100">
    <!-- Mobile: Toggle Header -->
    <div v-if="isMobile" class="flex items-center gap-2 p-3 border-b border-base-content/10 bg-base-200">
      <button
        v-if="canGoBack"
        @click="handleBack"
        class="btn btn-sm btn-ghost btn-circle"
        :title="isGridView ? 'Leave the grid' : 'Back to list'"
        aria-label="Back"
      >
        <i class="fa-solid fa-arrow-left"></i>
      </button>
      <h1 class="text-lg font-bold flex-1 truncate">Computer sessions</h1>
      <button
        v-if="isGridView"
        @click="showPicker = true"
        class="btn btn-sm btn-ghost btn-square"
        title="Select the machines on the wall"
        aria-label="Select the machines on the wall"
      >
        <i class="fa-solid fa-list-check"></i>
      </button>
      <button
        @click="toggleViewMode"
        class="btn btn-sm btn-ghost btn-square"
        :title="isGridView ? 'Single view' : 'Grid view'"
        :aria-label="isGridView ? 'Switch to single view' : 'Switch to grid view'"
      >
        <i :class="isGridView ? 'fa-solid fa-square' : 'fa-solid fa-table-cells-large'"></i>
      </button>
    </div>

    <!-- Desktop: fixed-width sidebar (collapsed rail or expanded list) + stage.
         The view controls live in the sidebar — also when it is collapsed — so
         nothing steals height from the machines. -->
    <div v-if="!isMobile" class="flex flex-1 min-h-0 gap-3 p-3 overflow-hidden">
      <aside
        class="flex-shrink-0 flex flex-col rounded-2xl border border-base-content/10 bg-base-100 shadow-sm overflow-hidden transition-[width] duration-200 ease-out"
        :class="sidebarCollapsed ? 'w-[68px]' : 'w-80'"
      >
        <BrowserSessionList
          :collapsed="sidebarCollapsed"
          @toggle-collapse="toggleSidebar"
          @manage="showPicker = true"
        />
      </aside>

      <section class="flex-1 min-w-0 min-h-0 rounded-2xl border border-base-content/10 bg-base-100 shadow-sm overflow-hidden flex flex-col">
        <ComputerGridView
          v-if="isGridView"
          class="p-2"
          :sessions="gridSessions"
          :layout="gridLayout"
          :active-id="gridActiveId"
          @activate="activateGridSession"
          @open="openGridSession"
          @remove="removeGridSession"
          @manage="showPicker = true"
        />
        <BrowserSessionPanel v-else />
      </section>
    </div>

    <!-- Mobile: Single column -->
    <div v-else class="flex-1 overflow-hidden flex flex-col">
      <Transition
        mode="out-in"
        enter-active-class="transition-all duration-300 ease-out"
        enter-from-class="opacity-0 translate-x-6"
        enter-to-class="opacity-100 translate-x-0"
        leave-active-class="transition-all duration-300 ease-out"
        leave-from-class="opacity-100 translate-x-0"
        leave-to-class="opacity-0 -translate-x-6"
      >
        <div v-if="showList" key="list" class="flex-1 overflow-hidden">
          <BrowserSessionList :collapsed="false" :view-controls="false" :collapsible="false" />
        </div>
        <div v-else key="panel" class="flex-1 overflow-hidden">
          <ComputerGridView
            v-if="isGridView"
            class="p-2"
            :sessions="gridSessions"
            :layout="gridLayout"
            :active-id="gridActiveId"
            @activate="activateGridSession"
            @open="openGridSession"
            @remove="removeGridSession"
            @manage="showPicker = true"
          />
          <BrowserSessionPanel v-else />
        </div>
      </Transition>
    </div>

    <ComputerGridPicker
      v-if="showPicker"
      :sessions="sessions"
      :selected-ids="gridSessionIds"
      @toggle="toggleGridSession"
      @select-all="selectAllGridSessions"
      @clear="clearGridSessions"
      @close="showPicker = false"
    />
  </div>
</template>
<script>
const SIDEBAR_STATE_KEY = 'codx.browsers.sidebarCollapsed'
const VIEW_MODE_KEY = 'codx.browsers.viewMode'
const GRID_LAYOUT_KEY = 'codx.browsers.gridLayout'
const GRID_SESSIONS_KEY = 'codx.browsers.gridSessionIds'

export default {
  name: 'BrowsersSessionManager',
  components: {
    BrowserSessionList,
    BrowserSessionPanel,
    ComputerGridView,
    ComputerGridPicker
  },
  data() {
    return {
      sidebarCollapsed: false,
      showPicker: false
    }
  },
  computed: {
    isMobile() {
      return this.$storex.ui.isMobile
    },
    sessions() {
      return this.$storex.browsers.allSessions
    },
    selectedSessionId() {
      return this.$storex.browsers.selectedSessionId
    },
    // ── Grid view (state lives in the store so it survives view changes) ────
    isGridView() {
      return this.$storex.browsers.isGridView
    },
    gridLayout() {
      return this.$storex.browsers.gridLayout
    },
    gridSessionIds() {
      return this.$storex.browsers.gridSessionIds
    },
    gridSessions() {
      return this.$storex.browsers.gridSessions
    },
    gridActiveId() {
      return this.$storex.browsers.gridActiveId
    },
    isMobileGrid() {
      return this.isMobile && this.isGridView
    },
    canGoBack() {
      return this.isMobileGrid || !!this.selectedSessionId
    },
    showList() {
      // The wall replaces the list on a phone: it is the screen, not a pane.
      if (this.isMobileGrid) return false
      if (this.isMobile) {
        return !this.selectedSessionId
      }
      return true
    },
    showPanel() {
      if (this.isMobile) {
        return !!this.selectedSessionId
      }
      return true
    }
  },
  watch: {
    sidebarCollapsed(value) {
      this.saveSidebarState(value)
    },
    isGridView(value) {
      this.saveValue(VIEW_MODE_KEY, value ? 'grid' : 'single')
    },
    gridLayout(value) {
      this.saveValue(GRID_LAYOUT_KEY, value)
    },
    gridSessionIds(value) {
      this.saveValue(GRID_SESSIONS_KEY, JSON.stringify(value || []))
    },
    selectedSessionId(value) {
      // In grid view the sidebar stays the source of sessions: picking one
      // puts it on the wall and gives it the live tile, so the list and the
      // wall never disagree about what is being watched.
      if (!this.isGridView || !value) return
      if (!this.gridSessionIds.includes(value)) {
        this.$storex.browsers.addGridSession(value)
      }
      this.$storex.browsers.setGridActive(value)
    }
  },
  methods: {
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
    },
    clearSelection() {
      this.$storex.browsers.clearSelection()
    },
    toggleViewMode() {
      this.setViewMode(this.isGridView ? 'single' : 'grid')
    },
    setViewMode(mode) {
      // The store seeds the wall on the first switch (watched machine + the
      // running ones), so every entry point behaves the same.
      this.$storex.browsers.setViewMode(mode)
    },
    toggleGridSession(sessionId) {
      this.$storex.browsers.toggleGridSession(sessionId)
    },
    selectAllGridSessions() {
      this.$storex.browsers.setGridSessions(this.sessions.map(session => session.id))
    },
    clearGridSessions() {
      this.$storex.browsers.setGridSessions([])
    },
    activateGridSession(sessionId) {
      if (!sessionId) return
      this.$storex.browsers.selectSession(sessionId)
      this.$storex.browsers.setGridActive(sessionId)
    },
    removeGridSession(sessionId) {
      this.$storex.browsers.removeGridSession(sessionId)
    },
    openGridSession(sessionId) {
      this.$storex.browsers.openSingleSession(sessionId)
    },
    handleBack() {
      if (this.isMobileGrid) {
        this.$storex.browsers.setViewMode('single')
        return
      }
      this.clearSelection()
    },
    loadSidebarState() {
      try {
        return window.localStorage.getItem(SIDEBAR_STATE_KEY) === 'true'
      } catch (error) {
        return false
      }
    },
    saveSidebarState(value) {
      this.saveValue(SIDEBAR_STATE_KEY, value ? 'true' : 'false')
    },
    saveValue(key, value) {
      try {
        window.localStorage.setItem(key, value)
      } catch (error) {
        // localStorage not available
      }
    },
    /** Restore the wall the user left behind (view, arrangement, selection). */
    restoreGridState() {
      try {
        const mode = window.localStorage.getItem(VIEW_MODE_KEY)
        if (mode === 'grid' || mode === 'single') {
          // Restore, do not seed: an empty wall was the user's own choice.
          this.$storex.browsers.setViewMode({ mode, seed: false })
        }
        const layout = window.localStorage.getItem(GRID_LAYOUT_KEY)
        if (layout) this.$storex.browsers.setGridLayout(layout)
        const raw = window.localStorage.getItem(GRID_SESSIONS_KEY)
        const ids = raw ? JSON.parse(raw) : []
        if (Array.isArray(ids) && ids.length) {
          this.$storex.browsers.setGridSessions(ids)
        }
      } catch (error) {
        // Corrupted payload or no localStorage: keep the defaults.
      }
    }
  },
  mounted() {
    this.sidebarCollapsed = this.loadSidebarState()
    this.restoreGridState()
  }
}
</script>
