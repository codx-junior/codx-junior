<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { API } from '@/api/api'
import { $storex } from '@/store'

const route = useRoute()
const props = defineProps({
  // Desktop app panels receive their parameters through `params`; mobile uses
  // the route. Supporting both keeps the same view in both shells.
  params: { type: Object, default: () => ({}) }
})
const meetId = computed(() => String(
  props.params?.meetId ||
  props.params?.params?.meetId ||
  route.params.meetId ||
  ''
))
const meet = ref(null)
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const generating = ref(false)
const board = ref('kanban')
const column = ref('tasks')
const runTasks = ref(false)
const scheduleAt = ref('')
const selected = ref([])
const recordingUrl = ref('')
let pollTimer = null

const minutes = computed(() => meet.value?.minutes || { status: 'none', summary: '', action_items: [] })
const actionItems = computed(() => minutes.value.action_items || [])
const pendingItems = computed(() => actionItems.value.filter(item => item.status !== 'created' && item.status !== 'dismissed'))
const transcript = computed(() => meet.value?.transcript || [])
const profiles = computed(() => meet.value?.profileParticipants || [])
const participants = computed(() => meet.value?.participantsUsers || meet.value?.participants || [])
const isLive = computed(() => meet.value?.status === 'active')
const canCreate = computed(() => selected.value.length > 0 || pendingItems.value.length > 0)

function displayDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

function profileAvatar(profile) {
  return profile.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(profile.name || profile.username)}`
}

function toggle(item) {
  const id = item.id || item.title
  const index = selected.value.indexOf(id)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(id)
}

function isSelected(item) {
  return selected.value.includes(item.id || item.title)
}

async function loadRecording() {
  if (meet.value?.recording?.status !== 'ready') return
  try {
    const blob = await API.meets.recordingBlob({ meetId: meetId.value })
    if (blob instanceof Blob && recordingUrl.value) URL.revokeObjectURL(recordingUrl.value)
    recordingUrl.value = blob instanceof Blob ? URL.createObjectURL(blob) : ''
  } catch (err) {
    console.warn('[meeting] could not load the recording', err)
  }
}

async function load({ silent = false } = {}) {
  if (!meetId.value) return
  if (!silent) loading.value = true
  error.value = ''
  try {
    const result = await API.meets.getMeetInfo({ meetId: meetId.value })
    meet.value = result?.meet || null
    if (meet.value?.board) board.value = meet.value.board
    if (meet.value?.minutes?.status === 'generating') startPolling()
    if (meet.value?.recording?.status === 'ready' && !recordingUrl.value) await loadRecording()
  } catch (err) {
    console.error('[meeting] summary load failed', err)
    error.value = err?.message || 'Could not load the meeting'
  } finally {
    loading.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await load({ silent: true })
    if (meet.value?.minutes?.status !== 'generating') stopPolling()
  }, 2500)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function generateMinutes() {
  if (generating.value) return
  generating.value = true
  error.value = ''
  try {
    await API.meets.generateNotes({ meetId: meetId.value, force: true, async: true })
    meet.value.minutes = { ...(meet.value.minutes || {}), status: 'generating' }
    startPolling()
  } catch (err) {
    console.error('[meeting] minutes failed', err)
    error.value = err?.message || 'Could not generate the minutes'
  } finally {
    generating.value = false
  }
}

async function createTasks() {
  if (!canCreate.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const chosen = pendingItems.value.filter(item => isSelected(item))
    const items = chosen.length
      ? chosen.map(item => ({
          id: item.id,
          title: item.title,
          description: item.description || item.title,
          assignee: item.assignee,
          profile: item.profile_name,
          priority: item.priority,
          due_date: item.due_date,
          scheduled_at: item.scheduled_at,
          run: item.run
        }))
      : null
    await API.meets.createTasks({
      meetId: meetId.value,
      items,
      board: board.value || null,
      column: column.value || null,
      run: runTasks.value,
      schedule_at: scheduleAt.value || null
    })
    $storex.session.onInfo(
      `${items ? items.length : pendingItems.value.length} task(s) created${runTasks.value ? ' and running' : ''}`
    )
    selected.value = []
    await load({ silent: true })
  } catch (err) {
    console.error('[meeting] task creation failed', err)
    error.value = err?.message || 'Could not create the tasks'
  } finally {
    busy.value = false
  }
}

function joinLive() {
  $storex.ui.openMeet(meetId.value)
}

function copyMinutes() {
  const lines = [
    `# ${meet.value?.name || 'Meeting'}`,
    '',
    minutes.value.summary || '',
    '',
    ...(minutes.value.decisions || []).map(decision => `- Decision: ${decision}`),
    ...(minutes.value.action_items || []).map(item => `- [ ] ${item.title}${item.assignee ? ` (@${item.assignee})` : ''}`),
    '',
    '## Transcript',
    ...transcript.value.map(entry => `**${entry.speaker}:** ${entry.text}`)
  ]
  navigator.clipboard.writeText(lines.join('\n'))
  $storex.session.onInfo('Minutes copied to the clipboard')
}

onMounted(load)
onBeforeUnmount(() => {
  stopPolling()
  if (recordingUrl.value) URL.revokeObjectURL(recordingUrl.value)
})
</script>

<template>
  <div class="h-full overflow-y-auto bg-base-100">
    <div class="mx-auto max-w-5xl px-4 py-6 md:px-8">
      <div class="mb-5 flex flex-wrap items-start gap-3">
        <button class="btn btn-ghost btn-sm gap-1" @click="$storex.ui.openMeet()">
          <i class="fa-solid fa-arrow-left"></i>
          Meetings
        </button>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <h1 class="truncate text-xl font-bold">{{ meet?.name || 'Meeting summary' }}</h1>
            <span v-if="isLive" class="badge badge-success badge-sm gap-1">
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-success-content"></span>
              Live
            </span>
            <span v-else class="badge badge-ghost badge-sm">Ended</span>
          </div>
          <p class="mt-0.5 text-xs text-base-content/50">
            {{ meet?.agenda || 'No agenda' }}
            <span v-if="meet?.ended_at"> · {{ displayDate(meet.ended_at) }}</span>
            <span v-else-if="meet?.created_at"> · {{ displayDate(meet.created_at) }}</span>
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button v-if="isLive" class="btn btn-primary btn-sm gap-1" @click="joinLive">
            <i class="fa-solid fa-phone"></i> Rejoin
          </button>
          <button class="btn btn-ghost btn-sm gap-1" @click="load()">
            <i class="fa-solid fa-rotate"></i> Refresh
          </button>
          <button class="btn btn-ghost btn-sm gap-1" @click="copyMinutes">
            <i class="fa-regular fa-copy"></i> Copy
          </button>
        </div>
      </div>

      <div v-if="loading" class="grid place-items-center py-20">
        <span class="loading loading-spinner loading-lg"></span>
      </div>
      <div v-else-if="!meet" class="rounded-xl border border-dashed border-base-content/20 py-20 text-center">
        <i class="fa-solid fa-video-slash mb-3 block text-3xl text-base-content/30"></i>
        <p class="text-sm font-semibold">Meeting not found</p>
        <p class="mt-1 text-xs text-base-content/50">{{ error || 'The meeting may belong to another project.' }}</p>
      </div>

      <template v-else>
        <p v-if="error" class="mb-4 rounded-lg bg-error/10 p-3 text-xs text-error">{{ error }}</p>

        <!-- Attendees and recording -->
        <section class="mb-5 grid gap-3 md:grid-cols-[1.3fr_1fr]">
          <div class="rounded-xl border border-base-content/10 bg-base-200/50 p-4">
            <div class="mb-3 flex items-center justify-between">
              <h2 class="text-sm font-bold"><i class="fa-solid fa-users mr-1 text-primary"></i> Attendees</h2>
              <span class="text-[11px] text-base-content/40">{{ participants.length }} people · {{ profiles.length }} AI</span>
            </div>
            <div class="flex flex-wrap gap-2">
              <div v-for="username in participants" :key="`p-${username}`" class="flex items-center gap-1.5 rounded-full border border-base-content/10 px-2 py-1">
                <img :src="`https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(username)}`" class="h-6 w-6 rounded-full" alt="" />
                <span class="text-xs">{{ username }}</span>
              </div>
              <div v-for="profile in profiles" :key="`pr-${profile.profileName || profile.username}`" class="flex items-center gap-1.5 rounded-full border border-secondary/20 bg-secondary/5 px-2 py-1">
                <img :src="profileAvatar(profile)" class="h-6 w-6 rounded-full" alt="" />
                <span class="text-xs">{{ profile.name || profile.username }}</span>
                <span class="text-[9px] font-bold text-secondary">AI</span>
              </div>
            </div>
          </div>

          <div class="rounded-xl border border-base-content/10 bg-base-200/50 p-4">
            <div class="mb-2 flex items-center justify-between">
              <h2 class="text-sm font-bold"><i class="fa-solid fa-microphone mr-1 text-primary"></i> Recording</h2>
              <span class="badge badge-xs" :class="meet.recording?.status === 'ready' ? 'badge-success' : 'badge-ghost'">
                {{ meet.recording?.status || 'none' }}
              </span>
            </div>
            <audio v-if="recordingUrl" :src="recordingUrl" controls class="w-full"></audio>
            <p v-else class="text-xs text-base-content/50">
              {{ meet.recording?.status === 'ready' ? 'Loading recording…' : 'No recording for this meeting.' }}
            </p>
            <p v-if="meet.recording?.size" class="mt-1 text-[11px] text-base-content/40">
              {{ (meet.recording.size / 1024 / 1024).toFixed(1) }} MB
              <span v-if="meet.recording.duration_seconds"> · {{ Math.round(meet.recording.duration_seconds) }}s</span>
            </p>
          </div>
        </section>

        <!-- Minutes -->
        <section class="mb-5 rounded-xl border border-base-content/10 bg-base-200/40">
          <div class="flex items-center gap-2 border-b border-base-content/10 px-4 py-3">
            <h2 class="text-sm font-bold"><i class="fa-solid fa-pen-to-square mr-1 text-primary"></i> Minutes</h2>
            <span class="badge badge-xs" :class="minutes.status === 'ready' ? 'badge-success' : (minutes.status === 'generating' ? 'badge-warning' : 'badge-ghost')">
              {{ minutes.status }}
            </span>
            <span class="ml-auto text-[11px] text-base-content/40">
              <span v-if="minutes.generated_at">{{ displayDate(minutes.generated_at) }}</span>
            </span>
            <button class="btn btn-ghost btn-xs gap-1" :disabled="generating || minutes.status === 'generating'" @click="generateMinutes">
              <span v-if="generating || minutes.status === 'generating'" class="loading loading-spinner loading-xs"></span>
              <i v-else class="fa-solid fa-wand-magic-sparkles"></i>
              {{ minutes.status === 'ready' ? 'Regenerate' : 'Generate' }}
            </button>
          </div>
          <div class="p-4">
            <div v-if="minutes.status === 'generating'" class="rounded-lg border border-primary/30 bg-primary/10 p-3 text-xs text-primary">
              <span class="loading loading-spinner loading-xs mr-1"></span>
              The minute taker is reading the transcript…
            </div>
            <div v-else-if="minutes.error" class="mb-3 rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
              {{ minutes.error }}
            </div>

            <p v-if="minutes.summary" class="whitespace-pre-line text-sm leading-relaxed">{{ minutes.summary }}</p>
            <p v-else-if="minutes.status !== 'generating'" class="text-xs text-base-content/50">
              No minutes yet. Generate them from the transcript, or end the meeting to generate them automatically.
            </p>

            <div v-if="minutes.key_points?.length" class="mt-4">
              <h3 class="mb-1 text-[11px] font-bold uppercase tracking-wide text-base-content/40">Key points</h3>
              <ul class="list-inside list-disc space-y-1 text-xs">
                <li v-for="(point, index) in minutes.key_points" :key="`kp-${index}`">{{ point }}</li>
              </ul>
            </div>
            <div v-if="minutes.decisions?.length" class="mt-4">
              <h3 class="mb-1 text-[11px] font-bold uppercase tracking-wide text-base-content/40">Decisions</h3>
              <ul class="space-y-1 text-xs">
                <li v-for="(decision, index) in minutes.decisions" :key="`d-${index}`" class="flex gap-1.5">
                  <i class="fa-solid fa-check mt-0.5 text-success"></i>
                  <span>{{ decision }}</span>
                </li>
              </ul>
            </div>
          </div>
        </section>

        <!-- Action items + tasks -->
        <section class="mb-5 rounded-xl border border-base-content/10 bg-base-200/40">
          <div class="flex flex-wrap items-center gap-2 border-b border-base-content/10 px-4 py-3">
            <h2 class="text-sm font-bold"><i class="fa-solid fa-list-check mr-1 text-primary"></i> Action items</h2>
            <span class="badge badge-xs badge-ghost">{{ actionItems.length }}</span>
            <span class="ml-auto text-[11px] text-base-content/40">Select items and create tasks, run or schedule them.</span>
          </div>
          <div class="space-y-3 p-4">
            <div v-if="!actionItems.length" class="rounded-lg border border-dashed border-base-content/20 p-4 text-center text-xs text-base-content/50">
              No action items were extracted from this meeting.
            </div>
            <div
              v-for="item in actionItems"
              :key="item.id || item.title"
              class="rounded-lg border p-3 transition-colors"
              :class="item.status === 'created' ? 'border-success/30 bg-success/5' : (isSelected(item) ? 'border-primary/40 bg-primary/5' : 'border-base-content/10')"
            >
              <div class="flex items-start gap-2">
                <input
                  v-if="item.status !== 'created' && item.status !== 'dismissed'"
                  type="checkbox"
                  class="checkbox checkbox-sm checkbox-primary mt-0.5"
                  :checked="isSelected(item)"
                  @change="toggle(item)"
                />
                <i v-else class="fa-solid fa-circle-check mt-1 text-success"></i>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="text-sm font-semibold">{{ item.title }}</span>
                    <span class="badge badge-xs" :class="item.priority === 'high' ? 'badge-error' : (item.priority === 'low' ? 'badge-ghost' : 'badge-warning')">{{ item.priority }}</span>
                    <span v-if="item.status === 'created'" class="badge badge-xs badge-success">task created</span>
                  </div>
                  <p v-if="item.description" class="mt-1 text-xs text-base-content/60">{{ item.description }}</p>
                  <div class="mt-1 flex flex-wrap gap-2 text-[11px] text-base-content/40">
                    <span v-if="item.assignee"><i class="fa-solid fa-user mr-0.5"></i>{{ item.assignee }}</span>
                    <span v-if="item.profile_name"><i class="fa-solid fa-robot mr-0.5"></i>{{ item.profile_name }}</span>
                    <span v-if="item.due_date"><i class="fa-regular fa-calendar mr-0.5"></i>{{ item.due_date }}</span>
                    <span v-if="item.scheduled_at"><i class="fa-regular fa-clock mr-0.5"></i>{{ item.scheduled_at }}</span>
                    <span v-if="item.run" class="text-primary">run now</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="grid gap-3 rounded-lg border border-base-content/10 bg-base-100/60 p-3 md:grid-cols-[1fr_1fr_1.2fr]">
              <div>
                <label class="mb-1 block text-[11px] font-semibold text-base-content/60">Board</label>
                <input v-model="board" class="input input-sm input-bordered w-full" placeholder="kanban" />
              </div>
              <div>
                <label class="mb-1 block text-[11px] font-semibold text-base-content/60">Column</label>
                <input v-model="column" class="input input-sm input-bordered w-full" placeholder="tasks" />
              </div>
              <div>
                <label class="mb-1 block text-[11px] font-semibold text-base-content/60">Schedule at (optional)</label>
                <input v-model="scheduleAt" type="datetime-local" class="input input-sm input-bordered w-full" />
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <label class="flex cursor-pointer items-center gap-2 text-xs">
                <input v-model="runTasks" type="checkbox" class="checkbox checkbox-xs checkbox-primary" />
                Run the created tasks immediately
              </label>
              <button class="btn btn-primary btn-sm ml-auto gap-2" :disabled="!canCreate || busy" @click="createTasks">
                <span v-if="busy" class="loading loading-spinner loading-xs"></span>
                <i v-else class="fa-solid fa-list-check"></i>
                Create tasks
              </button>
            </div>
          </div>
        </section>

        <!-- Transcript -->
        <section class="rounded-xl border border-base-content/10 bg-base-200/40">
          <div class="flex items-center gap-2 border-b border-base-content/10 px-4 py-3">
            <h2 class="text-sm font-bold"><i class="fa-solid fa-closed-captioning mr-1 text-primary"></i> Transcript</h2>
            <span class="badge badge-xs badge-ghost">{{ transcript.length }}</span>
          </div>
          <div class="max-h-[32rem] space-y-2 overflow-y-auto p-4">
            <div v-if="!transcript.length" class="py-8 text-center text-xs text-base-content/50">
              This meeting has no transcript.
            </div>
            <div
              v-for="entry in transcript"
              :key="entry.entry_id || entry.entryId || `${entry.at}-${entry.speaker}`"
              class="rounded-lg px-3 py-2"
              :class="(entry.speaker_kind || entry.speakerKind) === 'profile' ? 'bg-secondary/5' : 'bg-base-content/5'"
            >
              <div class="flex items-center gap-2 text-[11px] text-base-content/40">
                <span class="font-semibold" :class="(entry.speaker_kind || entry.speakerKind) === 'profile' ? 'text-secondary' : 'text-base-content/70'">{{ entry.speaker }}</span>
                <span>{{ displayDate(entry.at) }}</span>
                <span v-if="entry.interrupted" class="text-warning">interrupted</span>
              </div>
              <p class="mt-0.5 whitespace-pre-line text-sm">{{ entry.text }}</p>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>
