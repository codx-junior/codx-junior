"""
Turn scheduler of a meeting.

Only one participant speaks at a time. Human audio always wins: when a human
starts talking the scheduler *interrupts* the profile that is currently
answering (its text may already be in the chat, but its playback stops) and the
remaining queued profiles are skipped, exactly as a person would stop talking
when somebody else takes the floor.

The scheduler is process-wide because a meeting outlives the HTTP request that
started each turn. A per-meeting :class:`asyncio.Event` signals an interruption
to the running coroutine; cancelling the task releases the model call when
possible.
"""

from __future__ import annotations

import asyncio
import logging

from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class MeetingOrchestrator:
    """Runs at most one profile-answer loop per meeting."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._interrupts: Dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    def schedule(
        self,
        meet_id: str,
        runner: Callable[[asyncio.Event], Awaitable[None]],
    ) -> bool:
        """
        Start a new turn loop, cancelling the previous one for this meeting.

        Returns:
            ``True`` when the loop was scheduled.
        """
        if not meet_id or not callable(runner):
            return False
        self.interrupt(meet_id, reason="replaced")
        self._cancel_task(meet_id)
        event = asyncio.Event()
        self._interrupts[meet_id] = event

        async def _run() -> None:
            try:
                await runner(event)
            except asyncio.CancelledError:  # pragma: no cover - cooperative stop
                logger.info("Meeting '%s': profile turn cancelled", meet_id)
                raise
            except Exception as ex:  # noqa: BLE001 - never kill the event loop
                logger.exception("Meeting '%s': profile turn failed: %s", meet_id, ex)
            finally:
                current = self._interrupts.get(meet_id)
                if current is event:
                    self._interrupts.pop(meet_id, None)
                if self._tasks.get(meet_id) is asyncio.current_task():
                    self._tasks.pop(meet_id, None)

        self._tasks[meet_id] = asyncio.create_task(_run())
        return True

    def interrupt(self, meet_id: str, reason: str = "human-speaking") -> bool:
        """
        Signal the running loop of ``meet_id`` to stop speaking.

        Returns:
            True when a loop was running.
        """
        event = self._interrupts.get(meet_id)
        if event is None:
            return False
        event.set()
        logger.info(
            "Meeting '%s': profile turn interrupted (%s)", meet_id, reason
        )
        return True

    def _cancel_task(self, meet_id: str) -> None:
        """Cancel (and forget) the running loop, if any."""
        task = self._tasks.pop(meet_id, None)
        if task is not None and not task.done():
            task.cancel()

    def is_running(self, meet_id: str) -> bool:
        """True while a profile turn loop is scheduled for the meeting."""
        return self._task_for(meet_id) is not None

    def _task_for(self, meet_id: str) -> Optional[asyncio.Task]:
        """Return the live task of a meeting, cleaning finished ones."""
        task = self._tasks.get(meet_id)
        if task is not None and task.done():
            self._tasks.pop(meet_id, None)
            event = self._interrupts.get(meet_id)
            if event is not None and event.is_set():
                self._interrupts.pop(meet_id, None)
            return None
        return task

    def cancel(self, meet_id: str) -> None:
        """Cancel the running loop (meeting ended)."""
        self.interrupt(meet_id, reason="cancelled")
        self._cancel_task(meet_id)


_orchestrator: Optional[MeetingOrchestrator] = None


def get_orchestrator() -> MeetingOrchestrator:
    """Return the process-wide singleton orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MeetingOrchestrator()
    return _orchestrator
