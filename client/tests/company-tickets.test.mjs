import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// ─────────────────────────────────────────────────────────────────────────────
// COMPANY TICKETS + ASSISTANT CHAT CONTRACT
//
// The assistant is no longer a custom panel: it is a real ChatView backed by a
// company-owned chat with the `codx-junior` profile. These assertions guard the
// pieces that make that true without rendering the SPA.
// ─────────────────────────────────────────────────────────────────────────────

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

const shellSource = await read('../src/views/CompanyView.vue')
const routerSource = await read('../src/router/index.ts')
const apiSource = await read('../src/api/modules/companies.js')
const ticketsSource = await read('../src/components/company/CompanyTickets.vue')
const assistantSource = await read('../src/components/company/CompanyAssistant.vue')
const chatViewSource = await read('../src/views/ChatView.vue')
const chatSource = await read('../src/components/chat/Chat.vue')
const activitySource = await read('../src/components/company/CompanyActivity.vue')

test('the company shell exposes the ticket workbench', () => {
  assert.match(shellSource, /import CompanyTickets from '@\/components\/company\/CompanyTickets\.vue'/)
  assert.match(shellSource, /id: 'tickets'/)
  assert.match(shellSource, /section === 'tickets'/)
  assert.match(shellSource, /<CompanyTickets/)
})

test('the router accepts the tickets company section', () => {
  const sections = routerSource.slice(
    routerSource.indexOf('const COMPANY_SECTIONS'),
    routerSource.indexOf(']', routerSource.indexOf('const COMPANY_SECTIONS'))
  )
  assert.match(sections, /'tickets'/)
})

test('the client API covers the ticket lifecycle and the assistant chat', () => {
  for (const method of [
    'tickets(companyId',
    'ticketSummary(companyId',
    'ticket(companyId',
    'createTicket(companyId',
    'commentTicket(companyId',
    'configureTicket(companyId',
    'queueTicket(companyId',
    'runTicket(companyId',
    'cancelTicket(companyId',
    'reviewTicket(companyId',
    'assistantChat(companyId'
  ]) {
    assert.ok(apiSource.includes(method), `missing ${method}`)
  }
})

test('the workbench can place, run and review a ticket', () => {
  assert.match(ticketsSource, /api\.createTicket/)
  assert.match(ticketsSource, /api\.runTicket/)
  assert.match(ticketsSource, /api\.queueTicket/)
  assert.match(ticketsSource, /api\.reviewTicket/)
  assert.match(ticketsSource, /decision === 'accept'/)
  assert.match(ticketsSource, /review_pending/)
  assert.match(ticketsSource, /validationPayload/)
})

test('the company assistant is a real ChatView conversation', () => {
  assert.match(assistantSource, /import ChatView from '@\/views\/ChatView\.vue'/)
  assert.match(assistantSource, /<ChatView/)
  assert.match(assistantSource, /hide-chat-sidebar/)
  assert.match(assistantSource, /API\.companies\.assistantChat\(props\.companyId\)/)
  assert.match(assistantSource, /PROMPT_SETS/)
  assert.match(assistantSource, /sendPrompt\(prompt\)/)
  assert.match(assistantSource, /codx-junior profile/)
  // The old custom composer/panel is gone.
  assert.doesNotMatch(assistantSource, /api\.assistant\(/)
  assert.doesNotMatch(assistantSource, /toggleDictation/)
})

test('ChatView exposes prompt sending and can hide its own sidebar', () => {
  assert.match(chatViewSource, /v-if="!isMobile && !hideChatSidebar"/)
  assert.match(chatViewSource, /ref="chatBox"/)
  assert.match(chatViewSource, /async sendPrompt\(prompt\)/)
  assert.match(chatSource, /async sendPrompt\(prompt\)/)
  assert.match(chatSource, /postMyMessage\(\{ message: value \}\)/)
})

test('the activity entries and follow-up block adapt to mobile', () => {
  assert.match(activitySource, /70dvh/)
  assert.match(activitySource, /break-words text-\[13px\]/)
  assert.match(activitySource, /sm:hidden/)
  assert.match(activitySource, /sm:grid-cols-2/)
})
