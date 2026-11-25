import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  companyActiveProjectKey,
  resolveCompanyActiveProject
} from '../src/utils/companyActiveProject.js'

// ─────────────────────────────────────────────────────────────────────────────
// ACTIVE PROJECT ↔ COMPANY SCOPE
//
// Workspace mode has one active project, but the company owns the projects.
// Switching companies must never leave the previous company's project active:
// it is replaced by the new company's remembered/first project, or cleared when
// the company has no projects. These tests pin the pure rule and the wiring.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const projects = [
  { project_id: 'a-1', project_name: 'a-one' },
  { project_id: 'a-2', project_name: 'codx-junior' },
  { project_id: 'b-1', project_name: 'b-one' },
  { project_id: 'b-child', project_name: 'b-child' }
]

test('a valid active project is kept without a reload', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'a-1',
    visibleProjectIds: ['a-1', 'a-2']
  })
  assert.equal(result.project.project_id, 'a-1')
  assert.equal(result.changed, false)
  assert.equal(result.valid, true)
})

test('a project of the previous company is replaced by the remembered project', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'b-1',
    visibleProjectIds: ['a-1', 'a-2'],
    linkedProjectIds: ['a-1', 'a-2'],
    rememberedProjectId: 'a-2',
    lastProjectId: 'b-1'
  })
  assert.equal(result.project.project_id, 'a-2')
  assert.equal(result.changed, true)
})

test('the installation-wide last project is ignored when it belongs to another company', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'b-1',
    visibleProjectIds: ['a-1', 'a-2'],
    linkedProjectIds: ['a-1', 'a-2'],
    lastProjectId: 'b-1'
  })
  assert.equal(result.project.project_id, 'a-2', 'falls back to the default project')
  assert.equal(result.changed, true)
})

test('a cold restore prefers the company memory over the server active project', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'a-1',
    visibleProjectIds: ['a-1', 'a-2'],
    rememberedProjectId: 'a-2',
    preferRemembered: true
  })
  assert.equal(result.project.project_id, 'a-2')
  assert.equal(result.changed, true)
})

test('a company switch keeps a project shared by both companies', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'a-1',
    visibleProjectIds: ['a-1', 'b-1'],
    rememberedProjectId: 'b-1',
    preferRemembered: false
  })
  assert.equal(result.project.project_id, 'a-1')
  assert.equal(result.changed, false)
})

test('a child project is valid when the company sees it', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'b-child',
    visibleProjectIds: ['b-1', 'b-child']
  })
  assert.equal(result.project.project_id, 'b-child')
  assert.equal(result.changed, false)
})

test('a company with no visible project clears the active project', () => {
  const result = resolveCompanyActiveProject(projects, {
    activeProjectId: 'a-1',
    visibleProjectIds: []
  })
  assert.equal(result.project, null)
  assert.equal(result.changed, true)
  assert.equal(result.valid, false)
})

test('the per-company memory is namespaced by company id', () => {
  assert.equal(companyActiveProjectKey('company-a'), 'lastActiveProject:company-a')
  assert.equal(companyActiveProjectKey(''), 'lastActiveProject:default')
})

test('the project store reconciles the active project on company changes', async () => {
  const store = await read('../src/store/project.js')
  assert.match(store, /reconcileActiveCompanyProject/)
  assert.match(store, /resolveCompanyActiveProject/)
  assert.match(store, /companyActiveProjectKey/)
  assert.match(store, /clearActiveProject/)
  assert.match(
    store,
    /companyId\)[\s\S]{0,120}localStorage\.setItem/,
    'the active project is remembered per company'
  )
})

test('the company shell revalidates the active project after a switch', async () => {
  const shell = await read('../src/views/CompanyView.vue')
  assert.match(shell, /reconcileActiveCompanyProject/)
  assert.match(shell, /cannot reconcile the active project/)
})

test('workspace routes reject a project outside the company', async () => {
  const router = await read('../src/router/index.ts')
  assert.match(router, /visibleProjectIds/)
  assert.match(router, /not available in this company/)
})
