<template>
  <div class="log-explorer flex h-full min-h-0 flex-col gap-2 overflow-hidden bg-base-200 p-2">

    <!-- Sources / time range / order / live tail / query cost -->
    <LogExplorerToolbar
      :sources="sources"
      :selected-sources="selectedSources"
      :time-range="timeRange"
      :order="order"
      :loading="loading"
      :live-tail="liveTail"
      :stats="stats"
      :quick-filters="quickFilters"
      :active-quick-filter="activeQuickFilter"
      @select-sources="onSelectSources"
      @select-quick-filter="onSelectQuickFilter"
      @select-time-range="onSelectTimeRange"
      @select-order="onSelectOrder"
      @refresh="runSearch"
      @toggle-live="onToggleLive"
    />

    <!-- Query language (`text`, `"phrase"`, `level=error,warn`, `logger~"re"`, `-negation`) -->
    <LogQueryBar
      :model-value="query"
      :loading="loading"
      @update:model-value="onQueryInput"
      @run="runSearch"
      @clear="onClearQuery"
    />

    <div class="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row">

      <!-- Facets rail -->
      <aside class="flex w-full shrink-0 flex-col gap-2 overflow-auto lg:w-60">
        <LogLevelFacets
          :facets="facets"
          :levels="levels"
          :loading="loading"
          @toggle-level="onToggleLevel"
          @clear-levels="onClearLevels"
          @filter="onAddFilterTerm"
        />
      </aside>

      <!-- Results -->
      <section class="flex min-h-0 min-w-0 flex-1 flex-col gap-2">

        <!-- API analysis (default view for the API presets) -->
        <div v-if="showInsights" class="shrink-0 max-h-[45vh] overflow-auto">
          <LogApiInsights
            v-show="insightsPanelOpen"
            :insights="insights"
            :loading="insightsLoading"
            :error="insightsError || ''"
            @open-entry="onOpenInsightEntry"
            @filter-endpoint="onAddFilterTerm"
            @zoom="onZoom"
          />
        </div>

        <!-- Volume histogram (click a bar to zoom the time range) -->
        <LogHistogram :histogram="histogram" :loading="loading" @zoom="onZoom" />

        <div class="relative flex min-h-0 flex-1 overflow-hidden rounded-box border border-base-300 bg-base-100">

          <!-- Error state -->
          <div v-if="error" class="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
            <i class="fa-solid fa-triangle-exclamation text-2xl text-error"></i>
            <p class="text-sm font-semibold">The query failed</p>
            <p class="max-w-md text-xs opacity-60">{{ error }}</p>
            <button class="btn btn-sm btn-primary mt-1 gap-1" @click="runSearch">
              <i class="fa-solid fa-rotate-right text-xs"></i>
              Retry
            </button>
          </div>

          <!-- Loading / empty state -->
          <div v-else-if="!listEntries.length" class="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
            <span v-if="loading" class="text-xs opacity-60">
              <i class="fa-solid fa-circle-notch mr-2 animate-spin"></i>
              Querying the log files…
            </span>
            <template v-else>
              <i class="fa-solid fa-file-lines text-2xl opacity-30"></i>
              <p class="text-sm font-semibold">No entries</p>
              <p class="max-w-md text-xs opacity-60">{{ emptyHint }}</p>
            </template>
          </div>

          <!-- Virtualized result list -->
          <div v-else class="flex min-h-0 flex-1 flex-col">
            <div class="flex shrink-0 items-center gap-2 border-b border-base-300 bg-base-200/60 px-2 py-1 font-mono text-[10px] uppercase tracking-wide opacity-50">
              <span class="w-14">level</span>
              <span class="w-44">timestamp</span>
              <span class="w-48">logger</span>
              <span class="min-w-0 flex-1">message</span>
              <span class="w-16"></span>
            </div>

            <div class="relative min-h-0 flex-1">
              <LogVirtualList
                ref="list"
                :items="listEntries"
                :item-height="estimatedRowHeight"
                :overscan="12"
                @scroll-end="onScrollEnd"
              >
                <template #default="{ item, index }">
                  <LogEntryRow
                    :entry="item"
                    :highlights="highlights"
                    :selected="index === selectedIndex"
                    @select="onSelectEntry(index)"
                    @open="onOpenEntry(index)"
                    @filter="onAddFilterTerm"
                  />
                </template>
              </LogVirtualList>

              <!-- Live tail must never move the scroll position: pin a pill instead -->
              <button
                v-if="liveTail && pendingNew > 0"
                class="btn btn-xs btn-primary absolute left-1/2 top-2 z-20 -translate-x-1/2 gap-1 shadow-lg"
                @click="jumpToNewest"
              >
                <i class="fa-solid fa-arrow-down text-[10px]"></i>
                {{ pendingNew }} new {{ pendingNew === 1 ? 'entry' : 'entries' }}
              </button>

              <!-- Detail drawer (selected entry) -->
              <div v-if="detailOpen" class="absolute inset-y-0 right-0 z-30 w-full max-w-[34rem]">
                <LogDetailPanel
                  :entry="detailEntry"
                  :raw="detailRaw"
                  :loading="detailLoading"
                  :context-entries="contextEntries"
                  :context-loading="contextLoading"
                  @close="closeDetail"
                  @filter="onAddFilterTerm"
                  @context="loadContext"
                  @select-entry="onSelectContextEntry"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- Status bar -->
        <footer class="flex flex-wrap items-center gap-2 text-[11px] opacity-70">
          <span class="font-mono">{{ listEntries.length }} / {{ total }} entries</span>
          <span class="font-mono opacity-60">page {{ page }}</span>
          <button v-if="hasMore" class="btn btn-xs btn-ghost gap-1" :disabled="loading" @click="loadMore">
            <i class="fa-solid text-[10px]" :class="loading ? 'fa-circle-notch animate-spin' : 'fa-angles-down'"></i>
            Load more
          </button>
          <label class="flex items-center gap-1">
            <span class="opacity-60">size</span>
            <select class="select select-xs" :value="pageSize" @change="onPageSizeChange">
              <option v-for="size in pageSizes" :key="size" :value="size">{{ size }}</option>
            </select>
          </label>
          <span class="ml-auto hidden font-mono opacity-60 sm:inline">
            <kbd class="kbd kbd-xs">/</kbd> search ·
            <kbd class="kbd kbd-xs">j</kbd>/<kbd class="kbd kbd-xs">k</kbd> move ·
            <kbd class="kbd kbd-xs">Enter</kbd> details ·
            <kbd class="kbd kbd-xs">Esc</kbd> close
          </span>
        </footer>
      </section>
    </div>
  </div>
</template>

<script>
import LogExplorerToolbar from './LogExplorerToolbar.vue'
import LogQueryBar from './LogQueryBar.vue'
import LogLevelFacets from './LogLevelFacets.vue'
import LogHistogram from './LogHistogram.vue'
import LogVirtualList from './LogVirtualList.vue'
import LogEntryRow from './LogEntryRow.vue'
import LogDetailPanel from './LogDetailPanel.vue'
import LogApiInsights from './LogApiInsights.vue'

/**
 * Log Explorer — container for the Loki/Graylog-style application log view.
 *
 * Everything (filtering, paging, histogram and facets) is server side: the
 * component only ever holds the pages the user has asked for. `useLogExplorer`
 * owns that state; this component wires it to the presentational pieces and
 * keeps the list itself virtualized, so a source with 10k+ lines stays smooth.
 *
 * Behaviour worth knowing:
 *  - `scroll-end` on the virtual list loads the next page and APPENDS it, so
 *    scrolling feels continuous while `total`/`has_more` stay authoritative.
 *  - With the live tail on, new entries are merged at the top (or bottom for
 *    ascending order) but the scroll position is never moved: a pinned
 *    "N new entries" pill lets the user jump when they want to.
 *  - Keyboard: `/` focuses the query box (handled by LogQueryBar), `j`/`k`
 *    move the selection, `Enter` opens the detail drawer, `Esc` closes it.
 */
import { QUICK_FILTERS, tokenizeQuery, useLogExplorer } from '@/composables/useLogExplorer'

/**
 * Page sizes offered in the status bar.
 * 1000 is the backend cap (`MAX_PAGE_SIZE`); larger windows are reached with
 * the "Load more" button, which appends the next page instead of re-fetching.
 */
const PAGE_SIZES = [50, 100, 250, 500, 1000]

/** Approximate row height (px) used before a row has been measured. */
const ESTIMATED_ROW_HEIGHT = 24

/** Terms that the backend resolves (level=…, logger~"…"): not highlightable. */
const OPERATOR_TERM = /^[A-Za-z_][\w.]*(~|=)/

export default {
  name: 'LogExplorer',
  components: {
    LogExplorerToolbar,
    LogQueryBar,
    LogLevelFacets,
    LogHistogram,
    LogVirtualList,
    LogEntryRow,
    LogDetailPanel,
    LogApiInsights,
  },
  setup() {
    // The composable owns every server-driven ref and the API actions.
    return { ...useLogExplorer() }
  },
  data() {
    return {
      /** Loaded pages of entries (the virtual list renders this array). */
      listEntries: [],
      selectedIndex: -1,
      detailOpen: false,
      detailEntry: null,
      detailRaw: '',
      detailLoading: false,
      contextEntries: [],
      contextLoading: false,
      /** Entries merged by the live tail and not yet acknowledged by the user. */
      pendingNew: 0,
      pageSizes: PAGE_SIZES,
      estimatedRowHeight: ESTIMATED_ROW_HEIGHT,
      detailToken: 0,
      /** Default API-analysis views offered by the toolbar. */
      quickFilters: QUICK_FILTERS,
      /** The API analysis panel starts open: it is the default view. */
      insightsPanelOpen: true,
    }
  },
  computed: {
    /**
     * Free-text needles to highlight client side. Operator terms are skipped:
     * they are resolved by the server and never appear verbatim in a message.
     */
    highlights() {
      return tokenizeQuery(this.query)
        .filter((term) => !term.startsWith('-') && !OPERATOR_TERM.test(term))
        .map((term) => term.replace(/^"|"$/g, '').trim())
        .filter(Boolean)
    },
    selectedEntry() {
      return this.listEntries[this.selectedIndex] || null
    },
    emptyHint() {
      if (!this.sources.length) {
        return 'No log files in LOGS_FOLDER and no docker containers found. Check CODX_JUNIOR_API_LOGS and that the docker socket is reachable from the API container.'
      }
      if (this.activeFilterCount) {
        return 'No entry matches the current query and filters. Try widening the time range or removing a term.'
      }
      return 'The selected source has no entries in the current time window.'
    },
  },
  watch: {
    /** Live tail: merge the new entries without moving the scroll position. */
    entries(next) {
      if (!this.liveTail) return
      const known = new Set(this.listEntries.map((entry) => entry.id))
      const fresh = (next || []).filter((entry) => entry.id && !known.has(entry.id))
      if (!fresh.length) return
      if (this.order === 'asc') {
        this.listEntries = [...this.listEntries, ...fresh]
      } else {
        this.listEntries = [...fresh, ...this.listEntries]
        if (this.selectedIndex >= 0) this.selectedIndex += fresh.length
      }
      this.pendingNew += fresh.length
    },
    liveTail(value) {
      if (!value) this.pendingNew = 0
    },
  },
  mounted() {
    window.addEventListener('keydown', this.onKeydown)
    this.bootstrap()
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onKeydown)
    this.stopLiveTail()
  },
  methods: {
    // ── Query lifecycle ────────────────────────────────────────────────────
    async bootstrap() {
      await this.loadSources()
      await this.runSearch()
    },
    /** Run a fresh query from page 1 and replace the loaded entries. */
    async runSearch() {
      this.detailOpen = false
      await this.runAction(() => this.runQuery({ reset: true }))
    },
    /**
     * Run one composable action and, when it produced a result, adopt that
     * result as the loaded list (page 1 of a new query).
     */
    async runAction(action) {
      const result = await action()
      if (!result) return null
      this.applyResult(result)
      return result
    },
    applyResult(result) {
      this.listEntries = Array.isArray(result.entries) ? [...result.entries] : []
      this.pendingNew = 0
      this.selectedIndex = -1
      this.detailEntry = null
      this.detailRaw = ''
      this.contextEntries = []
      this.$nextTick(() => this.$refs.list?.scrollToIndex(0))
    },
    /** `scroll-end` from the virtual list: page in the next chunk. */
    async onScrollEnd() {
      await this.loadMore()
    },
    async loadMore() {
      if (this.loading || !this.hasMore) return
      const result = await this.nextPage()
      if (!result || !Array.isArray(result.entries) || !result.entries.length) return
      const known = new Set(this.listEntries.map((entry) => entry.id))
      const fresh = result.entries.filter((entry) => entry.id && !known.has(entry.id))
      if (fresh.length) this.listEntries = [...this.listEntries, ...fresh]
    },

    // ── Filter / toolbar wiring ────────────────────────────────────────────
    onQueryInput(value) {
      this.query = value
    },
    async onClearQuery() {
      this.query = ''
      await this.runSearch()
    },
    async onSelectSources(sourceIds) {
      await this.runAction(() => this.setSources(sourceIds))
    },
    /** Quick-filter chip: applies query + levels + minimum duration in one go. */
    async onSelectQuickFilter(id) {
      this.insightsPanelOpen = true
      await this.runAction(() => this.applyQuickFilter(id))
    },
    /** A slow request in the API panel: open its entry in the detail drawer. */
    async onOpenInsightEntry(id) {
      if (!id) return
      const index = this.listEntries.findIndex((item) => item.id === id)
      if (index >= 0) {
        this.onOpenEntry(index)
        return
      }
      this.selectedIndex = -1
      this.detailOpen = true
      this.detailLoading = true
      this.detailRaw = ''
      this.contextEntries = []
      const token = this.detailToken + 1
      this.detailToken = token
      const detail = await this.fetchEntry(id)
      if (token !== this.detailToken) return
      this.detailEntry = detail
      this.detailRaw = (detail && (detail.raw || detail.raw_preview)) || ''
      this.detailLoading = false
    },
    async onSelectTimeRange(range) {
      await this.runAction(() => this.setTimeRange(range))
    },
    async onSelectOrder(order) {
      await this.runAction(() => this.setOrder(order))
    },
    async onToggleLevel(level) {
      await this.runAction(() => this.toggleLevel(level))
    },
    async onClearLevels() {
      await this.runAction(() => this.clearLevels())
    },
    async onAddFilterTerm(term) {
      await this.runAction(() => this.addFilterTerm(term))
    },
    async onZoom(ts, bucketMs) {
      await this.runAction(() => this.zoomToBucket(ts, bucketMs))
    },
    async onPageSizeChange(event) {
      await this.runAction(() => this.setPageSize(Number(event.target.value)))
    },
    onToggleLive() {
      this.toggleLiveTail()
      if (!this.liveTail) this.pendingNew = 0
    },
    jumpToNewest() {
      const target = this.order === 'asc' ? this.listEntries.length - 1 : 0
      this.$refs.list?.scrollToIndex(Math.max(0, target))
      this.pendingNew = 0
    },

    // ── Selection & detail drawer ──────────────────────────────────────────
    onSelectEntry(index) {
      this.selectedIndex = index
      if (this.detailOpen) this.loadDetail(this.listEntries[index])
    },
    onOpenEntry(index) {
      this.selectedIndex = index
      this.openDetail()
    },
    moveSelection(delta) {
      if (!this.listEntries.length) return
      const next = this.selectedIndex < 0
        ? (delta > 0 ? 0 : this.listEntries.length - 1)
        : Math.min(this.listEntries.length - 1, Math.max(0, this.selectedIndex + delta))
      this.selectedIndex = next
      this.$refs.list?.scrollToIndex(next, { align: 'auto' })
      if (this.detailOpen) this.loadDetail(this.listEntries[next])
    },
    openDetail() {
      const entry = this.selectedEntry
      if (!entry) return
      this.detailOpen = true
      this.loadDetail(entry)
    },
    closeDetail() {
      this.detailOpen = false
      this.detailEntry = null
      this.detailRaw = ''
      this.contextEntries = []
    },
    async loadDetail(entry) {
      if (!entry) return
      const token = this.detailToken + 1
      this.detailToken = token
      this.detailEntry = entry
      this.detailRaw = entry.raw_preview || ''
      this.contextEntries = []
      this.detailLoading = true
      const detail = await this.fetchEntry(entry.id, entry.source)
      if (token !== this.detailToken) return
      this.detailEntry = detail || entry
      this.detailRaw = detail?.raw || entry.raw_preview || ''
      this.detailLoading = false
    },
    async loadContext() {
      const entry = this.detailEntry || this.selectedEntry
      if (!entry) return
      this.contextLoading = true
      const entries = await this.fetchContext(entry.id, { before: 10, after: 10 })
      this.contextEntries = (entries || []).filter((item) => item.id !== entry.id)
      this.contextLoading = false
    },
    onSelectContextEntry(entry) {
      if (!entry) return
      const index = this.listEntries.findIndex((item) => item.id === entry.id)
      if (index >= 0) {
        this.selectedIndex = index
        this.$refs.list?.scrollToIndex(index, { align: 'center' })
      }
      this.detailEntry = entry
      this.detailRaw = entry.raw || entry.raw_preview || ''
    },

    // ── Keyboard shortcuts ─────────────────────────────────────────────────
    onKeydown(event) {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target
      const tagName = (target?.tagName || '').toLowerCase()
      if (tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target?.isContentEditable) return

      if (event.key === 'j') {
        event.preventDefault()
        this.moveSelection(1)
        return
      }
      if (event.key === 'k') {
        event.preventDefault()
        this.moveSelection(-1)
        return
      }
      if (event.key === 'Enter') {
        if (!this.selectedEntry) return
        event.preventDefault()
        this.openDetail()
        return
      }
      if (event.key === 'Escape') {
        if (this.detailOpen) {
          event.preventDefault()
          this.closeDetail()
          return
        }
        if (this.selectedIndex >= 0) this.selectedIndex = -1
      }
    },
  },
}
</script>
