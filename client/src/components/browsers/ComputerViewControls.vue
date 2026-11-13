<script>
/**
 * Stage controls: one machine or the wall, the wall arrangement and the wall
 * selection.
 *
 * They live in the sessions sidebar — the expanded list and the collapsed icon
 * rail — instead of a toolbar above the stage: the machines get every pixel of
 * the grid area and the controls stay reachable when the sidebar is folded.
 */
export default {
  name: 'ComputerViewControls',
  props: {
    collapsed: { type: Boolean, default: false }
  },
  emits: ['manage'],
  data() {
    return {
      layoutOptions: [
        { value: 'grid', icon: 'fa-solid fa-table-cells-large', title: 'Equal tiles (wall)' },
        { value: 'spotlight', icon: 'fa-solid fa-table-cells', title: 'One principal + thumbnails below' },
        { value: 'sidebar', icon: 'fa-solid fa-table-columns', title: 'One principal + thumbnails on the right' }
      ]
    }
  },
  computed: {
    isGridView() {
      return this.$storex.browsers.isGridView
    },
    gridLayout() {
      return this.$storex.browsers.gridLayout
    },
    gridSessionsCount() {
      return (this.$storex.browsers.gridSessionIds || []).length
    }
  },
  methods: {
    setViewMode(mode) {
      // The store seeds the wall on the first switch, so the controls and the
      // mobile header always produce the same state.
      this.$storex.browsers.setViewMode(mode)
    },
    setGridLayout(layout) {
      this.$storex.browsers.setGridLayout(layout)
    }
  }
}
</script>

<template>
  <!-- Collapsed rail: icon buttons, same footprint as the session avatars -->
  <div v-if="collapsed" class="flex flex-col items-center gap-1">
    <button
      type="button"
      class="flex h-10 w-10 items-center justify-center rounded-xl transition-colors duration-150 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      :class="!isGridView ? 'bg-primary/15 text-primary' : 'text-base-content/60 hover:bg-base-200 hover:text-base-content'"
      title="Single view: one machine"
      aria-label="Single view"
      :aria-pressed="!isGridView ? 'true' : 'false'"
      @click="setViewMode('single')"
    >
      <i class="fa-solid fa-square text-[13px]" aria-hidden="true"></i>
    </button>
    <button
      type="button"
      class="flex h-10 w-10 items-center justify-center rounded-xl transition-colors duration-150 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      :class="isGridView ? 'bg-primary/15 text-primary' : 'text-base-content/60 hover:bg-base-200 hover:text-base-content'"
      title="Grid view: several machines at once"
      aria-label="Grid view"
      :aria-pressed="isGridView ? 'true' : 'false'"
      @click="setViewMode('grid')"
    >
      <i class="fa-solid fa-table-cells-large text-[13px]" aria-hidden="true"></i>
    </button>

    <template v-if="isGridView">
      <div class="my-1 w-6 border-t border-base-content/10"></div>
      <button
        v-for="option in layoutOptions"
        :key="option.value"
        type="button"
        class="flex h-10 w-10 items-center justify-center rounded-xl transition-colors duration-150 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        :class="gridLayout === option.value ? 'bg-primary/15 text-primary' : 'text-base-content/60 hover:bg-base-200 hover:text-base-content'"
        :title="option.title"
        :aria-label="option.title"
        :aria-pressed="gridLayout === option.value ? 'true' : 'false'"
        @click="setGridLayout(option.value)"
      >
        <i :class="option.icon" class="text-[13px]" aria-hidden="true"></i>
      </button>
      <button
        type="button"
        class="relative flex h-10 w-10 items-center justify-center rounded-xl text-base-content/60 transition-colors duration-150 hover:bg-base-200 hover:text-base-content focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        :title="`Select the machines on the wall (${gridSessionsCount})`"
        aria-label="Select the machines on the wall"
        @click="$emit('manage')"
      >
        <i class="fa-solid fa-list-check text-[13px]" aria-hidden="true"></i>
        <span
          class="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-primary px-1 text-[9px] font-bold leading-4 text-primary-content"
          aria-hidden="true"
        >{{ gridSessionsCount }}</span>
      </button>
    </template>
  </div>

  <!-- Expanded: a titled block of buttons -->
  <div v-else class="flex flex-col gap-1.5">
    <span class="px-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/40">View</span>
    <div class="join w-full">
      <button
        type="button"
        class="btn btn-xs join-item flex-1 gap-1"
        :class="!isGridView && 'btn-active'"
        title="One machine at a time"
        :aria-pressed="!isGridView ? 'true' : 'false'"
        @click="setViewMode('single')"
      >
        <i class="fa-solid fa-square" aria-hidden="true"></i>
        Single
      </button>
      <button
        type="button"
        class="btn btn-xs join-item flex-1 gap-1"
        :class="isGridView && 'btn-active'"
        title="Watch several machines at once"
        :aria-pressed="isGridView ? 'true' : 'false'"
        @click="setViewMode('grid')"
      >
        <i class="fa-solid fa-table-cells-large" aria-hidden="true"></i>
        Grid
      </button>
    </div>

    <div v-if="isGridView" class="join w-full">
      <button
        v-for="option in layoutOptions"
        :key="option.value"
        type="button"
        class="btn btn-xs join-item flex-1"
        :class="gridLayout === option.value && 'btn-active'"
        :title="option.title"
        :aria-label="option.title"
        :aria-pressed="gridLayout === option.value ? 'true' : 'false'"
        @click="setGridLayout(option.value)"
      >
        <i :class="option.icon" aria-hidden="true"></i>
      </button>
    </div>

    <button
      v-if="isGridView"
      type="button"
      class="btn btn-xs btn-outline w-full justify-start gap-1.5"
      title="Choose the machines on the wall"
      @click="$emit('manage')"
    >
      <i class="fa-solid fa-list-check" aria-hidden="true"></i>
      <span class="flex-1 text-left">Machines</span>
      <span class="badge badge-xs">{{ gridSessionsCount }}</span>
    </button>
  </div>
</template>
