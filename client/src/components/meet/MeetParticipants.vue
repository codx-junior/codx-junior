<script setup>
import { computed, ref } from 'vue'
import MicIndicator from './MicIndicator.vue'
import { $storex } from '@/store'

const props = defineProps({
  meet: { type: Object, default: null }
})

const currentUser = computed(() => $storex.users.user?.username || 'me')
const humans = computed(() => (props.meet?.participants || []).map(username => ({ username, kind: 'user' })))
const profiles = computed(() => props.meet?.profileParticipants || [])
const floor = computed(() => props.meet?.floor || {})
const profilesByName = computed(() => {
  const map = {}
  ;(props.meet?.profileParticipants || []).forEach(profile => {
    map[profile.profileName || profile.username] = profile
  })
  return map
})

function avatarForHuman(username) {
  if (username === currentUser.value) {
    return $storex.users.user?.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(username)}`
  }
  const online = $storex.users.onlineUsers?.[username]
  return online?.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(username)}`
}

function isMuted(username) {
  if (username === currentUser.value) return !$storex.meets.isLocalMicOn
  return false
}

function isSpeaking(username) {
  if (username === currentUser.value) return $storex.meets.isMicSpeaking
  return floor.value?.holder === username && floor.value?.kind === 'user'
}

function profileIsSpeaking(profile) {
  const name = profile.profileName || profile.username
  return floor.value?.holder === name
}

const initiator = computed(() => props.meet?.initiator || '')
const isInitiator = computed(() => !!initiator.value && initiator.value === currentUser.value)
const removing = ref('')

/** The initiator may drop a human peer; an AI profile can always be removed. */
function canRemovePerson(username) {
  return isInitiator.value && username !== currentUser.value
}

async function removeFromCall(name) {
  const meetId = props.meet?.meetId
  if (!meetId || !name || removing.value) return
  removing.value = name
  try {
    await $storex.meets.removeParticipant({ meetId, name })
  } finally {
    removing.value = ''
  }
}
</script>

<template>
  <div class="space-y-4 p-4">
    <div class="flex items-center justify-between">
      <h3 class="text-sm font-semibold">
        Participants
        <span class="font-normal text-base-content/60">({{ humans.length + profiles.length }})</span>
      </h3>
      <span v-if="profiles.length" class="badge badge-xs badge-secondary gap-1">
        <i class="fa-solid fa-robot"></i> {{ profiles.length }} AI
      </span>
    </div>

    <div v-if="humans.length || profiles.length" class="space-y-2">
      <!-- People -->
      <div
        v-for="person in humans"
        :key="person.username"
        class="flex items-center gap-3 rounded-lg border border-base-content/10 bg-base-content/5 p-2.5 transition-all"
        :class="isSpeaking(person.username) ? 'ring-2 ring-success/60' : ''"
      >
        <img :src="avatarForHuman(person.username)" class="h-10 w-10 shrink-0 rounded-full object-cover" alt="" />
        <div class="min-w-0 flex-1">
          <div class="truncate text-sm font-semibold">{{ person.username }}</div>
          <div class="text-xs text-base-content/50">
            <span v-if="person.username === currentUser" class="badge badge-xs badge-ghost">You</span>
            <span v-else-if="floor.holder === person.username && floor.kind === 'user'" class="text-success">Speaking…</span>
            <span v-else class="text-base-content/40">Connected</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <div class="flex h-7 w-7 items-center justify-center rounded-lg" :class="isMuted(person.username) ? 'bg-base-content/10' : 'bg-success/20'">
            <MicIndicator :enabled="!isMuted(person.username)" :speaking="isSpeaking(person.username)" size="sm" />
          </div>
          <button
            v-if="canRemovePerson(person.username)"
            class="flex h-7 w-7 items-center justify-center rounded-lg bg-base-content/10 text-base-content/60 transition-colors hover:bg-error/20 hover:text-error"
            :disabled="removing === person.username"
            title="Remove from the call"
            @click="removeFromCall(person.username)"
          >
            <i class="fa-solid fa-user-minus text-xs"></i>
          </button>
        </div>
      </div>

      <!-- AI profiles: camera off, profile avatar -->
      <div
        v-for="profile in profiles"
        :key="profile.profileName || profile.username"
        class="flex items-center gap-3 rounded-lg border border-secondary/20 bg-secondary/5 p-2.5 transition-all"
        :class="profileIsSpeaking(profile) ? 'ring-2 ring-secondary/60' : ''"
      >
        <div class="relative h-10 w-10 shrink-0">
          <img :src="profile.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(profile.name)}`" class="h-10 w-10 rounded-full object-cover" alt="" />
          <span class="absolute -bottom-1 -right-1 grid h-5 w-5 place-items-center rounded-full border-2 border-base-200 bg-secondary text-[9px] font-bold text-secondary-content">AI</span>
        </div>
        <div class="min-w-0 flex-1">
          <div class="truncate text-sm font-semibold">{{ profile.name || profile.username }}</div>
          <div class="text-xs text-base-content/50">
            <span v-if="profileIsSpeaking(profile)" class="text-secondary">Answering…</span>
            <span v-else>Camera off · avatar</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <div class="flex h-7 w-7 items-center justify-center rounded-lg" :class="profileIsSpeaking(profile) ? 'bg-secondary/25' : 'bg-base-content/5'">
            <i class="fa-solid fa-volume-high text-xs" :class="profileIsSpeaking(profile) ? 'text-secondary' : 'text-base-content/40'"></i>
          </div>
          <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-base-content/5" title="Camera off">
            <i class="fa-solid fa-video-slash text-xs text-base-content/30"></i>
          </div>
          <button
            class="flex h-7 w-7 items-center justify-center rounded-lg bg-error/10 text-error transition-colors hover:bg-error/20"
            :disabled="removing === (profile.profileName || profile.username)"
            title="Remove from the call"
            @click="removeFromCall(profile.profileName || profile.username)"
          >
            <i class="fa-solid fa-user-minus text-xs"></i>
          </button>
        </div>
      </div>
    </div>

    <div v-else class="py-8 text-center text-base-content/40">
      <i class="fa-solid fa-users mb-2 block text-3xl"></i>
      <p class="text-sm">No participants yet</p>
    </div>

    <div class="space-y-2 rounded-lg border border-info/30 bg-info/10 p-3 text-xs text-info">
      <div class="flex items-start gap-2">
        <i class="fa-solid fa-info-circle mt-0.5 shrink-0"></i>
        <div>
          <p class="mb-1 font-semibold">Share this meeting:</p>
          <p class="text-info/80">Send the code or the invite link from the Invite button.</p>
        </div>
      </div>
    </div>
  </div>
</template>
