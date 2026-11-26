<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import SetupAssistantPanel from './SetupAssistantPanel.vue'

/**
 * First-run setup and always-available setup assistant for the `core`
 * workspace product.
 *
 * The project is the only hard requirement: a workspace without a project is
 * an empty shell. AI is optional — the assistant falls back to a preconfigured
 * QA guide until a provider/model is configured.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  mandatory: { type: Boolean, default: false },
  /** Render only the assistant (the floating "Need help?" entry point). */
  assistantOnly: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'done'])

const STEP_ORDER = ['welcome', 'project', 'ai', 'finish']
const step = ref('welcome')
const loading = ref(false)
const creating = ref(false)
const savingAi = ref(false)
const error = ref('')
const projects = ref([])
const selected = ref(null)
const projectPath = ref('')
const ai = ref({ configured: false, provider: '', model: '' })
const aiForm = ref({
  provider: 'deepseek',
  api_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model: 'deepseek-chat'
})

const PRESETS = {
  deepseek: { api_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  openai: { api_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  openrouter: { api_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini' },
  mistral: { api_url: 'https://api.mistral.ai/v1', model: 'mistral-small-latest' },
  groq: { api_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' },
  custom: { api_url: '', model: '' }
}

const canContinue = computed(() => Boolean(selected.value))
const hasProjects = computed(() => projects.value.length > 0)
const assistantContext = computed(() => ({
  step: step.value,
  projects: projects.value.length,
  hasAi: ai.value.configured
}))
const stepIndex = computed(() => STEP_ORDER.indexOf(step.value))

function presetChanged() {
  const preset = PRESETS[aiForm.value.provider] || PRESETS.custom
  aiForm.value.api_url = preset.api_url
  aiForm.value.model = preset.model
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const status = await API.get('/api/setup/status')
    ai.value = status?.ai || ai.value
    const list = await API.projects.list(false)
    projects.value = Array.isArray(list) ? list : []
    if (!selected.value && projects.value.length) selected.value = projects.value[0]
  } catch (ex) {
    error.value = ex?.message || 'Cannot read the setup status.'
  } finally {
    loading.value = false
  }
}

async function createProject() {
  const path = projectPath.value.trim()
  if (!path) {
    error.value = 'Enter a Git URL or an absolute folder path.'
    return
  }
  creating.value = true
  error.value = ''
  try {
    const created = await API.projects.create(path)
    const list = await API.projects.list(false)
    projects.value = Array.isArray(list) ? list : []
    selected.value = projects.value.find((item) => item.project_id === created?.project_id) || created
    if (!selected.value) {
      error.value = 'The project was created but could not be loaded. Reload and try again.'
      return
    }
    projectPath.value = ''
    step.value = 'ai'
  } catch (ex) {
    error.value = ex?.message || 'Cannot create the project.'
  } finally {
    creating.value = false
  }
}

async function saveAi() {
  if (!aiForm.value.api_key.trim()) {
    error.value = 'Paste the provider API key, or skip this step.'
    return
  }
  savingAi.value = true
  error.value = ''
  try {
    const saved = await API.post('/api/setup/ai', {
      provider: aiForm.value.provider,
      api_url: aiForm.value.api_url.trim(),
      api_key: aiForm.value.api_key.trim(),
      model: aiForm.value.model.trim()
    })
    ai.value = { configured: true, provider: saved.provider, model: saved.model }
    aiForm.value.api_key = ''
    step.value = 'finish'
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || 'Cannot save the AI provider.'
  } finally {
    savingAi.value = false
  }
}

function skipAi() {
  error.value = ''
  step.value = 'finish'
}

async function finish() {
  if (!selected.value) {
    step.value = 'project'
    error.value = 'Add a project before opening the workspace.'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await $storex.projects.activeProjectChanged(selected.value)
    emit('done', selected.value)
    // Workspace mode lands on the project catalogue: the new project is now
    // visible there, and picking it opens its workspace. Reloading the route
    // avoids a half-initialised shell while the project chats/models load.
    window.location.assign('/projects')
  } catch (ex) {
    error.value = ex?.message || 'Cannot open the workspace.'
  } finally {
    loading.value = false
  }
}

function selectProject(project) {
  selected.value = project
  error.value = ''
}

function next() {
  error.value = ''
  if (step.value === 'welcome') step.value = 'project'
  else if (step.value === 'project' && canContinue.value) step.value = 'ai'
  else if (step.value === 'ai') step.value = 'finish'
}

function back() {
  error.value = ''
  const index = stepIndex.value
  if (index > 0) step.value = STEP_ORDER[index - 1]
}

function close() {
  if (!props.mandatory) emit('close')
}

watch(() => props.open, (value) => { if (value) load() })
onMounted(() => { if (props.open) load() })
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-[120] flex items-center justify-center bg-base-300/80 p-2 backdrop-blur-sm">
    <div
      v-if="assistantOnly"
      class="flex h-[76vh] max-h-[760px] w-[96vw] max-w-2xl flex-col overflow-hidden rounded-2xl bg-base-100 p-0 shadow-2xl"
    >
      <div class="flex items-center justify-between border-b border-base-300 px-4 py-2">
        <span class="text-sm font-bold">Setup assistant</span>
        <button class="btn btn-ghost btn-xs" @click="close"><i class="fa-solid fa-xmark"></i></button>
      </div>
      <div class="min-h-0 flex-1">
        <SetupAssistantPanel :context="assistantContext" />
      </div>
    </div>

    <div
      v-else
      class="flex h-[88vh] max-h-[860px] w-[96vw] max-w-6xl flex-col overflow-hidden rounded-2xl bg-base-100 p-0 shadow-2xl"
    >
      <div class="flex items-center gap-3 border-b border-base-300 px-5 py-3">
        <div class="grid h-9 w-9 place-items-center rounded-xl bg-primary/10 text-primary">
          <i class="fa-solid fa-wand-magic-sparkles"></i>
        </div>
        <div class="min-w-0">
          <h1 class="truncate text-base font-extrabold">Set up your workspace</h1>
          <p class="truncate text-[11px] text-base-content/60">
            Add a project, connect a model if you want, and start reviewing.
          </p>
        </div>
        <button v-if="!mandatory" class="btn btn-ghost btn-xs ml-auto" @click="close">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div class="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div class="flex min-h-0 flex-1 flex-col overflow-y-auto p-5 sm:p-7">
          <div v-if="error" class="alert alert-warning mb-4 py-2 text-xs">
            <i class="fa-solid fa-triangle-exclamation"></i>
            <span>{{ error }}</span>
          </div>

          <div v-if="loading" class="flex flex-1 items-center justify-center gap-2 text-sm text-base-content/60">
            <span class="loading loading-spinner loading-sm"></span> Reading the setup state…
          </div>

          <template v-else>
            <!-- Welcome -->
            <div v-if="step === 'welcome'" class="flex max-w-xl flex-1 flex-col justify-center">
              <span class="badge badge-outline badge-sm mb-3 w-fit">codx-junior 0.0.1</span>
              <h2 class="text-2xl font-extrabold sm:text-3xl">One project. One model. One workspace.</h2>
              <p class="mt-3 text-sm leading-relaxed text-base-content/70">
                codx-junior works on a repository, not on a pasted snippet. Add the
                project first; the model is optional but makes the agent useful.
              </p>
              <ul class="mt-5 space-y-3 text-sm">
                <li class="flex gap-3"><i class="fa-solid fa-folder-plus mt-1 text-primary"></i><span><strong>Add a project</strong> — a Git URL or a server folder.</span></li>
                <li class="flex gap-3"><i class="fa-solid fa-key mt-1 text-primary"></i><span><strong>Connect a model</strong> — any OpenAI-compatible provider, or skip.</span></li>
                <li class="flex gap-3"><i class="fa-solid fa-magnifying-glass-chart mt-1 text-primary"></i><span><strong>Open the workspace</strong> — review files, Git and the agent.</span></li>
              </ul>
              <div class="mt-7 flex gap-2">
                <button class="btn btn-primary" @click="next">Start setup <i class="fa-solid fa-arrow-right"></i></button>
                <span class="self-center text-[11px] text-base-content/50">The assistant on the right is always available.</span>
              </div>
            </div>

            <!-- Project -->
            <div v-else-if="step === 'project'" class="flex flex-col gap-5">
              <div>
                <h2 class="text-xl font-extrabold">Add your project</h2>
                <p class="mt-1 text-sm text-base-content/60">
                  Pick an existing project or create one from a Git URL / server folder.
                </p>
              </div>

              <div v-if="hasProjects" class="space-y-2">
                <div class="text-xs font-bold uppercase tracking-wide text-base-content/50">Existing projects</div>
                <button
                  v-for="project in projects"
                  :key="project.project_id"
                  class="flex w-full items-center gap-3 rounded-xl border p-3 text-left transition"
                  :class="selected?.project_id === project.project_id ? 'border-primary bg-primary/5' : 'border-base-300 hover:border-primary/40'"
                  @click="selectProject(project)"
                >
                  <i class="fa-solid fa-cube text-primary"></i>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-sm font-semibold">{{ project.project_name }}</span>
                    <span class="block truncate text-[11px] text-base-content/50">{{ project.abs_project_path }}</span>
                  </span>
                  <i v-if="selected?.project_id === project.project_id" class="fa-solid fa-circle-check text-primary"></i>
                </button>
              </div>

              <div class="rounded-xl border border-base-300 bg-base-200/40 p-4">
                <label class="label py-1 text-xs font-semibold" for="wizard-project-path">
                  New project — Git URL or absolute folder
                </label>
                <div class="flex flex-col gap-2 sm:flex-row">
                  <input
                    id="wizard-project-path"
                    v-model="projectPath"
                    class="input input-bordered input-sm min-w-0 flex-1"
                    placeholder="https://github.com/you/repo.git or /srv/projects/my-app"
                    :disabled="creating"
                    @keyup.enter="createProject"
                  />
                  <button class="btn btn-primary btn-sm" :disabled="creating || !projectPath.trim()" @click="createProject">
                    <span v-if="creating" class="loading loading-spinner loading-xs"></span>
                    <i v-else class="fa-solid fa-plus"></i>
                    Add project
                  </button>
                </div>
                <p class="mt-2 text-[11px] text-base-content/50">
                  Git URLs are cloned. A folder path must exist on the server; codx-junior creates its <code>.codx</code> workspace.
                </p>
              </div>

              <div class="mt-auto flex items-center justify-between border-t border-base-300 pt-4">
                <button class="btn btn-ghost btn-sm" @click="back"><i class="fa-solid fa-arrow-left"></i> Back</button>
                <button class="btn btn-primary btn-sm" :disabled="!canContinue" @click="next">Continue <i class="fa-solid fa-arrow-right"></i></button>
              </div>
            </div>

            <!-- AI -->
            <div v-else-if="step === 'ai'" class="flex flex-col gap-4">
              <div>
                <h2 class="text-xl font-extrabold">Connect a model <span class="text-sm font-normal text-base-content/50">(optional)</span></h2>
                <p class="mt-1 text-sm text-base-content/60">
                  Any OpenAI-compatible provider works. The key stays on this server.
                  Without a model the workspace still browses files and Git, and the
                  setup assistant uses its preconfigured guide.
                </p>
              </div>

              <div v-if="ai.configured" class="alert alert-success py-2 text-xs">
                <i class="fa-solid fa-circle-check"></i>
                <span>AI is already configured: {{ ai.provider }} / {{ ai.model }}</span>
              </div>

              <div class="grid gap-3 sm:grid-cols-2">
                <label class="form-control">
                  <span class="label-text text-xs font-semibold">Provider</span>
                  <select v-model="aiForm.provider" class="select select-bordered select-sm" @change="presetChanged">
                    <option value="deepseek">DeepSeek</option>
                    <option value="openai">OpenAI</option>
                    <option value="openrouter">OpenRouter</option>
                    <option value="mistral">Mistral</option>
                    <option value="groq">Groq</option>
                    <option value="custom">Custom (OpenAI-compatible)</option>
                  </select>
                </label>
                <label class="form-control">
                  <span class="label-text text-xs font-semibold">Chat model</span>
                  <input v-model="aiForm.model" class="input input-bordered input-sm" placeholder="deepseek-chat" />
                </label>
                <label class="form-control sm:col-span-2">
                  <span class="label-text text-xs font-semibold">API key</span>
                  <input v-model="aiForm.api_key" type="password" class="input input-bordered input-sm" placeholder="sk-…" autocomplete="off" />
                </label>
                <label class="form-control sm:col-span-2">
                  <span class="label-text text-xs font-semibold">Base URL</span>
                  <input v-model="aiForm.api_url" class="input input-bordered input-sm" placeholder="https://api.deepseek.com/v1" />
                </label>
              </div>

              <div class="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-base-300 pt-4">
                <button class="btn btn-ghost btn-sm" @click="back"><i class="fa-solid fa-arrow-left"></i> Back</button>
                <div class="flex gap-2">
                  <button class="btn btn-ghost btn-sm" :disabled="savingAi" @click="skipAi">Skip for now</button>
                  <button class="btn btn-primary btn-sm" :disabled="savingAi || !aiForm.api_key.trim()" @click="saveAi">
                    <span v-if="savingAi" class="loading loading-spinner loading-xs"></span>
                    Save and continue
                  </button>
                </div>
              </div>
            </div>

            <!-- Finish -->
            <div v-else class="flex max-w-xl flex-1 flex-col justify-center">
              <div class="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-success/10 text-success">
                <i class="fa-solid fa-check text-xl"></i>
              </div>
              <h2 class="text-2xl font-extrabold">Ready to work</h2>
              <p class="mt-2 text-sm text-base-content/60">Your workspace is configured with:</p>
              <div class="mt-4 space-y-2 text-sm">
                <div class="flex items-center gap-2"><i class="fa-solid fa-cube text-primary"></i><strong>{{ selected?.project_name }}</strong><span class="text-base-content/50">{{ selected?.abs_project_path }}</span></div>
                <div class="flex items-center gap-2"><i class="fa-solid fa-robot text-primary"></i>
                  <span v-if="ai.configured">AI ready: {{ ai.provider }} / {{ ai.model }}</span>
                  <span v-else>Assistant-only mode — connect a model later from Settings → AI Models.</span>
                </div>
              </div>
              <p v-if="!ai.configured" class="mt-4 rounded-xl border border-base-300 bg-base-200/40 p-3 text-xs text-base-content/60">
                The setup assistant is still available from the “Need help?” button in the
                workspace. It answers from the preconfigured guide until a model is connected.
              </p>
              <div class="mt-6 flex gap-2">
                <button class="btn btn-ghost btn-sm" @click="back"><i class="fa-solid fa-arrow-left"></i> Back</button>
                <button class="btn btn-primary" :disabled="loading" @click="finish">
                  <span v-if="loading" class="loading loading-spinner loading-xs"></span>
                  Open workspace <i class="fa-solid fa-arrow-right"></i>
                </button>
              </div>
            </div>
          </template>
        </div>

        <aside class="flex min-h-[320px] w-full shrink-0 flex-col border-t border-base-300 lg:w-[370px] lg:border-l lg:border-t-0">
          <SetupAssistantPanel :context="assistantContext" />
        </aside>
      </div>

      <div class="flex items-center justify-between border-t border-base-300 bg-base-200/40 px-5 py-2 text-[11px] text-base-content/50">
        <span>Step {{ Math.max(1, stepIndex + 1) }} of {{ STEP_ORDER.length }} · project required, AI optional</span>
        <span class="flex items-center gap-2"><i class="fa-solid fa-robot"></i> Assistant available</span>
      </div>
    </div>
  </div>
</template>
