"""
Background worker of the actors.

The API process never runs a shift inline: it reads state and writes the "take a
shift" intent. This thread — started from :mod:`codx.junior.background` —
discovers the projects, finds the actors whose schedule is due and runs their
shifts, at most a few at a time.

There is **one** worker for both actor sources:

* profiles that declare an ``actor`` block (coworkers and managers);
* boards that still carry a ``manager`` block (the legacy board manager). The
  board is the declaration, the runtime is the same, and the JSON action
  contract of the old board manager is gone.

A shift is protected by a per-actor ``flock`` (see
:meth:`AutonomyStore.shift_lock`), so even if two workers end up alive (API and
background process, or a restart overlap) the same actor never takes two shifts
at the same time. That matters more than for a chat: an actor has write access
to the repository.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import constants as C

logger = logging.getLogger(__name__)


def _env_bool(names: Any, default: bool) -> bool:
    """Read a boolean environment switch (first defined name wins)."""
    candidates = names if isinstance(names, (list, tuple)) else [names]
    for name in candidates:
        value = os.environ.get(name)
        if value is None:
            continue
        return str(value).strip().lower() not in {"0", "false", "no", "off", ""}
    return default


def _env_int(names: Any, default: int, minimum: int, maximum: int) -> int:
    """Read an integer environment setting with a safe fallback and clamp."""
    candidates = names if isinstance(names, (list, tuple)) else [names]
    for name in candidates:
        value = os.environ.get(name)
        if value is None or not str(value).strip():
            continue
        try:
            number = int(str(value))
        except (TypeError, ValueError):
            break
        return max(minimum, min(number, maximum))
    return default


def actors_enabled() -> bool:
    """Return True when the actor feature is enabled at all."""
    return _env_bool([C.ENV_ENABLED, C.ENV_LEGACY_ENABLED], True)


def schedule_enforced() -> bool:
    """
    Return True when the weekly availability windows are enforced.

    Setting ``CODX_JUNIOR_ACTORS_SCHEDULE=false`` makes every actor available
    24/7 regardless of its windows — useful to catch up after a long downtime,
    and the single switch an operator needs in an emergency.
    """
    return _env_bool(C.ENV_SCHEDULE, True)


def worker_enabled() -> bool:
    """Return True when the background worker should run."""
    return actors_enabled() and _env_bool(
        [C.ENV_WORKER, C.ENV_LEGACY_WORKER], True
    )


def _project_has_actors(settings: Any) -> bool:
    """Return True when the project declares at least one actor or board manager."""
    try:
        from .service import ActorService

        if ActorService(settings=settings).actors():
            return True
    except Exception as ex:
        logger.debug("actors: cannot inspect profiles: %s", ex)
    try:
        from codx.junior.board_manager.service import BoardManagerService

        service = BoardManagerService(settings=settings)
        for title, board in service.boards().items():
            document = board if isinstance(board, dict) else {"title": str(title)}
            if service.config_for(document).enabled:
                return True
        return False
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("actors: cannot inspect boards: %s", ex)
        return False


class ActorWorker:
    """Thread that takes the due shifts of the actors and board managers."""

    def __init__(
        self,
        *,
        tick_seconds: Optional[int] = None,
        max_actors: Optional[int] = None,
        stop_event: Any = None,
    ) -> None:
        self.tick_seconds = _env_int(
            [C.ENV_TICK_SECONDS, C.ENV_LEGACY_TICK_SECONDS],
            C.DEFAULT_TICK_SECONDS, 5, C.MAX_TICK_SECONDS,
        ) if tick_seconds is None else max(5, min(int(tick_seconds), C.MAX_TICK_SECONDS))
        self.max_actors = _env_int(
            [C.ENV_MAX_AGENTS, C.ENV_LEGACY_MAX_AGENTS], 2, 1, 8
        ) if max_actors is None else max(1, min(int(max_actors), 8))
        self.stop_event = stop_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool: Optional[ThreadPoolExecutor] = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> "ActorWorker":
        """Start the loop (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_actors, thread_name_prefix="ActorShift"
        )
        self._thread = threading.Thread(
            target=self._loop, name="ActorWorker", daemon=True
        )
        self._thread.start()
        logger.info(
            "Actor worker started (tick=%ss, max_actors=%d, schedule=%s)",
            self.tick_seconds, self.max_actors, schedule_enforced(),
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
        logger.info("Actor worker stopped")

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
        """Loop body: run one pass, wait, repeat."""
        while not self._stopped():
            try:
                self.run_once()
            except Exception as ex:  # pragma: no cover - never kill the loop
                logger.exception("Actor tick failed: %s", ex)
            self._stop.wait(self.tick_seconds)

    def run_once(self) -> List[Dict[str, Any]]:
        """Run one pass over every project that declares actors."""
        from codx.junior.project.project_discover import find_all_projects

        try:
            projects = list(find_all_projects().values())
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception("Actor worker could not discover projects: %s", ex)
            return []
        eligible = [project for project in projects if _project_has_actors(project)]
        if not eligible:
            return []

        pool = self._pool
        owns_pool = pool is None
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=self.max_actors, thread_name_prefix="ActorOnce"
            )
        results: List[Dict[str, Any]] = []
        try:
            futures = [pool.submit(self._process_project, project) for project in eligible]
            for future in futures:
                try:
                    results.extend(future.result() or [])
                except Exception as ex:  # pragma: no cover - defensive
                    logger.exception("Actor project pass failed: %s", ex)
        finally:
            if owns_pool:
                pool.shutdown(wait=True, cancel_futures=False)
        return results

    @staticmethod
    def _process_project(settings: Any) -> List[Dict[str, Any]]:
        """Take the due shifts of one project in its own event loop."""
        from .service import ActorService
        from .store import AutonomyStore

        project_id = getattr(settings, "project_id", "")
        stores = [AutonomyStore(getattr(settings, "codx_path", ""))]
        try:
            from codx.junior.board_manager.store import BoardManagerStore

            stores.append(BoardManagerStore(getattr(settings, "codx_path", "")))
        except Exception as ex:  # pragma: no cover - legacy store is optional
            logger.debug("actors: no board manager store: %s", ex)

        for store in stores:
            try:
                store.write_worker_heartbeat({"project_id": project_id})
            except Exception:  # pragma: no cover - heartbeat is best effort
                pass

        results: List[Dict[str, Any]] = []
        try:
            service = ActorService(settings=settings, store=stores[0])
            results.extend(asyncio.run(service.run_due()))
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception(
                "Actor shifts failed for project '%s': %s",
                getattr(settings, "project_name", ""), ex,
            )
        try:
            from codx.junior.board_manager.service import BoardManagerService

            results.extend(asyncio.run(BoardManagerService(settings=settings).run_due()))
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception(
                "Board managers failed for project '%s': %s",
                getattr(settings, "project_name", ""), ex,
            )
        finally:
            # A shift can outlive the heartbeat TTL; refresh it so the UI does
            # not report the worker offline right after a long shift.
            for store in stores:
                try:
                    store.write_worker_heartbeat({"project_id": project_id})
                except Exception:  # pragma: no cover
                    pass
        return results


# ---------------------------------------------------------------------------
# Module-level singleton (started by background.start_background_services)
# ---------------------------------------------------------------------------

_worker: Optional[ActorWorker] = None
_lock = threading.Lock()


def start_actor_worker(stop_event: Any = None) -> Optional[ActorWorker]:
    """Start the singleton worker (idempotent, no-op when disabled)."""
    global _worker

    if not worker_enabled():
        logger.info("Actor worker disabled by environment")
        return None
    with _lock:
        if _worker is not None and _worker.running:
            return _worker
        _worker = ActorWorker(stop_event=stop_event).start()
        return _worker


def stop_actor_worker() -> None:
    """Stop the singleton worker (idempotent)."""
    global _worker

    with _lock:
        worker = _worker
        _worker = None
    if worker is not None:
        worker.stop()


def get_actor_worker() -> Optional[ActorWorker]:
    """Return the running worker (tests/administration)."""
    return _worker


#: Legacy names: the worker used to be the "autonomy" worker.
AutonomyWorker = ActorWorker
start_autonomy_worker = start_actor_worker
stop_autonomy_worker = stop_actor_worker
get_autonomy_worker = get_actor_worker

# Made with ❤️ by codx-junior
