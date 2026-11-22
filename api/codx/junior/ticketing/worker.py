"""
Background worker of the autonomous tickets.

The API never runs a ticket inline: it reads state and writes the intent (queue,
configure, cancel). This thread — started from :mod:`codx.junior.background` —
discovers the projects, finds the autonomous tickets that are due and runs them,
at most a few at a time.

It follows the same rules as the actor worker:

* one run per ticket (a cross-process file lock in the ticket runtime folder);
* a ticket that is locked by the API or another worker is skipped, never
  duplicated;
* a worker heartbeat lets the UI say whether autonomous tickets can run at all;
* the loop never dies: a failing project/ticket is logged and the next tick
  continues.

A project without autonomous tickets is a no-op, so this worker is safe to start
on every installation.

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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codx.junior.globals import RUNTIME_FOLDER

from .autonomy import TicketAutonomyService, autonomy_enabled

logger = logging.getLogger(__name__)

ENV_WORKER = "CODX_JUNIOR_TICKET_AUTONOMY_WORKER"
ENV_TICK_SECONDS = "CODX_JUNIOR_TICKET_AUTONOMY_TICK_SECONDS"
ENV_MAX_WORKERS = "CODX_JUNIOR_TICKET_AUTONOMY_MAX"

DEFAULT_TICK_SECONDS = 20
MIN_TICK_SECONDS = 5
MAX_TICK_SECONDS = 600
DEFAULT_MAX_WORKERS = 2
MAX_WORKERS_LIMIT = 8

WORKER_FILE = "tickets-worker.json"
GLOBAL_WORKER_PATH = os.path.join(RUNTIME_FOLDER, WORKER_FILE)
WORKER_TTL_SECONDS = 120

#: How long the worker caches the list of projects that have ticket folders.
ELIGIBLE_TTL_SECONDS = 60


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment switch."""
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a clamped integer environment switch."""
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def worker_enabled() -> bool:
    """Return True when autonomous tickets and their worker are enabled."""
    return autonomy_enabled() and _env_bool(ENV_WORKER, True)


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write *payload* atomically (tmp + replace)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(temporary, path)
    except OSError as ex:  # pragma: no cover - heartbeat is best effort
        logger.debug("ticket autonomy: cannot write heartbeat: %s", ex)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    """Read a JSON object (None when missing/unreadable)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_global_worker_heartbeat(payload: Optional[Dict[str, Any]] = None) -> None:
    """Publish the process-wide ticket worker heartbeat."""
    _write_json(
        GLOBAL_WORKER_PATH,
        {"pid": os.getpid(), "at": datetime.now(tz=timezone.utc).isoformat(), **(payload or {})},
    )


def worker_present(ttl_seconds: int = WORKER_TTL_SECONDS) -> bool:
    """Return True when a live ticket worker heartbeat exists."""
    data = _read_json(GLOBAL_WORKER_PATH) or {}
    try:
        at = datetime.fromisoformat(str(data.get("at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age = (datetime.now(tz=timezone.utc) - at).total_seconds()
    return 0 <= age <= max(1, int(ttl_seconds))


def _project_has_tickets(settings: Any) -> bool:
    """Return True when the project has a ticket queue folder."""
    codx_path = str(getattr(settings, "codx_path", "") or "")
    if not codx_path:
        return False
    candidates = [
        os.path.join(codx_path, "tickets"),
        os.path.join(codx_path, "tasks", "tickets"),
    ]
    return any(os.path.isdir(path) for path in candidates)


def _write_project_heartbeat(settings: Any, payload: Optional[Dict[str, Any]] = None) -> None:
    """Write the per-project worker heartbeat (best effort)."""
    try:
        from codx.junior.runtime import runtime_dir

        path = os.path.join(runtime_dir(str(getattr(settings, "codx_path", "")), "tickets"), WORKER_FILE)
        _write_json(
            path,
            {
                "pid": os.getpid(),
                "project_id": getattr(settings, "project_id", None),
                "at": datetime.now(tz=timezone.utc).isoformat(),
                **(payload or {}),
            },
        )
    except Exception as ex:  # pragma: no cover - heartbeat is best effort
        logger.debug("ticket autonomy: cannot write project heartbeat: %s", ex)


class TicketAutonomyWorker:
    """Thread that runs the due autonomous tickets of every project."""

    def __init__(
        self,
        *,
        tick_seconds: Optional[int] = None,
        max_workers: Optional[int] = None,
        stop_event: Any = None,
    ) -> None:
        self.tick_seconds = (
            max(MIN_TICK_SECONDS, min(int(tick_seconds), MAX_TICK_SECONDS))
            if tick_seconds is not None
            else _env_int(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS, MIN_TICK_SECONDS, MAX_TICK_SECONDS)
        )
        self.max_workers = (
            max(1, min(int(max_workers), MAX_WORKERS_LIMIT))
            if max_workers is not None
            else _env_int(ENV_MAX_WORKERS, DEFAULT_MAX_WORKERS, 1, MAX_WORKERS_LIMIT)
        )
        self.stop_event = stop_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool: Optional[ThreadPoolExecutor] = None
        self._eligible: List[Any] = []
        self._eligible_at = 0.0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> "TicketAutonomyWorker":
        """Start the loop (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="TicketAutonomy"
        )
        self._thread = threading.Thread(
            target=self._loop, name="TicketAutonomyWorker", daemon=True
        )
        self._thread.start()
        write_global_worker_heartbeat({"state": "starting"})
        logger.info(
            "Ticket autonomy worker started (tick=%ss, max=%d)",
            self.tick_seconds, self.max_workers,
        )
        return self

    def stop(self) -> None:
        """Stop the loop and its pool."""
        self._stop.set()
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        logger.info("Ticket autonomy worker stopped")

    @property
    def running(self) -> bool:
        """Return True while the loop thread is alive."""
        return bool(self._thread is not None and self._thread.is_alive())

    # -- loop -------------------------------------------------------------

    def _stopped(self) -> bool:
        """Return True when the loop must stop."""
        if self.stop_event is not None and getattr(self.stop_event, "is_set", lambda: False)():
            return True
        return self._stop.is_set()

    def _loop(self) -> None:
        """Loop body: one pass, wait, repeat."""
        while not self._stopped():
            write_global_worker_heartbeat({"state": "scanning"})
            try:
                self.run_once()
            except Exception as ex:  # pragma: no cover - never kill the loop
                logger.exception("Ticket autonomy tick failed: %s", ex)
            write_global_worker_heartbeat({"state": "idle"})
            self._stop.wait(self.tick_seconds)

    def run_once(self) -> List[Dict[str, Any]]:
        """Run one pass over every project that has a ticket queue."""
        from codx.junior.project.project_discover import find_all_projects

        now = time.monotonic()
        if now - self._eligible_at >= ELIGIBLE_TTL_SECONDS:
            try:
                projects = list(find_all_projects().values())
            except Exception as ex:  # pragma: no cover - defensive
                logger.exception("Ticket autonomy: cannot discover projects: %s", ex)
                return []
            self._eligible = [
                project for project in projects if _project_has_tickets(project)
            ]
            self._eligible_at = now
            if self._eligible:
                logger.info(
                    "Ticket autonomy: %d/%d project(s) with a ticket queue",
                    len(self._eligible), len(projects),
                )
        eligible = list(self._eligible)
        if not eligible:
            return []

        pool = self._pool
        owns_pool = pool is None
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="TicketAutonomyOnce"
            )
        results: List[Dict[str, Any]] = []
        try:
            futures = [pool.submit(self._process_project, project) for project in eligible]
            for future in futures:
                try:
                    results.extend(future.result() or [])
                except Exception as ex:  # pragma: no cover - defensive
                    logger.exception("Ticket autonomy project pass failed: %s", ex)
        finally:
            if owns_pool:
                pool.shutdown(wait=True, cancel_futures=False)
        return results

    @staticmethod
    def _process_project(settings: Any) -> List[Dict[str, Any]]:
        """Run the due tickets of one project in its own event loop."""
        _write_project_heartbeat(settings, {"state": "scanning"})
        results: List[Dict[str, Any]] = []
        try:
            service = TicketAutonomyService(settings=settings)
            candidates = service.due_candidates()
            if candidates:
                logger.info(
                    "Ticket autonomy: %d due ticket(s) in '%s'",
                    len(candidates), getattr(settings, "project_name", ""),
                )
            for candidate in candidates:
                ticket_id = candidate.get("ticket_id") or ""
                if not ticket_id:
                    continue
                try:
                    results.append(asyncio.run(service.run(ticket_id)))
                except Exception as ex:  # pragma: no cover - one ticket is not fatal
                    logger.exception(
                        "Ticket autonomy: ticket '%s' failed to run: %s", ticket_id, ex
                    )
                    results.append({"ticket_id": ticket_id, "error": str(ex)})
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception(
                "Ticket autonomy failed for project '%s': %s",
                getattr(settings, "project_name", ""), ex,
            )
        finally:
            _write_project_heartbeat(settings, {"state": "idle"})
        return results


# ---------------------------------------------------------------------------
# Module-level singleton (started by background.start_background_services)
# ---------------------------------------------------------------------------

_worker: Optional[TicketAutonomyWorker] = None
_lock = threading.Lock()


def start_ticket_worker(stop_event: Any = None) -> Optional[TicketAutonomyWorker]:
    """Start the singleton worker (idempotent, no-op when disabled)."""
    global _worker

    if not worker_enabled():
        logger.info("Ticket autonomy worker disabled by environment")
        return None
    with _lock:
        if _worker is not None and _worker.running:
            return _worker
        _worker = TicketAutonomyWorker(stop_event=stop_event).start()
        return _worker


def stop_ticket_worker() -> None:
    """Stop the singleton worker (idempotent)."""
    global _worker

    with _lock:
        worker = _worker
        _worker = None
    if worker is not None:
        worker.stop()


def get_ticket_worker() -> Optional[TicketAutonomyWorker]:
    """Return the singleton worker (None when it was never started)."""
    return _worker


__all__ = [
    "TicketAutonomyWorker",
    "get_ticket_worker",
    "start_ticket_worker",
    "stop_ticket_worker",
    "worker_enabled",
    "worker_present",
    "write_global_worker_heartbeat",
]

# Made with ❤️ by codx-junior
