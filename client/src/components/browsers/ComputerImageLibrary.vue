<script>
/**
 * Unified image catalog (desktops, applications, browsers, saved and custom).
 *
 * One feed (`GET /api/computers/images/catalog`) merges the provider catalog,
 * the project's saved computers and the custom coworking gallery into
 * `CatalogImage` cards, so this view has a single grid, one search and one tag
 * filter. The provider (linuxserver / kasmweb / custom / saved / browseruse) is
 * just a tag, like the engine and the category, which is what keeps the
 * selector simple: no provider tabs, no nested galleries.
 *
 * A custom image reference is still possible through the coworking gallery
 * ("Custom images"): its cards are part of the same grid and its editor stays
 * one click away.
 */
import { SESSION_LABELS } from '@/utils/sessionLabels'
import CoworkingImageGallery from './CoworkingImageGallery.vue'

const TYPE_LABEL = {
  desktop: 'Desktop',
  application: 'App',
  browser: 'Browser',
  saved: 'Saved',
  custom: 'Custom'
}

export default {
  name: 'ComputerImageLibrary',
  props: {
    kind: { type: String, default: 'linux' },
    modelValue: { type: String, default: '' },
    // Render as a view of the caller's dialog instead of a dialog of its own:
    // the new-session wizard must never open a second modal on top of itself.
    embedded: { type: Boolean, default: false }
  },
  emits: ['close', 'select'],
  components: { CoworkingImageGallery },
  data() {
    return {
      images: [],
      query: '',
      selectedTags: [],
      selected: null,
      selectedTag: '',
      tags: [],
      tagsLoading: false,
      loading: false,
      error: null,
      refresh: false,
      customOpen: false,
      previewUrls: {},
      loadToken: 0
    }
  },
  computed: {
    sessionLabels() {
      return SESSION_LABELS
    },
    /**
     * Filter chips: providers, types and capabilities first (the useful
     * filters), then the most frequent remaining tags. A desktop catalog has
     * one tag per app, so the tail is capped to keep the row readable.
     */
    tagOptions() {
      const counts = new Map()
      for (const image of this.images) {
        for (const tag of this.tagsOf(image)) {
          counts.set(tag, (counts.get(tag) || 0) + 1)
        }
      }
      const priority = [
        'linuxserver', 'kasmweb', 'custom', 'saved', 'browseruse',
        'desktop', 'application', 'browser',
        'cdp', 'live-view-only', 'chromium', 'firefox', 'kasmvnc', 'selkies'
      ]
      const head = priority.filter((tag) => counts.has(tag))
      const tail = [...counts.keys()]
        .filter((tag) => !priority.includes(tag))
        .sort((left, right) => counts.get(right) - counts.get(left) || left.localeCompare(right))
        .slice(0, 12)
      return [...head, ...tail]
    },
    visibleImages() {
      const term = this.query.trim().toLowerCase()
      return this.images.filter((image) => {
        const tags = this.tagsOf(image)
        if (this.selectedTags.length && !this.selectedTags.every((tag) => tags.includes(tag))) {
          return false
        }
        if (!term) return true
        return [image.name, image.description, image.image, image.source, image.provider, image.type, ...tags]
          .some((value) => String(value || '').toLowerCase().includes(term))
      })
    },
    selectedImage() {
      if (!this.selected) return ''
      if (this.selected.source === 'custom' || this.selected.source === 'saved') {
        return this.selected.image || ''
      }
      const repository = this.repositoryOf(this.selected)
      const tag = this.selectedTag || this.tagOf(this.selected)
      return repository && tag ? `${repository}:${tag}` : (this.selected.image || '')
    },
    isBrowser() {
      return String(this.kind || '').toLowerCase() === 'browser'
    },
    emptyMessage() {
      if (this.loading) return ''
      if (this.query.trim() || this.selectedTags.length) return 'No image matches this filter.'
      return this.isBrowser
        ? 'No browser image yet. Rebuild the catalog or refresh.'
        : 'No image in the catalog yet.'
    }
  },
  watch: {
    kind: {
      immediate: true,
      handler() {
        this.reset()
        this.loadImages()
      }
    }
  },
  mounted() {
    // Standalone dialog: ESC closes it. Embedded, the wizard owns dismissal.
    if (!this.embedded) window.addEventListener('keydown', this.onKeydown)
  },
  beforeUnmount() {
    if (!this.embedded) window.removeEventListener('keydown', this.onKeydown)
    this.revokePreviews()
  },
  methods: {
    onKeydown(event) {
      if (event.key === 'Escape') this.$emit('close')
    },
    reset() {
      this.images = []
      this.selected = null
      this.selectedTag = ''
      this.tags = []
      this.selectedTags = []
      this.query = ''
      this.customOpen = false
      this.revokePreviews()
    },
    /** Every card tag, normalised: provider, engine, category/type and tags. */
    tagsOf(image) {
      const tags = new Set()
      ;[...(image.tags || []), image.source, image.provider, image.type]
        .filter(Boolean)
        .forEach((tag) => tags.add(String(tag).toLowerCase()))
      return [...tags]
    },
    tagOf(image) {
      const reference = String(image?.image || '')
      const colon = reference.lastIndexOf(':')
      return colon > reference.lastIndexOf('/') ? reference.slice(colon + 1) : 'latest'
    },
    repositoryOf(image) {
      const reference = String(image?.image || '')
      const colon = reference.lastIndexOf(':')
      return colon > reference.lastIndexOf('/') ? reference.slice(0, colon) : reference
    },
    async loadImages() {
      const token = ++this.loadToken
      this.loading = true
      this.error = null
      try {
        const response = await this.$storex.api.computers.imageCatalog({
          kind: this.kind,
          refresh: this.refresh
        })
        if (token !== this.loadToken) return
        this.images = response?.images || []
        this.syncFromModel()
        this.loadPreviewBlobs(token)
      } catch (error) {
        if (token !== this.loadToken) return
        this.error = error?.response?.data?.detail || error.message || 'Could not load the image catalog'
        this.images = []
      } finally {
        if (token === this.loadToken) {
          this.loading = false
          this.refresh = false
        }
      }
    },
    async loadPreviewBlobs(token) {
      // Saved computers serve their preview from an authenticated blob route.
      const cards = this.images.filter((image) => image.source === 'saved' && image.has_preview_blob)
      await Promise.allSettled(cards.map(async (image) => {
        try {
          const blob = await this.$storex.api.computers.savedComputerPreview(image.id)
          if (token !== this.loadToken) return
          const url = URL.createObjectURL(blob)
          this.previewUrls[image.id] = url
          image.preview_url = url
        } catch (error) {
          // No preview is fine: the card keeps the generic icon.
        }
      }))
    },
    revokePreviews() {
      Object.values(this.previewUrls).forEach((url) => URL.revokeObjectURL(url))
      this.previewUrls = {}
    },
    syncFromModel() {
      if (this.selected) {
        const current = this.images.find((image) => image.id === this.selected.id)
        if (current) this.selected = current
        return
      }
      if (!this.modelValue) return
      const current = this.images.find((image) =>
        image.image === this.modelValue ||
        `${this.repositoryOf(image)}:${this.tagOf(image)}` === this.modelValue
      )
      if (current) this.selectImage(current)
    },
    async selectImage(image) {
      this.selected = image
      this.selectedTag = this.tagOf(image)
      await this.loadTags()
    },
    async loadTags(force = false) {
      this.tags = []
      this.tagsLoading = false
      if (!this.selected) return
      const provider = String(this.selected.provider || this.selected.source || '').toLowerCase()
      if (provider !== 'kasmweb') return
      this.tagsLoading = true
      try {
        const response = await this.$storex.api.computers.imageTags(
          provider,
          this.repositoryOf(this.selected),
          force
        )
        this.tags = response?.tags || []
        if (this.tags.length && !this.tags.some((tag) => tag.name === this.selectedTag)) {
          this.selectedTag = this.tags[0].name
        }
      } catch (error) {
        // Keep the catalog tag; the image is still selectable.
        this.tags = []
      } finally {
        this.tagsLoading = false
      }
    },
    toggleTag(tag) {
      this.selectedTags = this.selectedTags.includes(tag)
        ? this.selectedTags.filter((value) => value !== tag)
        : [...this.selectedTags, tag]
    },
    previewFor(image) {
      return image.preview_url || image.icon_url || ''
    },
    badgeFor(image) {
      const tags = this.tagsOf(image)
      if (image.source === 'saved') return { label: 'Snapshot', class: 'badge-success' }
      if (image.source === 'custom') return { label: 'Custom', class: 'badge-warning' }
      if (tags.includes('browseruse')) return { label: 'Headless', class: 'badge-ghost' }
      if (tags.includes('cdp')) return { label: 'AI · CDP', class: 'badge-info' }
      if (tags.includes('live-view-only')) return { label: 'Live view', class: 'badge-ghost' }
      if (tags.includes('kasmvnc')) return { label: 'KasmVNC', class: 'badge-secondary' }
      return { label: 'Selkies', class: 'badge-info' }
    },
    typeLabel(image) {
      return TYPE_LABEL[String(image.type || '').toLowerCase()] || 'Image'
    },
    useSelected() {
      if (!this.selected || !this.selectedImage) return
      this.$emit('select', this.payloadFor(this.selected))
      this.$emit('close')
    },
    /** Shape the merged card like the payload the create wizard expects. */
    payloadFor(image) {
      const tags = this.tagsOf(image)
      const repository = this.repositoryOf(image)
      const base = {
        id: image.id,
        name: image.name,
        description: image.description || '',
        category: this.typeLabel(image),
        tags: image.tags || [],
        // Keep the card art in the wizard's selection summary.
        screenshot_url: image.preview_url || null,
        icon_url: image.icon_url || null
      }
      if (image.source === 'custom') {
        const manifest = image.coworking_image || {}
        return {
          ...base,
          provider: 'custom',
          coworking_id: image.id,
          coworking_image: manifest,
          repository,
          image: image.image || '',
          tag: 'latest',
          live_view: 'screenshot',
          live_port: manifest.live_port || null,
          live_scheme: 'http',
          strip_prefix: false,
          control: 'docker',
          viewer_user: manifest.desktop_user || null
        }
      }
      if (image.source === 'saved') {
        return {
          ...base,
          provider: 'saved',
          library_item_id: image.id,
          image_provider: image.provider,
          repository,
          image: image.image || '',
          tag: 'saved',
          default_tag: 'saved',
          live_view: null,
          control: null,
          screenshot_url: null
        }
      }
      return {
        ...base,
        provider: image.provider || image.source,
        repository,
        tag: this.selectedTag || this.tagOf(image),
        default_tag: this.tagOf(image),
        image: `${repository}:${this.selectedTag || this.tagOf(image)}`,
        live_view: tags.includes('kasmvnc')
          ? 'kasmvnc'
          : (tags.includes('browseruse') ? 'screenshot' : 'selkies'),
        control: tags.includes('cdp') ? 'cdp' : 'docker',
        browser_engine: tags.includes('firefox') ? 'firefox' : (tags.includes('chromium') ? 'chromium' : null),
        supports_cdp: tags.includes('cdp') ? true : (tags.includes('live-view-only') ? false : null)
      }
    },
    async deleteSelectedSaved() {
      const image = this.selected
      if (!image || image.source !== 'saved') return
      if (!window.confirm(`Delete the saved computer "${image.name}" and its Docker image?`)) return
      try {
        await this.$storex.api.computers.deleteSavedComputer(image.id)
        const preview = this.previewUrls[image.id]
        if (preview) URL.revokeObjectURL(preview)
        delete this.previewUrls[image.id]
        this.images = this.images.filter((entry) => !(entry.source === 'saved' && entry.id === image.id))
        this.selected = null
        this.$storex.ui.addNotification({ text: `Deleted saved computer "${image.name}"`, type: 'success' })
      } catch (error) {
        this.$storex.ui.addNotification({
          text: error?.response?.data?.detail || 'Could not delete the saved computer',
          type: 'error'
        })
      }
    },
    onCoworkingSelect(image) {
      // The gallery already shaped the payload like a catalog entry.
      this.customOpen = false
      this.$emit('select', image)
      this.$emit('close')
    },
    refreshCatalog() {
      this.refresh = true
      this.loadImages()
    },
  }
}
</script>

<template>
  <div
    :class="embedded ? 'flex flex-col min-h-0' : 'modal modal-open'"
    :role="embedded ? null : 'dialog'"
    :aria-modal="embedded ? null : 'true'"
    :aria-label="embedded ? null : sessionLabels.imageLibrary"
    @click.self="embedded ? null : $emit('close')"
  >
    <div
      class="flex flex-col gap-3"
      :class="embedded ? 'min-h-0 max-h-[62vh]' : 'modal-box w-11/12 md:w-[46rem] max-w-[46rem] max-h-[78vh]'"
    >
      <!-- Title -->
      <div class="flex items-center gap-2 flex-shrink-0">
        <i class="fa-solid fa-images text-primary" aria-hidden="true"></i>
        <h3 class="flex-1 text-sm font-bold">
          {{ sessionLabels.imageLibrary }}
          <span class="ml-1 text-[11px] font-normal text-base-content/40">all images · filter by tag</span>
        </h3>
        <button
          type="button"
          class="btn btn-xs btn-ghost gap-1"
          title="Create or edit custom images"
          :class="customOpen ? 'btn-primary' : ''"
          @click="customOpen = !customOpen"
        >
          <i class="fa-solid fa-wand-magic-sparkles"></i>
          Custom images
        </button>
        <button
          type="button"
          class="btn btn-xs btn-ghost btn-square"
          :title="embedded ? 'Back to the session' : 'Close'"
          @click="$emit('close')"
        >
          <i :class="embedded ? 'fa-solid fa-arrow-left' : 'fa-solid fa-xmark'"></i>
        </button>
      </div>

      <!-- Search + refresh -->
      <div class="flex flex-wrap items-center gap-2">
        <label class="input input-sm input-bordered flex items-center gap-2 flex-1 min-w-[12rem]">
          <i class="fa-solid fa-magnifying-glass text-base-content/40"></i>
          <input
            v-model="query"
            type="search"
            class="grow"
            :placeholder="isBrowser ? 'Search Chromium, Brave, Firefox…' : 'Search Ubuntu, Kali, VS Code…'"
          />
        </label>
        <button
          class="btn btn-sm btn-ghost btn-square"
          title="Reload the catalog from upstream"
          :disabled="loading"
          @click="refreshCatalog"
        >
          <i class="fa-solid fa-arrows-rotate" :class="loading ? 'animate-spin' : ''"></i>
        </button>
      </div>

      <!-- Tag filter (provider is a tag, like the rest) -->
      <div v-if="!customOpen && tagOptions.length" class="flex items-center gap-1.5 flex-wrap">
        <button
          type="button"
          class="btn btn-xs"
          :class="!selectedTags.length ? 'btn-primary' : 'btn-ghost'"
          @click="selectedTags = []"
        >All</button>
        <button
          v-for="tag in tagOptions"
          :key="tag"
          type="button"
          class="btn btn-xs"
          :class="selectedTags.includes(tag) ? 'btn-primary' : 'btn-ghost'"
          @click="toggleTag(tag)"
        >{{ tag }}</button>
        <span class="ml-auto text-[11px] text-base-content/40">
          {{ visibleImages.length }} of {{ images.length }}
        </span>
      </div>

      <p v-if="error" class="rounded-lg bg-error/10 px-2.5 py-2 text-[11px] text-error">
        <i class="fa-solid fa-circle-exclamation mr-1"></i>{{ error }}
      </p>

      <!-- Grid / custom gallery -->
      <div v-if="customOpen" class="flex-1 min-h-[16rem] overflow-y-auto pr-1">
        <CoworkingImageGallery @select="onCoworkingSelect" @close="customOpen = false" />
      </div>
      <div v-else class="flex-1 min-h-[16rem] overflow-y-auto pr-1">
        <div v-if="loading" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          <div v-for="n in 6" :key="n" class="h-36 rounded-xl bg-base-200 animate-pulse"></div>
        </div>
        <div v-else-if="!visibleImages.length" class="flex flex-col items-center justify-center py-14 text-base-content/40">
          <i class="fa-regular fa-images text-3xl mb-2"></i>
          <p class="text-sm">{{ emptyMessage }}</p>
        </div>
        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          <button
            v-for="image in visibleImages"
            :key="`${image.source}:${image.id}`"
            type="button"
            class="group relative flex flex-col text-left rounded-xl border overflow-hidden transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            :class="selected?.id === image.id
              ? 'border-primary bg-primary/5 ring-1 ring-primary'
              : 'border-base-content/10 bg-base-100 hover:border-primary/40 hover:bg-base-200/50'"
            @click="selectImage(image)"
          >
            <div class="relative h-24 w-full overflow-hidden bg-base-300/40">
              <img
                v-if="previewFor(image)"
                :src="previewFor(image)"
                :alt="image.name"
                loading="lazy"
                class="h-full w-full object-cover"
                @error="$event.target.style.display = 'none'"
              />
              <div v-else class="flex h-full items-center justify-center text-2xl text-base-content/25">
                <i :class="image.type === 'browser' ? 'fa-solid fa-compass' : (image.source === 'custom' ? 'fa-solid fa-wand-magic-sparkles' : 'fa-solid fa-desktop')"></i>
              </div>
              <span class="absolute left-1.5 top-1.5 badge badge-xs gap-1 bg-base-100/90 border-base-content/10">
                {{ image.provider || image.source }}
              </span>
              <span class="absolute right-1.5 top-1.5 badge badge-xs" :class="badgeFor(image).class">
                {{ badgeFor(image).label }}
              </span>
            </div>
            <div class="flex flex-1 flex-col gap-0.5 p-2">
              <span class="flex items-center gap-1.5">
                <span class="text-xs font-semibold truncate">{{ image.name }}</span>
                <span class="badge badge-xs badge-ghost ml-auto">{{ typeLabel(image) }}</span>
              </span>
              <span class="line-clamp-2 text-[11px] leading-snug text-base-content/55">
                {{ image.description }}
              </span>
              <span class="mt-1 flex flex-wrap gap-1">
                <span
                  v-for="tag in tagsOf(image).slice(0, 4)"
                  :key="tag"
                  class="badge badge-xs badge-outline font-mono text-[9px]"
                >{{ tag }}</span>
                <span
                  v-if="tagsOf(image).length > 4"
                  class="badge badge-xs badge-ghost text-[9px]"
                >+{{ tagsOf(image).length - 4 }}</span>
              </span>
              <span class="mt-auto flex items-center gap-2 pt-1 font-mono text-[10px] text-base-content/35">
                <span class="truncate">{{ image.image }}</span>
                <span v-if="image.status && image.status !== 'ready'" class="ml-auto whitespace-nowrap text-warning">
                  {{ image.status }}
                </span>
              </span>
            </div>
            <span
              v-if="selected?.id === image.id"
              class="absolute right-1.5 bottom-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-content"
            ><i class="fa-solid fa-check text-[10px]"></i></span>
          </button>
        </div>
      </div>

      <!-- Selected image + version tag -->
      <div v-if="selected" class="rounded-xl border border-primary/30 bg-primary/5 p-2.5 flex flex-wrap items-center gap-2">
        <i class="fa-solid fa-circle-check text-primary"></i>
        <span class="text-xs font-semibold">{{ selected.name }}</span>
        <code class="text-[10px] text-base-content/50">{{ selectedImage }}</code>
        <label v-if="tags.length || tagsLoading" class="ml-auto flex items-center gap-1 text-[11px]">
          <span class="text-base-content/60">Tag</span>
          <select v-model="selectedTag" class="select select-xs select-bordered font-mono" :disabled="tagsLoading">
            <option v-for="tag in tags" :key="tag.name" :value="tag.name">{{ tag.name }}</option>
          </select>
          <span v-if="tagsLoading" class="loading loading-spinner loading-xs"></span>
        </label>
        <button
          v-if="selected.source === 'saved'"
          class="btn btn-xs btn-ghost text-error ml-auto"
          title="Delete this saved computer and its Docker image"
          @click="deleteSelectedSaved"
        >
          <i class="fa-regular fa-trash-can"></i>
        </button>
        <button
          class="btn btn-xs btn-primary"
          :class="selected.source === 'saved' ? '' : 'ml-auto'"
          :disabled="!selectedImage"
          @click="useSelected"
        >
          Use image
        </button>
      </div>

      <p class="text-[10px] leading-snug text-base-content/40">
        Tags are the image provider (linuxserver, kasmweb, custom, saved, browseruse),
        its category and its capabilities (cdp, live-view-only, chromium, firefox…).
        Images are pulled on first use; a live-view-only image cannot be driven by the AI.
      </p>
    </div>
  </div>
</template>
