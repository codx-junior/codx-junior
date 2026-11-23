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
 * • Every step can be skipped, and so can the whole wizard (`Skip setup`).
 * • The wizard can be re-run at any time: it is opened with `open = true` from
 *   the company menu and never depends on a local flag.
 * • Skipping is not "done": the state stays on the server
 *   (`/api/companies/onboarding`), so a reload does not lose the decision and a
 *   half-filled wizard resumes where it stopped.
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
  references: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['close', 'done'])

const STEP_ORDER = ['welcome', 'company', 'projects', 'models', 'review']
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
  try {
    const onboarding = await API.companies.onboarding()
    state.value = onboarding?.state || null
    companies.value = onboarding?.companies || []
    if (!existingCompanies.value.length) form.value.mode = 'new'
    else if (form.value.mode !== 'new') form.value.mode = 'existing'
    const remembered = state.value?.steps || {}
    const firstPending = STEP_ORDER.find(id => (remembered[id] || 'pending') !== 'done')
    step.value = firstPending && firstPending !== 'review' ? firstPending : 'welcome'
    const configured = existingCompanies.value.find(item => item.id === state.value?.company_id)
    if (configured) form.value.companyId = configured.id
    else if (existingCompanies.value.length) form.value.companyId = existingCompanies.value[0].id
    const placeholder = existingCompanies.value.find(item => item.is_default)
    if (!state.value?.company_id && placeholder) {
      form.value.mode = 'existing'
      form.value.companyId = placeholder.id
      form.value.name = form.value.name || placeholder.name || ''
      form.value.description = form.value.description || placeholder.description || ''
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
      await prefillCompany(props.companyId)
    }
  } catch (ex) {
    error.value = ex?.message || 'Cannot load the setup state.'
  } finally {
    loading.value = false
  }
  loadProjects()
  loadModels()
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
    form.value.description = company.description || ''
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
  if (!form.value.projectIds.length) {
    // Pre-select what the company already covers, otherwise everything: the
    // wizard is an accelerator, not a gate.
    const configured = existingCompanies.value.find(item => item.id === form.value.companyId)
    const linked = (configured?.project_ids || []).map(String)
    form.value.projectIds = linked.length
      ? linked
      : projectCatalog.value.map(projectKey).filter(Boolean)
  }
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
  if (current === 'company' && form.value.mode === 'new' && !canCreate.value) {
    error.value = 'Give the company a name (or skip this step).'
    return
  }
  saving.value = true
  try {
    await persist({ step: current, step_status: 'done', settings: collectSettings() })
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
  saving.value = true
  error.value = ''
  try {
    await persist({ step: current, step_status: 'skipped' })
    const index = STEP_ORDER.indexOf(current)
    if (index >= STEP_ORDER.length - 2) return finish({ skipped: true })
    step.value = STEP_ORDER[index + 1]
  } catch (ex) {
    error.value = ex?.message || 'Cannot skip the step.'
  } finally {
    saving.value = false
  }
}

async function skipWizard() {
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

async function finish({ skipped = false } = {}) {
  saving.value = true
  error.value = ''
  try {
    if (skipped) {
      await persist({ status: 'completed', step: 'review', step_status: 'skipped' })
    } else {
      const companyId = await applyCompany()
      await applyProjects(companyId)
      await persist({
        status: 'completed',
        step: 'review',
        step_status: 'done',
        company_id: companyId,
        settings: collectSettings()
      })
    }
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
  try {
    await API.companies.setProjects(companyId, form.value.projectIds)
  } catch {
    // The project list is editable later from Company → Settings → Projects.
  }
}

function close() {
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
    <div class="cp-card w-full max-w-[720px] overflow-hidden p-0">
      <!-- Header: progress + the two escape hatches (skip step / skip setup) -->
      <div class="flex items-start justify-between gap-3 border-b border-[var(--cp-border)] p-4">
        <div class="min-w-0">
          <p class="cp-eyebrow">Setup assistant</p>
          <h2 class="mt-1 flex items-center gap-2 text-base font-extrabold">
            <i :class="stepMeta.icon" class="text-[12px] cp-lime"></i>
            {{ stepMeta.title }}
          </h2>
          <p class="mt-1 text-[10px] cp-muted">{{ stepMeta.hint }}</p>
        </div>
        <button class="cp-icon-btn !h-7 !w-7" aria-label="Close the wizard" @click="close">
          <i class="fa-solid fa-xmark text-[11px]"></i>
        </button>
      </div>

      <div class="h-1 w-full bg-[var(--cp-surface-soft)]">
        <div class="h-1 bg-[var(--cp-lime)] transition-all" :style="{ width: progress + '%' }"></div>
      </div>

      <div class="max-h-[62vh] overflow-y-auto p-4">
        <p v-if="loading" class="text-xs cp-muted">
          <span class="loading loading-spinner loading-xs mr-2"></span>Reading the installation…
        </p>

        <!-- 1 · Welcome ------------------------------------------------------ -->
        <div v-else-if="step === 'welcome'" class="space-y-3">
          <p class="text-[12px] leading-relaxed">
            codx-junior is installed and running in <strong>company mode</strong>. A company owns
            projects, hybrid teams of people and AI profiles, and the missions they execute.
          </p>
          <ul class="space-y-2 text-[11px] cp-muted">
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[9px] cp-lime"></i>
              Define the first company — name, purpose and brand.</li>
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[9px] cp-lime"></i>
              Choose the projects the company works with.</li>
            <li class="flex gap-2"><i class="fa-solid fa-check mt-0.5 text-[9px] cp-lime"></i>
              Set the default AI model for its teams.</li>
          </ul>
          <p class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3 text-[10px] cp-muted">
            Every step can be skipped, and you can run this assistant again at any time from the
            company menu → <strong>Setup assistant</strong>.
          </p>
        </div>

        <!-- 2 · Company ------------------------------------------------------ -->
        <div v-else-if="step === 'company'" class="space-y-3">
          <div v-if="existingCompanies.length" class="flex flex-wrap gap-2">
            <button
              class="cp-chip"
              :class="{ 'cp-chip-active': form.mode === 'existing' }"
              @click="form.mode = 'existing'"
            >
              Configure {{ existingCompanies[0].name || 'the default company' }}
            </button>
            <button
              class="cp-chip"
              :class="{ 'cp-chip-active': form.mode === 'new' }"
              @click="form.mode = 'new'"
            >
              Create a new company
            </button>
          </div>

          <label v-if="form.mode === 'existing'" class="form-control">
            <span class="label-text text-[10px]">Company</span>
            <select v-model="form.companyId" class="select select-sm select-bordered">
              <option v-for="item in existingCompanies" :key="item.id" :value="item.id">
                {{ item.name || item.id }}
              </option>
            </select>
          </label>

          <label class="form-control">
            <span class="label-text text-[10px]">Company name *</span>
            <input v-model="form.name" class="input input-sm input-bordered" placeholder="Acme Studio" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px]">What does it do?</span>
            <input v-model="form.description" class="input input-sm input-bordered" placeholder="AI-powered product studio" />
          </label>
          <label class="form-control">
            <span class="label-text text-[10px]">Mission <span class="opacity-50">(optional)</span></span>
            <textarea v-model="form.mission" rows="2" class="textarea textarea-sm textarea-bordered"
                      placeholder="Ship useful software faster than anyone"></textarea>
          </label>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label class="form-control">
              <span class="label-text text-[10px]">Website</span>
              <input v-model="form.website" class="input input-sm input-bordered" placeholder="https://" />
            </label>
            <label class="form-control">
              <span class="label-text text-[10px]">Colour</span>
              <input v-model="form.color" type="color" class="input input-sm input-bordered h-9 p-1" />
            </label>
            <label v-if="form.mode === 'new'" class="form-control">
              <span class="label-text text-[10px]">Starter</span>
              <select v-model="form.template" class="select select-sm select-bordered">
                <option value="blank">Blank</option>
                <option value="saas">SaaS org template</option>
              </select>
            </label>
          </div>
        </div>

        <!-- 3 · Projects ----------------------------------------------------- -->
        <div v-else-if="step === 'projects'" class="space-y-3">
          <div class="flex items-center justify-between gap-2">
            <p class="text-[11px] cp-muted">
              {{ form.projectIds.length }} of {{ projectCatalog.length }} project(s) selected.
            </p>
            <button class="cp-chip" @click="selectAllProjects">Select all</button>
          </div>
          <div v-if="!projectCatalog.length" class="rounded-xl border border-[var(--cp-border)] p-3 text-[11px] cp-muted">
            No project was discovered yet. Skip this step and create one later from
            Company → Projects.
          </div>
          <div v-else class="grid max-h-[38vh] grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
            <button
              v-for="item in projectCatalog"
              :key="projectKey(item)"
              class="flex items-start gap-2 rounded-xl border p-2 text-left transition"
              :class="form.projectIds.includes(projectKey(item))
                ? 'border-[var(--cp-lime-strong)] bg-[var(--cp-lime-soft)]'
                : 'border-[var(--cp-border)] hover:border-[var(--cp-border-strong)]'"
              @click="toggleProject(projectKey(item))"
            >
              <i
                class="mt-0.5 text-[10px]"
                :class="form.projectIds.includes(projectKey(item))
                  ? 'fa-solid fa-square-check cp-lime'
                  : 'fa-regular fa-square cp-muted-soft'"
              ></i>
              <span class="min-w-0">
                <span class="block truncate text-[11px] font-bold">{{ projectName(item) }}</span>
                <span v-if="projectPath(item)" class="mt-0.5 block truncate text-[9px] cp-muted-soft">
                  {{ projectPath(item) }}
                </span>
              </span>
            </button>
          </div>
        </div>

        <!-- 4 · AI models ---------------------------------------------------- -->
        <div v-else-if="step === 'models'" class="space-y-3">
          <label class="form-control">
            <span class="label-text text-[10px]">Default model for the company teams</span>
            <select v-model="form.defaultModel" class="select select-sm select-bordered">
              <option value="">Keep the project default</option>
              <option v-for="model in models" :key="model.name || model.id" :value="model.name || model.id">
                {{ model.name || model.id }}
              </option>
            </select>
          </label>
          <p v-if="modelsError" class="text-[10px] text-warning">{{ modelsError }}</p>
          <p class="rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3 text-[10px] cp-muted">
            Providers and API keys live in the installation AI settings; this only chooses the model
            the company starts with. You can change it later per project.
          </p>
        </div>

        <!-- 5 · Review ------------------------------------------------------- -->
        <div v-else class="space-y-3">
          <div class="rounded-xl border border-[var(--cp-border)] p-3">
            <p class="cp-eyebrow">Ready</p>
            <p class="mt-1 text-[12px] font-bold">
              {{ form.mode === 'existing'
                ? (existingCompanies.find(c => c.id === form.companyId)?.name || 'Company')
                : (form.name || 'New company') }}
            </p>
            <p v-if="form.description" class="text-[10px] cp-muted">{{ form.description }}</p>
            <dl class="mt-2 grid grid-cols-2 gap-2 text-[10px]">
              <div><dt class="cp-muted">Projects</dt><dd class="font-bold">{{ form.projectIds.length }}</dd></div>
              <div><dt class="cp-muted">Default model</dt><dd class="font-bold">{{ form.defaultModel || 'project default' }}</dd></div>
            </dl>
          </div>
          <p class="text-[10px] cp-muted">
            Finishing opens the company workspace. Teams, missions and the autopilot are configured
            from there — nothing is locked in by this wizard.
          </p>
        </div>

        <p v-if="error" class="mt-3 text-[11px] text-error">{{ error }}</p>
      </div>

      <!-- Footer: every step offers Skip, the wizard offers Skip setup -->
      <div class="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--cp-border)] p-3">
        <div class="flex items-center gap-2">
          <button
            class="cp-chip"
            :disabled="stepIndex === 0 || saving"
            @click="step = STEP_ORDER[Math.max(0, stepIndex - 1)]"
          >
            <i class="fa-solid fa-arrow-left text-[9px]"></i> Back
          </button>
          <button class="cp-chip" :disabled="saving" @click="skipWizard">Skip setup</button>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="!isLastStep"
            class="cp-chip"
            :disabled="saving"
            @click="skipStep"
          >
            Skip step
          </button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="saving || ('company' === step && form.mode === 'new' && !canCreate)"
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
