# 02 — Options, from "tune it" to "replace it"

The options are ordered by structural ambition, not by preference. Each one is
evaluated against the same criteria so the trade-off is explicit. All estimates
assume the current store shape (1.8k chats / 392 MB) and scale linearly to
10k chats / ~2.1 GB unless stated.

## 1. Evaluation criteria

| Criterion | Why it matters |
|---|---|
| **List/filter latency** | Every project switch, board, task table and planning view does it. |
| **Search latency and quality** | Today it is 5 s and only searches the last message. |
| **Write latency** | Every tool event and 5 s of streaming rewrites a chat. |
| **Multi-process safety** | API + background worker; sometimes two containers. |
| **Multi-user / ACL** | Per-user drafts, unread, read receipts; project permissions. |
| **LLM / git friendliness** | Files must stay readable, editable, diffable and rebuildable. |
| **Recovery / rebuild** | The index must be derivable from files, never a second truth. |
| **Operational cost** | No new service for the default install; backup/restore simple. |
| **Migration effort / risk** | 2,038 tracked files, many call sites; rollback must exist. |
| **Disk overhead** | Duplication is acceptable only if bounded and rebuildable. |

## 2. O0 — Tune the current code (no new store)

**What changes**

- Replace `find_by_id` with an O(1) path lookup: keep a `chat_id → path` map in
  memory + a small persisted map file, updated on save/delete; derive the path
  from `chat.file_path` when valid. `save_chat` stops scanning
  (`chat_manager.py:1539`).
- Metadata-only read for lists: parse JSON, build the summary without validating
  every `ToolEvent`; keep only the last-message preview fields (not the whole
  message). Avoid `Chat(**full_document)` for list/search.
- Replace `_CHAT_INFO_CACHE` deep-copied pydantic models with an immutable
  summary row (~1–2 KB) and a bounded LRU keyed by `(content_hash, rev)`.
- Use one `os.scandir` walk (not two `rglob`s); cache the directory listing for
  a few hundred ms.
- Coalesce writes: one persist per tool batch / debounce, not two per tool call;
  do not rewrite a chat for a draft or a read receipt (move both out of the
  chat file).
- Incremental CISO redaction: scan only the new/changed strings, not the whole
  13 MB document. Batch `fsync` (or rely on `os.replace` + periodic fsync).
- Externalize `tool_events[].output` larger than a threshold to blobs with an
  inline preview (this is also Phase 2 of the recommendation).

**Measured/expected**

| | today | after O0 |
|---|---:|---:|
| list 30 days | 2.7 s | **0.6–1.0 s** |
| list all | 5.0 s | **1.5–2.5 s** |
| search | 5.4 s | **2.5–3.5 s** |
| `find_by_id` | 0.28 s | **< 2 ms** |
| save small / big | 0.35 s / 1.5 s | **0.05–0.3 s / 0.1–0.4 s** |
| list payload | ~50 MB | ~5–10 MB (if tool output stripped) |

**Pros:** no new infrastructure, no migration, immediate relief, low conceptual
risk. **Cons:** still O(N) for list/search; still one file read per chat; cache
coherence across processes is hand-rolled; doesn't fix multi-user correctness or
the 10k-file ceiling. **Effort:** 3–7 days. **When to choose:** as the first
step regardless of the rest (it removes the worst fixed costs and bugs), not as
the final architecture.

## 3. O1 — Per-chat summary sidecars

**What changes:** write a small `<slug>.<id>.summary.json` next to every chat on
save (metadata + last-message preview + counts + per-user unread). Lists read
the summaries; full files are only opened on demand. Rebuild by scanning the
tree once. Deletes/renames keep the pair in sync.

**Pros:** no shared index file, no DB; per-file atomic writes; trivial rebuild;
works with existing file tooling. **Cons:** doubles inode count (2×2k–20k files);
listing is still O(N) small-file reads (0.1–0.5 s at 2k, 0.5–1.5 s at 10k);
search still needs full content (summaries only hold a preview) or an extra
pass; duplicates/renames are a new consistency surface; git noise unless
gitignored. **Effort:** 1–2 weeks. **Verdict:** a reasonable **interim** if a DB
is politically impossible; not the target.

## 4. O2 — Project-level JSON catalogue

**What changes:** one `tasks/chats.index.json` (or one per board) holding a
summary row per chat. Every write updates the catalogue under an `fcntl` lock
(same pattern as `KanbanStore`); every read is one file parse. Rebuild by
scanning files.

**Pros:** very fast list (a 2–10 MB JSON parses in 10–50 ms); no DB dependency;
easy to inspect/diff. **Cons:** every write rewrites the whole catalogue
(2–10 MB) and serializes all writers across processes; a crash between chat and
catalogue leaves drift; full-text search still requires scanning the chats or a
second content file (122 MB of content would make the catalogue huge); merge
conflicts on concurrent metadata updates. **Expected:** list 20–50 ms at 2k,
100–250 ms at 10k; search unchanged (~2–5 s). **Effort:** ~1 week.
**Verdict:** solves only the list half, and moves the contention problem from
"many files" to "one file". Rejected as the primary design.

## 5. O3 — SQLite sidecar index (recommended)

**What changes:** a per-project `<codx>/index/chats.db` with:

- `chats` — one row per chat with the projection contract (~3.9 MB for 1,841
  chats, including a 280-char last-message preview);
- `messages` — per-message metadata and preview (for unread/last/counts), not
  the tool payload;
- `message_fts` / `chat_fts` — FTS5 indexes over message content/think and
  chat name/description/history (contentless or external-content; see
  [03-target-architecture.md](03-target-architecture.md));
- `message_reads` or a per-user cursor, `drafts`, `participants` — per-user
  state;
- `meta` — schema version, build id, last reconcile.

Reads never open a chat file. Writes update the file (truth) **and** the index in
the same request. If the index is missing/corrupt/stale, it is rebuilt from the
files; if an index write fails, reads fall back to the file for that request.

**Measured on the real store**

| | value |
|---|---|
| metadata index | **3.9 MB**, build 0.58 s (+2.05 s JSON parse) |
| metadata + contentless FTS | **30.8 MB**, build 3.63 s |
| total recommended index | **~35 MB (9 % of the 392 MB store)** |
| list / board / count queries | **0.09 / 0.24 / 0.02 ms** |
| search over all messages | **4.96 ms** (vs 5.4 s today) |
| `find_by_id` | **< 1 ms** (PK lookup) |
| full rebuild at 2k chats | **~6 s** |
| full rebuild at 10k chats (est.) | **~30 s** (background) |

**Pros:** solves list, filter, sort, pagination, search, unread, counts and ACL
queries at once; multi-process safe (WAL, `busy_timeout`); no new service;
already proven in this codebase by `atlas/store.py` (same SQLite+FTS5 pattern);
index is disposable/rebuildable; tiny disk overhead; transactions give atomic
per-user state and optimistic concurrency. **Cons:** adds a schema and a
reconciliation surface; SQLite WAL requires a local filesystem (no NFS); FTS
semantics differ from the current substring `in` (`chat_searcher.py`) and must
be tested; index freshness is eventual for external (git) changes; the DB must
be excluded from git/backups or rebuilt there. **Effort:** 1.5–3 weeks
including tests and feature flag. **Verdict:** **recommended core.**

### 5.1 Why not SQLite as the primary store?

Because the product's contract is "the information stays in files". A DB-primary
model would break LLM file tools, git review, hand edits and the archive. The
index is a **read model**, not a second truth; deleting it costs one rebuild.

## 6. O4 — Split chat storage (+ O3)

**What changes:** replace `<slug>.<id>.json` with a directory:

```
<slug>.<id>/
  chat.json              # metadata, compact, LLM/git friendly
  messages/0001-<doc_id>.json ...   # one message per file (or messages.jsonl)
  blobs/<sha256>         # large tool outputs / attachments
```

Writes become tiny (one message file), open loads metadata + only the messages
needed, and git diffs are per message. O3 is still required for list/search.

**Pros:** constant write cost even for 10k-message chats; no full-file rewrite;
bounded file size; better LLM ergonomics (read the messages you need); a single
message can be edited without touching the rest. **Cons:** 5–10× the inode count
(10k–20k files per project at 2k tasks); every path resolver, export, archive,
DSH sync, git validator, tool and test that assumes a single JSON must change;
multi-file atomicity (metadata + message + index) is harder; `git mv` migration
for 2k files; more failure modes. **Expected:** writes < 10 ms at any size;
list/search same as O3. **Effort:** 4–8 weeks. **Verdict:** **defer**; only if,
after O0+O3, a chat is still too large to rewrite (e.g. multi-MB content) or
write latency remains above the target.

## 7. O5 — Server RDBMS (Postgres) or a shared index service

**What changes:** move the index (not necessarily the files) to Postgres with
proper indexes, `pg_trgm`/`tsvector`, row-level security and multi-writer
transactions.

**Pros:** horizontal scale, multi-host, strong ACL/RLS, centralized backup,
mature observability. **Cons:** a new service to deploy and operate for every
installation (against the "no service" default), Docker Compose complexity,
network latency on the write path, harder offline/demo mode; the files still
need reconciliation. **Effort:** 8+ weeks + ops. **When to choose:** only when
the deployment is multi-host or > ~50k tasks / many tenants. **Verdict:**
**not now**; keep the schema portable so a later migration is mechanical.

### 7.1 Embedded KV instead of SQLite (LMDB, RocksDB)

Fast point lookups, but no SQL, no FTS, no transactions across tables; you would
write and maintain the query layer and secondary indexes yourself. Not worth it
over SQLite for this workload. **Verdict:** rejected.

## 8. O6 — External search engine (Meilisearch / Elasticsearch / Tantivy)

**What changes:** ship message text to a search service; keep metadata in files
or O3.

**Pros:** excellent relevance, typo tolerance, facets, highlighting.
**Cons:** a new service + sync pipeline + failure mode; duplicates 122 MB of
content; overkill for 2k–50k chats; the product already ships an embedded
vector DB and SQLite. **Effort:** 2–4 weeks + ops. **Verdict:** not for the core;
could be an optional connector later if search quality becomes a product
differentiator.

## 9. O7 — Vector DB / LanceDB (semantic search)

LanceDB is already a dependency for knowledge/RAG (`knowledge_backend.py`). It is
good at "find chats semantically similar to this", not at list/filter/sort/
transactional CRUD. **Verdict:** **complement**, not a replacement: layer an
optional semantic search on top of the O3 index (chat summaries + embeddings)
after the core is fast.

## 10. Comparison matrix

Legend: ✅ good · ◐ acceptable · ❌ poor. Latencies at 2k / 10k chats.

| Option | List | Search | Write | Multi-proc | Multi-user/ACL | LLM/git | Rebuild | Disk | Effort | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| **O0** tune current | ◐ 0.6–1 s / 5–8 s | ❌ 2.5–3.5 s | ◐ 0.1–0.4 s | ◐ (fixable) | ❌ (bugs remain) | ✅ | ✅ none | ✅ 0 | 3–7 d | ◐ |
| **O1** sidecars | ◐ 0.2–0.5 s / 1–2 s | ❌ 1–3 s | ✅ small | ✅ per file | ◐ | ◐ extra files | ✅ trivial | ◐ 2× files | 1–2 w | ◐ |
| **O2** JSON catalogue | ✅ 20–50 ms / 100–250 ms | ❌ 2–5 s | ◐ lock contention | ◐ | ◐ | ✅ readable | ◐ | ✅ small | 1 w | ◐ |
| **O3** SQLite index | ✅ **< 1 ms / < 3 ms** | ✅ **5–20 ms** | ✅ +2–10 ms | ✅ WAL | ✅ tables/ACL | ✅ files untouched | ✅ 6 s / 30 s | ✅ 35 MB (9 %) | 1.5–3 w | ◐ |
| **O4** split + O3 | ✅ same as O3 | ✅ same | ✅ **< 10 ms any size** | ✅ | ✅ | ◐ many files | ✅ per file | ✅ | 4–8 w | ❌ high |
| **O5** Postgres index | ✅ < 5 ms | ✅ 10–50 ms | ◐ network | ✅ multi-host | ✅ RLS | ✅ files untouched | ✅ | ✅ | 8+ w + ops | ◐ ops |
| **O6** external search | ✅ | ✅ best | ◐ sync | ✅ | ◐ | ✅ | ✅ | ❌ +service | 2–4 w + ops | ◐ |
| **O7** LanceDB | ❌ (not its job) | ◐ semantic only | ❌ | ◐ | ◐ | ✅ | ◐ | ◐ | 2–3 w | ◐ |

## 11. Recommendation

**Adopt O3 as the core, preceded by O0 and followed by the Phase 2 payload and
multi-user work (blobs, per-user state, locks/`rev`) — not by the full O4 split
storage. The canonical file format stays unchanged.**

Reasons:

1. It is the **only single change** that fixes list, filter, search, unread,
   counts, ACL filtering and `find_by_id` at once, with measured headroom at 10k.
2. It preserves the product constraint: files remain the source of truth; the
   index is disposable and rebuildable in seconds.
3. It fits the existing architecture: one embedded DB per project, exactly like
   `atlas/store.py`, no new service, works in the demo/offline image.
4. It is **small**: 3.9 MB for the metadata that drives every list and card surface; +31 MB
   if we want correct full-history search.
5. It makes the multi-user fixes possible instead of harder: per-user tables,
   transactions, optimistic `rev`, ACL at query time.
6. O0 is still required: without it, writes remain slow and the cache remains a
   memory hazard; O0 also delivers immediate relief while O3 is built.
7. O4 and O5 are escape hatches for sizes this product does not have today.
   Designing them now would multiply the migration cost for no current benefit.

### What "done" looks like (target SLOs)

| Operation | p95 target at 2k chats | p95 at 10k chats |
|---|---:|---:|
| `GET /api/chats` (30 days) | **< 100 ms** (server) | < 200 ms |
| `GET /api/chats/recent` | < 50 ms | < 100 ms |
| `POST /api/chats/search` | **< 150 ms** | < 300 ms |
| `GET /api/chats?id=` (≤ 1 MB chat) | < 100 ms | < 150 ms |
| `find_by_id` | **< 2 ms** | < 3 ms |
| message/tool-event persist (≤ 1 MB file) | **< 80 ms** | < 80 ms |
| index overhead per write | < 10 ms | < 10 ms |
| index freshness after local write | < 100 ms | < 100 ms |
| freshness after external/git change | < 5 s (watcher), 60 s (reconcile) | same |
| rebuild | < 10 s | < 60 s |
| list payload | **~1–3 MB** | ~5–10 MB |
