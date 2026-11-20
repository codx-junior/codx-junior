<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { API } from '@/api/api'
import { $storex } from '@/store'
import MessengerChannelList from '@/components/messenger/MessengerChannelList.vue'
import ChatView from '@/views/ChatView.vue'
import { useCompanyI18n } from './i18n'

/**
 * Company chats — the company/team channels.
 *
 * The company does not reimplement messaging: a channel is a normal
 * `mode="channel"` Chat hosted by one of the company's linked projects, so
 * mentions, attachments, unread counters, AI replies and the existing
 * Messenger UI keep working. This panel only adds the company layer: the
 * channel list, the owning team and the project that hosts the messages.
 */
const props = defineProps({
  companyId: { type: String, required: true },
  snapshot: { type: Object, default: null },
  canManage: { type: Boolean, default: false },
  teamId: { type: String, default: '' },
  chatId: { type: String, default: '' }
})

const emit = defineEmits(['close', 'changed'])

const api = API.companies
const { t } = useCompanyI18n()

const loading = ref(true)
const busy = ref(false)
const chats = ref([])
const activeChat = ref(null)
const activeRef = ref(null)
const showCreate = ref(false)
const sidebarCollapsed = ref(false)
const showMobileChannels = ref(false)
const formError = ref('')

const form = reactive({
  name: '',
  description: '',
  team_id: '',
  project_id: ''
})

const projects = computed(() => props.snapshot?.projects || [])
const teams = computed(() => props.snapshot?.teams || [])

const focusedTeam = computed(() =>
  props.teamId ? teams.value.find(team => team.id === props.teamId) || null : null
)

const visibleChats = computed(() =>
  props.teamId ? chats.value.filter(item => item.team_id === props.teamId) : chats.value
)

const groups = computed(() =>
  visibleChats.value.map(item => ({
    id: item.chat_id,
    name: item.name || item.chat?.name || 'Channel',
    updated_at: item.chat?.updated_at || item.updated_at,
    messages: item.chat?.last_message ? [item.chat.last_message] : [],
    _companyChat: item
  }))
)

const activeTeam = computed(() => {
  const teamId = activeRef.value?.team_id
  return teams.value.find(team => team.id === teamId) || null
})

const activeProject = computed(() => {
  const projectId = activeRef.value?.project_id
  return projects.value.find(project => project.project_id === projectId) || null
})

function notify(text, type = 'info') {
  try {
    $storex.ui.addNotification({ text, type })
  } catch {
    // best effort
  }
}

function errorMessage(error) {
  return (
    error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
  )
}

async function loadChats({ selectFirst = false, selectId = '' } = {}) {
  loading.value = true
  try {
    chats.value = await api.listChats(props.companyId)
    const target = selectId
      ? visibleChats.value.find(item => item.chat_id === selectId)
      : null
    if (target) {
      await selectChat({ _companyChat: target })
    } else if (selectFirst && visibleChats.value.length) {
      const first = visibleChats.value.find(item => item.available)
      if (first) await selectChat({ _companyChat: first })
    }
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    loading.value = false
  }
}

async function selectChat(group) {
  const ref = group?._companyChat || group
  if (!ref?.chat_id) return
  if (!ref.available) {
    notify(ref.error || 'This channel is not available anymore', 'error')
    return
  }
  const project = $storex.projects.allProjectsById?.[ref.project_id]
  if (!project) {
    notify(`Project "${ref.project_id}" is not loaded in this workspace`, 'error')
    return
  }
  busy.value = true
  try {
    const chat = await $storex.chats.loadChat({
      id: ref.chat_id,
      owner_project_id: ref.project_id,
      project_id: ref.project_id
    })
    if (!chat) {
      notify('Could not load the channel messages', 'error')
      return
    }
    activeChat.value = chat
    activeRef.value = ref
    showMobileChannels.value = false
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    name: '',
    description: '',
    team_id: props.teamId || teams.value[0]?.id || '',
    project_id: projects.value[0]?.project_id || ''
  })
  formError.value = ''
  showCreate.value = true
}

async function createChannel() {
  if (!form.name.trim()) {
    formError.value = 'Channel name is required'
    return
  }
  busy.value = true
  try {
    await api.createChat(props.companyId, {
      name: form.name.trim(),
      description: form.description,
      team_id: form.team_id,
      project_id: form.project_id
    })
    showCreate.value = false
    await loadChats()
    const created = chats.value.find(
      item => item.name.toLowerCase() === form.name.trim().toLowerCase()
    )
    if (created) await selectChat({ _companyChat: created })
    emit('changed')
    notify('Channel created ✓', 'success')
  } catch (error) {
    formError.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function syncMembers() {
  if (!activeRef.value) return
  busy.value = true
  try {
    await api.syncChatMembers(props.companyId, activeRef.value.chat_id)
    notify('Members synced from the team ✓', 'success')
    await loadChats()
    if (activeChat.value) {
      activeChat.value = await $storex.chats.reloadChat(activeChat.value)
    }
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function removeChannel() {
  if (!activeRef.value) return
  if (!window.confirm(`Delete the channel "${activeRef.value.name}" and all its messages?`)) {
    return
  }
  busy.value = true
  try {
    await api.deleteChat(props.companyId, activeRef.value.chat_id)
    activeChat.value = null
    activeRef.value = null
    await loadChats({ selectFirst: true })
    emit('changed')
    notify('Channel deleted', 'info')
  } catch (error) {
    notify(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

watch(() => [props.teamId, props.companyId, props.chatId], async () => {
  activeChat.value = null
  activeRef.value = null
  await loadChats({ selectFirst: !props.chatId, selectId: props.chatId })
}, { immediate: true })
</script>

<template>
  <div class="fixed inset-0 z-[80] flex flex-col bg-base-100">
    <!-- Header -->
    <header class="shrink-0 border-b border-base-content/10 bg-base-100/90 backdrop-blur px-3 py-2">
      <div class="flex items-center gap-2">
        <div class="w-8 h-8 rounded-lg grid place-items-center bg-primary/15 text-primary shrink-0">
          <i class="fa-regular fa-comments"></i>
        </div>
        <div class="min-w-0 flex-1">
          <h2 class="text-sm font-bold truncate">{{ focusedTeam ? focusedTeam.name : t('chats.title') }}</h2>
          <p class="text-[10px] text-base-content/45 truncate">
            {{ visibleChats.length }} channel{{ visibleChats.length === 1 ? '' : 's' }} ·
            {{ focusedTeam ? t('chats.teamRoom') : t('chats.hosted') }}
          </p>
        </div>
        <button
          v-if="canManage"
          class="btn btn-sm btn-primary gap-1"
          :disabled="busy"
          :title="t('chats.newChannel')"
          @click="openCreate"
        >
          <i class="fa-solid fa-plus"></i>
          <span class="hidden sm:inline">{{ t('chats.newChannel') }}</span>
        </button>
        <button class="btn btn-sm btn-ghost" :title="t('chats.close')" @click="emit('close')">
          <i class="fa-solid fa-xmark"></i>
          <span class="hidden sm:inline">{{ t('chats.close') }}</span>
        </button>
      </div>
    </header>

    <div class="flex-1 min-h-0 flex overflow-hidden">
      <!-- Channel sidebar (desktop) -->
      <aside
        v-if="!$storex.ui.isMobile" class="flex min-w-0 shrink-0 flex-col overflow-hidden border-r border-base-content/10 bg-base-200/40"
        :style="sidebarCollapsed ? 'width:64px' : 'width:288px'"
      >
        <MessengerChannelList
          :groups="groups"
          :active-chat="activeChat"
          :user-name="$storex.user?.username || 'User'"
          :is-collapsed="sidebarCollapsed"
          :loading="loading"
          @select="selectChat"
          @new-group="openCreate"
          @toggle-collapse="sidebarCollapsed = !sidebarCollapsed"
        />
        <p class="px-3 py-2 text-[10px] text-base-content/35 border-t border-base-content/5">
          A channel belongs to a team. Its humans and AI profiles are the roster.
        </p>
      </aside>

      <!-- Mobile channel drawer -->
      <div
        v-if="showMobileChannels"
        class="fixed inset-0 z-50 bg-base-200 md:hidden"
      >
        <div class="flex items-center justify-between px-3 py-3 border-b border-base-content/10">
          <span class="text-sm font-bold">{{ t('chats.channels') }}</span>
          <button class="btn btn-ghost btn-sm btn-square" @click="showMobileChannels = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <MessengerChannelList
          :groups="groups"
          :active-chat="activeChat"
          :user-name="$storex.user?.username || 'User'"
          :loading="loading"
          @select="selectChat"
          @new-group="openCreate"
          @toggle-collapse="showMobileChannels = false"
        />
      </div>

      <!-- Conversation -->
      <div class="flex-1 min-w-0 flex flex-col">
        <div
          v-if="!activeChat"
          class="flex-1 flex flex-col items-center justify-center gap-3 text-base-content/45 px-6"
        >
          <i class="fa-regular fa-comments text-5xl"></i>
          <p class="text-sm text-center">
            {{ focusedTeam ? `${t('chats.noChannels')} · ${focusedTeam.name}` : t('chats.pick') }}
          </p>
          <div class="flex gap-2">
            <button class="btn btn-sm md:hidden" @click="showMobileChannels = true">
              <i class="fa-solid fa-hashtag"></i> {{ t('chats.channels') }}
            </button>
            <button v-if="canManage" class="btn btn-sm btn-primary" @click="openCreate">
              <i class="fa-solid fa-plus"></i> {{ t('chats.newChannel') }}
            </button>
          </div>
        </div>

        <template v-else>
          <div class="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-base-content/10 bg-base-100">
            <button class="btn btn-ghost btn-xs md:hidden" @click="showMobileChannels = true">
              <i class="fa-solid fa-hashtag"></i>
            </button>
            <div class="min-w-0 flex-1">
              <div class="text-xs font-semibold truncate"># {{ activeChat.name }}</div>
              <div class="text-[10px] text-base-content/40 truncate">
                {{ activeTeam?.name || 'No team' }}
                <template v-if="activeProject"> · {{ activeProject.name }}</template>
              </div>
            </div>
            <button
              v-if="activeTeam && canManage"
              class="btn btn-ghost btn-xs gap-1"
              :disabled="busy"
              title="Sync the channel roster with the team"
              @click="syncMembers"
            >
              <i class="fa-solid fa-users-between-lines"></i>
              <span class="hidden sm:inline">Sync members</span>
            </button>
            <button
              v-if="canManage"
              class="btn btn-ghost btn-xs btn-square text-error"
              :disabled="busy"
              title="Delete channel"
              @click="removeChannel"
            >
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
          <ChatView :chat="activeChat" channel-mode class="flex-1 min-h-0" />
        </template>
      </div>
    </div>

    <!-- Create channel -->
    <div v-if="showCreate" class="modal modal-open" role="dialog" aria-modal="true">
      <div class="modal-box max-w-lg">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-bold text-lg">{{ t('chats.newChannel') }}</h3>
          <button class="btn btn-ghost btn-sm btn-square" aria-label="Close" @click="showCreate = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="grid gap-3 md:grid-cols-2">
          <label class="form-control md:col-span-2">
            <span class="label-text text-xs">Name</span>
            <input v-model="form.name" class="input input-sm input-bordered" placeholder="platform" autofocus
                   @keyup.enter="createChannel" />
          </label>
          <label class="form-control md:col-span-2">
            <span class="label-text text-xs">Description</span>
            <input v-model="form.description" class="input input-sm input-bordered" placeholder="What is this channel for?" />
          </label>
          <label class="form-control">
            <span class="label-text text-xs">Team (sets the roster)</span>
            <select v-model="form.team_id" class="select select-sm select-bordered">
              <option value="">— no team —</option>
              <option v-for="team in teams" :key="team.id" :value="team.id">
                {{ team.name }} ({{ team.human_count }} human · {{ team.ai_count }} AI)
              </option>
            </select>
          </label>
          <label class="form-control">
            <span class="label-text text-xs">Hosted by project</span>
            <select v-model="form.project_id" class="select select-sm select-bordered">
              <option v-for="project in projects" :key="project.project_id" :value="project.project_id">
                {{ project.name || project.project_id }}
              </option>
            </select>
          </label>
        </div>

        <p v-if="formError" class="text-error text-xs mt-3">{{ formError }}</p>
        <p class="text-[10px] text-base-content/40 mt-3">
          Messages live in the selected project, so mentions, attachments, unread badges and AI replies use the existing Messenger.
        </p>

        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="showCreate = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="busy" @click="createChannel">
            Create channel
          </button>
        </div>
      </div>
      <div class="modal-backdrop" @click="showCreate = false"></div>
    </div>
  </div>
</template>

<!-- Made with ❤️ by codx-junior -->
