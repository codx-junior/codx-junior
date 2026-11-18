<script>
/**
 * Secrets editor: the out-of-band way to add a credential.
 *
 * The value is typed here, in the browser, and travels straight to the encrypted
 * vault (`/api/secrets`). No endpoint returns it: this table only ever shows the
 * name, the scope, the allowed tools and whether the secret resolves right now.
 * That is what makes it safe for an LLM to know the name: the model can only
 * reference a handle, never the value.
 *
 * The component talks to `/api/secrets` directly so it can be dropped into the
 * global settings and into the project settings without touching their models.
 */
export default {
  name: 'SecretsEditor',
  props: {
    // Scope shown in the table: 'all' (global + project) or a single scope.
    scope: { type: String, default: 'all' },
    // Scope the form writes to by default.
    defaultScope: { type: String, default: 'project' },
  },
  data() {
    return {
      loading: false,
      saving: false,
      error: '',
      flash: '',
      vault: { available: true, detail: '' },
      rows: [],
      defaultAllowTools: [],
      resolveHelp: '',
      form: {
        name: '',
        value: '',
        scope: this.defaultScope || 'project',
        description: '',
        allow_tools: '',
      },
    }
  },
  computed: {
    handleExample() {
      const name = this.form.name || 'MY_SECRET'
      const source = this.form.value ? 'secret' : 'env'
      return `{{${source}:${name}}}`
    },
    jsonExample() {
      const name = this.form.name || 'MY_SECRET'
      const source = this.form.value ? 'secret' : 'env'
      return `{"command": "docker login --password-stdin reg.local", "stdin": "{{${source}:${name}}}"}`
    },
    canSave() {
      return Boolean(this.form.name && this.form.scope) && !this.saving
    },
    isGlobalView() {
      return this.scope === 'global'
    },
  },
  mounted() {
    this.form.scope = this.defaultScope || (this.isGlobalView ? 'global' : 'project')
    this.form.allow_tools = (this.defaultAllowTools || []).join(', ')
    this.refresh()
  },
  methods: {
    async refresh() {
      this.loading = true
      this.error = ''
      try {
        const data = await this.$storex.api.get(
          `/api/secrets?scope=${encodeURIComponent(this.scope)}`
        )
        this.rows = (data && data.secrets) || []
        this.vault = (data && data.vault) || { available: true, detail: '' }
        this.defaultAllowTools = (data && data.default_allow_tools) || []
        this.resolveHelp = (data && data.resolve_help) || ''
        if (!this.form.allow_tools) {
          this.form.allow_tools = this.defaultAllowTools.join(', ')
        }
      } catch (ex) {
        this.error = this.describe(ex)
      } finally {
        this.loading = false
      }
    },
    describe(ex) {
      const detail = ex && (ex.response?.data?.detail || ex.message)
      return detail ? String(detail) : 'Unexpected error'
    },
    parseTools() {
      return (this.form.allow_tools || '')
        .split(',')
        .map((item) => item.trim())
        .filter((item) => item)
    },
    async save() {
      if (!this.canSave) return
      this.saving = true
      this.error = ''
      this.flash = ''
      try {
        await this.$storex.api.post('/api/secrets', {
          name: this.form.name.trim(),
          value: this.form.value,
          scope: this.form.scope,
          description: this.form.description,
          allow_tools: this.parseTools(),
        })
        this.flash = `Saved '${this.form.name.trim()}' in the ${this.form.scope} scope.`
        // The value must not stay in the DOM once it is stored.
        this.form.value = ''
        await this.refresh()
      } catch (ex) {
        this.error = this.describe(ex)
      } finally {
        this.saving = false
      }
    },
    async remove(row) {
      if (!confirm(`Delete secret '${row.name}' from the ${row.scope} scope?`)) return
      this.error = ''
      this.flash = ''
      try {
        await this.$storex.api.delete(
          `/api/secrets/${encodeURIComponent(row.name)}?scope=${encodeURIComponent(row.scope)}`
        )
        this.flash = `Deleted '${row.name}'.`
        await this.refresh()
      } catch (ex) {
        this.error = this.describe(ex)
      }
    },
    reuse(row) {
      this.form.name = row.name
      this.form.description = row.description || ''
      this.form.allow_tools = (row.allow_tools || []).join(', ') || this.defaultAllowTools.join(', ')
      this.form.value = ''
    },
    statusClass(status) {
      if (status === 'ready') return 'badge-success'
      if (status === 'pending') return 'badge-warning'
      return 'badge-error'
    },
  },
}
</script>

<template>
  <div class="w-full flex flex-col gap-6">
    <div>
      <div class="text-xl font-bold">Secrets</div>
      <p class="text-xs text-base-content/60 mt-1">
        Credentials live encrypted on the server. Only the name is visible here and to the
        model; the value is written once and never read back. An agent uses the name as a
        handle, so the value never enters the conversation.
      </p>
    </div>

    <div v-if="!vault.available" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span>The encrypted vault cannot be opened: {{ vault.detail }}</span>
    </div>

    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span>{{ error }}</span>
    </div>
    <div v-if="flash" class="alert alert-success text-sm">
      <i class="fa-solid fa-check"></i>
      <span>{{ flash }}</span>
    </div>

    <!-- Existing secrets: names, never values -->
    <div class="overflow-x-auto">
      <table class="table table-sm">
        <thead>
          <tr>
            <th>Name</th>
            <th>Scope</th>
            <th>Status</th>
            <th>Allowed tools</th>
            <th>Description</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="6" class="text-center text-base-content/60">
              <span class="loading loading-spinner loading-sm"></span>
            </td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="6" class="text-center text-base-content/60 py-6">
              No secret yet. Add the first one below.
            </td>
          </tr>
          <tr v-for="row in rows" :key="`${row.scope}:${row.name}`">
            <td class="font-mono text-xs">{{ row.name }}</td>
            <td>
              <span class="badge badge-ghost badge-sm">{{ row.scope }}</span>
            </td>
            <td>
              <span class="badge badge-sm" :class="statusClass(row.status)">{{ row.status }}</span>
            </td>
            <td class="text-xs">{{ (row.allow_tools || []).join(', ') || '— (no tool)' }}</td>
            <td class="text-xs">{{ row.description || '—' }}</td>
            <td class="text-right whitespace-nowrap">
              <button class="btn btn-xs btn-ghost" title="Edit" @click="reuse(row)">
                <i class="fa-regular fa-pen-to-square"></i>
              </button>
              <button class="btn btn-xs btn-ghost text-error" title="Delete" @click="remove(row)">
                <i class="fa-regular fa-trash-can"></i>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Add / update -->
    <div class="border border-base-300 rounded-lg p-4 flex flex-col gap-4">
      <div class="font-semibold">Add or update a secret</div>

      <div class="grid grid-cols-1 @md:grid-cols-2 gap-4">
        <div>
          <label class="label-text block mb-1">Name</label>
          <input
            v-model="form.name"
            type="text"
            class="input input-sm input-bordered w-full font-mono"
            placeholder="REGISTRY_PASSWORD"
          />
          <div class="text-xs text-base-content/60 mt-1">
            Letters, digits, '_', '-', '.', ':' or '@'. This is the only part the model sees.
          </div>
        </div>
        <div>
          <label class="label-text block mb-1">Scope</label>
          <select v-model="form.scope" class="select select-sm select-bordered w-full">
            <option value="project">project — this project only</option>
            <option value="global">global — every project (admins)</option>
          </select>
          <div class="text-xs text-base-content/60 mt-1">
            A project secret wins over a global one with the same name.
          </div>
        </div>
      </div>

      <div>
        <label class="label-text block mb-1">Value</label>
        <input
          v-model="form.value"
          type="password"
          autocomplete="new-password"
          class="input input-sm input-bordered w-full font-mono"
          placeholder="leave empty to declare the name only"
        />
        <div class="text-xs text-base-content/60 mt-1">
          Never paste a value in a chat: it would be stored in the conversation, the index and
          the provider logs. It is written here and never returned.
        </div>
      </div>

      <div class="grid grid-cols-1 @md:grid-cols-2 gap-4">
        <div>
          <label class="label-text block mb-1">Allowed tools</label>
          <input
            v-model="form.allow_tools"
            type="text"
            class="input input-sm input-bordered w-full font-mono text-xs"
            placeholder="execute_command, docker_exec"
          />
          <div class="text-xs text-base-content/60 mt-1">
            Comma separated. A trailing * is a prefix match. Empty means no tool may use it.
          </div>
        </div>
        <div>
          <label class="label-text block mb-1">Description</label>
          <input
            v-model="form.description"
            type="text"
            class="input input-sm input-bordered w-full"
            placeholder="What it unlocks"
          />
        </div>
      </div>

      <div class="flex items-center gap-3">
        <button class="btn btn-sm btn-primary" :disabled="!canSave" @click="save">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-floppy-disk"></i>
          Save secret
        </button>
        <span v-if="flash" class="text-xs text-success">{{ flash }}</span>
      </div>

      <div class="text-xs text-base-content/70 bg-base-200 rounded p-3 flex flex-col gap-2">
        <div>
          How an agent uses it — the handle must be the WHOLE value of
          <code>stdin</code> or of an <code>env</code> entry:
        </div>
        <code class="break-all">{{ handleExample }}</code>
        <code class="break-all">{{ jsonExample }}</code>
        <div v-if="resolveHelp" class="text-base-content/60">{{ resolveHelp }}</div>
      </div>
    </div>
  </div>
</template>
