<script setup>
/**
 * CISO findings: review the results of one scan.
 *
 * Server-side filtering and paging keep the payload bounded: a project scan
 * can produce thousands of findings (most of them entropy noise), so the panel
 * requests one page at a time and only expands the row the reviewer opened.
 *
 * Evidence is always already redacted by the backend: the CISO view never
 * receives the value a detector matched, only a safe excerpt.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  SEVERITIES,
  categoryLabel,
  formatDate,
  formatDuration,
  scopeLabel,
  severityClass,
  severityLabel,
  statusClass,
  STATUS_LABELS,
  sortFindings
} from './format'

const props = defineProps({
  api: { type: Object, default: null },
  scanId: { type: String, default: '' }
})

const emit = defineEmits(['open-scan', 'open-chat'])

const scans = ref([])
const selectedScanId = ref('')
const scan = ref(null)
const findings = ref([])
const total = ref(0)
const counts = ref({})
const categories = ref({})
const rules = ref([])
const loading = ref(false)
const loadingScans = ref(false)
const error = ref('')
const expanded = ref('')
const pageSize = 50
const offset = ref(0)
const filters = ref({ severity: 'high', category: '', query: '' })

// Findings selected for a remediation task. Keyed by a stable identity (scan +
// rule + path + line), so the selection survives paging and filter changes.
const selected = ref(new Map())
const taskDialog = ref(false)
const taskCreating = ref(false)
const taskError = ref('')
const taskResult = ref(null)
const taskForm = ref({ name: '', board: 'kanban', column: 'tasks', priority: 'high' })
const askingCiso = ref(false)

let searchTimer = null
let pollTimer = null

const severityOptions = SEVERITIES
const categoryOptions = computed(() => Object.keys(categories.value || {}))
const page = computed(() => Math.floor(offset.value / pageSize) + 1)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const hasPrevious = computed(() => offset.value > 0)
const hasNext = computed(() => offset.value + pageSize < total.value)
const scanRunning = computed(() => ['queued', 'running'].includes(scan.value?.status))
const scanOptions = computed(() =>
  scans.value.filter(item => item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled' || item.scan_id === selectedScanId.value)
)
const selectedFindings = computed(() => [...selected.value.values()])
const selectedCount = computed(() => selected.value.size)
const allPageSelected = computed(() =>
  findings.value.length > 0 && findings.value.every(finding => selected.value.has(findingIdentity(finding)))
)
const selectedSeverityCounts = computed(() => {
  const counts = {}
  selectedFindings.value.forEach(finding => {
    counts[finding.severity] = (counts[finding.severity] || 0) + 1
  })
  return counts
})

function errorMessage(ex) {
  return ex?.response?.data?.detail || ex?.message || String(ex)
}

/**
 * Stable identity of a finding inside a scan. Used for selection and as the
 * expansion key (the index is appended only to keep duplicate rows distinct).
 */
function findingIdentity(finding) {
  return [
    selectedScanId.value,
    finding?.rule_id || '',
    finding?.path || '',
    finding?.line || 0
  ].join('|')
}

function findingKey(finding, index) {
  return `${findingIdentity(finding)}|${index}`
}

function isSelected(finding) {
  return selected.value.has(findingIdentity(finding))
}

function toggleSelection(finding) {
  const identity = findingIdentity(finding)
  if (selected.value.has(identity)) {
    selected.value.delete(identity)
  } else {
    selected.value.set(identity, finding)
  }
}

function toggleSelectPage() {
  if (allPageSelected.value) {
    findings.value.forEach(finding => selected.value.delete(findingIdentity(finding)))
  } else {
    findings.value.forEach(finding => selected.value.set(findingIdentity(finding), finding))
  }
}

function clearSelection() {
  selected.value.clear()
}

function openTaskDialog() {
  if (!selectedCount.value) return
  taskError.value = ''
  taskResult.value = null
  const scope = scan.value?.scope ? scopeLabel(scan.value.scope) : 'security scan'
  taskForm.value = {
    name: `CISO: fix ${selectedCount.value} finding(s) from ${scope}`,
    board: 'kanban',
    column: 'tasks',
    priority: selectedSeverityCounts.value.critical ? 'high' : 'medium'
  }
  taskDialog.value = true
}

function closeTaskDialog() {
  if (taskCreating.value) return
  taskDialog.value = false
  taskError.value = ''
}

function cleanFindingText(value, limit = 1200) {
  const text = String(value || '').replace(/\s+$/g, '').trim()
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

function taskDescription() {
  const rows = sortFindings(selectedFindings.value)
  const lines = [
    '# CISO findings to fix',
    '',
    `- Scan: \`${selectedScanId.value}\``,
    `- Scope: ${scopeLabel(scan.value?.scope)}`,
    `- Selected findings: **${rows.length}**`,
    `- Severity mix: ${Object.entries(selectedSeverityCounts.value).map(([level, count]) => `\`${level}: ${count}\``).join(', ') || '—'}`,
    '',
    'Each finding was produced by a deterministic CISO detector. Fix the cause, not the report:',
    'remove the credential, replace the dangerous primitive or add a reviewed exception',
    'in the project policy if the pattern is legitimate.'
  ]
  let currentSeverity = ''
  rows.forEach(finding => {
    if (finding.severity !== currentSeverity) {
      currentSeverity = finding.severity
      lines.push('', `## ${severityLabel(currentSeverity)}`)
    }
    const location = finding.path
      ? `\`${finding.path}${finding.line ? `:${finding.line}` : ''}\``
      : 'unknown location'
    lines.push(
      '',
      `### ${finding.rule_id} — ${location}`,
      `- Title: ${cleanFindingText(finding.title || finding.reason || finding.rule_id, 300)}`,
      `- Category: ${categoryLabel(finding.category)}`,
      `- Reason: ${cleanFindingText(finding.reason || '—')}`,
      ...(finding.excerpt ? [`- Redacted excerpt: \`${cleanFindingText(finding.excerpt, 600).replace(/`/g, "'")}\``] : []),
      `- Remediation: ${cleanFindingText(finding.remediation || 'Review the finding and remove the cause.')}`
    )
  })
  lines.push(
    '',
    '## Done',
    '- [ ] Every selected finding is fixed or explicitly silenced with a reviewed policy exception.',
    '- [ ] A re-scan of the same scope shows no selected rule on the affected paths.',
    '- [ ] The fix has a regression test when the finding is code (not configuration).'
  )
  return lines.join('\n')
}

async function createTask() {
  if (!props.api || taskCreating.value || !selectedCount.value) return
  if (selectedCount.value > 150) {
    taskError.value = 'Select at most 150 findings per task: split the remediation into focused tasks.'
    return
  }
  const name = String(taskForm.value.name || '').trim()
  if (!name) {
    taskError.value = 'Give the task a name.'
    return
  }
  taskCreating.value = true
  taskError.value = ''
  try {
    const files = [...new Set(selectedFindings.value.map(finding => finding.path).filter(Boolean))]
    const payload = {
      name,
      description: taskDescription(),
      board: taskForm.value.board || undefined,
      column: taskForm.value.column || undefined,
      priority: taskForm.value.priority || undefined,
      files: files.slice(0, 100),
      tags: ['security', 'ciso']
    }
    taskResult.value = await props.api.post('/api/chats/tasks', payload)
    selected.value.clear()
  } catch (ex) {
    taskError.value = errorMessage(ex)
  } finally {
    taskCreating.value = false
  }
}

async function askCiso(finding = null) {
  if (!props.api || askingCiso.value) return
  const targets = finding ? [finding] : selectedFindings.value
  if (!targets.length || !selectedScanId.value) return
  askingCiso.value = true
  error.value = ''
  try {
    const result = await props.api.ciso.chat({
      scan_id: selectedScanId.value,
      finding_keys: targets.map(item => `${item.rule_id}|${item.path || ''}|${item.line || 0}`),
      auto_analyze: true
    })
    if (result?.chat) emit('open-chat', result.chat)
  } catch (ex) {
    error.value = `Could not open the CISO chat: ${errorMessage(ex)}`
  } finally {
    askingCiso.value = false
  }
}

async function loadScans() {
  if (!props.api) return
  loadingScans.value = true
  try {
    const payload = await props.api.ciso.scans({ limit: 50 })
    scans.value = payload?.scans || []
    if (!selectedScanId.value) {
      const preferred = props.scanId && scans.value.find(item => item.scan_id === props.scanId)
      const completed = scans.value.find(item => item.status === 'completed')
      selectedScanId.value = preferred?.scan_id || completed?.scan_id || scans.value[0]?.scan_id || ''
    }
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    loadingScans.value = false
  }
}

async function loadFindings() {
  if (!props.api || !selectedScanId.value) {
    findings.value = []
    total.value = 0
    scan.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const payload = await props.api.ciso.findings(selectedScanId.value, {
      severity: filters.value.severity,
      category: filters.value.category,
      query: filters.value.query,
      limit: pageSize,
      offset: offset.value
    })
    findings.value = payload?.findings || []
    total.value = payload?.total || 0
    scan.value = payload?.scan || null
    counts.value = payload?.counts || {}
    categories.value = payload?.categories || {}
    rules.value = payload?.rules || []
  } catch (ex) {
    error.value = errorMessage(ex)
  } finally {
    loading.value = false
    schedulePoll()
  }
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (scanRunning.value) {
    pollTimer = setTimeout(() => loadFindings(), 2500)
  }
}

function selectScan(scanId) {
  selectedScanId.value = scanId
  offset.value = 0
  expanded.value = ''
  clearSelection()
  loadFindings()
  emit('open-scan', scanId)
}

function applyFilters() {
  offset.value = 0
  expanded.value = ''
  loadFindings()
}

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(applyFilters, 300)
}

function previousPage() {
  if (!hasPrevious.value) return
  offset.value = Math.max(0, offset.value - pageSize)
  loadFindings()
}

function nextPage() {
  if (!hasNext.value) return
  offset.value += pageSize
  loadFindings()
}

function toggleFinding(finding, index) {
  const key = findingKey(finding, index)
  expanded.value = expanded.value === key ? '' : key
}

watch(
  () => props.scanId,
  value => {
    if (value && value !== selectedScanId.value) {
      selectedScanId.value = value
      offset.value = 0
      clearSelection()
      loadFindings()
    }
  }
)

watch(
  () => props.api,
  () => {
    loadScans().then(() => loadFindings())
  }
)

onMounted(async () => {
  await loadScans()
  if (props.scanId) selectedScanId.value = props.scanId
  await loadFindings()
})

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <div class="flex flex-col gap-3">
    <div v-if="error" class="alert alert-error text-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>{{ error }}
    </div>

    <div class="rounded-xl border border-base-300 bg-base-200/40 p-3 flex flex-col gap-3">
      <div class="grid grid-cols-1 md:grid-cols-12 gap-2 items-end">
        <label class="md:col-span-4 flex min-w-0 flex-col gap-1">
          <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Scan</span>
          <select
            :value="selectedScanId"
            class="select select-sm select-bordered w-full"
            :disabled="loadingScans || !scanOptions.length"
            @change="selectScan($event.target.value)"
          >
            <option v-if="!scanOptions.length" value="">No completed scans</option>
            <option v-for="item in scanOptions" :key="item.scan_id" :value="item.scan_id">
              {{ formatDate(item.created_at) }} · {{ scopeLabel(item.scope) }} · {{ STATUS_LABELS[item.status] || item.status }}
            </option>
          </select>
        </label>
        <label class="md:col-span-2 flex flex-col gap-1">
          <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Severity ≥</span>
          <select v-model="filters.severity" class="select select-sm select-bordered w-full" @change="applyFilters">
            <option value="">All</option>
            <option v-for="level in severityOptions" :key="level" :value="level">{{ severityLabel(level) }}</option>
          </select>
        </label>
        <label class="md:col-span-2 flex flex-col gap-1">
          <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Category</span>
          <select v-model="filters.category" class="select select-sm select-bordered w-full" @change="applyFilters">
            <option value="">All</option>
            <option v-for="category in categoryOptions" :key="category" :value="category">
              {{ categoryLabel(category) }}
            </option>
          </select>
        </label>
        <label class="md:col-span-3 flex min-w-0 flex-col gap-1">
          <span class="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Search</span>
          <input
            v-model="filters.query"
            class="input input-sm input-bordered w-full"
            placeholder="rule, path or reason"
            @input="onSearchInput"
          />
        </label>
        <button
          class="btn btn-sm btn-ghost md:col-span-1 md:justify-self-end"
          :disabled="loading || !selectedScanId"
          title="Refresh findings"
          @click="loadFindings"
        >
          <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
        </button>
      </div>

      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="max-w-3xl text-[11px] text-base-content/50">
          High and critical findings are shown by default. Choose <strong>All</strong> to include
          medium-severity entropy heuristics.
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <label class="flex cursor-pointer items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              class="checkbox checkbox-xs"
              :checked="allPageSelected"
              :disabled="!findings.length"
              @change="toggleSelectPage"
            />
            Select this page
          </label>
          <span v-if="selectedCount" class="badge badge-sm badge-primary">{{ selectedCount }} selected</span>
          <button v-if="selectedCount" class="btn btn-xs btn-primary" @click="openTaskDialog">
            <i class="fa-solid fa-list-check"></i> Create task
          </button>
          <button
            v-if="selectedCount"
            class="btn btn-xs btn-outline btn-secondary"
            :disabled="askingCiso"
            title="Open a chat with the ciso profile about the selected findings"
            @click="askCiso()"
          >
            <span v-if="askingCiso" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-comments"></i> Ask CISO
          </button>
          <button v-if="selectedCount" class="btn btn-xs btn-ghost" @click="clearSelection">Clear</button>
        </div>
      </div>
    </div>

    <div v-if="scan" class="flex items-center gap-2 flex-wrap text-xs">
      <span class="badge badge-sm" :class="statusClass(scan.status)">{{ STATUS_LABELS[scan.status] || scan.status }}</span>
      <span class="text-base-content/70">{{ scopeLabel(scan.scope) }}</span>
      <span class="text-base-content/50">{{ formatDate(scan.created_at) }} · {{ formatDuration(scan.duration_seconds) }}</span>
      <span class="badge badge-sm badge-ghost">{{ total }} matching</span>
      <span v-if="scan.summary?.blocked" class="badge badge-sm badge-error">policy would block</span>
      <span v-if="scan.summary?.requires_approval" class="badge badge-sm badge-warning">needs approval</span>
      <span v-if="scan.findings_truncated" class="badge badge-sm badge-warning">stored list truncated</span>
      <span v-if="scanRunning" class="text-info">
        <span class="loading loading-spinner loading-xs"></span> scanning…
      </span>
    </div>

    <div v-if="!selectedScanId" class="text-sm text-base-content/60">
      No scan to review yet. Run an analysis from the <strong>Scans</strong> tab.
    </div>

    <div v-else-if="loading && !findings.length" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Loading findings…
    </div>

    <div v-else-if="scanRunning && !findings.length" class="alert alert-info text-sm">
      <span class="loading loading-spinner loading-xs"></span>
      The scan is still running: findings will appear here as soon as it finishes.
    </div>

    <div v-else-if="!findings.length" class="alert alert-success text-sm">
      <i class="fa-solid fa-shield-halved"></i>
      No finding matches the current filters.
    </div>

    <div v-else class="flex flex-col gap-1.5">
      <div
        v-for="(finding, index) in findings"
        :key="findingKey(finding, index)"
        class="flex items-stretch rounded border border-base-300 bg-base-200/40"
      >
        <label class="flex shrink-0 cursor-pointer items-center pl-2.5" :title="isSelected(finding) ? 'Unselect finding' : 'Select finding'">
          <input
            type="checkbox"
            class="checkbox checkbox-xs"
            :checked="isSelected(finding)"
            @click.stop
            @change="toggleSelection(finding)"
          />
        </label>
        <button
          class="flex-1 text-left px-2.5 py-2 flex items-center gap-2 hover:bg-base-200/70 min-w-0"
          :aria-expanded="expanded === findingKey(finding, index)"
          @click="toggleFinding(finding, index)"
        >
          <span class="badge badge-sm shrink-0" :class="severityClass(finding.severity)">
            {{ severityLabel(finding.severity) }}
          </span>
          <code class="text-xs shrink-0">{{ finding.rule_id }}</code>
          <span class="text-xs truncate flex-1">
            <span class="font-mono">{{ finding.path || '—' }}</span>
            <span v-if="finding.line" class="text-base-content/50">:{{ finding.line }}</span>
            <span class="text-base-content/50"> — {{ finding.title || finding.reason }}</span>
          </span>
          <span class="badge badge-ghost badge-xs hidden sm:inline">{{ categoryLabel(finding.category) }}</span>
          <i class="fa-solid text-xs text-base-content/40" :class="expanded === findingKey(finding, index) ? 'fa-chevron-up' : 'fa-chevron-down'"></i>
        </button>

        <div v-if="expanded === findingKey(finding, index)" class="px-3 pb-3 flex flex-col gap-2 text-xs border-t border-base-300/60 pt-2">
          <div v-if="finding.reason"><span class="text-base-content/50">Reason:</span> {{ finding.reason }}</div>
          <div v-if="finding.excerpt" class="rounded bg-base-300/40 p-2 font-mono break-all whitespace-pre-wrap">
            {{ finding.excerpt }}
          </div>
          <div v-if="finding.remediation">
            <span class="text-base-content/50">Remediation:</span> {{ finding.remediation }}
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button
              class="btn btn-xs btn-outline btn-secondary"
              :disabled="askingCiso"
              @click="askCiso(finding)"
            >
              <span v-if="askingCiso" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-comments"></i> Ask CISO about this
            </button>
            <span v-if="finding.confidence != null" class="text-[11px] text-base-content/50">
              Confidence: {{ Math.round((finding.confidence || 0) * 100) }}%
            </span>
            <span v-if="finding.evidence" class="text-[11px] text-base-content/50">
              Evidence is redacted before it reaches this screen.
            </span>
          </div>
        </div>
      </div>

      <div class="flex items-center justify-between gap-2 text-xs text-base-content/60 pt-1">
        <span>
          Page {{ page }} / {{ pages }} · {{ total }} finding(s)
          <span v-if="rules.length"> · {{ rules.length }} rule(s) triggered</span>
          <span v-if="selectedCount"> · {{ selectedCount }} selected</span>
        </span>
        <div class="flex gap-1">
          <button class="btn btn-xs btn-ghost" :disabled="!hasPrevious || loading" @click="previousPage">
            <i class="fa-solid fa-chevron-left"></i> Prev
          </button>
          <button class="btn btn-xs btn-ghost" :disabled="!hasNext || loading" @click="nextPage">
            Next <i class="fa-solid fa-chevron-right"></i>
          </button>
        </div>
      </div>
    </div>

    <!-- Remediation task dialog ----------------------------------------- -->
    <div
      v-if="taskDialog"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      @click.self="closeTaskDialog"
    >
      <div class="card w-full max-w-2xl border border-base-300 bg-base-100 shadow-2xl">
        <div class="card-body gap-3 p-4">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h3 class="font-bold flex items-center gap-2">
                <i class="fa-solid fa-list-check text-primary"></i> Create a remediation task
              </h3>
              <p class="text-xs text-base-content/50">
                {{ selectedCount }} selected finding(s) will be written into the task description with
                their redacted evidence and remediation. Nothing is auto-fixed.
              </p>
            </div>
            <button class="btn btn-ghost btn-sm btn-square" :disabled="taskCreating" aria-label="Close" @click="closeTaskDialog">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>

          <div v-if="taskError" class="alert alert-error text-sm py-2">
            <i class="fa-solid fa-triangle-exclamation"></i> {{ taskError }}
          </div>
          <div v-if="taskResult" class="alert alert-success text-sm">
            <i class="fa-solid fa-circle-check"></i>
            Created <strong>{{ taskResult.name || 'task' }}</strong>
            <span class="font-mono text-[11px] opacity-70">{{ taskResult.id }}</span>. It is on the board.
          </div>

          <template v-if="!taskResult">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-semibold">Task name</span>
              <input v-model="taskForm.name" class="input input-sm input-bordered w-full" placeholder="Fix CISO findings" />
            </label>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <label class="flex flex-col gap-1">
                <span class="text-xs font-semibold">Board</span>
                <input v-model="taskForm.board" class="input input-sm input-bordered" placeholder="kanban" />
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-xs font-semibold">Column</span>
                <input v-model="taskForm.column" class="input input-sm input-bordered" placeholder="tasks" />
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-xs font-semibold">Priority</span>
                <select v-model="taskForm.priority" class="select select-sm select-bordered">
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </label>
            </div>
            <div class="rounded bg-base-200/60 p-2 text-[11px] text-base-content/60">
              The task gets the tag <code>ciso</code>, the affected files and a checklist of acceptance
              criteria. Board and column must already exist in this project (the defaults are
              <code>kanban</code> / <code>tasks</code>). Use <strong>Create task</strong> once; the selected
              findings are cleared afterwards.
            </div>
          </template>

          <div class="flex justify-end gap-2">
            <button v-if="!taskResult" class="btn btn-sm btn-ghost" :disabled="taskCreating" @click="closeTaskDialog">
              Cancel
            </button>
            <button v-if="!taskResult" class="btn btn-sm btn-primary" :disabled="taskCreating || !taskForm.name" @click="createTask">
              <span v-if="taskCreating" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-plus"></i> Create task
            </button>
            <button v-else class="btn btn-sm btn-primary" @click="closeTaskDialog">Close</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
