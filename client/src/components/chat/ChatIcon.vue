<template>
  <span
    class="tooltip tooltip-left"
    :class="{ 'animate-pulse': attention }"
    :data-tip="attention ? 'Needs your attention' : mode"
  >
    <span :class="iconColor('text-primary')" v-if="mode === 'task'"><i class="fa-regular fa-file-lines"></i></span>
    <span :class="iconColor('text-secondary')" v-if="!mode || mode === 'chat'"><i class="fa-regular fa-comment"></i></span>
    <span :class="iconColor('text-accent')" v-if="mode === 'code'"><i class="fa-solid fa-code"></i></span>
    <span :class="iconColor('text-info')" v-if="mode === 'browser'"><i class="fa-brands fa-chrome"></i></span>
    <span :class="iconColor('text-info')" v-if="mode === 'topic'"><i class="fa-regular fa-comments"></i></span>
    <span :class="iconColor('text-info')" v-if="mode === 'channel'"><i class="fa-solid fa-hashtag"></i></span>
    <span :class="iconColor('text-info')" v-if="mode === 'answer'"><i class="fa-solid fa-check-double"></i></span>
    <span :class="iconColor('text-info')" v-if="mode === 'prview'"><i class="fa-solid fa-code-pull-request"></i></span>
    <span :class="iconColor('text-warning')" v-if="mode === 'slides'"><i class="fa-solid fa-chalkboard"></i></span>
    <span :class="iconColor('text-warning')" v-if="mode === 'vibe'"><i class="fa-solid fa-hand-sparkles"></i></span>
  </span>
</template>

<script>
export default {
  props: {
    mode: {
      type: String,
      default: ''
    },
    // A machine session of this chat/task is waiting for the user (handoff,
    // login, MFA, captcha, payment approval). Optional and false by default, so
    // every existing usage keeps its normal mode colour. While true the icon
    // blinks in the warning colour until the user takes control.
    attention: {
      type: Boolean,
      default: false
    }
  },
  methods: {
    iconColor(baseClass) {
      return this.attention ? 'text-warning' : baseClass
    }
  }
}
</script>
