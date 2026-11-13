<script setup>
import SprintBoardModals from './board/SprintBoardModals.vue'
</script>

<template>
  <!-- App-level "New task" dialog.
       It reuses the very same ticket dialog the board uses (SprintBoardModals),
       so a task created from the sidebar "New" entry carries everything a board
       task does (name, profiles, mode, project, assignees, deadline, cost
       budget, initial message and Run). -->
  <SprintBoardModals
    ref="dialog"
    :boardType="boardType"
    :boardTitle="board?.title || null"
    :sprintId="board?.id || null"
    :sprintTitle="board?.title || 'Board'"
    :columns="columns"
    :compact="$ui.isMobile"
    :default-profiles="defaultProfiles"
    @task-created="onTaskCreated"
  />
</template>

<script>
// Profile pre-selected on every task created from this dialog: codx-junior is
// the harness profile that owns the work, so the ticket is assigned to it
// unless the user changes the selection.
const DEFAULT_PROFILES = ['codx-junior']

export default {
  name: 'NewTaskDialog',
  data() {
    return {
      defaultProfiles: DEFAULT_PROFILES
    }
  },
  computed: {
    // The dialog is driven by a one-shot flag in the ui store (`$ui.newTask`),
    // set by launchers that cannot open a board themselves.
    requested() {
      return !!this.$storex.ui.newTask
    },
    // Destination board: the first kanban board of the project (the one the
    // Task Manager opens by default), falling back to a sprint board when the
    // project has no kanban board at all.
    board() {
      const boards = this.$storex.kanban?.storedBoards || []
      return boards.find(b => b.type !== 'sprint') || boards[0] || null
    },
    boardType() {
      return this.board?.type === 'sprint' ? 'sprint' : 'kanban'
    },
    // Columns of the destination board, in the shape the dialog expects
    // (kanban: stored columns; sprint: the status columns of the sprint).
    columns() {
      const board = this.board
      if (!board) return []
      return this.boardType === 'sprint'
        ? (this.$storex.kanban.sprintColumns(board.id) || [])
        : (board.columns || [])
    }
  },
  watch: {
    requested: {
      handler(requested) {
        if (requested) this.openDialog()
      },
      immediate: true
    }
  },
  methods: {
    openDialog() {
      // Consume the request right away: from here on the dialog owns its own
      // visibility (it closes itself after creating the task or on cancel).
      this.$storex.ui.showNewTask(false)
      this.$nextTick(() => this.$refs.dialog?.openNewTask())
    },
    // A task IS a chat, so creation goes through the same store action the board
    // uses. The preferences the create payload does not persist (assignees,
    // deadline, cost budget, referenced files) are written afterwards, and the
    // initial message becomes the task's first user message. "Run" processes it
    // immediately.
    async onTaskCreated(payload = {}) {
      const {
        name, column, mode, status, profiles, llm_model,
        project_id, users, deadline, estimation_cost,
        message, files, attachments, parentId, run
      } = payload
      if (!name) return

      const board = this.board
      const chat = this.boardType === 'sprint' && board
        ? await this.$storex.kanban.newSprintTask({
            name,
            status,
            mode,
            profiles,
            llm_model,
            parentId: parentId || null,
            project_id: project_id || null,
            sprintId: board.id
          })
        : await this.$storex.kanban.newTask({
            name,
            column,
            mode,
            profiles,
            llm_model,
            board: board?.id || null,
            parentId: parentId || null,
            project_id: project_id || null
          })

      if (!chat) {
        this.$storex.ui.addNotification({ text: 'Could not create the task', type: 'error' })
        return
      }

      const updates = {}
      if (this.boardType === 'sprint' && project_id) updates.project_id = project_id
      if (users?.length) updates.users = users
      if (deadline) updates.deadline = deadline
      if (estimation_cost != null) updates.estimation_cost = estimation_cost
      if (files?.length) updates.file_list = files

      if (Object.keys(updates).length) {
        await this.$storex.chats.updateChatInfo({ chat, updates })
      }

      // The initial message (text + files + attachments) seeds the task so the
      // agent has something to work with.
      const hasInitialContent = !!(message || files?.length || attachments?.length)
      if (hasInitialContent) {
        const userMessage = this.$service.chat.getUserMessage({
          message: message || this.defaultTaskMessage({ files, attachments }),
          files: files || [],
          profiles: profiles || [],
          attachments: attachments || [],
          user: this.$user?.username
        })
        await this.$storex.chats.addMessage({ chat, message: userMessage })

        // "Run" → create the task and start processing it immediately
        if (run) {
          await this.$storex.projects.chatWihProject(chat)
        }
      }

      this.$storex.ui.addNotification({
        text: `Task created · ${chat.name || name}`,
        type: 'success'
      })
    },
    // A message is required to seed the task: files/images get a readable fallback
    defaultTaskMessage({ files, attachments } = {}) {
      const names = [
        ...(files || []).map(file => file.split('/').reverse()[0]),
        ...(attachments || []).map(attachment => attachment.file_name)
      ].filter(Boolean)
      return names.length
        ? `Work on the attached content:\n${names.map(name => `- ${name}`).join('\n')}`
        : 'Run this task.'
    }
  }
}
</script>
