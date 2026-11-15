"""
Tests for the DeepSeek thinking-mode `reasoning_content` handling.

DeepSeek returns the chain of thought in ``reasoning_content`` and, for
requests carrying the ``tools`` parameter, REQUIRES it to be echoed back. When
it is missing the continuation fails with::

    400 — The `reasoning_content` in the thinking mode must be passed back to the API

which used to kill whole chat turns. These tests cover the three pieces of the
fix:

  • the stream accumulator captures ``reasoning_content`` deltas,
  • the assistant tool-call message echoes it back verbatim,
  • a provider that still rejects the request is retried once with the
    thinking mode disabled instead of failing the turn.
"""
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

from codx.junior.ai.smol.messages import make_assistant_tool_calls_message
from codx.junior.ai.smol.smol_agent import SmolAgent


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def make_chunk(
    content: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    finish_reason: Optional[str] = None,
) -> Mock:
    """Create a mock OpenAI streaming chunk with an optional CoT delta."""
    chunk = Mock()
    chunk.usage = None
    chunk.choices = []

    choice = Mock()
    choice.delta = Mock()
    choice.delta.content = content
    choice.delta.tool_calls = None
    choice.finish_reason = finish_reason
    # Only expose reasoning_content when the test asks for it: a bare Mock
    # attribute would look like a truthy value.
    if reasoning_content is None:
        del choice.delta.reasoning_content
    else:
        choice.delta.reasoning_content = reasoning_content
    chunk.choices.append(choice)
    return chunk


class FakeAPIError(Exception):
    """Stand-in for openai.BadRequestError with a provider message."""


@pytest.fixture
def mock_settings():
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
    )
    return settings


@pytest.fixture
def mock_user():
    user = Mock()
    user.username = "test_user"
    user.api_key = None
    return user


@pytest.fixture
def mock_run_context():
    context = Mock()
    context.run_id = "run-123"
    context.checkpoint = Mock()
    context.emit = Mock()
    context.cancel = Mock()
    context.guard_stream = Mock(side_effect=lambda stream: iter(stream))
    return context


@pytest.fixture
def agent(mock_settings, mock_user):
    client = Mock()
    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as mock_openai_class:
        mock_openai_class.return_value = client
        instance = SmolAgent(settings=mock_settings, user=mock_user, system=None)
    instance.client = client
    instance.tools = []
    return instance


# ──────────────────────────────────────────────────────────────────────────────
# ASSISTANT MESSAGE
# ──────────────────────────────────────────────────────────────────────────────


class TestAssistantMessageReasoning:
    """The assistant tool-call message must echo the chain of thought."""

    def test_reasoning_content_is_echoed(self):
        message = make_assistant_tool_calls_message(
            {"call_1": {"function": "get_date", "arguments": "{}"}},
            content="",
            reasoning_content="I need the current date.",
        )

        assert message["reasoning_content"] == "I need the current date."
        assert message["role"] == "assistant"
        assert message["content"] is None
        assert message["tool_calls"][0]["id"] == "call_1"

    def test_no_key_when_no_reasoning(self):
        """OpenAI-style providers reject unknown fields: the key must be absent."""
        message = make_assistant_tool_calls_message(
            {"call_1": {"function": "get_date", "arguments": "{}"}},
            content="preamble",
        )

        assert "reasoning_content" not in message
        assert message["content"] == "preamble"

    def test_empty_reasoning_is_not_sent(self):
        message = make_assistant_tool_calls_message(
            {"call_1": {"function": "get_date", "arguments": "{}"}},
            reasoning_content="",
        )

        assert "reasoning_content" not in message


# ──────────────────────────────────────────────────────────────────────────────
# STREAM CAPTURE
# ──────────────────────────────────────────────────────────────────────────────


class TestReasoningCapture:
    """`reasoning_content` deltas are accumulated without leaking into content."""

    @pytest.mark.asyncio
    async def test_reasoning_deltas_are_collected(
        self, agent, mock_run_context
    ):
        stream = [
            make_chunk(reasoning_content="Let me think. "),
            make_chunk(reasoning_content="I should call the tool."),
            make_chunk(content="Calling it now."),
            make_chunk(finish_reason="stop"),
        ]
        agent.client.chat.completions.create.return_value = stream

        analytics = Mock()
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics", return_value=analytics
        ):
            content, tool_calls, usage, reasoning = await agent._stream_completion(
                kwargs={"model": "deepseek-flash", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
                cancellation_token=None,
                send_callback=lambda text, done: None,
                run_context=mock_run_context,
                request_id="req-1",
                chat_id="chat-1",
            )

        assert reasoning == "Let me think. I should call the tool."
        # The CoT must never be merged into the user-facing content.
        assert content == "Calling it now."
        assert tool_calls == {}

    @pytest.mark.asyncio
    async def test_provider_without_reasoning_returns_empty_string(
        self, agent, mock_run_context
    ):
        agent.client.chat.completions.create.return_value = [
            make_chunk(content="hello"),
            make_chunk(finish_reason="stop"),
        ]

        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics", return_value=Mock()
        ):
            content, _, _, reasoning = await agent._stream_completion(
                kwargs={"model": "gpt-4", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
                cancellation_token=None,
                send_callback=lambda text, done: None,
                run_context=mock_run_context,
            )

        assert content == "hello"
        assert reasoning == ""


# ──────────────────────────────────────────────────────────────────────────────
# SELF-HEALING RETRY
# ──────────────────────────────────────────────────────────────────────────────


class TestReasoningErrorRetry:
    """A provider that still rejects the request must not kill the turn."""

    def test_reasoning_error_is_detected(self):
        error = FakeAPIError(
            "Error code: 400 - {'error': {'message': 'The `reasoning_content` "
            "in the thinking mode must be passed back to the API.'}}"
        )

        assert SmolAgent._is_reasoning_content_error(error) is True

    def test_other_errors_are_not_matched(self):
        assert SmolAgent._is_reasoning_content_error(
            FakeAPIError("Error code: 400 - Failed to parse JSON: ")
        ) is False
        assert SmolAgent._is_reasoning_content_error(
            FakeAPIError("Connection error.")
        ) is False

    def test_retries_with_thinking_disabled(self, agent):
        stream = [make_chunk(content="ok", finish_reason="stop")]
        agent.client.chat.completions.create.side_effect = [
            FakeAPIError(
                "The `reasoning_content` in the thinking mode must be passed "
                "back to the API."
            ),
            stream,
        ]

        result = agent._create_completion(
            kwargs={"model": "deepseek-flash", "stream": True,
                    "reasoning_effort": "high"},
            openai_messages=[{"role": "user", "content": "hi"}],
            headers={},
            request_id="req-2",
            chat_id="chat-2",
        )

        assert result is stream
        assert agent.client.chat.completions.create.call_count == 2

        retry_kwargs = agent.client.chat.completions.create.call_args.kwargs
        assert retry_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        # thinking is disabled, so the effort hint no longer applies
        assert "reasoning_effort" not in retry_kwargs
        assert retry_kwargs["messages"] == [{"role": "user", "content": "hi"}]

    def test_unrelated_error_is_raised_untouched(self, agent):
        agent.client.chat.completions.create.side_effect = FakeAPIError(
            "Error code: 500 - internal"
        )

        with pytest.raises(FakeAPIError):
            agent._create_completion(
                kwargs={"model": "deepseek-flash", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
            )

        assert agent.client.chat.completions.create.call_count == 1

    def test_failed_retry_raises_the_retry_error(self, agent):
        """A retry that fails again surfaces that error (no infinite loop)."""
        agent.client.chat.completions.create.side_effect = [
            FakeAPIError("reasoning_content must be passed back"),
            FakeAPIError("reasoning_content must be passed back"),
        ]

        with pytest.raises(FakeAPIError):
            agent._create_completion(
                kwargs={"model": "deepseek-flash", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
            )

        assert agent.client.chat.completions.create.call_count == 2
