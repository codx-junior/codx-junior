<script setup>
import MenubarItem from './MenubarItem.vue'
import SettingsMenu from './SettingsMenu.vue'
import MenuDivider from './MenuDivider.vue'
import { $storex } from '@/store'
import { isAppEnabled, isFeatureEnabled } from '@/config/features'

// The main menu is the one launcher that ALWAYS contains everything that the
// installation exposes, in both product shells. Entries whose install feature
// is inactive (0.0.1 core) are filtered out instead of leading to a dead
// screen. See config/features.js for the app → feature map.
function openCompanyWorkspace() {
  const company = $storex.company?.activeCompany || $storex.company?.defaultCompany
  if (!company?.slug) return
  $storex.$router.push({
    name: 'company-home',
    params: { companySlug: company.slug }
  })
}

function openProjects() {
  $storex.ui.openApp('projects')
}

function openCompanySettings() {
  const company = $storex.company?.activeCompany || $storex.company?.defaultCompany
  if (!company?.slug) return
  $storex.$router.push({
    name: 'company-home',
    params: { companySlug: company.slug, section: 'settings' }
  })
}

// New chat = harness mode (brief → agent run → delivery), same destination as
// the left bar. Harness diagnostics is no longer a launcher item.
function openNewChat() {
  $storex.ui.openApp('harness-mode')
}

function appEnabled(appKey) {
  return isAppEnabled($storex.features, appKey)
}

function featureEnabled(featureId) {
  return isFeatureEnabled($storex.features, featureId)
}
</script>

<template>
  <MenubarItem @click="$ui.setActiveTab('home')">
    <i class="fa-solid fa-home"></i>
    Home
  </MenubarItem>
  <MenubarItem @click="openNewChat">
    <i class="fa-solid fa-plus"></i>
    New chat
  </MenubarItem>
  <MenubarItem
    v-if="$storex.features?.companyEnabled !== false"
    @click="openCompanyWorkspace"
  >
    <i class="fa-solid fa-building-columns"></i>
    Company
  </MenubarItem>
  <MenubarItem
    v-if="$storex.features?.companyEnabled !== false"
    @click="openCompanySettings"
  >
    <i class="fa-solid fa-building-user"></i>
    Company settings
  </MenubarItem>
  <MenubarItem v-if="appEnabled('projects')" @click="openProjects">
    <i class="fa-solid fa-cubes"></i>
    Projects
  </MenubarItem>
  <MenubarItem @click="$ui.showNewProject(true)">
    <i class="fa-solid fa-plus"></i>
    New project
  </MenubarItem>
  <MenubarItem v-if="appEnabled('wiki')" @click="$ui.setActiveTab('wiki')">
    <i class="fa-solid fa-graduation-cap"></i> Wiki
  </MenubarItem>
  <MenubarItem v-if="appEnabled('task-list')" @click="$ui.openApp('task-list')">
    <i class="fa-solid fa-list-check"></i>
    Tasks
  </MenubarItem>
  <MenubarItem v-if="appEnabled('planning')" @click="$ui.openPlanning()">
    <i class="fa-solid fa-diagram-project"></i>
    Planning
  </MenubarItem>
  <MenubarItem v-if="appEnabled('team')" @click="$ui.setActiveTab('team')">
    <i class="fa-solid fa-people-group"></i>
    Team
  </MenubarItem>
  <MenubarItem v-if="featureEnabled('meetings')" @click="$ui.openMeetingSetup({ source: 'manual' })">
    <i class="fa-solid fa-video"></i>
    New meeting
  </MenubarItem>
  <MenubarItem v-if="featureEnabled('meetings')" @click="$ui.openMeet()">
    <i class="fa-solid fa-phone"></i>
    Join a meeting
  </MenubarItem>
  <MenubarItem v-if="appEnabled('file-explorer')" @click="$ui.setActiveTab('file-explorer')">
    <i class="fa-regular fa-folder-open"></i>
    File explorer
  </MenubarItem>
  <MenubarItem v-if="appEnabled('browser-sessions')" @click="$ui.setActiveTab('browser-sessions')">
    <i class="fa-solid fa-desktop"></i> Computer sessions
  </MenubarItem>
  <MenubarItem v-if="appEnabled('git-view')" @click="$ui.openGitView()">
    <i class="fa-solid fa-code-branch"></i> Git
  </MenubarItem>
  <MenubarItem v-if="appEnabled('ciso')" @click="$ui.openApp('ciso')">
    <i class="fa-solid fa-shield-halved"></i> CISO
  </MenubarItem>
  <MenubarItem
    v-if="$users.isProjectAdmin && appEnabled('knowledge')"
    @click="$ui.setActiveTab('knowledge')"
  >
    <i class="fa-solid fa-magnifying-glass"></i>
    Knowledge
  </MenubarItem>
  <MenubarItem v-if="appEnabled('analytics')" @click="$ui.setActiveTab('analytics')">
    <i class="fa-solid fa-chart-area"></i>
    Analytics
  </MenubarItem>
  <MenubarItem v-if="$users.isAdmin && appEnabled('infra')" @click="$ui.openInfra()">
    <i class="fa-solid fa-server"></i>
    Infrastructure
  </MenubarItem>
  <MenuDivider />
  <SettingsMenu />
  <MenuDivider />
  <MenubarItem>
    <a
      class="flex gap-1 tooltip text-error"
      data-tip="Log out"
      @click.stop="$users.logout()">
      <i class="fa-solid fa-right-from-bracket"></i>
      Log out
    </a>
  </MenubarItem>
</template>
