<script setup>
/**
 * Harness diagnostic view.
 *
 * A harness task *is* a codx-junior orchestration run: the main task (a chat)
 * plus one child task card per plan step. This view answers, for one task:
 *
 * - what is the flow of the plan (stages, dependencies, next action),
 * - which tasks are associated and where they live on the board,
 * - what happened (run timeline + append-only audit journal),
 * - what the delivery/stop report says,
 * - and whether the project has a worker able to advance it at all.
 *
 * The layout is a resizable splitter (components/layout/VerticalSplitter.vue):
 * the run list on the left and the run diagnosis on the right. Selecting a task
 * (the main task, a step card or a delegated card) opens it in a THIRD panel
 * next to the diagnosis instead of navigating away, so the flow, the timeline
 * and the conversation of the task can be read at the same time.
 *
 * Deep links: `?run=<run_id>` (and/or `?chat=<chat_id>`) preselect the run and
 * open the task panel. That is what the "Harness" button of the task bar
 * (components/chat/ChatRunBar.vue) and any external link use.
 *
 * Read-only except for the run controls (start / pause / stop); the data comes
 * from `GET /api/orchestration` and `GET /api/harness/*`.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { $storex } from '@/store'
import HarnessRunList from '@/components/harness/HarnessRunList.vue'
import HarnessRunDetail from '@/components/harness/HarnessRunDetail.vue'
import HarnessAuditJournal from '@/components/harness/HarnessAuditJournal.vue'
import VerticalSplitter from '@/components/layout/VerticalSplitter.vue'
import ChatView from '@/views/ChatView.vue'

const route = useRoute()

// The project-scoped API instance carries the codx_path of the active project
// (the harness endpoints are project scoped).
const api = computed(() => $storex.projects.activeProject?.$api || $storex.api)
const isMobile = computed(() => $storex.ui.isMobile)

const status = ref(null)
const runs = ref([])
const selectedRunId = ref('')
const run = ref(null)
const runLoading = ref(false)
const runError = ref('')
const controlBusy = ref(false)

const audit = ref([])
const auditOffset = ref(0)
const auditEvent = ref('')
const auditScope = ref('run')
const loadingAudit = ref(false)

const loading = ref(false)
const error = ref('')
const live = ref(true)

// ── Panels ────────────────────────────────────────────────────────────────
// `?run=` / `?chat=` are applied ONCE, so the polling refresh never fights a
// selection the user made by hand.
const routeApplied = ref(false)
// Chat opened in the task panel (empty = the panel is closed).
const openedChatId = ref('')
const taskChat = computed(() =>
  openedChatId.value ? ($storex.chats.chats?.[openedChatId.value] || null) : null
)

// Sizes are percentages (radix-vue). The list stays narrow, the diagnosis owns
// the rest; with a task open the diagnosis and the conversation share the pane.
const listPanels = {
  left: { defaultSize: 22, minSize: 12, collapsible: false },
  right: { defaultSize: 78, minSize: 40, collapsible: false }
}
const taskPanels = {
  left: { defaultSize: 55, minSize: 25, collapsible: false },
  right: { defaultSize: 45, minSize: 25, collapsible: true }
}

let refreshTimer = null

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

/** Load the run list and the harness health of the project. */
async function refresh() {
  const client = api.value
  if (!client) return
  loading.value = true
  error.value = ''
  try {
    const [statusData, runsData] = await Promise.all([
      client.harness.status(),
      client.orchestration.list()
    ])
    status.value = statusData
    // `GET /api/orchestration` answers `{ runs: [...] }`.
    runs.value = Array.isArray(runsData) ? runsData : (runsData?.runs || [])
    const requested = await resolveRequestedTarget()
    if (requested?.runId) {
      selectedRunId.value = requested.runId
    }
    if (!selectedRunId.value && runs.value.length) {
      selectedRunId.value = runs.value[0].run_id
    }
    if (requested?.chatId) {
      await openTask(requested.chatId)
    }
    if (selectedRunId.value) {
      await loadRun(selectedRunId.value)
    }
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    loading.value = false
  }
}

/**
 * Resolve the run/task requested by the URL, once per mount.
 *
 * Accepts `?run=<run_id>` (the run process id shown on a task), and
 * `?chat=<chat_id>` for a main task or a step card. When only a chat id is
 * given the owning run is looked up (the run that has it as `main_chat_id` or
 * as a step card), so a link from any card lands on the right run.
 *
 * @returns {Promise<{runId: string, chatId: string}|null>} Target to select.
 */
async function resolveRequestedTarget() {
  if (routeApplied.value) return null
  routeApplied.value = true
  const runParam = String(route.query.run || '').trim()
  const chatParam = String(route.query.chat || '').trim()
  if (!runParam && !chatParam) return null
  const direct = runs.value.find(
    item =>
      item.run_id === runParam ||
      item.run_id === chatParam ||
      item.main_chat_id === runParam ||
      item.main_chat_id === chatParam
  )
  if (direct) {
    return { runId: direct.run_id, chatId: chatParam }
  }
  // A step (or delegated) card: find the run that owns it. The scan is bounded
  // and only runs on a deep link, so it never costs anything on a normal visit.
  if (chatParam && api.value) {
    for (const candidate of runs.value.slice(0, 15)) {
      try {
        const detail = await api.value.orchestration.get(candidate.run_id)
        if ((detail.steps || []).some(step => step.chat_id === chatParam)) {
          return { runId: candidate.run_id, chatId: chatParam }
        }
      } catch (ex) {
        // Ignore a run that fails to load and keep scanning.
      }
    }
  }
  return null
}

/** Load the full detail (plan, step results, next action, worker). */
async function loadRun(runId) {
  const client = api.value
  if (!client || !runId) {
    run.value = null
    return
  }
  runLoading.value = true
  runError.value = ''
  try {
    run.value = await client.orchestration.get(runId)
  } catch (ex) {
    run.value = null
    runError.value = errorMessage(ex)
  } finally {
    runLoading.value = false
  }
}

async function selectRun(runId) {
  if (!runId || runId === selectedRunId.value) return
  selectedRunId.value = runId
  // The open task belongs to the previous run: close the panel.
  openedChatId.value = ''
  await loadRun(runId)
  await loadAudit(true)
}

/**
 * Open a task (main task, step card or delegated card) in the task panel.
 *
 * The chat is loaded through the store so the panel renders the SAME reactive
 * object the rest of the UI uses (streaming updates, cost, status).
 */
async function openTask(chatId) {
  if (!chatId) return
  openedChatId.value = chatId
  if ($storex.chats.chats?.[chatId]) return
  try {
    await $storex.chats.loadChat({ id: chatId })
  } catch (ex) {
    error.value = errorMessage(ex)
  }
}

function closeTask() {
  openedChatId.value = ''
}

/** Open the task of the panel in the full chat screen (mobile / deep work). */
function openFullTask() {
  if (openedChatId.value) $storex.ui.openChat({ id: openedChatId.value })
}

/** Read the append-only audit journal (optionally scoped to the run). */
async function loadAudit(reset = true) {
  const client = api.value
  if (!client) return
  loadingAudit.value = true
  try {
    const offset = reset ? 0 : auditOffset.value
    const params = { limit: 50, offset }
    if (auditEvent.value) params.event = auditEvent.value
    if (auditScope.value === 'run' && selectedRunId.value) params.run_id = selectedRunId.value
    const data = await client.harness.audit(params)
    const entries = data.entries || []
    audit.value = reset ? entries : [...audit.value, ...entries]
    auditOffset.value = offset + entries.length
  } catch (ex) {
    // The audit failure must not hide the rest of the view.
    console.warn('Harness audit load failed', ex)
  } finally {
    loadingAudit.value = false
  }
}

/** Run controls (start / pause / stop) emitted by the detail pane. */
async function onControl({ action, runId }) {
  const client = api.value
  if (!client || !runId) return
  controlBusy.value = true
  error.value = ''
  try {
    if (action === 'start') await client.orchestration.start(runId)
    else if (action === 'pause') await client.orchestration.pause(runId, 'paused from the harness view')
    else if (action === 'stop') await client.orchestration.stop(runId, 'stopped from the harness view')
    await loadRun(runId)
    await loadAudit(true)
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    controlBusy.value = false
  }
}

onMounted(() => {
  refresh()
  loadAudit()
  refreshTimer = setInterval(() => {
    if (!live.value) return
    refresh()
    loadAudit(true)
  }, 10000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <div class="h-full flex flex-col gap-3 p-4 overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between gap-2 flex-wrap">
      <div>
        <div class="text-lg font-semibold flex items-center gap-2">
          <i class="fa-solid fa-bolt text-primary"></i>
          codx-junior Harness
        </div>
        <div class="text-xs text-base-content/60">
          Diagnose any harness task: its flow, every associated task card, the timeline and the
          audited work of the <code class="kbd kbd-xs">@codx-junior</code> profile.
        </div>
      </div>
      <div class="flex items-center gap-2">
        <label class="label cursor-pointer gap-1 py-0">
          <input v-model="live" type="checkbox" class="toggle toggle-xs" />
          <span class="label-text text-xs">live</span>
        </label>
        <span
          v-if="status"
          class="badge gap-1"
          :class="status.worker_running ? 'badge-success' : 'badge-error'"
        >
          <i :class="status.worker_running ? 'fa-solid fa-circle-check' : 'fa-solid fa-triangle-exclamation'"></i>
          worker {{ status.worker_running ? 'running' : 'off' }}
        </span>
        <button class="btn btn-sm" :disabled="loading" @click="refresh">
          <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
          Refresh
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>
      {{ error }}
    </div>

    <!-- Worker / watchdog strip -->
    <div v-if="status" class="grid grid-cols-2 md:grid-cols-4 gap-2">
      <div class="stat bg-base-200 rounded-box p-3">
        <div class="stat-title text-xs">Active runs</div>
        <div class="stat-value text-xl">{{ status.active_run_count }}</div>
      </div>
      <div class="stat bg-base-200 rounded-box p-3">
        <div class="stat-title text-xs">Worker heartbeat</div>
        <div class="stat-value text-sm pt-1">
          {{ status.worker?.age_seconds != null ? `${Math.round(status.worker.age_seconds)}s ago` : 'never' }}
        </div>
      </div>
      <div class="stat bg-base-200 rounded-box p-3">
        <div class="stat-title text-xs">Watchdog</div>
        <div class="stat-value text-sm pt-1">
          {{ status.watchdog?.running ? 'watching' : (status.watchdog?.enabled ? 'not running' : 'disabled') }}
        </div>
      </div>
      <div class="stat bg-base-200 rounded-box p-3">
        <div class="stat-title text-xs">Watchdog restarts</div>
        <div class="stat-value text-sm pt-1">{{ status.watchdog?.restarts ?? 0 }}</div>
      </div>
    </div>

    <!-- Runs + diagnosis + task: resizable panels (VerticalSplitter) ---------
         Left: the run list. Right: the diagnosis, split again when a task is
         open so the conversation of the card sits next to the plan, the timeline
         and the audit journal. -->
    <VerticalSplitter
      class="flex-1 min-h-0"
      :split="!isMobile"
      show-handle
      persist-key="harness-view"
      :panels="listPanels"
    >
      <template #left>
        <div class="h-full overflow-y-auto pr-1">
          <HarnessRunList
            :runs="runs"
            :selected-run-id="selectedRunId"
            :loading="loading"
            @select="selectRun"
            @refresh="refresh"
          />
        </div>
      </template>

      <template #right>
        <VerticalSplitter
          class="h-full"
          :split="!isMobile"
          :show-right="!!openedChatId"
          show-handle
          persist-key="harness-task"
          :panels="taskPanels"
        >
          <template #left>
            <div class="h-full overflow-y-auto flex flex-col gap-3 lg:pl-2">
              <HarnessRunDetail
                :run="run"
                :loading="runLoading"
                :error="runError"
                :busy="controlBusy"
                @open-chat="openTask"
                @refresh="loadRun(selectedRunId)"
                @control="onControl"
              />
              <HarnessAuditJournal
                :entries="audit"
                :loading="loadingAudit"
                v-model:event="auditEvent"
                v-model:scope="auditScope"
                @load="loadAudit(true)"
                @load-more="loadAudit(false)"
              />
            </div>
          </template>

          <!-- Third panel: the selected task, without leaving the harness -->
          <template #right>
            <div v-if="openedChatId" class="h-full min-w-0 flex flex-col border-l border-base-300">
              <div class="flex items-center gap-2 px-2 py-1 border-b border-base-300">
                <i class="fa-solid fa-clipboard-check text-primary text-xs"></i>
                <span class="text-xs font-semibold truncate">{{ taskChat?.name || 'Task' }}</span>
                <div class="ml-auto flex items-center gap-1">
                  <button
                    class="btn btn-ghost btn-xs"
                    title="Open the task in the full chat screen"
                    @click="openFullTask"
                  >
                    <i class="fa-solid fa-up-right-from-square"></i>
                  </button>
                  <button class="btn btn-ghost btn-xs" title="Close the task panel" @click="closeTask">
                    <i class="fa-solid fa-xmark"></i>
                  </button>
                </div>
              </div>
              <ChatView v-if="taskChat" :chat="taskChat" class="flex-1 min-h-0" />
              <div v-else class="flex-1 flex items-center justify-center gap-2 text-sm text-base-content/50">
                <span class="loading loading-spinner loading-sm"></span>
                Loading task…
              </div>
            </div>
          </template>
        </VerticalSplitter>
      </template>
    </VerticalSplitter>
  </div>
</template>
