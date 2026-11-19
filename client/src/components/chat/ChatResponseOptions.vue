<script setup>
import { computed } from 'vue'

const props = defineProps({
  options: { type: Array, default: () => [] },
  // Disabled while the answer is still streaming or the chat is read-only.
  disabled: { type: Boolean, default: false },
  hint: {
    type: String,
    default: 'Click a reply, or write your own'
  }
})

const emit = defineEmits(['select'])

const visibleOptions = computed(() => (props.options || []).filter(option => option?.label))

function buttonClass(option) {
  const style = option?.style || ''
  const base = [
    'btn btn-sm h-auto min-h-8 justify-start gap-2 rounded-lg px-3 py-1.5',
    'text-left font-normal normal-case whitespace-normal',
    'focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none'
  ]
  const tone = {
    primary: 'btn-primary',
    secondary: 'btn-secondary',
    accent: 'btn-accent',
    danger: 'btn-outline btn-error',
    warning: 'btn-outline btn-warning',
    success: 'btn-outline btn-success',
    info: 'btn-outline btn-info',
    ghost: 'btn-ghost border border-base-content/20 hover:border-primary/50'
  }[style]
  if (tone) return [...base, tone]
  return [
    ...base,
    'btn-outline border-base-content/25 bg-base-100',
    'hover:border-primary/60 hover:bg-primary/5 hover:text-base-content'
  ]
}

function optionTitle(option) {
  if (option?.value && option.value !== option.label) {
    return `Send: ${option.value}`
  }
  return option?.description || 'Send this reply'
}

function select(option) {
  if (props.disabled) return
  emit('select', option)
}
</script>

<template>
  <div
    v-if="visibleOptions.length"
    class="mt-3 mb-1 flex flex-col gap-1.5"
    role="group"
    aria-label="Suggested replies"
  >
    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] leading-none">
      <span class="inline-flex items-center gap-1.5 font-semibold uppercase tracking-wide text-base-content/45">
        <i class="fa-solid fa-hand-pointer text-[10px]" aria-hidden="true"></i>
        Suggested replies
      </span>
      <span class="text-base-content/35 normal-case tracking-normal">{{ hint }}</span>
    </div>

    <div class="flex flex-wrap items-stretch gap-2">
      <button
        v-for="(option, index) in visibleOptions"
        :key="`${option.label}-${index}`"
        type="button"
        class="group/option"
        :class="[buttonClass(option), disabled && 'opacity-60']"
        :disabled="disabled"
        :title="optionTitle(option)"
        :aria-label="`Send reply: ${option.value || option.label}`"
        @click.stop="select(option)"
      >
        <i
          v-if="option.icon"
          :class="option.icon"
          class="shrink-0 text-[12px] opacity-70"
          aria-hidden="true"
        ></i>
        <span class="flex min-w-0 flex-col items-start gap-0.5">
          <span class="font-medium leading-snug break-words">{{ option.label }}</span>
          <span
            v-if="option.description"
            class="text-[11px] font-normal leading-snug opacity-70"
          >{{ option.description }}</span>
        </span>
        <i
          class="fa-solid fa-arrow-right ml-1 shrink-0 text-[10px] opacity-30 transition-opacity group-hover/option:opacity-70"
          aria-hidden="true"
        ></i>
      </button>
    </div>
  </div>
</template>
