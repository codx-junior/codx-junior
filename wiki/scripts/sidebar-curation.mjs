#!/usr/bin/env node
/**
 * Public-navigation curation rule for the codx-junior wiki — ONE definition,
 * imported by both the site config and the site guard.
 *
 * Why this module exists: `.vitepress/config.json` is written by the external
 * site builder (it is the file that carries the `last_update` epoch and it is
 * regenerated wholesale), so any hand edit made to it is erased on the next
 * generation — that is how three earlier "delivered" curations evaporated. The
 * rule therefore lives in this hand-written, dependency-free module. The
 * build-time filter (`.vitepress/config.mts`) and the check
 * (`scripts/check-site.mjs`) import the same list, so the guard can never
 * disagree with what the site actually publishes.
 *
 * Nothing here touches the pages themselves: internal artefacts are only
 * UNLINKED from the navigation, their `.md` files stay in the tree and remain
 * reachable by direct URL.
 */

/**
 * File-name suffixes of a raw repository file that the builder turned into a
 * page: `config-json`, `pytest-ini`, `websockify-token-cfg`, `vscode-settings-json`,
 * a HuggingFace blob path, … Those pages are artifacts of the repository, not
 * documentation written for a customer.
 *
 * Kept as data so the rule stays readable and a future format is one entry, not
 * a new rule. Deliberately NOT including `-md`: every page in this site comes
 * from a `.md` file, so that suffix would hide the entire navigation.
 */
const RAW_FILE_SUFFIXES = ['-json', '-cfg', '-ini', '-yaml', '-yml', '-toml', '-txt'];

/** True when `value` ends with one of `suffixes`; a missing value never matches. */
function endsWithAny(value, suffixes) {
  const candidate = typeof value === 'string' ? value : '';
  if (!candidate) return false;
  return suffixes.some((suffix) => candidate.endsWith(suffix));
}

/**
 * Rules identifying a sidebar entry as an internal engineering artefact.
 *
 * `test(link, text)` receives the already-normalised strings of one item (empty
 * string when the field is missing) so a rule can look at either field.
 * `reason` is user-facing: it is printed by the curation report, so it must say
 * why the entry is not product documentation.
 *
 * Ordered most-specific-first: the first match wins, so a page with a named
 * reason (`pytest-ini` is a test-runner config) is not reported under the
 * generic "raw file dump" label.
 */
export const INTERNAL_RULES = [
  {
    id: 'shell-script-page',
    reason: 'shell-script page dump, not product documentation',
    test: (link) => link.endsWith('-sh'),
  },
  {
    id: 'generated-domain-page',
    reason: 'generated per-domain container/scope page',
    test: (link) => link.startsWith('/domains/'),
  },
  {
    id: 'pytest-config',
    reason: 'repo test-runner config, not product docs',
    test: (link, text) => `${link} ${text}`.includes('/development-and-testing/pytest-ini'),
  },
  {
    id: 'qa-defect-note',
    reason: 'internal QA defect write-up',
    test: (link, text) => `${link} ${text}`.includes('/development-and-testing/qa-gate-scope-defect'),
  },
  {
    id: 'index-surgery-runbook',
    reason: 'internal index-surgery runbook',
    test: (link, text) => `${link} ${text}`.includes('/development-and-testing/untrack-wiki-codx-runbook'),
  },
  {
    id: 'captured-scanwatch-log',
    reason: 'captured runtime log',
    test: (link, text) => `${link} ${text}`.includes('/deployment-and-operations/ciso-scripts-scanwatch'),
  },
  {
    id: 'captured-scan-event-log',
    reason: 'captured scan-event log',
    test: (link, text) => `${link} ${text}`.includes('-scan-events-'),
  },
  {
    id: 'control-plane-path',
    reason: 'control plane path leaked into the navigation',
    test: (link, text) => `${link} ${text}`.includes('.codx/'),
  },
  {
    id: 'model-cache-artifact',
    reason: 'vendored model-cache artefact (vLLM/HuggingFace cache path), not product documentation',
    test: (link, text) => `${link} ${text}`.includes('vllm-models-hub'),
  },
  {
    id: 'blob-hash-page',
    reason: 'generated page for a binary blob hash, not product documentation',
    test: (link, text) => `${link} ${text}`.includes('-blobs-'),
  },
  {
    id: 'raw-file-dump',
    reason: 'raw config/generated file dump, not product documentation',
    test: (link, text) => endsWithAny(link || text, RAW_FILE_SUFFIXES),
  },
];

/** Normalise a possibly-missing sidebar field to a string, for safe matching. */
function asText(value) {
  return typeof value === 'string' ? value : '';
}

/**
 * Decide whether ONE sidebar entry is an internal artefact.
 *
 * Returns the matched rule so callers can report WHY an entry was hidden; the
 * first matching rule wins, and the rule list is ordered most-specific-first so
 * the reported reason stays the useful one.
 *
 * @param {{ text?: string, link?: string }} item
 * @returns {{ internal: boolean, reason: string|null, rule: string|null }}
 */
export function isInternalArtifact(item) {
  const link = asText(item && item.link);
  const text = asText(item && item.text);

  for (const rule of INTERNAL_RULES) {
    if (rule.test(link, text)) {
      return { internal: true, reason: rule.reason, rule: rule.id };
    }
  }

  return { internal: false, reason: null, rule: null };
}

/**
 * Curate a sidebar for the public site: drop internal entries, and drop any
 * group (nested sub-groups included) whose `items` ends up empty.
 *
 * Deliberately non-mutating — it returns a filtered copy — because the same
 * generated array is the input of the site config, the guard and the report;
 * mutating it would make one caller's view depend on another's execution order.
 * An empty group is dropped rather than kept as `items: []`, otherwise VitePress
 * renders a heading that opens onto nothing.
 *
 * @param {Array<object>} sidebar
 * @returns {Array<object>}
 */
export function curateSidebar(sidebar) {
  if (!Array.isArray(sidebar)) return [];
  return curateItems(sidebar);
}

/** Recursive worker behind `curateSidebar`: groups are items that hold items. */
function curateItems(items) {
  const kept = [];

  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== 'object') {
      kept.push(item);
      continue;
    }

    if (isInternalArtifact(item).internal) continue;

    if (Array.isArray(item.items)) {
      const children = curateItems(item.items);
      if (children.length === 0) continue; // a group with no surviving child
      kept.push({ ...item, items: children });
      continue;
    }

    kept.push({ ...item });
  }

  return kept;
}

/**
 * Every entry the rule matches, walking nested groups too.
 *
 * Used for the curation report (what was hidden, and why) and by the guard to
 * prove that a curated sidebar has nothing left to hide. Recursion matters: the
 * generated navigation nests sub-groups today and may nest more tomorrow, and a
 * non-recursive collector would silently report "clean" while leaking.
 *
 * @param {Array<object>} sidebar
 * @returns {Array<{ link: string|null, text: string|null, reason: string|null }>}
 */
export function collectInternal(sidebar) {
  const found = [];

  (function walk(items) {
    for (const item of Array.isArray(items) ? items : []) {
      if (!item || typeof item !== 'object') continue;

      const verdict = isInternalArtifact(item);
      if (verdict.internal) {
        found.push({
          link: typeof item.link === 'string' ? item.link : null,
          text: typeof item.text === 'string' ? item.text : null,
          reason: verdict.reason,
        });
      }

      if (Array.isArray(item.items)) walk(item.items);
    }
  })(sidebar);

  return found;
}
