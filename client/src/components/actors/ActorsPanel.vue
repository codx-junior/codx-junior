<template>
  <div class="flex flex-col h-full min-h-0">
    <!-- Summary + actions -->
    <div class="shrink-0 flex items-center gap-2 flex-wrap pb-2">
      <div class="flex items-center gap-1.5 rounded-lg bg-base-200 border border-base-content/10 px-2 py-1" title="Autonomous profiles">
        <i class="fa-solid fa-robot text-primary text-[11px]"></i>
        <span class="text-sm font-bold leading-none">{{ stats.total }}</span>
        <span class="hidden md:inline text-[11px] text-base-content/50">Autonomous</span>
      </div>
      <div class="flex items-center gap-1.5 rounded-lg bg-base-200 border border-base-content/10 px-2 py-1" title="Enabled actors">
        <i class="fa-solid fa-play text-success text-[11px]"></i>
        <span class="text-sm font-bold leading-none">{{ stats.enabled }}</span>
        <span class="hidden md:inline text-[11px] text-base-content/50">Enabled</span>
      </div>
      <div class="flex items-center gap-1.5 rounded-lg bg-base-200 border border-base-content/10 px-2 py-1" title="Inside their working hours right now">
        <i class="fa-solid fa-clock text-info text-[11px]"></i>
        <span class="text-sm font-bold leading-none">{{ stats.openNow }}</span>
        <span class="hidden md:inline text-[11px] text-base-content/50">On duty now</span>
      </div>
      <div class="flex items-center gap-1.5 rounded-lg bg-base-200 border border-base-content/10 px-2 py-1" title="Spent today (UTC)">
        <i class="fa-solid fa-coins text-warning text-[11px]"></i>
        <span class="text-sm font-bold leading-none">{{ money(stats.spentToday) }}</span>
        <span class="hidden md:inline text-[11px] text-base-content/50">Today</span>
      </div>

      <button
        type="button"
        class="btn btn-sm btn-ghost gap-1 ml-auto"
        title="Reload actors"
        :disabled="loading || saving"
        @click="load()"
      >
        <i class="fa-solid fa-rotate" :class="loading ? 'animate-spin' : ''"></i>
      </button>
      <button
        type="button"
        class="btn btn-sm btn-primary gap-1"
        title="Generate autonomous teammates from a need"
        @click="$emit('generate')"
      >
        <i class="fa-solid fa-wand-magic-sparkles"></i>
        <span class="hidden md:inline">Generate</span>
      </button>
    </div>

    <!-- Toolbar: search, filter and "make an existing profile autonomous" -->
    <div class="shrink-0 flex items-center gap-2 flex-wrap pb-3">
      <div class="relative flex-1 min-w-[10rem]">
        <span class="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40">
          <i class="fa-solid fa-magnifying-glass text-xs"></i>
        </span>
        <input
          v-model="search"
          type="search"
          class="input input-sm input-bordered w-full pl-9"
          placeholder="Search autonomous profiles…"
          aria-label="Search autonomous profiles"
        />
      </div>

      <select v-model="filter" class="select select-sm select-bordered shrink-0" aria-label="Filter actors">
        <option value="all">All</option>
        <option value="enabled">Enabled</option>
        <option value="paused">Paused</option>
        <option value="closed">Outside working hours</option>
      </select>

      <div class="join shrink-0">
        <select v-model="profileToAdd" class="join-item select select-sm select-bordered max-w-[12rem]" aria-label="Profile to make autonomous">
          <option value="">Make a profile autonomous…</option>
          <option v-for="profile in availableProfiles" :key="profile.name" :value="profile.name">
            {{ profile.name }}
          </option>
        </select>
        <button
          type="button"
          class="join-item btn btn-sm gap-1"
          :disabled="!profileToAdd || saving"
          title="Give this profile an actor block (it starts paused on its own board)"
          @click="enableProfile(profileToAdd)"
        >
          <i class="fa-solid fa-plus"></i>
        </button>
      </div>
    </div>

    <!-- Worker warning: nothing runs while the background worker is down -->
    <div
      v-if="!loading && !workerPresent && entries.length"
      class="shrink-0 alert alert-warning py-2 px-3 text-xs mb-2"
      role="status"
    >
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span>
        The background worker is not running: requested shifts stay queued until it comes back.
      </span>
    </div>

    <!-- Body -->
    <div class="flex-1 min-h-0 overflow-auto pb-8">
      <!-- Loading -->
      <div v-if="loading" class="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-3">
        <div v-for="n in 3" :key="n" class="card bg-base-200 border border-base-content/10 p-3 gap-2">
          <div class="flex gap-2">
            <div class="skeleton w-10 h-10 rounded-xl"></div>
            <div class="flex-1 flex flex-col gap-2">
              <div class="skeleton h-3 w-2/5"></div>
              <div class="skeleton h-2 w-3/5"></div>
            </div>
          </div>
          <div class="skeleton h-2 w-full"></div>
          <div class="skeleton h-6 w-1/2"></div>
        </div>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="flex flex-col items-center justify-center gap-3 py-16 text-center" role="alert">
        <div class="w-14 h-14 rounded-2xl grid place-items-center bg-error/15 text-error">
          <i class="fa-solid fa-triangle-exclamation text-2xl"></i>
        </div>
        <h2 class="font-semibold">We couldn't load the autonomous profiles</h2>
        <p class="text-sm text-base-content/50 max-w-md">{{ error }}</p>
        <button type="button" class="btn btn-sm btn-primary mt-1" @click="load()">
          <i class="fa-solid fa-rotate"></i> Retry
        </button>
      </div>

      <!-- Empty -->
      <div v-else-if="!entries.length" class="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div class="w-14 h-14 rounded-2xl grid place-items-center bg-primary/15 text-primary">
          <i class="fa-solid fa-robot text-2xl"></i>
        </div>
        <h2 class="font-semibold">No autonomous profiles yet</h2>
        <p class="text-sm text-base-content/50 max-w-md">
          An autonomous profile is a teammate that takes its own shifts: it reads its boards,
          works until the job is done (or its budget is over) and schedules its own follow-ups.
        </p>
        <div class="flex flex-wrap items-center justify-center gap-2 mt-1">
          <button type="button" class="btn btn-sm btn-primary gap-1" @click="$emit('generate')">
            <i class="fa-solid fa-wand-magic-sparkles"></i> Generate a team
          </button>
          <span class="text-[11px] text-base-content/40">
            or pick an existing profile above
          </span>
        </div>
      </div>

      <!-- No results -->
      <div v-else-if="!filtered.length" class="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <div class="w-14 h-14 rounded-2xl grid place-items-center bg-base-200 text-base-content/40">
          <i class="fa-solid fa-magnifying-glass text-2xl"></i>
        </div>
        <h2 class="font-semibold">No autonomous profile matches</h2>
        <p class="text-sm text-base-content/50">Try another search or filter.</p>
        <button type="button" class="btn btn-sm btn-ghost mt-1" @click="resetFilters">Clear filters</button>
      </div>

      <!-- Actors -->
      <div v-else class="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-3">
        <ActorCard
          v-for="entry in filtered"
          :key="entry.actor"
          :entry="entry"
          :busy="busyActor === entry.actor"
          @toggle="toggle"
          @wake="wake"
          @edit="openEditor"
          @open-profile="name => $emit('edit-member', name)"
        />
      </div>
    </div>

    <!-- Editor -->
    <ActorEditorModal
      v-if="editorEntry"
      :entry="editorEntry"
      :boards="boards"
      :saving="saving"
      @save="saveActor"
      @close="editorEntry = null"
    />
  </div>
</template>

<script>
import ActorCard from './ActorCard.vue'
import ActorEditorModal from './ActorEditorModal.vue'

/**
 * Autonomous profiles of the project, managed from the Team view.
 *
 * An **actor** is a profile with an `actor` block: a role (`coworker` |
 * `manager`), the boards it is responsible for, a cadence, a weekly
 * availability window and budgets. This panel is the single management surface:
 *
 *   - list every actor with its live state (`GET /api/actors/status`);
 *   - enable / pause it (`PUT /api/actors/{profile}`);
 *   - request a shift now (`POST /api/actors/{profile}/wake`);
 *   - edit role, policy, session, boards, budgets and the weekly schedule;
 *   - turn an existing profile into an actor without touching its instructions.
 *
 * The panel is the only component that talks to the actors API: cards and the
 * editor are presentational and emit intents.
 */
export default {
  name: 'ActorsPanel',
  components: { ActorCard, ActorEditorModal },
  emits: ['generate', 'edit-member'],
  data() {
    return {
      loading: true,
      error: null,
      saving: false,
      /** Actor whose toggle/wake request is in flight. */
      busyActor: '',
      entries: [],
      defaults: {},
      workerPresent: false,
      boards: [],
      profiles: [],
      search: '',
      filter: 'all',
      profileToAdd: '',
      editorEntry: null,
      /** Poll timer: the state changes in the background worker. */
      timer: null
    }
  },
  computed: {
    /** The actors API of the active project (may be null while booting). */
    api() {
      return this.$project?.$api?.actors || null
    },
    filtered() {
      const query = (this.search || '').trim().toLowerCase()
      return this.entries.filter(entry => {
        const matchQuery = !query ||
          [entry.actor, entry.description, ...(entry.boards || [])]
            .join(' ')
            .toLowerCase()
            .includes(query)
        if (!matchQuery) return false
        const config = entry.config || {}
        switch (this.filter) {
          case 'enabled':
            return !!config.enabled
          case 'paused':
            return !config.enabled
          case 'closed':
            return !!config.enabled && !entry.schedule?.open
          default:
            return true
        }
      })
    },
    stats() {
      return {
        total: this.entries.length,
        enabled: this.entries.filter(entry => entry.config?.enabled).length,
        openNow: this.entries.filter(entry => entry.config?.enabled && entry.schedule?.open).length,
        spentToday: this.entries.reduce((total, entry) => total + Number(entry.daily_spent || 0), 0)
      }
    },
    /** Profiles that are not actors yet. */
    availableProfiles() {
      const actorNames = new Set(this.entries.map(entry => String(entry.actor || '').toLowerCase()))
      return this.profiles
        .filter(profile => profile?.name && !actorNames.has(String(profile.name).toLowerCase()))
        .sort((a, b) => a.name.localeCompare(b.name))
    }
  },
  watch: {
    // Reload when the active project changes (mobile switch / desktop panel).
    $project() {
      this.load()
    }
  },
  created() {
    this.load()
    // Shifts are executed by the background worker: a light poll keeps the
    // "open now / next shift / last outcome" badges truthful.
    this.timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !this.loading && !this.saving) {
        this.load(true)
      }
    }, 30000)
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  },
  methods: {
    /**
     * Read actors, boards and profiles.
     * @param {boolean} [silent] - Refresh without the loading skeleton.
     */
    async load(silent = false) {
      if (!this.api) {
        this.loading = false
        return
      }
      if (!silent) this.loading = true
      this.error = null
      try {
        const [status, boards, profiles] = await Promise.all([
          this.api.status(),
          this.api.boards().catch(() => []),
          this.$project.$api.profiles.list().catch(() => [])
        ])
        this.entries = status?.actors || []
        this.defaults = status?.defaults || {}
        this.workerPresent = !!status?.worker_present
        this.boards = boards || []
        this.profiles = profiles || []
        // Keep the editor in sync with the refreshed entry.
        if (this.editorEntry) {
          this.editorEntry = this.entries.find(item => item.actor === this.editorEntry.actor) || null
        }
      } catch (e) {
        console.error('[ActorsPanel] Failed to load actors', e)
        this.error = e?.message || 'Failed to load the autonomous profiles'
      } finally {
        this.loading = false
      }
    },

    /** Pause/resume one actor. */
    async toggle(entry) {
      if (!this.api || this.busyActor) return
      const enabled = !entry.config?.enabled
      this.busyActor = entry.actor
      try {
        await this.api.save(entry.actor, { enabled })
        await this.load(true)
        this.$ui.addNotification({
          text: enabled
            ? `“${entry.actor}” is now autonomous`
            : `“${entry.actor}” is paused`,
          type: 'success'
        })
      } catch (e) {
        console.error('[ActorsPanel] toggle failed', e)
        this.$ui.addNotification({ text: e?.message || 'Could not update the actor', type: 'error' })
      } finally {
        this.busyActor = ''
      }
    },

    /** Ask for a shift as soon as possible (the worker executes it). */
    async wake(entry) {
      if (!this.api || this.busyActor) return
      this.busyActor = entry.actor
      try {
        await this.api.wake(entry.actor)
        await this.load(true)
        this.$ui.addNotification({
          text: `Shift requested for “${entry.actor}”`,
          type: 'success'
        })
      } catch (e) {
        console.error('[ActorsPanel] wake failed', e)
        this.$ui.addNotification({ text: e?.message || 'Could not request a shift', type: 'error' })
      } finally {
        this.busyActor = ''
      }
    },

    /** Open the actor editor of one entry. */
    openEditor(entry) {
      this.editorEntry = entry
    },

    /** Persist the patch built by `ActorEditorModal`. */
    async saveActor(patch) {
      if (!this.api || !this.editorEntry || this.saving) return
      const name = this.editorEntry.actor
      this.saving = true
      try {
        await this.api.save(name, patch)
        this.editorEntry = null
        await this.load(true)
        this.$ui.addNotification({ text: `Autonomy of “${name}” saved`, type: 'success' })
      } catch (e) {
        console.error('[ActorsPanel] save failed', e)
        this.$ui.addNotification({ text: e?.message || 'Could not save the actor', type: 'error' })
      } finally {
        this.saving = false
      }
    },

    /**
     * Give an existing profile an actor block.
     *
     * It is created **paused** and without a board on purpose: the user reviews
     * the configuration (boards, schedule, budget) before it takes any shift.
     */
    async enableProfile(name) {
      if (!this.api || !name || this.saving) return
      this.saving = true
      try {
        await this.api.save(name, { enabled: false })
        this.profileToAdd = ''
        await this.load(true)
        const entry = this.entries.find(item => item.actor === name)
        if (entry) this.editorEntry = entry
        this.$ui.addNotification({
          text: `“${name}” can now work on its own — review its autonomy and enable it`,
          type: 'success'
        })
      } catch (e) {
        console.error('[ActorsPanel] enableProfile failed', e)
        this.$ui.addNotification({ text: e?.message || 'Could not configure the profile', type: 'error' })
      } finally {
        this.saving = false
      }
    },

    resetFilters() {
      this.search = ''
      this.filter = 'all'
    },

    money(value) {
      const amount = Number(value || 0)
      if (!amount) return '0'
      return amount < 0.01 ? amount.toFixed(4) : amount.toFixed(2)
    }
  }
}
</script>
