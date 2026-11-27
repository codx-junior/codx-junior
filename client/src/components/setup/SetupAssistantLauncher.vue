<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { $storex } from '@/store'
import SetupAssistantPanel from './SetupAssistantPanel.vue'

/**
 * Floating setup assistant (workspace mode).
 *
 * The launcher is a single codx-junior logo button in the bottom-right corner
 * (above the mobile tab bar on phones). It opens a fixed-size card that can be
 * closed again — the old inline implementation rendered the wizard without its
 * `open` prop, so clicking the button did nothing.
 */
const open = ref(false)

const context = computed(() => ({
  projects: ($storex.projects?.allProjects || []).length,
  hasAi: Boolean(($storex.projects?.ai?.models || []).length)
}))

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape') close()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <button
    type="button"
    class="btn btn-primary btn-circle fixed bottom-20 right-4 z-[114] h-12 w-12 shadow-lg lg:bottom-5 lg:right-5"
    :title="open ? 'Hide setup assistant' : 'Setup assistant'"
    :aria-label="open ? 'Hide setup assistant' : 'Open setup assistant'"
    :aria-expanded="open"
    @click="toggle"
  >
    <img src="/only_icon.png" alt="codx-junior" class="h-7 w-7 rounded-full" />
  </button>

  <Transition name="assistant-pop">
    <section
      v-if="open"
      class="fixed bottom-36 right-3 z-[115] flex h-[min(70vh,520px)] w-[min(94vw,380px)] flex-col overflow-hidden rounded-2xl border border-base-300 bg-base-100 shadow-2xl lg:bottom-24 lg:right-5"
      role="dialog"
      aria-modal="false"
      aria-label="Setup assistant"
    >
      <header class="flex items-center gap-2 border-b border-base-300 bg-base-100 px-3 py-2">
        <img src="/only_icon.png" alt="codx-junior" class="h-7 w-7 rounded-lg" />
        <div class="min-w-0 flex-1">
          <div class="truncate text-sm font-bold">Setup assistant</div>
          <div class="truncate text-[10px] text-base-content/55">
            {{ context.hasAi ? 'AI chat is ready' : 'Preconfigured guide' }}
          </div>
        </div>
        <button
          type="button"
          class="btn btn-ghost btn-xs btn-circle"
          aria-label="Close setup assistant"
          @click="close"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </header>
      <div class="min-h-0 flex-1">
        <SetupAssistantPanel :context="context" compact hide-header />
      </div>
    </section>
  </Transition>
</template>

<style scoped>
.assistant-pop-enter-active,
.assistant-pop-leave-active {
  transition: opacity 150ms ease, transform 150ms ease;
}
.assistant-pop-enter-from,
.assistant-pop-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.98);
}
</style>
