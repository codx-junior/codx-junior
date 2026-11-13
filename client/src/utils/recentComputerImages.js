/**
 * Recently used computer images.
 *
 * The image catalog lives on the server (`GET /api/computers/images`), but
 * "what did I start last time" is a per-browser preference: it is kept in
 * `localStorage` so the new-session wizard can offer the last choices in one
 * click, without a round-trip and without a backend endpoint. Entries are kept
 * small on purpose (no blob/preview URLs, which are revoked per page load).
 */

const STORAGE_KEY = 'codx.computers.recentImages'
const MAX_ENTRIES = 8

/**
 * Guess the library provider from an image reference.
 *
 * Sessions only keep the image string, so remembering a computer that is
 * already running still has to send a provider the backend understands.
 */
export function providerFromImage(image) {
  const reference = String(image || '').trim().toLowerCase()
  if (reference.startsWith('codx-saved/')) return 'saved'
  if (reference.startsWith('kasmweb/')) return 'kasmweb'
  if (reference.startsWith('lscr.io/') || reference.startsWith('linuxserver/')) return 'linuxserver'
  return 'custom'
}

/** Human name for an image reference (`lscr.io/linuxserver/webtop:debian-kde`). */
export function imageName(image) {
  const reference = String(image || '').trim()
  const last = reference.split('/').pop() || reference
  return last.split(':')[0] || reference
}

/** Image tag (`latest` when the reference does not carry one). */
export function imageTag(image) {
  const reference = String(image || '').trim()
  if (reference.startsWith('codx-saved/')) return 'saved'
  const tag = reference.split(':').pop()
  return tag && tag !== reference ? tag : 'latest'
}

/** Entries the browser remembers, newest first (empty when unavailable). */
export function loadRecentImages() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const entries = raw ? JSON.parse(raw) : []
    return Array.isArray(entries) ? entries.filter(entry => entry?.image) : []
  } catch (error) {
    // No localStorage (private mode, quota, corrupted payload): no recents.
    return []
  }
}

/**
 * Remember an image so it shows up in the wizard next time.
 *
 * @param {Object} entry - `{ image, name, provider, tag, live_view, kind, library_item_id }`.
 * @returns {Array} the stored list, newest first.
 */
export function rememberRecentImage(entry) {
  const image = String(entry?.image || '').trim()
  if (!image) return loadRecentImages()
  const stored = {
    id: `recent:${image}`,
    image,
    name: entry.name || imageName(image),
    provider: entry.provider || providerFromImage(image),
    tag: entry.tag || imageTag(image),
    live_view: entry.live_view || '',
    kind: entry.kind || 'linux',
    library_item_id: entry.library_item_id || null,
    saved_at: new Date().toISOString()
  }
  const others = loadRecentImages().filter(item => item.image !== image)
  const entries = [stored, ...others].slice(0, MAX_ENTRIES)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch (error) {
    // Storing is best-effort: the wizard still works without recents.
  }
  return entries
}
