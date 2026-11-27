<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import { isAppEnabled } from '@/config/features'
import ProjectDetailt from '@/components/ProjectDetailt.vue'

/**
 * Workspace home.
 *
 * A start page in the spirit of the VS Code welcome screen: the active project
 * at a glance, the actions that start real work, and one-line explanations of
 * each section. It intentionally replaces the old "Top projects" dashboard with
 * its contribution charts.
 */
const route = useRoute()
const router = useRouter()

const user = computed(() => $storex.users.user)
const project = computed(() => $storex.projects.activeProject)
const projects = computed(() => $storex.projects.allProjects || [])
const aiModels = computed(() => $storex.projects.ai?.models || [])
const hasModel = computed(() => aiModels.value.length > 0)
const isAdmin = computed(() => Boolean($storex.users.isAdmin))

const greeting = computed(() => {
  const name = (user.value?.username || '').trim()
  return name ? `Welcome back, ${name}` : 'Welcome to codx-junior'
})

const projectPath = computed(() => project.value?.abs_project_path || '')

function enabled(appKey) {
  return isAppEnabled($storex.features, appKey)
}

const actions = computed(() => {
  const entries = [
    {
      key: 'new-chat',
      appKey: 'harness-mode',
      title: 'New chat',
      description: 'Brief the agent in harness mode: plan, edit, test and review the change.',
      icon: 'fa-solid fa-bolt',
      tone: 'primary',
      run: () => $storex.ui.openApp('harness-mode')
    },
    {
      key: 'tasks',
      appKey: 'task-list',
      title: 'Tasks',
      description: 'Every task across boards, with its owner, status and delivery.',
      icon: 'fa-solid fa-list-check',
      tone: 'success',
      run: () => $storex.ui.openApp('task-list')
    },
    {
      key: 'files',
      appKey: 'file-explorer',
      title: 'Files',
      description: 'Browse the project tree, open a file and jump to chat references.',
      icon: 'fa-regular fa-folder-open',
      tone: 'warning',
      run: () => $storex.ui.openApp('file-explorer')
    },
    {
      key: 'git',
      appKey: 'git-view',
      title: 'Git review',
      description: 'Branches, staged changes, diffs and the review of what the agent changed.',
      icon: 'fa-solid fa-code-branch',
      tone: 'error',
      run: () => $storex.ui.openGitView()
    },
    {
      key: 'knowledge',
      appKey: 'knowledge',
      title: 'Knowledge',
      description: 'Search the indexed project so answers use your real code and docs.',
      icon: 'fa-solid fa-magnifying-glass',
      tone: 'info',
      run: () => $storex.ui.setActiveTab('knowledge')
    },
    {
      key: 'vibe',
      appKey: 'vibe-coding',
      title: 'Vibe coding',
      description: 'Describe what you want next to the live changes and the running app.',
      icon: 'fa-solid fa-wand-magic-sparkles',
      tone: 'accent',
      run: () => $storex.ui.openApp('vibe-coding')
    },
    {
      key: 'settings',
      appKey: 'settings',
      title: 'Project settings',
      description: 'Models, Git, knowledge, scripts and permissions for this project.',
      icon: 'fa-solid fa-gear',
      tone: 'secondary',
      run: () => $storex.ui.openApp('settings')
    }
  ]
  return entries.filter(item => enabled(item.appKey))
})

const sidebarActions = computed(() => {
  const entries = [
    {
      key: 'global-settings',
      appKey: 'global-settings',
      title: 'Global settings',
      description: 'Instance-wide providers, users and updates.',
      icon: 'fa-solid fa-sliders',
      admin: true,
      run: () => $storex.ui.openApp('global-settings')
    },
    {
      key: 'projects',
      appKey: 'projects',
      title: 'All projects',
      description: 'Open, add or switch between the projects of this installation.',
      icon: 'fa-solid fa-cubes',
      run: () => $storex.ui.openApp('projects')
    }
  ]
  return entries.filter(item => (!item.admin || isAdmin.value) && enabled(item.appKey))
})

function toneClass(tone) {
  const tones = {
    primary: 'bg-primary/10 text-primary',
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    error: 'bg-error/10 text-error',
    info: 'bg-info/10 text-info',
    accent: 'bg-accent/10 text-accent',
    secondary: 'bg-secondary/10 text-secondary'
  }
  return tones[tone] || tones.primary
}

function openProjectSettings() {
  $storex.ui.openApp('settings')
}

function newProject() {
  $storex.ui.showNewProject(true)
}

async function switchProject(nextProject) {
  if (!nextProject?.project_id) return
  if (nextProject.project_id === project.value?.project_id) return
  try {
    await $storex.projects.activeProjectChanged(nextProject)
    const company = $storex.company?.activeCompany || $storex.company?.defaultCompany
    await router.replace({
      name: 'home',
      params: {
        companySlug: company?.slug || route.params.companySlug,
        projectName: nextProject.project_name
      }
    }).catch(() => {})
  } catch (error) {
    $storex.ui.addNotification({ text: 'Could not switch project', type: 'error' })
  }
}
</script>

<template>
  <div class="h-full w-full overflow-y-auto bg-base-100">
    <div class="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
      <!-- ── Header ─────────────────────────────────────────────────────── -->
      <header class="flex flex-col gap-5 sm:flex-row sm:items-center">
        <div class="flex min-w-0 items-center gap-3">
          <img src="/only_icon.png" alt="codx-junior" class="h-12 w-12 shrink-0 rounded-2xl" />
          <div class="min-w-0">
            <h1 class="truncate text-xl font-extrabold sm:text-2xl">{{ greeting }}</h1>
            <p class="mt-0.5 text-sm text-base-content/60">
              Your AI partner for this repository — plan, build, review and ship.
            </p>
          </div>
        </div>
        <div class="flex flex-wrap gap-2 sm:ml-auto">
          <button class="btn btn-primary btn-sm gap-2" @click="$storex.ui.openApp('harness-mode')">
            <i class="fa-solid fa-bolt" aria-hidden="true"></i>
            New chat
          </button>
          <button class="btn btn-ghost btn-sm gap-2 border border-base-300" @click="newProject">
            <i class="fa-solid fa-plus" aria-hidden="true"></i>
            Add project
          </button>
        </div>
      </header>

      <!-- ── Active project ─────────────────────────────────────────────── -->
      <section class="mt-6 rounded-2xl border border-base-300 bg-base-200/40 p-4 sm:p-5">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-center">
          <img
            :src="project?.project_icon || '/only_icon.png'"
            :alt="project?.project_name || 'Project'"
            class="h-14 w-14 shrink-0 rounded-2xl border border-base-content/10 object-cover"
          />
          <div class="min-w-0 flex-1">
            <div class="text-[10px] font-bold uppercase tracking-wider text-base-content/45">Active project</div>
            <h2 class="truncate text-lg font-bold">{{ project?.project_name || 'No project selected' }}</h2>
            <p class="truncate text-xs text-base-content/55">{{ projectPath || 'Add a repository to start working.' }}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <span
              class="badge badge-sm gap-1"
              :class="hasModel ? 'badge-success badge-outline' : 'badge-ghost'"
              :title="hasModel ? 'A chat model is configured' : 'Connect a model to chat with the agent'"
            >
              <i class="fa-solid fa-brain text-[10px]" aria-hidden="true"></i>
              {{ hasModel ? 'Model ready' : 'No model yet' }}
            </span>
            <span class="badge badge-sm badge-ghost gap-1">
              <i class="fa-solid fa-cubes text-[10px]" aria-hidden="true"></i>
              {{ projects.length }} project{{ projects.length === 1 ? '' : 's' }}
            </span>
            <ProjectDetailt
              v-if="projects.length"
              :model-value="project"
              @select="switchProject"
              class="min-w-[150px]"
            />
            <button
              v-if="project"
              class="btn btn-ghost btn-sm gap-2 border border-base-300"
              @click="openProjectSettings"
            >
              <i class="fa-solid fa-gear" aria-hidden="true"></i>
              Settings
            </button>
          </div>
        </div>
      </section>

      <!-- ── Start + help ───────────────────────────────────────────────── -->
      <div class="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_290px]">
        <section>
          <h2 class="text-[11px] font-bold uppercase tracking-wider text-base-content/45">Start</h2>
          <div class="mt-3 grid gap-3 sm:grid-cols-2">
            <button
              v-for="action in actions"
              :key="action.key"
              type="button"
              class="group flex h-full items-start gap-3 rounded-2xl border border-base-300 bg-base-100 p-4 text-left transition hover:border-primary/40 hover:bg-base-200/40"
              @click="action.run()"
            >
              <span class="grid h-10 w-10 shrink-0 place-items-center rounded-xl" :class="toneClass(action.tone)">
                <i :class="action.icon" aria-hidden="true"></i>
              </span>
              <span class="min-w-0 flex-1">
                <span class="block text-sm font-bold">{{ action.title }}</span>
                <span class="mt-1 block text-xs leading-relaxed text-base-content/55">{{ action.description }}</span>
              </span>
              <i class="fa-solid fa-arrow-right mt-1 text-[10px] text-base-content/25 transition group-hover:text-primary" aria-hidden="true"></i>
            </button>
          </div>

          <div v-if="sidebarActions.length" class="mt-3 grid gap-3 sm:grid-cols-2">
            <button
              v-for="action in sidebarActions"
              :key="action.key"
              type="button"
              class="flex items-start gap-3 rounded-2xl border border-dashed border-base-300 p-3 text-left transition hover:border-primary/40"
              @click="action.run()"
            >
              <i :class="action.icon" class="mt-0.5 w-5 text-center text-base-content/50" aria-hidden="true"></i>
              <span class="min-w-0 flex-1">
                <span class="block text-sm font-semibold">{{ action.title }}</span>
                <span class="mt-0.5 block text-[11px] leading-relaxed text-base-content/50">{{ action.description }}</span>
              </span>
            </button>
          </div>
        </section>

        <aside class="space-y-4">
          <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
            <h2 class="text-[11px] font-bold uppercase tracking-wider text-base-content/45">Getting started</h2>
            <ol class="mt-3 space-y-3 text-xs">
              <li class="flex gap-2">
                <span class="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary/15 text-[10px] font-bold text-primary">1</span>
                <span><strong class="block text-base-content/80">Add a project</strong>Clone a Git URL or point at a server folder.</span>
              </li>
              <li class="flex gap-2">
                <span class="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary/15 text-[10px] font-bold text-primary">2</span>
                <span><strong class="block text-base-content/80">Connect a model</strong>DeepSeek, OpenAI, Anthropic, Ollama or any OpenAI-compatible API.</span>
              </li>
              <li class="flex gap-2">
                <span class="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary/15 text-[10px] font-bold text-primary">3</span>
                <span><strong class="block text-base-content/80">Start a chat</strong>Brief the agent, then review the diff in Git.</span>
              </li>
            </ol>
            <button
              v-if="!hasModel"
              class="btn btn-outline btn-primary btn-sm mt-4 w-full"
              @click="openProjectSettings"
            >
              Connect a model
            </button>
          </div>

          <div class="rounded-2xl border border-base-300 bg-base-100 p-4">
            <h2 class="text-[11px] font-bold uppercase tracking-wider text-base-content/45">Sections</h2>
            <ul class="mt-2 divide-y divide-base-300 text-xs">
              <li class="flex items-center gap-2 py-2">
                <i class="fa-solid fa-bolt w-4 text-center text-primary" aria-hidden="true"></i>
                <span class="flex-1">Agent chat</span>
                <span class="text-[10px] text-base-content/40">harness runs</span>
              </li>
              <li class="flex items-center gap-2 py-2">
                <i class="fa-solid fa-list-check w-4 text-center text-success" aria-hidden="true"></i>
                <span class="flex-1">Tasks</span>
                <span class="text-[10px] text-base-content/40">planning</span>
              </li>
              <li class="flex items-center gap-2 py-2">
                <i class="fa-regular fa-folder-open w-4 text-center text-warning" aria-hidden="true"></i>
                <span class="flex-1">Files</span>
                <span class="text-[10px] text-base-content/40">browse</span>
              </li>
              <li class="flex items-center gap-2 py-2">
                <i class="fa-solid fa-code-branch w-4 text-center text-error" aria-hidden="true"></i>
                <span class="flex-1">Git review</span>
                <span class="text-[10px] text-base-content/40">diff &amp; commit</span>
              </li>
              <li class="flex items-center gap-2 py-2">
                <i class="fa-solid fa-magnifying-glass w-4 text-center text-info" aria-hidden="true"></i>
                <span class="flex-1">Knowledge</span>
                <span class="text-[10px] text-base-content/40">indexed code</span>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>
