"""
Constants for SmolAgent tool calling and streaming.

Made with ❤️ by codx-junior
"""

# Fallback maximum number of iterative tool rounds
# (when not configured in model/provider settings)
DEFAULT_MAX_ITERATIONS: int = 10

# Fallback maximum number of tool calls per single round
# (when not configured in model/provider settings)
DEFAULT_MAX_TOOL_CALLS: int = 5

# ── Tool execution timeout ────────────────────────────────────────────────────
# A tool that hangs must never keep the whole chat turn (and with it the event
# loop / process) blocked forever. Every tool execution is therefore bounded:
#
# * a tool settings ``timeout_seconds`` (in the tool registry) wins,
# * then the model/provider ``tool_timeout_seconds`` (AISettings),
# * then ``CODX_JUNIOR_TOOL_TIMEOUT_SECONDS``,
# * then DEFAULT_TOOL_TIMEOUT_SECONDS.
#
# ``0`` (or a negative value) disables the timeout for a tool.
ENV_TOOL_TIMEOUT_SECONDS: str = "CODX_JUNIOR_TOOL_TIMEOUT_SECONDS"
DEFAULT_TOOL_TIMEOUT_SECONDS: float = 300.0

# Maximum number of sequential tool-call rounds (each round may contain
# multiple parallel tool calls) before aborting the conversation.
MAX_TOOL_ROUNDS: int = 20

# Number of consecutive identical tool-call rounds that trigger loop detection.
MAX_IDENTICAL_ROUNDS: int = 3

TOOL_ROUNDS_ERROR_MSG: str = (
    "Maximum tool rounds ({rounds}) exceeded. "
    "The model keeps requesting tools without producing a final answer."
)

TOOL_LOOP_ERROR_MSG: str = (
    "Infinite tool-call loop detected: the model requested the identical "
    "set of tools with the same arguments {count} times in a row."
)

# Seconds to buffer streamed chunks before flushing them to callbacks.
CALLBACK_FLUSH_SECONDS: float = 1.0

# Maximum characters of a tool result included in TOOL_END event payloads.
# Keeps chat message meta_data and streamed events reasonably small.
TOOL_RESULT_PREVIEW_MAX_CHARS: int = 4000


TOOL_CALLS_EXCEEDED_ERROR_MSG: str = (
    "Tool call limit exceeded in round: maximum {max_calls} allowed, but {actual_calls} were requested. "
    "Try using fewer tools or simpler operations."
)

# ── Tool-limit continuation (harness tasks) ───────────────────────────────────
# The tool limits above are a GUARDRAIL against rabbit holes, never a stop
# condition for harness work. When a harness turn (an orchestration worker, the
# run supervisor, a delegated subagent or the `codx-junior` harness profile) hits
# them, the turn is CONTINUED instead of ended: the model is told that it used
# too many tools, receives every fact already collected (deterministic ledger,
# no extra AI call) and must rethink its path with a smaller budget. A harness
# task is expected to resolve itself, so it only stops when the work is done or
# a human decision is required.

# How many course corrections a harness turn may get before it stops.
TOOL_LIMIT_CONTINUATIONS: int = 2

# Budget granted after a course correction: the model must adapt its path, not
# spend the same allowance again (fewer, better calls).
TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS: int = 6
TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS: int = 3

# Maximum characters of the deterministic "facts already collected" ledger
# embedded in the course-correction notice.
TOOL_LIMIT_FACTS_MAX_CHARS: int = 6000

# The course-correction notice injected into the conversation (request only: it
# is never persisted in the chat). ``{...}`` placeholders are filled by
# SmolAgent._build_tool_limit_rethink.
TOOL_LIMIT_RETHINK_TEMPLATE: str = (
    "## ⛔ Tool budget spent — continue with a DIFFERENT path\n\n"
    "You requested more tools than this turn's guardrail allows: {loop_error}\n\n"
    "This is a GUARDRAIL, not a stop: your work is NOT lost and the task is NOT "
    "over. The tool calls you were about to make were not executed.\n\n"
    "### Facts already collected (do NOT call tools to obtain them again)\n"
    "{facts}\n\n"
    "### What you must do now (continuation {attempt} of {attempts})\n"
    "1. Do NOT repeat any call listed above: its result is here and in the "
    "conversation. Repeating it burns the budget without adding facts.\n"
    "2. Pick the SINGLE smallest next action that moves the task forward and do "
    "it with at most {max_calls} tool calls in this round (batch independent "
    "reads in one call).\n"
    "3. If the guardrail tripped because you were investigating the WRONG path, "
    "say so and switch approach instead of digging deeper.\n"
    "4. State what is confirmed, what is ruled out and the next concrete step in "
    "your text, not only through tool calls.\n"
    "5. If you cannot progress without a human decision (missing credential, "
    "ambiguous requirement, destructive or infrastructural action, sandbox you "
    "cannot change), STOP calling tools and answer with: the confirmed facts, "
    "the exact question/decision you need from the user and what you will do "
    "next once answered.\n"
)

# ── Tool usage summary (generated only when the tool loop breaks) ─────────────
# When LoopGuard stops a run, the tool conversation (the results the model saw)
# would otherwise be discarded. A single lightweight request to the project's
# knowledge/RAG model distils the value of the tools used so it can be appended
# to the assistant answer and reused in the next iteration without re-running
# the same tools.

# Analytics tag used for the summary request token usage, so its cost is
# visible but separable from the main chat run.
TOOL_ANALYSIS_TAG: str = "tool-analysis"

# Maximum characters of each tool result embedded in the summary prompt.
TOOL_ANALYSIS_RESULT_MAX_CHARS: int = 2000

# Maximum characters of the user request embedded in the summary prompt.
TOOL_ANALYSIS_TASK_MAX_CHARS: int = 3000

# Maximum tokens requested for the generated summary.
TOOL_ANALYSIS_MAX_TOKENS: int = 800

# Heading that delimits the summary block appended to the assistant answer.
TOOL_ANALYSIS_HEADING: str = "### 🧰 Tool usage summary"

# System prompt for the tool usage summary request.
TOOL_ANALYSIS_SYSTEM_PROMPT: str = (
    "You review how an autonomous agent used its tools. The agent was "
    "interrupted because it reached its tool-usage limits, and its work must "
    "be preserved so the next iteration can continue without repeating tools.\n"
    "Given the user request and the tools that were executed (arguments and "
    "results), write a concise Markdown summary with these sections:\n"
    "1. **Useful findings** – what each relevant tool call revealed, with the "
    "concrete evidence (paths, files, values) that must NOT be re-discovered.\n"
    "2. **Not useful / already known** – tool calls that returned nothing new "
    "or were irrelevant, so they should not be repeated.\n"
    "3. **Suggested next step** – the smallest next action to continue.\n"
    "Be factual and specific. Never invent information absent from the tool "
    "results. Keep it under ~300 words."
)

# Heading used in logs/events when a tool limit triggers a course correction.
TOOL_LIMIT_RETHINK_LOGS_LABEL: str = "tool-limit-continuation"

# Fallback note appended when the summary cannot be produced by the model.
# It still lists the tools already executed so the next iteration is never
# worse off than before this feature existed.
TOOL_ANALYSIS_FALLBACK_NOTE: str = (
    "The tool iteration limit was reached. The tools listed below were "
    "already executed in this run and should not be repeated unless their "
    "inputs changed."
)

# ── Chat cost budget (Chat.estimation_cost) ───────────────────────────────────
# Appended to the assistant answer when the chat reaches its estimated cost
# limit. The run is stopped so the chat never spends more than the budget the
# user set on ``Chat.estimation_cost`` (in cxjcoins). ``spent`` is compared
# against the chat's ACCUMULATED cost (``Chat.cost``), which is not reduced when
# messages are deleted.
ESTIMATION_COST_EXCEEDED_MSG: str = (
    "\n\n---\n\n### 💸 Chat cost limit reached\n\n"
    "This chat reached its estimated cost limit "
    "(spent: {spent:.4f} cxjcoins, limit: {limit:.4f}). "
    "Execution was stopped to keep the chat within budget.\n"
)

# Made with ❤️ by codx-junior