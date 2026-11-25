#!/usr/bin/env node
/**
 * commit-slices — commit the pending client tree in coherent, revertible slices.
 *
 * Why this file exists: the agent harness has no git CLI and no shell (cards
 * 3a86fba9 / ae7df213), so a commit can only be produced by one auditable
 * command that the owner runs. This driver keeps that command honest:
 *
 *   - it stages BY PATH only; the whole tree is never `git add -A`-ed,
 *   - it makes exactly one commit per slice, in dependency order,
 *   - it prints every changed file that belongs to no slice instead of
 *     inventing a home for it (card ae7df213, rule 6),
 *   - it refuses a slice map with duplicate or overlapping paths, because an
 *     overlapping map cannot produce revertible commits.
 *
 * Usage (from `client/`, or anywhere — the driver resolves the git root):
 *   node tools/commit-slices.mjs             # dry run, prints the plan
 *   node tools/commit-slices.mjs --apply     # stage by path and commit
 *   node tools/commit-slices.mjs --strict    # exit 1 when leftovers exist
 *
 * The slice map is the measurement of 2026-11-24 (developer shift 12). At that
 * date the efforts increment, the `profiles.delete` envelope fix and the
 * efforts/response-options/voice tests are ALREADY at HEAD; what the tree
 * still carries is the company theme refactor, the planning date-range helpers
 * and the kanban follow-up fixes. The card's original six slices were written
 * against an older tree and no longer match it.
 */

import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Absolute path of the `client/` project this driver commits. */
export const CLIENT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/**
 * The slices, smallest and safest first. A path rule ending in `/` matches
 * every file under that directory; any other rule matches exactly one file.
 * Order matters: the first slice whose rules match a file owns it.
 */
export const SLICES = [
  {
    id: 'test-runner',
    message: 'test: expose the client node test suite as npm test',
    paths: ['package.json']
  },
  {
    id: 'planning-range',
    message: 'feat(planning): add task date range helpers and range filters',
    paths: [
      'src/utils/taskDateRange.js',
      'src/utils/gridPreview.js',
      'tests/taskDateRange.test.mjs',
      'tests/gridPreview.test.mjs',
      'src/components/kanban/board/PlanningRangeFilter.vue',
      'src/components/browsers/ComputerGridTile.vue',
      'src/store/planning.js',
      'src/views/CalendarView.vue',
      'src/views/GlobalTasksView.vue'
    ]
  },
  {
    id: 'company-theme',
    message: 'feat(company): move the company workspace onto the shared theme',
    paths: [
      'src/assets/',
      'src/components/company/',
      'src/components/ThemeSelector.vue',
      'src/utils/theme.js',
      'src/views/CompanyView.vue',
      'src/views/BoardManagementView.vue',
      'src/api/modules/companies.js',
      'src/store/company.js',
      'src/store/ui.js',
      'src/router/index.ts',
      'src/App.vue',
      'tests/company-first-routing.test.mjs',
      'tests/company-activity-followup.test.mjs',
      'tests/company-project-scope.test.mjs',
      'tests/company-theme.test.mjs'
    ]
  },
  {
    id: 'company-shell',
    message: 'feat(company): gate the shell on the company context',
    paths: [
      'src/components/ProjectDropdown.vue',
      'src/components/layout/AppsSection.vue',
      'src/components/mobile/MobileMenu.vue',
      'src/components/workspaces/'
    ]
  },
  {
    id: 'kanban-fixes',
    message: 'fix(kanban): correct task settings, responsibilities and follow-up',
    paths: [
      'src/components/kanban/TaskSettings.vue',
      'src/components/kanban/BoardResponsibilityModal.vue',
      'src/components/kanban/board/FollowupView.vue',
      'src/store/project.js'
    ]
  },
  {
    id: 'task-ref-tooling',
    message: 'tools: validate kanban task references and commit slices by path',
    paths: [
      'tools/validate_task_refs.py',
      'tests/test_validate_task_refs.py',
      'tools/commit-slices.mjs',
      'tests/commitSlices.test.mjs'
    ]
  }
]

/**
 * Turn a repository-root-relative git path into a path relative to `client/`.
 * `git status` reports paths from the git root, which is the parent of this
 * project, so every comparison against the slice map needs the prefix removed.
 */
export function toClientPath(gitPath, prefix = '') {
  const normalised = String(gitPath || '').replace(/\\/g, '/')
  const at = String(prefix || '').replace(/\\/g, '/')
  if (at && normalised.startsWith(at)) return normalised.slice(at.length)
  return normalised
}

/**
 * Parse `git status --porcelain` (v1) into `{ status, path }` entries.
 * Handles the rename form `R  old -> new` (the destination is the path that
 * must be staged) and git's quoted paths.
 */
export function parsePorcelain(stdout) {
  const changes = []
  for (const raw of String(stdout || '').split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (!line.trim()) continue
    const status = line.slice(0, 2).trim() || '??'
    let file = line.slice(3)
    if (file.includes(' -> ')) file = file.split(' -> ').pop()
    if (file.startsWith('"') && file.endsWith('"')) file = file.slice(1, -1)
    changes.push({ status, path: file })
  }
  return changes
}

/** True when a slice path rule covers a client-relative path. */
export function matchesRule(rule, clientPath) {
  if (rule.endsWith('/')) return clientPath.startsWith(rule)
  return clientPath === rule
}

/**
 * Assign every change to its slice. Files that no slice claims are returned as
 * `leftovers` — they are never committed and never silently dropped.
 */
export function classify(changes, slices = SLICES, prefix = '') {
  const assigned = new Map(slices.map(slice => [slice.id, []]))
  const leftovers = []
  for (const change of changes) {
    const clientPath = toClientPath(change.path, prefix)
    const owner = slices.find(slice => slice.paths.some(rule => matchesRule(rule, clientPath)))
    if (!owner) {
      leftovers.push(clientPath)
      continue
    }
    assigned.get(owner.id).push({ status: change.status, gitPath: change.path, clientPath })
  }
  return { assigned, leftovers }
}

/**
 * Build the commit plan: ordered commits that actually have files, plus the
 * leftovers. An empty slice is dropped instead of producing an empty commit.
 */
export function buildPlan(changes, slices = SLICES, prefix = '') {
  const { assigned, leftovers } = classify(changes, slices, prefix)
  const commits = slices
    .map(slice => {
      const entries = assigned.get(slice.id) || []
      return {
        id: slice.id,
        message: slice.message,
        files: entries.map(entry => entry.gitPath),
        clientFiles: entries.map(entry => entry.clientPath)
      }
    })
    .filter(commit => commit.files.length > 0)
  return { commits, leftovers }
}

/**
 * Validate a slice map: every slice needs an id and a message, no slice may be
 * empty, and no path may be claimed by two slices (an overlap makes the commits
 * impossible to revert independently).
 */
export function validateSlices(slices = SLICES) {
  const problems = []
  const exact = new Map()
  const prefixes = []

  for (const slice of slices) {
    if (!slice.id) problems.push('a slice is missing its id')
    if (!slice.message) problems.push(`${slice.id || '(no id)'}: missing commit message`)
    if (!Array.isArray(slice.paths) || slice.paths.length === 0) {
      problems.push(`${slice.id || '(no id)'}: no paths`)
      continue
    }
    for (const rule of slice.paths) {
      if (rule.endsWith('/')) {
        prefixes.push({ rule, id: slice.id })
        continue
      }
      if (exact.has(rule)) problems.push(`${rule}: claimed by ${exact.get(rule)} and ${slice.id}`)
      else exact.set(rule, slice.id)
    }
  }

  for (const { rule, id } of prefixes) {
    for (const [file, owner] of exact) {
      if (owner !== id && file.startsWith(rule)) {
        problems.push(`${file}: ${owner} lists it but ${id} claims all of ${rule}`)
      }
    }
  }

  const overlapping = prefixes.filter((candidate, index) =>
    prefixes.some((other, otherIndex) => otherIndex !== index && other.id !== candidate.id &&
      (candidate.rule.startsWith(other.rule) || other.rule.startsWith(candidate.rule)))
  )
  for (const entry of overlapping) problems.push(`${entry.rule}: overlaps another slice's prefix`)

  return problems
}

/** Render a plan for a human: one block per commit, then the leftovers. */
export function formatPlan(plan) {
  const lines = []
  plan.commits.forEach((commit, index) => {
    lines.push(`${index + 1}. ${commit.message}`)
    lines.push(`   slice ${commit.id} — ${commit.files.length} file(s)`)
    for (const file of commit.clientFiles) lines.push(`   + ${file}`)
  })
  if (plan.leftovers.length) {
    lines.push('')
    lines.push(`Left out of every commit (${plan.leftovers.length} file(s), unassigned):`)
    for (const file of plan.leftovers) lines.push(`   ? ${file}`)
  }
  return lines.join('\n')
}

function git(cwd, ...args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' })
}

function main(argv = process.argv.slice(2)) {
  const apply = argv.includes('--apply')
  const strict = argv.includes('--strict')

  const problems = validateSlices()
  if (problems.length) {
    console.error('commit-slices: the slice map is not revertible')
    for (const problem of problems) console.error(` - ${problem}`)
    return 1
  }

  const root = git(CLIENT_ROOT, 'rev-parse', '--show-toplevel').trim()
  const prefix = git(CLIENT_ROOT, 'rev-parse', '--show-prefix').trim()
  const status = git(root, 'status', '--porcelain', '--', prefix || '.')

  const plan = buildPlan(parsePorcelain(status), SLICES, prefix)
  if (plan.commits.length === 0) console.log('nothing to commit: no slice has a pending change')
  else console.log(formatPlan(plan))

  if (!apply) {
    console.log('\ndry run — nothing was staged or committed; re-run with --apply')
    return strict && plan.leftovers.length ? 1 : 0
  }

  for (const commit of plan.commits) {
    git(root, 'add', '-A', '--', ...commit.files)
    git(root, 'commit', '-m', commit.message)
    console.log(`committed ${commit.id}: ${commit.message} (${commit.files.length} file(s))`)
  }

  console.log('\nremaining in the working tree:')
  console.log(git(root, 'status', '--porcelain', '--', prefix || '.') || '(clean)')
  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main())
}
