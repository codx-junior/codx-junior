<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import { formatMeetCode } from '@/utils/meetCode'

const props = defineProps({
  meetId: { type: String, default: '' }
})
const emit = defineEmits(['close', 'invites-sent'])

const selectedUsers = ref([])
const selectedProfiles = ref([])
const profiles = ref([])
const users = ref([])
const currentMeet = ref(null)
const loading = ref(true)
const sending = ref(false)
const error = ref('')
const copied = ref(false)
const copiedLink = ref(false)
let copyTimeout = null

const meetCode = computed(() => formatMeetCode(props.meetId))
const inviteLink = computed(() => {
  const { origin } = window.location
  const projectName = $storex.projects.activeProject?.project_name
  const base = projectName ? `${origin}/${projectName}` : origin
  return `${base}/meet/${props.meetId}`
})
const onlineUsers = computed(() => {
  const online = $storex.users.onlineUsers || {}
  const current = $storex.users.user?.username
  return Object.values(online).filter(user => user.username && user.username !== current)
})
const availableUsers = computed(() => {
  const merged = new Map()
  ;[...(users.value || []), ...onlineUsers.value].forEach(user => {
    const username = user.username || user.name
    if (!username) return
    merged.set(username, { ...merged.get(username), ...user, username })
  })
  const current = $storex.users.user?.username
  return [...merged.values()].filter(user => user.username !== current)
})
const alreadyInvited = computed(() => {
  const humans = new Set((currentMeet.value?.participantsUsers || []).map(String))
  const profilesSet = new Set(
    (currentMeet.value?.profileParticipants || []).map(profile => profile.profileName || profile.username)
  )
  return { humans, profiles: profilesSet }
})

function avatarFor(value) {
  return value?.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(value?.username || value?.name || value)}`
}

function toggle(list, value) {
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}

function isSelected(list, value) {
  return list.includes(value)
}

async function load() {
  loading.value = true
  try {
    const project = $storex.projects.activeProject
    const results = await Promise.allSettled([
      API.meets.getMeetInfo({ meetId: props.meetId }),
      project ? $storex.profiles.loadProjectProfiles(project) : Promise.resolve([]),
      ($storex.users.users || []).length ? Promise.resolve() : $storex.users.loadUsers()
    ])
    if (results[0].status === 'fulfilled') {
      currentMeet.value = results[0].value?.meet || null
    }
    profiles.value = results[1].status === 'fulfilled' ? (results[1].value || []) : []
    users.value = $storex.users.users || []
  } catch (err) {
    console.warn('[meeting] invite catalogue failed', err)
  } finally {
    loading.value = false
  }
}

async function sendInvites() {
  if (sending.value || (!selectedUsers.value.length && !selectedProfiles.value.length)) return
  sending.value = true
  error.value = ''
  try {
    const result = await API.meets.addParticipants({
      meetId: props.meetId,
      users: selectedUsers.value,
      profiles: selectedProfiles.value
    })
    currentMeet.value = result?.meet || currentMeet.value
    emit('invites-sent', {
      users: selectedUsers.value,
      profiles: selectedProfiles.value
    })
    $storex.session.onInfo(
      `Invited ${selectedUsers.value.length} person(s) and ${selectedProfiles.value.length} profile(s)`
    )
    selectedUsers.value = []
    selectedProfiles.value = []
  } catch (err) {
    console.error('[meeting] invite failed', err)
    error.value = err?.message || 'Could not invite participants'
  } finally {
    sending.value = false
  }
}

function copyMeetId() {
  navigator.clipboard.writeText(meetCode.value)
  copied.value = true
  clearTimeout(copyTimeout)
  copyTimeout = setTimeout(() => { copied.value = false }, 2000)
}

function copyInviteLink() {
  navigator.clipboard.writeText(inviteLink.value)
  copiedLink.value = true
  clearTimeout(copyTimeout)
  copyTimeout = setTimeout(() => { copiedLink.value = false }, 2000)
}

function shareViaClipboard() {
  navigator.clipboard.writeText(`Join my meeting: ${inviteLink.value}\nCode: ${meetCode.value}`)
  $storex.session.onInfo('Invite link copied to clipboard')
}

onMounted(load)

onBeforeUnmount(() => {
  clearTimeout(copyTimeout)
})
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm" @keydown.esc="$emit('close')">
    <div class="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-base-content/10 bg-base-200 shadow-xl">
      <div class="flex items-center justify-between border-b border-base-content/10 px-6 py-4">
        <h2 class="flex items-center gap-2 text-base font-semibold">
          <i class="fa-solid fa-user-plus text-primary"></i>
          Invite to the meeting
        </h2>
        <button class="btn btn-ghost btn-sm btn-circle" @click="$emit('close')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div class="flex-1 space-y-4 overflow-y-auto px-6 py-4">
        <div class="space-y-2 rounded-lg border border-base-content/10 bg-base-content/5 p-3">
          <div class="text-xs font-semibold uppercase text-base-content/60">Meeting code</div>
          <div class="flex items-center gap-2">
            <code class="flex-1 rounded bg-base-300 px-3 py-2 text-center font-mono text-lg tracking-[0.25em]">{{ meetCode }}</code>
            <button class="btn btn-ghost btn-sm btn-square" title="Copy code" @click="copyMeetId">
              <i :class="copied ? 'fa-solid fa-check text-success' : 'fa-regular fa-copy'"></i>
            </button>
          </div>
          <div class="text-[11px] text-base-content/40">Anyone with the code or the link can ask to join.</div>
        </div>

        <div class="space-y-1">
          <div class="text-xs font-semibold uppercase text-base-content/60">Invite link</div>
          <div class="flex items-center gap-2">
            <input :value="inviteLink" readonly class="input input-bordered input-sm min-w-0 flex-1 text-xs" />
            <button class="btn btn-ghost btn-sm btn-square" title="Copy link" @click="copyInviteLink">
              <i :class="copiedLink ? 'fa-solid fa-check text-success' : 'fa-regular fa-copy'"></i>
            </button>
          </div>
        </div>

        <div v-if="loading" class="py-6 text-center">
          <span class="loading loading-spinner loading-sm"></span>
        </div>

        <template v-else>
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-xs font-semibold uppercase text-base-content/60">People</div>
              <span class="text-[11px] text-base-content/40">{{ selectedUsers.length }} selected</span>
            </div>
            <div class="max-h-44 space-y-1 overflow-y-auto">
              <button
                v-for="user in availableUsers"
                :key="user.username"
                class="flex w-full items-center gap-3 rounded-lg border p-2 text-left transition-colors"
                :class="isSelected(selectedUsers, user.username)
                  ? 'border-primary/40 bg-primary/10'
                  : 'border-base-content/10 bg-base-content/5 hover:border-base-content/20'"
                :disabled="alreadyInvited.humans.has(user.username)"
                @click="toggle(selectedUsers, user.username)"
              >
                <img :src="avatarFor(user)" class="h-8 w-8 shrink-0 rounded-full" alt="" />
                <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ user.name || user.username }}</span>
                <span v-if="alreadyInvited.humans.has(user.username)" class="badge badge-xs badge-ghost">already in</span>
                <i v-else-if="isSelected(selectedUsers, user.username)" class="fa-solid fa-check text-primary text-xs"></i>
              </button>
              <p v-if="!availableUsers.length" class="py-3 text-center text-xs text-base-content/40">No other users available.</p>
            </div>
          </div>

          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-xs font-semibold uppercase text-base-content/60">AI profiles</div>
              <span class="text-[11px] text-base-content/40">camera off</span>
            </div>
            <div class="max-h-48 space-y-1 overflow-y-auto">
              <button
                v-for="profile in profiles"
                :key="profile.name"
                class="flex w-full items-center gap-3 rounded-lg border p-2 text-left transition-colors"
                :class="isSelected(selectedProfiles, profile.name)
                  ? 'border-secondary/40 bg-secondary/10'
                  : 'border-base-content/10 bg-base-content/5 hover:border-base-content/20'"
                :disabled="alreadyInvited.profiles.has(profile.name)"
                @click="toggle(selectedProfiles, profile.name)"
              >
                <img :src="avatarFor(profile)" class="h-8 w-8 shrink-0 rounded-full" alt="" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium">{{ profile.name }}</span>
                  <span class="block truncate text-[11px] text-base-content/40">{{ profile.description || 'AI teammate' }}</span>
                </span>
                <span v-if="alreadyInvited.profiles.has(profile.name)" class="badge badge-xs badge-ghost">in meeting</span>
                <i v-else-if="isSelected(selectedProfiles, profile.name)" class="fa-solid fa-check text-secondary text-xs"></i>
              </button>
              <p v-if="!profiles.length" class="py-3 text-center text-xs text-base-content/40">No profiles in this project.</p>
            </div>
          </div>
        </template>

        <p v-if="error" class="rounded-lg bg-error/10 p-2 text-xs text-error">{{ error }}</p>

        <div class="flex gap-2">
          <button class="btn btn-ghost btn-sm flex-1 border border-base-content/20" @click="shareViaClipboard">
            <i class="fa-regular fa-clipboard"></i> Copy invitation
          </button>
        </div>
      </div>

      <div class="flex gap-3 border-t border-base-content/10 px-6 py-4">
        <button class="btn btn-ghost btn-sm flex-1" @click="$emit('close')">Close</button>
        <button
          class="btn btn-primary btn-sm flex-1 gap-2"
          :disabled="sending || (!selectedUsers.length && !selectedProfiles.length)"
          @click="sendInvites"
        >
          <span v-if="sending" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-paper-plane"></i>
          Send invites
        </button>
      </div>
    </div>
  </div>
</template>
