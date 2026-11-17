"""
Effort definitions: named effort budgets that profiles are linked to.

An **effort** is a named bundle of agent policy values: how many model rounds a
turn may use, how many tool calls per round, how many course corrections are
granted when a limit is hit, how long the turn may run and how much validation
is expected before delivery.

The two sides of the feature are deliberately separated:

* The **team view** only *links* a profile to an effort (``Profile.effort_name``).
  It shows the effort name and description and nothing else.
* **Global Settings** owns the *definition*: the values, with an explanation of
  what each one changes in the profile's work (:data:`EFFORT_FIELD_DOCS`).

The values end up as the runtime overrides consumed by
:func:`codx.junior.ai.smol.effort.effort_for`; storage and resolution live in
:mod:`codx.junior.efforts.effort_manager`.

Made with ❤️ by codx-junior
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: Base tiers an effort can start from. They map to the runtime policies in
#: :mod:`codx.junior.ai.smol.effort` (agile, max and the plain-chat default).
EFFORT_TIERS: tuple = ("agile", "max", "default")

#: Names seeded on first use. They mirror the hardcoded tiers, so a fresh project
#: behaves exactly as before this feature existed.
BUILTIN_EFFORTS: tuple = ("agile", "max", "default")

#: One-line description of each seeded effort.
BUILTIN_DESCRIPTIONS: Dict[str, str] = {
    "agile": (
        "Small, bounded increments: a short budget with course corrections and "
        "an advisory clock. A partial increment closed with its own summary is a "
        "valid outcome. This is the default for the codx-junior harness."
    ),
    "max": (
        "Heavy tier for multi-file refactors, migrations and full-suite "
        "validation: a much larger budget, the same course corrections and a "
        "long advisory target. Used by the codx-junior-max harness."
    ),
    "default": (
        "Plain chat behaviour: moderate rounds, one tool batch and no course "
        "correction. Used when no effort is associated with the profiles of the "
        "turn."
    ),
}


def _now() -> str:
    """Return the current timestamp in ISO format (UTC naive)."""
    return datetime.utcnow().replace(microsecond=0).isoformat()


class EffortValues(BaseModel):
    """
    The policy values of one effort.

    Every field is optional: ``None`` means "inherit the base tier", so a
    definition only declares what it really changes.
    """

    max_iterations: Optional[int] = Field(
        default=None,
        description="Model rounds (LLM calls) allowed in one turn.",
    )
    max_tool_calls: Optional[int] = Field(
        default=None,
        description="Tool calls allowed per model round.",
    )
    max_tool_rounds: Optional[int] = Field(
        default=None,
        description="Sequential tool rounds allowed before LoopGuard stops the loop.",
    )
    continue_on_tool_limit: Optional[bool] = Field(
        default=None,
        description="Grant a course correction when the tool budget is reached.",
    )
    continuations: Optional[int] = Field(
        default=None,
        description="Course corrections granted after a tool-limit stop.",
    )
    continuation_max_iterations: Optional[int] = Field(
        default=None,
        description="Model rounds granted by each course correction.",
    )
    continuation_max_calls: Optional[int] = Field(
        default=None,
        description="Tool calls per round granted by each course correction.",
    )
    wall_clock_seconds: Optional[float] = Field(
        default=None,
        description="Wall-clock budget (a deadline only when stop_on_wall_clock).",
    )
    target_seconds: Optional[float] = Field(
        default=None,
        description="Advisory target; the turn is never stopped by it.",
    )
    stop_on_wall_clock: Optional[bool] = Field(
        default=None,
        description="When true, wall_clock_seconds is ENFORCED.",
    )
    validation: Optional[str] = Field(
        default=None,
        description="Validation breadth expected before delivery.",
    )
    tool_allowlist: Optional[List[str]] = Field(
        default=None,
        description="Granted tools; None means every enabled tool.",
    )

    def to_overrides(self) -> Dict[str, Any]:
        """
        Return the values that are actually set.

        Returns:
            A mapping with the non-``None`` fields, ready to be used as
            ``settings`` by :func:`codx.junior.ai.smol.effort.effort_for`.
        """
        return {
            key: value
            for key, value in self.model_dump().items()
            if value is not None
        }


#: Documentation of every value, grouped for the advanced editor. The UI renders
#: this verbatim: it is the single place where the effect of each value is
#: explained, so backend and frontend never drift apart.
EFFORT_FIELD_DOCS: List[Dict[str, Any]] = [
    {
        "key": "max_iterations",
        "label": "Model rounds",
        "group": "Turn budget",
        "control": "number",
        "unit": "rounds",
        "summary": "How many times the model may think and ask for tools in one turn.",
        "effect": (
            "Each round is one LLM call: the model reads the result of the "
            "previous tools and decides what to do next. Too low and the profile "
            "stops mid-work with an unfinished increment; too high and a confused "
            "profile keeps going instead of handing back control."
        ),
    },
    {
        "key": "max_tool_calls",
        "label": "Tool calls per round",
        "group": "Turn budget",
        "control": "number",
        "unit": "calls",
        "summary": "How many tools the model may call inside a single round.",
        "effect": (
            "It bounds the breadth of one batch: reading many files at once, "
            "running a suite and grepping in the same round. A high value makes "
            "each round heavier (bigger prompts, more noise); a low value forces "
            "the profile to ask again for what it needs."
        ),
    },
    {
        "key": "max_tool_rounds",
        "label": "Sequential tool rounds",
        "group": "Turn budget",
        "control": "number",
        "unit": "rounds",
        "summary": "Hard stop of consecutive tool rounds before the loop is considered stuck.",
        "effect": (
            "LoopGuard stops a profile that keeps calling tools without "
            "progress. Raising it allows longer mechanical work; lowering it "
            "ends exploratory loops earlier."
        ),
    },
    {
        "key": "continue_on_tool_limit",
        "label": "Course correction on limit",
        "group": "Course corrections",
        "control": "toggle",
        "unit": "",
        "summary": "When a limit is reached, grant a smaller renewed budget instead of ending the turn.",
        "effect": (
            "On, the profile is told it ran out of budget, keeps its findings and "
            "continues with a reduced budget: the task is never dead-ended by a "
            "number. Off, the turn stops at the limit and the partial result is "
            "delivered."
        ),
    },
    {
        "key": "continuations",
        "label": "Corrections granted",
        "group": "Course corrections",
        "control": "number",
        "unit": "corrections",
        "summary": "How many course corrections a turn may consume.",
        "effect": (
            "Each correction is a second (third, ...) wind of a smaller budget. "
            "More corrections finish long tasks but can also extend a turn that "
            "should have been closed and handed back."
        ),
    },
    {
        "key": "continuation_max_iterations",
        "label": "Rounds per correction",
        "group": "Course corrections",
        "control": "number",
        "unit": "rounds",
        "summary": "The updated budget of each course correction.",
        "effect": (
            "Keeps a correction small on purpose: enough to close the increment "
            "in progress, not enough to restart the whole task."
        ),
    },
    {
        "key": "continuation_max_calls",
        "label": "Tool calls per correction",
        "group": "Course corrections",
        "control": "number",
        "unit": "calls",
        "summary": "Tool calls available per round inside a course correction.",
        "effect": (
            "Controls how much verification fits in a correction: a higher value "
            "allows running the tests one more time before closing."
        ),
    },
    {
        "key": "wall_clock_seconds",
        "label": "Wall clock",
        "group": "Clock",
        "control": "number",
        "unit": "seconds",
        "summary": "Time budget of the turn: advisory target or enforced deadline.",
        "effect": (
            "With 'Enforce wall clock' off it is only a target the profile sees "
            "and uses to pace itself. On, the turn is stopped when it is reached "
            "and the partial increment is delivered with a notice."
        ),
    },
    {
        "key": "target_seconds",
        "label": "Advisory target",
        "group": "Clock",
        "control": "number",
        "unit": "seconds",
        "summary": "A soft goal the profile can see, without ever stopping the turn.",
        "effect": (
            "Useful to stress long-running work in the max tier: the profile is "
            "encouraged to keep going towards the target but nothing is cut when "
            "it passes it."
        ),
    },
    {
        "key": "stop_on_wall_clock",
        "label": "Enforce wall clock",
        "group": "Clock",
        "control": "toggle",
        "unit": "",
        "summary": "Turn the wall clock into a real deadline.",
        "effect": (
            "On, the clock above becomes a hard stop. Off (recommended), only the "
            "budgets bound the turn: the clock is advisory, as in the agile tier."
        ),
    },
    {
        "key": "validation",
        "label": "Validation breadth",
        "group": "Validation",
        "control": "text",
        "unit": "",
        "summary": "What the profile must verify before reporting the work as done.",
        "effect": (
            "A short contract written in the prompt: e.g. "
            "'targeted+qa_review_changes' (agile) or 'full+coverage+security' "
            "(max). It does not run anything by itself, it states the evidence the "
            "close must contain."
        ),
    },
    {
        "key": "tool_allowlist",
        "label": "Tool allowlist",
        "group": "Validation",
        "control": "list",
        "unit": "",
        "summary": "Restrict the granted tools; empty means every enabled tool.",
        "effect": (
            "Only these tools are offered to the model, which shrinks the prompt "
            "and makes the profile focus. An allowlist that lacks a tool the task "
            "needs blocks the work, so leave it empty unless you mean it."
        ),
    },
]

#: Groups in the order the editor shows them.
EFFORT_FIELD_GROUPS: List[str] = ["Turn budget", "Course corrections", "Clock", "Validation"]


class EffortDefinition(BaseModel):
    """
    A named effort that profiles can be linked to.

    Attributes:
        name:        Unique name (also the folder name and the ``@``-free key
                     referenced by ``Profile.effort_name``).
        description: What the effort is for, shown in the team view picker.
        tier:        Base tier the values start from (``agile``, ``max`` or
                     ``default``).
        values:      The overrides applied on top of the base tier.
        builtin:     True for the seeded efforts, which cannot be deleted.
        profiles:    Profiles currently linked to it (computed by the manager).
        created_at:  Creation timestamp (ISO).
        updated_at:  Last update timestamp (ISO).
    """

    name: str = Field(default="", description="Unique effort name.")
    description: str = Field(default="", description="What the effort is for.")
    tier: str = Field(default="agile", description="Base tier: agile, max or default.")
    values: EffortValues = Field(
        default_factory=EffortValues,
        description="Overrides applied on top of the base tier.",
    )
    builtin: bool = Field(default=False, description="Seeded effort: it cannot be deleted.")
    profiles: List[str] = Field(
        default_factory=list,
        description="Profiles linked to this effort (read-only, computed).",
    )
    created_at: str = Field(default="", description="Creation timestamp (ISO).")
    updated_at: str = Field(default="", description="Last update timestamp (ISO).")

    def stamp_created(self) -> "EffortDefinition":
        """Fill the timestamps of a new definition."""
        now = _now()
        self.created_at = self.created_at or now
        self.updated_at = now
        return self

    def stamp_updated(self) -> "EffortDefinition":
        """Refresh the update timestamp."""
        self.updated_at = _now()
        return self

    def to_overrides(self) -> Dict[str, Any]:
        """Return the values of this effort as runtime overrides."""
        return self.values.to_overrides()


__all__ = [
    "BUILTIN_DESCRIPTIONS",
    "BUILTIN_EFFORTS",
    "EFFORT_FIELD_DOCS",
    "EFFORT_FIELD_GROUPS",
    "EFFORT_TIERS",
    "EffortDefinition",
    "EffortValues",
]

# Made with ❤️ by codx-junior
