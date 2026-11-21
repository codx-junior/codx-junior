<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { API } from '@/api/api'

/**
 * Goal dialog — a business outcome, not a ticket.
 *
 * Creates (or edits) a company mission with a plain-language objective,
 * acceptance criteria, an owning team, a project, a deadline and a budget.
 * Everything is optional except the title and the team, so an owner can shape
 * a goal in under a minute and let the team refine it.
 */
const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  teamId: { type: String, default: '' },
  mission: { type: Object, default: null }
})

const emit = defineEmits(['close', 'saved'])

const api = API.companies
const saving = ref(false)
const error = ref('')

const teams = computed(() => props.snapshot?.teams || [])
const projects = computed(() => props.snapshot?.projects || [])
const editing = computed(() => Boolean(props.mission?.id))
const autonomy = computed(() => props.snapshot?.autonomy || {})
const selectedTeam = computed(() => teams.value.find(team => team.id === form.team_id))

/**
 * A goal created in an autonomous company (or for an autonomous team) starts
 * itself. Defaulting every new goal to "manual" is why goals created after the
 * autopilot was enabled never started: the autopilot only dispatches `auto`.
 */
function defaultDispatchMode() {
  const autonomyOn = Boolean(autonomy.value.enabled) && autonomy.value.level !== 'off'
  const team = selectedTeam.value
  return autonomyOn || (team && team.autonomy !== 'manual') ? 'auto' : 'manual'
}

const form = reactive({
  title: '',
  objective: '',
  acceptance: '',
  team_id: '',
  project_id: '',
  priority: 'medium',
  due_date: '',
  budget_cxjcoins: 0,
  dispatch_mode: 'manual',
  requires_approval: false,
  status: 'draft'
})

function reset() {
  const mission = props.mission || {}
  Object.assign(form, {
    title: mission.title || '',
    objective: mission.objective || '',
    acceptance: mission.acceptance || '',
    team_id: mission.team_id || props.teamId || teams.value[0]?.id || '',
    project_id: mission.project_id || '',
    priority: mission.priority || 'medium',
    due_date: (mission.due_date || '').slice(0, 10),
    budget_cxjcoins: Number(mission.budget_cxjcoins || 0),
    dispatch_mode: mission.dispatch_mode || defaultDispatchMode(),
    requires_approval: Boolean(mission.requires_approval),
    status: mission.status || 'draft'
  })
  error.value = ''
}

watch(() => [props.mission, props.teamId], reset, { immediate: true, deep: true })

function close() {
  if (saving.value) return
  emit('close')
}

async function save() {
  error.value = ''
  if (!form.title.trim()) {
    error.value = 'Give the goal a name'
    return
  }
  if (!form.team_id) {
    error.value = 'Choose the team that owns the goal'
    return
  }
  saving.value = true
  try {
    const payload = {
      title: form.title.trim(),
      objective: (form.objective || form.title).trim(),
      acceptance: form.acceptance.trim(),
      team_id: form.team_id,
      project_id: form.project_id,
      priority: form.priority,
      due_date: form.due_date,
      budget_cxjcoins: Number(form.budget_cxjcoins || 0),
      dispatch_mode: form.dispatch_mode,
      requires_approval: form.requires_approval,
      owner_kind: 'user',
      owner_name: ''
    }
    if (editing.value) {
      await api.updateMission(props.companyId, props.mission.id, payload)
    } else {
      await api.addMission(props.companyId, {
        ...payload,
        status: form.project_id ? 'ready' : 'draft'
      })
    }
    emit('saved')
    emit('close')
  } catch (ex) {
    error.value = ex?.response?.data?.detail || ex?.message || String(ex)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="modal modal-open z-[90]" role="dialog" aria-modal="true" aria-label="Goal">
    <div class="modal-box max-w-2xl overflow-hidden border border-base-content/10 bg-base-100 p-0">
      <div class="relative border-b border-base-content/10 bg-gradient-to-br from-primary/15 via-base-100 to-secondary/10 px-5 py-4">
        <div class="flex items-start gap-3">
          <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-content shadow-lg shadow-primary/20">
            <i class="fa-solid fa-flag"></i>
          </div>
          <div class="min-w-0 flex-1">
            <h3 class="text-base font-black">{{ editing ? 'Refine this goal' : 'Set a goal' }}</h3>
            <p class="text-[11px] text-base-content/50">
              A clear outcome, an owner and a deadline. The team turns it into work.
            </p>
          </div>
          <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </div>

      <div class="max-h-[70vh] overflow-y-auto p-5">
        <label class="form-control">
          <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Goal name</span>
          <input v-model="form.title" class="input input-bordered mt-1" autofocus
                 placeholder="For example: Close 10 new customers this quarter" @keyup.enter="save" />
        </label>

        <label class="form-control mt-3">
          <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">What outcome do we want?</span>
          <textarea v-model="form.objective" class="textarea textarea-bordered mt-1" rows="3"
                    placeholder="Describe the result in business language: who benefits, what changes, why it matters."></textarea>
        </label>

        <label class="form-control mt-3">
          <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">How will we know it is done?</span>
          <textarea v-model="form.acceptance" class="textarea textarea-bordered mt-1" rows="2"
                    placeholder="A number, a deliverable, a customer outcome or a demo. This is what the team will be judged on."></textarea>
        </label>

        <div class="mt-3 grid gap-3 md:grid-cols-2">
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Owning team</span>
            <select v-model="form.team_id" class="select select-bordered mt-1">
              <option value="">Choose a team…</option>
              <option v-for="team in teams" :key="team.id" :value="team.id">
                {{ team.name }} · {{ team.human_count }} human / {{ team.ai_count }} AI
              </option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Where the work happens</span>
            <select v-model="form.project_id" class="select select-bordered mt-1">
              <option value="">No project yet (draft)</option>
              <option v-for="project in projects" :key="project.project_id" :value="project.project_id">
                {{ project.name || project.project_id }}
              </option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Priority</span>
            <select v-model="form.priority" class="select select-bordered mt-1">
              <option value="low">Low — when there is room</option>
              <option value="medium">Medium — normal</option>
              <option value="high">High — this matters</option>
              <option value="critical">Critical — the company needs it</option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Deadline</span>
            <input v-model="form.due_date" type="date" class="input input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">AI budget (cxj)</span>
            <input v-model.number="form.budget_cxjcoins" type="number" min="0" step="0.5" class="input input-bordered mt-1" />
          </label>
          <label class="form-control">
            <span class="label-text text-[11px] font-bold uppercase tracking-wide text-base-content/50">Start mode</span>
            <select v-model="form.dispatch_mode" class="select select-bordered mt-1">
              <option value="manual">I will start it</option>
              <option value="auto">Start automatically when ready</option>
            </select>
            <span
              v-if="form.dispatch_mode === 'auto' && !autonomy.enabled"
              class="mt-1 block text-[10px] text-warning"
            >
              Autopilot is off: the goal waits in the queue. Enable autonomy in
              Company settings to let it start by itself.
            </span>
          </label>
        </div>

        <label class="mt-3 flex cursor-pointer items-start gap-2 rounded-xl border border-base-content/10 bg-base-200/40 p-3">
          <input v-model="form.requires_approval" type="checkbox" class="checkbox checkbox-sm checkbox-primary mt-0.5" />
          <span>
            <span class="block text-xs font-bold">I want to approve this before work starts</span>
            <span class="block text-[10px] text-base-content/45">A manager can still dispatch it, but only after an explicit approval.</span>
          </span>
        </label>

        <p v-if="!teams.length" class="mt-3 rounded-xl border border-warning/30 bg-warning/10 p-3 text-[11px] text-warning">
          You need a team before setting a goal. Ask the assistant to “build my team” first.
        </p>
        <p v-if="error" class="mt-3 rounded-xl border border-error/30 bg-error/10 p-2.5 text-[11px] text-error">{{ error }}</p>
      </div>

      <div class="flex items-center justify-between gap-2 border-t border-base-content/10 bg-base-200/30 px-5 py-3">
        <p class="text-[10px] text-base-content/40">
          <i class="fa-solid fa-lock mr-1"></i>Only managers can create goals.
        </p>
        <div class="flex gap-2">
          <button class="btn btn-ghost btn-sm" @click="close">Cancel</button>
          <button class="btn btn-primary btn-sm gap-1.5" :disabled="saving || !teams.length" @click="save">
            <span v-if="saving" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-flag"></i>
            {{ editing ? 'Save goal' : 'Create goal' }}
          </button>
        </div>
      </div>
    </div>
    <div class="modal-backdrop" @click="close"></div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
