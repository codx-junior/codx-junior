<script setup>
/**
 * CISO view — the security control room of a project.
 *
 * One screen, five questions:
 *   Overview  how exposed are we, what did the gates do, is the repo clean?
 *   Findings  what exactly was detected, where and how do I fix it?
 *   Scans     run an analysis and follow it.
 *   Rules     what is detected and what does the platform do with it?
 *   Activity  every CISO gate decision, with redacted evidence.
 *
 * Data comes from `/api/ciso/*` (see `api/modules/ciso.js`). Every panel is
 * lazy in the sense that it only requests its own slice when opened; the
 * overview is the only request made on mount. The view never decides whether a
 * finding blocks: the policy engine owns that, this screen explains it.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import CisoOverview from '@/components/ciso/CisoOverview.vue'
import CisoFindings from '@/components/ciso/CisoFindings.vue'
import CisoScans from '@/components/ciso/CisoScans.vue'
import CisoRules from '@/components/ciso/CisoRules.vue'
import CisoActivity from '@/components/ciso/CisoActivity.vue'
import VerticalSplitter from '@/components/layout/VerticalSplitter.vue'
import ChatView from '@/views/ChatView.vue'

const route = useRoute()
const router = useRouter()
const api = computed(() => $storex.projects.activeProject?.$api || $storex.api)

const TABS = [
  { id: 'overview', label: 'Overview', icon: 'fa-solid fa-gauge-high' },
  { id: 'findings', label: 'Findings', icon: 'fa-solid fa-list-check' },
  { id: 'scans', label: 'Scans', icon: 'fa-solid fa-magnifying-glass-chart' },
  { id: 'rules', label: 'Rules & policy', icon: 'fa-solid fa-scale-balanced' },
  { id: 'activity', label: 'Activity', icon: 'fa-solid fa-clock-rotate-left' }
]

const activeTab = ref('overview')
const findingsScanId = ref('')
const overview = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const live = ref(true)
const chatBusy = ref(false)
let refreshTimer = null

// ── CISO conversation panel ───────────────────────────────────────────────
// The chat opened from a finding (or "Ask CISO") is mounted in a panel NEXT TO
// the security screen instead of navigating away to the chat view, so the
// findings, the policy and the discussion can be read at the same time.
// `openedChatId` empty = the panel is closed.
const openedChatId = ref('')
const isMobile = computed(() => $storex.ui.isMobile)
const cisoChat = computed(() =>
  openedChatId.value ? ($storex.chats.chats?.[openedChatId.value] || null) : null
)
const showCisoContent = computed(() => !isMobile.value || !openedChatId.value)
const showChatPane = computed(() => !!openedChatId.value)
// The security content keeps most of the width; the conversation gets a
// readable column and can be collapsed back.
const chatPanels = {
  left: { defaultSize: 62, minSize: 30, collapsible: false },
  right: { defaultSize: 38, minSize: 25, collapsible: true }
}

const posture = computed(() => overview.value?.posture || null)
const runningScans = computed(() => overview.value?.running_scans?.length || 0)

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

function validTab(tab) {
  return TABS.some(item => item.id === tab) ? tab : 'overview'
}

async function loadOverview({ silent = false } = {}) {
  if (!api.value) return
  if (!silent) loading.value = true
  error.value = ''
  try {
    overview.value = await api.value.ciso.overview()
    scheduleRefresh()
  } catch (ex) {
    error.value = `Security overview: ${errorMessage(ex)}`
  } finally {
    if (!silent) loading.value = false
  }
}

function scheduleRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
  if (live.value && (runningScans.value > 0 || activeTab.value === 'overview')) {
    refreshTimer = setTimeout(() => loadOverview({ silent: true }), runningScans.value ? 4000 : 15000)
  }
}

function stopRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
}

function selectTab(tab, options = {}) {
  activeTab.value = validTab(tab)
  if (options.scanId) findingsScanId.value = options.scanId
  const query = { ...route.query, tab: activeTab.value }
  if (activeTab.value === 'findings' && findingsScanId.value) {
    query.scan = findingsScanId.value
  } else {
    delete query.scan
  }
  router.replace({ query }).catch(() => {})
  if (activeTab.value === 'overview') loadOverview()
  scheduleRefresh()
}

function openTab(tab, payload = {}) {
  selectTab(tab, { scanId: payload.scanId || '' })
}

function openScan(scanId) {
  findingsScanId.value = scanId
  selectTab('findings', { scanId })
}

async function runScan(scope) {
  if (!api.value || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const started = await api.value.ciso.startScan({ scope })
    notice.value = `Scan ${started.scan_id} started (${scope}).`
    await loadOverview({ silent: true })
    selectTab('scans')
  } catch (ex) {
    error.value = `Could not start the scan: ${errorMessage(ex)}`
  } finally {
    busy.value = false
  }
}

function toggleLive() {
  live.value = !live.value
  if (live.value) {
    loadOverview({ silent: true })
    scheduleRefresh()
  } else {
    stopRefresh()
  }
}

/**
 * Open a CISO conversation in the side panel (never leaving this view).
 *
 * The chat is loaded through the store so the panel renders the SAME reactive
 * object the rest of the UI uses (streaming updates, cost, status).
 */
async function openCisoChat(chat) {
  if (!chat?.id) return
  const project = $storex.projects.activeProject
  openedChatId.value = chat.id
  if ($storex.chats.chats?.[chat.id]) return
  try {
    await $storex.chats.loadChat({
      id: chat.id,
      owner_project_id: chat.owner_project_id || project?.project_id
    })
  } catch (ex) {
    error.value = `Could not open the CISO chat: ${errorMessage(ex)}`
  }
}

function closeChat() {
  openedChatId.value = ''
}

/** Open the panel conversation in the full chat screen (mobile / deep work). */
function openFullChat() {
  if (openedChatId.value) $storex.ui.openChat({ id: openedChatId.value })
}

async function askCiso() {
  if (!api.value || chatBusy.value) return
  chatBusy.value = true
  error.value = ''
  try {
    const scanId = overview.value?.latest_completed_scan?.scan_id || overview.value?.latest_scan?.scan_id || ''
    const result = await api.value.ciso.chat({ scan_id: scanId, auto_analyze: true })
    if (result?.chat) {
      notice.value = 'CISO conversation opened — the first assessment is starting.'
      await openCisoChat(result.chat)
    }
  } catch (ex) {
    error.value = `Could not open the CISO chat: ${errorMessage(ex)}`
  } finally {
    chatBusy.value = false
  }
}

function applyRoute() {
  const tab = validTab(String(route.query.tab || 'overview'))
  activeTab.value = tab
  if (route.query.scan) findingsScanId.value = String(route.query.scan)
}

onMounted(async () => {
  applyRoute()
  await loadOverview()
})

onUnmounted(stopRefresh)
</script>

<template>
  <div class="h-full flex flex-col gap-2 sm:gap-3 p-2 sm:p-4 overflow-hidden">
    <!-- Content + CISO chat: resizable panels (VerticalSplitter) -----------
         The conversation lives in a second pane on the right, so asking the
         ciso about a finding never leaves the security screen. On a phone the
         panes cannot sit side by side: the chat takes the screen and the back
         control returns to the findings. -->
    <VerticalSplitter
      class="flex-1 min-h-0"
      :split="!isMobile"
      :show-left="showCisoContent"
      :show-right="showChatPane"
      show-handle
      persist-key="ciso-view"
      :panels="chatPanels"
    >
      <template #left>
        <div class="h-full flex flex-col gap-2 sm:gap-3 min-w-0">
    <!-- Header ---------------------------------------------------------->
    <div class="flex flex-col gap-1">
      <div class="flex items-center gap-2 flex-wrap">
        <div class="flex items-center gap-1.5 min-w-0 mr-auto">
          <i class="fa-solid fa-shield-halved text-primary text-sm sm:text-base"></i>
          <span class="text-sm sm:text-base font-semibold truncate">
            <span class="hidden sm:inline">codx-junior </span>CISO
          </span>
          <span class="text-xs text-base-content/50 hidden md:inline">
            {{ overview?.project?.name || $storex.projects.activeProject?.project_name || '' }}
          </span>
        </div>

        <div v-if="posture?.available" class="flex items-center gap-1 text-xs">
          <span class="badge badge-sm" :class="posture.score >= 90 ? 'badge-success' : posture.score >= 75 ? 'badge-info' : posture.score >= 60 ? 'badge-warning' : 'badge-error'">
            score {{ posture.score }} · {{ posture.grade }}
          </span>
          <span class="badge badge-sm" :class="overview?.policy?.enabled ? 'badge-ghost' : 'badge-error'">
            policy {{ overview?.policy?.enabled ? 'on' : 'off' }}
          </span>
          <span v-if="runningScans" class="badge badge-sm badge-info">
            <span class="loading loading-spinner loading-xs mr-1"></span>{{ runningScans }} scanning
          </span>
        </div>

        <button
          class="btn btn-xs btn-ghost px-1.5"
          :class="live ? 'text-success' : 'text-base-content/40'"
          :title="live ? 'Live refresh on (tap to pause)' : 'Live refresh paused (tap to resume)'"
          @click="toggleLive"
        >
          <i :class="live ? 'fa-solid fa-tower-broadcast' : 'fa-solid fa-pause'"></i>
        </button>
        <button class="btn btn-xs btn-ghost px-1.5" :disabled="loading || busy" title="Refresh" @click="loadOverview()">
          <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading || busy }"></i>
        </button>
        <button class="btn btn-xs btn-primary" :disabled="busy" @click="runScan('project')">
          <span v-if="busy" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-play"></i>
          <span class="hidden sm:inline">Scan project</span>
        </button>
        <button class="btn btn-xs btn-secondary" :disabled="chatBusy" title="Discuss the current scan with the ciso profile" @click="askCiso">
          <span v-if="chatBusy" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-comments"></i>
          <span class="hidden sm:inline">Ask CISO</span>
        </button>
      </div>
      <div class="hidden sm:block text-xs text-base-content/60">
        Threats, gate decisions and policy of this project. Scans are deterministic: no finding is decided by an LLM.
      </div>
    </div>

    <div v-if="error" class="alert alert-error text-sm py-2">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span class="flex-1">{{ error }}</span>
      <button class="btn btn-xs btn-ghost" @click="error = ''"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div v-if="notice" class="alert alert-success text-sm py-2">
      <i class="fa-solid fa-circle-check"></i>
      <span class="flex-1">{{ notice }}</span>
      <button class="btn btn-xs btn-ghost" @click="notice = ''"><i class="fa-solid fa-xmark"></i></button>
    </div>

    <!-- Tabs -------------------------------------------------------------->
    <div role="tablist" class="tabs tabs-boxed tabs-sm sm:tabs-md bg-base-200/60 p-1 flex-nowrap overflow-x-auto">
      <button
        v-for="tab in TABS"
        :key="tab.id"
        role="tab"
        class="tab gap-1.5 whitespace-nowrap"
        :class="{ 'tab-active': activeTab === tab.id }"
        :aria-selected="activeTab === tab.id"
        @click="selectTab(tab.id)"
      >
        <i :class="tab.icon"></i>
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <!-- Panels ------------------------------------------------------------>
    <div class="flex-1 min-h-0 overflow-y-auto pr-0.5">
      <CisoOverview
        v-if="activeTab === 'overview'"
        :overview="overview"
        :loading="loading"
        @refresh="loadOverview()"
        @run-scan="runScan"
        @open-tab="openTab"
      />
      <CisoFindings
        v-else-if="activeTab === 'findings'"
        :api="api"
        :scan-id="findingsScanId"
        @open-scan="openScan"
        @open-chat="openCisoChat"
      />
      <CisoScans
        v-else-if="activeTab === 'scans'"
        :api="api"
        @open-findings="openScan"
        @changed="loadOverview({ silent: true })"
      />
      <CisoRules
        v-else-if="activeTab === 'rules'"
        :api="api"
        @changed="loadOverview({ silent: true })"
      />
      <CisoActivity
        v-else-if="activeTab === 'activity'"
        :api="api"
      />
    </div>
        </div>
      </template>

      <!-- CISO conversation, next to the security screen ------------------>
      <template #right>
        <div v-if="openedChatId" class="h-full min-w-0 flex flex-col border-l border-base-300">
          <div class="flex items-center gap-1 px-2 py-1 border-b border-base-300 shrink-0">
            <button
              class="btn btn-ghost btn-xs"
              :title="isMobile ? 'Back to the security screen' : 'Close the CISO chat'"
              @click="closeChat"
            >
              <i :class="isMobile ? 'fa-solid fa-arrow-left' : 'fa-solid fa-xmark'"></i>
            </button>
            <i class="fa-solid fa-comments text-primary text-xs"></i>
            <span class="text-xs font-semibold truncate">{{ cisoChat?.name || 'CISO' }}</span>
            <button
              class="btn btn-ghost btn-xs ml-auto"
              title="Open the conversation in the full chat screen"
              @click="openFullChat"
            >
              <i class="fa-solid fa-up-right-from-square"></i>
            </button>
          </div>
          <ChatView v-if="cisoChat" :chat="cisoChat" class="flex-1 min-h-0" />
          <div v-else class="flex-1 flex items-center justify-center gap-2 text-sm text-base-content/50">
            <span class="loading loading-spinner loading-sm"></span>
            Loading chat…
          </div>
        </div>
      </template>
    </VerticalSplitter>
  </div>
</template>
