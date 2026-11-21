# Chat/task storage: deep analysis and refactor proposal

> **Status:** proposal **implemented in core** — Phase 0 + Phase 1 read model,
> per-user drafts/read cursors and tool-output blobs are in the branch. See
> [05-implementation.md](05-implementation.md) for what is live, how to enable
> it, and what remains.
> **Scope:** persistence and read/write paths of chats and tasks (a task is a chat
> stored in `.codx/tasks`), for single-project and multi-user deployments.
> **Measured on:** this checkout, project `codx-junior`, **1,841 chat files /
> 392.6 MB / ~9.5k messages** (2026-11-21). See
> [01-baseline.md](01-baseline.md) for the method and raw numbers.

## Executive summary

The platform is slow because **every chat/task operation treats one task as one
whole JSON document and scans the whole task tree to find it**. Listing 30 days
of chats parses **210 MB** and returns a **~50 MB** JSON response; search parses
the same store and — because `list_chats` truncates messages first — currently
searches **only the last message of each chat**. A single tool call inside a turn
rewrites the whole chat file **twice** and runs **two full `rglob` scans**, so a
14 MB task with 109 tool calls performs ~218 full rewrites and ~218 tree walks
(~1 GB written to one file) in one turn.

The files are not the problem. **The access pattern is.** Chat JSON files are
excellent for git, humans and LLMs; they are a terrible database. The fix is to
keep the files as the canonical, LLM-friendly artifact and add a **small,
rebuildable per-project SQLite index** that serves every list/filter/search/
unread query, while writes update the index in the same request.

Measured prototype on the real store (stdlib `sqlite3`, FTS5):

| | today | prototype index | delta |
|---|---|---|---|
| list 30 days (472 chats) | **2.7 s**, ~50 MB payload | **0.1 ms** query | ~27,000× |
| list all 1,841 chats | **5.0 s** cold | **0.1 ms** query | ~50,000× |
| full-text search | **5.4 s**, last message only | **5 ms**, all 9,533 messages | ~1,000× |
| `find_by_id` | **283 ms** (tree scan) | **< 1 ms** (PK lookup) | ~300× |
| index size | — | **3.9 MB metadata + 31 MB FTS** | 9 % of store |
| rebuild from files | — | **~6 s** (2 s parse + 4 s index) | — |

## Root causes (ranked)

1. **Whole-tree scans on every write.** `save_chat` always calls `find_by_id`
   (`api/codx/junior/chat_manager.py:1539`), which walks the entire `tasks` tree
   (`chat_manager.py:1779-1818`). Fixed cost ~0.35 s per persist at today's size,
   ~2 s at 10k files.
2. **Whole-document read-modify-write per message/tool event.** `add_message` /
   `update_message` reload the whole chat, merge, and rewrite it
   (`chat_manager.py:1279-1438`). `ChatEventBridge` does this on **every tool
   start and finish** (`chat/chat_event_bridge.py:336-386`) and every 5 s while
   streaming.
3. **List/search parse every file in full.** `list_chats` JSON-parses and fully
   pydantic-validates every chat, then truncates to the last message
   (`chat_manager.py:710-779`, `1674-1725`). The metadata the UI needs is only
   **8.8 MB (2.2 % of the store)**; the store is 392 MB.
4. **The in-process cache makes it worse.** `_CHAT_INFO_CACHE` deep-copies
   ~103 MB of parsed chats on every list call and holds **~234 MB RSS** per
   worker (measured), with a 5,000-entry cliff that clears everything.
5. **Tool output bloat.** `tool_events` is **~174 MB (44 %)** of the store and
   is stored untruncated by design (`chat_event_bridge.py:347-377`). It is never
   needed by a list/board/search query, yet it is parsed, validated, deep-copied,
   serialized, redacted and sent over Socket.IO.
6. **The 30-day list sends ~50 MB to the browser.** 472 chats × last message
   (46.3 MB of `tool_events`/content). Project setup awaits this call
   (`client/src/store/project.js:733`), so the whole UI waits for it.
7. **User-local state lives in shared files.** Drafts (one `unsent` slot per
   chat, no owner), `read_by`, and unread counts are written into the shared
   task JSON. Two users overwrite each other; a read receipt rewrites a whole
   (possibly 14 MB) file.

## Recommendation

Adopt a **derived SQLite read model** (not a new source of truth), in phases:

- **Phase 0 — stop the bleeding (days).** Fix the user-filter crash and the
  security gaps; make `find_by_id` O(1); parse metadata-only for list; drop the
  deep-copy cache; coalesce/avoid needless writes; externalize oversized
  `tool_events`/attachments to blobs. Expected: list 5 s → **< 1 s**, writes
  1.5 s → **< 100 ms**, even before the index.
- **Phase 1 — per-project chat index (1.5–3 weeks).** SQLite WAL + FTS5 at
  `<codx>/index/chats.db`, schema-versioned, rebuildable, reconciled with the
  files. List/board/planning/tasks/calendar/search/unread all read from it.
  Expected: list **< 10 ms**, search **< 50 ms**, `find_by_id` **< 1 ms**,
  payload **~1–3 MB**.
- **Phase 2 — write path and payload hygiene (1–2 weeks).** Content-addressed
  blobs for large tool output/attachments with inline preview, incremental
  redaction, per-chat write locks + optimistic `rev`, per-user drafts/read state
  in the index. Expected: every write **< 50 ms**; store **~200 MB**; no lost
  updates.
- **Phase 3 (optional, only if needed).** Split oversized chats into
  `chat.json` + `messages/*` for constant write cost, and Postgres if the
  deployment ever becomes multi-host. **Not recommended now.**

Do **not** replace the files with a DB, and do **not** adopt an external search
service or vector DB as the primary store. Files stay canonical; the index is
disposable and can be deleted at any time.

## Documents

| Document | Contents |
|---|---|
| [01-baseline.md](01-baseline.md) | Method, measured baseline, data composition, top offenders, data-quality and correctness bugs that shape the design |
| [02-options.md](02-options.md) | Options from "tune the current code" to Postgres/LanceDB, comparison matrix, recommendation rationale |
| [03-target-architecture.md](03-target-architecture.md) | Proposed design: index schema, projections, read/write flows, invalidation/rebuild, multi-user/ACL, LLM-friendly payloads, API contract |
| [04-roadmap.md](04-roadmap.md) | Phased delivery, migration, SLOs, test matrix, rollout/rollback, risks |
| [05-implementation.md](05-implementation.md) | Implemented modules, configuration, verification, measured after-numbers, migration and remaining work |
| [bench/chat_store_bench.py](bench/chat_store_bench.py) | Reproducible stdlib-only benchmark: current scan/parse vs prototype index |

## Guiding constraints (from the code and the product)

1. **Files are the truth.** Task JSON is git-tracked (2,038 files in this repo),
   hand-editable and read by LLMs with file tools. The index must be
   rebuildable from files alone.
2. **LLM-friendly means compact and readable.** A 14 MB JSON is not LLM-friendly.
   Externalizing tool output with a short preview *improves* readability.
3. **Two API processes minimum.** The API and the background worker run from the
   same image (`supervisor.conf`); both write chats. The index must be
   multi-process safe (SQLite WAL) — an in-memory cache is not enough.
4. **Multi-user correctness must not depend on luck.** Per-user state must be
   per-user; ACL must be applied server-side; whole-document saves need
   optimistic concurrency.
5. **No new service dependency** for the default installation. SQLite is already
   used for the project atlas (`api/codx/junior/atlas/store.py`), proving the
   pattern in this codebase.
