# 05 — Implementation status (this branch)

This page records what was actually implemented from the proposal, how to turn it
on/off, how to verify it, and what remains. The design documents
([01](01-baseline.md)–[04](04-roadmap.md)) remain the rationale; this is the
engineering record.

> **Status:** Phase 0 (correctness + O(1) lookups + payload hygiene) and the
> Phase 1 read model are implemented and feature-flagged. Phase 2 (per-user
> drafts/read cursors, blob externalization) is partly implemented; the
> remaining items are listed at the end. No canonical task JSON field was
> removed or renamed; files stay the source of truth.

## Implemented

### Read model

- **`api/codx/junior/chat/chat_index.py`** — `ChatIndexStore`, a stdlib-only
  (SQLite + FTS5) per-project index:
  - `chats` projection (metadata, path, last-message preview, counts, unread
    annotations), `messages` metadata, `participants`, `chat_reads`, `drafts`,
    `search_docs`, contentless `search_fts`;
  - WAL, `busy_timeout`, one connection per thread;
  - `rebuild()` in one write transaction; `upsert_chat()` write-through;
    `delete_chat()`;
  - `list_chats()`, `count_chats()`, `search()`, `find_path()`, `paths()`,
    `signatures()`, `mark_read()`, `unread_count()`, drafts, `health()`;
  - FTS5 unavailable → automatic `LIKE` fallback (`metadata` mode).
- **`api/codx/junior/chat_manager.py`** — integration:
  - lazy `ChatIndexStore` at `<codx_path>/index/chats.db`;
  - background build (`_start_chat_index_build`) with a process-wide guard so
    N requests do not start N builds;
  - write-through from `store_chat` (file first, then index); a rebuild in
    progress defers writes to a dirty set and reconciles afterwards;
  - index-backed `list_chats`, `get_recent_chats`, `search_chats`,
    `find_by_id`, `find_chat_path`, `find_chats`, `chat_count`;
  - index-backed drafts (`get_chat_draft`, `save_chat_draft`,
    `delete_chat_draft`);
  - `mark_message_as_seen` advances the per-user read cursor;
  - archive/restore update/delete the index row;
  - 60 s background reconcile (stat/signature diff) for `git checkout`, editor
    changes and deletions; corrupt files are recovered from their valid JSON
    prefix and marked `parse_error`;
  - a cross-process `build_in_progress` marker (180 s stale timeout) means a
    rebuild in the API process does not block the background worker's writes on
    SQLite; the write is remembered and reconciled after the build;
  - `CODX_JUNIOR_CHAT_INDEX=off` restores the old file-only behaviour.
- **`api/codx/junior/db.py`** — `ToolEvent.output_ref/output_bytes/
  output_sha256/output_truncated`; transient Chat projections
  (`message_count`, `pending_count`, `cover_image`) are never persisted;
  draft helpers become per-user and index-backed when an author/user is known.

### Correctness / multi-user fixes

- `_user_involved_in_chat` no longer reads the non-existent
  `Chat.created_by` / `Message.created_by` / `Message.user_id` (this crashed
  `GET /chats/recent?user_id=` and search); it uses `users`, `assignees`,
  profiles and `message.user`.
- `GET /chats/recent` passes `unread_for`; list/recent/search use the
  authenticated user when available.
- `project_ids` in search are intersected with the authenticated user's
  accessible projects.
- `GET /chats?file_path=` is restricted to the project's tasks tree.
- The duplicate `PUT /chats/mark-seen/{chat_id}/{message_id}` route was removed.
- Drafts are per user (`drafts(chat_id, user)`) and no longer rewrite the chat
  file; legacy `unsent` messages are imported on rebuild.
- Read state is a per-user cursor (`chat_reads.last_read_seq`); legacy
  `read_by` arrays are imported monotonically.

### Payload / write hygiene

- **`api/codx/junior/chat/blobs.py`** — content-addressed, redacted,
  de-duplicated blob store at `<tasks>/.blobs`; `GET /api/chats/blob?ref=...`
  serves the full output; path traversal is rejected; CISO redaction fails
  closed (no unredacted blob).
- `ChatEventBridge` externalizes tool output larger than
  `CODX_JUNIOR_CHAT_TOOL_OUTPUT_INLINE_BYTES` (default 32 KB) into a blob and
  keeps a 4 KB preview + reference in the `ToolEvent`.
- **`api/scripts/migrate_chat_blobs.py`** — dry-run by default, `--apply` with
  backups, for existing files. The reference store dry-run reports **73 files /
  649 events / ~61.8 MB** that could move out of the JSON.
- `.gitignore` and `runtime.DATA_PLANE_ENTRIES` include `index` and `.blobs`
  (the CISO repo check verifies both lists stay in sync).

### API payload / client

- List/recent/search return the projection plus `last_message`,
  `message_count`, `pending_count`, `unread_count`, `process_state`,
  `process_error`, `last_activity_at`, `cover_image`, while keeping
  `messages: [slim last]` for compatibility.
- Client consumers were updated to use the server values when present:
  sprint cards (unread/count/pending), sidebar counts, wall unread, task-card
  cover image, unread badges, and the chat list request (`unread_for`).
- Draft API sends the current user id so GET/PUT/DELETE operate on that user's
  draft.
- The client build (`npm run build-only`) passes.

## Measured on the reference project

Same store as 01 (1,841 chat files / 392.6 MB), index built once, warm cache:

| Operation | Before | After |
|---|---:|---:|
| `list_chats(30 days)` (474 chats) | **2.7 s** / ~50 MB | **68 ms** / **2.4 MB** |
| `get_recent_chats(page=1)` | **0.87 s** | **9 ms** |
| `search_chats("chat")` | **5.4 s**, last message only | **73 ms**, all messages |
| `search_chats("deployment")` | ~5 s | **13 ms** |
| `find_by_id` (full 13 MB chat) | **283 ms** + parse | **~49 ms** (full parse; path lookup < 1 ms) |
| `list_chats(all)` | **5.0 s** | **244 ms** |
| index size | — | **~33 MB** |
| initial build | — | **~8 s** in a background thread |
| list payload | ~50 MB | **2.4 MB** |

Numbers vary with CPU/load; the contrast (tens of ms vs seconds) is stable.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CODX_JUNIOR_CHAT_INDEX` | `on` | `off` = file-only; `shadow` = build/write the index but read files; `on` = reads use the index when ready, files otherwise |
| `CODX_JUNIOR_CHAT_INDEX_SEARCH` | `contentless` | `contentless` (default), `metadata` (no message FTS), `full` reserved |
| `CODX_JUNIOR_CHAT_TOOL_OUTPUT_INLINE_BYTES` | `32768` | Externalize tool output above this size (0 disables) |
| `CODX_JUNIOR_CHAT_TOOL_OUTPUT_PREVIEW_CHARS` | `4000` | Inline preview length |
| `CODX_JUNIOR_CODX_DATA_PATH` | unset | If used, it must be shared by the API and background worker; otherwise the index diverges. The index lives at `<project .codx>/index/chats.db`, next to `tasks/`. |

## Files added / changed

| File | Purpose |
|---|---|
| `api/codx/junior/chat/chat_index.py` | SQLite read model |
| `api/codx/junior/chat/blobs.py` | content-addressed tool-output store |
| `api/codx/junior/chat_manager.py` | index lifecycle, read/write integration, reconcile, drafts |
| `api/codx/junior/chat/chat_event_bridge.py` | tool-output externalization |
| `api/codx/junior/db.py` | ToolEvent blob fields, transient projections, per-user drafts |
| `api/codx/junior/api/chat.py` | summary payloads, file_path guard, filters, auth user, blob/health routes, duplicate route removed |
| `api/codx/junior/runtime.py`, `.gitignore` | data-plane entries |
| `api/scripts/migrate_chat_blobs.py` | one-time blob migration (dry-run default) |
| `client/src/...` | consume server projections; per-user draft calls |
| `api/tests/chat/test_chat_index.py`, `test_blobs.py`, `test_chat_manager_index.py` | unit + integration tests |

## How to verify

```bash
# stdlib tests (no app dependencies)
PYTHONPATH=api python -m unittest api/tests/chat/test_chat_index.py api/tests/chat/test_blobs.py

# app-level integration test (real deps / CI venv)
PYTHONPATH=api python -m pytest api/tests/chat/test_chat_manager_index.py -q

# benchmark / store scan (read-only)
python docs/performance/chat-task-store/bench/chat_store_bench.py --tasks .codx/tasks all

# index health (running API)
curl 'http://localhost:19980/api/chats/index/health'

# optional: inspect what blob migration would do (dry-run, no writes)
PYTHONPATH=api python api/scripts/migrate_chat_blobs.py --tasks .codx/tasks
```

The index is disposable:

```bash
rm -rf <project>/.codx/index        # next read rebuilds it
CODX_JUNIOR_CHAT_INDEX=off ...      # old behaviour, no index
```

## Migration / rollout

1. Deploy with `CODX_JUNIOR_CHAT_INDEX=shadow`; open the project; confirm
   `/api/chats/index/health` reports `ready: true` and a sensible `chats` count.
2. Run the benchmark on a copy and compare list/search results with the old
   endpoint. (The index searches all messages; the file path only searched the
   last message, so *more* results are expected, not a regression.)
3. Switch to `on` per project. First read after `index/` is deleted falls back to
   files and starts a background build (~8 s / 2k chats; ~30 s / 10k).
4. If desired, run `migrate_chat_blobs.py --apply` off-peak; it reports bytes
   saved and keeps backups.
5. To roll back: set `off` and delete `index/`. Task files are untouched by the
   index. Blob migration is reversible from the `.blobs-migration-backup/`
   copies; without it the inline preview remains readable and the full text can
   still be fetched by `output_ref`.

## Remaining / next

- **Read receipts still write the task file.** The cursor is already in the
  index, but `mark_message_as_seen` keeps the legacy `read_by` write for
  compatibility. Removing it (and hydrating `read_by` from the cursor on open)
  is the next write-amplification win.
- **Blob migration of old tasks is opt-in.** New tool output is externalized;
  existing 14 MB tasks need `migrate_chat_blobs.py --apply` or a natural save.
- **`full` FTS mode** is reserved; it currently behaves like `contentless` and
  returns previews rather than highlighted snippets.
- **Reconcile is a 60 s stat pass**, not a filesystem watcher per project. It
  was chosen for simplicity and multi-process safety; a watcher can be layered
  on later.
- **`list_chats(all)`** still builds 1,840 `Chat` objects (~244 ms). It is not
  the default list (30 days) but can be reduced with a lighter projection if a
  surface needs all rows.
- **Multi-host/NFS** remains unsupported for the SQLite index; use Postgres if
  the deployment ever requires it (see 02/O5).
- **Whole-document client saves** still exist; the `rev`/CAS work from Phase 2
  is not implemented yet. Per-chat write locks are also pending.
