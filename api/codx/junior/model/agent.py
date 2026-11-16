"""
Typed models describing the outcome and cost of an agent (SmolAgent) run.

These replace the previous practice of smuggling run information (cost,
accumulated cost, usage) through untyped dicts such as
``AIMessage.additional_kwargs``. They are the single source of truth shared by:

  * SmolAgent          (produces the result)
  * AI / a_chat_run    (returns the result to callers)
  * ChatEngine         (maps the result onto the response Message)
  * ChatEventBridge /  (stream the mapped result to clients)
    EventManager

Made with ❤️ by codx-junior
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RoundCost(BaseModel):
    """
    Token usage and cost (in cxjcoins) of a single LLM completion round.

    Produced by :meth:`SmolAgent._compute_cost` and aggregated by
    ``_run_chat_loop`` into the run total. This replaces the implicit
    "return a float" contract that hid the token/cost breakdown.
    """
    input_tokens: int = Field(default=0, description="Prompt tokens of the round.")
    output_tokens: int = Field(default=0, description="Completion tokens of the round.")
    input_cost_cxjcoins: float = Field(default=0.0)
    output_cost_cxjcoins: float = Field(default=0.0)

    @property
    def total_cost_cxjcoins(self) -> float:
        """Total round cost in cxjcoins (input + output)."""
        return self.input_cost_cxjcoins + self.output_cost_cxjcoins


class CostInfo(BaseModel):
    """
    Cost breakdown (in cxjcoins) of an agent run or of a single message.

    ``iteration_cost_cxjcoins`` is what must be stored on the response message
    (ChatManager accumulates it into ``Chat.cost``). Everything else is
    descriptive and streamed to clients.
    """
    iteration_cost_cxjcoins: float = Field(
        default=0.0,
        description=(
            "Cost of the run that produced this message, including every LLM "
            "round and every tool cost reported via ToolResponse.cost."
        ),
    )
    chat_cost_cxjcoins: float = Field(
        default=0.0,
        description="Accumulated cost of the chat AFTER this run (Chat.cost).",
    )
    estimation_cost_cxjcoins: Optional[float] = Field(
        default=None,
        description="Configured budget for the chat (Chat.estimation_cost), when set.",
    )
    budget_reached: bool = Field(
        default=False,
        description="True when the run stopped because the chat reached its budget.",
    )
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    input_cost_cxjcoins: float = Field(default=0.0)
    output_cost_cxjcoins: float = Field(default=0.0)
    tool_cost_cxjcoins: float = Field(
        default=0.0,
        description="Portion of iteration_cost reported by tools via ToolResponse.cost.",
    )


class ToolRunSummary(BaseModel):
    """Aggregated execution info for one tool within a single agent run."""
    tool: str = Field(description="Tool name.")
    calls: int = Field(default=0)
    errors: int = Field(default=0)
    cached: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    cost_cxjcoins: float = Field(default=0.0)


class ToolCacheStats(BaseModel):
    """Tool result cache statistics for the run."""
    hits: int = Field(default=0)
    misses: int = Field(default=0)
    size: int = Field(default=0)
    hit_rate: int = Field(default=0, description="Cache hit rate percentage (0-100).")


class AgentRunResult(BaseModel):
    """
    Complete, typed result of a single SmolAgent run (one chat turn).

    Replaces the previous ``additional_kwargs={'cost':..., 'chat_cost':...}``
    contract. ``SmolAgent.chat`` returns this; ``AI.a_chat_run`` surfaces it.
    """
    content: str = Field(
        default="",
        description="Final assistant text (thinking content + final answer).",
    )
    messages: List[Any] = Field(
        default_factory=list,
        description=(
            "Conversation including the appended final assistant message "
            "(LangChain objects; never serialized directly)."
        ),
    )
    cost: CostInfo = Field(default_factory=CostInfo)
    iterations: int = Field(default=0, description="Number of LLM rounds performed.")
    tools_used: bool = Field(default=False)
    tools: List[ToolRunSummary] = Field(default_factory=list)
    cache: ToolCacheStats = Field(default_factory=ToolCacheStats)
    loop_limit_reached: bool = Field(
        default=False, description="True when LoopGuard stopped the run."
    )
    agile: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Agile clock state of the turn (``codx.junior.ai.smol.agile``): "
            "mode/state/elapsed/percent/warning. The turn is NEVER stopped by "
            "the clock; ChatEngine stamps it on the response message "
            "metadata (``meta_data['agile']``) so the client can warn the user "
            "and the user can stop or split the task. ``None`` for a turn "
            "without an agile target (max, plain chats)."
        ),
    )
    reasoning_content: str = Field(
        default="",
        description=(
            "Chain of thought produced by a thinking-mode provider (DeepSeek "
            "``reasoning_content``) across the rounds of the turn. It is NEVER "
            "shown to the user: ChatEngine stores it on the response message "
            "so the next request can echo it back, because with ``tools`` "
            "present the provider requires the CoT of every previous turn "
            "(otherwise it answers 400 'The `reasoning_content` in the "
            "thinking mode must be passed back to the API'). Empty for "
            "providers without thinking mode."
        ),
    )

# Made with ❤️ by codx-junior