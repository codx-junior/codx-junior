<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'All' },
  allLabel: { type: String, default: '' },
  icon: { type: String, default: 'fa-solid fa-filter' },
  searchPlaceholder: { type: String, default: 'Search…' },
  emptyText: { type: String, default: 'No options' },
  compact: { type: Boolean, default: false },
  badge: { type: Boolean, default: true },
  minWidth: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue'])

const root = ref(null)
const dropdown = ref(null)
const open = ref(false)
const query = ref('')
const position = ref({ top: 0, left: 0, width: 288 })

const selected = computed(() => new Set((props.modelValue || []).map(String)))
const allText = computed(() => props.allLabel || props.placeholder)
const filteredOptions = computed(() => {
  const term = query.value.trim().toLowerCase()
  if (!term) return props.options
  return props.options.filter(option =>
    String(option.label || option.value || '').toLowerCase().includes(term)
  )
})
const label = computed(() => {
  const chosen = props.modelValue || []
  if (!chosen.length) return allText.value
  if (chosen.length === 1) {
    const option = props.options.find(item => String(item.value) === String(chosen[0]))
    return option?.label || String(chosen[0])
  }
  if (props.options.length && chosen.length === props.options.length) return allText.value
  return `${chosen.length} selected`
})

function isSelected(value) {
  return selected.value.has(String(value))
}

function toggle(value) {
  const key = String(value)
  const next = isSelected(key)
    ? props.modelValue.filter(item => String(item) !== key)
    : [...props.modelValue, key]
  emit('update:modelValue', next)
}

function selectAll() {
  emit('update:modelValue', props.options.map(option => String(option.value)))
}

function clearAll() {
  emit('update:modelValue', [])
}

function toggleOpen() {
  if (open.value) {
    open.value = false
    return
  }
  const rect = root.value?.getBoundingClientRect?.()
  if (rect) {
    const width = 288
    const left = Math.min(rect.left, Math.max(8, window.innerWidth - width - 8))
    position.value = { top: rect.bottom + 4, left, width }
  }
  open.value = true
}

function onDocumentClick(event) {
  if (root.value?.contains(event.target)) return
  if (dropdown.value?.contains(event.target)) return
  open.value = false
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div ref="root" class="relative shrink-0" @click.stop>
    <button
      type="button"
      class="btn btn-sm btn-ghost border border-base-300 justify-start gap-1.5 font-normal"
      :class="compact ? 'px-2' : 'min-w-40'"
      :style="minWidth ? { minWidth } : {}"
      :title="label"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggleOpen"
    >
      <i :class="[icon, 'text-xs text-base-content/40']"></i>
      <span class="truncate max-w-40 text-xs">{{ label }}</span>
      <span v-if="badge && modelValue.length" class="badge badge-xs badge-ghost">{{ modelValue.length }}</span>
      <i class="fa-solid fa-chevron-down text-[9px] text-base-content/30 ml-auto"></i>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="dropdown"
        class="fixed z-[80] rounded-lg border border-base-300 bg-base-100 p-2 shadow-xl"
        :style="{ top: `${position.top}px`, left: `${position.left}px`, width: `${position.width}px` }"
      >
        <div class="input input-xs input-bordered flex items-center gap-1.5">
          <i class="fa-solid fa-magnifying-glass text-[10px] opacity-40"></i>
          <input
            v-model="query"
            type="search"
            class="grow bg-transparent text-xs outline-none"
            :placeholder="searchPlaceholder"
            autofocus
            @keydown.esc="open = false"
          />
        </div>

        <div class="flex items-center justify-between px-1 pt-2 pb-1">
          <button type="button" class="link link-hover text-[11px] text-primary" @click="selectAll">Select all</button>
          <button type="button" class="link link-hover text-[11px] text-base-content/50" @click="clearAll">Clear</button>
        </div>

        <div class="max-h-64 overflow-y-auto" role="listbox" aria-multiselectable="true">
          <label
            v-for="option in filteredOptions"
            :key="String(option.value)"
            class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1.5 text-xs hover:bg-base-content/5"
            role="option"
            :aria-selected="isSelected(option.value)"
            :title="option.hint || option.label"
          >
            <input
              type="checkbox"
              class="checkbox checkbox-xs checkbox-primary"
              :checked="isSelected(option.value)"
              @change="toggle(option.value)"
            />
            <span
              v-if="option.color"
              class="h-2 w-2 rounded-full shrink-0"
              :style="{ backgroundColor: option.color }"
            ></span>
            <i v-else-if="option.icon" :class="[option.icon, 'text-[10px] text-base-content/40 shrink-0']"></i>
            <span class="truncate flex-1">{{ option.label }}</span>
            <span v-if="option.hint" class="max-w-24 truncate text-[10px] text-base-content/35">{{ option.hint }}</span>
          </label>
          <p v-if="!filteredOptions.length" class="px-2 py-3 text-center text-[11px] text-base-content/40">
            {{ emptyText }}
          </p>
        </div>

        <p class="border-t border-base-200 px-1 pt-2 text-[10px] text-base-content/40">
          Empty selection = no filter.
        </p>
      </div>
    </Teleport>
  </div>
</template>
