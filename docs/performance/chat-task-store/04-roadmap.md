# 04 — Roadmap, migration, tests and rollout

> **Implementation:** the core of this design is implemented; see
> [05-implementation.md](05-implementation.md) for the live behaviour, flags and
> remaining items.

The work is phased so that each step is independently useful and reversible.
Phase 0 delivers immediate relief and fixes correctness bugs; Phase 1 delivers
the structural speed-up; Phase 2 removes write amplification and the remaining
multi-user hazards. Phase 3 is optional and only for scale this deployment does
not have today.

## Phase 0 — Stop the bleeding (3–7 days)

**Goal:** make today's system usable at 2k tasks and fix the bugs that make a
faster store still incorrect. No schema change, no new dependency.

| # | Task | Files / areas | Acceptance |
|---|---|---|---|
| P0.1 | Fix `_user_involved_in_chat` (`created_by`, `user_id`) or replace it with real fields; pass `unread_for` from the session | `chat_manager.py:661-687`, `api/chat.py:627-711`, `1718-1722` | `GET /chats/recent?user_id=` and search with `user_id` return data, no `AttributeError` |
| P0.2 | Restrict `GET /chats?file_path=` to `tasks/`; remove the duplicate `mark-seen` route; populate `request.state.user`; intersect `project_ids` with ACL | `api/chat.py:1599-1730`, `2123/2181`, `chat_manager.py:826-846` | traversal test fails safely; ACL test passes |
| P0.3 | O(1) id lookup: persist `chat_id → path` map (`.codx/tasks/.chat_paths.json`, `flock`-protected, mtime-invalidated) and use it in `find_by_id` / `find_chat_path`; stop calling `find_by_id` from `save_chat` when the path is known | `chat_manager.py:1503-1574`, `1779-1845` | `find_by_id` < 5 ms at 2k; save fixed cost drops from ~0.35 s |
| P0.4 | Metadata-only list: build a **slim** Chat (no tool events, no full validation); keep a ≤ 280-char preview; `messages:[lastSlim]` | `chat_manager.py:710-779`, `1674-1725`, `chat_searcher.py` | list 30 d < 1 s; payload < 10 MB |
| P0.5 | Replace `_CHAT_INFO_CACHE` deep-copied pydantic models with a byte-bounded LRU of immutable summary rows keyed by `(content_hash, rev)` | `chat_manager.py:158-226` | RSS after list < 30 MB; warm list < 150 ms |
| P0.6 | One `scandir` walk instead of two `rglob`s; cache the inventory for 500 ms | `chat_manager.py:508-554` | `chat_paths` < 100 ms |
| P0.7 | Externalize oversized `tool_events[].output`, `base64_data` attachments and huge message content to `tasks/.blobs/<sha256>`, keeping the inline preview; forward-only on write + one-time batch tool | `chat_event_bridge.py:347-377`, `db.py` models, `store_chat` | store ≤ ~220 MB; biggest file < 2 MB; no information loss in `output_ref` |
| P0.8 | Per-chat `flock` around load → merge → write; `rev` column/stamp | `chat_manager.py`, `utils/file_lock.py` | two-process `add_message` test loses nothing |
| P0.9 | Incremental CISO redaction (only new/changed strings) and batched `fsync` | `security/ciso/payload.py`, `store_chat` | save 13 MB file < 400 ms |
| P0.10 | Move drafts to `.codx/tmp/drafts/<user>/<chat>.json` (or the Phase 1 DB if it lands first); stop rewriting the chat per keystroke; per-user | `db.py:952-1011`, `api/chat.py:1129-1255` | typing does not write a chat file; two users keep separate drafts |

**Phase 0 exit criteria:** list 30 d < 1 s, list all < 2.5 s, `find_by_id` < 5 ms,
small save < 100 ms, no known correctness bug from
[01-baseline.md §5](01-baseline.md) remains open, and a two-process stress test
does not lose messages.

## Phase 1 — Chat index (1.5–3 weeks)

**Goal:** every list/filter/search/unread query is served by SQLite; the UI never
waits for a full-store parse.

| # | Task | Details |
|---|---|---|
| P1.1 | `ChatIndexStore` | New module `api/codx/junior/chat/chat_index.py` modelled on `atlas/store.py`: connection lifecycle, schema, WAL/busy_timeout, thread-local connections, migrations, `meta`. |
| P1.2 | Schema v1 | `chats`, `messages`, `chat_reads`, `drafts`, `participants`, `search_docs`, `search_fts`, indexes ([03-target-architecture.md §3](03-target-architecture.md)). |
| P1.3 | Projection builder | `Chat → row` and `row → Chat` (slim). Shared by writer and rebuild; one source of truth for the projection. |
| P1.4 | Writer hooks | `save_chat`, `add_message`, `update_message`, `remove_message`, `update_chat_metadata`, `delete_chat`, archive/restore, cost roll-up. File first, index second, one transaction. |
| P1.5 | Read hooks | `list_chats`, `get_recent_chats`, `search_chats`, `find_by_id`, `find_chat_path`, `task_board.load_tasks`, `kanban/search.build_index`, `planning_manager`, `management_manager`, `metrics`, `calendar` projection. |
| P1.6 | FTS modes | `metadata` / `contentless` (default) / `full`; index assistant messages only when finalized; `SearchFilters` forwarded; `snippet` for `full`. |
| P1.7 | Per-user state | `chat_reads` cursor + `drafts`; import legacy `read_by`/`unsent` on rebuild; `mark-seen` and draft endpoints no longer write chat files. |
| P1.8 | Rebuild + reconcile | Startup full/quick check, rebuild in one write transaction (readers keep their snapshot; writers wait), shadow DB + atomic swap only for schema migrations, `watchfiles` debounce, 60 s periodic inventory, git checkout handling, corrupt/duplicate policy, quarantine. |
| P1.9 | Feature flag + shadow mode | `CODX_JUNIOR_CHAT_INDEX=off|shadow|on`; shadow compares index vs file results and logs mismatches; per-project override. |
| P1.10 | Health + metrics | `GET /api/chats/index/health`, Prometheus-style counters, rebuild progress events. |
| P1.11 | API compatibility | Keep bare array/envelopes/caps/ordering; add `last_message`, `message_count`, `unread_count`, `process_state`, `cover_image`; update the five client components that read `messages[last]` (cover/count/preview). |
| P1.12 | Tests | See the matrix below; include a fixture store with 2 corrupt files, 5 duplicates, 37 dot files, archived tasks and a ≥ 2 MB chat. |

**Phase 1 exit criteria (SLOs at 2k chats):**

| Metric | Target |
|---|---:|
| `GET /api/chats` (30 d) server p95 | **< 100 ms** |
| `GET /api/chats/recent` p95 | < 50 ms |
| `POST /api/chats/search` p95 | **< 150 ms** |
| `find_by_id` p95 | **< 2 ms** |
| index overhead per write p95 | < 10 ms |
| list payload | **< 3 MB** |
| full rebuild | **< 10 s** |
| index freshness after local write | < 100 ms |
| freshness after external/git change | < 5 s (watcher) / 60 s (reconcile) |
| RSS after list | < 30 MB per worker |

Shadow mode must show **zero** result mismatches on the project for a full day
before `on`.

## Phase 2 — Write path and multi-user hardening (1–2 weeks)

| # | Task | Details |
|---|---|---|
| P2.1 | Blob store completion (if not fully in P0.7) | Blob GC, retention settings, `output_ref` in tool events/attachments, batch migration tool with `--dry-run`/backup, rollback (`blobs inline`). |
| P2.2 | Field-level patches | `update_chat_metadata` writes only changed JSON fields (or a patch merge) + index columns; no whole-file serialization for a priority change. |
| P2.3 | Optimistic concurrency | `rev` on every row; whole-document `PUT/POST /api/chats` sends `rev`; merge/409 on conflict; stop trusting client documents. |
| P2.4 | Atomic cost roll-up | `UPDATE cost = cost + ?`; remove `.cost-rollup.lock`; debounce root file materialization; agent reads the budget from the index if the file lags. |
| P2.5 | Delta message responses | `POST/PUT /api/chats/message` can return only the changed message + summary; keep whole-chat as default for compatibility. |
| P2.6 | Socket deltas + rooms | Emit the projection row on change; target project/user/chat rooms; per-project `seq`; no more whole-message frames with `tool_events`. |
| P2.7 | ACL middleware | `request.state.user` populated; `find_all_user_projects` used for cross-project search; tests for cross-tenant access. |
| P2.8 | Repair tooling | `chats index rebuild`, `chats index repair --duplicates`, `chats files repair --corrupt`, `chats blobs gc`, all with dry-run. |

**Phase 2 exit criteria:** message/tool-event persist < 80 ms for a ≤ 1 MB file;
no lost update in the two-process test matrix; every per-user field is scoped;
socket payload per event < 50 KB; store ≤ ~200 MB on the reference project.

## Phase 3 — Optional scale (only if needed)

- **O4 split storage** (`chat.json` + `messages/*` + `blobs/*`) if a single chat
  can still be too large to rewrite after blobs. Requires a format version and a
  compatibility reader. 4–8 weeks.
- **Postgres index** if the deployment becomes multi-host or > ~50k tasks.
  Keep the SQL schema portable (no SQLite-only constructs beyond FTS; use
  `tsvector`/`pg_trgm` in a second implementation).
- **Semantic search** on top of the index (LanceDB): embed chat summaries and
  offer "find similar chats" as a separate endpoint; never in the critical list
  path.

## Migration plan

1. **Index: additive, no file migration.** Build it from the existing files in
   the background. If it is wrong, delete it. `index/` is gitignored and added
   to `DATA_PLANE_ENTRIES`.
2. **Per-user state: no file rewrite.** Import `read_by` (max seq per user) and
   `unsent` (per author) into the DB on rebuild; stop writing them back. Old
   readers keep seeing the legacy fields until the next natural save.
3. **Blobs: forward-only first.** New/updated tool output is externalized; old
   files are migrated by a batch tool (`--apply`) with a backup and a dry-run
   report. Rollback can inline blobs from `output_ref`.
4. **Project move/rename:** the index is keyed by `project_id`; update `path`,
   `owner_project_id` and references in one transaction. Do not rebuild from
   scratch.
5. **Archive:** keep the existing `.archive` payload/catalogue; the index row is
   marked `archived=1`. Restore flips it back. No live writer may resurrect an
   archived id.
6. **Rollback:** `CODX_JUNIOR_CHAT_INDEX=off` + delete `index/` returns the
   system to file-only behaviour. Blobs remain readable (preview inline); run
   `chats blobs inline --all` if full old behaviour is required.

## Test matrix (minimum before enabling `on`)

### A. Correctness / projection parity
- `Chat → row → Chat` preserves every field in the projection contract; unknown
  fields are not lost from files.
- Slim last message equals `_task_latest_message` (280 chars, markdown rules).
- `message_count`, `pending_count`, `unread_count`, `process_state`,
  `cover_image` match the current helpers.
- Filters (board, column, status, mode, type, date range, user, archived) return
  the same sets as the file implementation on the fixture store.
- Search: empty query → list shape; non-empty → scored shape; `filters` honored;
  `snippet` present in `full` mode; pagination/caps unchanged.

### B. Multi-user / ACL
- Two users have separate drafts; sending as A deletes only A's draft.
- `mark_seen` for A does not change B's unread count; read position is monotonic.
- Cross-project search with a forged `project_ids` returns only accessible
  projects.
- `GET /chats?file_path=../../etc/passwd` is rejected.
- Socket events are scoped to authorized rooms; `chat-event` carries no
  `read_by` of other users.

### C. Concurrency (two real processes)
- Two `add_message` to the same chat: both messages survive; order is stable.
- `add_message` concurrent with `save_chat(chat_only=False)`: no message lost,
  no status revert.
- `mark_seen` concurrent with a streamed `update_message`: the message content is
  not reverted by the read cursor.
- Cost roll-up from two children: root cost equals the sum (atomic increment).
- Delete vs save: the tombstone wins; no resurrection.
- Archive vs save: archived task is not recreated live.

### D. Rebuild / reconciliation
- Missing DB → rebuild; results identical.
- Schema version bump → rebuild; old DB kept aside.
- Corrupt DB → rebuild.
- External edit / `git checkout` → reindexed; unknown files adopted; deleted
  files removed.
- 2 corrupt files are recovered/quarantined, not silently dropped.
- 5 duplicate ids resolve deterministically; repair tool reports them.
- Dot-prefixed files, zero-message chats, archived tasks, `owner_project_id`
  ≠ `project_id`, project path move.
- Rebuild interrupted → old DB still serves; retry succeeds.
- Rebuild in one write transaction; readers never see a partial index. Schema
  changes may use a shadow DB + `os.replace`; processes reopen on build-id change.

### E. Crash / failure
- Kill between file write and index commit → reconcile repairs.
- Kill during blob write → no partial blob referenced.
- Disk full / read-only DB → file writes still succeed; degraded mode reported.
- SQLite busy under load → retries, no 500s.

### F. Performance / memory (CI gate)
- Synthetic corpus: 2k and 10k chats, 50 messages each, one 10 MB tool output.
- Assert list/search/find/save SLOs, index size, rebuild time, RSS.
- Run the benchmark in [bench/chat_store_bench.py](bench/chat_store_bench.py) on
  every PR that touches chat storage; fail on > 20 % regression.

## Rollout

1. **Dev/CI:** `shadow` mode on the fixture store; compare old vs new for every
   read endpoint; mismatch = test failure.
2. **Canary:** one low-risk project with `shadow` for 24 h, then `on`. Watch list
   latency, search quality, rebuild time, memory, error rate, parse errors.
3. **Default:** `on` for all projects after the canary; keep `off` available.
4. **Background worker:** same flag from the shared settings; verify both
   processes see the same DB and that no `CODX_JUNIOR_CODX_DATA_PATH` divergence
   splits the index.
5. **Rollback drill:** flip `off`, delete `index/`, confirm list/search still
   work (slower) and no file was modified by the index.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| FTS result semantics differ from substring search | Users can't find old text the same way | Prefix/word query parser, trigram/LIKE fallback for short queries, shadow-mode comparison, explicit release note |
| Index drifts from files after git/editor changes | Stale list/search | Watcher + 60 s reconcile + `content_hash`, health endpoint, rebuild command |
| SQLite on NFS/containers | Corruption/lock errors | Detect filesystem/setting; disable index; document Postgres for multi-host |
| Two containers don't share `index/` | Each has a partial index | Put index on the shared project volume; startup check for divergence (project_id/build_id); health warning |
| Blob externalization loses output on clone | Old tool output unavailable | Preview inline + `output_ref`; document; optional `blobs inline`; retention policy |
| Whole-document clients still overwrite | Lost updates | Per-chat lock + `rev` + field-level patches; reject/merge stale writes |
| Slim last message breaks a client component | Missing cover/count/preview | Add `cover_image`, `message_count`; update the five components in the same release; keep `messages:[lastSlim]` |
| Migration effort underestimated | Slips | Phase 0 alone delivers usable speed; Phase 1 can be split by endpoint (list first, search second) |
| Memory of per-user read state | 50 users × 9.5k messages | Cursor model (`chat_reads`) is O(users × chats), not per message; optional per-message overrides only if needed |
| Test suite is slow/flaky on FS | Regressions slip | Fixture store checked in as a tarball; deterministic clock; two-process tests in CI with a temp dir |

## Definition of done (programme)

- All Phase 1 SLOs met on the reference project and on the synthetic 10k corpus.
- No list/search endpoint parses chat files (verified by a test that makes files
  unreadable and still serves the list from the index).
- Two-process concurrency and crash/reconcile tests pass in CI.
- Multi-user tests pass: separate drafts, read cursors, ACL, socket rooms.
- The store can be rebuilt from files in < 10 s (2k) / < 60 s (10k), and the
  health endpoint reports it.
- The reference project's store is ≤ ~220 MB with no task information lost.
- A rollback drill (flag off + delete index) is documented and tested.

## Open questions for review

1. **Search semantics:** is word/prefix FTS acceptable, or must substring
   matching be preserved exactly? (Recommendation: word/prefix + trigram
   fallback for short queries; document the change.)
2. **Blob policy:** are tool outputs allowed to be local-only (gitignored), or
   must they survive a clone? (Recommendation: local-only, with preview and
   `output_ref`; retention configurable.)
3. **Read state:** is a monotonic per-chat cursor enough, or do we need
   arbitrary per-message `read_by` (e.g. compliance)? (Cursor is O(users × chats)
   and matches the UI; per-message is O(users × messages).)
4. **Multi-host timeline:** if a shared-volume deployment is planned soon, the
   Postgres variant should be designed in parallel; otherwise keep SQLite.
5. **Index placement:** `<codx>/index/` (shared with tasks) is the default; if
   `CODX_JUNIOR_CODX_DATA_PATH` is used, require an explicit shared
   `CODX_JUNIOR_CHAT_INDEX_PATH` and a startup consistency check.
