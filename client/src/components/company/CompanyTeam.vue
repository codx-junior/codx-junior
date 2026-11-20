<script setup>
import { ref, watch } from 'vue'
import CompanyTeams from './CompanyTeams.vue'
import CompanyPeople from './CompanyPeople.vue'
import { useCompanyI18n } from './i18n'

/**
 * Team — one company destination for the two halves of a team: the operating
 * units (teams) and the humans/AI profiles that belong to them.
 */
const props = defineProps({
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false },
  focusId: { type: String, default: '' }
})

const emit = defineEmits([
  'assistant', 'open-chat', 'new-goal', 'new-team'
])

const { t } = useCompanyI18n()
const tab = ref('teams')

watch(() => props.focusId, value => {
  if (value) tab.value = 'teams'
}, { immediate: true })
</script>

<template>
  <div class="space-y-3">
    <div class="cp-card inline-flex items-center gap-0.5 p-0.5" role="tablist" aria-label="Team views">
      <button
        type="button"
        role="tab"
        class="cp-chip !h-8 !border-transparent !px-3"
        :class="tab === 'teams' ? '!bg-[var(--cp-lime)] !text-[var(--cp-lime-ink)]' : ''"
        :aria-selected="tab === 'teams'"
        @click="tab = 'teams'"
      >
        <i class="fa-solid fa-people-group text-[9px]"></i> {{ t('team.tabTeams') }}
      </button>
      <button
        type="button"
        role="tab"
        class="cp-chip !h-8 !border-transparent !px-3"
        :class="tab === 'people' ? '!bg-[var(--cp-lime)] !text-[var(--cp-lime-ink)]' : ''"
        :aria-selected="tab === 'people'"
        @click="tab = 'people'"
      >
        <i class="fa-solid fa-user-group text-[9px]"></i> {{ t('team.tabPeople') }}
      </button>
    </div>

    <CompanyTeams
      v-if="tab === 'teams'"
      :snapshot="snapshot"
      :actors="actors"
      :can-manage="canManage"
      :focus-id="focusId"
      @assistant="emit('assistant', $event)"
      @open-chat="(teamId) => emit('open-chat', teamId)"
      @new-goal="(teamId) => emit('new-goal', teamId)"
      @new-team="emit('new-team')"
    />
    <CompanyPeople
      v-else
      :snapshot="snapshot"
      :actors="actors"
      :can-manage="canManage"
      :can-admin="canAdmin"
      @assistant="emit('assistant', $event)"
      @new-team="emit('new-team')"
    />
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
