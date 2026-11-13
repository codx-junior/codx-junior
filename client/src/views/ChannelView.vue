<script setup>
// Only imports belong here. The channel shell is a self-contained page for M0:
// it renders placeholders and does not talk to the media API yet.
// The Library tab reuses the existing documentation browser instead of a
// placeholder, so the wiki UI (tree, search, assistant) is never duplicated.
import WikiView from '@/views/WikiView.vue'
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-base-100 text-base-content">
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
          class="ml-auto flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-base-content/50 transition-colors hover:bg-base-content/5 hover:text-base-content"
          title="Open the wiki as a full page"
          aria-label="Open the wiki as a full page"
        >
          <i class="fa-solid fa-arrow-up-right-from-square text-xs" aria-hidden="true"></i>
        </RouterLink>
      </div>
    </header>

    <!-- ── Tab bar ────────────────────────────────────────────────────────
         Driven by ?tab= so every tab is a deep link that survives a refresh.
         Horizontal scroll keeps the five labels usable at 360 px, and every
         button keeps a 44×44 px touch target. -->
    <nav class="shrink-0 overflow-x-auto border-b border-base-content/10 px-2" aria-label="Channel sections">
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
      :class="activeTab === 'library' ? 'overflow-hidden' : 'overflow-y-auto'"
    >
      <!-- Home: hero + latest episodes skeleton + shorts rail. No data yet. -->
      <section v-if="activeTab === 'home'" class="flex flex-col gap-6 px-4 py-4">
        <!-- Hero reserved for the latest episode -->
        <div class="rounded-xl border border-dashed border-base-content/20 bg-base-200/40">
          <div class="flex aspect-video w-full flex-col items-center justify-center gap-2 text-center">
            <i class="fa-solid fa-circle-play text-3xl opacity-30" aria-hidden="true"></i>
            <p class="px-4 text-sm font-semibold">Latest episode</p>
            <p class="px-4 text-xs opacity-60">The newest episode of this channel will play here.</p>
          </div>
          <div class="border-t border-base-content/10 px-3 py-2 text-xs opacity-50">
            Nothing is published yet — this channel has no episodes.
          </div>
        </div>

        <!-- Latest episodes: skeleton cards -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Latest episodes</h2>
            <span class="text-xs opacity-40">0</span>
          </div>
          <div class="flex gap-3 overflow-x-auto pb-1">
            <div v-for="n in 4" :key="`ep-${n}`" class="w-40 shrink-0 animate-pulse">
              <div class="aspect-video w-full rounded-lg bg-base-content/10"></div>
              <div class="mt-2 h-2.5 w-4/5 rounded bg-base-content/10"></div>
              <div class="mt-1.5 h-2 w-1/2 rounded bg-base-content/10"></div>
            </div>
          </div>
        </div>

        <!-- Shorts: 9:16 rail -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-xs font-bold uppercase tracking-[0.18em] opacity-50">Shorts</h2>
            <span class="text-xs opacity-40">0</span>
          </div>
          <div class="flex gap-3 overflow-x-auto pb-1">
            <div v-for="n in 6" :key="`short-${n}`" class="w-24 shrink-0 animate-pulse">
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

      <!-- Episodes / Shorts / Learn: placeholder panels -->
      <section v-else class="px-4 py-4">
        <div class="rounded-xl border border-dashed border-base-content/20 bg-base-200/40 px-4 py-8 text-center">
          <i :class="[tabMeta.icon, 'text-2xl opacity-40']" aria-hidden="true"></i>
          <p class="mt-2 text-sm font-semibold">{{ tabMeta.title }}</p>
          <p class="mt-1 text-xs opacity-60">{{ tabMeta.description }}</p>
          <p class="mt-3 text-xs italic opacity-50">{{ tabMeta.empty }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script>
export default {
  components: { WikiView },
  props: [],
  data() {
    return {
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
    }
  },
  watch: {
    // Deep links, back/forward and external pushes all land here.
    '$route.query.tab'() {
      // Nothing to fetch yet; the computed `activeTab` does the switching.
    }
  },
  mounted() {
    // Normalise the URL once so a refresh on "/channel" and on
    // "/channel?tab=home" show the same thing and the tab is always shareable.
    if (!this.isKnownTab(this.$route.query.tab)) {
      this.selectTab('home')
    }
  },
  methods: {
    isKnownTab(tab) {
      return typeof tab === 'string' && this.tabs.some(item => item.id === tab)
    },
    // Selecting a tab only rewrites the query: no navigation away from the
    // channel, and the panel stays mounted.
    selectTab(tab) {
      if (this.$route.query.tab === tab) {
        return
      }
      this.$router.replace({ query: { ...this.$route.query, tab } }).catch(() => {})
    }
  }
}
</script>
