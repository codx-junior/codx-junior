"""
Application service of the actors.

One **shift** of one actor is a bounded pipeline:

    actor configuration -> boards -> agenda (due tasks)
        -> shift card on the desk board -> one or more agent turns with the
        whole tool set -> report on the card -> state store

This module is the **single runtime**. It used to be two:

* ``autonomy`` — a profile woke up, read its board and worked with every tool;
* ``board_manager`` — a profile reviewed a board and answered with a JSON
  action contract that was validated and applied afterwards.

The only real differences were the prompt (role), the tool policy and whether a
shift is one turn or turns until the work is done. Those are now three fields of
:class:`~codx.junior.autonomy.models.ActorConfig` (``role``, ``policy``,
``session``), and both entry points share this pipeline. The JSON contract is
gone: an actor uses the same task tools a human uses, so every change it makes
is visible on the board exactly like a human change.

Two things make this different from a chat:

* the actor is woken by the scheduler, not by a user, and it must decide by
  itself what to do (see :mod:`.prompts`);
* every follow-up becomes a **scheduled task** (``wake_at``), which is what makes
  "check that system again in two days" possible without a human in the loop.

The **weekly schedule** is enforced here as well: an actor only starts a shift
inside its availability window, and a due task that falls outside the window is
kept (its ``wake_at`` is not consumed) until the next opening.

Everything heavy (the AI stack, the chat engine) is imported lazily and both the
turn runner and the chat factory are injectable, so the whole pipeline can be
tested offline and the module can be imported by the API without the AI deps.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import constants as C
from . import prompts
from .models import (
    ActorConfig,
    ActorState,
    AgendaItem,
    ShiftReport,
    is_due,
    parse_iso,
    utcnow,
    utcnow_iso,
)
from .store import AutonomyStore

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 2000
MAX_MESSAGE_CHARS = 1000

#: A runner receives the shift chat (already created) and the system prompt.
TurnRunner = Callable[..., Any]


def _clip(value: Any, max_chars: int) -> str:
    """Clip a value to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


class EphemeralProfile:
    """
    Minimal profile-like object for actors declared outside the catalogue.

    A board manager is configured on the **board** (``board.manager``), not on a
    profile, but it must run through exactly the same pipeline. This object
    gives :meth:`ActorService._execute` the three things it needs from a profile
    (identity, instructions, model) without inventing a profile on disk.
    """

    def __init__(
        self,
        name: str,
        content: str = "",
        description: str = "",
        llm_model: str = "",
        avatar: str = "",
    ) -> None:
        self.name = str(name or "")
        self.content = str(content or "")
        self.description = str(description or "")
        self.llm_model = str(llm_model or "")
        self.avatar = str(avatar or "")
        self.profiles: List[str] = []
        self.tools: List[str] = []


class ActorService:
    """
    Runs and reports the actors of one project.

    Args:
        settings: Project settings.
        chat_manager: Optional ``ChatManager`` (built lazily).
        event_manager: Optional event manager for live UI notifications.
        store: Optional :class:`AutonomyStore` (tests).
        runner: Optional async callable replacing the real agent turn (tests).
        user: Optional ``CodxUser`` used by the AI call.
    """

    def __init__(
        self,
        settings: Any,
        chat_manager: Any = None,
        event_manager: Any = None,
        store: Optional[AutonomyStore] = None,
        runner: Optional[TurnRunner] = None,
        user: Any = None,
    ) -> None:
        self.settings = settings
        self.chat_manager = chat_manager
        self.event_manager = event_manager
        self.user = user
        self.store = store or AutonomyStore(getattr(settings, "codx_path", ""))
        self._runner = runner

    # -- dependencies -----------------------------------------------------

    def _manager(self) -> Any:
        """Return (creating if needed) the project ChatManager."""
        if self.chat_manager is None:
            from codx.junior.chat_manager import ChatManager

            self.chat_manager = ChatManager(settings=self.settings)
        return self.chat_manager

    def _profiles(self) -> Any:
        """Return the project ProfileManager."""
        from codx.junior.profiles.profile_manager import ProfileManager

        return ProfileManager(settings=self.settings)

    def _runner_for(self) -> TurnRunner:
        """Return the injected runner or the real agent turn."""
        return self._runner or self._run_agent_turn

    # -- catalogue --------------------------------------------------------

    def actors(self) -> Dict[str, Tuple[Any, ActorConfig]]:
        """
        Return the actors declared on a profile of the project.

        Returns:
            ``{profile_name: (Profile, ActorConfig)}`` for every profile whose
            ``actor`` block is enabled. A profile that cannot be read is skipped
            (the catalogue is never a single point of failure).
        """
        actors: Dict[str, Tuple[Any, ActorConfig]] = {}
        try:
            profiles = self._profiles().list_all_profiles() or []
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception("actors: cannot list profiles: %s", ex)
            return actors
        for profile in profiles:
            config = ActorConfig.from_profile(profile)
            if not config.enabled:
                continue
            name = str(getattr(profile, "name", "") or "").strip()
            if not name:
                continue
            actors[name] = (profile, config)
        return actors

    def agents(self) -> Dict[str, Tuple[Any, ActorConfig]]:
        """Legacy alias of :meth:`actors`."""
        return self.actors()

    def resolve_actor(self, actor: str) -> Tuple[str, Any, ActorConfig]:
        """
        Resolve an actor by profile name (case insensitive).

        Raises:
            ValueError: When the profile does not exist or is not an actor.
        """
        wanted = str(actor or "").strip().lstrip("@")
        if not wanted:
            raise ValueError("actor is required")
        actors = self.actors()
        for name, (profile, config) in actors.items():
            if name.lower() == wanted.lower():
                return name, profile, config
        known = ", ".join(actors) or "none"
        raise ValueError(
            f"'{wanted}' is not an actor (actors: {known}). "
            f"Enable the actor block of the profile first."
        )

    def resolve_agent(self, agent: str) -> Tuple[str, Any, ActorConfig]:
        """Legacy alias of :meth:`resolve_actor`."""
        return self.resolve_actor(agent)

    # -- boards -----------------------------------------------------------

    def ensure_boards(self, config: ActorConfig) -> List[str]:
        """Create the desk board and the watched boards when missing."""
        wanted: List[str] = []
        for board in [config.desk_board, *config.watched_boards]:
            title = str(board or "").strip()
            if title and title not in wanted:
                wanted.append(title)
        created: List[str] = []
        for title in wanted:
            if self._ensure_board(title):
                created.append(title)
        return created

    def _ensure_board(self, board_name: str) -> str:
        """
        Create *board_name* (with its columns) when it does not exist yet.

        Reuses the very same helpers the ``project_manager`` tool uses, so the
        catalogue, the storage folders and the UI stay consistent.

        Returns:
            The board title (empty when it cannot be created).
        """
        title = str(board_name or "").strip()
        if not title:
            return ""
        try:
            from codx.junior.tools import project_manager_tools as pm

            manager = self._manager()
            kanban = pm._load_kanban(manager)
            by_name = pm._board_catalogue(kanban=kanban)
            if pm._match_board(by_name=by_name, board=title) is not None:
                return title
            columns = list(C.DEFAULT_COLUMNS)
            entry = pm._new_board_entry(
                name=title,
                description=f"Actor board ({title})",
                columns=columns,
                board_type="kanban",
                parent_id=None,
            )
            raw_boards = pm._kanban_boards(kanban)
            if isinstance(raw_boards, list):
                entry["name"] = title
                raw_boards.append(entry)
            else:
                raw_boards[title] = entry
            pm._save_kanban(chat_manager=manager, kanban=kanban)
            pm._ensure_board_folders(
                chat_manager=manager, board_name=title, columns=columns
            )
            logger.info("actors: created board '%s'", title)
        except Exception as ex:  # pragma: no cover - board creation is best effort
            logger.warning("actors: cannot ensure board '%s': %s", title, ex)
        return title

    # -- snapshot ---------------------------------------------------------

    def collect_tasks(
        self,
        boards: List[str],
        config: ActorConfig,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return the snapshot of the watched boards handed to the actor."""
        moment = now or utcnow()
        watched = {str(board or "").strip() for board in boards or [] if str(board or "").strip()}
        if not watched:
            return []
        snapshot: List[Dict[str, Any]] = []
        for chat in self._manager().list_chats() or []:
            board = str(getattr(chat, "board", "") or "").strip()
            if board not in watched:
                continue
            if getattr(chat, "is_template", False):
                continue
            if str(getattr(chat, "mode", "") or "").strip().lower() == "channel":
                continue
            closed = self._is_closed(chat)
            if closed and not config.include_done:
                continue
            snapshot.append({
                "id": str(getattr(chat, "id", "") or ""),
                "name": _clip(getattr(chat, "name", ""), 300),
                "description": _clip(getattr(chat, "description", ""), MAX_DESCRIPTION_CHARS),
                "last_message": self._last_message(chat),
                "board": board,
                "column": str(getattr(chat, "column", "") or ""),
                "status": str(getattr(chat, "status", "") or ""),
                "wake_at": str(getattr(chat, "wake_at", "") or ""),
                "priority": str(getattr(chat, "priority", "") or ""),
                "due_date": str(getattr(chat, "due_date", "") or ""),
                "assignees": list(getattr(chat, "assignees", []) or []),
                "profiles": list(getattr(chat, "profiles", []) or []),
                "tags": list(getattr(chat, "tags", []) or []),
                "progress": int(getattr(chat, "progress", 0) or 0),
                "created_at": str(getattr(chat, "created_at", "") or ""),
                "updated_at": str(getattr(chat, "updated_at", "") or ""),
                "done": closed,
                "due_now": is_due(getattr(chat, "wake_at", ""), moment),
            })

        # Due and open tasks first, then the most recently touched ones: when the
        # boards have more cards than the prompt budget, the shift must see the
        # work that needs it now.
        snapshot.sort(key=lambda task: task.get("updated_at") or "", reverse=True)
        snapshot.sort(key=lambda task: 0 if task.get("due_now") else 1)
        snapshot.sort(key=lambda task: 1 if task.get("done") else 0)
        limit = max(1, min(int(config.max_tasks or C.DEFAULT_MAX_TASKS), C.MAX_TASKS_LIMIT))
        return snapshot[:limit]

    def due_agenda(
        self,
        boards: List[str],
        config: ActorConfig,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return the tasks whose ``wake_at`` is due (open ones only)."""
        moment = now or utcnow()
        items = [
            task
            for task in self.collect_tasks(boards, config, now=moment)
            if task.get("due_now") and not task.get("done")
        ]
        items.sort(key=lambda task: task.get("wake_at") or "")
        return items[: max(1, config.max_agenda)]

    @staticmethod
    def _is_closed(chat: Any) -> bool:
        """Return True when the task is finished and must never wake again."""
        status = str(getattr(chat, "status", "") or "").strip().lower()
        if status in C.CLOSED_STATUSES:
            return True
        return bool(getattr(chat, "done", False))

    @staticmethod
    def _last_message(chat: Any) -> str:
        """Return the last visible message content of *chat*."""
        for message in reversed(getattr(chat, "messages", []) or []):
            if getattr(message, "hide", False):
                continue
            content = _clip(getattr(message, "content", ""), MAX_MESSAGE_CHARS)
            if content:
                return content
        return ""

    # -- schedule ---------------------------------------------------------

    def is_within_schedule(
        self, config: ActorConfig, now: Optional[datetime] = None
    ) -> bool:
        """Return True when the weekly availability of the actor is open."""
        if not self._schedule_enforced():
            return True
        return config.schedule.normalized().is_open(now or utcnow())

    def next_opening(
        self, config: ActorConfig, now: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Return the next instant the actor may work (None when paused)."""
        moment = now or utcnow()
        if not self._schedule_enforced():
            return moment
        return config.schedule.normalized().next_opening(moment)

    @staticmethod
    def _schedule_enforced() -> bool:
        """Return True when the availability windows must be respected."""
        from .worker import schedule_enforced

        return schedule_enforced()

    def next_wake_at(
        self,
        config: ActorConfig,
        state: ActorState,
        now: Optional[datetime] = None,
    ) -> str:
        """
        Return the next planned wake of the actor (empty when disabled).

        The value already accounts for the weekly schedule: an actor that is
        outside its window reports the next opening instead of the cadence.
        """
        if not config.enabled or state.running:
            return ""
        moment = now or utcnow()
        planned = parse_iso(state.wake_at)
        if planned is None:
            last = parse_iso(state.last_shift_at)
            planned = (
                last + timedelta(minutes=config.interval_minutes)
                if last is not None else moment
            )
        if planned < moment:
            planned = moment
        opening = self.next_opening(config, planned)
        return opening.isoformat() if opening is not None else ""

    def is_due(
        self,
        config: ActorConfig,
        state: ActorState,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Return True when the actor should take a shift now.

        A manual wake (``state.wake_requested``) is always honoured: it is an
        explicit human decision. Automatic wakes only happen inside the weekly
        availability window, which is the whole point of quiet hours.
        """
        if not config.enabled:
            return False
        moment = now or utcnow()
        if state.wake_requested:
            return True
        # A shift can run for minutes; a "running" state is only abandoned when
        # it is far older than the runtime limit.
        if state.running:
            started = parse_iso(state.last_shift_at)
            if started is not None:
                age = (moment - started).total_seconds()
                limit = max(C.STALE_SHIFT_SECONDS, config.max_runtime_minutes * 60 + 300)
                if age < limit:
                    return False
        if not self.is_within_schedule(config, moment):
            return False
        planned = parse_iso(state.wake_at)
        if planned is not None:
            return planned <= moment
        last = parse_iso(state.last_shift_at)
        if last is None:
            return True
        return moment >= last + timedelta(minutes=config.interval_minutes)

    # -- status -----------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return the configuration + runtime state of every actor."""
        stored = self.store.load().get("agents") or {}
        entries: List[Dict[str, Any]] = []
        for name, (profile, config) in self.actors().items():
            try:
                state = ActorState(**(stored.get(name) or {}))
            except (TypeError, ValueError):
                state = ActorState(actor=name)
            entries.append(self._entry(name, profile, config, state))
        return {
            "actors": entries,
            # Legacy key: the UI reads `agents`.
            "agents": entries,
            "worker_present": self.store.worker_present()
            or any(entry["state"].get("running") for entry in entries),
            "defaults": {
                "role": C.DEFAULT_ROLE,
                "policy": C.DEFAULT_POLICY,
                "session": C.DEFAULT_SESSION,
                "mode": C.DEFAULT_MODE,
                "interval_minutes": C.DEFAULT_INTERVAL_MINUTES,
                "max_agenda": C.DEFAULT_MAX_AGENDA,
                "max_turns": C.DEFAULT_MAX_TURNS,
                "max_runtime_minutes": C.DEFAULT_MAX_RUNTIME_MINUTES,
                "columns": list(C.DEFAULT_COLUMNS),
                "weekdays": list(C.WEEKDAYS),
                "timezone": C.DEFAULT_TIMEZONE,
            },
        }

    def _entry(
        self,
        name: str,
        profile: Any,
        config: ActorConfig,
        state: ActorState,
    ) -> Dict[str, Any]:
        """Return the API/UI shape of one actor."""
        within_schedule = self.is_within_schedule(config)
        return {
            "actor": name,
            # Legacy key: the UI reads `agent`.
            "agent": name,
            "board": config.desk_board,
            "boards": config.watched_boards,
            "role": config.role,
            "description": _clip(getattr(profile, "description", ""), 400),
            "avatar": str(getattr(profile, "avatar", "") or ""),
            "linked_profiles": list(getattr(profile, "profiles", []) or []),
            "model": str(getattr(profile, "llm_model", "") or ""),
            "config": config.model_dump(),
            "state": state.public(),
            "schedule": {
                **config.schedule.normalized().model_dump(),
                "description": config.schedule.describe(),
                "open": within_schedule,
            },
            "due": self.is_due(config, state),
            "next_wake_at": self.next_wake_at(config, state),
            "daily_spent": state.day_spent(),
            "worker_present": self.store.worker_present(),
        }

    def request_wake(self, actor: str, when: str = "") -> Dict[str, Any]:
        """
        Record "take a shift now" (or at *when*) for *actor*.

        The API never runs a shift inline: it writes the intent and the
        background worker executes it, exactly like the orchestration runtime.
        """
        from .models import parse_when

        name, _profile, config = self.resolve_actor(actor)
        wake_at = parse_when(when) if when else ""
        state = self.store.request_wake(name, board=config.desk_board, wake_at=wake_at)
        self._emit(name, "wake", "Shift requested", board=config.desk_board)
        return {
            "queued": True,
            "actor": name,
            "board": config.desk_board,
            "wake_at": wake_at,
            "state": state.public(),
        }

    def schedule_wake(self, actor: str, when: str) -> Dict[str, Any]:
        """Set the next planned wake of *actor* (the actor schedules itself)."""
        from .models import parse_when

        name, _profile, config = self.resolve_actor(actor)
        wake_at = parse_when(when)
        if not wake_at:
            raise ValueError(f"cannot understand the schedule '{when}'")
        state = self.store.set_wake_at(name, wake_at)
        self._emit(name, "scheduled", f"Next shift scheduled at {wake_at}",
                   board=config.desk_board)
        return {
            "actor": name,
            "board": config.desk_board,
            "wake_at": wake_at,
            "state": state.public(),
        }

    # -- shifts -----------------------------------------------------------

    async def run_due(self, force: bool = False) -> List[Dict[str, Any]]:
        """Take a shift with every actor that is due (or every actor when forced)."""
        results: List[Dict[str, Any]] = []
        for name, (_profile, config) in self.actors().items():
            state = self.store.get_state(name)
            if not force and not self.is_due(config, state):
                continue
            results.append(await self.run_shift(name, force=True))
        return results

    async def run_shift(
        self,
        actor: str,
        *,
        force: bool = False,
        trigger: str = "schedule",
    ) -> Dict[str, Any]:
        """
        Run one shift of *actor*.

        Args:
            actor: Profile name of the actor.
            force: Run even when the schedule says it is not due.
            trigger: What caused the shift (``schedule``, ``wake``, ``api``).

        Returns:
            The public payload of the actor (config, state, boards, schedule).
        """
        try:
            name, profile, config = self.resolve_actor(actor)
        except ValueError as ex:
            return {"actor": actor, "agent": actor, "error": str(ex)}

        if not force and not self.is_within_schedule(config):
            state = self.store.get_state(name)
            self._emit(
                name, "skipped",
                "Outside the availability window of the actor",
                board=config.desk_board,
            )
            payload = self._entry(name, profile, config, state)
            payload["skipped"] = C.STOP_SCHEDULE
            return payload

        with self.store.shift_lock(name) as acquired:
            if not acquired:
                return self._entry(name, profile, config, self.store.get_state(name))

            state = self.store.mutate_state(
                name, lambda current: self._started(current, name, config.desk_board)
            )
            self._emit(name, "started", "Shift started", board=config.desk_board)
            report = await self._execute(
                name=name, profile=profile, config=config, trigger=trigger, state=state
            )
            state = self.store.get_state(name)
            state.mark_finished(report)
            fallback = self.next_wake_at(config, state)
            if not state.wake_at:
                state.wake_at = fallback
            self.store.put_state(name, state)
            self._emit(name, "finished", report.report or report.error,
                       board=config.desk_board, status=report.status,
                       cost=report.cost_cxjcoins)
            logger.info(
                "actors: shift of '%s' finished status=%s stop=%s cost=%.6f turns=%d",
                name, report.status, report.stop_reason, report.cost_cxjcoins,
                report.turns,
            )
            return self._entry(name, profile, config, state)

    async def run_actor(
        self,
        *,
        actor: str,
        profile: Any,
        config: ActorConfig,
        trigger: str = "board",
        state: Optional[ActorState] = None,
    ) -> ShiftReport:
        """
        Run one shift of an actor declared outside the profile catalogue.

        This is the entry point the board manager uses: the configuration lives
        on the board, the execution is the very same pipeline. Nothing is
        persisted in the actor store — the caller owns that state.
        """
        config = config.normalized()
        return await self._execute(
            name=actor,
            profile=profile,
            config=config,
            trigger=trigger,
            state=state or ActorState(actor=actor),
        )

    async def _execute(
        self,
        *,
        name: str,
        profile: Any,
        config: ActorConfig,
        trigger: str,
        state: ActorState,
    ) -> ShiftReport:
        """
        Core shift pipeline shared by every actor source.

        Returns the :class:`ShiftReport`; the caller decides where to persist it.
        """
        boards = config.watched_boards
        desk = config.desk_board
        report = ShiftReport(actor=name, agent=name, board=desk, role=config.role)
        started = time.monotonic()
        total_cost = 0.0
        contents: List[str] = []
        agenda: List[Dict[str, Any]] = []
        shift_chat: Any = None
        stop_reason = ""
        dry_run = config.mode == C.MODE_DRY_RUN

        try:
            self.ensure_boards(config)
            tasks = self.collect_tasks(boards, config)
            agenda = [
                task for task in tasks
                if task.get("due_now") and not task.get("done")
            ][: max(1, config.max_agenda)]
            report.agenda = [
                AgendaItem(chat_id=task["id"], name=task["name"],
                           wake_at=task.get("wake_at") or "")
                for task in agenda
            ]

            instructions = self._instructions(profile, config)
            system = prompts.build_shift_system(
                actor=name,
                board=desk,
                boards=boards,
                role=config.role,
                policy=config.policy,
                instructions=instructions,
                budget=config.budget_cxjcoins,
                daily_budget=config.daily_budget_cxjcoins,
                minutes=config.max_runtime_minutes,
                schedule=config.schedule.describe(),
                dry_run=dry_run,
            )
            prompt = prompts.build_shift_prompt(
                actor=name,
                board=desk,
                boards=boards,
                role=config.role,
                now=utcnow_iso(),
                trigger=trigger,
                shift_number=state.shifts,
                last_shift=state.last_summary,
                instructions=instructions,
                agenda=agenda,
                tasks=tasks,
            )

            max_turns = (
                config.max_turns
                if config.session == C.SESSION_UNTIL_DONE else 1
            )
            daily_spent = state.day_spent()
            remaining = [
                task for task in self.collect_tasks(boards, config)
                if not task.get("done")
            ]

            while True:
                if report.turns >= max_turns:
                    stop_reason = (
                        C.STOP_TURNS if config.session == C.SESSION_UNTIL_DONE
                        else C.STOP_SINGLE
                    )
                    break
                elapsed = time.monotonic() - started
                if report.turns and elapsed >= config.max_runtime_minutes * 60:
                    stop_reason = C.STOP_RUNTIME
                    break
                if report.turns and config.budget_cxjcoins \
                        and total_cost >= config.budget_cxjcoins:
                    stop_reason = C.STOP_BUDGET
                    break
                if report.turns and config.daily_budget_cxjcoins \
                        and (daily_spent + total_cost) >= config.daily_budget_cxjcoins:
                    stop_reason = C.STOP_DAILY_BUDGET
                    break

                timeout_seconds = max(
                    60,
                    int(config.max_runtime_minutes * 60 - elapsed),
                )
                if report.turns == 0:
                    turn_prompt = prompt
                else:
                    turn_prompt = prompts.build_continue_prompt(
                        turn=report.turns + 1,
                        max_turns=max_turns,
                        remaining=remaining,
                    )

                if dry_run:
                    content, cost, shift_chat = await self._dry_run(
                        name=name, board=desk, system=system, prompt=turn_prompt,
                        agenda=agenda, timeout_seconds=timeout_seconds,
                    )
                elif shift_chat is None:
                    content, cost, shift_chat = await self._active_turn(
                        name=name, board=desk, system=system, prompt=turn_prompt,
                        agenda=agenda, timeout_seconds=timeout_seconds,
                    )
                else:
                    content, cost = await self._continue_turn(
                        chat=shift_chat, system=system, prompt=turn_prompt,
                        timeout_seconds=timeout_seconds,
                    )

                report.turns += 1
                total_cost += max(0.0, float(cost or 0.0))
                if content:
                    contents.append(content)
                if config.session != C.SESSION_UNTIL_DONE:
                    stop_reason = C.STOP_SINGLE
                    break
                remaining = [
                    task for task in self.collect_tasks(boards, config)
                    if not task.get("done")
                ]
                if not remaining:
                    stop_reason = C.STOP_DONE
                    break

            content = "\n\n---\n\n".join(contents)
            report.shift_chat_id = str(getattr(shift_chat, "id", "") or "")
            report.summary = _clip(self._summary_from(content), 8000)
            report.report = _clip(content, 20000)
            report.cost_cxjcoins = total_cost
            report.stop_reason = stop_reason
            report.status = C.STATE_OK if contents else C.STATE_NO_WORK
        except asyncio.TimeoutError:
            report.status = C.STATE_ERROR
            report.stop_reason = C.STOP_RUNTIME
            report.error = (
                f"The shift exceeded {config.max_runtime_minutes} minutes and "
                "was cancelled"
            )
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception("actors: shift of '%s' failed: %s", name, ex)
            report.status = C.STATE_ERROR
            report.error = str(ex)
        finally:
            # The wake is always consumed: a due task that keeps waking the actor
            # forever is worse than a wake lost to an error, and the actor may
            # have rescheduled it during the shift (that wake is preserved by
            # _consume_agenda).
            self._consume_agenda(agenda)

        report.duration_seconds = time.monotonic() - started
        if shift_chat is not None:
            self._close_shift_card(shift_chat)
        return report

    async def _active_turn(
        self,
        *,
        name: str,
        board: str,
        system: str,
        prompt: str,
        agenda: List[Dict[str, Any]],
        timeout_seconds: int,
    ) -> Tuple[str, float, Any]:
        """Create the shift card and run the first agent turn on it."""
        chat = self._create_shift_card(
            name=name, board=board, prompt=prompt, agenda=agenda
        )
        runner = self._runner_for()
        await asyncio.wait_for(
            runner(chat=chat, system=system), timeout=timeout_seconds
        )
        content, cost = self._read_turn(chat)
        return content, cost, chat

    async def _continue_turn(
        self,
        *,
        chat: Any,
        system: str,
        prompt: str,
        timeout_seconds: int,
    ) -> Tuple[str, float]:
        """Run another turn of the same shift on the same card."""
        cost_before = self._chat_cost(chat)
        self._manager().add_message(chat=chat, message=self._user_message(prompt))
        runner = self._runner_for()
        await asyncio.wait_for(
            runner(chat=chat, system=system), timeout=timeout_seconds
        )
        content, _cost = self._read_turn(chat)
        return content, max(0.0, self._chat_cost(chat) - cost_before)

    async def _dry_run(
        self,
        *,
        name: str,
        board: str,
        system: str,
        prompt: str,
        agenda: List[Dict[str, Any]],
        timeout_seconds: int,
    ) -> Tuple[str, float, Any]:
        """
        Read-only shift: no tools, no card changes, just the report.

        A dry run is the honest way to preview an actor: the model cannot touch
        anything because it is called directly instead of through the agent loop
        (which is what exposes the tools).
        """
        chat = self._create_shift_card(
            name=name, board=board, prompt=prompt, agenda=agenda,
            description="Dry run: read-only preview of the shift.",
        )
        from codx.junior.ai.ai import AI

        ai = AI(settings=self.settings, user=self.user, system=system)
        result = await asyncio.wait_for(
            ai.a_chat_run(prompt=prompt), timeout=timeout_seconds
        )
        content = str(getattr(result, "content", "") or "")
        cost = float(
            getattr(getattr(result, "cost", None), "iteration_cost_cxjcoins", 0) or 0
        )
        return content, cost, chat

    async def _run_agent_turn(self, *, chat: Any, system: Optional[str] = None) -> Any:
        """
        Default runner: one full agent turn (tools included) on the shift card.

        The import is local so the service can be imported without the AI stack.
        """
        from codx.junior.chat.chat_engine import ChatEngine

        engine = ChatEngine(
            settings=self.settings,
            event_manager=self.event_manager,
            user=self.user,
        )
        await engine.chat_with_project(chat=chat, system=system)
        return chat

    # -- shift card -------------------------------------------------------

    def _user_message(self, prompt: str) -> Any:
        """Build the user message that continues a shift."""
        from codx.junior.db import Message

        return Message(role="user", content=str(prompt or ""), user=self.user)

    def _create_shift_card(
        self,
        *,
        name: str,
        board: str,
        prompt: str,
        agenda: List[Dict[str, Any]],
        description: str = "",
    ) -> Any:
        """Create the card of the shift on the desk board."""
        from codx.junior.task_manager import TaskManager

        names = ", ".join(item["name"] for item in agenda[:5]) or "free shift"
        task = TaskManager(settings=self.settings).create_task(
            name=f"Shift · {utcnow_iso()[:16].replace('T', ' ')}",
            description=(
                description
                or f"Autonomous shift of **{name}**. Agenda: {names}."
            ),
            content=prompt,
            board=board,
            column=C.SHIFT_COLUMN,
            status="doing",
            priority="high" if agenda else "medium",
            profiles=[name],
            tags=["actor", "shift"],
        )
        try:
            task.meta_data = {**(getattr(task, "meta_data", None) or {}), "actor": {
                "actor": name,
                "board": board,
                "agenda": [item["id"] for item in agenda],
                "started_at": utcnow_iso(),
            }}
            self._manager().save_chat(chat=task)
        except Exception as ex:  # pragma: no cover - metadata is best effort
            logger.debug("actors: cannot stamp shift metadata: %s", ex)
        return task

    def _close_shift_card(self, chat: Any) -> None:
        """Move the shift card to Done so the board keeps a readable history."""
        try:
            chat.status = "done"
            chat.column = C.SHIFT_DONE_COLUMN
            chat.progress = 100
            self._manager().save_chat(chat=chat)
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("actors: cannot close shift card: %s", ex)

    @staticmethod
    def _chat_cost(chat: Any) -> float:
        """Return the accumulated cost of a chat."""
        try:
            return float(getattr(chat, "cost", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _read_turn(self, chat: Any) -> Tuple[str, float]:
        """Return the final answer and the cost accumulated by the chat."""
        content = ""
        for message in reversed(getattr(chat, "messages", []) or []):
            if str(getattr(message, "role", "")) == "assistant":
                content = str(getattr(message, "content", "") or "")
                break
        return content, self._chat_cost(chat)

    def _consume_agenda(self, agenda: List[Dict[str, Any]]) -> None:
        """
        Clear the ``wake_at`` of the delivered tasks.

        The actor may have rescheduled itself (its own ``wake_at``) or created
        new scheduled tasks during the shift; only the tasks that were *due* are
        consumed here, so a follow-up the actor wrote during the shift survives.
        """
        for item in agenda or []:
            chat_id = str(item.get("id") or item.get("chat_id") or "")
            if not chat_id:
                continue
            try:
                chat = self._manager().find_by_id(chat_id=chat_id)
            except Exception as ex:  # pragma: no cover - defensive
                logger.debug("actors: cannot read task '%s': %s", chat_id, ex)
                continue
            if chat is None:
                continue
            current = str(getattr(chat, "wake_at", "") or "")
            if current and not is_due(current):
                # The actor scheduled it for later: respect that.
                continue
            try:
                chat.wake_at = ""
                self._manager().save_chat(chat=chat)
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("actors: cannot consume wake of '%s': %s", chat_id, ex)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _started(state: ActorState, name: str, board: str) -> ActorState:
        """Mark the state as running (called under the state lock)."""
        state.mark_started(name, board)
        return state

    @staticmethod
    def _instructions(profile: Any, config: ActorConfig) -> str:
        """Compose the mandate of the actor: profile content + standing orders."""
        parts = [
            str(getattr(profile, "content", "") or "").strip(),
            str(config.instructions or "").strip(),
        ]
        return "\n\n---\n\n".join([part for part in parts if part])

    @staticmethod
    def _summary_from(content: str) -> str:
        """Extract the shift summary from the report."""
        text = str(content or "").strip()
        if not text:
            return ""
        marker = "## Shift report"
        index = text.find(marker)
        return text[index:] if index >= 0 else text

    def _emit(self, actor: str, event: str, message: str = "", **data: Any) -> None:
        """Notify connected clients (best effort)."""
        if self.event_manager is None:
            return
        emit = getattr(self.event_manager, "autonomy_event", None)
        if not callable(emit):
            return
        try:
            emit({
                "actor": actor,
                "agent": actor,
                "event": event,
                "message": _clip(message, 4000),
                **data,
            })
        except Exception as ex:  # pragma: no cover - notifications are best effort
            logger.debug("actors: cannot emit event: %s", ex)


#: The class was called ``AutonomyService`` before the merge.
AutonomyService = ActorService


# Made with ❤️ by codx-junior
