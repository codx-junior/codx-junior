<script setup>
import { computed, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'

/**
 * New company wizard — the first thing a fresh installation runs.
 *
 * codx-junior renders the company mode as soon as it is installed, so the very
 * first screen is this assistant: it walks the owner through the first company,
 * the projects the company works with and the basic AI settings, then hands the
 * workspace over already configured.
 *
 * Rules the product promises
 * --------------------------
 * • On a fresh installation the wizard is a **gate**: it cannot be closed or
 *   skipped as a whole, and the product is not usable until the owner finishes
 *   it (`mandatory`, or `setup_required` from `/api/companies/onboarding`).
 * • The Projects step starts with **every discovered project selected**, so
 *   finishing the wizard never leaves the company with an empty project list.
 * • The wizard can be re-run at any time from the company menu; a re-run is a
 *   normal assistant and may be closed. It never depends on a local flag.
 * • The state stays on the server (`/api/companies/onboarding`), so a reload
 *   resumes where the wizard stopped, and only a real finish opens the gate.
 */
const props = defineProps({
  /** Render the wizard. The parent owns visibility. */
  open: { type: Boolean, default: false },
  /**
   * Company to configure. When set (a company was just created) the wizard
   * edits that company instead of offering to create another one and starts at
   * the company step.
   */
  companyId: { type: String, default: '' },
  /** Discovered projects (company references) used by the Projects step. */
  projects: { type: Array, default: () => [] },
  /** Profile/person catalogs, kept for the summary. */
  references: { type: Object, default: () => ({}) },
  /**
   * Force the first-run gate. The parent uses it on a fresh installation; when
   * omitted the wizard reads `setup_required` from the onboarding payload.
   */
  mandatory: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'done'])

const STEP_ORDER = ['welcome', 'company', 'projects', 'models', 'review']
// Internal first line of the auto-provisioned default company. It is not a
// description the owner wrote, so the wizard shows an empty field instead of
// asking them to edit that sentence.
const AUTO_DESCRIPTION_PREFIX = 'Default company created automatically.'
const STEP_META = {
  welcome: { title: 'Welcome', icon: 'fa-solid fa-hand-sparkles', hint: 'What the company workspace is.' },
  company: { title: 'First company', icon: 'fa-solid fa-building', hint: 'Name, purpose and brand.' },
  projects: { title: 'Projects', icon: 'fa-solid fa-diagram-project', hint: 'Repositories the company works with.' },
  models: { title: 'AI models', icon: 'fa-solid fa-brain', hint: 'Default model for the AI teams.' },
  review: { title: 'Finish', icon: 'fa-solid fa-flag-checkered', hint: 'Review and start.' }
}

const step = ref('welcome')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const state = ref(null)
const companies = ref([])
const projectCatalog = ref([])
const models = ref([])
const modelsError = ref('')
// `setup_required` is the server-side first-run gate. It is true until the
// owner finishes the wizard on a fresh installation; a configured upgrade or a
// re-run from the company menu is not locked.
const setupRequired = ref(false)

const form = ref({
  mode: 'new', // 'new' | 'existing'
  companyId: '',
  name: '',
  description: '',
  mission: '',
  website: '',
  color: '#6366f1',
  template: 'blank',
  projectIds: [],
  defaultModel: ''
})

const stepIndex = computed(() => Math.max(0, STEP_ORDER.indexOf(step.value)))
const isLastStep = computed(() => step.value === 'review')
const progress = computed(() => ((stepIndex.value + 1) / STEP_ORDER.length) * 100)
const existingCompanies = computed(() => companies.value || [])
const canCreate = computed(() => form.value.name.trim().length > 1)
const stepMeta = computed(() => STEP_META[step.value] || STEP_META.welcome)
/** The wizard is a blocking gate while the installation has no company yet. */
const mandatory = computed(() => props.mandatory || setupRequired.value)
/** True when the owner kept every catalog project selected (the default). */
const allProjectsSelected = computed(() => {
  const catalog = projectCatalog.value.map(projectKey).filter(Boolean)
  const selected = form.value.projectIds.map(String)
  return catalog.length > 0 && catalog.every(id => selected.includes(id))
})

function projectKey(item = {}) {
  return String(item.project_id || item.id || item.name || '')
}
function projectName(item = {}) {
  return item.name || item.project_name || item.path || projectKey(item)
}
function projectPath(item = {}) {
  return item.path || item.root_path || item.folder || ''
}

async function load() {
  loading.value = true
  error.value = ''
  // Each opening starts from the stored document, not from leftovers of the
  // previous run (a rejected wizard step must not leak its project selection).
  form.value.projectIds = []
  form.value.defaultModel = ''
  try {
    const onboarding = await API.companies.onboarding()
    state.value = onboarding?.state || null
    companies.value = onboarding?.companies || []
    setupRequired.value = Boolean(
      onboarding?.setup_required ?? onboarding?.needs_setup
    )
    // The first-run wizard configures the company the installation already
    // provisions; creating a second one is a company-menu action, not a way to
    // leave the fresh-install placeholder behind. A re-run keeps both options.
    if (!existingCompanies.value.length) form.value.mode = 'new'
    else form.value.mode = 'existing'
    const remembered = state.value?.steps || {}
    // A skipped optional step is resolved: a reload must not send the owner
    // back to it. Only pending steps are resumed.
    const resolved = stepId => ['done', 'skipped'].includes(remembered[stepId] || '')
    const firstPending = STEP_ORDER.find(stepId => !resolved(stepId))
    // A mandatory wizard always re-confirms the company basics: when there is
    // no pending step (for example a legacy `skipped` document) it starts at
    // the company step instead of jumping straight to the finish screen.
    step.value = firstPending && firstPending !== 'review'
      ? firstPending
      : (mandatory.value ? 'company' : 'welcome')
    const configured = existingCompanies.value.find(item => item.id === state.value?.company_id)
    const preferred = existingCompanies.value.find(item => item.is_default) || existingCompanies.value[0]
    if (configured) form.value.companyId = configured.id
    else if (preferred) form.value.companyId = preferred.id
    const placeholder = existingCompanies.value.find(item => item.is_default)
    if (!state.value?.company_id && placeholder) {
      form.value.mode = 'existing'
      form.value.companyId = placeholder.id
      form.value.name = form.value.name || placeholder.name || ''
    }
    if (state.value?.settings?.default_model) {
      form.value.defaultModel = state.value.settings.default_model
    }
    if (props.companyId) {
      // "New company → configure": the company already exists, so the wizard is
      // its own setup assistant (name, purpose, brand, projects and model) and
      // starts at the company step — the welcome screen is for an install.
      form.value.mode = 'existing'
      form.value.companyId = props.companyId
      step.value = 'company'
    }
    // Prefill the complete document (mission, website, brand and projects) from
    // the company the wizard configures. The onboarding summary is enough to
    // render, but the owner must see the stored values — also on a re-run from
    // the company menu where no `companyId` prop is passed.
    if (form.value.companyId) {
      if (!form.value.name) {
        const summary = existingCompanies.value.find(item => item.id === form.value.companyId)
        form.value.name = summary?.name || ''
      }
      await prefillCompany(form.value.companyId)
    }
    // Resolve the project catalog and the model list while the spinner is
    // still visible, so the step the owner lands on is already populated.
    await Promise.all([loadProjects(), loadModels()])
  } catch (ex) {
    error.value = ex?.message || 'Cannot load the setup state.'
  } finally {
    loading.value = false
  }
}

/**
 * Prefill the form from the company being configured.
 *
 * The onboarding payload only carries summaries, so the full document is read
 * once: the owner must see the values that are already stored, not an empty
 * form that would overwrite them.
 *
 * @param {String} companyId Company to read.
 */
async function prefillCompany(companyId) {
  try {
    const company = await API.companies.get(companyId)
    if (!company) return
    form.value.name = company.name || form.value.name
    const description = String(company.description || '').trim()
    // The auto-provisioned default carries an internal sentence; leave the
    // field empty so the owner writes the real one.
    form.value.description = description.startsWith(AUTO_DESCRIPTION_PREFIX)
      ? ''
      : description
    form.value.mission = company.mission || ''
    form.value.website = company.website || ''
    form.value.color = company.color || form.value.color
    const linked = (company.projects || [])
      .map(item => String(item?.project_id || ''))
      .filter(Boolean)
    const projectIds = linked.length
      ? linked
      : (company.project_ids || []).map(String).filter(Boolean)
    if (projectIds.length) form.value.projectIds = projectIds
  } catch {
    // Prefill is a convenience: the summary already selected is enough.
  }
}

/**
 * Re-prefill the form when the owner picks another existing company.
 *
 * Without this the fields would keep the previous company's name/brand and
 * "Start working" would write them over the company just selected.
 */
async function onCompanyChange() {
  if (!form.value.companyId) return
  await prefillCompany(form.value.companyId)
}

/** Configure an existing company (the default tab of the company step). */
async function configureCompany(companyId = '') {
  form.value.mode = 'existing'
  form.value.companyId = companyId || existingCompanies.value[0]?.id || ''
  await prefillCompany(form.value.companyId)
}

/** Switch to a blank company. The prefilled name is never reused by accident. */
function createNewCompany() {
  form.value.mode = 'new'
  form.value.companyId = ''
  form.value.name = ''
  form.value.description = ''
  form.value.mission = ''
  form.value.website = ''
  form.value.color = '#6366f1'
  form.value.projectIds = projectCatalog.value.map(projectKey).filter(Boolean)
  form.value.defaultModel = ''
}

async function loadProjects() {
  const fromProps = (props.projects || []).map(item => ({ ...item }))
  projectCatalog.value = fromProps
  try {
    const refs = await API.companies.references({})
    const list = refs?.projects || refs?.allProjects || []
    if (list.length) projectCatalog.value = list
  } catch {
    // The picker still works with the projects the shell already knows.
  }
  const catalog = projectCatalog.value.map(projectKey).filter(Boolean)
  const configured = existingCompanies.value.find(item => item.id === form.value.companyId)
  const linked = (configured?.project_ids || []).map(String)
  // Defaults to ALL projects. A company that already uses the `all_projects`
  // wildcard (the fresh-install default) selects the whole catalog even when
  // the summary carries explicit links; a deliberately narrowed company keeps
  // its own list. A brand-new company starts with everything selected.
  const allByDefault = configured ? configured.all_projects !== false : true
  form.value.projectIds = allByDefault ? catalog : linked
}

async function loadModels() {
  try {
    const list = await API.ai.models.list()
    models.value = Array.isArray(list) ? list : []
    modelsError.value = ''
  } catch (ex) {
    models.value = []
    modelsError.value = 'No AI provider configured yet — you can skip this step.'
  }
}

function toggleProject(id) {
  const key = String(id)
  const index = form.value.projectIds.indexOf(key)
  if (index >= 0) form.value.projectIds.splice(index, 1)
  else form.value.projectIds.push(key)
}
function selectAllProjects() {
  form.value.projectIds = projectCatalog.value.map(projectKey).filter(Boolean)
}

async function persist(patch) {
  const saved = await API.companies.saveOnboarding(patch)
  state.value = saved?.state || state.value
  if (saved?.companies) companies.value = saved.companies
  return saved
}

async function next() {
  error.value = ''
  const current = step.value
  if (current === 'company' && !canCreate.value) {
    error.value = 'Give the company a name before continuing.'
    return
  }
  saving.value = true
  try {
    const patch = { step: current, step_status: 'done', settings: collectSettings() }
    // Remember the configured company as soon as the company step is done, so
    // a reload resumes with the same one if the owner runs several companies.
    if (current === 'company' && form.value.mode === 'existing' && form.value.companyId) {
      patch.company_id = form.value.companyId
    }
    await persist(patch)
    const index = STEP_ORDER.indexOf(current)
    step.value = STEP_ORDER[Math.min(index + 1, STEP_ORDER.length - 1)]
  } catch (ex) {
    error.value = ex?.message || 'Cannot save the step.'
  } finally {
    saving.value = false
  }
}

async function skipStep() {
  const current = step.value
  // The company step is the one thing the wizard must collect; a mandatory
  // wizard also never skips the review because that would finish it without
  // applying the company and its projects.
  if (current === 'company' || (mandatory.value && current === 'review')) return
  saving.value = true
  error.value = ''
  try {
    await persist({ step: current, step_status: 'skipped' })
    // Skipping a step never finishes the wizard: the review screen (and the
    // explicit Start working) always applies the company and its projects. This
    // is what makes "only a real finish opens the gate" true on the server.
    const index = STEP_ORDER.indexOf(current)
    step.value = STEP_ORDER[Math.min(index + 1, STEP_ORDER.length - 1)]
  } catch (ex) {
    error.value = ex?.message || 'Cannot skip the step.'
  } finally {
    saving.value = false
  }
}

async function skipWizard() {
  if (mandatory.value) return
  saving.value = true
  error.value = ''
  try {
    await persist({ status: 'skipped' })
    await $storex.company.loadCompanies().catch(() => {})
    emit('done', { status: 'skipped' })
  } catch (ex) {
    error.value = ex?.message || 'Cannot skip the setup.'
  } finally {
    saving.value = false
  }
}

function collectSettings() {
  return { default_model: form.value.defaultModel || '' }
}

async function finish() {
  saving.value = true
  error.value = ''
  try {
    const companyId = await applyCompany()
    await applyProjects(companyId)
    await persist({
      status: 'completed',
      step: 'review',
      step_status: 'done',
      company_id: companyId,
      settings: collectSettings()
    })
    await $storex.company.loadCompanies().catch(() => {})
    const company = existingCompanies.value.find(item => item.id === form.value.companyId)
    if (company?.id) await $storex.company.select({ companyId: company.id, skipList: true }).catch(() => {})
    emit('done', { status: 'completed' })
  } catch (ex) {
    error.value = ex?.message || 'Cannot finish the setup.'
  } finally {
    saving.value = false
  }
}

async function applyCompany() {
  const payload = {
    name: form.value.name.trim(),
    description: form.value.description.trim(),
    mission: form.value.mission.trim(),
    website: form.value.website.trim(),
    color: form.value.color || '#6366f1'
  }
  if (form.value.mode === 'existing' && form.value.companyId) {
    const existing = existingCompanies.value.find(item => item.id === form.value.companyId) || {}
    const patch = {}
    Object.entries(payload).forEach(([key, value]) => {
      if (value && value !== existing[key]) patch[key] = value
    })
    // The auto-provisioned default carries an internal description. When the
    // owner leaves the field empty, clear it instead of keeping that sentence.
    if (!payload.description && existing.description) {
      patch.description = ''
    }
    if (Object.keys(patch).length) {
      await API.companies.update(form.value.companyId, patch)
    }
    return form.value.companyId
  }
  const created = await API.companies.create({
    ...payload,
    template: form.value.template || 'blank',
    project_ids: form.value.projectIds
  })
  form.value.companyId = created?.id || form.value.companyId
  if (created?.id && !companies.value.some(item => item.id === created.id)) {
    companies.value = [...companies.value, { id: created.id, name: created.name, slug: created.slug }]
  }
  return created?.id
}

async function applyProjects(companyId) {
  if (!companyId) return
  const catalog = projectCatalog.value.map(projectKey).filter(Boolean)
  // No project discovered yet: leaving the company's project policy untouched
  // is better than replacing its wildcard with an empty explicit list.
  if (!catalog.length) return
  try {
    if (allProjectsSelected.value) {
      // "All projects" is the default and a wildcard: newly discovered
      // projects join the company automatically instead of being missed.
      await API.companies.update(companyId, { all_projects: true })
    } else {
      await API.companies.setProjects(companyId, form.value.projectIds)
    }
  } catch {
    // The project list is editable later from Company → Settings → Projects.
  }
}

function close() {
  // The first-run gate has exactly one exit: Start working.
  if (mandatory.value) return
  emit('close')
}

watch(() => props.open, value => { if (value) load() }, { immediate: true })
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[120] grid place-items-center bg-black/55 p-3 backdrop-blur-sm"
    role="dialog"
    aria-modal="true"
    aria-label="New company wizard"
  >
    <div class="rounded-box border border-base-300 bg-base-100 shadow-sm w-full max-w-[720px] overflow-hidden p-0">
      <!-- Header: progress + close (only on an optional re-run, never on the gate) -->
      <div class="flex items-start justify-between gap-3 border-b border-base-300 p-4">
        <div class="min-w-0">
          <p class="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">
            Setup assistant
            <span
              v-if="mandatory"
              class="rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-bold normal-case tracking-normal text-accent-ink"
            >Required</span>
          </p>
          <h2 class="mt-1 flex items-center gap-2 text-base font-extrabold">
            <i :class="stepMeta.icon" class="text-sm text-accent-ink"></i>
            {{ stepMeta.title }}
          </h2>
          <p class="mt-1 text-xs text-base-content/60">{{ stepMeta.hint }}</p>
        </div>
        <button
          v-if="!mandatory"
          class="btn btn-ghost btn-sm btn-square !h-7 !w-7"
          aria-label="Close the wizard"
          @click="close"
        >
          <i class="fa-solid fa-xmark text-xs"></i>
        </button>
      </div>

      <div class="h-1 w-full bg-base-200">
        <div class="h-1 bg-accent transition-all" :style="{ width: progress + '%' }"></div>
      </div>

      <div class="max-h-[62vh] overflow-y-auto p-4">
        <p v-if="loading" class="text-xs text-base-content/60">
          <span class="loading loading-spinner loading-xs mr-2"></span>Reading the installation…
        </p>

        <!-- 1 · Welcome ------------------------------------------------------ -->
        <div v-else-if="step === 'welcome'" class="space-y-3">
          <p class="text-sm leading-relaxed">
            codx-junior is installed and running in <strong>company mode</strong>. A company owns
            projects, hybrid teams of people and AI profiles, and the missions they execute.
          </p>
          <ul class="space-y-2 text-xs text-base-content/60">
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[11px] text-accent-ink"></i>
              Define the first company — name, purpose and brand.</li>
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[11px] text-accent-ink"></i>
              Choose the projects the company works with.</li>
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[11px] text-accent-ink"></i>
              Set the default AI model for its teams.</li>
          </ul>
          <p
            class="rounded-xl border p-3 text-xs"
            :class="mandatory
              ? 'border-accent/40 bg-accent/10 text-base-content/80'
              : 'border-base-300 bg-base-200 text-base-content/60'"
          >
            <template v-if="mandatory">
              This installation is <strong>not ready yet</strong>: define your company and finish
              this assistant to start using codx-junior. The whole workspace stays locked until you
              press <strong>Start working</strong> on the last step.
            </template>
            <template v-else>
              You can run this assistant again at any time from the company menu →
              <strong>Setup assistant</strong>.
            </template>
          </p>
        </div>

        <!-- 2 · Company ------------------------------------------------------ -->
        <div v-else-if="step === 'company'" class="space-y-3">
          <div v-if="existingCompanies.length && !mandatory" class="flex flex-wrap gap-2">
            <button
              class="btn btn-xs"
              :class="{ 'btn-active': form.mode === 'existing' }"
              @click="configureCompany()"
            >
              Configure {{ existingCompanies[0].name || 'the default company' }}
            </button>
            <button
              class="btn btn-xs"
              :class="{ 'btn-active': form.mode === 'new' }"
              @click="createNewCompany"
            >
              Create a new company
            </button>
          </div>

          <label v-if="form.mode === 'existing'" class="flex flex-col gap-1">
            <span class="text-xs font-semibold text-base-content/60">Company</span>
            <select
              v-model="form.companyId"
              class="select select-sm select-bordered w-full"
              @change="onCompanyChange"
            >
              <option v-for="item in existingCompanies" :key="item.id" :value="item.id">
                {{ item.name || item.id }}
              </option>
            </select>
          </label>

          <label class="flex flex-col gap-1">
            <span class="text-xs font-semibold text-base-content/60">Company name *</span>
            <input v-model="form.name" class="input input-sm input-bordered w-full" placeholder="Acme Studio" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs font-semibold text-base-content/60">What does it do?</span>
            <input v-model="form.description" class="input input-sm input-bordered w-full" placeholder="AI-powered product studio" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs font-semibold text-base-content/60">Mission <span class="opacity-50">(optional)</span></span>
            <textarea v-model="form.mission" rows="2" class="textarea textarea-sm textarea-bordered w-full"
                      placeholder="Ship useful software faster than anyone"></textarea>
          </label>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-semibold text-base-content/60">Website</span>
              <input v-model="form.website" class="input input-sm input-bordered w-full" placeholder="https://" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-semibold text-base-content/60">Colour</span>
              <input v-model="form.color" type="color" class="input input-sm input-bordered h-9 w-full p-1" />
            </label>
            <label v-if="form.mode === 'new'" class="flex flex-col gap-1">
              <span class="text-xs font-semibold text-base-content/60">Starter</span>
              <select v-model="form.template" class="select select-sm select-bordered w-full">
                <option value="blank">Blank</option>
                <option value="saas">SaaS org template</option>
              </select>
            </label>
          </div>
        </div>

        <!-- 3 · Projects ----------------------------------------------------- -->
        <div v-else-if="step === 'projects'" class="space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="text-xs text-base-content/60">
              <strong>{{ form.projectIds.length }}</strong> of
              <strong>{{ projectCatalog.length }}</strong> project(s) selected<span
                v-if="allProjectsSelected && projectCatalog.length"
              > — <span class="text-accent-ink">All projects</span></span>.
            </p>
            <button class="btn btn-xs" :disabled="allProjectsSelected" @click="selectAllProjects">
              Select all
            </button>
          </div>
          <p class="rounded-xl border border-base-300 bg-base-200 p-3 text-xs text-base-content/60">
            Every discovered project is selected by default. Keep them all to let the company cover
            projects added later automatically, or deselect what this company must not see.
          </p>
          <div v-if="!projectCatalog.length" class="rounded-xl border border-base-300 p-3 text-xs text-base-content/60">
            No project was discovered yet. Skip this step and create one later from
            Company → Projects.
          </div>
          <div v-else class="grid max-h-[38vh] grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
            <button
              v-for="item in projectCatalog"
              :key="projectKey(item)"
              class="flex items-start gap-2 rounded-xl border p-2 text-left transition"
              :class="form.projectIds.includes(projectKey(item))
                ? 'border-accent bg-accent/10'
                : 'border-base-300 hover:border-base-content/20'"
              @click="toggleProject(projectKey(item))"
            >
              <i
                class="mt-0.5 text-xs"
                :class="form.projectIds.includes(projectKey(item))
                  ? 'fa-solid fa-square-check text-accent-ink'
                  : 'fa-regular fa-square text-base-content/40'"
              ></i>
              <span class="min-w-0">
                <span class="block truncate text-xs font-bold">{{ projectName(item) }}</span>
                <span v-if="projectPath(item)" class="mt-0.5 block truncate text-[11px] text-base-content/40">
                  {{ projectPath(item) }}
                </span>
              </span>
            </button>
          </div>
        </div>

        <!-- 4 · AI models ---------------------------------------------------- -->
        <div v-else-if="step === 'models'" class="space-y-3">
          <label class="flex flex-col gap-1">
            <span class="text-xs font-semibold text-base-content/60">Default model for the company teams</span>
            <select v-model="form.defaultModel" class="select select-sm select-bordered w-full">
              <option value="">Keep the project default</option>
              <option v-for="model in models" :key="model.name || model.id" :value="model.name || model.id">
                {{ model.name || model.id }}
              </option>
            </select>
          </label>
          <p v-if="modelsError" class="text-xs text-warning-ink">{{ modelsError }}</p>
          <p class="rounded-xl border border-base-300 bg-base-200 p-3 text-xs text-base-content/60">
            Providers and API keys live in the installation AI settings; this only chooses the model
            the company starts with. You can change it later per project.
          </p>
        </div>

        <!-- 5 · Review ------------------------------------------------------- -->
        <div v-else class="space-y-3">
          <div class="rounded-xl border border-base-300 p-3">
            <p class="text-xs font-bold uppercase tracking-[0.14em] text-base-content/40">Ready</p>
            <p class="mt-1 text-sm font-bold">
              {{ form.mode === 'existing'
                ? (existingCompanies.find(c => c.id === form.companyId)?.name || 'Company')
                : (form.name || 'New company') }}
            </p>
            <p v-if="form.description" class="text-xs text-base-content/60">{{ form.description }}</p>
            <dl class="mt-2 grid grid-cols-2 gap-2 text-xs">
              <div><dt class="text-base-content/60">Projects</dt><dd class="font-bold">{{ form.projectIds.length }}</dd></div>
              <div><dt class="text-base-content/60">Default model</dt><dd class="font-bold">{{ form.defaultModel || 'project default' }}</dd></div>
            </dl>
          </div>
          <p class="text-xs text-base-content/60">
            <template v-if="mandatory">
              <strong>Start working</strong> saves the company, links
              {{ allProjectsSelected ? 'all projects' : 'the selected projects' }} and unlocks the
              workspace for the first time. Teams, missions and the autopilot are configured from
              there afterwards.
            </template>
            <template v-else>
              Finishing opens the company workspace. Teams, missions and the autopilot are
              configured from there — nothing is locked in by this wizard.
            </template>
          </p>
        </div>

        <div v-if="error" class="mt-3 flex flex-wrap items-center gap-2 text-xs text-error-ink">
          <span>{{ error }}</span>
          <button v-if="mandatory" class="btn btn-xs" :disabled="loading" @click="load">Retry</button>
        </div>
      </div>

      <!-- Footer: the first-run gate has no skip exit; optional re-runs can be
           dismissed. The company step is never skipped because it owns the
           company basics the wizard exists to collect. -->
      <div class="flex flex-wrap items-center justify-between gap-2 border-t border-base-300 p-3">
        <div class="flex items-center gap-2">
          <button
            class="btn btn-xs"
            :disabled="stepIndex === 0 || saving"
            @click="step = STEP_ORDER[Math.max(0, stepIndex - 1)]"
          >
            <i class="fa-solid fa-arrow-left text-[11px]"></i> Back
          </button>
          <button v-if="!mandatory" class="btn btn-xs" :disabled="saving" @click="skipWizard">
            Skip setup
          </button>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="!isLastStep && step !== 'company'"
            class="btn btn-xs"
            :disabled="saving"
            @click="skipStep"
          >
            Skip step
          </button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="saving || (step === 'company' && !canCreate)"
            @click="isLastStep ? finish() : next()"
          >
            <span v-if="saving" class="loading loading-spinner loading-xs"></span>
            {{ isLastStep ? 'Start working' : 'Continue' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
