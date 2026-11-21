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
        "HIGH effort: the codx-junior harness profile. It runs the model at "
        "reasoning_effort=high, gives it room to finish a normal task in one "
        "turn (several tool rounds and course corrections) and keeps the clock "
        "advisory. A partial increment closed with its own summary is valid."
    ),
    "max": (
        "MAX effort: the codx-junior-max harness profile. It runs the model at "
        "reasoning_effort=max and plans the objective as phases, closing each "
        "one behind its own gate (tests, review, evidence) before the next one "
        "starts. Long-horizon work with more course corrections than high, "
        "full-suite + coverage + security validation and parallel workstreams."
    ),
    "default": (
        "Plain chat behaviour: moderate rounds, one tool batch and no course "
        "correction. Used when no effort is associated with the profiles of the "
        "turn."
    ),
}

#: Revision of the seeded built-ins. It is bumped whenever the values or the
#: description of a built-in effort change: ``ensure_builtins`` then REFRESHES
#: the definition on disk, so an existing installation receives the new method
#: instead of keeping the file created on its first run. A built-in is owned by
#: codx-junior: clone it under another name to keep a customized copy.
BUILTIN_REVISION: int = 4


# ── Base effort types ───────────────────────────────────────────────────────
# Effort is DATA: the numbers of a turn live in the effort definitions stored in
# Global Settings, never in ``ai/smol/constants.py``. These four types are what a
# new installation is seeded with, and they are the vocabulary the team view
# offers when linking a profile to an effort. Everything about them (name,
# description, values) can be edited, cloned or extended by the user.
#
# The types describe a METHOD, not a size:
#
# * ``agile``           — incrementally add value with quick user revisions.
# * ``waterfall``       — big thinking first, then deliver; notify the user only
#                         when all tests pass.
# * ``obsessed``        — the task must be perfect: think, rethink, experiment,
#                         test, try again.
# * ``fire-department`` — we have a bug: fix it now and push to production.
# * ``default``         — plain chat, no effort associated.
EFFORT_TYPES: tuple = (
    "agile",
    "waterfall",
    "obsessed",
    "fire-department",
    "default",
)

#: Effort used when a turn resolves to no associated effort.
FALLBACK_EFFORT: str = "default"

#: Human description of every base type: the tagline is what the team view shows
#: next to the effort name, ``method`` is what the advanced editor explains.
EFFORT_TYPE_DOCS: List[Dict[str, str]] = [
    {
        "name": "agile",
        "tagline": "Incrementally add value with quick user revisions",
        "method": (
            "Work in the smallest slice that is worth showing, then hand control "
            "back for a revision. The clock is a promise, not a deadline: a "
            "partial increment closed with its own summary is a valid outcome."
        ),
    },
    {
        "name": "waterfall",
        "tagline": "Big thinking, then deliver; notify when all tests pass",
        "method": (
            "Plan the whole objective as phases before touching code, close each "
            "phase behind its own gate (tests, review, evidence) and deliver once "
            "at the end. The user is notified only when the full validation is "
            "green; independent phases may run in parallel."
        ),
    },
    {
        "name": "obsessed",
        "tagline": "Perfect or nothing: think, rethink, experiment, test, try",
        "method": (
            "Treat correctness and quality as the goal, not the budget: re-read "
            "the change, question the first design, run throw-away experiments "
            "and repeat the reasoning until no known defect is left. Expensive by "
            "design and meant for work that must not come back."
        ),
    },
    {
        "name": "fire-department",
        "tagline": "We have a bug: fix it now and push to production",
        "method": (
            "Minimal diagnosis, smallest possible fix, targeted verification and "
            "straight to production. No refactors, no opportunistic cleanup, no "
            "planning phase: the only acceptable output is the incident closed, "
            "fast, without breaking anything else."
        ),
    },
    {
        "name": "default",
        "tagline": "Plain chat, no effort attached",
        "method": (
            "Moderate rounds, one tool batch and no course correction. Used when "
            "no effort is associated with the profiles of the turn."
        ),
    },
]

#: Values seeded for every base type. They are the single source of truth for a
#: new installation: no runtime constant is read, none is required.
#: ``None``/missing keys mean "inherit the engine default".
BASE_EFFORT_VALUES: Dict[str, Dict[str, Any]] = {
    "agile": {
        "strategy": "agile",
        "reasoning_effort": "high",
        "max_iterations": 30,
        "max_tool_calls": 8,
        "max_tool_rounds": 60,
        "continue_on_tool_limit": True,
        "continuations": 3,
        "continuation_max_iterations": 8,
        "continuation_max_calls": 4,
        "wall_clock_seconds": 300.0,
        "target_seconds": 1200.0,
        "stop_on_wall_clock": False,
        "validation": "targeted+qa_review_changes",
        "planning_depth": "light",
        "review_cycles": 1,
        "experiments_enabled": False,
        "user_checkpoints": "each-increment",
        "notify_on_tests_pass": False,
        "deliver_once": False,
        "parallel_workstreams": 1,
        "push_to_production": False,
    },
    "waterfall": {
        "strategy": "waterfall",
        "reasoning_effort": "max",
        "max_iterations": 80,
        "max_tool_calls": 12,
        "max_tool_rounds": 160,
        "continue_on_tool_limit": True,
        "continuations": 6,
        "continuation_max_iterations": 20,
        "continuation_max_calls": 8,
        "wall_clock_seconds": 14400.0,
        "target_seconds": 7200.0,
        "stop_on_wall_clock": False,
        "validation": "full+coverage+security",
        "planning_depth": "phases",
        "review_cycles": 1,
        "experiments_enabled": False,
        "user_checkpoints": "end",
        "notify_on_tests_pass": True,
        "deliver_once": True,
        "parallel_workstreams": 2,
        "push_to_production": False,
    },
    "obsessed": {
        "strategy": "obsessed",
        "reasoning_effort": "max",
        "max_iterations": 100,
        "max_tool_calls": 16,
        "max_tool_rounds": 200,
        "continue_on_tool_limit": True,
        "continuations": 8,
        "continuation_max_iterations": 24,
        "continuation_max_calls": 10,
        "wall_clock_seconds": 21600.0,
        "target_seconds": 10800.0,
        "stop_on_wall_clock": False,
        "validation": "full+coverage+security+mutation",
        "planning_depth": "exhaustive",
        "review_cycles": 3,
        "experiments_enabled": True,
        "user_checkpoints": "end",
        "notify_on_tests_pass": True,
        "deliver_once": True,
        "parallel_workstreams": 1,
        "push_to_production": False,
    },
    "fire-department": {
        "strategy": "fire-department",
        "reasoning_effort": "high",
        "max_iterations": 12,
        "max_tool_calls": 6,
        "max_tool_rounds": 10,
        "continue_on_tool_limit": True,
        "continuations": 1,
        "continuation_max_iterations": 4,
        "continuation_max_calls": 2,
        "wall_clock_seconds": 900.0,
        "target_seconds": 300.0,
        "stop_on_wall_clock": False,
        "validation": "targeted+smoke",
        "planning_depth": "none",
        "review_cycles": 0,
        "experiments_enabled": False,
        "user_checkpoints": "end",
        "notify_on_tests_pass": True,
        "deliver_once": True,
        "parallel_workstreams": 1,
        "push_to_production": True,
    },
    "default": {
        "strategy": "default",
        "max_iterations": 10,
        "max_tool_calls": 5,
        "max_tool_rounds": 20,
        "continue_on_tool_limit": False,
        "continuations": 0,
        "continuation_max_iterations": 0,
        "continuation_max_calls": 0,
        "wall_clock_seconds": 0.0,
        "target_seconds": 0.0,
        "stop_on_wall_clock": False,
        "validation": "none",
        "planning_depth": "light",
        "review_cycles": 0,
        "experiments_enabled": False,
        "user_checkpoints": "none",
        "notify_on_tests_pass": False,
        "deliver_once": False,
        "parallel_workstreams": 1,
        "push_to_production": False,
    },
}


def base_effort_values(name: str) -> Dict[str, Any]:
    """
    Return the seeded values of a base effort type.

    Args:
        name: One of :data:`EFFORT_TYPES`.

    Returns:
        A copy of the values mapping (safe to mutate).
    """
    return dict(BASE_EFFORT_VALUES.get(name, BASE_EFFORT_VALUES[FALLBACK_EFFORT]))


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
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Provider reasoning effort: off | low | high | max. None inherits "
            "the base tier (high for agile, max for max)."
        ),
    )
    tool_allowlist: Optional[List[str]] = Field(
        default=None,
        description="Granted tools; None means every enabled tool.",
    )

    # ── Method: what the profile DOES, not how much ─────────────────────────
    # These values describe the way the work is done. They are stored with the
    # effort definition (and explained by the advanced editor) so a user can
    # clone ``agile`` into a softer agile or turn ``obsessed`` into an extra
    # careful review profile without touching code.
    strategy: Optional[str] = Field(
        default=None,
        description=(
            "Method followed: agile | waterfall | obsessed | fire-department "
            "(any name is allowed for a custom effort)."
        ),
    )
    planning_depth: Optional[str] = Field(
        default=None,
        description=(
            "none | light | phases | exhaustive - how much thinking happens "
            "before the first change."
        ),
    )
    review_cycles: Optional[int] = Field(
        default=None,
        description=(
            "How many times the profile re-reads, re-thinks and re-tests its "
            "own work before delivering (0 = deliver on first success)."
        ),
    )
    experiments_enabled: Optional[bool] = Field(
        default=None,
        description=(
            "Allow throw-away experiments (spikes, probes, trial runs) before "
            "the real change."
        ),
    )
    user_checkpoints: Optional[str] = Field(
        default=None,
        description=(
            "none | each-increment | end - when the profile hands control back "
            "to the user for a revision."
        ),
    )
    notify_on_tests_pass: Optional[bool] = Field(
        default=None,
        description=(
            "Notify the user only once the whole validation is green, instead "
            "of reporting progress."
        ),
    )
    deliver_once: Optional[bool] = Field(
        default=None,
        description="One single delivery at the end instead of incremental ones.",
    )
    parallel_workstreams: Optional[int] = Field(
        default=None,
        description=(
            "Independent workstreams the profile may run in parallel (complex "
            "systems); 1 = strictly sequential."
        ),
    )
    push_to_production: Optional[bool] = Field(
        default=None,
        description=(
            "After the fix is verified, publish/deploy without a second "
            "confirmation (fire-department)."
        ),
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
            "(waterfall). It does not run anything by itself, it states the "
            "evidence the close must contain."
        ),
    },
    {
        "key": "reasoning_effort",
        "label": "Reasoning effort",
        "group": "Model thinking",
        "control": "text",
        "unit": "off | low | high | max",
        "summary": (
            "How hard the model thinks before answering: the DSH-style effort "
            "forwarded as reasoning_effort."
        ),
        "effect": (
            "high is the codx-junior default and max is the codx-junior-max "
            "default. An explicit model/provider reasoning_effort still wins; "
            "leave it empty to inherit the tier. off disables DeepSeek thinking."
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
    {
        "key": "strategy",
        "label": "Effort type",
        "group": "Method",
        "control": "select",
        "unit": "",
        "options": [name for name in EFFORT_TYPES],
        "summary": "The method the profile follows, not how big its budget is.",
        "effect": (
            "It is the label the team view shows and the method the prompt "
            "announces: 'agile' (small increments, quick user revisions), "
            "'waterfall' (plan the phases, deliver once when everything is "
            "green), 'obsessed' (perfect or nothing: rethink, experiment, "
            "retest), 'fire-department' (fix the incident now, push). Any other "
            "name is allowed for a custom effort."
        ),
    },
    {
        "key": "planning_depth",
        "label": "Planning depth",
        "group": "Method",
        "control": "select",
        "unit": "",
        "options": ["none", "light", "phases", "exhaustive"],
        "summary": "How much thinking happens before the first change.",
        "effect": (
            "'none' goes straight to the fix (incidents); 'light' is a short "
            "sketch; 'phases' splits the objective into gated phases before "
            "touching code; 'exhaustive' explores the problem and alternative "
            "designs first. More planning costs rounds up front and saves rework "
            "on large tasks."
        ),
    },
    {
        "key": "review_cycles",
        "label": "Review cycles",
        "group": "Method",
        "control": "number",
        "unit": "cycles",
        "summary": "How many times the profile re-reads and re-thinks its own work.",
        "effect": (
            "After a change looks done, each cycle re-reads the diff, questions the "
            "first design and re-runs the checks before delivering. 0 delivers on "
            "the first success (fast, for hotfixes); 3+ is the 'obsessed' method, "
            "where a second and third look are expected to find something."
        ),
    },
    {
        "key": "experiments_enabled",
        "label": "Allow experiments",
        "group": "Method",
        "control": "toggle",
        "unit": "",
        "summary": "Throw-away probes, spikes and trial runs before the real change.",
        "effect": (
            "On, the profile may try something, measure it and discard it instead "
            "of committing to the first idea. It costs turns and produces "
            "evidence the summary can cite; off keeps the profile on the shortest "
            "path to the change."
        ),
    },
    {
        "key": "user_checkpoints",
        "label": "User checkpoints",
        "group": "Method",
        "control": "select",
        "unit": "",
        "options": ["none", "each-increment", "end"],
        "summary": "When the profile hands control back for a revision.",
        "effect": (
            "'each-increment' closes every slice with a demo and waits for the "
            "user (agile); 'end' delivers once, after the whole objective "
            "(waterfall); 'none' is for unattended work. This is what makes a "
            "profile collaborative or autonomous."
        ),
    },
    {
        "key": "notify_on_tests_pass",
        "label": "Notify when tests pass",
        "group": "Method",
        "control": "toggle",
        "unit": "",
        "summary": "Report only when the validation is green, not while working.",
        "effect": (
            "On, intermediate progress stays silent and the user is notified when "
            "the full suite (and the coverage/security gates this effort asks for) "
            "passes. Off, the profile reports each increment as it finishes."
        ),
    },
    {
        "key": "deliver_once",
        "label": "Single delivery",
        "group": "Method",
        "control": "toggle",
        "unit": "",
        "summary": "One delivery at the end instead of incremental ones.",
        "effect": (
            "On, partial work is never presented as a result: the profile keeps "
            "going until the objective is complete (waterfall, obsessed). Off, "
            "every finished slice is a valid delivery."
        ),
    },
    {
        "key": "parallel_workstreams",
        "label": "Parallel workstreams",
        "group": "Method",
        "control": "number",
        "unit": "streams",
        "summary": "Independent workstreams the profile may run at the same time.",
        "effect": (
            "Complex systems split into independent fronts (API, client, infra) "
            "that can advance in parallel. 1 keeps the profile strictly "
            "sequential; raising it shortens long builds but multiplies "
            "concurrent edits, so use it only when the fronts really are "
            "independent."
        ),
    },
    {
        "key": "push_to_production",
        "label": "Push to production",
        "group": "Method",
        "control": "toggle",
        "unit": "",
        "summary": "Deploy after the fix is verified, without a second confirmation.",
        "effect": (
            "On (fire-department), the profile closes the incident by shipping it: "
            "fix, targeted verification, deploy. Off, the delivery stops at a "
            "reviewable change. Never turn this on for work that needs a review."
        ),
    },
]

#: Groups in the order the editor shows them.
EFFORT_FIELD_GROUPS: List[str] = [
    "Method",
    "Model thinking",
    "Turn budget",
    "Course corrections",
    "Clock",
    "Validation",
]


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
    revision: int = Field(
        default=0,
        description=(
            "Revision of the built-in seed that produced this definition "
            "(0 = written by hand or customized, never refreshed)."
        ),
    )

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
    "BASE_EFFORT_VALUES",
    "BUILTIN_DESCRIPTIONS",
    "BUILTIN_EFFORTS",
    "EFFORT_FIELD_DOCS",
    "EFFORT_FIELD_GROUPS",
    "EFFORT_TIERS",
    "EFFORT_TYPES",
    "EFFORT_TYPE_DOCS",
    "FALLBACK_EFFORT",
    "EffortDefinition",
    "EffortValues",
    "base_effort_values",
]

# Made with ❤️ by codx-junior
