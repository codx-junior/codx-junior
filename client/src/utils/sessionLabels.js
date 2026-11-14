/**
 * User-facing labels of the co-desktop concept.
 *
 * The concept used to be called "computer sessions" and, for a while, "Machines".
 * It is now called **"Coworking"**: a desktop (real machine, VM or browser)
 * that the user and the AI share. The section/navigation entry is `section`
 * ("Coworking"), one entry is `item` ("Co-desktop"), and `live` ("Live
 * session") is reserved for the running / handoff state of a co-desktop the user
 * has to look at.
 *
 * EVERY user-facing string that names the concept must come from this module so
 * that a future rename is one edit here instead of a tree-wide search and
 * replace. It holds display strings only: never use these values as ids, route
 * names, API/model fields, store keys or i18n keys — those keep their current
 * names on purpose (backwards compatibility).
 *
 * Placeholders in `{name}` form are filled in by `label(key, vars)`.
 */

export const SESSION_LABELS = Object.freeze({
  // ── Section / navigation ────────────────────────────────────────────────
  section: 'Coworking',
  sectionHint: 'Your co-desktops and their live screens',

  // ── One item of the section ─────────────────────────────────────────────
  item: 'Co-desktop',
  items: 'Coworking',
  itemShort: 'Co-desktop',
  untitled: 'Co-desktop',
  newItem: 'New co-desktop',
  create: 'New co-desktop',
  createError: 'Could not create the co-desktop',

  // ── Running / handoff state (never the section name) ────────────────────
  live: 'Live session',
  needsYou: 'Needs you',
  needsYouHint: 'This co-desktop is waiting for you',
  running: 'Running',
  stopped: 'Stopped',

  // ── Toast / notification strings (they name the concept too) ────────────
  noticeStopped: 'Co-desktop stopped',
  noticeRestarted: 'Co-desktop restarted',
  noticeDeleted: 'Co-desktop deleted',

  // ── List states ─────────────────────────────────────────────────────────
  loading: 'Loading co-desktops…',
  empty: 'No co-desktops yet',
  emptyHint: 'Create a co-desktop to get a desktop with the project files inside',
  loadError: 'Could not load co-desktops',
  loadErrorHint: 'The list of co-desktops could not be fetched',
  retry: 'Retry loading co-desktops',
  retryShort: 'Retry',
  reload: 'Reload co-desktops',
  noSelection: 'Select a co-desktop',

  // ── Settings of one co-desktop ──────────────────────────────────────────
  settingsTitle: 'Co-desktop settings',
  nameField: 'Co-desktop name',
  remoteSettingsHint: 'Remote co-desktops keep their own hardware. Only the name and connection settings can change.',

  // ── Actions (use the {name} placeholder) ────────────────────────────────
  selectItem: 'Select co-desktop {name}',
  runItem: 'Run co-desktop {name}',
  editItem: 'Edit co-desktop {name}',
  deleteItem: 'Delete co-desktop {name}',
  deleteTitle: 'Delete co-desktop',
  deleteConfirm: "Delete co-desktop '{name}'?",
  deleteHint: 'The session, its profile and its desktop state will be removed. This cannot be undone.',

  // ── Views ───────────────────────────────────────────────────────────────
  singleHint: 'Single view: one co-desktop',
  singleHintLong: 'One co-desktop at a time',
  gridHint: 'Grid view: several co-desktops at once',
  gridHintLong: 'Watch several co-desktops at once',
  wallTitle: 'Coworking on the wall',
  wallPick: 'Select the co-desktops on the wall',
  wallPickHint: 'Choose the co-desktops on the wall',
  wallPickShort: 'Coworking',
  gridPick: 'Pick the co-desktops you want to watch at the same time',
  gridEmpty: 'No co-desktops on the wall',
  gridWall: 'Wall of co-desktops',

  // ── Image library of the concept ────────────────────────────────────────
  imageLibrary: 'Co-desktop image library',
  browseImages: 'Browse the co-desktop image library'
})

/**
 * Reads a label by key and fills its `{name}` placeholders.
 *
 * Falls back to the key itself so a typo or a not-yet-migrated caller never
 * renders `undefined` in the UI.
 *
 * @param {string} key key of {@link SESSION_LABELS}
 * @param {Record<string, string|number>} [vars] values for the placeholders
 * @returns {string}
 */
export function label(key, vars) {
  const text = SESSION_LABELS[key] ?? key
  if (!vars) return text
  return String(text).replace(/\{(\w+)\}/g, (match, name) => (
    vars[name] === undefined || vars[name] === null ? match : String(vars[name])
  ))
}

/**
 * Short form of {@link label} for call sites that name the module in the call.
 *
 * @param {string} key key of {@link SESSION_LABELS}
 * @param {Record<string, string|number>} [vars] values for the placeholders
 * @returns {string}
 */
export function sessionLabel(key, vars) {
  return label(key, vars)
}

/**
 * True when a co-desktop is waiting for the user (handoff, login, MFA, …).
 *
 * Reads only the `needs_user` flag of the session, so it is safe to call from
 * templates and from the attention/blinking UI: guarded against null input and
 * free of side effects.
 *
 * @param {{ needs_user?: boolean }} [session]
 * @returns {boolean}
 */
export function sessionNeedsUser(session) {
  return !!session?.needs_user
}

export default SESSION_LABELS
