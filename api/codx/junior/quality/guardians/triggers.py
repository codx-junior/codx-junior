"""Debounced, background guardian runs on file changes.

File changes arrive in bursts: an editor saves, an agent writes five files in a
row, a refactor touches a module tree. Running a review per write would be
noisy and expensive. This scheduler coalesces the changes of one project and
runs once the project has been quiet for ``debounce_seconds`` (capped by
``max_delay_seconds``), exactly like the wiki scheduler.

The fast lane is the default: the change trigger runs deterministic checks. The
AI lane is opt-in with ``quality_policy.ai_on_change=true`` and is applied to the
run's bounded file set. A per-file cooldown prevents the same file from being
reviewed repeatedly while the user is typing.

The scheduler is a process-wide singleton started by the background service.
It is deliberately independent from the change manager: a missing linter, an
AI outage or a broken guardian can never stop knowledge indexing.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .policy import resolve_policy
from .runner import run_guardians_async

logger = logging.getLogger(__name__)

#: Seconds between scheduler dispatch ticks.
WORKER_INTERVAL_SECONDS = 1.0

#: Maximum concurrent project runs.
MAX_WORKERS = 2

#: Maximum paths kept per pending project run.
MAX_PENDING_PATHS = 500

#: Status file relative to the quality folder.
STATUS_FILE = "trigger.json"


def _project_key(settings: Any) -> str:
    """Return a stable scheduler key for a project."""
    return str(
        getattr(settings, "project_id", None)
        or getattr(settings, "codx_path", None)
        or getattr(settings, "project_name", "unknown")
    )


def _status_path(settings: Any) -> str:
    """Return the trigger status file of a project."""
    from .policy import quality_path

    return quality_path(settings, STATUS_FILE)


def _write_status(settings: Any, status: Dict[str, Any]) -> None:
    """Persist the scheduler status for the API/UI (best effort)."""
    try:
        path = _status_path(settings)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2)
        os.replace(tmp, path)
    except OSError as ex:  # pragma: no cover - best effort
        logger.debug("Cannot persist guardian trigger status: %s", ex)


def read_trigger_status(settings: Any) -> Dict[str, Any]:
    """Read the persisted trigger status of a project (API-process safe)."""
    try:
        with open(_status_path(settings), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _normalize_files(settings: Any, files: Iterable[str]) -> List[str]:
    """Normalize paths to project-relative paths, dropping duplicates."""
    from .diff import normalize_path

    result: List[str] = []
    for path in files or []:
        relative = normalize_path(settings, str(path))
        if relative and relative not in result:
            result.append(relative)
    if len(result) > MAX_PENDING_PATHS:
        result = result[:MAX_PENDING_PATHS]
    return result


@dataclass
class PendingRun:
    """One project's queued guardian work."""

    settings: Any
    files: Set[str] = field(default_factory=set)
    first_scheduled_at: float = field(default_factory=time.time)
    last_change_at: float = field(default_factory=time.time)
    due_at: float = field(default_factory=time.time)
    reason: str = "file-change"
    processing: bool = False

    @property
    def key(self) -> str:
        return _project_key(self.settings)

    def add_files(self, files: Iterable[str]) -> None:
        self.files.update(str(path) for path in files if path)
        if len(self.files) > MAX_PENDING_PATHS:
            # Keep the newest additions rather than an arbitrary old set.
            self.files = set(list(self.files)[-MAX_PENDING_PATHS:])

    def to_status(self) -> Dict[str, Any]:
        return {
            "pending": True,
            "files": sorted(self.files),
            "file_count": len(self.files),
            "reason": self.reason,
            "first_scheduled_at": int(self.first_scheduled_at),
            "last_change_at": int(self.last_change_at),
            "next_run_at": int(self.due_at),
            "processing": self.processing,
        }


class GuardianTriggerScheduler:
    """Process-wide debounced guardian runner for file changes."""

    def __init__(self, processor: Optional[Callable[[Any, List[str], str], None]] = None) -> None:
        self._lock = threading.RLock()
        self._pending: Dict[str, PendingRun] = {}
        self._running: Set[str] = set()
        self._last_runs: Dict[str, Dict[str, Any]] = {}
        self._last_path_run: Dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_WORKERS,
            thread_name_prefix="QualityGuardian",
        )
        self._processor = processor

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the dispatch thread (idempotent)."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="QualityGuardianScheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info("Quality guardian trigger scheduler started")

    def stop(self) -> None:
        """Stop the dispatch thread and abandon queued work."""
        self._stop_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=False)
        logger.info("Quality guardian trigger scheduler stopped")

    def is_running(self) -> bool:
        """Return whether the dispatch thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    # ── queueing ─────────────────────────────────────────────────────────
    def schedule(self, settings: Any, files: Iterable[str], reason: str = "file-change") -> bool:
        """Queue a debounced guardian run for the changed *files*.

        Args:
            settings: Project settings.
            files: Changed paths (absolute or project-relative).
            reason: Short reason recorded in the status/logs.

        Returns:
            bool: True when work was queued. False when guardians are disabled,
            no path is inside the project, or the per-file cooldown filters
            everything.
        """
        policy = resolve_policy(settings)
        if not policy.enabled or not policy.run_on_change:
            return False
        paths = _normalize_files(settings, files)
        if not paths:
            return False

        now = time.time()
        key = _project_key(settings)
        cooldown = max(0, int(policy.file_cooldown_seconds or 0))
        with self._lock:
            last_path_runs = self._last_path_run
            if cooldown:
                fresh = []
                for path in paths:
                    last = float(last_path_runs.get(f"{key}:{path}", 0) or 0)
                    if now - last >= cooldown:
                        fresh.append(path)
                paths = fresh
                if not paths:
                    return False

            pending = self._pending.get(key)
            if pending is None:
                pending = PendingRun(settings=settings, reason=reason)
                self._pending[key] = pending
            pending.add_files(paths)
            pending.last_change_at = now
            pending.reason = reason
            pending.due_at = min(
                pending.first_scheduled_at + max(0, int(policy.max_delay_seconds or 0)),
                now + max(0, int(policy.debounce_seconds or 0)),
            )
            status = pending.to_status()
            status.update(self._last_runs.get(key, {}))
            status["pending"] = True
            _write_status(settings, status)
            logger.debug(
                "Quality guardians queued for %s (%d file(s), due in %ds)",
                getattr(settings, "project_name", key),
                len(pending.files),
                max(0, pending.due_at - now),
            )
        self.start()
        return True

    def status(self, settings: Any) -> Dict[str, Any]:
        """Return queued/running/last-run information for a project."""
        key = _project_key(settings)
        with self._lock:
            pending = self._pending.get(key)
            status = dict(self._last_runs.get(key, {}))
            if pending and not pending.processing:
                status.update(pending.to_status())
            status["running"] = key in self._running or bool(
                pending and pending.processing
            )
        persisted = read_trigger_status(settings)
        for field_name, value in persisted.items():
            status.setdefault(field_name, value)
        return status

    def flush(self, settings: Any) -> None:
        """Run the queued work of a project immediately (used by tests)."""
        key = _project_key(settings)
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending:
            self._run_pending(pending)

    # ── worker ───────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._dispatch_due()
            except Exception as ex:  # pragma: no cover - defensive
                logger.exception("Quality guardian scheduler loop error: %s", ex)
            self._stop_event.wait(WORKER_INTERVAL_SECONDS)

    def _dispatch_due(self) -> None:
        now = time.time()
        due: List[PendingRun] = []
        with self._lock:
            for key, pending in list(self._pending.items()):
                if pending.processing or pending.due_at > now:
                    continue
                # Remove before starting: a change arriving while the batch
                # runs creates a fresh pending entry for the next pass.
                self._pending.pop(key, None)
                pending.processing = True
                self._running.add(key)
                due.append(pending)
        for pending in due:
            self._executor.submit(self._run_pending, pending)

    def _run_pending(self, pending: PendingRun) -> None:
        key = pending.key
        started = time.time()
        files = sorted(pending.files)
        try:
            if self._processor:
                self._processor(pending.settings, files, pending.reason)
                summary = {"action": "custom", "findings": 0}
            else:
                summary = self._process(pending.settings, files, pending.reason)
            with self._lock:
                for path in files:
                    self._last_path_run[f"{key}:{path}"] = time.time()
            self._last_runs[key] = {
                "pending": False,
                "last_run_at": int(started),
                "last_success_at": int(time.time()),
                "last_duration_seconds": round(time.time() - started, 2),
                "last_files": files[:100],
                "last_reason": pending.reason,
                "last_action": summary.get("action"),
                "last_findings": summary.get("findings", 0),
                "last_run_id": summary.get("run_id", ""),
                "last_error": "",
            }
            _write_status(pending.settings, self._last_runs[key])
        except Exception as ex:
            logger.exception(
                "Quality guardian run failed for %s: %s",
                getattr(pending.settings, "project_name", key),
                ex,
            )
            self._last_runs[key] = {
                "pending": False,
                "last_run_at": int(started),
                "last_success_at": self._last_runs.get(key, {}).get("last_success_at"),
                "last_duration_seconds": round(time.time() - started, 2),
                "last_files": files[:100],
                "last_reason": pending.reason,
                "last_action": "error",
                "last_findings": 0,
                "last_run_id": "",
                "last_error": str(ex),
            }
            _write_status(pending.settings, self._last_runs[key])
        finally:
            with self._lock:
                self._running.discard(key)
                pending.processing = False

    def _process(self, settings: Any, files: List[str], reason: str) -> Dict[str, Any]:
        """Run the guardians for one coalesced batch (synchronous thread)."""
        policy = resolve_policy(settings)
        # The change lane NEVER bootstraps profiles. A project gets its
        # guardians when a human opens the Quality panel or asks for a review;
        # an unattended file save must not write into .codx/profiles.
        try:
            from .profiles import has_guardians

            if not has_guardians(settings, trigger="change"):
                logger.debug(
                    "Quality change trigger skipped for %s: no change guardian",
                    getattr(settings, "project_name", ""),
                )
                return {
                    "action": "skipped",
                    "findings": 0,
                    "run_id": "",
                    "reason": "no-change-guardian",
                }
        except Exception as ex:
            logger.warning("Cannot inspect guardians before the change run: %s", ex)
            return {
                "action": "skipped",
                "findings": 0,
                "run_id": "",
                "reason": "guardian-inspection-failed",
            }
        ai = None
        if policy.ai_on_change:
            try:
                # The background process has no request/session: a bare AI with
                # the project settings is the smallest way to reach the project
                # model without booting the whole chat stack.
                from codx.junior.ai import AI

                ai = AI(settings=settings)
            except Exception as ex:
                logger.warning(
                    "Quality guardians: AI lane requested but unavailable for %s: %s",
                    getattr(settings, "project_name", ""),
                    ex,
                )
        run = asyncio.run(
            run_guardians_async(
                settings,
                ai=ai,
                trigger="change",
                scope="changed",
                mode="auto",
                paths=files,
                change_scope="all",
                user="",
            )
        )
        return {
            "action": (run.get("verdict") or {}).get("action"),
            "findings": len(
                [
                    finding for finding in (run.get("findings") or [])
                    if str(finding.get("status") or "open") == "open"
                ]
            ),
            "run_id": run.get("id", ""),
            "reason": reason,
        }


# -----------------------------------------------------------------------------
# Singleton API used by the background service / tests
# -----------------------------------------------------------------------------
_SCHEDULER: Optional[GuardianTriggerScheduler] = None
_SCHEDULER_LOCK = threading.Lock()


def get_guardian_scheduler() -> GuardianTriggerScheduler:
    """Return the process-wide scheduler (creating it if needed)."""
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        if _SCHEDULER is None:
            _SCHEDULER = GuardianTriggerScheduler()
        return _SCHEDULER


def start_guardian_scheduler() -> GuardianTriggerScheduler:
    """Start the process-wide scheduler and return it."""
    scheduler = get_guardian_scheduler()
    scheduler.start()
    return scheduler


def stop_guardian_scheduler() -> None:
    """Stop the process-wide scheduler."""
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        scheduler = _SCHEDULER
        _SCHEDULER = None
    if scheduler:
        scheduler.stop()


def schedule_guardian_run(
    settings: Any,
    files: Iterable[str],
    reason: str = "file-change",
) -> bool:
    """Queue a debounced guardian run for *files* (never raises)."""
    try:
        return get_guardian_scheduler().schedule(settings, files, reason=reason)
    except Exception as ex:  # pragma: no cover - never break the change pipeline
        logger.warning("Could not schedule a quality guardian run: %s", ex)
        return False


def guardian_trigger_status(settings: Any) -> Dict[str, Any]:
    """Return the scheduler status for *settings* (never raises)."""
    try:
        return get_guardian_scheduler().status(settings)
    except Exception:  # pragma: no cover - defensive
        return read_trigger_status(settings)


# Made with ❤️ by codx-junior
