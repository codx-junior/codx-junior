<script>
/**
 * "Which machines go on the wall" picker.
 *
 * A modal on purpose: the same dialog has to work from the desktop toolbar, the
 * mobile header and the empty-wall call to action, and an inline dropdown would
 * be clipped by the panels that host the grid.
 */
export default {
  name: 'ComputerGridPicker',
  props: {
    sessions: { type: Array, default: () => [] },
    selectedIds: { type: Array, default: () => [] }
  },
  emits: ['toggle', 'select-all', 'clear', 'close'],
  computed: {
    selectedCount() {
      return this.selectedIds.length
    }
  },
  mounted() {
    window.addEventListener('keydown', this.onKeydown)
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onKeydown)
  },
  methods: {
    onKeydown(event) {
      if (event.key === 'Escape') this.$emit('close')
    },
    kindIcon(session) {
      const kind = session?.kind || 'browser'
      return {
        linux: 'fa-brands fa-linux',
        windows: 'fa-brands fa-windows',
        macos: 'fa-brands fa-apple',
        remote: 'fa-solid fa-network-wired',
        browser: 'fa-solid fa-compass'
      }[kind] || 'fa-solid fa-desktop'
    },
    statusDotClass(status) {
      if (status === 'running') return 'bg-success'
      if (status === 'stopped') return 'bg-warning'
      return 'bg-base-content/30'
    }
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-base-300/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="computer-grid-picker-title"
      @click.self="$emit('close')"
    >
      <div class="flex max-h-[80vh] w-full max-w-sm flex-col overflow-hidden rounded-2xl border border-base-content/10 bg-base-100 shadow-2xl">
        <div class="flex items-center gap-2 border-b border-base-content/10 px-4 py-3">
          <i class="fa-solid fa-table-cells-large text-primary" aria-hidden="true"></i>
          <h3 id="computer-grid-picker-title" class="flex-1 text-sm font-bold">Machines on the wall</h3>
          <span class="badge badge-sm">{{ selectedCount }}</span>
          <button class="btn btn-ghost btn-xs btn-square" title="Close" aria-label="Close" @click="$emit('close')">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="flex-1 overflow-y-auto p-1.5">
          <p v-if="!sessions.length" class="px-2 py-6 text-center text-xs opacity-60">
            No computer sessions yet.
          </p>
          <label
            v-for="session in sessions"
            :key="session.id"
            class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-base-200"
          >
            <input
              type="checkbox"
              class="checkbox checkbox-xs checkbox-primary"
              :checked="selectedIds.includes(session.id)"
              @change="$emit('toggle', session.id)"
            />
            <span class="h-1.5 w-1.5 flex-shrink-0 rounded-full" :class="statusDotClass(session.status)"></span>
            <i :class="kindIcon(session)" class="text-[11px] text-base-content/50 flex-shrink-0" aria-hidden="true"></i>
            <span class="min-w-0 flex-1 truncate text-xs">{{ session.name }}</span>
          </label>
        </div>

        <div class="flex items-center gap-2 border-t border-base-content/10 px-3 py-2">
          <button class="btn btn-ghost btn-xs" @click="$emit('select-all')">All</button>
          <button class="btn btn-ghost btn-xs" @click="$emit('clear')">None</button>
          <span class="flex-1"></span>
          <button class="btn btn-primary btn-xs" @click="$emit('close')">Done</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
