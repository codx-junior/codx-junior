/**
 * Volume mapping rules shared by the coworking session UI.
 *
 * A co-desktop container sees the project through bind mounts. The backend is
 * the only owner of the truth and reports them in `BrowserSession`:
 *
 *  - `shared_folders[]` — every project folder mounted in the container:
 *    `{ id, name, folder, host_path, mount_path, readonly }`, where `folder` is
 *    the **project-relative** folder (`''` is the project root) and
 *    `mount_path` the path inside the container (`/home/<project_name>/<folder>`,
 *    `/shared` or `Z:\` for the Windows/macOS VMs);
 *  - `downloads_path` (host) / `downloads_folder` (project-relative) and
 *    `downloads_mount` (container) — where files saved by the AI/user land.
 *
 * The files API speaks project-relative paths (`''` is the project root), so
 * the UI mostly needs `shared_folders[].folder`. Docker-inspect style binds
 * (`host:container[:mode]`) are still accepted as a fallback, for records
 * written by other tooling.
 *
 * Keeping the rules here (instead of inside one component) means the settings
 * table, the footer folder browser and the volume explorer cannot disagree
 * about which project folder a bind points at.
 */

/** Project-relative folder, without leading/trailing slash. */
export function normalizeFolder(value) {
  return String(value || '')
    .replace(/\\/g, '/')
    .replace(/\/{2,}/g, '/')
    .replace(/^\/+|\/+$/g, '')
}

/** Mirror of the backend rule that builds `/home/<project_name>`. */
export function projectFolderName(projectName) {
  const name = String(projectName || '')
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '')
  return name || 'project'
}

export function isRemoteSession(session) {
  return (session?.kind || 'browser') === 'remote'
}

export function isVmSession(session) {
  const kind = session?.kind
  return kind === 'windows' || kind === 'macos'
}

/** True when *hostPath* lives inside *base* (both absolute). */
function isInside(base, hostPath) {
  const root = String(base || '').replace(/\/+$/, '')
  const target = String(hostPath || '').replace(/\/+$/, '')
  if (!root || !target) return false
  return target === root || target.startsWith(`${root}/`)
}

/** Normalize one `shared_folders` entry of a session record. */
function normalizeSharedFolder(entry) {
  if (!entry || typeof entry !== 'object') return null
  const container = entry.mount_path || entry.mountPath || entry.container
  if (!container) return null
  const folder = normalizeFolder(entry.folder)
  const label = entry.name || (folder ? `/${folder}` : 'Whole project')
  return {
    container,
    project: folder ? `/${folder}` : '/ (whole project)',
    folder,
    hostPath: entry.host_path || entry.hostPath || '',
    label,
    readonly: !!(entry.readonly ?? entry.read_only ?? entry.ro)
  }
}

/**
 * The binds the backend created for the session: `shared_folders` first (the
 * truth), then the downloads bind when it is not already covered by a share,
 * then any docker-inspect style list the caller passed through.
 */
export function reportedMounts(session) {
  if (!session) return []
  const list = []
  const seen = new Set()

  const add = (mapping) => {
    if (!mapping || !mapping.container || seen.has(mapping.container)) return
    seen.add(mapping.container)
    list.push(mapping)
  }

  // 1. Project folders mounted in the container.
  const shared = Array.isArray(session.shared_folders) ? session.shared_folders : []
  shared.forEach(entry => add(normalizeSharedFolder(entry)))

  // 2. The downloads folder: its own bind only when no share already holds it.
  const downloadsFolder = normalizeFolder(session.downloads_folder)
  const downloadsMount = session.downloads_mount
  const covered = list.some(mapping => mapping.container === downloadsMount ||
    (downloadsFolder && mapping.folder === downloadsFolder))
  if (downloadsMount && !covered) {
    add({
      container: downloadsMount,
      project: downloadsFolder ? `/${downloadsFolder}` : downloadsMount,
      folder: downloadsFolder || null,
      hostPath: session.downloads_path || '',
      label: downloadsFolder ? 'Downloads' : downloadsMount,
      readonly: false,
      downloads: true
    })
  }

  // 3. Explicit binds (`host:container[:mode]` / inspect objects).
  const raw = session.mounts || session.volume_mappings || session.binds
  if (Array.isArray(raw)) {
    raw.forEach((entry) => {
      if (typeof entry === 'string') {
        const parts = entry.split(':')
        if (parts.length < 2) return
        add({
          container: parts[1],
          project: parts[0],
          hostPath: parts[0],
          readonly: (parts[2] || '').split(',').includes('ro')
        })
        return
      }
      if (!entry || typeof entry !== 'object') return
      const container = entry.container || entry.target || entry.destination || entry.mount
      const host = entry.project || entry.source || entry.host || entry.origin
      if (!container) return
      add({
        container,
        project: host || '—',
        hostPath: host || '',
        readonly: !!(entry.readonly ?? entry.read_only ?? entry.ro ?? (entry.mode || '').includes('ro'))
      })
    })
  }

  return list
}

/**
 * Normalized `{ container, project, folder, hostPath, readonly }` list for a
 * session: `folder` is the project-relative folder to browse (`''` = root,
 * `null` = unknown, fold the host path instead).
 */
export function resolveVolumeMappings(session) {
  if (!session) return []
  const reported = reportedMounts(session)
  if (reported.length) return reported
  if (isRemoteSession(session)) return []

  // Older VM records keep the shared folder only as the guest mount point.
  const guest = session.guest_share_path
  const downloadsFolder = normalizeFolder(session.downloads_folder)
  if (isVmSession(session) && (guest || session.downloads_mount)) {
    return [{
      container: guest || session.downloads_mount,
      project: downloadsFolder ? `/${downloadsFolder}` : '/ (whole project)',
      folder: downloadsFolder || null,
      hostPath: session.downloads_path || '',
      label: downloadsFolder ? `/${downloadsFolder}` : 'Whole project',
      readonly: !!session.readonly
    }]
  }
  return []
}

/**
 * Project-relative path behind a bind, or null when it cannot be browsed.
 *
 * `shared_folders[].folder` is already project-relative, so it is returned as
 * is. A bind that only carries a host path is folded against the project root;
 * a host path outside the project (another project, a session profile folder)
 * cannot be read by this project's files API and yields `null`.
 */
export function volumePath(mapping, session, { projectName = '', absoluteProjectPath = '' } = {}) {
  if (!mapping || isRemoteSession(session)) return null
  if (typeof mapping.folder === 'string') return mapping.folder

  const base = String(absoluteProjectPath || '').replace(/\/+$/, '')
  const hostRaw = String(mapping.hostPath || '').replace(/\/+$/, '')
  if (hostRaw && isInside(base, hostRaw)) return hostRaw.slice(base.length).replace(/^\/+/, '')

  const raw = String(mapping.project || '').trim()
  if (!raw || raw === '—') return null
  if (isInside(base, raw)) return raw.slice(base.length).replace(/^\/+/, '')

  // Container path of a project folder: `/home/<project_name>[/<folder>]`.
  const home = `/home/${projectFolderName(projectName)}`
  if (raw === home) return ''
  if (raw.startsWith(`${home}/`)) return raw.slice(home.length + 1)
  if (raw === '/' || raw.startsWith('/ ')) return ''
  if (isVmSession(session) && raw.startsWith('/shared')) {
    return raw.slice('/shared'.length).replace(/^\/+/, '')
  }

  // An absolute host path outside the project cannot be browsed from here.
  if (raw.startsWith('/')) return null
  return raw.replace(/^\/+/, '')
}

/**
 * The browsable volumes of a session, ready for the file browser:
 * `[{ path, label, containerPath, readonly }]`.
 */
export function explorableVolumeRoots(session, options = {}) {
  return resolveVolumeMappings(session)
    .map((mapping) => {
      const path = volumePath(mapping, session, options)
      if (path === null) return null
      return {
        path,
        label: mapping.label || (path ? `/${path}` : 'Whole project'),
        containerPath: mapping.container,
        readonly: !!mapping.readonly
      }
    })
    .filter(Boolean)
}
