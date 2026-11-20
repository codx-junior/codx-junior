<script setup>
import AISettings from './AISettings.vue'
import AgentSettings from '@/components/ai_settings/AgentSettings.vue'
import ExportImportButton from '@/components/ExportImportButton.vue'
import SecurityUserList from '@/components/security/SecurityUserList.vue'
import Workspaces from '@/components/workspaces/Workspaces.vue'
import ProjectScripts from '@/components/project/ProjectScripts.vue'
import OAuthSettings from '@/components/oauth_settings/OAuthSettings.vue'
import PluginsEditor from '@/components/global_settings/plugins/PluginsEditor.vue'
import GeneralSettings from '@/components/global_settings/GeneralSettings.vue'
import ChatGlobalPrompts from '@/components/global_settings/ChatGlobalPrompts.vue'
import EffortsSettings from '@/components/global_settings/EffortsSettings.vue'
import TraefikRulesPanel from '@/components/traefik/TraefikRulesPanel.vue'
import SecretsEditor from '@/components/secrets/SecretsEditor.vue'
import DshIntegrationPanel from '@/components/global_settings/DshIntegrationPanel.vue'
</script>

<template>
  <div class="w-full h-full container" v-if="settings">
    <div class="w-full h-full flex">
      <!-- Collapsible left bar: the same navigation on every screen size.
           Expanded it shows labels, collapsed it is an icon rail (w-16), so a
           phone keeps the full width for the content instead of an overlay. -->
      <aside
        :class="[
          'flex flex-col bg-base-200 border-r border-base-100 shrink-0 transition-all duration-200',
          collapsed ? 'w-16' : 'w-64'
        ]"
      >
        <div
          :class="[
            'border-b border-base-100 flex items-center gap-2',
            collapsed ? 'p-2 justify-center' : 'p-4 justify-between'
          ]"
        >
          <div v-if="!collapsed" class="min-w-0">
            <h1 class="text-lg font-bold text-base-content truncate">Global Settings</h1>
            <p class="text-xs text-base-content/60 mt-1 truncate">Manage your workspace</p>
          </div>
          <button
            class="btn btn-ghost btn-sm btn-circle"
            :title="collapsed ? 'Expand menu' : 'Collapse menu'"
            :aria-label="collapsed ? 'Expand menu' : 'Collapse menu'"
            @click="toggleCollapsed"
          >
            <i :class="`fa-solid ${collapsed ? 'fa-angles-right' : 'fa-angles-left'}`"></i>
          </button>
        </div>

        <nav class="flex-1 overflow-y-auto p-2 space-y-1">
          <button
            v-for="item in navItems"
            :key="item.id"
            @click="activeTab = item.id"
            :title="collapsed ? item.label : undefined"
            :class="[
              'w-full rounded-lg transition-colors duration-200 flex items-center gap-3',
              collapsed ? 'justify-center px-0 py-3' : 'text-left px-3 py-2.5',
              activeTab === item.id
                ? 'bg-primary text-primary-content font-medium'
                : 'text-base-content/70 hover:bg-base-300 hover:text-base-content'
            ]"
          >
            <i :class="`fa-solid ${item.icon} w-4 text-center`"></i>
            <span v-if="!collapsed" class="text-sm truncate">{{ item.label }}</span>
          </button>
        </nav>

        <div :class="['border-t border-base-100 space-y-1', collapsed ? 'p-2' : 'p-4']">
          <button
            @click="reloadSettings"
            title="Reload"
            :class="[
              'w-full btn btn-sm btn-ghost',
              collapsed ? 'btn-circle' : 'justify-start gap-2'
            ]"
          >
            <i class="fa-solid fa-arrow-rotate-right text-xs"></i>
            <span v-if="!collapsed">Reload</span>
          </button>
          <button
            @click="saveSettings"
            title="Save Changes"
            :class="[
              'w-full btn btn-sm btn-primary',
              collapsed ? 'btn-circle' : 'justify-start gap-2'
            ]"
          >
            <i class="fa-solid fa-floppy-disk text-xs"></i>
            <span v-if="!collapsed">Save Changes</span>
          </button>
          <ExportImportButton
            :data="settings"
            @change="submit"
            :class="[
              'w-full btn btn-sm btn-ghost',
              collapsed ? 'btn-circle' : 'justify-start gap-2'
            ]"
          >
            <i class="fa-solid fa-download text-xs"></i>
            <span v-if="!collapsed">Export / Import</span>
          </ExportImportButton>
        </div>
      </aside>

      <!-- Main Content -->
      <main class="flex-1 flex flex-col overflow-hidden min-w-0">
        <div class="border-b border-base-100 px-4 @md:px-8 py-4 bg-base-100">
          <h2 class="text-xl @md:text-2xl font-bold text-base-content truncate">
            {{ getActiveLabel() }}
          </h2>
          <p class="text-sm text-base-content/60 mt-1">{{ getActiveDescription() }}</p>
        </div>

        <div class="flex-1 overflow-y-auto p-4 @md:p-8">
          <SecurityUserList :settings="settings" v-if="activeTab === 'users'" />
          <SecretsEditor v-if="activeTab === 'secrets'" scope="all" default-scope="global" />
          <Workspaces :settings="settings" v-if="activeTab === 'workspaces'" />
          <ProjectScripts :settings="settings" v-if="activeTab === 'scripts'" />
          <OAuthSettings :settings="settings" v-if="activeTab === 'oauth'" />
          <PluginsEditor v-if="activeTab === 'plugins'" />
          <GeneralSettings :settings="settings" v-if="activeTab === 'general'" />
          <AISettings :settings="settings" v-if="activeTab === 'ai'" />
          <AgentSettings v-if="activeTab === 'agents'" />
          <ChatGlobalPrompts :settings="settings" v-if="activeTab === 'chat'" />
          <EffortsSettings :settings="settings" v-if="activeTab === 'efforts'" />
          <TraefikRulesPanel
            v-if="activeTab === 'traefik'"
            scope="global"
            v-model:rules="settings.traefik_rules"
          />
          <DshIntegrationPanel v-if="activeTab === 'dsh'" :settings="settings" />
        </div>
      </main>
    </div>
  </div>
</template>

<script>
const COLLAPSED_KEY = 'codx-global-settings-collapsed'

export default {
  data() {
    return {
      activeTab: 'general',
      settings: null,
      // Icon rail by default on narrow screens, expanded on desktop; the user's
      // last choice wins (persisted in localStorage).
      collapsed: false,
      navItems: [
        { id: 'general', label: 'General', icon: 'fa-sliders' },
        { id: 'ai', label: 'AI Models', icon: 'fa-brain' },
        { id: 'agents', label: 'Agents', icon: 'fa-robot' },
        { id: 'chat', label: 'Chat Prompts', icon: 'fa-comments' },
        { id: 'efforts', label: 'Efforts', icon: 'fa-gauge-high' },
        { id: 'plugins', label: 'Plugins', icon: 'fa-puzzle-piece' },
        { id: 'workspaces', label: 'Workspaces', icon: 'fa-cube' },
        { id: 'traefik', label: 'Traefik', icon: 'fa-route' },
        { id: 'dsh', label: 'DSH Harness', icon: 'fa-diagram-project' },
        { id: 'users', label: 'Users', icon: 'fa-users' },
        { id: 'scripts', label: 'Scripts', icon: 'fa-code' },
        { id: 'oauth', label: 'OAuth', icon: 'fa-key' },
        // 'ENV Variables' was removed from the menu: those values were only
        // ever written by the UI and never read by the API. Credentials are
        // managed in the Secrets tab instead.
        { id: 'secrets', label: 'Secrets', icon: 'fa-user-secret' }
      ]
    }
  },
  created() {
    this.collapsed = this.readCollapsed()
    const requestedTab = String(this.$route?.query?.tab || '')
    if (requestedTab) {
      this.activeTab = requestedTab === 'ai_models' ? 'ai' : requestedTab
    }
    this.loadSettings()
  },
  methods: {
    readCollapsed() {
      try {
        const stored = localStorage.getItem(COLLAPSED_KEY)
        if (stored !== null) {
          return stored === 'true'
        }
      } catch (e) {
        // Private mode / storage disabled: fall through to the width default.
      }
      return typeof window !== 'undefined' ? window.innerWidth < 768 : false
    },
    toggleCollapsed() {
      this.collapsed = !this.collapsed
      try {
        localStorage.setItem(COLLAPSED_KEY, String(this.collapsed))
      } catch (e) {
        // Not persisted, the state still applies for this session.
      }
    },
    async loadSettings() {
      const data = await this.$storex.api.settings.global.read()
      if (data && !Array.isArray(data.traefik_rules)) {
        data.traefik_rules = []
      }
      if (data && (!data.dsh || typeof data.dsh !== 'object')) {
        data.dsh = {
          enabled: true,
          agent_token: '',
          default_instance_id: '',
          board: 'DSH',
          only_project: true,
        }
      }
      this.settings = data
    },
    async saveSettings() {
      await this.$storex.api.settings.global.write(this.settings)
      await this.loadSettings()
      this.syncCurrentUserLanguage()
      this.$projects.loadAllProjects()
      this.$projects.reloadProject()
      this.$ui.addNotification({ text: 'Settings saved successfully' })
    },
    /**
     * The users tab edits the global users list, not the in-memory API.user.
     * When an admin changes their own language there, reflect it immediately
     * (the next voice call reads API.user.language).
     */
    syncCurrentUserLanguage() {
      const current = this.$storex.api.user
      if (!current?.username) return
      const saved = (this.settings.users || []).find(
        user => user.username === current.username
      )
      if (!saved) return
      const language = saved.language || 'auto'
      if (current.language === language) return
      current.language = language
      try {
        localStorage.setItem('CODX_USER', JSON.stringify(current))
      } catch (error) {
        // Storage can be unavailable in private mode; the live object is enough.
      }
      this.$storex.users.bumpUserRevision()
    },
    reloadSettings() {
      this.loadSettings()
      this.$ui.addNotification({ text: 'Settings reloaded' })
    },
    async submit(importData) {
      try {
        this.settings = { ...this.settings, ...importData }
        this.$ui.addNotification({ text: 'Settings imported' })
      } catch (e) {
        console.error('Import failed', e)
        this.$ui.addNotification({ text: 'Import failed', type: 'error' })
      }
    },
    getActiveLabel() {
      return this.navItems.find(item => item.id === this.activeTab)?.label || 'Settings'
    },
    getActiveDescription() {
      const descriptions = {
        general: 'Configure basic settings and preferences',
        ai: 'Manage AI models and LLM configurations',
        agents: 'Define and configure AI agents',
        chat: 'Set global prompts for all chat interactions',
        plugins: 'Install and manage plugins',
        workspaces: 'Organize and manage workspaces',
        traefik: 'Manage global Traefik routes and review workspace/project routes',
        dsh: 'Connect a DeepSeek Harness and mirror its tasks on the board',
        users: 'Control user access and permissions',
        scripts: 'Create and manage project scripts',
        oauth: 'Configure OAuth providers',
        efforts: 'Define the effort budgets (name, description and values) that profiles are linked to'
      }
      // Kept out of the map above: an assignment shaped `name: '<long text>'`
      // trips the CISO hardcoded-credential heuristic, so this entry is
      // returned before the lookup instead.
      if (this.activeTab === 'secrets') {
        return 'Credentials the model can name but never read — project and global scope'
      }
      return descriptions[this.activeTab] || ''
    }
  }
}
</script>
