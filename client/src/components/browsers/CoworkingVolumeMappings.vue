<template>
  <div class="space-y-2">
    <div class="divider text-[10px] font-semibold uppercase tracking-wide opacity-50 my-1">
      Volume mappings
    </div>

    <table v-if="mappings.length" class="table table-xs w-full">
      <thead>
        <tr class="text-[10px] uppercase tracking-wide opacity-60">
          <th class="px-1">In container</th>
          <th class="px-1">Project</th>
          <th class="px-1 text-right">Mode</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="mapping in mappings" :key="`${mapping.container}-${mapping.project}`">
          <td class="px-1 font-mono text-[11px] break-all">{{ mapping.container }}</td>
          <td class="px-1 font-mono text-[11px] break-all">{{ mapping.project }}</td>
          <td class="px-1 text-right">
            <span
              class="badge badge-xs"
              :class="mapping.readonly ? 'badge-warning badge-outline' : 'badge-success badge-outline'"
            >{{ mapping.readonly ? 'ro' : 'rw' }}</span>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="text-[11px] opacity-60">
      {{ emptyHint }}
    </p>

    <p class="text-[10px] opacity-50">
      The container is created with these binds: they cannot change while it lives.
      Create a new co-desktop to mount different folders.
    </p>
  </div>
</template>

<script>
/**
 * Read-only view of the volume mappings shared by a co-desktop container.
 *
 * Two sources, in this order:
 *
 *  1. `session.mounts` — when the backend reports the binds it actually
 *     created (docker inspect). That is the truth, so it wins.
 *  2. The session settings (`share_project` / `share_folders` / `readonly` /
 *     `downloads_folder`) resolved with the SAME rule the new-session dialog
 *     applies (`/home/<project_name>` and `/shared` for VMs), so the settings
 *     panel explains what the container sees even for older sessions.
 */
export default {
  name: 'CoworkingVolumeMappings',
  props: {
    session: { type: Object, default: null }
  },
  computed: {
    kind() {
      return this.session?.kind || 'browser'
    },
    isRemote() {
      return this.kind === 'remote'
    },
    isVm() {
      return this.kind === 'windows' || this.kind === 'macos'
    },
    readonly() {
      return !!this.session?.readonly
    },
    projectName() {
      return this.$project?.project_name ||
        this.$storex?.projects?.activeProject?.project_name ||
        'project'
    },
    /** Mirror of the backend rule that builds `/home/<project_name>`. */
    projectFolderName() {
      const name = String(this.projectName || '')
        .replace(/[^A-Za-z0-9._-]+/g, '-')
        .replace(/^[-._]+|[-._]+$/g, '')
      return name || 'project'
    },
    homePath() {
      return `/home/${this.projectFolderName}`
    },
    /** Normalized container -> project mappings. */
    mappings() {
      if (!this.session) return []
      const reported = this.reportedMounts()
      if (reported.length) return reported
      if (this.isRemote) return []

      if (this.isVm) {
        const relative = this.normalizeFolder(this.session.downloads_folder)
        return [{
          container: this.kind === 'windows' ? 'Z:\\' : '/shared',
          project: relative ? `/${relative}` : '/ (whole project)',
          readonly: false
        }]
      }

      const folders = (this.session.share_folders || [])
        .map(folder => this.normalizeFolder(folder))
        .filter(Boolean)

      const list = []
      if (this.session.share_project) {
        list.push({ container: this.homePath, project: '/ (whole project)', readonly: this.readonly })
      } else {
        folders.forEach(folder => {
          list.push({
            container: `${this.homePath}/${folder}`,
            project: `/${folder}`,
            readonly: this.readonly
          })
        })
      }

      // A read-only session sends the downloads to their own bind.
      const downloads = this.normalizeFolder(this.session.downloads_folder)
      if (this.readonly && downloads) {
        list.push({ container: '/downloads', project: `/${downloads}`, readonly: false })
      }
      return list
    },
    emptyHint() {
      if (this.isRemote) return 'This co-desktop runs on a remote machine: it keeps its own filesystem.'
      return 'No project folder is mounted: the session cannot read or write project files.'
    }
  },
  methods: {
    /** Accepts the shapes a backend may use for a bind. */
    reportedMounts() {
      const raw = this.session?.mounts || this.session?.volume_mappings || this.session?.binds
      if (!Array.isArray(raw)) return []
      return raw
        .map(entry => {
          if (typeof entry === 'string') {
            // "host:container[:mode]"
            const parts = entry.split(':')
            if (parts.length < 2) return null
            return {
              project: parts[0],
              container: parts[1],
              readonly: (parts[2] || '').split(',').includes('ro')
            }
          }
          if (!entry || typeof entry !== 'object') return null
          const container = entry.container || entry.target || entry.destination || entry.mount
          const project = entry.project || entry.source || entry.host || entry.origin
          if (!container) return null
          return {
            container,
            project: project || '—',
            readonly: entry.readonly ?? entry.read_only ?? entry.ro ?? (entry.mode || '').includes('ro')
          }
        })
        .filter(Boolean)
    },
    normalizeFolder(value) {
      return String(value || '')
        .replace(/\\/g, '/')
        .replace(/\/{2,}/g, '/')
        .replace(/^\/+|\/+$/g, '')
    }
  }
}
</script>
