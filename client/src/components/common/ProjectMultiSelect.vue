<script setup>
import { computed } from 'vue'
import MultiSelectFilter from './MultiSelectFilter.vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  projects: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'All projects' },
  compact: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])

const options = computed(() => (props.projects || []).map(project => ({
  value: String(project.id),
  label: project.name || project.id,
  color: project.color || '#888'
})))

function update(value) {
  emit('update:modelValue', value)
}
</script>

<template>
  <MultiSelectFilter
    :model-value="modelValue"
    :options="options"
    :placeholder="placeholder"
    :all-label="placeholder"
    :compact="compact"
    icon="fa-solid fa-cubes"
    search-placeholder="Search projects by name…"
    empty-text="No projects match"
    @update:model-value="update"
  />
</template>
