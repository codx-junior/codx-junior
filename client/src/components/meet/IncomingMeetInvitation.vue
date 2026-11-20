<script setup>
import { computed, ref } from 'vue'
import { $storex } from '@/store'

const joining = ref('')
const invitations = computed(() => $storex.meets.allIncomingInvitations || [])

async function join(invitation) {
  if (!invitation?.meetId || joining.value) return
  joining.value = invitation.meetId
  try {
    await $storex.meets.acceptInvitation({ meetId: invitation.meetId })
    $storex.ui.openMeet(invitation.meetId)
  } catch (error) {
    console.error('[meeting] could not join the invited meeting', error)
    $storex.session.onError('Could not join the meeting')
  } finally {
    joining.value = ''
  }
}

function dismiss(invitation) {
  $storex.meets.rejectInvitation(invitation.meetId)
}
</script>

<template>
  <div class="pointer-events-none fixed bottom-4 right-4 z-40 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
    <div
      v-for="invitation in invitations"
      :key="invitation.meetId"
      class="pointer-events-auto overflow-hidden rounded-xl border border-primary/30 bg-base-100 shadow-2xl"
    >
      <div class="flex items-center gap-2 bg-primary/10 px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-primary">
        <span class="relative flex h-2 w-2">
          <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-70"></span>
          <span class="relative inline-flex h-2 w-2 rounded-full bg-primary"></span>
        </span>
        Incoming meeting
        <button class="ml-auto btn btn-ghost btn-xs btn-circle text-base-content/50" @click="dismiss(invitation)">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="space-y-2 p-3">
        <div class="flex items-start gap-2">
          <img
            :src="invitation.fromAvatar || `https://api.dicebear.com/7.x/initials/svg?seed=${invitation.from}`"
            class="h-9 w-9 rounded-full shrink-0"
            alt=""
          />
          <div class="min-w-0">
            <p class="truncate text-sm font-semibold">{{ invitation.name || 'Team meeting' }}</p>
            <p class="text-xs text-base-content/50">
              {{ invitation.fromName || invitation.from }} invited you
              <span v-if="invitation.channelName"> · #{{ invitation.channelName }}</span>
            </p>
          </div>
        </div>
        <p v-if="invitation.agenda" class="line-clamp-2 text-xs text-base-content/60">{{ invitation.agenda }}</p>
        <div class="flex flex-wrap items-center gap-1 text-[11px] text-base-content/50">
          <span v-if="invitation.participants?.length" class="badge badge-ghost badge-xs gap-1">
            <i class="fa-solid fa-users"></i> {{ invitation.participants.length }}
          </span>
          <span v-if="invitation.profiles?.length" class="badge badge-ghost badge-xs gap-1">
            <i class="fa-solid fa-robot"></i> {{ invitation.profiles.length }} AI
          </span>
          <span v-if="invitation.source === 'channel'" class="badge badge-info badge-xs">channel</span>
        </div>
        <div class="flex gap-2 pt-1">
          <button class="btn btn-ghost btn-xs flex-1" @click="dismiss(invitation)">Dismiss</button>
          <button class="btn btn-primary btn-xs flex-1 gap-1" :disabled="!!joining" @click="join(invitation)">
            <span v-if="joining === invitation.meetId" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-phone"></i>
            Join
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
