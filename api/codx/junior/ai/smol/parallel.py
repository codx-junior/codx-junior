"""
Execution scheduling for SmolAgent tool rounds.

One model round routinely asks for several tools at once: read three files,
search the atlas, fetch a page. Executing them one after the other pays the SUM
of their latencies, and a harness turn can issue dozens of calls (up to
``max_tool_calls`` per round, many rounds).

Executing the whole round at once is not safe either: some tools mutate shared
state (the working tree, the git index, the kanban board, the shared browser
page, one Docker daemon) and their order matters.

This module decides, per call, whether it may run CONCURRENTLY with its
neighbours and groups an ordered round into batches::

    round  : [read_a, read_b, read_c, write_x, read_a2]
    batches: [[read_a, read_b, read_c], [write_x], [read_a2]]

Batches always run in order and the calls inside a batch run concurrently, so
the model's call order is preserved exactly — a call never observes the state
from before a preceding call — while the common case (a burst of independent
reads, all I/O bound: files, HTTP, git/docker CLI) costs the latency of its
slowest call instead of the sum.

Classification rules, in priority order:

1. ``ToolSettings.parallel`` set on the tool registry entry (``True`` forces
   concurrent execution, ``False`` forces a barrier).
2. Registry ``tags`` that mark a mutation (``write``, ``modification``,
   ``deletion``, ``destructive``, ``creation``).
3. The explicit name list and tokens below (stateful tools, shared interactive
   sessions, heavy subprocess runs).
4. Everything else runs concurrently: an unknown tool is far more likely to be
   a read than a mutation.

Made with ❤️ by codx-junior
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Registry tags that mark a tool as a state mutation. A tool carrying one of
# them is never executed concurrently with its neighbours: the read that follows
# it in the same round must observe the new state.
MUTATION_TAGS = frozenset({
    "write",
    "modification",
    "deletion",
    "destructive",
    "creation",
})

# Tools that must run ALONE, in the model's order.
EXCLUSIVE_TOOL_NAMES = frozenset({
    # ── Filesystem / repository mutations ────────────────────────────────────
    "project_write_file",
    "apply_file_changes",
    "code_writer",
    "create_pr_branch",
    "close_pr",
    "abandon_pr",
    "restore_changes",
    # ── Orchestration / board state: read-then-act on shared cards ───────────
    "orchestrator",
    "delegate",
    "create_task",
    "process_task",
    "project_manager",
    "autonomy_management",
    "team_management",
    "company_management",
    # ── Profile / knowledge / tutorial mutations ─────────────────────────────
    "profile_learning",
    "profile_management",
    "create_profile",
    "update_profile",
    "delete_profile",
    "create_chapter",
    "modify_chapter",
    "delete_chapter",
    # ── Shared browser page: ONE page per project, actions are ordered ───────
    "start_session",
    "finish_session",
    "browse_action",
    "get_screenshot",
    "get_source_code",
    "browser_activity",
    "browser_status",
    "browser_control",
    "list_browser_scripts",
    "get_browser_script",
    "save_browser_script",
    "run_browser_script",
    "delete_browser_script",
    # ── Shared desktop (one VNC view, one mouse) ─────────────────────────────
    "start_computer",
    "finish_computer",
    "computer_action",
    "computer_screenshot",
    "computer_status",
    "computer_control",
    "computer_activity",
    "save_computer",
    # ── Container mutations: one Docker daemon, one compose file ─────────────
    "docker_build",
    "docker_pull",
    "docker_run",
    "docker_start",
    "docker_stop",
    "docker_restart",
    "docker_container",
    "docker_compose_service",
    "docker_exec",
    # ── Heavy subprocess runs and verify-then-report gates ───────────────────
    "qa_run_tests",
    "qa_flaky_check",
    "qa_review_changes",
    "security_review_changes",
})

# Tool-name tokens bound to a single shared interactive device. Matched as a
# substring so every family member (``list_browser_scripts``,
# ``run_browser_script``...) is covered without listing it twice.
EXCLUSIVE_TOOL_TOKENS: Tuple[str, ...] = ("browser", "computer")


def _normalise_tags(tags: Optional[Sequence[Any]]) -> List[str]:
    """Lower-case the registry tags of a tool (defensive against None)."""
    return [str(tag).lower() for tag in (tags or [])]


def tags_mark_mutation(tags: Optional[Sequence[Any]]) -> bool:
    """
    Return True when the registry tags mark the tool as a state mutation.

    Args:
        tags: The tool's registry ``tags`` (may be None).

    Returns:
        True when any tag belongs to :data:`MUTATION_TAGS`.
    """
    return bool(MUTATION_TAGS.intersection(_normalise_tags(tags)))


def is_exclusive_tool(
    tool_name: str,
    settings: Optional[Dict[str, Any]] = None,
    tags: Optional[Sequence[Any]] = None,
) -> bool:
    """
    Return True when *tool_name* must run alone (a barrier in the round).

    Args:
        tool_name: Tool name as exposed to the model.
        settings:  The tool's registry ``settings`` dict (may be None).
        tags:      The tool's registry ``tags`` (may be None).

    Returns:
        True when the call cannot run concurrently with its neighbours.
    """
    name = str(tool_name or "")
    if not name:
        # Unknown identity: never fan out something we cannot classify.
        return True

    configured = (settings or {}).get("parallel")
    if configured is True:
        return False
    if configured is False:
        return True

    if tags_mark_mutation(tags):
        return True

    if name in EXCLUSIVE_TOOL_NAMES:
        return True

    return any(token in name for token in EXCLUSIVE_TOOL_TOKENS)


def plan_tool_batches(
    tool_calls: Sequence[Tuple[str, Dict[str, Any]]],
    is_exclusive: Callable[[Dict[str, Any]], bool],
) -> List[List[Tuple[str, Dict[str, Any]]]]:
    """
    Group an ordered round into execution batches.

    Consecutive independent calls are grouped in one batch (they run
    concurrently); an exclusive call becomes its own batch (the barrier), so the
    batches — executed in order — reproduce the model's call order exactly.

    Args:
        tool_calls:   Ordered ``(tool_id, tool_call)`` pairs.
        is_exclusive: Predicate telling whether a ``tool_call`` is a barrier.

    Returns:
        List of batches, each a list of ``(tool_id, tool_call)`` pairs. Empty
        input yields an empty list.
    """
    batches: List[List[Tuple[str, Dict[str, Any]]]] = []
    current: List[Tuple[str, Dict[str, Any]]] = []

    for tool_id, tool_call in tool_calls:
        if is_exclusive(tool_call):
            if current:
                batches.append(current)
                current = []
            batches.append([(tool_id, tool_call)])
            continue
        current.append((tool_id, tool_call))

    if current:
        batches.append(current)
    return batches


def describe_batches(batches: Sequence[Sequence[Tuple[str, Dict[str, Any]]]]) -> str:
    """
    Render the batch plan for a log line (``[a, b] | [c]``).

    Args:
        batches: Batches produced by :func:`plan_tool_batches`.

    Returns:
        A compact human-readable description.
    """
    return " | ".join(
        "[" + ", ".join(str(tool_call.get("function", "?")) for _, tool_call in batch) + "]"
        for batch in batches
    )

# Made with ❤️ by codx-junior
