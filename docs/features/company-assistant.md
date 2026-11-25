# Company assistant

> **Status:** implemented · **UI:** company shell → **Ask codx-junior** ·
> **Profile:** `codx-junior` · **API:** `POST /api/companies/{id}/assistant/chat`

The company assistant is a **real chat**, not a custom quick-reply panel. The
company provisions one `Chat` hosted by one of its visible projects and opens it
with the standard `ChatView`, so streaming, tools, mentions, attachments, voice
calls and message history are the same as in every other conversation.

The chat is created once (deterministic id), uses the **`codx-junior` profile**,
and is not a company channel: it does not appear in the Messenger channel list.

## Starter prompts

The drawer shows a small set of predefined queries for the current section —
the executive brief, what needs attention, deliveries to review, blocked
tickets, business status, weekly review, and so on. Clicking one sends it
through the normal Chat composer pipeline (`Chat.sendPrompt`), so the turn is
persisted, streamed and processed exactly like typed input. The starter prompts
disappear after the first user message and can be toggled back from the wand
button in the drawer header.

## Company context and roles

The chat engine appends a live company section to the profile prompt: company
name/id, linked projects with their ids, teams, open goals and budget. This is
how the assistant can place a ticket on the right project without guessing.

The profile grants every tool. A **read-only company member** therefore gets a
narrower allowlist applied by the chat engine (project investigation, ticket
queries and web reads only); managers and admins keep the full set. This keeps
`codx-junior` as the profile while respecting the company role.

## Tools

With the full tool set the assistant can:

* inspect the company, projects and repository;
* operate the company with `company_management`;
* place, run, comment on and review project tickets with `company_ticket`;
* answer with the same business context used by the rest of the company shell.

A read-only member is told to name the manager action instead of claiming a
change it cannot make.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/companies/{id}/assistant/chat` | Ensure the assistant chat and return its locator (`chat_id`, project ids). |

The conversation itself uses the normal chat endpoints (`/api/chats/*`). The
client loads the returned chat and renders `ChatView`.
