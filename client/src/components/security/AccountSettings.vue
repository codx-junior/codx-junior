<script setup>
import ThemeSelector from '../ThemeSelector.vue'
</script>

<template>
  <div class="flex h-full flex-col overflow-auto">
    <div class="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6">
      <!-- Identity -->
      <div class="card bg-base-100 shadow-sm">
        <div class="card-body flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div class="flex min-w-0 items-center gap-4">
            <div class="avatar shrink-0">
              <div class="w-16 sm:w-20 rounded-full ring ring-primary ring-offset-2 ring-offset-base-100">
                <img :src="avatarPreview" :alt="username" @error="avatarBroken = true" />
              </div>
            </div>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h1 class="truncate text-2xl font-semibold">{{ username }}</h1>
                <span class="badge badge-primary badge-sm">{{ role }}</span>
              </div>
              <p class="mt-1 truncate text-xs text-base-content/60">
                {{ form.email || 'No email set — add one below' }}
              </p>
              <p class="mt-1 text-[11px] text-base-content/40">
                Account settings apply to you only. Installation-wide options live in
                Global settings.
              </p>
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <button type="button" class="btn btn-ghost btn-sm" :disabled="saving || !dirty" @click="reset">
              Reset
            </button>
            <button type="button" class="btn btn-primary btn-sm" :disabled="saving || !dirty" @click="save">
              <span v-if="saving" class="loading loading-spinner loading-xs"></span>
              {{ saving ? 'Saving…' : 'Update' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Profile -->
      <div class="card bg-base-100 shadow-sm">
        <div class="card-body gap-4">
          <div>
            <h2 class="card-title text-base">
              <i class="fa-solid fa-id-card text-base-content/50"></i>
              Profile
            </h2>
            <p class="text-xs text-base-content/60">How you are shown in chats, boards and tickets.</p>
          </div>

          <label class="form-control">
            <span class="label-text font-medium">Avatar</span>
            <div class="flex items-center gap-3">
              <input
                type="text"
                v-model="form.avatar"
                class="input input-bordered w-full"
                placeholder="https://… or /only_icon.png"
              />
              <div class="avatar shrink-0">
                <div class="w-10 rounded-full bg-base-200">
                  <img :src="form.avatar || fallbackAvatar" @error="avatarBroken = true" />
                </div>
              </div>
            </div>
            <span class="label-text-alt text-base-content/50">A URL (or a path served by the app).</span>
          </label>

          <label class="form-control">
            <span class="label-text font-medium">Theme</span>
            <div class="flex items-center gap-3">
              <ThemeSelector
                :data-tip="form.theme"
                @change="form.theme = $event"
              />
              <span class="text-xs text-base-content/60">{{ form.theme || 'default' }}</span>
            </div>
          </label>
        </div>
      </div>

      <!-- Contact + assistant -->
      <div class="card bg-base-100 shadow-sm">
        <div class="card-body gap-4">
          <div>
            <h2 class="card-title text-base">
              <i class="fa-solid fa-envelope text-base-content/50"></i>
              Contact &amp; assistant
            </h2>
          </div>

          <label class="form-control">
            <span class="label-text font-medium">Email</span>
            <input
              type="email"
              v-model="form.email"
              class="input input-bordered w-full"
              placeholder="you@example.com"
              autocomplete="email"
            />
            <span class="label-text-alt text-base-content/50">
              Used to sign in and to receive notifications.
            </span>
          </label>

          <label class="form-control">
            <span class="label-text font-medium">Assistant language</span>
            <select class="select select-bordered w-full max-w-xs" v-model="form.language">
              <option v-for="language in languageOptions" :key="language.code" :value="language.code">
                {{ language.label }}
              </option>
            </select>
            <span class="label-text-alt text-base-content/50">
              The assistant answers in this language. “Automatic” follows the language you write in.
            </span>
          </label>
        </div>
      </div>

      <!-- Security -->
      <div class="card bg-base-100 shadow-sm">
        <div class="card-body gap-4">
          <div>
            <h2 class="card-title text-base">
              <i class="fa-solid fa-shield-halved text-base-content/50"></i>
              Security
            </h2>
          </div>

          <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div class="min-w-0">
              <div class="font-medium">Password</div>
              <p class="text-xs text-base-content/60">
                {{ canResetPassword ? 'Change the password for this account.' : 'This account signs in with an external provider.' }}
              </p>
            </div>
            <button
              type="button"
              class="btn btn-outline btn-sm"
              :disabled="!canResetPassword"
              @click="openResetPassword"
            >
              Reset password
            </button>
          </div>

          <div class="divider my-0"></div>

          <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div class="min-w-0">
              <div class="font-medium">Session token</div>
              <p class="text-xs text-base-content/60">
                The credential this browser uses. Treat it like a password.
              </p>
            </div>
            <div class="flex items-center gap-2">
              <code class="truncate rounded bg-base-200 px-2 py-1 text-[11px] max-w-[16rem]">
                {{ tokenVisible ? sessionToken : maskedToken }}
              </code>
              <button type="button" class="btn btn-ghost btn-xs" @click="tokenVisible = !tokenVisible">
                <i :class="tokenVisible ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye'"></i>
              </button>
              <button type="button" class="btn btn-ghost btn-xs" @click="copyToken">
                <i class="fa-solid fa-copy"></i>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="flex justify-end pb-4">
        <button type="button" class="btn btn-primary" :disabled="saving || !dirty" @click="save">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          {{ saving ? 'Saving…' : 'Update' }}
        </button>
      </div>
    </div>

    <modal v-if="showResetPasswordModal" @close="showResetPasswordModal = false">
      <template #header>Reset password</template>
      <template #body>
        <div class="flex flex-col gap-3">
          <label class="form-control">
            <span class="label-text font-medium">New password</span>
            <input
              type="password"
              v-model="newPassword"
              class="input input-bordered w-full"
              placeholder="Enter new password"
              autocomplete="new-password"
            />
          </label>
          <label class="form-control">
            <span class="label-text font-medium">Confirm password</span>
            <input
              type="password"
              v-model="confirmPassword"
              class="input input-bordered w-full"
              placeholder="Confirm new password"
              autocomplete="new-password"
            />
          </label>
        </div>
      </template>
      <template #footer>
        <button type="button" class="btn btn-ghost btn-sm" @click="showResetPasswordModal = false">Cancel</button>
        <button type="button" class="btn btn-primary btn-sm" @click="resetPassword">Confirm</button>
      </template>
    </modal>
  </div>
</template>

<script>
import { languageLabel, USER_LANGUAGES } from '@/config/languages'

const FALLBACK_AVATAR = '/only_icon.png'

function emptyForm () {
  return { username: '', email: '', avatar: '', theme: '', language: 'auto', workspace_mode: 'auto' }
}

export default {
  data () {
    return {
      saving: false,
      avatarBroken: false,
      tokenVisible: false,
      showResetPasswordModal: false,
      newPassword: '',
      confirmPassword: '',
      // A local draft: the API user object is shared, so editing it in place
      // would change what the rest of the UI sees before the save succeeds.
      form: emptyForm()
    }
  },
  computed: {
    user () {
      // `$users.user` is the store getter over `API.user`; it is undefined
      // before the session is restored, so never dereference it blindly.
      return this.$users?.user || this.$user || {}
    },
    username () {
      return this.user.username || 'Account'
    },
    role () {
      return this.user.role || 'user'
    },
    sessionToken () {
      return this.user.token || ''
    },
    maskedToken () {
      const token = this.sessionToken
      if (!token) return '—'
      if (token.length <= 12) return '••••••'
      return `${token.slice(0, 6)}…${token.slice(-4)}`
    },
    canResetPassword () {
      return !this.user.github_only
    },
    fallbackAvatar () {
      return FALLBACK_AVATAR
    },
    avatarPreview () {
      if (this.avatarBroken) return FALLBACK_AVATAR
      return this.form.avatar || this.user.avatar || FALLBACK_AVATAR
    },
    languageOptions () {
      const options = [...USER_LANGUAGES]
      const current = this.form.language
      if (current && current !== 'auto' && !options.some(option => option.code === current)) {
        options.unshift({ code: current, label: languageLabel(current) })
      }
      return options
    },
    dirty () {
      const current = this.form
      const saved = this.user || {}
      return ['email', 'avatar', 'theme', 'language'].some(
        key => (current[key] || '') !== (saved[key] || '')
      )
    }
  },
  watch: {
    user: {
      immediate: true,
      handler () {
        this.reset()
      }
    }
  },
  methods: {
    reset () {
      const saved = this.user || {}
      this.form = {
        ...emptyForm(),
        username: saved.username || '',
        email: saved.email || '',
        avatar: saved.avatar || '',
        theme: saved.theme || '',
        language: saved.language || 'auto',
        workspace_mode: saved.workspace_mode || 'auto'
      }
      this.avatarBroken = false
    },
    async save () {
      if (this.saving) return
      const username = this.user?.username
      if (!username) {
        this.$toast?.error('Your session is not loaded yet; reload the page.')
        return
      }
      this.saving = true
      try {
        // Only the editable fields: the rest of the document (role,
        // memberships, projects) is granted by an admin and must not be sent.
        await this.$users.saveUser({
          ...this.user,
          username,
          email: this.form.email,
          avatar: this.form.avatar,
          theme: this.form.theme,
          language: this.form.language,
          workspace_mode: this.form.workspace_mode
        })
      } catch (ex) {
        this.$toast?.error(ex?.response?.data?.detail || 'Could not update the account')
      } finally {
        this.saving = false
      }
    },
    openResetPassword () {
      this.newPassword = ''
      this.confirmPassword = ''
      this.showResetPasswordModal = true
    },
    resetPassword () {
      if (!this.newPassword) {
        this.$toast?.error('Enter a new password')
        return
      }
      if (this.newPassword !== this.confirmPassword) {
        this.$toast?.error('Passwords do not match')
        return
      }
      this.$users.resetPassword(this.newPassword)
      this.$toast?.success('Password reset successfully')
      this.showResetPasswordModal = false
      this.newPassword = ''
      this.confirmPassword = ''
    },
    async copyToken () {
      try {
        await navigator.clipboard.writeText(this.sessionToken)
        this.$toast?.success('Session token copied')
      } catch {
        this.$toast?.error('Could not copy the token')
      }
    }
  }
}
</script>
