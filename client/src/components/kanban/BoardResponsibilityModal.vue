<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  board: { type: Object, default: null },
  flows: { type: Array, default: () => [] },
  users: { type: Array, default: () => [] },
  profiles: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'save'])

const flow = ref('agile')
const kind = ref('profile')
const name = ref('')
const propagate = ref(false)
const error = ref('')

const flowMeta = computed(() =>
  props.flows.find(item => item.value === flow.value) || props.flows[0] || null
)
const options = computed(() => {
  const seen = new Set()
  const result = []
  const add = (value, optionKind) => {
    const text = String(value || '').trim()
    if (!text) return
    const key = `${optionKind}:${text}`
    if (seen.has(key)) return
    seen.add(key)
    result.push({ value: text, kind: optionKind, label: text })
  }
  if (kind.value === 'user') {
    props.users.forEach(user => add(user.username || user.name, 'user'))
    add(props.board?.lead_name, 'user')
  } else {
    props.profiles.forEach(profile => add(profile.name || profile, 'profile'))
    add(props.board?.responsible_name, 'profile')
    add(props.board?.lead_name, 'profile')
    add(props.board?.assignee, 'profile')
  }
  return result
})

watch(
  () => props.board,
  board => {
    if (!board) return
    flow.value = board.flow || 'agile'
    kind.value = board.own_responsible_kind || (board.lead_kind === 'user' ? 'user' : 'profile')
    name.value = board.own_responsible_name || ''
    propagate.value = false
    error.value = ''
  },
  { immediate: true }
)

function submit() {
  const chosen = String(name.value || '').trim()
  emit('save', {
    boardId: props.board.id,
    flow: flow.value,
    responsibleKind: kind.value,
    responsibleName: chosen,
    propagateChildren: !!propagate.value
  })
}
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm" @keydown.esc="$emit('close')">
    <div class="w-full max-w-lg overflow-hidden rounded-xl border border-base-content/10 bg-base-100 shadow-2xl">
      <div class="flex items-start gap-3 border-b border-base-content/10 px-5 py-4">
        <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
          <i class="fa-solid fa-user-shield"></i>
        </div>
        <div class="min-w-0 flex-1">
          <h2 class="truncate text-base font-bold">Board responsibility</h2>
          <p class="truncate text-xs text-base-content/50">{{ board?.project_name }} · {{ board?.title }}</p>
        </div>
        <button class="btn btn-ghost btn-sm btn-circle" @click="$emit('close')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div class="space-y-4 px-5 py-4">
        <div class="rounded-lg border border-base-content/10 bg-base-200/50 p-3 text-xs">
          <div class="flex items-center gap-2">
            <span class="font-semibold">Current effective responsible:</span>
            <span class="badge badge-sm" :class="board?.responsible_source === 'default' ? 'badge-warning' : 'badge-ghost'">
              {{ board?.responsible_name || 'none' }}
            </span>
            <span class="badge badge-xs badge-outline">{{ board?.responsible_source || 'none' }}</span>
          </div>
          <p class="mt-1 text-base-content/50">
            A board without its own responsible inherits the nearest parent board's responsible.
            Every board always resolves to a responsible (ProjectManager as the last fallback).
          </p>
        </div>

        <div>
          <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Board flow</label>
          <select v-model="flow" class="select select-sm select-bordered w-full">
            <option v-for="item in flows" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
          <p v-if="flowMeta" class="mt-1 text-[11px] text-base-content/50">{{ flowMeta.hint }}</p>
        </div>

        <div class="grid grid-cols-[7rem_1fr] gap-2">
          <div>
            <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Kind</label>
            <select v-model="kind" class="select select-sm select-bordered w-full">
              <option value="profile">Profile</option>
              <option value="user">User</option>
            </select>
          </div>
          <div>
            <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Leader / responsible</label>
            <input
              v-model="name"
              list="board-responsible-options"
              class="input input-sm input-bordered w-full"
              :placeholder="board?.own_responsible_name ? 'Clear to inherit from the parent board' : 'Leave empty to inherit'"
            />
            <p class="mt-1 text-[11px] text-base-content/50">
              Current effective: <span class="font-semibold">{{ board?.responsible_name || 'none' }}</span>
              <span class="opacity-60"> ({{ board?.responsible_source || 'none' }})</span>
            </p>
            <datalist id="board-responsible-options">
              <option v-for="option in options" :key="`${option.kind}:${option.value}`" :value="option.value" />
            </datalist>
          </div>
        </div>

        <label class="flex cursor-pointer items-start gap-2 rounded-lg border border-base-content/10 p-2 hover:bg-base-content/5">
          <input v-model="propagate" type="checkbox" class="checkbox checkbox-xs checkbox-primary mt-0.5" />
          <span class="text-xs">
            <span class="block font-semibold">Assign to child boards without a responsible</span>
            <span class="block text-[11px] text-base-content/50">Inheritance already applies; this writes the value explicitly on those boards.</span>
          </span>
        </label>

        <p v-if="error" class="rounded-lg bg-error/10 p-2 text-xs text-error">{{ error }}</p>
      </div>

      <div class="flex justify-end gap-2 border-t border-base-content/10 px-5 py-3">
        <button class="btn btn-ghost btn-sm" @click="$emit('close')">Cancel</button>
        <button class="btn btn-primary btn-sm gap-2" :disabled="saving" @click="submit">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-check"></i>
          Save responsibility
        </button>
      </div>
    </div>
  </div>
</template>
