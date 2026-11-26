<script setup>
import { computed, onMounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import { API } from '@/api/api'
import Login from './components/user/Login.vue'
import CompanyWizard from './components/company/CompanyWizard.vue'
import WorkspaceWizard from './components/setup/WorkspaceWizard.vue'
import Toast from './components/Toast.vue'
import DesktopSidebar from './components/layout/DesktopSidebar.vue'
import NewProject from './components/project/NewProject.vue'
import MobileShell from './components/mobile/MobileShell.vue'
import NewTaskDialog from './components/kanban/NewTaskDialog.vue'
import BoardQuickChat from './components/kanban/board/BoardQuickChat.vue'
import NewMeetingModal from './components/meet/NewMeetingModal.vue'
import IncomingMeetInvitation from './components/meet/IncomingMeetInvitation.vue'
import VoiceCallOverlay from './components/voice/VoiceCallOverlay.vue'
import FileViewerModal from './components/filebrowser/FileViewerModal.vue'

const route = useRoute()
const router = useRouter()

// New (left bar) → codx-junior assistant chat in a modal. The card lands on the
// codx-junior board; the schedule selector decides whether it starts now, runs
// in the human non-working hours, or at a concrete instant.
const ASSISTANT_CHAT_COLUMNS = [{ title: 'tasks', value: 'tasks' }]
function onAssistantChatCreated(chat) {
  $storex.ui.showNewAssistantChat(false)
  if (chat?.id) $storex.ui.openApp({ appKey: 'chat', payload: { chat } })
}

const uiReady = computed(() => $storex.ui.uiReady)
const isLogin = computed(() => !$storex.users.user)
// Install-time feature profile. 0.0.1 installs default to `core`: the company
// shell (and this wizard) is hidden and the app starts in the project workspace.
const companyEnabled = computed(() => $storex.features?.companyEnabled !== false)

// ── First-run gate ─────────────────────────────────────────────────────────
// A fresh installation has no company basics yet. Until the owner finishes the
// company wizard the product must not be usable: every route (including a deep
// link opened directly) is replaced by the blocking setup screen. The state is
// server-side (`setup_required`), so a reload cannot bypass it and a configured
// upgrade or a re-run from the company menu is never locked.
const onboardingChecked = ref(false)
const setupRequired = ref(false)
const workspaceSetupRequired = ref(false)
const assistantOpen = ref(false)
const setupError = ref('')
let setupCheck = null

/**
 * Read the installation setup state.
 *
 * @param {Boolean} force Re-read even when a check is already in flight.
 * @returns {Promise<void>}
 */
function checkSetup(force = false) {
  if (isLogin.value) return Promise.resolve()
  if (setupCheck && !force) return setupCheck
  // Fail closed: while the state is being read (including a retry after an
  // error) the shell stays hidden instead of flashing the product.
  setupError.value = ''
  onboardingChecked.value = false
  setupRequired.value = false
  setupCheck = (async () => {
    try {
      // The feature profile decides whether the company shell exists at all.
      // Load it first so a `core` install never flashes the company wizard.
      await $storex.features?.ensureLoaded?.()
      if (!companyEnabled.value) {
        // Workspace mode: a project is the only hard requirement. A fresh
        // install starts in the workspace wizard instead of an empty project
        // list; the assistant inside stays available afterwards.
        setupRequired.value = false
        setupError.value = ''
        try {
          const projects = await API.projects.list(false)
          workspaceSetupRequired.value = !(Array.isArray(projects) && projects.length > 0)
        } catch (ex) {
          // Fail toward the guided flow: the wizard can retry and explain.
          workspaceSetupRequired.value = true
        }
        return
      }
      const status = await API.companies.onboarding()
      setupRequired.value = Boolean(
        status?.setup_required ?? status?.needs_setup
      )
    } catch (error) {
      // Fail closed: an unknown setup state cannot silently unlock the app.
      setupRequired.value = false
      setupError.value = error?.message || 'Cannot read the installation setup state.'
    } finally {
      onboardingChecked.value = true
      setupCheck = null
    }
  })()
  return setupCheck
}

/** The wizard finished: unlock the shell and land on the company workspace. */
async function onSetupDone() {
  setupRequired.value = false
  setupError.value = ''
  onboardingChecked.value = true
  await $storex.company.loadCompanies().catch(() => {})
  // The company the owner just configured is the active one, even when the
  // fresh-install default placeholder still exists next to it.
  const company = $storex.company.activeCompany || $storex.company.defaultCompany
  await router.replace(
    company?.slug
      ? { name: 'company-home', params: { companySlug: company.slug } }
      : '/'
  ).catch(() => {})
}

/** Workspace setup finished: unlock the project shell. */
function onWorkspaceSetupDone() {
  workspaceSetupRequired.value = false
  setupError.value = ''
  onboardingChecked.value = true
}

watch(isLogin, value => {
  if (value) {
    onboardingChecked.value = false
    setupRequired.value = false
    workspaceSetupRequired.value = false
    assistantOpen.value = false
    setupError.value = ''
    return
  }
  checkSetup()
}, { immediate: true })
const isMobile = computed(() => $storex.ui.isMobile)
// The router resolves / and legacy URLs; this watcher covers the login
// transition (before the feature/company stores were allowed to load) and keeps
// a logged-in user on the right landing: the company home for `all` installs,
// or the project workspace for the 0.0.1 `core` profile. Both `root` and the
// company shell are full screen.
const isCompanyRoute = computed(() => route.name === 'company-home' || route.name === 'root')
const isFullScreenRoute = computed(() => isCompanyRoute.value || route.meta?.fullScreen === true)

/**
 * Landing target in workspace mode: the first/active project of the default
 * company, or the `/projects` bootstrap while the installation has no project.
 */
function workspaceTarget() {
  const company = $storex.company?.defaultCompany || $storex.company?.activeCompany
  if (!company?.slug) return null
  const all = $storex.company?.companyProjects || $storex.projects?.allProjects || []
  const active = $storex.projects?.activeProject
  const project = (active && all.some(
    item => String(item.project_id) === String(active.project_id)
  ))
    ? active
    : all[0]
  if (!project?.project_name) return null
  return { companySlug: company.slug, projectName: project.project_name }
}

async function enterDefaultHome() {
  if (isLogin.value) return
  await $storex.features?.ensureLoaded?.().catch(() => {})
  await $storex.company.ensureLoaded().catch(() => {})
  const company = $storex.company.activeCompany || $storex.company.defaultCompany
  if (companyEnabled.value && !company?.slug) return
  if (route.name === 'legacy') {
    // Re-run the guard now that the user is known, so a legacy project URL
    // resolves to its workspace home instead of being dropped.
    router.replace({
      path: route.path,
      query: route.query,
      hash: route.hash,
      force: true
    }).catch(() => {})
    return
  }
  // A reload/deep link on a company URL ran the router guard before the user
  // (and therefore the feature profile) was known. Once the profile resolves to
  // workspace mode, leave the company shell.
  if (!companyEnabled.value && route.name === 'company-home') {
    const target = workspaceTarget()
    router.replace(
      target
        ? { name: 'home', params: target }
        : { name: 'workspace-projects' }
    ).catch(() => {})
    return
  }
  if (companyEnabled.value && route.name === 'workspace-projects') {
    router.replace({
      name: 'company-home',
      params: { companySlug: company.slug }
    }).catch(() => {})
    return
  }
  if (route.name !== 'root') return
  if (companyEnabled.value) {
    router.replace({
      name: 'company-home',
      params: { companySlug: company.slug }
    }).catch(() => {})
    return
  }
  const target = workspaceTarget()
  router.replace(
    target
      ? { name: 'home', params: target }
      : { name: 'workspace-projects' }
  ).catch(() => {})
}

watch(
  [isLogin, () => $storex.company.loaded, () => $storex.features?.loaded],
  () => enterDefaultHome(),
  { immediate: true }
)

function openAccountSettings() {
  $storex.ui.openApp({ appKey: 'account' })
}

// Teleported surfaces (the mobile launcher, action sheets, Reka poppers) live
// outside the themed wrapper. DaisyUI resolves `data-theme` from the document
// root, so mirror the active theme there — otherwise every overlay renders in
// the default light theme.
watchEffect(() => {
  const theme = $storex.ui.theme || 'codx-junior-dark'
  document.documentElement.setAttribute('data-theme', theme)
  // Resolve the scheme from the registered daisyUI theme instead of guessing
  // from the theme name: custom themes (codx-junior-dark) are dark too.
  const scheme = getComputedStyle(document.documentElement).colorScheme || ''
  const dark = scheme.includes('dark')
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', dark ? '#1f2937' : '#f3f4f6')
})

onMounted(() => {
  // The launcher must never survive a reload.
  if (isMobile.value) $storex.ui.closeMobileMenu()
  enterDefaultHome()
})
</script>

<template>
  <div
    v-if="uiReady"
    class="relative h-full w-full bg-base-100"
    :data-theme="$ui.theme"
  >
    <Login v-if="isLogin" />

    <!-- First-run gate: no product shell before the company basics exist. -->
    <div v-else-if="!onboardingChecked" class="grid h-full w-full place-items-center bg-base-100" aria-busy="true">
      <p class="flex items-center gap-2 text-sm text-base-content/60">
        <span class="loading loading-spinner loading-sm"></span>Checking the installation…
      </p>
    </div>

    <div v-else-if="setupError" class="grid h-full w-full place-items-center bg-base-100 p-4" role="alert">
      <div class="rounded-box w-full max-w-[420px] border border-base-300 bg-base-100 p-5 text-center">
        <i class="fa-solid fa-triangle-exclamation text-lg text-warning-ink"></i>
        <h1 class="mt-2 text-base font-extrabold">Cannot read the setup state</h1>
        <p class="mt-1 text-xs text-base-content/60">{{ setupError }}</p>
        <button class="btn btn-primary btn-sm mt-4" @click="checkSetup(true)">Retry</button>
      </div>
    </div>

    <template v-else-if="setupRequired && companyEnabled">
      <div class="h-full w-full bg-base-100">
        <CompanyWizard :open="true" mandatory @done="onSetupDone" />
      </div>
    </template>

    <template v-else-if="workspaceSetupRequired">
      <div class="h-full w-full bg-base-100">
        <WorkspaceWizard :open="true" mandatory @done="onWorkspaceSetupDone" />
      </div>
    </template>

    <template v-else>
      <!-- Phones and tablets (<= 1024px): one purpose-built app shell. The
           desktop sidebar is not rendered at all — the mobile experience lives
           in components/mobile (top bar, tab bar, launcher). -->
      <MobileShell v-if="isMobile && !isFullScreenRoute" />

      <div v-else-if="isFullScreenRoute" class="h-full w-full">
        <RouterView />
      </div>

      <!-- Desktop / dockview shell -->
      <div v-else class="flex h-full w-full flex-row">
        <DesktopSidebar
          :user-name="$user?.username || 'User'"
          :user-subtitle="'Pro'"
          @settings="openAccountSettings"
        />

        <div class="flex min-h-0 min-w-0 grow flex-col">
          <RouterView />
        </div>
      </div>

      <Toast />

      <!-- Meeting invitations are global: a channel or a person can call you
           while you are on another screen, and the call must be one click away. -->
      <IncomingMeetInvitation />

      <!-- Voice conversation mode: global because a call survives navigation
           between chats/panels (and can move the conversation to another chat
           while it keeps running). -->
      <VoiceCallOverlay />

      <!-- Global "New task" dialog, opened from the single-mode "New" entry
           (see components/kanban/NewTaskDialog.vue). -->
      <NewTaskDialog />

      <!-- New chat (left bar): codx-junior assistant chat in a modal, with the
           task schedule selector (now / non-working hours / concrete instant). -->
      <BoardQuickChat
        v-if="$ui.newAssistantChat"
        board-id="codx-junior"
        board-name="codx-junior"
        :columns="ASSISTANT_CHAT_COLUMNS"
        :default-profiles="['codx-junior']"
        mode="chat"
        schedule-enabled
        @close="$ui.showNewAssistantChat(false)"
        @created="onAssistantChatCreated"
      />

      <!-- Global "New meeting" dialog — opened from the main menu, the channel
           header and the team view; participants are chosen before the call. -->
      <modal v-if="$ui.meetingSetup" close="true" @close="$ui.closeMeetingSetup()">
        <NewMeetingModal />
      </modal>

      <!-- Global "New project" modal — opened from any launcher
           (mobile menu, sidebar AppsSection, WindowsMenu, QuickBar) through the
           ui store flag `newProject`, and from the file explorer with
           `$ui.showNewProject({ path })` so the dialog starts on that folder. -->
      <modal
        v-if="$ui.newProject"
        close="true"
        @close="$ui.showNewProject(false)"
      >
        <!-- Keyed by the requested folder, so a new request re-initialises the
             form (and its prefilled path) instead of reusing the previous one. -->
        <NewProject :key="String($ui.newProject?.path || 'new-project')" />
      </modal>

      <!-- File references in markdown messages (utils/markdownFileLinks.js)
           open here in single mode: the chat/board underneath stays in place.
           Desktop mode opens a `file-viewer` panel instead — the decision is
           made by $ui.openFileViewer. -->
      <FileViewerModal
        v-if="$ui.filePreview"
        :key="$ui.filePreview.filePath"
        :file-path="$ui.filePreview.filePath"
        @close="$ui.closeFileViewer()"
      />

      <!-- Workspace mode: setup help is always one click away. -->
      <button
        v-if="!companyEnabled"
        class="btn btn-primary btn-circle fixed bottom-5 right-5 z-40 shadow-lg"
        title="Setup assistant"
        aria-label="Open setup assistant"
        @click="assistantOpen = true"
      >
        <i class="fa-solid fa-robot"></i>
      </button>
      <WorkspaceWizard
        v-if="assistantOpen"
        assistant-only
        @close="assistantOpen = false"
      />
    </template>
  </div>

  <div
    v-else
    class="flex h-full w-full flex-col items-center justify-center px-4 py-2 font-2xl"
  >
    <div class="animate-pulse font-mono text-green-600">Wake up, codx-junior...</div>
  </div>
</template>
