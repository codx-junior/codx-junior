// ─────────────────────────────────────────────────────────────────────────────
// RELEASE FEATURE GATING
//
// The image ships every surface; the install-time profile decides which ids
// are exposed (see `store/features.js` + `GET /api/features`).
//
// Launch surfaces must be filtered in ONE place. A missing id means "always
// available" (Home, New chat, Tasks, Files, Git, Settings, …). The 0.0.1
// `core` profile leaves the feature ids below inactive, so their links and
// routes never appear in the first release.
// ─────────────────────────────────────────────────────────────────────────────

/** Map an app key (config/apps.js) to the install feature that gates it. */
export const APP_FEATURES = Object.freeze({
  // Core shell surfaces (active in the 0.0.1 `core` profile).
  home: 'workspace-shell',
  projects: 'projects',
  chat: 'chats',
  'file-explorer': 'file-explorer',
  'git-view': 'git-review',
  knowledge: 'knowledge',
  knowledge_settings: 'knowledge',
  wiki_settings: 'knowledge',
  settings: 'settings',
  'global-settings': 'settings',
  account: 'auth',
  // Task surfaces ship with the core board/tasks feature.
  'task-list': 'kanban',
  tasks: 'kanban',
  planning: 'kanban',
  'board-management': 'kanban',
  calendar: 'calendar',
  // Harness MODE is the New chat destination (always available).
  ciso: 'company-security',
  // Human teams, meetings and the project channel are post-0.0.1 surfaces.
  messenger: 'teams',
  meet: 'meetings',
  'meet-summary': 'meetings',
  team: 'profiles',
  'team-channel': 'teams',
  'team-dm': 'teams',
  'team-media-library': 'teams',
  channel: 'channel-creator',
  // Privileged / privileged-runtime surfaces.
  workspaces: 'workspaces',
  workspace: 'workspaces',
  'browser-sessions': 'browser-use',
  'browser-viewer': 'browser-use',
  infra: 'infra',
  // Documentation and observability.
  wiki: 'wiki',
  docs: 'wiki',
  metrics: 'analytics',
  analytics: 'analytics',
  'api-monitoring': 'logs',
  'log-viewer': 'logs',
  'chat-logs': 'analytics',
  'decision-model-logs': 'analytics'
})

/**
 * Whether a feature id is exposed by this installation.
 *
 * Fails OPEN: before the profile is loaded, or when `/api/features` failed, the
 * legacy product is kept (the store mirrors that contract). This prevents a
 * transient API error from emptying the navigation.
 *
 * @param {object|null|undefined} features `$storex.features`
 * @param {string|null|undefined} featureId
 * @returns {boolean}
 */
export function isFeatureEnabled(features, featureId) {
  if (!featureId) return true
  if (!features) return true
  if (typeof features.isActive !== 'function') return true
  return features.isActive(featureId) !== false
}

/**
 * Whether an app key may be shown/opened on this installation.
 *
 * @param {object|null|undefined} features `$storex.features`
 * @param {string} appKey
 * @returns {boolean}
 */
export function isAppEnabled(features, appKey) {
  return isFeatureEnabled(features, APP_FEATURES[appKey])
}

export default APP_FEATURES
