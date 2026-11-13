<template>
  <article
    class="card bg-base-200 border border-base-content/10 p-3 flex flex-col gap-2.5 transition-colors"
    :class="enabled ? 'border-base-content/20' : 'opacity-70'"
  >
    <!-- Identity + on/off -->
    <div class="flex items-start gap-2.5">
      <div class="avatar shrink-0">
        <div class="w-10 h-10 rounded-xl" :class="enabled ? 'ring-2 ring-primary/40' : ''">
          <img :src="avatar" :alt="entry.actor" />
        </div>
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-1.5">
          <span class="font-semibold text-sm truncate" :title="entry.actor">@{{ entry.actor }}</span>
          <span class="badge badge-xs shrink-0" :class="roleBadge">
            <i class="fa-solid" :class="roleIcon"></i>
            {{ entry.role }}
          </span>
          <span
            v-if="isDryRun"
            class="badge badge-xs badge-warning shrink-0"
            title="Dry run: it reports what it would do but never uses the tools"
          >dry run</span>
        </div>
        <p class="text-[11px] text-base-content/50 line-clamp-2" :title="entry.description">
          {{ entry.description || 'No description' }}
        </p>
      </div>

      <button
        type="button"
        class="btn btn-xs shrink-0"
        :class="enabled ? 'btn-success' : 'btn-ghost'"
        :title="enabled ? 'Pause this actor' : 'Enable this actor'"
        :disabled="busy"
        @click="$emit('toggle', entry)"
      >
        <span v-if="busy" class="loading loading-spinner loading-xs"></span>
        <i v-else class="fa-solid" :class="enabled ? 'fa-pause' : 'fa-play'"></i>
        <span class="hidden sm:inline">{{ enabled ? 'On' : 'Off' }}</span>
      </button>
    </div>

    <!-- Schedule: the weekly window and whether it is open right now -->
    <div class="flex items-center gap-1.5 text-[11px] flex-wrap">
      <span
        class="badge badge-xs gap-1 shrink-0"
        :class="scheduleOpen ? 'badge-success' : 'badge-ghost'"
        :title="scheduleTitle"
      >
        <i class="fa-solid fa-clock"></i>
        {{ scheduleOpen ? 'open now' : 'closed now' }}
      </span>
      <span class="text-base-content/60 truncate" :title="scheduleTitle">{{ scheduleLabel }}</span>
      <span v-if="entry.schedule?.timezone" class="text-base-content/30 shrink-0">
        · {{ entry.schedule.timezone }}
      </span>
    </div>

    <!-- Boards it is responsible for -->
    <div v-if="boards.length" class="flex items-center gap-1 flex-wrap">
      <span class="text-[10px] text-base-content/40 shrink-0">
        <i class="fa-solid fa-table-columns"></i>
      </span>
      <span
        v-for="board in boards"
        :key="board"
        class="badge badge-xs badge-outline truncate max-w-[10rem]"
        :title="board === entry.board ? `${board} (desk)` : board"
      >
        <i class="fa-solid fa-thumbtack text-[8px] mr-0.5" v-if="board === entry.board"></i>
        {{ board }}
      </span>
    </div>
    <div v-else class="text-[11px] text-base-content/30">
      <i class="fa-solid fa-table-columns"></i> no board yet — created on the first shift
    </div>

    <!-- Runtime: next shift, spend and the last outcome -->
    <div class="flex items-center gap-1.5 flex-wrap text-[11px]">
      <span class="badge badge-xs badge-ghost gap-1" :title="lastShiftTitle">
        <i class="fa-solid fa-bolt"></i> {{ state.shifts || 0 }}
      </span>
      <span class="badge badge-xs badge-ghost gap-1" title="Spent today (UTC)">
        <i class="fa-solid fa-coins"></i> {{ money(dailySpent) }}
        <template v-if="dailyBudget">/{{ money(dailyBudget) }}</template>
      </span>
      <span class="badge badge-xs gap-1" :class="statusBadge" :title="statusTitle">
        <i class="fa-solid" :class="statusIcon"></i> {{ statusLabel }}
      </span>
      <span class="text-base-content/40 ml-auto shrink-0" :title="nextWakeTitle">
        <i class="fa-solid fa-hourglass-half"></i> {{ nextWakeLabel }}
      </span>
    </div>

    <!-- Actions -->
    <div class="flex items-center gap-1 pt-1 border-t border-base-content/10">
      <button
        type="button"
        class="btn btn-xs btn-ghost gap-1"
        title="Take a shift as soon as possible"
        :disabled="busy || state.running"
        @click="$emit('wake', entry)"
      >
        <i class="fa-solid fa-play"></i> Wake now
      </button>
      <button
        type="button"
        class="btn btn-xs btn-ghost gap-1"
        title="Edit the autonomy of this profile"
        @click="$emit('edit', entry)"
      >
        <i class="fa-solid fa-sliders"></i> Configure
      </button>
      <button
        type="button"
        class="btn btn-xs btn-ghost gap-1 ml-auto"
        title="Open the profile settings (instructions, tools, knowledge)"
        @click="$emit('open-profile', entry)"
      >
        <i class="fa-solid fa-arrow-up-right-from-square"></i>
      </button>
    </div>
  </article>
</template>

<script>
import { avatarUrl } from '@/components/team-view/config'

/**
 * One actor of the project, summarised.
 *
 * The card is **presentational**: it renders the entry returned by
 * `GET /api/actors/status` and emits intents (`toggle`, `wake`, `edit`,
 * `open-profile`). The parent (`ActorsPanel`) owns the API calls, so the same
 * card can be reused in a drawer or a dashboard.
 */
export default {
  name: 'ActorCard',
  props: {
    /** Entry of `GET /api/actors/status` (`actor`, `config`, `state`, ...). */
    entry: { type: Object, required: true },
    /** A request for this actor is in flight: show spinners and block actions. */
    busy: { type: Boolean, default: false }
  },
  emits: ['toggle', 'wake', 'edit', 'open-profile'],
  computed: {
    config() {
      return this.entry.config || {}
    },
    state() {
      return this.entry.state || {}
    },
    enabled() {
      return !!this.config.enabled
    },
    isDryRun() {
      return this.config.mode === 'dry_run'
    },
    isManager() {
      return this.config.role === 'manager'
    },
    roleBadge() {
      return this.isManager ? 'badge-info' : 'badge-primary'
    },
    roleIcon() {
      return this.isManager ? 'fa-user-tie' : 'fa-user-astronaut'
    },
    avatar() {
      return avatarUrl({ name: this.entry.actor, avatar: this.entry.avatar })
    },
    boards() {
      return this.entry.boards || []
    },
    dailySpent() {
      return Number(this.entry.daily_spent || 0)
    },
    dailyBudget() {
      return Number(this.config.daily_budget_cxjcoins || 0)
    },
    scheduleOpen() {
      return !!this.entry.schedule?.open
    },
    scheduleLabel() {
      const schedule = this.entry.schedule || {}
      if (!schedule.enabled) return 'any hour'
      return schedule.description || 'limited availability'
    },
    scheduleTitle() {
      const schedule = this.entry.schedule || {}
      const windows = schedule.windows || []
      if (!schedule.enabled) return 'No working-hours restriction: available any time'
      if (!windows.length) return 'Schedule enabled without windows: the actor is paused'
      return windows
        .map(window => `${(window.days || []).join(',') || 'every day'} ${window.start}-${window.end}`)
        .join(' · ')
    },
    statusLabel() {
      if (this.state.running) return 'on shift'
      if (this.state.wake_requested) return 'wake requested'
      const status = this.state.last_status || ''
      return {
        ok: 'last: ok',
        no_work: 'last: no work',
        skipped: 'last: skipped',
        error: 'last: error',
        budget_exhausted: 'last: budget'
      }[status] || 'no shift yet'
    },
    statusIcon() {
      if (this.state.running) return 'fa-spinner fa-spin'
      if (this.state.wake_requested) return 'fa-bell'
      return {
        ok: 'fa-circle-check',
        no_work: 'fa-moon',
        skipped: 'fa-forward',
        error: 'fa-triangle-exclamation',
        budget_exhausted: 'fa-coins'
      }[this.state.last_status || ''] || 'fa-circle-minus'
    },
    statusBadge() {
      if (this.state.running) return 'badge-info'
      if (this.state.wake_requested) return 'badge-warning'
      return {
        ok: 'badge-success',
        no_work: 'badge-ghost',
        skipped: 'badge-ghost',
        error: 'badge-error',
        budget_exhausted: 'badge-warning'
      }[this.state.last_status || ''] || 'badge-ghost'
    },
    statusTitle() {
      const parts = []
      if (this.state.last_stop_reason) parts.push(`stopped: ${this.state.last_stop_reason}`)
      if (this.state.last_summary) parts.push(this.state.last_summary)
      if (this.state.last_error) parts.push(this.state.last_error)
      return parts.join(' · ') || 'No shift yet'
    },
    lastShiftTitle() {
      const at = this.state.last_shift_at
      return at ? `${this.state.shifts || 0} shift(s) · last ${this.when(at)}` : 'No shift yet'
    },
    nextWakeLabel() {
      const next = this.entry.next_wake_at
      if (!next) return this.entry.due ? 'due now' : 'not scheduled'
      return this.when(next)
    },
    nextWakeTitle() {
      return this.entry.next_wake_at
        ? `Next shift: ${this.entry.next_wake_at}`
        : 'No next shift planned'
    }
  },
  methods: {
    /** Compact relative label of an ISO instant ("in 2h", "5m ago"). */
    when(iso) {
      const date = new Date(iso)
      if (Number.isNaN(date.getTime())) return iso
      const diff = date.getTime() - Date.now()
      const abs = Math.abs(diff)
      const minutes = Math.round(abs / 60000)
      if (minutes < 1) return diff >= 0 ? 'in a moment' : 'just now'
      const text = minutes < 60
        ? `${minutes}m`
        : minutes < 1440
          ? `${Math.round(minutes / 60)}h`
          : `${Math.round(minutes / 1440)}d`
      return diff >= 0 ? `in ${text}` : `${text} ago`
    },
    money(value) {
      const amount = Number(value || 0)
      if (!amount) return '0'
      return amount < 0.01 ? amount.toFixed(4) : amount.toFixed(2)
    }
  }
}
</script>
