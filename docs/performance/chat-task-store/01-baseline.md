# 01 — Baseline: what is slow, why, and what is broken

This document is the factual basis for the proposal. Numbers are measured on
this checkout and this project unless marked **est.**. The method is reproducible
with [bench/chat_store_bench.py](bench/chat_store_bench.py).

## 1. Method

- **Store:** `.codx/tasks` of the `codx-junior` project (the largest local
  project). 1,841 chat files, 392.6 MB, 9,506 messages.
- **Code:** `api/codx/junior` at commit `cdaa7e7` (2026-11-21). The API virtualenv
  in the checkout is Python 3.11 site-packages while the only interpreter
  available for the audit was Python 3.13, so `pydantic_core` could not load.
  Raw filesystem/JSON/index numbers were measured with `python3.13`; pydantic
  validation and redaction costs were measured through the real code with two
  tiny compatibility stubs (`slugify`, `regex`), or are marked **est.**.
- **Environment:** warm OS page cache, single process, no other load. The API
  and the background worker each own a separate process cache, so production
  pays cold-cache costs up to twice per deploy.
- **Index prototypes:** stdlib `sqlite3` 3.46 (SQLite 3.46.1), WAL, FTS5; one
  database per project, built from the same files. They are throw-away
  prototypes, not the proposed implementation.

## 2. Measured baseline

### 2.1 Read paths

| Operation | Time | Notes |
|---|---:|---|
| `chat_paths()` (2 × `rglob` over the tree) | **0.21 s** | `chat_manager.py:508-554` |
| `chat_paths(last 30 days)` (+ `stat` per file) | **0.38 s** | 474 files |
| `list_chats()` — all chats, cold | **5.0 s** | parse + validate + annotate everything |
| `list_chats()` — all chats, warm `_CHAT_INFO_CACHE` | **0.86 s** | still deep-copies ~103 MB |
| `list_chats(last 30 days)` — 472 chats | **2.7 s** | what `GET /api/chats` does by default |
| `get_recent_chats(page=1, size=10)` | **0.87 s** | loads all chats, then paginates |
| `find_by_id()` | **0.28 s** | full tree scan; ignores the cache |
| `search_chats("chat")` | **5.4 s** | list all + score; searches last message only |
| `load_chat_from_path()` small / 13.4 MB | ~0 / **45 ms** | full open is not the main problem |
| `_CHAT_INFO_CACHE` RSS after one `list_chats` | **~234 MB** | per worker, deep-copied pydantic models |

`GET /api/chats` with its default 30-day window parses **210.7 MB** and returns
**~50 MB** of JSON to the browser (46.3 MB of last messages + 2.9 MB metadata +
envelope). Project activation **awaits** this call
(`client/src/store/project.js:731-734`), so the shell is empty until it finishes.

### 2.2 Write paths

| Operation | Time | Notes |
|---|---:|---|
| `save_chat()` small chat (`chat_only`) | **0.35 s** | dominated by `find_by_id` (tree scan) |
| `save_chat()` 13.4 MB chat (`chat_only`) | **1.5 s** steady; **17 s** first run | full rewrite + redaction + fsync |
| └ `redact_payload(model_dump)` on 13.4 MB | **0.90 s** | CISO scan of every string |
| └ `json.dumps(indent=2)` | **0.06 s** | |
| └ write + `fsync` + `os.replace` | **0.26 s** | |
| `store_chat()` 13.4 MB (no scan) | **1.2 s** | |
| `add_message()` small / big | **0.36 s / 1.57 s** | per tool event |
| Draft autosave (per debounced keystroke) | **1.0–1.6 s** | 2 scans + 3 parses + full rewrite |
| `mark_message_as_seen()` | **0.4–1.5 s** | rewrites the whole chat to append one username |

A **14.1 MB** task exists (`ciso.8d0ec6fc-…json`): one assistant message has
109 tool events, 9.2 MB of tool output. The event bridge persists on every tool
start **and** finish (`chat/chat_event_bridge.py:336-386`), so that turn rewrites
the growing file ~218 times and runs ~218 full tree scans: **~1 GB written to one
file** and **~72 s of pure `rglob`** at today's store size (est. ~390 s at 10k
files).

### 2.3 What is actually in the files

| Portion | Size | Share |
|---|---:|---:|
| Chat top-level fields **without** `messages` | **8.8 MB** | **2.2 %** |
| `messages[].tool_events` | **173.6 MB** | **44.2 %** |
| `messages[].content` | 121.7 MB | 31 % |
| `messages[].meta_data` | 49.9 MB | 13 % |
| `messages[].attachments` | 13.3 MB | 3 % |
| Last visible message of each chat (what list keeps) | **98.1 MB** | **25.0 %** |
| Store total | 392.6 MB | 100 % |

File size p50 **71 KB**, p90 **430 KB**, max **14.1 MB**. 73 chats are > 1 MB,
5 > 5 MB, 1 > 10 MB. After removing `tool_events`, only **24 files** remain > 1 MB.
In other words: **the metadata needed by every card/list is 2.2 % of the data,
and ~44 % of the store is tool output no list query ever reads.**

### 2.4 Prototype index on the same store

Built from the 1,839 parseable chat files (2 corrupted files excluded; see §4):

| Variant | DB size | Build | Representative query |
|---|---:|---:|---|
| Metadata + last-message preview | **3.9 MB** | **0.58 s** index (+2.05 s JSON parse) | list 50 newest **0.09 ms**; board column **0.24 ms** |
| + contentless FTS over all 9,533 messages | **30.8 MB** | **3.63 s** index | search "chat" **4.96 ms**; prefix **1.42 ms** |
| + full external-content FTS (snippets, updates) | 150 MB | 12.8 s | search **9.2 ms** |
| **Recommended default: metadata + contentless FTS** | **~35 MB (9 % of store)** | **~6 s total** | list < 0.3 ms, search < 5 ms |

This is the core argument: **a 35 MB disposable index answers in microseconds
what currently costs seconds and 200 MB of I/O.** It is 9 % of the store, and it
can be deleted at any time.

## 3. Top offenders (server side)

| # | Path | Cost at 1.8k files | At 10k files (linear est.) |
|---|---|---|---|
| 1 | **Per-tool-event persistence** — 2 full writes + 2 tree scans + 2 whole-message socket frames per tool call (`chat_event_bridge.py:336-386`, `chat_manager.py:1320-1438`) | 0.9–1.6 s per tool call; 50-tool turn **45–80 s**; a 109-tool turn **130–220 s** | 4–5 s per tool call |
| 2 | **`list_chats` full parse** — every list surface (`chat_manager.py:710-779`) | **4.5–6 s** cold | **20–30 s** |
| 3 | **Management overview** — 4 cross-project `load_tasks` per request (`management_manager.py:817,867-868,880`) | **18–24 s** | 80–120 s |
| 4 | **Draft autosave** — per debounce, 2 scans + 3 parses + rewrite (`api/chat.py:1160`, `db.py:952-988`) | 1.0–1.6 s while typing | 4–6 s |
| 5 | **`GET /api/tasks`** — all chats, no date bound (`task_board.py:502-542`) | 5–7 s cold | 25–35 s |
| 6 | **Kanban search** rebuild every 5 s (`kanban/search.py:38,197-226`) | 4.5–6 s per keystroke burst | 20–30 s |
| 7 | **Per-turn scaffolding** — parent lookups ×3 (`chat_prompt_builder.py:1014,1176,1267`), engine parent (`chat_engine.py:3036,4027,4061`), root walk (`identity.py:165`), final saves | 2–3.5 s per turn | 10–15 s |
| 8 | **`find_by_id` on every UI action** — mark-seen, metadata, schedule, draft, company, browser, voice, orchestration, tools (`chat_manager.py:1779-1818`) | 0.4–0.5 s each | 1.9–2.2 s each |
| 9 | **`POST /api/chats/search`** — `list_chats` per accessible project then scoring (`chat_manager.py:785-937`) | 5–7 s per project | 25–35 s per project |
| 10 | **Traverse/export** — all chats + `find_by_id` per node + O(chats×messages) link scan (`chat_manager.py:2090-2164`) | 6–10 s | 30–50 s |

Runners-up: DSH `sync_all` (up to 1000 × `find_by_id`, `integrations/dsh.py:540-560`)
≈ 7 min; archive/restore double scans; planning reorder N saves
(`planning_manager.py:749-791`); meeting utterances 5–7 s each
(`meets/chat_bridge.py:111-216`); metrics double parse
(`metrics/codx_junior_metrics.py:16-20`).

## 4. Data quality: the store is already dirty

An index must be designed for this reality, not for an ideal store.

| Finding | Count | Consequence today |
|---|---:|---|
| Unparseable files (`Extra data`: complete JSON + stale trailing bytes) | **2** | `list_chats` silently drops them (`chat_manager.py:770-773`); `find_by_id` **raises** on them |
| Duplicate chat IDs across folders (board renames left copies) | **5** | double counting in lists/cost; `find_by_id` returns whichever `rglob` sees first |
| Dot-prefixed chat files (empty `name` → empty slug) | **37** | invisible to shell globs/tools; included by `pathlib` today |
| Chats with zero messages | **88** | cards render with no preview |
| Chats with no stored `file_path` | **242** | granular persists fall back to an extra tree scan (`chat_manager.py:1023-1038`) |
| Files with a stale/absolute `file_path` (project moved) | unknown | fast-path check rejects it and scans again |
| `board.json` documents inside the tree | 143 | correctly filtered today, but any new walker must replicate the rule |
| Chats archived as `.tar.gz` | 149 flags in files | live list must not resurrect them; archive index is separate |

**Recovery is possible:** both corrupt files are a complete JSON document plus a
trailing fragment (4,199 / 39,185 bytes). A stricter reader can `raw_decode` the
prefix, import it, and quarantine the tail instead of losing the task.

## 5. Correctness and multi-user bugs that constrain the design

These are not "nice to have"; a faster store that preserves them is still broken.

1. **The user filter crashes.** `_user_involved_in_chat` reads
   `chat.created_by`, `message.created_by` and `message.user_id`
   (`chat_manager.py:661-687`), none of which exist on the pydantic models
   (`db.py:200-380,426-...`). Verified: `get_recent_chats(user_id=...)` raises
   `AttributeError`. The Follow-up quick access always sends `user_id`
   (`client/src/components/chats/RecentChatsQuickAccess.vue:229-233`), so that
   list fails; `POST /chats/search` with `user_id` fails too.
2. **Search only searches the last message.** `search_chats` calls `list_chats`,
   which truncates `messages` to the last one (`chat_manager.py:1718-1725`)
   *before* `ChatSearcher` scores it (`chat_manager.py:914-931`). Old content is
   unfindable. `SearchFilters` are read by the API but never forwarded
   (`api/chat.py:1767` vs `1790-1799`), so field-scope filtering is dead.
3. **Lost updates on shared files.** No lock protects a chat file. Two
   `add_message` calls in two processes both load, merge and write; the second
   overwrites the first. `save_chat(chat_only=False)` (`PUT /api/chats`) trusts
   the client's whole document and can revert status, metadata and messages.
4. **Drafts are shared and expensive.** One `unsent` message per chat, no owner
   check (`api/chat.py:1129-1255`, `db.py:952-1011`). Two users typing
   overwrite each other; sending deletes the other user's draft. Each debounce
   rewrites the whole file.
5. **Read receipts rewrite the file.** `read_by` is a username list inside the
   shared message; `mark_message_as_seen` rewrites the whole chat
   (`chat_manager.py:2166-2191`). Concurrent receipts lose entries; a 14 MB
   file is rewritten to record one read.
6. **Cost roll-up serializes the project.** `_roll_up_cost_to_root` holds
   `.cost-rollup.lock` across a full-tree `find_by_id` and a full rewrite of the
   root task (`chat_manager.py:1138-1204`). Parallel workers queue behind it.
7. **ACL is not enforced server-side.** `project_ids` from the request are used
   unchecked (`chat_manager.py:826-833`); `request.state.user` is never
   assigned, so the `find_all_user_projects` branch is dead in HTTP. A caller
   can search another project by id.
8. **`GET /chats?file_path=` accepts any path** (`api/chat.py:1669-1680`) — a
   path traversal/read primitive outside the tasks tree.
9. **A read endpoint writes.** `_seal_dead_responses` repairs messages while
   serving `GET /api/chats?id=` (`api/chat.py:130-212`, `chat/turn_store.py:1129-1137`).
10. **The archive is not crash-atomic and can be resurrected.** Archive deletes
    the live file before updating its catalogue (`kanban/archive.py:411-425`);
    a live writer can recreate an archived task; restore can overwrite a newer
    file.
11. **Duplicate/missing IDs and corrupt files** (see §4) make an id-keyed index
    non-trivial; the index needs a deterministic conflict policy and a repair
    command.

## 6. Process model

- **`uvicorn --workers 1` by default** (`set_env.sh:24`, `scripts/run_api.sh`):
  Socket.IO has no shared client manager, so multiple workers would break
  sessions. Single worker does **not** mean single thread: Starlette's threadpool,
  the Socket.IO pool (32), and several background threads write chats.
- **Always at least two API processes**: `api` and `api-background` from the same
  `run_api.sh` (`supervisor.conf`), and some deployments run them as two
  containers sharing only the projects volume. Each has its own in-memory cache.
- **Git is a writer.** 2,038 task files are tracked; `git checkout/pull` rewrites
  them in place, outside every lock. The index must reconcile external changes.
- **No NFS/multi-host support is claimed anywhere.** SQLite WAL is safe on a
  local filesystem; NFS would require the Postgres option or a single-writer
  service.

## 7. What this means for the design

1. A **metadata projection** is tiny (3.9 MB) and answers every list/board/
   planning/calendar query. This is the highest-value, lowest-risk change.
2. **Full-text search needs an index of message text** (31 MB contentless) if we
   want to fix the "last message only" bug. This is optional per project.
3. **Tool output must leave the hot path.** Externalizing it to blobs cuts the
   store by ~46 %, collapses the 14 MB file, and makes writes cheap without
   changing the canonical format.
4. **Per-user state must leave the shared file.** Drafts and read state belong
   in the index (per user), not in a git-tracked task.
5. **The index must be disposable.** Files are truth; the index is rebuilt from
   them, reconciled with git, and tolerated to be stale by seconds.
