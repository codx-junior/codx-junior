#!/usr/bin/env python3
"""
Benchmark the chat/task store and a prototype SQLite read model.

This is a *measurement* tool, not production code. It uses only the Python
standard library (sqlite3 + FTS5) and never modifies the canonical files.

It answers three questions on a real project store:

1. How many chat files are there, how large are they, and what are they made of
   (metadata vs last message vs tool output)?
2. What does the current access pattern cost (full tree walk, JSON parse)?
3. What would a per-project metadata + FTS index cost in build time, disk and
   query latency?

Usage::

    # everything (scan + index) on the current project
    python chat_store_bench.py --tasks .codx/tasks

    # only the file/store scan
    python chat_store_bench.py --tasks .codx/tasks scan

    # build the prototype index and run representative queries
    python chat_store_bench.py --tasks .codx/tasks --out /tmp/chats.db index

The output is plain text suitable for a PR comment or a design document.

Notes:
    * A chat lives at ``<tasks>/<board>/<column>/<slug>.<uuid>.json``.
      ``index.json``, ``boards/``, ``.archive/`` and catalogue backups are not
      chats and are excluded.
    * Corrupt files are detected with ``json.JSONDecodeError``; when the prefix
      is a complete document it is recovered with ``raw_decode`` (this is what a
      robust rebuild must do instead of silently dropping the task).
    * Duplicate chat ids are reported; the prototype index keeps the first one
      and counts the rest.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

#: Files at the root of the tasks tree that are storage, not chats.
STORE_FILES = {"index.json", "kanban.json", "boards.json", "kanban.v1.backup.json"}
STORE_DIRS = {"boards", ".archive", ".blobs", ".locks"}

TEXT_COLUMNS = {
    "path", "name", "description", "board", "column_name", "status", "mode",
    "parent_id", "root_id", "owner_project_id", "project_id", "created_at",
    "updated_at", "priority", "due_date", "tags", "profiles", "users",
    "assignees", "file_list", "sprints", "run_id", "run_status",
    "harness_status", "external_status", "last_at", "last_role", "last_user",
    "last_content", "last_error",
}
INTEGER_COLUMNS = {
    "chat_index", "pinned", "progress", "archived", "message_count",
    "pending_count", "last_done",
}


def _column_type(name: str) -> str:
    """SQLite column type of a projection field (id is the primary key)."""
    if name == "id":
        return "TEXT PRIMARY KEY"
    if name in TEXT_COLUMNS:
        return "TEXT"
    if name in INTEGER_COLUMNS:
        return "INTEGER"
    return "REAL"

CHAT_COLUMNS = [
    "id", "path", "name", "description", "board", "column_name", "status", "mode",
    "parent_id", "root_id", "owner_project_id", "project_id", "created_at",
    "updated_at", "chat_index", "pinned", "priority", "due_date", "progress",
    "tags", "profiles", "users", "assignees", "file_list", "sprints", "cost",
    "estimation_cost", "run_id", "run_status", "harness_status",
    "external_status", "archived", "message_count", "pending_count", "last_at",
    "last_role", "last_user", "last_content", "last_done", "last_error",
]


def is_chat_file(path: Path, tasks: Path) -> bool:
    """True when *path* is a real chat file (not catalogue/archive storage)."""
    try:
        parts = path.relative_to(tasks).parts
    except ValueError:
        return False
    if len(parts) != 3:
        return False
    if parts[0] in STORE_DIRS or parts[1] in STORE_DIRS:
        return False
    if parts[-1] in STORE_FILES:
        return False
    return parts[-1].endswith((".json", ".yaml"))


def chat_files(tasks: Path) -> List[Path]:
    """Return every chat file under *tasks* (including dot-prefixed names)."""
    return sorted(p for p in tasks.rglob("*") if p.is_file() and is_chat_file(p, tasks))


def parse_chat(path: Path) -> Tuple[Dict[str, Any] | None, str | None]:
    """
    Parse one chat file, recovering a valid prefix from a corrupt tail.

    Returns ``(document, error)``. ``document`` may still be returned when the
    file has a complete JSON prefix followed by garbage (the known corruption
    mode of the pre-atomic writer).
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), None
    except json.JSONDecodeError as exc:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            document, end = json.JSONDecoder().raw_decode(text)
            tail = len(text) - end
            return document, f"trailing {tail} bytes after JSON at char {end}"
        except Exception:  # noqa: BLE001 - diagnostics only
            return None, f"{exc}"
    except OSError as exc:
        return None, f"{exc}"


def percentile(values: List[float], fraction: float) -> float:
    """Nearest-rank percentile of *values* (already sorted or not)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def ms(seconds: float) -> str:
    """Human milliseconds."""
    return f"{seconds * 1000:.1f} ms"


def mb(size: int) -> str:
    """Human megabytes."""
    return f"{size / 1048576:.1f} MB"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def run_scan(tasks: Path) -> None:
    """Measure the store: inventory, parse cost, composition, payload size."""
    print(f"== Scan: {tasks} ==")
    started = time.perf_counter()
    paths = chat_files(tasks)
    walk = time.perf_counter() - started
    total_bytes = sum(p.stat().st_size for p in paths)
    print(f"files: {len(paths)}   bytes: {mb(total_bytes)}")
    print(f"walk (rglob + filter): {ms(walk)}")

    started = time.perf_counter()
    documents: List[Tuple[Path, Dict[str, Any]]] = []
    corrupt: List[Tuple[Path, str, bool]] = []
    for path in paths:
        document, error = parse_chat(path)
        if error:
            corrupt.append((path, error, document is not None))
        if document is not None and document.get("id"):
            documents.append((path, document))
    parse_time = time.perf_counter() - started
    recovered = sum(1 for _, _, ok in corrupt if ok)
    dropped = sum(1 for _, _, ok in corrupt if not ok)
    print(f"parsed: {len(documents)}   corrupt: {len(corrupt)} "
          f"(recovered prefix: {recovered}, dropped: {dropped})   "
          f"open+json.load all: {ms(parse_time)}")
    for path, error, ok in corrupt[:5]:
        print(f"  {'~' if ok else '!'} {path}: {error}")

    if not documents:
        return

    sizes = sorted(p.stat().st_size for p in paths)
    messages = [len(d.get("messages") or []) for _, d in documents]
    print(
        "size p50/p90/p99/max: "
        f"{sizes[len(sizes)//2]//1024} KB / "
        f"{sizes[int(len(sizes) * .9)]//1024} KB / "
        f"{sizes[int(len(sizes) * .99)]//1024} KB / "
        f"{sizes[-1]//1048576} MB"
    )
    print(
        f"messages: {sum(messages)} total, median {int(statistics.median(messages))}/chat, "
        f"max {max(messages)}; zero-message chats: {sum(1 for n in messages if n == 0)}"
    )

    # Composition: where do the bytes go?
    tool = content = meta = attachments = top = last = 0
    for _, document in documents:
        top += len(json.dumps({k: v for k, v in document.items() if k != "messages"}))
        visible = [m for m in document.get("messages") or []
                   if not m.get("unsent") and not m.get("hide")]
        if visible:
            last += len(json.dumps(visible[-1]))
        # Composition covers every message (hidden/unsent included): the bytes
        # are in the file and must be parsed even if the UI never shows them.
        for message in document.get("messages") or []:
            tool += len(json.dumps(message.get("tool_events") or []))
            content += len(json.dumps(message.get("content") or ""))
            meta += len(json.dumps(message.get("meta_data") or {}))
            attachments += len(json.dumps(message.get("attachments") or []))
    total = total_bytes or 1
    print("composition:")
    print(f"  top-level Chat fields: {mb(top):>9} ({top / total:6.1%})")
    print(f"  last visible message:  {mb(last):>9} ({last / total:6.1%})")
    print(f"  tool_events:           {mb(tool):>9} ({tool / total:6.1%})")
    print(f"  message content:       {mb(content):>9} ({content / total:6.1%})")
    print(f"  message meta_data:     {mb(meta):>9} ({meta / total:6.1%})")
    print(f"  attachments:           {mb(attachments):>9} ({attachments / total:6.1%})")

    # Duplicate ids are a rebuild hazard.
    ids: Dict[str, int] = {}
    for _, document in documents:
        ids[str(document.get("id"))] = ids.get(str(document.get("id")), 0) + 1
    duplicates = {key: count for key, count in ids.items() if count > 1}
    print(f"duplicate chat ids: {len(duplicates)}"
          + (f"  e.g. {list(duplicates)[:3]}" if duplicates else ""))


# ---------------------------------------------------------------------------
# Prototype index
# ---------------------------------------------------------------------------
def build_index(documents: Iterable[Tuple[Path, Dict[str, Any]]], db_path: Path,
                fts: bool = True) -> sqlite3.Connection:
    """Create *db_path* and load the metadata (+ optional contentless FTS)."""
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    placeholders = ",".join("?" for _ in CHAT_COLUMNS)
    ddl = ",\n".join(f"  {name} {_column_type(name)}" for name in CHAT_COLUMNS)
    db.executescript(f"""
        CREATE TABLE chats (
{ddl}
        );
        CREATE INDEX chats_updated ON chats(updated_at DESC);
        CREATE INDEX chats_board ON chats(board, column_name, chat_index);
        CREATE INDEX chats_status ON chats(status, updated_at DESC);
        CREATE TABLE search_docs (rid INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT, doc_id TEXT, field TEXT);
        CREATE VIRTUAL TABLE search_fts USING fts5(text, content='');
    """)
    rows = 0
    for path, document in documents:
        chat_id = str(document.get("id"))
        messages = document.get("messages") or []
        visible = [m for m in messages if not m.get("unsent") and not m.get("hide")]
        last = visible[-1] if visible else {}
        values = [
            chat_id, str(path), document.get("name"), document.get("description"),
            document.get("board"), document.get("column"), document.get("status"),
            document.get("mode"), document.get("parent_id"), document.get("root_id"),
            document.get("owner_project_id"), document.get("project_id"),
            document.get("created_at"), document.get("updated_at"),
            document.get("chat_index"), 1 if document.get("pinned") else 0,
            document.get("priority"), document.get("due_date"),
            document.get("progress"), json.dumps(document.get("tags") or []),
            json.dumps(document.get("profiles") or []),
            json.dumps(document.get("users") or []),
            json.dumps(document.get("assignees") or []),
            json.dumps(document.get("file_list") or []),
            json.dumps(document.get("sprints") or []),
            document.get("cost") or 0, document.get("estimation_cost"),
            document.get("run_id"), document.get("run_status"),
            document.get("harness_status"), document.get("external_status"),
            1 if document.get("archived") else 0, len(messages),
            sum(1 for m in messages if m.get("done") is False),
            last.get("created_at"), last.get("role"), last.get("user"),
            " ".join(str(last.get("content") or "").split())[:280],
            1 if last.get("done", True) else 0, last.get("error"),
        ]
        db.execute(f"INSERT OR REPLACE INTO chats VALUES ({placeholders})", values)
        if not fts:
            rows += 1
            continue
        # Chat-level searchable fields.
        for field, text in (
            ("name", document.get("name") or ""),
            ("description", document.get("description") or ""),
            ("history", " ".join(str(h.get("summary") or "")
                                 for h in (document.get("history") or []))),
            ("file_list", " ".join(document.get("file_list") or [])),
        ):
            if not str(text).strip():
                continue
            rid = db.execute(
                "INSERT INTO search_docs(chat_id, doc_id, field) VALUES (?,?,?)",
                (chat_id, None, field),
            ).lastrowid
            db.execute("INSERT INTO search_fts(rowid, text) VALUES (?,?)", (rid, str(text)))
        # Message-level searchable fields (never tool_events).
        for position, message in enumerate(messages):
            doc_id = str(message.get("doc_id") or position)
            for field, text in (("content", message.get("content") or ""),
                                ("think", message.get("think") or "")):
                if not str(text).strip():
                    continue
                rid = db.execute(
                    "INSERT INTO search_docs(chat_id, doc_id, field) VALUES (?,?,?)",
                    (chat_id, doc_id, field),
                ).lastrowid
                db.execute("INSERT INTO search_fts(rowid, text) VALUES (?,?)",
                           (rid, str(text)))
        rows += 1
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return db


def timed(db: sqlite3.Connection, label: str, sql: str,
          params: Tuple[Any, ...] = (), repeat: int = 5) -> None:
    """Run *sql* a few times and print the best wall time."""
    best = float("inf")
    rows = 0
    for _ in range(repeat):
        started = time.perf_counter()
        rows = len(db.execute(sql, params).fetchall())
        best = min(best, time.perf_counter() - started)
    print(f"  {label:<42} {ms(best):>10}  rows={rows}")


def run_index(tasks: Path, out: Path) -> None:
    """Build the prototype index and benchmark representative queries."""
    print(f"== Prototype index: {tasks} -> {out} ==")
    paths = chat_files(tasks)
    started = time.perf_counter()
    documents: List[Tuple[Path, Dict[str, Any]]] = []
    corrupt = recovered = 0
    seen: Dict[str, Path] = {}
    duplicates = 0
    for path in paths:
        document, error = parse_chat(path)
        if error:
            corrupt += 1
            recovered += int(document is not None)
        if document is None or not document.get("id"):
            continue
        chat_id = str(document["id"])
        if chat_id in seen:
            duplicates += 1
            continue
        seen[chat_id] = path
        documents.append((path, document))
    parse_time = time.perf_counter() - started
    print(f"parsed {len(documents)} chats (corrupt: {corrupt}, recovered prefix: "
          f"{recovered}, duplicate ids skipped: {duplicates}) in {ms(parse_time)}")

    started = time.perf_counter()
    db = build_index(documents, out)
    build_time = time.perf_counter() - started
    db_bytes = out.stat().st_size
    print(f"index build: {ms(build_time)}   db: {mb(db_bytes)}   "
          f"ratio: {db_bytes / max(1, sum(p.stat().st_size for p in paths)):.1%} of store")

    print("queries:")
    timed(db, "list 50 newest",
          "SELECT id,name FROM chats ORDER BY updated_at DESC LIMIT 50")
    top_board = db.execute(
        "SELECT board, column_name FROM chats GROUP BY board, column_name "
        "ORDER BY count(*) DESC LIMIT 1"
    ).fetchone() or ("", "")
    timed(db, "board/column page (200)",
          "SELECT id FROM chats WHERE board=? AND column_name=? "
          "ORDER BY chat_index DESC LIMIT 200",
          tuple(top_board))
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
    timed(db, "30-day window",
          "SELECT id FROM chats WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT 100",
          (cutoff,))
    timed(db, "count by status",
          "SELECT status, count(*) FROM chats GROUP BY status")
    timed(db, "id lookup", "SELECT path FROM chats WHERE id=?",
          (documents[0][1]["id"],))
    if db.execute("SELECT count(*) FROM search_fts").fetchone()[0]:
        timed(db, "FTS search 'chat'",
              "SELECT d.chat_id, count(*) c FROM search_fts f "
              "JOIN search_docs d ON d.rid=f.rowid WHERE search_fts MATCH ? "
              "GROUP BY d.chat_id ORDER BY c DESC LIMIT 20", ('"chat"',))
        timed(db, "FTS prefix 'kanb*'",
              "SELECT d.chat_id FROM search_fts f JOIN search_docs d ON d.rid=f.rowid "
              "WHERE search_fts MATCH ? LIMIT 20", ("kanb*",))
    db.close()
    print("\nnote: the prototype is rebuilt in one transaction and is not the "
          "proposed production schema (see 03-target-architecture.md).")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", default=".codx/tasks",
                        help="project tasks folder (default: .codx/tasks)")
    parser.add_argument("--out", default="/tmp/chat_index_bench.db",
                        help="prototype index path (default: /tmp/chat_index_bench.db)")
    parser.add_argument("command", nargs="?", choices=["scan", "index", "all"],
                        default="all", help="what to run (default: all)")
    args = parser.parse_args(argv)

    tasks = Path(args.tasks).expanduser().resolve()
    if not tasks.is_dir():
        parser.error(f"not a directory: {tasks}")

    if args.command in ("scan", "all"):
        run_scan(tasks)
    if args.command in ("index", "all"):
        if args.command == "all":
            print()
        run_index(tasks, Path(args.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
