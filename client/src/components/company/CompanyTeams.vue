<script setup>
import { computed, ref, watch } from 'vue'
import { useCompanyI18n } from './i18n'
import {
  formatDate,
  memberAvatar,
  statusMeta
} from './companyMeta'

/**
 * Teams — the operating units of the company.
 *
 * Each team is a hybrid group (humans + AI) with an accountable lead, a
 * purpose, a budget and a body of work. The drawer is the team room lobby:
 * open the conversation, assign a goal, ask the lead or inspect the roster.
 */
const { t } = useCompanyI18n()

const props = defineProps({
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false },
  focusId: { type: String, default: '' }
})

const emit = defineEmits(['assistant', 'open-chat', 'new-goal', 'new-team'])

const company = computed(() => props.snapshot?.company || {})
const teams = computed(() => props.snapshot?.teams || [])
const units = computed(() => company.value.org_units || [])
const missions = computed(() => props.snapshot?.missions || [])
const projects = computed(() => props.snapshot?.projects || [])
const selectedId = ref('')

const selected = computed(() => teams.value.find(team => team.id === selectedId.value) || null)
const selectedMissions = computed(() => selected.value
  ? missions.value.filter(mission => mission.team_id === selected.value.id)
  : [])
const selectedOpen = computed(() => selectedMissions.value.filter(mission => ['ready', 'running', 'blocked', 'review', 'paused'].includes(mission.status)))

watch(() => [props.focusId, props.snapshot], ([id]) => {
  if (id && teams.value.some(team => team.id === id)) selectedId.value = id
}, { immediate: true })
const selectedProjects = computed(() => selected.value
  ? projects.value.filter(project => (selected.value.project_ids || []).includes(project.project_id) || project.team_id === selected.value.id)
  : [])

const totalHumans = computed(() => teams.value.reduce((sum, team) => sum + Number(team.human_count || 0), 0))
const totalAI = computed(() => teams.value.reduce((sum, team) => sum + Number(team.ai_count || 0), 0))
const totalOpen = computed(() => missions.value.filter(mission => ['ready', 'running', 'blocked', 'review', 'paused'].includes(mission.status)).length)

function unitName(team) {
  return units.value.find(unit => unit.id === team.org_unit_id)?.name || 'Unassigned'
}

function teamMissions(team) {
  return missions.value.filter(mission => mission.team_id === team.id)
}

function openMissions(team) {
  return teamMissions(team).filter(mission => ['ready', 'running', 'blocked', 'review', 'paused'].includes(mission.status))
}

function runningMissions(team) {
  return teamMissions(team).filter(mission => mission.status === 'running')
}

function teamColor(team) {
  return team.color || '#6366f1'
}

function humanMembers(team) {
  return (team.members || []).filter(member => member.kind === 'user')
}

function aiMembers(team) {
  return (team.members || []).filter(member => member.kind === 'profile')
}

function actorFor(name) {
  return (props.actors || []).find(actor => actor.actor === name)
}

function riskCount(team) {
  return teamMissions(team).filter(mission =>
    mission.status === 'blocked'
    || mission.last_error
    || (['ready', 'blocked'].includes(mission.status) && ['pending', 'denied'].includes(mission.approval_status))
  ).length
}

function ask(prompt) {
  emit('assistant', prompt)
}

function openTeam(team) {
  selectedId.value = team.id
}

function closeTeam() {
  selectedId.value = ''
}

function askLead(team) {
  emit('assistant', `As the lead of ${team.name}, summarize the team status, the main blocker and what you need from me.`)
}

function openRoom(team) {
  emit('open-chat', team.id)
}

function assignGoal(team) {
  emit('new-goal', team.id)
}

function projectName(project) {
  return project.name || project.project_id
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-xs font-bold uppercase tracking-[0.2em] text-secondary-ink">Organization</p>
        <h1 class="mt-1 text-2xl font-black tracking-tight md:text-3xl">{{ t('nav.team') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          Independent teams with a lead, a purpose and their own work. Humans and AI share the same board and the same accountability.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <div class="flex items-center gap-4 rounded-xl border border-base-content/10 bg-base-100 px-3 py-2 text-xs text-base-content/50 max-sm:hidden">
          <span><strong class="text-base-content">{{ teams.length }}</strong> teams</span>
          <span><strong class="text-base-content">{{ totalHumans }}</strong> {{ totalHumans === 1 ? 'human' : 'humans' }}</span>
          <span><strong class="text-base-content">{{ totalAI }}</strong> AI</span>
          <span><strong class="text-primary-ink">{{ totalOpen }}</strong> open</span>
        </div>
        <button v-if="canManage" class="btn btn-primary btn-sm gap-1.5 rounded-xl shadow-lg shadow-primary/20" @click="emit('new-team')">
          <i class="fa-solid fa-plus"></i> {{ t('teams.build') }}
        </button>
      </div>
    </header>

    <div v-if="!teams.length" class="rounded-3xl border border-dashed border-base-content/20 bg-base-100 p-10 text-center">
      <div class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary-ink"><i class="fa-solid fa-people-group text-xl"></i></div>
      <h2 class="mt-3 text-lg font-black">No teams yet</h2>
      <p class="mx-auto mt-1 max-w-md text-sm text-base-content/50">
        Tell the assistant what the company needs — “I need a delivery team and a sales team” — and it will design the roles, choose the leads and create the teams.
      </p>
      <div class="mt-4 flex justify-center gap-2">
        <button class="btn btn-primary btn-sm" @click="ask('I need a team')"><i class="fa-solid fa-wand-magic-sparkles"></i> Ask AI to build it</button>
        <button v-if="canManage" class="btn btn-ghost btn-sm" @click="emit('new-team')">Open team builder</button>
      </div>
    </div>

    <div v-else class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <button v-for="team in teams" :key="team.id"
              class="group relative overflow-hidden rounded-2xl border border-base-content/10 bg-base-100 p-4 text-left transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-xl"
              @click="openTeam(team)">
        <div class="absolute inset-x-0 top-0 h-1" :style="{ backgroundColor: teamColor(team) }"></div>
        <div class="flex items-start gap-2">
          <span class="grid h-11 w-11 shrink-0 place-items-center rounded-2xl text-white shadow-lg"
                :style="{ backgroundColor: teamColor(team) }">
            <i :class="team.icon || 'fa-solid fa-people-group'"></i>
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5">
              <h2 class="truncate text-sm font-black">{{ team.name }}</h2>
              <span class="badge badge-xs" :class="team.autonomy === 'autonomous' ? 'badge-success' : team.autonomy === 'manual' ? 'badge-ghost' : 'badge-info'">{{ team.autonomy }}</span>
            </div>
            <p class="truncate text-xs text-base-content/45">{{ unitName(team) }} · Lead {{ team.lead_name || 'not set' }}</p>
          </div>
          <i class="fa-solid fa-arrow-right text-[11px] text-base-content/20 transition group-hover:translate-x-0.5 group-hover:text-primary-ink"></i>
        </div>

        <p class="mt-3 line-clamp-2 min-h-8 text-xs leading-relaxed text-base-content/55">
          {{ team.purpose || team.description || 'No purpose set yet.' }}
        </p>

        <div class="mt-3 flex items-center gap-2">
          <div class="avatar-group -space-x-2">
            <div v-for="member in humanMembers(team).slice(0, 3)" :key="member.name" class="avatar border-2 border-base-100">
              <div class="w-7 rounded-full"><img :src="memberAvatar(member)" :alt="member.name" /></div>
            </div>
            <div v-for="member in aiMembers(team).slice(0, 3)" :key="`ai-${member.name}`" class="avatar placeholder border-2 border-base-100">
              <div class="w-7 rounded-full bg-primary/15 text-primary-ink"><i class="fa-solid fa-robot text-[11px]"></i></div>
            </div>
          </div>
          <span class="text-xs text-base-content/40">{{ team.human_count }} human · {{ team.ai_count }} AI</span>
          <span v-if="riskCount(team)" class="ml-auto badge badge-xs badge-warning gap-1"><i class="fa-solid fa-triangle-exclamation text-[10px]"></i>{{ riskCount(team) }}</span>
        </div>

        <div class="mt-3 grid grid-cols-3 gap-1.5 border-t border-base-content/5 pt-2.5">
          <div>
            <p class="text-[11px] uppercase tracking-wide text-base-content/35">Open</p>
            <p class="text-xs font-black">{{ openMissions(team).length }}</p>
          </div>
          <div>
            <p class="text-[11px] uppercase tracking-wide text-base-content/35">Running</p>
            <p class="text-xs font-black text-primary-ink">{{ runningMissions(team).length }}</p>
          </div>
          <div>
            <p class="text-[11px] uppercase tracking-wide text-base-content/35">Budget</p>
            <p class="text-xs font-black">{{ team.budget_cxjcoins || 0 }}<span class="text-[10px] font-normal text-base-content/40"> cxj</span></p>
          </div>
        </div>
      </button>
    </div>

    <!-- ── Team drawer ─────────────────────────────────────────────────── -->
    <div v-if="selected" class="fixed inset-0 z-[85] flex justify-end">
      <div class="absolute inset-0 bg-base-300/60 backdrop-blur-sm" @click="closeTeam"></div>
      <aside class="relative flex h-full w-full max-w-xl flex-col border-l border-base-content/10 bg-base-100 shadow-2xl">
        <header class="shrink-0 border-b border-base-content/10 p-4 md:p-5" :style="{ background: `linear-gradient(135deg, ${teamColor(selected)}22, transparent 60%)` }">
          <div class="flex items-start gap-3">
            <span class="grid h-12 w-12 shrink-0 place-items-center rounded-2xl text-white shadow-lg" :style="{ backgroundColor: teamColor(selected) }">
              <i :class="selected.icon || 'fa-solid fa-people-group'" class="text-lg"></i>
            </span>
            <div class="min-w-0 flex-1">
              <h2 class="text-lg font-black">{{ selected.name }}</h2>
              <p class="text-xs text-base-content/50">{{ unitName(selected) }} · {{ selected.human_count }} human · {{ selected.ai_count }} AI · {{ selected.autonomy }}</p>
            </div>
            <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="closeTeam"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <p v-if="selected.purpose" class="mt-3 text-xs leading-relaxed text-base-content/60">{{ selected.purpose }}</p>

          <div class="mt-4 flex flex-wrap gap-1.5">
            <button class="btn btn-primary btn-xs gap-1" @click="openRoom(selected)"><i class="fa-regular fa-comments"></i> Team room</button>
            <button v-if="canManage" class="btn btn-ghost btn-xs gap-1" @click="assignGoal(selected)"><i class="fa-solid fa-flag"></i> Assign a goal</button>
            <button class="btn btn-ghost btn-xs gap-1" @click="askLead(selected)"><i class="fa-solid fa-wand-magic-sparkles"></i> Ask the lead</button>
          </div>
        </header>

        <div class="flex-1 overflow-y-auto p-4 md:p-5">
          <div class="grid gap-2 sm:grid-cols-4">
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
              <p class="text-[11px] uppercase text-base-content/40">Open</p>
              <p class="text-lg font-black">{{ selectedOpen.length }}</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
              <p class="text-[11px] uppercase text-base-content/40">Running</p>
              <p class="text-lg font-black text-primary-ink">{{ selectedOpen.filter(m => m.status === 'running').length }}</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
              <p class="text-[11px] uppercase text-base-content/40">To review</p>
              <p class="text-lg font-black text-warning-ink">{{ selectedOpen.filter(m => m.status === 'review').length }}</p>
            </div>
            <div class="rounded-xl border border-base-content/10 bg-base-200/30 p-2.5">
              <p class="text-[11px] uppercase text-base-content/40">Budget</p>
              <p class="text-lg font-black">{{ selected.budget_cxjcoins || 0 }}</p>
            </div>
          </div>

          <section class="mt-5">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Roster</h3>
              <span class="text-xs text-base-content/35">{{ (selected.members || []).length }} members</span>
            </div>
            <div class="mt-2 space-y-1.5">
              <div v-for="member in selected.members" :key="`${member.kind}:${member.name}`"
                   class="flex items-center gap-2 rounded-xl border border-base-content/10 bg-base-100 p-2.5">
                <div v-if="member.kind === 'user'" class="avatar">
                  <div class="w-8 rounded-full"><img :src="memberAvatar(member)" :alt="member.name" /></div>
                </div>
                <div v-else class="grid h-8 w-8 place-items-center rounded-full bg-primary/15 text-primary-ink"><i class="fa-solid fa-robot text-xs"></i></div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-1.5">
                    <p class="truncate text-xs font-bold">{{ member.name }}</p>
                    <span v-if="member.name === selected.lead_name" class="badge badge-xs badge-warning gap-1"><i class="fa-solid fa-crown text-[10px]"></i> Lead</span>
                    <span v-if="member.kind === 'profile' && actorFor(member.name)?.config?.enabled" class="badge badge-xs badge-success badge-outline">on duty</span>
                  </div>
                  <p class="truncate text-xs text-base-content/45">{{ member.title || member.responsibility || member.role || (member.kind === 'user' ? 'Human' : 'AI teammate') }}</p>
                </div>
                <button class="btn btn-ghost btn-xs btn-square" title="Ask this teammate" @click="ask(`What is ${member.name} working on in ${selected.name}?`)">
                  <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
                </button>
              </div>
            </div>
          </section>

          <section class="mt-5">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Work in this team</h3>
              <span class="text-xs text-base-content/35">{{ selectedOpen.length }} open</span>
            </div>
            <div v-if="selectedOpen.length" class="mt-2 space-y-1.5">
              <button v-for="mission in selectedOpen.slice(0, 6)" :key="mission.id"
                      class="w-full rounded-xl border border-base-content/10 p-2.5 text-left hover:border-primary/40"
                      @click="ask(`What is the status of ${mission.title}?`)">
                <div class="flex items-center gap-2">
                  <span class="badge badge-xs" :class="statusMeta(mission.status).badge">{{ statusMeta(mission.status).label }}</span>
                  <span class="truncate text-xs font-semibold">{{ mission.title }}</span>
                  <span class="ml-auto text-xs text-base-content/40">{{ mission.progress || 0 }}%</span>
                </div>
                <p class="mt-1 truncate text-[11px] text-base-content/40">{{ mission.due_date ? `Due ${formatDate(mission.due_date)}` : 'No deadline' }}</p>
              </button>
            </div>
            <p v-else class="mt-2 rounded-xl border border-dashed border-base-content/15 p-4 text-center text-xs text-base-content/40">
              No open work. Assign a goal to get the team moving.
            </p>
          </section>

          <section class="mt-5">
            <h3 class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Projects this team works on</h3>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <span v-for="project in selectedProjects" :key="project.project_id" class="badge badge-sm badge-outline gap-1">
                <i class="fa-solid fa-cube text-[10px]"></i>{{ projectName(project) }}
              </span>
              <span v-if="!selectedProjects.length" class="text-xs text-base-content/40">No project linked yet.</span>
            </div>
          </section>
        </div>
      </aside>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
