<script setup>
import { computed } from 'vue'
import CalendarView from '@/views/CalendarView.vue'
import MeetView from '@/views/MeetView.vue'
import BrowsersSessionManager from '@/components/browsers/BrowsersSessionManager.vue'

/**
 * Meet, CoWorking and Calendar embedded in the company main area.
 *
 * These are the existing product surfaces, not copies: the company shell only
 * provides the frame. Keeping them here means the owner never loses the company
 * context when checking a meeting, a coworking machine or the agenda.
 */
const props = defineProps({
  kind: { type: String, default: 'calendar' }
})

const emit = defineEmits(['close'])

const view = computed(() => ({
  meet: MeetView,
  coworking: BrowsersSessionManager,
  calendar: CalendarView
}[props.kind] || CalendarView))
</script>

<template>
  <div class="h-[calc(100dvh-8.5rem)] min-h-[560px] overflow-hidden rounded-[22px] border border-[var(--cp-border)] bg-base-100">
    <component
      :is="view"
      :params="{}"
      :embedded="kind === 'meet'"
      @close="emit('close')"
    />
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
