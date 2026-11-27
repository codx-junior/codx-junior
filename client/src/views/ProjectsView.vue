<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { $storex } from '@/store'
import ProjectList from '@/components/ProjectList.vue'

/**
 * Project catalogue.
 *
 * A plain, compact list (no contribution charts): pick a project, switch the
 * active one and land on its workspace home, or add a new repository. It is
 * also the bootstrap screen while an installation has no project yet.
 */
const route = useRoute()
const router = useRouter()

const currentProject = computed(() => $storex.projects.activeProject)
const projects = computed(() => $storex.projects.allProjects || [])

async function selectProject(project) {
  if (!project?.project_id) return
  try {
    if (project.project_id !== $storex.projects.activeProject?.project_id) {
      await $storex.projects.activeProjectChanged(project)
    }
    const company = $storex.company?.activeCompany || $storex.company?.defaultCompany
    await router.push({
      name: 'home',
      params: {
        companySlug: company?.slug || route.params.companySlug,
        projectName: project.project_name
      }
    }).catch(() => {})
  } catch (error) {
    $storex.ui.addNotification({ text: 'Could not open project', type: 'error' })
  }
}

function newProject() {
  $storex.ui.showNewProject(true)
}
</script>

<template>
  <div class="h-full w-full overflow-y-auto bg-base-100">
    <div class="mx-auto flex h-full w-full max-w-3xl flex-col px-4 py-6 sm:px-6">
      <div class="mb-4">
        <h1 class="text-xl font-extrabold">Projects</h1>
        <p class="mt-1 text-sm text-base-content/60">
          Open a project workspace, switch the active project or add a repository.
        </p>
      </div>

      <div class="min-h-0 flex-1 overflow-hidden rounded-2xl border border-base-300 bg-base-100">
        <ProjectList
          class="h-full"
          :current-project="currentProject"
          @select-project="selectProject"
          @open-new-project="newProject"
        />
      </div>

      <p v-if="!projects.length" class="mt-3 text-center text-xs text-base-content/45">
        A project is required to use codx-junior. Add a Git URL or a server folder to start.
      </p>
    </div>
  </div>
</template>
