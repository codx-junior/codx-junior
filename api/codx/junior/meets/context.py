"""
Task context of a meeting.

Meetings exist to review the status of work, so every profile answer and the
minutes get a bounded, fresh snapshot of the project's tasks: board, column,
status, progress, assignee, priority and due date. The snapshot is intentionally
small (a meeting answer must not carry a whole board of conversations) and is
rebuilt on demand, so a profile always sees the current state of the board.

The meeting can be scoped to a ``board`` and/or to explicit ``task_ids``. When
neither is set, the most recently updated open tasks of the project are used.
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Hard cap of tasks included in the meeting context (prompt budget).
MAX_TASKS = 60
#: Hard cap of task rows included in the minutes prompt.
MAX_MINUTES_TASKS = 40

_STATUS_ORDER = {
    "doing": 0,
    "in progress": 0,
    "todo": 1,
    "to do": 1,
    "onhold": 2,
    "on hold": 2,
    "done": 3,
    "rejected": 4,
}


def _text(value: Any) -> str:
    """Return a trimmed string (``None`` -> '')."""
    return "" if value is None else str(value).strip()


def _sort_key(task: Dict[str, Any]):
    """Prioritise active work, then recency."""
    status = _text(task.get("status")).lower()
    updated = _text(task.get("updated_at"))
    return (_STATUS_ORDER.get(status, 2), updated)


def _task_row(chat: Any) -> Dict[str, Any]:
    """Build the compact row of one task."""
    return {
        "id": _text(getattr(chat, "id", "")),
        "name": _text(getattr(chat, "name", "")),
        "description": _text(getattr(chat, "description", ""))[:600],
        "board": _text(getattr(chat, "board", "")),
        "column": _text(getattr(chat, "column", "")),
        "status": _text(getattr(chat, "status", "")),
        "priority": _text(getattr(chat, "priority", "")),
        "progress": int(getattr(chat, "progress", 0) or 0),
        "assignees": list(getattr(chat, "assignees", []) or []),
        "profiles": list(getattr(chat, "profiles", []) or []),
        "due_date": _text(getattr(chat, "due_date", "")),
        "updated_at": _text(getattr(chat, "updated_at", "")),
        "run_status": _text(getattr(chat, "run_status", "")),
    }


def task_snapshot(
    session: Any,
    *,
    board: Optional[str] = None,
    task_ids: Optional[List[str]] = None,
    include_done: bool = True,
    limit: int = MAX_TASKS,
) -> Dict[str, Any]:
    """
    Return the meeting's task snapshot.

    Args:
        session: Project ``CODXJuniorSession``.
        board: Board title to scope the snapshot (case insensitive).
        task_ids: Explicit tasks to include even when they are outside ``board``.
        include_done: Include closed tasks (they are shown as closed context).
        limit: Hard cap of tasks.

    Returns:
        ``{board, tasks, counts, generated_at}`` with task rows sorted by
        priority/status and recency.
    """
    wanted_board = _text(board).lower()
    wanted_ids = [str(item) for item in (task_ids or []) if item]
    rows: List[Dict[str, Any]] = []
    seen = set()
    try:
        chats = list(session.get_chat_manager().list_chats() or [])
    except Exception as ex:  # noqa: BLE001 - the meeting must survive a bad board
        logger.warning("Meeting: cannot load the task snapshot: %s", ex)
        chats = []

    for chat in chats:
        chat_id = _text(getattr(chat, "id", ""))
        if not chat_id or chat_id in seen:
            continue
        mode = _text(getattr(chat, "mode", "")).lower()
        if mode in {"channel", "meet"}:
            continue
        chat_board = _text(getattr(chat, "board", ""))
        if not chat_board:
            continue
        explicit = chat_id in wanted_ids
        if not explicit and wanted_board and chat_board.lower() != wanted_board:
            continue
        status = _text(getattr(chat, "status", "")).lower()
        if not include_done and status in {"done", "rejected"}:
            continue
        rows.append(_task_row(chat))
        seen.add(chat_id)

    rows.sort(key=_sort_key)
    rows = rows[: max(1, min(int(limit or MAX_TASKS), MAX_TASKS))]
    counts: Dict[str, int] = {}
    for row in rows:
        key = row["status"] or "todo"
        counts[key] = counts.get(key, 0) + 1
    return {
        "board": _text(board),
        "tasks": rows,
        "counts": counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _format_task_line(row: Dict[str, Any]) -> str:
    """Format one task as a compact prompt line."""
    parts = [f"- [{row.get('column') or '?'} / {row.get('status') or '?'}] {row.get('name')}"]
    details = []
    if row.get("priority"):
        details.append(f"priority={row['priority']}")
    if row.get("progress"):
        details.append(f"progress={row['progress']}%")
    if row.get("assignees"):
        details.append("assignees=" + ", ".join(row["assignees"]))
    if row.get("profiles"):
        details.append("profiles=" + ", ".join(row["profiles"]))
    if row.get("due_date"):
        details.append(f"due={row['due_date']}")
    if row.get("run_status"):
        details.append(f"run={row['run_status']}")
    if details:
        parts.append(f" ({'; '.join(details)})")
    if row.get("description"):
        snippet = " ".join(row["description"].split())[:220]
        parts.append(f"\n  {snippet}")
    return "".join(parts)


def format_task_context(snapshot: Optional[Dict[str, Any]], max_tasks: int = MAX_TASKS) -> str:
    """Render the snapshot as Markdown for a prompt."""
    if not snapshot or not snapshot.get("tasks"):
        scope = (snapshot or {}).get("board") or "the project"
        return f"No tasks were found for {scope}."
    board = snapshot.get("board") or "all boards"
    counts = snapshot.get("counts") or {}
    counts_text = ", ".join(f"{status}: {count}" for status, count in counts.items())
    lines = [
        f"Current task status ({board}) — {len(snapshot['tasks'])} task(s):"
        + (f" {counts_text}." if counts_text else ""),
    ]
    lines += [_format_task_line(row) for row in snapshot["tasks"][:max_tasks]]
    if len(snapshot["tasks"]) > max_tasks:
        lines.append(f"- ... {len(snapshot['tasks']) - max_tasks} more task(s) omitted")
    return "\n".join(lines)
