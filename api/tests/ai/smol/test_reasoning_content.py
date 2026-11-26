"""
Tests for the DeepSeek thinking-mode `reasoning_content` handling.

DeepSeek returns the chain of thought in ``reasoning_content`` and, for
requests carrying the ``tools`` parameter, REQUIRES it to be echoed back. When
it is missing the continuation fails with::

    400 — The `reasoning_content` in the thinking mode must be passed back to the API

which used to kill whole chat turns. These tests cover the four pieces of the
fix:

  • the stream accumulator captures ``reasoning_content`` deltas,
  • the assistant tool-call message echoes it back verbatim,
  • a chain of thought stored on a history message survives the conversion to
    the OpenAI payload (``to_openai_message``) instead of being stripped,
  • a provider that still rejects the request is retried once with the
    thinking mode disabled instead of failing the turn.
"""
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

from codx.junior.ai.smol.messages import (
    make_assistant_tool_calls_message,
    to_openai_message,
    to_openai_messages,
)
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
        api_key="sk-test",
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
        """Every retry failing surfaces that error (no infinite loop)."""
        agent.client.chat.completions.create.side_effect = [
            FakeAPIError("reasoning_content must be passed back"),
            FakeAPIError("reasoning_content must be passed back"),
            FakeAPIError("reasoning_content must be passed back"),
        ]

        with pytest.raises(FakeAPIError):
            agent._create_completion(
                kwargs={"model": "deepseek-flash", "stream": True},
                openai_messages=[{"role": "user", "content": "hi"}],
                headers={},
            )

        assert agent.client.chat.completions.create.call_count == 3


# ──────────────────────────────────────────────
# PREFLIGHT PAYLOAD NORMALISATION
# ──────────────────────────────────────────────


class TestThinkingPayloadPreparation:
    """A missing CoT is closed before sending, so the provider never rejects."""

    def test_missing_reasoning_is_filled(self, agent):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "get_date", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "2026-11-15"},
            {"role": "user", "content": "thanks"},
        ]

        filled = agent._prepare_thinking_messages(
            messages, {"tools": [{"type": "function"}]}
        )

        assert filled == 1
        assert messages[1]["reasoning_content"] == ""

    def test_no_tools_leaves_the_payload_untouched(self, agent):
        """Without the tools parameter the provider ignores the field."""
        messages = [{"role": "assistant", "content": "hello"}]

        assert agent._prepare_thinking_messages(messages, {}) == 0
        assert "reasoning_content" not in messages[0]

    def test_openai_provider_is_untouched(self, agent):
        """Strict OpenAI endpoints reject unknown message fields."""
        agent.llm_settings = Mock(provider="openai", model="gpt-4")
        agent.model = "gpt-4"
        # The endpoint is part of the evidence: an instance built for DeepSeek
        # that merely changes its provider/model fields is still a DeepSeek
        # endpoint. A real OpenAI turn has an OpenAI base_url.
        agent.base_url = "https://api.openai.com/v1"
        messages = [{"role": "assistant", "content": "hello"}]

        filled = agent._prepare_thinking_messages(
            messages, {"tools": [{"type": "function"}]}
        )

        assert filled == 0
        assert "reasoning_content" not in messages[0]

    def test_empty_assistant_message_is_dropped(self, agent):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None},
        ]

        agent._prepare_thinking_messages(messages, {"tools": [{"type": "function"}]})

        assert messages == [{"role": "user", "content": "hi"}]

    def test_thinking_disabled_is_sticky(self, agent):
        """A degraded round must not re-enable thinking on the next round."""
        stream = [make_chunk(content="ok", finish_reason="stop")]
        agent.client.chat.completions.create.side_effect = [
            FakeAPIError("reasoning_content must be passed back"),
            stream,
            stream,
        ]

        agent._create_completion(
            kwargs={"model": "deepseek-flash", "stream": True},
            openai_messages=[{"role": "user", "content": "hi"}],
            headers={},
        )
        # Second round of the same turn: thinking is already disabled.
        agent._create_completion(
            kwargs={"model": "deepseek-flash", "stream": True,
                    "reasoning_effort": "high"},
            openai_messages=[{"role": "user", "content": "hi"}],
            headers={},
        )

        last_kwargs = agent.client.chat.completions.create.call_args.kwargs
        assert last_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in last_kwargs


# ──────────────────────────────────────────────────────────────────────────────
# HISTORY REPLAY
# ──────────────────────────────────────────────────────────────────────────────


class TestHistoryReasoningReplay:
    """A stored CoT must survive the conversion of the chat history."""

    def test_reasoning_content_survives_the_conversion(self):
        message = to_openai_message(
            {
                "role": "assistant",
                "content": "It is 2026-11-15.",
                "reasoning_content": "The tool answered, I can reply now.",
            }
        )

        assert message["reasoning_content"] == "The tool answered, I can reply now."
        assert message["content"] == "It is 2026-11-15."

    def test_key_is_absent_when_nothing_was_stored(self):
        """Strict OpenAI-compatible endpoints reject unknown message fields."""
        message = to_openai_message({"role": "assistant", "content": "hello"})

        assert "reasoning_content" not in message

    def test_blank_reasoning_is_not_sent(self):
        message = to_openai_message(
            {"role": "assistant", "content": "hello", "reasoning_content": "  "}
        )

        assert "reasoning_content" not in message

    def test_history_keeps_the_cot_in_the_payload(self):
        messages = to_openai_messages(
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "a real chain of thought",
                },
            ],
            system="be nice",
        )

        assert messages[0]["role"] == "system"
        assert messages[2]["reasoning_content"] == "a real chain of thought"


class TestThinkingDialectDetection:
    """The DeepSeek endpoint counts, even behind a generic provider name."""

    def test_deepseek_model_enables_the_dialect(self, agent):
        agent.llm_settings = Mock(provider="custom")
        agent.model = "deepseek-flash"
        agent.base_url = "https://my-gateway.example.com/v1"

        assert agent._uses_thinking_dialect([{"role": "user", "content": "hi"}])

    def test_deepseek_endpoint_enables_the_dialect(self, agent):
        """A gateway in front of DeepSeek keeps the same contract."""
        agent.llm_settings = Mock(provider="custom")
        agent.model = "flash"
        agent.base_url = "https://api.deepseek.com/v1"

        assert agent._uses_thinking_dialect([{"role": "user", "content": "hi"}])

    def test_other_providers_are_left_alone(self, agent):
        agent.llm_settings = Mock(provider="openai")
        agent.model = "gpt-4.1-mini"
        agent.base_url = "https://api.openai.com/v1"

        assert not agent._uses_thinking_dialect([{"role": "user", "content": "hi"}])


class TestStoredReasoningReplay:
    """The CoT of a persisted turn is replayed verbatim, never fabricated."""

    def test_real_cot_is_kept_for_deepseek(self, agent):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "done",
             "reasoning_content": "the real chain of thought"},
        ]

        filled = agent._prepare_thinking_messages(
            messages, {"tools": [{"type": "function"}]}
        )

        assert filled == 0
        assert messages[1]["reasoning_content"] == "the real chain of thought"

    def test_foreign_cot_is_stripped_for_other_providers(self, agent):
        """A chat re-run with another model must not leak the field (400)."""
        agent.llm_settings = Mock(provider="openai")
        agent.model = "gpt-4.1-mini"
        agent.base_url = "https://api.openai.com/v1"
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "done",
             "reasoning_content": "a deepseek chain of thought"},
        ]

        agent._prepare_thinking_messages(messages, {"tools": [{"type": "function"}]})

        assert "reasoning_content" not in messages[1]

    def test_payload_without_tools_never_carries_the_field(self, agent):
        messages = [
            {"role": "assistant", "content": "done", "reasoning_content": "a cot"},
        ]

        agent._prepare_thinking_messages(messages, {})

        assert "reasoning_content" not in messages[0]

    def test_db_message_replays_its_cot(self):
        from codx.junior.chat.chat_prompt_builder import ChatPromptBuilder
        from codx.junior.db import Message as DbMessage

        stored = DbMessage(
            role="assistant",
            content="It is 2026-11-15.",
            reasoning_content="a stored chain of thought",
        )

        payload = to_openai_message(ChatPromptBuilder.convert_message(stored))

        assert payload["reasoning_content"] == "a stored chain of thought"
        assert payload["content"] == "It is 2026-11-15."

    def test_db_message_without_cot_adds_no_key(self):
        from codx.junior.chat.chat_prompt_builder import ChatPromptBuilder
        from codx.junior.db import Message as DbMessage

        stored = DbMessage(role="assistant", content="hello")

        payload = to_openai_message(ChatPromptBuilder.convert_message(stored))

        assert "reasoning_content" not in payload
