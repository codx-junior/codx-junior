import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// COWORKING CHAT PROFILES
//
// A co-desktop chat hides the normal files/profiles strip, so the profiles of
// the conversation must be visible in the chat pane header. The header uses the
// shared profile selector, whose trigger renders the active profiles as
// avatars (name in a tooltip) inside a DaisyUI avatar group instead of printing
// the profile names. These tests read the sources; the SFCs are compile-checked
// separately by the build.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const panel = await read('../src/components/browsers/BrowserSessionPanel.vue')
const profileSelector = await read('../src/components/chat/ChatProfileSelector.vue')
const baseSelector = await read('../src/components/chat/Selector.vue')

test('the coworking chat header renders the profile selector', () => {
  assert.match(
    panel,
    /import ChatProfileSelector from '@\/components\/chat\/ChatProfileSelector\.vue'/,
    'the browser session panel imports the shared profile selector'
  )
  // The selector lives in the header row, bound to the docked chat's profiles.
  assert.match(
    panel,
    /<ChatProfileSelector[\s\S]*?:selected-profiles="browserChatProfiles"[\s\S]*?@profiles-changed="onBrowserChatProfilesChanged"[\s\S]*?\/>/,
    'the selector shows and updates the profiles of the docked chat'
  )
  assert.match(
    panel,
    /onBrowserChatProfilesChanged\(selectedProfiles\)[\s\S]*?chat\.profiles = [\s\S]*?saveChatInfo\(chat\)/,
    'changing the profiles in the header persists them on the chat'
  )
})

test('active profiles render as avatars, not names', () => {
  assert.match(
    baseSelector,
    /<slot name="selected" :items="activeItems">/,
    'the selector exposes the active items to its owner'
  )
  assert.match(
    profileSelector,
    /<template #selected="\{ items \}">/,
    'the profile selector supplies its own active rendering'
  )
  assert.match(
    profileSelector,
    /class="avatar-group -space-x-2 rtl:space-x-reverse"/,
    'several avatars overlap as a DaisyUI avatar group'
  )
  assert.match(
    profileSelector,
    /:data-tip="profile\.name"/,
    'each avatar shows the profile name in a tooltip'
  )
  assert.match(
    profileSelector,
    /items\.length > maxActiveAvatars[\s\S]*?\+[\s\S]*?items\.length - maxActiveAvatars/,
    'the profiles that do not fit collapse into a +N avatar'
  )
})
