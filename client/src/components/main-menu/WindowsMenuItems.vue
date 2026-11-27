<script setup>
import MenubarItem from './MenubarItem.vue'
import SettingsMenu from './SettingsMenu.vue'
</script>

<template>
  <!-- Search -->
  <div class="p-3 border-b border-base-content/10">
    <label class="input input-sm input-bordered flex items-center gap-2 bg-base-100/60">
      <i class="fa-solid fa-magnifying-glass opacity-50"></i>
      <input
        v-model="search"
        type="text"
        class="grow bg-transparent"
        placeholder="Search apps & commands"
      />
      <button v-if="search" class="btn btn-ghost btn-xs" @click="search = ''">
        <i class="fa-solid fa-xmark"></i>
      </button>
    </label>
  </div>

  <!-- Apps grid: ONLY the app results scroll. Keeping this block bounded is
       what stops the Settings section below from being clipped. -->
  <div
    class="p-2 grid grid-cols-2 gap-1 text-sm"
    :class="mobile ? '' : 'overflow-y-auto max-h-[300px]'"
  >
    <MenubarItem
      v-for="item in filteredItems"
      :key="item.action"
      class="rounded-lg"
      @click="run(item)"
    >
      <i :class="item.icon" class="w-5 text-center"></i>
      {{ item.label }}
    </MenubarItem>

    <div v-if="!filteredItems.length" class="col-span-2 px-3 py-2 text-xs opacity-50">
      No results
    </div>
  </div>

  <!-- Settings: always visible, below the scrolling app grid. Hidden while
       searching so the results stay focused. Workspaces were removed from the
       product UI (deprecated feature). -->
  <div v-if="!search" class="border-t border-base-content/10 px-2 py-2">
    <div class="px-3 pt-1 pb-1 text-[11px] font-bold uppercase tracking-wide opacity-50">
      Settings
    </div>
    <SettingsMenu />
  </div>
</template>

<script>
import { isFeatureEnabled } from '@/config/features'

export default {
  props: {
    // Mobile layout: drop the internal max-height/scroll so the whole
    // WindowsMenu sheet scrolls as a single unit instead of nested areas.
    mobile: { type: Boolean, default: false }
  },
  emits: ['close'],
  data() {
    return {
      search: '',
      items: [
        { label: 'Home', icon: 'fa-solid fa-home', action: 'home' },
        // New chat opens the full-screen harness workspace.
        { label: 'New chat', icon: 'fa-solid fa-plus', action: 'harness-mode' },
        { label: 'Company', icon: 'fa-solid fa-building-columns', action: 'company', company: true },
        { label: 'Company settings', icon: 'fa-solid fa-building-user', action: 'company-settings', company: true },
        { label: 'Projects', icon: 'fa-solid fa-cubes', action: 'projects', feature: 'projects' },
        { label: 'New project', icon: 'fa-solid fa-plus', action: 'new-project' },
        { label: 'Wiki', icon: 'fa-solid fa-graduation-cap', action: 'wiki', feature: 'wiki' },
        { label: 'Chats', icon: 'fa-regular fa-comments', action: 'chats' },
        { label: 'Messenger', icon: 'fa-regular fa-comment-dots', action: 'messenger', feature: 'teams' },
        { label: 'CISO', icon: 'fa-solid fa-shield-halved', action: 'ciso', feature: 'company-security' },
        { label: 'Git', icon: 'fa-solid fa-code-branch', action: 'git-view', feature: 'git-review' },
        { label: 'Tasks', icon: 'fa-solid fa-list-check', action: 'task-list', feature: 'kanban' },
        { label: 'Board', icon: 'fa-brands fa-trello', action: 'tasks', feature: 'kanban' },
        { label: 'Planning', icon: 'fa-solid fa-diagram-project', action: 'planning', feature: 'kanban' },
        { label: 'Team', icon: 'fa-solid fa-people-group', action: 'team', feature: 'teams' },
        { label: 'Meet', icon: 'fa-solid fa-video', action: 'meet', feature: 'meetings' },
        { label: 'File explorer', icon: 'fa-regular fa-folder-open', action: 'file-explorer', feature: 'file-explorer' },
        { label: 'Computer sessions', icon: 'fa-solid fa-desktop', action: 'browser-sessions', feature: 'browser-use' },
        { label: 'Profiles', icon: 'fa-solid fa-id-badge', action: 'profiles', admin: true },
        { label: 'Knowledge', icon: 'fa-solid fa-magnifying-glass', action: 'knowledge', admin: true, feature: 'knowledge' },
        { label: 'Analytics', icon: 'fa-solid fa-chart-area', action: 'analytics', feature: 'analytics' },
        // Docker infrastructure (containers, stacks, images, volumes,
        // networks, events) — see config/apps.js → infra.
        { label: 'Infrastructure', icon: 'fa-solid fa-server', action: 'infra', feature: 'infra' }
      ]
    }
  },
  computed: {
    isProjectAdmin() {
      return this.$users.isProjectAdmin
    },
    filteredItems() {
      const term = this.search.trim().toLowerCase()
      return this.items
        .filter(item => !item.admin || this.isProjectAdmin)
        .filter(item => !item.company || this.$storex.features?.companyEnabled !== false)
        .filter(item => !item.feature || isFeatureEnabled(this.$storex.features, item.feature))
        .filter(item => !term || item.label.toLowerCase().includes(term))
    }
  },
  methods: {
    run(item) {
      const companyLocation = (section = '') => {
        const company = this.$storex.company?.activeCompany || this.$storex.company?.defaultCompany
        return {
          name: 'company-home',
          params: {
            ...(company?.slug ? { companySlug: company.slug } : {}),
            ...(section ? { section } : {})
          }
        }
      }
      const actions = {
        home: () => this.$ui.setActiveTab('home'),
        'harness-mode': () => this.$ui.openApp('harness-mode'),
        company: () => this.$storex.$router.push(companyLocation()).catch(() => {}),
        'company-settings': () => this.$storex.$router.push(companyLocation('settings')).catch(() => {}),
        projects: () => this.$ui.openApp('projects'),
        'new-project': () => this.$ui.showNewProject(true),
        wiki: () => this.$ui.setActiveTab('wiki'),
        chats: () => this.$storex.$router.push({ name: 'chats' }).catch(() => {}),
        messenger: () => this.$storex.$router.push({ name: 'messenger' }).catch(() => {}),
        ciso: () => this.$ui.openApp('ciso'),
        'git-view': () => this.$ui.openApp('git-view'),
        'task-list': () => this.$ui.openApp('task-list'),
        tasks: () => this.$ui.setActiveTab('tasks'),
        planning: () => this.$ui.openPlanning(),
        team: () => this.$ui.setActiveTab('team'),
        meet: () => this.$ui.openMeet(),
        'file-explorer': () => this.$ui.setActiveTab('file-explorer'),
        'browser-sessions': () => this.$ui.setActiveTab('browser-sessions'),
        profiles: () => this.$ui.setActiveTab('profiles'),
        knowledge: () => this.$ui.setActiveTab('knowledge'),
        analytics: () => this.$ui.setActiveTab('analytics'),
        infra: () => this.$ui.openInfra()
      }
      actions[item.action]?.()
      this.$emit('close')
    }
  }
}
</script>
