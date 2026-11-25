<script setup>
import { computed } from 'vue'
import { useCompanyI18n } from './i18n'
import { memberAvatar } from './companyMeta'

/**
 * People — the humans and AI teammates of the company.
 *
 * The owner sees who is accountable, which team they belong to and whether the
 * AI is on duty. Hiring and invitation flows live in the assistant (or the
 * team builder), never in a cold settings table.
 */
const { t } = useCompanyI18n()

const props = defineProps({
  snapshot: { type: Object, default: null },
  actors: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false },
  canAdmin: { type: Boolean, default: false }
})

const emit = defineEmits(['assistant', 'new-team'])

const company = computed(() => props.snapshot?.company || {})
const members = computed(() => props.snapshot?.members || [])
const teams = computed(() => props.snapshot?.teams || [])
const units = computed(() => company.value.org_units || [])
const actorList = computed(() => props.actors || [])

const aiTeammates = computed(() => {
  const map = new Map()
  for (const team of teams.value) {
    for (const member of team.members || []) {
      if (member.kind !== 'profile') continue
      const current = map.get(member.name) || {
        name: member.name,
        role: member.role || member.title || 'AI teammate',
        responsibility: member.responsibility || '',
        teams: []
      }
      if (!current.teams.includes(team.name)) current.teams.push(team.name)
      if (!current.responsibility && member.responsibility) current.responsibility = member.responsibility
      map.set(member.name, current)
    }
  }
  return [...map.values()].sort((a, b) => a.name.localeCompare(b.name))
})

const onDuty = computed(() => aiTeammates.value.filter(profile => actorInfo(profile.name)?.config?.enabled).length)

function teamsOf(username) {
  return teams.value.filter(team => (team.members || []).some(member => member.kind === 'user' && member.name === username))
}

function actorInfo(name) {
  return actorList.value.find(actor => actor.actor === name) || null
}

function actorBudget(actor) {
  return Number(actor?.config?.daily_budget_cxjcoins || 0)
}

function actorSpend(actor) {
  const state = actor?.state || {}
  return Number(state.day_spent || state.spent_today || 0)
}

function schedule(actor) {
  return actor?.schedule?.description || (actor?.config?.enabled ? 'Always available' : 'Paused')
}

function roleLabel(member) {
  return member.company_role === 'admin' ? 'Admin' : member.company_role === 'manager' ? 'Manager' : 'Member'
}

function ask(prompt) {
  emit('assistant', prompt)
}

</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-xs font-bold uppercase tracking-[0.2em] text-info-ink">People</p>
        <h1 class="mt-1 text-2xl font-black tracking-tight md:text-3xl">{{ t('people.title') }}</h1>
        <p class="mt-1 text-sm text-base-content/55">
          Humans and AI teammates with clear roles. The owner is always in control of access, money and final decisions.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button v-if="canManage" class="btn btn-sm gap-1.5 rounded-xl border-base-content/15" @click="ask('I need a team')">
          <i class="fa-solid fa-robot"></i> {{ t('people.hire') }}
        </button>
        <button v-if="canAdmin" class="btn btn-primary btn-sm gap-1.5 rounded-xl shadow-lg shadow-primary/20" @click="ask('Add a person')">
          <i class="fa-solid fa-user-plus"></i> {{ t('people.add') }}
        </button>
      </div>
    </header>

    <section class="grid grid-cols-2 gap-3 md:grid-cols-4">
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-4">
        <p class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Humans</p>
        <p class="mt-1 text-2xl font-black">{{ members.length }}</p>
        <p class="mt-1 text-xs text-base-content/40">Owners, managers and team members</p>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-4">
        <p class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">AI teammates</p>
        <p class="mt-1 text-2xl font-black">{{ aiTeammates.length }}</p>
        <p class="mt-1 text-xs text-base-content/40">{{ onDuty }} on duty right now</p>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-4">
        <p class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Teams</p>
        <p class="mt-1 text-2xl font-black">{{ teams.length }}</p>
        <p class="mt-1 text-xs text-base-content/40">Hybrid operating units</p>
      </div>
      <div class="rounded-2xl border border-base-content/10 bg-base-100 p-4">
        <p class="text-xs font-bold uppercase tracking-[0.16em] text-base-content/40">Departments</p>
        <p class="mt-1 text-2xl font-black">{{ units.length }}</p>
        <p class="mt-1 text-xs text-base-content/40">Functions the company runs on</p>
      </div>
    </section>

    <section>
      <div class="flex items-center justify-between">
        <div>
          <h2 class="flex items-center gap-2 text-sm font-black">
            <span class="grid h-7 w-7 place-items-center rounded-lg bg-info/15 text-info-ink"><i class="fa-solid fa-user-tie text-xs"></i></span>
            Humans
          </h2>
          <p class="mt-1 text-xs text-base-content/45">The people who own decisions, money and customer relationships.</p>
        </div>
        <button v-if="canAdmin" class="btn btn-ghost btn-xs gap-1" @click="ask('Who is in the company?')">Ask about people</button>
      </div>

      <div v-if="!members.length" class="mt-3 rounded-2xl border border-dashed border-base-content/20 p-8 text-center">
        <i class="fa-solid fa-user-group text-2xl text-base-content/20"></i>
        <p class="mt-2 text-sm font-bold">No people yet.</p>
        <p class="text-xs text-base-content/45">Invite the owner, a manager or a teammate to work with the AI.</p>
        <button v-if="canAdmin" class="btn btn-primary btn-xs mt-3" @click="ask('Add a person')">Add the first person</button>
      </div>

      <div v-else class="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="member in members" :key="member.username"
                 class="rounded-2xl border border-base-content/10 bg-base-100 p-4">
          <div class="flex items-start gap-2.5">
            <div class="avatar">
              <div class="w-11 rounded-xl" :class="member.disabled ? 'grayscale opacity-60' : ''">
                <img :src="member.avatar || memberAvatar({ name: member.username })" :alt="member.username" />
              </div>
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5">
                <h3 class="truncate text-sm font-black">{{ member.username }}</h3>
                <span v-if="member.owner" class="badge badge-xs badge-warning gap-1"><i class="fa-solid fa-crown text-[10px]"></i> owner</span>
              </div>
              <p class="truncate text-xs text-base-content/45">{{ member.title || member.email || 'codx-junior user' }}</p>
            </div>
            <span class="badge badge-xs" :class="member.company_role === 'admin' ? 'badge-error' : member.company_role === 'manager' ? 'badge-primary' : 'badge-ghost'">{{ roleLabel(member) }}</span>
          </div>

          <div class="mt-3 flex flex-wrap gap-1">
            <span v-for="team in teamsOf(member.username)" :key="team.id" class="badge badge-xs badge-outline gap-1">
              <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: team.color || '#6366f1' }"></span>{{ team.name }}
            </span>
            <span v-if="!teamsOf(member.username).length" class="badge badge-xs badge-ghost">not in a team yet</span>
          </div>

          <div class="mt-3 flex items-center gap-1.5 border-t border-base-content/5 pt-2.5">
            <button class="btn btn-ghost btn-xs gap-1" @click="ask(`What is ${member.username} working on and what do they need from me?`)">
              <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i> Ask AI
            </button>
            <button v-if="canAdmin" class="btn btn-ghost btn-xs ml-auto" @click="ask(`Change the company role of ${member.username}`)">
              <i class="fa-solid fa-user-shield text-[11px]"></i> Role
            </button>
          </div>
        </article>
      </div>
    </section>

    <section>
      <div class="flex items-center justify-between">
        <div>
          <h2 class="flex items-center gap-2 text-sm font-black">
            <span class="grid h-7 w-7 place-items-center rounded-lg bg-primary/15 text-primary-ink"><i class="fa-solid fa-robot text-xs"></i></span>
            AI teammates
          </h2>
          <p class="mt-1 text-xs text-base-content/45">Each one has a role, a team, a schedule and a budget. Ask them anything.</p>
        </div>
        <span class="badge badge-sm badge-success badge-outline">{{ onDuty }} on duty</span>
      </div>

      <div v-if="!aiTeammates.length" class="mt-3 rounded-2xl border border-dashed border-base-content/20 p-8 text-center">
        <i class="fa-solid fa-robot text-2xl text-base-content/20"></i>
        <p class="mt-2 text-sm font-bold">No AI teammates yet.</p>
        <p class="text-xs text-base-content/45">Generate a team from a business need; codx-junior creates the roles and the team.</p>
        <button v-if="canManage" class="btn btn-primary btn-xs mt-3" @click="emit('new-team')">Build my team</button>
      </div>

      <div v-else class="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="profile in aiTeammates" :key="profile.name"
                 class="group rounded-2xl border border-base-content/10 bg-base-100 p-4 transition hover:border-primary/30">
          <div class="flex items-start gap-2.5">
            <div class="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-primary/20 to-secondary/20 text-primary-ink">
              <i class="fa-solid fa-robot"></i>
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5">
                <h3 class="truncate text-sm font-black">{{ profile.name }}</h3>
                <span class="badge badge-xs" :class="actorInfo(profile.name)?.config?.enabled ? 'badge-success badge-outline' : 'badge-ghost'">
                  {{ actorInfo(profile.name)?.config?.enabled ? 'on duty' : 'paused' }}
                </span>
              </div>
              <p class="truncate text-xs text-base-content/45">{{ profile.role }}</p>
            </div>
          </div>

          <p class="mt-3 line-clamp-2 min-h-8 text-xs leading-relaxed text-base-content/55">
            {{ profile.responsibility || 'A teammate of the company. Ask for a status or assign a goal.' }}
          </p>

          <div class="mt-2 flex flex-wrap gap-1">
            <span v-for="team in profile.teams.slice(0, 3)" :key="team" class="badge badge-xs badge-outline">{{ team }}</span>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-1.5 border-t border-base-content/5 pt-2.5 text-xs">
            <div>
              <p class="text-[11px] uppercase text-base-content/35">Schedule</p>
              <p class="truncate font-semibold">{{ schedule(actorInfo(profile.name)) }}</p>
            </div>
            <div>
              <p class="text-[11px] uppercase text-base-content/35">Today</p>
              <p class="font-semibold">{{ actorSpend(actorInfo(profile.name)).toFixed(2) }}<template v-if="actorBudget(actorInfo(profile.name))"> / {{ actorBudget(actorInfo(profile.name)) }}</template> cxj</p>
            </div>
          </div>

          <div class="mt-2 flex items-center gap-1.5">
            <button class="btn btn-ghost btn-xs gap-1" @click="ask(`What is ${profile.name} working on and how is it performing?`)">
              <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i> Ask
            </button>
            <button class="btn btn-ghost btn-xs ml-auto" @click="ask(`Give me a performance review of ${profile.name}`)">
              <i class="fa-solid fa-chart-line text-[11px]"></i> Review
            </button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
