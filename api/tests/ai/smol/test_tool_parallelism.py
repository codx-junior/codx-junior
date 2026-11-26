"""
Tests for SmolAgent tool-round parallelism.

Covers the scheduling helpers (:mod:`codx.junior.ai.smol.parallel`) and the
agent methods that use them (:meth:`SmolAgent._execute_tool_round` /
:meth:`SmolAgent._execute_tool_batch`):

  • classification: reads run concurrently, mutations/sessions are barriers
  • batch planning preserves the model's call order
  • a round of independent calls costs one call's latency, not the sum
  • an exclusive call serializes the calls issued around it
  • the concurrency limit is respected (and 1 keeps it sequential)
  • results keep the original order, internal failures become tool errors and
    cancellation still propagates
"""
import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

from engine.agent_runtime import AgentCancelled, AgentRunContext

from codx.junior.ai.smol.parallel import (
    EXCLUSIVE_TOOL_NAMES,
    describe_batches,
    is_exclusive_tool,
    plan_tool_batches,
    tags_mark_mutation,
)
from codx.junior.ai.smol.smol_agent import SmolAgent


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def make_agent(parallelism: int = 8) -> SmolAgent:
    """Build a real SmolAgent over mocked settings (no provider call)."""
    llm_settings = Mock()
    llm_settings.model = "gpt-4"
    llm_settings.api_key = "sk-test"
    llm_settings.api_url = "https://api.openai.com/v1"
    llm_settings.system = None
    llm_settings.provider = "openai"
    llm_settings.temperature = 0.0
    llm_settings.tool_timeout_seconds = None
    llm_settings.tool_parallelism = parallelism

    settings = Mock()
    settings.get_llm_settings.return_value = llm_settings
    settings.project_name = "Test Project"
    settings.project_id = "test-project-123"

    with patch("codx.junior.ai.smol.smol_agent.OpenAI"):
        return SmolAgent(settings=settings, user=None, system=None)


def tool_call(name: str, call_id: Optional[str] = None) -> Dict[str, Any]:
    """Minimal tool-call dict, as produced by ToolCallAccumulator."""
    return {"id": call_id or name, "function": name, "arguments": "{}"}


def round_of(*names: str) -> Dict[str, Dict[str, Any]]:
    """Build an ordered tool-call round (tool_call_id → tool_call)."""
    return {
        f"id{i}": tool_call(name, f"id{i}") for i, name in enumerate(names)
    }


class FakeRunContext(AgentRunContext):
    """Real run context (events, analytics, run_id) with a cancel switch."""

    def __init__(self, cancelled: bool = False) -> None:
        super().__init__(project_id="test-project")
        self.cancelled = cancelled
        self.checkpoints = 0

    def checkpoint(self) -> None:
        self.checkpoints += 1
        if self.cancelled:
            raise AgentCancelled("cancelled")


def install_fake_execute(
    agent: SmolAgent,
    delay: float = 0.0,
    events: Optional[List[str]] = None,
    tracker: Optional[Dict[str, int]] = None,
):
    """Replace _execute_tool with a stub that records what happened."""

    async def fake_execute(
        tool_call: Dict[str, Any],
        run_context: Any = None,
        request_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        tool_cache: Any = None,
    ) -> str:
        name = tool_call["function"]
        if tracker is not None:
            tracker["in_flight"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["in_flight"])
        if events is not None:
            events.append(f"start:{name}")
        if delay:
            await asyncio.sleep(delay)
        if tracker is not None:
            tracker["in_flight"] -= 1
        if events is not None:
            events.append(f"end:{name}")
        return f"result:{name}"

    agent._execute_tool = fake_execute  # type: ignore[method-assign]
    return fake_execute


# ──────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION
# ──────────────────────────────────────────────────────────────────────────────


class TestToolClassification:
    """Which calls may run concurrently with their neighbours."""

    def test_read_only_tools_run_concurrently(self):
        for name in (
            "project_read_file",
            "project_locate",
            "project_map",
            "search_codx",
            "repository_log",
            "docker_logs",
            "fetch_webpage",
            "duckduckgo_search",
        ):
            assert is_exclusive_tool(name) is False, name

    def test_mutations_are_barriers(self):
        for name in (
            "project_write_file",
            "apply_file_changes",
            "orchestrator",
            "create_task",
            "process_task",
            "qa_run_tests",
            "docker_compose_service",
        ):
            assert name in EXCLUSIVE_TOOL_NAMES
            assert is_exclusive_tool(name) is True, name

    def test_shared_interactive_sessions_are_barriers(self):
        for name in (
            "browse_action",
            "get_screenshot",
            "list_browser_scripts",
            "run_browser_script",
            "computer_action",
            "start_computer",
        ):
            assert is_exclusive_tool(name) is True, name

    def test_registry_tags_mark_mutations(self):
        assert tags_mark_mutation(["project", "write"]) is True
        assert tags_mark_mutation(["project", "edit", "MODIFICATION"]) is True
        assert tags_mark_mutation(["project", "read"]) is False
        assert tags_mark_mutation(None) is False

    def test_tool_settings_override_the_classification(self):
        assert is_exclusive_tool("project_read_file", {"parallel": False}) is True
        assert is_exclusive_tool("project_write_file", {"parallel": True}) is False

    def test_unknown_tool_is_never_fanned_out(self):
        assert is_exclusive_tool("") is True

    def test_new_read_tool_defaults_to_concurrent(self):
        assert is_exclusive_tool("some_new_read_tool", {"scope": "chat"}, ["read"]) is False

    def test_agent_resolves_the_registry_settings(self):
        agent = make_agent()
        assert agent._tool_is_exclusive(tool_call("project_read_file")) is False
        assert agent._tool_is_exclusive(tool_call("project_write_file")) is True


# ──────────────────────────────────────────────────────────────────────────────
# BATCH PLANNING
# ──────────────────────────────────────────────────────────────────────────────


class TestBatchPlanning:
    """Grouping an ordered round without changing its semantics."""

    @staticmethod
    def names(batches) -> List[List[str]]:
        return [[call["function"] for _, call in batch] for batch in batches]

    def test_consecutive_reads_share_one_batch(self):
        calls = list(round_of("project_read_file", "project_locate", "fetch_webpage").items())
        batches = plan_tool_batches(
            calls, lambda c: is_exclusive_tool(c["function"])
        )
        assert self.names(batches) == [
            ["project_read_file", "project_locate", "fetch_webpage"]
        ]

    def test_a_mutation_splits_the_round_and_keeps_the_order(self):
        calls = list(
            round_of(
                "project_read_file",
                "project_write_file",
                "project_read_file",
            ).items()
        )
        batches = plan_tool_batches(
            calls, lambda c: is_exclusive_tool(c["function"])
        )
        assert self.names(batches) == [
            ["project_read_file"],
            ["project_write_file"],
            ["project_read_file"],
        ]

    def test_trailing_reads_after_a_mutation_stay_separate(self):
        calls = list(
            round_of("orchestrator", "project_read_file", "docker_logs").items()
        )
        batches = plan_tool_batches(
            calls, lambda c: is_exclusive_tool(c["function"])
        )
        assert self.names(batches) == [
            ["orchestrator"],
            ["project_read_file", "docker_logs"],
        ]

    def test_empty_round(self):
        assert plan_tool_batches([], lambda c: False) == []

    def test_describe_batches_is_readable(self):
        calls = list(round_of("project_read_file", "project_write_file").items())
        batches = plan_tool_batches(
            calls, lambda c: is_exclusive_tool(c["function"])
        )
        assert describe_batches(batches) == "[project_read_file] | [project_write_file]"


# ──────────────────────────────────────────────────────────────────────────────
# EXECUTION
# ──────────────────────────────────────────────────────────────────────────────


class TestToolRoundExecution:
    """Concurrency, ordering and safety of a full round."""

    @pytest.mark.asyncio
    async def test_independent_tools_run_concurrently(self):
        agent = make_agent(parallelism=8)
        install_fake_execute(agent, delay=0.2)
        calls = round_of(
            "project_read_file",
            "project_locate",
            "fetch_webpage",
            "docker_logs",
        )

        started = time.monotonic()
        results = await agent._execute_tool_round(
            calls, run_context=FakeRunContext()
        )
        elapsed = time.monotonic() - started

        # Sequential execution would take 4 × 0.2s = 0.8s.
        assert elapsed < 0.45, f"round took {elapsed:.2f}s, expected one call's latency"
        assert [call["function"] for _, call, _ in results] == [
            "project_read_file",
            "project_locate",
            "fetch_webpage",
            "docker_logs",
        ]
        assert [result for _, _, result in results] == [
            "result:project_read_file",
            "result:project_locate",
            "result:fetch_webpage",
            "result:docker_logs",
        ]

    @pytest.mark.asyncio
    async def test_exclusive_tool_is_a_barrier(self):
        agent = make_agent(parallelism=8)
        events: List[str] = []
        install_fake_execute(agent, delay=0.02, events=events)
        calls = round_of(
            "project_read_file",
            "project_write_file",
            "project_locate",
        )

        await agent._execute_tool_round(calls, run_context=FakeRunContext())

        assert events == [
            "start:project_read_file",
            "end:project_read_file",
            "start:project_write_file",
            "end:project_write_file",
            "start:project_locate",
            "end:project_locate",
        ]

    @pytest.mark.asyncio
    async def test_parallelism_limit_is_respected(self):
        agent = make_agent(parallelism=2)
        tracker = {"in_flight": 0, "peak": 0}
        install_fake_execute(agent, delay=0.05, tracker=tracker)
        calls = round_of(
            "project_read_file",
            "project_locate",
            "fetch_webpage",
            "docker_logs",
            "repository_log",
            "project_grep",
        )

        results = await agent._execute_tool_round(
            calls, run_context=FakeRunContext()
        )

        assert tracker["peak"] == 2
        assert len(results) == 6

    @pytest.mark.asyncio
    async def test_parallelism_one_stays_sequential(self):
        agent = make_agent(parallelism=1)
        tracker = {"in_flight": 0, "peak": 0}
        install_fake_execute(agent, delay=0.05, tracker=tracker)
        calls = round_of("project_read_file", "project_locate", "fetch_webpage")

        started = time.monotonic()
        await agent._execute_tool_round(calls, run_context=FakeRunContext())
        elapsed = time.monotonic() - started

        assert tracker["peak"] == 1
        assert elapsed >= 0.15

    @pytest.mark.asyncio
    async def test_every_call_gets_a_cancellation_checkpoint(self):
        agent = make_agent(parallelism=4)
        install_fake_execute(agent, delay=0.01)
        context = FakeRunContext()
        calls = round_of(
            "project_read_file",
            "project_write_file",
            "fetch_webpage",
        )

        await agent._execute_tool_round(calls, run_context=context)

        assert context.checkpoints >= len(calls)

    @pytest.mark.asyncio
    async def test_cancellation_propagates_from_a_parallel_batch(self):
        agent = make_agent(parallelism=4)
        install_fake_execute(agent, delay=0.01)
        calls = round_of("project_read_file", "project_locate", "fetch_webpage")

        with pytest.raises(AgentCancelled):
            await agent._execute_tool_round(
                calls, run_context=FakeRunContext(cancelled=True)
            )

    @pytest.mark.asyncio
    async def test_internal_failure_becomes_a_tool_error(self):
        agent = make_agent(parallelism=4)

        async def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        agent._execute_tool = boom  # type: ignore[method-assign]
        calls = round_of("project_read_file", "fetch_webpage")

        results = await agent._execute_tool_round(
            calls, run_context=FakeRunContext()
        )

        assert len(results) == 2
        for _, _, result in results:
            assert "kaboom" in result

    @pytest.mark.asyncio
    async def test_single_call_round_skips_the_batch_machinery(self):
        agent = make_agent(parallelism=8)
        events: List[str] = []
        install_fake_execute(agent, events=events)

        results = await agent._execute_tool_round(
            round_of("project_write_file"), run_context=FakeRunContext()
        )

        assert events == ["start:project_write_file", "end:project_write_file"]
        assert results[0][2] == "result:project_write_file"

    @pytest.mark.asyncio
    async def test_empty_round_returns_no_results(self):
        agent = make_agent()
        assert await agent._execute_tool_round({}, run_context=FakeRunContext()) == []


class TestRealBlockingTools:
    """End-to-end through _execute_tool: real sync tools, real threads."""

    @staticmethod
    def slow_tool_registry(names, delay=0.2, seen=None):
        tools = []
        for name in names:
            def slow(_name=name, **kwargs):
                if seen is not None:
                    seen.append(_name)
                time.sleep(delay)
                return f"ok:{_name}"

            tools.append(
                {
                    "tool_json": {"function": {"name": name}},
                    "settings": {"scope": "chat", "async": False},
                    "tags": ["read"],
                    "tool_call": slow,
                }
            )
        return tools

    async def run_round(self, parallelism: int, names, delay=0.2):
        agent = make_agent(parallelism=parallelism)
        agent.harness = False
        agent.tools = self.slow_tool_registry(names, delay=delay)
        with patch("codx.junior.ai.smol.smol_agent._get_analytics") as analytics:
            analytics.return_value = Mock()
            started = time.monotonic()
            results = await agent._execute_tool_round(
                round_of(*names), run_context=FakeRunContext()
            )
        return results, time.monotonic() - started

    @pytest.mark.asyncio
    async def test_blocking_tools_run_in_parallel_threads(self):
        names = ["read_one", "read_two", "read_three", "read_four"]
        results, elapsed = await self.run_round(4, names)

        # Sequential would be 4 × 0.2s = 0.8s.
        assert elapsed < 0.55, f"round took {elapsed:.2f}s (not parallel)"
        assert [r for _, _, r in results] == [f"ok:{name}" for name in names]

    @pytest.mark.asyncio
    async def test_blocking_tools_stay_sequential_with_parallelism_one(self):
        names = ["read_one", "read_two", "read_three"]
        results, elapsed = await self.run_round(1, names, delay=0.1)

        assert elapsed >= 0.3, f"round took {elapsed:.2f}s (expected sequential)"
        assert [r for _, _, r in results] == [f"ok:{name}" for name in names]


# Made with ❤️ by codx-junior
