import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

from codx.junior.api_logs.model import ApiCallEvent

logger = logging.getLogger(__name__)

# One file per day to keep individual files small
FILE_DATE_FORMAT = "%Y-%m-%d"

# ── Write-behind pipeline ─────────────────────────────────────────────────────
# ``write()`` used to run on the request path: a process-wide ``threading.Lock``
# plus one ``open``/``write``/``flush`` per request inside the event loop. With
# Socket.IO long-polling and several browsers that lock serialised EVERY request
# of the platform and the syscall ran on the loop thread.
#
# Events are now handed to a bounded in-process queue (never blocking) and a
# single daemon writer thread batches them into one append per file.
WRITE_QUEUE_MAXSIZE = int(os.environ.get("CODX_JUNIOR_API_LOGS_QUEUE_SIZE", "50000"))
BATCH_MAX_LINES = int(os.environ.get("CODX_JUNIOR_API_LOGS_BATCH_LINES", "500"))
BATCH_MAX_SECONDS = float(os.environ.get("CODX_JUNIOR_API_LOGS_BATCH_SECONDS", "0.5"))
# Append-only JSONL files are never deleted otherwise: keep a bounded window.
RETENTION_DAYS = int(os.environ.get("CODX_JUNIOR_API_LOGS_RETENTION_DAYS", "30"))
PRUNE_INTERVAL_SECONDS = 3600

_WRITE_QUEUE: "queue.Queue" = queue.Queue(maxsize=WRITE_QUEUE_MAXSIZE)
_WRITER_THREAD: Optional[threading.Thread] = None
_WRITER_START_LOCK = threading.Lock()
_WRITTEN_EVENTS = 0
_DROPPED_EVENTS = 0
_KNOWN_BASE_PATHS: set = set()


def _prune_old_files(keep_days: int) -> None:
    """Delete ``*_api_calls.jsonl`` files older than ``keep_days``."""
    if keep_days <= 0:
        return
    cutoff = (datetime.utcnow() - timedelta(days=keep_days)).strftime(FILE_DATE_FORMAT)
    for base_path in list(_KNOWN_BASE_PATHS):
        try:
            for fname in os.listdir(base_path):
                if not fname.endswith("_api_calls.jsonl"):
                    continue
                date_part = fname.replace("_api_calls.jsonl", "")
                if len(date_part) == 10 and date_part < cutoff:
                    os.remove(os.path.join(base_path, fname))
                    logger.info("ApiLogStorage: pruned expired log %s", fname)
        except OSError as ex:
            logger.error("ApiLogStorage: cannot prune %s: %s", base_path, ex)


def _flush_batch(batch: List[tuple]) -> None:
    """Append a batch of ``(file_path, line)`` items, one write per file."""
    global _WRITTEN_EVENTS
    by_file: dict = {}
    for file_path, line in batch:
        by_file.setdefault(file_path, []).append(line)
    for file_path, lines in by_file.items():
        try:
            with open(file_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
                fh.write("\n")
            _WRITTEN_EVENTS += len(lines)
        except OSError as ex:
            logger.error("ApiLogStorage.write failed for %s: %s", file_path, ex)
    for _ in batch:
        _WRITE_QUEUE.task_done()


def _writer_loop() -> None:
    """Daemon writer: drain the queue in batches and flush once per file."""
    last_prune = 0.0
    while True:
        try:
            first = _WRITE_QUEUE.get(timeout=BATCH_MAX_SECONDS)
        except queue.Empty:
            first = None

        if first is not None:
            batch = [first]
            while len(batch) < BATCH_MAX_LINES:
                try:
                    batch.append(_WRITE_QUEUE.get_nowait())
                except queue.Empty:
                    break
            _flush_batch(batch)

        now = time.monotonic()
        if now - last_prune >= PRUNE_INTERVAL_SECONDS:
            last_prune = now
            try:
                _prune_old_files(RETENTION_DAYS)
            except Exception as ex:  # pragma: no cover - defensive
                logger.error("ApiLogStorage: prune failed: %s", ex)


def _ensure_writer() -> None:
    """Start the writer thread once per process."""
    global _WRITER_THREAD
    if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
        return
    with _WRITER_START_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        _WRITER_THREAD = threading.Thread(
            target=_writer_loop,
            name="ApiLogWriter",
            daemon=True,
        )
        _WRITER_THREAD.start()


def flush_pending(timeout: float = 2.0) -> None:
    """
    Block until every queued event is on disk (bounded by ``timeout``).

    Used by readers so a freshly recorded call is visible immediately, and by
    tests that assert on the file contents.
    """
    _ensure_writer()
    deadline = time.monotonic() + timeout
    while _WRITE_QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)


def queue_stats() -> dict:
    """Return the write-behind pipeline counters (used by ``/api/health``)."""
    return {
        "queue_depth": _WRITE_QUEUE.qsize(),
        "queue_maxsize": WRITE_QUEUE_MAXSIZE,
        "written_events": _WRITTEN_EVENTS,
        "dropped_events": _DROPPED_EVENTS,
        "writer_alive": bool(_WRITER_THREAD and _WRITER_THREAD.is_alive()),
    }


class ApiLogStorage:
    """
    Incremental append-only storage for API call performance events.

    Files are written under:
        <logs_path>/<YYYY-MM-DD>_api_calls.jsonl

    The logs path is a global directory shared across all projects, initialised
    from the ``CODX_JUNIOR_API_LOGS_DATA_PATH`` environment variable (see
    ``codx.junior.globals.API_LOGS_DATA_PATH``).

    Each line is a JSON-encoded ``ApiCallEvent`` dict (JSONL format).

    Writes are asynchronous: :meth:`write` only enqueues the serialised line and
    a single daemon writer thread batches the appends, so the request path never
    waits on the filesystem nor on a process-wide lock.

    Diagram:
    classDiagram
        class ApiLogStorage {
            +str base_path
            +write(event: ApiCallEvent) void
            +read_events(start_date, end_date, path, method, status_code, username,
                         project_name, project_id, min_duration_seconds, errors_only) List
            +list_available_dates() List[str]
        }
    """

    def __init__(self, logs_path: str) -> None:
        """
        Args:
            logs_path: Global directory where all API performance JSONL files
                       are stored. Typically set from
                       ``CODX_JUNIOR_API_LOGS_DATA_PATH``.
        """
        self.base_path = logs_path
        os.makedirs(self.base_path, exist_ok=True)
        _KNOWN_BASE_PATHS.add(self.base_path)
        logger.info("ApiLogStorage initialized at: %s", self.base_path)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _file_path_for_date(self, iso_date: str) -> str:
        """Return the API call JSONL file path for a given ISO date."""
        return os.path.join(self.base_path, f"{iso_date}_api_calls.jsonl")

    def _current_file_path(self) -> str:
        """Get current API call file path for today."""
        today = datetime.utcnow().strftime(FILE_DATE_FORMAT)
        return self._file_path_for_date(today)

    # ── Write ──────────────────────────────────────────────────────────────────

    def write(self, event: ApiCallEvent) -> None:
        """
        Enqueue an ApiCallEvent for today's JSONL file.

        Never blocks and never raises: this runs on the request path. When the
        queue is full (the writer thread died or the disk cannot keep up) the
        event is dropped and counted instead of stalling the event loop.

        Args:
            event: The API call event to persist.
        """
        global _DROPPED_EVENTS
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False)
        except (TypeError, ValueError) as ex:
            logger.error("ApiLogStorage.write: cannot serialise event: %s", ex)
            return

        _ensure_writer()
        try:
            _WRITE_QUEUE.put_nowait((self._current_file_path(), line))
        except queue.Full:
            _DROPPED_EVENTS += 1
            if _DROPPED_EVENTS % 1000 == 1:
                logger.warning(
                    "ApiLogStorage queue full (%d): dropped %d events so far",
                    WRITE_QUEUE_MAXSIZE,
                    _DROPPED_EVENTS,
                )

    def flush(self, timeout: float = 2.0) -> None:
        """Block until every queued event has been written (bounded)."""
        flush_pending(timeout=timeout)

    # ── Read ───────────────────────────────────────────────────────────────────

    def read_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        username: Optional[str] = None,
        project_name: Optional[str] = None,
        project_id: Optional[str] = None,
        min_duration_seconds: Optional[float] = None,
        errors_only: bool = False,
    ) -> List[ApiCallEvent]:
        """
        Read and optionally filter stored API call events from JSONL files.

        Args:
            start_date:           Inclusive ISO date lower bound (YYYY-MM-DD).
            end_date:             Inclusive ISO date upper bound (YYYY-MM-DD).
            path:                 Filter by exact path (route template).
            method:               Filter by HTTP method (case-insensitive).
            status_code:          Filter by exact status code.
            username:             Filter by exact username.
            project_name:         Filter by exact project name.
            project_id:           Filter by exact project id.
            min_duration_seconds: Only include calls slower than this value.
            errors_only:          Only include failed calls (error or status >= 500).

        Returns:
            List of matching ApiCallEvent objects ordered by timestamp.
        """
        # Serve the freshest data: a call recorded microseconds ago must be
        # visible here instead of waiting for the batch window.
        flush_pending()
        available = self.list_available_dates()
        matching_files = []
        for date_str in available:
            if start_date and date_str < start_date:
                continue
            if end_date and date_str > end_date:
                continue
            matching_files.append(self._file_path_for_date(date_str))

        events: List[ApiCallEvent] = []
        for file_path in matching_files:
            events.extend(self._read_file(file_path))

        # Apply filters
        if path:
            events = [e for e in events if e.path == path]
        if method:
            events = [e for e in events if e.method.upper() == method.upper()]
        if status_code is not None:
            events = [e for e in events if e.status_code == status_code]
        if username:
            events = [e for e in events if e.username == username]
        if project_name:
            events = [e for e in events if e.project_name == project_name]
        if project_id:
            events = [e for e in events if e.project_id == project_id]
        if min_duration_seconds is not None:
            events = [e for e in events if e.duration_seconds >= min_duration_seconds]
        if errors_only:
            events = [e for e in events if e.error or e.status_code >= 500]

        events.sort(key=lambda e: e.timestamp)
        return events

    # ── Internal Readers ──────────────────────────────────────────────────────

    def _read_file(self, file_path: str) -> List[ApiCallEvent]:
        """Parse single JSONL file into list of events, skipping bad lines."""
        events: List[ApiCallEvent] = []
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        events.append(ApiCallEvent.from_dict(data))
                    except (json.JSONDecodeError, TypeError, ValueError) as ex:
                        logger.warning(
                            "Skipping malformed api log line %d in %s: %s",
                            line_no,
                            file_path,
                            ex,
                        )
        except FileNotFoundError:
            pass
        except OSError as ex:
            logger.error("Error reading api log file %s: %s", file_path, ex)
        return events

    # ── Date Management ────────────────────────────────────────────────────────

    def list_available_dates(self) -> List[str]:
        """
        Return sorted list of ISO date strings for which data files exist.

        Returns:
            Sorted list of YYYY-MM-DD strings in ascending order.
        """
        dates: List[str] = []
        try:
            for fname in os.listdir(self.base_path):
                if fname.endswith("_api_calls.jsonl"):
                    date_part = fname.replace("_api_calls.jsonl", "")
                    # Basic sanity check — must be 10 chars YYYY-MM-DD
                    if len(date_part) == 10:
                        dates.append(date_part)
        except OSError as ex:
            logger.error("Error listing api log directory: %s", ex)
        return sorted(dates)

# Made with ❤️ by codx-junior