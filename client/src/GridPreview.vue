<script>
import ComputerGridView from './components/browsers/ComputerGridView.vue'

// Temporary harness: renders the grid component with fake sessions so the
// layouts can be verified without logging into the application.
const fakeSessions = [
  { id: 's1', name: 'Ubuntu desktop', kind: 'linux', status: 'running', controller: 'ai' },
  { id: 's2', name: 'Windows 11 VM', kind: 'windows', status: 'running', controller: 'user' },
  { id: 's3', name: 'Chromium session', kind: 'browser', status: 'running', controller: 'ai' },
  { id: 's4', name: 'macOS Sonoma', kind: 'macos', status: 'stopped', controller: 'none' },
  { id: 's5', name: 'Remote mac-mini', kind: 'remote', status: 'running', controller: 'ai' }
]

export default {
  name: 'GridPreview',
  components: { ComputerGridView },
  data() {
    return {
      sessions: fakeSessions,
      layout: 'grid',
      activeId: 's1'
    }
  }
}
</script>

<template>
  <div class="h-screen w-screen flex flex-col bg-base-300 text-base-content">
    <div class="flex items-center gap-2 p-3 border-b border-base-content/10 bg-base-100">
      <strong class="text-sm mr-2">Grid preview</strong>
      <button
        v-for="layout in ['grid', 'spotlight', 'sidebar']"
        :key="layout"
        class="btn btn-xs"
        :class="layout === layout && 'btn-active'"
        :style="layout === layout ? '' : ''"
        @click="layout && (this.layout = layout)"
      >{{ layout }}</button>
      <span class="ml-4 text-xs opacity-60">active: {{ activeId }}</span>
    </div>
    <div class="flex-1 min-h-0 p-2">
      <ComputerGridView
        :sessions="sessions"
        :layout="layout"
        :active-id="activeId"
        @activate="activeId = $event"
      />
    </div>
  </div>
</template>
