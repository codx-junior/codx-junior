<template>
  <modal close title="Send a file to the page" @close="$emit('close')">
    <div class="flex flex-col gap-3 md:w-[36rem]">
      <p class="text-xs text-base-content/70">
        The browser image's own file dialog cannot browse the project folders
        (and the kiosk browser does not deliver the selection). Pick a file here
        and codx-junior pushes the bytes into the page's uploader instead — no
        native dialog involved.
      </p>

      <label class="flex flex-col gap-1">
        <span class="text-[11px] font-semibold uppercase tracking-wide opacity-60">
          File input or upload button (optional)
        </span>
        <input
          v-model="selector"
          type="text"
          class="input input-sm input-bordered w-full font-mono"
          placeholder="input[type=file]  (leave empty to auto-detect)"
        />
        <span class="text-[10px] opacity-50">
          Pass a selector when the page has several uploaders (avatar + cover)
          or when the input only exists inside a dialog.
        </span>
      </label>

      <div class="flex items-center justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wide opacity-60">
          Files in this session
        </span>
        <button class="btn btn-xs btn-ghost" :disabled="loading" @click="load">
          <span v-if="loading" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-rotate"></i>
          Refresh
        </button>
      </div>

      <div class="max-h-56 overflow-y-auto rounded-lg border border-base-content/10 divide-y divide-base-content/10">
        <p v-if="!loading && !files.length" class="p-3 text-xs opacity-60">
          No files yet. Upload one from this computer below, or download it with
          the browser first.
        </p>
        <label
          v-for="file in files"
          :key="file.relative_path || file.name"
          class="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-base-content/5"
          :class="path === fileValue(file) && 'bg-primary/10'"
        >
          <input v-model="path" type="radio" class="radio radio-xs" :value="fileValue(file)" />
          <i class="fa-regular fa-file text-base-content/40" aria-hidden="true"></i>
          <span class="flex-1 truncate font-medium">{{ file.name }}</span>
          <span class="opacity-50">{{ formatSize(file.size) }}</span>
        </label>
      </div>

      <label class="flex items-center gap-2 text-xs">
        <span class="btn btn-xs btn-outline gap-1">
          <span v-if="uploading" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-arrow-up-from-bracket"></i>
          Upload from this computer
        </span>
        <input type="file" class="hidden" :disabled="uploading" @change="onFileChosen" />
        <span class="opacity-50">the file is stored in the session's downloads folder</span>
      </label>

      <p v-if="error" class="rounded bg-error/10 px-2 py-1.5 text-xs text-error">{{ error }}</p>
      <p v-if="result" class="rounded bg-success/10 px-2 py-1.5 text-xs text-success">
        ✅ {{ result }}
      </p>

      <div class="flex items-center justify-end gap-2 pt-1">
        <button class="btn btn-sm btn-ghost" @click="$emit('close')">Close</button>
        <button
          class="btn btn-sm btn-primary gap-1"
          :disabled="!path || sending"
          @click="send"
        >
          <span v-if="sending" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-paperclip"></i>
          Send to page
        </button>
      </div>
    </div>
  </modal>
</template>

<script>
/**
 * Push a session file into the page's uploader without the browser's native
 * file dialog.
 *
 * Why this exists: the kiosk browser image can open a file dialog but does not
 * deliver the picked file to the page (unlike the desktop image, which has a
 * real desktop session). The backend resolves the file from the session's
 * downloads/shared folders, streams its bytes over CDP and sets them on the
 * page's ``<input type="file">`` - the exact same path the AI uses
 * (``browse_action(action="upload")``).
 */
export default {
  name: 'SendFileToPage',
  props: {
    sessionId: { type: String, required: true }
  },
  emits: ['close'],
  data() {
    return {
      loading: false,
      uploading: false,
      sending: false,
      files: [],
      path: '',
      selector: '',
      error: null,
      result: null
    }
  },
  mounted() {
    this.load()
  },
  methods: {
    async load() {
      this.loading = true
      this.error = null
      try {
        const response = await this.$storex.api.browsers.sharedFiles(this.sessionId)
        const files = (response?.files || []).slice()
        files.sort((a, b) => String(b.modified_at || '').localeCompare(String(a.modified_at || '')))
        this.files = files
        if (!this.path && files.length) this.path = this.fileValue(files[0])
      } catch (error) {
        this.error = error?.message || 'Could not list the session files'
      } finally {
        this.loading = false
      }
    },
    fileValue(file) {
      // Downloads-relative is what the backend resolver tries first; it also
      // accepts a bare name, a browser path or a host path.
      return file?.relative_path || file?.name || ''
    },
    async onFileChosen(event) {
      const file = event?.target?.files?.[0]
      if (!file) return
      this.uploading = true
      this.error = null
      this.result = null
      try {
        const uploaded = await this.$storex.api.browsers.uploadFile(this.sessionId, file)
        await this.load()
        this.path = uploaded?.path || this.path
        this.result = `Stored ${uploaded?.name || file.name} (${this.formatSize(uploaded?.size || file.size)})`
      } catch (error) {
        this.error = error?.response?.data?.detail || error?.message || 'Could not upload the file'
      } finally {
        this.uploading = false
        if (event?.target) event.target.value = ''
      }
    },
    async send() {
      if (!this.path || this.sending) return
      this.sending = true
      this.error = null
      this.result = null
      try {
        const response = await this.$storex.api.browsers.sendFileToPage(this.sessionId, {
          path: this.path,
          selector: this.selector.trim() || null
        })
        this.result = `Sent ${response?.file || 'the file'} to the page` +
          (response?.strategy ? ` (${response.strategy})` : '') +
          '. Check the preview/attachment before submitting.'
      } catch (error) {
        this.error = error?.response?.data?.detail || error?.message || 'The page did not accept the file'
      } finally {
        this.sending = false
      }
    },
    formatSize(value) {
      let size = Number(value || 0)
      if (!size) return ''
      for (const unit of ['B', 'KB', 'MB', 'GB']) {
        if (size < 1024 || unit === 'GB') {
          return unit === 'B' ? `${Math.round(size)} B` : `${size.toFixed(1)} ${unit}`
        }
        size /= 1024
      }
      return ''
    }
  }
}
</script>
