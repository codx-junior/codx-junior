<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="$emit('close')"></div>

    <div
      class="relative card bg-base-100 border border-base-content/10 shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label="Generate a team"
    >
      <div class="card-body gap-4">
        <div class="flex items-start justify-between gap-2">
          <div>
            <h2 class="card-title text-lg">
              <i class="fa-solid fa-wand-magic-sparkles text-primary"></i>
              Generate a team
            </h2>
            <p class="text-xs text-base-content/50">
              Describe the need in plain language. You will see who would be created
              before anything is written.
            </p>
          </div>
          <button type="button" class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="$emit('close')">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- 1. The need -->
        <div class="flex flex-col gap-2">
          <label class="label-text text-xs font-semibold">What do you need?</label>
          <textarea
            v-model="need"
            rows="2"
            class="textarea textarea-bordered textarea-sm w-full"
            placeholder="e.g. I need someone to take care of the maintenance tickets"
            @keydown.ctrl.enter="suggest"
          ></textarea>
          <div class="flex flex-wrap gap-1">
            <button
              v-for="example in examples"
              :key="example"
              type="button"
              class="btn btn-xs btn-ghost"
              @click="need = example"
            >{{ example }}</button>
          </div>
        </div>

        <!-- 2. Autonomy + availability -->
        <div class="rounded-xl border border-base-content/10 p-3 flex flex-col gap-3">
          <label class="flex items-start gap-2 cursor-pointer">
            <input v-model="autonomous" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
            <span class="text-xs">
              <b>Autonomous teammates</b>
              <span class="block text-base-content/50">
                Every generated profile gets an actor block: it works on its own board
                until the job is done or the daily budget is over, and schedules its own
                follow-ups.
              </span>
            </span>
          </label>

          <div v-if="autonomous" class="flex flex-col gap-3 border-t border-base-content/10 pt-3">
            <div>
              <label class="label-text text-xs font-semibold pb-1 block">Board they are responsible for</label>
              <select v-model="board" class="select select-sm select-bordered w-full">
                <option value="">Each teammate on its own board</option>
                <option v-for="item in boards" :key="item.name" :value="item.name">{{ item.name }}</option>
              </select>
            </div>

            <label class="flex items-start gap-2 cursor-pointer">
              <input v-model="schedule.enabled" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
              <span class="text-xs">
                <b>Working hours only</b>
                <span class="block text-base-content/50">
                  Keep the teammates out of the hours where nobody is around to review
                  their work. Outside the window they stay idle and catch up at the next
                  opening.
                </span>
              </span>
            </label>

            <template v-if="schedule.enabled">
              <div class="flex flex-wrap items-end gap-2">
                <div class="flex-1 min-w-[7rem]">
                  <label class="label-text text-[11px] pb-1 block">From</label>
                  <input v-model="window.start" type="time" class="input input-sm input-bordered w-full" />
                </div>
                <div class="flex-1 min-w-[7rem]">
                  <label class="label-text text-[11px] pb-1 block">To</label>
                  <input v-model="window.end" type="time" class="input input-sm input-bordered w-full" />
                </div>
                <div class="flex-1 min-w-[9rem]">
                  <label class="label-text text-[11px] pb-1 block">Timezone</label>
                  <input v-model="schedule.timezone" type="text" class="input input-sm input-bordered w-full" placeholder="Europe/Madrid" />
                </div>
              </div>
              <div class="flex flex-wrap gap-1">
                <button
                  v-for="day in weekdays"
                  :key="day.id"
                  type="button"
                  class="btn btn-xs"
                  :class="window.days.includes(day.id) ? 'btn-primary' : 'btn-ghost'"
                  @click="toggleDay(day.id)"
                >{{ day.label }}</button>
              </div>
            </template>
          </div>
        </div>

        <p v-if="error" class="text-xs text-error">
          <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ error }}
        </p>

        <!-- 3. Proposal -->
        <div v-if="suggestion" class="flex flex-col gap-2">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-bold">
              Proposal: {{ suggestion.team_name || 'Team' }}
            </h3>
            <span class="badge badge-sm">{{ suggestion.mode }}</span>
            <span v-if="suggestion.autonomous" class="badge badge-sm badge-primary">autonomous</span>
          </div>
          <ul class="flex flex-col divide-y divide-base-content/10 rounded-xl border border-base-content/10">
            <li
              v-for="role in suggestion.roles || []"
              :key="role.profile_name"
              class="flex items-start gap-2 p-2"
            >
              <i
                class="fa-solid mt-1 text-xs"
                :class="role.create ? 'fa-circle-plus text-success' : 'fa-recycle text-base-content/40'"
              ></i>
              <div class="min-w-0 flex-1">
                <div class="text-xs font-semibold truncate">
                  {{ role.profile_name }}
                  <span class="text-base-content/40 font-normal">· {{ role.role_id }}</span>
                </div>
                <div class="text-[11px] text-base-content/60">{{ role.description }}</div>
                <div v-if="role.actor && role.actor.enabled" class="text-[11px] text-primary/80">
                  <i class="fa-solid fa-robot mr-1"></i>
                  {{ role.actor.role }} · {{ role.actor.session }}
                  · {{ describeSchedule(role.actor.schedule) }}
                </div>
              </div>
              <span class="text-[11px] shrink-0" :class="role.create ? 'text-success' : 'text-base-content/40'">
                {{ role.create ? 'create' : 'reuse' }}
              </span>
            </li>
          </ul>
          <p v-if="suggestion.summary" class="text-[11px] text-base-content/50 whitespace-pre-line">{{ suggestion.summary }}</p>
        </div>

        <p v-if="result" class="text-xs text-success">
          <i class="fa-solid fa-circle-check mr-1"></i>
          Team <b>{{ result.team?.name }}</b> created — {{ (result.created_profiles || []).length }} profile(s) created,
          {{ (result.reused_profiles || []).length }} reused.
          <span v-if="result.errors && result.errors.length" class="text-warning block">
            {{ result.errors.join('; ') }}
          </span>
        </p>

        <div class="card-actions justify-end">
          <button type="button" class="btn btn-sm btn-ghost" @click="$emit('close')">Cancel</button>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="busy || !need.trim()"
            @click="suggest"
          >
            <span v-if="busy && !suggestion" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-lightbulb"></i>
            Suggest
          </button>
          <button
            type="button"
            class="btn btn-sm btn-primary"
            :disabled="busy || !suggestion || !!result"
            @click="create"
          >
            <span v-if="busy && suggestion" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-people-group"></i>
            Create team
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
/**
 * Generate a team from a plain-language need.
 *
 * Two round trips, on purpose:
 *   1. `POST /api/teams/generate/suggest` — a **proposal** (nothing is written),
 *      including the actor block each new profile would get;
 *   2. `POST /api/teams/generate/apply` — write the missing profiles + the team.
 *
 * Existing profiles are never modified by the generator: a role that already has
 * a profile is reused as-is.
 */
const WEEKDAYS = [
  { id: 'mon', label: 'Mon' },
  { id: 'tue', label: 'Tue' },
  { id: 'wed', label: 'Wed' },
  { id: 'thu', label: 'Thu' },
  { id: 'fri', label: 'Fri' },
  { id: 'sat', label: 'Sat' },
  { id: 'sun', label: 'Sun' }
]

export default {
  name: 'GenerateTeamDialog',
  emits: ['close', 'created'],
  data() {
    return {
      need: '',
      autonomous: false,
      board: '',
      boards: [],
      weekdays: WEEKDAYS,
      schedule: { enabled: false, timezone: '' },
      window: { days: ['mon', 'tue', 'wed', 'thu', 'fri'], start: '09:00', end: '18:00' },
      suggestion: null,
      result: null,
      busy: false,
      error: '',
      examples: [
        'I need an IT team',
        'I need someone to take care of the maintenance tickets',
        'I need a QA engineer'
      ]
    }
  },
  created() {
    this.schedule.timezone =
      Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    this.loadBoards()
  },
  computed: {
    /** Teams API of the active project (nothing is hardcoded). */
    teamsApi() {
      return this.$project?.$api?.teams
    }
  },
  methods: {
    async loadBoards() {
      try {
        const api = this.$project?.$api?.actors
        this.boards = api ? await api.boards() : []
      } catch (e) {
        console.warn('[GenerateTeamDialog] cannot load boards', e)
        this.boards = []
      }
    },
    toggleDay(day) {
      const days = this.window.days
      this.window.days = days.includes(day)
        ? days.filter(item => item !== day)
        : WEEKDAYS.map(item => item.id).filter(item => item === day || days.includes(item))
    },
    schedulePayload() {
      if (!this.autonomous || !this.schedule.enabled) return null
      return {
        enabled: true,
        timezone: this.schedule.timezone || 'UTC',
        windows: [{
          days: this.window.days,
          start: this.window.start,
          end: this.window.end
        }]
      }
    },
    describeSchedule(schedule) {
      if (!schedule || !schedule.enabled) return 'always available'
      const windows = schedule.windows || []
      if (!windows.length) return 'paused'
      return windows
        .map(w => `${(w.days || []).join(',') || 'every day'} ${w.start}-${w.end}`)
        .join('; ')
    },
    async suggest() {
      if (!this.need.trim() || this.busy) return
      if (!this.teamsApi) {
        this.error = 'Teams API not available'
        return
      }
      this.busy = true
      this.error = ''
      this.result = null
      try {
        this.suggestion = await this.teamsApi.suggestGeneration({
          need: this.need.trim(),
          autonomous: this.autonomous,
          board: this.board,
          schedule: this.schedulePayload()
        })
      } catch (e) {
        console.error('[GenerateTeamDialog] suggest failed', e)
        this.error = e?.message || 'Could not build a proposal'
        this.suggestion = null
      } finally {
        this.busy = false
      }
    },
    async create() {
      if (!this.suggestion || this.busy) return
      this.busy = true
      this.error = ''
      try {
        this.result = await this.teamsApi.applyGeneration({
          suggestion: this.suggestion
        })
        this.$ui.addNotification({
          text: `Team “${this.result.team?.name || this.suggestion.team_name}” created`,
          type: 'success'
        })
        this.$emit('created', this.result)
        this.$emit('close')
      } catch (e) {
        console.error('[GenerateTeamDialog] apply failed', e)
        this.error = e?.message || 'Could not create the team'
      } finally {
        this.busy = false
      }
    }
  }
}
</script>
