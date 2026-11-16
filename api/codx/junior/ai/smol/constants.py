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
#
# The default is deliberately SHORT: a harness turn pays for this timeout in
# wall-clock time, and a tool that needs minutes (docker build, full test suite)
# is almost always waiting on something the agent cannot fix by waiting longer.
# A timed-out tool returns a clear error to the model, which then changes
# strategy instead of blocking the step. Slow-but-legitimate tools must declare
# their own ``timeout_seconds`` in the tool registry.
ENV_TOOL_TIMEOUT_SECONDS: str = "CODX_JUNIOR_TOOL_TIMEOUT_SECONDS"
DEFAULT_TOOL_TIMEOUT_SECONDS: float = 120.0

# ── Tool parallelism ──────────────────────────────────────────────────────────
# Independent tool calls of the SAME model round run concurrently: a round that
# asks for six independent reads used to pay the sum of the six latencies, and a
# harness turn can issue dozens of calls. Mutating and session-bound calls act as
# a barrier so the model's call order is still honoured (see
# ``codx.junior.ai.smol.parallel``).
#
# ``tool_parallelism`` caps how many calls of one batch run at the same time:
# the server is shared with other chats/workers, so an unbounded fan-out would
# starve them. Priority: model/provider ``tool_parallelism`` > env > default.
ENV_TOOL_PARALLELISM: str = "CODX_JUNIOR_TOOL_PARALLELISM"
DEFAULT_TOOL_PARALLELISM: int = 8
MAX_TOOL_PARALLELISM: int = 32

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

# ── Effort tiers (agile by default, max only for big work) ────────────────────
# AGILE (profile ``codx-junior``) is the DEFAULT tier: a small base budget that
# is a PACING signal, never the end of the task. Reaching the tool limit grants
# the bounded course correction below (the facts already collected + a smaller
# renewed budget), so a turn is not dead-ended by its own guardrail.
# Its TIME policy is different: an agile turn is NEVER killed by the clock.
# ``AGILE_WALL_CLOCK_SECONDS`` is the first "hurry up" checkpoint, not a
# deadline, and ``AGILE_TARGET_SECONDS`` is the agile promise (a turn under 20
# minutes). Past the target the turn keeps running and the response message is
# MARKED so the client can warn the user (see ``codx.junior.ai.smol.agile``).
# MAX (profile ``codx-junior-max``) has the bigger budget and the longer
# continuation. Neither tier may cancel a task: every built-in hard stop was
# removed because the user must remain the only one who ends a task. A caller
# can still opt into an enforced clock with
# ``settings={"stop_on_wall_clock": True}``; nothing enables it by default.
# Resolution lives in ``codx.junior.ai.smol.effort``.
AGILE_MAX_ITERATIONS: int = 10
AGILE_MAX_TOOL_CALLS: int = 5
AGILE_MAX_TOOL_ROUNDS: int = 12
# An agile turn is NEVER dead-ended by its budget. Reaching the tool limit
# grants the same bounded course correction the harness always had (the facts
# already collected + a smaller renewed budget); the tier stays agile because
# the base budget and the continuations are smaller than max's. A limit is a
# pacing signal, not the end of the task.
AGILE_CONTINUATIONS: int = TOOL_LIMIT_CONTINUATIONS
# First agility checkpoint of an agile turn (5 min): it only produces a notice,
# it never stops the turn. Kept as a float and overridable by env.
AGILE_WALL_CLOCK_SECONDS: float = 300.0
ENV_AGILE_WALL_CLOCK_SECONDS: str = "CODX_JUNIOR_AGILE_WALL_CLOCK_SECONDS"

# ── Agile time policy (nudges, never a stop) ──────────────────────────────────
# An agile turn must fit in 20 minutes. The clock is used to STRESS the model,
# not to kill the work: at every checkpoint the model receives a deterministic
# notice (fewer tool calls, wrap up, deliver the increment) and the response
# message metadata is stamped so the client can warn the user. The turn keeps
# running: only the human decides to stop it.
AGILE_TARGET_SECONDS: float = 1200.0
ENV_AGILE_TARGET_SECONDS: str = "CODX_JUNIOR_AGILE_TARGET_SECONDS"

# Fractions of the target at which the model is told to speed up. The target
# itself is always a checkpoint (the agile promise) and the clock keeps
# reminding once per target period after it, so a runaway turn is never silent.
AGILE_NUDGE_FRACTIONS: tuple = (0.25, 0.5, 0.75)

# From this fraction of the target the message metadata is flagged as a WARNING
# (the client can already offer "stop / split the task" before the promise is
# broken) and from 1.0 as OVERTIME.
AGILE_WARNING_FRACTION: float = 0.75

# Metadata key of the agile state on the response message meta_data.
AGILE_META_KEY: str = "agile"
AGILE_STATE_OK: str = "ok"
AGILE_STATE_WARNING: str = "warning"
AGILE_STATE_OVERTIME: str = "overtime"

# ── Agile mode: prompt + notices ──────────────────────────────────────────────
# Injected into the system prompt of an agile turn: the model must know it is
# running in agile mode BEFORE it plans (that is the cheapest way to be fast).
# ``{target_minutes}`` is filled by ``codx.junior.ai.smol.agile``.
AGILE_SYSTEM_SECTION: str = (
    "\n\n### ⏱️ AGILE MODE — this turn must be FAST\n"
    "\n"
    "You are the `codx-junior` (AGILE) harness. An agile turn is expected to "
    "deliver a useful increment in less than {target_minutes} minutes, and the "
    "clock is measured on YOU: every round and every tool call spends it.\n"
    "\n"
    "**How to be agile (apply from the first round):**\n"
    "1. ONE small increment per turn: the smallest slice the user can look at "
    "now. Never chain investigations — if the work needs several increments, "
    "do the first one and list the rest under `## Next tasks`.\n"
    "2. BATCH your discovery: one `project_locate` / one bulk `project_read_file` "
    "with several paths / one `snapshot` instead of many calls. Never page "
    "through a large file: request only the lines you need.\n"
    "3. State a hypothesis after 3-5 discovery calls and make the smallest "
    "change that tests it. Investigation without action is the main way a "
    "turn gets slow.\n"
    "4. Prefer FAST tools: targeted tests over the full suite, `docker ps` "
    "over long builds, `project_read_file` over shell commands. A tool that "
    "waits minutes is almost never the agile choice.\n"
    "5. VALIDATE what you touched, then close with the four sections "
    "(What happened / What I did / Demo / Next tasks) and the checkpoint line.\n"
    "6. Never end a turn with only tool calls: write down what is confirmed, "
    "what is ruled out and the single next step.\n"
    "\n"
    "This turn is NOT stopped by the clock: if it grows long you will be asked "
    "to wrap up and the user is warned. Wrapping up on time is YOUR job.\n"
)

# Notice injected (request only, never persisted) into the model conversation at
# each agility checkpoint reached before the agile promise is broken.
AGILE_NUDGE_TEMPLATE: str = (
    "## ⏱️ Time checkpoint — {elapsed} of {target} used ({pct}%)\n"
    "\n"
    "This turn has a {target} target and {remaining} left. Your work "
    "is NOT lost and the task is NOT stopped — but you must SPEED UP now.\n"
    "\n"
    "1. Finish the increment you already started instead of opening a new "
    "investigation.\n"
    "2. No more discovery calls unless they are strictly required by the change "
    "you are already making: batch what is left in ONE call.\n"
    "3. Prefer the fastest validation you can justify (targeted tests for the "
    "files you touched).\n"
    "4. In your NEXT message state: what is confirmed, what is ruled out and the "
    "single next concrete step. If you cannot finish, deliver the partial "
    "increment with its own summary and list the rest under `## Next tasks`.\n"
)

# Notice injected at, and after, the agile target (the promise is broken).
AGILE_OVERTIME_TEMPLATE: str = (
    "## 🚨 Turn OVER TIME — wrap up NOW\n"
    "\n"
    "This turn passed its target by {overdue} ({elapsed} of {target}). "
    "It is NOT stopped: the user sees a warning and decides whether to stop it, "
    "so the turn must become useful IMMEDIATELY.\n"
    "\n"
    "1. Stop investigating. Spend at most ONE round finishing the increment you "
    "already have (edit the file, run the smallest check) or zero rounds if it "
    "is already done.\n"
    "2. Then answer with the four sections and the checkpoint line, naming under "
    "`## Next tasks` everything that is still pending.\n"
    "3. Never leave the turn without a written answer: what is confirmed, what "
    "is ruled out and the single next step.\n"
)

# Footnote appended to the answer when an agile turn ENDED past its target. It
# replaces the old "time budget reached" stop notice: the work is delivered, it
# was only slower than the agile promise.
AGILE_OVERTIME_FOOTNOTE: str = (
    "\n\n> ⏱️ **Time target exceeded** — this turn took {elapsed} against a "
    "{target} target. It was not stopped: the increment above is the delivery. "
    "Split the remaining work into a new turn (`## Next tasks`).\n\n"
)

# Warnings stamped into the response message metadata so the CLIENT can show
# them and the user can take action. ``{...}`` placeholders are filled by
# ``AgileClock.metadata``.
AGILE_WARNING_USER_MESSAGE: str = (
    "This turn used {pct}% of its {target} target ({elapsed} used, "
    "{remaining} left). It is still running — stop it or split the task if "
    "you don't need the result."
)
AGILE_OVERTIME_USER_MESSAGE: str = (
    "This turn exceeded its {target} target by {overdue} and is still "
    "running. It was NOT stopped: the model has been told to wrap up. Stop it "
    "or split the task to get an answer sooner."
)

MAX_MAX_ITERATIONS: int = 24
MAX_MAX_TOOL_CALLS: int = 6
MAX_MAX_TOOL_ROUNDS: int = 40
# ADVISORY target of a max turn (1 h), NOT a deadline: the turn is told to wrap
# up and the response is marked, but only the user stops it.
MAX_WALL_CLOCK_SECONDS: float = 3600.0

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