<template>
  <!-- The global <modal> caps its box at max-w-2xl: the browser must fit that
       width instead of asking for more, otherwise its content is painted
       outside the dialog and gets clipped. -->
  <div class="flex flex-col gap-2 w-full max-w-full h-[70vh] max-h-[70vh] min-h-0 overflow-hidden">
    <p v-if="session?.name" class="flex items-center gap-1.5 text-xs text-base-content/60 min-w-0">
      <i class="fa-solid fa-window-restore flex-shrink-0" aria-hidden="true"></i>
      <span class="truncate">{{ session.name }}</span>
    </p>

    <!-- One chip per mounted folder, the active one highlighted -->
    <div v-if="roots.length" class="flex flex-wrap gap-1">
      <button
        v-for="root in roots"
        :key="keyOf(root)"
        class="btn btn-xs gap-1 max-w-full"
        :class="isActive(root) ? 'btn-primary' : 'btn-ghost'"
        :title="`Mounted in the computer at ${root.containerPath}${root.readonly ? ' (read only)' : ''}`"
        @click="select(root)"
      >
        <i class="fa-solid fa-folder text-[10px]" aria-hidden="true"></i>
        <span class="truncate max-w-40">{{ root.label }}</span>
        <span v-if="root.readonly" class="badge badge-xs badge-warning badge-outline">ro</span>
      </button>
    </div>

    <p v-else class="rounded-lg bg-base-200/60 px-2.5 py-2 text-[11px] opacity-70" role="status">
      <i class="fa-solid fa-circle-info mr-1" aria-hidden="true"></i>
      {{ emptyHint }}
    </p>

    <!-- File manager rooted at the selected bind -->
    <div
      v-if="selected"
      class="flex-1 min-h-0 min-w-0 flex flex-col rounded-lg border border-base-300 overflow-hidden"
    >
      <template v-if="openedFile">
        <div class="flex items-center gap-2 px-2 py-1 border-b border-base-300 bg-base-200/50">
          <button class="btn btn-xs btn-ghost gap-1" @click="closeFile">
            <i class="fa-solid fa-arrow-left" aria-hidden="true"></i>
            <span class="hidden sm:inline">Back to files</span>
          </button>
          <span class="text-[11px] font-mono truncate opacity-70">{{ openedFile }}</span>
        </div>
        <FileViewer class="flex-1 min-h-0 overflow-auto" :params="{ filePath: openedFile }" />
      </template>

      <FileExplorer
        v-else
        :key="keyOf(selected)"
        class="flex-1 min-h-0 overflow-hidden"
        :root-path="absoluteRootPath"
        @open="onOpenFile"
      />
    </div>

    <p v-if="selected" class="text-[10px] opacity-50 truncate">
      The computer sees this folder at
      <code class="font-mono">{{ selected.containerPath }}</code>.
    </p>
  </div>
</template>

<script>
import FileExplorer from '@/components/filebrowser/FileExplorer.vue'
import FileViewer from '@/components/filebrowser/FileViewer.vue'
import { explorableVolumeRoots, isRemoteSession } from './volumeMappings'

/**
 * Browsable list of the volumes mounted in a co-desktop.
 *
 * The body is the project FileExplorer (single source of truth for listing and
 * file operations); a file opened there is shown by FileViewer inside the same
 * dialog, so the user never loses the modal or the folder context.
 */
export default {
  name: 'CoworkingFoldersBrowser',
  components: { FileExplorer, FileViewer },
  props: {
    session: { type: Object, default: null }
  },
  data() {
    return {
      // Key of the selected root (`<containerPath>|<path>`)
      selectedKey: null,
      // Project-relative path of the file opened from the explorer ('' = none)
      openedFile: ''
    }
  },
  computed: {
    projectName() {
      return this.$project?.project_name ||
        this.$storex?.projects?.activeProject?.project_name ||
        'project'
    },
    absoluteProjectPath() {
      const root = this.$project?.abs_project_path ||
        this.$storex?.projects?.activeProject?.abs_project_path ||
        ''
      return String(root).replace(/\/+$/, '')
    },
    roots() {
      return explorableVolumeRoots(this.session, {
        projectName: this.projectName,
        absoluteProjectPath: this.absoluteProjectPath
      })
    },
    selected() {
      return this.roots.find(root => this.keyOf(root) === this.selectedKey) || this.roots[0] || null
    },
    /**
     * Absolute path of the selected bind: FileExplorer lists from an absolute
     * folder and refuses to walk above it, which is exactly the mount rule.
     */
    absoluteRootPath() {
      const root = this.selected
      if (!root) return this.absoluteProjectPath
      const relative = String(root.path || '').replace(/^\/+/, '')
      if (!relative) return this.absoluteProjectPath
      const base = this.absoluteProjectPath
      // A root that already is a host path (outside the project) is used as is.
      return base ? `${base}/${relative}` : relative
    },
    emptyHint() {
      if (isRemoteSession(this.session)) {
        return 'This computer runs on a remote machine: it keeps its own filesystem, so there is nothing to browse.'
      }
      return 'This computer has no project folder mounted: only its own binds (profile, downloads) exist. Create a new co-desktop to mount project folders.'
    }
  },
  watch: {
    // A root that disappeared (session restarted) must not leave a dangling key.
    roots: {
      handler(list) {
        if (!list.some(root => this.keyOf(root) === this.selectedKey)) {
          this.selectedKey = list.length ? this.keyOf(list[0]) : null
          this.openedFile = ''
        }
      },
      immediate: true
    }
  },
  methods: {
    keyOf(root) {
      return `${root.containerPath}|${root.path}`
    },
    isActive(root) {
      return !!this.selected && this.keyOf(this.selected) === this.keyOf(root)
    },
    select(root) {
      if (this.isActive(root)) return
      this.selectedKey = this.keyOf(root)
      this.openedFile = ''
    },
    /**
     * FileExplorer emits the absolute path of the clicked file; the files API
     * (and FileViewer) speak project-relative paths.
     */
    onOpenFile({ path } = {}) {
      const absolute = String(path || '')
      const base = this.absoluteProjectPath
      if (base && absolute.startsWith(`${base}/`)) {
        this.openedFile = absolute.slice(base.length + 1)
        return
      }
      if (absolute && absolute === base) return
      this.openedFile = absolute.replace(/^\/+/, '')
    },
    closeFile() {
      this.openedFile = ''
    }
  }
}
</script>
