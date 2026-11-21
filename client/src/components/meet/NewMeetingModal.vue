<script setup>
import { computed, onMounted, ref } from 'vue'
import { $storex } from '@/store'

// The dialog can be hosted by a view (MeetView) or by the global host in
// App.vue: `close` lets the host that owns the instance hide it.
const emit = defineEmits(['close'])
const prefill = computed(() => $storex.ui.meetingSetup || {})
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const users = ref([])
const profiles = ref([])
const teams = ref([])
const boards = ref([])

const form = ref({
  name: '',
  agenda: '',
  teamId: '',
  board: '',
  selectedUsers: [],
  selectedProfiles: [],
  settings: {
    recording_enabled: true,
    auto_minutes: true,
    auto_respond: true,
    live_notes: false,
    max_profile_turns: 2
  }
})

function displayName(user) {
  return user.name || user.username || user
}

function userAvatar(user) {
  return user.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(user.username || user.name || user)}`
}

function profileAvatar(profile) {
  return profile.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(profile.name)}`
}

function toggle(listName, value) {
  const list = form.value[listName]
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}

function isSelected(listName, value) {
  return form.value[listName].includes(value)
}

function applyPrefill() {
  const source = prefill.value || {}
  form.value.name = source.name || `Team review – ${new Date().toLocaleDateString()}`
  form.value.agenda = source.agenda || ''
  form.value.teamId = source.teamId || ''
  form.value.board = source.board || ''
  form.value.selectedUsers = (source.users || []).filter(Boolean).map(String)
  form.value.selectedProfiles = (source.profiles || [])
    .map(profile => (typeof profile === 'string' ? profile : profile?.name || profile?.profile_name))
    .filter(Boolean)
}

function applyTeam(teamId) {
  const team = teams.value.find(candidate => candidate.id === teamId)
  if (!team) return
  const teamUsers = (team.members || []).map(member => member.username).filter(Boolean)
  const teamProfiles = (team.profiles || []).filter(Boolean)
  form.value.selectedUsers = [...new Set([...form.value.selectedUsers, ...teamUsers])]
  form.value.selectedProfiles = [...new Set([...form.value.selectedProfiles, ...teamProfiles])]
}

async function loadCatalog() {
  loading.value = true
  error.value = ''
  try {
    const project = $storex.projects.activeProject
    if (project) {
      if (!($storex.users.users || []).length) {
        await $storex.users.loadUsers().catch(() => {})
      }
      users.value = ($storex.users.users || []).filter(user => user?.username)
      profiles.value = await $storex.profiles
        .loadProjectProfiles(project)
        .catch(() => [])
    }
    teams.value = $storex.teams.teams || []
    boards.value = ($storex.kanban.storedBoards || []).filter(board => board?.title)
  } catch (err) {
    console.warn('[meeting] could not load the participant catalogue', err)
    error.value = 'Could not load the team and profile catalogues.'
  } finally {
    loading.value = false
  }
}

function onStart() {
  if (starting.value) return
  const name = (form.value.name || '').trim()
  if (!name) {
    error.value = 'Give the meeting a name.'
    return
  }
  startMeeting(name)
}

async function startMeeting(name) {
  starting.value = true
  error.value = ''
  try {
    const me = $storex.users.user?.username
    const selectedUsers = form.value.selectedUsers.filter(username => username && username !== me)
    const participants = selectedUsers.map(username => ({ username, kind: 'user' }))
    const meetId = await $storex.meets.initiateMeet({
      name,
      agenda: form.value.agenda,
      participants,
      profiles: form.value.selectedProfiles,
      invitedUsers: selectedUsers,
      board: form.value.board || null,
      channelId: prefill.value.channelId || null,
      channelName: prefill.value.channelName || '',
      teamId: form.value.teamId || prefill.value.teamId || null,
      source: prefill.value.source || 'manual',
      settings: { ...form.value.settings }
    })
    $storex.ui.closeMeetingSetup()
    // The host that owns this instance (MeetView or App.vue) hides the dialog.
    emit('close')
    $storex.ui.openMeet(meetId)
  } catch (err) {
    console.error('[meeting] could not start the meeting', err)
    error.value = err?.message || 'Could not start the meeting.'
  } finally {
    starting.value = false
  }
}

onMounted(async () => {
  applyPrefill()
  await loadCatalog()
  if (form.value.teamId) applyTeam(form.value.teamId)
})
</script>

<template>
  <div class="w-full max-w-4xl max-h-[86vh] overflow-y-auto rounded-2xl bg-base-100 border border-base-content/10 shadow-2xl">
    <!-- Header -->
    <div class="flex items-start gap-3 border-b border-base-content/10 px-5 py-4">
      <div class="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary/15 text-primary">
        <i class="fa-solid fa-video"></i>
      </div>
      <div class="min-w-0 flex-1">
        <h2 class="text-base font-bold leading-tight">Start a team meeting</h2>
        <p class="mt-0.5 text-xs text-base-content/50">
          Invite people and AI profiles. The meeting is recorded, transcribed and turned into minutes.
        </p>
      </div>
      <button class="btn btn-ghost btn-sm btn-circle shrink-0" title="Close" @click="$storex.ui.closeMeetingSetup(); emit('close')">
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>

    <div class="grid gap-5 p-5 md:grid-cols-[1.1fr_1fr]">
      <!-- Left: meeting definition -->
      <section class="space-y-4">
        <div>
          <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Meeting name</label>
          <input v-model="form.name" class="input input-sm input-bordered w-full" placeholder="Weekly team review" />
        </div>
        <div>
          <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Agenda</label>
          <textarea
            v-model="form.agenda"
            class="textarea textarea-bordered w-full text-sm"
            rows="3"
            placeholder="Review the status of tasks, unblock the team and decide next steps..."
          ></textarea>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Review board</label>
            <select v-model="form.board" class="select select-sm select-bordered w-full">
              <option value="">All boards / recent tasks</option>
              <option v-for="board in boards" :key="board.title" :value="board.title">{{ board.title }}</option>
            </select>
          </div>
          <div>
            <label class="mb-1 block text-xs font-bold uppercase tracking-wide text-base-content/50">Call a team</label>
            <select v-model="form.teamId" class="select select-sm select-bordered w-full" @change="applyTeam(form.teamId)">
              <option value="">Choose a team…</option>
              <option v-for="team in teams" :key="team.id" :value="team.id">{{ team.name }}</option>
            </select>
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <span class="text-xs font-bold uppercase tracking-wide text-base-content/50">People</span>
            <span class="text-[11px] text-base-content/40">{{ form.selectedUsers.length }} invited</span>
          </div>
          <div v-if="loading" class="py-3 text-center text-xs text-base-content/40">
            <span class="loading loading-spinner loading-xs"></span> Loading people…
          </div>
          <div v-else class="grid max-h-44 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
            <button
              v-for="user in users"
              :key="user.username"
              type="button"
              class="flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors"
              :class="isSelected('selectedUsers', user.username)
                ? 'border-primary/50 bg-primary/10'
                : 'border-base-content/10 hover:bg-base-content/5'"
              @click="toggle('selectedUsers', user.username)"
            >
              <img :src="userAvatar(user)" class="h-7 w-7 rounded-full object-cover" alt="" />
              <span class="min-w-0 flex-1 truncate text-xs font-medium">{{ displayName(user) }}</span>
              <i v-if="isSelected('selectedUsers', user.username)" class="fa-solid fa-check text-primary text-xs"></i>
            </button>
            <p v-if="!users.length" class="col-span-full py-2 text-xs text-base-content/40">No other people found.</p>
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <span class="text-xs font-bold uppercase tracking-wide text-base-content/50">AI profiles</span>
            <span class="text-[11px] text-base-content/40">camera off · avatar only</span>
          </div>
          <div v-if="loading" class="py-3 text-center text-xs text-base-content/40">
            <span class="loading loading-spinner loading-xs"></span> Loading profiles…
          </div>
          <div v-else class="grid max-h-52 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
            <button
              v-for="profile in profiles"
              :key="profile.name"
              type="button"
              class="flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors"
              :class="isSelected('selectedProfiles', profile.name)
                ? 'border-secondary/50 bg-secondary/10'
                : 'border-base-content/10 hover:bg-base-content/5'"
              @click="toggle('selectedProfiles', profile.name)"
            >
              <img :src="profileAvatar(profile)" class="h-7 w-7 rounded-full object-cover" alt="" />
              <span class="min-w-0 flex-1">
                <span class="block truncate text-xs font-medium">{{ profile.name }}</span>
                <span class="block truncate text-[10px] text-base-content/40">{{ profile.description || 'AI teammate' }}</span>
              </span>
              <i v-if="isSelected('selectedProfiles', profile.name)" class="fa-solid fa-check text-secondary text-xs"></i>
            </button>
            <p v-if="!profiles.length" class="col-span-full py-2 text-xs text-base-content/40">No profiles in this project.</p>
          </div>
        </div>
      </section>

      <!-- Right: meeting behaviour -->
      <section class="space-y-3 rounded-xl border border-base-content/10 bg-base-200/40 p-4">
        <h3 class="text-sm font-bold">Meeting behaviour</h3>

        <label class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-content/5">
          <input v-model="form.settings.recording_enabled" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
          <span>
            <span class="block text-xs font-semibold">Record the call</span>
            <span class="block text-[11px] text-base-content/50">The host mixes the microphones and saves a playable file with the minutes.</span>
          </span>
        </label>

        <label class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-content/5">
          <input v-model="form.settings.auto_minutes" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
          <span>
            <span class="block text-xs font-semibold">Minutes at the end</span>
            <span class="block text-[11px] text-base-content/50">Summary, decisions and actionable items generated when the meeting ends.</span>
          </span>
        </label>

        <label class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-content/5">
          <input v-model="form.settings.auto_respond" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
          <span>
            <span class="block text-xs font-semibold">Profiles answer live</span>
            <span class="block text-[11px] text-base-content/50">One at a time. They stop speaking as soon as a person starts talking.</span>
          </span>
        </label>

        <label class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-content/5">
          <input v-model="form.settings.live_notes" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
          <span>
            <span class="block text-xs font-semibold">Live notes</span>
            <span class="block text-[11px] text-base-content/50">Refresh the notes during the meeting, not only when it ends.</span>
          </span>
        </label>

        <div class="rounded-lg border border-base-content/10 p-2">
          <label class="mb-1 block text-[11px] font-semibold text-base-content/60">Profile answers per turn</label>
          <input
            v-model.number="form.settings.max_profile_turns"
            type="range" min="1" max="4" step="1"
            class="range range-xs range-primary"
          />
          <div class="mt-1 text-[11px] text-base-content/40">{{ form.settings.max_profile_turns }} profile(s) may answer each human turn.</div>
        </div>

        <div class="rounded-lg bg-info/10 p-3 text-[11px] leading-relaxed text-info">
          <i class="fa-solid fa-circle-info mr-1"></i>
          Everything said by people and profiles is stored in the meeting chat. The minutes can create tasks and schedule them after the call.
        </div>

        <p v-if="error" class="rounded-lg bg-error/10 p-2 text-xs text-error">{{ error }}</p>
      </section>
    </div>

    <!-- Footer -->
    <div class="flex items-center justify-end gap-2 border-t border-base-content/10 px-5 py-4">
      <button class="btn btn-ghost btn-sm" @click="$storex.ui.closeMeetingSetup(); emit('close')">Cancel</button>
      <button class="btn btn-primary btn-sm gap-2" :disabled="starting" @click="onStart">
        <span v-if="starting" class="loading loading-spinner loading-xs"></span>
        <i v-else class="fa-solid fa-video"></i>
        Start meeting
      </button>
    </div>
  </div>
</template>
