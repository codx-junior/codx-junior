"""
Autonomous ticket runtime: work a ticket until its validation gate is green.

A ticket is a request one project places on another one (see
:mod:`codx.junior.ticketing`). With ``ChatTicket.autonomy.enabled`` the target
project stops waiting for a human: this module runs a bounded agent loop on the
ticket conversation itself and dives, analyses, edits, tests and iterates until
the ticket is **validated** — or reports the concrete blocker.

Design
------
* **The ticket is the conversation.** Every round is appended to the ticket
  chat, so the requester sees the work and the target keeps the audit trail.
* **The runtime owns validation.** The model never decides that the ticket is
  done: after every round :meth:`TicketAutonomyService._validate` runs the
  configured commands or the project QA suite in the ticket workspace. Only a
  green gate closes the ticket (``done``); a red one is fed back to the model
  for the next round.
* **Isolation.** The run uses the same per-task git worktree as autonomous
  shifts (``TaskWorkspaceManager``): changes live on a ticket branch and are
  committed there, never in the main checkout.
* **Bounded.** ``max_rounds``, ``max_runtime_minutes`` and
  ``budget_cxjcoins`` are hard stops. One ticket runs at a time (file lock), so
  the API and the background worker cannot duplicate work.

Entry points: :class:`TicketAutonomyService` for the tools/API/tests and
:mod:`codx.junior.ticketing.worker` for the background thread.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from codx.junior.ticketing import prompts
from codx.junior.ticketing.model import (
    TICKET_AUTONOMY_BLOCKED,
    TICKET_AUTONOMY_CANCELLED,
    TICKET_AUTONOMY_FAILED,
    TICKET_AUTONOMY_OFF,
    TICKET_AUTONOMY_PASSED,
    TICKET_AUTONOMY_QUEUED,
    TICKET_AUTONOMY_RUNNING,
    TICKET_AUTONOMY_RUNNABLE_STATES,
    TICKET_SIDE_TARGET,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
    TICKET_STATUS_OPEN,
    TICKET_TERMINAL_STATUSES,
    TICKET_VALIDATION_COMMANDS,
    TICKET_VALIDATION_NONE,
    TICKET_ACTION_STATUS,
    TICKET_ACTION_UPDATED,
    DEFAULT_TICKET_PROFILE_CANDIDATES,
    TicketAutonomy,
    TicketRecord,
    TicketValidation,
    column_for_status,
    now_iso,
)
from codx.junior.ticketing.service import TicketError, TicketService

logger = logging.getLogger(__name__)

#: Folders inside the project runtime folder for the cross-process run locks
#: and the cancellation flags (never in the repository).
LOCKS_DIR = "locks"
CANCELLED_DIR = "cancelled"

#: Maximum output kept from one validation run (the tail carries the failure).
MAX_VALIDATION_OUTPUT_CHARS = 8000

#: Environment switch for the feature. A manual run still works when disabled.
ENV_ENABLED = "CODX_JUNIOR_TICKET_AUTONOMY"

#: How long a "running" state may stay without a live lock before it is
#: considered abandoned (seconds). It is added to the run runtime.
STALE_GRACE_SECONDS = 600



def autonomy_enabled() -> bool:
    """Return True when autonomous tickets are enabled at all."""
    value = os.environ.get(ENV_ENABLED)
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _clip(value: Any, max_chars: int) -> str:
    """Return *value* as a stripped string clipped to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return "…\n" + text[-max_chars:]


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO instant into an aware UTC datetime (None when invalid)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_due(value: Any, now: Optional[datetime] = None) -> bool:
    """Return True when the ISO instant *value* is in the past."""
    moment = _parse_iso(value)
    if moment is None:
        return False
    return moment <= (now or datetime.now(tz=timezone.utc))


def _ticket_lock_name(ticket_id: str) -> str:
    """Return a filesystem-safe lock file name for a ticket."""
    digest = hashlib.sha1(str(ticket_id or "").encode("utf-8")).hexdigest()[:20]
    return f"{digest}.lock"


def _ticket_flag_name(ticket_id: str) -> str:
    """Return a filesystem-safe cancellation flag name for a ticket."""
    digest = hashlib.sha1(str(ticket_id or "").encode("utf-8")).hexdigest()[:20]
    return f"{digest}.cancel"


@dataclass
class ValidationResult:
    """Outcome of one ticket validation gate."""

    ok: bool = False
    command: str = ""
    output: str = ""
    reason: str = ""
    duration_seconds: float = 0.0
    blocked: bool = False
    source: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """Return a compact markdown block with the evidence."""
        headline = "PASS" if self.ok else ("BLOCKED" if self.blocked else "FAIL")
        lines = [f"**Validation {headline}**"]
        if self.command:
            lines.append(f"- command: `{self.command}`")
        if self.reason:
            lines.append(f"- reason: {self.reason}")
        if self.duration_seconds:
            lines.append(f"- duration: {self.duration_seconds:.1f}s")
        if self.output:
            lines += ["", "```", _clip(self.output, MAX_VALIDATION_OUTPUT_CHARS), "```"]
        return "\n".join(lines)


class _TicketLock:
    """Cross-process, non-blocking per-ticket lock (flock with a fallback)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._handle = None
        self._thread_lock = threading.Lock()

    def acquire(self) -> bool:
        """Try to take the lock; return False when another run owns it."""
        if not self._thread_lock.acquire(blocking=False):
            return False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
            try:
                import fcntl
            except ImportError:  # pragma: no cover - non POSIX
                fcntl = None
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    self._thread_lock.release()
                    return False
            self._handle = handle
            return True
        except OSError as ex:  # pragma: no cover - read-only control plane
            logger.warning("ticket autonomy: cannot lock %s: %s", self.path, ex)
            self._thread_lock.release()
            return False

    def release(self) -> None:
        """Release the lock (idempotent)."""
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:  # pragma: no cover - best effort
                pass
            try:
                handle.close()
            except Exception:  # pragma: no cover
                pass
        try:
            self._thread_lock.release()
        except RuntimeError:  # pragma: no cover - already released
            pass


class TicketAutonomyService:
    """
    Runs, configures and reports the autonomous work of the tickets of a project.

    Args:
        settings: Project settings of the TARGET project (the ticket owner).
        chat_manager: Optional project ``ChatManager`` (built on demand).
        runner: Optional async callable replacing the real agent turn (tests).
            Signature: ``runner(chat=..., system=..., tool_allowlist=...)``.
        validator: Optional sync callable replacing the validation gate (tests).
            Signature: ``validator(chat=..., settings=..., config=...)`` and it
            must return a :class:`ValidationResult`.
        event_manager: Optional event manager for live UI notifications.
        user: Optional user/agent used by the AI call.
    """

    def __init__(
        self,
        settings: Any,
        chat_manager: Any = None,
        runner: Optional[Callable[..., Any]] = None,
        validator: Optional[Callable[..., ValidationResult]] = None,
        event_manager: Any = None,
        user: Any = None,
    ) -> None:
        if settings is None:
            raise TicketError("ticket autonomy requires the project settings")
        self.settings = settings
        self.chat_manager = chat_manager
        self.user = user
        self._runner = runner
        self._validator = validator
        self._event_manager = event_manager
        self.ticket_service = TicketService(
            settings=settings, chat_manager=chat_manager, user=getattr(user, "username", None)
        )
        self.codx_path = str(getattr(settings, "codx_path", "") or "")

    # -- dependencies -----------------------------------------------------

    def _profiles(self) -> Any:
        """Return the project ProfileManager."""
        from codx.junior.profiles.profile_manager import ProfileManager

        return ProfileManager(settings=self.settings)

    def _events(self) -> Any:
        """Return (creating if needed) the event manager used by the run."""
        if self._event_manager is None:
            try:
                from codx.junior.events.event_manager import EventManager

                self._event_manager = EventManager(codx_path=self.codx_path)
            except Exception as ex:  # pragma: no cover - notifications optional
                logger.debug("ticket autonomy: no event manager: %s", ex)
        return self._event_manager

    # -- runtime folders / locks -----------------------------------------

    def _runtime_path(self, *parts: str) -> str:
        """
        Return a path under the project runtime folder for the ticket runtime.

        Locks and cancellation flags churn on every run and are not source code:
        like the actor worker heartbeats they live in the installation runtime
        folder (``CODX_JUNIOR_RUNTIME_PATH``, ``/tmp`` by default), never in the
        repository.
        """
        from codx.junior.runtime import runtime_dir

        return os.path.join(runtime_dir(self.codx_path, "tickets"), *parts)

    @contextmanager
    def ticket_lock(self, ticket_id: str) -> Iterator[bool]:
        """Try to take the exclusive lock of one ticket run."""
        lock = _TicketLock(self._runtime_path(LOCKS_DIR, _ticket_lock_name(ticket_id)))
        acquired = lock.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def _clear_cancel(self, ticket_id: str) -> None:
        """Remove the cancellation flag of a ticket (start of a run)."""
        path = self._runtime_path(CANCELLED_DIR, _ticket_flag_name(ticket_id))
        try:
            os.remove(path)
        except OSError:
            pass

    def _cancel_requested(self, ticket_id: str) -> bool:
        """Return True when a cancel flag exists for the ticket."""
        return os.path.exists(self._runtime_path(CANCELLED_DIR, _ticket_flag_name(ticket_id)))

    def _write_cancel(self, ticket_id: str) -> None:
        """Create the cancellation flag of a ticket."""
        path = self._runtime_path(CANCELLED_DIR, _ticket_flag_name(ticket_id))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(now_iso())
        except OSError as ex:  # pragma: no cover - control plane must be writable
            logger.warning("ticket autonomy: cannot write cancel flag: %s", ex)

    # -- resolution -------------------------------------------------------

    def _target_chat(self, ticket_id: str) -> Tuple[Any, Any]:
        """
        Resolve a ticket chat and assert this project is its target.

        Raises:
            TicketError: When the ticket does not exist or belongs to another
                project (only the target owns the queue and may run it).
        """
        chat, manager, _target = self.ticket_service._resolve_ticket_chat(ticket_id)
        if chat is None or manager is None or getattr(chat, "ticket", None) is None:
            raise TicketError(f"ticket '{ticket_id}' not found")
        side = self.ticket_service._assert_may_access(chat, manager)
        if side != TICKET_SIDE_TARGET:
            raise TicketError(
                "only the target project can run an autonomous ticket: "
                f"'{getattr(chat.ticket, 'target_project_name', '')}' owns the queue"
            )
        # The column is the visible state: a human dragging the card to
        # Accepted/Done must be reflected before the runtime decides anything.
        self.ticket_service._sync_status_from_column(
            chat=chat, manager=manager, persist=True
        )
        return chat, manager

    def _profile_names(self) -> Dict[str, str]:
        """Return ``{lowercase name: canonical name}`` of the project profiles."""
        try:
            profiles = self._profiles().list_all_profiles() or []
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("ticket autonomy: cannot list profiles: %s", ex)
            return {}
        result: Dict[str, str] = {}
        for profile in profiles:
            name = str(getattr(profile, "name", "") or "").strip()
            if name:
                result[name.lower()] = name
        return result

    def resolve_profile(self, config: TicketAutonomy, chat: Any) -> str:
        """
        Resolve the profile (agent) that works the ticket.

        Args:
            config: Autonomy declaration.
            chat: Ticket chat (``chat.profiles`` is the target's suggestion).

        Returns:
            The canonical profile name, or ``""`` when the project has none.

        Raises:
            TicketError: When the declaration names a profile that does not exist.
        """
        profiles = self._profile_names()
        wanted = str(config.profile or "").strip().lstrip("@")
        if wanted:
            if wanted.lower() in profiles:
                return profiles[wanted.lower()]
            raise TicketError(
                f"the autonomy profile '{wanted}' does not exist in this project "
                f"(profiles: {', '.join(sorted(profiles.values())) or 'none'})"
            )
        for candidate in list(getattr(chat, "profiles", None) or []):
            name = str(candidate or "").strip().lstrip("@")
            if name and name.lower() in profiles:
                return profiles[name.lower()]
        for candidate in DEFAULT_TICKET_PROFILE_CANDIDATES:
            if candidate.lower() in profiles:
                return profiles[candidate.lower()]
        return next(iter(profiles.values()), "")

    #: Ticket-management tools are never handed to the autonomous run: the
    #: agent must not be able to close its own ticket or weaken its validation
    #: gate. The runtime owns the lifecycle; the agent owns the code.
    RUN_BLOCKED_TOOLS = frozenset({
        "ticket_create",
        "ticket_update",
        "ticket_abandon",
        "ticket_autonomy",
    })

    def tool_allowlist(self) -> Optional[set]:
        """
        Return the tool allowlist of an autonomous run (``None`` = profile grant).

        The list is the whole registry minus the ticket-management tools. The
        gate is enforced by ``ChatEngine`` after the profile grants, so a linked
        ``codx-junior`` profile cannot hand the agent the very tools that would
        let it mark the ticket done without a green validation.
        """
        try:
            from codx.junior.tools import all_tool_names

            return {
                name for name in all_tool_names()
                if name not in self.RUN_BLOCKED_TOOLS
            }
        except Exception as ex:  # pragma: no cover - registry import failure
            # Fail closed: ``None`` would mean "profile grant" and could hand
            # the agent the ticket-management tools.
            logger.error("ticket autonomy: cannot build the tool allowlist: %s", ex)
            return set()

    # -- configure / queue / cancel --------------------------------------

    def _merge_config(
        self,
        current: Optional[TicketAutonomy],
        *,
        enabled: Optional[bool] = None,
        profile: Optional[str] = None,
        auto_accept: Optional[bool] = None,
        max_rounds: Optional[int] = None,
        max_runtime_minutes: Optional[int] = None,
        budget_cxjcoins: Optional[float] = None,
        validation: Optional[Any] = None,
    ) -> TicketAutonomy:
        """Merge only the provided fields into the current declaration."""
        data = current.model_dump() if current is not None else {}
        updates: Dict[str, Any] = {
            "enabled": enabled,
            "profile": profile,
            "auto_accept": auto_accept,
            "max_rounds": max_rounds,
            "max_runtime_minutes": max_runtime_minutes,
            "budget_cxjcoins": budget_cxjcoins,
        }
        for key, value in updates.items():
            if value is not None:
                data[key] = value
        if validation is not None:
            if isinstance(validation, TicketValidation):
                data["validation"] = validation.model_dump()
            elif isinstance(validation, dict):
                merge = dict(data.get("validation") or {})
                merge.update(validation)
                data["validation"] = merge
            else:
                raise TicketError("validation must be an object")
        return TicketAutonomy(**data).normalized()

    def configure(self, ticket_id: str, **options: Any) -> Dict[str, Any]:
        """
        Create/update the autonomy declaration of a ticket (target side).

        Args:
            ticket_id: Ticket id (== chat id).
            **options: ``enabled``, ``profile``, ``auto_accept``, ``max_rounds``,
                ``max_runtime_minutes``, ``budget_cxjcoins``, ``validation`` and
                ``accept`` (accept the open ticket now).

        Returns:
            The public status payload of the ticket.

        Raises:
            TicketError: When the ticket is not owned by this project or it is
                already closed.
        """
        chat, manager = self._target_chat(ticket_id)
        ticket = chat.ticket
        if ticket.status in TICKET_TERMINAL_STATUSES:
            raise TicketError(
                f"ticket '{ticket_id}' is {ticket.status}; autonomy cannot be changed"
            )
        accept = bool(options.pop("accept", False))
        current = ticket.autonomy or TicketAutonomy()
        config = self._merge_config(
            current,
            enabled=options.get("enabled"),
            profile=options.get("profile"),
            auto_accept=options.get("auto_accept"),
            max_rounds=options.get("max_rounds"),
            max_runtime_minutes=options.get("max_runtime_minutes"),
            budget_cxjcoins=options.get("budget_cxjcoins"),
            validation=options.get("validation"),
        )
        if config.enabled:
            if config.state in TICKET_AUTONOMY_RUNNABLE_STATES:
                config.mark_queued()
            if accept or (config.auto_accept and ticket.status == TICKET_STATUS_OPEN):
                accept = True
        else:
            if config.state == TICKET_AUTONOMY_RUNNING:
                raise TicketError(
                    f"ticket '{ticket_id}' is running; cancel the run before "
                    "disabling autonomy"
                )
            if config.state in (TICKET_AUTONOMY_QUEUED, TICKET_AUTONOMY_FAILED, TICKET_AUTONOMY_BLOCKED):
                config.state = TICKET_AUTONOMY_OFF
        config.updated_at = now_iso()
        ticket.autonomy = config

        # Accept before saving so a single write persists both changes.
        if accept and ticket.status == TICKET_STATUS_OPEN:
            previous = ticket.status
            ticket.status = TICKET_STATUS_ACCEPTED
            chat.column = column_for_status(TICKET_STATUS_ACCEPTED)
            ticket.append_event(
                action=TICKET_ACTION_STATUS,
                project_id=getattr(self.settings, "project_id", None),
                project_name=getattr(self.settings, "project_name", None),
                user=getattr(self.user, "username", None) or "ticket-autonomy",
                note="accepted by autonomy",
                from_status=previous,
                status=TICKET_STATUS_ACCEPTED,
            )

        saved = manager.save_chat(chat=chat) or chat
        self._notify(saved, "ticket_autonomy_configured")
        return self._entry(saved)

    def queue(self, ticket_id: str) -> Dict[str, Any]:
        """Mark a ticket as queued (it will be picked up by the worker)."""
        return self.configure(ticket_id, enabled=True, accept=True)

    def cancel(self, ticket_id: str) -> Dict[str, Any]:
        """Cancel a queued/running ticket run without closing the ticket."""
        chat, manager = self._target_chat(ticket_id)
        ticket = chat.ticket
        if ticket.status in TICKET_TERMINAL_STATUSES:
            raise TicketError(
                f"ticket '{ticket_id}' is {ticket.status}; there is no run to cancel"
            )
        config = (ticket.autonomy or TicketAutonomy()).normalized()
        if config.state == TICKET_AUTONOMY_PASSED:
            raise TicketError(
                f"ticket '{ticket_id}' already finished with a green validation"
            )
        self._write_cancel(ticket_id)
        if config.state == TICKET_AUTONOMY_RUNNING:
            self._cancel_inflight(chat)
        config.state = TICKET_AUTONOMY_CANCELLED
        config.finished_at = now_iso()
        config.updated_at = config.finished_at
        ticket.autonomy = config
        ticket.append_event(
            action=TICKET_ACTION_UPDATED,
            project_id=getattr(self.settings, "project_id", None),
            project_name=getattr(self.settings, "project_name", None),
            user=getattr(self.user, "username", None) or "ticket-autonomy",
            note="autonomous run cancelled",
            status=ticket.status,
        )
        saved = manager.save_chat(chat=chat) or chat
        self._notify(saved, "ticket_autonomy_cancelled")
        return self._entry(saved)

    def _cancel_inflight(self, chat: Any) -> None:
        """Ask the in-flight agent turn of *chat* to stop."""
        try:
            from codx.junior.ai.cancellation import CANCELLATION_REGISTRY

            CANCELLATION_REGISTRY.cancel(chat.doc_id)
        except Exception as ex:  # pragma: no cover - cancellation is best effort
            logger.debug("ticket autonomy: cannot cancel the turn: %s", ex)

    # -- catalogue --------------------------------------------------------

    def entries(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        """Return the autonomous tickets stored in this project (newest first)."""
        records: List[Dict[str, Any]] = []
        manager = self.ticket_service.own_manager()
        for chat in self.ticket_service._own_ticket_chats():
            try:
                self.ticket_service._sync_status_from_column(
                    chat=chat, manager=manager, persist=True
                )
            except Exception as ex:  # pragma: no cover - listing is best effort
                logger.debug("ticket autonomy: cannot sync ticket column: %s", ex)
            autonomy = getattr(getattr(chat, "ticket", None), "autonomy", None)
            if autonomy is None:
                continue
            if not include_disabled and not autonomy.enabled:
                continue
            records.append(self._entry(chat))
        records.sort(
            key=lambda entry: str((entry.get("autonomy") or {}).get("updated_at") or ""),
            reverse=True,
        )
        return records

    def status(self, ticket_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Return one ticket's autonomy state or the whole target queue.

        A ticket id may also be read from the SOURCE project: the requester sees
        the same autonomy state, flagged ``can_configure=False``. Changing or
        running it still requires the target project.
        """
        if ticket_id:
            chat, manager, _target = self.ticket_service._resolve_ticket_chat(ticket_id)
            if chat is None or manager is None or getattr(chat, "ticket", None) is None:
                raise TicketError(f"ticket '{ticket_id}' not found")
            side = self.ticket_service._assert_may_access(chat, manager)
            self.ticket_service._sync_status_from_column(
                chat=chat, manager=manager, persist=True
            )
            return self._entry(chat, side=side)
        try:
            from codx.junior.ticketing.worker import worker_present
        except Exception:  # pragma: no cover - worker module is optional
            worker_present = lambda: False  # noqa: E731
        return {
            "tickets": self.entries(),
            "worker_present": bool(worker_present()),
        }

    def _entry(self, chat: Any, side: str = TICKET_SIDE_TARGET) -> Dict[str, Any]:
        """Return the public payload of one ticket (side-aware)."""
        ticket = chat.ticket
        record = TicketRecord.from_chat(chat, direction=side)
        autonomy = getattr(ticket, "autonomy", None)
        return {
            "ticket_id": getattr(ticket, "id", "") or getattr(chat, "id", ""),
            "chat_id": getattr(chat, "id", ""),
            "name": getattr(chat, "name", ""),
            "status": getattr(ticket, "status", ""),
            "column": getattr(chat, "column", ""),
            "kind": getattr(ticket, "kind", ""),
            "source_project_name": getattr(ticket, "source_project_name", ""),
            "target_project_name": getattr(ticket, "target_project_name", ""),
            "can_configure": side == TICKET_SIDE_TARGET,
            "direction": side,
            "autonomy": autonomy.model_dump() if hasattr(autonomy, "model_dump") else autonomy,
            "record": record.model_dump(),
        }

    # -- candidates -------------------------------------------------------

    def due_candidates(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Return the autonomous tickets that should run now.

        A ticket is due when its autonomy is enabled, it is still open/accepted,
        and its state is ``queued`` (or ``failed`` with a due ``next_retry_at``,
        or ``running`` but abandoned). Closed and cancelled tickets are never
        returned.

        Args:
            now: Optional reference instant (tests).

        Returns:
            A list of compact entries with ``ticket_id``, ``name`` and ``state``.
        """
        if not autonomy_enabled():
            return []
        moment = now or datetime.now(tz=timezone.utc)
        candidates: List[Dict[str, Any]] = []
        manager = self.ticket_service.own_manager()
        for chat in self.ticket_service._own_ticket_chats():
            try:
                # The board column is the human's interface: dragging the card
                # to Accepted starts the ticket, Done/Declined stops it.
                self.ticket_service._sync_status_from_column(
                    chat=chat, manager=manager, persist=True
                )
            except Exception as ex:  # pragma: no cover - never break the scan
                logger.debug("ticket autonomy: cannot sync ticket column: %s", ex)
            ticket = getattr(chat, "ticket", None)
            autonomy = getattr(ticket, "autonomy", None)
            if ticket is None or autonomy is None or not autonomy.enabled:
                continue
            if getattr(ticket, "status", "") in TICKET_TERMINAL_STATUSES:
                continue
            if getattr(ticket, "status", "") not in (TICKET_STATUS_OPEN, TICKET_STATUS_ACCEPTED):
                continue
            if getattr(ticket, "status", "") == TICKET_STATUS_OPEN and not autonomy.auto_accept:
                # Waiting for the target to accept: the worker would only fail.
                continue
            state = str(autonomy.state or TICKET_AUTONOMY_OFF)
            due = state in (TICKET_AUTONOMY_QUEUED, TICKET_AUTONOMY_OFF)
            if state == TICKET_AUTONOMY_FAILED:
                due = bool(autonomy.next_retry_at) and _is_due(autonomy.next_retry_at, moment)
            if state == TICKET_AUTONOMY_RUNNING:
                started = _parse_iso(autonomy.started_at)
                limit = max(1, int(autonomy.max_runtime_minutes or 30)) * 60 + STALE_GRACE_SECONDS
                due = started is None or (moment - started).total_seconds() > limit
            if not due:
                continue
            candidates.append({
                "ticket_id": str(getattr(ticket, "id", "") or getattr(chat, "id", "")),
                "chat_id": str(getattr(chat, "id", "")),
                "name": str(getattr(chat, "name", "") or ""),
                "state": state,
                "status": getattr(ticket, "status", ""),
            })
        return candidates

    async def run_due(self) -> List[Dict[str, Any]]:
        """Run every due autonomous ticket of this project."""
        results: List[Dict[str, Any]] = []
        for candidate in self.due_candidates():
            try:
                results.append(await self.run(candidate["ticket_id"]))
            except Exception as ex:  # pragma: no cover - one ticket is not fatal
                logger.exception(
                    "ticket autonomy: ticket '%s' failed to run: %s",
                    candidate["ticket_id"], ex,
                )
                results.append({
                    "ticket_id": candidate["ticket_id"],
                    "error": str(ex),
                    "state": TICKET_AUTONOMY_FAILED,
                })
        return results

    # -- the run ----------------------------------------------------------

    async def run(self, ticket_id: str, *, force: bool = False) -> Dict[str, Any]:
        """
        Run one autonomous ticket now.

        Args:
            ticket_id: Ticket id (== chat id).
            force: Run even when the declaration is disabled (e.g. a human
                pressing "Run now"); the run is still bounded and validated.

        Returns:
            The public payload of the ticket after the run.

        Raises:
            TicketError: When the ticket does not exist, this project is not the
                target, it is closed, or another run owns the lock.
        """
        started_monotonic = time.monotonic()
        chat, manager = self._target_chat(ticket_id)
        config = (chat.ticket.autonomy or TicketAutonomy()).normalized()
        if not config.enabled and not force:
            raise TicketError(
                f"autonomy is disabled for ticket '{ticket_id}'; enable it first "
                "(ticket_autonomy action='enable')"
            )
        if chat.ticket.status in TICKET_TERMINAL_STATUSES:
            raise TicketError(
                f"ticket '{ticket_id}' is {chat.ticket.status}; nothing to run"
            )
        if config.state == TICKET_AUTONOMY_RUNNING:
            # A live lock would make the run below fail; a stale state is recovered.
            started = _parse_iso(config.started_at)
            limit = max(1, int(config.max_runtime_minutes or 30)) * 60 + STALE_GRACE_SECONDS
            stale = (
                started is None
                or (datetime.now(tz=timezone.utc) - started).total_seconds() > limit
            )
            if not stale:
                raise TicketError(
                    f"ticket '{ticket_id}' is already running; cancel it or wait"
                )

        with self.ticket_lock(ticket_id) as acquired:
            if not acquired:
                raise TicketError(
                    f"ticket '{ticket_id}' is already being worked by another run"
                )
            self._clear_cancel(ticket_id)
            # Re-read under the lock: configure/cancel may have changed the ticket.
            chat, manager = self._target_chat(ticket_id)
            return await self._run_locked(
                chat=chat,
                manager=manager,
                ticket_id=ticket_id,
                force=force,
                started_monotonic=started_monotonic,
            )

    async def _run_locked(
        self,
        *,
        chat: Any,
        manager: Any,
        ticket_id: str,
        force: bool,
        started_monotonic: float,
    ) -> Dict[str, Any]:
        """Body of a run; the caller holds the ticket lock."""
        ticket = chat.ticket
        config = (ticket.autonomy or TicketAutonomy()).normalized()
        if not config.enabled:
            if not force:
                raise TicketError(f"autonomy is disabled for ticket '{ticket_id}'")
            config.enabled = True
        if config.state == TICKET_AUTONOMY_CANCELLED and not force:
            # A cancel may have landed between the candidate scan and the lock;
            # the queued run must honour it instead of restarting the work.
            raise TicketError(f"ticket '{ticket_id}' run was cancelled")
        # A new run never inherits the workspace of a previous one.
        config.workspace_branch = None
        config.workspace_path = None

        # Triage: an open ticket is accepted by the runtime when allowed. A
        # forced run is an explicit target action ("Run now"), so it may accept
        # too; the background worker always uses force=False and honours consent.
        if ticket.status == TICKET_STATUS_OPEN:
            if config.auto_accept or force:
                self.ticket_service.update(
                    ticket_id=ticket_id,
                    status=TICKET_STATUS_ACCEPTED,
                    note="autonomy accepted the ticket",
                )
                chat, manager = self._target_chat(ticket_id)
                ticket = chat.ticket
                config = (ticket.autonomy or TicketAutonomy()).normalized()
                config.enabled = True
            else:
                raise TicketError(
                    f"ticket '{ticket_id}' is open and auto_accept is disabled: "
                    "accept it first or enable auto_accept"
                )
        if ticket.status != TICKET_STATUS_ACCEPTED:
            raise TicketError(
                f"ticket '{ticket_id}' is {ticket.status}; only an accepted ticket "
                "can be worked autonomously"
            )

        try:
            profile = self.resolve_profile(config, chat)
        except TicketError as ex:
            return self._finish_failed(
                chat=chat, manager=manager, config=config,
                error=str(ex), state=TICKET_AUTONOMY_BLOCKED,
                content="", validation=None,
                started_monotonic=started_monotonic,
            )
        if not profile:
            return self._finish_failed(
                chat=chat, manager=manager, config=config,
                error=(
                    "no profile available to work the ticket: create/link a "
                    "coding profile (e.g. `codx-junior`) in this project"
                ),
                state=TICKET_AUTONOMY_BLOCKED,
                content="", validation=None,
                started_monotonic=started_monotonic,
            )

        config.profile = profile
        config.mark_running()
        chat.profiles = [profile]
        chat.estimation_cost = (
            float(config.budget_cxjcoins) if config.budget_cxjcoins > 0 else None
        )
        chat.ticket.autonomy = config
        self._save(chat, manager)
        self._audit(
            "ticket_autonomy_started",
            ticket_id=ticket_id,
            ticket=getattr(chat, "name", ""),
            profile=profile,
            max_rounds=config.max_rounds,
            max_runtime_minutes=config.max_runtime_minutes,
            budget_cxjcoins=config.budget_cxjcoins,
        )
        self._notify(chat, "ticket_autonomy_started")

        workspace, run_settings = self._workspace(chat, manager)
        if workspace is not None:
            config.workspace_branch = getattr(workspace, "branch", None)
            config.workspace_path = getattr(workspace, "path", None)
            chat.ticket.autonomy = config
            self._save(chat, manager)

        system = prompts.build_ticket_system(
            ticket_id=ticket_id,
            name=getattr(chat, "name", ""),
            description=getattr(chat, "description", ""),
            kind=getattr(ticket, "kind", ""),
            priority=getattr(chat, "priority", None),
            reference=getattr(ticket, "reference", None),
            source=getattr(ticket, "source_project_name", ""),
            target=getattr(ticket, "target_project_name", ""),
            validation=config.validation,
            branch=getattr(workspace, "branch", "") if workspace else "",
            workspace=getattr(workspace, "path", "") if workspace else "",
            started_at=config.started_at or now_iso(),
            max_rounds=config.max_rounds,
        )

        # The default runner must build the ChatEngine on the *workspace*
        # settings, otherwise the file/shell tools would edit the main checkout.
        # An injected runner (tests) keeps its own simple signature.
        injected_runner = self._runner

        async def _turn(chat: Any, system: Optional[str] = None, tool_allowlist: Any = None) -> Any:
            if injected_runner is not None:
                return await injected_runner(
                    chat=chat, system=system, tool_allowlist=tool_allowlist
                )
            return await self._run_agent_turn(
                chat=chat,
                system=system,
                tool_allowlist=tool_allowlist,
                settings=run_settings,
            )

        tool_allowlist = self.tool_allowlist()
        start_cost = float(getattr(chat, "cost", 0) or 0)
        deadline = started_monotonic + max(1, config.max_runtime_minutes) * 60
        contents: List[str] = []
        last_validation: Optional[ValidationResult] = None
        error = ""
        blocked = False
        cancelled = False
        rounds = 0

        for round_number in range(1, max(1, config.max_rounds) + 1):
            if self._cancel_requested(ticket_id):
                cancelled = True
                break
            elapsed = time.monotonic() - started_monotonic
            if time.monotonic() >= deadline:
                error = (
                    f"run exceeded {config.max_runtime_minutes} minutes "
                    f"({int(elapsed)}s elapsed)"
                )
                break
            spent = float(getattr(chat, "cost", 0) or 0) - start_cost
            if config.budget_cxjcoins > 0 and spent >= config.budget_cxjcoins:
                error = (
                    f"run budget exhausted: {spent:.4f} / "
                    f"{config.budget_cxjcoins:.4f} cxjcoins"
                )
                break

            if round_number == 1:
                prompt_text = prompts.build_round_prompt(
                    ticket_id=ticket_id,
                    name=getattr(chat, "name", ""),
                    description=getattr(chat, "description", ""),
                    validation=config.validation,
                    round=round_number,
                    max_rounds=config.max_rounds,
                )
            else:
                prompt_text = prompts.build_continue_prompt(
                    validation_output=(last_validation.output if last_validation else ""),
                    command=(last_validation.command if last_validation else ""),
                    round=round_number,
                    max_rounds=config.max_rounds,
                    elapsed=f"{int(elapsed)}s",
                )

            self._append_user_message(chat, prompt_text)
            self._save(chat, manager)
            rounds = round_number

            try:
                remaining = max(30.0, deadline - time.monotonic())
                await asyncio.wait_for(
                    _turn(chat=chat, system=system, tool_allowlist=tool_allowlist),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                error = f"agent round timed out after {config.max_runtime_minutes} minutes"
                break
            except Exception as ex:  # pragma: no cover - provider/runtime failures
                logger.exception("ticket autonomy: agent round failed: %s", ex)
                error = f"agent round failed: {ex}"
                break

            content = self._last_assistant(chat)
            if content:
                contents.append(content)
            config.rounds = round_number
            config.last_round_at = now_iso()
            config.updated_at = config.last_round_at
            chat.ticket.autonomy = config
            self._save(chat, manager)

            if self._cancel_requested(ticket_id):
                cancelled = True
                break

            validation = await asyncio.to_thread(
                self._validate, chat, run_settings, config
            )
            last_validation = validation
            config.last_validation = validation.describe()
            config.validation_passed = bool(validation.ok)
            config.updated_at = now_iso()
            chat.ticket.autonomy = config
            self._save(chat, manager)

            if validation.ok:
                break
            if validation.blocked:
                blocked = True
                error = validation.reason or "validation is not available"
                break
            if round_number >= config.max_rounds:
                error = (
                    f"validation still failing after {config.max_rounds} round(s): "
                    f"{validation.reason or 'red gate'}"
                )
                break

        content = "\n\n---\n\n".join(contents)
        # A human may have moved the card to Done/Declined while the agent was
        # working. Their decision wins: stop without overwriting the ticket.
        if chat.ticket.status in TICKET_TERMINAL_STATUSES or chat.ticket.status == TICKET_STATUS_DECLINED:
            config.last_error = (
                f"ticket left the accepted state externally ({chat.ticket.status})"
            )
            return self._finish_cancelled(
                chat=chat, manager=manager, config=config,
                content=content, validation=last_validation,
                started_monotonic=started_monotonic,
                reason=config.last_error,
            )
        if cancelled:
            return self._finish_cancelled(
                chat=chat, manager=manager, config=config,
                content=content, validation=last_validation,
                started_monotonic=started_monotonic,
            )
        if chat.ticket.status != TICKET_STATUS_ACCEPTED:
            config.last_error = (
                f"ticket left the accepted state externally ({chat.ticket.status})"
            )
            return self._finish_cancelled(
                chat=chat, manager=manager, config=config,
                content=content, validation=last_validation,
                started_monotonic=started_monotonic,
                reason=config.last_error,
            )
        if last_validation is not None and last_validation.ok:
            return self._finish_passed(
                chat=chat, manager=manager, config=config,
                content=content, validation=last_validation,
                started_monotonic=started_monotonic,
            )
        return self._finish_failed(
            chat=chat, manager=manager, config=config,
            error=error or "the run stopped without a green validation",
            state=TICKET_AUTONOMY_BLOCKED if blocked else TICKET_AUTONOMY_FAILED,
            content=content, validation=last_validation,
            started_monotonic=started_monotonic,
        )

    # -- workspace / turn -------------------------------------------------

    def _workspace(self, chat: Any, manager: Any) -> Tuple[Any, Any]:
        """Create/resolve the ticket worktree and its settings clone."""
        try:
            from codx.junior.task_workspace import TaskWorkspaceManager

            workspaces = TaskWorkspaceManager(self.settings)
            workspace = workspaces.ensure(chat)
            if workspace is None:
                return None, self.settings
            return workspace, workspaces.settings_for(workspace, self.settings)
        except Exception as ex:  # pragma: no cover - isolation is best effort
            logger.warning("ticket autonomy: cannot create the ticket workspace: %s", ex)
            return None, self.settings

    async def _run_agent_turn(
        self,
        *,
        chat: Any,
        system: Optional[str] = None,
        tool_allowlist: Optional[set] = None,
        settings: Any = None,
    ) -> Any:
        """
        Default runner: one full agent turn (tools included) on the ticket chat.

        Args:
            chat: Ticket chat.
            system: System prompt of the run.
            tool_allowlist: Optional structural tool restriction.
            settings: Settings clone rooted at the ticket workspace (falls back
                to the target project settings).

        The imports are local so the service can be imported without the AI stack
        (the worker/tests use an injected runner).
        """
        from codx.junior.chat.chat_engine import ChatEngine

        engine = ChatEngine(
            settings=settings or self.settings,
            event_manager=self._events(),
            user=self.user,
            tool_allowlist=tool_allowlist,
        )
        return await engine.chat_with_project(chat=chat, system=system)

    # -- validation -------------------------------------------------------

    def _validate(
        self,
        chat: Any,
        settings: Any,
        config: TicketAutonomy,
    ) -> ValidationResult:
        """Run the validation gate of the ticket (injected validator wins)."""
        if self._validator is not None:
            return self._validator(chat=chat, settings=settings, config=config)
        validation = config.validation
        source = validation.effective_source()
        if source == TICKET_VALIDATION_COMMANDS:
            return self._validate_commands(settings, validation)
        if source == TICKET_VALIDATION_NONE:
            if validation.required:
                return ValidationResult(
                    ok=False,
                    blocked=True,
                    reason="validation source is 'none' but validation is required",
                    source=source,
                )
            return ValidationResult(
                ok=True,
                reason="validation is not required for this ticket",
                source=source,
            )
        return self._validate_qa(settings, validation)

    def _validate_commands(
        self, settings: Any, validation: TicketValidation
    ) -> ValidationResult:
        """Run the explicit command lines of the ticket, one at a time."""
        started = time.monotonic()
        outputs: List[str] = []
        for raw in validation.commands:
            try:
                argv = shlex.split(raw)
            except ValueError as ex:
                return ValidationResult(
                    ok=False, command=raw, reason=f"invalid command line: {ex}",
                    source=TICKET_VALIDATION_COMMANDS,
                )
            if not argv:
                continue
            command_line = shlex.join(argv)
            block = self._ciso_block(command_line)
            if block:
                return ValidationResult(
                    ok=False, blocked=True, command=command_line,
                    reason=block, source=TICKET_VALIDATION_COMMANDS,
                )
            cwd = str(
                getattr(settings, "abs_project_path", "")
                or getattr(self.settings, "abs_project_path", "")
            )
            env = dict(os.environ)
            env.setdefault("CI", "1")
            env.setdefault("PYTHONUNBUFFERED", "1")
            try:
                completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                    argv,
                    cwd=cwd or None,
                    capture_output=True,
                    text=True,
                    timeout=validation.timeout_seconds,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return ValidationResult(
                    ok=False,
                    command=command_line,
                    reason=f"timed out after {validation.timeout_seconds}s",
                    source=TICKET_VALIDATION_COMMANDS,
                    duration_seconds=time.monotonic() - started,
                )
            except (OSError, ValueError) as ex:
                return ValidationResult(
                    ok=False,
                    command=command_line,
                    reason=f"cannot execute the command: {ex}",
                    source=TICKET_VALIDATION_COMMANDS,
                    duration_seconds=time.monotonic() - started,
                )
            output = _clip(
                "\n".join(part for part in (completed.stdout, completed.stderr) if part),
                MAX_VALIDATION_OUTPUT_CHARS,
            )
            outputs.append(f"$ {command_line}\n{output}".strip())
            if completed.returncode != 0:
                return ValidationResult(
                    ok=False,
                    command=command_line,
                    reason=f"exit code {completed.returncode}",
                    output="\n\n".join(outputs),
                    source=TICKET_VALIDATION_COMMANDS,
                    duration_seconds=time.monotonic() - started,
                )
        return ValidationResult(
            ok=True,
            command=" && ".join(validation.commands),
            output="\n\n".join(outputs),
            reason="all validation commands passed",
            source=TICKET_VALIDATION_COMMANDS,
            duration_seconds=time.monotonic() - started,
        )

    def _ciso_block(self, command_line: str) -> str:
        """Return the CISO block reason of a command, or ``""``."""
        try:
            from codx.junior.security.ciso import gate as ciso_gate

            verdict = ciso_gate.guard_command(command_line, settings=self.settings)
            if verdict is not None and getattr(verdict, "blocked", False):
                return f"blocked by the CISO command gate ({verdict.summary})"
        except Exception as ex:  # pragma: no cover - the gate must never break a run
            logger.debug("ticket autonomy: CISO gate unavailable: %s", ex)
        return ""

    def _validate_qa(
        self, settings: Any, validation: TicketValidation
    ) -> ValidationResult:
        """Run the project QA suite as the validation gate."""
        started = time.monotonic()
        try:
            from codx.junior.quality.qa import gate as qa_gate

            verdict = qa_gate.guard_tests(
                settings,
                suite_name=validation.suite or "auto",
                paths=validation.paths or None,
                timeout=validation.timeout_seconds,
            )
        except Exception as ex:  # pragma: no cover - QA import/runtime failure
            logger.exception("ticket autonomy: QA gate failed: %s", ex)
            return ValidationResult(
                ok=False,
                blocked=True,
                reason=f"the QA gate could not run: {ex}",
                source="auto",
                duration_seconds=time.monotonic() - started,
            )
        rule_ids = set(getattr(verdict, "rule_ids", []) or [])
        runs = list(getattr(verdict, "runs", []) or [])
        output_parts: List[str] = []
        commands: List[str] = []
        for run in runs:
            command = " ".join(getattr(run, "command", []) or [])
            if command:
                commands.append(command)
            output_parts.append(str(getattr(run, "summary", "") or ""))
            tail = str(getattr(run, "output_tail", "") or "")
            if tail:
                output_parts.append(tail)
        for finding in list(getattr(verdict, "findings", []) or []):
            output_parts.append(
                f"[{getattr(finding, 'severity', '')}] {getattr(finding, 'title', '')}"
                + (f" — {getattr(finding, 'evidence', '')}" if getattr(finding, "evidence", None) else "")
            )
        summary = str(getattr(verdict, "summary", "") or "")
        if summary:
            output_parts.insert(0, summary)
        disabled = summary.strip().lower().startswith("disabled")
        blocked = bool(
            getattr(verdict, "blocked", False)
            or "tests.no-suite" in rule_ids
            or disabled
        )
        ok = bool(getattr(verdict, "passed", False)) and not blocked
        reason = summary or ("validation passed" if ok else "validation failed")
        return ValidationResult(
            ok=ok,
            blocked=blocked,
            command=" && ".join(commands) or "qa:auto",
            output=_clip("\n".join(output_parts), MAX_VALIDATION_OUTPUT_CHARS),
            reason=reason,
            source="auto",
            duration_seconds=time.monotonic() - started,
            details={"rule_ids": sorted(rule_ids)},
        )

    # -- completion -------------------------------------------------------

    def _finish_passed(
        self,
        *,
        chat: Any,
        manager: Any,
        config: TicketAutonomy,
        content: str,
        validation: Optional[ValidationResult],
        started_monotonic: float,
    ) -> Dict[str, Any]:
        """Close the ticket as ``done`` (only called on a green gate)."""
        ticket = chat.ticket
        previous = ticket.status
        evidence = validation.describe() if validation else "_validation passed_"
        delivery = (
            (content.strip() + "\n\n---\n\n" if content.strip() else "")
            + evidence
            + "\n\n**Delivered.** The validation gate is green; the ticket is done."
        )
        config.mark_passed(delivery=_clip(delivery, 20000))
        ticket.autonomy = config
        ticket.status = TICKET_STATUS_DONE
        chat.column = column_for_status(TICKET_STATUS_DONE)
        chat.status = "done"
        chat.progress = 100
        ticket.append_event(
            action=TICKET_ACTION_STATUS,
            project_id=getattr(self.settings, "project_id", None),
            project_name=getattr(self.settings, "project_name", None),
            user=getattr(self.user, "username", None) or "ticket-autonomy",
            note="autonomous run passed validation",
            from_status=previous,
            status=TICKET_STATUS_DONE,
        )
        self._append_assistant_message(
            chat,
            "## Ticket delivered\n\n" + _clip(delivery, 16000),
        )
        self._save(chat, manager)
        self._commit(
            chat,
            message=f"ticket: {getattr(chat, 'name', '')} (autonomy validated)",
        )
        payload = self._entry(chat)
        payload["duration_seconds"] = time.monotonic() - started_monotonic
        self._audit(
            "ticket_autonomy_delivered",
            ticket_id=getattr(ticket, "id", ""),
            ticket=getattr(chat, "name", ""),
            rounds=config.rounds,
            attempts=config.attempts,
            workspace_branch=config.workspace_branch,
        )
        self._notify(chat, "ticket_autonomy_finished", status=TICKET_AUTONOMY_PASSED)
        return payload

    def _finish_failed(
        self,
        *,
        chat: Any,
        manager: Any,
        config: TicketAutonomy,
        error: str,
        state: str,
        content: str,
        validation: Optional[ValidationResult],
        started_monotonic: float,
    ) -> Dict[str, Any]:
        """Report a red/blocked run without closing the ticket."""
        ticket = chat.ticket
        evidence = validation.describe() if validation else ""
        report_parts = [
            f"## Autonomous run {state}",
            "",
            f"**Reason:** {error}",
        ]
        if evidence:
            report_parts += ["", evidence]
        if content.strip():
            report_parts += ["", "---", "", _clip(content, 12000)]
        report = "\n".join(report_parts)
        config.mark_failed(_clip(error, 2000), state=state)
        config.delivery = _clip(report, 20000)
        ticket.autonomy = config
        ticket.append_event(
            action=TICKET_ACTION_UPDATED,
            project_id=getattr(self.settings, "project_id", None),
            project_name=getattr(self.settings, "project_name", None),
            user=getattr(self.user, "username", None) or "ticket-autonomy",
            note=f"autonomous run {state}: {_clip(error, 300)}",
            status=ticket.status,
        )
        self._append_assistant_message(chat, _clip(report, 16000))
        self._save(chat, manager)
        self._commit(
            chat,
            message=f"wip: ticket {getattr(chat, 'name', '')} (autonomy {state})",
        )
        payload = self._entry(chat)
        payload["duration_seconds"] = time.monotonic() - started_monotonic
        self._audit(
            "ticket_autonomy_failed",
            ticket_id=getattr(ticket, "id", ""),
            ticket=getattr(chat, "name", ""),
            state=state,
            error_present=bool(error),
            rounds=config.rounds,
            attempts=config.attempts,
        )
        self._notify(chat, "ticket_autonomy_failed", status=state)
        return payload

    def _finish_cancelled(
        self,
        *,
        chat: Any,
        manager: Any,
        config: TicketAutonomy,
        content: str,
        validation: Optional[ValidationResult],
        started_monotonic: float,
        reason: str = "cancelled by user",
    ) -> Dict[str, Any]:
        """Stop the run by user request/external close, without changing the ticket status."""
        ticket = chat.ticket
        config.state = TICKET_AUTONOMY_CANCELLED
        config.finished_at = now_iso()
        config.last_error = reason
        config.updated_at = config.finished_at
        ticket.autonomy = config
        ticket.append_event(
            action=TICKET_ACTION_UPDATED,
            project_id=getattr(self.settings, "project_id", None),
            project_name=getattr(self.settings, "project_name", None),
            user=getattr(self.user, "username", None) or "ticket-autonomy",
            note=f"autonomous run stopped: {_clip(reason, 300)}",
            status=ticket.status,
        )
        self._append_assistant_message(
            chat,
            "## Autonomous run stopped\n\n"
            f"**Reason:** {reason}\n\n"
            "The ticket is not closed by the runtime; the work done so far is "
            "kept in the conversation and the ticket branch.",
        )
        self._save(chat, manager)
        self._commit(
            chat,
            message=f"wip: ticket {getattr(chat, 'name', '')} (autonomy cancelled)",
        )
        payload = self._entry(chat)
        payload["duration_seconds"] = time.monotonic() - started_monotonic
        self._audit(
            "ticket_autonomy_cancelled",
            ticket_id=getattr(ticket, "id", ""),
            ticket=getattr(chat, "name", ""),
            reason=_clip(reason, 2000),
        )
        self._notify(chat, "ticket_autonomy_cancelled", status=TICKET_AUTONOMY_CANCELLED)
        return payload

    # -- helpers ----------------------------------------------------------

    def _save(self, chat: Any, manager: Any) -> Any:
        """
        Persist the ticket chat without losing a concurrent human change.

        The run owns the agent loop, but the ticket is shared: the requester can
        comment, the target can rename it and a human can drag the card to
        ``Done`` or ``Declined`` while the agent works. ``save_chat`` writes the
        whole document, so saving the run's in-memory copy blindly would drop
        those changes. Before writing, the stored ticket is merged in:

        * messages and ticket history are unioned by ``doc_id`` (new ones kept);
        * the stored status/column wins when it moved to a terminal/declined
          state (a human decision is never undone by the runtime);
        * human-editable metadata (title, description, priority, tags,
          assignees, files) is taken from disk — the run only owns the autonomy
          declaration and its own progress fields.

        Args:
            chat: In-memory ticket chat.
            manager: Its ``ChatManager``.

        Returns:
            The persisted chat.
        """
        try:
            # Fast path: the known file. If a human moved the card to another
            # column the old path is gone, so resolve the current one (O(1) with
            # the chat index, an exact scan otherwise).
            path = str(getattr(chat, "file_path", "") or "")
            if not path or not os.path.isfile(path):
                path = manager.find_chat_path(chat.id) or ""
            stored = (
                manager.load_chat_from_path(chat_file=path, chat_only=False)
                if path and os.path.isfile(path)
                else None
            )
        except Exception as ex:  # pragma: no cover - defensive
            logger.debug("ticket autonomy: cannot reload ticket before save: %s", ex)
            stored = None
        if stored is not None and getattr(stored, "id", None) == getattr(chat, "id", None):
            self._merge_stored(chat, stored)
        if getattr(chat, "ticket", None) is not None:
            chat.ticket.updated_at = now_iso()
        try:
            return manager.save_chat(chat=chat)
        except Exception as ex:  # pragma: no cover - persistence is critical
            logger.exception("ticket autonomy: cannot save ticket '%s': %s", getattr(chat, "id", ""), ex)
            raise

    @staticmethod
    def _merge_stored(chat: Any, stored: Any) -> None:
        """Merge the persisted ticket into the in-memory run copy (see _save)."""
        stored_ticket = getattr(stored, "ticket", None)
        ticket = getattr(chat, "ticket", None)

        # Messages: union by doc_id, keeping the in-memory version of a message
        # the run itself just appended/updated.
        merged_messages: Dict[str, Any] = {}
        for message in list(getattr(stored, "messages", None) or []) + list(
            getattr(chat, "messages", None) or []
        ):
            # Legacy messages may have no doc_id: use content + timestamp so a
            # reload does not duplicate the whole history on every save.
            key = str(getattr(message, "doc_id", "") or "")
            if not key:
                digest = hashlib.sha1(
                    (
                        f"{getattr(message, 'role', '')}|"
                        f"{getattr(message, 'created_at', '')}|"
                        f"{getattr(message, 'content', '')}"
                    ).encode("utf-8")
                ).hexdigest()
                key = f"legacy:{digest}"
            merged_messages[key] = message
        chat.messages = list(merged_messages.values())

        # Human-editable metadata the run never sets.
        for field_name in (
            "name", "description", "priority", "tags", "assignees", "file_list",
            "due_date", "calendar_ids", "scheduled_start", "scheduled_end",
            "all_day", "users", "sprint", "sprints", "board",
        ):
            if hasattr(stored, field_name):
                try:
                    setattr(chat, field_name, getattr(stored, field_name))
                except Exception:  # pragma: no cover - defensive
                    pass

        # Keep cost monotonic: the run and the streaming engine may both add.
        try:
            chat.cost = max(float(getattr(stored, "cost", 0) or 0), float(getattr(chat, "cost", 0) or 0))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass

        # A human card move adds a status-history entry on disk: keep it.
        try:
            if len(getattr(stored, "status_history", None) or []) > len(
                getattr(chat, "status_history", None) or []
            ):
                chat.status_history = list(stored.status_history)
        except Exception:  # pragma: no cover - defensive
            pass

        if stored_ticket is None or ticket is None:
            return

        # Ticket history: union by event id.
        merged_history: Dict[str, Any] = {}
        for event in list(getattr(stored_ticket, "history", None) or []) + list(
            getattr(ticket, "history", None) or []
        ):
            key = str(getattr(event, "doc_id", "") or "")
            if not key:
                digest = hashlib.sha1(
                    (
                        f"{getattr(event, 'at', '')}|"
                        f"{getattr(event, 'action', '')}|"
                        f"{getattr(event, 'note', '')}"
                    ).encode("utf-8")
                ).hexdigest()
                key = f"legacy:{digest}"
            merged_history[key] = event
        ticket.history = list(merged_history.values())

        stored_status = str(getattr(stored_ticket, "status", "") or "")
        current_status = str(getattr(ticket, "status", "") or "")
        human_closed = stored_status in TICKET_TERMINAL_STATUSES or stored_status == TICKET_STATUS_DECLINED
        if human_closed and current_status not in TICKET_TERMINAL_STATUSES:
            ticket.status = stored_status
            chat.column = getattr(stored, "column", chat.column)
            chat.status = getattr(stored, "status", chat.status)
        elif (
            current_status in (TICKET_STATUS_OPEN, TICKET_STATUS_ACCEPTED)
            and stored_status in (TICKET_STATUS_OPEN, TICKET_STATUS_ACCEPTED)
            and stored_status != current_status
        ):
            # A human moved the card between Inbox and Accepted while the run
            # was working: honour it (the run keeps going only on Accepted).
            ticket.status = stored_status
            chat.column = getattr(stored, "column", chat.column)
            chat.status = getattr(stored, "status", chat.status)

        # A cancel/disable written by the API while the model was running wins
        # over the run's own (older) autonomy snapshot.
        stored_autonomy = getattr(stored_ticket, "autonomy", None)
        current_autonomy = getattr(ticket, "autonomy", None)
        if stored_autonomy is not None and current_autonomy is not None:
            stored_state = str(getattr(stored_autonomy, "state", "") or "")
            current_state = str(getattr(current_autonomy, "state", "") or "")
            if stored_state == TICKET_AUTONOMY_CANCELLED and current_state != TICKET_AUTONOMY_CANCELLED:
                ticket.autonomy = stored_autonomy
            elif (
                getattr(stored_autonomy, "enabled", True) is False
                and current_state == TICKET_AUTONOMY_RUNNING
            ):
                # Someone disabled the declaration; stop owning the state.
                current_autonomy.enabled = False

    def _append_user_message(self, chat: Any, content: str) -> None:
        """Append an automation instruction (the round work order) to the ticket."""
        from codx.junior.db import Message

        chat.messages.append(
            Message(
                doc_id=str(uuid.uuid4()),
                role="user",
                content=str(content or ""),
                user="ticket-autonomy",
                project_id=getattr(chat, "project_id", None),
                created_at=now_iso(),
                updated_at=now_iso(),
                done=True,
            )
        )
        chat.updated_at = now_iso()

    def _append_assistant_message(self, chat: Any, content: str) -> None:
        """Append a runtime report (delivery/failure) to the ticket conversation."""
        from codx.junior.db import Message

        chat.messages.append(
            Message(
                doc_id=str(uuid.uuid4()),
                role="assistant",
                content=str(content or ""),
                user=getattr(self.user, "username", None) or "ticket-autonomy",
                project_id=getattr(chat, "project_id", None),
                created_at=now_iso(),
                updated_at=now_iso(),
                done=True,
            )
        )
        chat.updated_at = now_iso()

    @staticmethod
    def _last_assistant(chat: Any) -> str:
        """Return the last assistant answer of the ticket conversation."""
        for message in reversed(getattr(chat, "messages", None) or []):
            if str(getattr(message, "role", "")) == "assistant":
                return str(getattr(message, "content", "") or "")
        return ""

    def _commit(self, chat: Any, message: str) -> None:
        """Commit the ticket workspace (best effort; the run already happened)."""
        autonomy = getattr(getattr(chat, "ticket", None), "autonomy", None)
        if not getattr(autonomy, "workspace_path", None):
            # No isolated worktree: never commit the main checkout on behalf of
            # a ticket (that would mix the agent's changes with the user's).
            return
        try:
            from codx.junior.task_workspace import TaskWorkspaceManager

            result = TaskWorkspaceManager(self.settings).commit(chat, message=message)
            if result.get("error"):
                logger.warning(
                    "ticket autonomy: cannot commit ticket workspace: %s",
                    result["error"],
                )
        except Exception as ex:  # pragma: no cover - commit is best effort
            logger.warning("ticket autonomy: commit failed: %s", ex)

    def _audit(self, event_type: str, **data: Any) -> None:
        """Append a ticket-autonomy event to the harness audit journal."""
        try:
            from codx.junior.harness.audit import record_audit

            record_audit(self.codx_path, event_type, **data)
        except Exception as ex:  # pragma: no cover - auditing is best effort
            logger.debug("ticket autonomy: cannot write audit event: %s", ex)

    def _notify(self, chat: Any, event_type: str, **data: Any) -> None:
        """Notify connected clients about a ticket change (best effort)."""
        manager = self._events()
        if manager is None:
            return
        try:
            emit = getattr(manager, "chat_event", None)
            if callable(emit):
                # The socket contract for a ticket is the same as any chat:
                # clients refresh the card when it changes.
                emit(chat=chat, event_type="changed")
                return
            send = getattr(manager, "send_event", None)
            if callable(send):
                send(message=f"{event_type} {data}".strip())
        except Exception as ex:  # pragma: no cover - notifications are best effort
            logger.debug("ticket autonomy: cannot emit event: %s", ex)


#: Convenience alias used by the API/tools.
AutonomyTicketService = TicketAutonomyService

__all__ = [
    "AutonomyTicketService",
    "TicketAutonomyService",
    "ValidationResult",
    "autonomy_enabled",
]

# Made with ❤️ by codx-junior
