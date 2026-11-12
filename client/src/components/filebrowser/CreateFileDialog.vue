<script setup>
</script>

<template>
  <modal v-if="isVisible" :title="title" close @close="onCancel">
    <!-- One field only: the name is used verbatim (extension included when the
         user types it), so no extension parsing or checks happen here. -->
    <div class="flex flex-col gap-3 md:min-w-[20rem] md:max-w-md">
      <label class="form-control">
        <span class="label-text text-xs text-base-content/60">{{ label }}</span>
        <input
          v-model="name"
          type="text"
          :placeholder="placeholder"
          class="input input-bordered input-sm w-full mt-1"
          @keydown.enter="onSubmit"
          ref="inputRef"
        />
      </label>
    </div>

    <template #footer>
      <button class="btn btn-sm btn-ghost" @click="onCancel">Cancel</button>
      <button class="btn btn-sm btn-primary" @click="onSubmit" :disabled="!name.trim()">
        {{ confirmLabel }}
      </button>
    </template>
  </modal>
</template>

<script>
export default {
  props: {
    isVisible: {
      type: Boolean,
      default: false
    },
    // Creating a folder (no extension, no rename target)
    isDir: {
      type: Boolean,
      default: false
    },
    // 'create' (new file / folder) or 'rename' (rename the selected entry)
    mode: {
      type: String,
      default: 'create'
    },
    // Name prefilled when the dialog opens (the current name, when renaming)
    initialName: {
      type: String,
      default: ''
    }
  },
  emits: ['create', 'rename', 'cancel'],
  data() {
    return {
      name: ''
    }
  },
  computed: {
    isRename() {
      return this.mode === 'rename'
    },
    title() {
      if (this.isRename) return this.isDir ? 'Rename Folder' : 'Rename File'
      return this.isDir ? 'Create Folder' : 'Create File'
    },
    label() {
      if (this.isRename) return 'New name'
      return this.isDir ? 'Folder name' : 'Name'
    },
    placeholder() {
      return this.isDir || this.isRename ? 'Enter name' : 'e.g. notes.md'
    },
    confirmLabel() {
      if (this.isRename) return 'Rename'
      return this.isDir ? 'Create Folder' : 'Create File'
    }
  },
  watch: {
    isVisible(newVal) {
      if (!newVal) return
      this.name = this.initialName || ''
      this.$nextTick(() => {
        const input = this.$refs.inputRef
        if (!input) return
        input.focus()
        // Renaming: preselect the name without the extension, so typing replaces
        // it while the extension is kept unless the user deletes it too.
        const dot = this.name.lastIndexOf('.')
        const end = this.isRename && !this.isDir && dot > 0 ? dot : this.name.length
        input.setSelectionRange(0, end)
      })
    }
  },
  methods: {
    onSubmit() {
      const name = this.name.trim()
      if (!name) return
      // The name goes to the caller exactly as typed
      this.$emit(this.isRename ? 'rename' : 'create', { name })
      this.name = ''
    },
    onCancel() {
      this.name = ''
      this.$emit('cancel')
    }
  }
}
</script>
