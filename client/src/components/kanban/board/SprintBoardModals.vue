<script setup>
import ChatCostSelector from '../../chat/ChatCostSelector.vue'
import ChatInputBox from '../../chat/ChatInputBox.vue'
import ChatIntelliSense from '../../chat/ChatIntelliSense.vue'
import ChatAttachmentPreview from '../../chat/ChatAttachmentPreview.vue'
import ChatModeSelector from '../../chat/ChatModeSelector.vue'
import ChatProfileSelector from '../../chat/ChatProfileSelector.vue'
import IconSelector from '../../ui/IconSelector.vue'
import ProjectDetailt from '../../ProjectDetailt.vue'
import BoardQuickChat from './BoardQuickChat.vue'
import { getCurrentInstance } from 'vue'

const instance = getCurrentInstance()
defineExpose({
  openNewTask: (...args) => instance.proxy.openNewTask(...args),
  openAddExisting: (...args) => instance.proxy.openAddExisting(...args),
  openNewColumn: (...args) => instance.proxy.openNewColumn(...args),
  openNewBoard: (...args) => instance.proxy.openNewBoard(...args),
  openEditBoard: (...args) => instance.proxy.openEditBoard(...args),
})
</script>

<template>
  <div class="sprint-board-modals">

    <!-- ── The single New Task / Subtask dialog (boards and sprints) ── -->
    <!-- It is the same BoardQuickChat used by the sidebar New entry: one
         surface for normal tasks, cross-project tickets and budgets. -->
    <BoardQuickChat
      v-if="quickChatOpen"
      :board-id="quickChat.boardId"
      :board-type="quickChat.boardType"
      :board-name="quickChat.boardName"
      :columns="quickChat.columns"
      :default-profiles="quickChat.profiles"
      :initial-column="quickChat.initialColumn"
      :initial-status="quickChat.initialStatus"
      :initial-project-id="quickChat.projectId"
      :parent-id="quickChat.parentId"
      :mode="quickChat.mode || 'task'"
      schedule-enabled
      @close="quickChatOpen = false"
    />

    <!-- ── Add existing tasks Modal ── -->
    <modal close @close="closeAddExisting" v-if="showAddExisting">
      <div class="p-1 flex flex-col max-h-[70vh]">
        <h2 class="font-bold text-base mb-3 flex items-center gap-2">
          <i class="fa-solid fa-folder-plus text-primary text-sm"></i>
          Add existing tasks
        </h2>

        <!-- Search -->
        <label class="input input-sm input-bordered flex items-center gap-2 w-full mb-3 shrink-0">
          <i class="fa-solid fa-magnifying-glass text-xs text-base-content/40"></i>
          <input
            v-model="candidateSearch"
            type="text"
            placeholder="Search tasks across boards…"
            class="grow bg-transparent outline-none text-xs min-w-0"
          />
        </label>

        <!-- Candidates -->
        <div class="flex-1 overflow-y-auto pr-1 flex flex-col gap-1 min-h-0">
          <label
            v-for="t in candidateTasks"
            :key="t.id"
            class="flex items-center gap-3 px-2 py-2 rounded-lg cursor-pointer border transition-colors"
            :class="selected.includes(t.id)
              ? 'border-primary/40 bg-primary/10'
              : 'border-transparent hover:bg-base-content/5'"
          >
            <input type="checkbox" class="checkbox checkbox-xs checkbox-primary" :value="t.id" v-model="selected" />
            <i :class="[statusIcon(t.status), 'text-xs w-3 text-center shrink-0']"></i>
            <span class="flex-1 text-sm truncate">{{ t.name || 'Untitled' }}</span>
            <span class="text-[10px] text-base-content/40 shrink-0">
              {{ t.board || t.column || '—' }} · {{ statusLabel(t.status) }}
            </span>
          </label>

          <div
            v-if="!candidateTasks.length"
            class="flex flex-col items-center gap-2 py-10 text-base-content/25"
          >
            <i class="fa-solid fa-inbox text-2xl"></i>
            <span class="text-xs">No tasks available to add</span>
          </div>
        </div>

        <!-- Actions -->
        <div class="flex items-center justify-between gap-2 pt-3 mt-2 border-t border-base-100 shrink-0">
          <span class="text-xs text-base-content/40">{{ selected.length }} selected</span>
          <div class="flex gap-2">
            <button class="btn btn-ghost btn-sm" @click="closeAddExisting">Cancel</button>
            <button
              class="btn btn-primary btn-sm gap-1.5"
              @click="confirmAddExisting"
              :disabled="!selected.length"
            >
              <i class="fa-solid fa-check text-xs"></i> Add selected
            </button>
          </div>
        </div>
      </div>
    </modal>

    <!-- ── New / Edit Column Modal (kanban & sprint boards) ── -->
    <modal close @close="closeNewColumn" v-if="showNewColumn">
      <div class="p-1">
        <h2 class="font-bold text-base mb-4 flex items-center gap-2">
          <i class="fa-solid fa-table-columns text-primary text-sm"></i>
          {{ newColumn.id ? 'Edit Column' : 'New Column' }}
        </h2>

        <!-- Column name (UI title) -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Column name</span>
          </label>
          <input
            ref="colNameInput"
            v-model="newColumn.title"
            type="text"
            placeholder="e.g. Review, Blocked, Testing…"
            class="input input-bordered input-sm w-full"
            @keydown.enter="confirmNewColumn"
          />
          <label class="label py-0.5" v-if="columnError">
            <span class="label-text-alt text-error">{{ columnError }}</span>
          </label>
        </div>

        <!-- Status value — sprint boards only: the chat.status a task takes here -->
        <div v-if="!isKanban" class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Status <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <input
            v-model="newColumn.status"
            type="text"
            placeholder="Defaults to the column name"
            class="input input-bordered input-sm w-full"
            @keydown.enter="confirmNewColumn"
          />
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-center gap-1">
            <i class="fa-solid fa-circle-info"></i>
            Chat status assigned to the tasks placed in this column. Defaults to the column name.
          </p>
        </div>

        <!-- Icon — sprint boards only: a Font Awesome icon shown in the column header -->
        <div v-if="!isKanban" class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Icon <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <IconSelector v-model="newColumn.icon" label="Column icon" />
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-center gap-1">
            <i class="fa-solid fa-circle-info"></i>
            Shown at the start of the column header. Leave empty to fall back to the colour dot.
          </p>
        </div>

        <!-- Color picker -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Color</span>
          </label>
          <div class="flex gap-2.5 flex-wrap">
            <span
              v-for="color in columnColors"
              :key="color"
              @click="newColumn.color = color"
              class="w-6 h-6 rounded-full cursor-pointer transition-transform hover:scale-110"
              :style="`background: ${color}`"
              :class="newColumn.color === color ? 'ring-2 ring-offset-2 ring-base-content/50' : ''"
            ></span>
          </div>
        </div>

        <!-- Project assigned to the tasks created in this column -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Project <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
            <button
              v-if="newColumn.project_id"
              type="button"
              class="label-text-alt text-[10px] text-error hover:underline"
              title="Clear project"
              @click="newColumn.project_id = null"
            >
              Clear
            </button>
          </label>
          <ProjectDetailt
            :model-value="selectedColumnProject"
            @update:model-value="onColumnProjectSelected"
            @select="onColumnProjectSelected"
          />
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-center gap-1">
            <i class="fa-solid fa-circle-info"></i>
            Tasks created in this column are assigned to this project. Leave empty to use the board's project.
          </p>
        </div>

        <!-- Default profiles inherited by tasks created in this column -->
        <div class="form-control mb-4">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Profiles <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
            <span
              v-if="newColumn.profiles.length"
              class="label-text-alt text-[10px] text-base-content/40"
            >{{ newColumn.profiles.length }} selected</span>
          </label>
          <ChatProfileSelector
            :project="$project"
            :selected-profiles="newColumn.profiles"
            @update:selected-profiles="onColumnProfilesSelected"
          />
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-center gap-1">
            <i class="fa-solid fa-circle-info"></i>
            Pre-selected on tasks created in this column.
          </p>
        </div>

        <!-- Danger zone — remove the column (sprint boards opt in) -->
        <div v-if="allowColumnDelete && newColumn.id" class="form-control mb-4 pt-3 border-t border-base-100">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-error">Danger zone</span>
          </label>
          <button
            type="button"
            class="btn btn-sm btn-outline btn-error w-full justify-start gap-2"
            :disabled="columns.length <= 1"
            @click="deleteColumn"
          >
            <i class="fa-solid fa-trash-can text-xs"></i>
            Delete column
          </button>
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-center gap-1">
            <i class="fa-solid fa-circle-info"></i>
            <template v-if="columns.length <= 1">
              A board must keep at least one column.
            </template>
            <template v-else>
              The tasks of this column are moved to the first remaining column.
            </template>
          </p>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 justify-end">
          <button class="btn btn-ghost btn-sm" @click="closeNewColumn">Cancel</button>
          <button
            class="btn btn-primary btn-sm gap-1.5"
            @click="confirmNewColumn"
            :disabled="!newColumn.title?.trim()"
          >
            <i class="fa-solid fa-check text-xs"></i>
            {{ newColumn.id ? 'Save' : 'Create' }}
          </button>
        </div>
      </div>
    </modal>

    <!-- ── New Board Modal ── -->
    <modal close @close="closeNewBoard" v-if="showNewBoard">
      <div class="p-1">
        <h2 class="font-bold text-base mb-4 flex items-center gap-2">
          <i class="fa-brands fa-trello text-primary text-sm"></i>
          New Board
        </h2>

        <!-- Owning team -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Owning team</span>
            <span class="label-text-alt text-[10px] text-base-content/40">Optional</span>
          </label>
          <select v-model="newBoard.team_id" class="select select-bordered select-sm w-full">
            <option value="">— no team —</option>
            <option v-for="team in teams" :key="team.id" :value="team.id">
              {{ team.name }}{{ team.lead_name ? ` · lead ${team.lead_name}` : '' }}
            </option>
          </select>
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-start gap-1">
            <i class="fa-solid fa-circle-info text-[10px] mt-0.5 shrink-0"></i>
            <span>
              Boards are managed by teams: the team lead is the responsible person for this board.
              A sprint is just a board used by its team for a time-boxed goal.
            </span>
          </p>
        </div>

        <!-- Board name -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Board name</span>
          </label>
          <input
            ref="boardNameInput"
            v-model="newBoard.title"
            type="text"
            placeholder="e.g. Sprint Q4, Roadmap 2026…"
            class="input input-bordered input-sm w-full"
            @keydown.enter="confirmNewBoard"
          />
        </div>

        <!-- Description -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Description <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <textarea
            v-model="newBoard.description"
            rows="2"
            placeholder="What is this board for?"
            class="textarea textarea-bordered textarea-sm w-full resize-none"
          ></textarea>
        </div>

        <!-- Background image -->
        <div class="form-control mb-4">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Background image <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <input
            v-model="newBoard.background"
            type="text"
            placeholder="https://example.com/image.jpg"
            class="input input-bordered input-sm w-full"
          />
          <div
            v-if="newBoard.background"
            class="mt-3 h-24 rounded bg-cover bg-center opacity-60"
            :style="{ backgroundImage: `url(${newBoard.background})` }"
          ></div>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 justify-end">
          <button class="btn btn-ghost btn-sm" @click="closeNewBoard">Cancel</button>
          <button
            class="btn btn-primary btn-sm gap-1.5"
            @click="confirmNewBoard"
            :disabled="!newBoard.title?.trim()"
          >
            <i class="fa-solid fa-plus text-xs"></i> Create Board
          </button>
        </div>
      </div>
    </modal>

    <!-- ── Board settings Modal (name · description · background · assistant · participants) ── -->
    <modal close @close="closeEditBoard" v-if="showEditBoard">
      <div class="p-1">
        <h2 class="font-bold text-base mb-4 flex items-center gap-2">
          <i class="fa-solid fa-gear text-primary text-sm"></i>
          Board settings
        </h2>

        <!-- Name -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Board name</span>
          </label>
          <input
            ref="editBoardNameInput"
            v-model="editBoardForm.title"
            type="text"
            placeholder="Board name"
            class="input input-bordered input-sm w-full"
            @keydown.enter="confirmEditBoard"
          />
        </div>

        <!-- Description -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Description <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <textarea
            v-model="editBoardForm.description"
            rows="2"
            placeholder="What is this board for?"
            class="textarea textarea-bordered textarea-sm w-full resize-none"
          ></textarea>
        </div>

        <!-- Background image -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">
              Background image <span class="normal-case font-normal opacity-50">(optional)</span>
            </span>
          </label>
          <input
            v-model="editBoardForm.background"
            type="text"
            placeholder="https://example.com/image.jpg"
            class="input input-bordered input-sm w-full"
          />
          <div
            v-if="editBoardForm.background"
            class="mt-3 h-24 rounded bg-cover bg-center opacity-60"
            :style="{ backgroundImage: `url(${editBoardForm.background})` }"
          ></div>
        </div>

        <!-- Owning team: its lead is responsible for the board -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Owning team</span>
            <span class="label-text-alt text-[10px] text-base-content/40">
              {{ teamLead(editBoardForm.team_id) ? `lead · ${teamLead(editBoardForm.team_id)}` : 'unassigned' }}
            </span>
          </label>
          <select v-model="editBoardForm.team_id" class="select select-bordered select-sm w-full">
            <option value="">— no team —</option>
            <option v-for="team in teams" :key="team.id" :value="team.id">
              {{ team.name }}{{ team.lead_name ? ` · lead ${team.lead_name}` : '' }}
            </option>
          </select>
          <p class="text-[10px] text-base-content/40 mt-1.5">
            The team lead is the person responsible for this board.
          </p>
        </div>

        <!-- Board assistant: the profile that answers the board chat -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Board assistant</span>
            <span class="label-text-alt text-[10px] text-base-content/40">
              {{ editBoardForm.assignee ? editBoardForm.assignee : 'ProjectManager (default)' }}
            </span>
          </label>
          <ChatProfileSelector
            :project="boardProject || $project"
            single
            allow-deselect
            label="Assistant"
            icon="fa-solid fa-user-tie"
            empty-label="ProjectManager (default)"
            :selected-profiles="editBoardAssigneeProfiles"
            @update:selected-profiles="onAssigneeSelected"
          />
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-start gap-1">
            <i class="fa-solid fa-circle-info text-[10px] mt-0.5 shrink-0"></i>
            <span>
              The profile that answers this board's chat (status, orders, follow-up). Leave it empty to use
              <b>ProjectManager</b>.
            </span>
          </p>
        </div>

        <!-- Board manager: the profile that reviews this board in the background -->
        <div class="form-control mb-3">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Board manager</span>
            <span class="label-text-alt text-[10px] text-base-content/40">
              {{ editBoardForm.manager?.enabled ? 'enabled' : 'disabled' }}
            </span>
          </label>
          <button
            type="button"
            class="btn btn-sm btn-ghost border border-base-100 w-full justify-start gap-1.5"
            @click="openBoardManager"
          >
            <i class="fa-solid fa-user-tie text-xs text-primary"></i>
            <span class="text-xs font-medium truncate">
              {{ editBoardForm.manager?.profile || editBoardForm.assignee || 'ProjectManager (default)' }}
            </span>
            <span class="text-[10px] text-base-content/40 ml-auto">Configure</span>
            <i class="fa-solid fa-chevron-right text-[10px] text-base-content/30"></i>
          </button>
          <p class="text-[10px] text-base-content/40 mt-1.5 flex items-start gap-1">
            <i class="fa-solid fa-circle-info text-[10px] mt-0.5 shrink-0"></i>
            <span>
              A background review keeps this board healthy: move cards, comment, close finished tasks.
            </span>
          </p>
        </div>

        <!-- Participants (user list) -->
        <div class="form-control mb-4">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-base-content/50">Participants</span>
            <span class="label-text-alt text-[10px] text-base-content/40">Optional</span>
          </label>
          <div class="dropdown">
            <button
              type="button"
              tabindex="0"
              class="btn btn-sm btn-ghost border border-base-100 w-full justify-start gap-1.5"
            >
              <i class="fa-solid fa-users text-xs"></i>
              <span class="text-xs font-medium">
                {{ editBoardForm.users.length ? `${editBoardForm.users.length} participants` : 'Add participants' }}
              </span>
            </button>
            <ul
              tabindex="0"
              class="dropdown-content menu bg-base-200 border border-base-100 rounded-box z-40 w-56 max-h-56 overflow-y-auto p-1 mt-1 shadow-lg"
            >
              <li v-for="u in allUsers" :key="u.username">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    class="checkbox checkbox-xs checkbox-primary"
                    :value="u.username"
                    v-model="editBoardForm.users"
                  />
                  <div class="avatar placeholder">
                    <div class="w-5 h-5 rounded-full bg-base-300 text-[9px] text-base-content/60">
                      <img v-if="u.avatar" :src="u.avatar" :alt="u.username" />
                      <span v-else>{{ initial(u) }}</span>
                    </div>
                  </div>
                  <span class="text-xs truncate">{{ u.username }}</span>
                </label>
              </li>
              <li v-if="!allUsers.length" class="menu-title text-xs opacity-40">No users</li>
            </ul>
          </div>
          <!-- Selected participants -->
          <div v-if="editBoardForm.users.length" class="flex flex-wrap gap-1 mt-2">
            <span
              v-for="name in editBoardForm.users"
              :key="name"
              class="badge badge-sm badge-ghost gap-1"
            >
              <i class="fa-solid fa-user text-[10px]"></i>{{ name }}
              <button
                type="button"
                class="hover:text-error"
                :aria-label="`Remove ${name}`"
                @click.stop="toggleBoardUser(name)"
              >
                <i class="fa-solid fa-xmark text-[10px]"></i>
              </button>
            </span>
          </div>
        </div>

        <!-- Danger zone — delete the whole board -->
        <div class="form-control mb-4 pt-3 border-t border-base-100">
          <label class="label py-1">
            <span class="label-text text-xs font-semibold uppercase tracking-wider text-error">Danger zone</span>
          </label>
          <button
            type="button"
            class="btn btn-sm btn-outline btn-error w-full justify-start gap-2"
            @click="openDeleteBoard"
          >
            <i class="fa-solid fa-trash-can text-xs"></i>
            Delete board
          </button>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 justify-end">
          <button class="btn btn-ghost btn-sm" @click="closeEditBoard">Cancel</button>
          <button
            class="btn btn-primary btn-sm gap-1.5"
            @click="confirmEditBoard"
            :disabled="!editBoardForm.title?.trim()"
          >
            <i class="fa-solid fa-check text-xs"></i> Save
          </button>
        </div>
      </div>
    </modal>

    <!-- ── Delete Board confirmation Modal ── -->
    <modal close @close="closeDeleteBoard" v-if="showDeleteBoard">
      <div class="p-1 flex flex-col gap-3">
        <div class="flex items-center gap-2 text-error">
          <i class="fa-solid fa-triangle-exclamation text-xl"></i>
          <h2 class="font-bold text-base">Delete board</h2>
        </div>

        <p class="text-sm text-base-content/70 leading-relaxed">
          Are you sure you want to delete <b>{{ boardName }}</b>? This action cannot be undone.
        </p>

        <!-- Kanban only: ask whether the tasks should be deleted too -->
        <label
          v-if="isKanban"
          class="flex items-start gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors"
          :class="deleteBoardChats ? 'border-error/40 bg-error/5' : 'border-base-100 hover:border-base-content/20'"
        >
          <input
            type="checkbox"
            class="checkbox checkbox-xs checkbox-error mt-0.5"
            v-model="deleteBoardChats"
          />
          <span class="flex flex-col gap-0.5">
            <span class="text-sm text-base-content/80">
              Also delete its {{ deleteTaskCount }} task{{ deleteTaskCount === 1 ? '' : 's' }}
            </span>
            <span class="text-xs text-base-content/40">
              <template v-if="deleteBoardChats">
                The tasks and all their messages will be permanently removed.
              </template>
              <template v-else>
                The tasks are kept and moved out of the board — they will show under “No board”.
              </template>
            </span>
          </span>
        </label>

        <!-- Sprint: tasks are never deleted, they just leave the sprint -->
        <div v-else class="flex gap-2 items-start p-2.5 rounded-lg bg-info/5 border border-info/15">
          <i class="fa-solid fa-circle-info text-info text-xs mt-0.5 shrink-0"></i>
          <span class="text-xs text-base-content/60">
            The {{ deleteTaskCount }} task{{ deleteTaskCount === 1 ? '' : 's' }} are kept and only removed
            from this sprint — nothing else is deleted.
          </span>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 justify-end">
          <button type="button" class="btn btn-ghost btn-sm" @click="closeDeleteBoard">Cancel</button>
          <button
            type="button"
            class="btn btn-error btn-sm gap-1.5"
            :disabled="deletingBoard"
            @click="confirmDeleteBoard"
          >
            <span v-if="deletingBoard" class="loading loading-spinner loading-xs"></span>
            <i v-else class="fa-solid fa-trash-can text-xs"></i>
            Delete board
          </button>
        </div>
      </div>
    </modal>

  </div>
</template>

<script>
import ChatAttachment from '@/api/model/ChatAttachment.js'
import { API } from '@/api/api'

// Attachments travel inline as base64 — keep them reasonably small
const MAX_ATTACHMENT_SIZE_MB = 50

const COLUMN_COLORS = [
  '#6366f1', '#f59e0b', '#10b981', '#ef4444',
  '#3b82f6', '#8b5cf6', '#ec4899', '#14b8a6'
]

const BOARD_TYPES = [
  { value: 'kanban', label: 'Kanban', icon: 'fa-solid fa-columns' },
  { value: 'sprint', label: 'Sprint', icon: 'fa-solid fa-layer-group' },
]

const DEFAULT_NEW_TASK = () => ({
  name: '',
  // 'task' = normal card on this board; 'ticket' = request placed on another project
  processing: 'task',
  ticket_target: '',
  ticket_autonomous: true,
  mode: 'chat',
  status: 'todo',
  // Kanban destination column (chat.column). Ignored by sprint boards.
  column: '',
  parentId: null,
  selectedProfiles: [],
  llm_model: null,
  project_id: null,
  users: [],
  deadline: null,
  // "Non urgent": arm the task outside the human working window instead of
  // expecting it to be worked on now (independent of the deadline)
  nonUrgent: false,
  estimation_cost: null,
  // Files referenced from the message (dropped on the composer or @filename)
  files: [],
  attachments: []
})

const DEFAULT_NEW_COLUMN = () => ({
  // `id` is set only while editing an existing column
  id: null,
  // UI label
  title: '',
  // Chat status the column writes (sprint only; falls back to the title)
  status: '',
  // Font Awesome icon shown in the header (sprint only; optional)
  icon: '',
  color: COLUMN_COLORS[0],
  // Profiles pre-selected on tasks created in this column
  profiles: [],
  // Project tasks created in this column are assigned to (optional)
  project_id: null
})

const DEFAULT_NEW_BOARD = () => ({
  title: '',
  description: '',
  background: '',
  // A board is managed by a team; its lead is the responsible person.
  team_id: '',
  type: 'kanban'
})

const DEFAULT_EDIT_BOARD = () => ({
  originalTitle: null,
  title: '',
  description: '',
  background: '',
  // Owning team: its lead is the responsible person for the board.
  team_id: '',
  // Profile name the board is assigned to (its chat assistant) — optional
  assignee: null,
  // Background board manager configuration (opt-in)
  manager: null,
  users: []
})

export default {
  name: 'SprintBoardModals',
  props: {
    // Sprint membership id (add-existing filter). Also used as board id for kanban.
    sprintId: { type: String, default: null },
    sprintTitle: { type: String, default: 'Sprint' },
    // 'kanban' | 'sprint' — drives the column picker & context copy
    boardType: { type: String, default: 'sprint' },
    // Display name of the active board (kanban uses it instead of sprintTitle)
    boardTitle: { type: String, default: null },
    // Board columns ({ id, title, icon?, color, status?, profiles?, project_id? })
    columns: { type: Array, default: () => [] },
    // Column being edited (opens the column dialog in "edit" mode)
    editingColumn: { type: Object, default: null },
    // Show the "Delete column" action inside the column dialog (sprint boards)
    allowColumnDelete: { type: Boolean, default: false },
    // Compact layout for narrow hosts (e.g. the solopreneur quick ticket on
    // mobile): optional fields are hidden and spacing/controls are reduced.
    compact: { type: Boolean, default: false },
    // Profiles pre-selected when the dialog opens, BEFORE the profiles the
    // destination column pre-selects. Used by launchers that create a task on
    // behalf of a profile (e.g. the sidebar "New" entry → codx-junior).
    defaultProfiles: { type: Array, default: () => [] }
  },
  emits: [
    'task-created', 'tasks-added', 'column-created', 'column-updated', 'column-deleted',
    'board-created', 'board-updated', 'board-delete', 'board-manager',
    'ticket-created'
  ],
  data() {
    return {
      showNewTask: false,
      // Single new-task dialog for boards and sprints (BoardQuickChat)
      quickChatOpen: false,
      quickChat: {},
      showAddExisting: false,
      showNewColumn: false,
      showNewBoard: false,
      showEditBoard: false,
      showDeleteBoard: false,
      newTask: DEFAULT_NEW_TASK(),
      newColumn: DEFAULT_NEW_COLUMN(),
      newBoard: DEFAULT_NEW_BOARD(),
      editBoardForm: DEFAULT_EDIT_BOARD(),
      // Delete-board dialog state. `deleteBoardChats` is the kanban-only choice
      // of also deleting the board's tasks.
      deleteBoardChats: true,
      deletingBoard: false,
      columnError: null,
      columnColors: COLUMN_COLORS,
      boardTypes: BOARD_TYPES,
      teams: [],
      candidateSearch: '',
      selected: [],
      // Cross-project ticket targets and their loading/error state.
      ticketTargets: [],
      ticketTargetsLoading: false,
      ticketError: '',
      // Mirror of the composer text so the "Run" button knows when there is
      // content. Kept in sync through `update:cursor-word`, which the composer
      // emits on every keystroke (no extra events needed).
      composerText: '',
      cursorWord: {},
      creating: false,
      // ── @-mention file intellisense (same shape as the chat composer) ──
      intelliSenseSuggestions: [],
      intelliSenseIndex: 0,
      intelliSenseQuery: '',
      intelliSenseProgress: '',
      intelliSenseDebounce: null,
      intelliSenseDismissed: false,
      searchController: null,
      previousQuery: null
    }
  },
  mounted() {
    // Users are needed by the assignee picker and the board participants list
    if (!this.$storex.users.users?.length) {
      this.$storex.users.loadUsers().catch(() => {})
    }
    this.loadTeams()
  },
  unmounted() {
    clearTimeout(this.intelliSenseDebounce)
    this.cancelIntelliSense()
  },
  computed: {
    selectedTicketTarget() {
      return this.ticketTargets.find(
        (target) => (target.project_id || target.project_name) === this.newTask.ticket_target
      ) || null
    },
    // ── Board flavour ────────────────────────────────────────────────────────
    isKanban() {
      return this.boardType === 'kanban'
    },
    boardName() {
      return this.boardTitle || this.sprintTitle || 'Board'
    },
    currentColumnLabel() {
      return this.newTask.column || 'uncategorized'
    },
    // Board id/title of the board being edited/deleted. Boards are keyed by
    // title, so the sprint id and the board title are the same value.
    boardId() {
      return this.sprintId || this.boardTitle || this.editBoardForm.originalTitle || null
    },
    // Project the board belongs to: its profiles are the ones the board
    // assistant can be picked from (may differ from the active project).
    boardProject() {
      if (!this.boardId) return null
      const board = this.$storex.kanban.boardById(this.boardId)
      return this.$storex.projects.allProjectsById?.[board?.project_id] || null
    },
    // Selected assistant, as the profile selector expects (list of profiles)
    editBoardAssigneeProfiles() {
      const name = this.editBoardForm.assignee
      return name ? [{ name }] : []
    },
    // Number of tasks affected by deleting this board (kanban vs sprint)
    deleteTaskCount() {
      if (!this.boardId) return 0
      return this.isKanban
        ? (this.$storex.kanban.boardChats(this.boardId)?.length || 0)
        : (this.$storex.kanban.sprintBoardChats(this.boardId)?.length || 0)
    },

    allChats() {
      return Object.values(this.$storex.chats.chats || {})
    },
    // Chats not already in this sprint (and not templates / channels)
    candidateTasks() {
      const q = this.candidateSearch?.trim().toLowerCase()
      return this.allChats
        .filter(c => c?.id && !c.is_template && c.mode !== 'channel')
        .filter(c => !(c.sprints || []).includes(this.sprintId))
        .filter(c => !q || (c.name || '').toLowerCase().includes(q))
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    },
    // Children of the parent task that have NOT been added to this sprint
    parentChildren() {
      const parentId = this.newTask.parentId
      if (!parentId) return []
      return this.allChats
        .filter(c => c?.id && c.parent_id === parentId)
        .filter(c => !(c.sprints || []).includes(this.sprintId))
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    },
    parentName() {
      if (!this.newTask.parentId) return null
      return this.$storex.chats.chats?.[this.newTask.parentId]?.name || null
    },
    // Column (status) the new task will be created in. The sprint column is
    // resolved from the board columns (its status, or its title when no status
    // was defined) so the label matches the board; otherwise the canonical
    // status config is used.
    newTaskStatus() {
      const value = this.newTask.status
      const column = this.columns.find(c => (c.status || c.title) === value)
      if (column) {
        return {
          label: column.title,
          textColor: column.textColor || 'text-base-content/60'
        }
      }
      return this.$storex.chats.statusConfig(value)
    },

    // Currently selected project (null ⇒ ProjectDetailt falls back to $project)
    selectedProject() {
      const id = this.newTask.project_id
      if (!id) return null
      return this.$storex.projects.allProjectsById?.[id] || null
    },
    // Project picked for the column being edited (null ⇒ the board's project)
    selectedColumnProject() {
      const id = this.newColumn.project_id
      if (!id) return null
      return this.$storex.projects.allProjectsById?.[id] || null
    },
    // Project the composer works against — same resolution as the chat composer
    chatProject() {
      return this.$storex.projects.allProjectsById?.[this.newTask.project_id] || this.$project
    },
    // Profiles & models come from the same sources as the chat composer
    profiles() {
      return this.chatProject?.$state?.profiles || []
    },
    aiModels() {
      return this.$storex.projects.ai.models || []
    },
    // The composer toolbar selects profiles as items, the task stores their names
    selectedProfiles() {
      return this.profiles.filter(p => this.newTask.selectedProfiles.includes(p.name))
    },
    // Files a task can run with: dropped paths + @file references in the message
    composerFiles() {
      return this.resolveFiles(this.composerText)
    },
    // A task can only be run when it carries an initial prompt / file / image
    hasInitialContent() {
      return !!(this.composerText?.trim() || this.composerFiles.length || this.newTask.attachments.length)
    },
    // Users available for assignment / board participants
    allUsers() {
      return this.$storex.users.users || []
    },
    selectedUsers() {
      return this.newTask.users.map(name =>
        this.allUsers.find(u => u.username === name) || { username: name, avatar: null }
      )
    },
    // The "Urgent" shortcut is on while the task is due tomorrow
    isUrgent() {
      return !!this.newTask.deadline && this.newTask.deadline === this.dateISO(1)
    },
    // The name field is a multiline brief, so it grows with the content
    // (capped, otherwise a pasted document would push the actions off-screen)
    nameRows() {
      // Explicit newlines plus the wrapped length of long lines: a one-line
      // brief stays one row, a pasted document is capped so the dialog fits.
      const wrapped = String(this.newTask.name || '')
        .split('\n')
        .reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / 68)), 0)
      return Math.min(6, Math.max(1, wrapped))
    }
  },
  watch: {
    // The composer selectors follow the project picked in the dialog
    chatProject(project) {
      if (project && this.showNewTask) this.initComposerProject()
    },
    // Every composer keystroke refreshes the @-mention search
    composerText() {
      this.scheduleIntelliSense()
    },
    // Kanban: switching the destination column re-applies what the column
    // defines for its tasks (profiles + project)
    'newTask.column'(title) {
      if (!this.isKanban) return
      const column = this.findColumn({ title })
      if (!column) return
      this.newTask.selectedProfiles = [...(column.profiles || [])]
      this.newTask.project_id = column.project_id || null
    },
    // Board view sets the column to edit before opening the dialog
    editingColumn(col) {
      if (col) {
        this.newColumn = {
          id: col.id || col.status || col.title || null,
          title: col.title || '',
          // Prefill with the effective status (the title when none was defined)
          status: col.status || col.title || '',
          // Prefill with the column's icon (may be the canonical status icon)
          icon: col.icon || '',
          color: col.color || COLUMN_COLORS[0],
          profiles: [...(col.profiles || [])],
          project_id: col.project_id || null
        }
      }
    }
  },
  methods: {
    statusIcon(status) {
      return this.$storex.chats.statusIcon(status)
    },
    statusLabel(status) {
      return this.$storex.chats.statusLabel(status)
    },
    initial(user) {
      return (user.username || user.name || '?')[0].toUpperCase()
    },
    // Normalize the cost budget draft to a positive number or null (no budget)
    normalizeCost(value) {
      const num = Number(value)
      if (value === '' || value === null || value === undefined || Number.isNaN(num) || num <= 0) {
        return null
      }
      return num
    },
    fileName(path) {
      return path?.split('/').reverse()[0] || path
    },
    // Resolve the board column that matches a kanban title and/or a sprint status
    findColumn({ title, status } = {}) {
      return (this.columns || []).find(c =>
        (title && c.title === title) ||
        (status && (c.status || c.title) === status)
      ) || null
    },

    // ── Initial message: composer wiring ─────────────────────────────────────
    // The chat composer needs the project's profiles and models to render its
    // selectors — initialize the project exactly like the chat does.
    async initComposerProject() {
      const project = this.chatProject
      if (!project) return
      try {
        await this.$storex.projects.initProjectState(project)
      } catch (error) {
        console.error('[SprintBoardModals] Could not initialize the composer project:', error)
      }
    },
    onCursorWord(wordInfo) {
      this.cursorWord = wordInfo || {}
      this.composerText = this.$refs.composer?.getEditorText() || ''
    },
    onModelChanged(modelName) {
      this.newTask.llm_model = modelName || null
    },
    onProfilesSelected(profiles) {
      this.newTask.selectedProfiles = (profiles || []).map(p => p.name || p)
    },
    // Keys while the mention list is open navigate it; otherwise Ctrl/Cmd+Enter
    // runs the task right away, like the chat composer.
    onComposerKeyDown(event) {
      const hasSuggestions = this.intelliSenseSuggestions.length > 0
      if (hasSuggestions) {
        if (event.key === 'Tab') {
          event.preventDefault(); event.stopPropagation()
          this.onIntelliSenseSelect(this.intelliSenseSuggestions[this.intelliSenseIndex])
          return
        }
        if (event.key === 'Escape') { this.dismissIntelliSense(); return }
        if (event.key === ' ' && event.ctrlKey) {
          event.preventDefault(); event.stopPropagation()
          this.$refs.intelliSense?.toggleSelected(this.intelliSenseSuggestions[this.intelliSenseIndex])
          return
        }
        if (event.key === 'ArrowUp') {
          event.preventDefault()
          this.intelliSenseIndex = Math.max(0, this.intelliSenseIndex - 1)
          return
        }
        if (event.key === 'ArrowDown') {
          event.preventDefault()
          this.intelliSenseIndex = Math.min(this.intelliSenseSuggestions.length - 1, this.intelliSenseIndex + 1)
          return
        }
      }
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
        event.preventDefault()
        event.stopPropagation()
        this.runNewTask()
      }
    },

    // ── Initial message: file intellisense (@mentions) ───────────────────────
    // Mirrors the chat composer: typing `@` + 3 chars opens the mention search
    // so the user can pick project files to attach to the task.
    scheduleIntelliSense(word = null) {
      if (this.intelliSenseDismissed) {
        const currentWord = word || this.cursorWord.word
        if (currentWord !== this.intelliSenseQuery) this.intelliSenseDismissed = false
      }
      clearTimeout(this.intelliSenseDebounce)
      this.intelliSenseDebounce = setTimeout(() => this.runIntelliSense(word), 220)
    },
    async runIntelliSense(word = null) {
      if (this.intelliSenseDismissed) return
      const searchWord = word || this.cursorWord.word
      if (!searchWord?.startsWith('@')) { this.cancelIntelliSense(); return }
      const rawQuery = searchWord.slice(1)
      if (!rawQuery || rawQuery.trim().length < 3) { this.cancelIntelliSense(); return }
      if (this.previousQuery !== rawQuery && this.searchController) this.cancelIntelliSense()
      this.previousQuery = rawQuery
      this.searchController = await this.$storex.projects.createSearchController()
      this.intelliSenseQuery = rawQuery
      this.intelliSenseIndex = 0
      this.searchController.onProgress = ({ stage, project }) => {
        this.intelliSenseProgress = `${stage}: ${project}`
      }
      try {
        await this.chatProject?.$state?.searchMentions?.({
          query: rawQuery,
          limit: 10,
          controller: this.searchController,
          onResults: (results) => {
            if (!this.searchController?.isCancelled) {
              if (results.length === 1 && results[0].name === rawQuery) {
                this.onIntelliSenseSelect(results[0])
              } else {
                this.intelliSenseSuggestions = results
                this.intelliSenseIndex = 0
              }
            }
          }
        })
      } catch (error) {
        if (error.message !== 'Search cancelled') console.error('[IntelliSense] Search error:', error)
      }
      this.intelliSenseProgress = ''
    },
    cancelIntelliSense() {
      if (this.searchController) { this.searchController.cancel(); this.searchController = null }
      this.intelliSenseSuggestions = []
      this.intelliSenseQuery = ''
      this.previousQuery = null
    },
    onIntelliSenseSelect(suggestion) {
      const { file, name } = suggestion
      const { caretIndex, word } = this.cursorWord
      const text = this.$refs.composer?.getEditorText() || ''
      const left = text.slice(0, caretIndex - word.length)
      const right = text.slice(caretIndex)
      let insert = '@' + name
      // Files are attached to the task instead of inserted as a mention
      if (file) {
        this.addProjectFile(file)
        insert = ''
      }
      this.setComposerText(left + insert + ' ' + right)
      this.dismissIntelliSense()
      this.$nextTick(() => this.$refs.composer?.focusEditor())
    },
    onIntelliSenseAcceptMulti(items) {
      const { caretIndex, word } = this.cursorWord
      const text = this.$refs.composer?.getEditorText() || ''
      const left = text.slice(0, caretIndex - word.length)
      const right = text.slice(caretIndex)
      const mentionInserts = []
      items.forEach(({ file, name }) => {
        if (file) {
          this.addProjectFile(file)
        } else {
          mentionInserts.push('@' + name)
        }
      })
      const insert = mentionInserts.join(' ')
      this.setComposerText(left + insert + (insert ? ' ' : '') + right)
      this.dismissIntelliSense()
      this.$nextTick(() => this.$refs.composer?.focusEditor())
    },
    dismissIntelliSense() {
      this.intelliSenseDismissed = true
    },

    // ── Initial message: attachments ─────────────────────────────────────────
    async onUploadFiles(files) {
      await this.addAttachments(files)
    },
    async onComposerPaste(event) {
      const imageFile = await this.$service.chat.parseImageFromPaste(event)
      if (!imageFile) return
      event.preventDefault()
      await this.addAttachments([imageFile])
    },
    async onComposerDrop(event) {
      const files = Array.from(event.dataTransfer?.files || [])
      if (files.length) await this.addAttachments(files)
      this.addProjectFilesFromDrop(event.dataTransfer)
    },
    async addAttachments(files) {
      for (const file of files || []) {
        const attachment = await this.prepareAttachment(file)
        if (attachment) this.newTask.attachments.push(attachment)
      }
    },
    async prepareAttachment(file) {
      if ((file?.size || 0) > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024) {
        this.$ui?.addNotification?.({
          text: `File exceeds maximum size of ${MAX_ATTACHMENT_SIZE_MB}MB`,
          type: 'error'
        })
        return null
      }
      try {
        return await ChatAttachment.fromFile(file)
      } catch (error) {
        console.error('[SprintBoardModals] Failed to process attachment:', error)
        this.$ui?.addNotification?.({ text: 'Failed to process file', type: 'error' })
        return null
      }
    },
    removeAttachment(index) {
      this.newTask.attachments.splice(index, 1)
    },

    // ── Initial message: project files ───────────────────────────────────────
    // Files referenced from the message as `@file.py`, resolved against the
    // project file list (the same list the chat mention list is built from).
    resolveMentionedFiles(text) {
      const available = this.chatProject?.$state?.files || []
      const tokens = [...(text || '').matchAll(/@([^\s]+)/g)].map(m => m[1])
      if (!tokens.length) return []
      return available.filter(file =>
        tokens.includes(this.fileName(file)) || tokens.includes(file)
      )
    },
    resolveFiles(text) {
      return [...new Set([...this.newTask.files, ...this.resolveMentionedFiles(text)])]
    },
    isProjectFile(path) {
      return !!this.$storex.projects.allProjects.find(p => path?.startsWith(p.abs_project_path))
    },
    addProjectFile(file) {
      const path = file?.trim()
      if (!path || this.newTask.files.includes(path)) return
      this.newTask.files.push(path)
    },
    // A project file dropped on the composer behaves like in the chat: it is
    // attached to the message instead of being uploaded.
    addProjectFilesFromDrop(dataTransfer) {
      if (!dataTransfer) return
      const candidates = []
      const plain = dataTransfer.getData('text/plain')
      if (plain) candidates.push(...decodeURIComponent(plain).split('\n'))
      const urls = dataTransfer.getData('resourceurls')
      if (urls) {
        try {
          JSON.parse(urls).forEach(url => candidates.push(new URL(url).pathname))
        } catch (error) {
          console.error('[SprintBoardModals] Invalid dropped url list:', error)
        }
      }
      const jsonFiles = dataTransfer.getData('application/x-file-list-json')
      if (jsonFiles) {
        try {
          (JSON.parse(jsonFiles).files || []).forEach(f => { if (!f.is_dir) candidates.push(f.path) })
        } catch (error) {
          console.error('[SprintBoardModals] Invalid dropped file list:', error)
        }
      }
      candidates
        .map(path => path?.trim())
        .filter(path => path && this.isProjectFile(path))
        .forEach(path => this.addProjectFile(path))
    },
    removeComposerFile(file) {
      this.newTask.files = this.newTask.files.filter(f => f !== file)
      // The file may also be referenced from the message — drop the reference too
      const name = this.fileName(file)
      const text = this.$refs.composer?.getEditorText() || ''
      if (text.includes(`@${name}`)) {
        this.setComposerText(text.split(`@${name}`).join(''))
      }
    },
    setComposerText(text) {
      this.$refs.composer?.setEditorText(text || '')
      this.composerText = text || ''
      this.cursorWord = this.$refs.composer?.getCaretWordInfo() ?? {}
    },

    // ── New Task / Subtask ───────────────────────────────────────────────────
    // `status` is the target sprint column's status, `column` the target kanban
    // column. A column's "+" button passes its own values; otherwise the first
    // column is used (so a custom first column always receives the new task).
    // The destination column also seeds the task's default profiles & project.
    openNewTask({ parentId, status, column, mode } = {}) {
      // The board/sprint passes the column the user clicked (a column "+"
      // button, the header action or a subtask). BoardQuickChat owns the rest:
      // title, message, project, profiles, budget, schedule and ticket mode.
      const parent = parentId ? this.$storex.chats.chats?.[parentId] : null
      const targetColumn = this.findColumn({ title: column, status })
        || this.columns.find(candidate => candidate && !candidate.virtual)
        || this.columns[0]
        || null
      const profiles = [
        ...(this.defaultProfiles || []).map(profile => profile?.name || profile),
        ...(targetColumn?.profiles || [])
      ].filter(Boolean)
      this.quickChat = {
        boardId: this.sprintId || this.boardTitle || this.sprintTitle || 'tasks',
        boardType: this.boardType,
        boardName: this.boardTitle || this.sprintTitle,
        columns: this.columns,
        profiles: [...new Set(profiles)],
        initialColumn: targetColumn?.title || column || null,
        initialStatus: status || targetColumn?.status || null,
        projectId: parent?.project_id || targetColumn?.project_id || null,
        parentId: parentId || null,
        mode: mode || 'task'
      }
      this.quickChatOpen = true
    },
    closeNewTask() {
      this.showNewTask = false
      this.composerText = ''
      clearTimeout(this.intelliSenseDebounce)
      this.cancelIntelliSense()
      this.$refs.composer?.setEditorText('')
    },
    // Load the projects this one can place a ticket on. Cached for the life of
    // the component (the target list changes rarely); refreshed on open errors.
    async loadTicketTargets() {
      if (this.ticketTargetsLoading) return
      this.ticketTargetsLoading = true
      this.ticketError = ''
      try {
        const targets = await API.get('/api/tickets/targets')
        this.ticketTargets = Array.isArray(targets) ? targets : []
        if (!this.newTask.ticket_target && this.ticketTargets.length) {
          const first = this.ticketTargets[0]
          this.newTask.ticket_target = first.project_id || first.project_name
        }
      } catch (ex) {
        this.ticketTargets = []
        this.ticketError = ex?.message || 'Could not read the ticket targets'
      } finally {
        this.ticketTargetsLoading = false
      }
    },
    // Place a cross-project ticket from the same dialog. The ticket lives on the
    // TARGET project's `tickets` board; this project keeps the ledger reference.
    async placeTicket() {
      const [title, ...brief] = String(this.newTask.name).split('\n')
      const name = title.trim()
      if (!name) return
      if (!this.newTask.ticket_target) {
        this.ticketError = 'Choose the target project'
        return
      }
      const description = [
        this.newTask.name,
        (this.$refs.composer?.getEditorText() || '').trim()
      ].filter(Boolean).join('\n\n')
      this.creating = true
      this.ticketError = ''
      try {
        const record = await API.post('/api/tickets', {
          target_project: this.newTask.ticket_target,
          name,
          description,
          kind: 'change',
          content: description,
          autonomous: !!this.newTask.ticket_autonomous
        })
        this.$emit('ticket-created', record)
        this.$ui?.addNotification?.({
          text: `Ticket placed on ${record?.target_project_name || 'the target project'} (${record?.ticket_id || ''}) — accept it in the target's tickets board.`,
          type: 'success'
        })
        this.closeNewTask()
      } catch (ex) {
        this.ticketError = ex?.message || ex?.detail || 'Could not place the ticket'
      } finally {
        this.creating = false
      }
    },
    addNewTask() {
      return this.submitNewTask(false)
    },
    runNewTask() {
      return this.submitNewTask(true)
    },
    // Create the task, optionally seeding it with the initial message (text,
    // files and attachments) and (when `run` is set) processing it immediately.
    submitNewTask(run) {
      if (!this.newTask.name?.trim() || this.creating) return
      if (this.newTask.processing === 'ticket') {
        return this.placeTicket()
      }
      // Multiline brief: the first line titles the card, the remaining lines are
      // the task's first message, ahead of whatever the composer holds.
      const [title, ...brief] = String(this.newTask.name).split('\n')
      const name = title.trim()
      if (!name) return
      const message = [
        brief.join('\n').trim(),
        (this.$refs.composer?.getEditorText() || '').trim()
      ].filter(Boolean).join('\n\n')
      const files = this.resolveFiles(message)
      const attachments = this.newTask.attachments.map(a => a.toJSON?.() || a)
      const hasContent = !!(message || files.length || attachments.length)
      this.creating = true
      try {
        this.$emit('task-created', {
          name,
          mode: this.newTask.mode,
          status: this.newTask.status || 'todo',
          // Kanban destination column (ignored by sprint boards)
          column: this.newTask.column || null,
          parentId: this.newTask.parentId || null,
          profiles: [...this.newTask.selectedProfiles],
          llm_model: this.newTask.llm_model || null,
          project_id: this.newTask.project_id || null,
          users: [...this.newTask.users],
          deadline: this.newTask.deadline || null,
          estimation_cost: this.normalizeCost(this.newTask.estimation_cost),
          // "Non urgent": the board arms `wake_at` outside working hours
          nonUrgent: !!this.newTask.nonUrgent,
          message,
          files,
          attachments,
          // Run is only meaningful when there is something to send
          run: !!run && hasContent
        })
        this.closeNewTask()
      } finally {
        this.creating = false
      }
    },
    // Selecting an existing child adds it to this sprint and closes the dialog
    selectParentChild(child) {
      if (!child?.id) return
      this.$emit('tasks-added', { taskIds: [child.id] })
      this.closeNewTask()
    },
    // `ProjectDetailt` is a v-model component: the selection is delivered through
    // `update:modelValue` (its canonical event). `@select` is kept as a legacy
    // fallback. Native events that fall through to the root element carry no
    // project id and are ignored.
    onProjectSelected(project) {
      const id = typeof project === 'string'
        ? project
        : (project?.project_id || project?.id || null)
      if (!id) return
      this.newTask.project_id = id
    },
    // Same contract as `onProjectSelected`, for the column dialog's project field
    onColumnProjectSelected(project) {
      const id = typeof project === 'string'
        ? project
        : (project?.project_id || project?.id || null)
      this.newColumn.project_id = id || null
    },
    // Local YYYY-MM-DD for "today + offset" days, so the native date input and
    // the backend agree on the same day
    dateISO(offsetDays = 0) {
      const date = new Date()
      date.setDate(date.getDate() + offsetDays)
      return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
        .toISOString()
        .split('T')[0]
    },
    setDeadline(preset) {
      const offset = preset === 'tomorrow' ? 1 : preset === 'week' ? 7 : 0
      this.newTask.deadline = this.dateISO(offset)
    },
    // "Urgent" is a due-date shortcut: on ⇒ due tomorrow, off ⇒ no deadline
    toggleUrgent() {
      this.newTask.deadline = this.isUrgent ? null : this.dateISO(1)
    },
    // "Non urgent" never touches the deadline: it only decides *when* the agent
    // works on the task (outside the human working window)
    toggleNonUrgent() {
      this.newTask.nonUrgent = !this.newTask.nonUrgent
    },

    // ── Add existing ─────────────────────────────────────────────────────────
    openAddExisting() {
      this.selected = []
      this.candidateSearch = ''
      this.showAddExisting = true
    },
    closeAddExisting() {
      this.showAddExisting = false
    },
    confirmAddExisting() {
      if (!this.selected.length) return
      this.$emit('tasks-added', { taskIds: [...this.selected] })
      this.closeAddExisting()
    },

    // ── New / Edit / Delete Column (kanban & sprint boards) ──────────────────
    // `column` lets the caller open the dialog in "edit" mode without relying on
    // the `editingColumn` prop being flushed (kanban still uses the prop).
    openNewColumn(column = null) {
      const target = column || this.editingColumn
      if (target) {
        this.newColumn = {
          id: target.id || target.status || target.title || null,
          title: target.title || '',
          // Prefill with the effective status (the title when none was defined)
          status: target.status || target.title || '',
          // Prefill with the column's icon (may be the canonical status icon)
          icon: target.icon || '',
          color: target.color || COLUMN_COLORS[0],
          // Profiles tasks created in this column inherit by default
          profiles: [...(target.profiles || [])],
          // Project tasks created in this column are assigned to
          project_id: target.project_id || null
        }
      } else {
        this.newColumn = DEFAULT_NEW_COLUMN()
      }
      this.columnError = null
      this.showNewColumn = true
      this.$nextTick(() => this.$refs.colNameInput?.focus())
    },
    closeNewColumn() {
      this.showNewColumn = false
      this.columnError = null
    },
    // The profile picker delivers full profile objects; the column stores names
    onColumnProfilesSelected(profiles) {
      this.newColumn.profiles = (profiles || [])
        .map(p => p?.name || p)
        .filter(Boolean)
    },
    confirmNewColumn() {
      const title = this.newColumn.title?.trim()
      if (!title) return

      // Sprint columns map a UI title to the chat.status they write. When no
      // status is defined the title is used instead.
      const status = this.newColumn.status?.trim() || title
      // Optional Font Awesome icon shown in the column header
      const icon = this.newColumn.icon || null
      // Profiles pre-selected on tasks created in this column
      const profiles = [...this.newColumn.profiles]
      // Project tasks created in this column are assigned to (optional)
      const project_id = this.newColumn.project_id || null

      const duplicateTitle = this.columns.find(
        c => c.title === title && c.id !== this.newColumn.id
      )
      if (duplicateTitle) {
        this.columnError = 'A column with this name already exists'
        return
      }

      // Two columns must not write the same chat status (the tasks would be
      // listed twice)
      const duplicateStatus = this.columns.find(
        c => (c.status || c.title) === status && c.id !== this.newColumn.id
      )
      if (duplicateStatus) {
        this.columnError = `The status "${status}" is already used by "${duplicateStatus.title}"`
        return
      }

      // `id` is only set while editing
      if (this.newColumn.id) {
        this.$emit('column-updated', {
          id: this.newColumn.id,
          title,
          status,
          icon,
          color: this.newColumn.color,
          profiles,
          project_id
        })
      } else {
        this.$emit('column-created', {
          title,
          status,
          icon,
          color: this.newColumn.color,
          profiles,
          project_id
        })
      }
      this.closeNewColumn()
    },
    // Remove the edited column. The parent/store moves its tasks to another
    // column; the last remaining column cannot be removed.
    deleteColumn() {
      if (!this.newColumn.id || this.columns.length <= 1) return
      this.$emit('column-deleted', {
        id: this.newColumn.id,
        status: this.newColumn.status || null
      })
      this.closeNewColumn()
    },

    // Teams that can own a board. The team lead is responsible for it, so the
    // picker shows the lead next to the team name. Company data is optional:
    // without it the board still works (unassigned team).
    async loadTeams() {
      try {
        const data = await API.tasks.boards()
        this.teams = data?.teams || []
      } catch (error) {
        this.teams = []
      }
    },
    teamLead(teamId) {
      const team = this.teams.find(item => item.id === teamId)
      return team ? (team.lead_name || '') : ''
    },
    // ── New Board ────────────────────────────────────────────────────────────
    openNewBoard({ type } = {}) {
      this.newBoard = { ...DEFAULT_NEW_BOARD(), type: type || 'kanban' }
      this.showNewBoard = true
      this.$nextTick(() => this.$refs.boardNameInput?.focus())
    },
    closeNewBoard() {
      this.showNewBoard = false
    },
    confirmNewBoard() {
      if (!this.newBoard.title?.trim()) return
      const team = this.teams.find(item => item.id === this.newBoard.team_id)
      this.$emit('board-created', {
        title: this.newBoard.title.trim(),
        description: this.newBoard.description?.trim() || '',
        background: this.newBoard.background?.trim() || '',
        team_id: this.newBoard.team_id || '',
        team_name: team?.name || '',
        type: 'kanban'
      })
      this.closeNewBoard()
    },

    // ── Board settings (name · description · background · assistant · participants) ──
    // Works for both sprint and kanban boards.
    openEditBoard(board) {
      if (!board) return
      this.editBoardForm = {
        ...DEFAULT_EDIT_BOARD(),
        originalTitle: board.id || board.title,
        title: board.title || '',
        description: board.description || '',
        background: board.background || '',
        team_id: board.team_id || '',
        // Profile the board is assigned to (its chat assistant) — single name
        assignee: board.assignee || null,
        // Background review policy (keep it when the board settings dialog is
        // saved: saveBoard merges it, so the manager modal owns its edits)
        manager: board.manager || null,
        users: [...(board.users || [])]
      }
      this.showEditBoard = true
      this.$nextTick(() => this.$refs.editBoardNameInput?.focus())
    },
    closeEditBoard() {
      this.showEditBoard = false
    },
    // The manager configuration has its own dialog (it also shows the runtime
    // status and the last actions); closing the settings dialog first avoids
    // stacking two modals.
    openBoardManager() {
      const board = this.$storex.kanban.boardById(this.boardId)
      this.showEditBoard = false
      this.$emit('board-manager', board)
    },
    // Single-selection profile picker: the board assistant is ONE profile.
    // Clearing the selection falls back to ProjectManager (see the board view).
    onAssigneeSelected(profiles) {
      this.editBoardForm.assignee = profiles?.[0]?.name || null
    },
    toggleBoardUser(username) {
      const users = this.editBoardForm.users
      this.editBoardForm.users = users.includes(username)
        ? users.filter(u => u !== username)
        : [...users, username]
    },
    confirmEditBoard() {
      const title = this.editBoardForm.title?.trim()
      if (!title) return
      const team = this.teams.find(item => item.id === this.editBoardForm.team_id)
      this.$emit('board-updated', {
        originalTitle: this.editBoardForm.originalTitle,
        board: {
          title,
          description: this.editBoardForm.description?.trim() || '',
          background: this.editBoardForm.background?.trim() || '',
          // Owning team: its lead is the responsible person for the board
          team_id: this.editBoardForm.team_id || '',
          team_name: team?.name || '',
          // Profile that answers the board chat (null ⇒ ProjectManager)
          assignee: this.editBoardForm.assignee || null,
          // Background review policy (the dedicated modal edits it, but the
          // value must survive a board-settings save)
          manager: this.editBoardForm.manager || null,
          users: [...this.editBoardForm.users]
        }
      })
      this.closeEditBoard()
    },

    // ── Delete board (kanban & sprint) ───────────────────────────────────────
    // Opens the confirmation dialog. The settings dialog is closed so the two
    // modals never stack. Kanban boards let the user choose whether the tasks
    // are deleted too; sprint tasks are always kept (only leave the sprint).
    openDeleteBoard() {
      this.deleteBoardChats = true
      this.showEditBoard = false
      this.showDeleteBoard = true
    },
    closeDeleteBoard() {
      this.showDeleteBoard = false
    },
    confirmDeleteBoard() {
      if (this.deletingBoard) return
      this.deletingBoard = true
      try {
        this.$emit('board-delete', {
          boardId: this.boardId,
          // Only kanban boards offer the "delete tasks too" choice
          deleteChats: this.isKanban ? this.deleteBoardChats : false
        })
        this.closeDeleteBoard()
      } finally {
        // The parent owns the async deletion; the dialog closes right away.
        this.deletingBoard = false
      }
    }
  }
}
</script>