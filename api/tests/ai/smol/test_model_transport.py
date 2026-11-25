"""Transport-level regression tests for SmolAgent.

These cover the latency fixes that are independent from the prompt content:

* reading a provider stream must NOT block the event loop (the sync OpenAI
  client used to freeze the chat loop for the whole response),
* the client must carry explicit transport limits (a silent stream used to
  keep a turn alive for the SDK's ten-minute default),
* oversized tool results must be pruned before they are replayed on every
  model round,
* the configured DeepSeek reasoning effort/thinking switch must reach the
  request (the Harness exposes both; codx-junior ignored them).
"""

import asyncio
import time
from unittest.mock import Mock, patch

import httpx
import pytest

from codx.junior.ai.smol.effort import agile_policy, default_policy, effort_for, max_policy
from codx.junior.ai.smol.messages import (
    make_tool_message,
    prune_tool_result_for_model,
)
from codx.junior.ai.smol.smol_agent import SmolAgent
from codx.junior.global_settings import get_model_settings
from codx.junior.model.ai_model import AILLMModelSettings
from codx.junior.model.model import AIModel, AIProvider, GlobalSettings


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def make_chunk(content: str = None, reasoning: str = None) -> Mock:
    """Create a mock streaming chunk (only the fields the agent reads)."""
    chunk = Mock()
    chunk.usage = None
    chunk.choices = []
    choice = Mock()
    choice.delta = Mock()
    choice.delta.content = content
    choice.delta.tool_calls = None
    choice.finish_reason = "stop"
    if reasoning is None:
        del choice.delta.reasoning_content
    else:
        choice.delta.reasoning_content = reasoning
    chunk.choices.append(choice)
    return chunk


class SlowStream:
    """Sync iterator whose reads block like a provider that is thinking."""

    def __init__(self, chunks, delay: float):
        self.chunks = chunks
        self.delay = delay

    def __iter__(self):
        for chunk in self.chunks:
            time.sleep(self.delay)
            yield chunk


@pytest.fixture
def settings():
    settings = Mock()
    settings.project_id = "test-project-123"
    settings.project_name = "Test Project"
    settings.get_llm_settings.return_value = Mock(
        model="deepseek-flash",
        api_key="",
        api_url="https://api.deepseek.com/v1",
        system="You are a helpful assistant.",
        provider="deepseek",
        temperature=0.0,
        input_k_tokens_cxjcoins=0.1,
        output_k_tokens_cxjcoins=0.2,
        max_tool_calls=10,
        max_iterations=10,
        tool_timeout_seconds=30,
        tool_parallelism=8,
        reasoning_effort=None,
        thinking=None,
    )
    return settings


@pytest.fixture
def user():
    user = Mock()
    user.username = "test_user"
    user.api_key = None
    return user


@pytest.fixture
def agent(settings, user):
    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as openai_class:
        openai_class.return_value = Mock()
        instance = SmolAgent(settings=settings, user=user)
    instance.tools = []
    return instance


# ──────────────────────────────────────────────────────────────────────────────
# NON-BLOCKING STREAM
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_reads_do_not_block_the_event_loop(agent):
    """A slow provider read must leave the loop free for other work."""
    agent.client.chat.completions.create.return_value = SlowStream(
        [make_chunk(content=f"t{i}") for i in range(8)], delay=0.05
    )

    from engine.agent_runtime import AgentRunContext

    run_context = AgentRunContext("test-project-123")
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    try:
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics", return_value=Mock()
        ):
            content, tool_calls, _usage, reasoning = await agent._stream_completion(
                kwargs={"model": "deepseek-flash", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
                cancellation_token=None,
                send_callback=lambda text, done: None,
                run_context=run_context,
                request_id="req-1",
                chat_id="chat-1",
            )
    finally:
        stop = True
        await ticker_task

    assert content == "t0t1t2t3t4t5t6t7"
    assert tool_calls == {}
    assert reasoning == ""
    # A blocking implementation would have produced ~0 ticks: the whole loop
    # was inside ``next(stream)`` for the entire test.
    assert ticks > 10


@pytest.mark.asyncio
async def test_async_stream_object_is_supported(agent):
    """An AsyncOpenAI-style stream can be used without changing the caller."""

    async def chunks():
        yield make_chunk(reasoning="thinking")
        yield make_chunk(content="hello")

    agent.client.chat.completions.create.return_value = chunks()

    from engine.agent_runtime import AgentRunContext

    with patch(
        "codx.junior.ai.smol.smol_agent._get_analytics", return_value=Mock()
    ):
        content, _, _, reasoning = await agent._stream_completion(
            kwargs={"model": "deepseek-flash", "stream": True},
            openai_messages=[{"role": "user", "content": "hi"}],
            headers={},
            cancellation_token=None,
            send_callback=lambda text, done: None,
            run_context=AgentRunContext("test-project-123"),
        )

    assert content == "hello"
    assert reasoning == "thinking"


# ──────────────────────────────────────────────────────────────────────────────
# TRANSPORT LIMITS
# ──────────────────────────────────────────────────────────────────────────────


def test_client_has_explicit_timeouts_and_retries(settings, user, monkeypatch):
    for name in (
        "CODX_JUNIOR_LLM_CONNECT_TIMEOUT_SECONDS",
        "CODX_JUNIOR_LLM_READ_TIMEOUT_SECONDS",
        "CODX_JUNIOR_LLM_WRITE_TIMEOUT_SECONDS",
        "CODX_JUNIOR_LLM_POOL_TIMEOUT_SECONDS",
        "CODX_JUNIOR_LLM_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)

    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as openai_class:
        SmolAgent(settings=settings, user=user)

    kwargs = openai_class.call_args.kwargs
    timeout = kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 300.0
    assert timeout.connect == 15.0
    assert kwargs["max_retries"] == 1


def test_timeout_is_configurable(settings, user, monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_LLM_READ_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("CODX_JUNIOR_LLM_MAX_RETRIES", "0")

    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as openai_class:
        SmolAgent(settings=settings, user=user)

    kwargs = openai_class.call_args.kwargs
    assert kwargs["timeout"].read == 42.0
    assert kwargs["max_retries"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# TOOL RESULT PRUNING
# ──────────────────────────────────────────────────────────────────────────────


def test_small_tool_result_is_unchanged():
    assert prune_tool_result_for_model("short output") == "short output"


def test_large_tool_result_keeps_head_and_tail():
    result = "HEAD" + ("x" * 20000) + "TAIL"
    pruned = prune_tool_result_for_model(result)
    assert len(pruned) <= 8192
    assert pruned.startswith("HEAD")
    assert pruned.endswith("TAIL")
    assert "[... tool result middle pruned ...]" in pruned


def test_tool_message_uses_the_pruned_result():
    message = make_tool_message("call-1", "y" * 20000)
    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call-1"
    assert len(message["content"]) <= 8192


def test_pruning_budget_can_be_configured(monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_TOOL_RESULT_MAX_CHARS", "512")
    pruned = prune_tool_result_for_model("z" * 5000)
    assert len(pruned) <= 512


# ──────────────────────────────────────────────────────────────────────────────
# REASONING CONTROLS
# ──────────────────────────────────────────────────────────────────────────────


def test_reasoning_effort_and_thinking_reach_deepseek(agent):
    agent.llm_settings.reasoning_effort = "low"
    kwargs = agent._build_request_kwargs([])
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}


def test_reasoning_off_disables_thinking(agent):
    agent.llm_settings.reasoning_effort = "off"
    kwargs = agent._build_request_kwargs([])
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_thinking_disabled_wins_over_effort(agent):
    agent.llm_settings.reasoning_effort = "high"
    agent.llm_settings.thinking = "disabled"
    kwargs = agent._build_request_kwargs([])
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_no_reasoning_fields_when_not_configured(agent):
    kwargs = agent._build_request_kwargs([])
    assert "reasoning_effort" not in kwargs
    assert "extra_body" not in kwargs

def test_effort_tier_supplies_reasoning_effort(agent):
    """codx-junior -> high, codx-junior-max -> max (DSH parity)."""
    agile = agent._build_request_kwargs([], effort_policy=agile_policy())
    assert agile["reasoning_effort"] == "high"
    assert agile["extra_body"]["thinking"] == {"type": "enabled"}

    maximum = agent._build_request_kwargs([], effort_policy=max_policy())
    assert maximum["reasoning_effort"] == "max"
    assert maximum["extra_body"]["thinking"] == {"type": "enabled"}


def _settings_with_effort(model_effort=None, provider_effort=None):
    return GlobalSettings(
        ai_providers=[
            AIProvider(
                name="Deepseek",
                provider="openai",
                api_url="https://api.deepseek.com/v1",
                api_key="x",
                reasoning_effort=provider_effort,
            )
        ],
        ai_models=[
            AIModel(
                name="deepseek-flash",
                ai_provider="Deepseek",
                ai_model="deepseek-flash",
                settings=AILLMModelSettings(reasoning_effort=model_effort),
            )
        ],
    )


def test_reasoning_resolution_model_wins_over_provider():
    settings = get_model_settings(
        "deepseek-flash",
        global_settings=_settings_with_effort(
            model_effort="low", provider_effort="high"
        ),
    )
    assert settings.reasoning_effort == "low"


def test_reasoning_resolution_provider_fallback():
    settings = get_model_settings(
        "deepseek-flash",
        global_settings=_settings_with_effort(provider_effort="high"),
    )
    assert settings.reasoning_effort == "high"


def test_reasoning_resolution_environment_fallback(monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_REASONING_EFFORT", "max")
    settings = get_model_settings(
        "deepseek-flash", global_settings=_settings_with_effort()
    )
    assert settings.reasoning_effort == "max"


def test_reasoning_effort_is_not_sent_to_non_deepseek(agent):
    agent.base_url = "https://api.openai.com/v1"
    agent.model = "gpt-4"
    agent.llm_settings.provider = "openai"
    agent.llm_settings.reasoning_effort = "low"
    kwargs = agent._build_request_kwargs([])
    assert kwargs["reasoning_effort"] == "low"
    assert "extra_body" not in kwargs


# ──────────────────────────────────────────────────────────────────────────────
# LOOP LIMITS COME FROM THE EFFORT POLICY
# ──────────────────────────────────────────────────────────────────────────────


def test_loop_limits_come_from_the_effort_policy(agent):
    assert agent._resolve_loop_limits(default_policy()) == (10, 5)
    assert agent._resolve_loop_limits(agile_policy()) == (30, 8)
    assert agent._resolve_loop_limits(max_policy()) == (80, 12)


def test_profile_effort_overrides_the_tier_defaults(agent):
    policy = effort_for([], settings={"max_iterations": 50, "max_tool_calls": 7})
    assert agent._resolve_loop_limits(policy) == (50, 7)


def test_effort_header_is_parsed(agent):
    settings = agent._resolve_effort_settings(
        {}, {"effort": '{"max_iterations": 50, "max_tool_calls": 7}'}
    )
    assert settings == {"max_iterations": 50, "max_tool_calls": 7}


def test_effort_config_dict_is_accepted(agent):
    settings = agent._resolve_effort_settings(
        {"effort": {"continutations": 0, "max_iterations": 33}}, {}
    )
    assert settings["max_iterations"] == 33


def test_invalid_effort_header_is_ignored(agent):
    assert agent._resolve_effort_settings({}, {"effort": "not-json"}) == {}


# ──────────────────────────────────────────────────────────────────────────────
# EFFORT-STOP NOTICE
# ──────────────────────────────────────────────────────────────────────────────


def test_loop_limit_notice_explains_how_to_raise_effort(agent):
    notice = agent._build_loop_limit_notice(
        "Maximum tool rounds (80) exceeded.",
        default_policy(),
        max_iterations=80,
    )
    assert "Turn paused by the effort budget" in notice
    assert "Maximum tool rounds (80) exceeded." in notice
    assert "80 model rounds" in notice
    assert "@codx-junior-max" in notice


def test_loop_limit_notice_for_agile_tier(agent):
    notice = agent._build_loop_limit_notice(
        "Maximum tool rounds (10) exceeded.",
        agile_policy(),
    )
    assert "continuations" in notice
    assert "@codx-junior-max" in notice
    assert "effort.max_iterations" in notice


def test_loop_limit_notice_for_max_tier(agent):
    notice = agent._build_loop_limit_notice(
        "Maximum tool rounds (24) exceeded.",
        max_policy(),
    )
    assert "continuations" in notice
    assert "Split the task" in notice
    assert "effort.max_iterations" in notice


