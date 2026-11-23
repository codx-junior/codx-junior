<script setup>
import BoardQuickChat from '@/components/kanban/board/BoardQuickChat.vue'
</script>

<template>
  <!-- App-level "New task" entry (company view and any launcher that cannot
       reach a board). It is the SAME dialog boards and sprints use
       (BoardQuickChat), so there is exactly one creation surface everywhere. -->
  <BoardQuickChat
    v-if="open"
    :board-id="boardId"
    :board-type="boardType"
    :board-name="board?.title || 'Tasks'"
    :columns="columns"
    :default-profiles="defaultProfiles"
    mode="task"
    schedule-enabled
    @close="closeDialog"
  />
</template>

<script>
// Profile pre-selected on every task created from this entry: codx-junior is
// the harness profile that owns the work.
const DEFAULT_PROFILES = ['codx-junior']

export default {
  name: 'NewTaskDialog',
  data() {
    return {
      open: false,
      defaultProfiles: DEFAULT_PROFILES
    }
  },
  computed: {
    // The dialog is driven by a one-shot flag in the ui store (`$ui.newTask`).
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
    boardId() {
      return this.board?.id || this.board?.title || 'tasks'
    },
    boardType() {
      return this.board?.type === 'sprint' ? 'sprint' : 'kanban'
    },
    // Columns of the destination board, in the shape BoardQuickChat expects.
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
        if (!requested) return
        // The dialog owns its own visibility; consume the one-shot flag.
        this.open = true
        this.$storex.ui.showNewTask(false)
      },
      immediate: true
    }
  },
  methods: {
    closeDialog() {
      this.open = false
      this.$storex.ui.showNewTask(false)
    }
  }
}
</script>
