"""
Constants shared by the orchestration runtime and its callers.

This module is intentionally dependency free (standard library only) because it
is imported by both the agent runtime (``codx.junior.ai.smol``), the chat engine
and the orchestration package itself. Importing the heavy orchestration modules
from those places would create import cycles.

The orchestration runtime turns one **main task** (a normal ``Chat``) into a
durable, multi-step run:

* the main task owns the objective, the budget and the supervisor agent,
* every plan step is a **child task card** on a board and a sprint, so the work
  stays visible in the existing board/sprint UI,
* a background worker advances the run one tick at a time, surviving restarts.

Made with ❤️ by codx-junior
"""

# Internal AI request header that declares the orchestration role of the current
# chat. It is consumed by SmolAgent and never forwarded to the provider.
ORCHESTRATION_HEADER = "orchestration"

# Tool names. They are plain chat-scope tools registered in ``tools/TOOLS`` and
# injected per run (supervisor) or provided by the built-in orchestrator profile.
ORCHESTRATOR_TOOL = "orchestrator"
DELEGATE_TOOL = "delegate"

# Chat mode used by the main task of a run. Unknown modes behave like a normal
# chat (see ChatEngine._resolve_chat_mode_flags), which is exactly what the
# supervisor needs: a standard turn with the orchestrator profile composed.
CHAT_MODE_ORCHESTRATE = "orchestrate"

# Chat mode used by delegated subagent cards created by the ``delegate`` tool.
# A delegated chat cannot delegate again (single level), which keeps the tree
# finite and the budget predictable.
CHAT_MODE_DELEGATE = "delegate"

# ── Run statuses ────────────────────────────────────────────────────────────
RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_PAUSED = "paused"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_STOPPED = "stopped"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_BUDGET_EXHAUSTED = "budget_exhausted"
RUN_STATUS_STALLED = "stalled"

TERMINAL_RUN_STATUSES = frozenset({
    RUN_STATUS_COMPLETED,
    RUN_STATUS_STOPPED,
    RUN_STATUS_FAILED,
    RUN_STATUS_BUDGET_EXHAUSTED,
    RUN_STATUS_STALLED,
})

ACTIVE_RUN_STATUSES = frozenset({
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
})

# ── Step statuses ───────────────────────────────────────────────────────────
STEP_STATUS_PENDING = "pending"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_DONE = "done"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_BLOCKED = "blocked"
STEP_STATUS_SKIPPED = "skipped"

TERMINAL_STEP_STATUSES = frozenset({
    STEP_STATUS_DONE,
    STEP_STATUS_FAILED,
    STEP_STATUS_BLOCKED,
    STEP_STATUS_SKIPPED,
})

SUCCESSFUL_STEP_STATUSES = frozenset({
    STEP_STATUS_DONE,
    STEP_STATUS_SKIPPED,
})

# ── Kanban mapping ──────────────────────────────────────────────────────────
# Plan phase -> board column title. A run board is a normal kanban board whose
# columns describe the orchestration phases; the sprint of the run is a sprint
# board over the same cards (it groups by Chat.status).
DEFAULT_RUN_COLUMNS = {
    "backlog": "Backlog",
    "running": "In progress",
    "done": "Done",
    "blocked": "Blocked",
}

SPRINT_COLUMN_TITLES = ("To do", "Doing", "On hold", "Done")

# Card status (Chat.status) per step status.
STEP_CHAT_STATUS = {
    STEP_STATUS_PENDING: "todo",
    STEP_STATUS_RUNNING: "doing",
    STEP_STATUS_DONE: "done",
    STEP_STATUS_FAILED: "rejected",
    STEP_STATUS_BLOCKED: "onhold",
    STEP_STATUS_SKIPPED: "done",
}

# Board phase per step status.
STEP_BOARD_PHASE = {
    STEP_STATUS_PENDING: "backlog",
    STEP_STATUS_RUNNING: "running",
    STEP_STATUS_DONE: "done",
    STEP_STATUS_FAILED: "blocked",
    STEP_STATUS_BLOCKED: "blocked",
    STEP_STATUS_SKIPPED: "done",
}

# ── Toolset injected into isolated workers ──────────────────────────────────
# Workers run in their own chat (own context). The profile assigned to the step
# still adds its tools; this baseline guarantees that a worker can inspect and
# modify the project even when the run/step does not declare a profile.
DEFAULT_WORKER_TOOLS = (
    "project_map",
    "project_locate",
    "project_trace",
    "project_impact",
    "project_health",
    "project_read_file",
    "project_write_file",
    "project_changes",
    "apply_file_changes",
    "code_writer",
    "fetch_webpage",
    "get_file_last_version",
    "repository_status",
    "repository_log",
    # Runtime diagnostics: a worker must be able to verify what the *running*
    # service actually loaded (an API started without --reload keeps the old
    # code and every card creation fails with a signature error). Without these
    # the only way out of that state was a human restarting the container.
    "docker_ps",
    "docker_logs",
    "docker_container",
    "docker_exec",
)

# ── Limits / defaults ───────────────────────────────────────────────────────
DEFAULT_BOARD = "Orchestration"
DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_ROUNDS = 40
# Independent steps run CONCURRENTLY, so a wider batch is the cheapest way to
# shorten a run. Each step is its own chat/turn, so the limit is set by the
# worker pool (CODX_JUNIOR_ORCHESTRATION_MAX_RUNS) and the budget, not by the
# model.
DEFAULT_MAX_PARALLEL = 4
MAX_PARALLEL_LIMIT = 4
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_STAGNATION_LIMIT = 3
# A step is ONE worker turn. 30 minutes was long enough that a wedged turn
# (blocked tool, dead provider socket) consumed half a run's wall-clock time
# before reporting anything; 15 minutes is plenty for a focused step and the
# supervisor replans whatever does not finish.
DEFAULT_STEP_TIMEOUT_SECONDS = 900
# The supervisor turn only plans/verifies: it never needs 15 minutes.
DEFAULT_SUPERVISOR_TIMEOUT_SECONDS = 600
# Consecutive step batches executed inside ONE worker tick. Without it a run
# with several independent batches waited for the next tick (seconds apart)
# between every batch.
DEFAULT_MAX_BATCHES_PER_TICK = 3
DEFAULT_MAX_DELEGATE_TASKS = 8
DEFAULT_DELEGATE_CONCURRENCY = 2

# ── Tool-limit stops ─────────────────────────────────────────────────────────
# A harness turn never stops just because it reached its tool budget: the agent
# CONTINUES it (the facts already collected plus a smaller budget) as many times
# as configured (``ai/smol/constants.TOOL_LIMIT_CONTINUATIONS``). Only when those
# continuations are exhausted does the turn end, and the answer then carries the
# marker below. The executor turns that stop into a BLOCKED step (a human
# decision is required) instead of a failed one, so the work and its evidence are
# kept and the supervisor replans instead of retrying the same approach.
TOOL_LIMIT_STOP_MARKER = "Tool budget exhausted"
TOOL_LIMIT_EXHAUSTED_NOTE = (
    "\n\n### ⛔ Tool budget exhausted\n\n"
    "This turn used its complete tool budget: every round allowed by the model "
    "settings plus {attempts} course correction(s) granted after the guardrail "
    "tripped. The facts collected are listed above and remain in the "
    "conversation.\n\n"
    "**A human decision is required to continue**: narrower instructions, a "
    "credential, an environment change or an explicit go-ahead for a "
    "destructive step.\n"
)

# Result contract: the worker must finish with this JSON object. The parser in
# ``models.parse_step_result`` also accepts a fenced JSON block.
STEP_RESULT_STATUSES = ("done", "blocked", "failed")
STEP_RESULT_INSTRUCTIONS = (
    'Finish with a JSON object (and nothing after it):\n'
    '{"status":"done"|"blocked"|"failed","summary":"what was done",'
    '"files":["paths touched"],"verification":"how you verified it",'
    '"followups":["remaining work"]}'
)

# Directories under ``<project>/.codx``.
ORCHESTRATION_DIR = "orchestration"
RUNS_DIR = "runs"
WORKER_FILE = "worker.json"

# Environment switches (documented in docs/orchestration.md).
ENV_ENABLED = "CODX_JUNIOR_ORCHESTRATION"
ENV_WORKER = "CODX_JUNIOR_ORCHESTRATION_WORKER"
ENV_TICK_SECONDS = "CODX_JUNIOR_ORCHESTRATION_TICK_SECONDS"
ENV_MAX_RUNS = "CODX_JUNIOR_ORCHESTRATION_MAX_RUNS"
ENV_WORKER_TTL = "CODX_JUNIOR_ORCHESTRATION_WORKER_TTL"

# Made with ❤️ by codx-junior
