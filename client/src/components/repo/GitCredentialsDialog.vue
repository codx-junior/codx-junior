<script>
/**
 * One-shot git credential prompt.
 *
 * Opened only when a remote operation answered `requires_credentials`, i.e. the
 * remote rejected the call and the project has no stored token. The values are
 * handed to the parent through `submit` and used for **that single operation**:
 * nothing is stored (no settings write, no localStorage / sessionStorage, no git
 * credential store) and the component wipes its own copy before emitting, so
 * after the retry no secret is left in the component state.
 *
 * @example
 * <GitCredentialsDialog
 *   v-if="credentialRequest"
 *   :operation="credentialRequest.operation"
 *   :remote="credentialRequest.remote"
 *   @submit="submitCredentials"
 *   @cancel="closeCredentials"
 * />
 */
export default {
  name: 'GitCredentialsDialog',
  props: {
    // Operation waiting for the credentials (fetch, pull or push)
    operation: { type: String, default: 'push' },
    // Remote that rejected the call
    remote: { type: String, default: 'origin' },
    // Backend message explaining the failure
    message: { type: String, default: '' },
    // Retry running: both buttons are disabled and a spinner is shown
    busy: { type: Boolean, default: false }
  },
  emits: ['submit', 'cancel'],
  data() {
    return {
      // One-shot values; never persisted. Cleared on submit and on cancel.
      username: '',
      password: '',
      token: ''
    }
  },
  computed: {
    /** A token alone is enough, otherwise a username needs its password. */
    canSubmit() {
      if (this.busy) return false
      if (this.token.trim()) return true
      return !!this.username.trim() && !!this.password.trim()
    },
    /** `Push` / `Fetch` / `Pull`, used in the title and the explanation. */
    operationLabel() {
      const name = String(this.operation || '').toLowerCase()
      return name ? name.charAt(0).toUpperCase() + name.slice(1) : 'Operation'
    }
  },
  methods: {
    /** Hand the values to the parent and wipe this component's copy at once. */
    submit() {
      if (!this.canSubmit) return
      const credential = {
        username: this.username.trim(),
        password: this.password,
        token: this.token.trim()
      }
      this.reset()
      this.$emit('submit', credential)
    },
    /** Dismiss the prompt: nothing is retried. */
    cancel() {
      this.reset()
      this.$emit('cancel')
    },
    /** Drop every value held by the component. */
    reset() {
      this.username = ''
      this.password = ''
      this.token = ''
    }
  },
  mounted() {
    this.$nextTick(() => this.$refs.usernameInput?.focus())
  }
}
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
    @click.self="cancel"
  >
    <div class="bg-base-100 border border-base-content/10 rounded-xl w-full max-w-md shadow-xl">
      <div class="flex items-center justify-between px-4 py-3 border-b border-base-content/10">
        <h2 class="text-sm font-semibold flex items-center gap-2">
          <i class="fa-solid fa-key text-warning"></i> Credentials required
        </h2>
        <button class="btn btn-xs btn-ghost" @click="cancel" aria-label="Close">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <form class="px-4 py-3 flex flex-col gap-3" autocomplete="off" @submit.prevent="submit">
        <p class="text-[11px] opacity-70 leading-tight break-words">
          <span class="font-semibold">{{ operationLabel }}</span> to
          <span class="font-mono">{{ remote }}</span> was rejected by the remote.
          <span v-if="message" class="block mt-1 opacity-80">{{ message }}</span>
        </p>

        <div class="flex flex-col gap-1">
          <label class="text-xs opacity-60" for="git-credential-username">Username</label>
          <input
            id="git-credential-username"
            ref="usernameInput"
            v-model="username"
            type="text"
            name="codx-git-username"
            autocomplete="off"
            spellcheck="false"
            placeholder="git user or token owner"
            class="input input-sm input-bordered w-full"
            @keyup.esc="cancel"
          />
        </div>

        <div class="flex flex-col gap-1">
          <label class="text-xs opacity-60" for="git-credential-password">Password or token</label>
          <input
            id="git-credential-password"
            v-model="password"
            type="password"
            name="codx-git-password"
            autocomplete="new-password"
            class="input input-sm input-bordered w-full"
            @keyup.esc="cancel"
          />
        </div>

        <div class="flex flex-col gap-1">
          <label class="text-xs opacity-60" for="git-credential-token">
            Access token <span class="opacity-60">(optional, replaces the password)</span>
          </label>
          <input
            id="git-credential-token"
            v-model="token"
            type="password"
            name="codx-git-token"
            autocomplete="new-password"
            placeholder="ghp_..., glpat-..., PAT"
            class="input input-sm input-bordered w-full font-mono"
            @keyup.esc="cancel"
          />
        </div>

        <p class="text-[11px] opacity-50 leading-tight">
          Used only for this operation: codx-junior does not save it, does not write it to the git
          configuration and does not log it. It is retried once and then forgotten.
        </p>
      </form>

      <div class="flex gap-2 px-4 py-3 border-t border-base-content/10">
        <button class="btn btn-sm btn-ghost grow" :disabled="busy" @click="cancel">Cancel</button>
        <button class="btn btn-sm btn-primary grow" :disabled="!canSubmit" @click="submit">
          <i class="fa-solid fa-spinner animate-spin" v-if="busy"></i>
          <i class="fa-solid fa-key" v-else></i>
          {{ busy ? 'Retrying…' : 'Retry once' }}
        </button>
      </div>
    </div>
  </div>
</template>
