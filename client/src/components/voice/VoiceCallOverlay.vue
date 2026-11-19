<template>
  <Teleport to="body">
    <!-- ── Floating pill while the call is minimized ─────────────────── -->
    <div
      v-if="$voice.minimized && $voice.session?.status === 'active'"
      class="fixed bottom-4 right-4 z-[80] flex items-center gap-3 rounded-full border border-success/30 bg-base-100/95 px-3 py-2 shadow-2xl backdrop-blur"
    >
      <button
        class="relative flex h-10 w-10 items-center justify-center rounded-full bg-success/15 text-success"
        title="Open voice call"
        @click="$voice.restore()"
      >
        <i class="fa-solid fa-phone"></i>
        <span
          class="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-base-100"
          :class="$voice.status === 'error' ? 'bg-error' : 'bg-success animate-pulse'"
        ></span>
      </button>
      <div class="min-w-0 pr-1">
        <div class="truncate text-xs font-semibold">{{ callTitle }}</div>
        <div class="flex items-center gap-2 text-[11px] text-base-content/60">
          <span class="truncate">{{ $voice.statusLabel }}</span>
          <span class="tabular-nums">{{ elapsedLabel }}</span>
        </div>
      </div>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        :class="$voice.muted ? 'text-error' : 'text-success'"
        :title="$voice.muted ? 'Unmute microphone' : 'Mute microphone'"
        @click="$voice.toggleMute()"
      >
        <i :class="$voice.muted ? 'fa-solid fa-microphone-slash' : 'fa-solid fa-microphone'"></i>
      </button>
      <button
        class="btn btn-error btn-xs btn-circle"
        title="End call"
        @click="$voice.endCall({ reason: 'ended from minimized call', announce: true })"
      >
        <i class="fa-solid fa-phone-slash"></i>
      </button>
    </div>

    <!-- ── Main panel ────────────────────────────────────────────────── -->
    <div
      v-if="$voice.open"
      class="fixed inset-0 z-[90] flex items-end justify-center bg-base-300/70 p-0 backdrop-blur-sm sm:items-center sm:p-6"
      @click.self="$voice.close()"
    >
      <section
        class="flex max-h-full w-full flex-col overflow-hidden rounded-t-3xl border border-base-content/10 bg-base-100 shadow-2xl sm:max-w-2xl sm:rounded-3xl"
        role="dialog"
        aria-modal="true"
        :aria-label="isCallRunning ? 'Voice conversation' : 'Voice conversation setup'"
      >
        <!-- ═════════════ CONNECTING ═════════════ -->
        <template v-if="isConnecting">
          <div class="flex flex-col items-center justify-center gap-4 px-6 py-16 text-center">
            <span class="flex h-16 w-16 items-center justify-center rounded-full bg-codx-primary/10 text-codx-primary">
              <i class="fa-solid fa-phone-volume fa-beat text-2xl"></i>
            </span>
            <div>
              <h2 class="text-base font-semibold">Starting the call…</h2>
              <p class="mt-1 text-xs text-base-content/60">
                Opening the chat and the microphone.
              </p>
            </div>
            <button class="btn btn-ghost btn-sm" @click="$voice.close()">Cancel</button>
          </div>
        </template>

        <!-- ═════════════ SETUP ═════════════ -->
        <template v-else-if="!isCallRunning && !isEndedSummary">
          <header class="flex items-start justify-between gap-3 border-b border-base-content/10 px-5 py-4">
            <div class="flex items-center gap-3">
              <span class="flex h-11 w-11 items-center justify-center rounded-2xl bg-codx-primary/15 text-codx-primary">
                <i class="fa-solid fa-phone-volume text-lg"></i>
              </span>
              <div>
                <h2 class="text-base font-semibold">Voice conversation</h2>
                <p class="text-xs text-base-content/60">
                  Talk to codx-junior and hear the answers. Everything is saved in the chat.
                </p>
              </div>
            </div>
            <button class="btn btn-ghost btn-sm btn-circle" aria-label="Close" @click="$voice.close()">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </header>

          <div class="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
            <div
              v-if="$voice.error"
              class="rounded-xl border border-error/30 bg-error/10 px-3 py-2 text-xs text-error"
            >
              <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ $voice.error }}
            </div>
            <!-- Capability warnings -->
            <div
              v-if="!$voice.support.speechSynthesis && !$voice.capabilities?.synthesis?.available"
              class="rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              <i class="fa-solid fa-triangle-exclamation mr-1"></i>
              This browser cannot speak: answers will be shown as text only.
            </div>
            <div
              v-if="!canCaptureAtAll"
              class="rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              <i class="fa-solid fa-triangle-exclamation mr-1"></i>
              Voice input is not available here. You can still start the call and type your instructions.
            </div>

            <!-- Start in the current chat -->
            <div class="rounded-2xl border border-base-content/10 bg-base-200/50 p-4">
              <div class="text-[11px] uppercase tracking-wide text-base-content/50">Continue in this chat</div>
              <div class="mt-1 flex items-center gap-2">
                <i class="fa-regular fa-comments text-base-content/50"></i>
                <span class="min-w-0 flex-1 truncate text-sm font-semibold">{{ setupChatName }}</span>
              </div>
              <div class="mt-3 flex flex-wrap gap-2">
                <button class="btn btn-primary btn-sm" @click="startHere">
                  <i class="fa-solid fa-phone"></i>
                  {{ setupChat ? 'Start call here' : 'Start a call' }}
                </button>
                <button class="btn btn-outline btn-sm" @click="$voice.startNewConversation()">
                  <i class="fa-solid fa-plus"></i>
                  New conversation
                </button>
              </div>
            </div>

            <!-- Resume / history -->
            <div>
              <div class="mb-2 flex items-center justify-between gap-2">
                <div class="text-[11px] uppercase tracking-wide text-base-content/50">Recent calls</div>
                <button class="btn btn-ghost btn-xs" :disabled="$voice.loadingSessions" @click="$voice.loadRecentSessions()">
                  <i class="fa-solid fa-rotate" :class="{ 'fa-spin': $voice.loadingSessions }"></i>
                </button>
              </div>
              <div v-if="$voice.loadingSessions" class="py-4 text-center text-xs text-base-content/50">
                Loading calls…
              </div>
              <div v-else-if="!$voice.recentSessions.length" class="rounded-xl border border-dashed border-base-content/15 py-5 text-center text-xs text-base-content/50">
                No previous voice conversations yet.
              </div>
              <ul v-else class="space-y-1.5">
                <li v-for="session in $voice.recentSessions" :key="session.session_id">
                  <button
                    class="flex w-full items-center gap-3 rounded-xl border border-base-content/10 px-3 py-2 text-left transition-colors hover:border-codx-primary/40 hover:bg-base-200/60"
                    @click="$voice.resumeSession(session)"
                  >
                    <span
                      class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                      :class="session.status === 'active' ? 'bg-success/15 text-success' : 'bg-base-content/10 text-base-content/50'"
                    >
                      <i :class="session.status === 'active' ? 'fa-solid fa-phone' : 'fa-solid fa-phone-slash'"></i>
                    </span>
                    <span class="min-w-0 flex-1">
                      <span class="block truncate text-sm">{{ session.title || 'Voice conversation' }}</span>
                      <span class="block truncate text-[11px] text-base-content/50">
                        {{ session.last_user_text || session.chat_id }}
                        · {{ formatDate(session.updated_at) }}
                      </span>
                    </span>
                    <span class="badge badge-sm" :class="session.status === 'active' ? 'badge-success' : 'badge-ghost'">
                      {{ session.status === 'active' ? 'Resume' : 'Continue' }}
                    </span>
                  </button>
                </li>
              </ul>
            </div>

            <!-- Language -->
            <label class="flex items-center gap-3 rounded-xl border border-base-content/10 px-3 py-2 text-xs">
              <i class="fa-solid fa-language text-base-content/50"></i>
              <span class="shrink-0">Speech language</span>
              <select class="select select-bordered select-xs ml-auto" :value="$voice.language" @change="$voice.setLanguage($event.target.value)">
                <option v-for="option in speechLanguageOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
            </label>
          </div>
        </template>

        <!-- ═════════════ CALL ═════════════ -->
        <template v-else-if="isCallRunning">
          <header class="flex items-center gap-3 border-b border-base-content/10 px-4 py-3 sm:px-5">
            <button class="btn btn-ghost btn-sm btn-circle" title="Minimize call" @click="$voice.minimize()">
              <i class="fa-solid fa-chevron-down"></i>
            </button>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="truncate text-sm font-semibold">{{ callTitle }}</span>
                <span class="badge badge-sm" :class="statusBadgeClass">{{ $voice.statusLabel }}</span>
              </div>
              <div class="flex items-center gap-2 text-[11px] text-base-content/50">
                <span class="tabular-nums">{{ elapsedLabel }}</span>
                <span>·</span>
                <span class="truncate">{{ callChatName }}</span>
                <span v-if="!$voice.isOnCallChat" class="text-warning">· viewing another chat</span>
              </div>
            </div>
            <button
              v-if="!$voice.isOnCallChat && $chats.activeChat"
              class="btn btn-ghost btn-sm btn-circle text-info"
              title="Move the call to this chat"
              @click="bringCallHere"
            >
              <i class="fa-solid fa-arrow-right-to-bracket"></i>
            </button>
            <button class="btn btn-ghost btn-sm btn-circle" title="Open call chat" @click="openCallChat">
              <i class="fa-regular fa-comments"></i>
            </button>
            <button class="btn btn-error btn-sm btn-circle" title="End call" @click="$voice.endCall({ reason: 'ended by user', announce: true })">
              <i class="fa-solid fa-phone-slash"></i>
            </button>
          </header>

          <div class="min-h-0 flex-1 overflow-y-auto">
            <!-- Voice orb -->
            <div class="flex flex-col items-center px-5 pb-2 pt-6">
              <div
                class="voice-orb relative flex h-28 w-28 items-center justify-center rounded-full"
                :class="orbClass"
              >
                <span class="absolute inset-0 rounded-full border border-current opacity-20" :class="orbPulseClass"></span>
                <i :class="orbIcon" class="text-3xl"></i>
                <span class="absolute bottom-2 text-[10px] uppercase tracking-widest text-base-content/50">
                  {{ orbHint }}
                </span>
              </div>
              <div
                v-if="$voice.progressText && $voice.status === 'thinking'"
                class="mt-2 max-w-xs text-center text-xs text-base-content/60"
              >
                <i class="fa-solid fa-circle-notch fa-spin mr-1 text-[10px]"></i>
                {{ $voice.progressText }}
              </div>
              <div class="mt-3 flex h-5 items-end gap-1" aria-hidden="true">
                <span
                  v-for="index in 9"
                  :key="index"
                  class="w-1 rounded-full bg-current transition-all duration-150"
                  :class="$voice.level > index * 9 ? 'opacity-90' : 'opacity-20'"
                  :style="{ height: `${6 + Math.min(18, ($voice.level / 100) * 18 + index * 1.2)}px` }"
                ></span>
              </div>
            </div>

            <!-- Captions -->
            <div v-if="$voice.captions" class="mx-auto max-w-xl space-y-2 px-5 pb-4">
              <div
                v-for="message in captionMessages"
                :key="message.doc_id || message.id"
                class="rounded-xl px-3 py-2 text-sm"
                :class="message.role === 'user'
                  ? 'ml-8 bg-primary/10 text-base-content'
                  : 'mr-8 bg-base-200/70 text-base-content/90'"
              >
                <span class="mb-0.5 block text-[10px] uppercase tracking-wide text-base-content/45">
                  {{ message.role === 'user' ? 'You' : 'codx-junior' }}
                </span>
                <span class="whitespace-pre-wrap break-words">{{ shortText(message.content) }}</span>
              </div>
              <div v-if="$voice.interim" class="ml-8 rounded-xl bg-primary/5 px-3 py-2 text-sm italic text-base-content/60">
                <span class="mb-0.5 block text-[10px] uppercase tracking-wide text-base-content/40">You</span>
                {{ $voice.interim }}
              </div>
              <div v-if="!captionMessages.length && !$voice.interim" class="py-3 text-center text-xs text-base-content/50">
                Say something like “show me the task board” or “switch to the payments task”.
                You can interrupt me while I am speaking.
              </div>
            </div>

            <!-- Typed fallback -->
            <div class="border-t border-base-content/10 px-4 py-3 sm:px-5">
              <div class="flex items-center gap-2">
                <input
                  v-model="typedText"
                  class="input input-bordered input-sm min-w-0 flex-1"
                  :placeholder="typedPlaceholder"
                  :disabled="$voice.status === 'thinking' || $voice.status === 'transcribing'"
                  @keydown.enter.prevent="sendTyped"
                />
                <button
                  class="btn btn-primary btn-sm"
                  :disabled="!typedText.trim() || $voice.status === 'thinking' || $voice.status === 'transcribing'"
                  @click="sendTyped"
                >
                  <i class="fa-solid fa-paper-plane"></i>
                </button>
              </div>
              <div v-if="$voice.error" class="mt-2 flex items-start gap-2 text-[11px] text-error">
                <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
                <span class="min-w-0 flex-1">{{ $voice.error }}</span>
                <button class="link" @click="$voice.retryMicrophone()">Retry mic</button>
              </div>
            </div>
          </div>

          <!-- Controls -->
          <footer class="flex flex-wrap items-center justify-center gap-2 border-t border-base-content/10 px-3 py-3">
            <button
              class="btn btn-circle btn-sm"
              :class="$voice.muted ? 'btn-error' : 'btn-success'"
              :title="$voice.muted ? 'Unmute microphone' : 'Mute microphone'"
              @click="$voice.toggleMute()"
            >
              <i :class="$voice.muted ? 'fa-solid fa-microphone-slash' : 'fa-solid fa-microphone'"></i>
            </button>
            <button
              class="btn btn-circle btn-sm"
              :class="$voice.status === 'speaking' ? 'btn-info' : 'btn-ghost'"
              :title="$voice.status === 'speaking' ? 'Stop speaking' : 'Speak last answer again'"
              @click="speakLast"
            >
              <i :class="$voice.status === 'speaking' ? 'fa-solid fa-stop' : 'fa-solid fa-volume-high'"></i>
            </button>
            <button class="btn btn-circle btn-sm btn-ghost" title="Captions" @click="$voice.toggleCaptions()">
              <i :class="$voice.captions ? 'fa-solid fa-closed-captioning' : 'fa-regular fa-closed-captioning'"></i>
            </button>
            <button
              class="btn btn-circle btn-sm btn-ghost"
              :class="$voice.narrateProgress ? 'text-info' : 'text-base-content/30'"
              :title="$voice.narrateProgress ? 'Spoken progress on' : 'Spoken progress off'"
              @click="$voice.toggleNarration()"
            >
              <i class="fa-solid fa-bullhorn"></i>
            </button>
            <button
              class="btn btn-circle btn-sm btn-ghost text-secondary"
              :title="`Language: ${currentLanguageLabel}`"
              @click="showLanguage = true"
            >
              <i class="fa-solid fa-language"></i>
            </button>
            <button class="btn btn-circle btn-sm btn-ghost" title="Switch conversation" @click="showSwitch = true">
              <i class="fa-solid fa-right-left"></i>
            </button>
            <button class="btn btn-error btn-sm gap-2" @click="$voice.endCall({ reason: 'ended by user', announce: true })">
              <i class="fa-solid fa-phone-slash"></i>
              <span class="hidden sm:inline">End</span>
            </button>
          </footer>
        </template>

        <!-- ═════════════ ENDED SUMMARY ═════════════ -->
        <template v-else-if="isEndedSummary">
          <header class="flex items-center justify-between gap-3 border-b border-base-content/10 px-5 py-4">
            <div class="flex items-center gap-3">
              <span class="flex h-11 w-11 items-center justify-center rounded-2xl bg-base-content/10 text-base-content/60">
                <i class="fa-solid fa-phone-slash"></i>
              </span>
              <div>
                <h2 class="text-base font-semibold">Call ended</h2>
                <p class="text-xs text-base-content/60">{{ callChatName }} · {{ elapsedLabel }}</p>
              </div>
            </div>
            <button class="btn btn-ghost btn-sm btn-circle" aria-label="Close" @click="$voice.close()">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </header>
          <div class="space-y-4 px-5 py-4">
            <p class="text-sm text-base-content/70">
              The conversation is saved in the chat. You can continue it as text, resume the call, or start a new one.
            </p>
            <div class="flex flex-wrap gap-2">
              <button class="btn btn-primary btn-sm" @click="$voice.resumeSession($voice.session)">
                <i class="fa-solid fa-phone"></i> Resume call
              </button>
              <button class="btn btn-outline btn-sm" @click="$voice.startNewConversation()">
                <i class="fa-solid fa-plus"></i> New conversation
              </button>
              <button class="btn btn-ghost btn-sm" @click="openCallChat">
                <i class="fa-regular fa-comments"></i> Open chat
              </button>
              <button class="btn btn-ghost btn-sm ml-auto" @click="$voice.close()">Close</button>
            </div>
          </div>
        </template>
      </section>
    </div>

    <!-- ── Switch chat dialog ────────────────────────────────────────── -->
    <div
      v-if="showSwitch"
      class="fixed inset-0 z-[95] flex items-center justify-center bg-base-300/70 p-4 backdrop-blur-sm"
      @click.self="showSwitch = false"
    >
      <div class="flex max-h-[80vh] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-base-content/10 bg-base-100 shadow-2xl">
        <div class="flex items-center justify-between border-b border-base-content/10 px-4 py-3">
          <h3 class="text-sm font-semibold">Switch voice conversation</h3>
          <button class="btn btn-ghost btn-xs btn-circle" @click="showSwitch = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="px-4 py-3">
          <input
            v-model="switchSearch"
            class="input input-bordered input-sm w-full"
            placeholder="Search chats…"
            autofocus
          />
          <p class="mt-2 text-[11px] text-base-content/50">
            You can also say “switch to &lt;chat name&gt;” during the call.
          </p>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          <button
            v-for="chat in filteredSwitchChats"
            :key="chat.id"
            class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-base-200"
            @click="switchTo(chat)"
          >
            <i class="fa-regular fa-comment text-base-content/40"></i>
            <span class="min-w-0 flex-1 truncate text-sm">{{ chat.name || chat.id }}</span>
            <span class="text-[10px] text-base-content/40">{{ formatDate(chat.updated_at) }}</span>
          </button>
          <div v-if="!filteredSwitchChats.length" class="py-6 text-center text-xs text-base-content/50">
            No other chats found.
          </div>
        </div>
      </div>
    </div>

    <!-- ── Conversation language dialog ──────────────────────────────── -->
    <div
      v-if="showLanguage"
      class="fixed inset-0 z-[95] flex items-center justify-center bg-base-300/70 p-4 backdrop-blur-sm"
      @click.self="showLanguage = false"
    >
      <div class="flex max-h-[80vh] w-full max-w-sm flex-col overflow-hidden rounded-2xl border border-base-content/10 bg-base-100 shadow-2xl">
        <div class="flex items-center justify-between border-b border-base-content/10 px-4 py-3">
          <div>
            <h3 class="text-sm font-semibold">Conversation language</h3>
            <p class="text-[11px] text-base-content/50">Applies to what you say and hear in this call.</p>
          </div>
          <button class="btn btn-ghost btn-xs btn-circle" @click="showLanguage = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto p-2">
          <button
            v-for="option in speechLanguageOptions"
            :key="option.value"
            class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-base-200"
            :class="option.value === $voice.language && 'bg-secondary/10 text-secondary'"
            @click="chooseLanguage(option.value)"
          >
            <i
              class="w-4 text-[11px]"
              :class="option.value === $voice.language ? 'fa-solid fa-check' : 'fa-solid fa-language opacity-40'"
            ></i>
            <span class="min-w-0 flex-1 truncate">{{ option.label }}</span>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script>
import { languageLabel, SPEECH_LANGUAGE_OPTIONS } from '@/config/languages'

export default {
  data() {
    return {
      typedText: '',
      switchSearch: '',
      showSwitch: false,
      showLanguage: false
    }
  },
  computed: {
    isCallRunning() {
      return Boolean(this.$voice.session && this.$voice.session.status === 'active')
    },
    isConnecting() {
      return this.$voice.status === 'connecting' && !this.isCallRunning
    },
    isEndedSummary() {
      return Boolean(this.$voice.session && this.$voice.session.status === 'ended')
    },
    setupChat() {
      return this.$voice.setupChat || this.$chats.activeChat
    },
    setupChatName() {
      return this.setupChat?.name || 'A new conversation'
    },
    callChat() {
      return this.$voice.chat
    },
    callChatName() {
      return this.callChat?.name || this.$voice.session?.title || 'Voice conversation'
    },
    callTitle() {
      return this.callChat?.name || this.$voice.session?.title || this.callChatName
    },
    elapsedLabel() {
      return this.formatDuration(this.$voice.elapsed)
    },
    currentLanguageLabel() {
      return languageLabel(this.$voice.language) || this.$voice.language
    },
    captionMessages() {
      return (this.$voice.callMessages || []).filter(message =>
        ['user', 'assistant'].includes(message.role) && (message.content || '').trim()
      )
    },
    filteredSwitchChats() {
      const query = (this.switchSearch || '').toLowerCase().trim()
      const chats = this.$voice.switchChats || []
      return query
        ? chats.filter(chat => (chat.name || '').toLowerCase().includes(query))
        : chats
    },
    statusBadgeClass() {
      const tone = this.$voice.statusTone
      if (tone === 'success') return 'badge-success'
      if (tone === 'warning') return 'badge-warning'
      if (tone === 'info') return 'badge-info'
      if (tone === 'error') return 'badge-error'
      return 'badge-ghost'
    },
    orbClass() {
      if (this.$voice.status === 'speaking') return 'bg-info/10 text-info voice-orb-speaking'
      if (this.$voice.status === 'thinking' || this.$voice.status === 'transcribing') return 'bg-warning/10 text-warning voice-orb-thinking'
      if (this.$voice.status === 'error') return 'bg-error/10 text-error'
      if (this.$voice.muted) return 'bg-base-content/5 text-base-content/40'
      return 'bg-success/10 text-success voice-orb-listening'
    },
    orbPulseClass() {
      return ['listening', 'speaking', 'thinking'].includes(this.$voice.status)
        ? 'animate-ping'
        : ''
    },
    orbIcon() {
      if (this.$voice.status === 'speaking') return 'fa-solid fa-volume-high'
      if (this.$voice.status === 'thinking' || this.$voice.status === 'transcribing') return 'fa-solid fa-spinner fa-spin'
      if (this.$voice.status === 'error') return 'fa-solid fa-triangle-exclamation'
      if (this.$voice.muted) return 'fa-solid fa-microphone-slash'
      return 'fa-solid fa-microphone'
    },
    orbHint() {
      if (this.$voice.status === 'listening') return this.$voice.interim ? 'hearing you' : 'listening'
      if (this.$voice.status === 'speaking') return 'speaking'
      if (this.$voice.status === 'thinking') return 'working'
      if (this.$voice.status === 'transcribing') return 'transcribing'
      if (this.$voice.status === 'error') return 'check mic'
      if (this.$voice.muted) return 'muted'
      return 'ready'
    },
    typedPlaceholder() {
      if (!this.$voice.canListen) return 'Type your instruction (voice input unavailable)…'
      return 'Or type here…'
    },
    canCaptureAtAll() {
      return Boolean(
        this.$voice.support.speechRecognition ||
        (this.$voice.support.microphone &&
          this.$voice.support.mediaRecorder &&
          this.$voice.capabilities?.transcription?.available)
      )
    },
    speechLanguageOptions() {
      const options = SPEECH_LANGUAGE_OPTIONS.map(option => ({ ...option }))
      const current = this.$voice.language
      if (current && !options.some(option => option.value === current)) {
        options.unshift({ value: current, label: languageLabel(current) })
      }
      return options
    }
  },
  mounted() {
    window.addEventListener('keydown', this.onKeyDown)
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onKeyDown)
  },
  methods: {
    onKeyDown(event) {
      if (event.key !== 'Escape') return
      const tag = String(event.target?.tagName || '').toUpperCase()
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return
      if (this.showSwitch) {
        this.showSwitch = false
        return
      }
      if (this.showLanguage) {
        this.showLanguage = false
        return
      }
      if (this.$voice.open) this.$voice.close()
    },
    startHere() {
      return this.$voice.startCall({ chat: this.setupChat })
    },
    sendTyped() {
      const value = this.typedText.trim()
      if (!value) return
      this.typedText = ''
      return this.$voice.sendTyped(value)
    },
    speakLast() {
      if (this.$voice.status === 'speaking') return this.$voice.stopSpeaking()
      if (['thinking', 'transcribing', 'switching'].includes(this.$voice.status)) return
      const last = this.$voice.lastReply
      return this.$voice.speak({ text: last?.content || 'There is nothing to repeat yet.', force: true })
    },
    bringCallHere() {
      const chat = this.$chats.activeChat
      if (!chat?.id) return
      return this.$voice.switchToChat({ chat, reason: 'moved to the chat I am viewing' })
    },
    openCallChat() {
      if (!this.$voice.session) return
      this.$ui.openChat({
        id: this.$voice.session.chat_id,
        name: this.callChatName
      })
    },
    async switchTo(chat) {
      this.showSwitch = false
      return this.$voice.switchToChat({ chat, reason: 'switched from the voice panel' })
    },
    async chooseLanguage(language) {
      this.showLanguage = false
      if (language === this.$voice.language) return true
      return this.$voice.setLanguage(language)
    },
    shortText(text) {
      const value = String(text || '').replace(/\s+/g, ' ').trim()
      return value.length > 280 ? `${value.slice(0, 277)}…` : value
    },
    formatDuration(totalSeconds) {
      const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0))
      const minutes = Math.floor(seconds / 60)
      return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
    },
    formatDate(value) {
      if (!value) return ''
      try {
        return new Date(value).toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit'
        })
      } catch (error) {
        return ''
      }
    }
  }
}
</script>

<style scoped>
.voice-orb {
  border: 1px solid currentColor;
  transition: background-color 0.2s ease, color 0.2s ease;
}
.voice-orb-listening {
  animation: voice-breathe 2.4s ease-in-out infinite;
}
.voice-orb-speaking {
  animation: voice-breathe 1.1s ease-in-out infinite;
}
.voice-orb-thinking {
  animation: voice-breathe 1.8s ease-in-out infinite;
}
@keyframes voice-breathe {
  0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 currentColor; }
  50% { transform: scale(1.04); box-shadow: 0 0 24px 2px currentColor; }
}
@media (prefers-reduced-motion: reduce) {
  .voice-orb,
  .voice-orb-listening,
  .voice-orb-speaking,
  .voice-orb-thinking {
    animation: none;
  }
}
</style>
