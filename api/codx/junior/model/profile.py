from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from codx.junior.media.model import VoiceProfile
from codx.junior.model.user import CodxUser


class ProfileGuardrailSettings(BaseModel):
    """Security policy applied to a profile.

    Every profile carries a guardrail policy. When multiple profiles work
    together in a chat, their policies are aggregated before a request is
    processed.
    """

    enabled: bool = Field(
        description="Enable guardrails for this profile",
        default=False,
    )
    scope: Optional[str] = Field(
        description="Natural-language scope description used for relevance checks",
        default=None,
    )
    scope_keywords: List[str] = Field(
        description="Keywords that indicate a request is on-scope",
        default_factory=list,
    )
    scope_similarity_threshold: float = Field(
        description="Minimum cosine similarity for the embedding scope check",
        default=0.35,
    )
    allowed_tools: List[str] = Field(
        description="Tool allowlist for this profile (empty = no tools allowed)",
        default_factory=list,
    )
    refusal_message: str = Field(
        default="I can only answer questions within my configured scope."
    )
    risk_threshold: float = Field(
        description="Cumulative risk score that forces a block",
        default=10.0,
    )
    use_llm_classifier: bool = Field(
        description="Placeholder for a future local decision model",
        default=False,
    )


class ProfileApiSettings(BaseModel):
    active: bool = Field(description="Model is visible through API", default=False)
    model_name: Optional[str] = Field(description="Model's name", default=None)
    description: Optional[str] = Field(description="Model's description", default=None)


class ProfileEffortSettings(BaseModel):
    """Agent effort budget carried by a profile.

    The effort tier is selected by the active profile names (``codx-junior`` →
    agile, ``codx-junior-max`` → max) and these fields override the tier
    defaults. They are AGENT policy (model rounds, tool breadth, continuations,
    wall clock, validation), not model capabilities: that is why they live on
    the profile and not on ``AIProvider``/``AIModel``.

    Every field is optional; an unset field keeps the value of the resolved
    tier (which falls back to the constants in
    ``codx.junior.ai.smol.constants``).
    """

    max_iterations: Optional[int] = Field(
        default=None,
        description="Model rounds allowed in the turn (fallback: tier default).",
    )
    max_tool_calls: Optional[int] = Field(
        default=None,
        description="Tool calls allowed per model round (fallback: tier default).",
    )
    max_tool_rounds: Optional[int] = Field(
        default=None,
        description="Reserved for LoopGuard sequential rounds.",
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
        description="Rounds granted by each course correction.",
    )
    continuation_max_calls: Optional[int] = Field(
        default=None,
        description="Tool calls per round granted by each course correction.",
    )
    wall_clock_seconds: Optional[float] = Field(
        default=None,
        description="Wall-clock target/deadline (see stop_on_wall_clock).",
    )
    target_seconds: Optional[float] = Field(
        default=None,
        description="Advisory target; the turn is never stopped by it.",
    )
    stop_on_wall_clock: Optional[bool] = Field(
        default=None,
        description="When true, wall_clock_seconds is ENFORCED (opt-in).",
    )
    validation: Optional[str] = Field(
        default=None,
        description="Validation breadth expected before delivery.",
    )
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Provider reasoning effort: off | low | high | max. Inherited from "
            "the effort definition (high/max) when unset."
        ),
    )
    tool_allowlist: Optional[List[str]] = Field(
        default=None,
        description="Granted tools; None means every enabled tool.",
    )


class Profile(BaseModel):
    name: str = Field(default="")
    url: str = Field(default="")
    avatar: str = Field(default="")
    description: str = Field(default="")
    category: str = Field(default="", description="Profile category: global, file, coding, ...")
    file_match: str = Field(default="",
        description="Optional regex to apply profiles based on file absolute path.")
    content: Optional[str] = Field(default=None)
    parsed_content: Optional[str] = Field(default=None)
    path: str = Field(default="")
    profiles: Optional[List[str]] = Field(default=[], description="Linked profiles to include with this profile")
    team: Optional[List[str]] = Field(
        default=[],
        description=(
            "Profile names the LLM can DELEGATE work to. They are NOT active in "
            "the chat: they are listed as a name/description table in the system "
            "prompt, after the profile composition, and the agent hands them a "
            "self-contained task with the delegation tools instead of asking the "
            "user to re-run the task."
        )
    )
    llm_model: Optional[str] = Field(default='')
    use_knowledge: Optional[bool] = Field(default=True)
    user: Optional[CodxUser] = Field(default=CodxUser())
    tools: Optional[List[str]] = Field(default=[])
    tags: Optional[List[str]] = Field(default=[])
    effort_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of the effort this profile is linked to (see `.codx/efforts` "
            "and Global Settings → Efforts"
            ")" 
            "and Global Settings → Efforts). The team view only exposes this "
            "name: the values are materialized into `effort` when the profile "
            "is saved."
        ),
    )
    effort: Optional[ProfileEffortSettings] = Field(
        default=None,
        description=(
            "Agent effort budget for turns where this profile is active. It is "
            "usually materialized from `effort_name` and overrides the tier "
            "defaults from codx.junior.ai.smol.effort (max_iterations, "
            "max_tool_calls, continuations, wall clock...)."
        ),
    )
    api_settings: Optional[ProfileApiSettings] = Field(description="Indicates if the profile is accessible through the LLM API", default=ProfileApiSettings())
    guardrails: ProfileGuardrailSettings = Field(
        description="Guardrail policy applied when this profile is active",
        default=ProfileGuardrailSettings(),
    )
    chat_mode: Optional[str] = Field(description="Affects on how conversation works. Like writing a document or chat messages", default=None)
    actor: Optional[Dict[str, Any]] = Field(
        description=(
            "Actor declaration of the profile: when ``actor.enabled`` is true the "
            "profile works on its own (shifts on its boards, weekly schedule, "
            "budgets) without a human in the loop. Settings live in "
            "codx.junior.autonomy.models.ActorConfig (role, policy, session, "
            "boards, interval_minutes, schedule, mode, budgets, instructions...). "
            "Kept as a plain mapping so this core model never depends on the "
            "actor package. The legacy ``autonomy`` key is read as a fallback "
            "and migrated on save."
        ),
        default=None,
    )
    autonomy: Optional[Dict[str, Any]] = Field(
        description=(
            "Legacy actor declaration (superseded by ``actor``): when "
            "``autonomy.enabled`` is true the profile becomes an autonomous "
            "agent that takes shifts on its own board. Still read, migrated to "
            "``actor`` on save, never written by new code."
        ),
        default=None,
    )
    voice: Optional[VoiceProfile] = Field(
        description=(
            "Voice used when this profile speaks in the Project's Channel. "
            "``enabled=False`` means the profile is silent (no fallback). "
            "Omitted from the serialized profile when unset, so existing "
            ".codx/profiles files keep their current bytes."
        ),
        default=None,
        exclude_if=lambda voice: voice is None,
    )
    project_id: Optional[str] = Field(description="Profile's project", default=None)
    chat_id: Optional[str] = Field(default="", description="Unique identifier for chat")