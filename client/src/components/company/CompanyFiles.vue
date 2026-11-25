<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { API } from '@/api/api'

/**
 * Company files — the company-owned coworking spaces.
 *
 * A space belongs to the company, never to a project. The browser shows the
 * company's own files (writable) plus two virtual roots:
 *
 *   projects/<id>  the real files of a linked company project (read-only)
 *   folders/<id>   a host folder the company mapped into the space
 *
 * Nothing is copied: navigating a virtual folder resolves the project/folder on
 * the server, so the company always sees the current disk state and the project
 * keeps a single source of truth.
 */
const props = defineProps({
  companyId: { type: String, default: '' },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false }
})

const api = API.companies

const spaces = ref([])
const activeSpaceId = ref('')
const path = ref('')
const listing = ref(null)
const loading = ref(false)
const error = ref('')
const busy = ref(false)

const openEntry = ref(null)
const textPreview = ref('')
const textLoading = ref(false)
const previewError = ref('')

const spaceDialog = ref(false)
const spaceEditing = ref(false)
const spaceForm = ref({ name: '', description: '', project_ids: [], folders: [] })
const spaceError = ref('')

const folderDialog = ref(false)
const folderName = ref('')
const folderError = ref('')

const uploadInput = ref(null)
const uploading = ref(false)

const companyProjects = computed(() =>
  (props.snapshot?.projects || []).map(project => ({
    project_id: project.project_id,
    name: project.name || project.discovered_name || project.project_id,
    available: project.available !== false
  }))
)
const activeSpace = computed(() =>
  spaces.value.find(space => space.id === activeSpaceId.value) || null
)
const breadcrumbs = computed(() => listing.value?.breadcrumbs || [])
const entries = computed(() => listing.value?.entries || [])
const isWritable = computed(() =>
  Boolean(props.canManage && listing.value && !listing.value.readonly)
)

const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico']
const PDF_EXTENSIONS = ['pdf']
const BINARY_EXTENSIONS = ['zip', 'gz', 'tgz', 'tar', '7z', 'rar', 'exe', 'dll', 'so', 'bin', 'iso', 'mp4', 'mp3', 'wav', 'mov', 'avi', 'webm']

function extension(name) {
  const value = String(name || '')
  const index = value.lastIndexOf('.')
  return index >= 0 ? value.slice(index + 1).toLowerCase() : ''
}

function isImage(entry) {
  return IMAGE_EXTENSIONS.includes(extension(entry?.name))
}

function isPdf(entry) {
  return PDF_EXTENSIONS.includes(extension(entry?.name))
}

function isBinary(entry) {
  return BINARY_EXTENSIONS.includes(extension(entry?.name))
}

function iconFor(entry) {
  if (entry.kind === 'projects_root') return 'fa-solid fa-diagram-project'
  if (entry.kind === 'folders_root') return 'fa-solid fa-folder-tree'
  if (entry.kind === 'project') return 'fa-solid fa-cube'
  if (entry.kind === 'mapped_folder') return 'fa-solid fa-hard-drive'
  if (entry.is_dir) return 'fa-solid fa-folder'
  return 'fa-regular fa-file'
}

function entryHint(entry) {
  if (entry.kind === 'project') return 'Project files · read only'
  if (entry.kind === 'mapped_folder') return entry.readonly ? 'Mapped folder · read only' : 'Mapped folder'
  if (entry.kind === 'projects_root') return 'Virtual folder of the company projects'
  if (entry.kind === 'folders_root') return 'Virtual folder of the mapped host folders'
  if (entry.readonly) return 'Read only'
  return ''
}

function formatSize(size) {
  if (size === null || size === undefined) return ''
  const value = Number(size)
  if (!Number.isFinite(value)) return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function notify(message) {
  error.value = message
}

function clearError() {
  error.value = ''
}

async function loadSpaces() {
  if (!props.companyId) return
  loading.value = true
  clearError()
  try {
    const result = await api.spaces(props.companyId)
    spaces.value = Array.isArray(result) ? result : []
    if (!spaces.value.some(space => space.id === activeSpaceId.value)) {
      activeSpaceId.value = spaces.value[0]?.id || ''
      path.value = ''
    }
    if (activeSpaceId.value) {
      await loadFiles()
    } else {
      listing.value = null
    }
  } catch (ex) {
    notify(ex?.response?.data?.detail || ex?.message || 'Could not load the company spaces')
  } finally {
    loading.value = false
  }
}

async function loadFiles() {
  if (!props.companyId || !activeSpaceId.value) return
  loading.value = true
  clearError()
  try {
    listing.value = await api.spaceFiles(props.companyId, activeSpaceId.value, path.value)
    if (listing.value?.path !== undefined) path.value = listing.value.path
  } catch (ex) {
    listing.value = null
    notify(ex?.response?.data?.detail || ex?.message || 'Could not list this folder')
  } finally {
    loading.value = false
  }
}

function selectSpace(space) {
  if (!space || space.id === activeSpaceId.value) return
  activeSpaceId.value = space.id
  path.value = ''
  openEntry.value = null
  loadFiles()
}

function navigate(target) {
  path.value = target || ''
  openEntry.value = null
  loadFiles()
}

function goUp() {
  const parts = String(path.value || '').split('/').filter(Boolean)
  parts.pop()
  navigate(parts.join('/'))
}

function openEntryItem(entry) {
  if (entry.available === false) {
    notify(
      entry.kind === 'project'
        ? `The project folder of "${entry.name}" is not available on this host`
        : `The folder "${entry.name}" is not available on this host`
    )
    return
  }
  if (entry.is_dir || entry.kind === 'projects_root' || entry.kind === 'folders_root') {
    navigate(entry.path)
    return
  }
  openFile(entry)
}

async function openFile(entry) {
  openEntry.value = entry
  textPreview.value = ''
  previewError.value = ''
  if (isImage(entry) || isPdf(entry) || isBinary(entry)) return
  textLoading.value = true
  try {
    const url = api.spaceFileUrl(props.companyId, activeSpaceId.value, entry.path)
    const response = await fetch(url, { headers: API.connection.headers })
    if (!response.ok) {
      throw new Error(`The server answered ${response.status}`)
    }
    const text = await response.text()
    textPreview.value = text.length > 500000
      ? `${text.slice(0, 500000)}\n\n… preview truncated (download the file to read it all)`
      : text
  } catch (ex) {
    previewError.value = ex?.message || 'Could not read the file'
  } finally {
    textLoading.value = false
  }
}

function closeFile() {
  openEntry.value = null
  textPreview.value = ''
  previewError.value = ''
}

function fileUrl(entry, download = false) {
  return api.spaceFileUrl(props.companyId, activeSpaceId.value, entry.path, { download })
}

function previewUrl(entry) {
  return api.spaceFileUrl(props.companyId, activeSpaceId.value, entry.path)
}

async function deleteEntry(entry) {
  if (!props.canManage) return
  if (!window.confirm(`Delete "${entry.name}"? This cannot be undone.`)) return
  busy.value = true
  clearError()
  try {
    await api.deleteSpaceEntry(props.companyId, activeSpaceId.value, entry.path)
    await loadFiles()
    await loadSpaces()
  } catch (ex) {
    notify(ex?.response?.data?.detail || ex?.message || 'Could not delete the entry')
  } finally {
    busy.value = false
  }
}

function openCreateFolder() {
  folderName.value = ''
  folderError.value = ''
  folderDialog.value = true
}

async function createFolder() {
  const name = folderName.value.trim()
  if (!name) {
    folderError.value = 'Type a folder name'
    return
  }
  busy.value = true
  try {
    await api.createSpaceFolder(props.companyId, activeSpaceId.value, path.value, name)
    folderDialog.value = false
    await loadFiles()
    await loadSpaces()
  } catch (ex) {
    folderError.value = ex?.response?.data?.detail || ex?.message || 'Could not create the folder'
  } finally {
    busy.value = false
  }
}

function chooseUpload() {
  uploadInput.value?.click()
}

async function onUpload(event) {
  const files = Array.from(event.target?.files || [])
  if (!files.length) return
  uploading.value = true
  clearError()
  try {
    for (const file of files) {
      await api.uploadSpaceFile(props.companyId, activeSpaceId.value, path.value, file)
    }
    await loadFiles()
    await loadSpaces()
  } catch (ex) {
    notify(ex?.response?.data?.detail || ex?.message || 'Could not upload the file')
  } finally {
    uploading.value = false
    if (event.target) event.target.value = ''
  }
}

// ── Space editor ──────────────────────────────────────────────────────────

function resetSpaceForm() {
  spaceForm.value = { name: '', description: '', project_ids: [], folders: [] }
  spaceError.value = ''
}

function openCreateSpace() {
  resetSpaceForm()
  spaceEditing.value = false
  spaceDialog.value = true
}

function openEditSpace(space) {
  if (!space) return
  spaceEditing.value = true
  spaceForm.value = {
    name: space.name || '',
    description: space.description || '',
    project_ids: [...(space.project_ids || [])],
    folders: (space.folders || []).map(folder => ({
      id: folder.id,
      name: folder.name || '',
      path: folder.path || '',
      readonly: folder.readonly !== false
    }))
  }
  spaceError.value = ''
  spaceDialog.value = true
}

function addFolderRow() {
  spaceForm.value.folders.push({ id: '', name: '', path: '', readonly: true })
}

function removeFolderRow(index) {
  spaceForm.value.folders.splice(index, 1)
}

function toggleProject(projectId, checked) {
  const current = new Set(spaceForm.value.project_ids || [])
  if (checked) current.add(projectId)
  else current.delete(projectId)
  spaceForm.value.project_ids = [...current]
}

async function saveSpace() {
  const payload = {
    name: spaceForm.value.name.trim(),
    description: spaceForm.value.description.trim(),
    project_ids: spaceForm.value.project_ids,
    folders: spaceForm.value.folders
      .filter(folder => String(folder.path || '').trim())
      .map(folder => ({
        id: folder.id || '',
        name: folder.name || '',
        path: folder.path.trim(),
        readonly: folder.readonly !== false
      }))
  }
  if (!payload.name) {
    spaceError.value = 'The space needs a name'
    return
  }
  busy.value = true
  spaceError.value = ''
  try {
    let saved
    if (spaceEditing.value && activeSpace.value) {
      saved = await api.updateSpace(props.companyId, activeSpace.value.id, payload)
    } else {
      saved = await api.createSpace(props.companyId, payload)
    }
    spaceDialog.value = false
    await loadSpaces()
    if (saved?.id) {
      activeSpaceId.value = saved.id
      path.value = ''
      await loadFiles()
    }
  } catch (ex) {
    spaceError.value = ex?.response?.data?.detail || ex?.message || 'Could not save the space'
  } finally {
    busy.value = false
  }
}

async function deleteSpace() {
  const space = activeSpace.value
  if (!space || !props.canManage) return
  if (!window.confirm(`Delete the space "${space.name}"? Its company files are moved to the company trash.`)) return
  busy.value = true
  try {
    await api.deleteSpace(props.companyId, space.id)
    spaceDialog.value = false
    activeSpaceId.value = ''
    path.value = ''
    await loadSpaces()
  } catch (ex) {
    spaceError.value = ex?.response?.data?.detail || ex?.message || 'Could not delete the space'
  } finally {
    busy.value = false
  }
}

watch(() => props.companyId, () => {
  activeSpaceId.value = ''
  path.value = ''
  spaces.value = []
  loadSpaces()
})
watch(() => props.snapshot, () => {
  // A project linked/unlinked elsewhere changes the virtual folders.
  if (props.companyId && !spaceDialog.value) loadSpaces()
}, { deep: false })

onMounted(loadSpaces)
</script>

<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h2 class="text-lg font-black flex items-center gap-2">
          <i class="fa-solid fa-folder-tree text-primary-ink"></i> Company files
        </h2>
        <p class="text-xs text-base-content/50 max-w-2xl">
          Coworking spaces owned by the company. Each space keeps the company's own files and
          exposes the linked projects as read-only virtual folders, so the team can navigate
          company and project files from one place.
        </p>
      </div>
      <div v-if="canManage" class="flex items-center gap-2">
        <button class="btn btn-sm btn-ghost border border-base-content/10" @click="openCreateSpace">
          <i class="fa-solid fa-plus"></i> New space
        </button>
        <button
          v-if="activeSpace"
          class="btn btn-sm btn-primary"
          :disabled="busy"
          @click="openEditSpace(activeSpace)"
        >
          <i class="fa-solid fa-sliders"></i> Space settings
        </button>
      </div>
    </header>

    <p v-if="error" class="rounded-xl border border-error/30 bg-error/10 px-3 py-2 text-xs text-error-ink">
      <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ error }}
      <button class="btn btn-ghost btn-xs ml-2" @click="clearError">Dismiss</button>
    </p>

    <div class="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <!-- Spaces rail -->
      <aside class="rounded-2xl border border-base-content/10 bg-base-100 p-2">
        <p class="px-2 py-1 text-xs font-bold uppercase tracking-wider text-base-content/40">Spaces</p>
        <button
          v-for="space in spaces"
          :key="space.id"
          class="w-full flex items-start gap-2 rounded-xl px-2 py-2 text-left transition-colors"
          :class="space.id === activeSpaceId ? 'bg-primary/10 text-primary-ink' : 'hover:bg-base-200/70'"
          @click="selectSpace(space)"
        >
          <i :class="[space.icon || 'fa-solid fa-folder-tree', 'mt-0.5 text-sm']"></i>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-xs font-bold">{{ space.name }}</span>
            <span class="block truncate text-xs opacity-60">
              {{ space.project_count || 0 }} projects · {{ space.folder_count || 0 }} folders
            </span>
          </span>
        </button>
        <p v-if="!spaces.length && !loading" class="px-2 py-3 text-xs opacity-60">
          No spaces yet. Create one to start storing company files.
        </p>
      </aside>

      <!-- Browser -->
      <section class="min-h-[460px] rounded-2xl border border-base-content/10 bg-base-100 flex flex-col overflow-hidden">
        <template v-if="activeSpace">
          <div class="flex flex-wrap items-center gap-1 border-b border-base-content/10 p-2">
            <nav class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" aria-label="Folder path">
              <button
                v-for="(crumb, index) in breadcrumbs"
                :key="`${crumb.path}-${index}`"
                class="btn btn-xs btn-ghost flex-shrink-0 gap-1"
                :class="{ 'text-primary-ink': index === breadcrumbs.length - 1 }"
                @click="navigate(crumb.path)"
              >
                <i v-if="index === 0" class="fa-solid fa-folder-tree text-xs"></i>
                <i v-else class="fa-solid fa-chevron-right text-[10px] opacity-40"></i>
                <span class="max-w-40 truncate">{{ crumb.name }}</span>
              </button>
            </nav>
            <div class="flex items-center gap-1">
              <button class="btn btn-xs btn-ghost btn-square" title="Refresh" aria-label="Refresh" @click="loadFiles">
                <i class="fa-solid fa-rotate"></i>
              </button>
              <button
                v-if="canManage && isWritable"
                class="btn btn-xs btn-ghost border border-base-content/10"
                :disabled="busy"
                @click="openCreateFolder"
              >
                <i class="fa-solid fa-folder-plus"></i> New folder
              </button>
              <button
                v-if="canManage && isWritable"
                class="btn btn-xs btn-ghost border border-base-content/10"
                :disabled="uploading"
                @click="chooseUpload"
              >
                <span v-if="uploading" class="loading loading-spinner loading-xs"></span>
                <i v-else class="fa-solid fa-upload"></i> Upload
              </button>
              <input ref="uploadInput" type="file" multiple class="hidden" @change="onUpload" />
            </div>
          </div>

          <p v-if="listing?.readonly" class="border-b border-base-content/10 bg-base-200/40 px-3 py-1.5 text-xs opacity-70">
            <i class="fa-solid fa-lock mr-1"></i>
            This virtual folder is read only. Company files are writable from the space root.
          </p>

          <div v-if="loading" class="flex flex-1 items-center justify-center gap-2 p-8 text-xs opacity-70">
            <span class="loading loading-spinner loading-xs"></span> Loading files…
          </div>

          <div v-else class="flex-1 overflow-y-auto divide-y divide-base-content/5">
            <button
              v-if="path"
              class="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-base-200/60"
              @click="goUp"
            >
              <i class="fa-solid fa-arrow-turn-up w-3.5 text-center text-xs opacity-50"></i>
              <span class="font-mono text-xs">..</span>
            </button>

            <p v-if="!entries.length" class="p-8 text-center text-xs opacity-60">
              <i class="fa-regular fa-folder-open mb-2 block text-2xl opacity-30"></i>
              This folder is empty.
            </p>

            <div
              v-for="entry in entries"
              :key="entry.path"
              class="flex items-center gap-2 px-3 py-2 hover:bg-base-200/60"
            >
              <button class="flex min-w-0 flex-1 items-center gap-2 text-left" @click="openEntryItem(entry)">
                <i :class="[iconFor(entry), entry.is_dir ? 'text-warning-ink' : 'opacity-50', 'w-4 text-center text-sm']"></i>
                <span class="min-w-0">
                  <span class="block truncate text-xs font-medium">{{ entry.name }}</span>
                  <span v-if="entryHint(entry)" class="block truncate text-xs opacity-50">{{ entryHint(entry) }}</span>
                  <span v-else-if="entry.kind === 'file'" class="block truncate text-xs opacity-40">
                    {{ formatSize(entry.size) }}
                  </span>
                </span>
              </button>
              <span v-if="entry.kind === 'project'" class="badge badge-xs badge-ghost">project</span>
              <span v-else-if="entry.kind === 'mapped_folder'" class="badge badge-xs badge-ghost">mapped</span>
              <span v-if="entry.readonly" class="badge badge-xs badge-outline">ro</span>
              <a
                v-if="!entry.is_dir && entry.available"
                :href="fileUrl(entry, true)"
                class="btn btn-xs btn-ghost btn-square"
                title="Download"
                :aria-label="`Download ${entry.name}`"
                target="_blank"
                rel="noopener noreferrer"
              >
                <i class="fa-solid fa-download"></i>
              </a>
              <button
                v-if="canManage && isWritable && !entry.virtual"
                class="btn btn-xs btn-ghost btn-square text-error-ink"
                title="Delete"
                :aria-label="`Delete ${entry.name}`"
                :disabled="busy"
                @click="deleteEntry(entry)"
              >
                <i class="fa-regular fa-trash-can"></i>
              </button>
            </div>
          </div>
        </template>

        <div v-else class="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <i class="fa-solid fa-folder-tree text-3xl text-base-content/20"></i>
          <p class="text-sm font-semibold">No coworking space yet</p>
          <p class="max-w-sm text-xs opacity-60">
            A space is stored in the company and holds its own files. Create one to add company
            files and map the projects the company runs.
          </p>
          <button v-if="canManage" class="btn btn-sm btn-primary mt-1" @click="openCreateSpace">
            <i class="fa-solid fa-plus"></i> Create the first space
          </button>
        </div>
      </section>
    </div>

    <!-- File preview -->
    <div v-if="openEntry" class="modal modal-open" role="dialog" aria-modal="true" aria-label="File preview">
      <div class="modal-box flex h-[80vh] max-w-5xl flex-col p-0">
        <div class="flex items-center gap-2 border-b border-base-content/10 px-3 py-2">
          <button class="btn btn-xs btn-ghost gap-1" @click="closeFile">
            <i class="fa-solid fa-arrow-left"></i>
            <span class="hidden sm:inline">Back to files</span>
          </button>
          <span class="min-w-0 flex-1 truncate font-mono text-xs opacity-70">{{ openEntry.path }}</span>
          <a
            :href="fileUrl(openEntry, true)"
            class="btn btn-xs btn-ghost"
            target="_blank"
            rel="noopener noreferrer"
          >
            <i class="fa-solid fa-download"></i> Download
          </a>
          <button class="btn btn-xs btn-ghost btn-square" aria-label="Close" @click="closeFile">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="min-h-0 flex-1 overflow-auto bg-base-200/30">
          <img
            v-if="isImage(openEntry)"
            :src="previewUrl(openEntry)"
            :alt="openEntry.name"
            class="mx-auto max-h-full max-w-full object-contain p-4"
          />
          <iframe
            v-else-if="isPdf(openEntry)"
            :src="previewUrl(openEntry)"
            :title="openEntry.name"
            class="h-full w-full border-0"
          ></iframe>
          <div v-else-if="textLoading" class="flex items-center justify-center gap-2 p-8 text-xs opacity-70">
            <span class="loading loading-spinner loading-xs"></span> Loading the file…
          </div>
          <p v-else-if="previewError" class="p-6 text-xs text-error-ink">
            <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ previewError }}
          </p>
          <pre v-else-if="textPreview" class="whitespace-pre-wrap break-words p-4 text-xs leading-relaxed">{{ textPreview }}</pre>
          <div v-else class="grid place-items-center p-8 text-center text-xs opacity-60">
            <i class="fa-regular fa-file mb-2 text-3xl opacity-30"></i>
            This file cannot be previewed in the browser. Download it to open it.
          </div>
        </div>
      </div>
      <div class="modal-backdrop" @click="closeFile"></div>
    </div>

    <!-- Space editor -->
    <div v-if="spaceDialog" class="modal modal-open" role="dialog" aria-modal="true">
      <div class="modal-box max-w-2xl">
        <div class="mb-3 flex items-center justify-between">
          <h3 class="text-lg font-bold">{{ spaceEditing ? 'Space settings' : 'New coworking space' }}</h3>
          <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="spaceDialog = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="grid gap-3 md:grid-cols-2">
          <label class="form-control md:col-span-2">
            <span class="label-text text-xs">Space name</span>
            <input v-model="spaceForm.name" class="input input-sm input-bordered" placeholder="Company files" />
          </label>
          <label class="form-control md:col-span-2">
            <span class="label-text text-xs">Description <span class="opacity-50">(optional)</span></span>
            <input v-model="spaceForm.description" class="input input-sm input-bordered" placeholder="What is kept in this space?" />
          </label>
        </div>

        <div class="mt-4 rounded-xl border border-base-content/10 p-3">
          <p class="mb-2 flex items-center gap-1.5 text-xs font-bold">
            <i class="fa-solid fa-cube text-primary-ink"></i> Projects shown as virtual folders
          </p>
          <p class="mb-2 text-xs opacity-60">
            Their files are read only through the company browser; the project keeps its own files.
          </p>
          <div class="grid gap-1.5 sm:grid-cols-2">
            <label
              v-for="project in companyProjects"
              :key="project.project_id"
              class="flex items-start gap-2 rounded-lg border border-base-content/10 px-2 py-1.5"
              :class="{ 'border-primary/40 bg-primary/5': spaceForm.project_ids.includes(project.project_id) }"
            >
              <input
                type="checkbox"
                class="checkbox checkbox-xs mt-0.5"
                :checked="spaceForm.project_ids.includes(project.project_id)"
                @change="toggleProject(project.project_id, $event.target.checked)"
              />
              <span class="min-w-0">
                <span class="block truncate text-xs font-medium">{{ project.name }}</span>
                <span class="block truncate text-xs opacity-50">
                  {{ project.available ? 'available' : 'folder missing' }}
                </span>
              </span>
            </label>
          </div>
          <p v-if="!companyProjects.length" class="text-xs opacity-60">
            The company has no linked projects yet. Link a project first to browse its files here.
          </p>
        </div>

        <div class="mt-3 rounded-xl border border-base-content/10 p-3">
          <p class="mb-2 flex items-center gap-1.5 text-xs font-bold">
            <i class="fa-solid fa-hard-drive text-primary-ink"></i> Mapped host folders
          </p>
          <p class="mb-2 text-xs opacity-60">
            Absolute paths on the codx-junior host. They are read only unless you allow writes.
          </p>
          <div v-for="(folder, index) in spaceForm.folders" :key="index" class="mb-2 grid gap-1.5 rounded-lg border border-base-content/10 p-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto_auto]">
            <input v-model="folder.name" class="input input-xs input-bordered" placeholder="Display name" />
            <input v-model="folder.path" class="input input-xs input-bordered font-mono" placeholder="/absolute/host/folder" />
            <label class="flex items-center gap-1 text-xs">
              <input v-model="folder.readonly" type="checkbox" class="checkbox checkbox-xs" /> read only
            </label>
            <button class="btn btn-xs btn-ghost btn-square text-error-ink" title="Remove folder" @click="removeFolderRow(index)">
              <i class="fa-solid fa-trash-can"></i>
            </button>
          </div>
          <button class="btn btn-xs btn-ghost border border-base-content/10" @click="addFolderRow">
            <i class="fa-solid fa-plus"></i> Map a folder
          </button>
        </div>

        <p v-if="spaceError" class="mt-3 text-xs text-error-ink">{{ spaceError }}</p>

        <div class="modal-action">
          <button v-if="spaceEditing" class="btn btn-sm btn-ghost mr-auto text-error-ink" :disabled="busy" @click="deleteSpace">
            Delete space
          </button>
          <button class="btn btn-sm btn-ghost" @click="spaceDialog = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="busy || !spaceForm.name.trim()" @click="saveSpace">
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            {{ spaceEditing ? 'Save space' : 'Create space' }}
          </button>
        </div>
      </div>
      <div class="modal-backdrop" @click="spaceDialog = false"></div>
    </div>

    <!-- New folder -->
    <div v-if="folderDialog" class="modal modal-open" role="dialog" aria-modal="true">
      <div class="modal-box max-w-sm">
        <h3 class="mb-3 text-base font-bold">New folder</h3>
        <label class="form-control">
          <span class="label-text text-xs">Folder name</span>
          <input v-model="folderName" class="input input-sm input-bordered" autofocus @keydown.enter="createFolder" />
        </label>
        <p v-if="folderError" class="mt-2 text-xs text-error-ink">{{ folderError }}</p>
        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="folderDialog = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="busy" @click="createFolder">Create</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="folderDialog = false"></div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
