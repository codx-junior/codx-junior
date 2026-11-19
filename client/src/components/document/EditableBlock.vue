<script setup>
import { ref } from 'vue'
import BlockEditor from '../BlockEditor.vue'

const props = defineProps({
  content: { type: String, default: '' },
  blockIndex: { type: Number, default: 0 },
  editable: { type: Boolean, default: false },
  label: { type: String, default: 'Editing markdown block' }
})

const emit = defineEmits(['save'])
const blockEditor = ref(null)
const editing = ref(false)

function startEdit() {
  if (!props.editable) return
  blockEditor.value?.startEdit()
}

function onSave({ originalContent, newContent }) {
  editing.value = false
  emit('save', {
    originalContent,
    newContent,
    blockIndex: props.blockIndex
  })
}
</script>

<template>
  <div class="relative">
    <BlockEditor
      ref="blockEditor"
      :original-content="content"
      :is-code-block="false"
      :label="label"
      @edit-start="editing = true"
      @edit-cancel="editing = false"
      @edit-save="onSave"
    >
      <slot></slot>
    </BlockEditor>
  </div>
</template>
