"""
Data model of the board manager.

A board manager is now only a **declaration**: the board says who reviews it and
how often (``board.manager``). The execution lives in
:mod:`codx.junior.autonomy.service` — the same runtime as every other actor.

The JSON **action contract** that used to live here is gone. The manager used to
answer ``{"summary": "...", "actions": [...]}`` and a second pass validated and
applied those actions; today the manager is an actor with the ``manager`` role
and uses the ordinary task tools, so every change it makes is recorded on the
board exactly like a human change (same audit trail, same permissions).

:meth:`BoardManagerConfig.to_actor_config` is the bridge between the two worlds:
it turns the board-level declaration into the
:class:`~codx.junior.autonomy.models.ActorConfig` the runtime understands, which
is what keeps existing boards working after the merge.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from . import constants as C

logger = logging.getLogger(__name__)


def utcnow_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _clip(value: Any, max_chars: int) -> str:
    """Convert *value* to a stripped string clipped to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _as_str_list(value: Any, max_items: int = 50, max_chars: int = 200) -> List[str]:
    """Normalize a string/iterable into a de-duplicated list of short strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    result: List[str] = []
    try:
        items = list(value)
    except TypeError:
        items = [value]
    for item in items:
        text = _clip(item, max_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Clamp *value* into ``[minimum, maximum]`` with a safe fallback."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _as_float(value: Any, default: float) -> float:
    """Convert *value* to a non-negative float with a safe fallback."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, number)


# ---------------------------------------------------------------------------
# Configuration (stored on the board document)
# ---------------------------------------------------------------------------


class BoardManagerConfig(BaseModel):
    """
    Per-board automation policy.

    Attributes:
        enabled: When False the worker never reviews the board.
        profile: Profile name that acts as the board manager. Empty falls back
            to the board assistant (``board.assignee``) and then to
            :data:`codx.junior.board_manager.constants.DEFAULT_PROFILE`.
        mode: ``apply`` (the actor may change the board) or ``suggest`` (dry
            run: it only writes a report). Mapped onto the actor modes.
        interval_minutes: Minimum time between two automatic reviews.
        max_actions: How many changes one review should make. It used to bound
            the action list; it is now part of the mandate, because the manager
            acts through the tools.
        include_done: Whether closed tasks are also sent as context (they are
            never modified).
        instructions: Extra, board-specific policy written by the user.
        max_tasks: How many tasks the prompt includes.
        announce: Whether the review also leaves a short comment on the board.
        budget_cxjcoins: Hard cap for one review (0 = no shift cap).
        daily_budget_cxjcoins: Hard cap for one UTC day across reviews.
    """

    enabled: bool = False
    profile: str = ""
    mode: str = C.MODE_APPLY
    interval_minutes: int = C.DEFAULT_INTERVAL_MINUTES
    max_actions: int = C.DEFAULT_MAX_ACTIONS
    include_done: bool = False
    instructions: str = ""
    max_tasks: int = C.DEFAULT_MAX_TASKS
    announce: bool = True
    budget_cxjcoins: float = C.DEFAULT_BUDGET_CXJCOINS
    daily_budget_cxjcoins: float = C.DEFAULT_DAILY_BUDGET_CXJCOINS

    def normalized(self) -> "BoardManagerConfig":
        """Return a clamped copy (never mutate the input)."""
        data = self.model_dump()
        data["profile"] = _clip(data.get("profile"), 120).lstrip("@")
        mode = str(data.get("mode") or C.MODE_APPLY).strip().lower()
        data["mode"] = mode if mode in C.MODES else C.MODE_APPLY
        data["interval_minutes"] = _as_int(
            data.get("interval_minutes"),
            C.DEFAULT_INTERVAL_MINUTES,
            C.MIN_INTERVAL_MINUTES,
            C.MAX_INTERVAL_MINUTES,
        )
        data["max_actions"] = _as_int(
            data.get("max_actions"),
            C.DEFAULT_MAX_ACTIONS,
            1,
            C.MAX_ACTIONS_LIMIT,
        )
        data["max_tasks"] = _as_int(
            data.get("max_tasks"),
            C.DEFAULT_MAX_TASKS,
            1,
            C.MAX_TASKS_LIMIT,
        )
        data["instructions"] = _clip(data.get("instructions"), 8000)
        data["enabled"] = bool(data.get("enabled"))
        data["include_done"] = bool(data.get("include_done"))
        data["announce"] = bool(data.get("announce", True))
        data["budget_cxjcoins"] = _as_float(
            data.get("budget_cxjcoins"), C.DEFAULT_BUDGET_CXJCOINS
        )
        data["daily_budget_cxjcoins"] = _as_float(
            data.get("daily_budget_cxjcoins"), C.DEFAULT_DAILY_BUDGET_CXJCOINS
        )
        return BoardManagerConfig(**data)

    @classmethod
    def from_board(cls, board: Optional[Dict[str, Any]]) -> "BoardManagerConfig":
        """
        Read the manager configuration of a board document.

        The board assistant (``assignee``) is the fallback profile: a board that
        already declares who answers its chat has a sensible default manager
        without a second selection.
        """
        board = board if isinstance(board, dict) else {}
        raw = board.get(C.BOARD_CONFIG_KEY)
        if not isinstance(raw, dict):
            raw = {}
        # The board's responsible is the natural manager of the board. A human
        # responsible cannot run a background actor, so only a profile is used
        # here; the legacy board assistant (``assignee``) stays the fallback.
        responsible_kind = str(board.get("responsible_kind") or "").strip().lower()
        responsible_name = _clip(board.get("responsible_name"), 120)
        profile = (
            _clip(raw.get("profile"), 120)
            or (
                responsible_name
                if responsible_name and responsible_kind != "user"
                else ""
            )
            or _clip(board.get("assignee"), 120)
        )
        return cls(
            enabled=bool(raw.get("enabled")),
            profile=profile,
            mode=raw.get("mode") or C.MODE_APPLY,
            interval_minutes=raw.get("interval_minutes") or C.DEFAULT_INTERVAL_MINUTES,
            max_actions=raw.get("max_actions") or C.DEFAULT_MAX_ACTIONS,
            include_done=bool(raw.get("include_done")),
            instructions=raw.get("instructions") or "",
            max_tasks=raw.get("max_tasks") or C.DEFAULT_MAX_TASKS,
            announce=bool(raw.get("announce", True)),
            budget_cxjcoins=(
                raw["budget_cxjcoins"]
                if raw.get("budget_cxjcoins") is not None
                else C.DEFAULT_BUDGET_CXJCOINS
            ),
            daily_budget_cxjcoins=(
                raw["daily_budget_cxjcoins"]
                if raw.get("daily_budget_cxjcoins") is not None
                else C.DEFAULT_DAILY_BUDGET_CXJCOINS
            ),
        ).normalized()

    def to_board_value(self, fallback_profile: str = "") -> Dict[str, Any]:
        """Return the JSON value stored on the board document."""
        data = self.normalized().model_dump()
        if not data["profile"]:
            data["profile"] = _clip(fallback_profile, 120)
        return data

    def to_actor_config(self, board: str, flow: str = "agile") -> Any:
        """
        Translate the board declaration into the actor that executes it.

        A board manager is an actor with:

        * ``role="manager"`` — it curates the board, it does not implement;
        * ``policy="board_only"`` — no code, no shell, no browser;
        * ``session="single"`` — one review per run;
        * ``boards=[board]`` — the reviewed board feeds the agenda;
        * its own **desk** board (``Agent: <profile>``) where the review report
          is written, so the reviewed board is never polluted by shift cards.

        Args:
            board: Title of the reviewed board.
            flow: Board flow (``agile`` by default). The flow tells the manager
                which statuses mean active, blocked, done or discarded.

        Returns:
            An :class:`codx.junior.autonomy.models.ActorConfig`.
        """
        from codx.junior.autonomy import constants as A
        from codx.junior.autonomy.models import ActorConfig, default_board_name

        config = self.normalized()
        flow_name = str(flow or "agile").strip().lower()
        flow_rules = {
            "agile": (
                "Use the agile flow: todo → doing → done, onhold for blocked "
                "work and rejected for discarded/duplicated work. Archived "
                "means done work that no longer needs board space."
            ),
            "delivery": (
                "Use the delivery flow: backlog → todo → doing → review → done, "
                "rejected for discarded work. A task is only done after review."
            ),
            "support": (
                "Use the support flow: new → triage → waiting → resolved → "
                "closed. Waiting means blocked on the requester; closed means "
                "resolved and no longer active."
            ),
        }.get(flow_name, "")
        instructions = [
            f"You are the manager of the board **{board}**.",
            "Review the status of its tasks, request the missing information, "
            "add what is missing, reorganise and re-prioritise.",
            flow_rules or f"Follow the board's own flow ({flow_name}).",
            "Every task must end this review with a responsible, a clear next "
            "step, or a terminal status (done/rejected/archived).",
            f"Do not make more than {config.max_actions} changes in one review.",
        ]
        if config.instructions:
            instructions.append(config.instructions)
        return ActorConfig(
            enabled=True,
            role=A.ROLE_MANAGER,
            policy=A.POLICY_BOARD,
            session=A.SESSION_SINGLE,
            board=default_board_name(config.profile) if config.profile else "",
            boards=[board] if board else [],
            interval_minutes=config.interval_minutes,
            mode=A.MODE_ALIASES.get(config.mode, A.MODE_ACTIVE),
            max_tasks=config.max_tasks,
            include_done=config.include_done,
            max_agenda=config.max_actions,
            budget_cxjcoins=config.budget_cxjcoins,
            daily_budget_cxjcoins=config.daily_budget_cxjcoins,
            notify=config.announce,
            instructions="\n\n".join(instructions),
        ).normalized()


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


class ManagerState(BaseModel):
    """Persisted runtime state of one board manager."""

    board: str = Field(default="")
    board_id: str = Field(default="")
    running: bool = Field(default=False)
    wake_requested: bool = Field(default=False)
    last_run_at: str = Field(default="")
    last_finished_at: str = Field(default="")
    last_status: str = Field(default="")
    last_error: str = Field(default="")
    last_summary: str = Field(default="")
    last_report: str = Field(default="")
    last_cost: float = Field(default=0.0)
    total_cost: float = Field(default=0.0)
    day: str = Field(default="", description="UTC day of day_cost (YYYY-MM-DD)")
    day_cost: float = Field(default=0.0)
    runs: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    last_profile: str = Field(default="")
    last_turns: int = Field(default=0)
    #: Kept for the UI: the contract is gone, so it stays empty.
    last_actions: List[Dict[str, Any]] = Field(default_factory=list)

    def mark_run_started(self, board: str, board_id: str, profile: str) -> None:
        """
        Set the in-flight markers before the shift.

        The previous report is kept: the UI shows "running" while the review is
        in flight and the last useful report remains visible until the new one
        replaces it (or an error is recorded).
        """
        self.board = board
        self.board_id = board_id
        self.running = True
        self.wake_requested = False
        self.last_run_at = utcnow_iso()
        self.last_profile = profile

    def mark_finished(
        self,
        *,
        status: str,
        report: str = "",
        summary: str = "",
        error: str = "",
        cost: float = 0.0,
        duration_ms: float = 0.0,
        turns: int = 0,
    ) -> None:
        """Record a finished review (success or failure)."""
        self.running = False
        self.wake_requested = False
        self.last_finished_at = utcnow_iso()
        self.last_status = status
        self.last_report = _clip(report, 20000)
        self.last_summary = _clip(summary, 8000)
        self.last_error = _clip(error, 4000)
        self.last_cost = max(0.0, float(cost or 0.0))
        self.total_cost = float(self.total_cost or 0.0) + self.last_cost
        day = (self.last_run_at or utcnow_iso())[:10]
        if self.day != day:
            self.day = day
            self.day_cost = 0.0
        self.day_cost = float(self.day_cost or 0.0) + self.last_cost
        self.duration_ms = max(0.0, float(duration_ms or 0.0))
        self.runs = int(self.runs or 0) + 1
        self.last_turns = max(0, int(turns or 0))

    def day_spent(self, today: Optional[str] = None) -> float:
        """Return the cost already spent today (UTC)."""
        key = today or utcnow_iso()[:10]
        return float(self.day_cost or 0.0) if self.day == key else 0.0

    def public(self) -> Dict[str, Any]:
        """Return the API/UI shape of the state."""
        return self.model_dump()

# Made with ❤️ by codx-junior
