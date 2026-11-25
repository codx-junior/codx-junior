<script setup>
import { computed, ref } from 'vue'
import CalendarView from '@/views/CalendarView.vue'
import MeetView from '@/views/MeetView.vue'
import BrowsersSessionManager from '@/components/browsers/BrowsersSessionManager.vue'
import CompanyFiles from './CompanyFiles.vue'

/**
 * Meet, CoWorking and Calendar embedded in the company main area.
 *
 * These are the existing product surfaces, not copies: the company shell only
 * provides the frame. The CoWorking surface also exposes the company-owned
 * coworking spaces (company files + virtual project folders), because a space
 * belongs to the company and not to a single project.
 */
const props = defineProps({
  kind: { type: String, default: 'calendar' },
  companyId: { type: String, default: '' },
  snapshot: { type: Object, default: null },
  // Projects linked to the company. The embedded Calendar uses them as its
  // whole project scope instead of the installation-wide catalogue.
  projectIds: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false }
})

const emit = defineEmits(['close'])

const coworkingTab = ref('machines')
const isCoworking = computed(() => props.kind === 'coworking')
const view = computed(() => ({
  meet: MeetView,
  coworking: BrowsersSessionManager,
  calendar: CalendarView
}[props.kind] || CalendarView))
</script>

<template>
  <div class="h-[calc(100dvh-8.5rem)] min-h-[560px] flex flex-col overflow-hidden rounded-[22px] bg-base-100">
    <div v-if="isCoworking" class="flex shrink-0 items-center gap-1 border-b border-base-content/10 px-3 pt-2">
      <button
        type="button"
        class="btn btn-xs btn-ghost gap-1"
        :class="coworkingTab === 'machines' ? 'btn-active' : ''"
        @click="coworkingTab = 'machines'"
      >
        <i class="fa-solid fa-window-maximize"></i> Machines
      </button>
      <button
        type="button"
        class="btn btn-xs btn-ghost gap-1"
        :class="coworkingTab === 'files' ? 'btn-active' : ''"
        @click="coworkingTab = 'files'"
      >
        <i class="fa-solid fa-folder-tree"></i> Company files
      </button>
    </div>

    <CompanyFiles
      v-if="isCoworking && coworkingTab === 'files'"
      class="min-h-0 flex-1 overflow-y-auto p-3"
      :company-id="companyId"
      :snapshot="snapshot"
      :can-manage="canManage"
    />
    <component
      v-else
      :is="view"
      class="min-h-0 flex-1"
      :params="{}"
      :project-ids="kind === 'calendar' ? projectIds : undefined"
      :embedded="kind === 'meet'"
      @close="emit('close')"
    />
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
