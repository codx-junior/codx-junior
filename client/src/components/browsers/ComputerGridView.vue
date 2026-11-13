<script>
import ComputerGridTile from './ComputerGridTile.vue'

/**
 * Wall of computer sessions ("grid view").
 *
 * Watching several machines at once is a layout problem, so the same tiles are
 * arranged in the three layouts people know from meeting/video-wall software:
 *
 *  - `grid`      uniform tiles; the column count follows the number of tiles
 *                (1, 2×2, 3×3, 4×4) so the wall never scrolls;
 *  - `spotlight` one principal machine with a filmstrip of thumbnails below;
 *  - `sidebar`   one principal machine with the thumbnails in a right rail.
 *
 * Only the principal (active) machine is interactive; see `ComputerGridTile`
 * for why. The component is stateless: the selection, the layout and the
 * active tile live in the browsers store, so the wall survives a view change.
 */
export default {
  name: 'ComputerGridView',
  components: { ComputerGridTile },
  props: {
    sessions: { type: Array, default: () => [] },
    layout: { type: String, default: 'grid' },
    activeId: { type: String, default: null }
  },
  emits: ['activate', 'open', 'remove', 'manage'],
  computed: {
    activeSession() {
      return this.sessions.find(session => session.id === this.activeId) || this.sessions[0] || null
    },
    activeSessionId() {
      return this.activeSession?.id || null
    },
    isStageLayout() {
      return this.layout !== 'grid'
    },
    /**
     * Square-ish wall: as many columns as keep the tiles readable, never more
     * than one empty cell per row for small counts.
     */
    columns() {
      const count = Math.max(1, this.sessions.length)
      if (count <= 1) return 1
      if (count <= 4) return 2
      if (count <= 9) return 3
      return 4
    },
    gridStyle() {
      const rows = Math.max(1, Math.ceil(this.sessions.length / this.columns))
      return {
        gridTemplateColumns: `repeat(${this.columns}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`
      }
    }
  }
}
</script>

<template>
  <div
    class="h-full min-h-0 w-full flex gap-2 overflow-hidden"
    :class="isStageLayout && sessions.length ? (layout === 'sidebar' ? 'flex-row' : 'flex-col') : 'flex-col'"
  >
    <!-- Nothing selected: say it instead of showing an empty wall -->
    <div
      v-if="!sessions.length"
      class="flex-1 flex items-center justify-center p-6 text-center text-base-content/50"
    >
      <div>
        <i class="fa-solid fa-table-cells-large text-4xl block mb-3 opacity-25" aria-hidden="true"></i>
        <p class="text-sm font-semibold">No machines on the wall</p>
        <p class="text-xs opacity-60 mt-1 max-w-xs">
          Pick the computer sessions you want to watch at the same time.
        </p>
        <button class="btn btn-sm btn-primary mt-3 gap-1" @click="$emit('manage')">
          <i class="fa-solid fa-list-check"></i>
          Select sessions
        </button>
      </div>
    </div>

    <template v-else>
      <!-- Principal stage (spotlight / sidebar) -->
      <div v-if="isStageLayout" class="flex-1 min-h-0 min-w-0 flex">
        <ComputerGridTile
          v-if="activeSession"
          :session="activeSession"
          :active="true"
          class="flex-1"
          @activate="$emit('activate', activeSession.id)"
          @open="$emit('open', activeSession.id)"
          @remove="$emit('remove', activeSession.id)"
        />
      </div>

      <!-- Uniform wall -->
      <div v-else class="flex-1 min-h-0 min-w-0 grid gap-2" :style="gridStyle">
        <ComputerGridTile
          v-for="session in sessions"
          :key="session.id"
          :session="session"
          :active="session.id === activeSessionId"
          @activate="$emit('activate', session.id)"
          @open="$emit('open', session.id)"
          @remove="$emit('remove', session.id)"
        />
      </div>

      <!-- Filmstrip -->
      <div v-if="layout === 'spotlight'" class="flex gap-2 flex-shrink-0 h-24 overflow-x-auto overflow-y-hidden">
        <ComputerGridTile
          v-for="session in sessions"
          :key="`thumb-${session.id}`"
          :session="session"
          :active="session.id === activeSessionId"
          compact
          preview-only
          class="h-full w-40 flex-shrink-0"
          @activate="$emit('activate', session.id)"
          @open="$emit('open', session.id)"
          @remove="$emit('remove', session.id)"
        />
      </div>

      <!-- Side rail -->
      <aside v-else-if="layout === 'sidebar'" class="flex flex-col gap-2 flex-shrink-0 w-44 overflow-y-auto overflow-x-hidden">
        <ComputerGridTile
          v-for="session in sessions"
          :key="`rail-${session.id}`"
          :session="session"
          :active="session.id === activeSessionId"
          compact
          preview-only
          class="h-24 w-full flex-shrink-0"
          @activate="$emit('activate', session.id)"
          @open="$emit('open', session.id)"
          @remove="$emit('remove', session.id)"
        />
      </aside>
    </template>
  </div>
</template>
