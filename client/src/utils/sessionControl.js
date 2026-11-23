/**
 * Pure helpers for shared control of a co-working session.
 *
 * The backend is authoritative: it stores the lease (`controller`,
 * `control_owner`), the pending request and the participant list.  These
 * helpers only translate that state into the label/action a viewer renders, so
 * the same rules are used by the desktop viewer, the browser viewer and the
 * tests.
 *
 * Control modes
 * -------------
 * - `auto`: clicking the live view takes the lease immediately.
 * - `request`: clicking asks the current owner, who must approve it.  When the
 *   AI owns the lease no human can approve, so the backend grants it at once.
 *
 * Made with ❤️ by codx-junior
 */

export const AUTO_CONTROL = 'auto'
export const REQUEST_CONTROL = 'request'

export const CONTROL_MODE_OPTIONS = [
  {
    id: AUTO_CONTROL,
    label: 'Auto',
    description: 'Anyone can click and take control; the previous driver becomes an observer.'
  },
  {
    id: REQUEST_CONTROL,
    label: 'Request',
    description: 'The current driver must approve before another participant can control.'
  }
]

/** Identity of the viewer: `{ username, participantId, isAdmin }`. */
export function viewerIdentity(user = null, participantId = '') {
  return {
    username: user?.username || '',
    participantId: participantId || '',
    isAdmin: user?.role === 'admin'
  }
}

/** Participant list of a session (always an array). */
export function sessionParticipants(session) {
  const list = Array.isArray(session?.participants) ? session.participants : []
  return list.filter(participant => participant && (participant.id || participant.username))
}

/** Display name of one participant. */
export function participantLabel(participant) {
  if (!participant) return 'Guest'
  return participant.name || participant.username || 'Guest'
}

/** Two-letter initials for the avatar bubble. */
export function participantInitials(participant) {
  const name = participantLabel(participant).trim()
  if (!name) return '?'
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length > 1) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function ownerMatches(owner, identity) {
  if (!owner) return false
  if (identity?.username) {
    // Authenticated accounts own the lease by account, not by tab id.
    return owner === identity.username
  }
  return !!identity?.participantId && owner === identity.participantId
}

/** True when this viewer currently owns the human control lease. */
export function isControlOwner(session, identity) {
  if (!session || session.controller !== 'user') return false
  return ownerMatches(session.control_owner, identity)
}

/**
 * True when this viewer may send input right now.
 *
 * The AI lease and the paused state do not block a human (they can take over);
 * another human's lease does, unless an admin acts.
 */
export function mayControlSession(session, identity) {
  if (!session) return false
  if (identity?.isAdmin) return true
  if (session.controller !== 'user' || !session.control_owner) return true
  return isControlOwner(session, identity)
}

/** True when the pending request belongs to this viewer. */
export function hasPendingControlRequest(session, identity) {
  const request = session?.control_request
  if (!request) return false
  if (identity?.username) return request.username === identity.username
  return !!identity?.participantId && request.participant_id === identity.participantId
}

/** True when this viewer can approve/deny the pending request. */
export function canResolveControlRequest(session, identity) {
  if (!session?.control_request) return false
  if (hasPendingControlRequest(session, identity)) return false
  return !!identity?.isAdmin || isControlOwner(session, identity)
}

/**
 * Action the primary control button should perform.
 *
 * @returns {{ action: ?string, label: string, hint: string, disabled: boolean }}
 *   `action` is one of `take_control`, `request_control`, `release` or null.
 */
export function controlActionFor(session, identity) {
  if (!session) {
    return { action: null, label: 'No session', hint: '', disabled: true }
  }
  if (isControlOwner(session, identity)) {
    return {
      action: 'release',
      label: 'Give back to AI',
      hint: 'You are driving this machine',
      disabled: false
    }
  }
  if (hasPendingControlRequest(session, identity)) {
    const owner = session.control_owner_name || session.control_owner || 'the owner'
    return {
      action: null,
      label: 'Request pending…',
      hint: `Waiting for ${owner} to approve`,
      disabled: true
    }
  }
  const anotherOwner = session.controller === 'user' && !!session.control_owner
  if (session.control_mode === REQUEST_CONTROL && anotherOwner) {
    return {
      action: 'request_control',
      label: 'Request control',
      hint: `${session.control_owner_name || session.control_owner} is driving`,
      disabled: false
    }
  }
  return {
    action: 'take_control',
    label: 'Take control',
    hint: anotherOwner
      ? `${session.control_owner_name || session.control_owner} is driving`
      : 'The AI is driving',
    disabled: false
  }
}

/** Short status line for the session header. */
export function controlSummary(session, identity) {
  if (!session) return { label: 'No session', tone: 'muted', ownerName: '' }
  if (isControlOwner(session, identity)) {
    return { label: 'You drive', tone: 'owner', ownerName: '' }
  }
  if (session.controller === 'user' && session.control_owner) {
    const ownerName = session.control_owner_name || session.control_owner
    return { label: `${ownerName} drives`, tone: 'other', ownerName }
  }
  if (session.controller === 'none') {
    return { label: 'Paused', tone: 'muted', ownerName: '' }
  }
  return { label: 'AI drives', tone: 'ai', ownerName: '' }
}

/**
 * Message shown on the live-view overlay for a viewer without the lease.
 *
 * @returns {{ interactive: boolean, label: string, hint: string, action: ?string }}
 */
export function overlayControlFor(session, identity) {
  if (!session) return { interactive: false, label: '', hint: '', action: null }
  if (isControlOwner(session, identity) || identity?.isAdmin) {
    return { interactive: false, label: '', hint: '', action: null }
  }
  if (hasPendingControlRequest(session, identity)) {
    const owner = session.control_owner_name || session.control_owner || 'the owner'
    return {
      interactive: true,
      label: 'Request sent',
      hint: `Waiting for ${owner} to approve`,
      action: null
    }
  }
  const anotherOwner = session.controller === 'user' && !!session.control_owner
  if (session.control_mode === REQUEST_CONTROL && anotherOwner) {
    return {
      interactive: true,
      label: 'Request control',
      hint: `${session.control_owner_name || session.control_owner} is driving — click to ask`,
      action: 'request_control'
    }
  }
  return {
    interactive: true,
    label: 'Take control',
    hint: anotherOwner
      ? `${session.control_owner_name || session.control_owner} is driving — click to take over`
      : 'Click anywhere on the screen to start driving',
    action: 'take_control'
  }
}

/** Normalize an API session payload (defensive for older backends). */
export function normalizeControlState(session) {
  if (!session) return session
  return {
    ...session,
    control_mode: session.control_mode === REQUEST_CONTROL ? REQUEST_CONTROL : AUTO_CONTROL,
    participants: sessionParticipants(session),
    control_request: session.control_request || null
  }
}
