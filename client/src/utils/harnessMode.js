/**
 * Effort tiers of harness mode (the only place the tier/profile mapping lives).
 *
 * A harness session is executed by a normal codx-junior profile. "Effort" is
 * therefore the profile tier that runs the turn, and it must be selectable both
 * when the ticket is written and while the session is being continued: the
 * same `codx-junior`/`codx-junior-max` pair owns the budget, reasoning effort
 * and continuation policy.
 *
 * @typedef {{id: string, label: string, shortLabel: string, hint: string, icon: string}} HarnessTier
 */

/** Built-in harness tiers, in display order. */
export const HARNESS_TIERS = Object.freeze([
  {
    id: 'standard',
    label: 'Standard mode',
    shortLabel: 'Standard',
    hint: 'codx-junior · high effort · one turn',
    icon: 'fa-solid fa-bolt'
  },
  {
    id: 'deep',
    label: 'Deep work',
    shortLabel: 'Deep',
    hint: 'codx-junior-max · max effort · long tasks',
    icon: 'fa-solid fa-mountain-sun'
  }
])

/** Profile that executes each tier. `codx-junior-max` inherits `codx-junior`. */
export const HARNESS_TIER_PROFILES = Object.freeze({
  standard: 'codx-junior',
  deep: 'codx-junior-max'
})

const HARNESS_PROFILES = new Set(Object.values(HARNESS_TIER_PROFILES))

/**
 * Profile name of a tier.
 *
 * @param {string} mode Tier id (`standard`/`deep`).
 * @returns {string} `codx-junior` or `codx-junior-max`.
 */
export function tierProfileName(mode) {
  return HARNESS_TIER_PROFILES[mode] || HARNESS_TIER_PROFILES.standard
}

/**
 * Resolve the tier of a persisted session from its profile list.
 *
 * @param {string[]} profiles `session.profiles`.
 * @returns {'standard'|'deep'} Tier id.
 */
export function modeForProfiles(profiles) {
  return (Array.isArray(profiles) ? profiles : []).includes(HARNESS_TIER_PROFILES.deep)
    ? 'deep'
    : 'standard'
}

/**
 * Profiles to send with a follow-up turn.
 *
 * The selected tier leads (a max tier already inherits the base profile); the
 * session's other profiles stay linked. Any previous harness tier is replaced,
 * so switching Standard → Deep (or back) never composes both tiers.
 *
 * @param {string[]} profiles Profiles currently stored on the session.
 * @param {string} mode Selected tier id.
 * @returns {string[]} Profiles for the next user message.
 */
export function followUpProfiles(profiles, mode) {
  const tier = tierProfileName(mode)
  const rest = (Array.isArray(profiles) ? profiles : [])
    .map(profile => String(profile || '').trim())
    .filter(profile => profile && !HARNESS_PROFILES.has(profile) && profile !== tier)
  return [tier, ...rest.filter((profile, index) => rest.indexOf(profile) === index)]
}

/**
 * Human hint of a tier (the profile/effort summary shown next to the control).
 *
 * @param {string} mode Tier id.
 * @returns {string} One-line hint.
 */
export function tierHint(mode) {
  return HARNESS_TIERS.find(tier => tier.id === mode)?.hint || HARNESS_TIERS[0].hint
}

/**
 * Icon of a tier.
 *
 * @param {string} mode Tier id.
 * @returns {string} Font Awesome class.
 */
export function tierIcon(mode) {
  return HARNESS_TIERS.find(tier => tier.id === mode)?.icon || HARNESS_TIERS[0].icon
}
