<template>
  <div class="space-y-3">
    <div v-if="!selected.includes('*')" class="space-y-2">
      <label class="label cursor-pointer">
        <span class="label-text">Mount all company projects</span>
        <input
          type="checkbox"
          class="checkbox"
          @change="toggleAllProjects"
        />
      </label>
    </div>

    <div v-if="!selected.includes('*')" class="space-y-2 max-h-48 overflow-y-auto">
      <div
        v-for="proj in availableProjects"
        :key="proj.project_id"
        class="form-control"
      >
        <label class="label cursor-pointer">
          <span class="label-text text-sm">{{ proj.project_name }}</span>
          <input
            :checked="selected.includes(proj.project_id)"
            type="checkbox"
            class="checkbox"
            @change="toggleProject(proj.project_id)"
          />
        </label>
      </div>
    </div>

    <div v-else class="alert alert-info">
      <i class="fa-solid fa-star"></i>
      <span>All company projects will be mounted</span>
    </div>
  </div>
</template>

<script>
export default {
  props: {
    selected: Array,
    project: Object,
    // Projects offered by the caller (the company-scoped list). When omitted,
    // the component resolves the active company scope itself so an old caller
    // can never fall back to every project of the installation.
    projects: { type: Array, default: null }
  },
  emits: ['update'],
  data() {
    return {
      fetchedProjects: []
    }
  },
  computed: {
    availableProjects() {
      // Prefer the live company getter when a company is loaded: the owner can
      // link a project while the dialog is open, and the selector must follow.
      if (this.$storex.company?.loaded) {
        return this.$storex.company.linkedProjects || []
      }
      if (Array.isArray(this.projects)) return this.projects
      return this.fetchedProjects
    }
  },
  async mounted() {
    if (Array.isArray(this.projects)) return
    if (this.$storex.company?.loaded) return
    try {
      this.fetchedProjects = await this.project.$api.projects.list() || []
    } catch (error) {
      console.error('Failed to load projects', error)
    }
  },
  methods: {
    toggleAllProjects() {
      if (this.selected.includes('*')) {
        this.$emit('update', [])
      } else {
        this.$emit('update', ['*'])
      }
    },
    toggleProject(projectId) {
      const newSelected = [...this.selected]
      const idx = newSelected.indexOf(projectId)
      if (idx > -1) {
        newSelected.splice(idx, 1)
      } else {
        newSelected.push(projectId)
      }
      this.$emit('update', newSelected)
    }
  }
}
</script>