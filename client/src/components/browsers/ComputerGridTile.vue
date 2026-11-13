<script>
import BrowserViewer from './BrowserViewer.vue'
import DesktopViewer from './DesktopViewer.vue'

/**
 * One machine inside the multi-session wall (`ComputerGridView`).
 *
 * The tile renders in three ways:
 *  - `live`     the active tile: the real viewer, fully interactive;
 *  - `iframe`   browser-kind preview: the same live page the viewer embeds, so
 *               a Chromium preview costs no server-side capture;
 *  - `snapshot` desktop preview: a periodically refreshed screenshot.
 *
 * Only ONE tile per screen is live, and that is deliberate: desktop viewers
 * share process-wide state (KasmVNC resolves its websocket from the global
 * `path` localStorage key, so two live KasmVNC views on the same page steal
 * each other's route) and every extra live stream holds a VNC/WebRTC
 * connection plus its own polling. The wall therefore keeps a single
 * interactive machine and cheap previews for the rest; clicking a preview
 * promotes it (`activate`).
 */
export default {
  name: 'ComputerGridTile',
  components: { BrowserViewer, DesktopViewer },
  props: {
    session: { type: Object, required: true },
    // This tile carries the live, interactive viewer.
    active: { type: Boolean, default: false },
    // Thumbnail density (filmstrip / side rail): no header, label overlay only.
    compact: { type: Boolean, default: false },
    // Force the non-interactive preview even on the active tile: the principal
    // stage already renders the live viewer and must not open a second stream.
    previewOnly: { type: Boolean, default: false }
  },
  emits: ['activate', 'open', 'remove'],
  data() {
    return {
      preview: null,
      previewError: null,
      loadingPreview: false,
      previewTimer: null
    }
  },
  computed: {
    sessionId() {
      return this.session?.id || null
    },
    kind() {
      return this.session?.kind || 'browser'
    },
    isBrowser() {
      return this.kind === 'browser'
    },
    isRunning() {
      return this.session?.status === 'running'
    },
    live() {
      return this.active && !this.previewOnly
    },
    kindIcon() {
      return {
        linux: 'fa-brands fa-linux',
        windows: 'fa-brands fa-windows',
        macos: 'fa-brands fa-apple',
        remote: 'fa-solid fa-network-wired',
        browser: 'fa-solid fa-compass'
      }[this.kind] || 'fa-solid fa-desktop'
    },
    control() {
      return this.session?.controller || 'ai'
    },
    controlIcon() {
      if (this.control === 'user') return 'fa-solid fa-hand'
      if (this.control === 'none') return 'fa-solid fa-pause'
      return 'fa-solid fa-robot'
    },
    controlLabel() {
      if (this.control === 'user') return 'You drive'
      if (this.control === 'none') return 'Paused'
      return 'AI drives'
    },
    /** Same-origin path of the embedded live page (browser sessions only). */
    embedUrl() {
      const session = this.session
      if (!session) return ''
      if (session.vnc_path) return session.vnc_path
      if (!session.vnc_url) return ''
      try {
        return new URL(session.vnc_url, window.location.origin).pathname
      } catch (error) {
        return session.vnc_url
      }
    },
    /** How the tile body is rendered: live viewer, live iframe or snapshot. */
    previewMode() {
      if (this.live) return 'live'
      if (this.isBrowser && this.isRunning && this.embedUrl) return 'iframe'
      return 'snapshot'
    },
    usesSnapshot() {
      return this.previewMode === 'snapshot'
    }
  },
  watch: {
    previewMode() {
      this.syncPreviewLoop()
    },
    'session.status'() {
      this.syncPreviewLoop()
    }
  },
  mounted() {
    document.addEventListener('visibilitychange', this.onVisibilityChange)
    this.syncPreviewLoop()
  },
  beforeUnmount() {
    document.removeEventListener('visibilitychange', this.onVisibilityChange)
    this.stopPreviewLoop()
  },
  methods: {
    /** Poll only while this tile actually shows a desktop snapshot. */
    syncPreviewLoop() {
      if (!this.usesSnapshot || !this.isRunning) {
        this.stopPreviewLoop()
        return
      }
      if (!this.preview && !this.loadingPreview) this.loadPreview()
      if (this.previewTimer) return
      this.previewTimer = window.setInterval(() => {
        if (!document.hidden) this.loadPreview()
      }, 5000)
    },
    stopPreviewLoop() {
      if (!this.previewTimer) return
      window.clearInterval(this.previewTimer)
      this.previewTimer = null
    },
    onVisibilityChange() {
      // A hidden tab must not keep capturing every machine on the wall.
      if (document.hidden) {
        this.stopPreviewLoop()
        return
      }
      this.syncPreviewLoop()
    },
    async loadPreview() {
      if (!this.sessionId || this.loadingPreview || !this.usesSnapshot) return
      this.loadingPreview = true
      try {
        const result = await this.$storex.api.computers.screenshot(this.sessionId, {
          // The wall only needs pixels: OCR on every tile would be wasted work.
          include_text: false,
          max_width: 960
        })
        if (result?.screenshot) {
          this.preview = result.screenshot
          this.previewError = null
        } else if (result?.error) {
          this.previewError = result.error
        }
      } catch (error) {
        // One machine failing must never break the wall.
        this.previewError = error?.message || 'Preview unavailable'
      } finally {
        this.loadingPreview = false
      }
    }
  }
}
</script>

<template>
  <div
    class="group relative flex flex-col min-h-0 min-w-0 overflow-hidden rounded-xl border bg-base-100 transition-shadow duration-150"
    :class="active ? 'border-primary ring-1 ring-primary/40 shadow-md' : 'border-base-content/10'"
  >
    <!-- Header: identity + tile actions (full tiles only) -->
    <div
      v-if="!compact"
      class="flex items-center gap-1.5 px-2 py-1 border-b border-base-content/10 bg-base-200/70 flex-shrink-0"
    >
      <span class="h-2 w-2 flex-shrink-0 rounded-full" :class="isRunning ? 'bg-success' : 'bg-base-content/30'"></span>
      <i :class="kindIcon" class="text-[11px] text-base-content/50 flex-shrink-0" aria-hidden="true"></i>
      <button
        class="min-w-0 flex-1 truncate text-left text-xs font-semibold hover:text-primary transition-colors duration-150"
        :title="`Interact with ${session.name}`"
        @click="$emit('activate')"
      >{{ session.name }}</button>
      <span v-if="active" class="badge badge-xs badge-success gap-1 flex-shrink-0">live</span>
      <span class="badge badge-xs badge-ghost gap-1 flex-shrink-0" :title="controlLabel">
        <i :class="controlIcon"></i>
      </span>
      <button
        class="btn btn-ghost btn-xs btn-square"
        title="Open it alone (single view)"
        :aria-label="`Open ${session.name} in single view`"
        @click.stop="$emit('open')"
      >
        <i class="fa-solid fa-expand text-[11px]"></i>
      </button>
      <button
        class="btn btn-ghost btn-xs btn-square opacity-60 hover:opacity-100"
        title="Remove from the grid"
        :aria-label="`Remove ${session.name} from the grid`"
        @click.stop="$emit('remove')"
      >
        <i class="fa-solid fa-xmark text-[11px]"></i>
      </button>
    </div>

    <!-- Body: exactly one of live viewer / live iframe / snapshot -->
    <div class="relative flex-1 min-h-0 bg-black/85 overflow-hidden">
      <BrowserViewer v-if="previewMode === 'live' && isBrowser" :session="session" compact class="h-full" />
      <DesktopViewer v-else-if="previewMode === 'live'" :session="session" class="h-full" />

      <iframe
        v-else-if="previewMode === 'iframe'"
        :src="embedUrl"
        class="w-full h-full border-0 bg-black pointer-events-none"
        :title="`${session.name} preview`"
        tabindex="-1"
        aria-hidden="true"
      ></iframe>

      <template v-else>
        <img
          v-if="preview"
          :src="preview"
          class="w-full h-full object-contain"
          :alt="`${session.name} preview`"
        />
        <div
          v-else
          class="absolute inset-0 flex flex-col items-center justify-center gap-1 px-2 text-center text-[11px] text-base-content/50"
        >
          <span v-if="previewError" class="break-words text-error/80">{{ previewError }}</span>
          <template v-else>
            <span v-if="isRunning" class="loading loading-spinner loading-xs"></span>
            <i v-else :class="kindIcon" class="text-lg opacity-40"></i>
            <span>{{ isRunning ? 'Loading preview…' : 'Session stopped' }}</span>
          </template>
        </div>
      </template>

      <!-- Promote this tile: the overlay keeps previews cheap but clickable -->
      <button
        v-if="previewMode !== 'live'"
        class="absolute inset-0 z-10 flex items-end justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :title="`Interact with ${session.name}`"
        :aria-label="`Interact with ${session.name}`"
        @click="$emit('activate')"
      >
        <span
          class="mb-2 inline-flex items-center gap-1 rounded-full bg-base-content/70 text-base-100 text-[10px] px-2 py-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150"
        >
          <i class="fa-solid fa-hand-pointer"></i>
          Click to interact
        </span>
      </button>

      <!-- Thumbnail label (compact tiles have no header) -->
      <div
        v-if="compact"
        class="pointer-events-none absolute inset-x-0 bottom-0 z-20 flex items-center gap-1 bg-black/60 px-1.5 py-1"
      >
        <span class="h-1.5 w-1.5 flex-shrink-0 rounded-full" :class="isRunning ? 'bg-success' : 'bg-base-content/40'"></span>
        <span class="truncate text-[10px] text-white/90">{{ session.name }}</span>
        <i v-if="active" class="fa-solid fa-bolt ml-auto text-[8px] text-success"></i>
      </div>
    </div>
  </div>
</template>
