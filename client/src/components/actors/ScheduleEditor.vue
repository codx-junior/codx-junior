<template>
  <div class="flex flex-col gap-3">
    <label class="flex items-start gap-2 cursor-pointer">
      <input
        v-model="local.enabled"
        type="checkbox"
        class="checkbox checkbox-sm checkbox-primary mt-0.5"
        :disabled="disabled"
        @change="onEnabledChange"
      />
      <span class="text-xs">
        <b>Working hours only</b>
        <span class="block text-base-content/50">
          Outside these windows the actor stays idle and catches up at the next opening.
          Use it to keep autonomous work out of the hours where nobody can review it.
        </span>
      </span>
    </label>

    <template v-if="local.enabled">
      <div class="flex items-end gap-2">
        <div class="flex-1 min-w-[9rem]">
          <label class="label-text text-[11px] pb-1 block">Timezone</label>
          <input
            v-model="local.timezone"
            type="text"
            class="input input-sm input-bordered w-full"
            placeholder="Europe/Madrid"
            :disabled="disabled"
            @change="emitUpdate"
          />
        </div>
        <span class="text-[11px] text-base-content/40 pb-2">
          {{ openLabel }}
        </span>
      </div>

      <div
        v-for="(window, index) in local.windows"
        :key="index"
        class="rounded-lg border border-base-content/10 bg-base-200/50 p-2 flex flex-col gap-2"
      >
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-semibold text-base-content/60">
            Window {{ index + 1 }}
          </span>
          <span class="text-[10px] text-base-content/40">
            {{ window.end <= window.start ? 'crosses midnight' : '' }}
          </span>
          <button
            type="button"
            class="btn btn-ghost btn-xs btn-square ml-auto"
            title="Remove this window"
            :disabled="disabled"
            @click="removeWindow(index)"
          >
            <i class="fa-solid fa-trash text-[10px]"></i>
          </button>
        </div>

        <div class="flex flex-wrap gap-1">
          <button
            v-for="day in weekdays"
            :key="day.id"
            type="button"
            class="btn btn-xs"
            :class="(window.days || []).includes(day.id) ? 'btn-primary' : 'btn-ghost'"
            :disabled="disabled"
            @click="toggleDay(index, day.id)"
          >{{ day.label }}</button>
          <span v-if="!(window.days || []).length" class="text-[11px] text-base-content/40 self-center ml-1">
            every day
          </span>
        </div>

        <div class="flex items-end gap-2">
          <div class="flex-1">
            <label class="label-text text-[10px] pb-0.5 block">From</label>
            <input
              v-model="window.start"
              type="time"
              class="input input-xs input-bordered w-full"
              :disabled="disabled"
              @change="emitUpdate"
            />
          </div>
          <div class="flex-1">
            <label class="label-text text-[10px] pb-0.5 block">To</label>
            <input
              v-model="window.end"
              type="time"
              class="input input-xs input-bordered w-full"
              :disabled="disabled"
              @change="emitUpdate"
            />
          </div>
        </div>
      </div>

      <button
        type="button"
        class="btn btn-xs btn-ghost w-fit gap-1"
        :disabled="disabled"
        @click="addWindow"
      >
        <i class="fa-solid fa-plus"></i> Add window
      </button>

      <p v-if="!local.windows.length" class="text-[11px] text-warning flex items-start gap-1">
        <i class="fa-solid fa-triangle-exclamation mt-0.5"></i>
        <span>
          The schedule is enabled but has no window: the actor stays
          <b>paused</b> until you add one.
        </span>
      </p>
    </template>

    <p v-else class="text-[11px] text-base-content/40 flex items-center gap-1">
      <i class="fa-solid fa-infinity"></i>
      Available at any hour.
    </p>
  </div>
</template>

<script>
/**
 * Weekly availability editor of an actor.
 *
 * The backend model is `WeeklySchedule` (`api/codx/junior/autonomy/models.py`):
 *
 *     { enabled, timezone, windows: [{ days: ['mon', ...], start: 'HH:MM', end: 'HH:MM' }] }
 *
 * Conventions kept in sync with the backend:
 *   - `days` empty means **every day**;
 *   - `end <= start` is an **overnight** window (e.g. 22:00 → 06:00);
 *   - `enabled` with **no** window means the actor is paused.
 *
 * The component is self-contained (single `v-model`) so it can be reused by the
 * team generator and by the actor editor.
 *
 * @example
 * <ScheduleEditor v-model="form.schedule" :disabled="saving" />
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

/**
 * Return a clean, editable copy of a schedule.
 * @param {Object|null} schedule - Schedule coming from the API.
 * @returns {{enabled: boolean, timezone: string, windows: Object[]}}
 */
function normalize(schedule) {
  const source = schedule || {}
  return {
    enabled: !!source.enabled,
    timezone:
      source.timezone ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      'UTC',
    windows: (source.windows || []).map(window => ({
      days: [...(window.days || [])],
      start: window.start || '09:00',
      end: window.end || '18:00'
    }))
  }
}

export default {
  name: 'ScheduleEditor',
  props: {
    /** Current schedule (`{ enabled, timezone, windows }`). */
    modelValue: { type: Object, default: null },
    /** Disable every control (while saving). */
    disabled: { type: Boolean, default: false }
  },
  emits: ['update:modelValue'],
  data() {
    return {
      weekdays: WEEKDAYS,
      local: normalize(this.modelValue)
    }
  },
  computed: {
    /** Human summary shown next to the timezone field. */
    openLabel() {
      if (!this.local.enabled) return 'always available'
      if (!this.local.windows.length) return 'paused'
      return 'limited availability'
    }
  },
  watch: {
    // Re-sync only when the incoming value really differs, so typing in the
    // inputs is never overwritten by the parent re-render.
    modelValue: {
      deep: true,
      handler(value) {
        const next = normalize(value)
        if (JSON.stringify(next) !== JSON.stringify(this.local)) {
          this.local = next
        }
      }
    }
  },
  methods: {
    /** Push the current value to the parent. */
    emitUpdate() {
      this.$emit('update:modelValue', normalize(this.local))
    },
    /**
     * Enabling the schedule with no window would silently pause the actor, so a
     * sensible default (Mon-Fri 09:00-18:00) is created on the first enable.
     */
    onEnabledChange() {
      if (this.local.enabled && !this.local.windows.length) {
        this.local.windows = [this.defaultWindow()]
      }
      this.emitUpdate()
    },
    defaultWindow() {
      return {
        days: ['mon', 'tue', 'wed', 'thu', 'fri'],
        start: '09:00',
        end: '18:00'
      }
    },
    addWindow() {
      this.local.windows = [...this.local.windows, this.defaultWindow()]
      this.emitUpdate()
    },
    removeWindow(index) {
      this.local.windows = this.local.windows.filter((_, i) => i !== index)
      this.emitUpdate()
    },
    /**
     * Toggle a weekday in a window, keeping the week order and never leaving the
     * list empty (an empty list would mean "every day", the opposite intent).
     */
    toggleDay(index, day) {
      const window = this.local.windows[index]
      if (!window) return
      const days = window.days || []
      const next = days.includes(day)
        ? days.filter(item => item !== day)
        : WEEKDAYS.map(item => item.id).filter(id => id === day || days.includes(id))
      window.days = next
      this.emitUpdate()
    }
  }
}
</script>
