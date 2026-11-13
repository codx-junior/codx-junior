"""
Runtime persistence of the autonomous agents.

Two files live under ``<codx_path>/autonomy``:

* ``state.json`` — one record per agent (last shift, report, cost, next wake).
  Written atomically while holding a project-level ``fcntl`` lock, so the API
  process (status, wake) and the background worker (shift) never lose each
  other's update.
* ``worker.json`` — heartbeat of the background worker, so the UI can tell
  whether autonomous shifts can run at all.

Heartbeats and per-agent shift locks churn on every tick, so they live in the
project **runtime** folder (``/tmp`` by default) and never inside the repository.

The agent **configuration** is never stored here: it lives inside the profile
(``.codx/profiles/<Name>/<Name>.profile``), so the profile catalogue stays the
single source of truth and a shift never rewrites a profile.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional

try:  # POSIX only; the container always has it.
    import fcntl
except ImportError:  # pragma: no cover - non POSIX fallback
    fcntl = None

from codx.junior.runtime import runtime_dir

from . import constants as C
from .models import AutonomyState, utcnow_iso

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def _agent_lock_name(agent: str) -> str:
    """Return a filesystem-safe lock file name for *agent*."""
    digest = hashlib.sha1(str(agent or "").encode("utf-8")).hexdigest()[:16]
    return f"{digest}.lock"


class AutonomyStore:
    """
    Persistence of the autonomy runtime state.

    Args:
        codx_path: Absolute ``<project>/.codx`` folder.
    """

    def __init__(self, codx_path: str) -> None:
        self.codx_path = str(codx_path or "")
        self.base_path = os.path.join(self.codx_path, C.AUTONOMY_DIR)
        self.state_path = os.path.join(self.base_path, C.STATE_FILE)
        self.runtime_path = (
            runtime_dir(self.codx_path, C.AUTONOMY_DIR)
            if self.codx_path
            else self.base_path
        )
        self.worker_path = os.path.join(self.runtime_path, C.WORKER_FILE)
        self.lock_path = os.path.join(self.runtime_path, C.LOCK_FILE)
        self.locks_path = os.path.join(self.runtime_path, C.LOCKS_DIR)
        self._thread_lock = threading.RLock()

    # -- low level --------------------------------------------------------

    @staticmethod
    def _read_json(path: str) -> Optional[dict]:
        """Read a JSON object, returning None when missing/unreadable."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read())
            return data if isinstance(data, dict) else None
        except (OSError, ValueError) as ex:
            logger.warning("autonomy: cannot read '%s': %s", path, ex)
            return None

    @staticmethod
    def _write_json(path: str, data: dict) -> None:
        """Write *data* atomically (tmp + replace)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2))
        os.replace(tmp_path, path)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the project-level state lock (cross process)."""
        os.makedirs(self.base_path, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            with self._thread_lock:
                yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    @contextmanager
    def shift_lock(self, agent: str) -> Iterator[bool]:
        """
        Try to acquire the exclusive lock of one agent shift.

        Yields True when the lock was acquired, False when another process or
        thread is already shifting the same agent. Non blocking by design: two
        workers must never run the same agent twice.
        """
        os.makedirs(self.locks_path, exist_ok=True)
        lock_path = os.path.join(self.locks_path, _agent_lock_name(agent))
        handle = open(lock_path, "a+", encoding="utf-8")
        acquired = True
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    acquired = False
            yield acquired
        finally:
            if acquired and fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()

    # -- state ------------------------------------------------------------

    def _load_unlocked(self) -> Dict[str, Any]:
        """Read the raw state document (caller holds the lock)."""
        data = self._read_json(self.state_path) or {}
        agents = data.get("agents")
        if not isinstance(agents, dict):
            agents = {}
        return {"version": data.get("version") or STATE_VERSION, "agents": agents}

    def load(self) -> Dict[str, Any]:
        """Return ``{"version", "agents": {agent: state_dict}}``."""
        with self.locked():
            return self._load_unlocked()

    def get_state(self, agent: str) -> AutonomyState:
        """Return the persisted state of *agent* (empty when unknown)."""
        data = self.load()
        raw = data["agents"].get(str(agent)) or {}
        try:
            return AutonomyState(**raw)
        except (TypeError, ValueError) as ex:
            logger.warning("autonomy: invalid state for '%s': %s", agent, ex)
            return AutonomyState(agent=str(agent))

    def put_state(self, agent: str, state: AutonomyState) -> AutonomyState:
        """Persist one agent state without losing the other agents."""
        key = str(agent)
        with self.locked():
            data = self._load_unlocked()
            data["agents"][key] = state.model_dump()
            self._write_json(self.state_path, data)
        return state

    def mutate_state(
        self,
        agent: str,
        callback: Callable[[AutonomyState], AutonomyState],
    ) -> AutonomyState:
        """Read-modify-write one agent state under the state lock."""
        key = str(agent)
        with self.locked():
            data = self._load_unlocked()
            try:
                current = AutonomyState(**(data["agents"].get(key) or {}))
            except (TypeError, ValueError):
                current = AutonomyState(agent=key)
            updated = callback(current)
            data["agents"][key] = updated.model_dump()
            self._write_json(self.state_path, data)
        return updated

    def request_wake(self, agent: str, board: str = "", wake_at: str = "") -> AutonomyState:
        """Mark the agent as "take a shift as soon as possible"."""

        def _wake(state: AutonomyState) -> AutonomyState:
            state.agent = str(agent)
            if board:
                state.board = board
            state.wake_requested = True
            if wake_at:
                state.wake_at = wake_at
            return state

        return self.mutate_state(agent, _wake)

    def set_wake_at(self, agent: str, wake_at: str) -> AutonomyState:
        """Record the planned next wake of *agent*."""

        def _set(state: AutonomyState) -> AutonomyState:
            state.agent = str(agent)
            state.wake_at = str(wake_at or "")
            return state

        return self.mutate_state(agent, _set)

    # -- worker heartbeat -------------------------------------------------

    def write_worker_heartbeat(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Publish the background worker heartbeat (best effort)."""
        data = {"pid": os.getpid(), "at": utcnow_iso(), **(payload or {})}
        try:
            self._write_json(self.worker_path, data)
        except OSError as ex:  # pragma: no cover - read-only filesystem
            logger.debug("autonomy: cannot write worker heartbeat: %s", ex)

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        """Parse an ISO instant into an aware datetime."""
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def worker_present(self, ttl_seconds: int = C.WORKER_TTL_SECONDS) -> bool:
        """Return True when a background worker heartbeat is recent enough."""
        data = self._read_json(self.worker_path) or {}
        at = self._parse_iso(data.get("at"))
        if at is None:
            return False
        age = (datetime.now(tz=timezone.utc) - at).total_seconds()
        return 0 <= age <= max(1, int(ttl_seconds))

# Made with ❤️ by codx-junior
