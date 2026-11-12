<template>
  <div
    ref="viewport"
    class="log-virtual-list relative h-full w-full overflow-auto"
    @scroll.passive="onScroll"
  >
    <!-- Spacer standing in for every row above the rendered window -->
    <div :style="{ height: `${topSpacer}px` }" aria-hidden="true"></div>

    <!-- Only the visible rows (plus overscan) are mounted -->
    <div
      v-for="row in rows"
      :key="`${row.key}:${row.index}`"
      :data-row-index="row.index"
    >
      <slot :item="row.item" :index="row.index"></slot>
    </div>

    <!-- Spacer standing in for every row below the rendered window -->
    <div :style="{ height: `${bottomSpacer}px` }" aria-hidden="true"></div>
  </div>
</template>

<script>
/**
 * Dependency-free virtual scroller.
 *
 * With 10k+ log lines only the visible window (plus `overscan`) is in the DOM,
 * so scrolling and highlighting stay smooth. Rows may have different heights:
 * the component measures the rendered rows and remembers the height PER ITEM
 * KEY (not per index), which keeps the offsets correct when the live tail
 * prepends new entries.
 *
 * The layout is plain flow with two spacers (above and below the window), so
 * nothing has to be absolutely positioned and rows keep their natural height.
 *
 * Public methods (the Options API equivalent of `defineExpose`):
 *   - `scrollToIndex(index, { align })` — `start` (default), `center` or `auto`
 *     (`auto` only scrolls when the row is outside the viewport).
 *   - `scrollToTop()`, `scrollToBottom()`, `refresh()`.
 *
 * Emits `scroll-end` when the user gets close to the bottom, so the container
 * can request the next page.
 */

/** Rows rendered above/below the viewport by default. */
const DEFAULT_OVERSCAN = 10

/** How close (px) to the bottom the user must be for `scroll-end` to fire. */
const DEFAULT_END_THRESHOLD = 320

/** Ignore sub-pixel changes so measuring cannot ping-pong. */
const MEASURE_TOLERANCE = 0.5

export default {
  name: 'LogVirtualList',
  props: {
    /** Items to render (any shape; the row is provided by the parent slot). */
    items: { type: Array, default: () => [] },
    /** Estimated row height in px, used until a row has been measured. */
    itemHeight: { type: Number, default: 24 },
    /** Extra rows rendered outside the viewport. */
    overscan: { type: Number, default: DEFAULT_OVERSCAN },
    /** Item property used as the height cache key (ids keep prepends stable). */
    keyField: { type: String, default: 'id' },
    /** Distance from the bottom (px) that triggers `scroll-end`. */
    endThreshold: { type: Number, default: DEFAULT_END_THRESHOLD },
  },
  emits: ['scroll-end'],
  data() {
    return {
      scrollTop: 0,
      viewportHeight: 0,
      /** Item key -> measured height in px. */
      heights: {},
      endReached: false,
    }
  },
  computed: {
    /** Prefix sums: `offsets[i]` is the top of item `i`, `offsets[n]` the total. */
    offsets() {
      const items = this.items || []
      const heights = this.heights || {}
      const offsets = new Float64Array(items.length + 1)
      for (let index = 0; index < items.length; index += 1) {
        const measured = heights[this.keyOf(items[index], index)]
        offsets[index + 1] = offsets[index] + (measured > 0 ? measured : this.itemHeight)
      }
      return offsets
    },
    totalHeight() {
      return this.offsets[this.items.length]
    },
    /** First and last (exclusive) item indexes to mount, overscan included. */
    visibleRange() {
      const count = this.items.length
      if (!count) return { start: 0, end: 0 }
      const offsets = this.offsets
      const top = Math.max(0, this.scrollTop)
      const viewport = this.viewportHeight || this.itemHeight
      const start = this.findIndex(offsets, top)
      const end = Math.min(count, Math.max(start + 1, this.findIndex(offsets, top + viewport) + 1))
      return {
        start: Math.max(0, start - this.overscan),
        end: Math.min(count, end + this.overscan),
      }
    },
    rows() {
      const { start, end } = this.visibleRange
      const rows = []
      for (let index = start; index < end; index += 1) {
        const item = this.items[index]
        rows.push({ index, item, key: this.keyOf(item, index) })
      }
      return rows
    },
    topSpacer() {
      return this.offsets[this.visibleRange.start] || 0
    },
    bottomSpacer() {
      const remaining = this.totalHeight - (this.offsets[this.visibleRange.end] || 0)
      return remaining > 0 ? remaining : 0
    },
  },
  watch: {
    items: {
      flush: 'post',
      handler() {
        this.$nextTick(() => {
          this.syncViewport()
          this.measureRows()
        })
      },
    },
    itemHeight() {
      // The estimate changed: measured heights are still valid, keep them.
      this.$nextTick(this.measureRows)
    },
  },
  created() {
    // Observers are not reactive state: keep them off the data() object.
    this.resizeObserver = null
    this.scrollFrame = 0
  },
  mounted() {
    this.syncViewport()
    this.observeViewport()
    this.measureRows()
  },
  updated() {
    this.measureRows()
  },
  beforeUnmount() {
    this.disconnectViewport()
    if (this.scrollFrame) {
      cancelAnimationFrame(this.scrollFrame)
      this.scrollFrame = 0
    }
  },
  methods: {
    /** Stable key of one item (falls back to its index). */
    keyOf(item, index) {
      if (item && typeof item === 'object' && this.keyField) {
        const value = item[this.keyField]
        if (value !== undefined && value !== null && value !== '') return String(value)
      }
      return `#${index}`
    },
    /**
     * Largest index whose top is <= `y` (binary search over the prefix sums).
     * Keeps the range lookup O(log n) so 10k rows scroll smoothly.
     */
    findIndex(offsets, y) {
      const count = this.items.length
      if (!count) return 0
      let low = 0
      let high = count - 1
      while (low < high) {
        const middle = Math.ceil((low + high) / 2)
        if (offsets[middle] <= y) low = middle
        else high = middle - 1
      }
      return low
    },
    syncViewport() {
      const viewport = this.$refs.viewport
      if (!viewport) return
      this.viewportHeight = viewport.clientHeight
      this.scrollTop = viewport.scrollTop
    },
    observeViewport() {
      const viewport = this.$refs.viewport
      if (!viewport) return
      if (typeof ResizeObserver === 'undefined') {
        window.addEventListener('resize', this.syncViewport)
        return
      }
      this.resizeObserver = new ResizeObserver(() => {
        this.syncViewport()
        this.measureRows()
      })
      this.resizeObserver.observe(viewport)
    },
    disconnectViewport() {
      if (this.resizeObserver) {
        this.resizeObserver.disconnect()
        this.resizeObserver = null
        return
      }
      window.removeEventListener('resize', this.syncViewport)
    },
    /**
     * Read the real height of every mounted row and commit the changes in one
     * reactive update (one prefix-sum rebuild instead of one per row).
     */
    measureRows() {
      const viewport = this.$refs.viewport
      if (!viewport) return
      const nodes = viewport.querySelectorAll('[data-row-index]')
      if (!nodes.length) return
      const known = this.heights || {}
      let next = null
      nodes.forEach((node) => {
        const index = Number(node.dataset.rowIndex)
        const item = this.items[index]
        if (!Number.isFinite(index) || !item) return
        // Fractions matter: rounding every row would accumulate a visible
        // drift over thousands of entries.
        const height = node.getBoundingClientRect().height
        if (!height) return
        const key = this.keyOf(item, index)
        const previous = known[key]
        if (previous && Math.abs(previous - height) <= MEASURE_TOLERANCE) return
        if (!next) next = { ...known }
        next[key] = height
      })
      if (next) this.heights = next
    },
    onScroll(event) {
      const viewport = event?.target || this.$refs.viewport
      if (!viewport) return
      this.scrollTop = viewport.scrollTop
      this.viewportHeight = viewport.clientHeight
      if (this.scrollFrame) return
      this.scrollFrame = requestAnimationFrame(() => {
        this.scrollFrame = 0
        this.checkScrollEnd(viewport)
      })
    },
    /** Emit `scroll-end` once per approach so the container can page in data. */
    checkScrollEnd(viewport) {
      const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      if (distance <= this.endThreshold) {
        if (this.endReached) return
        this.endReached = true
        this.$emit('scroll-end')
        return
      }
      if (distance > this.endThreshold * 2) this.endReached = false
    },
    /** Height of one item (measured value, or the estimate). */
    heightOf(index) {
      const measured = this.heights[this.keyOf(this.items[index], index)]
      return measured > 0 ? measured : this.itemHeight
    },
    /**
     * Scroll so that `index` is visible.
     *
     * @param {number} index - Item index.
     * @param {object} [options]
     * @param {'start'|'center'|'auto'} [options.align='start'] - Scroll strategy.
     */
    scrollToIndex(index, options = {}) {
      const viewport = this.$refs.viewport
      if (!viewport || !this.items.length) return
      const target = Math.min(Math.max(Number(index) || 0, 0), this.items.length - 1)
      const top = this.offsets[target]
      const height = this.heightOf(target)
      const align = options.align || 'start'
      if (align === 'auto') {
        const viewTop = viewport.scrollTop
        const viewBottom = viewTop + viewport.clientHeight
        if (top >= viewTop && top + height <= viewBottom) return
      }
      const next = align === 'center'
        ? Math.max(0, top - viewport.clientHeight / 2 + height / 2)
        : top
      viewport.scrollTop = next
      this.scrollTop = viewport.scrollTop
    },
    scrollToTop() {
      const viewport = this.$refs.viewport
      if (!viewport) return
      viewport.scrollTop = 0
      this.scrollTop = 0
      this.endReached = false
    },
    scrollToBottom() {
      const viewport = this.$refs.viewport
      if (!viewport) return
      viewport.scrollTop = viewport.scrollHeight
      this.scrollTop = viewport.scrollTop
    },
    /** Re-read the viewport size and the mounted row heights. */
    refresh() {
      this.syncViewport()
      this.measureRows()
    },
  },
}
</script>
