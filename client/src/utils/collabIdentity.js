/**
 * Stable id for this browser tab/device.
 *
 * The backend uses it (header `x-participant-id`) to tell two tabs of the
 * same account apart, so two windows do not fight over the control lease and
 * the participant list shows both.  `sessionStorage` scopes the id to the tab
 * and survives reloads of that tab; a private/embedded context that rejects
 * storage still gets a process-local id.
 *
 * Made with ❤️ by codx-junior
 */

const STORAGE_KEY = 'codx.browser.participantId'

let memoryId = null

function randomId() {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
    if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
      const bytes = crypto.getRandomValues(new Uint8Array(16))
      return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
    }
  } catch (error) {
    // fall through to the time-based id
  }
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Return the participant id of this tab.
 *
 * @returns {string} Stable id for the lifetime of the tab.
 */
export function getParticipantId() {
  if (memoryId) return memoryId
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      const stored = window.sessionStorage.getItem(STORAGE_KEY)
      if (stored) {
        memoryId = stored
        return memoryId
      }
      memoryId = randomId()
      window.sessionStorage.setItem(STORAGE_KEY, memoryId)
      return memoryId
    }
  } catch (error) {
    // localStorage/sessionStorage can be unavailable in private/embedded contexts.
  }
  memoryId = randomId()
  return memoryId
}

/**
 * Identity payload sent with presence/control calls.
 *
 * @param {Object} [user] - Logged-in user (`username`, `avatar`, `role`).
 * @returns {{ participant_id: string, display_name: string, avatar: ?string }}
 */
export function getParticipantIdentity(user = null) {
  return {
    participant_id: getParticipantId(),
    display_name: user?.name || user?.username || 'Guest',
    avatar: user?.avatar || null
  }
}

/** Test helper: forget the cached id. */
export function resetParticipantId() {
  memoryId = null
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      window.sessionStorage.removeItem(STORAGE_KEY)
    }
  } catch (error) {
    // ignore
  }
}
