/**
 * Contract tests for the commit-slices driver (`tools/commit-slices.mjs`).
 *
 * The driver decides what goes into each commit, so a wrong decision here
 * silently mixes two features into one revert unit. These tests pin the parsing
 * of `git status --porcelain`, the git-root/`client/` prefix shift, the
 * first-match ownership rule, the leftover rule and the "revertible map"
 * validation — all without running git.
 *
 * Run with: node --test tests/*.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SLICES,
  buildPlan,
  classify,
  formatPlan,
  matchesRule,
  parsePorcelain,
  toClientPath,
  validateSlices
} from '../tools/commit-slices.mjs'

const PREFIX = 'client/'

test('parsePorcelain reads modified, untracked and deleted entries', () => {
  const changes = parsePorcelain(
    [' M client/package.json', '?? client/tests/gridPreview.test.mjs', 'D  client/src/assets/company.css'].join('\n')
  )
  assert.deepEqual(changes, [
    { status: 'M', path: 'client/package.json' },
    { status: '??', path: 'client/tests/gridPreview.test.mjs' },
    { status: 'D', path: 'client/src/assets/company.css' }
  ])
})

test('parsePorcelain stages the destination of a rename and unquotes paths', () => {
  const changes = parsePorcelain('R  client/src/old.js -> client/src/utils/theme.js')
  assert.equal(changes.length, 1)
  assert.equal(changes[0].path, 'client/src/utils/theme.js')
  assert.equal(changes[0].status, 'R')

  const quoted = parsePorcelain('?? "client/src/components/company/Company Files.vue"')
  assert.equal(quoted[0].path, 'client/src/components/company/Company Files.vue')
})

test('parsePorcelain ignores blank lines and trailing CR', () => {
  assert.deepEqual(parsePorcelain('').length, 0)
  assert.deepEqual(parsePorcelain(null).length, 0)
  assert.equal(parsePorcelain('M  client/src/App.vue\r\n')[0].path, 'client/src/App.vue')
})

test('toClientPath drops the git root prefix only when it is present', () => {
  assert.equal(toClientPath('client/src/App.vue', PREFIX), 'src/App.vue')
  assert.equal(toClientPath('src/App.vue', PREFIX), 'src/App.vue')
  assert.equal(toClientPath('client/src/App.vue', ''), 'client/src/App.vue')
  assert.equal(toClientPath('client\\src\\App.vue', 'client\\'), 'src/App.vue')
})

test('matchesRule supports exact paths and directory prefixes', () => {
  assert.equal(matchesRule('package.json', 'package.json'), true)
  assert.equal(matchesRule('package.json', 'package.json5'), false)
  assert.equal(matchesRule('src/components/company/', 'src/components/company/CompanyHome.vue'), true)
  assert.equal(matchesRule('src/components/company/', 'src/components/kanban/TaskCard.vue'), false)
})

test('classify assigns by the first matching slice and keeps the rest as leftovers', () => {
  const slices = [
    { id: 'one', message: 'one', paths: ['package.json'] },
    { id: 'two', message: 'two', paths: ['src/assets/'] },
    { id: 'three', message: 'three', paths: ['src/assets/base.css'] }
  ]
  const { assigned, leftovers } = classify(
    parsePorcelain(
      [
        'M  client/package.json',
        'M  client/src/assets/base.css',
        '?? client/src/api/modules/companies.js'
      ].join('\n')
    ),
    slices,
    PREFIX
  )

  assert.deepEqual(assigned.get('one').map(entry => entry.clientPath), ['package.json'])
  assert.deepEqual(assigned.get('two').map(entry => entry.clientPath), ['src/assets/base.css'])
  assert.deepEqual(assigned.get('three'), [])
  assert.deepEqual(leftovers, ['src/api/modules/companies.js'])
})

test('buildPlan drops empty slices and keeps whole-tree paths for git add', () => {
  const plan = buildPlan(
    parsePorcelain(['M  client/package.json', 'D  client/src/assets/company.css'].join('\n')),
    SLICES,
    PREFIX
  )

  assert.deepEqual(plan.commits.map(commit => commit.id), ['test-runner', 'company-theme'])
  assert.deepEqual(plan.commits[1].files, ['client/src/assets/company.css'])
  assert.deepEqual(plan.commits[1].clientFiles, ['src/assets/company.css'])
  assert.deepEqual(plan.leftovers, [])
})

test('buildPlan never invents a home: unassigned files stay leftovers', () => {
  const plan = buildPlan(parsePorcelain('?? client/src/components/unknown/Thing.vue'), SLICES, PREFIX)
  assert.deepEqual(plan.commits, [])
  assert.deepEqual(plan.leftovers, ['src/components/unknown/Thing.vue'])
})

test('the shipped slice map is revertible', () => {
  assert.deepEqual(validateSlices(SLICES), [])
  assert.equal(new Set(SLICES.map(slice => slice.id)).size, SLICES.length)
  for (const slice of SLICES) {
    assert.ok(slice.paths.length > 0, `${slice.id} has paths`)
    assert.ok(!slice.message.endsWith('.'), `${slice.id} message is a conventional subject`)
  }
})

test('validateSlices rejects overlaps that would break revertibility', () => {
  const problems = validateSlices([
    { id: 'a', message: 'a', paths: ['src/assets/'] },
    { id: 'b', message: 'b', paths: ['src/assets/base.css'] },
    { id: 'c', message: 'c', paths: ['src/assets/'] },
    { id: 'd', message: 'd', paths: [] }
  ])

  assert.ok(problems.some(problem => problem.includes('src/assets/base.css')), 'exact path inside a prefix')
  assert.ok(problems.some(problem => problem.includes('overlaps')), 'two slices claiming the same prefix')
  assert.ok(problems.some(problem => problem.includes('no paths')), 'an empty slice')
})

test('the shipped map assigns the measured company theme slice', () => {
  const { assigned } = classify(
    parsePorcelain(
      [
        'D  client/src/assets/company.css',
        'D  client/src/assets/daisy-override.css',
        'M  client/src/assets/base.css',
        'M  client/src/components/company/CompanyHome.vue',
        '?? client/src/components/company/CompanyFiles.vue',
        'M  client/src/views/CompanyView.vue',
        'M  client/src/api/modules/companies.js',
        'M  client/tests/company-theme.test.mjs'
      ].join('\n')
    ),
    SLICES,
    PREFIX
  )

  assert.equal(assigned.get('company-theme').length, 8)
  assert.equal(assigned.get('planning-range').length, 0)
})

test('the new utils ship with the components that consume them', () => {
  const { assigned } = classify(
    parsePorcelain(
      [
        'M  client/src/components/browsers/ComputerGridTile.vue',
        'M  client/src/components/ThemeSelector.vue'
      ].join('\n')
    ),
    SLICES,
    PREFIX
  )

  assert.deepEqual(assigned.get('planning-range').map(entry => entry.clientPath), [
    'src/components/browsers/ComputerGridTile.vue'
  ])
  assert.deepEqual(assigned.get('company-theme').map(entry => entry.clientPath), [
    'src/components/ThemeSelector.vue'
  ])
})

test('the company shell consumers ship after the company theme slice', () => {
  const { assigned, leftovers } = classify(
    parsePorcelain(
      [
        'M  client/src/components/ProjectDropdown.vue',
        'M  client/src/components/mobile/MobileMenu.vue',
        'M  client/src/components/workspaces/Workspaces.vue',
        'M  client/src/components/kanban/TaskSettings.vue'
      ].join('\n')
    ),
    SLICES,
    PREFIX
  )

  assert.deepEqual(assigned.get('company-shell').map(entry => entry.clientPath), [
    'src/components/ProjectDropdown.vue',
    'src/components/mobile/MobileMenu.vue',
    'src/components/workspaces/Workspaces.vue'
  ])
  assert.deepEqual(assigned.get('kanban-fixes').map(entry => entry.clientPath), [
    'src/components/kanban/TaskSettings.vue'
  ])
  assert.deepEqual(leftovers, [])

  const ids = SLICES.map(slice => slice.id)
  assert.ok(
    ids.indexOf('company-shell') > ids.indexOf('company-theme'),
    'the shell reads the company store, so it ships after it'
  )
})

test('formatPlan shows every commit and every leftover', () => {
  const text = formatPlan(
    buildPlan(
      parsePorcelain(['M  client/package.json', '?? client/src/unknown.js'].join('\n')),
      SLICES,
      PREFIX
    )
  )

  assert.match(text, /^1\. test: expose the client node test suite as npm test$/)
  assert.match(text, /\+ package\.json/)
  assert.match(text, /Left out of every commit \(1 file\(s\), unassigned\):/)
  assert.match(text, /\? src\/unknown\.js/)
})
