<template>
  <!--
    File explorer of ONE mounted volume of a co-desktop.

    The volumes of a coworking session are plain project folders: the container
    sees them through a bind, the project sees them through the files API. So
    the explorer is the same one-level-per-request listing the folder picker
    uses — no tree, no prefetch — with the container path shown as the address
    so the operator can map what the browser sees to what is on disk.
  -->
  <div
    class="modal modal-open"
    role="dialog"
    aria-modal="true"
    :aria-label="`Files of ${title}`"
    @click.self="close"
  >
    <div class="modal-box w-11/12 max-w-3xl max-h-[85vh] flex flex-col gap-3">
      <!-- Header -->
      <div class="flex items-start gap-2">
        <div class="min-w-0 flex-1">
          <h3 class="text-base font-bold truncate">{{ title }}</h3>
          <p class="text-[11px] opacity-60 truncate">
            Mounted in the co-desktop at
            <code class="font-mono">{{ containerPath || '—' }}</code>
            <span v-if="readonly" class="badge badge-xs badge-warning badge-outline ml-1">read only</span>
          </p>
        </div>
        <button class="btn btn-sm btn-circle btn-ghost" aria-label="Close the file explorer" @click="close">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <CoworkingFileBrowser :root-path="startPath" :root-label="title" />

      <p class="text-[10px] opacity-50">
        These files live in the project; the co-desktop only sees them through the bind above.
      </p>
    </div>
  </div>
</template>

<script>
import CoworkingFileBrowser from './CoworkingFileBrowser.vue'

/**
 * Modal wrapper around the inline file browser (used by the volume table).
 */
export default {
  name: 'CoworkingVolumeExplorer',
  components: { CoworkingFileBrowser },
  props: {
    /** Volume label shown in the title. */
    title: { type: String, default: 'Mounted files' },
    /** Project-relative folder to open ('' = project root). */
    startPath: { type: String, default: '' },
    /** Path the same folder has INSIDE the container (informational). */
    containerPath: { type: String, default: '' },
    readonly: { type: Boolean, default: false }
  },
  emits: ['close'],
  methods: {
    close() {
      this.$emit('close')
    },
    handleWindowKeydown(event) {
      if (event.key === 'Escape') this.close()
    }
  },
  mounted() {
    window.addEventListener('keydown', this.handleWindowKeydown)
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.handleWindowKeydown)
  }
}
</script>
