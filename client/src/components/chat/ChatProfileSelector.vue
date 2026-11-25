<script setup>
import Selector from './Selector.vue'
import ProfileCard from '../ProfileCard.vue'
</script>

<template>
  <Selector
    :items="profiles"
    :selected-items="selectedProfiles"
    :label="label"
    :icon="icon"
    :single="single"
    :allow-deselect="allowDeselect"
    :empty-label="emptyLabel"
    :compact="compact"
    @select="onProfileSelected"
    @update:selected-items="onProfilesSelected"
  >
    <!-- Active profiles in the trigger: one avatar per profile, its name in a
         tooltip, and a DaisyUI avatar group so several avatars overlap instead
         of widening the header with names. -->
    <template #selected="{ items }">
      <div class="avatar-group -space-x-2 rtl:space-x-reverse" style="overflow: visible" role="group" aria-label="Selected profiles">
        <!-- The tooltip wrapper is the avatar-group child: `.avatar-group .avatar`
             clips its own overflow, so the tooltip must live one level above the
             avatar or DaisyUI would clip it. -->
        <div
          v-for="profile in items.slice(0, maxActiveAvatars)"
          :key="profile.name"
          class="tooltip tooltip-bottom"
          :data-tip="profile.name"
          :aria-label="profile.name"
        >
          <div class="avatar">
            <div class="w-6 rounded-full">
              <img :src="avatarFor(profile)" :alt="profile.name" />
            </div>
          </div>
        </div>
        <div
          v-if="items.length > maxActiveAvatars"
          class="tooltip tooltip-bottom"
          :data-tip="overflowProfilesTooltip(items)"
        >
          <div class="avatar placeholder">
            <div class="w-6 rounded-full bg-neutral text-neutral-content">
              <span class="text-[10px] font-semibold leading-none">
                +{{ items.length - maxActiveAvatars }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Custom profile card rendering in mini style -->
    <template #default="{ item, selected }">
      <div :class="selected ? 'ring-2 ring-primary rounded-lg' : 'rounded-lg'">
        <ProfileCard :profile="item" :mini="$ui.isMobile" />
      </div>
    </template>
  </Selector>
</template>

<script>
export default {
  props: {
    project: {
      type: Object,
      default: null
    },
    openFromBottom: {
      type: Boolean,
      default: false
    },
    selectedProfiles: {
      type: Array,
      default: () => []
    },
    // Single-selection mode: only one profile can be selected at a time.
    // Listeners receive the selected profile only (`select` event + a list with
    // one item at most), which is what single-valued fields (a board assignee)
    // need.
    single: {
      type: Boolean,
      default: false
    },
    // Single mode only: clicking the selected profile clears the selection
    allowDeselect: {
      type: Boolean,
      default: false
    },
    // Trigger button label / icon / "nothing selected" hint
    label: {
      type: String,
      default: 'Profiles'
    },
    icon: {
      type: String,
      default: 'fa-solid fa-plus'
    },
    emptyLabel: {
      type: String,
      default: ''
    },
    // Hide the "Profiles" label on the trigger: the avatars (and their
    // tooltips) already identify the control in narrow headers.
    compact: {
      type: Boolean,
      default: false
    }
  },
  emits: ['update:selected-profiles', 'profiles-changed', 'select'],
  data() {
    return {
      profiles: []
    }
  },
  computed: {
    // Avatars shown before the rest collapse into a +N counter.
    maxActiveAvatars() {
      return 4
    }
  },
  mounted() {
    this.loadProfiles(this.project || this.$project)
  },
  watch: {
    project: {
      handler(newProject) {
        if (newProject) {
          this.loadProfiles(newProject)
        }
      },
      immediate: true
    }
  },
  methods: {
    async loadProfiles(project) {
      try {
        if (project?.$api) {
          const profilesList = await project?.$api.profiles.list()
          this.profiles = (profilesList || []).sort((a, b) => a.name > b.name ? 1 : -1)
        }
      } catch (error) {
        console.error('[ChatProfileSelector] Error loading profiles:', error)
        this.profiles = []
      }
    },
    onProfilesSelected(selectedItems) {
      this.$emit('update:selected-profiles', selectedItems)
      this.$emit('profiles-changed', selectedItems)
    },
    // Single mode: the selector reports the clicked profile (null when cleared),
    // so a single-valued field never receives a list.
    onProfileSelected(profile) {
      const selected = profile ? [profile] : []
      this.$emit('select', profile?.name || null)
      this.$emit('update:selected-profiles', selected)
      this.$emit('profiles-changed', selected)
    },
    // Same fallback as <ProfileCard>: a stable robot avatar per profile name.
    avatarFor(profile) {
      return profile?.avatar ||
        `https://gravatar.com/avatar/baa8db8ab2afb7ababc235269e762662?s=400&d=robohash&r=${encodeURIComponent(profile?.name || '')}`
    },
    // Tooltip of the "+N" avatar: the profiles that did not fit.
    overflowProfilesTooltip(items) {
      return items.slice(this.maxActiveAvatars).map(profile => profile.name).join(', ')
    }
  }
}
</script>
