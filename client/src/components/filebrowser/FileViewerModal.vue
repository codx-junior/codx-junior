<script setup>
/**
 * FileViewer as a dialog — the single-mode counterpart of the desktop
 * `file-viewer` dockview panel.
 *
 * File references found in markdown messages (`./coolify-install.sh`,
 * `src/App.vue`, …) call `$ui.openFileViewer()`, which shows the panel on
 * desktop and this dialog everywhere else, so the chat / board the reference
 * lives in stays behind it. Mounted once, globally, from App.vue.
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { $storex } from '@/store'
import FileViewer from './FileViewer.vue'

const props = defineProps({
  filePath: { type: String, required: true }
})

const emit = defineEmits(['close'])

const fileName = computed(() => props.filePath.split('/').filter(Boolean).pop() || props.filePath)

function close() {
  emit('close')
}

function copyPath() {
  $storex.ui.copyTextToClipboard(props.filePath)
}

function onKeyDown(event) {
  if (event.key === 'Escape') close()
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <div
    class="fixed inset-0 z-[70] flex items-stretch justify-center bg-base-100/70 backdrop-blur-sm sm:items-center sm:p-4"
    @click.self="close"
  >
    <div
      class="relative flex h-full w-full flex-col overflow-hidden border-base-content/10 bg-base-300 shadow-2xl sm:h-[90vh] sm:w-[min(1400px,95vw)] sm:rounded-xl sm:border"
    >
      <!-- Slim header: the full path (the viewer itself shows the file name) -->
      <div class="flex shrink-0 items-center gap-2 border-b border-base-content/10 px-3 py-2">
        <i class="fa-regular fa-file text-primary"></i>
        <span class="truncate font-mono text-xs opacity-70" :title="filePath">{{ filePath }}</span>
        <div class="grow"></div>
        <button
          class="btn btn-xs btn-ghost"
          title="Copy path"
          aria-label="Copy path"
          @click="copyPath"
        >
          <i class="fa-regular fa-copy"></i>
        </button>
        <button
          class="btn btn-xs btn-ghost"
          title="Close (Esc)"
          aria-label="Close file viewer"
          @click="close"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <FileViewer :key="filePath" :params="{ filePath }" class="min-h-0 flex-1" />
    </div>
  </div>
</template>
