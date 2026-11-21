"""
Chat + AI
This module is responsible for chat persistence and management.

Besides the classic full-chat ``save_chat``, this module now exposes granular,
merge-safe message operations (``add_message``, ``update_message``,
``remove_message``) used to persist chat changes *during* an AI turn (e.g.
tool-usage notification messages, streamed partial responses) without
overwriting concurrent updates.

It also provides full-text search capabilities via the ``search_chats`` method,
delegating to the ``ChatSearcher`` class for filtering by time frame, keyword
matching, and pagination.

Merge strategy (per message, keyed by ``doc_id``):

    ```mermaid
    flowchart TD
        A[Incoming chat messages] --> C{doc_id exists in stored chat?}
        B[Stored chat messages] --> C
        C -->|No| D[Keep message]
        C -->|Yes| E{parse incoming.updated_at >= parse stored.updated_at?}
        E -->|Yes| F[Keep incoming]
        E -->|No| G[Keep stored]
        D --> H[Merged message list]
        F --> H
        G --> H
    ```

CHANGED — kanban storage v2:
    ``load_kanban`` / ``save_kanban`` / ``delete_kanban`` delegate to
    :class:`codx.junior.kanban.store.KanbanStore`, which keeps one file per board
    behind an ``fcntl`` lock and migrates the legacy single ``kanban.json``.
    Board/column **folders are immutable**: :meth:`ChatManager.get_chat_file`
    resolves the folder from the catalogue (``resolve_folders``) instead of
    using ``Chat.board``/``Chat.column`` directly, so renaming a board no longer
    moves a folder nor rewrites every task.

CHANGED — chat file discovery:
    :meth:`ChatManager.chat_paths` only returns real chat files
    (``<board>/<column>/<name>.<id>.json``). The storage files (``index.json``,
    ``kanban.json``, ``boards/<id>/board.json``) live in the same tree and must
    never be parsed as chats.

CHANGED — exact chat lookup:
    :meth:`ChatManager.find_by_id` matches the ``.<chat_id>.json`` suffix
    instead of ``chat_id in path``. The substring match returned the wrong chat
    whenever two ids shared a prefix, which broke ancestor resolution.

CHANGED — timestamp normalisation:
    ``updated_at`` values historically exist in TWO formats:
    ``str(datetime.now())`` (space separator, Message default) and
    ``datetime.isoformat()`` (T separator, stamped by add/update_message).
    Comparing them as raw strings is WRONG ("...T..." always beats "... ..."
    lexicographically for the same day). :func:`_parse_timestamp` parses both
    formats so the last-writer-wins decision uses real datetimes.

CHANGED — persist hot-path:
    The granular operations are now called frequently mid-run (tool events,
    throttled stream persists). :meth:`ChatManager._load_stored_chat` reads
    the chat directly from ``chat.file_path`` when possible instead of
    scanning the whole tasks tree via ``find_by_id``.

NEW — chat search:
    The :meth:`ChatManager.search_chats` method provides full-text search
    across all chat data with optional time-frame filtering, user filtering,
    and pagination, delegating to :class:`codx.junior.chat_searcher.ChatSearcher`.

NEW — chat filtering:
    The :meth:`ChatManager.get_recent_chats` method now supports filtering by
    user_id, board, column, and chat_type (task or chat).

CHANGED — project_ids filtering:
    The :meth:`ChatManager.search_chats` method now supports filtering by
    project_ids list. When no query is provided, returns latest chats from
    accessible user projects.

CHANGED — chat ID validation:
    The :meth:`ChatManager.load_chat_from_path` method now ensures all loaded
    chats have a valid ID. Missing IDs are extracted from the filename or
    generated fresh, guaranteeing no chat is returned without an ID.

CHANGED — time-range filtering:
    :meth:`ChatManager.chat_paths` and :meth:`ChatManager.list_chats` now
    accept an optional **upper bound** (``before_update`` / ``to_date``) in
    addition to the existing lower bound (``last_update`` / ``from_date``),
    enabling an explicit ``[from_date, to_date]`` window. Both bounds are
    applied against the chat file ``mtime``. When a bound is ``None`` it is
    ignored, so existing callers are unaffected.

NEW — accumulated chat cost (``Chat.cost``):
    ``Chat.cost`` accumulates the cxjcoins spent on a chat. It is updated here
    whenever a message carrying a cost is added or updated, using the persisted
    version of that message as the baseline (so a cost registered *after* the
    message was first persisted is still captured exactly once). The value is
    MONOTONIC — deleting a message never reduces it — so the chat budget stays
    accurate even when messages are removed.

NEW — cost roll-up to the root chat:
    The same delta is also added to the **root** chat of the tree
    (``Chat.root_id``, see :mod:`codx.junior.identity`). That makes the budget
    of a main task (``Chat.estimation_cost``) cover every descendant turn, which
    is what a long-running run needs: the run budget lives on the main task and
    the agent already stops a turn when ``Chat.cost`` reaches
    ``Chat.estimation_cost``.

NEW — chat status history (``Chat.status_history``):
    ``Chat.status`` was previously a single mutable field ("last writer wins"),
    making it impossible to reconstruct when a task moved between statuses. This
    module now appends a :class:`codx.junior.db.ChatStatusEvent` to
    ``Chat.status_history`` every time ``status`` actually changes (detected in
    :meth:`ChatManager.save_chat` via :meth:`ChatManager._register_status_change`).
    The log is append-only and merge-safe (dedup by ``doc_id``), giving future
    task-performance metrics (cycle time, throughput, error rate) the raw
    transition feed they need.
"""
import logging
import os
import json
import uuid
import shutil
import threading
import time

from slugify import slugify

from typing import Dict, List, Optional, Any

from datetime import datetime, timedelta
from codx.junior.settings import CODXJuniorSettings

from codx.junior.db import Chat, Message, ChatStatusEvent

from codx.junior.identity import resolve_root_id

from codx.junior.kanban.archive import TaskArchive
from codx.junior.kanban.model import DEFAULT_BOARD, DEFAULT_COLUMN, is_store_file
from codx.junior.kanban.store import KanbanStore

from codx.junior.profiling.profiler import profile_function

from codx.junior.chat.chat_export import ChatExport, ExportedDocument
from codx.junior.chat.chat_index import ChatIndexStore

from codx.junior.events.collection_events import (
    ACTION_CHANGED,
    ACTION_DELETED,
    COLLECTION_BOARDS,
    emit_collection_event
)
from codx.junior.events.event_manager import EventManager

from codx.junior.project.project_discover import find_project_by_id

from codx.junior.security.ciso.payload import redact_payload

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Chat metadata cache (the ``GET /api/chats`` hot path)
# -----------------------------------------------------------------------------
# Listing chats used to read and fully validate (pydantic) EVERY task file of
# the project on every request, just to return metadata: with a large project
# (hundreds of MB spread over the tasks tree) that took more than ten seconds
# while the client waited for its main screen. The cache keeps the parsed Chat
# keyed by ``(mtime_ns, size)``, so an unchanged file costs a single ``os.stat``
# and a rewritten file (``store_chat``) invalidates itself. Entries are always
# handed out as deep copies, because callers of ``list_chats`` are free to
# mutate the objects they receive.
_CHAT_INFO_CACHE: Dict[str, Any] = {}
_CHAT_INFO_CACHE_MAX = 5000
_CHAT_INFO_CACHE_LOCK = threading.Lock()

# Process-wide guards for index maintenance. A ChatManager is created per
# session/request in several code paths; without these, every request would
# start its own rebuild/reconcile for the same project.
_CHAT_INDEX_MAINTENANCE_LOCK = threading.Lock()
_CHAT_INDEX_BUILDING: set = set()
_CHAT_INDEX_RECONCILING: set = set()
_CHAT_INDEX_DIRTY: set = set()
_CHAT_INDEX_LAST_RECONCILE: Dict[str, float] = {}


def _chat_file_signature(file_path: str):
    """
    Return the cheap change signature of a chat file.

    :param file_path: Path of the chat file.
    :return: ``(mtime_ns, size)`` tuple, or ``None`` when the file cannot be
        stat'ed (missing/permission), which disables the cache for it.
    """
    try:
        stat = os.stat(file_path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def _chat_info_cache_get(file_path: str, signature) -> Optional[Chat]:
    """
    Return a cached copy of the chat metadata when the file is unchanged.

    :param file_path: Path of the chat file.
    :param signature: Current ``(mtime_ns, size)`` of the file.
    :return: A detached copy of the cached Chat, or ``None`` on a miss.
    """
    if signature is None:
        return None
    with _CHAT_INFO_CACHE_LOCK:
        entry = _CHAT_INFO_CACHE.get(file_path)
    if not entry or entry[0] != signature:
        return None
    return entry[1].model_copy(deep=True)


def _chat_info_cache_put(file_path: str, signature, chat: Chat) -> None:
    """
    Store the chat metadata for *file_path* under *signature*.

    :param file_path: Path of the chat file.
    :param signature: ``(mtime_ns, size)`` the chat was read with.
    :param chat: Chat whose metadata is reused by ``list_chats``.
    """
    if signature is None:
        return
    cached = chat.model_copy(deep=True)
    with _CHAT_INFO_CACHE_LOCK:
        if len(_CHAT_INFO_CACHE) >= _CHAT_INFO_CACHE_MAX:
            # Cheap bound: the cache is only an accelerator, rebuilding it
            # costs one full read of the tree and never loses data.
            _CHAT_INFO_CACHE.clear()
        _CHAT_INFO_CACHE[file_path] = (signature, cached)


# Execution states that mean the task did not finish cleanly. They are
# derived while a chat file is read and annotate the transient task-table
# fields on Chat (never persisted).
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

#: A task whose last activity is older than this is marked as "old" by the
#: Follow-up view (the UI still shows the exact age; this only drives the
#: visual emphasis and the `old` filter/badge).
OLD_TASK_DAYS = 7


def _task_visible_messages(messages: List[Message]) -> List[Message]:
    """Return the messages that belong to the visible task conversation."""
    return [
        message for message in (messages or [])
        if not getattr(message, "unsent", False)
        and not getattr(message, "hide", False)
    ]


def _task_latest_message(messages: List[Message]) -> Optional[Dict[str, Any]]:
    """Compact payload of the newest visible message (Follow-up preview)."""
    visible = _task_visible_messages(messages)
    if not visible:
        return None
    message = visible[-1]
    content = " ".join(str(getattr(message, "content", "") or "").split())
    if len(content) > 280:
        content = content[:277].rstrip() + "…"
    return {
        "doc_id": getattr(message, "doc_id", None),
        "role": getattr(message, "role", "") or "",
        "user": getattr(message, "user", "") or "",
        "content": content,
        "created_at": getattr(message, "created_at", None),
        "updated_at": getattr(message, "updated_at", None),
        "done": getattr(message, "done", True),
        "error": getattr(message, "error", None),
        "read_by": list(getattr(message, "read_by", None) or []),
    }


def _task_unread_count(messages: List[Message], username: Optional[str]) -> int:
    """
    Count unread assistant replies for *username*.

    Same rule as the client (``src/utils/chatUnread.js``): assistant replies by
    somebody else, not in ``read_by``, finished (``done != False``), visible.
    """
    if not username:
        return 0
    count = 0
    for message in _task_visible_messages(messages):
        if (getattr(message, "role", "") or "") != "assistant":
            continue
        if (getattr(message, "user", "") or "") == username:
            continue
        if username in (getattr(message, "read_by", None) or []):
            continue
        if getattr(message, "done", True) is False:
            continue
        count += 1
    return count


def _task_process_state(chat: Chat, messages: List[Message]) -> tuple:
    """
    Derive ``(state, error)`` for the task execution.

    ``run_status`` (orchestration), ``harness_status``/``external_status``
    (external execution) and the last assistant message are combined. The
    workflow ``status`` (todo/doing/done) is intentionally NOT used: a task can
    be "doing" without any process running and vice versa.
    """
    last_error = ""
    last_open = False
    for message in _task_visible_messages(messages):
        if (getattr(message, "role", "") or "") != "assistant":
            continue
        error = getattr(message, "error", None)
        # Only the LAST assistant message decides the current state; an old
        # failed answer followed by a successful one must not stay "error".
        last_error = str(error) if error else ""
        if getattr(message, "done", True) is False:
            last_open = True
        else:
            last_open = False

    harness_meta = getattr(chat, "harness_metadata", None) or {}
    meta_error = ""
    if isinstance(harness_meta, dict):
        meta_error = str(
            harness_meta.get("error")
            or harness_meta.get("error_message")
            or harness_meta.get("last_error")
            or ""
        ).strip()

    candidates = [
        ("run", getattr(chat, "run_status", None)),
        ("harness", getattr(chat, "harness_status", None)),
        ("external", getattr(chat, "external_status", None)),
    ]
    for label, raw in candidates:
        status = str(raw or "").strip().lower()
        if not status:
            continue
        if status in _PROCESS_ERROR_STATUSES:
            return "error", last_error or meta_error or f"{label} status: {status}"
        if status in _PROCESS_STOPPED_STATUSES:
            return "stopped", last_error or meta_error or f"{label} status: {status}"
        if status in _PROCESS_RUNNING_STATUSES:
            return "running", None
    if last_error:
        return "error", last_error
    if last_open:
        return "running", None
    if meta_error:
        return "error", meta_error
    return "idle", None


def _annotate_task_activity(chat: Chat, unread_for: Optional[str] = None) -> Chat:
    """Fill the transient Follow-up fields of *chat* before messages are cut."""
    messages = list(getattr(chat, "messages", None) or [])
    latest = _task_latest_message(messages)
    chat.message_count = len(messages)
    chat.pending_count = sum(1 for message in messages if getattr(message, "done", True) is False)
    for message in messages:
        images = getattr(message, "images", None) or []
        if images:
            first = images[0]
            if isinstance(first, str):
                chat.cover_image = first
            elif isinstance(first, dict):
                chat.cover_image = first.get("url") or first.get("path")
            if chat.cover_image:
                break
    chat.task_latest_message = latest
    chat.task_unread_count = _task_unread_count(messages, unread_for)
    chat.task_process_state, chat.task_process_error = _task_process_state(chat, messages)
    chat.task_last_activity_at = (
        (latest or {}).get("created_at")
        or (latest or {}).get("updated_at")
        or getattr(chat, "updated_at", None)
    )
    return chat


def _parse_timestamp(value: Optional[str]) -> datetime:
    """
    Parse a message timestamp that may be in either historical format.

    Handles both ``datetime.isoformat()`` ("2023-10-01T12:00:00") and
    ``str(datetime.now())`` ("2023-10-01 12:00:00") — ``fromisoformat``
    accepts both separators. Unparseable/missing values sort OLDEST so a
    real timestamp always wins over a missing one.

    :param value: Raw timestamp string (may be None/empty/invalid).
    :return: Parsed datetime, or ``datetime.min`` when unparseable.
    """
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.debug("Unparseable message timestamp: '%s'", value)
        return datetime.min


def _extract_id_from_filename(file_path: str) -> Optional[str]:
    """
    Extract chat ID from filename.

    Chat files follow the pattern: {slugified_name}.{uuid}.json
    This function extracts the UUID part.

    :param file_path: Full path to the chat file.
    :return: Extracted UUID or None if pattern doesn't match.
    """
    try:
        filename = os.path.basename(file_path)
        # Remove .json extension
        name_without_ext = filename.replace('.json', '').replace('.yaml', '')
        # Split by '.' and take the last part (should be the UUID)
        parts = name_without_ext.split('.')
        if len(parts) >= 2:
            potential_id = parts[-1]
            # Validate it looks like a UUID (36 chars with hyphens)
            if len(potential_id) == 36 and potential_id.count('-') == 4:
                return potential_id
    except Exception as ex:
        logger.warning("Failed to extract ID from filename '%s': %s", file_path, ex)
    return None


class ChatManager:
    """
    Manages chat persistence and retrieval for a given project.

    Provides full-chat save/load operations as well as granular, merge-safe
    message operations (``add_message``, ``update_message``, ``remove_message``)
    that are safe to call while an AI turn is in progress.

    Also provides full-text search via ``search_chats`` with user filtering,
    project_ids filtering, time-frame filtering and pagination support.

    Contract relied upon by :class:`codx.junior.chat.chat_event_bridge.ChatEventBridge`:
        * ``add_message`` inserts (idempotent: skips in-memory duplicate by ``doc_id``).
        * ``update_message`` matches by ``doc_id`` (appends if missing).
        * Both merge against the stored chat so concurrent updates are never lost.

    Contract relied upon for the chat budget:
        * ``add_message`` / ``update_message`` accumulate ``message.cost`` into
          ``chat.cost`` (monotonic) and into the ROOT chat of the tree.
          ``remove_message`` never reduces ``chat.cost``, so deleted messages
          still count towards the budget.

    Contract relied upon for task-performance metrics:
        * Every real ``Chat.status`` transition is appended to
          ``Chat.status_history`` by :meth:`save_chat` (via
          :meth:`_register_status_change`), producing an append-only,
          merge-safe transition log.
    """

    def __init__(self, settings: CODXJuniorSettings, event_manager: Optional[EventManager] = None):
        """
        Initialise the manager, creating the required directory structure.

        :param settings: Project-level settings.
        :param event_manager: Optional shared EventManager; a new one is
            created if not supplied.
        """
        self.settings = settings
        self.chat_path = f"{settings.codx_path}/tasks"
        self.event_manager = (
            event_manager if event_manager else EventManager(codx_path=settings.codx_path)
        )
        self.kanban_store = KanbanStore(tasks_path=self.chat_path)
        # Compressed storage of the archived tasks (tasks/.archive).
        self.task_archive = TaskArchive(tasks_path=self.chat_path)
        os.makedirs(self.chat_path, exist_ok=True)
        os.makedirs(f"{self.chat_path}/{DEFAULT_BOARD}/{DEFAULT_COLUMN}", exist_ok=True)
        # Derived read model (SQLite). Created lazily so a ChatManager that is
        # only used for a single write does not pay for a schema creation.
        self._chat_index: Optional[ChatIndexStore] = None
        self._chat_index_lock = threading.Lock()
        self._chat_index_build_started = False
        self._chat_index_build_error = 0.0
        self._chat_index_reconcile_started = False
        self._chat_index_last_reconcile = 0.0

    # -------------------------------------------------------------------------
    # Derived chat index
    # -------------------------------------------------------------------------
    @staticmethod
    def _chat_index_mode() -> str:
        """Return the configured index mode: ``off``, ``shadow`` or ``on``.

        ``CODX_JUNIOR_CHAT_INDEX`` accepts ``off/false/0``, ``shadow`` (build
        and compare, read files) and any other value (``on``/``auto``). The
        default is ``on``: reads use the index when it is ready and fall back to
        the files otherwise.
        """
        value = str(os.environ.get("CODX_JUNIOR_CHAT_INDEX", "on")).strip().lower()
        if value in ("0", "false", "no", "off", "disabled"):
            return "off"
        if value == "shadow":
            return "shadow"
        return "on"

    def _chat_index_db_path(self) -> str:
        """Absolute path of the project index database."""
        return os.path.join(
            str(self.settings.codx_path or self.chat_path), "index", "chats.db"
        )

    def _chat_index_store(self) -> Optional[ChatIndexStore]:
        """Return the lazily created index store (None when disabled)."""
        if self._chat_index_mode() == "off":
            return None
        with self._chat_index_lock:
            if self._chat_index is None:
                mode = str(
                    os.environ.get("CODX_JUNIOR_CHAT_INDEX_SEARCH", "contentless")
                ).strip().lower()
                db_path = self._chat_index_db_path()
                try:
                    self._chat_index = ChatIndexStore(
                        tasks_path=self.chat_path,
                        db_path=db_path,
                        search_mode=mode,
                    )
                except Exception as ex:  # pragma: no cover - defensive
                    logger.warning(
                        "Chat index unavailable for '%s': %s", self.chat_path, ex
                    )
                    self._chat_index = None
            return self._chat_index

    def _chat_index_ready(self) -> bool:
        """True when the index is built and can serve reads."""
        if self._chat_index_mode() not in ("on", "shadow"):
            return False
        store = self._chat_index_store()
        try:
            return bool(store and store.is_ready())
        except Exception:  # pragma: no cover - defensive
            return False

    def _start_chat_index_build(self) -> None:
        """Build the index once, in a daemon thread (never blocks a request)."""
        if self._chat_index_mode() not in ("on", "shadow"):
            return
        store = self._chat_index_store()
        if store is None or store.is_ready() or store.is_building():
            return
        key = os.path.abspath(self.chat_path)
        with _CHAT_INDEX_MAINTENANCE_LOCK:
            if key in _CHAT_INDEX_BUILDING:
                return
            # Do not hammer a broken build on every request.
            if self._chat_index_build_error and (
                time.time() - self._chat_index_build_error < 60
            ):
                return
            _CHAT_INDEX_BUILDING.add(key)
        thread = threading.Thread(
            target=self._build_chat_index,
            name=f"chat-index-{getattr(self.settings, 'project_id', 'project')}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as ex:  # pragma: no cover - extremely unlikely
            with _CHAT_INDEX_MAINTENANCE_LOCK:
                _CHAT_INDEX_BUILDING.discard(key)
            logger.warning("Cannot start chat index build for '%s': %s", self.chat_path, ex)

    def _build_chat_index(self) -> None:
        """Parse every chat file and replace the index (background)."""
        try:
            store = self._chat_index_store()
            if store is None:
                return
            count = store.rebuild(self._iter_chat_documents())
            logger.info(
                "Chat index ready for '%s': %d chats", self.chat_path, count
            )
        except Exception as ex:  # pragma: no cover - defensive
            self._chat_index_build_error = time.time()
            logger.exception("Chat index build failed for '%s': %s", self.chat_path, ex)
        finally:
            self._chat_index_build_started = False
            key = os.path.abspath(self.chat_path)
            dirty = False
            with _CHAT_INDEX_MAINTENANCE_LOCK:
                _CHAT_INDEX_BUILDING.discard(key)
                dirty = key in _CHAT_INDEX_DIRTY
                _CHAT_INDEX_DIRTY.discard(key)
                if dirty:
                    _CHAT_INDEX_LAST_RECONCILE[key] = 0.0
            if dirty:
                self._maybe_start_reconcile()

    def _iter_chat_documents(self):
        """Yield ``(path, document)`` for every parseable chat file.

        Corrupt files are recovered from their valid JSON prefix when possible
        (the store contains two such files) and annotated with
        ``__parse_error__``; they are never silently dropped.
        """
        for file_path in self.chat_paths():
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    document = json.load(handle)
            except json.JSONDecodeError as ex:
                try:
                    with open(file_path, "r", encoding="utf-8") as handle:
                        document, _ = json.JSONDecoder().raw_decode(handle.read())
                    document["__parse_error__"] = str(ex)
                except Exception:
                    logger.warning("Chat index: cannot parse %s: %s", file_path, ex)
                    continue
            except OSError as ex:
                logger.warning("Chat index: cannot read %s: %s", file_path, ex)
                continue
            if isinstance(document, dict) and document.get("id"):
                yield file_path, document

    def _index_upsert_payload(self, payload: Dict[str, Any], file_path: str) -> None:
        """Write one chat to the index; never fail the canonical write."""
        if self._chat_index_mode() == "off" or not payload:
            return
        key = os.path.abspath(self.chat_path)
        with _CHAT_INDEX_MAINTENANCE_LOCK:
            # A full rebuild owns the database for several seconds. Do not
            # block a user write behind it: remember that the file changed and
            # reconcile once the rebuild finishes (the file is the truth).
            if key in _CHAT_INDEX_BUILDING:
                _CHAT_INDEX_DIRTY.add(key)
                return
        try:
            store = self._chat_index_store()
            if store is None:
                return
            if store.is_building():
                _CHAT_INDEX_DIRTY.add(key)
                _CHAT_INDEX_LAST_RECONCILE[key] = 0.0
                return
            store.upsert_chat(payload, file_path)
            # First write of a project: build the rest of the index in the
            # background so the next read is already fast.
            if not store.is_ready():
                self._start_chat_index_build()
        except Exception as ex:  # pragma: no cover - defensive
            _CHAT_INDEX_DIRTY.add(key)
            logger.warning("Chat index upsert failed for %s: %s", file_path, ex)

    def _mark_index_dirty_if_building(self) -> None:
        """Remember a change that a full rebuild may have missed."""
        key = os.path.abspath(self.chat_path)
        with _CHAT_INDEX_MAINTENANCE_LOCK:
            if key in _CHAT_INDEX_BUILDING:
                _CHAT_INDEX_DIRTY.add(key)

    def _wait_for_index_idle(self, timeout: float = 60.0) -> None:
        """Wait (bounded) for a rebuild to finish before a rare mutation.

        Archive/restore are not hot paths, but they change which file exists.
        Waiting avoids a rebuild that is already reading the tree re-adding the
        row we are about to delete (or missing the file we just restored).
        """
        deadline = time.time() + max(0.0, timeout)
        key = os.path.abspath(self.chat_path)
        while time.time() < deadline:
            with _CHAT_INDEX_MAINTENANCE_LOCK:
                building = key in _CHAT_INDEX_BUILDING
            store = self._chat_index
            if not building and not (store is not None and store.is_building()):
                return
            time.sleep(0.05)

    def _index_delete(self, chat_id: Optional[str]) -> None:
        """Remove a chat from the index (best effort)."""
        if not chat_id or self._chat_index_mode() == "off":
            return
        key = os.path.abspath(self.chat_path)
        with _CHAT_INDEX_MAINTENANCE_LOCK:
            if key in _CHAT_INDEX_BUILDING:
                _CHAT_INDEX_DIRTY.add(key)
                return
        try:
            store = self._chat_index_store()
            if store is not None:
                if store.is_building():
                    _CHAT_INDEX_DIRTY.add(key)
                    _CHAT_INDEX_LAST_RECONCILE[key] = 0.0
                    return
                store.delete_chat(str(chat_id))
        except Exception as ex:  # pragma: no cover - defensive
            _CHAT_INDEX_DIRTY.add(key)
            logger.warning("Chat index delete failed for %s: %s", chat_id, ex)

    def _chat_from_index(self, row: Dict[str, Any]) -> Chat:
        """Build a slim ``Chat`` from an index projection row.

        Only the last message is attached (as a 280-char preview); the full
        conversation is still loaded from the file by
        :meth:`load_chat_from_path` when a client opens the chat.
        """
        data: Dict[str, Any] = {}
        for name in Chat.model_fields:
            # Drop NULLs: a missing file field must fall back to the model
            # default instead of failing pydantic validation.
            if row.get(name) is not None:
                data[name] = row[name]
        # The index stores the board column as ``column_name`` (SQL keyword
        # safety); the model field is ``column``.
        data["column"] = row.get("column_name") or row.get("column") or ""
        last = row.get("last_message") or row.get("task_latest_message")
        if last:
            try:
                data["messages"] = [Message.model_validate(last)]
            except Exception:
                data["messages"] = []
        else:
            data["messages"] = []
        chat = Chat.model_validate(data)
        chat.file_path = row.get("path") or chat.file_path
        if not chat.owner_project_id:
            chat.owner_project_id = self.settings.project_id
        chat.message_count = int(row.get("message_count") or 0)
        chat.pending_count = int(row.get("pending_count") or 0)
        chat.cover_image = row.get("cover_image")
        chat.task_latest_message = last
        chat.task_unread_count = int(row.get("unread_count") or 0)
        chat.task_process_state = row.get("process_state") or "idle"
        chat.task_process_error = row.get("process_error")
        chat.task_last_activity_at = row.get("last_activity_at")
        return chat

    def _chat_index_search(
        self,
        query: str,
        user_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Search this project's index, or None when it is not ready."""
        if self._chat_index_mode() != "on":
            return None
        if not self._chat_index_ready():
            self._start_chat_index_build()
            return None
        store = self._chat_index_store()
        if store is None:
            return None
        try:
            return store.search(
                query=query,
                user_id=user_id,
                from_date=from_date,
                to_date=to_date,
                filters=filters,
                page=page,
                page_size=page_size,
            )
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index search failed for '%s': %s", self.chat_path, ex)
            return None

    def _maybe_start_reconcile(self) -> None:
        """Reconcile the index with external file changes at most every 60 s.

        Local writes are write-through; this catches ``git checkout``, editor
        changes and deletions made outside the API. It runs in a daemon thread
        and never blocks a request.
        """
        if self._chat_index_mode() not in ("on", "shadow") or not self._chat_index_ready():
            return
        store = self._chat_index_store()
        if store is not None and store.is_building():
            return
        now = time.time()
        key = os.path.abspath(self.chat_path)
        with _CHAT_INDEX_MAINTENANCE_LOCK:
            if key in _CHAT_INDEX_RECONCILING:
                return
            if now - _CHAT_INDEX_LAST_RECONCILE.get(key, 0.0) < 60:
                return
            _CHAT_INDEX_RECONCILING.add(key)
        thread = threading.Thread(
            target=self._reconcile_chat_index,
            name=f"chat-index-reconcile-{getattr(self.settings, 'project_id', 'project')}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as ex:  # pragma: no cover - extremely unlikely
            with _CHAT_INDEX_MAINTENANCE_LOCK:
                _CHAT_INDEX_RECONCILING.discard(key)
            logger.warning("Cannot start chat index reconcile for '%s': %s", self.chat_path, ex)

    def _reconcile_chat_index(self) -> None:
        """Stat the task tree, reindex changed/new files, drop missing ones."""
        try:
            store = self._chat_index_store()
            if store is None:
                return
            current: Dict[str, str] = {}
            for path in self.chat_paths():
                try:
                    stat = os.stat(path)
                    current[path] = f"{stat.st_mtime_ns}:{stat.st_size}"
                except OSError:
                    continue
            indexed = store.signatures()
            changed = [
                path for path, sig in current.items()
                if indexed.get(path) != sig
            ]
            for path in changed:
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        document = json.load(handle)
                except json.JSONDecodeError as ex:
                    try:
                        with open(path, "r", encoding="utf-8") as handle:
                            document, _ = json.JSONDecoder().raw_decode(handle.read())
                        document["__parse_error__"] = str(ex)
                    except Exception:
                        logger.warning("Chat reconcile: cannot parse %s", path)
                        continue
                except OSError:
                    continue
                if isinstance(document, dict) and document.get("id"):
                    store.upsert_chat(document, path)
            known = {path: chat_id for chat_id, path in store.paths().items()}
            for path in set(known) - set(current):
                store.delete_chat(known[path])
            store.set_meta("last_reconcile_at", datetime.now().isoformat())
            if changed or set(known) - set(current):
                logger.info(
                    "Chat index reconciled for '%s': %d changed/new, %d removed",
                    self.chat_path,
                    len(changed),
                    len(set(known) - set(current)),
                )
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index reconcile failed for '%s': %s", self.chat_path, ex)
        finally:
            self._chat_index_last_reconcile = time.time()
            self._chat_index_reconcile_started = False
            with _CHAT_INDEX_MAINTENANCE_LOCK:
                _CHAT_INDEX_LAST_RECONCILE[os.path.abspath(self.chat_path)] = time.time()
                _CHAT_INDEX_RECONCILING.discard(os.path.abspath(self.chat_path))

    # -------------------------------------------------------------------------
    # Per-user drafts (index-backed; no chat file rewrite)
    # -------------------------------------------------------------------------
    def get_chat_draft(self, chat_id: str, user: str) -> Optional[Dict[str, Any]]:
        """Return a user's composer draft (None when the index is disabled)."""
        store = self._chat_index_store()
        if store is None or self._chat_index_mode() == "off":
            return None
        try:
            return store.get_draft(chat_id, user)
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index draft read failed for %s: %s", chat_id, ex)
            return None

    def save_chat_draft(
        self,
        chat_id: str,
        user: str,
        content: str,
        doc_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Persist a user's composer draft without touching the chat file."""
        store = self._chat_index_store()
        if store is None or self._chat_index_mode() == "off":
            return None
        try:
            return store.save_draft(chat_id, user, content, doc_id=doc_id)
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index draft write failed for %s: %s", chat_id, ex)
            return None

    def delete_chat_draft(self, chat_id: str, user: str) -> Optional[bool]:
        """Delete a user's composer draft (None when the index is disabled)."""
        store = self._chat_index_store()
        if store is None or self._chat_index_mode() == "off":
            return None
        try:
            return store.delete_draft(chat_id, user)
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index draft delete failed for %s: %s", chat_id, ex)
            return None

    # -------------------------------------------------------------------------
    # Path helpers
    # -------------------------------------------------------------------------

    def get_chat_file(self, chat: Chat) -> str:
        """
        Build the canonical file path for *chat*.

        The path is derived from the **immutable folders** of the board and the
        column (resolved from the catalogue through
        :meth:`KanbanStore.resolve_folders`), not from their titles: renaming a
        board or a column therefore keeps every task where it is.

        :param chat: Chat whose path to compute.
        :return: Absolute file path string.
        """
        board_folder, column_folder = self.kanban_store.resolve_folders(
            board=chat.board or DEFAULT_BOARD,
            column=chat.column or DEFAULT_COLUMN,
        )
        return (
            f"{self.chat_path}/{board_folder}/{column_folder}"
            f"/{slugify(chat.name)}.{chat.id}.json"
        )

    @staticmethod
    def _is_chat_file(file_path: str) -> bool:
        """
        Tell whether *file_path* is a stored chat.

        A chat always lives at ``<tasks>/<board folder>/<column folder>/<file>``.
        The catalogue lives in the same tree (``index.json``, the legacy
        ``kanban.json`` and ``boards/<id>/board.json``), so it must be filtered
        out — otherwise :meth:`list_chats` would try to parse a board as a chat.

        :param file_path: Candidate path.
        :return: True when the path is a chat file.
        """
        if is_store_file(file_path):
            return False
        return True

    def chat_paths(
        self,
        last_update: Optional[datetime] = None,
        before_update: Optional[datetime] = None,
    ) -> List[str]:
        """
        Return chat file paths, optionally filtering by last update time.

        CHANGED — time-range support:
            An optional upper bound ``before_update`` was added so callers can
            request an explicit ``[last_update, before_update]`` window. Both
            bounds are compared against the chat file ``mtime``. A ``None``
            bound is ignored, preserving the previous behavior.

        CHANGED — storage files are excluded:
            Only ``<board>/<column>/<slug>.<id>.json|yaml`` paths are returned
            (see :meth:`_is_chat_file`), so the kanban catalogue is never read
            as a chat.

        :param last_update: Only return paths for chats updated **after** this
            date (exclusive lower bound).
        :param before_update: Only return paths for chats updated **on or
            before** this date (inclusive upper bound).
        :return: List of file paths.
        """
        import pathlib

        all_paths = [
            str(path)
            for suffix in ("*.yaml", "*.json")
            for path in pathlib.Path(self.chat_path).rglob(suffix)
        ]
        all_paths = [path for path in all_paths if self._is_chat_file(path)]

        if last_update:
            all_paths = [
                path for path in all_paths
                if datetime.fromtimestamp(os.path.getmtime(path)) > last_update
            ]

        if before_update:
            all_paths = [
                path for path in all_paths
                if datetime.fromtimestamp(os.path.getmtime(path)) <= before_update
            ]

        return all_paths

    def chat_board_column_name_from_path(
        self, file_path: str
    ) -> tuple:
        """
        Parse board, column and name from a chat file path.

        :param file_path: Absolute path to a chat file.
        :return: Tuple of (board, column, name) or (None, None, None).
        """
        chat_parts = file_path.replace(self.chat_path, "").split("/")[1:]
        if len(chat_parts) != 3:
            return None, None, None

        name = chat_parts[-1].replace(".md", "")
        column = chat_parts[-2]
        board = chat_parts[-3]
        return board, column, name

    # -------------------------------------------------------------------------
    # Chat listing
    # -------------------------------------------------------------------------

    def get_recent_chats(
        self,
        user_id: Optional[str] = None,
        board: Optional[str] = None,
        column: Optional[str] = None,
        chat_type: Optional[str] = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 10,
    ) -> Dict[str, Any]:
        """
        Get recent chats with optional filtering and pagination.

        Loads all chats and filters by user, board, column, and/or chat type
        (if provided), then sorts by recency and paginates.

        :param user_id: Optional user ID to filter chats (only chats where
            user created or participated). If None, returns all recent chats.
        :param board: Optional board name filter.
        :param column: Optional column name filter.
        :param chat_type: Optional chat type filter ('task' or 'chat').
        :param include_archived: Also list the archived (compressed) tasks.
            They are metadata only: their messages stay inside the archive
            payload until the task is restored.
        :param page: Page number (1-indexed).
        :param page_size: Number of results per page.
        :return: Dict with paginated results and metadata.
        """
        # Fast path: the index applies user/board/column/type filters in SQL
        # and returns the page projection (no file parse).
        if (
            self._chat_index_mode() == "on"
            and self._chat_index_ready()
            and not include_archived
        ):
            try:
                store = self._chat_index_store()
                total = store.count_chats(
                    include_archived=False,
                    user_id=user_id,
                    board=board,
                    column=column,
                    chat_type=chat_type,
                ) if store is not None else 0
                rows = store.list_chats(
                    user_id=user_id,
                    board=board,
                    column=column,
                    chat_type=chat_type,
                    include_archived=False,
                    unread_for=user_id,
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ) if store is not None else []
                chats = [self._chat_from_index(row) for row in rows]
                total_pages = (total + page_size - 1) // page_size if total else 0
                return {
                    "chats": chats,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                }
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning(
                    "Chat index recent failed for '%s', falling back: %s",
                    self.chat_path,
                    ex,
                )
        elif self._chat_index_mode() == "on":
            self._start_chat_index_build()

        all_chats = self.list_chats(
            include_archived=include_archived,
            unread_for=user_id,
        )

        # Apply filters
        filtered_chats = all_chats
        
        if user_id:
            filtered_chats = [
                chat for chat in filtered_chats
                if self._user_involved_in_chat(chat, user_id)
            ]
        
        if board:
            filtered_chats = [
                chat for chat in filtered_chats
                if chat.board and chat.board.lower() == board.lower()
            ]
        
        if column:
            filtered_chats = [
                chat for chat in filtered_chats
                if chat.column and chat.column.lower() == column.lower()
            ]
        
        if chat_type:
            filtered_chats = [
                chat for chat in filtered_chats
                if self._matches_chat_type(chat, chat_type)
            ]

        # Sort by recency (updated_at descending)
        sorted_chats = sorted(
            filtered_chats,
            key=lambda c: _parse_timestamp(c.updated_at),
            reverse=True,
        )

        # Calculate pagination
        total = len(sorted_chats)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_chats = sorted_chats[start_idx:end_idx]

        return {
            "chats": paginated_chats,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }

    @staticmethod
    def _user_involved_in_chat(chat: Chat, user_id: str) -> bool:
        """
        Check if a user is involved in a chat (created, participated, or mentioned).

        :param chat: The chat to check.
        :param user_id: The user ID to look for.
        :return: True if user is involved in chat.
        """
        if not chat or not user_id:
            return False

        # Chat has no ``created_by`` field (the old code assumed one and raised
        # AttributeError for every chat). Participation is expressed by the
        # users/assignees lists, the profiles and the messages.
        if user_id in (getattr(chat, "users", None) or []):
            return True
        if user_id in (getattr(chat, "assignees", None) or []):
            return True

        # Profiles have historically been either names or {id,user_id} dicts.
        for profile in getattr(chat, "profiles", None) or []:
            if isinstance(profile, dict):
                if user_id in (profile.get("id"), profile.get("user_id")):
                    return True
            elif str(profile) == str(user_id):
                return True

        # Check if user has messages in chat (``user`` is the model field;
        # ``created_by``/``user_id`` were never fields on Message).
        for message in getattr(chat, "messages", None) or []:
            if not message:
                continue
            if getattr(message, "user", None) == user_id:
                return True
            if getattr(message, "created_by", None) == user_id:
                return True

        return False

    @staticmethod
    def _matches_chat_type(chat: Chat, chat_type: str) -> bool:
        """
        Check if a chat matches the specified type.

        :param chat: The chat to check.
        :param chat_type: The type to match ('task' or 'chat').
        :return: True if chat matches the type.
        """
        if not chat or not chat_type:
            return True
        
        chat_type = chat_type.lower()
        
        # Determine chat type based on attributes
        if chat_type == 'task':
            return bool(chat.board or chat.column)
        elif chat_type == 'chat':
            return not (chat.board or chat.column)
        
        return True

    def list_chats(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        include_archived: bool = False,
        unread_for: Optional[str] = None,
    ) -> List[Chat]:
        """
        Return a sorted list of all chats (metadata only, no messages).

        CHANGED — time-range filtering:
            An optional upper bound ``to_date`` was added, turning the previous
            single lower-bound filter into a full ``[from_date, to_date]``
            window. Both bounds are optional; when omitted no filtering is
            applied on that side (backward compatible). Invalid date strings
            raise :class:`ValueError` (propagated to the caller).

        :param from_date: ISO-format date string; only chats modified after
            this date are returned (lower bound).
        :param to_date: ISO-format date string; only chats modified on or
            before this date are returned (inclusive upper bound).
        :param include_archived: Also list the archived (compressed) tasks.
            They are metadata only (``archived=True``), because their messages
            live inside the archive payload, not in the tasks tree.
        :param unread_for: Optional username used to compute the transient
            ``task_unread_count`` annotation (Follow-up view).
        :return: List of Chat objects sorted by index/updated_at descending.
        """
        last_update = datetime.fromisoformat(from_date) if from_date else None
        before_update = datetime.fromisoformat(to_date) if to_date else None

        # Fast path: the derived index already has the projection (metadata +
        # last-message preview + unread). No chat file is parsed here.
        if self._chat_index_mode() == "on" and self._chat_index_ready():
            self._maybe_start_reconcile()
            try:
                store = self._chat_index_store()
                rows = store.list_chats(
                    from_date=from_date,
                    to_date=to_date,
                    include_archived=include_archived,
                    unread_for=unread_for,
                ) if store is not None else []
                chats = [self._chat_from_index(row) for row in rows]
                if include_archived:
                    chats = chats + self.archived_chats()
                return sorted(
                    chats,
                    key=lambda x: str(x.chat_index or x.updated_at),
                    reverse=True,
                )
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning(
                    "Chat index list failed for '%s', falling back to files: %s",
                    self.chat_path,
                    ex,
                )
        else:
            self._start_chat_index_build()

        file_paths = self.chat_paths(
            last_update=last_update,
            before_update=before_update,
        )

        def _load_chat_info(file_path: str) -> Optional[Chat]:
            try:
                signature = _chat_file_signature(file_path)
                # The unread count is user-scoped, so the cache key includes the
                # reader; metadata-only callers keep the shared entry.
                cache_key = f"{file_path}::unread::{unread_for}" if unread_for else file_path
                cached = _chat_info_cache_get(cache_key, signature)
                if cached is not None:
                    return cached
                chat = self.load_chat_from_path(
                    chat_file=file_path,
                    chat_only=True,
                    unread_for=unread_for,
                )
                # Only cache when the file did not change while it was being
                # read, otherwise the stale signature would hide the update.
                if chat is not None and _chat_file_signature(file_path) == signature:
                    _chat_info_cache_put(cache_key, signature, chat)
                return chat
            except (OSError, ValueError, KeyError) as ex:
                # ValueError covers json.JSONDecodeError and pydantic validation
                logger.error("Error loading chat '%s': %s", file_path, ex)
            return None

        chats = [c for c in (_load_chat_info(p) for p in file_paths) if c]
        chats = sorted(chats, key=lambda x: str(x.chat_index or x.updated_at), reverse=True)
        if not include_archived:
            return chats
        # Archived tasks have no file in the live tree: they are merged from
        # the archive catalogue (metadata only, nothing is decompressed).
        return sorted(
            chats + self.archived_chats(),
            key=lambda x: str(x.updated_at or ""),
            reverse=True,
        )

    # -------------------------------------------------------------------------
    # Chat search
    # -------------------------------------------------------------------------

    def search_chats(
        self,
        query: str,
        user_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 20,
        user: Optional[object] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search chats with full-text search, user filtering, project filtering,
        and pagination.

        When no query is provided and project_ids is specified, returns the
        latest chats from those projects (sorted by recency).

        Delegates to :class:`codx.junior.chat_searcher.ChatSearcher` for
        full-text search across chat data. Results can be filtered by user_id
        (chats where user created or participated) and project_ids list.

        :param query: Search query string (case-insensitive substring matching).
            If empty and project_ids is provided, returns latest chats instead.
        :param user_id: Optional user ID to filter search results to only chats
            where this user created or participated. If None, searches all chats.
        :param from_date: ISO-format date string; only include chats updated
            after this date (inclusive).
        :param to_date: ISO-format date string; only include chats updated
            before this date (inclusive).
        :param project_ids: Optional list of project IDs to filter chats by.
            If None, all projects are searched. If empty list, no chats are returned.
        :param page: Page number (1-indexed).
        :param page_size: Number of results per page.
        :param user: Optional CodxUser object for permission-based project loading.
            If provided and project_ids is None, loads all user's accessible projects.
        :return: Dict with paginated results and metadata.
        """
        from codx.junior.project.project_discover import find_all_user_projects

        # Determine which projects to search
        projects_to_search = []
        
        if project_ids is not None:
            # Never trust caller-supplied project ids blindly: intersect them
            # with the projects the authenticated user can access. Without a
            # user (demo / auth disabled) the old behaviour is preserved.
            allowed_ids = None
            if user is not None:
                try:
                    allowed_ids = {
                        str(project.project_id)
                        for project in find_all_user_projects(user)
                    }
                except Exception as ex:  # pragma: no cover - defensive
                    logger.warning("search_chats: cannot resolve accessible projects: %s", ex)
                    allowed_ids = set()
            for proj_id in project_ids:
                if allowed_ids is not None and str(proj_id) not in allowed_ids:
                    logger.warning(
                        "search_chats: project '%s' is not accessible to the user",
                        proj_id,
                    )
                    continue
                proj = find_project_by_id(proj_id)
                if proj:
                    projects_to_search.append(proj)
        elif user is not None:
            # Load all user's accessible projects
            try:
                user_projects = list(find_all_user_projects(user))
                projects_to_search = [p for p in user_projects]
            except Exception as ex:
                logger.warning(
                    "search_chats: failed to load user projects for user '%s': %s",
                    user,
                    ex,
                )
                # Fall back to current project only
                projects_to_search = [self.settings]
        else:
            # Default: search current project
            projects_to_search = [self.settings]

        if not projects_to_search:
            logger.warning(
                "search_chats: no projects to search for user_id='%s' project_ids=%s",
                user_id,
                project_ids,
            )
            return {
                "results": [],
                "total": 0,
                "page": 1,
                "page_size": page_size,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            }

        query_text = str(query or "").strip()

        # Fast path: each project's index searches its whole history (the file
        # path can only search the last message, because list_chats truncates).
        if query_text:
            from codx.junior.chat_searcher import ChatSearcher, SearchFilters

            # Single-project search (the common case): let the index paginate
            # directly, without the cross-project merge cap.
            if len(projects_to_search) == 1:
                single = ChatManager(
                    settings=projects_to_search[0], event_manager=self.event_manager
                )
                direct = single._chat_index_search(
                    query=query,
                    user_id=user_id,
                    from_date=from_date,
                    to_date=to_date,
                    filters=filters,
                    page=page,
                    page_size=page_size,
                )
                if direct is not None:
                    return direct

            index_results: List[Dict[str, Any]] = []
            index_total = 0
            fallback_chats: List[Chat] = []
            used_index = False
            for proj_settings in projects_to_search:
                try:
                    proj_manager = ChatManager(
                        settings=proj_settings, event_manager=self.event_manager
                    )
                    result = proj_manager._chat_index_search(
                        query=query,
                        user_id=user_id,
                        from_date=from_date,
                        to_date=to_date,
                        filters=filters,
                        page=1,
                        page_size=1000,
                    )
                    if result is not None:
                        used_index = True
                        index_total += int(result.get("total") or 0)
                        index_results.extend(result.get("results") or [])
                        continue
                    prefiltered = proj_manager.list_chats()
                    if user_id:
                        prefiltered = [
                            chat for chat in prefiltered
                            if self._user_involved_in_chat(chat, user_id)
                        ]
                    fallback_chats.extend(prefiltered)
                except Exception as ex:
                    logger.warning(
                        "search_chats: index search failed for '%s': %s",
                        proj_settings.project_name,
                        ex,
                    )

            if used_index or fallback_chats:
                if fallback_chats:
                    legacy = ChatSearcher().search(
                        chats=fallback_chats,
                        query=query,
                        from_date=from_date,
                        to_date=to_date,
                        page=1,
                        page_size=1000,
                        filters=SearchFilters.from_dict(filters),
                    )
                    index_total += int(legacy.get("total") or 0)
                    index_results.extend(legacy.get("results") or [])
                if index_results:
                    index_results.sort(
                        key=lambda item: (
                            float(item.get("relevance_score") or 0),
                            str((item.get("chat") or {}).get("updated_at") or ""),
                        ),
                        reverse=True,
                    )
                total = index_total
                total_pages = (total + page_size - 1) // page_size if total else 0
                start = max(0, (page - 1) * page_size)
                return {
                    "results": index_results[start:start + page_size],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                }

        # Load chats from all accessible projects
        all_project_chats: List[Chat] = []
        for proj_settings in projects_to_search:
            try:
                proj_manager = ChatManager(settings=proj_settings, event_manager=self.event_manager)
                proj_chats = proj_manager.list_chats()
                all_project_chats.extend(proj_chats)
            except Exception as ex:
                logger.warning(
                    "search_chats: failed to load chats from project '%s': %s",
                    proj_settings.project_name,
                    ex,
                )

        # Apply user_id filter
        if user_id:
            all_project_chats = [
                chat for chat in all_project_chats
                if self._user_involved_in_chat(chat, user_id)
            ]

        # If no query provided, return latest chats (already filtered by projects)
        if not query or not query.strip():
            sorted_chats = sorted(
                all_project_chats,
                key=lambda c: _parse_timestamp(c.updated_at),
                reverse=True,
            )
            total = len(sorted_chats)
            total_pages = (total + page_size - 1) // page_size if total > 0 else 0
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size

            logger.info(
                "search_chats: no query, returning %d latest chats from %d total",
                len(sorted_chats[start_idx:end_idx]),
                total,
            )
            return {
                "results": sorted_chats[start_idx:end_idx],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            }

        # Perform full-text search
        from codx.junior.chat_searcher import ChatSearcher, SearchFilters

        searcher = ChatSearcher()
        results = searcher.search(
            chats=all_project_chats,
            query=query,
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_size=page_size,
            filters=SearchFilters.from_dict(filters),
        )

        logger.info(
            "search_chats: query='%s' user_id=%s project_ids=%s returned %d results, page %d of %d",
            query,
            user_id or "all",
            project_ids or "all",
            results.get("total", 0),
            results.get("page", 1),
            results.get("total_pages", 0),
        )
        return results

    # -------------------------------------------------------------------------
    # Owner project resolution
    # -------------------------------------------------------------------------

    def _resolve_owner_manager(self, chat: Chat) -> "ChatManager":
        """
        Return the ChatManager owning *chat*.

        When the chat belongs to a different project (``owner_project_id``),
        a manager scoped to that project is returned so persistence lands in
        the correct location.

        :param chat: The chat whose owner manager to resolve.
        :return: ``self`` or a ChatManager scoped to the owner project.
        """
        if chat.owner_project_id and chat.owner_project_id != self.settings.project_id:
            chat_project = find_project_by_id(chat.owner_project_id)
            if chat_project:
                return ChatManager(settings=chat_project, event_manager=self.event_manager)
            logger.warning(
                "Owner project '%s' not found for chat '%s'; using current manager",
                chat.owner_project_id,
                chat.id,
            )
        return self

    # -------------------------------------------------------------------------
    # Merge-safe message helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _merge_message_lists(
        stored_messages: List[Message],
        incoming_messages: List[Message],
    ) -> List[Message]:
        """
        Merge two message lists by ``doc_id`` using ``updated_at`` to decide
        which version wins (last-writer-wins per message, never regress).

        Stored message ordering is preserved; new incoming messages are
        appended in their original relative order.

        FIXED: timestamps are parsed with :func:`_parse_timestamp` before
        comparison. Raw string comparison was incorrect because message
        timestamps exist in two formats (isoformat with 'T' vs
        ``str(datetime)`` with a space): the 'T' variant always won
        lexicographically, letting an OLDER incoming message overwrite a
        NEWER stored one.

        :param stored_messages: Messages loaded from disk.
        :param incoming_messages: Messages from the in-memory chat.
        :return: Merged, ordered message list.
        """
        merged: Dict[str, Message] = {}
        order: List[str] = []

        for message in list(stored_messages) + list(incoming_messages):
            if not message.doc_id:
                message.doc_id = str(uuid.uuid4())

            existing = merged.get(message.doc_id)
            if existing is None:
                merged[message.doc_id] = message
                order.append(message.doc_id)
            elif _parse_timestamp(message.updated_at) >= _parse_timestamp(existing.updated_at):
                # Incoming (or later duplicate) is newer or equal: overwrite
                merged[message.doc_id] = message

        return [merged[doc_id] for doc_id in order]

    def _load_stored_chat(self, chat: Chat) -> Optional[Chat]:
        """
        ADDED: Load the persisted version of *chat* with a fast-path.

        The granular message operations are hot-path code during an AI turn
        (invoked on every tool event and throttled stream persist). Scanning
        the whole tasks tree via :meth:`find_by_id` on every call is wasteful,
        so when ``chat.file_path`` is known, valid and inside this manager's
        tree it is read directly; otherwise fall back to :meth:`find_by_id`
        (covers moved/renamed chats).

        :param chat: The in-memory chat whose stored version to load.
        :return: The stored Chat, or ``None`` when not persisted yet.
        """
        if (
            chat.file_path
            and chat.file_path.startswith(self.chat_path)
            and os.path.isfile(chat.file_path)
        ):
            try:
                return self.load_chat_from_path(chat_file=chat.file_path)
            except (OSError, ValueError, KeyError) as ex:
                logger.warning(
                    "Fast-path load failed for chat '%s' at '%s': %s — "
                    "falling back to find_by_id",
                    chat.id,
                    chat.file_path,
                    ex,
                )
        return self.find_by_id(chat_id=chat.id) if chat.id else None

    def _accumulate_message_cost(
        self,
        chat: Chat,
        stored_chat: Optional[Chat],
        message: Message,
    ) -> float:
        """
        Accumulate the cost of a single *message* into ``chat.cost`` (monotonic).

        The persisted (stored) version of the message is used as the baseline so
        the cost is counted **exactly once**, while still capturing costs that
        are registered AFTER the message was first persisted (SmolAgent sets the
        cost on the response message once the AI call returns, which happens
        after the message may already have been streamed/saved with cost 0).

        Only POSITIVE deltas are applied, so ``chat.cost`` never decreases. As a
        result, deleting a message (see :meth:`remove_message`) never reduces the
        accumulated cost of the chat.

        ADDED — roll-up: the same delta is added to the ROOT chat of the tree
        (see :meth:`_roll_up_cost_to_root`), so the budget declared on a main
        task covers the cost of every descendant.

        :param chat: The in-memory chat whose accumulated cost to update.
        :param stored_chat: The persisted chat (baseline), or ``None``.
        :param message: The message whose cost to account for.
        :return: The delta actually added (0.0 when nothing changed).
        """
        new_cost = getattr(message, "cost", 0.0) or 0.0
        if new_cost <= 0:
            return 0.0

        previous_cost = 0.0
        if stored_chat:
            stored_message = next(
                (m for m in stored_chat.messages if m.doc_id == message.doc_id),
                None,
            )
            if stored_message is not None:
                previous_cost = getattr(stored_message, "cost", 0.0) or 0.0

        delta = new_cost - previous_cost
        if delta <= 0:
            return 0.0

        chat.cost = (getattr(chat, "cost", 0.0) or 0.0) + delta
        logger.info(
            "Accumulated chat cost: chat='%s' message='%s' delta=%.6f total=%.6f",
            chat.id,
            message.doc_id,
            delta,
            chat.cost,
        )
        self._roll_up_cost_to_root(chat=chat, delta=delta)
        return delta

    def _sync_message_cost_floor(self, chat: Chat) -> float:
        """
        Make ``chat.cost`` cover the cost of its messages (monotonic).

        ``add_message`` / ``update_message`` accumulate the cost of a message as
        they save it, but a caller may also append a message (or restore an
        older chat) and persist it with :meth:`save_chat`. This floor makes the
        accumulated cost of the chat never smaller than the sum of the costs of
        the messages it currently holds, and rolls the missing delta up to the
        root chat.

        It is IDEMPOTENT: when the cost is already accounted for (the normal
        path, where ``_accumulate_message_cost`` ran first) ``total <= cost`` and
        nothing is added, so a message is never counted twice. Deleted messages
        never reduce the accumulated budget (their cost stays in ``chat.cost``).

        :param chat: The chat about to be persisted.
        :return: The delta added (0.0 when nothing changed).
        """
        messages = getattr(chat, "messages", None) or []
        if not messages:
            return 0.0
        total = 0.0
        for message in messages:
            cost = getattr(message, "cost", 0.0) or 0.0
            if cost > 0:
                total += cost
        current = getattr(chat, "cost", 0.0) or 0.0
        if total <= current:
            return 0.0
        delta = total - current
        chat.cost = total
        logger.info(
            "Chat cost floor: chat='%s' messages=%.6f stored=%.6f delta=%.6f",
            chat.id,
            total,
            current,
            delta,
        )
        self._roll_up_cost_to_root(chat=chat, delta=delta)
        return delta

    def _roll_up_cost_to_root(self, chat: Chat, delta: float) -> float:
        """
        Add *delta* to the cost of the root chat of *chat* (the main task).

        A chat without a parent is its own root, so nothing is added twice. The
        root chat is persisted immediately: it carries
        ``Chat.estimation_cost`` (the run budget) and the agent compares it
        against ``Chat.cost``, so the value must be on disk before the next turn
        starts.

        Failures are logged and swallowed: a broken ancestor chain must never
        break the turn that produced the cost.

        :param chat: The chat that spent the money.
        :param delta: Positive cost delta already added to ``chat.cost``.
        :return: The delta rolled up (0.0 when the chat is the root).
        """
        if not delta or delta <= 0:
            return 0.0

        try:
            root_id = chat.root_id or resolve_root_id(chat=chat, chat_manager=self)
            if not root_id or root_id == chat.id:
                return 0.0

            # Parallel orchestration workers can finish at the same time and
            # roll their cost up into the same root chat. The read-modify-write
            # must be serialized across threads AND processes, otherwise one
            # update overwrites the other and the run budget drifts downwards.
            from codx.junior.utils.file_lock import interprocess_lock

            codx_path = str(getattr(self.settings, "codx_path", "") or "")
            lock_path = os.path.join(codx_path, ".cost-rollup.lock") if codx_path else ""
            if lock_path:
                with interprocess_lock(lock_path, timeout=30.0):
                    root_chat = self.find_by_id(chat_id=root_id)
                    if root_chat is None:
                        logger.warning(
                            "Cost roll-up: root chat '%s' of '%s' not found (delta=%.6f)",
                            root_id,
                            chat.id,
                            delta,
                        )
                        return 0.0
                    root_chat.cost = (root_chat.cost or 0.0) + delta
                    self.save_chat(chat=root_chat)
            else:
                root_chat = self.find_by_id(chat_id=root_id)
                if root_chat is None:
                    return 0.0
                root_chat.cost = (root_chat.cost or 0.0) + delta
                self.save_chat(chat=root_chat)
            logger.info(
                "Cost roll-up: root='%s' chat='%s' delta=%.6f root_total=%.6f "
                "budget=%s",
                root_id,
                chat.id,
                delta,
                root_chat.cost,
                root_chat.estimation_cost,
            )
            return delta
        except Exception as ex:
            logger.exception(
                "Cost roll-up failed for chat '%s' (delta=%.6f): %s", chat.id, delta, ex
            )
            return 0.0

    def _ensure_root_id(self, chat: Chat) -> Optional[str]:
        """
        Resolve and backfill ``Chat.root_id`` before the chat is persisted.

        Legacy chats have no ``root_id``; :func:`codx.junior.identity.resolve_root_id`
        walks ``parent_id`` once, stamps the cache on the in-memory chat and the
        value is stored with the chat. A chat whose ancestors cannot be loaded
        becomes its own root, which keeps the invariant "there is always a root"
        true.

        :param chat: The chat being saved (mutated in place).
        :return: The resolved root id (or None when it cannot be determined).
        """
        if chat.root_id:
            return chat.root_id
        try:
            return resolve_root_id(chat=chat, chat_manager=self)
        except Exception as ex:
            logger.warning("Could not resolve root_id for chat '%s': %s", chat.id, ex)
            return chat.root_id

    def _emit_message_event(self, chat: Chat, message: Message) -> None:
        """
        Announce a single message change to connected clients.

        The event carries the WHOLE message, so a client patches it in place
        instead of re-fetching the chat (see
        :meth:`codx.junior.events.event_manager.EventManager.message_event`).
        During an AI turn this is the hot path: every streamed chunk is
        persisted and announced here.

        Best effort: a notification failure must never break persistence.

        UNSENT DRAFTS ARE NEVER ANNOUNCED: the composer text persisted as
        ``Message.unsent`` is private to the client that is typing it, so it
        must not reach any other connected client through a socket event.

        :param chat: The chat the message belongs to.
        :param message: The message that changed.
        """
        if getattr(message, "unsent", False):
            return
        try:
            self.event_manager.message_event(chat=chat, message=message)
        except Exception:  # pragma: no cover - notifications are best effort
            logger.exception(
                "Could not emit message event for chat '%s' message '%s'",
                getattr(chat, "id", None),
                getattr(message, "doc_id", None),
            )

    def _emit_message_removed_event(self, chat: Chat, message_doc_id: str) -> None:
        """
        Announce that a message was removed (best effort).

        A removal has no content to stream, so it needs its own event: without
        it another client would keep the deleted message until the next full
        reload.

        :param chat: The chat the message was removed from.
        :param message_doc_id: ``doc_id`` of the removed message.
        """
        try:
            self.event_manager.message_removed_event(
                chat=chat, message_doc_id=message_doc_id
            )
        except Exception:  # pragma: no cover - notifications are best effort
            logger.exception(
                "Could not emit message removed event for chat '%s' message '%s'",
                getattr(chat, "id", None),
                message_doc_id,
            )

    def _persist_chat_messages(
        self,
        chat: Chat,
        stored_chat: Optional[Chat] = None,
        cost_message: Optional[Message] = None,
    ) -> Chat:
        """
        Merge-safe persistence used by the granular message operations.

        Reloads the stored chat (if any), merges message lists so mid-turn
        saves never clobber concurrent updates, optionally accumulates the cost
        of *cost_message* into ``chat.cost`` and then delegates to
        :meth:`save_chat`.

        :param chat: The in-memory chat to persist.
        :param stored_chat: Optional pre-loaded stored chat (avoids a second
            disk read when the caller already loaded it).
        :param cost_message: Optional message whose cost must be accumulated
            into ``chat.cost`` before saving.
        :return: The persisted chat (same instance, messages may be merged).
        """
        manager = self._resolve_owner_manager(chat)
        if stored_chat is None:
            stored_chat = manager._load_stored_chat(chat)
        if stored_chat:
            # Keep the accumulated cost monotonic: never regress a value that
            # may already have been persisted by another writer.
            if (stored_chat.cost or 0.0) > (chat.cost or 0.0):
                chat.cost = stored_chat.cost
            chat.messages = self._merge_message_lists(
                stored_messages=stored_chat.messages,
                incoming_messages=chat.messages,
            )
        if cost_message is not None:
            manager._accumulate_message_cost(
                chat=chat,
                stored_chat=stored_chat,
                message=cost_message,
            )
        return manager.save_chat(chat=chat)

    def add_message(self, chat: Chat, message: Message) -> Chat:
        """
        Append *message* to *chat* and persist immediately (merge-safe).

        Used to notify events (tool usage, run lifecycle) in real time while
        an AI turn is still running. Idempotent in memory: the message is not
        appended twice if its ``doc_id`` is already on the chat.

        The message's cost (if any) is accumulated into ``chat.cost`` and rolled
        up into the root chat.

        :param chat: The chat to add the message to (mutated in place).
        :param message: The message to add.
        :return: The persisted chat.
        """
        if not message.doc_id:
            message.doc_id = str(uuid.uuid4())
        message.updated_at = datetime.now().isoformat()
        if not any(m.doc_id == message.doc_id for m in chat.messages):
            chat.messages.append(message)
        logger.info(
            "add_message: chat='%s' message='%s' role='%s'",
            chat.id,
            message.doc_id,
            message.role,
        )
        saved = self._persist_chat_messages(chat, cost_message=message)
        self._emit_message_event(chat=chat, message=message)
        return saved

    def update_message(self, chat: Chat, message: Message) -> Chat:
        """
        Update an existing message in *chat* (matched by ``doc_id``) and
        persist immediately (merge-safe). If the message is not found it is
        appended instead.

        The message's cost (if any) is accumulated into ``chat.cost`` — this
        captures the cost SmolAgent registers on the response message AFTER the
        AI call completes, even if the message was already persisted with a
        cost of 0 during streaming.

        :param chat: The chat containing the message (mutated in place).
        :param message: The updated message.
        :return: The persisted chat.
        """
        if not message.doc_id:
            message.doc_id = str(uuid.uuid4())
        message.updated_at = datetime.now().isoformat()
        found = False
        for index, existing in enumerate(chat.messages):
            if existing.doc_id == message.doc_id:
                chat.messages[index] = message
                found = True
                break
        if not found:
            chat.messages.append(message)
        # CHANGED: debug level — this is hot-path (tool events + throttled
        # stream persists) and info-level logging would flood the logs.
        logger.debug(
            "update_message: chat='%s' message='%s' found=%s",
            chat.id,
            message.doc_id,
            found,
        )
        saved = self._persist_chat_messages(chat, cost_message=message)
        self._emit_message_event(chat=chat, message=message)
        return saved

    def update_chat_metadata(self, chat: Chat, metadata: dict) -> Chat:
        """
        Update chat metadata only (name, description, board, column, etc.)
        without touching messages.

        :param chat: The chat to update (mutated in place).
        :param metadata: Dict of metadata fields to update (excluding messages).
        :return: The persisted chat.
        """
        # Update only metadata fields, preserve messages
        for key, value in metadata.items():
            if key != "messages" and hasattr(chat, key):
                setattr(chat, key, value)
        
        # Save with chat_only=True to preserve messages
        updated_chat = self.save_chat(chat=chat, chat_only=True)
        
        logger.info(
            "update_chat_metadata: updated metadata for chat '%s'",
            chat.id
        )
        return updated_chat

    def remove_message(self, chat: Chat, message_doc_id: str) -> Chat:
        """
        Remove the message identified by *message_doc_id* from *chat* and
        persist. The removal is applied on top of the merged (stored +
        in-memory) message list so no other concurrent change is lost.

        The accumulated cost (``chat.cost``) is intentionally NOT changed:
        deleting a message keeps its cost in the chat budget.

        :param chat: The chat containing the message (mutated in place).
        :param message_doc_id: ``doc_id`` of the message to remove.
        :return: The persisted chat.
        """
        manager = self._resolve_owner_manager(chat)
        stored_chat = manager._load_stored_chat(chat)
        if stored_chat:
            chat.messages = self._merge_message_lists(
                stored_messages=stored_chat.messages,
                incoming_messages=chat.messages,
            )
        chat.messages = [m for m in chat.messages if m.doc_id != message_doc_id]
        logger.info(
            "remove_message: chat='%s' message='%s'", chat.id, message_doc_id
        )
        saved = manager.save_chat(chat=chat)
        self._emit_message_removed_event(chat=saved, message_doc_id=message_doc_id)
        return saved

    # -------------------------------------------------------------------------
    # Full-chat persistence
    # -------------------------------------------------------------------------

    def _register_status_change(
        self,
        chat: Chat,
        current_chat: Optional[Chat],
        user: Optional[str] = None,
    ) -> bool:
        """
        Detect and record a ``Chat.status`` transition into ``chat.status_history``.

        The history is APPEND-ONLY: persisted entries (from ``current_chat``) are
        merged with any in-memory entries (dedup by ``doc_id``) so concurrent
        writers never lose a transition. A new :class:`ChatStatusEvent` is only
        appended when the status actually changed.

        This produces the raw transition feed required by the task-performance
        metrics (cycle time, throughput, error rate) that cannot be derived from
        the single mutable ``Chat.status`` field.

        :param chat: The in-memory chat being persisted (mutated in place).
        :param current_chat: The previously persisted chat, or ``None`` if new.
        :param user: Optional user that triggered the change.
        :return: ``True`` when a new transition was registered.
        """
        # Merge persisted (source of truth) + in-memory history, dedup by doc_id
        merged: Dict[str, ChatStatusEvent] = {}
        stored_history = current_chat.status_history if current_chat else []
        for event in list(stored_history) + list(chat.status_history):
            if not event.doc_id:
                event.doc_id = str(uuid.uuid4())
            merged[event.doc_id] = event

        history = sorted(merged.values(), key=lambda e: _parse_timestamp(e.at))

        previous_status = (current_chat.status if current_chat else None) or None
        new_status = chat.status or None

        if previous_status == new_status:
            # No real transition — keep merged history and bail out
            chat.status_history = history
            return False

        history.append(
            ChatStatusEvent(
                doc_id=str(uuid.uuid4()),
                from_status=previous_status,
                to_status=new_status or "",
                user=user,
                at=datetime.now().isoformat(),
            )
        )
        chat.status_history = history
        logger.info(
            "Chat status change: chat='%s' '%s' -> '%s' (history=%d)",
            chat.id,
            previous_status,
            new_status,
            len(history),
        )
        return True

    def save_chat(self, chat: Chat, chat_only: bool = False) -> Chat:
        """
        Persist *chat* to disk, handling board/column defaults, ID assignment,
        user/profile aggregation, old-file cleanup and event emission.

        When ``chat.owner_project_id`` points to a different project the call
        is transparently forwarded to the correct :class:`ChatManager`.

        Status transitions are registered into ``chat.status_history`` before
        the chat is written, using the previously persisted chat as the
        ``from_status`` baseline.

        ADDED: ``Chat.root_id`` is resolved and backfilled here for legacy
        chats, so every persisted chat knows the main task it belongs to.

        :param chat: The chat to save.
        :param chat_only: If ``True`` and the chat already exists, preserve
            its stored messages (useful for metadata-only updates).
        :return: The saved chat.
        """
        # Delegate to the owning project's manager when necessary
        manager = self._resolve_owner_manager(chat)
        if manager is not self:
            return manager.save_chat(chat=chat, chat_only=chat_only)

        if not chat.board:
            chat.board = DEFAULT_BOARD
        if not chat.column:
            chat.column = DEFAULT_COLUMN
        if not chat.id:
            chat.id = str(uuid.uuid4())
        if not chat.created_at:
            chat.created_at = chat.updated_at

        chat.updated_at = datetime.now().isoformat()

        current_chat = self.find_by_id(chat_id=chat.id)
        if chat_only and current_chat:
            chat.messages = current_chat.messages

        # Ancestor invariant: every chat must know its root (main task).
        self._ensure_root_id(chat=chat)

        # Detect and register chat status transitions (performance metrics).
        # Runs before store_chat so the new record is persisted with the chat.
        self._register_status_change(chat=chat, current_chat=current_chat)

        # Never persist a chat whose message costs are not reflected in its
        # accumulated cost (and in the root chat's budget).
        self._sync_message_cost_floor(chat)

        chat.file_path = self.get_chat_file(chat)

        self.store_chat(chat=chat)

        # Remove superseded yaml file (legacy format)
        yaml_path = chat.file_path.replace(".json", ".yaml")
        if os.path.isfile(yaml_path):
            logger.info("Remove old yaml chat: %s", chat.file_path)
            os.remove(yaml_path)

        # Remove old path if the chat was moved (board/column/name changed)
        if current_chat:
            logger.info(
                "Save chat, current_chat %s at %s", current_chat.id, current_chat.file_path
            )
            if chat.file_path != current_chat.file_path:
                self.delete_chat(current_chat.file_path, delete_index=False)
                self._mark_index_dirty_if_building()

        self.event_manager.chat_event(chat=chat, event_type="changed")

        return chat

    def store_chat(self, chat: Chat) -> Dict[str, Any]:
        """
        Write *chat* to its ``file_path`` as JSON.

        A chat (task card) is a **git-tracked artifact** living under
        ``.codx/tasks``, so this is the last boundary before an in-memory
        conversation becomes permanent repository history: everything the
        conversation touched passes through here, including the
        ``tool_events`` that carry the output of a tool which printed
        ``.git/config``, an environment dump or a container log.

        CISO: the serialised payload is sanitised with
        :func:`codx.junior.security.ciso.payload.redact_payload` before it is
        written, so a credential can never enter a task card. Every
        replacement is logged and (when ``settings.codx_path`` is known) also
        recorded by the gate that produced it.

        :param chat: The chat to write.
        :return: The sanitized payload that was written (also used to update
            the derived index without serializing the chat twice).
        """
        logger.info("Save chat: %s", chat.file_path)
        os.makedirs(os.path.dirname(chat.file_path), exist_ok=True)
        payload, redactions = redact_payload(chat.model_dump())
        if redactions:
            logger.warning(
                "CISO: redacted %d secret(s) before storing chat '%s' at %s",
                redactions,
                chat.id,
                chat.file_path,
            )
        # CONCURRENCY: the write must be atomic. ``open(..., "w")`` truncates
        # the file and then refills it, so a reader running at the same time
        # (another request, the background worker, a socket handler) used to
        # see a partially written task and fail with
        # ``Extra data: line N column 2`` — corrupting the very artifact this
        # module is meant to preserve. Writing to a sibling temp file and
        # renaming it keeps readers on either the old or the new content,
        # never in between (``os.replace`` is atomic on POSIX).
        tmp_path = f"{chat.file_path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, chat.file_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    logger.warning("Could not remove temp chat file %s", tmp_path)
        # Write-through index. The file is the truth and has been replaced
        # atomically; a failure here only leaves the index stale until the
        # reconciler runs, so it must never fail the save.
        self._index_upsert_payload(payload, chat.file_path)
        return payload

    def delete_chat(
        self,
        file_path: Optional[str] = None,
        chat_id: Optional[str] = None,
        delete_index: bool = True,
    ) -> None:
        """
        Delete a chat file from disk.

        :param file_path: Direct path to the chat file.
        :param chat_id: Chat ID; resolved to a path via :meth:`find_chat_path`.
        :param delete_index: Remove the row from the derived index too. Set to
            False when *file_path* is the previous location of a chat that is
            being moved (the new row has already been written).
        """
        logger.info("Removing chat by file_path: %s  - chat_id: %s", file_path, chat_id)

        if chat_id:
            # O(1) path lookup; deleting a chat does not need its messages.
            file_path = self.find_chat_path(chat_id)
            if not file_path:
                logger.error("delete_chat: chat_id '%s' not found, nothing to delete", chat_id)
                return

        if file_path and os.path.isfile(file_path) and file_path.startswith(self.chat_path):
            logger.info("Removing chat at %s", file_path)
            os.remove(file_path)
            if delete_index:
                removed_id = chat_id or _extract_id_from_filename(file_path)
                self._wait_for_index_idle()
                self._index_delete(removed_id)
        else:
            logger.error("Removing chat error %s", file_path)
    # -------------------------------------------------------------------------
    # Chat loading
    # -------------------------------------------------------------------------

    def load_chat(
        self,
        board: str,
        column: Optional[str] = None,
        chat_name: Optional[str] = None,
    ) -> Chat:
        """
        Load a chat by board/column/name, returning an empty Chat if not found.

        :param board: Board name.
        :param column: Column name.
        :param chat_name: Chat name.
        :return: Loaded or new Chat object.
        """
        chat = Chat(board=board, column=column, name=chat_name)
        chat_file = self.get_chat_file(chat)
        if not os.path.isfile(chat_file):
            chat.file_path = chat_file
            return chat
        return self.load_chat_from_path(chat_file=chat_file)

    def load_chat_from_path(
        self,
        chat_file: str,
        chat_only: bool = False,
        unread_for: Optional[str] = None,
    ) -> Chat:
        """
        Load a Chat from a JSON file, ensuring the loaded chat always has a valid ID.

        NEW: If the chat has no ID in the JSON data, attempts to extract it from the
        filename (format: {slugified_name}.{uuid}.json). If extraction fails, generates
        a fresh UUID and logs a warning.

        :param chat_file: Path to the chat JSON file.
        :param chat_only: If ``True`` the returned chat has an empty message list.
        :param unread_for: Optional username used to compute the transient
            unread/activity annotations before the message list is truncated.
        :return: Loaded Chat object with guaranteed valid ID.
        """
        with open(chat_file, "r", encoding="utf-8") as f:
            chat_data = json.loads(f.read())
        chat = Chat(**chat_data)
        
        # ADDED: Ensure chat always has an ID
        if not chat.id:
            # Try to extract ID from filename
            extracted_id = _extract_id_from_filename(chat_file)
            if extracted_id:
                chat.id = extracted_id
                logger.warning(
                    "Chat loaded from '%s' had no ID; extracted from filename: %s",
                    chat_file,
                    extracted_id,
                )
            else:
                # Generate a new ID as fallback
                chat.id = str(uuid.uuid4())
                logger.warning(
                    "Chat loaded from '%s' had no ID; generated new UUID: %s",
                    chat_file,
                    chat.id,
                )
        
        # Annotate the task table BEFORE the message list is truncated: the
        # latest message, unread count and execution state need the full
        # conversation, while `chat_only` keeps the payload small.
        _annotate_task_activity(chat, unread_for=unread_for)
        if chat_only:
            chat.messages = [chat.messages[-1]] if len(chat.messages) else []
        chat.owner_project_id = self.settings.project_id
        return chat

    # -------------------------------------------------------------------------
    # Kanban helpers (delegated to the v2 store)
    # -------------------------------------------------------------------------

    def delete_kanban(self, kanban_title: str) -> None:
        """
        Remove an entire kanban board: its chats folder and its catalogue entry.

        CHANGED: the folder is resolved through the catalogue (the board name is
        only a label), so a renamed board is deleted where it actually lives.
        The catalogue entry is removed as well, because a board left in the
        index without a folder would show up as an empty board forever.

        :param kanban_title: Name (title) of the kanban board to delete.
        """
        board_folder, _ = self.kanban_store.resolve_folders(board=kanban_title)
        board_path = f"{self.chat_path}/{board_folder}"
        if os.path.isdir(board_path):
            shutil.rmtree(board_path, ignore_errors=True)
        self.kanban_store.remove_board(kanban_title)

        # The board catalogue is cached by every client: announce the deletion
        # so the other tabs/users reload it (best effort).
        emit_collection_event(
            COLLECTION_BOARDS,
            ACTION_DELETED,
            {"name": kanban_title, "title": kanban_title},
            settings=self.settings,
            source="chat_manager",
        )

    def chat_count(self) -> int:
        """Return the total number of chat files on disk."""
        if self._chat_index_mode() == "on" and self._chat_index_ready():
            try:
                store = self._chat_index_store()
                if store is not None:
                    return store.count_chats()
            except Exception:  # pragma: no cover - defensive
                pass
        return len(self.chat_paths())

    def last_chats(self) -> List[Chat]:
        """
        Return up to three chats modified within the last two days.

        :return: List of recently modified Chat objects.
        """
        cutoff = (datetime.now() - timedelta(days=2)).timestamp()
        chat_paths = [
            {"chat_path": p, "updated_at": os.stat(p).st_ctime}
            for p in self.chat_paths()
        ]
        recent = sorted(
            [c for c in chat_paths if c["updated_at"] > cutoff],
            key=lambda x: x["updated_at"],
            reverse=True,
        )[:3]
        return [self.load_chat_from_path(chat_file=c["chat_path"]) for c in recent]

    def find_by_id(self, chat_id: Optional[str]) -> Optional[Chat]:
        """
        Find and load a chat by its ID.

        FIXED: the match is now exact. The previous implementation used
        ``chat_id in path``, so looking for ``1234`` could return a chat whose
        id was ``12345`` — and ancestor resolution (:mod:`codx.junior.identity`)
        would then walk a completely different tree. A chat file is always named
        ``<slugified name>.<chat_id>.json`` (or ``.yaml``), so the id is matched
        as a filename suffix.

        :param chat_id: The UUID of the chat to locate.
        :return: Loaded Chat or ``None`` if not found.
        """
        if not chat_id:
            return None

        wanted = str(chat_id).strip()
        if not wanted:
            return None

        # O(1) id -> path when the index is available. When the index is
        # complete, a miss is authoritative (a new chat does not need a tree
        # scan); when it is still building, a miss falls back to the tree.
        if self._chat_index_mode() == "on" and (
            self._chat_index is not None or os.path.exists(self._chat_index_db_path())
        ):
            store = self._chat_index_store()
            if store is not None:
                try:
                    indexed_path = store.find_path(wanted)
                except Exception:  # pragma: no cover - defensive
                    indexed_path = None
                if indexed_path and os.path.isfile(indexed_path):
                    return self.load_chat_from_path(chat_file=indexed_path)
                # A miss is not authoritative: a file may have just been added
                # by git/another process before reconciliation. Fall through to
                # the exact filename scan (cheap on a miss, and only on a miss).

        all_paths = self.chat_paths()
        file_path = next(
            (
                path for path in all_paths
                if os.path.basename(path).endswith(f".{wanted}.json")
                or os.path.basename(path).endswith(f".{wanted}.yaml")
            ),
            None,
        )
        if file_path:
            return self.load_chat_from_path(chat_file=file_path)

        # CHANGED: log the count instead of dumping every path (log noise)
        logger.error(
            "[chat_id] not found: %s (searched %d chat files)",
            wanted,
            len(all_paths),
        )
        return None

    def find_chat_path(self, chat_id: Optional[str]) -> Optional[str]:
        """
        Return the file that stores *chat_id*, or None when it is not live.

        The id is matched as a filename suffix (``<slug>.<chat_id>.json``),
        never as a substring, and an archived task has no file in the live tree
        at all (it lives compressed in ``tasks/.archive``).

        :param chat_id: The UUID of the chat to locate.
        :return: Absolute path of the chat file, or None.
        """
        wanted = str(chat_id or "").strip()
        if not wanted:
            return None
        if self._chat_index_mode() == "on" and (
            self._chat_index is not None or os.path.exists(self._chat_index_db_path())
        ):
            store = self._chat_index_store()
            if store is not None:
                try:
                    indexed_path = store.find_path(wanted)
                except Exception:  # pragma: no cover - defensive
                    indexed_path = None
                if indexed_path and os.path.isfile(indexed_path):
                    return indexed_path
        return next(
            (
                path for path in self.chat_paths()
                if os.path.basename(path).endswith(f".{wanted}.json")
                or os.path.basename(path).endswith(f".{wanted}.yaml")
            ),
            None,
        )

    # -------------------------------------------------------------------------
    # Archiving (compressed, out of the live tree)
    # -------------------------------------------------------------------------

    def archived_chats(self) -> List[Chat]:
        """
        Return the archived tasks as metadata-only chats.

        Archived tasks are compressed into ``tasks/.archive`` (see
        :class:`codx.junior.kanban.archive.TaskArchive`), so nothing is
        decompressed here: listing them stays cheap however big the archive
        grows. Every returned chat carries ``archived=True``, ``archived_at``
        and ``archived_by`` and has an empty message list.

        :return: List of archived Chat objects (no messages).
        """
        return [self._archived_chat(entry) for entry in self.task_archive.list_entries()]

    @staticmethod
    def _archived_chat(entry: Dict[str, Any]) -> Chat:
        """
        Build the metadata-only Chat of an archive catalogue entry.

        :param entry: Entry returned by ``TaskArchive.list_entries()``.
        :return: Chat carrying the listing fields and ``archived=True``.
        """
        return Chat(
            id=entry.get("chat_id"),
            name=entry.get("name") or "(archived)",
            board=entry.get("board") or "",
            column=entry.get("column") or "",
            status=entry.get("status") or "",
            priority=entry.get("priority"),
            due_date=entry.get("due_date"),
            wake_at=entry.get("wake_at"),
            progress=int(entry.get("progress") or 0),
            created_at=entry.get("created_at"),
            updated_at=entry.get("updated_at"),
            profiles=list(entry.get("profiles") or []),
            tags=list(entry.get("tags") or []),
            sprints=list(entry.get("sprints") or []),
            assignees=list(entry.get("assignees") or []),
            parent_id=entry.get("parent_id"),
            root_id=entry.get("root_id"),
            project_id=entry.get("project_id"),
            messages=[],
            archived=True,
            archived_at=entry.get("archived_at"),
            archived_by=entry.get("archived_by"),
        )

    def archive_chat(self, chat_id: str, user: Optional[str] = None) -> Dict[str, Any]:
        """
        Archive a task: compress it and remove it from the live tree.

        The task stops appearing in every listing, search, board and cost
        roll-up (archived tasks are only returned when the caller asks for them
        with ``include_archived``) and the space it used is released. The
        payload keeps the original file name, so a restore is byte for byte.

        The call is idempotent: archiving an already archived task returns the
        stored entry instead of failing.

        :param chat_id: Id of the task to archive.
        :param user: User (or profile) doing it; stored in the catalogue.
        :return: The archive catalogue entry.
        :raises ValueError: When the task is neither live nor archived.
        """
        chat = self.find_by_id(chat_id)
        if chat is None:
            entry = self.task_archive.get(chat_id)
            if entry is None:
                raise ValueError(f"task '{chat_id}' not found")
            return entry

        file_path = self.find_chat_path(chat.id or chat_id)
        if not file_path or not os.path.isfile(file_path):
            raise ValueError(f"task '{chat_id}' has no stored file to archive")

        entry = self.task_archive.archive(
            chat_file=file_path,
            chat_id=chat.id or chat_id,
            archived_by=user,
            metadata={
                "name": chat.name,
                "board": chat.board,
                "column": chat.column,
                "status": chat.status,
                "priority": chat.priority,
                "due_date": chat.due_date,
                "wake_at": chat.wake_at,
                "progress": chat.progress,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
                "profiles": chat.profiles,
                "tags": chat.tags,
                "sprints": chat.sprints,
                "assignees": chat.assignees,
                "parent_id": chat.parent_id,
                "root_id": chat.root_id,
                "project_id": chat.project_id,
            },
        )
        self._wait_for_index_idle()
        self._index_delete(chat.id or chat_id)
        self._emit_archive_event(chat, archived=True)
        logger.info("Archived task '%s' (%s)", chat.id, chat.name)
        return entry

    def restore_chat(self, chat_id: str, user: Optional[str] = None) -> Optional[Chat]:
        """
        Restore an archived task into its board/column folder.

        The destination folders are resolved through the kanban catalogue (they
        are immutable), so a task archived before a board or column rename still
        lands where the catalogue points to, not where its title says.

        :param chat_id: Id of the archived task.
        :param user: User (or profile) doing it; only logged.
        :return: The restored Chat, with its messages.
        :raises ValueError: When the task is not archived.
        """
        entry = self.task_archive.get(chat_id)
        if entry is None:
            raise ValueError(f"task '{chat_id}' is not archived")

        board_folder, column_folder = self.kanban_store.resolve_folders(
            board=entry.get("board") or DEFAULT_BOARD,
            column=entry.get("column") or DEFAULT_COLUMN,
        )
        target_dir = os.path.join(self.chat_path, board_folder, column_folder)
        file_path = self.task_archive.restore(
            chat_id=str(chat_id), target_dir=target_dir
        )
        chat = self.load_chat_from_path(chat_file=file_path)
        try:
            self._wait_for_index_idle()
            self._index_upsert_payload(chat.model_dump(), file_path)
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Chat index restore upsert failed for %s: %s", chat_id, ex)
        self._emit_archive_event(chat, archived=False)
        logger.info(
            "Restored task '%s' (%s) to %s/%s by %s",
            chat.id,
            chat.name,
            board_folder,
            column_folder,
            user or "-",
        )
        return chat

    def _emit_archive_event(self, chat: Chat, archived: bool) -> None:
        """
        Announce an archive/restore so every client reloads its boards.

        Best effort: a lost event only leaves a client with a stale board until
        its next reload and must never fail a completed write.

        :param chat: The task that moved.
        :param archived: True when it was archived, False when restored.
        """
        try:
            emit_collection_event(
                COLLECTION_BOARDS,
                ACTION_CHANGED,
                {"name": chat.name, "chat_id": chat.id, "board": chat.board},
                settings=self.settings,
                source="chat_manager",
                archived=archived,
            )
        except Exception as ex:  # never break a completed write
            logger.warning(
                "Could not emit the archive event of '%s': %s", chat.id, ex
            )

    def load_kanban_from_file(self, kanban_file: str) -> dict:
        """
        Load kanban state from a JSON file, applying defaults when missing.

        Kept for backward compatibility: the canonical entry point is
        :meth:`load_kanban`, which reads the v2 store.

        :param kanban_file: Path to the kanban JSON file.
        :return: Kanban dict with ``version``, ``boards`` and ``tags`` keys.
        """
        kanban: dict = {"version": 0.1, "boards": {}, "tags": {}}
        if os.path.isfile(kanban_file):
            with open(kanban_file, "r", encoding="utf-8") as f:
                kanban = json.loads(f.read())
            # Migrate legacy format that lacked the version envelope
            if not kanban.get("version"):
                kanban = {"version": 0.1, "boards": kanban, "tags": {}}
        logger.info("Loading %s kanban", self.settings.project_name)
        return kanban

    @profile_function
    def load_kanban(self) -> dict:
        """
        Load the project's kanban configuration.

        CHANGED: boards are read from the v2 store (one file per board, migrated
        from the legacy ``kanban.json`` on first access) and assembled back into
        the legacy shape, so every existing caller keeps working.

        :return: Kanban dict ``{"version", "boards", "tags"}``.
        """
        logger.info("load_kanban %s", self.chat_path)
        return self.kanban_store.load()

    def save_kanban(self, kanban: dict) -> None:
        """
        Persist the kanban configuration to disk.

        CHANGED: the assembled document is diffed against the v2 index and
        written atomically while holding the project wide lock, so the API
        process and the background process can no longer clobber each other.

        :param kanban: Kanban dict to save.
        """
        self.kanban_store.save(kanban)

        # One request rewrites the whole catalogue (boards + their columns), so
        # the event is intentionally coarse: clients reload it and work out the
        # differences. Best effort: the document is already persisted.
        emit_collection_event(
            COLLECTION_BOARDS,
            ACTION_CHANGED,
            {"boards": list(((kanban or {}).get("boards") or {}).keys())},
            settings=self.settings,
            source="chat_manager",
        )

    # -------------------------------------------------------------------------
    # Chat querying
    # -------------------------------------------------------------------------

    def find_chats(self, last_update: Optional[datetime] = None) -> List[Chat]:
        """
        Return chats based on filters.

        :param last_update: Return chats updated since this date.
        :return: List of Chat objects.
        """
        if self._chat_index_mode() == "on" and self._chat_index_ready():
            try:
                store = self._chat_index_store()
                if store is not None:
                    rows = store.list_chats(
                        from_date=last_update.isoformat() if last_update else None,
                    )
                    return [self._chat_from_index(row) for row in rows]
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("Chat index find_chats failed: %s", ex)
        filtered_chats: List[Chat] = []
        for file_path in self.chat_paths(last_update=last_update):
            chat = self.load_chat_from_path(chat_file=file_path)
            if chat:
                filtered_chats.append(chat)
        return filtered_chats

    # -------------------------------------------------------------------------
    # Chat export
    # -------------------------------------------------------------------------

    def traverse_chat_messages(
        self, chat_id: str, all_chats: List[Chat]
    ) -> List[Message]:
        """
        Recursively traverse messages in *chat_id* and its descendants.

        Linked chats (via ``message_id``) and child chats (via ``parent_id``)
        are both followed.

        :param chat_id: Root chat ID to start traversal from.
        :param all_chats: Full list of chats used for link resolution.
        :return: Ordered list of messages.
        """
        messages: List[Message] = []
        chat = self.find_by_id(chat_id)

        logger.info(
            "chat_export traversing chat: %s, messages: %s", chat.name, len(chat.messages)
        )
        for message in chat.messages:
            # Follow any chat that is linked to this message
            linked_chat = next(
                (c for c in all_chats if c.message_id == message.doc_id), None
            )
            if linked_chat:
                messages.extend(
                    self.traverse_chat_messages(
                        chat_id=linked_chat.id, all_chats=all_chats
                    )
                )
            messages.append(message)

        # Append child chats in index order
        child_chats = sorted(
            [c for c in all_chats if c.parent_id == chat.id],
            key=lambda x: x.child_index,
        )
        for child_chat in child_chats:
            messages.extend(
                self.traverse_chat_messages(chat_id=child_chat.id, all_chats=all_chats)
            )

        return messages

    def build_markdown_document(self, chat_id: str) -> str:
        """
        Traverse the chat and generate a markdown document.

        Messages with ``hide=True`` are excluded.

        :param chat_id: ID of the root chat.
        :return: Markdown string.
        """
        all_chats = self.list_chats()
        messages = self.traverse_chat_messages(chat_id=chat_id, all_chats=all_chats)
        markdown = ""
        for msg in messages:
            # Unsent drafts are private composer state, never part of an export.
            if not msg.hide and not msg.unsent:
                markdown += f"# {msg.content}\n\n"
        return markdown

    def export_chat(self, chat_id: str, export_format: str) -> ExportedDocument:
        """
        Export a chat and its descendants to the specified format.

        :param chat_id: The ID of the chat to export.
        :param export_format: Target format (e.g. ``markdown``, ``docx``,
            ``pdf``, ``excel``).
        :return: An ExportedDocument containing the exported content.
        """
        chat = self.find_by_id(chat_id=chat_id)
        content = self.build_markdown_document(chat_id=chat_id)
        chat_exporter = ChatExport(chat=chat, content=content, export_format=export_format)
        return chat_exporter.export_chat()

    def mark_message_as_seen(self, chat: Chat, message: Message, username: str) -> List[str]:
        """
        Add username to message's read_by list if not already present.
        
        Persists the updated message immediately (merge-safe).
        
        :param chat: The chat containing the message.
        :param message: The message to mark as seen.
        :param username: Username of the reader.
        :return: Updated read_by list.
        """
        if not message.read_by:
            message.read_by = []
        
        if username not in message.read_by:
            message.read_by.append(username)
            message.updated_at = datetime.now().isoformat()
            # Advance the per-user read cursor before the (compatibility) file
            # update so unread counts are correct even if the write is slow.
            try:
                store = self._chat_index_store()
                if store is not None and self._chat_index_mode() == "on":
                    store.mark_read(chat.id, username, message.doc_id)
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("Chat index mark_read failed: %s", ex)
            self.update_message(chat=chat, message=message)
            logger.info(
                "mark_message_as_seen: chat='%s' message='%s' user='%s'",
                chat.id,
                message.doc_id,
                username,
            )
        
        return message.read_by

    def unmark_message_as_seen(self, chat: Chat, message: Message, username: str) -> List[str]:
        """
        Remove username from message's read_by list.
        
        Persists the updated message immediately (merge-safe).
        
        :param chat: The chat containing the message.
        :param message: The message to mark as un-seen.
        :param username: Username of the reader to remove.
        :return: Updated read_by list.
        """
        if not message.read_by:
            message.read_by = []
        
        if username in message.read_by:
            message.read_by.remove(username)
            message.updated_at = datetime.now().isoformat()
            self.update_message(chat=chat, message=message)
            # Recompute the cursor from the remaining read_by markers. The
            # write-through import only raises the cursor, so lower it after
            # the update to honour the undo.
            try:
                store = self._chat_index_store()
                if store is not None and self._chat_index_mode() == "on":
                    max_seq = 0
                    for index, current in enumerate(chat.messages or []):
                        if username in (getattr(current, "read_by", None) or []):
                            max_seq = max(max_seq, index)
                    if max_seq == 0:
                        # No marker left: start before the first message.
                        max_seq = -1
                    store.set_read(chat.id, username, max_seq)
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("Chat index unmark_read failed: %s", ex)
            logger.info(
                "unmark_message_as_seen: chat='%s' message='%s' user='%s'",
                chat.id,
                message.doc_id,
                username,
            )
        
        return message.read_by

# Made with ❤️ by codx-junior