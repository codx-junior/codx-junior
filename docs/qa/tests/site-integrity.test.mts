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

// ---------------------------------------------------------------------------
// T4 — route claims (card 49007737)
// ---------------------------------------------------------------------------
//
// 97583327: `features/browser-use.md` claimed an `/api/computers` route that the
// published route table does not contain. The prose was corrected, but nothing
// guarded it, so a later docs edit could reintroduce the false claim and stay green.
//
// The oracle is the docs' own REST tables: a markdown row whose first cell names an
// HTTP method and whose route cell holds a `/api/...` path — or a path relative to the
// `... are under \`/api/x\`` sentence that introduces the table. A *claim* is a
// `METHOD /api/...` pair written outside a table: inline code, prose or a curl line.

const ROUTE_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']

/**
 * A table cell that only names HTTP methods and their separators: `GET`, `GET/POST/DELETE`,
 * `POST|PUT`. A cell that carries a path (`POST /initiate`) is a route cell, not a method cell.
 */
const METHODS_ONLY = new RegExp('^[\\s`]*(?:(?:' + ROUTE_METHODS.join('|') + ')[\\s`,/|]*)+$')

interface RouteClaim {
  page: string
  method: string
  route: string
  text: string
}

/** `/api/computers/{id}/vnc?ticket=…` -> `/api/computers/{}/vnc`. Path params become `{}`. */
function normalizeRoute(raw: string): string {
  const clean = raw
    .replace(/`/g, '')
    .trim()
    .split('#')[0]
    .split('?')[0]
    .replace(/<[^>]*>/g, '{}')
    .replace(/\{[^}]*\}/g, '{}')
    .replace(/[.,;:]+$/, '')
    .replace(/\/+$/, '')
  return clean
}

function routeSegments(route: string): string[] {
  return route.split('/').filter((segment) => segment !== '')
}

/**
 * Segment-wise comparison of a claim against a published route (or a waiver).
 * A path parameter published by the docs (`{}`) absorbs the literal the prose uses
 * (`/api/browsers/{id}/cost` vs `/api/browsers/<id>/cost`), but a `{}` written in a
 * *claim* never matches a different published resource: `/api/computers/{id}/recording`
 * must not be satisfied by `/api/computers/library/{item}`.
 */
function segmentsMatch(claimed: string[], published: string[]): boolean {
  if (claimed.length !== published.length) return false
  return claimed.every(
    (segment, index) => segment === published[index] || published[index] === '{}',
  )
}

/**
 * A claim is declared when a published route matches it segment for segment, or when it
 * is exactly one segment deeper/shallower than a published route (a concrete action of a
 * published resource, e.g. `/recording/start` under a published `/recording`). A deeper
 * gap is not evidence: `/api/computers` being published does not cover
 * `/api/computers/{id}/recording/start`, which is the 97583327 defect.
 */
function isDeclared(route: string, declared: string[]): boolean {
  const claim = routeSegments(route)
  return declared.some((candidate) => {
    const published = routeSegments(candidate)
    const gap = claim.length - published.length
    if (gap === 0) return segmentsMatch(claim, published)
    if (gap === 1) return segmentsMatch(claim.slice(0, published.length), published)
    if (gap === -1) return segmentsMatch(claim, published.slice(0, claim.length))
    return false
  })
}

/**
 * Routes the docs claim but deliberately do not publish in a REST table. Every entry
 * carries a reason; the route waives everything below it. Keep this list short — an
 * entry a table could carry is a hole in the guard.
 */
const UNPUBLISHED_ROUTE_WAIVERS: Array<{ route: string; reason: string }> = [
  { route: '/api/users/login', reason: 'auth entry point, prose only in features/api.md' },
  { route: '/api/settings', reason: 'project settings object, narrated in features/slack-whatsapp-integration.md' },
  { route: '/api/global', reason: 'global settings object, narrated in features/slack-whatsapp-integration.md' },
  { route: '/api/profiles', reason: 'per-profile voice endpoints, narrated in features/profiles.md (no table)' },
  { route: '/api/health', reason: 'wiki/theme health probe (features/wiki.md), not a product route' },
  { route: '/api/site', reason: 'wiki/theme sitemap feed (features/wiki.md), not a product route' },
  { route: '/api/page', reason: 'wiki/theme page index (features/wiki.md), not a product route' },
  { route: '/api/assistant', reason: 'wiki AI assistant (features/wiki.md), not a product route' },
  { route: '/api/traefik', reason: 'Traefik config feed, infrastructure endpoint (features/workspaces.md)' },
  { route: '/api/kanban', reason: 'board search endpoint, narrated in features/kanban.md' },
  {
    route: '/api/chats/message',
    reason:
      'chat send endpoint narrated in features/slack-whatsapp-integration.md; features/kanban.md publishes only /api/chats/archive and /api/chats/restore',
  },
  {
    route: '/api/chats/tasks',
    reason:
      'create-task-from-chat endpoint narrated in features/ciso.md; no page publishes a chats route table',
  },
  { route: '/api/tasks', reason: 'boards + teams snapshot, narrated in features/kanban.md' },
  { route: '/api/teams', reason: 'team generation proposal, narrated in features/autonomous-agents.md' },
  {
    route: '/api/companies',
    reason:
      'company shell: /security is served by the CISO router and /actors by the actors router; the company-spaces table only declares space/file routes',
  },
  { route: '/api/external/settings', reason: 'external-actors settings, narrated in features/external-actors.md' },
]

function isWaived(route: string): boolean {
  const claim = routeSegments(route)
  return UNPUBLISHED_ROUTE_WAIVERS.some((waiver) => {
    const waived = routeSegments(waiver.route)
    return waived.length <= claim.length && segmentsMatch(claim.slice(0, waived.length), waived)
  })
}

const CLAIM_PATTERN = new RegExp(
  `((?:(?:${ROUTE_METHODS.join('|')})\\b[\\s|/,]*)+)(\\/api\\/[^\\s\`\"'()<>,;]+)`,
  'g',
)

/** `METHOD /api/...` pairs written outside a table row. */
function claimsInSource(page: string, source: string): RouteClaim[] {
  const claims: RouteClaim[] = []
  for (const line of source.split('\n')) {
    if (line.trimStart().startsWith('|')) continue // a table row is a declaration, not a claim
    for (const match of line.matchAll(CLAIM_PATTERN)) {
      const route = normalizeRoute(match[2])
      if (!route.startsWith('/api/')) continue
      const methods = [...match[1].matchAll(/\b(GET|POST|PUT|PATCH|DELETE)\b/g)].map((m) => m[1])
      for (const method of methods) claims.push({ page, method, route, text: match[0].trim() })
    }
  }
  return claims
}

const ROUTE_TOKEN = /\/[^\s|\`",)'\]]+/g

/** Routes declared by the REST tables of one page. */
function declaredRoutesIn(source: string): string[] {
  const declared: string[] = []
  let prefix = ''
  for (const line of source.split('\n')) {
    const under = line.match(/`(\/api\/[^`]*)`/)
    if (!line.trimStart().startsWith('|')) {
      if (under && /\bunder\b/i.test(line)) prefix = normalizeRoute(under[1])
      continue
    }
    const cells = line
      .split('|')
      .map((cell) => cell.trim())
      .filter((cell) => cell !== '')
    if (cells.length < 2) continue
    if (!ROUTE_METHODS.some((method) => new RegExp(`\\b${method}\\b`).test(cells[0]))) continue
    // The route lives in the first cell that is not a bare method list, so all these
    // shapes are parsed: `| `GET` | `/api/x` |`, `| `GET /api/x` |` and the
    // multi-method cell `| `GET/POST/DELETE` | `/api/x` |` (browser recording/scripts).
    for (const cell of cells.filter((cell) => !METHODS_ONLY.test(cell))) {
      for (const token of cell.match(ROUTE_TOKEN) ?? []) {
        const route = token.startsWith('/api/')
          ? normalizeRoute(token)
          : prefix
            ? normalizeRoute(`${prefix}/${token.replace(/^\/+/, '')}`)
            : ''
        if (route.startsWith('/api/')) declared.push(route)
      }
    }
  }
  return declared
}

/** Every route declared by the REST tables of the published feature pages. */
function publishedRoutes(): string[] {
  const routes = new Set<string>()
  for (const page of featurePages()) {
    if (isExcluded(page)) continue
    for (const route of declaredRoutesIn(readFileSync(join(ROOT, page), 'utf8'))) routes.add(route)
  }
  return [...routes]
}

/** Every `METHOD /api/...` claim written in the published feature pages. */
function featureRouteClaims(): RouteClaim[] {
  const claims: RouteClaim[] = []
  for (const page of featurePages()) {
    if (isExcluded(page)) continue
    claims.push(...claimsInSource(page, readFileSync(join(ROOT, page), 'utf8')))
  }
  return claims
}

function uncovered(claims: RouteClaim[], declared: string[]): RouteClaim[] {
  return claims.filter((claim) => !isDeclared(claim.route, declared) && !isWaived(claim.route))
}

describe('T4 — every route claimed in the feature pages is published in a route table', () => {
  // ignoreDeadLinks + no route oracle: a page could claim an endpoint that no table
  // declares and both `docs:build` and the assertions above stayed green (97583327).
  it('scans route claims (canary: a zero-claim scan would pass vacuously)', () => {
    const claims = featureRouteClaims()
    expect(claims.length, 'no `METHOD /api/...` claim found in features/*.md').toBeGreaterThan(30)
    const pages = new Set(claims.map((claim) => claim.page))
    expect(pages.size, 'route claims found in too few pages — the scan is broken').toBeGreaterThan(10)
  })

  it('parses the published route tables (canary: an empty parse would pass everything)', () => {
    const declared = publishedRoutes()
    expect(declared.length, 'no `/api/...` row parsed from the features/*.md REST tables').toBeGreaterThan(80)
    expect(declared, 'the computers table is not being parsed').toContain('/api/computers/{}/action')
    expect(declared, 'relative tables lost their `/api/...` prefix').toContain('/api/meets/initiate')
  })

  it('every claimed route is declared in a published route table (or in a documented waiver)', () => {
    const declared = publishedRoutes()
    const missing = uncovered(featureRouteClaims(), declared).map(
      (claim) => `${claim.page}: ${claim.method} ${claim.route} (\`${claim.text}\`)`,
    )
    expect(
      missing,
      `route claims with no published table entry — declare the route in a REST table of the owning page, or add a reasoned waiver: ${missing.join(' | ')}`,
    ).toEqual([])
  })

  it('fails on the pre-fix 97583327 wording (quoted verbatim below)', () => {
    // Verbatim shape of the sentence that shipped green: browser sub-routes claimed
    // under the `/api/computers` prefix, which the published table does not contain.
    const preFix = [
      'All computer session routes are mirrored under `/api/computers`:',
      '`POST /api/computers/{id}/recording/start` and `POST /api/computers/{id}/recording/stop`.',
    ].join('\n')
    expect(uncovered(claimsInSource('fixture', preFix), publishedRoutes()).map((claim) => claim.route)).toEqual([
      '/api/computers/{}/recording/start',
      '/api/computers/{}/recording/stop',
    ])
  })

  it('accepts the corrected wording (positive control: the guard is not unconditional)', () => {
    const corrected =
      'Every computer session also exposes `POST /api/computers/{id}/screenshot` ' +
      'and `GET /api/computers/{id}/cost`; `/api/browsers` is a backwards-compatible alias.'
    expect(uncovered(claimsInSource('fixture', corrected), publishedRoutes())).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// T5 — the Docker Hub image stays documented
// ---------------------------------------------------------------------------
//
// Install pulls the published image instead of compiling. An image tag that no
// page documents (or a repository rename that leaves the docs describing the old
// coordinates) is an install path nobody can debug. These assertions tie the page
// to the release workflow, the installer defaults and the publish script, so a
// rename has to touch the documentation in the same change.

const REPO_ROOT = resolve(ROOT, '..')
const DOCKER_HUB_PAGE = 'docker-hub.md'

/** The image coordinate the release workflow publishes, e.g. `org/repo`. */
function releaseImage(): string {
  const workflow = readFileSync(join(REPO_ROOT, '.github', 'workflows', 'release.yml'), 'utf8')
  const match = workflow.match(/^\s*IMAGE:\s*([^\s#]+)\s*$/m)
  expect(match, 'release.yml no longer declares IMAGE').not.toBeNull()
  if (match === null) throw new Error('release.yml no longer declares IMAGE')
  return match[1]
}

describe('T5 — the Docker Hub image stays documented', () => {
  it('release.yml declares a repository coordinate (canary: the parse works)', () => {
    expect(releaseImage()).toMatch(/^[a-z0-9_.-]+\/[a-z0-9_.-]+$/)
  })

  it('the Docker Hub page exists and is reachable from nav or sidebar', () => {
    expect(existsSync(join(ROOT, DOCKER_HUB_PAGE))).toBe(true)
    expect(declaredLinks()).toContain('/docker-hub')
    expect(pageFileFor('/docker-hub')).toBe(DOCKER_HUB_PAGE)
  })

  it('the page names the exact repository published by CI', () => {
    const page = readFileSync(join(ROOT, DOCKER_HUB_PAGE), 'utf8')
    expect(page).toContain(releaseImage())
    expect(page).toContain('DOCKERHUB.md')
  })

  it('the installer and publish script default to the same repository', () => {
    const image = releaseImage()
    const [namespace, repository] = image.split('/')
    if (!namespace || !repository) throw new Error(`invalid image coordinate: ${image}`)
    const installer = readFileSync(join(REPO_ROOT, 'codx-junior-installer', 'install.sh'), 'utf8')
    expect(installer).toContain(`DEFAULT_IMAGE="${image}:latest"`)

    const publish = readFileSync(join(REPO_ROOT, 'scripts', 'dockerhub_publish.sh'), 'utf8')
    expect(publish).toContain(`DOCKERHUB_NAMESPACE:-${namespace}`)
    expect(publish).toContain(`IMAGE_NAME:-${repository}`)
  })

  it('the page documents the tags the install and release paths use', () => {
    const page = readFileSync(join(ROOT, DOCKER_HUB_PAGE), 'utf8')
    for (const tag of ['latest', 'vX.Y.Z', 'dind', 'dev']) {
      expect(page, `tag ${tag} is no longer documented in docs/${DOCKER_HUB_PAGE}`).toContain(tag)
    }
  })

  it('the Docker Hub listing source exists and describes the image', () => {
    const listing = readFileSync(join(REPO_ROOT, 'DOCKERHUB.md'), 'utf8')
    expect(listing).toContain(releaseImage())
    expect(listing).toContain('Quick start')
    for (const tag of ['latest', 'vX.Y.Z', 'dind']) {
      expect(listing, `tag ${tag} is missing from DOCKERHUB.md`).toContain(tag)
    }
  })

  it('the Docker Hub page workflow publishes that listing', () => {
    const workflow = readFileSync(
      join(REPO_ROOT, '.github', 'workflows', 'dockerhub-page.yml'),
      'utf8',
    )
    expect(workflow).toContain('peter-evans/dockerhub-description@v5')
    expect(workflow).toContain('readme-filepath: ./DOCKERHUB.md')
    expect(workflow).toContain(releaseImage())
    expect(workflow).toContain('workflow_dispatch')
  })

  it('the manual fallback script targets the same repository', () => {
    const image = releaseImage()
    const [namespace, repository] = image.split('/')
    if (!namespace || !repository) throw new Error(`invalid image coordinate: ${image}`)
    const script = readFileSync(join(REPO_ROOT, 'scripts', 'dockerhub_description.sh'), 'utf8')
    expect(script).toContain(`DOCKERHUB_NAMESPACE:-${namespace}`)
    expect(script).toContain(`IMAGE_NAME:-${repository}`)
  })
})
