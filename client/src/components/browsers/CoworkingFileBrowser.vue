<template>
  <!--
    Inline file browser over ONE mounted project folder.

    Nothing is cached: every navigation is a single `/api/files` request, the
    same call the folder picker uses, so the listing cannot go out of sync with
    the disk. The component draws no chrome of its own (no modal): the caller
    decides whether it lives inside a dialog, a drawer or a panel.
  -->
  <div class="flex flex-col min-h-0 flex-1 gap-2">
    <!-- Breadcrumb: the mount root first, then the path below it -->
    <nav class="flex items-center gap-1 overflow-x-auto text-xs" aria-label="Folder path">
      <button class="btn btn-xs btn-ghost gap-1 flex-shrink-0" @click="goTo(basePath)">
        <i class="fa-solid fa-folder-open text-[10px]"></i>
        <span class="max-w-40 truncate">{{ rootLabel || projectName }}</span>
      </button>
      <template v-for="(part, index) in crumbs" :key="`${part}-${index}`">
        <i class="fa-solid fa-chevron-right text-[9px] opacity-40 flex-shrink-0" aria-hidden="true"></i>
        <button class="btn btn-xs btn-ghost flex-shrink-0" @click="goTo(pathUntil(index))">
          {{ part }}
        </button>
      </template>
    </nav>

    <!-- Listing -->
    <div class="flex-1 min-h-0 overflow-y-auto rounded-lg border border-base-content/10 divide-y divide-base-content/10">
      <div v-if="loading" class="flex items-center justify-center gap-2 p-6 text-xs opacity-70">
        <span class="loading loading-spinner loading-xs"></span>
        Loading files...
      </div>

      <p v-else-if="error" class="p-4 text-xs text-error">
        <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ error }}
      </p>

      <template v-else>
        <!-- Up (never above the mount root) -->
        <button
          v-if="currentPath !== basePath"
          class="w-full flex items-center gap-2 px-2 py-2 text-left hover:bg-base-200/60 transition-colors duration-150"
          aria-label="Go up one folder"
          @click="goUp"
        >
          <i class="fa-solid fa-arrow-turn-up text-[11px] opacity-50 w-3.5 text-center"></i>
          <span class="text-xs font-mono">..</span>
        </button>

        <p v-if="!entries.length" class="p-4 text-xs opacity-60">
          This folder is empty.
        </p>

        <div
          v-for="entry in entries"
          :key="entry.path || entry.name"
          class="flex items-center gap-2 px-2 py-1.5 hover:bg-base-200/60 transition-colors duration-150"
        >
          <button
            v-if="entry.is_dir"
            class="flex items-center gap-2 min-w-0 flex-1 text-left"
            :aria-label="`Open ${entry.name}`"
            @click="goTo(entry.path)"
          >
            <i class="fa-solid fa-folder text-[12px] text-warning w-3.5 text-center"></i>
            <span class="text-xs truncate">{{ entry.name }}</span>
          </button>
          <template v-else>
            <i class="fa-regular fa-file text-[12px] opacity-50 w-3.5 text-center"></i>
            <span class="text-xs truncate flex-1">{{ entry.name }}</span>
          </template>

          <span v-if="!entry.is_dir && entry.size !== null" class="text-[10px] opacity-50 flex-shrink-0">
            {{ formatSize(entry.size) }}
          </span>
          <a
            v-if="downloadHref(entry) !== '#'"
            :href="downloadHref(entry)"
            target="_blank"
            rel="noopener noreferrer"
            class="btn btn-xs btn-ghost flex-shrink-0"
            :aria-label="`Download ${entry.name}`"
            @click.stop
          >
            <i class="fa-solid fa-download"></i>
          </a>
        </div>
      </template>
    </div>
  </div>
</template>

<script>
import { normalizeFolder } from './volumeMappings'

/**
 * One-level-at-a-time listing of a project folder, rooted at `rootPath`.
 */
export default {
  name: 'CoworkingFileBrowser',
  props: {
    /** Project-relative folder the browser is rooted at ('' = project root). */
    rootPath: { type: String, default: '' },
    /** Label of the root crumb (defaults to the project name). */
    rootLabel: { type: String, default: '' }
  },
  data() {
    return {
      // Project-relative folder being listed ('' = project root)
      currentPath: '',
      entries: [],
      loading: false,
      error: null
    }
  },
  computed: {
    // Same resolution as the folder picker: the project of the current view
    // first, the store API as the fallback.
    api() {
      return this.$project?.$api || this.$storex?.api || null
    },
    projectName() {
      return this.$project?.project_name ||
        this.$storex?.projects?.activeProject?.project_name ||
        'project'
    },
    basePath() {
      return normalizeFolder(this.rootPath)
    },
    baseProjectPath() {
      const root = this.$project?.abs_project_path ||
        this.$storex?.projects?.activeProject?.abs_project_path ||
        ''
      return String(root).replace(/\/+$/, '')
    },
    /** Segments below the mount root. */
    crumbs() {
      const relative = this.basePath && this.currentPath.startsWith(this.basePath)
        ? this.currentPath.slice(this.basePath.length)
        : this.currentPath
      return relative ? relative.split('/').filter(Boolean) : []
    }
  },
  watch: {
    rootPath() {
      this.load(this.basePath)
    }
  },
  mounted() {
    this.load(this.basePath)
  },
  methods: {
    absolutePath(relativePath) {
      return relativePath ? `${this.baseProjectPath}/${relativePath}` : this.baseProjectPath
    },
    /** Project-relative path of the crumbs up to `index` (inclusive). */
    pathUntil(index) {
      if (index < 0) return this.basePath
      const suffix = this.crumbs.slice(0, index + 1).join('/')
      return this.basePath ? `${this.basePath}/${suffix}` : suffix
    },
    async load(relativePath) {
      const target = relativePath || ''
      if (!this.api?.files?.list) {
        this.error = 'File API not available for this project'
        this.entries = []
        return
      }
      this.loading = true
      this.error = null
      try {
        const response = await this.api.files.list(this.absolutePath(target))
        const base = this.baseProjectPath
        this.entries = (response?.files || [])
          .map((entry) => {
            const absolute = String(entry.file_path || '').replace(/\/+$/, '')
            const relative = base && absolute.startsWith(`${base}/`)
              ? absolute.slice(base.length + 1)
              : absolute.replace(/^\/+/, '')
            const size = entry.size ?? entry.size_bytes ?? entry.file_size
            return {
              name: entry.name || relative.split('/').pop() || relative,
              path: relative,
              absolute,
              is_dir: !!(entry.is_dir ?? entry.type === 'dir'),
              size: Number.isFinite(size) ? size : null
            }
          })
          .filter(entry => entry.path || entry.is_dir)
          // Folders first, then names: the eye finds a folder before a file.
          .sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
            return String(a.name).localeCompare(String(b.name))
          })
        this.currentPath = target
      } catch (ex) {
        console.error('[CoworkingFileBrowser] Error listing files:', ex)
        this.error = ex?.message || 'Could not list the mounted folder'
        this.entries = []
      } finally {
        this.loading = false
      }
    },
    goTo(relativePath) {
      const target = relativePath || ''
      if (target === this.currentPath) return
      this.load(target)
    },
    goUp() {
      const index = this.crumbs.length - 2
      this.goTo(index < 0 ? this.basePath : this.pathUntil(index))
    },
    downloadHref(entry) {
      if (entry.is_dir || !this.api?.files?.getFileDownloadUrl) return '#'
      return this.api.files.getFileDownloadUrl(entry.absolute || this.absolutePath(entry.path))
    },
    formatSize(size) {
      const value = Number(size)
      if (!Number.isFinite(value) || value < 0) return ''
      if (value < 1024) return `${value} B`
      const units = ['KB', 'MB', 'GB', 'TB']
      let scaled = value / 1024
      let index = 0
      while (scaled >= 1024 && index < units.length - 1) {
        scaled /= 1024
        index += 1
      }
      return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[index]}`
    }
  }
}
</script>
