<template>
  <modal close title="Sessions &amp; passwords" @close="$emit('close')">
    <div class="flex flex-col gap-3 md:w-[40rem]">
      <p class="text-xs text-base-content/70">
        Keep the logins you already have when you switch browser. Saved
        <strong>site sessions</strong> carry the cookies and local storage of the
        sites this browser is logged into; <strong>credentials</strong> store the
        password so you (or the AI) can fill a login form without typing it again.
        Both are project-scoped and encrypted with the project vault key.
      </p>

      <div role="tablist" class="tabs tabs-boxed tabs-sm self-start">
        <button
          type="button"
          role="tab"
          class="tab"
          :class="tab === 'sessions' ? 'tab-active' : ''"
          @click="tab = 'sessions'"
        >
          <i class="fa-solid fa-cookie-bite mr-1"></i>
          Site sessions
          <span class="badge badge-xs ml-1">{{ sessions.length }}</span>
        </button>
        <button
          type="button"
          role="tab"
          class="tab"
          :class="tab === 'passwords' ? 'tab-active' : ''"
          @click="tab = 'passwords'"
        >
          <i class="fa-solid fa-key mr-1"></i>
          Passwords
          <span class="badge badge-xs ml-1">{{ credentials.length }}</span>
        </button>
      </div>

      <p v-if="error" class="rounded bg-error/10 px-2 py-1.5 text-xs text-error">{{ error }}</p>
      <p v-if="result" class="rounded bg-success/10 px-2 py-1.5 text-xs text-success">✅ {{ result }}</p>

      <!-- ── Site sessions ─────────────────────────────────────────────── -->
      <template v-if="tab === 'sessions'">
        <!-- What is open right now: tabs are not saved sessions until captured. -->
        <div
          v-if="running"
          class="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-2 text-xs"
        >
          <i class="fa-solid fa-window-restore text-primary"></i>
          <span v-if="previewLoading" class="opacity-60">Reading this browser…</span>
          <template v-else-if="preview">
            <span>
              Open now: <strong>{{ preview.page_count }} site(s)</strong>
              · {{ preview.cookie_count }} cookie(s)
            </span>
            <span class="w-full truncate text-[10px] opacity-50">
              {{ preview.origins.length ? preview.origins.join(' · ') : 'No site open: navigate to the sites you want to save.' }}
            </span>
          </template>
          <span v-else-if="previewError" class="opacity-60">{{ previewError }}</span>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <input
            v-model="newSessionName"
            type="text"
            class="input input-sm input-bordered flex-1 min-w-[12rem]"
            placeholder="Name it after the sites, e.g. LinkedIn + Gmail"
            @keyup.enter="saveSession"
          />
          <button
            class="btn btn-sm btn-primary gap-1"
            :disabled="!canSave || savingSession"
            :title="running ? '' : 'Start the browser first: a session captures its live cookies.'"
            @click="saveSession"
          >
            <span v-if="savingSession" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-floppy-disk"></i>
            <template v-if="preview?.page_count">
              Save these {{ preview.page_count }} site(s)
            </template>
            <template v-else>Save this browser</template>
          </button>
        </div>
        <p class="text-[10px] text-base-content/50">
          {{ running
            ? 'Captures every cookie and the local storage of the open sites.'
            : 'The browser is not running: start it, log in, then save.' }}
        </p>

        <div class="max-h-64 overflow-y-auto rounded-lg border border-base-content/10 divide-y divide-base-content/10">
          <p v-if="!sessions.length" class="p-3 text-xs opacity-60">
            <template v-if="preview?.page_count">
              Nothing saved yet — the {{ preview.page_count }} site(s) open in this
              browser are not a session until you press
              <strong>Save these {{ preview.page_count }} site(s)</strong>. Then open the
              other browser and press <strong>Use here</strong>.
            </template>
            <template v-else>
              No saved session yet. Log in once, open the sites, then save them here
              and reuse them in another browser.
            </template>
          </p>
          <div
            v-for="entry in sessions"
            :key="entry.id"
            class="flex flex-wrap items-center gap-2 px-3 py-2 text-xs"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate font-semibold">{{ entry.name }}</span>
              <span class="block truncate text-[10px] opacity-50">
                {{ entry.cookie_count }} cookie(s) · {{ entry.origin_count }} site(s)
                <template v-if="entry.origins?.length"> · {{ entry.origins.slice(0, 3).join(', ') }}</template>
                <template v-if="entry.updated_at"> · {{ shortDate(entry.updated_at) }}</template>
              </span>
            </span>
            <button
              class="btn btn-xs btn-outline gap-1"
              :disabled="!running || busy === entry.id"
              title="Apply these cookies/local storage to this browser"
              @click="applySession(entry)"
            >
              <span v-if="busy === entry.id" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-arrow-right-to-bracket"></i>
              Use here
            </button>
            <button class="btn btn-xs btn-ghost text-error" :disabled="busy === entry.id" @click="deleteSession(entry)">
              <i class="fa-regular fa-trash-can"></i>
            </button>
          </div>
        </div>
      </template>

      <!-- ── Passwords ─────────────────────────────────────────────────── -->
      <template v-else>
        <div class="max-h-64 overflow-y-auto rounded-lg border border-base-content/10 divide-y divide-base-content/10">
          <p v-if="!credentials.length" class="p-3 text-xs opacity-60">
            No credential yet. Add one below; the AI can then fill its login form
            with <code>browse_action(action="login", value="&lt;name&gt;")</code>
            without ever receiving the password.
          </p>
          <div
            v-for="entry in credentials"
            :key="entry.id"
            class="flex flex-wrap items-center gap-2 px-3 py-2 text-xs"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate font-semibold">{{ entry.name }}</span>
              <span class="block truncate text-[10px] opacity-50">
                {{ entry.username || '(no username)' }}
                <template v-if="entry.url"> · {{ entry.url }}</template>
              </span>
            </span>
            <button class="btn btn-xs btn-ghost" :disabled="busy === entry.id" @click="copyPassword(entry)">
              <i class="fa-regular fa-copy"></i>
              Copy
            </button>
            <button class="btn btn-xs btn-ghost" @click="editCredential(entry)">
              <i class="fa-solid fa-pen"></i>
            </button>
            <button class="btn btn-xs btn-ghost text-error" :disabled="busy === entry.id" @click="deleteCredential(entry)">
              <i class="fa-regular fa-trash-can"></i>
            </button>
          </div>
        </div>

        <div class="rounded-lg border border-base-content/10 p-2.5">
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label class="flex flex-col gap-0.5">
              <span class="text-[10px] uppercase tracking-wide opacity-60">Name</span>
              <input v-model="form.name" type="text" class="input input-xs input-bordered" placeholder="LinkedIn" />
            </label>
            <label class="flex flex-col gap-0.5">
              <span class="text-[10px] uppercase tracking-wide opacity-60">Site URL</span>
              <input v-model="form.url" type="text" class="input input-xs input-bordered" placeholder="https://www.linkedin.com/login" />
            </label>
            <label class="flex flex-col gap-0.5">
              <span class="text-[10px] uppercase tracking-wide opacity-60">Username / email</span>
              <input v-model="form.username" type="text" class="input input-xs input-bordered" autocomplete="off" />
            </label>
            <label class="flex flex-col gap-0.5">
              <span class="text-[10px] uppercase tracking-wide opacity-60">
                Password{{ editingId ? ' (leave empty to keep)' : '' }}
              </span>
              <input
                v-model="form.password"
                type="password"
                class="input input-xs input-bordered"
                autocomplete="new-password"
                placeholder="••••••••"
              />
            </label>
          </div>
          <div class="mt-2 flex items-center gap-2">
            <span class="text-[10px] opacity-50">Encrypted at rest with the project vault key.</span>
            <button v-if="editingId" class="btn btn-xs btn-ghost ml-auto" @click="resetForm">Cancel</button>
            <button
              class="btn btn-xs btn-primary ml-auto"
              :class="editingId ? 'ml-0' : ''"
              :disabled="!form.name.trim() || (!editingId && !form.password) || savingCredential"
              @click="saveCredential"
            >
              <span v-if="savingCredential" class="loading loading-spinner loading-xs"></span>
              {{ editingId ? 'Update' : 'Add credential' }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </modal>
</template>

<script>
/**
 * Saved site sessions and credentials for coworking browsers.
 *
 * - Sessions: capture cookies + localStorage from the running session, apply
 *   them to any other session of the project (Chromium-family images; the store
 *   is transport agnostic, but a Firefox image has no CDP runtime).
 * - Credentials: site/username/password encrypted with the project vault key;
 *   the copy button reveals the password to the human (audited) and the AI uses
 *   `browse_action(action="login", value=<name>)` without seeing it.
 */
export default {
  name: 'BrowserStoreModal',
  props: {
    sessionId: { type: String, default: '' },
    running: { type: Boolean, default: false }
  },
  emits: ['close'],
  data() {
    return {
      tab: 'sessions',
      sessions: [],
      preview: null,
      previewLoading: false,
      previewError: null,
      credentials: [],
      newSessionName: '',
      savingSession: false,
      savingCredential: false,
      busy: '',
      error: null,
      result: null,
      editingId: '',
      form: { name: '', url: '', username: '', password: '' }
    }
  },
  computed: {
    canSave() {
      return this.running && !!this.sessionId && !!this.newSessionName.trim()
    }
  },
  mounted() {
    this.loadSessions()
    this.loadCredentials()
    this.loadPreview()
  },
  methods: {
    api() {
      return this.$storex.api.browsers
    },
    clearMessages() {
      this.error = null
      this.result = null
    },
    async loadSessions() {
      try {
        const response = await this.api().savedSessions()
        this.sessions = response?.sessions || []
      } catch (error) {
        this.error = this.messageOf(error, 'Could not list the saved sessions')
      }
    },
    /**
     * Show what this browser has open right now.
     *
     * The "site sessions" list only contains sessions the user saved before;
     * the browser's open tabs are not sessions until they are captured. The
     * preview makes that visible and suggests a name, so saving is one click.
     */
    async loadPreview() {
      if (!this.running || !this.sessionId) return
      this.previewLoading = true
      this.previewError = null
      try {
        this.preview = await this.api().previewSiteSession(this.sessionId)
        if (this.preview?.suggested_name && !this.newSessionName.trim()) {
          this.newSessionName = this.preview.suggested_name
        }
      } catch (error) {
        this.previewError = this.messageOf(error, 'Could not read the open sites')
      } finally {
        this.previewLoading = false
      }
    },
    async loadCredentials() {
      try {
        const response = await this.api().credentials()
        this.credentials = response?.credentials || []
      } catch (error) {
        this.error = this.messageOf(error, 'Could not list the credentials')
      }
    },
    async saveSession() {
      if (!this.canSave || this.savingSession) return
      this.clearMessages()
      this.savingSession = true
      try {
        const saved = await this.api().saveSiteSession(this.sessionId, this.newSessionName.trim())
        this.newSessionName = ''
        this.result = `Saved "${saved.name}" (${saved.cookie_count} cookies, ${saved.origin_count} sites).`
        await this.loadSessions()
      } catch (error) {
        this.error = this.messageOf(error, 'Could not save the session')
      } finally {
        this.savingSession = false
      }
    },
    async applySession(entry) {
      if (!this.running || !this.sessionId) return
      this.clearMessages()
      this.busy = entry.id
      try {
        const receipt = await this.api().applySiteSession(entry.id, this.sessionId)
        this.result = `Applied "${entry.name}": ${receipt.cookies} cookies, ${receipt.localStorage} local-storage items. Open the site.`
      } catch (error) {
        this.error = this.messageOf(error, 'Could not apply the session')
      } finally {
        this.busy = ''
      }
    },
    async deleteSession(entry) {
      if (!window.confirm(`Delete the saved session "${entry.name}"?`)) return
      this.clearMessages()
      this.busy = entry.id
      try {
        await this.api().deleteSiteSession(entry.id)
        await this.loadSessions()
      } catch (error) {
        this.error = this.messageOf(error, 'Could not delete the session')
      } finally {
        this.busy = ''
      }
    },
    async saveCredential() {
      if (this.savingCredential) return
      this.clearMessages()
      this.savingCredential = true
      const payload = {
        name: this.form.name.trim(),
        url: this.form.url.trim(),
        username: this.form.username.trim()
      }
      try {
        if (this.editingId) {
          if (this.form.password) payload.password = this.form.password
          await this.api().updateCredential(this.editingId, payload)
          this.result = `Updated "${payload.name}".`
        } else {
          payload.password = this.form.password
          await this.api().createCredential(payload)
          this.result = `Stored "${payload.name}".`
        }
        this.resetForm()
        await this.loadCredentials()
      } catch (error) {
        this.error = this.messageOf(error, 'Could not save the credential')
      } finally {
        this.savingCredential = false
      }
    },
    editCredential(entry) {
      this.clearMessages()
      this.editingId = entry.id
      this.form = { name: entry.name, url: entry.url || '', username: entry.username || '', password: '' }
    },
    resetForm() {
      this.editingId = ''
      this.form = { name: '', url: '', username: '', password: '' }
    },
    async deleteCredential(entry) {
      if (!window.confirm(`Delete the credential "${entry.name}"?`)) return
      this.clearMessages()
      this.busy = entry.id
      try {
        await this.api().deleteCredential(entry.id)
        await this.loadCredentials()
      } catch (error) {
        this.error = this.messageOf(error, 'Could not delete the credential')
      } finally {
        this.busy = ''
      }
    },
    async copyPassword(entry) {
      this.clearMessages()
      this.busy = entry.id
      try {
        const response = await this.api().revealCredential(entry.id)
        await this.copyText(response?.password || '')
        this.result = `Password for "${entry.name}" copied to the clipboard.`
      } catch (error) {
        this.error = this.messageOf(error, 'Could not reveal the password')
      } finally {
        this.busy = ''
      }
    },
    async copyText(text) {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        return
      }
      // Fallback for non-secure contexts.
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      area.remove()
    },
    messageOf(error, fallback) {
      return error?.response?.data?.detail || error?.message || fallback
    },
    shortDate(value) {
      try {
        return new Date(value).toLocaleDateString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit'
        })
      } catch (error) {
        return String(value || '')
      }
    }
  }
}
</script>
