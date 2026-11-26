<script setup>
/**
 * Harness mode: the DSH-like full-screen workspace of a project.
 *
 * Layout: a session sidebar (workspaces + sessions) and either the
 * "Into the Unknown" composer or the selected session transcript. The view is
 * a lens over existing data:
 *
 *   • sessions  → `store/harness.js` (GET /api/harness/sessions[/*])
 *   • messages  → the regular chats store, updated by chat socket events
 *   • profiles / models / projects → the existing project stores
 *
 * Creating a session persists the chat + harness brief on the backend and then
 * starts the turn through the SAME `codx-junior-chat` socket event every chat
 * uses, so streaming, cancellation and crash recovery are shared, not cloned.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import HarnessModeSidebar from '@/components/harness/mode/HarnessModeSidebar.vue'
import HarnessHome from '@/components/harness/mode/HarnessHome.vue'
import HarnessSessionView from '@/components/harness/mode/HarnessSessionView.vue'

const route = useRoute()
const router = useRouter()

const collapsed = ref(false)
const mobileSessions = ref(false)
const mode = ref('standard')
const selectedProfiles = ref([])
const selectedModel = ref('')
const creating = ref(false)
const busy = ref(false)
const actionError = ref('')
const localProfiles = ref([])
const localModels = ref([])
const homeRef = ref(null)
const sessionRef = ref(null)

let pollTimer = null

const isMobile = computed(() => $storex.ui.isMobile)
const project = computed(() => $storex.projects.activeProject)
const projects = computed(() => {
  const all = $storex.projects.allProjects || []
  const visible = $storex.company?.visibleProjectIds
  if (!Array.isArray(visible) || !visible.length) return all
  const allowed = new Set(visible.map(String))
  // Keep the active project reachable even while the company snapshot is
  // loading: hiding the current workspace would make the picker useless.
  return all.filter(item =>
    allowed.has(String(item.project_id)) ||
    item.project_id === project.value?.project_id
  )
})

const sessions = computed(() => $storex.harness.sessions || [])
const runningCount = computed(() => $storex.harness.runningCount || 0)
const activeSessionId = computed(() => $storex.harness.activeSessionId || '')
const detail = computed(() =>
  activeSessionId.value ? $storex.harness.details?.[activeSessionId.value] || null : null
)
const session = computed(() =>
  activeSessionId.value ? $storex.harness.sessionById(activeSessionId.value) : null
)
const chat = computed(() => {
  if (!activeSessionId.value) return null
  return $storex.chats.chats?.[activeSessionId.value] || detail.value?.chat || null
})
const isLive = computed(() => String(session.value?.phase || '').toLowerCase() === 'running')
// The list entry is the freshest phase/timing source (the detail's `session`
// is a snapshot taken when it was loaded). Comparing both tells the poll when
// a turn started or finished and the detail (diff/files) must be reloaded.
const listedSession = computed(() =>
  sessions.value.find(item => item.session_id === activeSessionId.value) || null
)
const loadingSessions = computed(() => $storex.harness.loadingSessions)
const loadingSession = computed(() => $storex.harness.loadingSession)
const harnessError = computed(() => $storex.harness.error || '')
const error = computed(() => actionError.value || harnessError.value)

const allProfiles = computed(() => {
  const stored = $storex.profiles?.profiles || []
  return stored.length ? stored : localProfiles.value
})
const models = computed(() => {
  const stored = $storex.projects.ai?.models || []
  return stored.length ? stored : localModels.value
})
const tierProfileName = computed(() =>
  mode.value === 'deep' ? 'codx-junior-max' : 'codx-junior'
)
const tierProfiles = computed(() =>
  allProfiles.value.filter(profile => profile.name === tierProfileName.value)
)

watch(
  [tierProfiles, allProfiles],
  () => {
    // Profile catalogues arrive after the first render: seed the tier once they
    // are known. A later tier change goes through `setMode`, which replaces the
    // selection explicitly.
    if (!selectedProfiles.value.length) selectedProfiles.value = [...tierProfiles.value]
  },
  { immediate: true }
)

function setMode(value) {
  mode.value = value
  const target = value === 'deep' ? 'codx-junior-max' : 'codx-junior'
  const profiles = allProfiles.value.filter(profile => profile.name === target)
  if (profiles.length) selectedProfiles.value = [...profiles]
}

// ── Data loading ────────────────────────────────────────────────────────────

async function ensureProfilesAndModels() {
  const target = project.value
  if (!target?.$api) return
  try {
    if (!($storex.profiles?.profiles || []).length) {
      const loaded = await $storex.profiles.loadProjectProfiles(target)
      if (!loaded?.length) localProfiles.value = await target.$api.profiles.list()
    }
  } catch (ex) {
    console.warn('[HarnessMode] Could not load profiles', ex)
  }
  try {
    if (!($storex.projects.ai?.models || []).length) {
      localModels.value = await target.$api.projects.ai.models.list()
    }
  } catch (ex) {
    console.warn('[HarnessMode] Could not load models', ex)
  }
}

function applyRouteSession() {
  const requested = String(route.query.session || '').trim()
  if (!requested) return
  selectSession(requested, { updateRoute: false })
}

async function refresh({ silent = true } = {}) {
  await $storex.harness.loadSessions({ silent })
  if (!activeSessionId.value) return
  // The detail is expensive (full chat + git diff): refresh it while the
  // session is live, on an explicit request, when it was never loaded, or when
  // the list reports a phase the loaded detail does not know yet (the turn
  // just started/finished, so the branch diff changed).
  const detailPhase = String(detail.value?.session?.phase || '').toLowerCase()
  const listedPhase = String(listedSession.value?.phase || '').toLowerCase()
  const phaseChanged = !!listedPhase && detailPhase !== listedPhase
  if (!detail.value || isLive.value || phaseChanged || !silent) {
    await $storex.harness.loadSession({ id: activeSessionId.value, silent })
  }
}

onMounted(async () => {
  applyRouteSession()
  await ensureProfilesAndModels()
  await $storex.harness.loadSessions()
  if (activeSessionId.value && !detail.value) {
    await $storex.harness.loadSession({ id: activeSessionId.value })
  }
  pollTimer = setInterval(() => {
    refresh({ silent: true }).catch(() => {})
  }, 4000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

// ── Navigation ──────────────────────────────────────────────────────────────

async function selectSession(sessionId, { updateRoute = true } = {}) {
  if (!sessionId) return
  mobileSessions.value = false
  actionError.value = ''
  $storex.harness.selectSession(sessionId)
  await $storex.harness.loadSession({ id: sessionId })
  if (!detail.value && !session.value) {
    // Deleted/unknown session (a stale deep link): go back to the composer
    // instead of rendering an empty transcript forever.
    newSession()
    return
  }
  if (updateRoute) {
    router.replace({
      name: 'harness-mode',
      params: route.params,
      query: { ...route.query, session: sessionId }
    }).catch(() => {})
  }
}

function newSession() {
  $storex.harness.selectSession('')
  mobileSessions.value = false
  actionError.value = ''
  router.replace({
    name: 'harness-mode',
    params: route.params,
    query: {}
  }).catch(() => {})
}

function closeSession() {
  $storex.harness.selectSession('')
  router.replace({
    name: 'harness-mode',
    params: route.params,
    query: {}
  }).catch(() => {})
}

async function selectProject(target) {
  if (typeof target === 'string') {
    target = $storex.projects.allProjectsById?.[target] || null
  }
  if (!target?.project_id || target.project_id === project.value?.project_id) return
  mobileSessions.value = false
  actionError.value = ''
  // The catalogs belong to the previous project until the new one loads.
  localProfiles.value = []
  localModels.value = []
  selectedProfiles.value = []
  selectedModel.value = ''
  try {
    await $storex.projects.activeProjectChanged(target)
    $storex.harness.reset()
    await router.replace({
      name: 'harness-mode',
      params: {
        ...route.params,
        projectName: target.project_name
      },
      query: {}
    })
    await ensureProfilesAndModels()
    await $storex.harness.loadSessions()
  } catch (ex) {
    actionError.value = errorMessage(ex)
  }
}

function openDiagnostics() {
  $storex.ui.openApp('harness')
}

function openSettings() {
  $storex.ui.openApp('settings')
}

function openChat() {
  if (chat.value?.id) $storex.ui.openChat({ ...chat.value, name: session.value?.name || chat.value.name })
}

function openGit() {
  $storex.ui.openGitView()
}

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

// ── Start / continue a session ─────────────────────────────────────────────
// The backend starts every turn (POST /api/harness/sessions[/{id}/prompt])
// through the same `CODXJuniorSession.chat_with_project` the chat socket uses.
// The client only reads the resulting chat via the normal chat events, so a
// closed tab cannot leave a session idle.

function selectedProfileNames() {
  const names = selectedProfiles.value.map(profile => profile.name || profile).filter(Boolean)
  if (!names.length) return [tierProfileName.value]
  if (!names.includes(tierProfileName.value)) names.unshift(tierProfileName.value)
  return names
}

async function submit(payload) {
  // Accepts both the composer payload (`{ text, attachments }`) and a plain
  // string, so the view keeps working with any host that sends only text.
  const text = String((payload && payload.text) || (typeof payload === 'string' ? payload : '') || '').trim()
  const attachments = (payload && payload.attachments) || []
  if ((!text && !attachments.length) || creating.value) return
  creating.value = true
  actionError.value = ''
  try {
    const created = await $storex.harness.createSession({
      prompt: text,
      profiles: selectedProfileNames(),
      model: selectedModel.value || undefined,
      attachments
    })
    homeRef.value?.clear?.()
    await selectSession(created.session_id)
    if (created.started === false) {
      actionError.value = 'The session was created, but the background turn could not start.'
    }
  } catch (ex) {
    actionError.value = errorMessage(ex)
  } finally {
    creating.value = false
  }
}

async function sendFollowUp({ text, model, attachments = [] } = {}) {
  if ((!text && !attachments.length) || !activeSessionId.value || busy.value) return
  busy.value = true
  actionError.value = ''
  try {
    const updated = await $storex.harness.promptSession({
      id: activeSessionId.value,
      content: text,
      model: model || undefined,
      attachments
    })
    sessionRef.value?.clearComposer?.()
    if (updated?.started === false) {
      actionError.value = 'The message was saved, but the background turn could not start.'
    }
    await $storex.harness.loadSession({ id: activeSessionId.value, silent: true })
    sessionRef.value?.scrollToBottom?.()
  } catch (ex) {
    actionError.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

async function stopSession() {
  const target = project.value
  if (!activeSessionId.value || !target?.$api) return
  busy.value = true
  actionError.value = ''
  try {
    await target.$api.chats.cancelMessage(null, activeSessionId.value)
    await $storex.harness.loadSession({ id: activeSessionId.value, silent: true })
  } catch (ex) {
    actionError.value = errorMessage(ex)
  } finally {
    busy.value = false
  }
}

watch(
  () => route.query.session,
  (value) => {
    const requested = String(value || '').trim()
    if (requested && requested !== activeSessionId.value) {
      selectSession(requested, { updateRoute: false })
    }
  }
)
</script>

<template>
  <div class="h-full w-full flex overflow-hidden bg-base-100 text-base-content">
    <!-- Desktop sidebar / mobile drawer -->
    <template v-if="!isMobile">
      <HarnessModeSidebar
        :sessions="sessions"
        :active-session-id="activeSessionId"
        :projects="projects"
        :active-project="project"
        :loading="loadingSessions"
        :collapsed="collapsed"
        :running-count="runningCount"
        @select-session="selectSession"
        @new-session="newSession"
        @select-project="selectProject"
        @toggle-collapse="collapsed = !collapsed"
        @diagnostics="openDiagnostics"
        @settings="openSettings"
        @refresh="refresh({ silent: false })"
      />
    </template>
    <template v-else>
      <div
        v-if="mobileSessions"
        class="fixed inset-0 z-40 bg-base-300/60 backdrop-blur-sm"
        @click="mobileSessions = false"
      ></div>
      <div
        v-if="mobileSessions"
        class="fixed left-0 top-0 bottom-0 z-50"
      >
        <HarnessModeSidebar
          :sessions="sessions"
          :active-session-id="activeSessionId"
          :projects="projects"
          :active-project="project"
          :loading="loadingSessions"
          :collapsed="false"
          :running-count="runningCount"
          @select-session="selectSession"
          @new-session="newSession"
          @select-project="selectProject"
          @toggle-collapse="mobileSessions = false"
          @diagnostics="openDiagnostics"
          @settings="openSettings"
          @refresh="refresh({ silent: false })"
        />
      </div>
    </template>

    <!-- Main pane -->
    <main class="flex-1 min-w-0 h-full relative">
      <!-- Mobile top bar: sessions drawer + title -->
      <div
        v-if="isMobile"
        class="absolute top-0 left-0 right-0 z-20 flex items-center gap-2 px-2 py-1.5 bg-base-200/80 backdrop-blur border-b border-base-content/10"
      >
        <button
          type="button"
          class="btn btn-ghost btn-xs btn-square"
          title="Sessions"
          @click="mobileSessions = true"
        >
          <i class="fa-solid fa-bars"></i>
        </button>
        <span class="text-xs font-semibold truncate">codx-junior HARNESS</span>
        <span v-if="runningCount" class="badge badge-success badge-xs">{{ runningCount }}</span>
        <span class="grow"></span>
        <button type="button" class="btn btn-ghost btn-xs" @click="newSession">
          <i class="fa-solid fa-plus text-[10px]"></i>
          New
        </button>
      </div>

      <div class="h-full" :class="isMobile && 'pt-9'">
        <HarnessSessionView
          v-if="activeSessionId"
          ref="sessionRef"
          :session="session"
          :detail="detail"
          :chat="chat"
          :loading="loadingSession"
          :busy="busy"
          :error="error"
          :models="models"
          @back="closeSession"
          @open-chat="openChat"
          @open-git="openGit"
          @open-diagnostics="openDiagnostics"
          @open-project="openSettings"
          @stop="stopSession"
          @send="sendFollowUp"
          @refresh="refresh({ silent: false })"
        />
        <HarnessHome
          v-else
          ref="homeRef"
          :project="project"
          :mode="mode"
          :profiles="allProfiles"
          :selected-profiles="selectedProfiles"
          :models="models"
          :selected-model="selectedModel"
          :creating="creating"
          :error="error"
          @update:project="selectProject"
          @update:mode="setMode"
          @update:profiles="selectedProfiles = $event"
          @update:model="selectedModel = $event"
          @submit="submit"
        />
      </div>
    </main>
  </div>
</template>
