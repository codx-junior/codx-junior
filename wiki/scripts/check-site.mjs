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
 *  2. The published sidebar keeps the shape VitePress expects:
 *     groups -> non-empty `items` -> items with `text` and `link`.
 *  3. No published `link` is absolute/protocol-relative or escapes the site root
 *     with `..` (the classic way a generated sidebar breaks the build).
 *  4. `docs.json` is valid JSON with the metadata the theme reads.
 *  5. The published sidebar publishes no internal engineering artefact — after
 *     the curation rule of `scripts/sidebar-curation.mjs` is applied, which is
 *     the same module `.vitepress/config.mts` applies at build time, so this
 *     guard and the rendered site cannot disagree about what is internal.
 *
 * "Published" throughout means `curateSidebar(config.sidebar)`: the generated
 * navigation is the builder's output and the site only ever renders the curated
 * view, so checking the raw generated sidebar would bless a navigation no
 * visitor sees.
 *
 * Reported, deliberately not fatal (the first two files are generator output, so
 * a finding there is a curation decision for the owner, not a broken build):
 *  - the curation report: how many internal entries were hidden, and why;
 *  - sidebar links that are duplicated;
 *  - sidebar links with no markdown page (link-integrity evidence without a
 *    VitePress build);
 *  - `docs.json` pages with no markdown file in this tree.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectInternal, curateSidebar } from './sidebar-curation.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CONFIG_FILE = join(ROOT, '.vitepress', 'config.json');
const CONFIG_MTS_FILE = join(ROOT, '.vitepress', 'config.mts');
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

/* ------------------------------------------------------- what the site shows */

/** The navigation the site actually renders (see `.vitepress/config.mts`). */
const published = curateSidebar(sidebar);

/** Flatten every item of a sidebar (groups may nest `items` arbitrarily). */
function collectItems(items, out) {
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    out.push(item);
    if (Array.isArray(item.items)) collectItems(item.items, out);
  }
  return out;
}

/** Count only entries that point somewhere — groups are containers, not items. */
function countLinks(list) {
  return collectItems(Array.isArray(list) ? list : [], []).filter(
    (item) => typeof item.link === 'string',
  ).length;
}

const items = collectItems(published, []);
const links = items.filter((item) => typeof item.link === 'string').map((item) => item.link);

/* ------------------------------------------------------------------- checks */

check('sidebar is a non-empty list of groups with items', () => {
  assert(sidebar !== null, '`sidebar` is missing or is not an array');
  assert(published.length > 0, '`sidebar` is empty after curation');
  for (const [index, group] of published.entries()) {
    assert(group && typeof group === 'object', `sidebar[${index}] is not an object`);
    assert(typeof group.text === 'string' && group.text.trim(), `sidebar[${index}] has no text`);
    assert(Array.isArray(group.items) && group.items.length > 0, `group "${group.text}" has no items`);
  }
});

check('every published sidebar item has a text label and a link', () => {
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

check('published sidebar links are root-relative and stay inside the site', () => {
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

/**
 * Check 5 — the curation must actually hold.
 *
 * This is the check that was missing when the sidebar shipped `pytest.ini`, a
 * security scan log and our own defect write-ups to the public site. The rule is
 * not restated here: it is imported from `scripts/sidebar-curation.mjs`, so the
 * guard tests the very rule the build applies, against the curated view and its
 * nested groups.
 */
check('published sidebar carries no internal engineering artefact', () => {
  assert(sidebar !== null, '`sidebar` is missing or is not an array');
  const leaked = collectInternal(published);
  assert(
    leaked.length === 0,
    `after curation the published sidebar still contains ${leaked.length} internal artefact(s):\n     ` +
      leaked.map((entry) => `${entry.link || entry.text} — ${entry.reason}`).join('\n     '),
  );
});

/**
 * Check 6 — the build must actually apply the rule.
 *
 * Check 5 proves the RULE is correct, not that the site uses it. The defect this
 * guards: `.vitepress/config.mts` passed the raw generated `sidebar` straight
 * into `themeConfig`, so the deployed navigation published every artefact the
 * rule hides while check 5 still printed PASS.
 */
check('build applies the curation rule', () => {
  const source = readFileSync(CONFIG_MTS_FILE, 'utf8');
  assert(
    /sidebar:\s*curateSidebar\s*\(/.test(source),
    '`.vitepress/config.mts` does not wire `sidebar: curateSidebar(sidebar)` into `themeConfig`: ' +
      'the site would publish the raw generated sidebar while this guard reports PASS',
  );
});

/* ----------------------------------------------------- informational reports */

const removed = collectInternal(sidebar);
const beforeLinks = countLinks(sidebar);
const afterLinks = countLinks(published);
const droppedGroups = (Array.isArray(sidebar) ? sidebar.length : 0) - published.length;

console.log('');
console.log(
  `curation report: ${removed.length} internal item(s) removed from the generated sidebar ` +
    `(${beforeLinks} before → ${afterLinks} after), ${droppedGroups} group(s) dropped`,
);
for (const entry of removed.slice(0, 10)) {
  console.log(`  - hidden ${entry.link || entry.text} — ${entry.reason}`);
}
if (removed.length > 10) console.log(`  … and ${removed.length - 10} more`);

const unresolved = links
  .filter((link) => link.startsWith('/') && !link.split('/').includes('..'))
  .filter((link) => !linkTargets(link).some((target) => mdPages.has(target)));

console.log('');
console.log(
  `sidebar: ${published.length} group(s), ${links.length} link(s) | ` +
    `markdown pages: ${mdPages.size}`,
);
if (unresolved.length === 0) {
  console.log('link report: every sidebar link resolves to a markdown page');
} else {
  console.log(`link report: ${unresolved.length} sidebar link(s) have no markdown page:`);
  for (const link of unresolved) console.log(`  - ${link}`);
}

/**
 * The five links a reviewer clicks after a navigation curation change.
 *
 * `/deployment-and-operations/codx-junior` replaced the former
 * `/deployment-and-operations/installer-sh` spot check because every `-sh` page
 * is curated OUT of the public navigation by `scripts/sidebar-curation.mjs`.
 */
const SPOT_CHECK_LINKS = [
  '/home',
  '/setup-and-installation',
  '/deployment-and-operations/codx-junior',
  '/project-overview/license-md',
];

console.log('');
console.log('sidebar groups:');
for (const group of published) {
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
