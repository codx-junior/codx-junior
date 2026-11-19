<template>
  <!-- Collapsed: only the affordance, it must not steal the reading space -->
  <button
    v-if="!open"
    class="btn btn-ghost btn-xs btn-circle bg-base-100/80 backdrop-blur shadow"
    title="Find in this chat (Ctrl+F)"
    aria-label="Find in this chat"
    @click="openBar"
  >
    <i class="fa-solid fa-magnifying-glass text-xs"></i>
  </button>

  <div
    v-else
    class="flex flex-col gap-1 rounded-lg border border-base-300 bg-base-100/95 backdrop-blur shadow-lg p-1.5"
  >
    <div class="flex items-center gap-1">
      <div class="flex items-center gap-1 input input-xs input-bordered">
        <i class="fa-solid fa-magnifying-glass text-base-content/50"></i>
        <input
          ref="input"
          v-model="query"
          type="text"
          class="bg-transparent w-40 min-w-0"
          placeholder="Find in messages"
          aria-label="Find in messages"
          @keydown.enter.prevent="step($event.shiftKey ? -1 : 1)"
          @keydown.esc.prevent="closeBar"
        />
        <button
          v-if="query"
          class="text-base-content/50 hover:text-base-content"
          title="Clear"
          @click="query = ''"
        >
          <i class="fa-regular fa-circle-xmark"></i>
        </button>
      </div>

      <!-- Occurrence counter: "3 / 17" (or the regex error) -->
      <span
        class="text-xs tabular-nums px-1 whitespace-nowrap"
        :class="error ? 'text-error' : 'text-base-content/70'"
      >{{ error || counter }}</span>

      <button
        class="btn btn-ghost btn-xs btn-square"
        :disabled="!total"
        title="Previous match (Shift+Enter)"
        aria-label="Previous match"
        @click="step(-1)"
      >
        <i class="fa-solid fa-chevron-up"></i>
      </button>
      <button
        class="btn btn-ghost btn-xs btn-square"
        :disabled="!total"
        title="Next match (Enter)"
        aria-label="Next match"
        @click="step(1)"
      >
        <i class="fa-solid fa-chevron-down"></i>
      </button>
      <button
        class="btn btn-ghost btn-xs btn-square"
        title="Close (Esc)"
        aria-label="Close search"
        @click="closeBar"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>

    <!-- Modifiers: match case · whole word · regular expression -->
    <div class="flex items-center gap-1 pl-0.5">
      <button
        class="btn btn-xs btn-square font-semibold"
        :class="caseSensitive ? 'btn-primary' : 'btn-ghost'"
        :aria-pressed="caseSensitive"
        title="Match case"
        @click="caseSensitive = !caseSensitive"
      >Aa</button>
      <button
        class="btn btn-xs btn-square"
        :class="wholeWord ? 'btn-primary' : 'btn-ghost'"
        :aria-pressed="wholeWord"
        title="Whole word"
        @click="wholeWord = !wholeWord"
      >ab<span class="font-mono">|</span></button>
      <button
        class="btn btn-xs btn-square font-mono"
        :class="regex ? 'btn-primary' : 'btn-ghost'"
        :aria-pressed="regex"
        title="Regular expression"
        @click="regex = !regex"
      >.*</button>
      <span v-if="!highlightSupported" class="text-[10px] text-base-content/50">
        highlighting not supported
      </span>
    </div>
  </div>
</template>

<script>
import { findMatchesInMessages, findMatchesInText } from '@/utils/chatFind'

// CSS Custom Highlight API registry names (see the style block below).
const HIGHLIGHT_ALL = 'chat-find'
const HIGHLIGHT_CURRENT = 'chat-find-current'

export default {
  name: 'ChatFindBar',
  props: {
    // The conversation being searched.
    messages: { type: Array, default: () => [] },
    // The scrollable element that owns the `[data-message-ix]` wrappers.
    container: { type: Object, default: null },
  },
  data() {
    return {
      open: false,
      query: '',
      // Modifiers
      caseSensitive: false,
      wholeWord: false,
      regex: false,
      // Index into `matches` of the occurrence the user is standing on.
      activeIndex: 0,
    }
  },
  computed: {
    result() {
      return findMatchesInMessages(this.messages, {
        query: this.query,
        caseSensitive: this.caseSensitive,
        wholeWord: this.wholeWord,
        regex: this.regex,
      })
    },
    matcher() {
      return this.result.matcher
    },
    error() {
      return this.result.error
    },
    matches() {
      return this.result.matches
    },
    total() {
      return this.matches.length
    },
    activeMatch() {
      return this.matches[this.activeIndex] || null
    },
    activeMessageIndex() {
      return this.activeMatch ? this.activeMatch.messageIndex : -1
    },
    counter() {
      if (!this.query) return ''
      if (!this.total) return '0 / 0'
      return `${this.activeIndex + 1} / ${this.total}`
    },
    // Painting uses the CSS Custom Highlight API so the DOM of the messages is
    // never mutated: Vue keeps full control of the rendered markdown.
    highlightSupported() {
      return typeof CSS !== 'undefined' && !!CSS.highlights && typeof Highlight !== 'undefined'
    },
  },
  watch: {
    query() {
      this.onCriteriaChanged()
    },
    caseSensitive() {
      this.onCriteriaChanged()
    },
    wholeWord() {
      this.onCriteriaChanged()
    },
    regex() {
      this.onCriteriaChanged()
    },
    total() {
      // Streaming/new messages change the amount of matches: repaint.
      if (this.open) this.$nextTick(() => this.repaint())
    },
    container() {
      this.repaint()
    },
  },
  mounted() {
    window.addEventListener('keydown', this.onWindowKeydown)
  },
  beforeUnmount() {
    window.removeEventListener('keydown', this.onWindowKeydown)
    clearTimeout(this.repaintTimer)
    this.clearHighlights()
  },
  methods: {
    openBar() {
      this.open = true
      this.$nextTick(() => this.focusInput())
    },
    closeBar() {
      this.open = false
      this.query = ''
      this.activeIndex = 0
      this.clearHighlights()
    },
    focusInput() {
      const input = this.$refs.input
      if (!input) return
      input.focus()
      input.select?.()
    },
    // Any change of query or modifier restarts the walkthrough at the first hit.
    onCriteriaChanged() {
      this.activeIndex = 0
      this.$nextTick(() => {
        this.repaint()
        if (this.total) this.jumpToActive()
      })
    },
    step(delta) {
      if (!this.total) return
      this.activeIndex = (this.activeIndex + delta + this.total) % this.total
      this.repaint()
      this.jumpToActive()
    },
    messageElement(messageIndex) {
      const el = this.container
      if (!el || typeof el.querySelector !== 'function') return null
      return el.querySelector(`[data-message-ix="${messageIndex}"]`)
    },
    jumpToActive() {
      const el = this.messageElement(this.activeMessageIndex)
      if (!el) return
      el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      // Messages are lazily mounted: the first repaint often lands on a
      // skeleton. Repaint again once the mount has settled.
      this.$nextTick(() => this.repaint())
      clearTimeout(this.repaintTimer)
      this.repaintTimer = setTimeout(() => this.repaint(), 250)
    },
    repaint() {
      if (!this.highlightSupported) return
      this.clearHighlights()
      if (!this.matcher || !this.container) return

      const all = []
      const current = []
      const messageIndexes = [...new Set(this.matches.map(match => match.messageIndex))]
      messageIndexes.forEach(messageIndex => {
        const root = this.messageElement(messageIndex)
        if (!root) return
        this.collectRanges(root, messageIndex === this.activeMessageIndex, all, current)
      })

      if (all.length) CSS.highlights.set(HIGHLIGHT_ALL, new Highlight(...all))
      if (current.length) CSS.highlights.set(HIGHLIGHT_CURRENT, new Highlight(...current))
    },
    collectRanges(root, isCurrent, all, current) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      let node = walker.nextNode()
      while (node) {
        const text = node.nodeValue
        if (text && text.trim()) {
          findMatchesInText(text, this.matcher).forEach(hit => {
            const range = document.createRange()
            range.setStart(node, hit.start)
            range.setEnd(node, hit.end)
            all.push(range)
            if (isCurrent) current.push(range)
          })
        }
        node = walker.nextNode()
      }
    },
    clearHighlights() {
      if (!this.highlightSupported) return
      CSS.highlights.delete(HIGHLIGHT_ALL)
      CSS.highlights.delete(HIGHLIGHT_CURRENT)
    },
    onWindowKeydown(event) {
      const key = String(event.key || '').toLowerCase()
      if (key === 'escape' && this.open) {
        this.closeBar()
        return
      }
      if (key !== 'f' || !(event.ctrlKey || event.metaKey) || event.altKey) return

      // Only take Ctrl/Cmd+F over while the reader is inside this conversation
      // (or already searching in it), so other panels keep their own shortcut.
      const active = document.activeElement
      const inside = !!this.container && !!active && this.container.contains(active)
      if (!inside && !this.open) return
      event.preventDefault()
      this.openBar()
    },
  },
}
</script>

<!-- NOT scoped on purpose: a ::highlight() pseudo-element cannot carry the
     scoped data attribute, so these rules must stay global. -->
<style>
::highlight(chat-find) {
  background-color: #fde68a;
  color: inherit;
}

::highlight(chat-find-current) {
  background-color: #f59e0b;
  color: #111827;
}
</style>
