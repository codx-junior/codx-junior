<script setup>
import MeetHeader from '@/components/meet/MeetHeader.vue'
import MeetParticipants from '@/components/meet/MeetParticipants.vue'
import MeetInviteModal from '@/components/meet/MeetInviteModal.vue'
import MeetSettingsModal from '@/components/meet/MeetSettingsModal.vue'
import MeetControls from '@/components/meet/MeetControls.vue'
import MicIndicator from '@/components/meet/MicIndicator.vue'
import MeetBrowserPanel from '@/components/meet/MeetBrowserPanel.vue'
import ChatView from '@/views/ChatView.vue'
import VerticalSplitter from '@/components/layout/VerticalSplitter.vue'
import { onBrowserCollab } from '@/utils/browserCollab'
import { createVoiceCapture } from '@/utils/voice/capture'
import { createVoicePlayer } from '@/utils/voice/player'
import { createMeetingRecorder } from '@/utils/meetRecording'
import { isAssistantEcho } from '@/utils/voice/bargeIn'
</script>

<template>
  <div class="h-full overflow-hidden bg-[#1c1e20]">
    <!-- One vertical splitter owns the call layout: the meeting surface on the
         left and the People / Chat / Browser panels on the right. The handle is
         draggable and its last position is remembered. -->
    <VerticalSplitter
      class="h-full"
      show-handle
      persist-key="meet.panels"
      :show-left="true"
      :show-right="!!currentMeet && !panelCollapsed"
      :panels="{
        left: { minSize: 45, defaultSize: 70, class: 'h-full min-w-0' },
        right: { minSize: 18, defaultSize: 30, class: 'h-full min-w-0' }
      }"
    >
      <template #left>
        <div class="flex h-full min-w-0 flex-col">

      <!-- Header (always shown) -->
      <MeetHeader
        :meet="currentMeet"
        :meetId="activeMeetId"
        @close="onClose"
        @settings="showCallSettings = true"
      />

      <!-- Content -->
      <div class="flex-1 flex flex-col min-h-0 overflow-hidden">

        <!-- ─────────────────────────────────────────── -->
        <!-- INCOMING JOIN REQUESTS (knock / admit)      -->
        <!-- ─────────────────────────────────────────── -->
        <div
          v-if="joinRequests.length"
          class="shrink-0 px-4 py-3 bg-warning/10 border-b border-warning/30 space-y-2"
        >
          <div
            v-for="req in joinRequests"
            :key="req.key"
            class="flex items-center gap-3"
          >
            <img
              :src="req.fromAvatar || `https://api.dicebear.com/7.x/initials/svg?seed=${req.from}`"
              :alt="req.from"
              class="w-9 h-9 rounded-full shrink-0"
            />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-white truncate">
                {{ req.fromName }} wants to join
              </div>
              <div class="text-xs text-white/40">Waiting for your approval</div>
            </div>
            <button
              @click="admitJoinRequest(req)"
              class="btn btn-success btn-xs rounded-full gap-1"
              title="Admit"
            >
              <i class="fa-solid fa-check"></i>
              Admit
            </button>
            <button
              @click="denyJoinRequest(req)"
              class="btn btn-ghost btn-xs rounded-full gap-1 text-white/60 hover:text-white"
              title="Deny"
            >
              <i class="fa-solid fa-xmark"></i>
              Deny
            </button>
          </div>
        </div>

        <!-- ─────────────────────────────────────────── -->
        <!-- PRE-JOIN LOBBY                              -->
        <!-- ─────────────────────────────────────────── -->
        <div v-if="!currentMeet" class="flex-1 flex items-center justify-center p-6 overflow-auto">
          <div class="flex flex-col lg:flex-row items-center gap-10 w-full max-w-5xl">

            <!-- Left: Camera preview tile -->
            <div class="flex-1 w-full max-w-xl">
              <div class="relative w-full aspect-video rounded-2xl overflow-hidden bg-[#3c4043] shadow-xl">
                <video
                  v-if="localCameraEnabled && localCameraStream"
                  ref="localVideo"
                  autoplay
                  muted
                  playsinline
                  class="w-full h-full object-cover scale-x-[-1]"
                ></video>
                <div v-else class="absolute inset-0 flex items-center justify-center">
                  <img
                    :src="humanAvatar(currentUserName)"
                    :alt="currentUserName"
                    class="w-24 h-24 rounded-full object-cover ring-2 ring-white/10"
                  />
                </div>
                <div v-if="isStartingCamera" class="absolute inset-0 flex items-center justify-center bg-black/50">
                  <span class="loading loading-spinner loading-md text-primary"></span>
                </div>
                <div class="absolute bottom-3 left-3">
                  <span class="text-white text-sm font-medium drop-shadow-lg bg-black/40 px-2 py-1 rounded-lg backdrop-blur-sm">
                    {{ currentUserName }}
                  </span>
                </div>
                <div v-if="localMuted" class="absolute top-3 right-3">
                  <div class="w-8 h-8 rounded-full bg-red-500 flex items-center justify-center shadow-lg">
                    <i class="fa-solid fa-microphone-slash text-white text-xs"></i>
                  </div>
                </div>
              </div>

              <!-- Camera / Mic toggle bar -->
              <div class="flex items-center justify-center gap-3 mt-4">
                <button
                  @click="toggleLocalMic"
                  class="w-12 h-12 rounded-full flex items-center justify-center transition-all duration-150 active:scale-95 shadow-md"
                  :class="localMuted ? 'bg-red-500 hover:bg-red-600 text-white' : 'bg-[#3c4043] hover:bg-[#4c5055] text-white'"
                  :title="localMuted ? 'Unmute' : 'Mute'"
                >
                  <MicIndicator :enabled="!localMuted" size="lg" tone="inherit" />
                </button>
                <button
                  @click="toggleLocalCamera"
                  class="w-12 h-12 rounded-full flex items-center justify-center transition-all duration-150 active:scale-95 shadow-md"
                  :class="localCameraEnabled ? 'bg-[#3c4043] hover:bg-[#4c5055] text-white' : 'bg-red-500 hover:bg-red-600 text-white'"
                  :title="localCameraEnabled ? 'Turn off camera' : 'Turn on camera'"
                >
                  <i class="fa-solid text-base" :class="localCameraEnabled ? 'fa-video' : 'fa-video-slash'"></i>
                </button>
                <button
                  @click="showCallSettings = true"
                  class="w-12 h-12 rounded-full bg-[#3c4043] hover:bg-[#4c5055] flex items-center justify-center text-white transition-all duration-150 active:scale-95 shadow-md"
                  title="Settings"
                >
                  <i class="fa-solid fa-sliders text-base"></i>
                </button>
              </div>
            </div>

            <!-- Right: Join panel -->
            <div class="flex flex-col items-center gap-5 text-center lg:text-left lg:items-start w-full max-w-sm">
              <div class="space-y-2">
                <h1 class="text-2xl font-bold text-white">Ready to join?</h1>
                <p class="text-sm text-white/50">Start a call or join with a code</p>
              </div>

              <!-- Start / Join actions (side by side when there is room) -->
              <div class="w-full space-y-2">
                <div class="flex flex-wrap items-stretch gap-2">
                  <button
                    @click="startNewMeet"
                    class="btn btn-primary gap-2 rounded-full px-5 flex-1 min-w-[8.5rem] whitespace-nowrap"
                  >
                    <i class="fa-solid fa-phone"></i>
                    Start call
                  </button>

                  <button
                    @click="$ui.openMeetingSetup({ source: 'manual' })"
                    class="btn btn-outline gap-2 rounded-full px-4 flex-1 min-w-[10rem] whitespace-nowrap text-white border-white/20 hover:bg-white/10"
                  >
                    <i class="fa-solid fa-users"></i>
                    Start with participants
                  </button>

                  <div class="flex-1 min-w-[11rem] flex items-center gap-2">
                    <input
                      v-model="joinCode"
                      @keyup.enter="onJoinByCode"
                      type="text"
                      placeholder="Join with a code"
                      class="input input-sm input-bordered flex-1 min-w-0 font-mono lowercase tracking-wider bg-white/5 border-white/10 text-white placeholder:text-white/30"
                    />
                    <button
                      @click="onJoinByCode"
                      :disabled="!joinCode || isJoining"
                      class="btn btn-sm btn-primary rounded-full px-4 shrink-0"
                    >
                      <span v-if="isJoining" class="loading loading-spinner loading-xs"></span>
                      <span v-else>Join</span>
                    </button>
                  </div>
                </div>
                <p v-if="joinError" class="text-xs text-error text-left">{{ joinError }}</p>
              </div>

              <!-- Active meets where you are a participant -->
              <div v-if="joinableMeets.length > 0" class="w-full space-y-2">
                <div class="text-xs font-semibold text-white/30 uppercase tracking-wider text-left">Your active meets</div>
                <button
                  v-for="meet in joinableMeets"
                  :key="meet.meetId"
                  @click="onJoinActiveMeet(meet)"
                  class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                >
                  <div class="min-w-0">
                    <div class="font-medium text-sm text-white truncate">{{ meet.name }}</div>
                    <div class="text-xs text-white/40 font-mono lowercase">{{ formatCode(meet.meetId) }}</div>
                  </div>
                  <i class="fa-solid fa-arrow-right text-white/30 text-xs"></i>
                </button>
              </div>

              <!-- Persisted meetings: minutes and recording stay available -->
              <div v-if="meetingHistory.length > 0" class="w-full space-y-2 pt-1">
                <div class="text-xs font-semibold text-white/30 uppercase tracking-wider text-left">Recent meetings</div>
                <button
                  v-for="meeting in meetingHistory"
                  :key="`history-${meeting.meet_id}`"
                  @click="$ui.openApp({ appKey: 'meet-summary', payload: { meetId: meeting.meet_id } })"
                  class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                >
                  <div class="min-w-0">
                    <div class="font-medium text-sm text-white truncate">{{ meeting.name }}</div>
                    <div class="text-xs text-white/40">
                      {{ formatDate(meeting.ended_at || meeting.created_at) }}
                      <span v-if="meeting.minutes_status === 'ready'"> · minutes</span>
                      <span v-if="meeting.recording_status === 'ready'"> · recording</span>
                    </div>
                  </div>
                  <i class="fa-solid fa-arrow-right text-white/30 text-xs"></i>
                </button>
              </div>

              <!-- Recent -->
              <div v-if="recentMeets.length > 0" class="w-full space-y-2 pt-1">
                <div class="text-xs font-semibold text-white/30 uppercase tracking-wider">Recent</div>
                <button
                  v-for="meet in recentMeets"
                  :key="meet.meetId"
                  @click="joinMeet(meet.meetId)"
                  class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                >
                  <div>
                    <div class="font-medium text-sm text-white">{{ meet.name }}</div>
                    <div class="text-xs text-white/40">{{ formatDate(meet.endedAt) }}</div>
                  </div>
                  <i class="fa-solid fa-arrow-right text-white/30 text-xs"></i>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- ─────────────────────────────────────────── -->
        <!-- ACTIVE CALL VIEW                            -->
        <!-- ─────────────────────────────────────────── -->
        <div v-else class="flex-1 flex flex-col min-h-0">

          <!-- Participants grid area -->
          <div class="flex-1 p-4 overflow-auto">

            <!-- Duration badge -->
            <div class="flex items-center justify-between mb-4">
              <div class="flex items-center gap-2 text-sm">
                <span class="badge badge-success gap-1.5">
                  <span class="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span>
                  Live
                </span>
                <span class="text-white/50 text-xs font-mono">{{ formattedDuration }}</span>
                <span v-if="isRecordingMeeting || recordingStatus === 'recording'" class="badge badge-error badge-sm gap-1.5">
                  <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-white"></span>
                  REC
                </span>
                <span v-else-if="recordingStatus === 'ready'" class="badge badge-success badge-sm gap-1">
                  <i class="fa-solid fa-circle-check"></i>
                  Recorded
                </span>
              </div>
              <button
                @click="showCallSettings = true"
                class="btn btn-ghost btn-xs btn-circle text-white/40 hover:text-white"
              >
                <i class="fa-solid fa-sliders"></i>
              </button>
            </div>

            <!-- Video tiles grid -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">

              <!-- Self camera tile -->
              <div class="relative aspect-video rounded-2xl overflow-hidden bg-[#3c4043]">
                <video
                  v-if="localCameraEnabled && localCameraStream"
                  ref="localVideoInCall"
                  autoplay
                  muted
                  playsinline
                  class="w-full h-full object-cover scale-x-[-1]"
                ></video>
                <div v-else class="absolute inset-0 flex items-center justify-center">
                  <img
                    :src="humanAvatar(currentUserName)"
                    :alt="currentUserName"
                    class="w-20 h-20 rounded-full object-cover ring-2 ring-white/10"
                  />
                </div>
                <div class="absolute bottom-2 left-2">
                  <span class="text-white text-xs bg-black/50 px-2 py-0.5 rounded-md backdrop-blur-sm">
                    {{ currentUserName }} (You)
                  </span>
                </div>
                <div v-if="localMuted" class="absolute top-2 right-2 w-6 h-6 rounded-full bg-red-500 flex items-center justify-center">
                  <i class="fa-solid fa-microphone-slash text-white text-xs"></i>
                </div>
              </div>

              <!-- ─── Screen share tiles (one per active share) ─── -->
              <div
                v-for="share in localScreenStreams"
                :key="share.id"
                class="relative aspect-video rounded-2xl overflow-hidden bg-[#3c4043] ring-2 ring-primary/60"
              >
                <video
                  :ref="el => bindScreenShareVideo(el, share.id)"
                  autoplay
                  muted
                  playsinline
                  class="w-full h-full object-contain bg-black"
                ></video>

                <!-- Label bottom-left -->
                <div class="absolute bottom-2 left-2 flex items-center gap-1.5">
                  <span class="text-white text-xs bg-primary/70 px-2 py-0.5 rounded-md backdrop-blur-sm flex items-center gap-1">
                    <i class="fa-solid fa-share-from-square text-xs"></i>
                    {{ share.label || 'Screen' }}
                  </span>
                </div>

                <!-- Stop button top-right (×) -->
                <div class="absolute top-2 right-2">
                  <button
                    @click="stopScreenShare(share.id)"
                    class="w-7 h-7 rounded-full bg-black/60 hover:bg-red-500 flex items-center justify-center text-white transition-all duration-150 backdrop-blur-sm"
                    title="Stop this share"
                  >
                    <i class="fa-solid fa-xmark text-xs"></i>
                  </button>
                </div>
              </div>

              <!-- Remote participant tiles (one per remote *session*; a second
                   device of the same user gets its own tile) -->
              <div
                v-for="peer in remotePeers"
                :key="peer.id"
                class="relative aspect-video rounded-2xl overflow-hidden bg-[#3c4043] flex items-center justify-center"
                :class="[
                  peer.presenting && 'ring-2 ring-primary/60',
                  meetingFloor.kind === 'user' && meetingFloor.holder === peer.username && !peer.presenting && 'ring-2 ring-success/70'
                ]"
              >
                <video
                  v-if="peer.hasStream"
                  :ref="el => bindRemoteVideo(el, peer.id)"
                  autoplay
                  playsinline
                  class="w-full h-full"
                  :class="peer.presenting ? 'object-contain bg-black' : 'object-cover'"
                ></video>
                <img
                  v-else
                  :src="humanAvatar(peer.username)"
                  :alt="peer.username"
                  class="w-20 h-20 rounded-full object-cover ring-2 ring-white/10"
                />
                <div class="absolute bottom-2 left-2 flex items-center gap-1.5">
                  <span
                    v-if="peer.presenting"
                    class="text-white text-xs bg-primary/70 px-2 py-0.5 rounded-md backdrop-blur-sm flex items-center gap-1"
                  >
                    <i class="fa-solid fa-share-from-square text-xs"></i>
                    {{ peer.presentationLabel || 'Screen' }}
                  </span>
                  <span class="text-white text-xs bg-black/50 px-2 py-0.5 rounded-md backdrop-blur-sm">{{ peer.label }}</span>
                </div>
              </div>

              <!-- AI profiles: camera off, profile avatar. They are virtual
                   participants, never WebRTC peers. -->
              <div
                v-for="profile in profileParticipants"
                :key="`profile-${profile.profileName || profile.username}`"
                class="relative aspect-video rounded-2xl overflow-hidden bg-[#2a2d31] flex items-center justify-center"
                :class="meetingFloor.kind === 'profile' && meetingFloor.holder === (profile.profileName || profile.username)
                  ? 'ring-2 ring-secondary/80'
                  : 'ring-1 ring-white/5'"
              >
                <div class="relative">
                  <img
                    :src="profileAvatar(profile)"
                    :alt="profile.name || profile.username"
                    class="w-20 h-20 rounded-full object-cover ring-2 ring-white/10"
                    :class="meetingFloor.kind === 'profile' && meetingFloor.holder === (profile.profileName || profile.username)
                      ? 'animate-pulse'
                      : ''"
                  />
                  <span class="absolute -bottom-1 -right-1 grid h-6 w-6 place-items-center rounded-full border-2 border-[#2a2d31] bg-secondary text-[10px] font-bold text-secondary-content">AI</span>
                </div>
                <div class="absolute top-2 right-2 flex h-6 w-6 items-center justify-center rounded-full bg-black/50" title="Camera off">
                  <i class="fa-solid fa-video-slash text-[10px] text-white/50"></i>
                </div>
                <div class="absolute bottom-2 left-2 flex items-center gap-1.5">
                  <span class="text-white text-xs bg-black/50 px-2 py-0.5 rounded-md backdrop-blur-sm">{{ profile.name || profile.username }}</span>
                  <span
                    v-if="meetingFloor.kind === 'profile' && meetingFloor.holder === (profile.profileName || profile.username)"
                    class="text-secondary-content text-[10px] bg-secondary/80 px-1.5 py-0.5 rounded-md backdrop-blur-sm"
                  >
                    <i class="fa-solid fa-volume-high mr-1"></i>Speaking
                  </span>
                  <span v-else-if="!meetingFloor.holder" class="text-white/40 text-[10px]">Listening</span>
                </div>
              </div>
            </div>

          </div>

          <!-- Live captions / floor: who is talking, plus the interim
               transcription of this microphone. -->
          <div
            v-if="interimTranscript || isSpeakingProfile || (meetingFloor.holder && meetingFloor.kind === 'profile')"
            class="flex shrink-0 items-center gap-2 border-t border-white/5 bg-[#17191b] px-4 py-2 text-xs text-white/70"
          >
            <i
              class="fa-solid fa-volume-high"
              :class="isSpeakingProfile ? 'animate-pulse text-secondary' : 'text-white/30'"
            ></i>
            <span v-if="interimTranscript" class="min-w-0 flex-1 truncate italic text-white/50">
              “{{ interimTranscript }}”
            </span>
            <span v-else-if="isSpeakingProfile" class="min-w-0 flex-1 truncate">
              <span class="font-semibold text-secondary">{{ meetingFloor.holder }}</span> is speaking…
            </span>
            <span v-if="isRecordingMeeting" class="badge badge-error badge-xs gap-1">
              <span class="h-1 w-1 animate-pulse rounded-full bg-white"></span>
              recording
            </span>
          </div>

          <!-- ─── Call controls bar ─── -->
          <MeetControls
            :is-muted="isMuted"
            :camera-enabled="localCameraEnabled"
            :screen-share-count="localScreenStreams.length"
            :is-requesting-screen="isRequestingScreen"
            :active-panel-tab="activePanelTab"
            :panel-collapsed="panelCollapsed"
            :has-shared-browser="!!sharedBrowserInfo"
            :duration="formattedDuration"
            :is-initiator="isInitiator"
            @toggle-mute="onToggleMute"
            @toggle-camera="toggleLocalCamera"
            @start-screen-share="startScreenShare"
            @settings="showCallSettings = true"
            @invite="showInviteModal = true"
            @chat="openMeetChat"
            @notes="openMeetNotes"
            @people="togglePanel('people')"
            @browser="openMeetBrowser"
            @leave="onLeaveMeet"
            @end="onEndMeet"
          />
        </div>
        </div>
        </div>
      </template>

      <!-- ─────────────────────────────────────────── -->
      <!-- Right Panel: People / Chat / Browser         -->
      <!-- ─────────────────────────────────────────── -->
      <template #right>
        <div class="flex h-full min-w-0 flex-col border-l border-white/5 bg-[#202124]">
        <!-- Tab bar -->
        <div class="flex items-center border-b border-white/5 bg-[#1c1e20]">
          <button
            @click="activePanelTab = 'people'"
            :class="activePanelTab === 'people' ? 'border-b-2 border-primary text-white' : 'border-b-2 border-transparent text-white/40'"
            class="flex-1 btn btn-ghost btn-sm rounded-none gap-2 text-xs font-semibold"
          >
            <i class="fa-solid fa-users"></i>
            People
          </button>
          <button
            @click="activePanelTab = 'chat'"
            :class="activePanelTab === 'chat' ? 'border-b-2 border-primary text-white' : 'border-b-2 border-transparent text-white/40'"
            class="flex-1 btn btn-ghost btn-sm rounded-none gap-2 text-xs font-semibold"
          >
            <i class="fa-solid fa-comments"></i>
            Chat
          </button>
          <button
            @click="activePanelTab = 'notes'"
            :class="activePanelTab === 'notes' ? 'border-b-2 border-primary text-white' : 'border-b-2 border-transparent text-white/40'"
            class="flex-1 btn btn-ghost btn-sm rounded-none gap-2 text-xs font-semibold"
          >
            <i class="fa-solid fa-pen-to-square"></i>
            Notes
            <span v-if="meetingMinutes.status === 'generating'" class="loading loading-spinner loading-xs"></span>
            <span v-else-if="minutesReady" class="badge badge-xs badge-success"></span>
          </button>
          <button
            @click="openMeetBrowser"
            :class="activePanelTab === 'browser' ? 'border-b-2 border-primary text-white' : 'border-b-2 border-transparent text-white/40'"
            class="flex-1 btn btn-ghost btn-sm rounded-none gap-2 text-xs font-semibold"
          >
            <i class="fa-solid fa-window-maximize"></i>
            Browser
            <span v-if="sharedBrowserInfo" class="badge badge-xs badge-primary" title="A participant shared a browser session">
              live
            </span>
          </button>
          <button
            @click="panelCollapsed = true"
            class="btn btn-ghost btn-sm btn-circle text-white/40 hover:text-white mr-1"
            title="Close panel"
          >
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="flex-1 min-h-0 overflow-hidden">
          <MeetParticipants v-if="activePanelTab === 'people'" :meet="currentMeet" />

          <!-- Notes: live transcript + minutes. The minute taker sees the same
               transcript the AI profiles see, and every action item can be
               turned into a task from the summary page. -->
          <div v-if="activePanelTab === 'notes'" class="flex h-full flex-col overflow-hidden">
            <div class="flex shrink-0 items-center gap-2 border-b border-white/5 px-3 py-2">
              <div class="min-w-0 flex-1">
                <div class="text-xs font-semibold text-white">{{ minutesStatusLabel }}</div>
                <div class="truncate text-[11px] text-white/40">{{ currentMeet.name }}</div>
              </div>
              <button
                class="btn btn-ghost btn-xs gap-1 text-white/60 hover:text-white"
                :disabled="notesBusy || meetingMinutes.status === 'generating'"
                title="Generate or refresh the minutes"
                @click="generateMeetingMinutes"
              >
                <span v-if="notesBusy || meetingMinutes.status === 'generating'" class="loading loading-spinner loading-xs"></span>
                <i v-else class="fa-solid fa-wand-magic-sparkles"></i>
                Minutes
              </button>
              <button
                class="btn btn-ghost btn-xs text-white/60 hover:text-white"
                title="Open the meeting summary"
                @click="openMeetingSummary"
              >
                <i class="fa-solid fa-arrow-up-right-from-square"></i>
              </button>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-3 py-3">
              <div v-if="meetingMinutes.status === 'generating'" class="mb-3 rounded-lg border border-primary/30 bg-primary/10 p-3 text-xs text-primary">
                <span class="loading loading-spinner loading-xs mr-1"></span>
                The minute taker is reading the transcript…
              </div>
              <div v-else-if="meetingMinutes.error" class="mb-3 rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
                {{ meetingMinutes.error }}
              </div>

              <section v-if="meetingMinutes.summary" class="mb-3 space-y-1">
                <h4 class="text-[11px] font-bold uppercase tracking-wide text-white/40">Summary</h4>
                <p class="whitespace-pre-line text-xs leading-relaxed text-white/80">{{ meetingMinutes.summary }}</p>
              </section>

              <section v-if="meetingMinutes.decisions?.length" class="mb-3 space-y-1">
                <h4 class="text-[11px] font-bold uppercase tracking-wide text-white/40">Decisions</h4>
                <ul class="space-y-1 text-xs text-white/70">
                  <li v-for="(decision, index) in meetingMinutes.decisions" :key="`decision-${index}`" class="flex gap-1.5">
                    <i class="fa-solid fa-check mt-0.5 text-success"></i>
                    <span>{{ decision }}</span>
                  </li>
                </ul>
              </section>

              <section v-if="meetingMinutes.action_items?.length" class="mb-4 space-y-1">
                <h4 class="text-[11px] font-bold uppercase tracking-wide text-white/40">Action items</h4>
                <div class="space-y-1">
                  <div
                    v-for="item in meetingMinutes.action_items"
                    :key="item.id || item.title"
                    class="rounded-lg border border-white/10 bg-white/5 p-2 text-xs"
                  >
                    <div class="flex items-start gap-1.5">
                      <i class="fa-regular fa-square-check mt-0.5 text-white/30"></i>
                      <span class="min-w-0 flex-1 text-white/80">{{ item.title }}</span>
                      <span v-if="item.priority" class="badge badge-xs" :class="item.priority === 'high' ? 'badge-error' : (item.priority === 'low' ? 'badge-ghost' : 'badge-warning')">
                        {{ item.priority }}
                      </span>
                    </div>
                    <div class="mt-1 flex flex-wrap items-center gap-1 pl-5 text-[10px] text-white/40">
                      <span v-if="item.assignee"><i class="fa-solid fa-user mr-0.5"></i>{{ item.assignee }}</span>
                      <span v-if="item.scheduled_at"><i class="fa-regular fa-clock mr-0.5"></i>{{ item.scheduled_at }}</span>
                      <span v-if="item.task_id" class="text-success">task created</span>
                    </div>
                  </div>
                </div>
              </section>

              <section class="space-y-1">
                <div class="flex items-center justify-between">
                  <h4 class="text-[11px] font-bold uppercase tracking-wide text-white/40">Transcript</h4>
                  <span class="text-[10px] text-white/30">{{ meetingTranscript.length }} lines</span>
                </div>
                <div v-if="!meetingTranscript.length" class="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-white/30">
                  The transcript starts as soon as somebody speaks.
                </div>
                <div v-else class="space-y-1.5">
                  <div
                    v-for="entry in meetingTranscript"
                    :key="entry.entryId || entry.messageId || `${entry.at}-${entry.speaker}`"
                    class="rounded-lg px-2 py-1.5"
                    :class="entry.speakerKind === 'profile' ? 'bg-secondary/10' : 'bg-white/5'"
                  >
                    <div class="flex items-center gap-1.5 text-[10px] text-white/40">
                      <span class="font-semibold" :class="entry.speakerKind === 'profile' ? 'text-secondary' : 'text-white/70'">{{ entry.speaker }}</span>
                      <span>{{ formatTranscriptTime(entry.at) }}</span>
                      <span v-if="entry.interrupted" class="text-warning">· interrupted</span>
                    </div>
                    <p class="mt-0.5 whitespace-pre-line text-xs leading-relaxed text-white/75">{{ entry.text }}</p>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <MeetBrowserPanel
            v-if="activePanelTab === 'browser'"
            :meet-id="currentMeet.meetId"
            :current-user="currentUserName"
            :is-initiator="isInitiator"
            :shared-session="sharedBrowserInfo"
            class="h-full"
          />
          <div v-if="activePanelTab === 'chat'" class="flex flex-col h-full">
            <div v-if="!meetChatData" class="flex-1 flex flex-col items-center justify-center gap-4 p-6 text-center">
              <div class="w-14 h-14 rounded-full bg-white/5 flex items-center justify-center">
                <i class="fa-solid fa-comments text-2xl text-white/30"></i>
              </div>
              <div>
                <p class="text-sm text-white/60 font-medium">No chat for this meet yet</p>
                <p class="text-xs text-white/30 mt-1">Create a chat to collaborate with participants</p>
              </div>
              <button @click="createMeetChat" :disabled="isCreatingChat" class="btn btn-primary btn-sm gap-2">
                <span v-if="isCreatingChat" class="loading loading-spinner loading-xs"></span>
                <i v-else class="fa-solid fa-plus"></i>
                Create Meet Chat
              </button>
            </div>
            <ChatView
              v-else
              :chat="meetChatData"
              :kanban="null"
              :params="{ params: { chat: meetChatData } }"
              class="h-full"
            />
          </div>
        </div>
        </div>
      </template>
    </VerticalSplitter>

    <!-- Modals -->
    <MeetInviteModal
      v-if="showInviteModal"
      :meetId="activeMeetId"
      @close="showInviteModal = false"
    />

    <MeetSettingsModal
      v-if="showCallSettings"
      :currentSettings="callSettings"
      @close="showCallSettings = false"
      @save="onSaveCallSettings"
    />
  </div>
</template>

<script>
import { formatMeetCode } from '@/utils/meetCode'
import { createMicLevelMonitor } from '@/utils/audioLevel'

export default {
  name: 'MeetView',
  props: {
    // Panel params (desktop mode) may carry a meetId to auto-join a deep link.
    params: { type: Object, default: () => ({}) },
    // Embedded in the company shell: closing returns to the company, never to
    // the project workspace.
    embedded: { type: Boolean, default: false }
  },
  data() {
    return {
      activeMeetId: null,
      isMuted: false,
      showInviteModal: false,
      showCallSettings: false,
      callSettings: {},
      activePanelTab: 'people',
      panelCollapsed: true,
      // Last browser session announced by a call participant. Kept here (not
      // only in the browser tab) so the notification arrives even when the tab
      // is closed and is ready when the user opens it.
      sharedBrowserSession: null,
      // Session id already auto-opened, so a re-announcement does not yank the
      // user back to the browser tab every time it is re-sent.
      lastSharedBrowserId: null,
      browserCollabUnsubscribe: null,
      // Join by code
      joinCode: '',
      joinError: null,
      isJoining: false,
      // Local media
      localMuted: false,
      localMicStream: null,
      // Web Audio level monitor for the local mic (reports 0–100 to the store)
      micMonitor: null,
      localCameraEnabled: false,
      localCameraStream: null,
      isStartingCamera: false,
      cameraError: null,
      // Multi-screen share: array of { id, stream, label }
      localScreenStreams: [],
      isRequestingScreen: false,
      // Chat
      isCreatingChat: false,
      isCreatingChatError: null,
      // ── Meeting capture / notes / recording ────────────────────────────
      meetingCapture: null,
      meetingPlayer: null,
      meetingRecorder: null,
      isRecordingMeeting: false,
      recordingBusy: false,
      interimTranscript: '',
      capturedChatId: null,
      speechQueue: [],
      isPlayingProfileSpeech: false,
      currentSpeechText: '',
      humanFloorHeld: false,
      floorEndTimer: null,
      notesBusy: false,
      endBusy: false
    }
  },
  computed: {
    // Meet currently registered in the store (set by the REST lifecycle and by
    // socket events such as an approved join request). Mirroring it makes every
    // entry path open the call room.
    storeCurrentMeetId() {
      return this.$storex.meets.currentMeetId
    },
    currentMeet() {
      return this.$storex.meets.meetForId(this.activeMeetId)
    },
    recentMeets() {
      return [...this.$storex.meets.recentMeetHistory].reverse().slice(0, 5)
    },
    meetingHistory() {
      return (this.$storex.meets.persistedMeetings || []).slice(0, 6)
    },
    // Active meets where the current user is already a participant
    joinableMeets() {
      if (this.currentMeet) return []
      return this.$storex.meets.joinableMeets
    },
    // Incoming knock requests (people asking to join this meet)
    joinRequests() {
      return this.$storex.meets.joinRequestsList
    },
    currentUserName() {
      return this.$storex.users.user?.username || 'You'
    },
    // Whether the current user created this meet (can end it for everyone).
    isInitiator() {
      return !!this.currentMeet && this.currentMeet.initiator === this.currentUserName
    },
    // Remote tiles, one per remote *session*.
    //
    // The store keys remote streams by a device-scoped peer id, so when the same
    // account is connected from two devices (e.g. desktop + phone) each device
    // renders the other as its own tile instead of collapsing them together.
    remotePeers() {
      if (!this.currentMeet) return []
      const me = this.currentUserName
      const peers = []
      const seenUsernames = new Set()
      const presentations = this.$storex.meets.remotePresentationsFor(this.currentMeet.meetId)

      // Peers we are already receiving media from (they carry a device id).
      Object.values(this.currentMeet.remoteStreams || {}).forEach(entry => {
        const username = entry.username || entry.peerId
        const presentation = presentations[entry.peerId]
        peers.push({
          id: entry.peerId,
          username,
          // A same-username peer is unmistakably the user's other device.
          label: username === me ? `${username} (other device)` : username,
          hasStream: true,
          // The screen-share announcement only changes how the tile is
          // rendered: the media itself arrives through the regular video track.
          presenting: !!presentation,
          presentationLabel: presentation?.label || ''
        })
        seenUsernames.add(username)
      })

      // Participants that are connected but not sending media yet (camera off),
      // excluding ourselves. These are keyed by username.
      ;(this.currentMeet.participants || []).forEach(participant => {
        if (participant === me || seenUsernames.has(participant)) return
        peers.push({
          id: participant,
          username: participant,
          label: participant,
          hasStream: false,
          presenting: false,
          presentationLabel: ''
        })
      })

      return peers
    },
    formattedDuration() {
      if (!this.currentMeet) return '00:00'
      const secs = this.currentMeet.duration || 0
      const h = Math.floor(secs / 3600)
      const m = Math.floor((secs % 3600) / 60)
      const s = secs % 60
      if (h > 0) {
        return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      }
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    },
    meetChatData() {
      if (!this.currentMeet || !this.currentMeet.chatId) return null
      return this.$storex.chats.chats?.[this.currentMeet.chatId] || null
    },
    // Browser/computer session shared with this meet.
    //
    // The backend keeps the association on the meet (so late joiners and page
    // reloads get it back); the WebRTC announcement is a live fallback for
    // older backends. Both are normalised to the shape the browser panel and
    // the call controls expect.
    sharedBrowserInfo() {
      const meetId = this.currentMeet?.meetId || this.activeMeetId
      const fromStore = meetId ? this.$storex.meets.sharedBrowserFor(meetId) : null
      const raw = fromStore || this.sharedBrowserSession
      if (!raw?.sessionId) return null
      return {
        ...raw,
        kind: 'browser-session',
        sessionId: raw.sessionId,
        sessionName: raw.sessionName || 'Browser session',
        user: raw.user || {
          username: raw.owner || '',
          name: raw.owner || ''
        }
      }
    },
    // Virtual AI participants: camera-off tiles with their profile avatar.
    profileParticipants() {
      return this.currentMeet?.profileParticipants || []
    },
    meetingMinutes() {
      return this.currentMeet?.minutes || { status: 'none', summary: '', action_items: [] }
    },
    meetingTranscript() {
      return this.currentMeet?.transcript || []
    },
    meetingFloor() {
      return this.currentMeet?.floor || { holder: '', kind: 'user' }
    },
    recordingStatus() {
      return this.currentMeet?.recording?.status || 'none'
    },
    isSpeakingProfile() {
      return this.meetingFloor.kind === 'profile' && !!this.meetingFloor.holder
    },
    profileTurnSeq() {
      return this.$storex.meets.profileTurnSeq
    },
    localMicSpeaking() {
      return this.$storex.meets.isMicSpeaking
    },
    activeProfileTurn() {
      return this.$storex.meets.activeProfileTurn
    },
    canRecordMeeting() {
      return this.isInitiator && this.currentMeet?.settings?.recording_enabled !== false
    },
    notesReady() {
      return this.meetingMinutes.status === 'ready'
    },
    minutesStatusLabel() {
      const status = this.meetingMinutes.status
      if (status === 'generating') return 'Taking minutes…'
      if (status === 'ready') return 'Minutes ready'
      if (status === 'error') return 'Minutes failed'
      return 'No minutes yet'
    },
    // True when at least one screen share is active
    isScreenSharing() {
      return this.localScreenStreams.length > 0
    }
  },
  watch: {
    // Mirror the store's current meet into the view so "Start call", "Join with
    // a code", an approved knock, an accepted invitation and a deep link all
    // open the call room.
    storeCurrentMeetId: {
      handler(meetId) {
        if (meetId && meetId !== this.activeMeetId) {
          this.activeMeetId = meetId
        }
      },
      immediate: true
    },
    currentMeet: {
      handler(newVal, oldVal) {
        if (newVal?.meetId !== oldVal?.meetId) {
          this.sharedBrowserSession = null
          this.lastSharedBrowserId = null
        }
        if (newVal && !this.activeMeetId) {
          this.activeMeetId = newVal.meetId
        }
        // Entering a room (start/join/deep link) must publish the media that is
        // already capturing, not only the streams toggled afterwards.
        if (newVal?.meetId) {
          this.$nextTick(() => this.publishLocalMedia())
          if (newVal.chatId) {
            this.$storex.meets._ensureMeetChat(newVal).catch(() => {})
          }
          if (newVal.status === 'active') {
            this.startMeetingMedia()
          }
        }
      },
      immediate: true
    },
    // The host started a browser session for the room → open the co-browsing
    // panel for everyone in the call.
    sharedBrowserInfo: {
      handler(info) {
        if (!info?.sessionId) {
          this.lastSharedBrowserId = null
          return
        }
        this.onSharedBrowserAvailable(info)
      },
      immediate: true
    },
    'currentMeet.status'(newStatus) {
      if (newStatus === 'active') {
        // Real call: acquire the microphone (unless muted) and publish media,
        // then bind the local preview.
        if (!this.localMuted && !this.localMicStream) {
          this.enableMic()
        }
        // Media started in the pre-join lobby was never published (there was no
        // meet to publish it to), so the peer connection would be created with
        // no tracks and the other participants would only see an avatar even
        // though the camera is on.
        this.publishLocalMedia()
        this.$nextTick(() => this.bindStreamToInCallVideo())
        this.startMeetingMedia()
      }
    },
    localCameraStream(stream) {
      this.$nextTick(() => {
        if (this.$refs.localVideoInCall) {
          this.$refs.localVideoInCall.srcObject = stream || null
        }
        if (this.$refs.localVideo) {
          this.$refs.localVideo.srcObject = stream || null
        }
      })
    },
    // A profile answer arrived: speak it unless a human currently has the
    // floor. The text is already in the meeting chat either way.
    profileTurnSeq() {
      const turn = this.activeProfileTurn
      if (turn) this.onActiveProfileTurn(turn)
    },
    // A human (this client or another participant) took the floor: profiles
    // must stop talking immediately.
    'currentMeet.floor': {
      handler(floor) {
        if (floor?.kind === 'user' && floor?.holder) {
          this.stopProfileSpeech()
        }
      },
      deep: true
    },
    // Detect *this* microphone's speech for barge-in (also used to tell the
    // server, so the other clients stop the profile audio too).
    localMicSpeaking: {
      handler(speaking) {
        this.onLocalSpeechActivity(speaking)
      }
    },
    // Attach/detach remote audio streams to the recorder when peers come/go.
    'currentMeet.remoteStreams': {
      handler(streams) {
        if (!this.meetingRecorder) return
        Object.entries(streams || {}).forEach(([peerId, entry]) => {
          if (entry?.stream) this.meetingRecorder.attachStream(`remote:${peerId}`, entry.stream)
        })
      },
      deep: true
    },
    // Minutes finished (or failed) while the user is still in the meeting.
    'meetingMinutes.status': {
      handler(status, previous) {
        if (status === 'ready' && previous !== 'ready') {
          this.$storex.session.onInfo('The meeting minutes are ready')
        }
        if (status === 'error') {
          this.$storex.session.onWarning('The meeting minutes could not be generated')
        }
      }
    }
  },
  mounted() {
    // Resolve a meetId from a deep link (route param or panel params)
    const meetId = this.$route?.params?.meetId ||
      this.params?.meetId ||
      this.params?.params?.meetId
    if (meetId) {
      this.activeMeetId = meetId
      this.joinMeet(meetId)
    }
    // Discover active meets to (re)join and the project's meeting history
    this.$storex.meets.loadActiveMeets().catch(() => {})
    this.$storex.meets.loadMeetingHistory({ limit: 20 }).catch(() => {})
    try {
      const saved = localStorage.getItem('meetSettings')
      if (saved) {
        this.callSettings = JSON.parse(saved)
        if (this.callSettings.cameraEnabled) {
          this.localCameraEnabled = true
          this.startLocalCamera()
        }
      }
    } catch (e) {
      console.warn('[MeetView] Could not load saved settings')
    }
    this.browserCollabUnsubscribe = onBrowserCollab((event) => this.onBrowserCollabEvent(event))
  },
  beforeUnmount() {
    const meetId = this.activeMeetId
    this.stopMeetingCapture()
    this.stopProfileSpeech()
    this.cancelMeetingRecording()
    this.disableMic()
    this.stopLocalCamera()
    this.stopAllScreenShares()
    if (meetId) {
      // Stop publishing and leave the meet on the backend
      this.$storex.meets.setLocalStream({ meetId, stream: null })
      this.$storex.meets.leaveMeet({ meetId }).catch(() => {})
    }
    if (this.browserCollabUnsubscribe) this.browserCollabUnsubscribe()
  },
  methods: {
    // ─── Formatting ─────────────────────────────────────────────────────────
    formatCode(meetId) {
      return formatMeetCode(meetId)
    },

    // ─── Join requests (knock approval) ─────────────────────────────────────
    admitJoinRequest(req) {
      this.$storex.meets.approveJoinRequest({ meetId: req.meetId, from: req.from })
    },
    denyJoinRequest(req) {
      this.$storex.meets.rejectJoinRequest({ meetId: req.meetId, from: req.from })
    },

    // ─── Join by code ───────────────────────────────────────────────────────
    async onJoinByCode() {
      const code = this.joinCode?.trim()
      if (!code) return
      this.joinError = null
      this.isJoining = true
      try {
        const result = await this.$storex.meets.joinByCode({ code })
        this.joinCode = ''
        // A direct join returns the meet id; a "knock" returns { pending: true }
        // and the room opens once the host approves (via storeCurrentMeetId).
        if (typeof result === 'string' && result) {
          this.activeMeetId = result
        }
      } catch (error) {
        console.error('[MeetView] join by code failed:', error)
        this.joinError = error.message || 'Could not join with that code'
      } finally {
        this.isJoining = false
      }
    },
    async onJoinActiveMeet(meet) {
      if (!meet?.meetId) return
      try {
        await this.$storex.meets.joinMeet({ meetId: meet.meetId })
        this.activeMeetId = meet.meetId
      } catch (error) {
        console.error('[MeetView] join active meet failed:', error)
        this.$storex.session.onError('Failed to join meet')
      }
    },

    // ─── Panel toggle ───────────────────────────────────────────────────────
    togglePanel(tab) {
      if (this.panelCollapsed) {
        this.activePanelTab = tab
        this.panelCollapsed = false
      } else if (this.activePanelTab === tab) {
        this.panelCollapsed = true
      } else {
        this.activePanelTab = tab
      }
    },

    /** Collaboration announcements that arrive over the meet data channel. */
    onBrowserCollabEvent(event) {
      if (!event) return
      const currentId = this.currentMeet?.meetId || this.activeMeetId
      if (currentId && event.meetId && event.meetId !== currentId) return

      // A remote participant started/stopped presenting its screen. The video
      // itself rides the regular track, this only drives the tile rendering.
      if (event.kind === 'screen-share') {
        this.$storex.meets.setRemotePresentation({
          meetId: currentId,
          peerId: event.fromPeerId,
          username: event.user?.username || '',
          label: event.label,
          active: event.active !== false
        })
        return
      }

      if (event.kind !== 'browser-session') return
      this.sharedBrowserSession = event
      const who = event.user?.name || event.user?.username || 'A participant'
      this.$storex.ui.addNotification({
        text: `${who} shared browser session "${event.sessionName || 'session'}" — open the Browser tab to join`,
        type: 'info'
      })
    },

    /**
     * A shared browser session was announced for the room (socket event or the
     * data channel). Open the co-browsing panel so the session is really
     * "started for everyone", not just listed somewhere.
     */
    onSharedBrowserAvailable(info) {
      if (!info?.sessionId || !this.currentMeet) return
      if (this.lastSharedBrowserId === info.sessionId) return
      this.lastSharedBrowserId = info.sessionId
      this.activePanelTab = 'browser'
      this.panelCollapsed = false
      const who = info.user?.name || info.user?.username || info.owner || 'The host'
      // The owner already knows: do not notify them about their own session.
      if ((info.owner || info.user?.username) === this.currentUserName) return
      this.$storex.ui.addNotification({
        text: `${who} started the shared browser "${info.sessionName}" — everyone in the call is in it`,
        type: 'info'
      })
    },

    /**
     * Open the shared-browser panel. The panel is wider than the chat/people
     * panel because a co-browsing session needs room for the page and the
     * annotation toolbar.
     */
    openMeetBrowser() {
      if (this.panelCollapsed) {
        this.activePanelTab = 'browser'
        this.panelCollapsed = false
      } else if (this.activePanelTab === 'browser') {
        this.panelCollapsed = true
        return
      } else {
        this.activePanelTab = 'browser'
      }
    },

    // ─── Meet chat ──────────────────────────────────────────────────────────
    openMeetChat() {
      if (this.meetChatData) {
        this.togglePanel('chat')
      } else {
        this.activePanelTab = 'chat'
        this.panelCollapsed = false
      }
    },
    openMeetNotes() {
      this.togglePanel('notes')
    },
    async createMeetChat() {
      if (!this.currentMeet || this.isCreatingChat) return
      this.isCreatingChat = true
      try {
        // Ask the meeting service for the associated chat: it also stores the
        // link on the meeting and mirrors the meeting metadata into the chat.
        const result = await this.$storex.api.meets.ensureChat({
          meetId: this.currentMeet.meetId,
          name: `Meet – ${this.currentMeet.name || this.activeMeetId}`
        })
        const chatId = result?.chatId || result?.chat?.id
        if (chatId) {
          this.currentMeet.chatId = chatId
          await this.$storex.chats.loadChat({ id: chatId }).catch(() => {})
        }
      } catch (error) {
        console.error('[MeetView] Failed to create meet chat:', error)
        this.$storex.session.onError('Failed to create meet chat')
      } finally {
        this.isCreatingChat = false
      }
    },

    // ─── Meeting capture, profiles and minutes ──────────────────────────────

    /** Avatar of a virtual profile participant. */
    profileAvatar(profile) {
      return profile.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(profile.name || profile.username)}`
    },

    /** Avatar of a human participant (online info when available). */
    humanAvatar(username) {
      if (username === this.currentUserName) {
        return this.$storex.users.user?.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(username)}`
      }
      const online = this.$storex.users.onlineUsers?.[username]
      return online?.avatar || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(username)}`
    },

    formatTranscriptTime(at) {
      if (!at) return ''
      const date = new Date(at)
      if (Number.isNaN(date.getTime())) return ''
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    },

    /** Start microphone transcription and (for the host) recording. */
    startMeetingMedia() {
      if (!this.currentMeet || this.currentMeet.status !== 'active') return
      if (!this.localMuted) this.startMeetingCapture()
      if (this.canRecordMeeting) this.startMeetingRecording()
    },

    startMeetingCapture() {
      if (this.meetingCapture || this.localMuted || !this.currentMeet) return
      const language = this.currentMeet.settings?.language || this.$storex.ui.voiceLanguage || 'en-US'
      try {
        this.meetingCapture = createVoiceCapture({
          language,
          serverTranscribe: async blob => {
            const result = await this.$storex.api.voice.transcribe(blob, language)
            return result?.text || ''
          },
          onFinal: text => this.onMeetingUtterance(text),
          onInterim: text => {
            const value = String(text || '').trim()
            if (!value) {
              this.interimTranscript = ''
              return
            }
            // The profile audio is played through the speakers: ignore the
            // recognizer hearing its own words, otherwise every answer would
            // interrupt itself.
            if (this.isPlayingProfileSpeech && isAssistantEcho(value, this.currentSpeechText)) {
              return
            }
            this.interimTranscript = value
            this.reportHumanSpeaking(true)
          },
          onError: error => {
            console.warn('[meet] speech capture unavailable:', error)
            this.$storex.session.onWarning(
              'Speech recognition is unavailable. You can still type in the meeting chat.'
            )
          },
          onStateChange: state => {
            if (state === 'idle') this.interimTranscript = ''
          }
        })
        this.meetingCapture.start().catch(error => {
          console.warn('[meet] could not start the speech capture', error)
        })
      } catch (error) {
        console.warn('[meet] speech capture setup failed', error)
      }
    },

    stopMeetingCapture() {
      if (!this.meetingCapture) return
      try { this.meetingCapture.stop() } catch (error) { /* ignore */ }
      this.meetingCapture = null
      this.interimTranscript = ''
      if (this.humanFloorHeld) this.reportHumanSpeaking(false)
    },

    async onMeetingUtterance(text) {
      const value = String(text || '').trim()
      this.interimTranscript = ''
      if (!value || !this.currentMeet) return
      if (this.isPlayingProfileSpeech && isAssistantEcho(value, this.currentSpeechText)) {
        return
      }
      try {
        await this.$storex.meets.sendUtterance({
          meetId: this.currentMeet.meetId,
          text: value,
          source: 'speech'
        })
      } catch (error) {
        console.error('[meet] could not record the utterance', error)
        this.$storex.session.onError('Could not record what you said')
      }
    },

    /**
     * Tell the room that this microphone is picking up speech. The server
     * interrupts the profile that is talking, so a profile never talks over a
     * person. Debounced so normal pauses do not flap the floor.
     */
    reportHumanSpeaking(active) {
      if (!this.currentMeet) return
      if (this.floorEndTimer) {
        clearTimeout(this.floorEndTimer)
        this.floorEndTimer = null
      }
      if (active) {
        if (this.humanFloorHeld) return
        this.humanFloorHeld = true
        this.stopProfileSpeech()
        this.$storex.meets.reportFloor({
          meetId: this.currentMeet.meetId,
          event: 'start',
          speaker: this.currentUserName
        }).catch(() => {})
      } else {
        this.floorEndTimer = setTimeout(() => {
          this.floorEndTimer = null
          if (!this.humanFloorHeld || !this.currentMeet) return
          this.humanFloorHeld = false
          this.$storex.meets.reportFloor({
            meetId: this.currentMeet.meetId,
            event: 'end',
            speaker: this.currentUserName
          }).catch(() => {})
        }, 650)
      }
    },

    onLocalSpeechActivity(speaking) {
      if (!this.currentMeet || this.currentMeet.status !== 'active') return
      // While a profile is speaking the microphone picks up its own output.
      // Barge-in is then driven by the speech recognizer text (which filters
      // the assistant echo), not by the raw level.
      if (this.isPlayingProfileSpeech) return
      this.reportHumanSpeaking(!!speaking)
    },

    /** A profile answer arrived; queue it for speech. */
    onActiveProfileTurn(turn) {
      if (!this.currentMeet || !turn) return
      if (turn.interrupted || !turn.speech) return
      if (this.localMicSpeaking || this.humanFloorHeld) return
      if (this.speechQueue.some(item => item.turnId === turn.turnId)) return
      this.speechQueue.push(turn)
      this.playNextProfileSpeech()
    },

    async playNextProfileSpeech() {
      if (this.isPlayingProfileSpeech || !this.speechQueue.length || !this.currentMeet) return
      const turn = this.speechQueue.shift()
      if (!this.meetingPlayer) {
        this.meetingPlayer = createVoicePlayer()
      }
      this.isPlayingProfileSpeech = true
      this.currentSpeechText = turn.speech || ''
      const voice = turn.voice?.voice || {}
      const language = voice.language || this.currentMeet.settings?.language || 'en-US'
      const rate = Number(voice.speed) > 0 ? Number(voice.speed) : 1
      const meetId = this.currentMeet.meetId
      try {
        await this.meetingPlayer.speak(turn.speech, {
          language,
          rate,
          serverAudio: async () => {
            return this.$storex.api.meets.speechBlob({
              meetId,
              messageId: turn.messageId,
              profile: turn.profile
            })
          },
          onAudioElement: element => {
            this.meetingRecorder?.attachAudioElement(`tts:${turn.turnId}`, element)
          }
        })
      } catch (error) {
        console.warn('[meet] could not speak the profile answer', error)
      } finally {
        if (this.meetingRecorder) this.meetingRecorder.detachAudioElement(`tts:${turn.turnId}`)
        this.isPlayingProfileSpeech = false
        this.currentSpeechText = ''
        if (!this.humanFloorHeld && !this.localMicSpeaking) this.playNextProfileSpeech()
      }
    },

    stopProfileSpeech() {
      this.speechQueue = []
      this.isPlayingProfileSpeech = false
      this.currentSpeechText = ''
      if (this.meetingPlayer) this.meetingPlayer.stop()
    },

    async startMeetingRecording() {
      if (!this.currentMeet || !this.canRecordMeeting || this.meetingRecorder) return
      const recorder = createMeetingRecorder()
      if (!recorder.supported()) return
      const started = await recorder.start()
      if (!started) return
      this.meetingRecorder = recorder
      this.isRecordingMeeting = true
      if (this.localMicStream) recorder.attachStream('local', this.localMicStream)
      Object.entries(this.currentMeet.remoteStreams || {}).forEach(([peerId, entry]) => {
        if (entry?.stream) recorder.attachStream(`remote:${peerId}`, entry.stream)
      })
      try {
        await this.$storex.api.meets.startRecording({ meetId: this.currentMeet.meetId })
      } catch (error) {
        // The recording still works locally; the server only misses the
        // "recording started" timestamp.
        console.warn('[meet] could not mark the recording as started', error)
      }
    },

    async finalizeMeetingRecording() {
      if (!this.meetingRecorder || this.recordingBusy) return null
      const meetId = this.currentMeet?.meetId
      this.recordingBusy = true
      const recorder = this.meetingRecorder
      this.meetingRecorder = null
      this.isRecordingMeeting = false
      try {
        const result = await recorder.stop()
        if (result?.blob && meetId) {
          await this.$storex.api.meets.uploadRecording({
            meetId,
            blob: result.blob,
            duration: result.duration,
            mime: result.mime
          })
          this.$storex.session.onInfo('The meeting recording was saved')
        }
        return result
      } catch (error) {
        console.error('[meet] could not save the recording', error)
        this.$storex.session.onWarning('The meeting recording could not be saved')
        return null
      } finally {
        this.recordingBusy = false
      }
    },

    cancelMeetingRecording() {
      if (!this.meetingRecorder) return
      try { this.meetingRecorder.cancel() } catch (error) { /* ignore */ }
      this.meetingRecorder = null
      this.isRecordingMeeting = false
    },

    async generateMeetingMinutes() {
      if (!this.currentMeet || this.notesBusy) return
      this.notesBusy = true
      try {
        await this.$storex.meets.generateMeetNotes({
          meetId: this.currentMeet.meetId,
          force: true
        })
        this.$storex.session.onInfo('Generating the meeting minutes…')
      } catch (error) {
        console.error('[meet] could not generate the minutes', error)
        this.$storex.session.onError('Could not generate the meeting minutes')
      } finally {
        this.notesBusy = false
      }
    },

    openMeetingSummary() {
      const meetId = this.currentMeet?.meetId || this.activeMeetId
      if (!meetId) return
      if (this.embedded) {
        this.$emit('close')
        return
      }
      this.$storex.ui.openApp({ appKey: 'meet-summary', payload: { meetId } })
    },

    // ─── Local media publishing ─────────────────────────────────────────────
    /**
     * Merge the active mic + camera tracks and publish them to the store so
     * they are sent to every connected peer.
     */
    publishLocalMedia() {
      const meetId = this.activeMeetId || this.$storex.meets.currentMeetId
      if (!meetId || !this.$storex.meets.meetForId(meetId)) return
      const tracks = []
      if (this.localMicStream) tracks.push(...this.localMicStream.getAudioTracks())
      if (this.localCameraStream) tracks.push(...this.localCameraStream.getVideoTracks())
      const stream = tracks.length ? new MediaStream(tracks) : null
      this.$storex.meets.setLocalStream({ meetId, stream })
    },

    // ─── Microphone ─────────────────────────────────────────────────────────
    async enableMic() {
      if (this.localMicStream) return
      try {
        const deviceId = this.callSettings.selectedMicrophone
        const audio = (deviceId && deviceId !== 'default')
          ? {
              deviceId: { exact: deviceId },
              echoCancellation: this.callSettings.echoCancellation !== false,
              noiseSuppression: this.callSettings.noiseSuppression !== false,
              autoGainControl: this.callSettings.autoGainControl !== false
            }
          : true
        this.localMicStream = await navigator.mediaDevices.getUserMedia({ audio, video: false })
        // Create the level monitor once and (re)bind it to the current stream so
        // the global "speaking" state tracks whatever mic is capturing.
        if (!this.micMonitor) {
          this.micMonitor = createMicLevelMonitor({
            onLevel: (level) => this.$storex.meets.setMicLevel(level)
          })
        }
        this.micMonitor.start(this.localMicStream)
        // Reflect the mic state globally so the UI (Meet panel tab, call
        // controls, participant list) shows it — even in the pre-join lobby.
        this.$storex.meets.setLocalMicEnabled(true)
        if (this.meetingRecorder) this.meetingRecorder.attachStream('local', this.localMicStream)
        this.publishLocalMedia()
      } catch (error) {
        console.error('[MeetView] Microphone error:', error)
        this.$storex.session.onWarning('Microphone unavailable')
      }
    },
    disableMic() {
      // Stop the level monitor first so it reports a final 0 level.
      if (this.micMonitor) {
        this.micMonitor.stop()
      }
      if (this.localMicStream) {
        if (this.meetingRecorder) this.meetingRecorder.detachStream('local')
        this.localMicStream.getTracks().forEach(t => t.stop())
        this.localMicStream = null
        this.publishLocalMedia()
      }
      this.$storex.meets.setLocalMicEnabled(false)
    },
    async toggleLocalMic() {
      this.localMuted = !this.localMuted
      this.isMuted = this.localMuted
      if (this.localMuted) {
        this.stopMeetingCapture()
        this.disableMic()
      } else {
        await this.enableMic()
        this.startMeetingCapture()
      }
    },

    // ─── Local camera ───────────────────────────────────────────────────────
    async toggleLocalCamera() {
      if (this.localCameraEnabled) {
        this.stopLocalCamera()
        this.localCameraEnabled = false
      } else {
        this.localCameraEnabled = true
        await this.startLocalCamera()
      }
    },
    async startLocalCamera() {
      this.isStartingCamera = true
      this.cameraError = null
      try {
        const deviceId = this.callSettings.selectedCamera
        const constraints = {
          video: deviceId ? { deviceId: { exact: deviceId } } : true,
          audio: false
        }
        const stream = await navigator.mediaDevices.getUserMedia(constraints)
        this.localCameraStream = stream
        // Reflect the camera state globally so the UI (e.g. the Meet panel tab
        // icon) shows it even in the pre-join lobby with no active meet.
        this.$storex.meets.setLocalCameraEnabled(true)
        this.publishLocalMedia()
        this.$nextTick(() => {
          if (this.$refs.localVideo) this.$refs.localVideo.srcObject = stream
          if (this.$refs.localVideoInCall) this.$refs.localVideoInCall.srcObject = stream
        })
      } catch (error) {
        console.error('[MeetView] Camera error:', error)
        this.cameraError = error.message
        this.localCameraEnabled = false
        this.localCameraStream = null
        this.$storex.meets.setLocalCameraEnabled(false)
      } finally {
        this.isStartingCamera = false
      }
    },
    stopLocalCamera() {
      if (this.localCameraStream) {
        this.localCameraStream.getTracks().forEach(t => t.stop())
        this.localCameraStream = null
        this.publishLocalMedia()
      }
      // Keep the global camera flag in sync when the camera is turned off.
      this.$storex.meets.setLocalCameraEnabled(false)
    },
    bindStreamToInCallVideo() {
      if (this.$refs.localVideoInCall && this.localCameraStream) {
        this.$refs.localVideoInCall.srcObject = this.localCameraStream
      }
    },

    // ─── Remote media ───────────────────────────────────────────────────────
    // Streams are keyed by the device-scoped peer id (see store/meets.js), not
    // by username, so two devices of the same account stay independent.
    hasRemoteStream(peerId) {
      return !!this.currentMeet?.remoteStreams?.[peerId]?.stream
    },
    bindRemoteVideo(el, peerId) {
      if (!el) return
      const stream = this.currentMeet?.remoteStreams?.[peerId]?.stream
      if (stream && el.srcObject !== stream) {
        el.srcObject = stream
      }
    },

    // ─── Multi-screen share ─────────────────────────────────────────────────

    /**
     * Bind a screen-share stream to its dynamically-created <video> element.
     * Called via :ref="el => bindScreenShareVideo(el, share.id)"
     */
    bindScreenShareVideo(el, shareId) {
      if (!el) return
      const share = this.localScreenStreams.find(s => s.id === shareId)
      if (share && el.srcObject !== share.stream) {
        el.srcObject = share.stream
      }
    },

    /**
     * Open the browser's screen/tab/window picker and add a new share tile.
     * Can be called multiple times — each call creates an independent tile.
     */
    async startScreenShare() {
      if (!navigator.mediaDevices?.getDisplayMedia) {
        this.$storex.session.onError('Screen sharing is not supported in this browser')
        return
      }
      this.isRequestingScreen = true
      try {
        const stream = await navigator.mediaDevices.getDisplayMedia({
          video: { cursor: 'always' },
          audio: false,
          // Allow user to choose: current tab, any tab, or window
          selfBrowserSurface: "include"
        })

        // Derive a human-readable label from the track
        const videoTrack = stream.getVideoTracks()[0]
        const rawLabel = videoTrack?.label || ''
        const label = this.prettifyShareLabel(rawLabel)

        const shareId = `share_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`

        // Listen for the user stopping the share via the browser's native UI
        videoTrack?.addEventListener('ended', () => {
          this.stopScreenShare(shareId)
        })

        this.localScreenStreams = [
          ...this.localScreenStreams,
          { id: shareId, stream, label }
        ]
        // Publish the newest share to every peer: the display track replaces the
        // camera track on the wire, so the other participants really see it.
        this.presentScreenShare(shareId, stream, label)
      } catch (error) {
        // NotAllowedError = user cancelled the picker — not an error worth toasting
        if (error.name !== 'NotAllowedError') {
          console.error('[MeetView] Screen share error:', error)
          this.$storex.session.onError('Failed to start screen share: ' + error.message)
        }
      } finally {
        this.isRequestingScreen = false
      }
    },

    /** Forward a screen share to the call (no-op outside an active meet). */
    presentScreenShare(shareId, stream, label) {
      if (!this.activeMeetId || !stream) return
      this.$storex.meets.presentScreenShare({
        meetId: this.activeMeetId,
        shareId,
        stream,
        label
      })
    },

    /**
     * Stop a specific screen share by its id.
     */
    stopScreenShare(shareId) {
      const share = this.localScreenStreams.find(s => s.id === shareId)
      if (share) {
        share.stream.getTracks().forEach(t => t.stop())
        this.localScreenStreams = this.localScreenStreams.filter(s => s.id !== shareId)
      }
      // Fall back to the next active share, or to the camera when none is left.
      const remaining = this.localScreenStreams
      if (this.activeMeetId) {
        if (remaining.length) {
          const next = remaining[remaining.length - 1]
          this.presentScreenShare(next.id, next.stream, next.label)
        } else {
          this.$storex.meets.stopPresentation({ meetId: this.activeMeetId })
        }
      }
    },

    /**
     * Stop ALL active screen shares (used on leave/unmount).
     */
    stopAllScreenShares() {
      this.localScreenStreams.forEach(share => {
        share.stream.getTracks().forEach(t => t.stop())
      })
      this.localScreenStreams = []
      if (this.activeMeetId) {
        this.$storex.meets.stopPresentation({ meetId: this.activeMeetId })
      }
    },

    /**
     * Convert a raw MediaStreamTrack label into something readable.
     * E.g. "screen:0:0" → "Screen", "tab:chrome-extension://..." → "Tab"
     */
    prettifyShareLabel(raw) {
      if (!raw) return `Screen ${this.localScreenStreams.length + 1}`
      if (raw.toLowerCase().includes('tab')) return 'Tab'
      if (raw.toLowerCase().includes('window')) return 'Window'
      if (raw.toLowerCase().includes('screen') || raw.toLowerCase().includes('display')) return 'Screen'
      // Use the track label up to 20 chars as a fallback
      return raw.length > 20 ? raw.slice(0, 20) + '…' : raw
    },

    // ─── Meet actions ───────────────────────────────────────────────────────
    formatDate(timestamp) {
      if (!timestamp) return ''
      return new Date(timestamp).toLocaleDateString()
    },
    async startNewMeet() {
      // The store generates a shareable code when no meetId is supplied.
      try {
        const meetId = await this.$storex.meets.initiateMeet({
          name: `Meet ${new Date().toLocaleTimeString()}`
        })
        if (meetId) this.activeMeetId = meetId
        // Refresh so this meet appears in the discovery list for invites.
        this.$storex.meets.loadActiveMeets().catch(() => {})
      } catch (error) {
        console.error('Failed to start meet:', error)
        this.$storex.session.onError('Failed to start meet')
      }
    },
    async joinMeet(meetId) {
      this.activeMeetId = meetId
      try {
        await this.$storex.meets.joinMeet({ meetId })
      } catch (error) {
        console.error('Failed to join meet:', error)
        this.$storex.session.onError('Failed to join meet')
      }
    },
    async onToggleMute() {
      await this.toggleLocalMic()
    },
    async onLeaveMeet() {
      if (!this.activeMeetId) return
      const meetId = this.activeMeetId
      this.stopMeetingCapture()
      this.stopProfileSpeech()
      await this.finalizeMeetingRecording()
      this.disableMic()
      this.stopLocalCamera()
      this.stopAllScreenShares()
      try {
        await this.$storex.meets.leaveMeet({ meetId })
      } catch (error) {
        console.error('Failed to leave meet:', error)
      }
      this.activeMeetId = null
      this.panelCollapsed = true
    },
    async onEndMeet() {
      if (!this.activeMeetId || this.endBusy) return
      this.endBusy = true
      const meetId = this.activeMeetId
      this.stopMeetingCapture()
      this.stopProfileSpeech()
      // Save the recording BEFORE ending: the meeting document must exist when
      // the file is uploaded (and the summary page links it).
      await this.finalizeMeetingRecording()
      this.disableMic()
      this.stopLocalCamera()
      this.stopAllScreenShares()
      try {
        await this.$storex.meets.endMeet(meetId)
      } catch (error) {
        console.error('Failed to end meet:', error)
        this.$storex.session.onError('Could not end the meeting')
      } finally {
        this.activeMeetId = null
        this.panelCollapsed = true
        this.endBusy = false
      }
      if (this.embedded) {
        this.$emit('close')
      } else {
        this.$storex.ui.openApp({ appKey: 'meet-summary', payload: { meetId } })
      }
    },
    async onSaveCallSettings(settings) {
      this.callSettings = { ...settings }
      if (settings.cameraEnabled !== this.localCameraEnabled) {
        this.localCameraEnabled = settings.cameraEnabled
        if (settings.cameraEnabled) {
          await this.startLocalCamera()
        } else {
          this.stopLocalCamera()
        }
      }
      // Re-acquire the microphone so the new device/options apply
      if (!this.localMuted) {
        this.disableMic()
        await this.enableMic()
      }
      this.$storex.session.onInfo('Settings saved')
    },
    async onClose() {
      await this.onLeaveMeet()
      if (this.embedded) {
        this.$emit('close')
        return
      }
      this.$router.push({ name: 'home' })
    },
  }
}
</script>
