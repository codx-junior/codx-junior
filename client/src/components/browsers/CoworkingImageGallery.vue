<script>
/**
 * Custom coworking images — the gallery of `.codx/coworking/images`.
 *
 * Every image is a folder `<image_id>.<slug>` with a manifest (`image.json`), a
 * `Dockerfile`, a generated `README.md`, `support/` (files copied into the
 * image) and `media/` (icon and preview). The folder is the contract with the
 * `codx-junior-max` assistant: the gallery creates the scaffold and shows the
 * prompt that hands the folder over, so the recipe itself is written by the
 * assistant inside the project.
 *
 * Three sources are supported and all of them end up as a folder:
 *   dockerfile — a base desktop plus the packages the team needs;
 *   dockerhub  — an existing image imported by reference (kept as `FROM`);
 *   git        — a repository that ships its own Dockerfile (`build.sh`).
 *
 * Visibility decides who sees an image: `user` (creator only), `project`
 * (this project, the default), `custom` (an explicit list of projects) and
 * `global` (every project of the instance).
 *
 * Routes: `GET/POST /api/computers/library/custom`,
 * `POST /library/custom/import`, `GET/PUT/DELETE /library/custom/{id}`.
 */
const VISIBILITY_OPTIONS = [
  { value: 'user', label: 'Only me', hint: 'Only its creator sees it' },
  { value: 'project', label: 'Project', hint: 'Every user of this project' },
  { value: 'custom', label: 'Projects', hint: 'Only the projects listed below' },
  { value: 'global', label: 'Global', hint: 'Every project of the instance' }
]

const SOURCE_LABELS = {
  dockerfile: 'Dockerfile',
  dockerhub: 'Docker Hub',
  git: 'Git repo'
}

const VISIBILITY_CLASSES = {
  user: 'badge-ghost',
  project: 'badge-info',
  custom: 'badge-secondary',
  global: 'badge-success'
}

export default {
  name: 'CoworkingImageGallery',
  emits: ['select', 'close'],
  data() {
    return {
      images: [],
      loading: false,
      error: null,
      query: '',
      // '' | 'create' | 'import': which inline form is open.
      mode: '',
      saving: false,
      formError: null,
      // Image whose assistant prompt is expanded.
      promptImage: null,
      form: {
        name: '',
        description: '',
        base_image: '',
        tags: '',
        visibility: 'project',
        projects: ''
      },
      importForm: {
        source: 'dockerhub',
        reference: '',
        git_url: '',
        git_ref: 'main',
        visibility: 'project',
        projects: ''
      }
    }
  },
  computed: {
    visibilityOptions() {
      return VISIBILITY_OPTIONS
    },
    filtered() {
      const term = this.query.trim().toLowerCase()
      if (!term) return this.images
      return this.images.filter(image => [
        image.name,
        image.description,
        image.slug,
        image.docker_ref,
        image.git_url,
        image.created_by,
        ...(image.tags || [])
      ].some(value => String(value || '').toLowerCase().includes(term)))
    }
  },
  mounted() {
    this.load()
  },
  methods: {
    // ── data ───────────────────────────────────────────────────────────────
    async load() {
      this.loading = true
      this.error = null
      try {
        const response = await this.$storex.api.computers.coworkingImages()
        this.images = response?.images || []
      } catch (error) {
        this.error = error?.response?.data?.detail || error.message || 'Could not load the custom images'
        this.images = []
      } finally {
        this.loading = false
      }
    },
    dockerRef(image) {
      return `codx-coworking/${image.slug}:latest`
    },
    listOf(value) {
      return String(value || '')
        .split(',')
        .map(entry => entry.trim())
        .filter(Boolean)
    },
    notify(text, type = 'success') {
      this.$storex.ui.addNotification({ text, type })
    },
    reportError(error, fallback) {
      this.formError = error?.response?.data?.detail || error.message || fallback
      this.notify(this.formError, 'error')
    },

    // ── forms ──────────────────────────────────────────────────────────────
    startCreate() {
      this.mode = this.mode === 'create' ? '' : 'create'
      this.formError = null
    },
    startImport() {
      this.mode = this.mode === 'import' ? '' : 'import'
      this.formError = null
    },
    cancelForm() {
      this.mode = ''
      this.formError = null
    },
    async submitCreate() {
      if (!this.form.name.trim()) {
        this.formError = 'The image needs a name'
        return
      }
      const projects = this.listOf(this.form.projects)
      if (this.form.visibility === 'custom' && !projects.length) {
        this.formError = 'Choose at least one project for a custom visibility'
        return
      }
      this.saving = true
      this.formError = null
      try {
        const created = await this.$storex.api.computers.createCoworkingImage({
          name: this.form.name.trim(),
          description: this.form.description.trim() || null,
          base_image: this.form.base_image.trim() || null,
          tags: this.listOf(this.form.tags),
          visibility: this.form.visibility,
          projects
        })
        this.form = { name: '', description: '', base_image: '', tags: '', visibility: 'project', projects: '' }
        this.mode = ''
        await this.load()
        this.notify(`Created the image folder "${created?.folder || created?.name}"`)
      } catch (error) {
        this.reportError(error, 'Could not create the image')
      } finally {
        this.saving = false
      }
    },
    async submitImport() {
      const projects = this.listOf(this.importForm.projects)
      const payload = {
        source: this.importForm.source,
        visibility: this.importForm.visibility,
        projects
      }
      if (this.importForm.source === 'dockerhub') {
        payload.docker_ref = this.importForm.reference.trim()
        payload.name = payload.docker_ref.split(':')[0].split('/').pop()
      } else {
        payload.git_url = this.importForm.git_url.trim()
        payload.git_ref = this.importForm.git_ref.trim() || 'main'
        payload.name = payload.git_url.replace(/\/+$/, '').split('/').pop().replace(/\.git$/, '')
      }
      if (!payload.name) {
        this.formError = 'The reference is empty'
        return
      }
      this.saving = true
      this.formError = null
      try {
        const created = await this.$storex.api.computers.importCoworkingImage(payload)
        this.importForm = {
          source: 'dockerhub',
          reference: '',
          git_url: '',
          git_ref: 'main',
          visibility: 'project',
          projects: ''
        }
        this.mode = ''
        await this.load()
        this.notify(`Imported "${created?.name}" from ${SOURCE_LABELS[payload.source]}`)
      } catch (error) {
        this.reportError(error, 'Could not import the image')
      } finally {
        this.saving = false
      }
    },

    // ── actions on an image ────────────────────────────────────────────────
    async changeVisibility(image, visibility) {
      if (visibility === image.visibility) return
      let projects = image.projects || []
      if (visibility === 'custom' && !projects.length) {
        const answer = window.prompt(
          `Projects that may see "${image.name}" (comma separated project ids):`,
          ''
        )
        if (answer === null) return
        projects = this.listOf(answer)
        if (!projects.length) {
          this.notify('A custom visibility needs at least one project', 'error')
          return
        }
      }
      try {
        const updated = await this.$storex.api.computers.updateCoworkingImage(image.id, {
          visibility,
          projects: visibility === 'custom' ? projects : []
        })
        const index = this.images.findIndex(entry => entry.id === image.id)
        if (index >= 0) this.images.splice(index, 1, updated)
        this.notify(`Visibility of "${image.name}" is now ${visibility}`)
      } catch (error) {
        this.reportError(error, 'Could not change the visibility')
      }
    },
    async remove(image) {
      if (!window.confirm(`Delete the definition folder of "${image.name}"? The Dockerfile and support files are removed.`)) return
      try {
        await this.$storex.api.computers.deleteCoworkingImage(image.id)
        this.images = this.images.filter(entry => entry.id !== image.id)
        if (this.promptImage?.id === image.id) this.promptImage = null
        this.notify(`Deleted "${image.name}"`)
      } catch (error) {
        this.reportError(error, 'Could not delete the image')
      }
    },
    togglePrompt(image) {
      this.promptImage = this.promptImage?.id === image.id ? null : image
    },
    async copyPrompt(image) {
      const prompt = image.assistant_prompt || ''
      try {
        await navigator.clipboard.writeText(prompt)
        this.notify(`Prompt copied: paste it in a chat with @${image.assistant_profile}`)
      } catch (error) {
        this.notify('Could not copy the prompt; select the text manually', 'error')
      }
    },
    choose(image) {
      // The image is built locally from its folder; until it exists the session
      // falls back to screenshot viewing (same as a custom reference).
      const reference = this.dockerRef(image)
      this.$emit('select', {
        id: `coworking:${image.id}`,
        provider: 'custom',
        coworking_id: image.id,
        coworking_image: image,
        repository: reference.split(':')[0],
        image: reference,
        tag: 'latest',
        name: image.name,
        description: image.description || 'Custom coworking image',
        category: 'Coworking',
        live_view: 'screenshot',
        live_port: image.live_port || null,
        live_scheme: 'http',
        strip_prefix: false,
        control: 'docker',
        viewer_user: image.desktop_user || null,
        tags: image.tags || []
      })
      this.$emit('close')
    },

    // ── formatting ─────────────────────────────────────────────────────────
    visibilityLabel(value) {
      return VISIBILITY_OPTIONS.find(option => option.value === value)?.label || value
    },
    visibilityClass(value) {
      return VISIBILITY_CLASSES[value] || 'badge-ghost'
    },
    sourceLabel(value) {
      return SOURCE_LABELS[value] || value
    },
    statusClass(value) {
      if (value === 'ready') return 'badge-success'
      if (value === 'error') return 'badge-error'
      return 'badge-warning'
    }
  }
}
</script>

<template>
  <div class="flex flex-col gap-2 min-h-0">
    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="btn btn-xs"
        :class="mode === 'create' ? 'btn-primary' : 'btn-outline'"
        @click="startCreate"
      >
        <i class="fa-solid fa-plus"></i> New image
      </button>
      <button
        type="button"
        class="btn btn-xs"
        :class="mode === 'import' ? 'btn-primary' : 'btn-outline'"
        @click="startImport"
      >
        <i class="fa-solid fa-file-import"></i> Import
      </button>
      <label class="input input-xs input-bordered flex items-center gap-2 flex-1 min-w-[10rem]">
        <i class="fa-solid fa-magnifying-glass text-base-content/40"></i>
        <input v-model="query" type="search" class="grow" placeholder="Search custom images…" />
      </label>
      <button class="btn btn-xs btn-ghost btn-square" title="Reload" :disabled="loading" @click="load">
        <i class="fa-solid fa-arrows-rotate" :class="loading ? 'animate-spin' : ''"></i>
      </button>
    </div>

    <div v-if="mode === 'create'" class="rounded-xl border border-base-content/10 bg-base-200/40 p-2.5 flex flex-col gap-1.5">
      <div class="flex flex-wrap gap-1.5">
        <input v-model="form.name" class="input input-xs input-bordered flex-1 min-w-[10rem]" placeholder="Name (e.g. Firefox + Jenkins)" />
        <input v-model="form.base_image" class="input input-xs input-bordered flex-1 min-w-[12rem] font-mono" placeholder="Base image (lscr.io/linuxserver/webtop:ubuntu-kde)" />
      </div>
      <input v-model="form.description" class="input input-xs input-bordered" placeholder="What is this image for?" />
      <div class="flex flex-wrap gap-1.5 items-center">
        <input v-model="form.tags" class="input input-xs input-bordered flex-1 min-w-[8rem]" placeholder="tags: ci, firefox" />
        <select v-model="form.visibility" class="select select-xs select-bordered">
          <option v-for="option in visibilityOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <input
          v-if="form.visibility === 'custom'"
          v-model="form.projects"
          class="input input-xs input-bordered flex-1 min-w-[8rem]"
          placeholder="project ids: p2, p3"
        />
        <button class="btn btn-xs btn-primary" :disabled="saving" @click="submitCreate">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-folder-plus"></i> Create folder
        </button>
        <button class="btn btn-xs btn-ghost" @click="cancelForm">Cancel</button>
      </div>
      <p class="text-[10px] leading-snug text-base-content/50">
        Creates <code>.codx/coworking/images/&lt;id&gt;.&lt;slug&gt;/</code> with the manifest,
        a Dockerfile scaffold and a README. Hand the folder to the
        <code>codx-junior-max</code> assistant to finish the recipe.
      </p>
    </div>

    <div v-if="mode === 'import'" class="rounded-xl border border-base-content/10 bg-base-200/40 p-2.5 flex flex-col gap-1.5">
      <div class="flex flex-wrap gap-1.5 items-center">
        <select v-model="importForm.source" class="select select-xs select-bordered">
          <option value="dockerhub">Docker Hub</option>
          <option value="git">Git repository</option>
        </select>
        <input
          v-if="importForm.source === 'dockerhub'"
          v-model="importForm.reference"
          class="input input-xs input-bordered flex-1 min-w-[12rem] font-mono"
          placeholder="jenkins/jenkins:lts-jdk17"
          @keyup.enter="submitImport"
        />
        <template v-else>
          <input
            v-model="importForm.git_url"
            class="input input-xs input-bordered flex-1 min-w-[12rem] font-mono"
            placeholder="https://github.com/acme/coworking-ide.git"
            @keyup.enter="submitImport"
          />
          <input v-model="importForm.git_ref" class="input input-xs input-bordered w-24 font-mono" placeholder="main" />
        </template>
        <select v-model="importForm.visibility" class="select select-xs select-bordered">
          <option v-for="option in visibilityOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <input
          v-if="importForm.visibility === 'custom'"
          v-model="importForm.projects"
          class="input input-xs input-bordered flex-1 min-w-[8rem]"
          placeholder="project ids: p2, p3"
        />
        <button class="btn btn-xs btn-primary" :disabled="saving" @click="submitImport">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-file-import"></i> Import
        </button>
        <button class="btn btn-xs btn-ghost" @click="cancelForm">Cancel</button>
      </div>
      <p class="text-[10px] leading-snug text-base-content/50">
        A Docker Hub import keeps a <code>FROM &lt;ref&gt;</code> recipe you can extend; a git
        import keeps a <code>build.sh</code> that clones the repository and runs its Dockerfile.
        Both live in the same folder format as a new image.
      </p>
    </div>

    <p v-if="formError" class="rounded-lg bg-error/10 px-2.5 py-2 text-[11px] text-error">
      <i class="fa-solid fa-circle-exclamation mr-1"></i>{{ formError }}
    </p>
    <p v-if="error" class="rounded-lg bg-error/10 px-2.5 py-2 text-[11px] text-error">
      <i class="fa-solid fa-circle-exclamation mr-1"></i>{{ error }}
    </p>

    <div class="flex-1 min-h-[14rem] overflow-y-auto pr-1">
      <div v-if="loading" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div v-for="n in 4" :key="n" class="h-28 rounded-xl bg-base-200 animate-pulse"></div>
      </div>
      <div v-else-if="!filtered.length" class="flex flex-col items-center justify-center py-12 text-base-content/40">
        <i class="fa-regular fa-images text-3xl mb-2"></i>
        <p class="text-sm">No custom images yet.</p>
        <p class="mt-1 text-[11px]">Create one or import it from Docker Hub or a git repository.</p>
      </div>
      <div v-else class="flex flex-col gap-2">
        <div
          v-for="image in filtered"
          :key="image.id"
          class="rounded-xl border border-base-content/10 bg-base-100 p-2.5 flex flex-col gap-1"
        >
          <div class="flex flex-wrap items-center gap-1.5">
            <i class="fa-solid fa-image text-primary" aria-hidden="true"></i>
            <span class="text-xs font-semibold">{{ image.name }}</span>
            <span class="badge badge-xs" :class="visibilityClass(image.visibility)">
              <i class="fa-solid fa-eye mr-0.5"></i>{{ visibilityLabel(image.visibility) }}
            </span>
            <span class="badge badge-xs badge-outline">{{ sourceLabel(image.source) }}</span>
            <span class="badge badge-xs" :class="statusClass(image.status)">{{ image.status }}</span>
            <span class="ml-auto flex items-center gap-1">
              <select
                class="select select-xs select-bordered"
                :value="image.visibility"
                title="Who sees this image"
                @change="changeVisibility(image, $event.target.value)"
              >
                <option v-for="option in visibilityOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
              <button class="btn btn-xs btn-ghost btn-square text-error" title="Delete the folder" @click="remove(image)">
                <i class="fa-regular fa-trash-can"></i>
              </button>
            </span>
          </div>
          <p class="text-[11px] leading-snug text-base-content/60">
            {{ image.description || 'No description yet.' }}
          </p>
          <div class="flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-base-content/40">
            <span class="truncate">{{ dockerRef(image) }}</span>
            <span v-if="image.created_by">· by {{ image.created_by }}</span>
            <span v-if="image.docker_ref">· {{ image.docker_ref }}</span>
            <span v-if="image.git_url">· {{ image.git_url }}@{{ image.git_ref || 'main' }}</span>
            <span v-if="image.projects?.length">· projects: {{ image.projects.join(', ') }}</span>
          </div>
          <div class="flex flex-wrap items-center gap-1">
            <span v-for="tag in image.tags || []" :key="tag" class="badge badge-xs badge-ghost">{{ tag }}</span>
            <span class="flex-1"></span>
            <button class="btn btn-xs btn-ghost" @click="togglePrompt(image)">
              <i class="fa-solid fa-wand-magic-sparkles mr-1"></i>Prompt
            </button>
            <button class="btn btn-xs btn-primary" @click="choose(image)">
              <i class="fa-solid fa-desktop mr-1"></i>Use image
            </button>
          </div>
          <div v-if="promptImage?.id === image.id" class="mt-1 rounded-lg bg-base-200/60 p-2">
            <div class="flex items-center gap-1.5">
              <span class="text-[11px] font-semibold text-base-content/60">
                Hand the folder to @{{ image.assistant_profile || 'codx-junior-max' }}
              </span>
              <button class="btn btn-xs btn-ghost ml-auto" @click="copyPrompt(image)">
                <i class="fa-regular fa-copy mr-1"></i>Copy
              </button>
            </div>
            <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-[10px] leading-snug text-base-content/60">{{ image.assistant_prompt }}</pre>
          </div>
        </div>
      </div>
    </div>

    <p class="text-[10px] leading-snug text-base-content/40">
      Definitions live in <code>.codx/coworking/images/</code>: one folder per image with its
      manifest, Dockerfile, support files and media. The folder is not committed and the image
      is built locally with <code>docker build</code>.
    </p>
  </div>
</template>
