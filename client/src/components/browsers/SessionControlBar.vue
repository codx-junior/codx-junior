<script>
import {
  CONTROL_MODE_OPTIONS,
  canResolveControlRequest,
  controlActionFor,
  controlSummary,
  hasPendingControlRequest,
  isControlOwner,
  participantInitials,
  participantLabel,
  sessionParticipants,
  viewerIdentity
} from '@/utils/sessionControl'

/**
 * Presence + control bar shared by the browser and desktop viewers.
 *
 * It renders who is connected, who drives, the sharing mode and the one action
 * that makes sense for this viewer (take / request / wait / give back).  When a
 * request is pending, the current owner gets the approve/deny buttons here.
 *
 * The component is presentational: every button emits an event the viewer
 * forwards to the browsers store, which talks to the API.
 */
export default {
  name: 'SessionControlBar',
  props: {
    session: { type: Object, default: null },
    currentUser: { type: Object, default: null },
    participantId: { type: String, default: '' },
    busy: { type: Boolean, default: false },
    compact: { type: Boolean, default: false }
  },
  emits: [
    'take-control',
    'request-control',
    'release-control',
    'approve-request',
    'deny-request',
    'set-mode'
  ],
  computed: {
    identity() {
      return viewerIdentity(this.currentUser, this.participantId)
    },
    participants() {
      return sessionParticipants(this.session)
    },
    visibleParticipants() {
      return this.participants.slice(0, 4)
    },
    extraParticipants() {
      return Math.max(0, this.participants.length - this.visibleParticipants.length)
    },
    summary() {
      return controlSummary(this.session, this.identity)
    },
    primaryAction() {
      return controlActionFor(this.session, this.identity)
    },
    pendingRequest() {
      return this.session?.control_request || null
    },
    pendingForMe() {
      return hasPendingControlRequest(this.session, this.identity)
    },
    canResolve() {
      return canResolveControlRequest(this.session, this.identity)
    },
    owner() {
      return isControlOwner(this.session, this.identity)
    },
    controlModeOptions() {
      return CONTROL_MODE_OPTIONS
    },
    controlMode() {
      return this.session?.control_mode === 'request' ? 'request' : 'auto'
    },
    /** Only the owner or an admin changes the mode while a human drives. */
    canChangeMode() {
      if (!this.session) return false
      if (this.identity.isAdmin) return true
      if (this.session.controller !== 'user' || !this.session.control_owner) return true
      return this.owner
    },
    summaryClass() {
      return {
        owner: 'badge-success',
        other: 'badge-warning',
        ai: 'badge-info',
        muted: 'badge-ghost'
      }[this.summary.tone] || 'badge-ghost'
    },
    pendingLabel() {
      const name = this.pendingRequest?.name || this.pendingRequest?.username || 'A participant'
      return `${name} requests control`
    }
  },
  methods: {
    participantLabel,
    participantInitials,
    participantTitle(participant) {
      const name = participantLabel(participant)
      const flags = []
      if (this.session?.control_owner === participant.username ||
          this.session?.control_owner === participant.id) {
        flags.push('driving')
      }
      if (participant.username && participant.username === this.identity.username) {
        flags.push('you')
      }
      return flags.length ? `${name} (${flags.join(', ')})` : name
    },
    primary() {
      const action = this.primaryAction.action
      if (this.busy || !action) return
      if (action === 'release') this.$emit('release-control')
      else if (action === 'request_control') this.$emit('request-control')
      else this.$emit('take-control')
    },
    changeMode(event) {
      const mode = event.target.value
      if (mode !== this.controlMode) this.$emit('set-mode', mode)
    },
    approve() {
      this.$emit('approve-request', this.pendingRequest?.id || null)
    },
    deny() {
      this.$emit('deny-request', this.pendingRequest?.id || null)
    }
  }
}
</script>

<template>
  <div v-if="session" class="flex flex-wrap items-center gap-1.5" data-testid="session-control-bar">
    <!-- Presence: one bubble per connected human (max 4 + overflow). -->
    <div v-if="participants.length" class="flex items-center -space-x-1.5" :title="participants.map(participantLabel).join(', ')">
      <span
        v-for="participant in visibleParticipants"
        :key="participant.id"
        class="inline-flex h-6 w-6 items-center justify-center rounded-full border-2 border-base-100 bg-primary/15 text-[10px] font-bold text-primary"
        :title="participantTitle(participant)"
      >
        {{ participantInitials(participant) }}
      </span>
      <span
        v-if="extraParticipants"
        class="inline-flex h-6 w-6 items-center justify-center rounded-full border-2 border-base-100 bg-base-200 text-[10px] font-semibold"
        :title="`${extraParticipants} more participant(s)`"
      >+{{ extraParticipants }}</span>
    </div>

    <span class="badge badge-xs gap-1" :class="summaryClass" :title="summary.label">
      <i :class="owner ? 'fa-solid fa-hand' : (summary.tone === 'ai' ? 'fa-solid fa-robot' : 'fa-solid fa-user')"></i>
      {{ summary.label }}
    </span>

    <select
      v-if="!compact"
      class="select select-xs select-bordered"
      :value="controlMode"
      :disabled="busy || !canChangeMode"
      title="How humans share control"
      aria-label="Control sharing mode"
      @change="changeMode"
    >
      <option v-for="option in controlModeOptions" :key="option.id" :value="option.id" :title="option.description">
        {{ option.label }}
      </option>
    </select>

    <button
      v-if="primaryAction.action || primaryAction.disabled"
      class="btn btn-xs"
      :class="owner ? 'btn-outline' : (primaryAction.action === 'request_control' ? 'btn-warning btn-outline' : 'btn-primary')"
      :disabled="busy || primaryAction.disabled"
      :title="primaryAction.hint"
      @click="primary"
    >
      <span v-if="busy" class="loading loading-spinner loading-xs"></span>
      <i v-else :class="owner ? 'fa-solid fa-robot' : (primaryAction.action === 'request_control' ? 'fa-solid fa-paper-plane' : 'fa-solid fa-hand')"></i>
      <span class="hidden sm:inline">{{ primaryAction.label }}</span>
    </button>

    <!-- The owner resolves a pending request without leaving the screen. -->
    <div
      v-if="pendingRequest && canResolve"
      class="flex items-center gap-1 rounded-lg border border-warning/40 bg-warning/10 px-2 py-1 text-[11px]"
      role="alert"
    >
      <i class="fa-solid fa-hand text-warning"></i>
      <span class="max-w-[12rem] truncate">{{ pendingLabel }}</span>
      <button class="btn btn-xs btn-success" :disabled="busy" @click="approve">Approve</button>
      <button class="btn btn-xs btn-ghost" :disabled="busy" @click="deny">Deny</button>
    </div>

    <div
      v-else-if="pendingForMe"
      class="rounded-lg border border-info/40 bg-info/10 px-2 py-1 text-[11px] text-info"
      role="status"
    >
      <i class="fa-solid fa-hourglass-half"></i> Waiting for approval…
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
