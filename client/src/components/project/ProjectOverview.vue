<script setup>
import ProjectCard from './ProjectCard.vue'
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex justify-between">
      <div class="text-xl font-bold">
        <span v-if="filterQuery">Find projects: '{{ filterQuery }}'</span>
        <span v-else>Top projects</span>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-primary btn-sm" @click="$ui.showNewProject(true)">
          <i class="fa-solid fa-plus"></i> New project
        </button>
        <div class="flex items-center gap-2 mb-2 input input-sm input-bordered">
          <input type="text" class="" placeholder="Filter projects..." v-model="filterQuery" />
          <div class="click" @click="filterQuery = null" v-if="filterQuery">
            <i class="fa-solid fa-circle-xmark"></i>
          </div>
        </div>
      </div>
    </div>
    <div class="grid grid-cols-2 @xl:grid-cols-3 gap-2">
      <ProjectCard
        v-for="project in filteredProjects"
        :key="project.project_id"
        :project="project"
        @click="setActiveProject(project)"
      />
    </div>
  </div>
</template>

<script>
export default {
  data() {
    return {
      filterQuery: null
    }
  },
  computed: {
    filteredProjects() {
      // The company projection is a company-shell concern. In workspace mode
      // (`core`) the project catalogue is the only source of truth: a company
      // that has not linked the new project yet must not hide it from the list.
      const workspaceMode = this.$storex.features?.companyEnabled === false
      const visible = !workspaceMode && this.$storex.company?.loaded
        ? (this.$storex.company.companyProjects || [])
        : (this.$projects.allProjects || [])
      const projects = [...visible].sort((a, b) => 
        Object.keys(a.metrics?.heatmap || {}).length > Object.keys(b.metrics?.heatmap || {}).length ? -1 : 1 
      )
      return this.filterQuery ? projects.filter(project =>
        project.project_name.toLowerCase().includes(this.filterQuery.toLowerCase()) ||
        project.abs_project_path.toLowerCase().includes(this.filterQuery.toLowerCase())
      ) : projects.slice(0, 6)
    }
  },
  methods: {
    async setActiveProject(project) {
      try {
        await this.$projects.activeProjectChanged(project)
      } catch {
        // The store reports its own loading failure; keep the user here.
        return
      }
      this.$ui.setActiveTab("tasks")
      // The workspace bootstrap (`/projects`) is a landing screen, not a
      // destination: picking a project opens its workspace home. In company
      // mode the guard redirects this route to the company home instead.
      if (this.$route?.name === 'workspace-projects') {
        const company = this.$storex.company?.defaultCompany ||
          this.$storex.company?.activeCompany
        if (company?.slug && project?.project_name) {
          this.$router.replace({
            name: 'home',
            params: {
              companySlug: company.slug,
              projectName: project.project_name
            }
          }).catch(() => {})
        }
      }
    }
  }
}
</script>