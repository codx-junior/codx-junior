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
// The assistant is an explicit, collapsible card: it starts hidden so the
// setup form always has the full width, and a fixed height so its answers can
// never grow over the form.
const assistantVisible = ref(false)
const ai = ref({ configured: false, provider: '', model: '' })
const aiForm = ref({
  provider: 'deepseek',
  api_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model: 'deepseek-flash'
})

// Real, currently published chat model ids per provider. Selecting a provider
// prefills the chat-model input with a model that actually exists, instead of a
// placeholder. The field stays editable for custom deployments.
const PRESETS = {
  deepseek: {
    api_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-flash',
    note: 'deepseek-flash (fast) · deepseek-v4-pro (deep reasoning)'
  },
  openai: {
    api_url: 'https://api.openai.com/v1',
    model: 'gpt-4.1-mini',
    note: 'gpt-4.1-mini · gpt-4.1 · gpt-4o-mini'
  },
  anthropic: {
    api_url: 'https://api.anthropic.com/v1',
    model: 'claude-sonnet-4-5',
    note: 'claude-sonnet-4-5 · claude-opus-4-1 · claude-3-5-haiku-latest'
  },
  ollama: {
    api_url: 'http://localhost:11434/v1',
    model: 'qwen2.5-coder:7b',
    note: 'qwen2.5-coder:7b · qwen2.5:7b (local Ollama)'
  },
  openrouter: {
    api_url: 'https://openrouter.ai/api/v1',
    model: 'openai/gpt-4o-mini',
    note: 'Any model from the OpenRouter catalogue'
  },
  mistral: {
    api_url: 'https://api.mistral.ai/v1',
    model: 'mistral-small-latest',
    note: 'mistral-small-latest · mistral-large-latest'
  },
  groq: {
    api_url: 'https://api.groq.com/openai/v1',
    model: 'llama-3.3-70b-versatile',
    note: 'llama-3.3-70b-versatile and other Groq models'
  },
  custom: { api_url: '', model: '', note: 'Any OpenAI-compatible endpoint' }
}

const canContinue = computed(() => Boolean(selected.value))
const hasProjects = computed(() => projects.value.length > 0)
const assistantContext = computed(() => ({
  step: step.value,
  projects: projects.value.length,
  hasAi: ai.value.configured
}))
const stepIndex = computed(() => STEP_ORDER.indexOf(step.value))
const presetNote = computed(() => (PRESETS[aiForm.value.provider] || PRESETS.custom).note)

// Local Ollama has no API key. Send the conventional placeholder so the
// provider is stored as configured without asking the user for a fake secret.
function isLocalProvider() {
  return aiForm.value.provider === 'ollama'
}

function effectiveApiKey() {
  const key = aiForm.value.api_key.trim()
  return isLocalProvider() && !key ? 'ollama' : key
}

function toggleAssistant() {
  assistantVisible.value = !assistantVisible.value
}

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
    // Reflect an already-configured provider/model in the form so the fields
    // are never prefilled with a different provider's defaults.
    if (status?.ai?.configured) {
      const provider = status.ai.provider || aiForm.value.provider
      aiForm.value.provider = provider
      aiForm.value.model = status.ai.model || aiForm.value.model
      const preset = PRESETS[provider]
      if (preset?.api_url) aiForm.value.api_url = preset.api_url
    }
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
  if (!effectiveApiKey()) {
    error.value = 'Paste the provider API key, or skip this step.'
    return
  }
  savingAi.value = true
  error.value = ''
  try {
    const saved = await API.post('/api/setup/ai', {
      provider: aiForm.value.provider,
      api_url: aiForm.value.api_url.trim(),
      api_key: effectiveApiKey(),
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
    // Land directly on the project workspace instead of the `/projects`
    // catalogue: that route has no project segment, which is why the left-bar
    // project selector looked missing on the first visit.
    await $storex.company.ensureLoaded().catch(() => {})
    const company = $storex.company?.defaultCompany || $storex.company?.activeCompany
    const slug = company?.slug
    const projectName = selected.value?.project_name
    const target = slug && projectName
      ? `/${slug}/workspace/${encodeURIComponent(projectName)}`
      : '/projects'
    window.location.assign(target)
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
  <template v-if="open">
    <!-- Floating entry point (the bottom-right codx-junior button). A card,
         not a full-screen modal: it can be closed and reopened in place. -->
    <section
      v-if="assistantOnly"
      class="fixed bottom-36 right-3 z-[130] flex h-[min(70vh,520px)] w-[min(94vw,380px)] flex-col overflow-hidden rounded-2xl border border-base-300 bg-base-100 shadow-2xl lg:bottom-24 lg:right-5"
      role="dialog"
      aria-label="Setup assistant"
    >
      <header class="flex items-center gap-2 border-b border-base-300 bg-base-200/60 px-3 py-2">
        <img src="/only_icon.png" alt="codx-junior" class="h-7 w-7 rounded-lg" />
        <div class="min-w-0 flex-1">
          <div class="truncate text-sm font-bold">Setup assistant</div>
          <div class="truncate text-[10px] text-base-content/55">
            {{ ai.configured ? 'AI chat is ready' : 'Preconfigured guide' }}
          </div>
        </div>
        <button type="button" class="btn btn-ghost btn-xs btn-circle" aria-label="Close setup assistant" @click="close">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </header>
      <div class="min-h-0 flex-1">
        <SetupAssistantPanel :context="assistantContext" compact hide-header />
      </div>
    </section>

    <div
      v-else
      class="fixed inset-0 z-[120] flex items-center justify-center bg-base-300/80 p-2 backdrop-blur-sm"
    >
      <div class="flex h-[90vh] max-h-[900px] w-[96vw] max-w-6xl flex-col overflow-hidden rounded-2xl bg-base-100 p-0 shadow-2xl">
        <div class="flex items-center gap-3 border-b border-base-300 px-5 py-3">
          <div class="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-xl bg-primary/10">
            <img src="/only_icon.png" alt="codx-junior" class="h-7 w-7 rounded-lg" />
          </div>
          <div class="min-w-0">
            <h1 class="truncate text-base font-extrabold">Set up your workspace</h1>
            <p class="truncate text-[11px] text-base-content/60">
              Add a project, connect a model if you want, and start reviewing.
            </p>
          </div>

          <!-- The assistant is opt-in: it stays hidden until requested and can
               be hidden again. -->
          <button
            type="button"
            class="btn btn-ghost btn-sm ml-auto shrink-0 gap-2"
            :class="assistantVisible ? 'bg-primary/10 text-primary' : ''"
            :aria-expanded="assistantVisible"
            :aria-label="assistantVisible ? 'Hide setup assistant' : 'Show setup assistant'"
            @click="toggleAssistant"
          >
            <img src="/only_icon.png" alt="" class="h-5 w-5 rounded-md" aria-hidden="true" />
            <span class="hidden sm:inline">{{ assistantVisible ? 'Hide assistant' : 'Assistant' }}</span>
          </button>
          <button v-if="!mandatory" class="btn btn-ghost btn-xs btn-circle shrink-0" aria-label="Close setup" @click="close">
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
                <div class="mt-7 flex flex-wrap items-center gap-3">
                  <button class="btn btn-primary" @click="next">Start setup <i class="fa-solid fa-arrow-right"></i></button>
                  <button class="btn btn-ghost btn-sm gap-2" @click="toggleAssistant">
                    <img src="/only_icon.png" alt="" class="h-5 w-5 rounded-md" aria-hidden="true" />
                    {{ assistantVisible ? 'Hide assistant' : 'Ask the assistant' }}
                  </button>
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

                <!-- New project: a clearly bordered, full-size input. The old
                     input-sm border disappeared against the card background. -->
                <div class="rounded-2xl border-2 border-primary/30 bg-base-200/40 p-4 sm:p-5">
                  <label class="mb-2 block text-sm font-bold" for="wizard-project-path">
                    New project — Git URL or absolute folder
                  </label>
                  <div class="flex flex-col gap-2 sm:flex-row">
                    <input
                      id="wizard-project-path"
                      v-model="projectPath"
                      class="input input-bordered h-12 min-w-0 flex-1 border-base-content/25 bg-base-100 text-base"
                      placeholder="https://github.com/you/repo.git or /srv/projects/my-app"
                      :disabled="creating"
                      @keyup.enter="createProject"
                    />
                    <button
                      class="btn btn-primary h-12 shrink-0 gap-2 px-5"
                      :disabled="creating || !projectPath.trim()"
                      @click="createProject"
                    >
                      <span v-if="creating" class="loading loading-spinner loading-xs"></span>
                      <i v-else class="fa-solid fa-plus"></i>
                      Add project
                    </button>
                  </div>
                  <p class="mt-2 text-xs text-base-content/55">
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

                <!-- One consistent field layout: every label sits directly
                     above a full-width control of the same height. -->
                <div class="grid items-start gap-x-4 gap-y-4 sm:grid-cols-2">
                  <div class="flex flex-col gap-1.5">
                    <label class="text-xs font-semibold" for="wizard-ai-provider">Provider</label>
                    <select
                      id="wizard-ai-provider"
                      v-model="aiForm.provider"
                      class="select select-bordered h-11 w-full bg-base-100"
                      @change="presetChanged"
                    >
                      <option value="deepseek">DeepSeek</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic (Claude)</option>
                      <option value="ollama">Ollama (local)</option>
                      <option value="openrouter">OpenRouter</option>
                      <option value="mistral">Mistral</option>
                      <option value="groq">Groq</option>
                      <option value="custom">Custom (OpenAI-compatible)</option>
                    </select>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <label class="text-xs font-semibold" for="wizard-ai-model">Chat model</label>
                    <input
                      id="wizard-ai-model"
                      v-model="aiForm.model"
                      class="input input-bordered h-11 w-full bg-base-100"
                      :placeholder="PRESETS[aiForm.provider]?.model || 'model id'"
                    />
                  </div>
                  <div class="flex flex-col gap-1.5 sm:col-span-2">
                    <label class="text-xs font-semibold" for="wizard-ai-key">
                      API key
                      <span v-if="isLocalProvider()" class="font-normal text-base-content/50">(not required for Ollama)</span>
                    </label>
                    <input
                      id="wizard-ai-key"
                      v-model="aiForm.api_key"
                      type="password"
                      class="input input-bordered h-11 w-full bg-base-100"
                      :placeholder="isLocalProvider() ? 'Not needed for a local Ollama' : 'sk-…'"
                      autocomplete="off"
                    />
                  </div>
                  <div class="flex flex-col gap-1.5 sm:col-span-2">
                    <label class="text-xs font-semibold" for="wizard-ai-url">Base URL</label>
                    <input
                      id="wizard-ai-url"
                      v-model="aiForm.api_url"
                      class="input input-bordered h-11 w-full bg-base-100"
                      placeholder="https://api.deepseek.com/v1"
                    />
                  </div>
                </div>
                <p class="text-xs text-base-content/50">{{ presetNote }}</p>

                <div class="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-base-300 pt-4">
                  <button class="btn btn-ghost btn-sm" @click="back"><i class="fa-solid fa-arrow-left"></i> Back</button>
                  <div class="flex gap-2">
                    <button class="btn btn-ghost btn-sm" :disabled="savingAi" @click="skipAi">Skip for now</button>
                    <button class="btn btn-primary btn-sm" :disabled="savingAi || !effectiveApiKey()" @click="saveAi">
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
                  <div class="flex items-center gap-2">
                    <img src="/only_icon.png" alt="" class="h-4 w-4 rounded" aria-hidden="true" />
                    <span v-if="ai.configured">AI ready: {{ ai.provider }} / {{ ai.model }}</span>
                    <span v-else>Assistant-only mode — connect a model later from Settings → AI Models.</span>
                  </div>
                </div>
                <p v-if="!ai.configured" class="mt-4 rounded-xl border border-base-300 bg-base-200/40 p-3 text-xs text-base-content/60">
                  The setup assistant is still available from the codx-junior button in the
                  bottom-right corner. It answers from the preconfigured guide until a model is connected.
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

          <!-- Assistant card: right on desktop, bottom on mobile. Its height is
               fixed so long answers scroll inside instead of covering the
               settings above. -->
          <aside
            v-if="assistantVisible"
            class="shrink-0 border-t border-base-300 bg-base-200/30 p-3 lg:w-[390px] lg:self-start lg:border-l lg:border-t-0"
          >
            <div class="flex h-[320px] flex-col overflow-hidden rounded-2xl border border-base-300 bg-base-100 shadow-sm lg:h-[520px]">
              <div class="flex items-center gap-2 border-b border-base-300 bg-base-200/60 px-3 py-2">
                <img src="/only_icon.png" alt="codx-junior" class="h-7 w-7 rounded-lg" />
                <div class="min-w-0 flex-1">
                  <div class="truncate text-sm font-bold">Setup assistant</div>
                  <div class="truncate text-[10px] text-base-content/55">
                    {{ ai.configured ? 'AI chat is ready' : 'Preconfigured guide — no AI key needed' }}
                  </div>
                </div>
                <button type="button" class="btn btn-ghost btn-xs btn-circle" aria-label="Hide setup assistant" @click="toggleAssistant">
                  <i class="fa-solid fa-xmark"></i>
                </button>
              </div>
              <div class="min-h-0 flex-1">
                <SetupAssistantPanel :context="assistantContext" compact hide-header />
              </div>
            </div>
          </aside>
        </div>

        <div class="flex items-center justify-between gap-2 border-t border-base-300 bg-base-200/40 px-5 py-2 text-[11px] text-base-content/50">
          <span>Step {{ Math.max(1, stepIndex + 1) }} of {{ STEP_ORDER.length }} · project required, AI optional</span>
          <button type="button" class="flex items-center gap-2 hover:text-base-content/80" @click="toggleAssistant">
            <img src="/only_icon.png" alt="" class="h-4 w-4 rounded" aria-hidden="true" />
            {{ assistantVisible ? 'Hide assistant' : 'Assistant available' }}
          </button>
        </div>
      </div>
    </div>
  </template>
</template>
