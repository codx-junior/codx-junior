import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// The REAL site config, never a copy of it. If the sidebar changes, these
// assertions follow it; a hardcoded copy would silently stop protecting anything.
import siteConfig from '../../.vitepress/config.mts'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const FEATURES_DIR = join(ROOT, 'features')

interface NavItem {
  text?: string
  link?: string
  items?: NavItem[]
}

interface SiteConfig {
  base?: string
  srcExclude?: string[]
  themeConfig?: {
    nav?: NavItem[]
    sidebar?: Record<string, NavItem[]> | NavItem[]
  }
}

const site = siteConfig as unknown as SiteConfig
const BASE = site.base ?? '/'
const SRC_EXCLUDE: string[] = site.srcExclude ?? []

function toPosix(value: string): string {
  return value.split('\\').join('/')
}

/** Minimal glob -> regexp. Supports `*`, `**` and `?`, which is all `srcExclude` uses. */
function globToRegExp(glob: string): RegExp {
  let out = ''
  for (let i = 0; i < glob.length; i += 1) {
    const char = glob[i]
    if (char === '*') {
      if (glob[i + 1] === '*') {
        out += '.*'
        i += 1
      } else {
        out += '[^/]*'
      }
    } else if (char === '?') {
      out += '[^/]'
    } else {
      out += char.replace(/[.+^${}()|[\]\\]/g, '\\$&')
    }
  }
  return new RegExp(`^${out}$`)
}

/** true when a repo-relative path is excluded from the built site by `srcExclude`. */
function isExcluded(relPath: string): boolean {
  return SRC_EXCLUDE.some((glob) => globToRegExp(glob).test(relPath))
}

function collectLinks(items: NavItem[] | undefined, acc: string[] = []): string[] {
  for (const item of items ?? []) {
    if (item && typeof item.link === 'string') acc.push(item.link)
    if (item && Array.isArray(item.items)) collectLinks(item.items, acc)
  }
  return acc
}

function isInternal(link: string): boolean {
  return !/^[a-z][a-z0-9+.-]*:/i.test(link) && !link.startsWith('#')
}

/** Every internal link declared in `nav` or in any `sidebar` block. */
function declaredLinks(): string[] {
  const themeConfig = site.themeConfig ?? {}
  const sidebar = themeConfig.sidebar
  const sidebarBlocks = Array.isArray(sidebar) ? sidebar : Object.values(sidebar ?? {}).flat()
  return [...collectLinks(themeConfig.nav), ...collectLinks(sidebarBlocks)].filter(isInternal)
}

/** The markdown file a site link resolves to, or null when nothing exists on disk. */
function pageFileFor(link: string): string | null {
  const clean = link.split('#')[0].split('?')[0]
  const withoutBase = BASE !== '/' && clean.startsWith(BASE) ? `/${clean.slice(BASE.length)}` : clean
  const rel = withoutBase.replace(/^\/+/, '').replace(/\/+$/, '')
  const candidates = rel === '' ? ['index.md'] : [`${rel}.md`, `${rel}/index.md`]
  return candidates.find((candidate) => existsSync(join(ROOT, candidate))) ?? null
}

function featurePages(): string[] {
  return readdirSync(FEATURES_DIR)
    .filter((name) => name.endsWith('.md'))
    .map((name) => `features/${name}`)
    .sort()
}

/** Links written in the body of `features/index.md`, resolved to site-root paths. */
function featuresIndexLinks(): string[] {
  const source = readFileSync(join(FEATURES_DIR, 'index.md'), 'utf8')
  const targets = [...source.matchAll(/\]\(([^)\s]+)\)/g)].map((match) => match[1])
  const resolved: string[] = []
  for (const target of targets) {
    if (!isInternal(target)) continue
    const clean = target.split('#')[0].split('?')[0]
    if (clean === '') continue
    if (clean.startsWith('/')) {
      resolved.push(clean)
    } else {
      resolved.push(`/${toPosix(relative(ROOT, resolve(FEATURES_DIR, clean)))}`)
    }
  }
  return resolved
}

/** Pages reachable from the `/features/` sidebar or from `features/index.md`. */
function reachableFeaturePages(): Set<string> {
  const sources = [
    ...declaredLinks().filter((link) => link.startsWith('/features')),
    ...featuresIndexLinks(),
  ]
  return new Set(sources.map(pageFileFor).filter((page): page is string => page !== null))
}

describe('T1 — internal review pages never reach the published build', () => {
  // Regression lock on card 07006e18. If someone deletes `srcExclude`, the two
  // engineering reviews below are published as live URLs and the local search
  // provider indexes them: an unauthenticated-proxy finding becomes public.
  const REVIEW_PAGES = [
    'features/workspaces-container-routing-review.md',
    'features/workspaces-settings-scope-review.md',
  ]

  it('srcExclude is declared at all (canary: an empty list disables every check below)', () => {
    expect(Array.isArray(SRC_EXCLUDE)).toBe(true)
    expect(SRC_EXCLUDE.length, 'srcExclude is empty or missing from .vitepress/config.mts').toBeGreaterThan(0)
  })

  it('both internal review pages are matched by a srcExclude pattern', () => {
    const unprotected = REVIEW_PAGES.filter((page) => !isExcluded(page))
    expect(
      unprotected,
      `these pages are published and indexed by search: ${unprotected.join(', ')}`,
    ).toEqual([])
  })

  it('the matcher is not vacuous: ordinary pages are not treated as excluded', () => {
    expect(isExcluded('features/voice-conversation.md')).toBe(false)
    expect(isExcluded('features/index.md')).toBe(false)
  })
})

describe('T2 — no nav or sidebar link points at a page that does not exist', () => {
  // `ignoreDeadLinks: true` (config.mts:15) was set on purpose for cross-links in
  // page bodies, so `docs:build` cannot fail on a broken nav entry. This is the
  // only automated check that a sidebar line points somewhere real (card 54658755).
  it('parses the real config (canary: a zero-link parse would pass everything below)', () => {
    expect(declaredLinks().length, 'no nav/sidebar link parsed from .vitepress/config.mts').toBeGreaterThan(20)
  })

  it('every declared link resolves to a markdown file on disk', () => {
    const broken = declaredLinks().filter((link) => pageFileFor(link) === null)
    expect(broken, `dead nav/sidebar links: ${broken.join(', ')}`).toEqual([])
  })
})

describe('T3 — every published feature page is reachable', () => {
  // VitePress publishes every .md under the root unless `srcExclude` removes it.
  // A page that is published but in no sidebar and in no index is unlisted
  // documentation: it exists for search engines only (card 07ba6b72).
  it('scans the features directory (canary: an empty scan would pass vacuously)', () => {
    expect(featurePages().length, 'features/*.md not scanned').toBeGreaterThan(20)
  })

  it('every publishable features/*.md is linked from the sidebar or from features/index.md', () => {
    const reachable = reachableFeaturePages()
    const orphans = featurePages().filter(
      (page) => page !== 'features/index.md' && !isExcluded(page) && !reachable.has(page),
    )
    expect(
      orphans,
      `published but unreachable — link them from the sidebar/features/index.md, or exclude them deliberately: ${orphans.join(', ')}`,
    ).toEqual([])
  })
})
