"""
Board manager service — the board declaration of an actor.

This module is deliberately thin. It answers three questions and delegates the
work:

* **Who reviews this board, and how often?** (:class:`BoardManagerConfig`, stored
  on the board document, read here).
* **Is the review due?** (own state in :mod:`codx.junior.board_manager.store`, so
  the UI of a board keeps working exactly as before).
* **Run it.** Which is *not* implemented here: the board declaration is translated
  into an :class:`~codx.junior.autonomy.models.ActorConfig`
  (:meth:`BoardManagerConfig.to_actor_config`) and executed by
  :class:`codx.junior.autonomy.service.ActorService` — the single runtime.

What disappeared in the merge:

* the ``{"summary", "actions": [...]}`` **JSON contract** and its parser;
* the :class:`ActionApplier` validation pass and the ``max_actions`` whitelist
  enforcement after the fact (the manager now acts through the ordinary task
  tools, so the change is on the board, with the same permissions and the same
  audit trail as a human change);
* the board manager **worker**: there is one background worker, and it drives
  both actor sources (see :mod:`codx.junior.autonomy.worker`).

What stayed, because the UI and the boards rely on it: the per-board
configuration surface (``GET``/``PUT`` of the manager block through
``/api/boards/...``), the runtime state of each board (``last_report``,
``last_status``, ``next_run_at``, ``worker_present``) and ``mode: suggest``
(mapped onto a dry run).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import constants as C
from .models import BoardManagerConfig, ManagerState, utcnow_iso
from .store import BoardManagerStore

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1000

#: A runner receives the shift chat and the system prompt (see ActorService).
Runner = Any


def _clip(value: Any, max_chars: int) -> str:
    """Clip a value to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO instant into an aware datetime (None when invalid)."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class BoardManagerService:
    """
    Runs and reports the board managers of one project.

    Args:
        settings: Project settings.
        chat_manager: Optional ``ChatManager`` (built lazily).
        event_manager: Optional event manager for live UI notifications.
        store: Optional :class:`BoardManagerStore` (tests).
        runner: Optional async callable replacing the real agent turn (tests).
        user: Optional ``CodxUser`` used by the AI call.
        message_factory: Legacy, unused (kept for call compatibility).
    """

    def __init__(
        self,
        settings: Any,
        chat_manager: Any = None,
        event_manager: Any = None,
        store: Optional[BoardManagerStore] = None,
        runner: Optional[Runner] = None,
        user: Any = None,
        message_factory: Any = None,
    ) -> None:
        self.settings = settings
        self.chat_manager = chat_manager
        self.event_manager = event_manager
        self.user = user
        self.store = store or BoardManagerStore(getattr(settings, "codx_path", ""))
        self._runner = runner
        self.message_factory = message_factory

    # -- dependencies -----------------------------------------------------

    def _manager(self) -> Any:
        """Return (creating if needed) the project ChatManager."""
        if self.chat_manager is None:
            from codx.junior.chat_manager import ChatManager

            self.chat_manager = ChatManager(settings=self.settings)
        return self.chat_manager

    def _actors(self) -> Any:
        """Return the :class:`ActorService` that executes the reviews."""
        from codx.junior.autonomy.service import ActorService

        return ActorService(
            settings=self.settings,
            chat_manager=self.chat_manager,
            event_manager=self.event_manager,
            runner=self._runner,
            user=self.user,
        )

    # -- catalogue --------------------------------------------------------

    def boards(self) -> Dict[str, Dict[str, Any]]:
        """Return the boards of the project keyed by title."""
        kanban = self._manager().load_kanban() or {}
        boards = kanban.get("boards")
        if isinstance(boards, list):
            # Newer catalogues keep the boards in a list.
            return {
                str(board.get("name") or board.get("title") or index): board
                for index, board in enumerate(boards)
                if isinstance(board, dict)
            }
        return boards if isinstance(boards, dict) else {}

    def resolve_board(self, board_id: str) -> Tuple[str, Dict[str, Any]]:
        """
        Resolve a board by title **or** document id (case insensitive).

        Returns:
            ``(title, board_document)``.

        Raises:
            ValueError: When the board does not exist.
        """
        wanted = str(board_id or "").strip()
        if not wanted:
            raise ValueError("board is required")
        lowered = wanted.lower()
        for title, board in self.boards().items():
            if not isinstance(board, dict):
                board = {"title": str(title)}
            if str(title).strip().lower() == lowered:
                return str(title), board
            if str(board.get("id") or "").strip().lower() == lowered:
                return str(title), board
        raise ValueError(f"board '{wanted}' does not exist")

    @staticmethod
    def board_key(title: str, board: Dict[str, Any]) -> str:
        """Return the stable key used by the state store."""
        return str(board.get("id") or title)

    def config_for(self, board: Dict[str, Any]) -> BoardManagerConfig:
        """Return the normalized manager configuration of *board*."""
        config = BoardManagerConfig.from_board(board)
        if not config.profile:
            config.profile = C.DEFAULT_PROFILE
        return config

    # -- status -----------------------------------------------------------

    def _next_run_at(self, config: BoardManagerConfig, state: ManagerState) -> str:
        """Return the next automatic review instant (empty when not scheduled)."""
        if not config.enabled or state.running:
            return ""
        last = _parse_iso(state.last_run_at)
        if last is None:
            return utcnow_iso()
        return (last + timedelta(minutes=config.interval_minutes)).isoformat()

    def is_due(self, config: BoardManagerConfig, state: ManagerState) -> bool:
        """Return True when the board should be reviewed now."""
        if not config.enabled:
            return False
        if state.wake_requested:
            return True
        if state.running:
            # A crashed review must not block the board forever.
            started = _parse_iso(state.last_run_at)
            if started is not None:
                age = (datetime.now(tz=timezone.utc) - started).total_seconds()
                if age < C.STALE_RUN_SECONDS:
                    return False
        last = _parse_iso(state.last_run_at)
        if last is None:
            return True
        return datetime.now(tz=timezone.utc) >= last + timedelta(
            minutes=config.interval_minutes
        )

    def status(self) -> Dict[str, Any]:
        """Return the manager configuration + runtime state of every board."""
        entries: List[Dict[str, Any]] = []
        stored_states = self.store.load().get("boards") or {}
        for title, board in self.boards().items():
            if not isinstance(board, dict):
                board = {"title": str(title)}
            key = self.board_key(title, board)
            config = self.config_for(board)
            try:
                state = ManagerState(**(stored_states.get(key) or {}))
            except (TypeError, ValueError):
                state = ManagerState(board_id=key)
            actor = config.to_actor_config(str(title))
            entries.append({
                "board": str(title),
                "board_id": key,
                "type": str(board.get("type") or "kanban"),
                "columns": board.get("columns") or [],
                "config": config.model_dump(),
                "state": state.public(),
                "actor": actor.model_dump(),
                "due": self.is_due(config, state),
                "next_run_at": self._next_run_at(config, state),
            })
        return {
            "boards": entries,
            # A review can outlive the heartbeat interval; while a board reports
            # `running`, a worker is demonstrably alive.
            "worker_present": self.store.worker_present()
            or any(entry["state"].get("running") for entry in entries),
            "defaults": {
                "profile": C.DEFAULT_PROFILE,
                "mode": C.MODE_APPLY,
                "interval_minutes": C.DEFAULT_INTERVAL_MINUTES,
                "max_actions": C.DEFAULT_MAX_ACTIONS,
                "max_tasks": C.DEFAULT_MAX_TASKS,
            },
        }

    def request_run(self, board_id: str) -> Dict[str, Any]:
        """Record "run now" intent for *board_id* (the worker executes it)."""
        title, board = self.resolve_board(board_id)
        config = self.config_for(board)
        if not config.enabled:
            raise ValueError(
                f"the board manager of '{title}' is disabled; enable it first"
            )
        state = self.store.request_wake(self.board_key(title, board), title)
        self._emit(title, "wake", "Review requested")
        return {
            "queued": True,
            "board": title,
            "board_id": self.board_key(title, board),
            "state": state.public(),
        }

    # -- review -----------------------------------------------------------

    def _profile(self, name: str) -> Any:
        """
        Resolve the manager profile into a profile-like object for the runtime.

        An unknown profile still yields an object (with empty instructions) so
        the review can run and report instead of failing silently.
        """
        wanted = str(name or "").strip().lstrip("@")
        if not wanted:
            return None
        try:
            from codx.junior.profiles.profile_manager import ProfileManager

            manager = ProfileManager(settings=self.settings)
            profiles = manager.list_all_profiles()
            profile = next(
                (item for item in profiles if str(item.name).lower() == wanted.lower()),
                None,
            )
            if profile is None:
                profile = manager.read_profile(wanted)
            if profile is None:
                logger.warning("board manager: profile '%s' not found", wanted)
                return None
            return profile
        except Exception as ex:  # pragma: no cover - profiles are best effort
            logger.warning("board manager: cannot resolve profile '%s': %s", wanted, ex)
            return None

    async def run_due(self, force: bool = False) -> List[Dict[str, Any]]:
        """Review every board that is due (or forced)."""
        results: List[Dict[str, Any]] = []
        for title, board in self.boards().items():
            if not isinstance(board, dict):
                board = {"title": str(title)}
            config = self.config_for(board)
            if not config.enabled:
                continue
            key = self.board_key(title, board)
            state = self.store.get_state(key)
            if not force and not self.is_due(config, state):
                continue
            results.append(await self.run_board(title, force=True))
        return results

    async def run_board(self, board_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Review one board through the actor runtime.

        Args:
            board_id: Board title or document id.
            force: Run even when the schedule says it is not due (the manual
                "Run now" path). Disabled boards are never run.

        Returns:
            The public state of the board manager after the review.
        """
        try:
            title, board = self.resolve_board(board_id)
        except ValueError as ex:
            return {"board": board_id, "error": str(ex)}
        key = self.board_key(title, board)
        config = self.config_for(board)
        if not config.enabled:
            state = self.store.get_state(key)
            state.mark_finished(
                status=C.STATE_SKIPPED,
                report="The board manager is disabled for this board.",
            )
            self.store.put_state(key, state)
            return self._payload(title, key, config, state)

        with self.store.run_lock(key) as acquired:
            if not acquired:
                return self._payload(title, key, config, self.store.get_state(key))

            profile = self._profile(config.profile)
            actor = config.to_actor_config(title)

            def _start(current: ManagerState) -> ManagerState:
                current.mark_run_started(title, key, config.profile)
                return current

            self.store.mutate_state(key, _start)
            self._emit(title, "started", "Review started")

            started = time.monotonic()
            try:
                report = await self._actors().run_actor(
                    actor=config.profile or title,
                    profile=profile,
                    config=actor,
                    trigger="board",
                )
                state = self.store.get_state(key)
                state.mark_finished(
                    status=report.status,
                    report=report.report,
                    summary=report.summary,
                    error=report.error,
                    cost=report.cost_cxjcoins,
                    duration_ms=(time.monotonic() - started) * 1000,
                    turns=report.turns,
                )
                self.store.put_state(key, state)
                self._emit(title, "finished", report.report or report.error,
                           status=report.status)
                return self._payload(title, key, config, state)
            except asyncio.TimeoutError:
                state = self.store.get_state(key)
                state.mark_finished(
                    status=C.STATE_ERROR,
                    report="",
                    error=(
                        f"The review exceeded {C.DEFAULT_TIMEOUT_SECONDS}s and was "
                        "cancelled"
                    ),
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                self.store.put_state(key, state)
                self._emit(title, "error", state.last_error)
                return self._payload(title, key, config, state)
            except Exception as ex:  # pragma: no cover - defensive
                logger.exception("board manager: review of '%s' failed: %s", title, ex)
                state = self.store.get_state(key)
                state.mark_finished(
                    status=C.STATE_ERROR,
                    report="",
                    error=str(ex),
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                self.store.put_state(key, state)
                self._emit(title, "error", str(ex))
                return self._payload(title, key, config, state)

    # -- helpers ----------------------------------------------------------

    def _payload(
        self,
        title: str,
        key: str,
        config: BoardManagerConfig,
        state: ManagerState,
    ) -> Dict[str, Any]:
        """Return the API shape of one board manager."""
        return {
            "board": title,
            "board_id": key,
            "config": config.model_dump(),
            "state": state.public(),
            "actor": config.to_actor_config(title).model_dump(),
            "due": self.is_due(config, state),
            "next_run_at": self._next_run_at(config, state),
            "worker_present": self.store.worker_present(),
        }

    def _emit(self, board: str, event: str, message: str = "", **data: Any) -> None:
        """Notify connected clients (best effort)."""
        if self.event_manager is None:
            return
        emit = getattr(self.event_manager, "board_manager_event", None)
        if not callable(emit):
            return
        try:
            emit({
                "board": board,
                "event": event,
                "message": _clip(message, 4000),
                **data,
            })
        except Exception as ex:  # pragma: no cover - notifications are best effort
            logger.debug("board manager: cannot emit event: %s", ex)

# Made with ❤️ by codx-junior
