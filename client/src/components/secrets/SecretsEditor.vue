<script>
import ProjectDetailt from '@/components/ProjectDetailt.vue'

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
  components: { ProjectDetailt },
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
      // One view at a time: the list gets the whole width, the form too.
      view: 'list',
      // Row whose details are open (view === 'detail').
      selected: null,
      vault: { available: true, detail: '' },
      rows: [],
      defaultAllowTools: [],
      resolveHelp: '',
      // 'global' (one machine-wide store) or 'projects' (one vault per project).
      scopeMode: this.defaultScope === 'global' ? 'global' : 'projects',
      // Projects that receive a project-scoped secret, chosen with the project
      // picker so nobody has to type a path.
      selectedProjects: [],
      // Tools the secret may be used by, picked from the runtime catalogue.
      allowTools: [],
      toolCatalogue: [],
      deniedTools: [],
      toolFilter: '',
      toolPick: '',
      customTool: '',
      form: {
        name: '',
        value: '',
        description: '',
        // Project scope only: let the projects inside this one read the secret.
        shareWithChildren: false,
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
    scopeValid() {
      return this.scopeMode === 'global' || this.selectedProjects.length > 0
    },
    canSave() {
      return Boolean(this.form.name && this.scopeValid) && !this.saving
    },
    // Tools still grantable: the catalogue minus the ones already added.
    availableTools() {
      const filter = (this.toolFilter || '').toLowerCase()
      return (this.toolCatalogue || []).filter((tool) => {
        if (this.allowTools.includes(tool.name)) return false
        if (!filter) return true
        return (
          tool.name.toLowerCase().includes(filter) ||
          (tool.description || '').toLowerCase().includes(filter) ||
          (tool.tags || []).some((tag) => String(tag).toLowerCase().includes(filter))
        )
      })
    },
    // Declared tools that the CISO policy would refuse anyway.
    hasDeniedTool() {
      return (this.allowTools || []).some((name) => this.isDeniedTool(name))
    },
    isGlobalView() {
      return this.scope === 'global'
    },
  },
  mounted() {
    this.scopeMode = this.defaultScope === 'global' || this.isGlobalView ? 'global' : 'projects'
    if (this.scopeMode === 'projects') {
      // Seed with the project already open so the common case needs no click.
      const active = this.$projects?.activeProject || this.$project
      if (active?.project_id) {
        this.selectedProjects = [this.projectOption(active)]
      }
    }
    this.refresh()
    this.loadToolCatalogue()
  },
  methods: {
    async refresh() {
      this.loading = true
      this.error = ''
      try {
        const query = new URLSearchParams({ scope: this.scope })
        if (this.selectedProjects.length) {
          query.set('projects', this.selectedProjects.map((p) => p.project_id).join(','))
        }
        const data = await this.$storex.api.get(`/api/secrets?${query.toString()}`)
        this.rows = (data && data.secrets) || []
        this.vault = (data && data.vault) || { available: true, detail: '' }
        this.defaultAllowTools = (data && data.default_allow_tools) || []
        this.resolveHelp = (data && data.resolve_help) || ''
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
    projectOption(project) {
      return {
        project_id: project.project_id,
        project_name: project.project_name || project.abs_project_path,
        abs_project_path: project.abs_project_path || '',
      }
    },
    addProject(project) {
      if (!project || !project.project_id) return
      if (this.selectedProjects.some((item) => item.project_id === project.project_id)) return
      this.selectedProjects = [...this.selectedProjects, this.projectOption(project)]
      this.refresh()
    },
    removeProject(project) {
      this.selectedProjects = this.selectedProjects.filter(
        (item) => item.project_id !== project.project_id
      )
      this.refresh()
    },
    async loadToolCatalogue() {
      try {
        const data = await this.$storex.api.get('/api/secrets/tools')
        this.toolCatalogue = (data && data.tools) || []
        this.deniedTools = (data && data.denied_tools) || []
        // Tools start empty on purpose: the user picks exactly the ones the new
        // secret needs instead of inheriting a preselected list.
      } catch (ex) {
        this.error = this.describe(ex)
      }
    },
    pickTool() {
      if (!this.toolPick) return
      this.addTool(this.toolPick)
      this.toolPick = ''
    },
    addTool(name) {
      const tool = String(name || '').trim()
      if (!tool || this.allowTools.includes(tool)) return
      this.allowTools = [...this.allowTools, tool]
    },
    removeTool(name) {
      this.allowTools = this.allowTools.filter((item) => item !== name)
    },
    addCustomTool() {
      const pattern = (this.customTool || '').trim()
      if (!pattern) return
      this.addTool(pattern)
      this.customTool = ''
    },
    isDeniedTool(name) {
      return (this.deniedTools || []).some((pattern) => {
        if (pattern.endsWith('*')) return name.startsWith(pattern.slice(0, -1))
        return name === pattern
      })
    },
    async save() {
      if (!this.canSave) return
      this.saving = true
      this.error = ''
      this.flash = ''
      try {
        const body = {
          name: this.form.name.trim(),
          value: this.form.value,
          scope: this.scopeMode === 'global' ? 'global' : 'project',
          description: this.form.description,
          allow_tools: this.allowTools,
          // Ignored by the backend for the global scope: a global secret is
          // already readable from every project, so it has nothing to share down.
          share_with_children: this.scopeMode === 'global' ? false : this.form.shareWithChildren,
        }
        if (this.scopeMode === 'projects') {
          body.projects = this.selectedProjects.map((project) => project.project_id)
        }
        await this.$storex.api.post('/api/secrets', body)
        const where = this.scopeMode === 'global'
          ? 'the global scope'
          : this.selectedProjects.map((project) => project.project_name).join(', ')
        this.flash = `Saved '${body.name}' for ${where}.`
        // Clean the form so the next secret is typed from scratch: no value left
        // in the DOM, no stale name and no tools inherited from the previous one.
        this.form.name = ''
        this.form.value = ''
        this.form.description = ''
        this.form.shareWithChildren = false
        this.allowTools = []
        this.toolPick = ''
        this.customTool = ''
        await this.refresh()
        // Back to the list so the result of the save is visible in the table.
        this.view = 'list'
      } catch (ex) {
        this.error = this.describe(ex)
      } finally {
        this.saving = false
      }
    },
    async remove(row) {
      const where = row.project_name || row.scope
      if (!confirm(`Delete secret '${row.name}' from ${where}?`)) return
      this.error = ''
      this.flash = ''
      try {
        const query = new URLSearchParams({ scope: row.scope })
        if (row.project_id) {
          query.set('project_id', row.project_id)
        }
        await this.$storex.api.delete(
          `/api/secrets/${encodeURIComponent(row.name)}?${query.toString()}`
        )
        this.flash = `Deleted '${row.name}'.`
        await this.refresh()
      } catch (ex) {
        this.error = this.describe(ex)
      }
    },
    openForm() {
      this.error = ''
      this.flash = ''
      this.view = 'form'
    },
    backToList() {
      this.error = ''
      this.selected = null
      this.view = 'list'
    },
    // Clicking a row opens its details instead of a modal stacked on the table.
    openDetail(row) {
      this.error = ''
      this.flash = ''
      this.selected = row
      this.view = 'detail'
    },
    editSelected() {
      if (!this.selected) return
      this.reuse(this.selected)
    },
    reuse(row) {
      this.view = 'form'
      this.form.name = row.name
      this.form.description = row.description || ''
      this.form.value = ''
      this.allowTools = [...(row.allow_tools || [])]
      this.form.shareWithChildren = Boolean(row.share_with_children)
      this.scopeMode = row.scope === 'global' ? 'global' : 'projects'
      if (row.project_id) {
        this.selectedProjects = [
          this.projectOption({
            project_id: row.project_id,
            project_name: row.project_name,
            abs_project_path: row.abs_project_path || '',
          }),
        ]
      }
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
    <div class="flex items-start justify-between gap-4">
      <div>
        <div class="text-xl font-bold">Secrets</div>
        <p class="text-xs text-base-content/60 mt-1">
          Credentials live encrypted on the server. Only the name is visible here and to the
          model; the value is written once and never read back. An agent uses the name as a
          handle, so the value never enters the conversation.
        </p>
      </div>
      <button
        v-if="view === 'list'"
        class="btn btn-sm btn-primary shrink-0"
        @click="openForm"
      >
        <i class="fa-solid fa-plus"></i>
        Add secret
      </button>
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
    <div v-if="view === 'list'" class="overflow-x-auto">
      <table class="table table-xs">
        <thead>
          <tr>
            <th>Name</th>
            <th>Scope</th>
            <th>Applies to</th>
            <th>Status</th>
            <th>Allowed tools</th>
            <th>Description</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="7" class="text-center text-base-content/60">
              <span class="loading loading-spinner loading-sm"></span>
            </td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="7" class="text-center text-base-content/60 py-6">
              No secret yet. Use “Add secret”.
            </td>
          </tr>
          <tr
            v-for="row in rows"
            :key="`${row.scope}:${row.name}`"
            class="cursor-pointer hover:bg-base-200/60"
            @click="openDetail(row)"
          >
            <td class="font-mono text-xs">{{ row.name }}</td>
            <td>
              <span class="badge badge-ghost badge-sm">{{ row.scope }}</span>
              <span
                v-if="row.share_with_children"
                class="badge badge-info badge-xs ml-1"
                title="Child projects can use this secret"
              >+children</span>
            </td>
            <td class="text-xs">
              <template v-if="row.inherited_from">
                {{ row.project_name || 'this project' }}
                <span class="text-base-content/60">— inherited from {{ row.inherited_from }}</span>
              </template>
              <template v-else-if="row.scope === 'global'">all projects</template>
              <template v-else>
                {{ row.project_name || 'this project' }}
                <span v-if="row.share_with_children" class="text-base-content/60">+ child projects</span>
              </template>
            </td>
            <td>
              <span class="badge badge-sm" :class="statusClass(row.status)">{{ row.status }}</span>
            </td>
            <td class="text-xs whitespace-nowrap">
              {{ (row.allow_tools || []).join(', ') || '— (no tool)' }}
            </td>
            <td class="text-xs">
              <div class="max-w-[18rem] truncate" :title="row.description || ''">
                {{ row.description || '—' }}
              </div>
            </td>
            <td class="text-right whitespace-nowrap">
              <span
                v-if="row.inherited_from"
                class="text-base-content/40"
                title="Declared in the parent project"
              >
                <i class="fa-solid fa-lock"></i>
              </span>
              <template v-else>
                <button class="btn btn-xs btn-ghost" title="Edit" @click.stop="reuse(row)">
                  <i class="fa-regular fa-pen-to-square"></i>
                </button>
                <button class="btn btn-xs btn-ghost text-error" title="Delete" @click.stop="remove(row)">
                  <i class="fa-regular fa-trash-can"></i>
                </button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Details of one secret: its own view, reached by clicking a table row -->
    <div
      v-if="view === 'detail' && selected"
      class="border border-base-300 rounded-lg p-4 flex flex-col gap-4"
    >
      <div class="flex items-center justify-between gap-3">
        <div class="font-mono font-semibold">{{ selected.name }}</div>
        <button class="btn btn-xs btn-ghost" @click="backToList">
          <i class="fa-solid fa-arrow-left"></i>
          Back to list
        </button>
      </div>

      <div class="grid grid-cols-1 @md:grid-cols-2 gap-4 text-sm">
        <div>
          <div class="text-xs text-base-content/60 mb-1">Scope</div>
          <div>
            <span class="badge badge-ghost badge-sm">{{ selected.scope }}</span>
            <span
              v-if="selected.share_with_children"
              class="badge badge-info badge-xs ml-1"
            >+children</span>
          </div>
        </div>
        <div>
          <div class="text-xs text-base-content/60 mb-1">Applies to</div>
          <div>
            <template v-if="selected.inherited_from">
              {{ selected.project_name || 'this project' }}
              <span class="text-base-content/60">— inherited from {{ selected.inherited_from }}</span>
            </template>
            <template v-else-if="selected.scope === 'global'">all projects</template>
            <template v-else>
              {{ selected.project_name || 'this project' }}
              <span v-if="selected.share_with_children" class="text-base-content/60">+ child projects</span>
            </template>
          </div>
        </div>
        <div>
          <div class="text-xs text-base-content/60 mb-1">Status</div>
          <div>
            <span class="badge badge-sm" :class="statusClass(selected.status)">{{ selected.status }}</span>
          </div>
        </div>
        <div>
          <div class="text-xs text-base-content/60 mb-1">Allowed tools</div>
          <div class="text-xs font-mono break-words">
            {{ (selected.allow_tools || []).join(', ') || '— (no tool: the value is unreadable)' }}
          </div>
        </div>
        <div class="@md:col-span-2">
          <div class="text-xs text-base-content/60 mb-1">Description</div>
          <div class="whitespace-pre-wrap break-words">{{ selected.description || '—' }}</div>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <template v-if="selected.inherited_from">
          <span class="text-xs text-base-content/60">
            <i class="fa-solid fa-lock"></i>
            Declared in the parent project: edit it there.
          </span>
        </template>
        <template v-else>
          <button class="btn btn-sm btn-primary" @click="editSelected">
            <i class="fa-regular fa-pen-to-square"></i>
            Set a new value
          </button>
          <button class="btn btn-sm btn-ghost text-error" @click="remove(selected)">
            <i class="fa-regular fa-trash-can"></i>
            Delete
          </button>
        </template>
      </div>
    </div>

    <!-- Add / update: its own view, so the table keeps the full width -->
    <div
      v-if="view === 'form'"
      class="border border-base-300 rounded-lg p-4 flex flex-col gap-4"
    >
      <div class="flex items-center justify-between gap-3">
        <div class="font-semibold">Add or update a secret</div>
        <button class="btn btn-xs btn-ghost" @click="backToList">
          <i class="fa-solid fa-arrow-left"></i>
          Back to list
        </button>
      </div>

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
          <select
            v-model="scopeMode"
            class="select select-sm select-bordered w-full"
            @change="refresh"
          >
            <option value="global">global — every project (admins)</option>
            <option value="projects">projects — pick one or more projects</option>
          </select>
          <div class="text-xs text-base-content/60 mt-1">
            A project secret wins over a global one with the same name.
          </div>

          <div v-if="scopeMode === 'projects'" class="mt-2 flex flex-col gap-2">
            <div class="flex flex-wrap gap-1">
              <span
                v-for="project in selectedProjects"
                :key="project.project_id"
                class="badge badge-primary badge-sm gap-1"
              >
                {{ project.project_name }}
                <button
                  class="text-primary-content/70 hover:text-primary-content"
                  title="Remove this project"
                  @click="removeProject(project)"
                >
                  <i class="fa-solid fa-xmark"></i>
                </button>
              </span>
              <span v-if="!selectedProjects.length" class="text-xs text-warning">
                Add at least one project.
              </span>
            </div>
            <ProjectDetailt placeholder="Add a project…" @select="addProject" />
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

      <div class="flex flex-col gap-4">
        <div>
          <label class="label-text block mb-1">Allowed tools</label>
          <div class="flex flex-wrap gap-1 mb-2 min-h-6">
            <span
              v-for="tool in allowTools"
              :key="tool"
              class="badge badge-sm gap-1"
              :class="isDeniedTool(tool) ? 'badge-error' : 'badge-secondary'"
              :title="isDeniedTool(tool)
                ? 'The CISO policy refuses this tool: it would never receive a secret'
                : tool"
            >
              {{ tool }}
              <button class="hover:opacity-70" title="Remove" @click="removeTool(tool)">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </span>
            <span v-if="!allowTools.length" class="text-xs text-base-content/60">
              No tool may use it yet — the secret resolves for nobody.
            </span>
          </div>
          <div class="flex flex-col @md:flex-row gap-2">
            <input
              v-model="toolFilter"
              type="text"
              class="input input-sm input-bordered w-full @md:w-44"
              placeholder="Filter tools…"
            />
            <select
              v-model="toolPick"
              class="select select-sm select-bordered w-full"
              @change="pickTool"
            >
              <option value="">Add a tool…</option>
              <option v-for="tool in availableTools" :key="tool.name" :value="tool.name">
                {{ tool.name }}
              </option>
            </select>
          </div>
          <div class="flex gap-2 mt-2">
            <input
              v-model="customTool"
              type="text"
              class="input input-sm input-bordered w-full font-mono text-xs"
              placeholder="custom pattern, e.g. docker_*"
              @keyup.enter="addCustomTool"
            />
            <button class="btn btn-sm btn-ghost" @click="addCustomTool">
              <i class="fa-solid fa-plus"></i>
            </button>
          </div>
          <div class="text-xs text-base-content/60 mt-1">
            Picked from the runtime tool catalogue; a trailing * is a prefix match.
            Empty means no tool may use it.
          </div>
          <div v-if="hasDeniedTool" class="text-xs text-warning mt-1">
            A red tool is refused by policy even when granted here.
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

      <label
        v-if="scopeMode === 'projects'"
        class="flex items-start gap-2 cursor-pointer"
      >
        <input
          v-model="form.shareWithChildren"
          type="checkbox"
          class="checkbox checkbox-sm mt-0.5"
        />
        <span class="text-sm">
          Let child projects use it
          <span class="block text-xs text-base-content/60">
            Projects whose folder sits inside the selected ones can read this secret.
            Off by default: a credential never travels down the folder tree unless you
            allow it, and a child's own declaration of the same name still wins.
          </span>
        </span>
      </label>

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
