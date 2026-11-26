<script setup>
/**
 * Changed-files block of a harness session (DSH "Edited N files +X -Y").
 *
 * Paths come from the session API: the diff of the task worktree when the run
 * used one, otherwise the session's write-tool arguments. A click opens the
 * file in the standard FileViewer (the project API resolves relative paths).
 */
import { computed } from 'vue'


const props = defineProps({
  files: { type: Array, default: () => [] },
  workspace: { type: Object, default: null },
  diff: { type: Object, default: null }
})

const summary = computed(() => {
  const count = props.files.length
  if (!count) return 'No files changed'
  return `Edited ${count} file${count === 1 ? '' : 's'}`
})
const branch = computed(() => props.workspace?.branch || props.diff?.branch || '')
const additions = computed(() =>
  props.files.reduce((total, file) => total + (Number(file.additions) || 0), 0)
)
const deletions = computed(() =>
  props.files.reduce((total, file) => total + (Number(file.deletions) || 0), 0)
)

function statusIcon(status) {
  if (status === 'added') return 'fa-solid fa-plus text-success'
  if (status === 'deleted') return 'fa-solid fa-minus text-error'
  if (status === 'renamed') return 'fa-solid fa-arrow-right-arrow-left text-info'
  return 'fa-solid fa-pen text-warning'
}
</script>

<template>
  <div class="rounded-xl border border-base-content/10 bg-base-200/40 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2">
      <i class="fa-solid fa-code-compare text-base-content/50 text-xs"></i>
      <span class="text-xs font-semibold">{{ summary }}</span>
      <span v-if="branch" class="badge badge-ghost badge-xs font-mono truncate max-w-48" :title="branch">
        {{ branch }}
      </span>
      <span class="grow"></span>
      <span v-if="additions" class="text-[11px] text-success tabular-nums">+{{ additions }}</span>
      <span v-if="deletions" class="text-[11px] text-error tabular-nums">-{{ deletions }}</span>
    </div>

    <div v-if="files.length" class="border-t border-base-content/10 divide-y divide-base-content/5 max-h-64 overflow-y-auto">
      <div
        v-for="file in files"
        :key="file.path"
        class="w-full flex items-center gap-2 px-3 py-1.5 text-left"
        :title="file.path"
      >
        <i :class="[statusIcon(file.status), 'w-3 text-center text-[10px] shrink-0']"></i>
        <span class="font-mono text-[11px] truncate min-w-0">{{ file.path }}</span>
        <span class="grow"></span>
        <span v-if="file.additions" class="text-[10px] text-success/80 tabular-nums shrink-0">+{{ file.additions }}</span>
        <span v-if="file.deletions" class="text-[10px] text-error/80 tabular-nums shrink-0">-{{ file.deletions }}</span>
      </div>
    </div>
    <p v-else class="border-t border-base-content/10 px-3 py-2 text-[11px] text-base-content/40">
      No files changed yet.
    </p>
  </div>
</template>
