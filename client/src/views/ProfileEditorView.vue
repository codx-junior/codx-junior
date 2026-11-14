<script setup>
// Imports used by the template and the options block below.
// The team-view config is reused so the full editor speaks the same language
// (roles, avatars) as TeamMemberDrawer / TeamView and never duplicates them.
import { ROLE_META, roleMeta, avatarUrl } from '@/components/team-view/config'
</script>

<template>
  <div class="flex h-full min-h-0 w-full flex-col overflow-x-hidden bg-base-100 text-base-content">
    <!-- Header: which profile is being edited -->
    <header class="shrink-0 border-b border-base-content/10 px-4 py-3 lg:px-6">
      <div class="flex min-w-0 items-center gap-3">
        <button
          type="button"
          class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-base-content/60 transition-colors hover:bg-base-content/5 hover:text-base-content"
          title="Back to the team panel"
          aria-label="Back to the team panel"
          @click="goBack"
        >
          <i class="fa-solid fa-arrow-left text-sm" aria-hidden="true"></i>
        </button>

        <div class="avatar shrink-0">
          <div class="w-10 rounded-full">
            <img :src="avatar" :alt="profileName || 'Profile'" />
          </div>
        </div>

        <div class="min-w-0 flex-1">
          <h1 class="truncate text-base font-bold leading-tight lg:text-lg">
            {{ profileName || 'Profile' }}
          </h1>
          <p class="truncate text-[11px] text-base-content/50 lg:text-xs">
            <i :class="role.icon" class="text-[10px]" aria-hidden="true"></i>
            {{ role.label }} - {{ role.hint }}
          </p>
        </div>

        <span v-if="dirty" class="badge badge-sm badge-warning shrink-0">Unsaved</span>
      </div>
    </header>

    <!-- Loading -->
    <div v-if="loading" class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4 lg:px-6">
      <div class="skeleton h-28 w-full rounded-xl"></div>
      <div class="skeleton h-56 w-full rounded-xl"></div>
      <div class="skeleton h-40 w-full rounded-xl"></div>
    </div>

    <!-- Load error -->
    <div v-else-if="loadError" class="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-4 text-center" role="alert">
      <div class="grid h-14 w-14 place-items-center rounded-2xl bg-error/15 text-error">
        <i class="fa-solid fa-triangle-exclamation text-2xl" aria-hidden="true"></i>
      </div>
      <h2 class="font-semibold">This profile could not be opened</h2>
      <p class="max-w-sm text-sm text-base-content/50">{{ loadError }}</p>
      <div class="mt-1 flex gap-2">
        <button type="button" class="btn btn-sm btn-primary min-h-[44px]" @click="load(false)">
          <i class="fa-solid fa-rotate" aria-hidden="true"></i> Retry
        </button>
        <button type="button" class="btn btn-sm btn-ghost min-h-[44px]" @click="goBack">Back to team</button>
      </div>
    </div>

    <!-- Body: one column on phones, two columns from lg -->
    <div v-else class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-4 lg:px-6">
      <div class="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-6">
        <!-- Voice: resolved voice first, then the editable values -->
        <section class="flex min-w-0 flex-col gap-4 lg:col-span-2" aria-labelledby="voice-heading">
          <div class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 lg:p-4">
            <div class="flex flex-wrap items-center gap-2">
              <i class="fa-solid fa-microphone-lines text-primary" aria-hidden="true"></i>
              <h2 id="voice-heading" class="text-sm font-bold">Voice</h2>
              <span v-if="resolved" class="badge badge-sm" :class="sourceBadgeClass">{{ sourceLabel }}</span>
              <button
                type="button"
                class="ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-base-content/50 transition-colors hover:bg-base-content/5 hover:text-base-content"
                :disabled="resolving"
                title="Resolve the voice again"
                aria-label="Resolve the voice again"
                @click="loadResolved()"
              >
                <i class="fa-solid fa-rotate text-xs" :class="resolving ? 'animate-spin' : ''" aria-hidden="true"></i>
              </button>
            </div>

            <!-- Resolved voice: one readable sentence, taken from the API -->
            <p class="mt-2 break-words text-sm font-medium leading-relaxed" :class="resolvedMutedClass">
              <template v-if="resolved">{{ resolvedSentence }}</template>
              <template v-else-if="resolving">Resolving the voice of this profile...</template>
              <template v-else-if="resolvedError">The voice could not be resolved: {{ resolvedError }}</template>
              <template v-else>Voice unknown.</template>
            </p>
            <p v-if="resolved?.reason" class="mt-1 break-words text-[11px] text-base-content/50">
              {{ resolved.reason }}
            </p>

            <!-- Resolved details as chips, so they wrap on a 360px screen -->
            <div v-if="resolved?.voice" class="mt-2 flex flex-wrap gap-1">
              <span class="badge badge-sm badge-ghost gap-1">
                <i class="fa-solid fa-bolt text-[9px]" aria-hidden="true"></i>{{ resolved.voice.speed }}x
              </span>
              <span v-if="resolved.voice.language" class="badge badge-sm badge-ghost gap-1">
                <i class="fa-solid fa-language text-[9px]" aria-hidden="true"></i>{{ resolved.voice.language }}
              </span>
              <span v-if="resolved.voice.style" class="badge badge-sm badge-ghost gap-1">
                <i class="fa-solid fa-wand-magic-sparkles text-[9px]" aria-hidden="true"></i>{{ resolved.voice.style }}
              </span>
              <span v-if="resolved.voice.model" class="badge badge-sm badge-ghost gap-1">
                <i class="fa-solid fa-cube text-[9px]" aria-hidden="true"></i>{{ resolved.voice.model }}
              </span>
            </div>

            <!-- Editable voice of this profile -->
            <h3 class="mt-4 text-[11px] font-bold uppercase tracking-[0.14em] text-base-content/50">Voice of this profile</h3>
            <p class="mt-1 text-[11px] text-base-content/40">
              Stored in this profile. An empty field means the project or system voice is used.
            </p>

            <label class="mt-2 flex min-h-[44px] cursor-pointer items-center gap-3 rounded-lg border border-base-content/10 bg-base-100 px-3 py-2">
              <i
                class="fa-solid fa-microphone shrink-0 text-sm"
                :class="draft.voice.enabled ? 'text-primary' : 'text-base-content/40'"
                aria-hidden="true"
              ></i>
              <span class="min-w-0 flex-1">
                <span class="block text-sm font-medium">Speak in videos</span>
                <span class="block text-[11px] text-base-content/50">Off keeps this profile silent, with no project or system voice.</span>
              </span>
              <input
                v-model="draft.voice.enabled"
                type="checkbox"
                class="toggle toggle-primary shrink-0"
                aria-label="Speak in videos"
              />
            </label>

            <div
              class="mt-3 grid grid-cols-1 gap-3 transition-opacity sm:grid-cols-2"
              :class="draft.voice.enabled ? '' : 'opacity-60'"
            >
              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="voice-provider">Provider</label>
                <input
                  id="voice-provider"
                  v-model="draft.voice.provider"
                  list="voice-provider-options"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="local"
                  autocomplete="off"
                />
                <datalist id="voice-provider-options">
                  <option v-for="option in providerOptions" :key="option" :value="option"></option>
                </datalist>
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="voice-voice-id">Voice</label>
                <input
                  id="voice-voice-id"
                  v-model="draft.voice.voice_id"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="e.g. af_bella"
                  autocomplete="off"
                />
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="voice-model">Model</label>
                <input
                  id="voice-model"
                  v-model="draft.voice.model"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="e.g. kokoro-82m"
                  autocomplete="off"
                />
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="voice-language">Language</label>
                <input
                  id="voice-language"
                  v-model="draft.voice.language"
                  list="voice-language-options"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="e.g. en"
                  autocomplete="off"
                />
                <datalist id="voice-language-options">
                  <option v-for="option in languageOptions" :key="option" :value="option"></option>
                </datalist>
              </div>

              <div class="min-w-0 sm:col-span-2">
                <label class="label-text block pb-1 text-xs font-semibold" for="voice-style">Style</label>
                <input
                  id="voice-style"
                  v-model="draft.voice.style"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="e.g. calm narrator"
                  autocomplete="off"
                />
              </div>

              <div class="min-w-0 sm:col-span-2">
                <label class="flex min-h-[44px] items-center justify-between gap-2 text-xs font-semibold" for="voice-speed">
                  <span>Speed</span>
                  <span class="font-mono text-base-content/60">{{ Number(draft.voice.speed).toFixed(2) }}x</span>
                </label>
                <input
                  id="voice-speed"
                  v-model.number="draft.voice.speed"
                  type="range"
                  min="0.5"
                  max="2"
                  step="0.05"
                  class="range range-sm range-primary h-11 w-full"
                />
              </div>
            </div>

            <!-- Preview: no fake audio, the server reason is shown right here -->
            <div class="mt-3 rounded-lg border border-base-content/10 bg-base-100 p-3">
              <div class="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="btn btn-sm btn-primary min-h-[44px] w-full gap-2 sm:w-auto"
                  :disabled="previewing || !profileName"
                  @click="previewVoice"
                >
                  <span v-if="previewing" class="loading loading-spinner loading-xs"></span>
                  <i v-else class="fa-solid fa-play" aria-hidden="true"></i>
                  Preview
                </button>
                <span class="text-[11px] text-base-content/50">One short sentence, synthesized with the saved voice.</span>
              </div>

              <!-- Sample ready -->
              <div v-if="preview && preview.ok" class="mt-2" role="status">
                <audio v-if="previewSampleUrl" :src="previewSampleUrl" controls class="h-11 w-full"></audio>
                <p class="break-words text-xs text-base-content/60">
                  Sample ready<span v-if="preview.provider"> from <b>{{ preview.provider }}</b></span>.
                </p>
              </div>

              <!-- 503 / 409 / other: the reason belongs to this section, not a toast -->
              <div v-else-if="preview" class="mt-2 flex gap-2" role="status">
                <i class="fa-solid fa-circle-info mt-0.5 shrink-0 text-info" aria-hidden="true"></i>
                <div class="min-w-0">
                  <p class="break-words text-xs font-medium">{{ previewNotice }}</p>
                  <p v-if="preview.reason" class="mt-0.5 break-words font-mono text-[11px] text-base-content/50">
                    {{ preview.reason }}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Identity and persona (right column from lg) -->
        <aside class="flex min-w-0 flex-col gap-4">
          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 lg:p-4" aria-labelledby="identity-heading">
            <h2 id="identity-heading" class="text-sm font-bold">Profile</h2>

            <div class="mt-3 flex flex-col gap-3">
              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="profile-name">Name</label>
                <input
                  id="profile-name"
                  :value="profileName"
                  type="text"
                  class="input input-bordered h-11 w-full font-mono text-xs"
                  readonly
                />
                <p class="mt-1 text-[11px] text-base-content/40">The name identifies the profile and cannot be edited here.</p>
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="profile-avatar">Avatar URL</label>
                <input
                  id="profile-avatar"
                  v-model="draft.avatar"
                  type="text"
                  class="input input-bordered h-11 w-full"
                  placeholder="Paste an image URL (optional)"
                />
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="profile-role">Role</label>
                <select id="profile-role" v-model="draft.category" class="select select-bordered h-11 w-full">
                  <option v-for="(meta, key) in roleMetaMap" :key="key" :value="key">
                    {{ meta.label }} - {{ meta.hint }}
                  </option>
                </select>
              </div>

              <div class="min-w-0">
                <label class="label-text block pb-1 text-xs font-semibold" for="profile-description">Short description</label>
                <textarea
                  id="profile-description"
                  v-model="draft.description"
                  rows="3"
                  class="textarea textarea-bordered w-full"
                  placeholder="What does this teammate do?"
                ></textarea>
              </div>
            </div>
          </section>

          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 lg:p-4" aria-labelledby="persona-heading">
            <h2 id="persona-heading" class="text-sm font-bold">Persona</h2>
            <p class="mt-1 text-[11px] text-base-content/40">How this teammate behaves and what it focuses on.</p>
            <textarea
              v-model="draft.content"
              rows="12"
              class="textarea textarea-bordered mt-2 w-full text-sm"
              placeholder="Describe the behaviour and focus of this teammate..."
              aria-label="Persona"
            ></textarea>
            <div class="mt-1 text-[11px] text-base-content/40">{{ (draft.content || '').length }} characters</div>
          </section>

          <section class="rounded-xl border border-base-content/10 bg-base-200/40 p-3 lg:p-4">
            <h2 class="text-sm font-bold">More settings</h2>
            <p class="mt-1 text-[11px] text-base-content/50">
              Skills, guardrails, knowledge and API sharing stay in the team panel, where they fit the drawer.
            </p>
            <button type="button" class="btn btn-sm btn-ghost mt-2 min-h-[44px] w-full gap-1" @click="goBack">
              <i class="fa-solid fa-people-group" aria-hidden="true"></i>
              Open the team panel
            </button>
          </section>
        </aside>
      </div>
    </div>

    <!-- Save bar: always visible (sticky footer of the page) -->
    <footer class="sticky bottom-0 z-10 shrink-0 border-t border-base-content/10 bg-base-100/95 px-4 py-3 backdrop-blur lg:px-6">
      <div class="flex flex-wrap items-center gap-2">
        <span class="mr-auto min-w-0 text-[11px] text-base-content/40">
          <template v-if="saving">Saving...</template>
          <template v-else-if="dirty">Unsaved changes</template>
          <template v-else>All changes are saved</template>
        </span>
        <button type="button" class="btn btn-sm btn-ghost min-h-[44px] flex-1 sm:flex-none" @click="goBack">Back</button>
        <button
          type="button"
          class="btn btn-sm btn-primary min-h-[44px] flex-1 gap-1 sm:flex-none"
          :disabled="saving || loading || !profileName"
          @click="save"
        >
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          <i v-else class="fa-solid fa-floppy-disk" aria-hidden="true"></i>
          Save profile
        </button>
      </div>
    </footer>
  </div>
</template>

<script>
// Local-first providers offered as suggestions in the Voice section. Any other
// value can be typed: the media provider registry decides who can speak.
const PROVIDER_SUGGESTIONS = ['local', 'kokoro', 'piper', 'comfyui']
// Common language prefixes; the field stays free text.
const LANGUAGE_SUGGESTIONS = ['en', 'es', 'ca', 'fr', 'de', 'pt']

/**
 * Normalize a voice payload (profile.voice, possibly absent) to the shape of the
 * backend VoiceProfile model (codx/junior/media/model.py).
 * @param {Object|null} voice
 * @returns {Object} Voice draft.
 */
function toVoiceDraft(voice) {
  const source = voice || {}
  return {
    enabled: source.enabled !== false,
    provider: source.provider || '',
    voice_id: source.voice_id || '',
    model: source.model || '',
    speed: typeof source.speed === 'number' ? source.speed : 1,
    style: source.style || '',
    language: source.language || '',
    reference_asset: source.reference_asset || null
  }
}

export default {
  name: 'ProfileEditorView',
  data() {
    return {
      loading: true,
      resolving: false,
      saving: false,
      loadError: '',
      resolvedError: '',
      // Profile as returned by the API. The write payload is built on top of it,
      // so fields this screen does not render (tools, guardrails, api_settings)
      // survive a save untouched.
      profile: null,
      // Editable subset of the profile.
      draft: {
        avatar: '',
        description: '',
        category: 'assistant',
        content: '',
        voice: toVoiceDraft(null)
      },
      // JSON snapshot of the draft right after a load: drives the dirty flag.
      baseline: '',
      // GET .../profiles/{name}/voice gives { voice, source, reason, profile }
      resolved: null,
      // POST .../profiles/{name}/voice/preview result, kept inline (never a toast)
      preview: null,
      previewing: false,
      roleMetaMap: ROLE_META,
      providerOptions: PROVIDER_SUGGESTIONS,
      languageOptions: LANGUAGE_SUGGESTIONS
    }
  },
  computed: {
    /** Profile name from the route, e.g. /<project>/profiles/<name>/edit. */
    profileName() {
      return String(this.$route?.params?.name || this.profile?.name || '').trim()
    },
    role() {
      return roleMeta(this.draft.category)
    },
    avatar() {
      return avatarUrl({ name: this.profileName, avatar: this.draft.avatar })
    },
    /** One sentence describing the resolved voice and where it comes from. */
    resolvedSentence() {
      const resolved = this.resolved
      if (!resolved) return ''
      if (resolved.source === 'disabled') {
        return 'This profile stays silent in videos because its voice is turned off.'
      }
      const voice = resolved.voice || {}
      const name = [voice.provider, voice.voice_id].filter(Boolean).join(' ')
      const label = name || 'the default voice'
      const suffix = {
        profile: 'own voice',
        project: 'inherited from project',
        system: 'system default'
      }[resolved.source] || 'system default'
      return `This profile will sound like ${label} - ${suffix}.`
    },
    sourceLabel() {
      return {
        profile: 'own voice',
        project: 'inherited from project',
        system: 'system default',
        disabled: 'silent'
      }[this.resolved?.source] || 'unknown source'
    },
    sourceBadgeClass() {
      return {
        profile: 'badge-primary',
        project: 'badge-info',
        system: 'badge-ghost',
        disabled: 'badge-warning'
      }[this.resolved?.source] || 'badge-ghost'
    },
    resolvedMutedClass() {
      return this.resolved?.source === 'disabled' ? 'text-warning' : ''
    },
    /** Inline text of a failed preview (503 means no TTS engine installed). */
    previewNotice() {
      const preview = this.preview
      if (!preview || preview.ok) return ''
      if (preview.status === 503) {
        return 'No TTS engine installed yet. The voice will be rendered once a voice provider is available.'
      }
      if (preview.status === 409) {
        return 'This profile is silent, so there is nothing to preview.'
      }
      if (preview.status === 404) {
        return 'This profile is unknown to the server.'
      }
      return 'The preview could not be generated.'
    },
    /** Playable sample URL, when a provider returned one. */
    previewSampleUrl() {
      const sample = this.preview?.sample
      const value = typeof sample === 'string' ? sample : sample?.url
      if (typeof value !== 'string' || !value) return ''
      return /^(data:audio\/|https?:\/\/)/.test(value) ? value : ''
    },
    /** True when the draft differs from the last loaded or saved profile. */
    dirty() {
      return !!this.baseline && this.snapshot() !== this.baseline
    }
  },
  watch: {
    // Two profiles are two route params of the same component.
    '$route.params.name'() {
      this.load(false)
    }
  },
  created() {
    this.load(false)
  },
  methods: {
    // Loading ---------------------------------------------------------------
    /**
     * Read the profile and, right after, its resolved voice.
     * @param {boolean} silent - Keep the current screen while refreshing.
     */
    async load(silent) {
      const api = this.$project?.$api
      if (!api || !this.profileName) {
        this.loading = false
        this.loadError = api ? 'No profile name in the URL' : 'The project is not ready yet'
        return
      }
      if (!silent) this.loading = true
      this.loadError = ''
      try {
        const profile = await api.profiles.load(this.profileName)
        this.profile = profile || {}
        this.applyProfile(this.profile)
      } catch (error) {
        console.error('[ProfileEditor] Failed to load the profile', error)
        this.loadError = this.errorMessage(error) || 'Unknown profile'
      } finally {
        this.loading = false
      }
      if (!this.loadError) await this.loadResolved(api)
    },
    /**
     * Resolve the voice with the endpoint owned by api/profile_voice.py:
     * GET /api/profiles/{name}/voice returns { voice, source, reason, profile }.
     * @param {Object} [api] - Project API instance ($project.$api).
     */
    async loadResolved(api) {
      const projectApi = api || this.$project?.$api
      if (!projectApi || !this.profileName) return
      this.resolving = true
      this.resolvedError = ''
      try {
        this.resolved = await projectApi.get(
          `/api/profiles/${encodeURIComponent(this.profileName)}/voice`
        )
      } catch (error) {
        console.error('[ProfileEditor] Failed to resolve the voice', error)
        this.resolved = null
        this.resolvedError = this.errorMessage(error) || 'unknown reason'
      } finally {
        this.resolving = false
      }
    },
    /** Copy the loaded profile into the editable draft. */
    applyProfile(profile) {
      this.draft = {
        avatar: profile?.avatar || '',
        description: profile?.description || '',
        category: profile?.category || 'assistant',
        content: profile?.content || '',
        voice: toVoiceDraft(profile?.voice)
      }
      this.baseline = this.snapshot()
    },

    // Saving ----------------------------------------------------------------
    /** Voice object in the shape the backend model expects (empty -> null). */
    voicePayload() {
      const voice = this.draft.voice
      return {
        enabled: !!voice.enabled,
        provider: voice.provider || '',
        voice_id: voice.voice_id || '',
        model: voice.model || null,
        speed: typeof voice.speed === 'number' ? voice.speed : 1,
        style: voice.style || null,
        language: voice.language || null,
        reference_asset: voice.reference_asset || null
      }
    },
    /**
     * Full profile payload: the loaded profile plus the edited fields, so the
     * write goes through the same endpoint TeamMemberDrawer already uses
     * (api.profiles.save, i.e. POST /api/profiles).
     */
    buildPayload() {
      return {
        ...(this.profile || {}),
        name: this.profileName,
        avatar: this.draft.avatar || '',
        description: this.draft.description || '',
        category: this.draft.category || '',
        content: this.draft.content || '',
        voice: this.voicePayload()
      }
    },
    /** Comparable snapshot of the editable fields (stable key order). */
    snapshot() {
      return JSON.stringify({
        avatar: this.draft.avatar || '',
        description: this.draft.description || '',
        category: this.draft.category || '',
        content: this.draft.content || '',
        voice: this.voicePayload()
      })
    },
    async save() {
      const api = this.$project?.$api
      if (!api || this.saving || !this.profileName) return
      this.saving = true
      try {
        await api.profiles.save(this.buildPayload())
        // Reload so the resolved voice reflects what the server now stores.
        await this.load(true)
        this.$ui?.addNotification?.({
          text: `Changes to ${this.profileName} saved`,
          type: 'success'
        })
      } catch (error) {
        console.error('[ProfileEditor] Failed to save the profile', error)
        this.$ui?.addNotification?.({
          text: `Failed to save: ${this.errorMessage(error) || 'unknown error'}`,
          type: 'error'
        })
      } finally {
        this.saving = false
      }
    },

    // Preview ---------------------------------------------------------------
    /**
     * Ask for a sample of the saved voice. A 503 (no TTS engine installed) is
     * stored as a plain status so the section can render its reason inline.
     */
    async previewVoice() {
      const api = this.$project?.$api
      if (!api || this.previewing || !this.profileName) return
      this.previewing = true
      this.preview = null
      const url = `/api/profiles/${encodeURIComponent(this.profileName)}/voice/preview`
      try {
        const body = await api.post(url, {})
        this.preview = {
          ok: true,
          status: 200,
          provider: body?.provider || '',
          reason: body?.reason || '',
          sample: body?.sample ?? null
        }
      } catch (error) {
        const status = error?.response?.status || 0
        const body = error?.response?.data || {}
        this.preview = {
          ok: false,
          status,
          provider: body?.provider || '',
          reason: body?.reason || body?.detail || this.errorMessage(error),
          sample: null
        }
      } finally {
        this.previewing = false
      }
    },

    // Helpers ---------------------------------------------------------------
    /** Human message of an HTTP error (FastAPI detail or the body reason). */
    errorMessage(error) {
      const body = error?.response?.data
      if (typeof body === 'string') return body
      return body?.detail || body?.reason || error?.message || ''
    },
    goBack() {
      this.$router?.push({ name: 'team' })
    }
  }
}
</script>
