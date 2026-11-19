<template>
  <div class="space-y-2">
    <div class="divider text-[10px] font-semibold uppercase tracking-wide opacity-50 my-1">
      Volume mappings
    </div>

    <!-- Which container holds these binds: the name the infrastructure view
         searches by, plus a shortcut that opens its detail drawer. -->
    <div class="flex items-center gap-2 flex-wrap">
      <span class="text-[10px] font-semibold uppercase tracking-wide opacity-50">Container</span>
      <code v-if="containerName" class="font-mono text-[11px] break-all">{{ containerName }}</code>
      <span v-else class="text-[11px] opacity-60">
        {{ isRemote ? 'remote machine — no container' : 'not started yet' }}
      </span>
      <button
        v-if="containerName"
        class="btn btn-xs btn-ghost gap-1 text-[11px]"
        title="Open this container in the infrastructure view"
        @click="openInInfra"
      >
        <i class="fas fa-cubes"></i>
        Infrastructure
      </button>
    </div>

    <table v-if="mappings.length" class="table table-xs w-full">
      <thead>
        <tr class="text-[10px] uppercase tracking-wide opacity-60">
          <th class="px-1">In container</th>
          <th class="px-1">Project</th>
          <th class="px-1 text-right">Mode</th>
          <th class="px-1 text-right">Files</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="mapping in mappings"
          :key="`${mapping.container}-${mapping.project}`"
          :class="isExplorable(mapping) ? 'cursor-pointer hover' : ''"
          :title="isExplorable(mapping) ? 'Open this volume in the file explorer' : exploreHint"
          @click="openExplorer(mapping)"
        >
          <td class="px-1 font-mono text-[11px] break-all">{{ mapping.container }}</td>
          <td class="px-1 font-mono text-[11px] break-all">{{ mapping.project }}</td>
          <td class="px-1 text-right">
            <span
              class="badge badge-xs"
              :class="mapping.readonly ? 'badge-warning badge-outline' : 'badge-success badge-outline'"
            >{{ mapping.readonly ? 'ro' : 'rw' }}</span>
          </td>
          <td class="px-1 text-right">
            <button
              v-if="isExplorable(mapping)"
              class="btn btn-xs btn-ghost gap-1"
              :aria-label="`Open ${mapping.project} in the file explorer`"
              @click.stop="openExplorer(mapping)"
            >
              <i class="fa-regular fa-folder-open"></i>
              <span class="hidden sm:inline">Files</span>
            </button>
            <span v-else class="text-[10px] opacity-40">—</span>
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
      Click a volume to browse its files.
    </p>

    <!-- File explorer of the clicked volume (same project files, other view) -->
    <CoworkingVolumeExplorer
      v-if="explorer"
      :title="explorer.title"
      :start-path="explorer.path"
      :container-path="explorer.containerPath"
      :readonly="explorer.readonly"
      @close="explorer = null"
    />
  </div>
</template>

<script>
import CoworkingVolumeExplorer from './CoworkingVolumeExplorer.vue'
import { resolveVolumeMappings, volumePath as resolveVolumePath } from './volumeMappings'

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
  components: { CoworkingVolumeExplorer },
  props: {
    session: { type: Object, default: null }
  },
  data() {
    return {
      // Volume opened in the file explorer (`{ path, title, containerPath }`),
      // null while the explorer is closed.
      explorer: null
    }
  },
  computed: {
    /** Absolute path of the project: the base every bind is resolved against. */
    absoluteProjectPath() {
      const root = this.$project?.abs_project_path ||
        this.$storex?.projects?.activeProject?.abs_project_path ||
        ''
      return String(root).replace(/\/+$/, '')
    },
    kind() {
      return this.session?.kind || 'browser'
    },
    /** Docker container name (the id as a fallback): what infra searches by. */
    containerName() {
      return this.session?.container_name ||
        this.session?.containerName ||
        this.session?.docker?.name ||
        this.session?.container_id ||
        ''
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
    /** Normalized container -> project mappings (shared resolution rules). */
    mappings() {
      return resolveVolumeMappings(this.session, { projectName: this.projectName })
    },
    emptyHint() {
      if (this.isRemote) return 'This co-desktop runs on a remote machine: it keeps its own filesystem.'
      return 'This container has only its own binds (profile, downloads): no project folder was mounted. Create a new co-desktop to mount project folders.'
    },
    /** Why a volume cannot be browsed (used as the row tooltip). */
    exploreHint() {
      if (this.isRemote) return 'A remote machine keeps its own filesystem'
      return 'This bind points outside the current project, so its files cannot be browsed from here'
    }
  },
  methods: {
    /**
     * Project-relative path behind a bind, or null when it cannot be browsed.
     *
     * The files API speaks project-relative paths (`''` is the project root),
     * while a bind speaks container paths and a docker inspect speaks host
     * paths. All three are folded into the one the explorer understands.
     */
    volumePath(mapping) {
      if (!mapping || this.isRemote) return null
      const raw = String(mapping.project || '').trim()
      if (!raw || raw === '—') return null
      return resolveVolumePath(mapping, this.session, {
        projectName: this.projectName,
        absoluteProjectPath: this.absoluteProjectPath
      })
    },
    isExplorable(mapping) {
      return this.volumePath(mapping) !== null
    },
    /** Open a volume in the file explorer modal. */
    openExplorer(mapping) {
      const path = this.volumePath(mapping)
      if (path === null) return
      this.explorer = {
        path,
        title: path ? `/${path}` : 'Whole project files',
        containerPath: mapping.container,
        readonly: !!mapping.readonly
      }
    },
    /**
     * Jump to the infrastructure view with this container selected.
     *
     * `InfraView` opens the detail drawer from the `container` query param, so
     * the operator lands on the right container instead of searching the list.
     */
    openInInfra() {
      const id = this.containerName
      if (!id) return
      const projectName = this.$route?.params?.projectName
      const path = projectName ? `/${projectName}/infra` : '/infra'
      this.$router.push({ path, query: { container: id } })
    },

  }
}
</script>
