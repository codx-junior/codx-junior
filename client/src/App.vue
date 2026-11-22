<script setup>
import { computed, onMounted, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import Login from './components/user/Login.vue'
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
const isMobile = computed(() => $storex.ui.isMobile)
// The company is the root of the product. The router resolves / and legacy
// URLs; this watcher covers the login transition (before the company store was
// allowed to load) and keeps a logged-in user on the company home.
const isCompanyRoute = computed(() => route.name === 'company-home')
const isFullScreenRoute = computed(() => isCompanyRoute.value)

async function enterCompanyHome() {
  if (isLogin.value) return
  await $storex.company.ensureLoaded().catch(() => {})
  const company = $storex.company.activeCompany || $storex.company.defaultCompany
  if (!company?.slug) return
  if (route.name === 'legacy') {
    // Re-run the guard now that the user is known, so a legacy project URL
    // resolves to its company workspace instead of being dropped.
    router.replace({
      path: route.path,
      query: route.query,
      hash: route.hash,
      force: true
    }).catch(() => {})
    return
  }
  if (route.name !== 'root') return
  router.replace({
    name: 'company-home',
    params: { companySlug: company.slug }
  }).catch(() => {})
}

watch([isLogin, () => $storex.company.loaded], () => enterCompanyHome(), { immediate: true })

function openAccountSettings() {
  $storex.ui.openApp({ appKey: 'account' })
}

// Teleported surfaces (the mobile launcher, action sheets, Reka poppers) live
// outside the themed wrapper. DaisyUI resolves `data-theme` from the document
// root, so mirror the active theme there — otherwise every overlay renders in
// the default light theme.
watchEffect(() => {
  const theme = $storex.ui.theme || 'dark'
  document.documentElement.setAttribute('data-theme', theme)
  document.documentElement.style.colorScheme = theme === 'dark' ? 'dark' : 'light'
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'dark' ? '#1f2937' : '#f3f4f6')
})

onMounted(() => {
  // The launcher must never survive a reload.
  if (isMobile.value) $storex.ui.closeMobileMenu()
  enterCompanyHome()
})
</script>

<template>
  <div
    v-if="uiReady"
    class="relative h-full w-full bg-base-100"
    :data-theme="$ui.theme"
  >
    <Login v-if="isLogin" />

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
        mode="task"
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
    </template>
  </div>

  <div
    v-else
    class="flex h-full w-full flex-col items-center justify-center px-4 py-2 font-2xl"
  >
    <div class="animate-pulse font-mono text-green-600">Wake up, codx-junior...</div>
  </div>
</template>
