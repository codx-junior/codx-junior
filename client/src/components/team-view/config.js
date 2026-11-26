// ─────────────────────────────────────────────────────────────────────────────
// Team View – shared metadata & presentation helpers
//
// The Team screen re-frames each *profile* as an AI "team member". It speaks
// manager language (roles, skills) instead of raw technical concepts
// (category, file_match, api_settings).
//
// Teams are persisted per project through the teams API (see
// `src/api/modules/teams.js` → `$project.$api.teams`) and are the SINGLE source
// of truth for teams AND membership. There is no client-side fallback: an empty
// list simply means the project has no teams yet and the UI prompts to create
// one.
//
// Membership is many-to-many: a member (profile name) may appear in the
// `profiles` list of several teams. See `src/views/TeamView.vue`
// (`memberTeamIds`) for the consumer side.
// ─────────────────────────────────────────────────────────────────────────────

// ── Business roles ───────────────────────────────────────────────────────────
// Maps the profile `category` to a friendly label, an icon, a hint and a
// DaisyUI badge class. Class names are full literals so Tailwind detects them.
export const ROLE_META = {
  project: {
    label: 'Team Lead',
    hint: 'Leads and coordinates the team',
    icon: 'fa-solid fa-crown',
    badge: 'badge-primary'
  },
  assistant: {
    label: 'Assistant',
    hint: 'General-purpose helper for everyday work',
    icon: 'fa-solid fa-wand-magic-sparkles',
    badge: 'badge-secondary'
  },
  chat: {
    label: 'Chat Assistant',
    hint: 'Conversational responder for people-facing chats',
    icon: 'fa-regular fa-comments',
    badge: 'badge-info'
  },
  agent: {
    label: 'Agent',
    hint: 'Works autonomously on multi-step tasks',
    icon: 'fa-solid fa-robot',
    badge: 'badge-accent'
  },
  'agent-validator': {
    label: 'Reviewer',
    hint: "Checks and validates other members' work",
    icon: 'fa-solid fa-shield-halved',
    badge: 'badge-success'
  },
  guardian: {
    label: 'Quality Guardian',
    hint: 'Owns the quality of a set of files and reviews every change to them',
    icon: 'fa-solid fa-shield-heart',
    badge: 'badge-success'
  },
  file: {
    label: 'File Specialist',
    hint: 'Expert on a specific set of files',
    icon: 'fa-regular fa-file-code',
    badge: 'badge-warning'
  }
}

export function roleMeta(category) {
  return ROLE_META[category] || ROLE_META.assistant
}

// Default guardrails applied to a member that has none yet. Kept in sync with
// the shape used by EditProfile.vue / the profiles API.
export const EMPTY_GUARDRAILS = {
  enabled: false,
  scope: '',
  scope_keywords: [],
  scope_similarity_threshold: 0.35,
  allowed_tools: [],
  refusal_message: 'I can only answer questions within my configured scope.',
  risk_threshold: 10.0,
  use_llm_classifier: false
}

// ── API <-> local team mapping ───────────────────────────────────────────────
// The backend `Team` model (codx/junior/teams/model.py) matches the local team
// object closely, so the mapping is a thin normalisation.

/** Build a local UI team from an API team. */
export function toLocalTeam(apiTeam) {
  return {
    id: apiTeam.id,
    name: apiTeam.name || 'Team',
    description: apiTeam.description || '',
    color: apiTeam.color || '#6366f1',
    icon: apiTeam.icon || 'fa-solid fa-people-group',
    logo: apiTeam.logo || '',
    profiles: apiTeam.profiles || []
  }
}

/** Serialise a local UI team into the payload accepted by the teams API. */
export function toApiTeam(team) {
  return {
    id: team.id,
    name: team.name,
    description: team.description || '',
    color: team.color || '#6366f1',
    icon: team.icon || '',
    logo: team.logo || '',
    profiles: team.profiles || []
  }
}

// ── Effort tiers ─────────────────────────────────────────────────────────────
// Effort is a PROFILE property, never a model/provider one (see
// `api/codx/junior/ai/smol/effort.py`). The tier is selected by the profile
// names taking part in the turn — `codx-junior` → agile, `codx-junior-max` →
// max, anything else → plain chat — and the profile `effort` block only
// OVERRIDES the tier defaults. The numbers are not duplicated here: the UI shows
// the resolved tier and the overrides the member carries.
export const AGILE_PROFILE = 'codx-junior'
export const MAX_PROFILE = 'codx-junior-max'

export const EFFORT_TIERS = {
  agile: {
    label: 'Agile',
    badge: 'badge-success',
    profile: AGILE_PROFILE,
    hint: 'Default tier: small base budget, bounded course corrections, advisory clock.'
  },
  max: {
    label: 'Max',
    badge: 'badge-error',
    profile: MAX_PROFILE,
    hint: 'Heavy tier: bigger base budget, full-suite validation, long advisory target.'
  },
  default: {
    label: 'Plain chat',
    badge: 'badge-ghost',
    profile: '',
    hint: 'No effort tier profile active: moderate rounds and no course correction.'
  }
}

/**
 * Resolve the effort tier of a set of profile names.
 *
 * Same precedence as the backend `effort_for`: max wins over agile and any
 * other name resolves to the plain-chat policy.
 */
export function effortTier(profileNames = []) {
  const names = (Array.isArray(profileNames) ? profileNames : [profileNames])
    .filter(Boolean)
  if (names.includes(MAX_PROFILE)) return { id: 'max', ...EFFORT_TIERS.max }
  if (names.includes(AGILE_PROFILE)) return { id: 'agile', ...EFFORT_TIERS.agile }
  return { id: 'default', ...EFFORT_TIERS.default }
}

// Editable overrides of the tier defaults, mirroring `ProfileEffortSettings`
// (api/codx/junior/model/profile.py). `control` drives the input: number,
// toggle, text or list. An empty value means "inherit the tier default".
export const EFFORT_FIELDS = [
  {
    key: 'max_iterations',
    label: 'Model rounds',
    group: 'Budget',
    control: 'number',
    hint: 'Rounds allowed in one turn (fallback when the model defines none).'
  },
  {
    key: 'max_tool_calls',
    label: 'Tool calls per round',
    group: 'Budget',
    control: 'number',
    hint: 'Tool calls allowed in each model round.'
  },
  {
    key: 'max_tool_rounds',
    label: 'Sequential tool rounds',
    group: 'Budget',
    control: 'number',
    hint: 'LoopGuard sequential tool rounds.'
  },
  {
    key: 'continue_on_tool_limit',
    label: 'Continue after a tool limit',
    group: 'Course corrections',
    control: 'toggle',
    hint: 'Grant a smaller renewed budget instead of ending the turn.'
  },
  {
    key: 'continuations',
    label: 'Course corrections',
    group: 'Course corrections',
    control: 'number',
    hint: 'How many renewed budgets are granted after a tool-limit stop.'
  },
  {
    key: 'continuation_max_iterations',
    label: 'Rounds per correction',
    group: 'Course corrections',
    control: 'number',
    hint: 'Rounds granted by each course correction.'
  },
  {
    key: 'continuation_max_calls',
    label: 'Tool calls per correction',
    group: 'Course corrections',
    control: 'number',
    hint: 'Tool calls per round granted by each course correction.'
  },
  {
    key: 'target_seconds',
    label: 'Advisory target (seconds)',
    group: 'Clock',
    control: 'number',
    hint: 'Advisory target: the turn is never stopped by it.'
  },
  {
    key: 'wall_clock_seconds',
    label: 'Wall clock (seconds)',
    group: 'Clock',
    control: 'number',
    hint: 'Target or deadline, depending on the next switch.'
  },
  {
    key: 'stop_on_wall_clock',
    label: 'Enforce the wall clock',
    group: 'Clock',
    control: 'toggle',
    hint: 'When on, the wall clock is a real deadline (opt-in).'
  },
  {
    key: 'validation',
    label: 'Validation breadth',
    group: 'Validation',
    control: 'text',
    hint: 'What the turn must validate before delivering (e.g. targeted+qa_review_changes).'
  },
  {
    key: 'tool_allowlist',
    label: 'Tool allowlist',
    group: 'Validation',
    control: 'list',
    hint: 'Comma separated tool names. Empty means every enabled tool.'
  }
]

/**
 * Short effort summary of a single member, used by the cards so the tier is
 * visible without opening the member drawer.
 *
 * `visible` is false for a normal member with no overrides, so the card stays
 * clean; a tier profile (codx-junior / codx-junior-max) always shows its tier.
 */
export function effortSummary(member) {
  const tier = effortTier([member?.name])
  // The team view links a profile to a NAMED effort (Global Settings owns the
  // values), so the linked name is what the card shows.
  const linked = (member?.effort_name || '').trim()
  if (linked) {
    return {
      ...tier,
      id: linked,
      overrides: 0,
      visible: true,
      label: linked,
      badge: 'badge-secondary',
      hint: `Linked to the '${linked}' effort`,
      tooltip: `Effort: ${linked} (Global Settings → Efforts)`
    }
  }
  const overrides = Object.values(member?.effort || {})
    .filter(value => value !== null && value !== undefined && value !== '')
    .length
  const isTierProfile = tier.id !== 'default'
  return {
    ...tier,
    overrides,
    visible: isTierProfile || overrides > 0,
    label: isTierProfile ? tier.label : `Effort +${overrides}`,
    tooltip: isTierProfile
      ? `${tier.label} tier — ${tier.hint}`
      : `${overrides} effort override(s) on the plain-chat defaults`
  }
}

// ── Presentation helpers ─────────────────────────────────────────────────────
// Same robohash/gravatar fallback used by ProfileCard / EditProfile.
export function avatarUrl(member) {
  if (member?.avatar) return member.avatar
  const seed = encodeURIComponent(member?.name || 'member')
  return `https://gravatar.com/avatar/baa8db8ab2afb7ababc235269e762662?s=200&d=robohash&r=${seed}`
}

// Made with ❤️ by codx-junior