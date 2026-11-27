<script setup>
import NavItem from './NavItem.vue'
import ProjectDetailt from '../ProjectDetailt.vue'
import ViewModeToggle from './ViewModeToggle.vue'
import PinnedProjectsSwitcher from './PinnedProjectsSwitcher.vue';
import { isMultiInstance } from '@/config/apps'
import { isOpenInNewTabEvent, openProjectInNewTab } from '@/utils/projectLinks'
import { SESSION_LABELS } from '@/utils/sessionLabels'
</script>

<template>
  <div class="px-2 py-3 shrink-0 border-b border-base-content/5">
    <div class="flex gap-2 pb-1 min-w-0">  
      
      <!-- Project selector: visible as soon as a project exists. On the
           workspace bootstrap (`/projects`) the URL has no project segment yet,
           but the active project is known — hiding the selector there was the
           "first load has no project selector" bug. -->
      <ProjectDetailt
        v-if="!isCollapsed && canShowProjectSelector"
        class="flex-1 min-w-0"
        @click.stop=""
        @select="onProjectSelected"
        @open="$emit('overlay-open')"
        @close="$emit('overlay-close')"
      />

      <!-- Pin / unpin the active project (quick switcher) -->
      <button
        v-if="!isCollapsed"
        class="btn tooltip tooltip-right shrink-0"
        :class="isActiveProjectPinned ? 'click text-primary' : 'click text-base-content/50'"
        :data-tip="isActiveProjectPinned ? 'Unpin project' : 'Pin project'"
        :aria-pressed="isActiveProjectPinned"
        aria-label="Pin project"
        @click.stop="togglePinCurrentProject"
      >
        <i class="fa-solid fa-thumbtack text-xs"></i>
      </button>

      <ViewModeToggle v-if="!isCollapsed"
        class="shrink-0" />
          
    </div>

    <!-- Pinned projects quick switcher (icons only while the rail is collapsed) -->
    <PinnedProjectsSwitcher 
      v-if="!isCollapsed"
      @select="onProjectSelected"
    />

    <button
      v-if="!isCollapsed"
      class="flex items-center justify-between w-full px-3 py-2 text-xs font-semibold text-base-content/60 hover:text-base-content/80 transition-colors"
      @click="primaryNavExpanded = !primaryNavExpanded"
      title="Toggle Primary Navigation"
    >
      APPS
      <i :class="primaryNavExpanded ? 'fa-solid fa-chevron-up' : 'fa-solid fa-chevron-down'" class="text-xs"></i>
    </button>

    <!-- Single, unfiltered application launcher: every option is always
         visible, whatever the user's workspace mode is. -->
    <div v-if="primaryNavExpanded || isCollapsed" class="flex flex-col gap-1">
      
      <!-- Company is the top-level element of the full product. The 0.0.1
           `core` feature profile hides it (workspace mode): no company shell,
           no company wizard, no company launcher. -->
      <NavItem
        v-if="companyEnabled"
        icon="fas fa-building-columns"
        label="Company"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToCompany"
      />

      <NavItem
        icon="fas fa-plus"
        label="New chat"
        :is-collapsed="isCollapsed"
        :is-primary="true"
        @click="handleNavigateToNewChat"
      />

      <!-- New company: a company is the top-level element ABOVE projects, so
           creating one lives here, next to its workspace. It opens the company
           shell with the create dialog already requested (`?new=1`) — the
           dialog itself is owned by views/CompanyView.vue, so it is never
           duplicated. Creating a project is a company-level action and lives
           in the company workspace (Projects panel). -->
      <NavItem
        v-if="companyEnabled"
        icon="fas fa-building-circle-arrow-right"
        label="New company"
        :is-collapsed="isCollapsed"
        @click="handleNewCompany"
      />

      <!-- Tasks: the general table of the latest tasks across every board.
           Boards are opened from a row, so the board canvas is not a separate
           launcher item. -->
      <NavItem
        v-if="appEnabled('task-list')"
        icon="fas fa-list-check"
        label="Tasks"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToTasks"
      />

      <NavItem
        v-if="appEnabled('planning')"
        icon="fas fa-diagram-project"
        label="Planning"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToPlanning"
      />

      <!-- Agenda / Calendar manager: ONE global agenda across every project
           (design/06-calendar/01-analysis.md, decision D1) — the projects are
           a FILTER of the view, never a data root. Registered as 'calendar' in
           config/apps.js, so this opens a dockview panel on desktop and the
           "/:projectName/calendar" route in single / mobile mode. -->
      <NavItem
        v-if="appEnabled('calendar')"
        icon="fa-regular fa-calendar-days"
        label="Calendar"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToCalendar"
      />

      <NavItem
        v-if="appEnabled('messenger')"
        icon="fa-regular fa-comments"
        label="Messenger"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToMessenger"
      />

      <NavItem
        v-if="appEnabled('meet')"
        icon="fas fa-video"
        label="Meet"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToMeet"
      />

      <NavItem
        v-if="appEnabled('team')"
        icon="fas fa-people-group"
        label="Team"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToTeam"
      />

      <NavItem
        v-if="appEnabled('file-explorer')"
        icon="fas fa-folder-open"
        label="File Explorer"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToFileExplorer"
      />

      <!-- Running Chromium sessions shared by the user and the AI. Opens the
           session manager (list + live co-browsing view) on desktop and the
           "/:projectName/browsers" route in single / mobile mode. -->
      <NavItem
        v-if="appEnabled('browser-sessions')"
        icon="fa-regular fa-compass"
        :label="SESSION_LABELS.section"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToBrowserSessions"
      />

      <NavItem
        v-if="appEnabled('git-view')"
        icon="fas fa-code-branch"
        label="Git"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToGitView"
      />

      <!-- CISO security view: posture, scans, findings, rules and policy.
           Registered as 'ciso' in config/apps.js, so the single opener uses a
           dockview panel on desktop and "/:projectName/ciso" otherwise. -->
      <NavItem
        v-if="appEnabled('ciso')"
        icon="fas fa-shield-halved"
        label="CISO"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToCiso"
      />

      <!-- `/api/infra/*` is admin-only, including the container shell. Hide the
           entry for other roles instead of opening a view that can only 403. -->
      <NavItem
        v-if="$users.isAdmin && appEnabled('infra')"
        icon="fas fa-server"
        label="Infrastructure"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToInfra"
      />

      <!-- Project channel (design/project-channel.md): shipped after 0.0.1.
           The route and the component stay in the codebase; the install feature
           profile decides whether the launcher is visible. -->
      <NavItem
        v-if="appEnabled('channel')"
        icon="fas fa-tower-broadcast"
        label="Channel"
        :is-collapsed="isCollapsed"
        @click="handleNavigateToChannel"
      />

    </div>
  </div>
</template>

<script>
import { isAppEnabled } from '@/config/features'

export default {
  props: {
    isCollapsed: { type: Boolean, default: false },
    moreExpanded: { type: Boolean, default: false },
    // Only /<companySlug>/workspace/<projectName>/… gets the project selector.
    inWorkspaceMode: { type: Boolean, default: false }
  },
  emits: ['update:moreExpanded', 'close', 'overlay-open', 'overlay-close'],
  data() {
    return {
      primaryNavExpanded: true
    }
  },
  mounted() {
    // The selector and the launcher read the project store. The shell can mount
    // before the first project refresh finishes (deep link / first login), so
    // make sure the catalogue is requested here as well — this is what made the
    // project selector look missing until a task screen forced a load.
    if (!(this.$storex.projects.allProjects || []).length) {
      this.$storex.projects.loadAllProjects?.()
    }
  },
  computed: {
    // True when the active project is in the UI store's pinned list
    isActiveProjectPinned() {
      return this.$storex.ui.isActiveProjectPinned
    },
    // The company launcher only exists in the full product. 0.0.1 `core`
    // installs start in the project workspace with the company OS hidden.
    companyEnabled() {
      return this.$storex.features?.companyEnabled !== false
    },
    // Show the switcher whenever there is a project to show. The URL only has a
    // project segment on workspace routes, but the bootstrap `/projects` screen
    // also needs a way to switch before the first workspace navigation.
    canShowProjectSelector() {
      return Boolean(this.inWorkspaceMode || this.$storex.projects.activeProject)
    },
    // Install-time feature filters (see config/features.js). Fails open while
    // the profile is loading or unavailable.
    appEnabled() {
      return appKey => isAppEnabled(this.$storex.features, appKey)
    }
  },
  methods: {
    // Project selector → switch the ACTIVE PROJECT.
    //
    // The switch is store-driven: `activeProjectChanged` reloads the project's
    // settings, chats, models and boards while the shell (sidebar + desktop
    // layout) stays mounted. Ctrl/Cmd+click (or middle click) is the browser's
    // "open in a new tab" gesture: the project then opens in ANOTHER tab and
    // the current one keeps running untouched (see utils/projectLinks.js).
    async onProjectSelected(project, event) {
      if (!project?.project_id) {
        this.$ui.addNotification({ text: 'Invalid project', type: 'error' })
        return
      }

      // New tab gesture → never touch the current tab, not even when the
      // project is already the active one.
      if (isOpenInNewTabEvent(event)) {
        openProjectInNewTab(project)
        this.$emit('close')
        return
      }

      // Selecting the project that is already active is a no-op, not a reload.
      if (project.project_id === this.$storex.projects.activeProject?.project_id) {
        this.$emit('close')
        return
      }

      // Single mode: land on the new project's home BEFORE activating it.
      // `activeProjectChanged` only swaps the project segment of the current
      // URL, so switching while a chat / workspace / browser-session / file id
      // is in the route would leave an id that belongs to the previous
      // project. Desktop mode is the exception: the dockview watches the
      // active project and restores that project's own layout, so the desktop
      // stays mounted and keeps its panels.
      const desktopMode = this.$storex.$router?.$navigation?.isDesktopMode
      if (!desktopMode && this.$route?.name !== 'home') {
        await this.$router.push({ name: 'home' }).catch(() => {})
      }

      try {
        await this.$storex.projects.activeProjectChanged(project)
        this.$emit('close')
      } catch (error) {
        console.error('[AppsSection] cannot switch project', error)
        this.$ui.addNotification({ text: 'Could not switch project', type: 'error' })
      }
    },
    // Single entry point for opening an app from this menu.
    // On desktop a multi-instance app (declared in config/apps.js) opens a NEW
    // tab each time so several instances can coexist — e.g. the Task Manager,
    // where each tab is a board. Every other case keeps the original behaviour:
    // setActiveTab focuses/creates the single desktop panel, or switches to the
    // app's own route in single/mobile mode.
    openApp(appKey) {
      if (this.$ui.isDesktopMode && isMultiInstance(appKey)) {
        // newInstance tells showApp to skip its per-key de-duplication and
        // create a brand-new panel (see store/ui.js → showApp).
        this.$ui.openApp({ appKey, payload: { newInstance: true } })
      } else {
        this.$ui.setActiveTab(appKey)
      }
      this.$emit('close')
    },
    // Pin/unpin the currently active project. Pins live in the UI store and are
    // persisted in localStorage (uiState), then surfaced by PinnedProjectsSwitcher.
    togglePinCurrentProject() {
      const project = this.$storex.projects.activeProject
      if (!project?.project_id) {
        this.$ui.addNotification({ text: 'No active project to pin', type: 'warning' })
        return
      }
      this.$ui.togglePinProject(project.project_id)
      this.$ui.addNotification({
        text: this.isActiveProjectPinned
          ? `Pinned ${project.project_name}`
          : `Unpinned ${project.project_name}`
      })
    },
    // New company: opens the company shell asking it to show its "Create
    // company" dialog (query flag `new`). It is a router push — the company
    // shell is a full screen, not a project panel.
    handleNewCompany() {
      if (!this.companyEnabled) return
      const company = this.$storex.company?.activeCompany || this.$storex.company?.defaultCompany
      const router = this.$storex.$router
      if (company?.slug) {
        router.push({
          name: 'company-home',
          params: { companySlug: company.slug },
          query: { new: '1' }
        }).catch(() => {})
      } else {
        router.push('/').catch(() => {})
      }
      this.$emit('close')
    },
    handleNavigateToNewChat() {
      // New chat IS harness mode in the 0.0.1 workspace: the full-screen
      // session workspace (brief → agent run → delivery), not the old modal.
      this.openApp('harness-mode')
    },
    handleNavigateToTasks() {
      this.openApp('task-list')
    },
    // Board canvas (kept for callers that need to open a board directly).
    handleNavigateToKanban() {
      this.openApp('tasks')
    },
    handleNavigateToPlanning() {
      this.openApp('planning')
    },
    // Opens the global agenda (calendar manager). It is registered as
    // 'calendar' in config/apps.js, so the unified opener shows a dockview
    // panel on desktop and navigates to "/:projectName/calendar" in
    // single / mobile mode (see views/CalendarView.vue).
    handleNavigateToCalendar() {
      this.openApp('calendar')
    },
    // Opens the CISO security view for the active project (same app registry
    // entry as the menu / route: desktop panel or single-mode route).
    handleNavigateToCiso() {
      this.openApp('ciso')
    },
    handleNavigateToCompany() {
      // Company is the root shell, never a project panel. Hidden in workspace
      // mode, where the router keeps every company URL in the project shell.
      if (!this.companyEnabled) return
      const company = this.$storex.company?.activeCompany || this.$storex.company?.defaultCompany
      if (company?.slug) {
        this.$storex.$router.push({
          name: 'company-home',
          params: { companySlug: company.slug }
        }).catch(() => {})
      }
      this.$emit('close')
    },
    handleNavigateToMessenger() {
      this.openApp('messenger')
    },
    handleNavigateToMeet() {
      this.openApp('meet')
    },
    // Open the Team screen via the UI store opener, which routes through the
    // unified openApp() (dockview panel on desktop / route in single mode).
    handleNavigateToTeam() {
      this.$storex.ui.openTeams()
      this.$emit('close')
    },
    handleNavigateToFileExplorer() {
      this.openApp('file-explorer')
    },
    // Opens the co-browsing session manager so the user can create, inspect,
    // open, restart or delete the Chromium sessions shared with the AI.
    handleNavigateToBrowserSessions() {
      this.openApp('browser-sessions')
    },
    handleNavigateToGitView() {
      this.openApp('git-view')
    },
    // Docker infrastructure (containers, compose stacks, images, volumes,
    // networks, daemon events). Uses the same unified opener as the other
    // entries: a dockview panel on desktop, "/:projectName/infra" in
    // single / mobile mode (see config/apps.js → infra).
    // NOTE: `/api/infra/*` is admin-only on the backend; gate this NavItem with
    // v-if="$users.isProjectAdmin" once the role is enforced server-side.
    handleNavigateToInfra() {
      this.openApp('infra')
    },
    // Opens the project channel. It is a full screen (with its own tab bar),
    // so it is reached by ROUTE rather than through openApp(): the channel is
    // not a dockview panel yet. The router injects the active project prefix
    // because the route path contains ":projectName" (see router/index.ts).
    handleNavigateToChannel() {
      this.$router.push({ name: 'channel' }).catch(() => {})
      this.$emit('close')
    },
    // Kept for the screens that still open the wiki directly: the 'wiki' app
    // is registered in config/apps.js and "/:projectName/wiki" still resolves,
    // so the old documentation entry point is never broken.
    handleNavigateToWiki() {
      this.openApp('wiki')
    },
    toggleCollapse() {
      this.$emit('toggle-collapse')
    }
  }
}
</script>
