"""
Agile clock: keep an agile turn FAST without ever killing it.

An agile turn (``codx-junior``, the default tier) promises an increment in less
than 20 minutes (``AGILE_TARGET_SECONDS``). The clock is used to STRESS the
model, never to break the task:

* at every checkpoint of the target (25%, 50%, 75%, the target itself and once
  per target period after it) the model receives a deterministic notice: finish
  the increment, batch the remaining calls, wrap up;
* the response message metadata is stamped with the state (``ok`` / ``warning``
  / ``overtime``) so the CLIENT can show a warning and the user decides what to
  do — stop the turn or split the task;
* the turn is never interrupted by the clock. Only the human stops it (or the
  stall detector of the turn store, which detects a hung execution, not a slow
  one).

This module is pure python (a monotonic clock, no I/O, no imports from
``smol_agent``), so it is fully unit-testable.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from codx.junior.ai.smol.constants import (
    AGILE_META_KEY,
    AGILE_NUDGE_FRACTIONS,
    AGILE_NUDGE_TEMPLATE,
    AGILE_OVERTIME_FOOTNOTE,
    AGILE_OVERTIME_TEMPLATE,
    AGILE_OVERTIME_USER_MESSAGE,
    AGILE_STATE_OK,
    AGILE_STATE_OVERTIME,
    AGILE_STATE_WARNING,
    AGILE_SYSTEM_SECTION,
    AGILE_TARGET_SECONDS,
    AGILE_WARNING_FRACTION,
    AGILE_WARNING_USER_MESSAGE,
)

# Level of a checkpoint reached BEFORE the agile target is broken.
LEVEL_HURRY: str = "hurry"
# Level of the checkpoint at/after the target.
LEVEL_OVERTIME: str = "overtime"


def format_duration(seconds: float) -> str:
    """
    Render a duration the way the notices read it ("4m 05s", "20m", "1m 3s").

    Args:
        seconds: Duration in seconds (negative values clamp to ``0s``).

    Returns:
        A compact human readable duration.
    """
    total = int(max(0.0, round(float(seconds or 0.0))))
    minutes, secs = divmod(total, 60)
    if not minutes:
        return f"{secs}s"
    if not secs:
        return f"{minutes}m"
    return f"{minutes}m {secs:02d}s"


@dataclass(frozen=True)
class AgileNudge:
    """
    One agility checkpoint reached inside an agile turn.

    Attributes:
        level:           ``"hurry"`` (before the target) or ``"overtime"``.
        checkpoint:      Seconds of the target that triggered the nudge.
        elapsed_seconds: Time spent when the nudge was produced.
        target_seconds:  Agile target of the turn.
        model_notice:    Text injected into the model conversation (this turn
                         only; never persisted in the chat).
        metadata:        Agile state to stamp on the response message.
    """

    level: str
    checkpoint: float
    elapsed_seconds: float
    target_seconds: float
    model_notice: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_overtime(self) -> bool:
        """True when the agile target is already broken."""
        return self.level == LEVEL_OVERTIME


class AgileClock:
    """
    Track the elapsed time of an agile turn and produce its checkpoints.

    The clock fires each checkpoint ONCE, in order, and can be asked for its
    state at any moment (``ok`` / ``warning`` / ``overtime``) so the metadata of
    the response message is always accurate even between checkpoints.

    Args:
        target_seconds: Agile target of the turn (0 disables the clock).
        nudge_seconds:  Extra first checkpoint (the old agile wall clock, 5 min).
                        Ignored when it is not strictly inside the target.
        started_at:     Monotonic start of the turn (defaults to "now").
        clock:          Time source, injectable for tests.
        fractions:      Fractions of the target used as checkpoints.
        warning_fraction: Fraction from which the state becomes ``warning``.
        mode:           Tier name stamped in the metadata (``agile`` or
                        ``max``). The clock is the same; only the label and the
                        target differ.
    """

    def __init__(
        self,
        target_seconds: float,
        nudge_seconds: float = 0.0,
        started_at: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
        fractions: Sequence[float] = AGILE_NUDGE_FRACTIONS,
        warning_fraction: float = AGILE_WARNING_FRACTION,
        metadata_key: str = AGILE_META_KEY,
        mode: str = "agile",
    ) -> None:
        self.target_seconds: float = max(0.0, float(target_seconds or 0.0))
        self.warning_fraction: float = float(warning_fraction)
        self.metadata_key: str = metadata_key
        self.mode: str = str(mode or "agile")
        self._clock: Callable[[], float] = clock or time.monotonic
        self.started_at: float = (
            self._clock() if started_at is None else float(started_at)
        )
        self._checkpoints: Tuple[float, ...] = self._build_checkpoints(
            nudge_seconds, fractions
        )
        self._fired: int = 0
        # Duration of the turn when it ended (None while it is running).
        self.finished_seconds: Optional[float] = None

    # ── Checkpoints ───────────────────────────────────────────────────────────

    def _build_checkpoints(
        self,
        nudge_seconds: float,
        fractions: Sequence[float],
    ) -> Tuple[float, ...]:
        """
        Build the ordered, de-duplicated list of checkpoints of this target.

        The list is finite so a runaway turn cannot fire an unbounded number of
        notices: before the target it holds the configured fractions, then the
        target itself and one reminder per target period up to four periods.

        Args:
            nudge_seconds: Optional extra first checkpoint.
            fractions:     Fractions of the target to use before the target.

        Returns:
            Sorted checkpoint offsets in seconds.
        """
        target = self.target_seconds
        if target <= 0:
            return ()
        points: List[float] = []
        if 0.0 < float(nudge_seconds or 0.0) < target:
            points.append(float(nudge_seconds))
        points.extend(f * target for f in fractions if 0.0 < f < 1.0)
        points.append(target)
        points.extend(k * target for k in (2.0, 3.0, 4.0))
        normalized = {round(point, 3) for point in points if point > 0.0}
        return tuple(sorted(normalized))

    @property
    def checkpoints(self) -> Tuple[float, ...]:
        """Checkpoint offsets of this clock, in seconds."""
        return self._checkpoints

    @property
    def nudges_fired(self) -> int:
        """How many checkpoints already produced a notice."""
        return self._fired

    # ── Time ──────────────────────────────────────────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        """Seconds spent so far (frozen once the turn has finished)."""
        if self.finished_seconds is not None:
            return self.finished_seconds
        return max(0.0, self._clock() - self.started_at)

    @property
    def remaining_seconds(self) -> float:
        """Seconds left before the agile target is broken (never negative)."""
        return max(0.0, self.target_seconds - self.elapsed_seconds)

    @property
    def overdue_seconds(self) -> float:
        """Seconds spent past the agile target (0 while inside it)."""
        return max(0.0, self.elapsed_seconds - self.target_seconds)

    @property
    def fraction(self) -> float:
        """Elapsed share of the target (0.0 when there is no target)."""
        if self.target_seconds <= 0:
            return 0.0
        return self.elapsed_seconds / self.target_seconds

    @property
    def state(self) -> str:
        """Current agile state of the message metadata: ok / warning / overtime."""
        if self.target_seconds <= 0:
            return AGILE_STATE_OK
        fraction = self.fraction
        if fraction >= 1.0:
            return AGILE_STATE_OVERTIME
        if fraction >= self.warning_fraction:
            return AGILE_STATE_WARNING
        return AGILE_STATE_OK

    def finish(self) -> None:
        """Freeze the clock at the moment the turn ended."""
        if self.finished_seconds is None:
            self.finished_seconds = max(0.0, self._clock() - self.started_at)

    # ── Notices ───────────────────────────────────────────────────────────────

    def _format_values(self) -> Dict[str, str]:
        """Values shared by every notice and by the client warnings."""
        return {
            "target": format_duration(self.target_seconds),
            "elapsed": format_duration(self.elapsed_seconds),
            "remaining": format_duration(self.remaining_seconds),
            "overdue": format_duration(self.overdue_seconds),
            "pct": str(int(round(self.fraction * 100))),
        }

    def metadata(self) -> Dict[str, Any]:
        """
        Build the agile state stamped on the response message metadata.

        The client uses it to show (and update) the warning: ``state`` is
        ``warning`` from ``warning_fraction`` of the target and ``overtime``
        past it, and ``warning`` carries the ready-to-show text (``None`` while
        everything is on time).

        Returns:
            The agile metadata dict (the caller stores it under
            ``meta_data["agile"]``).
        """
        values = self._format_values()
        state = self.state
        warning: Optional[str] = None
        if state == AGILE_STATE_OVERTIME:
            warning = AGILE_OVERTIME_USER_MESSAGE.format(**values)
        elif state == AGILE_STATE_WARNING:
            warning = AGILE_WARNING_USER_MESSAGE.format(**values)
        return {
            "mode": self.mode,
            "state": state,
            "target_seconds": round(self.target_seconds, 1),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "remaining_seconds": round(self.remaining_seconds, 1),
            "overdue_seconds": round(self.overdue_seconds, 1),
            "percent": int(round(self.fraction * 100)),
            "nudges": self._fired,
            "finished": self.finished_seconds is not None,
            "warning": warning,
        }

    def _build_nudge(self, level: str, checkpoint: float) -> AgileNudge:
        """
        Build the nudge (model notice + metadata) of *checkpoint*.

        Args:
            level:      ``LEVEL_HURRY`` or ``LEVEL_OVERTIME``.
            checkpoint: The checkpoint that fired.

        Returns:
            The immutable :class:`AgileNudge`.
        """
        values = self._format_values()
        template = (
            AGILE_OVERTIME_TEMPLATE if level == LEVEL_OVERTIME
            else AGILE_NUDGE_TEMPLATE
        )
        return AgileNudge(
            level=level,
            checkpoint=round(checkpoint, 1),
            elapsed_seconds=round(self.elapsed_seconds, 1),
            target_seconds=round(self.target_seconds, 1),
            model_notice=template.format(**values),
            metadata=self.metadata(),
        )

    def next_nudge(self) -> Optional[AgileNudge]:
        """
        Return the next checkpoint reached, or ``None`` when there is none.

        Every checkpoint fires once, in order. Checkpoints crossed between two
        calls collapse into a single notice (the latest one), so a slow tool can
        never flood the conversation with notices.

        Returns:
            The :class:`AgileNudge` to inject, or ``None``.
        """
        if not self._checkpoints:
            return None
        elapsed = self.elapsed_seconds
        latest: Optional[float] = None
        while self._fired < len(self._checkpoints):
            checkpoint = self._checkpoints[self._fired]
            if elapsed < checkpoint:
                break
            latest = checkpoint
            self._fired += 1
        if latest is None:
            return None
        level = LEVEL_OVERTIME if latest >= self.target_seconds else LEVEL_HURRY
        return self._build_nudge(level, latest)

    def overtime_footnote(self) -> Optional[str]:
        """
        Return the footnote appended to the answer of an overtime turn.

        Returns:
            The rendered footnote, or ``None`` when the turn ended inside the
            agile target.
        """
        if self.state != AGILE_STATE_OVERTIME:
            return None
        return AGILE_OVERTIME_FOOTNOTE.format(**self._format_values())


def agile_clock_for(policy: Any, started_at: Optional[float] = None) -> Optional[AgileClock]:
    """
    Build the advisory :class:`AgileClock` of an effort policy.

    Both effort tiers use the same clock: agile has a 20-minute target and max a
    one-hour one. ``None`` is returned only when the policy has no target or when
    a caller opted into an ENFORCED clock (nothing does by default).

    Args:
        policy:      The resolved :class:`~codx.junior.ai.smol.effort.EffortPolicy`.
        started_at:  Monotonic start of the turn.

    Returns:
        The clock of the turn, or ``None`` when it has no advisory target.
    """
    target = float(getattr(policy, "target_seconds", 0.0) or 0.0)
    if target <= 0.0 or getattr(policy, "stop_on_wall_clock", False):
        return None
    return AgileClock(
        target_seconds=target,
        nudge_seconds=float(getattr(policy, "wall_clock_seconds", 0.0) or 0.0),
        started_at=started_at,
        mode=str(getattr(policy, "name", "agile") or "agile"),
    )


def agile_system_section(target_seconds: float = AGILE_TARGET_SECONDS) -> str:
    """
    Render the AGILE MODE section injected into the system prompt.

    Args:
        target_seconds: Agile target of the tier.

    Returns:
        The rendered prompt section.
    """
    minutes = max(1, int(round(float(target_seconds or AGILE_TARGET_SECONDS) / 60.0)))
    return AGILE_SYSTEM_SECTION.format(target_minutes=minutes)


__all__ = [
    "AgileClock",
    "AgileNudge",
    "LEVEL_HURRY",
    "LEVEL_OVERTIME",
    "agile_clock_for",
    "agile_system_section",
    "format_duration",
]
