<script setup>
// Only imports belong here. The channel shell is a self-contained page for M0:
// it renders placeholders and does not talk to the media API yet.
// The Library tab reuses the existing documentation browser instead of a
// placeholder, so the wiki UI (tree, search, assistant) is never duplicated.
import WikiView from '@/views/WikiView.vue'
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-x-hidden bg-base-100 text-base-content">
    <!-- ── Header: which project's channel is this ────────────────────── -->
    <header class="shrink-0 border-b border-base-content/10 px-4 pb-3 pt-4">
      <div class="flex items-center gap-3">
        <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <i class="fa-solid fa-tower-broadcast" aria-hidden="true"></i>
        </div>
        <div class="min-w-0">
          <div class="text-[0.65rem] font-bold uppercase tracking-[0.18em] opacity-50">Project channel</div>
          <h1 class="truncate text-base font-bold leading-tight">{{ channelName }}</h1>
        </div>
        <!-- The docs live inside the channel as the Library tab; the standalone
             "/:projectName/wiki" page stays one tap away for old deep links. -->
        <RouterLink
          v-if="activeTab === 'library'"
          :to="{ name: 'wiki' }"
          class="ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-base-content/50 transition-colors hover:bg-base-content/5 hover:text-base-content"
          title="Open the wiki as a full page"
          aria-label="Open the wiki as a full page"
        >
          <i class="fa-solid fa-arrow-up-right-from-square text-xs" aria-hidden="true"></i>
        </RouterLink>
      </div>
    </header>

    <!-- ── Tabs, md+ ─────────────────────────────────────────────────────
         ≥768px keeps the horizontal bar; phones get the thumb-reachable
         bottom bar rendered after the panels (below). -->
    <nav class="hidden shrink-0 overflow-x-auto border-b border-base-content/10 px-2 md:block" aria-label="Channel sections">
      <div class="flex min-w-max items-stretch gap-1">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          class="flex min-h-[44px] min-w-[44px] items-center gap-2 whitespace-nowrap rounded-t-lg px-3 text-sm font-semibold transition-colors"
          :class="tab.id === activeTab
            ? 'border-b-2 border-primary text-primary'
            : 'text-base-content/60 hover:bg-base-content/5 hover:text-base-content'"
          :aria-current="tab.id === activeTab ? 'page' : undefined"
          @click="selectTab(tab.id)"
        >
          <i :class="tab.icon" aria-hidden="true"></i>
          <span>{{ tab.label }}</span>
        </button>
      </div>
    </nav>

    <!-- ── Panels ───────────────────────────────────────────────────────
         The Library renders the real documentation browser, which owns its
         own scroll containers; the wrapper only scrolls for the other tabs so
         the wiki never ends up in a nested scroll area. -->
    <div
      class="min-h-0 flex-1"
      :class="activeTab === 'library' ? 'overflow-hidden' : 'overflow-y-auto overflow-x-hidden'"
    >
      <!-- Home: hero + latest episodes skeleton + shorts rail. No data yet. -->
      <section v-if="activeTab === 'home'" class="flex flex-col gap-6 px-4 py-4">
        <!-- Hero reserved for the latest episode; tapping it opens the player. -->
        <button
          type="button"
          class="w-full overflow-hidden rounded-xl border border-dashed border-base-content/20 bg-base-200/40 text-left transition-colors hover:bg-base-200/70"
          @click="openPlayer()"
        >
          <div class="flex aspect-video w-full flex-col items-center justify-center gap-2 text-center">
            <i class="fa-solid fa-circle-play text-3xl opacity-30" aria-hidden="true"></i>
            <p class="px-4 text-sm font-semibold">Latest episode</p>
            <p class="px-4 text-xs opacity-60">The newest episode of this channel will play here.</p>
          </div>
          <span class="block border-t border-base-content/10 px-3 py-2 text-xs opacity-50">
            Nothing is published yet — this channel has no episodes.
          </span>
        </button>

        <!-- Latest episodes: skeleton cards -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Latest episodes</h2>
            <span class="text-xs opacity-40">0</span>
          </div>
          <div class="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1">
            <div
              v-for="n in episodePlaceholders"
              :key="`ep-${n}`"
              class="w-40 shrink-0 animate-pulse snap-start"
            >
              <div class="aspect-video w-full rounded-lg bg-base-content/10"></div>
              <div class="mt-2 h-2.5 w-4/5 rounded bg-base-content/10"></div>
              <div class="mt-1.5 h-2 w-1/2 rounded bg-base-content/10"></div>
            </div>
          </div>
        </div>

        <!-- Shorts: 9:16 rail (primary mobile format) -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Shorts</h2>
            <span class="text-xs opacity-40">0</span>
          </div>
          <div
            class="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1"
            role="list"
            aria-label="Shorts"
            tabindex="0"
            @keydown.left.prevent="scrollRail($event, -1)"
            @keydown.right.prevent="scrollRail($event, 1)"
          >
            <div
              v-for="n in shortPlaceholders"
              :key="`short-${n}`"
              class="w-24 shrink-0 animate-pulse snap-start"
              data-rail-item
              role="listitem"
            >
              <div class="aspect-[9/16] w-full rounded-lg bg-base-content/10"></div>
              <div class="mt-2 h-2 w-3/4 rounded bg-base-content/10"></div>
            </div>
          </div>
        </div>
      </section>

      <!-- Library: the documentation itself. <WikiView> is reused as-is, so
           the same page tree, search, "on this page" rail and assistant serve
           both "/:projectName/wiki" and this tab. On phones (<480 px) it
           already turns its tree into a drawer behind the "Docs" button, which
           keeps the article readable at 360 px. -->
      <section v-else-if="activeTab === 'library'" class="h-full min-h-0">
        <WikiView />
      </section>

      <!-- Shorts tab: the same 9:16 rail, one card per viewport slice. -->
      <section v-else-if="activeTab === 'shorts'" class="px-4 py-4">
        <div
          class="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-2"
          role="list"
          aria-label="Shorts"
          tabindex="0"
          @keydown.left.prevent="scrollRail($event, -1)"
          @keydown.right.prevent="scrollRail($event, 1)"
        >
          <div
            v-for="n in shortPlaceholders"
            :key="`short-tab-${n}`"
            class="w-[72vw] max-w-[260px] shrink-0 snap-center"
            data-rail-item
            role="listitem"
          >
            <div class="flex aspect-[9/16] w-full items-center justify-center rounded-xl border border-dashed border-base-content/20 bg-base-200/40">
              <i class="fa-solid fa-mobile-screen text-2xl opacity-20" aria-hidden="true"></i>
            </div>
            <div class="mt-2 h-2.5 w-3/4 rounded bg-base-content/10"></div>
            <div class="mt-1.5 h-2 w-1/2 rounded bg-base-content/10"></div>
          </div>
        </div>
        <p class="mt-3 text-center text-xs italic opacity-50">No shorts have been generated yet.</p>
      </section>

      <!-- Episodes / Learn: placeholder panels -->
      <section v-else class="px-4 py-4">
        <div class="rounded-xl border border-dashed border-base-content/20 bg-base-200/40 px-4 py-8 text-center">
          <i :class="[tabMeta.icon, 'text-2xl opacity-40']" aria-hidden="true"></i>
          <p class="mt-2 text-sm font-semibold">{{ tabMeta.title }}</p>
          <p class="mt-1 text-xs opacity-60">{{ tabMeta.description }}</p>
          <p class="mt-3 text-xs italic opacity-50">{{ tabMeta.empty }}</p>
        </div>
      </section>
    </div>

    <!-- ── Bottom tab bar, phones ────────────────────────────────────────
         One-handed reach: five 56px tall targets sitting on the safe-area
         inset, so nothing is lost behind the home indicator. Driven by the
         same ?tab= query as the desktop bar, so both stay one deep link. -->
    <nav
      class="shrink-0 border-t border-base-content/10 bg-base-100 pb-[env(safe-area-inset-bottom)] md:hidden"
      aria-label="Channel sections"
    >
      <div class="flex items-stretch">
        <button
          v-for="tab in tabs"
          :key="`bar-${tab.id}`"
          type="button"
          class="flex min-h-[56px] min-w-[44px] flex-1 flex-col items-center justify-center gap-1 px-1 text-[0.65rem] font-semibold transition-colors"
          :class="tab.id === activeTab
            ? 'text-primary'
            : 'text-base-content/60 active:bg-base-content/5 hover:text-base-content'"
          :aria-current="tab.id === activeTab ? 'page' : undefined"
          @click="selectTab(tab.id)"
        >
          <i :class="[tab.icon, 'text-base']" aria-hidden="true"></i>
          <span class="max-w-full truncate">{{ tab.label }}</span>
        </button>
      </div>
    </nav>

    <!-- ── Player overlay: chapters + transcript, full-bleed video ───────
         The overlay reserves the video area with an aspect-video spacer; the
         <video> itself is the fixed stage below (single element, never
         unmounted). -->
    <div v-show="playerOpen" class="fixed inset-0 z-30 flex flex-col bg-base-100">
      <div class="aspect-video w-full shrink-0"></div>
      <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[calc(1.5rem_+_env(safe-area-inset-bottom))] pt-4">
        <h2 class="text-base font-bold leading-tight">{{ currentEpisode.title }}</h2>
        <p class="mt-1 text-xs opacity-60">{{ currentEpisode.subtitle }}</p>

        <section class="mt-5" aria-label="Chapters">
          <h3 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Chapters</h3>
          <ul v-if="currentEpisode.chapters.length" class="mt-2 flex flex-col gap-1">
            <li v-for="chapter in currentEpisode.chapters" :key="chapter.at">
              <button
                type="button"
                class="flex min-h-[44px] w-full items-center gap-3 rounded-lg px-2 text-left text-sm hover:bg-base-content/5"
                @click="seekTo(chapter.at)"
              >
                <span class="font-mono text-xs opacity-60">{{ chapter.at }}</span>
                <span class="min-w-0 flex-1 truncate">{{ chapter.title }}</span>
              </button>
            </li>
          </ul>
          <p v-else class="mt-2 text-xs italic opacity-50">Chapters appear once the episode is rendered.</p>
        </section>

        <section class="mt-5" aria-label="Transcript">
          <h3 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Transcript</h3>
          <div v-if="currentEpisode.transcript.length" class="mt-2 flex flex-col gap-2">
            <p v-for="(line, index) in currentEpisode.transcript" :key="index" class="text-sm leading-relaxed">
              <span class="mr-2 font-mono text-xs opacity-50">{{ line.at }}</span>{{ line.text }}
            </p>
          </div>
          <p v-else class="mt-2 text-xs italic opacity-50">The transcript appears once the episode is rendered.</p>
        </section>
      </div>
    </div>

    <!-- ── Player stage ─────────────────────────────────────────────────
         ONE <video> for the whole shell: the element is only re-styled, never
         unmounted, so the playback position survives every tab switch and the
         mini-player. -->
    <div :class="stageClass">
      <video
        ref="video"
        class="h-full w-full bg-black object-contain"
        playsinline
        preload="metadata"
        :src="videoSrc"
        :aria-label="currentEpisode.title"
        @click="togglePlay"
        @play="playing = true"
        @pause="playing = false"
        @ended="playing = false"
      ></video>

      <div
        v-if="!videoSrc"
        class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 bg-base-300/90 px-2 text-center"
      >
        <i class="fa-solid fa-circle-play text-2xl opacity-30" aria-hidden="true"></i>
        <p class="text-[0.65rem] opacity-60">No media rendered yet</p>
      </div>

      <!-- Full player controls (over the video). -->
      <template v-if="playerOpen">
        <button
          type="button"
          class="absolute right-2 top-2 flex h-11 w-11 items-center justify-center rounded-full bg-black/50 text-white"
          aria-label="Minimise player"
          @click="closePlayer()"
        >
          <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>
        </button>
        <button
          v-if="videoSrc"
          type="button"
          class="absolute bottom-2 left-2 flex h-11 w-11 items-center justify-center rounded-full bg-black/50 text-white"
          :aria-label="playing ? 'Pause' : 'Play'"
          @click="togglePlay"
        >
          <i :class="playing ? 'fa-solid fa-pause' : 'fa-solid fa-play'" aria-hidden="true"></i>
        </button>
      </template>

      <!-- Mini-player controls (thumbnail, above the tab bar). -->
      <div
        v-else
        class="pointer-events-none absolute inset-0 flex items-end justify-between gap-1 bg-gradient-to-t from-black/70 to-transparent p-1"
      >
        <button
          type="button"
          class="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full bg-black/60 text-white"
          :aria-label="playing ? 'Pause' : 'Play'"
          @click="togglePlay"
        >
          <i :class="playing ? 'fa-solid fa-pause' : 'fa-solid fa-play'" aria-hidden="true"></i>
        </button>
        <button
          type="button"
          class="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full bg-black/60 text-white"
          aria-label="Expand player"
          @click="openPlayer()"
        >
          <i class="fa-solid fa-up-right-and-down-left-from-center text-xs" aria-hidden="true"></i>
        </button>
        <button
          type="button"
          class="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full bg-black/60 text-white"
          aria-label="Close player"
          @click="dismissPlayer()"
        >
          <i class="fa-solid fa-xmark" aria-hidden="true"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  components: { WikiView },
  props: [],
  data() {
    return {
      // Episode currently loaded in the (single) <video>. Kept in memory so the
      // mini-player survives closing the overlay; `?episode=` drives the open
      // state, which makes the player a deep link like the tabs.
      episode: null,
      playing: false,
      episodePlaceholders: [1, 2, 3, 4],
      shortPlaceholders: [1, 2, 3, 4, 5, 6],
      // The five sections of the channel. `?tab=` holds one of these ids.
      tabs: [
        {
          id: 'home',
          label: 'Home',
          icon: 'fa-solid fa-house',
          title: 'Home',
          description: 'The latest episode, what changed and the newest shorts.',
          empty: 'Nothing published yet.'
        },
        {
          // No placeholder fields: this tab renders <WikiView>, the same
          // documentation browser served by "/:projectName/wiki".
          id: 'library',
          label: 'Library',
          icon: 'fa-solid fa-book-open'
        },
        {
          id: 'episodes',
          label: 'Episodes',
          icon: 'fa-solid fa-film',
          title: 'Episodes',
          description: 'Every rendered episode of this project, newest first.',
          empty: 'No episodes have been generated yet.'
        },
        {
          id: 'shorts',
          label: 'Shorts',
          icon: 'fa-solid fa-mobile-screen',
          title: 'Shorts',
          description: 'Vertical 9:16 clips: what changed this week, in under a minute.',
          empty: 'No shorts have been generated yet.'
        },
        {
          id: 'learn',
          label: 'Learn',
          icon: 'fa-solid fa-graduation-cap',
          title: 'Learn',
          description: 'Ask about what you just watched, scoped to this page or episode.',
          empty: 'The teaching assistant is not wired yet.'
        }
      ]
    }
  },
  computed: {
    // Name shown in the header: the active project's channel.
    channelName() {
      const project = this.$storex.projects.activeProject
      return project?.title || project?.project_name || 'Channel'
    },
    // The tab selected in the URL (?tab=), falling back to Home so an unknown
    // or missing value never renders an empty screen.
    activeTab() {
      const tab = this.$route.query.tab
      return this.isKnownTab(tab) ? tab : 'home'
    },
    tabMeta() {
      return this.tabs.find(tab => tab.id === this.activeTab) || this.tabs[0]
    },
    // The player is open while "?episode=" is in the URL (shareable/back-safe).
    playerOpen() {
      return Boolean(this.$route.query.episode)
    },
    // Mini-player: an episode is loaded but the overlay is closed.
    miniVisible() {
      return !this.playerOpen && Boolean(this.currentEpisode)
    },
    // Deep links carry only the id, so fall back to a stub until the media API
    // is wired: the shell still shows its chapters/transcript layout.
    currentEpisode() {
      if (this.episode) return this.episode
      const id = this.$route.query.episode
      if (!id) return null
      return {
        id,
        title: 'Episode',
        subtitle: 'Rendered by the media worker — layout only in this build.',
        src: null,
        chapters: [],
        transcript: []
      }
    },
    // An <video> without src must not get src="": Vue drops undefined attrs.
    videoSrc() {
      return (this.currentEpisode && this.currentEpisode.src) || undefined
    },
    // One element, two layouts: full-bleed while the overlay is open, a
    // thumbnail above the tab bar while the user browses other tabs.
    stageClass() {
      if (this.playerOpen) {
        return 'fixed inset-x-0 top-0 z-40 aspect-video w-full bg-black'
      }
      if (this.miniVisible) {
        return 'fixed bottom-[calc(3.75rem_+_env(safe-area-inset-bottom))] right-3 z-40 h-24 w-40 overflow-hidden rounded-xl bg-black shadow-lg ring-1 ring-base-content/20 md:bottom-4 md:right-4'
      }
      return 'hidden'
    }
  },
  watch: {
    // Deep links, back/forward and external pushes all land here.
    '$route.query.tab'() {
      // Nothing to fetch yet; the computed `activeTab` does the switching.
    },
    playerOpen(open) {
      // Leaving the overlay keeps playing (mini-player); nothing to do.
      if (open) this.playVideo()
    }
  },
  mounted() {
    // Normalise the URL once so a refresh on "/channel" and on
    // "/channel?tab=home" show the same thing and the tab is always shareable.
    if (!this.isKnownTab(this.$route.query.tab)) {
      this.selectTab('home')
    }
    // A deep link straight into the player still needs an episode object.
    if (this.$route.query.episode && !this.episode) {
      this.episode = this.currentEpisode
    }
  },
  methods: {
    isKnownTab(tab) {
      return typeof tab === 'string' && this.tabs.some(item => item.id === tab)
    },
    // Selecting a tab only rewrites the query: no navigation away from the
    // channel, and the panel stays mounted (the <video> keeps its position).
    selectTab(tab) {
      if (this.$route.query.tab === tab) {
        return
      }
      this.$router.replace({ query: { ...this.$route.query, tab } }).catch(() => {})
    },
    // Arrow keys move the snap rails one card at a time, like a pointer swipe.
    scrollRail(event, direction) {
      const rail = event.currentTarget
      const card = rail.querySelector('[data-rail-item]')
      const step = card ? card.getBoundingClientRect().width + 12 : rail.clientWidth
      rail.scrollBy({ left: direction * step, behavior: 'smooth' })
    },
    // Opens the full-bleed player for the given (or placeholder) episode.
    openPlayer(episode) {
      if (episode) {
        this.episode = episode
      }
      if (!this.episode) {
        this.episode = {
          id: 'latest',
          title: 'Latest episode',
          subtitle: 'The newest episode of this channel plays here.',
          src: null,
          chapters: [],
          transcript: []
        }
      }
      if (this.$route.query.episode === this.episode.id) {
        return
      }
      this.$router.replace({ query: { ...this.$route.query, episode: this.episode.id } }).catch(() => {})
    },
    // Minimise: the overlay closes but playback continues in the mini-player.
    closePlayer() {
      if (!this.$route.query.episode) return
      const query = { ...this.$route.query }
      delete query.episode
      this.$router.replace({ query }).catch(() => {})
    },
    // Close for good: drop the query and free the media element.
    dismissPlayer() {
      this.closePlayer()
      this.stopVideo()
      this.episode = null
    },
    playVideo() {
      const video = this.$refs.video
      if (!video || !this.videoSrc) return
      const result = video.play()
      if (result && typeof result.catch === 'function') {
        result.catch(() => { this.playing = false })
      }
    },
    stopVideo() {
      const video = this.$refs.video
      if (!video) return
      video.pause()
      this.playing = false
    },
    togglePlay() {
      if (!this.videoSrc) return
      if (this.playing) {
        this.stopVideo()
        return
      }
      this.playVideo()
    },
    // Chapters jump within the same element, so the mini-player follows.
    seekTo(at) {
      const video = this.$refs.video
      if (!video || !this.videoSrc) return
      const [minutes, seconds] = String(at).split(':').map(Number)
      video.currentTime = (minutes || 0) * 60 + (seconds || 0)
      this.playVideo()
    }
  }
}
</script>
