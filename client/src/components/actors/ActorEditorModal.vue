<template>
  <div class="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4">
    <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="$emit('close')"></div>

    <div
      class="relative w-full sm:max-w-2xl max-h-[92vh] bg-base-100 border border-base-content/10 rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden"
      role="dialog"
      aria-modal="true"
      aria-label="Configure autonomous profile"
    >
      <!-- Header -->
      <div class="shrink-0 flex items-start gap-2 px-4 py-3 border-b border-base-content/10">
        <div class="avatar shrink-0">
          <div class="w-9 h-9 rounded-xl">
            <img :src="avatar" :alt="entry.actor" />
          </div>
        </div>
        <div class="min-w-0">
          <h2 class="font-semibold leading-tight truncate">@{{ entry.actor }}</h2>
          <p class="text-[11px] text-base-content/50">
            How this teammate works on its own. Instructions and skills are edited from the member settings.
          </p>
        </div>
        <button type="button" class="btn btn-ghost btn-sm btn-square ml-auto shrink-0" aria-label="Close" @click="$emit('close')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- Body -->
      <div class="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-4">
        <!-- Role + behaviour -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="label-text text-xs font-semibold pb-1 block">Role</label>
            <select v-model="form.role" class="select select-sm select-bordered w-full">
              <option value="coworker">Coworker — owns a job end to end</option>
              <option value="manager">Manager — curates the boards</option>
            </select>
            <p class="text-[11px] text-base-content/40 mt-1">
              {{ form.role === 'manager'
                ? 'Reviews tasks, requests info and organises the board.'
                : 'Takes the job as its own work, like another teammate.' }}
            </p>
          </div>

          <div>
            <label class="label-text text-xs font-semibold pb-1 block">Tools it may use</label>
            <select v-model="form.policy" class="select select-sm select-bordered w-full">
              <option value="full">Everything the profile is granted</option>
              <option value="board_only">Board and task tools only</option>
            </select>
            <p class="text-[11px] text-base-content/40 mt-1">
              <code>board_only</code> keeps it away from code, shell and git.
            </p>
          </div>

          <div>
            <label class="label-text text-xs font-semibold pb-1 block">Session</label>
            <select v-model="form.session" class="select select-sm select-bordered w-full" :disabled="isManager">
              <option value="until_done">Work until the job is done</option>
              <option value="single">One turn per shift</option>
            </select>
            <p class="text-[11px] text-base-content/40 mt-1">
              {{ isManager
                ? 'A manager always takes a single review pass.'
                : 'Bounded by the turn limit, the shift budget and the daily budget.' }}
            </p>
          </div>

          <div>
            <label class="label-text text-xs font-semibold pb-1 block">Mode</label>
            <select v-model="form.mode" class="select select-sm select-bordered w-full">
              <option value="active">Active — acts on the boards</option>
              <option value="dry_run">Dry run — reports only</option>
            </select>
            <p class="text-[11px] text-base-content/40 mt-1">
              Use dry run to see what it would do before letting it write.
            </p>
          </div>
        </div>

        <!-- Boards -->
        <div class="rounded-xl border border-base-content/10 p-3 flex flex-col gap-3">
          <div class="flex items-center gap-2">
            <i class="fa-solid fa-table-columns text-primary text-xs"></i>
            <span class="text-xs font-semibold">Boards</span>
            <span class="text-[11px] text-base-content/40">their tasks are the agenda of every shift</span>
          </div>

          <div v-if="boards.length" class="flex flex-wrap gap-1">
            <button
              v-for="board in selectedBoards"
              :key="board"
              type="button"
              class="btn btn-xs btn-primary gap-1"
              :title="`Stop watching ${board}`"
              @click="toggleBoard(board)"
            >
              {{ board }}
              <i class="fa-solid fa-xmark text-[9px]"></i>
            </button>
            <span v-if="!selectedBoards.length" class="text-[11px] text-base-content/40">
              No board selected yet
            </span>
          </div>

          <!-- Boards are searched, not listed: a project can have hundreds -->
          <div class="flex items-end gap-2">
            <div class="flex-1 min-w-[8rem]">
              <label class="label-text text-[11px] pb-1 block">Add a board</label>
              <input
                v-model="boardQuery"
                type="search"
                class="input input-sm input-bordered w-full"
                :placeholder="boards.length ? `Search ${boards.length} boards…` : 'No boards found'"
              />
            </div>
            <div class="flex-1 min-w-[8rem]">
              <label class="label-text text-[11px] pb-1 block">or create a new one</label>
              <div class="join w-full">
                <input
                  v-model="newBoard"
                  type="text"
                  class="join-item input input-sm input-bordered w-full"
                  placeholder="Agent: agenda"
                  @keydown.enter.prevent="addTypedBoard"
                />
                <button
                  type="button"
                  class="join-item btn btn-sm btn-square"
                  title="Watch this new board"
                  :disabled="!newBoard.trim()"
                  @click="addTypedBoard"
                >
                  <i class="fa-solid fa-plus"></i>
                </button>
              </div>
            </div>
          </div>

          <div v-if="boardOptions.length" class="flex flex-wrap gap-1">
            <button
              v-for="board in boardOptions"
              :key="board.name"
              type="button"
              class="btn btn-xs btn-ghost gap-1"
              :title="board.columns.length ? `Columns: ${board.columns.join(', ')}` : board.name"
              @click="toggleBoard(board.name)"
            >
              <i class="fa-solid fa-plus text-[9px]"></i>
              {{ board.name }}
            </button>
          </div>
          <p v-else-if="boardQuery" class="text-[11px] text-base-content/40">
            No board matches “{{ boardQuery }}” — type it above to watch a new one.
          </p>

          <div v-if="form.boards.length">
            <label class="label-text text-[11px] pb-1 block">Desk board (where its shift reports are written)</label>
            <select v-model="form.board" class="select select-xs select-bordered w-full">
              <option value="">Same as the first board</option>
              <option v-for="name in form.boards" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>

          <div class="flex items-end gap-2 flex-wrap">
            <div class="flex-1 min-w-[8rem]">
              <label class="label-text text-[11px] pb-1 block">Check the boards every (minutes)</label>
              <input v-model.number="form.interval_minutes" type="number" min="5" step="5" class="input input-sm input-bordered w-full" />
            </div>
            <div class="flex-1 min-w-[8rem]">
              <label class="label-text text-[11px] pb-1 block">Due tasks per shift</label>
              <input v-model.number="form.max_agenda" type="number" min="1" max="50" class="input input-sm input-bordered w-full" />
            </div>
            <div class="flex-1 min-w-[8rem]">
              <label class="label-text text-[11px] pb-1 block">Tasks as context</label>
              <input v-model.number="form.max_tasks" type="number" min="0" max="300" class="input input-sm input-bordered w-full" />
            </div>
          </div>

          <label class="flex items-center gap-2 cursor-pointer">
            <input v-model="form.include_done" type="checkbox" class="checkbox checkbox-xs" />
            <span class="text-[11px] text-base-content/60">Include closed tasks as context</span>
          </label>
        </div>

        <!-- Budgets -->
        <div class="rounded-xl border border-base-content/10 p-3 flex flex-col gap-3">
          <div class="flex items-center gap-2">
            <i class="fa-solid fa-coins text-warning text-xs"></i>
            <span class="text-xs font-semibold">Budgets and limits</span>
            <span class="text-[11px] text-base-content/40">0 means "no cap"</span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div>
              <label class="label-text text-[11px] pb-1 block">Per shift</label>
              <input v-model.number="form.budget_cxjcoins" type="number" min="0" step="0.5" class="input input-sm input-bordered w-full" />
            </div>
            <div>
              <label class="label-text text-[11px] pb-1 block">Per day</label>
              <input v-model.number="form.daily_budget_cxjcoins" type="number" min="0" step="0.5" class="input input-sm input-bordered w-full" />
            </div>
            <div>
              <label class="label-text text-[11px] pb-1 block">Max minutes</label>
              <input v-model.number="form.max_runtime_minutes" type="number" min="1" max="240" class="input input-sm input-bordered w-full" />
            </div>
            <div>
              <label class="label-text text-[11px] pb-1 block">Max turns</label>
              <input v-model.number="form.max_turns" type="number" min="1" max="50" class="input input-sm input-bordered w-full" :disabled="isManager || form.session === 'single'" />
            </div>
          </div>
        </div>

        <!-- Weekly schedule -->
        <div class="rounded-xl border border-base-content/10 p-3 flex flex-col gap-2">
          <div class="flex items-center gap-2">
            <i class="fa-solid fa-calendar-week text-info text-xs"></i>
            <span class="text-xs font-semibold">Weekly schedule</span>
            <span v-if="scheduleOpen" class="badge badge-xs badge-success ml-auto">open now</span>
            <span v-else class="badge badge-xs badge-ghost ml-auto">closed now</span>
          </div>
          <ScheduleEditor v-model="form.schedule" :disabled="saving" />
        </div>

        <!-- Standing orders -->
        <div>
          <label class="label-text text-xs font-semibold pb-1 block">Standing orders</label>
          <textarea
            v-model="form.instructions"
            rows="3"
            class="textarea textarea-bordered textarea-sm w-full"
            placeholder="e.g. Every shift: check the open maintenance tickets, chase the ones waiting for an answer, and never close a ticket without a written conclusion."
          ></textarea>
          <p class="text-[11px] text-base-content/40 mt-1">
            Added to the profile mandate on every shift. The profile instructions still apply.
          </p>
        </div>

        <p v-if="error" class="text-xs text-error flex items-center gap-1">
          <i class="fa-solid fa-triangle-exclamation"></i> {{ error }}
        </p>
      </div>

      <!-- Footer -->
      <div class="shrink-0 flex items-center gap-2 px-4 py-3 border-t border-base-content/10">
        <span class="text-[11px] text-base-content/40 mr-auto hidden sm:block">
          <i class="fa-solid fa-circle-info"></i>
          {{ state.shifts || 0 }} shift(s) · spent today {{ money(entry.daily_spent) }}
        </span>
        <button type="button" class="btn btn-sm btn-ghost" :disabled="saving" @click="$emit('close')">Cancel</button>
        <button type="button" class="btn btn-sm btn-primary gap-1" :disabled="saving" @click="submit">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-floppy-disk"></i>
          Save
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import ScheduleEditor from './ScheduleEditor.vue'
import { avatarUrl } from '@/components/team-view/config'

/**
 * Editor of the `actor` block of a profile.
 *
 * The dialog is **controlled**: it builds the patch and emits `save`, the parent
 * (`ActorsPanel`) calls `PUT /api/actors/{profile}`. That keeps the API call and
 * the busy state in one place and lets the same editor be used elsewhere.
 *
 * The weekly schedule is delegated to `ScheduleEditor`.
 */
export default {
  name: 'ActorEditorModal',
  components: { ScheduleEditor },
  props: {
    /** Entry of `GET /api/actors/status`. */
    entry: { type: Object, required: true },
    /** Boards of the project (`GET /api/actors/boards`). */
    boards: { type: Array, default: () => [] },
    /** Disable the form while the parent saves. */
    saving: { type: Boolean, default: false }
  },
  emits: ['close', 'save'],
  data() {
    const config = this.entry.config || {}
    return {
      error: '',
      boardQuery: '',
      newBoard: '',
      form: {
        role: config.role || 'coworker',
        policy: config.policy || 'full',
        session: config.session || 'single',
        mode: config.mode || 'active',
        board: config.board || '',
        boards: [...(config.boards || [])],
        interval_minutes: Number(config.interval_minutes || 60),
        budget_cxjcoins: Number(config.budget_cxjcoins || 0),
        daily_budget_cxjcoins: Number(config.daily_budget_cxjcoins || 0),
        max_runtime_minutes: Number(config.max_runtime_minutes || 20),
        max_turns: Number(config.max_turns || 5),
        max_agenda: Number(config.max_agenda || 5),
        max_tasks: Number(config.max_tasks || 40),
        include_done: !!config.include_done,
        instructions: config.instructions || '',
        schedule: config.schedule || { enabled: false, timezone: '', windows: [] }
      }
    }
  },
  computed: {
    state() {
      return this.entry.state || {}
    },
    isManager() {
      return this.form.role === 'manager'
    },
    scheduleOpen() {
      return !!this.entry.schedule?.open
    },
    /** Boards currently watched (rendered as removable chips). */
    selectedBoards() {
      return this.form.boards
    },
    /** Available boards matching the search, excluding the ones already picked. */
    boardOptions() {
      const query = (this.boardQuery || '').trim().toLowerCase()
      return this.boards
        .filter(board => !this.form.boards.includes(board.name))
        .filter(board => !query || board.name.toLowerCase().includes(query))
        .slice(0, 24)
    },
    avatar() {
      return avatarUrl({ name: this.entry.actor, avatar: this.entry.avatar })
    }
  },
  watch: {
    // A manager never runs a multi-turn session: keep the form honest.
    isManager(manager) {
      if (manager) this.form.session = 'single'
    }
  },
  methods: {
    /**
     * Watch a board typed by the user: an actor can own a board that does not
     * exist yet (it is created on the first shift).
     */
    addTypedBoard() {
      const name = (this.newBoard || '').trim()
      if (!name) return
      if (!this.form.boards.includes(name)) {
        this.form.boards = [...this.form.boards, name]
      }
      if (!this.form.board) this.form.board = name
      this.newBoard = ''
    },
    toggleBoard(name) {
      const boards = this.form.boards
      this.form.boards = boards.includes(name)
        ? boards.filter(item => item !== name)
        : [...boards, name]
      // The desk board must be one of the watched boards.
      if (!this.form.boards.includes(this.form.board)) {
        this.form.board = this.form.boards[0] || ''
      }
    },
    /** Validate, normalise and hand the patch to the parent. */
    submit() {
      if (!this.form.boards.length) {
        this.error = 'Pick at least one board: without a board the actor has nothing to work on.'
        return
      }
      this.error = ''
      this.$emit('save', {
        role: this.form.role,
        policy: this.form.policy,
        session: this.form.session,
        mode: this.form.mode,
        board: this.form.board || this.form.boards[0],
        boards: this.form.boards,
        interval_minutes: Math.max(5, Number(this.form.interval_minutes) || 60),
        budget_cxjcoins: Math.max(0, Number(this.form.budget_cxjcoins) || 0),
        daily_budget_cxjcoins: Math.max(0, Number(this.form.daily_budget_cxjcoins) || 0),
        max_runtime_minutes: Math.max(1, Number(this.form.max_runtime_minutes) || 20),
        max_turns: Math.max(1, Number(this.form.max_turns) || 5),
        max_agenda: Math.max(1, Number(this.form.max_agenda) || 5),
        max_tasks: Math.max(0, Number(this.form.max_tasks) || 0),
        include_done: !!this.form.include_done,
        instructions: this.form.instructions || '',
        schedule: this.form.schedule
      })
    },
    money(value) {
      const amount = Number(value || 0)
      return amount < 0.01 ? amount.toFixed(4) : amount.toFixed(2)
    }
  }
}
</script>
