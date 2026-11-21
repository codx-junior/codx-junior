"""
Per-project SQLite read model for chats/tasks.

The canonical artifact is still the JSON file under
``<codx_path>/tasks/<board>/<column>/<slug>.<id>.json`` (git-tracked, editable by
humans and LLMs). This module only *derives* a small, disposable index from
those files so list/filter/search/unread queries never parse the whole store.

Design constraints
------------------
* **Stdlib only** (``sqlite3`` + FTS5). It must not import pydantic or the rest
  of codx-junior: the index has to be testable and usable from any context.
* **Files are truth.** Deleting the database only costs a rebuild. Every row is
  derived from a file or from explicit user-local state (drafts, read position).
* **Multi-process safe.** WAL + ``busy_timeout``; one connection per thread.
  The API process and the background worker can write the same database.
* **Small.** The metadata projection is ~4 MB for 1.8k chats (vs 392 MB of
  files); the contentless FTS adds ~30 MB for 9.5k messages.

Search modes
------------
``metadata``
    No FTS. Search falls back to ``LIKE`` over name/description/last message.
``contentless`` (default)
    FTS5 contentless table over chat name/description/history/files and over
    message content/think. No copy of the conversation text; superseded rows
    are marked ``valid = 0`` and compacted by a rebuild.
``full``
    Same as ``contentless`` but stores the searchable text in
    ``search_source`` so ``snippet()`` can be produced. Opt-in for projects
    with disk budget.

The index is intentionally *eventually consistent* for external changes: local
writes are write-through, while a git checkout/editor change is picked up by the
reconciler in :class:`codx.junior.chat_manager.ChatManager`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_SEARCH_MODE = "contentless"
#: Meta key used to tell OTHER processes that a full rebuild owns the index.
BUILD_MARKER = "build_in_progress"
BUILD_MARKER_MAX_AGE = 180.0
SEARCH_MODES = ("metadata", "contentless", "full")
PREVIEW_CHARS = 280

#: Error states used by the task table, same vocabulary as ChatManager.
_PROCESS_ERROR_STATUSES = {
    "failed", "error", "stalled", "budget_exhausted", "budget-exhausted",
    "aborted", "timeout", "crashed",
}
_PROCESS_STOPPED_STATUSES = {
    "stopped", "cancelled", "canceled", "paused", "halted", "interrupted",
}
_PROCESS_RUNNING_STATUSES = {
    "queued", "running", "starting", "waiting_input", "waiting-input", "in_progress",
}

#: Searchable chat-level fields and their relative weight.
CHAT_SEARCH_FIELDS: Dict[str, float] = {
    "name": 3.0,
    "description": 2.0,
    "history": 2.0,
    "file_list": 1.0,
    "users": 1.0,
    "tags": 1.0,
    "status": 1.0,
    "mode": 0.5,
    "llm_model": 0.5,
}
MESSAGE_SEARCH_FIELDS: Dict[str, float] = {"content": 2.5, "think": 1.5}
SEARCH_WEIGHTS: Dict[str, float] = {**CHAT_SEARCH_FIELDS, **MESSAGE_SEARCH_FIELDS}

#: Projection columns stored in ``chats``. Kept in one place so the INSERT and
#: the row -> dict conversion cannot drift.
CHAT_COLUMNS: Tuple[str, ...] = (
    "id", "doc_id", "path", "file_mtime", "file_size", "sig", "content_hash",
    "name", "description",
    "board", "board_folder", "column_name", "column_folder",
    "status", "mode", "parent_id", "root_id",
    "project_id", "owner_project_id",
    "created_at", "updated_at", "last_activity_at",
    "chat_index", "sort_index", "pinned", "is_template",
    "priority", "due_date", "wake_at",
    "scheduled_start", "scheduled_end", "all_day", "calendar_ids",
    "progress", "tags", "profiles", "users", "assignees", "sprints", "file_list",
    "cost", "estimation_cost", "run_id", "run_status",
    "harness", "harness_status", "harness_instance_id", "harness_task_id",
    "external_source", "external_status",
    "archived", "archived_at", "archived_by",
    "message_count", "pending_count",
    "last_message_at", "last_role", "last_user", "last_content",
    "last_done", "last_error", "cover_image",
    "parse_error", "index_rev", "indexed_at",
)

_TEXT_COLUMNS = {
    "id", "doc_id", "path", "sig", "content_hash", "name", "description",
    "board", "board_folder", "column_name", "column_folder", "status", "mode",
    "parent_id", "root_id", "project_id", "owner_project_id", "created_at",
    "updated_at", "last_activity_at", "priority", "due_date", "wake_at",
    "scheduled_start", "scheduled_end", "calendar_ids", "tags", "profiles",
    "users", "assignees", "sprints", "file_list", "run_id", "run_status",
    "harness", "harness_status", "harness_instance_id", "harness_task_id",
    "external_source", "external_status", "archived_at", "archived_by",
    "last_message_at", "last_role", "last_user", "last_content", "last_error",
    "cover_image", "parse_error",
}
_INT_COLUMNS = {
    "file_size", "chat_index", "sort_index", "pinned", "is_template", "all_day",
    "progress", "archived", "message_count", "pending_count", "last_done",
    "index_rev",
}
_REAL_COLUMNS = {"file_mtime", "cost", "estimation_cost", "indexed_at"}


def column_type(name: str) -> str:
    """SQLite type for a projection column."""
    if name in _TEXT_COLUMNS:
        return "TEXT"
    if name in _INT_COLUMNS:
        return "INTEGER"
    if name in _REAL_COLUMNS:
        return "REAL"
    return "TEXT"


def _ddl() -> str:
    """DDL of the ``chats`` table, generated from :data:`CHAT_COLUMNS`."""
    lines = []
    for name in CHAT_COLUMNS:
        if name == "id":
            lines.append("  id TEXT PRIMARY KEY")
        else:
            lines.append(f"  {name} {column_type(name)}")
    return ",\n".join(lines)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
{_ddl()}
);

CREATE INDEX IF NOT EXISTS chats_updated   ON chats(updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_activity  ON chats(last_activity_at DESC);
CREATE INDEX IF NOT EXISTS chats_board     ON chats(board, column_name, sort_index, updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_status    ON chats(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_parent    ON chats(parent_id);
CREATE INDEX IF NOT EXISTS chats_root      ON chats(root_id);
CREATE INDEX IF NOT EXISTS chats_owner     ON chats(owner_project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_archived  ON chats(archived, updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_due       ON chats(due_date);
CREATE INDEX IF NOT EXISTS chats_mtime     ON chats(file_mtime);

CREATE TABLE IF NOT EXISTS messages (
  chat_id      TEXT NOT NULL,
  doc_id       TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  role         TEXT,
  user         TEXT,
  profiles     TEXT,
  created_at   TEXT,
  updated_at   TEXT,
  preview      TEXT,
  content_hash TEXT,
  hide         INTEGER NOT NULL DEFAULT 0,
  unsent       INTEGER NOT NULL DEFAULT 0,
  done         INTEGER NOT NULL DEFAULT 1,
  error        TEXT,
  cost         REAL,
  PRIMARY KEY (chat_id, doc_id)
);
CREATE INDEX IF NOT EXISTS messages_seq    ON messages(chat_id, seq);
CREATE INDEX IF NOT EXISTS messages_unread ON messages(chat_id, role, done, hide, seq);

CREATE TABLE IF NOT EXISTS chat_reads (
  chat_id       TEXT NOT NULL,
  user          TEXT NOT NULL,
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  last_read_at  TEXT,
  PRIMARY KEY (chat_id, user)
);
CREATE INDEX IF NOT EXISTS chat_reads_user ON chat_reads(user, chat_id);

CREATE TABLE IF NOT EXISTS drafts (
  chat_id    TEXT NOT NULL,
  user       TEXT NOT NULL,
  doc_id     TEXT,
  content    TEXT,
  updated_at TEXT,
  PRIMARY KEY (chat_id, user)
);

CREATE TABLE IF NOT EXISTS participants (
  chat_id TEXT NOT NULL,
  user    TEXT NOT NULL,
  kind    TEXT NOT NULL,
  PRIMARY KEY (chat_id, user, kind)
);
CREATE INDEX IF NOT EXISTS participants_user ON participants(user, chat_id);

CREATE TABLE IF NOT EXISTS search_docs (
  rid          INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id      TEXT NOT NULL,
  doc_id       TEXT,
  field        TEXT NOT NULL,
  valid        INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT,
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS search_docs_chat ON search_docs(chat_id, valid);
CREATE INDEX IF NOT EXISTS search_docs_doc  ON search_docs(doc_id, valid);
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Optional[str]:
    """Normalize a timestamp to ISO-8601 with a ``T`` separator.

    Files historically contain both ``str(datetime.now())`` (space separator)
    and ``datetime.isoformat()``. The index stores one form so string ordering
    and range comparisons are correct.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # Only rewrite the date/time separator, never a space inside a value.
    if len(text) >= 19 and text[10] == " ":
        text = text[:10] + "T" + text[11:]
    return text


def _preview(text: Any, limit: int = PREVIEW_CHARS) -> str:
    """Collapse whitespace and truncate a preview string.

    Tool output can be megabytes; only the head can possibly contribute to a
    280-char preview, so cut it before ``split()`` (this is on the rebuild hot
    path for every message).
    """
    value = str(text or "")
    if len(value) > limit * 8:
        value = value[: limit * 8]
    return " ".join(value.split())[:limit]


def _text_hash(text: str) -> str:
    """Stable content hash for search de-duplication."""
    return hashlib.sha1(str(text or "").encode("utf-8", "ignore")).hexdigest()


def _json_list(value: Any) -> str:
    """Serialize a list for storage; keep the result JSON-parseable."""
    if value is None:
        return "[]"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(list(value))
    except TypeError:
        return "[]"


def _as_list(value: Any) -> List[Any]:
    """Normalize a stored JSON list (or a Python list) to a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, (list, tuple)) else []
        except (ValueError, TypeError):
            return []
    return []


def _as_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    """Best-effort int conversion."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_timestamp(value: Optional[str]) -> Optional[float]:
    """Convert an ISO date/datetime string to a POSIX timestamp (local time)."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None


def _fts_query(query: str) -> Optional[str]:
    """Build a safe FTS5 AND/prefix query from a user string.

    The current search is a case-insensitive substring match; FTS tokenizes.
    We preserve the intent with word/prefix matching and never pass raw user
    syntax to FTS (which would raise on quotes/operators).
    """
    tokens = re.findall(r"[^\W_]+", str(query or ""), flags=re.UNICODE)
    tokens = [token for token in tokens if token]
    if not tokens:
        return None
    # A single short token (1-2 chars) is handled by the LIKE fallback.
    return " AND ".join(f"{token}*" for token in tokens)


def _like_query(query: str) -> str:
    """Escape ``%``/``_`` for a LIKE search."""
    escaped = str(query or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


class ChatIndexStore:
    """Read model for one project's chat/task store.

    Args:
        tasks_path: Absolute ``<codx_path>/tasks`` folder.
        db_path: Index path. Defaults to ``<codx_path>/index/chats.db``.
        search_mode: ``metadata``, ``contentless`` (default) or ``full``.
        logger: Optional logger (defaults to this module's logger).
    """

    def __init__(
        self,
        tasks_path: str,
        db_path: Optional[str] = None,
        search_mode: str = DEFAULT_SEARCH_MODE,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.tasks_path = os.path.abspath(str(tasks_path))
        self.db_path = os.path.abspath(
            db_path
            or os.path.join(os.path.dirname(self.tasks_path), "index", "chats.db")
        )
        mode = str(search_mode or DEFAULT_SEARCH_MODE).strip().lower()
        self.search_mode = mode if mode in SEARCH_MODES else DEFAULT_SEARCH_MODE
        self.logger = logger_ or logger
        self._local = threading.local()
        self._lock = threading.RLock()
        self._bulk = False
        self.fts_available = False
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connections and schema
    # ------------------------------------------------------------------
    def connection(self) -> sqlite3.Connection:
        """Return the thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close the current thread's connection (best effort)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    def _ensure_schema(self) -> None:
        """Create/upgrade the schema; the index is disposable."""
        conn = self.connection()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.logger.warning(
                "ChatIndex: schema %s != %s, rebuilding the index",
                version,
                SCHEMA_VERSION,
            )
            self._drop_all(conn)
            version = 0
        conn.executescript(SCHEMA)
        self._ensure_fts(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        previous_mode = conn.execute(
            "SELECT value FROM meta WHERE key='search_mode'"
        ).fetchone()
        if previous_mode and previous_mode[0] != self.search_mode:
            # Changing the search mode changes which rows exist; force a
            # rebuild instead of serving a half-populated index.
            self.logger.info(
                "ChatIndex: search mode %s -> %s, scheduling a rebuild",
                previous_mode[0],
                self.search_mode,
            )
            conn.execute("DELETE FROM meta WHERE key='build_id'")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('search_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (self.search_mode,),
        )
        conn.commit()

    def _drop_all(self, conn: sqlite3.Connection) -> None:
        """Drop the derived tables (never ``chat_reads``/``drafts``)."""
        for table in ("search_fts", "search_docs", "messages", "participants", "chats"):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            except sqlite3.Error:
                pass
        conn.commit()

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        """Create the FTS table when the runtime supports it."""
        if self.search_mode == "metadata":
            self.fts_available = self._has_fts(conn)
            return
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
                "text, content='', tokenize='unicode61 remove_diacritics 2')"
            )
            self.fts_available = True
        except sqlite3.OperationalError as ex:
            self.logger.warning("ChatIndex: FTS5 unavailable (%s), using LIKE search", ex)
            self.fts_available = False
            self.search_mode = "metadata"

    @staticmethod
    def _has_fts(conn: sqlite3.Connection) -> bool:
        """True when the FTS table exists and is queryable."""
        try:
            conn.execute("SELECT 1 FROM search_fts LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------
    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write in one ``BEGIN IMMEDIATE`` transaction (or join one)."""
        conn = self.connection()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        """Read a metadata value."""
        row = self.connection().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: Any) -> None:
        """Write a metadata value."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    def is_ready(self) -> bool:
        """True when a complete build has been committed."""
        return bool(self.get_meta("build_id"))

    def is_building(self, max_age: float = BUILD_MARKER_MAX_AGE) -> bool:
        """True when a rebuild is running (in this or another process).

        The marker is a timestamp so a crashed builder does not block writers
        forever; after *max_age* seconds it is ignored.
        """
        value = self.get_meta(BUILD_MARKER)
        if not value:
            return False
        try:
            return (time.time() - float(value)) < max_age
        except (TypeError, ValueError):
            return False

    def mark_ready(self, count: int) -> None:
        """Stamp a completed build."""
        with self._transaction() as conn:
            for key, value in (
                ("build_id", uuid.uuid4().hex),
                ("built_at", datetime.now().isoformat()),
                ("chats_count", str(count)),
            ):
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    # ------------------------------------------------------------------
    # Build / rebuild
    # ------------------------------------------------------------------
    def rebuild(self, documents: Iterable[Tuple[str, Dict[str, Any]]]) -> int:
        """Replace the derived index from ``(path, document)`` pairs.

        ``chat_reads`` and ``drafts`` are user-local state and are preserved.
        The whole rebuild is one transaction: readers keep their snapshot and a
        crash leaves the previous index intact.

        Args:
            documents: Iterable of ``(file_path, chat_document)``.

        Returns:
            Number of chats indexed.
        """
        started = time.perf_counter()
        count = 0
        seen: set[str] = set()
        duplicates = 0
        try:
            self.set_meta(BUILD_MARKER, str(time.time()))
        except sqlite3.Error:
            pass
        conn = self.connection()
        try:
            conn.execute("PRAGMA synchronous=OFF")
        except sqlite3.Error:
            pass
        self._bulk = True
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM search_docs")
            if self.fts_available:
                # 'delete-all' is the FTS5 contentless way to clear the index
                # without dropping the table: readers never see a missing
                # schema while a rebuild is running.
                conn.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM participants")
            conn.execute("DELETE FROM chats")
            for path, document in documents:
                chat_id = str((document or {}).get("id") or "").strip()
                if not chat_id:
                    continue
                if chat_id in seen:
                    duplicates += 1
                    continue
                seen.add(chat_id)
                self._upsert_document(conn, document, path)
                count += 1
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('build_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (uuid.uuid4().hex,),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('built_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now().isoformat(),),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('chats_count', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(count),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._bulk = False
            try:
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error:
                pass
            try:
                with self._transaction() as marker_conn:
                    marker_conn.execute(
                        "DELETE FROM meta WHERE key=?", (BUILD_MARKER,)
                    )
            except sqlite3.Error:
                pass
        elapsed = time.perf_counter() - started
        self.logger.info(
            "ChatIndex: rebuilt %d chats in %.2fs (%d duplicate ids skipped) at %s",
            count,
            elapsed,
            duplicates,
            self.db_path,
        )
        return count

    # ------------------------------------------------------------------
    # Upsert / delete
    # ------------------------------------------------------------------
    def upsert_chat(self, document: Dict[str, Any], path: str) -> None:
        """Write one chat (metadata + message rows + search entries)."""
        if not document or not str(document.get("id") or "").strip():
            return
        with self._transaction() as conn:
            self._upsert_document(conn, document, path)

    def delete_chat(self, chat_id: str) -> None:
        """Remove a chat and its derived rows (per-user state is kept)."""
        if not chat_id:
            return
        with self._transaction() as conn:
            conn.execute("UPDATE search_docs SET valid=0 WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM participants WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    def update_archived(self, chat_id: str, archived: bool, **fields: Any) -> None:
        """Flip the archived flag (and optional metadata) without a full upsert."""
        if not chat_id:
            return
        with self._transaction() as conn:
            conn.execute(
                "UPDATE chats SET archived=?, archived_at=?, archived_by=?, "
                "index_rev=COALESCE(index_rev,0)+1, indexed_at=? WHERE id=?",
                (
                    1 if archived else 0,
                    fields.get("archived_at") or (datetime.now().isoformat() if archived else None),
                    fields.get("archived_by"),
                    time.time(),
                    chat_id,
                ),
            )

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------
    def _upsert_document(
        self, conn: sqlite3.Connection, document: Dict[str, Any], path: str
    ) -> None:
        """Insert/replace one chat and its messages/search rows."""
        chat_id = str(document.get("id"))
        messages = list(document.get("messages") or [])
        visible = [
            m for m in messages
            if not bool(m.get("unsent")) and not bool(m.get("hide"))
        ]
        last = visible[-1] if visible else None
        pending = sum(1 for m in messages if m.get("done") is False)

        try:
            stat = os.stat(path)
            file_mtime = stat.st_mtime
            file_size = stat.st_size
        except OSError:
            file_mtime = None
            file_size = None

        history = " ".join(
            str(entry.get("summary") or "")
            for entry in (document.get("history") or [])
            if isinstance(entry, dict)
        )
        file_list = " ".join(str(value) for value in _as_list(document.get("file_list")))
        users = _as_list(document.get("users"))
        assignees = _as_list(document.get("assignees"))
        profiles = _as_list(document.get("profiles"))

        values = {
            "id": chat_id,
            "doc_id": document.get("doc_id"),
            "path": path,
            "file_mtime": file_mtime,
            "file_size": file_size,
            "sig": f"{stat.st_mtime_ns}:{stat.st_size}" if file_size is not None else None,
            "content_hash": None,
            "name": document.get("name") or "",
            "description": document.get("description") or "",
            "board": document.get("board") or "",
            "board_folder": None,
            "column_name": document.get("column") or "",
            "column_folder": None,
            "status": document.get("status") or "",
            "mode": document.get("mode") or "",
            "parent_id": document.get("parent_id"),
            "root_id": document.get("root_id"),
            "project_id": document.get("project_id"),
            "owner_project_id": document.get("owner_project_id"),
            "created_at": _iso(document.get("created_at")),
            "updated_at": _iso(document.get("updated_at")),
            "last_activity_at": _iso((last or {}).get("created_at") or document.get("updated_at")),
            "chat_index": _as_int(document.get("chat_index")),
            "sort_index": document.get("sort_index"),
            "pinned": 1 if document.get("pinned") else 0,
            "is_template": 1 if document.get("is_template") else 0,
            "priority": document.get("priority"),
            "due_date": _iso(document.get("due_date")),
            "wake_at": _iso(document.get("wake_at")),
            "scheduled_start": _iso(document.get("scheduled_start")),
            "scheduled_end": _iso(document.get("scheduled_end")),
            "all_day": 1 if document.get("all_day") else 0,
            "calendar_ids": _json_list(document.get("calendar_ids")),
            "progress": _as_int(document.get("progress")),
            "tags": _json_list(document.get("tags")),
            "profiles": _json_list(profiles),
            "users": _json_list(users),
            "assignees": _json_list(assignees),
            "sprints": _json_list(document.get("sprints")),
            "file_list": _json_list(document.get("file_list")),
            "cost": _as_float(document.get("cost")),
            "estimation_cost": document.get("estimation_cost"),
            "run_id": document.get("run_id"),
            "run_status": document.get("run_status"),
            "harness": document.get("harness"),
            "harness_status": document.get("harness_status"),
            "harness_instance_id": document.get("harness_instance_id"),
            "harness_task_id": document.get("harness_task_id"),
            "external_source": document.get("external_source"),
            "external_status": document.get("external_status"),
            "archived": 1 if document.get("archived") else 0,
            "archived_at": _iso(document.get("archived_at")),
            "archived_by": document.get("archived_by"),
            "message_count": len(messages),
            "pending_count": pending,
            "last_message_at": _iso((last or {}).get("created_at")),
            "last_role": (last or {}).get("role") or "",
            "last_user": (last or {}).get("user") or "",
            "last_content": _preview((last or {}).get("content")),
            "last_done": 1 if (last or {}).get("done", True) else 0,
            "last_error": (last or {}).get("error"),
            "cover_image": self._cover_image(messages),
            "parse_error": document.get("__parse_error__"),
            "index_rev": 1,
            "indexed_at": time.time(),
        }

        columns = list(CHAT_COLUMNS)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in columns if name != "id"
        )
        conn.execute(
            f"INSERT INTO chats ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, "
            "index_rev=COALESCE(chats.index_rev,0)+1",
            [values.get(name) for name in columns],
        )

        self._sync_messages(conn, chat_id, messages)
        self._sync_legacy_reads(conn, chat_id, messages)
        self._sync_participants(conn, chat_id, users, assignees, profiles, messages)
        self._sync_chat_search(
            conn,
            chat_id,
            {
                "name": values["name"],
                "description": values["description"],
                "history": history,
                "file_list": file_list,
                "users": " ".join(str(v) for v in users + assignees),
                "tags": " ".join(str(v) for v in _as_list(document.get("tags"))),
            },
        )

    @staticmethod
    def _cover_image(messages: Sequence[Dict[str, Any]]) -> Optional[str]:
        """First image of the conversation, for task cards."""
        for message in messages:
            for image in message.get("images") or []:
                if isinstance(image, str) and image:
                    return image
                if isinstance(image, dict):
                    candidate = image.get("url") or image.get("path") or image.get("file")
                    if candidate:
                        return str(candidate)
        return None

    def _sync_messages(
        self, conn: sqlite3.Connection, chat_id: str, messages: Sequence[Dict[str, Any]]
    ) -> None:
        """Upsert message rows and their search entries; remove deleted rows."""
        incoming: List[str] = []
        for seq, message in enumerate(messages):
            doc_id = str(message.get("doc_id") or f"seq-{seq}")
            incoming.append(doc_id)
            content = str(message.get("content") or "")
            think = str(message.get("think") or "")
            content_hash = _text_hash(content + "\x00" + think)
            conn.execute(
                "INSERT INTO messages(chat_id, doc_id, seq, role, user, profiles, "
                "created_at, updated_at, preview, content_hash, hide, unsent, done, "
                "error, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(chat_id, doc_id) DO UPDATE SET "
                "seq=excluded.seq, role=excluded.role, user=excluded.user, "
                "profiles=excluded.profiles, created_at=excluded.created_at, "
                "updated_at=excluded.updated_at, preview=excluded.preview, "
                "content_hash=excluded.content_hash, hide=excluded.hide, "
                "unsent=excluded.unsent, done=excluded.done, error=excluded.error, "
                "cost=excluded.cost",
                (
                    chat_id,
                    doc_id,
                    seq,
                    message.get("role") or "",
                    message.get("user"),
                    _json_list(message.get("profiles")),
                    _iso(message.get("created_at")),
                    _iso(message.get("updated_at")),
                    _preview(content),
                    content_hash,
                    1 if message.get("hide") else 0,
                    1 if message.get("unsent") else 0,
                    1 if message.get("done", True) else 0,
                    message.get("error"),
                    _as_float(message.get("cost")),
                ),
            )
            if self._bulk and message.get("unsent"):
                # Legacy drafts lived in the task file. Import them once, per
                # author, without ever rewriting the file.
                author = str(message.get("user") or "shared")
                conn.execute(
                    "INSERT INTO drafts(chat_id, user, doc_id, content, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(chat_id, user) DO NOTHING",
                    (
                        chat_id,
                        author,
                        doc_id,
                        str(message.get("content") or ""),
                        _iso(message.get("updated_at")),
                    ),
                )
            indexable = (
                not message.get("hide")
                and not message.get("unsent")
                and (message.get("role") != "assistant" or message.get("done", True))
            )
            if indexable:
                if content.strip():
                    self._index_field(conn, chat_id, doc_id, "content", content)
                if think.strip():
                    self._index_field(conn, chat_id, doc_id, "think", think)
            else:
                conn.execute(
                    "UPDATE search_docs SET valid=0 WHERE chat_id=? AND doc_id=?",
                    (chat_id, doc_id),
                )

        if incoming:
            placeholders = ",".join("?" for _ in incoming)
            conn.execute(
                f"DELETE FROM messages WHERE chat_id=? AND doc_id NOT IN ({placeholders})",
                [chat_id, *incoming],
            )
            conn.execute(
                f"UPDATE search_docs SET valid=0 WHERE chat_id=? AND doc_id IS NOT NULL "
                f"AND doc_id NOT IN ({placeholders})",
                [chat_id, *incoming],
            )
        else:
            conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            conn.execute(
                "UPDATE search_docs SET valid=0 WHERE chat_id=? AND doc_id IS NOT NULL",
                (chat_id,),
            )

    def _sync_legacy_reads(
        self, conn: sqlite3.Connection, chat_id: str, messages: Sequence[Dict[str, Any]]
    ) -> None:
        """Import the legacy ``read_by`` arrays into the per-user read cursor.

        The cursor is monotonic: an older ``read_by`` can never move a user
        backwards. This runs on every write, so receipts written by the old
        code keep working during the migration.
        """
        latest: Dict[str, int] = {}
        for seq, message in enumerate(messages):
            for user in message.get("read_by") or []:
                name = str(user or "").strip()
                if name:
                    latest[name] = max(latest.get(name, 0), seq)
        for user, seq in latest.items():
            conn.execute(
                "INSERT INTO chat_reads(chat_id, user, last_read_seq, last_read_at) "
                "VALUES (?,?,?,?) ON CONFLICT(chat_id, user) DO UPDATE SET "
                "last_read_seq=max(chat_reads.last_read_seq, excluded.last_read_seq)",
                (chat_id, user, seq, datetime.now().isoformat()),
            )

    def _sync_participants(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        users: Sequence[Any],
        assignees: Sequence[Any],
        profiles: Sequence[Any],
        messages: Sequence[Dict[str, Any]],
    ) -> None:
        """Rebuild the participant rows used by the user filter."""
        conn.execute("DELETE FROM participants WHERE chat_id=?", (chat_id,))
        seen: set[Tuple[str, str]] = set()

        def add(user: Any, kind: str) -> None:
            name = str(user or "").strip()
            if not name or (name, kind) in seen:
                return
            seen.add((name, kind))
            conn.execute(
                "INSERT OR IGNORE INTO participants(chat_id, user, kind) VALUES (?,?,?)",
                (chat_id, name, kind),
            )

        for user in users:
            add(user, "participant")
        for user in assignees:
            add(user, "assignee")
        for profile in profiles:
            add(profile, "profile")
        for message in messages:
            add(message.get("user"), "participant")
        # First user message is the best available creator signal (Chat has no
        # created_by field; the old code assumed one and crashed).
        for message in messages:
            if message.get("role") == "user" and message.get("user"):
                add(message.get("user"), "creator")
                break

    def _sync_chat_search(
        self, conn: sqlite3.Connection, chat_id: str, fields: Dict[str, str]
    ) -> None:
        """Index/replace chat-level searchable fields."""
        for field, text in fields.items():
            if str(text or "").strip():
                self._index_field(conn, chat_id, None, field, str(text))
            else:
                conn.execute(
                    "UPDATE search_docs SET valid=0 WHERE chat_id=? AND doc_id IS NULL "
                    "AND field=? AND valid=1",
                    (chat_id, field),
                )

    def _index_field(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        doc_id: Optional[str],
        field: str,
        text: str,
    ) -> None:
        """Insert a new search version when the text changed; invalidate old."""
        if not self.fts_available or self.search_mode == "metadata":
            return
        content_hash = _text_hash(text)
        if self._bulk:
            # A rebuild starts from empty tables: no previous version to
            # invalidate and no hash to compare.
            rid = conn.execute(
                "INSERT INTO search_docs(chat_id, doc_id, field, valid, content_hash, updated_at) "
                "VALUES (?,?,?,1,?,?)",
                (chat_id, doc_id, field, content_hash, datetime.now().isoformat()),
            ).lastrowid
            conn.execute(
                "INSERT INTO search_fts(rowid, text) VALUES (?, ?)", (rid, text)
            )
            return
        row = conn.execute(
            "SELECT content_hash FROM search_docs WHERE chat_id=? AND doc_id IS ? "
            "AND field=? AND valid=1 ORDER BY rid DESC LIMIT 1",
            (chat_id, doc_id, field),
        ).fetchone()
        if row and row[0] == content_hash:
            return
        conn.execute(
            "UPDATE search_docs SET valid=0 WHERE chat_id=? AND doc_id IS ? "
            "AND field=? AND valid=1",
            (chat_id, doc_id, field),
        )
        rid = conn.execute(
            "INSERT INTO search_docs(chat_id, doc_id, field, valid, content_hash, updated_at) "
            "VALUES (?,?,?,1,?,?)",
            (chat_id, doc_id, field, content_hash, datetime.now().isoformat()),
        ).lastrowid
        conn.execute(
            "INSERT INTO search_fts(rowid, text) VALUES (?, ?)", (rid, text)
        )

    # ------------------------------------------------------------------
    # Read: chat rows
    # ------------------------------------------------------------------
    @staticmethod
    def _row_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a chats row to a JSON-friendly dict."""
        data = {key: row[key] for key in row.keys()}
        for key in ("tags", "profiles", "users", "assignees", "sprints", "file_list",
                    "calendar_ids"):
            data[key] = _as_list(data.get(key))
        data["pinned"] = bool(data.get("pinned"))
        data["is_template"] = bool(data.get("is_template"))
        data["all_day"] = bool(data.get("all_day"))
        data["archived"] = bool(data.get("archived"))
        data["last_done"] = bool(data.get("last_done"))
        # API/model aliases: the index uses SQL-safe names.
        data["column"] = data.get("column_name") or ""
        data["file_path"] = data.get("path") or ""
        return data

    @staticmethod
    def _last_message(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        """Compact last-message payload for cards."""
        if not row["last_message_at"] and not row["last_role"] and not row["last_content"]:
            return None
        return {
            "doc_id": None,
            "role": row["last_role"] or "",
            "user": row["last_user"] or "",
            "content": row["last_content"] or "",
            "created_at": row["last_message_at"],
            "updated_at": row["last_message_at"],
            "done": bool(row["last_done"]) if row["last_done"] is not None else True,
            "error": row["last_error"],
            "read_by": [],
        }

    def _unread_counts(
        self, chat_ids: Sequence[str], user: Optional[str]
    ) -> Dict[str, int]:
        """Unread assistant replies per chat for *user* (one grouped query)."""
        if not user or not chat_ids:
            return {}
        placeholders = ",".join("?" for _ in chat_ids)
        rows = self.connection().execute(
            f"""
            SELECT m.chat_id,
                   SUM(CASE WHEN m.role='assistant' AND m.done=1 AND m.hide=0
                            AND m.seq > COALESCE(r.last_read_seq, 0)
                            AND (m.user IS NULL OR m.user != ?)
                       THEN 1 ELSE 0 END) AS unread
            FROM messages m
            LEFT JOIN chat_reads r ON r.chat_id=m.chat_id AND r.user=?
            WHERE m.chat_id IN ({placeholders})
            GROUP BY m.chat_id
            """,
            [user, user, *chat_ids],
        ).fetchall()
        return {row[0]: int(row[1] or 0) for row in rows}

    @staticmethod
    def _process_state(row: sqlite3.Row) -> Tuple[str, Optional[str]]:
        """Derive the task execution state from the row (same vocabulary)."""
        last_error = str(row["last_error"] or "").strip()
        candidates = (
            ("run", row["run_status"]),
            ("harness", row["harness_status"]),
            ("external", row["external_status"]),
        )
        for label, raw in candidates:
            status = str(raw or "").strip().lower()
            if not status:
                continue
            if status in _PROCESS_ERROR_STATUSES:
                return "error", last_error or f"{label} status: {status}"
            if status in _PROCESS_STOPPED_STATUSES:
                return "stopped", last_error or f"{label} status: {status}"
            if status in _PROCESS_RUNNING_STATUSES:
                return "running", None
        if last_error:
            return "error", last_error
        if row["last_done"] is not None and row["last_done"] == 0:
            return "running", None
        return "idle", None

    def _attach_summaries(
        self, rows: Sequence[sqlite3.Row], unread_for: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Turn rows into projection dicts with unread/process annotations."""
        ids = [row["id"] for row in rows]
        unread = self._unread_counts(ids, unread_for)
        result: List[Dict[str, Any]] = []
        for row in rows:
            data = self._row_dict(row)
            data["last_message"] = self._last_message(row)
            data["unread_count"] = unread.get(row["id"], 0)
            data["message_count"] = int(row["message_count"] or 0)
            data["pending_count"] = int(row["pending_count"] or 0)
            state, error = self._process_state(row)
            data["process_state"] = state
            data["process_error"] = error
            data["task_latest_message"] = data["last_message"]
            data["task_unread_count"] = data["unread_count"]
            data["task_process_state"] = state
            data["task_process_error"] = error
            data["task_last_activity_at"] = data["last_activity_at"]
            result.append(data)
        return result

    def _filter_clause(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        include_archived: bool = False,
        user_id: Optional[str] = None,
        board: Optional[str] = None,
        column: Optional[str] = None,
        chat_type: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        """Build the shared WHERE clause/params for list/count queries."""
        where: List[str] = []
        params: List[Any] = []
        if not include_archived:
            where.append("archived=0")
        if board:
            where.append("lower(board)=lower(?)")
            params.append(board)
        if column:
            where.append("lower(column_name)=lower(?)")
            params.append(column)
        if chat_type:
            kind = str(chat_type).strip().lower()
            if kind == "task":
                where.append("(COALESCE(board,'') != '' OR COALESCE(column_name,'') != '')")
            elif kind == "chat":
                where.append("(COALESCE(board,'') = '' AND COALESCE(column_name,'') = '')")
        if user_id:
            where.append(
                "EXISTS (SELECT 1 FROM participants p WHERE p.chat_id=chats.id AND p.user=?)"
            )
            params.append(user_id)
        if from_date:
            stamp = _to_timestamp(from_date)
            if stamp is not None:
                where.append("file_mtime >= ?")
                params.append(stamp)
        if to_date:
            stamp = _to_timestamp(to_date)
            if stamp is not None:
                where.append("file_mtime <= ?")
                params.append(stamp)
        return (f"WHERE {' AND '.join(where)}" if where else ""), params

    def list_chats(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        include_archived: bool = False,
        unread_for: Optional[str] = None,
        user_id: Optional[str] = None,
        board: Optional[str] = None,
        column: Optional[str] = None,
        chat_type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return projection rows, newest first, with optional filters."""
        clause, params = self._filter_clause(
            from_date=from_date,
            to_date=to_date,
            include_archived=include_archived,
            user_id=user_id,
            board=board,
            column=column,
            chat_type=chat_type,
        )
        sql = f"SELECT * FROM chats {clause} ORDER BY updated_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, int(limit), max(0, int(offset))]
        rows = self.connection().execute(sql, params).fetchall()
        return self._attach_summaries(rows, unread_for)

    def count_chats(
        self,
        include_archived: bool = False,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
        board: Optional[str] = None,
        column: Optional[str] = None,
        chat_type: Optional[str] = None,
    ) -> int:
        """Number of chats matching the same filters as :meth:`list_chats`."""
        clause, params = self._filter_clause(
            from_date=from_date,
            to_date=to_date,
            include_archived=include_archived,
            user_id=user_id,
            board=board,
            column=column,
            chat_type=chat_type,
        )
        return int(
            self.connection().execute(f"SELECT count(*) FROM chats {clause}", params)
            .fetchone()[0]
        )

    def get_chat(self, chat_id: str, unread_for: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return one projection row by id."""
        if not chat_id:
            return None
        row = self.connection().execute(
            "SELECT * FROM chats WHERE id=?", (chat_id,)
        ).fetchone()
        if row is None:
            return None
        return self._attach_summaries([row], unread_for)[0]

    def find_path(self, chat_id: str) -> Optional[str]:
        """O(1) chat id -> file path lookup."""
        if not chat_id:
            return None
        row = self.connection().execute(
            "SELECT path FROM chats WHERE id=?", (chat_id,)
        ).fetchone()
        return row[0] if row else None

    def paths(self) -> Dict[str, str]:
        """Return ``chat_id -> path`` for every indexed chat."""
        rows = self.connection().execute("SELECT id, path FROM chats").fetchall()
        return {row[0]: row[1] for row in rows}

    def signatures(self) -> Dict[str, str]:
        """Return ``path -> "mtime_ns:size"`` for every indexed chat."""
        rows = self.connection().execute("SELECT path, sig FROM chats").fetchall()
        return {row[0]: row[1] for row in rows if row[1]}

    # ------------------------------------------------------------------
    # Read: search
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Full-text search with the envelope expected by ``/chats/search``."""
        query = str(query or "").strip()
        page = max(1, int(page or 1))
        page_size = max(1, int(page_size or 20))
        offset = (page - 1) * page_size
        allowed_fields = self._allowed_fields(filters)
        if not allowed_fields:
            return self._search_envelope([], 0, page, page_size)
        if not query:
            return self._search_envelope([], 0, page, page_size)

        if self.fts_available and self.search_mode != "metadata":
            return self._search_fts(
                query, allowed_fields, user_id, from_date, to_date, page, page_size, offset
            )
        return self._search_like(
            query, allowed_fields, user_id, from_date, to_date, page, page_size, offset
        )

    @staticmethod
    def _allowed_fields(filters: Optional[Dict[str, Any]]) -> List[str]:
        """Map the API SearchFilters flags to indexed fields."""
        flags = filters or {}

        def enabled(name: str, default: bool = True) -> bool:
            value = flags.get(name, default)
            return bool(value)

        fields: List[str] = []
        if enabled("search_name"):
            fields.append("name")
        if enabled("search_description"):
            fields.append("description")
        if enabled("search_history"):
            fields.append("history")
        if enabled("search_files"):
            fields.append("file_list")
        if enabled("search_messages"):
            fields.extend(["content", "think"])
        if enabled("search_message_metadata"):
            fields.append("users")
        if enabled("search_model"):
            fields.extend(["llm_model", "tags"])
        if enabled("search_status"):
            fields.append("status")
        if enabled("search_mode"):
            fields.append("mode")
        return [field for field in fields if field in SEARCH_WEIGHTS]

    def _date_clause(
        self, from_date: Optional[str], to_date: Optional[str]
    ) -> Tuple[List[str], List[Any]]:
        """SQL clauses for the file-mtime window (kept compatible with today)."""
        where: List[str] = []
        params: List[Any] = []
        if from_date:
            stamp = _to_timestamp(from_date)
            if stamp is not None:
                where.append("file_mtime >= ?")
                params.append(stamp)
        if to_date:
            stamp = _to_timestamp(to_date)
            if stamp is not None:
                where.append("file_mtime <= ?")
                params.append(stamp)
        return where, params

    def _search_fts(
        self,
        query: str,
        fields: Sequence[str],
        user_id: Optional[str],
        from_date: Optional[str],
        to_date: Optional[str],
        page: int,
        page_size: int,
        offset: int,
    ) -> Dict[str, Any]:
        """FTS5 path.

        ``bm25()`` cannot be used through a CTE/aggregate (SQLite rejects it
        with "unable to use function bm25 in the requested context"), so we
        fetch the ranked hits and group them per chat in Python. The number of
        hits is bounded; a search that matches more than that keeps the best
        ranked ones and still counts every matching chat for ``total``.
        """
        match = _fts_query(query)
        if not match:
            return self._search_like(
                query, fields, user_id, from_date, to_date, page, page_size, offset
            )
        field_placeholders = ",".join("?" for _ in fields)
        where, params = self._date_clause(from_date, to_date)
        where_sql = f"AND {' AND '.join(where)}" if where else ""
        base = f"""
            FROM search_fts f
            JOIN search_docs sd ON sd.rid=f.rowid AND sd.valid=1
            JOIN chats c ON c.id=sd.chat_id AND c.archived=0
            WHERE search_fts MATCH ?
              AND sd.field IN ({field_placeholders})
              {where_sql}
        """
        user_sql = ""
        user_params: List[Any] = []
        if user_id:
            user_sql = (
                " AND EXISTS (SELECT 1 FROM participants p "
                "WHERE p.chat_id=c.id AND p.user=?)"
            )
            user_params.append(user_id)
        base += user_sql
        args = [match, *fields, *params, *user_params]
        hit_limit = min(20000, max(5000, (page + 1) * page_size * 50))
        try:
            total = int(
                self.connection().execute(
                    f"SELECT count(DISTINCT sd.chat_id) {base}", args
                ).fetchone()[0]
            )
            hits = self.connection().execute(
                f"""
                SELECT sd.chat_id, sd.doc_id, sd.field,
                       bm25(search_fts) AS rank, c.updated_at AS chat_updated_at
                {base}
                ORDER BY rank ASC
                LIMIT ?
                """,
                [*args, hit_limit],
            ).fetchall()
        except sqlite3.OperationalError as ex:
            self.logger.warning("ChatIndex: FTS query failed (%s), using LIKE", ex)
            return self._search_like(
                query, fields, user_id, from_date, to_date, page, page_size, offset
            )

        grouped: Dict[str, Dict[str, Any]] = {}
        for hit in hits:
            chat_id = hit["chat_id"]
            entry = grouped.setdefault(
                chat_id,
                {
                    "best_rank": hit["rank"],
                    "updated_at": hit["chat_updated_at"],
                    "fields": set(),
                    "doc_ids": [],
                    "hits": 0,
                },
            )
            entry["hits"] += 1
            if hit["rank"] is not None and (
                entry["best_rank"] is None or hit["rank"] < entry["best_rank"]
            ):
                entry["best_rank"] = hit["rank"]
            if hit["field"]:
                entry["fields"].add(hit["field"])
            if hit["doc_id"] and hit["doc_id"] not in entry["doc_ids"]:
                entry["doc_ids"].append(hit["doc_id"])

        ordered = sorted(
            grouped.items(),
            key=lambda item: (
                item[1]["best_rank"] if item[1]["best_rank"] is not None else 0.0,
                "" if item[1]["updated_at"] is None else str(item[1]["updated_at"]),
            ),
            reverse=False,
        )
        # Best rank first, then newest chat for ties.
        ordered.sort(
            key=lambda item: str(item[1]["updated_at"] or ""),
            reverse=True,
        )
        ordered.sort(
            key=lambda item: item[1]["best_rank"]
            if item[1]["best_rank"] is not None
            else 0.0
        )
        page_items = ordered[offset: offset + page_size]
        chat_ids = [chat_id for chat_id, _ in page_items]
        rows = self._rows_by_ids(chat_ids)
        summaries = self._attach_summaries(rows, user_id)
        by_id = {row["id"]: row for row in summaries}
        results: List[Dict[str, Any]] = []
        for chat_id, entry in page_items:
            data = by_id.get(chat_id)
            if data is None:
                continue
            doc_id = next(
                (value for value in entry["doc_ids"] if value),
                None,
            )
            results.append(
                {
                    "chat": data,
                    "relevance_score": self._score(entry["best_rank"], entry["hits"]),
                    "matched_fields": sorted(entry["fields"]),
                    "snippet": self._snippet(chat_id, doc_id),
                }
            )
        return self._search_envelope(results, total, page, page_size)

    def _rows_by_ids(self, chat_ids: Sequence[str]) -> List[sqlite3.Row]:
        """Fetch chat rows preserving the given id order."""
        if not chat_ids:
            return []
        placeholders = ",".join("?" for _ in chat_ids)
        rows = self.connection().execute(
            f"SELECT * FROM chats WHERE id IN ({placeholders})", list(chat_ids)
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        return [by_id[chat_id] for chat_id in chat_ids if chat_id in by_id]

    def _search_like(
        self,
        query: str,
        fields: Sequence[str],
        user_id: Optional[str],
        from_date: Optional[str],
        to_date: Optional[str],
        page: int,
        page_size: int,
        offset: int,
    ) -> Dict[str, Any]:
        """Fallback: LIKE over the projection (metadata mode / FTS unavailable)."""
        pattern = _like_query(query)
        searchable = {
            "name": "name",
            "description": "description",
            "last_content": "last_content",
            "file_list": "file_list",
            "users": "users",
            "tags": "tags",
            "status": "status",
            "mode": "mode",
        }
        clauses = [
            f"lower(COALESCE({column},'')) LIKE ? ESCAPE '\\'"
            for field, column in searchable.items()
            if field in fields or (
                field == "last_content" and ("content" in fields or "think" in fields)
            )
        ]
        if not clauses:
            clauses = ["lower(COALESCE(name,'')) LIKE ? ESCAPE '\\'"]
        where, date_params = self._date_clause(from_date, to_date)
        # Parameter order must match the SQL: date clause first, then the
        # LIKE patterns, then the optional user filter.
        params: List[Any] = [*date_params, *([pattern] * len(clauses))]
        user_sql = ""
        if user_id:
            user_sql = (
                " AND EXISTS (SELECT 1 FROM participants p "
                "WHERE p.chat_id=chats.id AND p.user=?)"
            )
            params.append(user_id)
        clause = " AND ".join(where) if where else "1=1"
        try:
            total = int(
                self.connection().execute(
                    f"SELECT count(*) FROM chats WHERE archived=0 AND {clause} "
                    f"AND ({' OR '.join(clauses)}){user_sql}",
                    params,
                ).fetchone()[0]
            )
            rows = self.connection().execute(
                f"SELECT * FROM chats WHERE archived=0 AND {clause} "
                f"AND ({' OR '.join(clauses)}){user_sql} "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        except sqlite3.Error as ex:
            self.logger.warning("ChatIndex: LIKE search failed: %s", ex)
            return self._search_envelope([], 0, page, page_size)
        results = []
        for row in rows:
            data = self._attach_summaries([row], user_id)[0]
            results.append(
                {
                    "chat": data,
                    "relevance_score": 50.0,
                    "matched_fields": ["name"],
                    "snippet": data.get("last_message", {}).get("content") if data.get("last_message") else None,
                }
            )
        return self._search_envelope(results, total, page, page_size)

    def _snippet(self, chat_id: str, doc_id: Optional[str]) -> Optional[str]:
        """Preview of the matched message (contentless mode has no snippet)."""
        if not chat_id:
            return None
        if doc_id:
            row = self.connection().execute(
                "SELECT preview FROM messages WHERE chat_id=? AND doc_id=?",
                (chat_id, doc_id),
            ).fetchone()
            if row and row[0]:
                return row[0]
        row = self.connection().execute(
            "SELECT last_content FROM chats WHERE id=?", (chat_id,)
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _score(best_rank: Any, hit_count: Any) -> float:
        """Normalize bm25 to a 0-100 relevance score (display only)."""
        value = abs(_as_float(best_rank, 0.0))
        score = 100.0 * value / (value + 1.0) if value else 0.0
        # A chat with many matching messages gets a small boost.
        boost = min(10.0, _as_float(hit_count) * 0.5)
        return round(min(100.0, score + boost), 2)

    @staticmethod
    def _search_envelope(
        results: Sequence[Dict[str, Any]], total: int, page: int, page_size: int
    ) -> Dict[str, Any]:
        """Response envelope shared by all search paths."""
        total = int(total or 0)
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {
            "results": list(results),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }

    # ------------------------------------------------------------------
    # Per-user state
    # ------------------------------------------------------------------
    def mark_read(self, chat_id: str, user: str, doc_id: Optional[str] = None) -> int:
        """Advance the read cursor to *doc_id* (or the last message)."""
        if not chat_id or not user:
            return 0
        seq = None
        if doc_id:
            row = self.connection().execute(
                "SELECT seq FROM messages WHERE chat_id=? AND doc_id=?",
                (chat_id, doc_id),
            ).fetchone()
            seq = int(row[0]) if row else None
        if seq is None:
            row = self.connection().execute(
                "SELECT max(seq) FROM messages WHERE chat_id=?", (chat_id,)
            ).fetchone()
            seq = int(row[0] or 0) if row else 0
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO chat_reads(chat_id, user, last_read_seq, last_read_at) "
                "VALUES (?,?,?,?) ON CONFLICT(chat_id, user) DO UPDATE SET "
                "last_read_seq=max(chat_reads.last_read_seq, excluded.last_read_seq), "
                "last_read_at=excluded.last_read_at",
                (chat_id, user, seq, datetime.now().isoformat()),
            )
            row = conn.execute(
                "SELECT last_read_seq FROM chat_reads WHERE chat_id=? AND user=?",
                (chat_id, user),
            ).fetchone()
        return int(row[0] or 0) if row else seq

    def set_read(self, chat_id: str, user: str, seq: int) -> int:
        """Set an absolute read cursor (used when a read is undone)."""
        if not chat_id or not user:
            return 0
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO chat_reads(chat_id, user, last_read_seq, last_read_at) "
                "VALUES (?,?,?,?) ON CONFLICT(chat_id, user) DO UPDATE SET "
                "last_read_seq=excluded.last_read_seq, last_read_at=excluded.last_read_at",
                (chat_id, user, max(0, int(seq)), datetime.now().isoformat()),
            )
        return max(0, int(seq))

    def unread_count(self, chat_id: str, user: str) -> int:
        """Unread assistant replies for one chat/user."""
        if not chat_id or not user:
            return 0
        return self._unread_counts([chat_id], user).get(chat_id, 0)

    def get_draft(self, chat_id: str, user: str) -> Optional[Dict[str, Any]]:
        """Return ``{doc_id, content, updated_at}`` for a user's draft."""
        if not chat_id or not user:
            return None
        row = self.connection().execute(
            "SELECT doc_id, content, updated_at FROM drafts WHERE chat_id=? AND user=?",
            (chat_id, user),
        ).fetchone()
        return dict(row) if row else None

    def save_draft(
        self, chat_id: str, user: str, content: str, doc_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Create/update a user's draft (empty content deletes it)."""
        if not chat_id or not user:
            return None
        text = str(content or "")
        if not text.strip():
            self.delete_draft(chat_id, user)
            return None
        stamp = datetime.now().isoformat()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO drafts(chat_id, user, doc_id, content, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(chat_id, user) DO UPDATE SET "
                "doc_id=COALESCE(drafts.doc_id, excluded.doc_id), "
                "content=excluded.content, updated_at=excluded.updated_at",
                (chat_id, user, doc_id or str(uuid.uuid4()), text, stamp),
            )
        return self.get_draft(chat_id, user)

    def delete_draft(self, chat_id: str, user: str) -> bool:
        """Delete a user's draft."""
        if not chat_id or not user:
            return False
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM drafts WHERE chat_id=? AND user=?", (chat_id, user)
            )
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------
    # Health / maintenance
    # ------------------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        """Diagnostics for ``/api/chats/index/health`` and tests."""
        conn = self.connection()
        counts = {}
        for table in ("chats", "messages", "participants", "chat_reads", "drafts",
                      "search_docs"):
            try:
                counts[table] = int(
                    conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
            except sqlite3.Error:
                counts[table] = -1
        try:
            dead = int(
                conn.execute(
                    "SELECT count(*) FROM search_docs WHERE valid=0"
                ).fetchone()[0]
            )
        except sqlite3.Error:
            dead = -1
        try:
            size = os.path.getsize(self.db_path)
        except OSError:
            size = 0
        meta = {
            row[0]: row[1]
            for row in conn.execute("SELECT key, value FROM meta").fetchall()
        }
        return {
            "db_path": self.db_path,
            "db_bytes": size,
            "ready": self.is_ready(),
            "building": self.is_building(),
            "schema_version": meta.get("schema_version"),
            "search_mode": self.search_mode,
            "fts_available": self.fts_available,
            "counts": counts,
            "dead_search_rows": dead,
            "meta": meta,
        }

    def optimize(self) -> None:
        """Run SQLite maintenance (safe to call periodically)."""
        conn = self.connection()
        conn.execute("PRAGMA optimize")
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def reset(self) -> None:
        """Drop all derived data so the next rebuild starts clean."""
        conn = self.connection()
        with self._lock:
            self._drop_all(conn)
            conn.executescript(SCHEMA)
            self._ensure_fts(conn)
            conn.execute("DELETE FROM meta WHERE key != 'schema_version'")
            conn.commit()
