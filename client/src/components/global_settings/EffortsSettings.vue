<template>
  <div class="w-full flex flex-col @md:flex-row gap-4">
    <!-- Effort list -->
    <div class="w-full @md:w-72 shrink-0 flex flex-col gap-2">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-bold flex-1">Efforts</h3>
        <button class="btn btn-xs btn-primary gap-1" @click="newEffort">
          <i class="fa-solid fa-plus"></i> New
        </button>
      </div>

      <div class="alert py-2 text-[11px] bg-base-200 border-base-content/10">
        <i class="fa-solid fa-circle-info text-info"></i>
        <span>
          An effort is a named budget. Profiles are linked to an effort in the
          <strong>Team</strong> view; the values below are what that link applies.
        </span>
      </div>

      <div class="flex flex-col gap-1">
        <button
          v-for="effort in efforts"
          :key="effort.name"
          type="button"
          class="text-left p-2 rounded-lg border transition-colors"
          :class="draft.name === effort.name
            ? 'border-primary bg-primary/10'
            : 'border-base-content/10 hover:bg-base-200'"
          @click="edit(effort)"
        >
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold truncate">{{ effort.name }}</span>
            <span class="badge badge-xs badge-ghost">{{ effort.tier }}</span>
            <i
              v-if="effort.builtin"
              class="fa-solid fa-lock text-[10px] text-base-content/40 ml-auto"
              title="Built-in effort: editable, not deletable"
            ></i>
          </div>
          <div class="text-[10px] text-base-content/50 truncate">
            {{ effort.profiles.length }} profile{{ effort.profiles.length === 1 ? '' : 's' }}
          </div>
        </button>
      </div>
    </div>

    <!-- Editor -->
    <div class="flex-1 min-w-0 flex flex-col gap-4">
      <div v-if="error" class="alert alert-error py-2 text-xs">
        <i class="fa-solid fa-triangle-exclamation"></i>
        <span>{{ error }}</span>
      </div>

      <div class="flex flex-wrap items-end gap-2">
        <div class="flex-1 min-w-[12rem]">
          <label class="label-text text-xs font-semibold block mb-1">Name</label>
          <input
            class="input input-sm input-bordered w-full font-mono"
            :value="draft.name"
            :disabled="!isNew"
            placeholder="careful"
            @change="draft.name = $event.target.value"
          />
        </div>
        <div class="w-40">
          <label class="label-text text-xs font-semibold block mb-1">Base tier</label>
          <select
            class="select select-sm select-bordered w-full"
            :value="draft.tier"
            @change="draft.tier = $event.target.value"
          >
            <option v-for="tier in tiers" :key="tier" :value="tier">{{ tier }}</option>
          </select>
        </div>
        <button
          class="btn btn-sm btn-primary gap-1"
          :disabled="saving || !draft.name"
          @click="save"
        >
          <i class="fa-solid fa-floppy-disk" :class="saving ? 'animate-pulse' : ''"></i>
          {{ isNew ? 'Create' : 'Save' }}
        </button>
        <button
          class="btn btn-sm btn-ghost gap-1 text-error"
          :disabled="saving || isNew || draft.builtin"
          :title="draft.builtin ? 'Built-in efforts cannot be deleted' : 'Delete this effort'"
          @click="remove"
        >
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>

      <div>
        <label class="label-text text-xs font-semibold block mb-1">Description</label>
        <textarea
          class="textarea textarea-sm textarea-bordered w-full"
          rows="3"
          placeholder="What this effort is for: this is what the team view shows when someone links a profile to it."
          :value="draft.description"
          @change="draft.description = $event.target.value"
        ></textarea>
      </div>

      <div v-if="draft.profiles && draft.profiles.length" class="text-[11px] text-base-content/60">
        Linked profiles:
        <span class="font-mono">{{ draft.profiles.join(', ') }}</span>
      </div>

      <!-- Values, grouped, with the explanation of their effect -->
      <div v-for="group in groups" :key="group" class="flex flex-col gap-2">
        <div class="text-[11px] font-semibold text-base-content/60 uppercase tracking-wide">
          {{ group }}
        </div>

        <div
          v-for="field in fieldsOf(group)"
          :key="field.key"
          class="p-3 rounded-lg bg-base-200 border border-base-content/10"
        >
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold">{{ field.label }}</span>
            <span class="text-[10px] text-base-content/40 font-mono">{{ field.key }}</span>
            <span v-if="field.unit" class="text-[10px] text-base-content/40">({{ field.unit }})</span>
            <button
              class="btn btn-xs btn-ghost btn-circle ml-auto"
              :title="'What does this change?'"
              @click="toggleHelp(field.key)"
            >
              <i class="fa-solid fa-circle-question text-xs"></i>
            </button>
          </div>

          <p class="text-[11px] text-base-content/60 mt-1">{{ field.summary }}</p>

          <div class="mt-2">
            <div v-if="field.control === 'toggle'" class="flex items-center gap-2">
              <input
                type="checkbox"
                class="toggle toggle-sm toggle-primary"
                :checked="draft.values[field.key] === true"
                @change="setValue(field.key, $event.target.checked, 'toggle')"
              />
              <span class="text-[11px] text-base-content/50">empty = inherit the base tier</span>
            </div>
            <input
              v-else-if="field.control === 'list'"
              type="text"
              class="input input-sm input-bordered w-full font-mono"
              placeholder="empty = every enabled tool"
              :value="(draft.values[field.key] || []).join(', ')"
              @change="setValue(field.key, $event.target.value, 'list')"
            />
            <input
              v-else
              :type="field.control === 'number' ? 'number' : 'text'"
              class="input input-sm input-bordered w-full"
              :class="field.control === 'number' ? 'font-mono' : ''"
              placeholder="empty = inherit the base tier"
              :value="draft.values[field.key] ?? ''"
              @change="setValue(field.key, $event.target.value, field.control)"
            />
          </div>

          <div v-if="help[field.key]" class="mt-2 p-2 rounded bg-base-300/60 text-[11px] leading-relaxed">
            <strong>What it changes:</strong> {{ field.effect }}
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <button class="btn btn-xs btn-ghost gap-1" :disabled="saving" @click="clearValues">
          <i class="fa-solid fa-rotate-left"></i> Reset values to the base tier
        </button>
        <span class="text-[11px] text-base-content/50">
          {{ overrides }} value{{ overrides === 1 ? '' : 's' }} set
        </span>
        <button class="btn btn-xs btn-ghost gap-1 ml-auto" @click="load">
          <i class="fa-solid fa-rotate" :class="loading ? 'animate-spin' : ''"></i> Reload
        </button>
      </div>
    </div>
  </div>
</template>

<script>
/**
 * Advanced effort editor: the definitions profiles are linked to.
 *
 * The field documentation comes from the backend (`/api/efforts/fields`) so the
 * explanation of what each value does to a turn never drifts from the resolver
 * in `api/codx/junior/ai/smol/effort.py`.
 */
const EMPTY_DRAFT = () => ({
  name: '',
  description: '',
  tier: 'agile',
  values: {},
  builtin: false,
  profiles: []
})

export default {
  name: 'EffortsSettings',
  props: {
    /** Global settings object (kept for symmetry with the other panels). */
    settings: { type: Object, default: null }
  },
  data() {
    return {
      efforts: [],
      fields: [],
      groups: [],
      tiers: ['agile', 'max', 'default'],
      draft: EMPTY_DRAFT(),
      isNew: false,
      loading: false,
      saving: false,
      error: null,
      help: {}
    }
  },
  computed: {
    overrides() {
      return Object.values(this.draft.values || {}).filter(
        value => value !== null && value !== undefined && value !== '' &&
          !(Array.isArray(value) && !value.length)
      ).length
    }
  },
  watch: {
    settings: {
      immediate: true,
      handler() {
        this.load()
      }
    }
  },
  methods: {
    api() {
      return this.$project?.$api?.efforts
    },
    /** Load the definitions and the field documentation. */
    async load() {
      const api = this.api()
      if (!api || this.loading) return
      this.loading = true
      this.error = null
      try {
        const [efforts, docs] = await Promise.all([
          api.list(),
          this.fields.length ? Promise.resolve(null) : api.fields().catch(() => null)
        ])
        this.efforts = efforts || []
        if (docs) {
          this.fields = docs.fields || []
          this.groups = docs.groups || []
          this.tiers = docs.tiers || this.tiers
        }
        const current = this.efforts.find(item => item.name === this.draft.name)
        const next = current || this.efforts[0] || null
        if (next) this.edit(next)
        else this.newEffort()
      } catch (error) {
        this.error = error?.message || 'Could not load the efforts'
      } finally {
        this.loading = false
      }
    },
    fieldsOf(group) {
      return this.fields.filter(field => field.group === group)
    },
    toggleHelp(key) {
      this.help = { ...this.help, [key]: !this.help[key] }
    },
    edit(effort) {
      this.isNew = false
      this.draft = {
        name: effort.name,
        description: effort.description || '',
        tier: effort.tier || 'agile',
        values: { ...(effort.values || {}) },
        builtin: !!effort.builtin,
        profiles: effort.profiles || []
      }
    },
    newEffort() {
      this.isNew = true
      this.draft = EMPTY_DRAFT()
    },
    setValue(key, value, control) {
      const values = { ...this.draft.values }
      if (control === 'toggle') {
        values[key] = value
      } else if (control === 'list') {
        const list = String(value || '')
          .split(',')
          .map(item => item.trim())
          .filter(Boolean)
        values[key] = list.length ? list : null
      } else if (String(value).trim() === '') {
        values[key] = null
      } else if (control === 'number') {
        const parsed = Number(value)
        values[key] = Number.isNaN(parsed) ? null : parsed
      } else {
        values[key] = String(value)
      }
      this.draft = { ...this.draft, values }
    },
    clearValues() {
      this.draft = { ...this.draft, values: {} }
    },
    payload() {
      return {
        name: this.draft.name.trim(),
        description: this.draft.description || '',
        tier: this.draft.tier,
        values: this.draft.values || {}
      }
    },
    async save() {
      const api = this.api()
      if (!api || this.saving) return
      this.saving = true
      this.error = null
      try {
        const payload = this.payload()
        const saved = this.isNew
          ? await api.create(payload)
          : await api.save(payload.name, payload)
        await this.load()
        const fresh = this.efforts.find(item => item.name === saved.name)
        if (fresh) this.edit(fresh)
      } catch (error) {
        this.error = error?.message || 'Could not save the effort'
      } finally {
        this.saving = false
      }
    },
    async remove() {
      const api = this.api()
      if (!api || this.saving || !this.draft.name) return
      if (!window.confirm(`Delete the effort '${this.draft.name}'?`)) return
      this.saving = true
      this.error = null
      try {
        await api.delete(this.draft.name)
        this.draft = EMPTY_DRAFT()
        await this.load()
      } catch (error) {
        this.error = error?.message || 'Could not delete the effort'
      } finally {
        this.saving = false
      }
    }
  }
}
</script>

<!-- Made with ❤️ by codx-junior -->
