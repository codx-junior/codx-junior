#!/usr/bin/env node
/**
 * Site integrity checks for the codx-junior wiki.
 *
 * Runnable with no installed dependency and no shell wrapper:
 *
 *   npm test        ->  node scripts/check-site.mjs
 *
 * Why it exists: this project had no runnable suite at all (`python3 -m pytest`
 * dies before collecting because `pytest` is not importable, and `package.json`
 * declared no `test` script). Everything below was previously only reachable
 * through `npm run docs:build`, which needs a full VitePress install.
 *
 * What it guards
 *  1. `.vitepress/config.json` is valid JSON — a stray comma takes the docs
 *     build down, and that file is rewritten by generators.
 *  2. The sidebar keeps the shape VitePress expects:
 *     groups -> non-empty `items` -> items with `text` and `link`.
 *  3. No sidebar `link` is absolute/protocol-relative or escapes the site root
 *     with `..` (the classic way a generated sidebar breaks the build).
 *  4. `docs.json` is valid JSON with the metadata the theme reads.
 *  5. The public sidebar publishes no internal engineering artefact — defect
 *     write-ups, runbooks, repo test config and captured runtime logs are not
 *     product documentation (curation rule of the docs navigation).
 *
 * Reported, deliberately not fatal (both files are generator output, so a
 * finding here is a curation decision for the owner, not a broken build):
 *  - sidebar links that are duplicated;
 *  - sidebar links with no markdown page (link-integrity evidence without a
 *    VitePress build);
 *  - `docs.json` pages with no markdown file in this tree.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CONFIG_FILE = join(ROOT, '.vitepress', 'config.json');
const DOCS_FILE = join(ROOT, 'docs.json');

/* ------------------------------------------------------------------ helpers */

let failures = 0;

function check(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failures += 1;
    console.log(`FAIL ${name}`);
    console.log(`     ${error.message}`);
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

/** Every markdown page of the site, as site-relative paths without extension. */
function collectMarkdownPages() {
  const pages = new Set();
  const skip = new Set(['node_modules', 'dist', 'cache']);

  (function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue;
      if (skip.has(entry.name)) continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.name.endsWith('.md')) continue;
      if (!statSync(full).isFile()) continue;
      const sitePath = relative(ROOT, full).split(sep).join('/');
      pages.add(sitePath.slice(0, -'.md'.length));
    }
  })(ROOT);

  return pages;
}

/** Candidate site paths for a sidebar link (`/a/b` -> `a/b`, `a/b/index`). */
function linkTargets(link) {
  const clean = link
    .split(/[?#]/)[0]
    .replace(/^\/+/, '')
    .replace(/\/+$/, '')
    .replace(/\.html?$/, '')
    .replace(/\.md$/, '');
  if (!clean) return ['index'];
  return [clean, `${clean}/index`];
}

/* --------------------------------------------------------------- read input */

const mdPages = collectMarkdownPages();

let config;
check('.vitepress/config.json is valid JSON', () => {
  config = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
});

const sidebar = config && Array.isArray(config.sidebar) ? config.sidebar : null;

/** Flatten every item of the sidebar (groups may nest `items` arbitrarily). */
function collectItems(items, out) {
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    out.push(item);
    if (Array.isArray(item.items)) collectItems(item.items, out);
  }
  return out;
}

const items = sidebar ? collectItems(sidebar, []) : [];
const links = items.filter((item) => typeof item.link === 'string').map((item) => item.link);

/* ------------------------------------------------------------------- checks */

check('sidebar is a non-empty list of groups with items', () => {
  assert(sidebar !== null, '`sidebar` is missing or is not an array');
  assert(sidebar.length > 0, '`sidebar` is empty');
  for (const [index, group] of sidebar.entries()) {
    assert(group && typeof group === 'object', `sidebar[${index}] is not an object`);
    assert(typeof group.text === 'string' && group.text.trim(), `sidebar[${index}] has no text`);
    assert(Array.isArray(group.items) && group.items.length > 0, `group "${group.text}" has no items`);
  }
});

check('every sidebar item has a text label and a link', () => {
  assert(items.length > 0, 'sidebar contains no items');
  for (const [index, item] of items.entries()) {
    if (Array.isArray(item.items) && typeof item.link !== 'string') continue; // pure sub-group
    assert(typeof item.text === 'string' && item.text.trim(), `item #${index} has no text`);
    assert(typeof item.link === 'string' && item.link.trim(), `item "${item.text}" has no link`);
  }
});

const duplicateLinks = (() => {
  const seen = new Set();
  const duplicates = new Set();
  for (const link of links) {
    if (seen.has(link)) duplicates.add(link);
    seen.add(link);
  }
  return [...duplicates];
})();

check('sidebar links are root-relative and stay inside the site', () => {
  const offenders = [];
  for (const link of links) {
    if (!link.startsWith('/')) offenders.push(`${link} (not root-relative)`);
    else if (/^\/\//.test(link)) offenders.push(`${link} (protocol-relative)`);
    else if (link.split('/').includes('..')) offenders.push(`${link} (escapes the site root)`);
  }
  assert(offenders.length === 0, offenders.join(', '));
});

let docsData = null;
check('docs.json is valid JSON with the expected metadata', () => {
  assert((docsData = JSON.parse(readFileSync(DOCS_FILE, 'utf8'))), 'docs.json is not an object');
  assert(typeof docsData.version === 'number', 'docs.json has no numeric `version`');
  assert(
    docsData.project && typeof docsData.project.title === 'string',
    'docs.json has no project.title',
  );
});

const missingDocsPages = (() => {
  const pages =
    docsData && docsData.pages && typeof docsData.pages === 'object'
      ? Object.values(docsData.pages)
      : [];
  const missing = new Set();
  for (const page of pages) {
    const path =
      page && typeof page.path === 'string'
        ? page.path.replace(/^\/+/, '').replace(/\.md$/, '')
        : '';
    if (path && !mdPages.has(path) && !mdPages.has(`${path}/index`)) missing.add(path);
  }
  return [...missing];
})();

/* --------------------------------------------------- non-fatal link report */

/**
 * Fragments that identify internal engineering artefacts. Their targets are
 * generated from defect notes, runbooks, repo config and captured logs, so they
 * change on every regeneration; the fragment is matched against both `text` and
 * `link` of every sidebar item. Keep the reason beside the rule.
 */
const INTERNAL_NAV_PATTERNS = [
  { needle: '/development-and-testing/pytest-ini', reason: 'repo test-runner config, not product docs' },
  {
    needle: '/development-and-testing/qa-gate-scope-defect',
    reason: 'internal QA defect write-up',
  },
  {
    needle: '/development-and-testing/untrack-wiki-codx-runbook',
    reason: 'internal index-surgery runbook',
  },
  {
    needle: '/deployment-and-operations/ciso-scripts-scanwatch',
    reason: 'captured runtime log',
  },
  { needle: '-scan-events-', reason: 'captured scan-event log' },
  { needle: '.codx/', reason: 'control plane path leaked into the navigation' },
];

check('public sidebar publishes no internal engineering artefact', () => {
  assert(sidebar !== null, '`sidebar` is missing or is not an array');
  const violations = [];
  for (const item of items) {
    const haystack = `${item.text || ''} ${item.link || ''}`;
    for (const { needle, reason } of INTERNAL_NAV_PATTERNS) {
      if (!haystack.includes(needle)) continue;
      violations.push(`${item.link || item.text} — ${reason}`);
    }
  }
  assert(
    violations.length === 0,
    `the public sidebar still publishes ${violations.length} internal artefact(s):\n     ` +
      violations.join('\n     '),
  );
});

const unresolved = links
  .filter((link) => link.startsWith('/') && !link.split('/').includes('..'))
  .filter((link) => !linkTargets(link).some((target) => mdPages.has(target)));

console.log('');
console.log(
  `sidebar: ${sidebar ? sidebar.length : 0} group(s), ${links.length} link(s) | ` +
    `markdown pages: ${mdPages.size}`,
);
if (unresolved.length === 0) {
  console.log('link report: every sidebar link resolves to a markdown page');
} else {
  console.log(`link report: ${unresolved.length} sidebar link(s) have no markdown page:`);
  for (const link of unresolved) console.log(`  - ${link}`);
}

/** The five links a reviewer clicks after a navigation curation change. */
const SPOT_CHECK_LINKS = [
  '/home',
  '/setup-and-installation',
  '/deployment-and-operations/installer-sh',
  '/integrations/dsh-harness',
  '/project-overview/license-md',
];

console.log('');
console.log('sidebar groups:');
for (const group of sidebar || []) {
  const count = Array.isArray(group.items) ? collectItems(group.items, []).length : 0;
  console.log(`  - ${group.text} (${count} item(s))`);
}

console.log('spot check:');
for (const link of SPOT_CHECK_LINKS) {
  const resolved = linkTargets(link).find((target) => mdPages.has(target));
  const state = resolved ? `OK  -> ${resolved}.md` : '404 -> no markdown page';
  const where = links.includes(link) ? '' : '  (not in the sidebar)';
  console.log(`  ${state}  ${link}${where}`);
}

if (duplicateLinks.length === 0) {
  console.log('duplicate report: no sidebar link appears twice');
} else {
  console.log(`duplicate report: ${duplicateLinks.length} sidebar link(s) appear more than once:`);
  for (const link of duplicateLinks) console.log(`  - ${link}`);
}

if (missingDocsPages.length === 0) {
  console.log('docs.json report: every registered page has a markdown file in this tree');
} else {
  console.log(
    `docs.json report: ${missingDocsPages.length} registered page(s) have no markdown file here`,
  );
}

console.log('');
if (failures === 0) {
  console.log('PASS — all site integrity checks passed');
  process.exit(0);
}
console.log(`FAIL — ${failures} check(s) failed`);
process.exit(1);
