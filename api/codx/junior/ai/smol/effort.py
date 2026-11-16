"""
Effort policy: how much work a single SmolAgent turn is allowed to do.

Effort is a RUNTIME policy, not prompt phrasing. It is resolved from the active
profile names and it drives the tool budget, the tool-limit continuation, the
wall-clock deadline, the granted tool set (prompt size) and the validation
breadth:

* ``codx-junior`` (AGILE, the DEFAULT tier) — small base budget, bounded course
  corrections when the budget is reached and an ADVISORY clock. A partial
  increment closed with its own summary is a valid outcome, but the budget is
  never allowed to dead-end the task: the model gets a smaller renewed budget
  and the user remains the only one who can stop it.
* ``codx-junior-max`` (MAX) — the heavier tier: bigger base budget, the same
  bounded course corrections and a long ADVISORY target.
* plain chats — moderate rounds, no continuation (the previous behaviour).

This module is pure python: no imports from ``smol_agent`` and no side effects
beyond reading an environment variable, so it is fully unit-testable.

Made with ❤️ by codx-junior
"""
import os
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from codx.junior.ai.smol.constants import (
    AGILE_CONTINUATIONS,
    AGILE_MAX_ITERATIONS,
    AGILE_MAX_TOOL_CALLS,
    AGILE_MAX_TOOL_ROUNDS,
    AGILE_TARGET_SECONDS,
    AGILE_WALL_CLOCK_SECONDS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_TOOL_CALLS,
    ENV_AGILE_TARGET_SECONDS,
    ENV_AGILE_WALL_CLOCK_SECONDS,
    MAX_MAX_ITERATIONS,
    MAX_MAX_TOOL_CALLS,
    MAX_MAX_TOOL_ROUNDS,
    MAX_TOOL_ROUNDS,
    MAX_WALL_CLOCK_SECONDS,
    TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS,
    TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS,
    TOOL_LIMIT_CONTINUATIONS,
)

# ── Profile names that select an effort tier ──────────────────────────────────
AGILE_PROFILE: str = "codx-junior"
MAX_PROFILE: str = "codx-junior-max"

# ── Resolved tier names ──────────────────────────────────────────────────────
AGILE_NAME: str = "agile"
MAX_NAME: str = "max"
DEFAULT_NAME: str = "default"

# ── Validation breadth (written once, reused by every caller) ────────────────
# AGILE: targeted tests for the touched files + ``qa_review_changes`` (a ``warn``
# is acceptable only when the gap is named in the close "Next tasks").
# MAX:   full suite + coverage + ``security_review_changes``.
AGILE_VALIDATION: str = "targeted+qa_review_changes"
MAX_VALIDATION: str = "full+coverage+security"

# Fields an ``overrides``/``settings`` mapping may set.
_OVERRIDABLE_FIELDS: Tuple[str, ...] = (
    "max_iterations",
    "max_tool_calls",
    "max_tool_rounds",
    "continue_on_tool_limit",
    "continuations",
    "continuation_max_iterations",
    "continuation_max_calls",
    "wall_clock_seconds",
    "target_seconds",
    "stop_on_wall_clock",
    "validation",
    "tool_allowlist",
)


@dataclass(frozen=True)
class EffortPolicy:
    """
    Concrete policy applied to one turn.

    Attributes:
        name:                      Resolved tier: ``agile``, ``max`` or ``default``.
        max_iterations:            FALLBACK model rounds for the turn, used
                                   only when neither the model nor the provider
                                   defines ``max_iterations`` (model > provider >
                                   this value).
        max_tool_calls:            FALLBACK tool calls per round batch, used
                                   only when neither the model nor the provider
                                   defines ``max_tool_calls``.
        max_tool_rounds:           LoopGuard sequential tool rounds.
        continue_on_tool_limit:    Whether reaching a limit grants a course
                                   correction instead of ending the turn.
                                   Agile and MAX both continue; only a plain
                                   chat and an explicit ``explicit=False``
                                   stop at the limit.
        continuations:             Course corrections granted after a limit.
        continuation_max_iterations: Model rounds granted per course correction.
        continuation_max_calls:    Tool calls granted per course correction.
        wall_clock_seconds:        Time budget of the turn; ``0`` means none.
                                   It is ENFORCED (the turn stops) only when
                                   ``stop_on_wall_clock`` is set; otherwise it
                                   is the first "hurry up" checkpoint.
        validation:                Validation breadth expected before delivery.
        tool_allowlist:            Granted tools; ``None`` means every tool
                                   (the future prompt-size lever).
        target_seconds:            Advisory target of the turn (the agile
                                   promise, 20 min). Reaching it NEVER stops
                                   the turn: it stresses the model and marks
                                   the response message so the client can warn
                                   the user. ``0`` disables the target.
        stop_on_wall_clock:        Opt-in: True makes ``wall_clock_seconds``
                                   ENFORCED (the turn stops at it). No built-in
                                   tier enables it: both agile and MAX are
                                   advisory, because only the user may end a
                                   task.
    """

    name: str
    max_iterations: int
    max_tool_calls: int
    max_tool_rounds: int
    continue_on_tool_limit: bool
    continuations: int
    continuation_max_iterations: int
    continuation_max_calls: int
    wall_clock_seconds: float
    validation: str
    tool_allowlist: Optional[Tuple[str, ...]] = None
    target_seconds: float = 0.0
    stop_on_wall_clock: bool = False

    @property
    def has_deadline(self) -> bool:
        """True when the turn has a time budget (enforced or advisory)."""
        return self.wall_clock_seconds > 0

    @property
    def enforces_wall_clock(self) -> bool:
        """True when reaching ``wall_clock_seconds`` STOPS the turn (MAX)."""
        return bool(self.stop_on_wall_clock) and self.wall_clock_seconds > 0

    @property
    def is_time_advisory(self) -> bool:
        """
        True when the turn is never stopped by the clock (AGILE).

        Such a turn gets agility notices, an advisory target and metadata marks
        instead of a hard stop: the user decides whether to stop the task.
        """
        return self.target_seconds > 0 and not self.stop_on_wall_clock


def _normalize_names(profile_names: Union[None, str, Sequence[str]]) -> Tuple[str, ...]:
    """
    Normalize profile names for comparison.

    Matches the convention already used by ``SmolAgent``: strip, lowercase and
    drop a leading ``@``. Accepts ``None``, a single name or a sequence.

    Args:
        profile_names: Raw profile names of the turn.

    Returns:
        Unique normalized names, order preserved.
    """
    if profile_names is None:
        return ()
    if isinstance(profile_names, str):
        profile_names = [profile_names]
    normalized = []
    for name in profile_names:
        value = str(name or "").strip().lower().lstrip("@").strip()
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _as_allowlist(value: Any) -> Optional[Tuple[str, ...]]:
    """Coerce an override value into a tool allowlist (``None`` = every tool)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    return tuple(str(item).strip() for item in value if str(item or "").strip())


def _env_float(name: str, default: float) -> float:
    """
    Read a float from the environment, falling back to ``default``.

    Args:
        name:    Environment variable name.
        default: Value used when the variable is unset or invalid.

    Returns:
        The configured float or ``default``.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _apply_overrides(
    policy: EffortPolicy,
    overrides: Optional[Mapping[str, Any]],
) -> EffortPolicy:
    """
    Apply a settings/overrides mapping on top of a policy.

    Unknown keys are ignored and ``None`` values keep the default, so callers
    can pass a whole settings dict. Kept deliberately simple: only the fields
    listed in ``_OVERRIDABLE_FIELDS`` can be overridden.

    Args:
        policy:    Base policy built from the constants.
        overrides: Optional mapping of field name to value.

    Returns:
        The policy with the overrides applied (frozen dataclass copy).
    """
    if not overrides:
        return policy
    if not isinstance(overrides, Mapping):
        raise TypeError(
            f"effort overrides must be a mapping, got {type(overrides).__name__}"
        )
    changes: Dict[str, Any] = {}
    for key in _OVERRIDABLE_FIELDS:
        if key not in overrides:
            continue
        value = overrides[key]
        if value is None:
            continue
        changes[key] = _as_allowlist(value) if key == "tool_allowlist" else value
    if not changes:
        return policy
    return replace(policy, **changes)


def agile_policy(settings: Optional[Mapping[str, Any]] = None) -> EffortPolicy:
    """
    Build the AGILE policy (the DEFAULT tier).

    Small BASE budget that never dead-ends the task: reaching a limit grants
    the bounded course correction of ``AGILE_CONTINUATIONS`` with a smaller
    renewed budget. The clock is advisory, so a long increment is marked and
    stressed, never stopped.

    Args:
        settings: Optional overrides (``settings`` priority > env > constants).

    Returns:
        The agile :class:`EffortPolicy`.
    """
    deadline = _env_float(ENV_AGILE_WALL_CLOCK_SECONDS, AGILE_WALL_CLOCK_SECONDS)
    target = _env_float(ENV_AGILE_TARGET_SECONDS, AGILE_TARGET_SECONDS)
    policy = EffortPolicy(
        name=AGILE_NAME,
        max_iterations=AGILE_MAX_ITERATIONS,
        max_tool_calls=AGILE_MAX_TOOL_CALLS,
        max_tool_rounds=AGILE_MAX_TOOL_ROUNDS,
        # A tool limit is a PACING signal, not the end of the task: the model
        # receives the facts it already collected plus a smaller renewed budget.
        continue_on_tool_limit=True,
        continuations=AGILE_CONTINUATIONS,
        continuation_max_iterations=TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS,
        continuation_max_calls=TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS,
        wall_clock_seconds=deadline,
        validation=AGILE_VALIDATION,
        tool_allowlist=None,
        # ADVISORY clock: the agile target is a promise, not a deadline. The
        # turn is stressed (notices + metadata) and never stopped.
        target_seconds=target,
        stop_on_wall_clock=False,
    )
    return _apply_overrides(policy, settings)


def max_policy(settings: Optional[Mapping[str, Any]] = None) -> EffortPolicy:
    """
    Build the MAX policy (``codx-junior-max``, big/complex work).

    Larger budget, the same bounded tool-limit continuation and a long ADVISORY
    target: the turn is asked to wrap up and marked, it is never cancelled by
    the clock.

    Args:
        settings: Optional overrides.

    Returns:
        The max :class:`EffortPolicy`.
    """
    policy = EffortPolicy(
        name=MAX_NAME,
        max_iterations=MAX_MAX_ITERATIONS,
        max_tool_calls=MAX_MAX_TOOL_CALLS,
        max_tool_rounds=MAX_MAX_TOOL_ROUNDS,
        continue_on_tool_limit=True,
        continuations=TOOL_LIMIT_CONTINUATIONS,
        continuation_max_iterations=TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS,
        continuation_max_calls=TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS,
        wall_clock_seconds=MAX_WALL_CLOCK_SECONDS,
        validation=MAX_VALIDATION,
        tool_allowlist=None,
        # A long ADVISORY target, exactly like agile but 3x longer: reaching it
        # stresses the model and marks the response, it never stops the task.
        target_seconds=MAX_WALL_CLOCK_SECONDS,
        stop_on_wall_clock=False,
    )
    return _apply_overrides(policy, settings)


def default_policy(settings: Optional[Mapping[str, Any]] = None) -> EffortPolicy:
    """
    Build the policy of a plain chat (no effort profile, no orchestration).

    Keeps the previous behaviour: moderate rounds, no continuation, no deadline.

    Args:
        settings: Optional overrides.

    Returns:
        The default :class:`EffortPolicy`.
    """
    policy = EffortPolicy(
        name=DEFAULT_NAME,
        max_iterations=DEFAULT_MAX_ITERATIONS,
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        max_tool_rounds=MAX_TOOL_ROUNDS,
        continue_on_tool_limit=False,
        continuations=0,
        continuation_max_iterations=TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS,
        continuation_max_calls=TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS,
        wall_clock_seconds=0.0,
        validation=AGILE_VALIDATION,
        tool_allowlist=None,
    )
    return _apply_overrides(policy, settings)


def _resolve_base_policy(
    profile_names: Union[None, str, Sequence[str]],
    orchestration_info: Optional[Mapping[str, Any]],
    settings: Optional[Mapping[str, Any]],
) -> EffortPolicy:
    """
    Resolve the tier from the profile names, without applying ``explicit``.

    Orchestration (worker/supervisor) keeps its previous behaviour and maps to
    the MAX policy; otherwise MAX wins over AGILE and a plain chat gets the
    default policy.
    """
    names = _normalize_names(profile_names)
    if orchestration_info:
        return max_policy(settings)
    if MAX_PROFILE in names:
        return max_policy(settings)
    if AGILE_PROFILE in names:
        return agile_policy(settings)
    return default_policy(settings)


def effort_for(
    profile_names: Union[None, str, Sequence[str]] = None,
    orchestration_info: Optional[Mapping[str, Any]] = None,
    explicit: Optional[bool] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> EffortPolicy:
    """
    Resolve the effort policy of a turn.

    Args:
        profile_names:      Profiles taking part in the turn.
        orchestration_info: Parsed ``orchestration`` header (or None). A truthy
                            value means a worker/supervisor turn and resolves to
                            the MAX policy, as before.
        explicit:           Optional caller override (config
                            ``continue_on_tool_limit``). When not ``None`` it
                            forces ``continue_on_tool_limit``; forcing it on
                            also grants the continuation budget of the MAX tier
                            when the base tier had none.
        settings:           Optional overrides passed to the tier factories.

    Returns:
        The :class:`EffortPolicy` to apply to the turn.
    """
    policy = _resolve_base_policy(profile_names, orchestration_info, settings)
    if explicit is None:
        return policy
    if not explicit:
        return replace(policy, continue_on_tool_limit=False)
    if policy.continuations > 0:
        return replace(policy, continue_on_tool_limit=True)
    return replace(
        policy,
        continue_on_tool_limit=True,
        continuations=TOOL_LIMIT_CONTINUATIONS,
        continuation_max_iterations=TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS,
        continuation_max_calls=TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS,
    )


def is_agile(
    profile_names: Union[None, str, Sequence[str]] = None,
    orchestration_info: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    True when the turn runs the AGILE tier (``codx-junior``).

    Convenience helper for callers that need the tier name rather than the
    continuation decision (both tiers continue after a tool limit now).

    Args:
        profile_names:      Profiles taking part in the turn.
        orchestration_info: Parsed ``orchestration`` header (or None).

    Returns:
        True for the AGILE tier, False for MAX and for a plain chat.
    """
    return (
        _resolve_base_policy(profile_names, orchestration_info, None).name
        == AGILE_NAME
    )


__all__ = [
    "AGILE_NAME",
    "AGILE_PROFILE",
    "AGILE_VALIDATION",
    "DEFAULT_NAME",
    "EffortPolicy",
    "MAX_NAME",
    "MAX_PROFILE",
    "MAX_VALIDATION",
    "agile_policy",
    "default_policy",
    "effort_for",
    "is_agile",
    "max_policy",
]

# Made with ❤️ by codx-junior
