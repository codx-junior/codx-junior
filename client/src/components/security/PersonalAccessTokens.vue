<script setup>
</script>

<template>
  <div class="w-full max-w-3xl flex flex-col gap-4">
    <div class="flex items-start gap-3">
      <div class="w-10 h-10 rounded-lg bg-primary/10 text-primary grid place-items-center shrink-0">
        <i class="fa-solid fa-key"></i>
      </div>
      <div class="flex-1 min-w-0">
        <div class="text-xl font-bold">Personal Access Tokens</div>
        <p class="text-xs text-base-content/60 mt-1">
          Let a third-party application (a support widget, a CRM, an automation)
          talk to codx-junior with a profile and its tools. A token is scoped to a
          project and, optionally, to specific teammates.
        </p>
      </div>
      <button class="btn btn-primary btn-sm gap-2" :disabled="!activeProject" @click="openForm">
        <i class="fa-solid fa-plus"></i> Create token
      </button>
    </div>

    <div v-if="!activeProject" class="alert alert-warning py-2 text-xs">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span>Open a project first: a public token is always bound to one project.</span>
    </div>

    <div v-if="error" class="alert alert-error py-2 text-xs">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span>{{ error }}</span>
    </div>

    <!-- The clear token is shown exactly once. -->
    <div v-if="createdToken" class="rounded-xl border border-success/40 bg-success/10 p-4 flex flex-col gap-2">
      <div class="flex items-center gap-2 text-sm font-semibold text-success">
        <i class="fa-solid fa-circle-check"></i>
        Token created — copy it now
      </div>
      <p class="text-[11px] text-base-content/70">
        This is the only time the value is shown. Store it in the third-party app's
        secret store; codx-junior keeps only a hash.
      </p>
      <div class="flex gap-2">
        <code class="flex-1 min-w-0 rounded-lg bg-base-100 border border-base-content/10 px-3 py-2 text-[11px] font-mono break-all">{{ createdToken }}</code>
        <button class="btn btn-sm btn-success" @click="copyCreated">
          <i class="fa-solid fa-copy"></i> Copy
        </button>
        <button class="btn btn-sm btn-ghost btn-square" title="Dismiss" @click="createdToken = ''">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center py-8">
      <span class="loading loading-spinner loading-md"></span>
    </div>

    <div v-else-if="!tokens.length" class="rounded-xl border border-dashed border-base-content/15 p-8 text-center">
      <i class="fa-solid fa-key text-2xl text-base-content/30"></i>
      <p class="text-sm text-base-content/60 mt-2">No tokens yet.</p>
      <p class="text-[11px] text-base-content/40">
        Create one for each external application. Revoking one does not affect the others.
      </p>
    </div>

    <div v-else class="flex flex-col gap-2">
      <div
        v-for="token in tokens"
        :key="token.id"
        class="rounded-xl border p-4 flex flex-col gap-2"
        :class="token.active ? 'border-base-content/10 bg-base-100' : 'border-base-content/5 bg-base-200/40 opacity-70'"
      >
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-semibold text-sm">{{ token.name || 'API token' }}</span>
              <span class="badge badge-xs" :class="token.active ? 'badge-success' : 'badge-ghost'">
                {{ token.active ? 'Active' : (token.revoked_at ? 'Revoked' : 'Expired') }}
              </span>
              <code class="text-[10px] text-base-content/50">{{ token.prefix }}…</code>
            </div>
            <div class="text-[11px] text-base-content/50 mt-1">
              Created {{ formatDate(token.created_at) }}
              <span v-if="token.last_used_at"> · last used {{ formatDate(token.last_used_at) }}</span>
              <span v-else> · never used</span>
              <span v-if="token.expires_at"> · expires {{ formatDate(token.expires_at) }}</span>
              <span v-else> · no expiry</span>
            </div>
          </div>
          <button
            v-if="token.active"
            class="btn btn-ghost btn-xs text-error gap-1"
            :disabled="revoking === token.id"
            @click="revoke(token)"
          >
            <span v-if="revoking === token.id" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-ban"></i>
            Revoke
          </button>
        </div>

        <div class="flex flex-wrap gap-1">
          <span v-for="scope in token.scopes" :key="scope" class="badge badge-xs badge-outline font-mono">
            {{ scope }}
          </span>
          <span v-if="token.require_public_exposure_protection" class="badge badge-xs badge-info gap-1">
            <i class="fa-solid fa-shield-halved"></i> Protection required
          </span>
        </div>

        <div class="text-[11px] text-base-content/60">
          <i class="fa-solid fa-folder-tree mr-1"></i>
          {{ token.project_name || token.project_id || 'Any project' }}
          <span class="mx-1">·</span>
          <i class="fa-solid fa-user-group mr-1"></i>
          {{ token.profiles?.length ? token.profiles.join(', ') : 'Any exposed teammate' }}
        </div>
      </div>
    </div>

    <!-- Create form -->
    <div v-if="formOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="closeForm"></div>
      <div class="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-base-100 border border-base-content/10 shadow-2xl">
        <div class="flex items-center gap-2 px-5 py-4 border-b border-base-content/10">
          <i class="fa-solid fa-key text-primary"></i>
          <div class="font-semibold">New personal access token</div>
          <button class="btn btn-ghost btn-sm btn-square ml-auto" @click="closeForm">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="p-5 flex flex-col gap-4">
          <div>
            <label class="label-text text-xs font-semibold block mb-1">Name</label>
            <input
              v-model="form.name"
              type="text"
              class="input input-sm input-bordered w-full"
              maxlength="120"
              placeholder="Support widget"
            />
            <p class="text-[10px] text-base-content/50 mt-1">Shown only here, to tell tokens apart.</p>
          </div>

          <div>
            <div class="label-text text-xs font-semibold mb-1">Permissions</div>
            <div class="flex flex-col gap-1">
              <label
                v-for="scope in scopes"
                :key="scope.id"
                class="flex items-start gap-2 p-2 rounded-lg bg-base-200/50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  class="checkbox checkbox-xs checkbox-primary mt-0.5"
                  :checked="form.scopes.includes(scope.id)"
                  @change="toggleScope(scope.id)"
                />
                <span class="min-w-0">
                  <span class="text-xs font-medium block">{{ scope.label }}</span>
                  <span class="text-[10px] text-base-content/50 block">{{ scope.description }}</span>
                  <code class="text-[10px] text-base-content/40">{{ scope.id }}</code>
                </span>
              </label>
            </div>
          </div>

          <div>
            <label class="label-text text-xs font-semibold block mb-1">Project</label>
            <div class="input input-sm input-bordered w-full flex items-center gap-2 bg-base-200/50">
              <i class="fa-solid fa-folder-tree text-base-content/40"></i>
              <span class="text-xs">{{ activeProject?.project_name }}</span>
            </div>
          </div>

          <div>
            <label class="label-text text-xs font-semibold block mb-1">Teammates (optional)</label>
            <p class="text-[10px] text-base-content/50 mb-1">
              Leave all unchecked to allow any teammate exposed through the API.
            </p>
            <div v-if="profilesLoading" class="text-xs text-base-content/50">Loading teammates…</div>
            <div v-else-if="!exposedProfiles.length" class="text-xs text-base-content/50">
              No teammate is shareable via API yet. Enable “Shareable via API” in a teammate.
            </div>
            <div v-else class="flex flex-col gap-1 max-h-36 overflow-y-auto rounded-lg bg-base-200/50 p-2">
              <label
                v-for="profile in exposedProfiles"
                :key="profile.name"
                class="flex items-center gap-2 text-xs cursor-pointer"
              >
                <input
                  type="checkbox"
                  class="checkbox checkbox-xs checkbox-primary"
                  :checked="form.profiles.includes(profile.name)"
                  @change="toggleProfile(profile.name)"
                />
                <span class="flex-1 truncate">{{ profile.name }}</span>
                <i
                  class="fa-solid fa-shield-halved text-[10px]"
                  :class="profile.guardrails?.enabled ? 'text-info' : 'text-error'"
                  :title="profile.guardrails?.enabled ? 'Public Exposure Protection enabled' : 'Protection disabled'"
                ></i>
              </label>
            </div>
          </div>

          <label class="flex items-start gap-2 p-3 rounded-lg bg-info/10 border border-info/30 cursor-pointer">
            <input
              v-model="form.require_public_exposure_protection"
              type="checkbox"
              class="toggle toggle-xs toggle-info mt-0.5"
            />
            <span class="min-w-0">
              <span class="text-xs font-semibold block">Require Public Exposure Protection</span>
              <span class="text-[10px] text-base-content/60 block">
                Refuse to use a teammate whose scope, decision model and tool allowlist
                are not protecting the conversation.
              </span>
            </span>
          </label>

          <div>
            <label class="label-text text-xs font-semibold block mb-1">Expiry</label>
            <select v-model.number="form.expires_in_days" class="select select-sm select-bordered w-full">
              <option :value="7">7 days</option>
              <option :value="30">30 days</option>
              <option :value="90">90 days</option>
              <option :value="365">1 year</option>
              <option :value="0">Never</option>
            </select>
          </div>

          <div v-if="formError" class="alert alert-error py-2 text-xs">
            <i class="fa-solid fa-circle-exclamation"></i>
            <span>{{ formError }}</span>
          </div>
        </div>

        <div class="flex justify-end gap-2 px-5 py-3 border-t border-base-content/10">
          <button class="btn btn-sm btn-ghost" @click="closeForm">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="creating" @click="create">
            <span v-if="creating" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-key"></i>
            Create token
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'PersonalAccessTokens',
  data() {
    return {
      loading: true,
      error: '',
      tokens: [],
      scopes: [],
      profiles: [],
      profilesLoading: false,
      formOpen: false,
      creating: false,
      formError: '',
      createdToken: '',
      revoking: '',
      form: {
        name: '',
        scopes: ['chat:write'],
        profiles: [],
        expires_in_days: 90,
        require_public_exposure_protection: true
      }
    }
  },
  computed: {
    activeProject() {
      return this.$project || null
    },
    exposedProfiles() {
      return (this.profiles || []).filter(profile => profile?.api_settings?.active)
    }
  },
  mounted() {
    this.load()
  },
  methods: {
    async load() {
      this.loading = true
      this.error = ''
      try {
        const data = await this.$api.users.listTokens()
        this.tokens = data?.tokens || []
        this.scopes = data?.scopes || []
        if (!this.scopes.length) this.scopes = [
          { id: 'chat:write', label: 'Chat with profiles', description: 'Call the chat endpoint.' },
          { id: 'models:read', label: 'List models', description: 'Discover exposed models.' },
          { id: 'profiles:read', label: 'Read profiles', description: 'Read exposed profiles.' },
          { id: 'projects:read', label: 'Read projects', description: 'Read the bound project.' }
        ]
      } catch (ex) {
        this.error = ex?.response?.data?.detail?.message || ex?.message || 'Could not load tokens'
      } finally {
        this.loading = false
      }
    },
    async loadProfiles() {
      if (!this.$project?.$api?.profiles) return
      this.profilesLoading = true
      try {
        this.profiles = await this.$project.$api.profiles.list()
      } catch (ex) {
        this.profiles = []
      } finally {
        this.profilesLoading = false
      }
    },
    openForm() {
      if (!this.activeProject) return
      this.formError = ''
      this.form = {
        name: '',
        scopes: ['chat:write'],
        profiles: [],
        expires_in_days: 90,
        require_public_exposure_protection: true
      }
      this.formOpen = true
      this.loadProfiles()
    },
    closeForm() {
      if (this.creating) return
      this.formOpen = false
    },
    toggleScope(scope) {
      this.form.scopes = this.form.scopes.includes(scope)
        ? this.form.scopes.filter(item => item !== scope)
        : [...this.form.scopes, scope]
    },
    toggleProfile(name) {
      this.form.profiles = this.form.profiles.includes(name)
        ? this.form.profiles.filter(item => item !== name)
        : [...this.form.profiles, name]
    },
    async create() {
      if (this.creating) return
      if (!this.form.scopes.length) {
        this.formError = 'Select at least one permission.'
        return
      }
      if (this.form.scopes.includes('chat:write') && !this.activeProject?.project_id) {
        this.formError = 'Open a project before creating a chat token.'
        return
      }
      this.creating = true
      this.formError = ''
      try {
        const data = await this.$api.users.createToken({
          name: this.form.name || 'API token',
          scopes: this.form.scopes,
          project_id: this.activeProject?.project_id || null,
          profiles: this.form.profiles,
          expires_in_days: this.form.expires_in_days,
          require_public_exposure_protection: this.form.require_public_exposure_protection
        })
        this.createdToken = data?.token || ''
        this.formOpen = false
        await this.load()
        this.$toast?.success?.('Personal access token created')
      } catch (ex) {
        const detail = ex?.response?.data?.detail
        this.formError = detail?.message || detail || ex?.message || 'Could not create the token'
      } finally {
        this.creating = false
      }
    },
    async revoke(token) {
      if (this.revoking) return
      if (!window.confirm(`Revoke “${token.name || 'this token'}”? Applications using it will stop working immediately.`)) {
        return
      }
      this.revoking = token.id
      try {
        await this.$api.users.revokeToken(token.id)
        await this.load()
        this.$toast?.success?.('Token revoked')
      } catch (ex) {
        this.error = ex?.response?.data?.detail?.message || ex?.message || 'Could not revoke the token'
      } finally {
        this.revoking = ''
      }
    },
    async copyCreated() {
      try {
        await navigator.clipboard.writeText(this.createdToken)
        this.$toast?.success?.('Token copied')
      } catch (ex) {
        this.$toast?.error?.('Could not copy — select the token manually')
      }
    },
    formatDate(value) {
      if (!value) return ''
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
    }
  }
}
</script>
