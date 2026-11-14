<script setup>
import ConfirmDialog from '../ConfirmDialog.vue'
import BrowserSessionCreateModal from './BrowserSessionCreateModal.vue'
import ComputerImageLibrary from './ComputerImageLibrary.vue'
import ComputerViewControls from './ComputerViewControls.vue'
</script>

<template>
  <!-- Collapsed: icon rail -->
  <div v-if="collapsed" class="flex flex-col items-center h-full w-full py-3 bg-base-100">
    <!-- Brand mark: the icon itself expands the sidebar (no separate arrow) -->
    <button
      @click="$emit('toggle-collapse')"
      class="w-10 h-10 flex-shrink-0 rounded-xl bg-primary/15 text-primary flex items-center justify-center transition-colors duration-150 hover:bg-primary/25 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      title="Expand sidebar"
      aria-label="Expand sidebar"
      aria-expanded="false"
    >
      <i class="fa-solid fa-window-restore"></i>
    </button>

    <div class="w-6 border-t border-base-content/10 mt-3 mb-2 flex-shrink-0"></div>

    <!-- Stage controls: reachable while the sidebar is collapsed, so the
         stage itself carries no toolbar. -->
    <ComputerViewControls
      v-if="viewControls"
      collapsed
      class="flex-shrink-0"
      @manage="$emit('manage')"
    />
    <div v-if="viewControls" class="w-6 border-t border-base-content/10 mt-2 mb-2 flex-shrink-0"></div>

    <!-- Session rail -->
    <div class="flex-1 w-full min-h-0 overflow-y-auto flex flex-col items-center gap-1.5 px-2 pb-2">
      <div v-if="isLoading" class="flex items-center justify-center py-4">
        <div class="loading loading-spinner loading-xs"></div>
      </div>

      <template v-else>
        <button
          v-for="session in sessions"
          :key="session.id"
          @click="handleSelectSession(session.id)"
          class="relative w-10 h-10 flex-shrink-0 rounded-xl flex items-center justify-center text-sm font-semibold uppercase transition-colors duration-150 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          :class="sessionItemClass(session)"
          :title="sessionTooltip(session)"
          :aria-label="sessionLabel('selectItem', session)"
          :aria-current="selectedSessionId === session.id ? 'true' : 'false'"
        >
          {{ sessionInitial(session) }}
          <span
            v-if="session.status"
            class="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-base-100"
            :class="statusDotClass(session.status)"
            aria-hidden="true"
          ></span>
          <span
            v-if="session.needs_user"
            class="absolute -bottom-1 -right-1 flex h-3.5 w-3.5 items-center justify-center rounded-full border-2 border-base-100 bg-warning text-warning-content animate-pulse"
            :title="sessionLabels.needsYouHint"
            aria-hidden="true"
          >
            <i class="fa-solid fa-hand text-[6px]"></i>
          </span>
        </button>

        <!-- The list could not be fetched: say it and let the user retry
             instead of showing an empty rail that looks like "no sessions". -->
        <button
          v-if="hasError"
          @click="reloadSessions"
          class="w-10 h-10 flex-shrink-0 rounded-xl flex items-center justify-center text-error/80 hover:bg-error/10 transition-colors duration-150"
          :title="`Could not load sessions: ${errorMessage}`"
          :aria-label="sessionLabels.retry"
        >
          <i class="fa-solid fa-triangle-exclamation"></i>
        </button>

        <div v-if="!hasError && sessions.length === 0" class="py-3 text-base-content/40" :title="sessionLabels.empty">
          <i class="fa-solid fa-inbox"></i>
        </div>
      </template>
    </div>

    <div class="w-6 border-t border-base-content/10 mb-2 flex-shrink-0"></div>

    <!-- Bottom actions -->
    <div class="flex flex-col items-center gap-2 flex-shrink-0">
      <button
        @click="handleRailNewSession"
        class="btn btn-sm btn-primary btn-circle"
        :title="sessionLabels.newItem"
        :aria-label="sessionLabels.newItem"
      >
        <i class="fa-solid fa-plus"></i>
      </button>
      <button
        @click="browseImageLibrary"
        class="btn btn-sm btn-ghost btn-circle text-base-content/50 hover:text-base-content"
        :title="sessionLabels.browseImages"
        :aria-label="sessionLabels.browseImages"
      >
        <i class="fa-solid fa-images"></i>
      </button>
      <button
        @click="reloadSessions"
        class="btn btn-sm btn-ghost btn-circle text-base-content/50 hover:text-base-content"
        :title="sessionLabels.reload"
        :aria-label="sessionLabels.reload"
      >
        <i class="fa-solid fa-arrows-rotate"></i>
      </button>
    </div>
  </div>

  <!-- Expanded -->
  <div v-else class="flex flex-col h-full bg-base-100 p-3 pb-4">
    <!-- Header: the brand icon is also the collapse toggle (no separate arrow) -->
    <div class="flex items-center gap-2.5 px-1 flex-shrink-0">
      <button
        v-if="collapsible"
        @click="$emit('toggle-collapse')"
        class="w-10 h-10 flex-shrink-0 rounded-xl bg-primary/15 text-primary flex items-center justify-center transition-colors duration-150 hover:bg-primary/25 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        title="Collapse sidebar"
        aria-label="Collapse sidebar"
        aria-expanded="true"
      >
        <i class="fa-solid fa-window-restore"></i>
      </button>
      <div
        v-else
        class="w-10 h-10 flex-shrink-0 rounded-xl bg-primary/15 text-primary flex items-center justify-center"
      >
        <i class="fa-solid fa-window-restore"></i>
      </div>
      <h2 class="text-base font-bold truncate flex-1">{{ sessionLabels.section }}</h2>
    </div>

    <div class="border-t border-base-content/10 mt-3 mb-2 flex-shrink-0"></div>

    <!-- Stage controls (same actions as the collapsed rail) -->
    <ComputerViewControls
      v-if="viewControls"
      class="mb-2 flex-shrink-0"
      @manage="$emit('manage')"
    />
    <div v-if="viewControls" class="border-t border-base-content/10 mb-2 flex-shrink-0"></div>

    <!-- Sessions List -->
    <div class="flex-1 min-h-0 overflow-y-auto mt-1 space-y-0.5">
      <div v-if="isLoading" class="flex items-center justify-center py-8">
        <div class="loading loading-spinner loading-sm"></div>
        <span class="ml-2 text-sm">{{ sessionLabels.loading }}</span>
      </div>

      <!-- Failure state: the request did not succeed, so "no sessions yet"
           would be a lie. Show what happened and offer a retry. -->
      <div
        v-else-if="hasError"
        class="flex flex-col items-center gap-2 px-2 py-8 text-center"
        role="alert"
        aria-live="polite"
      >
        <i class="fa-solid fa-triangle-exclamation text-2xl text-error/70" aria-hidden="true"></i>
        <p class="text-sm font-semibold text-error">{{ sessionLabels.loadError }}</p>
        <p class="max-w-[18rem] break-words text-[11px] opacity-60">{{ errorMessage }}</p>
        <button class="btn btn-xs btn-outline gap-1" @click="reloadSessions">
          <i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i>
          {{ sessionLabels.retryShort }}
        </button>
      </div>

      <div v-else-if="sessions.length === 0" class="text-center py-8 text-base-content/50">
        <i class="fa-solid fa-inbox text-2xl mb-2 block"></i>
        <p class="text-sm">{{ sessionLabels.empty }}</p>
      </div>

      <template v-else>
        <div
          v-for="session in sessions"
          :key="session.id"
          class="group flex items-center gap-1 pl-0 pr-1 rounded-full transition-colors duration-150"
          :class="sessionRowClass(session)"
        >
          <button
            @click="handleSelectSession(session.id)"
            class="flex-1 min-w-0 flex items-center gap-2.5 pl-3 pr-2 py-2 text-left rounded-full focus:outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
            :aria-label="sessionLabel('selectItem', session)"
            :aria-current="selectedSessionId === session.id ? 'true' : 'false'"
          >
            <span
              class="w-7 h-7 flex-shrink-0 rounded-lg flex items-center justify-center text-[11px] font-semibold uppercase"
              :class="sessionAvatarClass(session)"
            >
              {{ sessionInitial(session) }}
            </span>
            <span class="flex-1 min-w-0">
              <span
                class="block text-sm truncate"
                :class="selectedSessionId === session.id ? 'font-semibold text-primary' : 'text-base-content/80'"
              >
                {{ session.name }}
              </span>
              <!-- Kind + project mapping of the session (falls back to its URL) -->
              <span
                class="flex items-center gap-1 text-[11px] text-base-content/50"
                :title="sessionTooltip(session)"
              >
                <i :class="kindIcon(session)" class="text-[9px] flex-shrink-0" aria-hidden="true"></i>
                <span class="badge badge-xs badge-ghost px-1">{{ kindLabel(session) }}</span>
                <span class="truncate">{{ sessionSubtitle(session) }}</span>
                <span
                  v-if="session.needs_user"
                  class="badge badge-xs badge-warning gap-1 animate-pulse"
                  :title="sessionLabels.needsYouHint"
                >
                  <i class="fa-solid fa-hand" aria-hidden="true"></i>{{ sessionLabels.needsYou }}
                </span>
              </span>
            </span>
            <span
              v-if="session.status"
              class="w-2 h-2 flex-shrink-0 rounded-full"
              :class="statusDotClass(session.status)"
              :title="session.status"
              aria-hidden="true"
            ></span>
          </button>

          <!-- Actions: quiet until hover/focus on desktop, always visible on touch -->
          <div class="flex items-center gap-0.5 flex-shrink-0 opacity-100 md:opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
            <button
              @click="handleRunSession(session.id, $event)"
              class="btn btn-ghost btn-xs btn-circle text-base-content/40 hover:text-success"
              :title="sessionLabel('runItem', session)"
              :aria-label="sessionLabel('runItem', session)"
            >
              <i class="fa-solid fa-play text-[10px]"></i>
            </button>
            <button
              @click="handleEditSession(session.id, $event)"
              class="btn btn-ghost btn-xs btn-circle text-base-content/40 hover:text-info"
              :title="sessionLabel('editItem', session)"
              :aria-label="sessionLabel('editItem', session)"
            >
              <i class="fa-solid fa-gear text-[10px]"></i>
            </button>
            <button
              @click="handleDeleteSession(session.id, $event)"
              class="btn btn-ghost btn-xs btn-circle text-base-content/40 hover:text-error"
              :title="sessionLabel('deleteItem', session)"
              :aria-label="sessionLabel('deleteItem', session)"
            >
              <i class="fa-solid fa-trash text-[10px]"></i>
            </button>
          </div>
        </div>
      </template>
    </div>

    <!-- Footer actions -->
    <div class="grid grid-cols-2 gap-2 flex-shrink-0 pt-3 mt-2 border-t border-base-content/10">
      <button @click="browseImageLibrary" class="btn btn-sm btn-outline gap-1">
        <i class="fa-solid fa-images"></i>
        Library
      </button>
      <button @click="toggleNewForm" class="btn btn-sm btn-primary gap-1">
        <i class="fa-solid fa-plus"></i>
        New session
      </button>
    </div>
  </div>

  <!-- New session settings (folder mapping + downloads) -->
  <BrowserSessionCreateModal v-if="showCreateModal" />

  <!-- Standalone image library: browse without starting from the form -->
  <Teleport to="body">
    <ComputerImageLibrary
      v-if="showImageLibrary"
      kind="linux"
      @select="onLibraryImageSelected"
      @close="showImageLibrary = false"
    />
  </Teleport>

  <!-- Delete confirmation modal -->
  <ConfirmDialog
    v-if="deleteTarget"
    :title="sessionLabels.deleteTitle"
    :message="deleteConfirmMessage(deleteTarget)"
    :hint="sessionLabels.deleteHint"
    icon="fa-trash"
    icon-class="text-error"
    confirm-label="Delete"
    confirm-icon="fa-trash"
    confirm-class="btn-error"
    :busy="deleting"
    @confirm="confirmDeleteSession"
    @cancel="deleteTarget = null"
  />
</template>

<script>
import { SESSION_LABELS, label } from '@/utils/sessionLabels'

export default {
  name: 'BrowserSessionList',
  components: {
    ConfirmDialog,
    BrowserSessionCreateModal
  },
  props: {
    collapsed: {
      type: Boolean,
      default: false
    },
    // The view controls belong to the desktop sidebar; the mobile list is a
    // full screen of its own and already has them in the header.
    viewControls: {
      type: Boolean,
      default: true
    },
    // The brand icon toggles the sidebar: only when the sidebar can actually
    // collapse (the mobile list is a full screen, so there it stays a mark).
    collapsible: {
      type: Boolean,
      default: true
    }
  },
  emits: ['toggle-collapse', 'manage'],
  data() {
    return {
      deleteTarget: null,
      deleting: false,
      showImageLibrary: false
    }
  },
  computed: {
    // Every user-facing name of the concept comes from utils/sessionLabels.js
    sessionLabels() {
      return SESSION_LABELS
    },
    sessions() {
      return this.$storex.browsers.allSessions
    },
    selectedSessionId() {
      return this.$storex.browsers.selectedSessionId
    },
    isLoading() {
      return this.$storex.browsers.isLoading
    },
    // Load failure of the session list: rendered instead of the empty state so
    // a failed request never looks like "there are no sessions".
    hasError() {
      return this.$storex.browsers.hasError
    },
    errorMessage() {
      return this.$storex.browsers.errorMessage
    },
    // The modal is owned by the store so both the rail and the footer button
    // (and any other entry point) open the very same dialog.
    showCreateModal() {
      return this.$storex.browsers.showNewSessionForm
    }
  },
  methods: {
    /** Fills a `{name}` label of the concept for one session. */
    sessionLabel(key, session) {
      return label(key, { name: session?.name || SESSION_LABELS.untitled })
    },
    deleteConfirmMessage(session) {
      return label('deleteConfirm', { name: session?.name || SESSION_LABELS.untitled })
    },
    sessionInitial(session) {
      const label = (session.name || session.current_url || '?').trim()
      return label.charAt(0)
    },
    kindLabel(session) {
      return session.kind_label || {
        browser: 'Browser',
        linux: 'Linux',
        windows: 'Windows',
        macos: 'macOS',
        remote: 'Remote'
      }[session.kind || 'browser'] || 'Computer'
    },
    kindIcon(session) {
      return {
        browser: 'fa-solid fa-compass',
        linux: 'fa-brands fa-linux',
        windows: 'fa-brands fa-windows',
        macos: 'fa-brands fa-apple',
        remote: 'fa-solid fa-network-wired'
      }[session.kind || 'browser'] || 'fa-solid fa-desktop'
    },
    /**
     * One-line description of what the session can see: the configured folder
     * mapping wins, the page URL is only a fallback.
     */
    sessionSubtitle(session) {
      const folders = session.folders || session.shared_folders || []
      if (folders.length === 1) {
        return folders[0].mount_path || folders[0].host_path || '1 folder'
      }
      if (folders.length > 1) {
        return `${folders.length} folders · ${session.home_path || ''}`.trim()
      }
      if (session.downloads_path) {
        return `Shared · ${session.guest_share_path || session.downloads_mount || session.downloads_path}`
      }
      if (session.kind === 'remote') {
        return `${session.vnc_host || 'remote'}:${session.vnc_port || 5900}`
      }
      return session.current_url || 'No project files'
    },
    /** Full mapping, shown as the row tooltip. */
    sessionTooltip(session) {
      const lines = [session.name || SESSION_LABELS.untitled]
      if (session.needs_user) lines.push(SESSION_LABELS.needsYouHint)
      const folders = (session.folders || session.shared_folders || [])
        .map(folder => folder.mount_path || folder.host_path)
        .filter(Boolean)
      lines.push(folders.length ? `Folders: ${folders.join(', ')}` : 'Folders: none')
      if (session.downloads_mount || session.downloads_path) {
        lines.push(`Downloads: ${session.downloads_mount || session.downloads_path}`)
      }
      const resources = []
      if (session.cpu_cores) resources.push(`${session.cpu_cores} vCPU`)
      if (session.ram_size) resources.push(`${session.ram_size} RAM`)
      if (session.disk_size) resources.push(`${session.disk_size} disk`)
      if (resources.length) lines.push(`Resources: ${resources.join(' · ')}`)
      if (session.image) lines.push(`Image: ${session.image}`)
      return lines.join('\n')
    },
    sessionItemClass(session) {
      // A machine waiting for the user blinks in the warning colour until the
      // user takes control (the API clears `needs_user` on take-control).
      if (session.needs_user) {
        return 'bg-warning/15 text-warning animate-pulse'
      }
      if (this.selectedSessionId === session.id) {
        return 'bg-primary/10 text-primary'
      }
      return 'text-base-content/60 hover:bg-base-200 hover:text-base-content'
    },
    sessionAvatarClass(session) {
      if (session.needs_user) {
        return 'bg-warning/20 text-warning animate-pulse'
      }
      if (this.selectedSessionId === session.id) {
        return 'bg-primary/20 text-primary'
      }
      return 'bg-base-200 text-base-content/70'
    },
    sessionRowClass(session) {
      if (this.selectedSessionId === session.id) {
        return 'bg-primary/10'
      }
      return 'hover:bg-base-200'
    },
    statusDotClass(status) {
      if (status === 'running') return 'bg-success'
      if (status === 'stopped') return 'bg-warning'
      return 'bg-base-content/30'
    },
    reloadSessions() {
      this.$storex.browsers.loadSessions()
    },
    handleRailNewSession() {
      // The dialog is self contained: it works from the collapsed rail too, so
      // the sidebar no longer has to expand to create a session.
      this.$storex.browsers.toggleNewSessionForm(true)
    },
    handleSelectSession(sessionId) {
      this.$storex.browsers.selectSession(sessionId)
    },
    handleDeleteSession(sessionId, event) {
      event.stopPropagation()
      this.deleteTarget = this.sessions.find(session => session.id === sessionId) || null
    },
    async confirmDeleteSession() {
      if (!this.deleteTarget) return

      this.deleting = true
      try {
        await this.$storex.browsers.deleteSession(this.deleteTarget.id)
        this.deleteTarget = null
      } catch (error) {
        console.error('Failed to delete session:', error)
      } finally {
        this.deleting = false
      }
    },
    async handleRunSession(sessionId, event) {
      event.stopPropagation()
      try {
        await this.$storex.browsers.runSession(sessionId)
      } catch (error) {
        console.error('Failed to run session:', error)
      }
    },
    handleEditSession(sessionId, event) {
      event.stopPropagation()
      this.$storex.browsers.startEditingSession(sessionId)
    },
    toggleNewForm() {
      this.$storex.browsers.toggleNewSessionForm(true)
    },
    browseImageLibrary() {
      this.showImageLibrary = true
    },
    onLibraryImageSelected(image) {
      this.showImageLibrary = false
      this.$storex.browsers.openNewSessionFromLibrary(image)
    }
  },
  mounted() {
    this.$storex.browsers.loadSessions()
  }
}
</script>