<template>
  <div
    v-if="visible"
    class="alert alert-warning rounded-xl mb-2 py-2 px-3 items-center gap-2 text-sm shadow-sm"
  >
    <i class="fa-solid fa-triangle-exclamation"></i>

    <div class="min-w-0">
      <div class="font-semibold">
        {{ deadTitle }}
      </div>
      <div class="text-xs opacity-80">
        {{ detail }}
      </div>
    </div>

    <div class="ml-auto flex items-center gap-1.5 shrink-0">
      <button
        class="btn btn-xs btn-primary gap-1"
        :disabled="busy"
        :title="'Run the last request of this chat again'"
        @click="rerun"
      >
        <i v-if="busy" class="fa-solid fa-spinner fa-spin"></i>
        <i v-else class="fa-solid fa-rotate-right"></i>
        Re-run
      </button>
      <button
        class="btn btn-xs btn-ghost"
        :disabled="busy"
        title="Hide this notice (the next turn will report again)"
        @click="dismissed = true"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>
  </div>
</template>

<script>
/**
 * Dead-turn notice of a chat.
 *
 * A chat message renders as "processing" while the server owns an active turn
 * for it. The server also tells when that turn stopped making progress
 * (`stalled`, no heartbeat for `stale_seconds`) or lost its owner process
 * (`alive: false`): the message is DEAD and no answer will ever arrive.
 *
 * The state is pushed over Socket.IO (`codx-junior-chat-turns`), so nothing
 * here polls the server: this component only
 *   - hits `GET /api/chat/processing` ONCE when it is created / the chat changes
 *     (so an already dead turn is visible right after opening the chat), and
 *   - ticks a LOCAL clock to render "no progress for 3m 12s".
 *
 * `Re-run` calls `POST /api/chat/turn/rerun`, which repairs the dead message and
 * executes the same request again on the server.
 */
export default {
  props: {
    // Server state of the turn of `chat` (see the chats store `chatTurn`).
    turn: { type: Object, default: null },
    chat: { type: Object, default: null },
    // Project that owns the chat (provides `$api.chats`).
    project: { type: Object, default: null }
  },
  emits: ['rerun'],
  data() {
    return {
      busy: false,
      dismissed: false,
      // Local clock: only re-renders the elapsed label, no network involved.
      now: Date.now()
    }
  },
  computed: {
    visible() {
      if (this.dismissed || !this.chat?.id || !this.turn) return false
      return this.turn.stalled === true || this.turn.alive === false
    },
    // Seconds without progress, counted from the last heartbeat the server
    // reported plus the time elapsed since that event was received.
    silentSeconds() {
      const base = Number(this.turn?.heartbeat_age || 0)
      const elapsed = Math.max(0, (this.now - this.receivedAt) / 1000)
      return Math.round(base + elapsed)
    },
    detail() {
      const limit = this.turn?.stale_seconds || 600
      if (this.turn?.alive === false) {
        return 'The process that was running it is gone. No answer will arrive.'
      }
      return `No progress for ${this.formatDuration(this.silentSeconds)} ` +
        `(the server considers a turn dead after ${this.formatDuration(limit)}).`
    },
    deadTitle() {
      return this.busy ? 'Re-running the request...' : 'This message is not being processed'
    }
  },
  watch: {
    // The heartbeat age of a NEW event starts counting from zero again.
    turn: {
      handler() {
        this.receivedAt = Date.now()
        this.now = this.receivedAt
        if (this.visible) this.dismissed = false
      },
      deep: true
    },
    'chat.id': {
      handler() {
        this.dismissed = false
        this.refresh()
      }
    }
  },
  created() {
    // Not reactive state on purpose: it is bookkeeping of this render only.
    this.receivedAt = Date.now()
  },
  mounted() {
    this.refresh()
    // Local ticker for the elapsed label; it stops with the component.
    this._clock = setInterval(() => {
      if (this.visible) this.now = Date.now()
    }, 1000)
  },
  beforeUnmount() {
    if (this._clock) clearInterval(this._clock)
    this._clock = null
  },
  methods: {
    formatDuration(seconds) {
      const total = Math.max(0, Math.round(Number(seconds) || 0))
      if (total < 60) return `${total}s`
      const minutes = Math.floor(total / 60)
      const rest = total % 60
      if (minutes < 60) return `${minutes}m ${rest}s`
      return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
    },
    // One-shot read: the live updates arrive through the socket event handled
    // by the chats store (`onChatTurnsEvent`).
    async refresh() {
      const chatsApi = this.project?.$api?.chats
      if (!chatsApi?.getTurnStates) return
      try {
        const data = await chatsApi.getTurnStates()
        if (Array.isArray(data?.turns)) {
          this.$chats.setChatTurns({
            turns: data.turns,
            staleSeconds: data.stale_seconds
          })
        }
      } catch (error) {
        // A dead turn is also visible from the socket event: never break the UI.
        console.warn('[ChatTurnAlert] could not read turn states:', error)
      }
    },
    async rerun() {
      const chatId = this.chat?.id
      const chatsApi = this.project?.$api?.chats
      if (!chatId || !chatsApi?.rerunTurn) return
      this.busy = true
      try {
        const result = await chatsApi.rerunTurn(chatId)
        if (result?.started === false) {
          this.$ui.addNotification({
            text: result.reason === 'already_running'
              ? 'The turn is still running on the server.'
              : (result.error || 'The request could not be re-run.'),
            type: result.reason === 'already_running' ? 'info' : 'error'
          })
        } else {
          // Instant feedback; the socket event replaces it with the real state.
          this.$chats.startChatProcessing({ chatId })
          this.dismissed = true
          this.$emit('rerun', chatId)
        }
      } catch (error) {
        console.error('[ChatTurnAlert] re-run failed:', error)
        this.$ui.addNotification({
          text: `The request could not be re-run: ${error?.message || error}`,
          type: 'error'
        })
      } finally {
        this.busy = false
      }
    }
  }
}
</script>
