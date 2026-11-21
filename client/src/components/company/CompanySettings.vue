<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { API } from '@/api/api'
import { $storex } from '@/store'
import { AUTONOMY_LEVELS } from './companyMeta'
import { useCompanyI18n } from './i18n'

/**
 * Settings — the minimum an owner needs to run codx-junior.
 *
 * Company context, one AI provider/model/key and the autonomy envelope. The
 * rest of codx-junior (advanced models, users, infrastructure) stays in Global
 * Settings. The layout follows the company workspace design (cp-*).
 */
const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false }
})

const emit = defineEmits(['refresh', 'assistant', 'deleted', 'open-projects', 'new-company'])

const api = API.companies
const router = useRouter()
const { t } = useCompanyI18n()
const savingProfile = ref(false)
const savingAutonomy = ref(false)
const confirmDelete = ref(false)

const company = computed(() => props.snapshot?.company || {})
const autonomy = computed(() => company.value.autonomy || {})
const counts = computed(() => props.snapshot?.counts || {})
const members = computed(() => props.snapshot?.members || [])

const profile = reactive({
  name: '',
  description: '',
  mission: '',
  vision: '',
  values: '',
  website: ''
})

const policy = reactive({
  enabled: false,
  level: 'supervised',
  interval_minutes: 60,
  max_concurrent_missions: 2,
  max_missions_per_cycle: 1,
  daily_budget_cxjcoins: 0,
  require_human_acceptance: true
})

// ── AI setup ───────────────────────────────────────────────────────────────

const ai = reactive({
  loading: false,
  saving: false,
  error: '',
  providers: [],
  models: [],
  defaultModel: '',
  selectedProvider: '',
  newProvider: false,
  providerName: '',
  providerType: 'openai',
  apiUrl: '',
  apiKey: '',
  showKey: false,
  newModelName: '',
  newModelId: '',
  newModelError: '',
  roles: {},
  applyAll: true,
  savingRoles: false
})

const selectedProvider = computed(() => ai.providers.find(item => item.name === ai.selectedProvider) || null)
const defaultModelOptions = computed(() => {
  const names = ai.models.filter(model => (model.model_type || 'llm') === 'llm').map(model => model.name).filter(Boolean)
  if (ai.defaultModel && !names.includes(ai.defaultModel)) names.unshift(ai.defaultModel)
  return names
})
const roleRows = computed(() => [
  { id: 'llm_model', label: 'Reasoning' },
  { id: 'rag_model', label: 'RAG' },
  { id: 'wiki_model', label: 'Wiki' },
  { id: 'guardrail_model', label: 'Guardrails' },
  { id: 'embeddings_model', label: 'Embeddings' },
  { id: 'image_generation_model', label: 'Image generation' },
  { id: 'image_vision_model', label: 'Image vision' }
].map(role => ({ ...role, value: ai.roles[role.id] || '' })))

function providerFields(provider) {
  ai.providerName = provider?.name || ''
  ai.providerType = provider?.provider || 'openai'
  ai.apiUrl = provider?.api_url || ''
  ai.apiKey = provider?.api_key || ''
}

function chooseProvider(name) {
  ai.selectedProvider = name
  ai.newProvider = name === '__new__'
  if (ai.newProvider) {
    providerFields({ name: '', provider: 'openai', api_url: '', api_key: '' })
    return
  }
  providerFields(selectedProvider.value)
}

async function loadAI() {
  if (!props.canAdmin) return
  ai.loading = true
  ai.error = ''
  try {
    const settings = await API.settings.global.read()
    ai.providers = Array.isArray(settings.ai_providers) ? settings.ai_providers.map(item => ({ ...item })) : []
    ai.models = Array.isArray(settings.ai_models) ? settings.ai_models.map(item => ({ ...item })) : []
    ai.defaultModel = settings.llm_model || ''
    ai.roles = Object.fromEntries([
      'llm_model', 'rag_model', 'wiki_model', 'guardrail_model',
      'embeddings_model', 'image_generation_model', 'image_vision_model'
    ].map(key => [key, settings[key] || '']))
    if (ai.providers.length) {
      chooseProvider(ai.providers[0].name)
    } else {
      chooseProvider('__new__')
    }
  } catch (error) {
    ai.error = errorMessage(error)
  } finally {
    ai.loading = false
  }
}

function addModelToDraft() {
  ai.newModelError = ''
  const name = ai.newModelName.trim()
  const id = ai.newModelId.trim()
  if (!name) {
    ai.newModelError = 'Give the model a name you will recognise.'
    return
  }
  if (!id) {
    ai.newModelError = 'The provider model id is required (for example gpt-4o).'
    return
  }
  if (!ai.providerName.trim()) {
    ai.newModelError = 'Choose or name a provider first.'
    return
  }
  if (ai.models.some(model => model.name === name)) {
    ai.newModelError = 'A model with that name already exists.'
    return
  }
  ai.models.push({
    name,
    ai_provider: ai.providerName.trim(),
    ai_model: id,
    model_type: 'llm',
    tags: [],
    settings: { temperature: 0.7, context_length: 0 },
    system: '',
    prompt_template: '{ MESSAGE }'
  })
  if (!ai.defaultModel) ai.defaultModel = name
  ai.newModelName = ''
  ai.newModelId = ''
  notify(`Model "${name}" added — save the AI setup to apply it`, 'info')
}

async function saveAI() {
  if (!props.canAdmin) return
  ai.saving = true
  ai.error = ''
  try {
    const name = ai.providerName.trim()
    if (!name) throw new Error('The provider needs a name.')
    if (!ai.selectedProvider && !ai.newProvider) throw new Error('Select a provider first.')
    if (ai.newProvider && ai.providers.some(item => item.name === name)) {
      throw new Error('A provider with that name already exists — select it instead.')
    }
    if (ai.applyAll && !ai.defaultModel) {
      throw new Error('Choose or add a model before applying it to every AI role.')
    }

    const provider = {
      ...(selectedProvider.value || {}),
      name,
      provider: ai.providerType.trim() || 'openai',
      api_url: ai.apiUrl.trim(),
      api_key: ai.apiKey
    }
    const providers = [...ai.providers]
    const index = providers.findIndex(item => item.name === ai.selectedProvider)
    if (index >= 0) providers.splice(index, 1, provider)
    else providers.push(provider)

    await API.settings.global.saveSection('ai_providers', providers)
    await API.settings.global.saveSection('ai_models', ai.models)

    if (ai.applyAll) {
      const settings = await API.settings.global.read()
      const model = ai.defaultModel || ''
      for (const key of [
        'llm_model', 'rag_model', 'wiki_model', 'guardrail_model',
        'embeddings_model', 'image_generation_model', 'image_vision_model'
      ]) {
        settings[key] = model
      }
      await API.settings.global.write(settings)
    } else {
      await API.settings.global.saveSection('llm_model', ai.defaultModel || '')
    }

    ai.providers = providers
    ai.selectedProvider = name
    ai.newProvider = false
    notify('AI setup saved ✓', 'success')
    await loadAI()
    ai.selectedProvider = name
    chooseProvider(name)
  } catch (error) {
    ai.error = errorMessage(error)
    notify(ai.error, 'error')
  } finally {
    ai.saving = false
  }
}

function openAdvancedSettings(tab = '') {
  const linked = (props.snapshot?.projects || []).find(project => project.available)
  const project = $storex.projects.activeProject ||
    ($storex.projects.allProjects || []).find(item => item.project_id === linked?.project_id)
  const projectName = project?.project_name || linked?.discovered_name || linked?.project_id
  if (!projectName) {
    notify('Create or link a project before opening advanced settings.', 'error')
    return
  }
  router.push({
    name: 'global-settings',
    params: { projectName },
    ...(tab ? { query: { tab } } : {})
  }).catch(() => {})
}

function openAdvancedAI() {
  const linked = (props.snapshot?.projects || []).find(project => project.available)
  const project = $storex.projects.activeProject ||
    ($storex.projects.allProjects || []).find(item => item.project_id === linked?.project_id)
  const projectName = project?.project_name || linked?.discovered_name || linked?.project_id
  if (!projectName) {
    notify('Open a project before changing advanced AI settings.', 'error')
    return
  }
  router.push({
    name: 'global-settings',
    params: { projectName },
    query: { tab: 'ai' }
  }).catch(() => {})
}

// ── Company profile & autonomy ─────────────────────────────────────────────

watch(company, () => {
  Object.assign(profile, {
    name: company.value.name || '',
    description: company.value.description || '',
    mission: company.value.mission || '',
    vision: company.value.vision || '',
    values: (company.value.values || []).join(', '),
    website: company.value.website || ''
  })
  Object.assign(policy, {
    enabled: Boolean(autonomy.value.enabled),
    level: autonomy.value.level || 'supervised',
    interval_minutes: Number(autonomy.value.interval_minutes || 60),
    max_concurrent_missions: Number(autonomy.value.max_concurrent_missions || 2),
    max_missions_per_cycle: Number(autonomy.value.max_missions_per_cycle || 1),
    daily_budget_cxjcoins: Number(autonomy.value.daily_budget_cxjcoins || 0),
    require_human_acceptance: autonomy.value.require_human_acceptance !== false
  })
}, { immediate: true })

onMounted(loadAI)

function notify(text, type = 'info') {
  try { $storex.ui.addNotification({ text, type }) } catch { /* best effort */ }
}

function errorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || String(error)
}

async function saveProfile() {
  savingProfile.value = true
  try {
    await api.update(props.companyId, {
      name: profile.name.trim(),
      description: profile.description,
      mission: profile.mission,
      vision: profile.vision,
      values: profile.values.split(',').map(value => value.trim()).filter(Boolean),
      website: profile.website
    })
    emit('refresh')
    notify('Company context saved ✓', 'success')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    savingProfile.value = false
  }
}

async function saveAutonomy() {
  savingAutonomy.value = true
  try {
    await api.updateAutonomy(props.companyId, {
      enabled: Boolean(policy.enabled),
      level: policy.level,
      interval_minutes: Number(policy.interval_minutes || 60),
      max_concurrent_missions: Number(policy.max_concurrent_missions || 2),
      max_missions_per_cycle: Number(policy.max_missions_per_cycle || 1),
      daily_budget_cxjcoins: Number(policy.daily_budget_cxjcoins || 0),
      require_human_acceptance: Boolean(policy.require_human_acceptance)
    })
    emit('refresh')
    notify('Autonomy saved ✓', 'success')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    savingAutonomy.value = false
  }
}

async function deleteCompany() {
  try {
    await api.remove(props.companyId)
    emit('deleted')
  } catch (error) {
    notify(errorMessage(error), 'error')
  }
}

function ask(prompt) {
  emit('assistant', prompt)
}
</script>

<template>
  <div class="space-y-4">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="cp-eyebrow">Administration</p>
        <h1 class="mt-1 text-xl font-extrabold tracking-tight md:text-2xl">{{ t('settings.title') }}</h1>
        <p class="mt-1 text-[11px] leading-relaxed cp-muted">
          {{ t('settings.subtitle') }}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button class="cp-chip" @click="openAdvancedSettings()">
          <i class="fa-solid fa-sliders text-[9px]"></i> Advanced settings
        </button>
        <button class="cp-chip" @click="ask('Review my company settings and tell me what is missing')">
          <i class="fa-solid fa-wand-magic-sparkles text-[9px]"></i> Ask AI
        </button>
      </div>
    </header>

    <div class="grid gap-4 xl:grid-cols-12">
      <div class="space-y-4 xl:col-span-7">
        <!-- Company context -->
        <section class="cp-card cp-card-pad">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h2 class="cp-title">{{ t('settings.companyContext') }}</h2>
              <p class="mt-0.5 text-[10px] cp-muted">{{ t('settings.companyHint') }}</p>
            </div>
            <span class="cp-icon-tile cp-icon-tile--lime !h-7 !w-7"><i class="fa-solid fa-building text-[10px]"></i></span>
          </div>

          <div class="mt-3 grid gap-2.5 sm:grid-cols-2">
            <label class="cp-field sm:col-span-2">
              <span>{{ t('settings.name') }}</span>
              <input v-model="profile.name" class="cp-input" :disabled="!canManage" />
            </label>
            <label class="cp-field sm:col-span-2">
              <span>{{ t('settings.description') }}</span>
              <textarea v-model="profile.description" class="cp-textarea" rows="2" :disabled="!canManage" placeholder="One paragraph a new teammate would understand."></textarea>
            </label>
            <label class="cp-field sm:col-span-2">
              <span>{{ t('settings.mission') }}</span>
              <textarea v-model="profile.mission" class="cp-textarea" rows="2" :disabled="!canManage" placeholder="Why the company exists and what winning looks like."></textarea>
            </label>
          </div>

          <details class="mt-3 border-t border-[var(--cp-border)] pt-2">
            <summary class="cursor-pointer list-none text-[9px] font-semibold cp-muted transition hover:text-[var(--cp-text)]">
              {{ t('settings.moreContext') }}
            </summary>
            <div class="mt-2 grid gap-2.5 sm:grid-cols-2">
              <label class="cp-field sm:col-span-2">
                <span>{{ t('settings.vision') }}</span>
                <textarea v-model="profile.vision" class="cp-textarea" rows="2" :disabled="!canManage"></textarea>
              </label>
              <label class="cp-field">
                <span>{{ t('settings.values') }}</span>
                <input v-model="profile.values" class="cp-input" :disabled="!canManage" />
              </label>
              <label class="cp-field">
                <span>{{ t('settings.website') }}</span>
                <input v-model="profile.website" class="cp-input" :disabled="!canManage" placeholder="https://" />
              </label>
            </div>
          </details>

          <div class="mt-3 flex justify-end">
            <button class="cp-cta" :disabled="!canManage || savingProfile" @click="saveProfile">
              <span v-if="savingProfile" class="loading loading-spinner loading-xs"></span>
              {{ t('settings.saveContext') }}
            </button>
          </div>
        </section>

        <!-- AI provider, key and default model -->
        <section class="cp-card cp-card-pad">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h2 class="cp-title">{{ t('settings.aiConnection') }}</h2>
              <p class="mt-0.5 text-[10px] cp-muted">{{ t('settings.aiHint') }}</p>
            </div>
            <button class="cp-chip !h-7 !text-[8.5px]" @click="openAdvancedAI">
              <i class="fa-solid fa-sliders text-[8px]"></i> {{ t('settings.advanced') }}
            </button>
          </div>

          <div v-if="!canAdmin" class="mt-3 rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface-soft)] p-3">
            <p class="text-[10px] font-semibold">{{ t('settings.adminOnly') }}</p>
            <p class="mt-0.5 text-[9px] cp-muted">Ask an admin to update them in Global Settings → AI Providers.</p>
          </div>

          <div v-else-if="ai.loading" class="mt-4 flex items-center gap-2 text-[10px] cp-muted">
            <span class="loading loading-spinner loading-xs"></span> {{ t('settings.loading') }}
          </div>

          <div v-else class="mt-3 space-y-2.5">
            <div v-if="ai.error" class="rounded-lg border border-[var(--cp-red)]/30 bg-[var(--cp-red-soft)] p-2 text-[9px] text-[var(--cp-error-ink)]">
              {{ ai.error }}
            </div>

            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <p class="text-[9px] font-bold">Current global model roles</p>
              <div class="mt-1.5 flex flex-wrap gap-1">
                <span
                  v-for="role in roleRows"
                  :key="role.id"
                  class="rounded-full border border-[var(--cp-border)] bg-[var(--cp-surface)] px-1.5 py-0.5 text-[8px] cp-muted"
                  :title="role.value || 'Not set'"
                >
                  {{ role.label }}: <strong class="text-[var(--cp-text)]">{{ role.value || '—' }}</strong>
                </span>
              </div>
            </div>

            <label class="cp-field">
              <span>{{ t('settings.provider') }}</span>
              <select class="cp-select" :value="ai.selectedProvider" :disabled="ai.saving" @change="chooseProvider($event.target.value)">
                <option v-for="provider in ai.providers" :key="provider.name" :value="provider.name">{{ provider.name }}</option>
                <option value="__new__">+ Add provider</option>
              </select>
            </label>

            <div class="grid gap-2.5 sm:grid-cols-2">
              <label class="cp-field">
                <span>{{ t('settings.providerName') }}</span>
                <input v-model="ai.providerName" class="cp-input" :disabled="ai.saving || !ai.newProvider" placeholder="e.g. OpenAI" />
                <span v-if="!ai.newProvider" class="cp-help">Existing provider names are fixed. Choose “+ Add provider” to create one.</span>
              </label>
              <label class="cp-field">
                <span>{{ t('settings.protocol') }}</span>
                <input v-model="ai.providerType" class="cp-input" :disabled="ai.saving" list="cp-ai-protocols" placeholder="openai" />
                <datalist id="cp-ai-protocols">
                  <option value="openai"></option>
                  <option value="anthropic"></option>
                  <option value="ollama"></option>
                  <option value="llmfactory"></option>
                </datalist>
              </label>
              <label class="cp-field sm:col-span-2">
                <span>{{ t('settings.apiUrl') }}</span>
                <input v-model="ai.apiUrl" class="cp-input font-mono" :disabled="ai.saving" placeholder="https://api.openai.com/v1" />
              </label>
              <label class="cp-field sm:col-span-2">
                <span>{{ t('settings.apiKey') }}</span>
                <div class="relative">
                  <input v-model="ai.apiKey" class="cp-input pr-16 font-mono" :type="ai.showKey ? 'text' : 'password'" :disabled="ai.saving" autocomplete="off" placeholder="sk-…" />
                  <button type="button" class="absolute right-1 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-[8px] font-bold cp-muted hover:text-[var(--cp-text)]" @click="ai.showKey = !ai.showKey">
                    {{ ai.showKey ? t('settings.hide') : t('settings.show') }}
                  </button>
                </div>
                <span class="cp-help">{{ t('settings.storedGlobally') }}</span>
              </label>
            </div>

            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <div class="flex items-center justify-between gap-2">
                <div>
                  <p class="text-[10px] font-bold">{{ t('settings.model') }}</p>
                  <p class="mt-0.5 text-[8.5px] cp-muted">{{ t('settings.modelHint') }}</p>
                </div>
                <span class="cp-chip !h-6 !text-[8px]">{{ defaultModelOptions.length }} available</span>
              </div>
              <label class="cp-field mt-2">
                <span>Model</span>
                <select v-model="ai.defaultModel" class="cp-select" :disabled="ai.saving || !defaultModelOptions.length">
                  <option value="">{{ defaultModelOptions.length ? 'Choose a model…' : 'No model yet' }}</option>
                  <option v-for="name in defaultModelOptions" :key="name" :value="name">{{ name }}</option>
                </select>
              </label>

              <div class="mt-2 grid gap-2 sm:grid-cols-2">
                <label class="cp-field">
                  <span>{{ t('settings.displayName') }}</span>
                  <input v-model="ai.newModelName" class="cp-input" :disabled="ai.saving" placeholder="e.g. GPT-4o" />
                </label>
                <label class="cp-field">
                  <span>{{ t('settings.modelId') }}</span>
                  <input v-model="ai.newModelId" class="cp-input font-mono" :disabled="ai.saving" placeholder="gpt-4o" />
                </label>
              </div>
              <div class="mt-1.5 flex flex-wrap items-center justify-between gap-2">
                <p v-if="ai.newModelError" class="text-[8.5px] text-[var(--cp-error-ink)]">{{ ai.newModelError }}</p>
                <p v-else class="text-[8.5px] cp-muted">{{ t('settings.modelHelp') }}</p>
                <button type="button" class="cp-chip !h-7 !text-[8.5px]" :disabled="ai.saving" @click="addModelToDraft">
                  <i class="fa-solid fa-plus text-[7px]"></i> {{ t('settings.addModelBtn') }}
                </button>
              </div>
            </div>

            <label class="flex items-start gap-2 rounded-xl border border-[var(--cp-border)] p-2.5 text-[9px] leading-relaxed">
              <input v-model="ai.applyAll" type="checkbox" class="mt-0.5 accent-[var(--cp-lime)]" />
              <span>
                <strong class="block text-[10px]">{{ t('settings.applyAll') }}</strong>
                {{ t('settings.applyAllHint') }}
                Use <button type="button" class="font-bold text-[var(--cp-accent-ink)]" @click="openAdvancedAI">Advanced</button> for separate models.
              </span>
            </label>

            <div class="flex items-center justify-between gap-2 pt-0.5">
              <p class="text-[8.5px] cp-muted">{{ t('settings.advancedHint') }}</p>
              <button class="cp-cta" :disabled="ai.saving" @click="saveAI">
                <span v-if="ai.saving" class="loading loading-spinner loading-xs"></span>
                {{ t('settings.saveAI') }}
              </button>
            </div>
          </div>
        </section>
      </div>

      <div class="space-y-4 xl:col-span-5">
        <!-- Autonomy -->
        <section class="cp-card cp-card-pad">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h2 class="cp-title">{{ t('settings.autonomy') }}</h2>
              <p class="mt-0.5 text-[10px] cp-muted">{{ t('settings.autonomyHint') }}</p>
            </div>
            <button
              class="relative h-5 w-9 shrink-0 rounded-full border transition-colors"
              :class="policy.enabled ? 'border-transparent bg-[var(--cp-lime)]' : 'border-[var(--cp-border-strong)] bg-[var(--cp-surface-strong)]'"
              role="switch"
              :aria-checked="policy.enabled"
              aria-label="Enable company autopilot"
              @click="policy.enabled = !policy.enabled"
            >
              <span class="absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-all" :class="policy.enabled ? 'left-[18px]' : 'left-0.5'"></span>
            </button>
          </div>

          <div class="mt-3 grid gap-2.5">
            <label class="cp-field">
              <span>{{ t('settings.level') }}</span>
              <select v-model="policy.level" class="cp-select" :disabled="!canManage">
                <option v-for="level in AUTONOMY_LEVELS" :key="level.id" :value="level.id">{{ level.label }} — {{ level.hint }}</option>
              </select>
            </label>
            <label class="cp-field">
              <span>{{ t('settings.dailyBudget') }}</span>
              <input v-model.number="policy.daily_budget_cxjcoins" type="number" min="0" step="1" class="cp-input" :disabled="!canManage" />
              <span class="cp-help">{{ t('settings.noLimit') }}</span>
            </label>
            <label class="flex items-start gap-2 text-[9.5px] leading-relaxed">
              <input v-model="policy.require_human_acceptance" type="checkbox" class="mt-0.5 accent-[var(--cp-lime)]" :disabled="!canManage" />
              <span>{{ t('settings.acceptance') }}</span>
            </label>
          </div>

          <details class="mt-2 border-t border-[var(--cp-border)] pt-2">
            <summary class="cursor-pointer list-none text-[9px] font-semibold cp-muted hover:text-[var(--cp-text)]">{{ t('settings.advancedCycle') }}</summary>
            <div class="mt-2 grid grid-cols-2 gap-2">
              <label class="cp-field">
                <span>{{ t('settings.interval') }}</span>
                <input v-model.number="policy.interval_minutes" type="number" min="5" class="cp-input" :disabled="!canManage" />
              </label>
              <label class="cp-field">
                <span>{{ t('settings.concurrent') }}</span>
                <input v-model.number="policy.max_concurrent_missions" type="number" min="1" class="cp-input" :disabled="!canManage" />
              </label>
              <label class="cp-field col-span-2">
                <span>{{ t('settings.perCycle') }}</span>
                <input v-model.number="policy.max_missions_per_cycle" type="number" min="1" class="cp-input" :disabled="!canManage" />
              </label>
            </div>
          </details>

          <div class="mt-3 flex justify-end">
            <button class="cp-cta" :disabled="!canManage || savingAutonomy" @click="saveAutonomy">
              <span v-if="savingAutonomy" class="loading loading-spinner loading-xs"></span>
              {{ t('settings.saveAutonomy') }}
            </button>
          </div>
        </section>

        <!-- People -->
        <section class="cp-card cp-card-pad">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h2 class="cp-title">People</h2>
              <p class="mt-0.5 text-[10px] cp-muted">Owners, managers and members.</p>
            </div>
            <button class="cp-chip" :disabled="!canAdmin" @click="ask('Add a person to my company')">
              <i class="fa-solid fa-user-plus text-[9px]"></i> Add person
            </button>
          </div>
          <div v-if="members.length" class="mt-2 space-y-1.5">
            <div v-for="member in members" :key="member.username" class="flex items-center gap-2 rounded-lg bg-[var(--cp-surface-soft)] px-2.5 py-1.5">
              <span class="min-w-0 flex-1 truncate text-[10px] font-semibold">{{ member.title || member.username }}</span>
              <span class="text-[8px] uppercase tracking-wide cp-muted">{{ member.company_role || 'member' }}</span>
            </div>
          </div>
          <p v-else class="mt-2 text-[10px] cp-muted">No people yet.</p>
        </section>

        <!-- Plan -->
        <section class="cp-card cp-card-pad">
          <div class="flex items-start justify-between gap-2">
            <h2 class="cp-title">{{ t('settings.plan') }}</h2>
            <button class="cp-chip" @click="emit('open-projects')" :disabled="!canManage">
              <i class="fa-solid fa-cubes text-[9px]"></i> Manage projects
            </button>
          </div>
          <div class="mt-2.5 grid grid-cols-2 gap-2">
            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <p class="cp-eyebrow !text-[7.5px]">{{ t('settings.teams') }}</p>
              <p class="mt-0.5 text-lg font-extrabold leading-none">{{ counts.teams || 0 }}</p>
            </div>
            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <p class="cp-eyebrow !text-[7.5px]">{{ t('settings.people') }}</p>
              <p class="mt-0.5 text-lg font-extrabold leading-none">{{ (counts.human || counts.humans || 0) + (counts.ai || 0) }}</p>
            </div>
            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <p class="cp-eyebrow !text-[7.5px]">{{ t('settings.projects') }}</p>
              <p class="mt-0.5 text-lg font-extrabold leading-none">{{ counts.projects || 0 }}</p>
            </div>
            <div class="rounded-xl bg-[var(--cp-surface-soft)] p-2.5">
              <p class="cp-eyebrow !text-[7.5px]">{{ t('settings.aiTeammates') }}</p>
              <p class="mt-0.5 text-lg font-extrabold leading-none">{{ counts.ai || 0 }}</p>
            </div>
          </div>
        </section>

        <section class="cp-card cp-card-pad">
          <h2 class="cp-title">Companies</h2>
          <p class="mt-0.5 text-[9.5px] leading-relaxed cp-muted">
            The default company covers every project. Create another company when the
            business, teams or project list must stay separate.
          </p>
          <button class="cp-chip mt-2.5" @click="emit('new-company')">
            <i class="fa-solid fa-plus text-[9px]"></i> New company
          </button>
        </section>

        <section v-if="canAdmin" class="cp-card cp-card-pad border-[var(--cp-red)]/25">
          <h2 class="cp-title text-error">{{ t('settings.danger') }}</h2>
          <p class="mt-0.5 text-[9.5px] leading-relaxed cp-muted">{{ t('settings.dangerHint') }}</p>
          <button class="cp-chip mt-2.5 !border-error/30 !text-error" @click="confirmDelete = true">{{ t('settings.delete') }}</button>
        </section>
      </div>
    </div>

    <div v-if="confirmDelete" class="fixed inset-0 z-[95] grid place-items-center bg-black/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true">
      <div class="cp-card w-full max-w-sm p-4">
        <h3 class="text-base font-extrabold">{{ t('settings.deleteConfirm') }} · {{ company.name }}</h3>
        <p class="mt-1 text-[10px] leading-relaxed cp-muted">This removes the company document and its journal. Project files, boards and chats are kept.</p>
        <div class="mt-3 flex justify-end gap-2">
          <button class="cp-chip" @click="confirmDelete = false">{{ t('settings.cancel') }}</button>
          <button class="cp-chip !border-transparent !bg-[var(--cp-red)] !text-white" @click="deleteCompany(); confirmDelete = false">{{ t('settings.delete') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
