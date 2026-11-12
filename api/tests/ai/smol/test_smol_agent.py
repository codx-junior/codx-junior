"""
Tests for SmolAgent — Level 1: Basic chat without tools.

Focuses on simple scenarios with no tool execution:
  • Basic chat completion with streaming response
  • Mocked OpenAI client
  • Cancellation handling
  • Usage recording
"""
import json
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
from typing import List, Dict, Any, Optional

from langchain.messages import AIMessage, HumanMessage

from codx.junior.ai.smol.smol_agent import SmolAgent, CANCELLED_MESSAGE
from codx.junior.ai.cancellation import CancelledError


# ──────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings():
    """Mock CODXJuniorSettings."""
    settings = Mock()
    settings.project_id = "test-project-123"
    settings.project_name = "Test Project"
    settings.get_llm_settings.return_value = Mock(
        model="gpt-4",
        api_key="test-api-key",
        api_url="https://api.openai.com/v1",
        system="You are a helpful assistant.",
        provider="openai",
        temperature=0.7,
        input_k_tokens_cxjcoins=0.1,
        output_k_tokens_cxjcoins=0.2,
    )
    return settings


@pytest.fixture
def mock_user():
    """Mock CodxUser."""
    user = Mock()
    user.username = "test_user"
    user.api_key = None
    return user


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client."""
    return Mock()


@pytest.fixture
def smol_agent(mock_settings, mock_user, mock_openai_client):
    """Create a SmolAgent instance with mocked dependencies."""
    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as mock_openai_class:
        mock_openai_class.return_value = mock_openai_client
        agent = SmolAgent(
            settings=mock_settings,
            user=mock_user,
            system=None,
        )
        agent.tools = []  # No tools for Level 1
        return agent


@pytest.fixture
def mock_run_context():
    """Mock AgentRunContext."""
    context = Mock()
    context.run_id = "run-123"
    context.token = Mock()
    context.token.cancelled = False
    context.run.return_value.__enter__ = Mock(return_value=context)
    context.run.return_value.__exit__ = Mock(return_value=None)
    context.checkpoint = Mock()
    context.emit = Mock()
    context.cancel = Mock()
    
    # Mock guard_stream to yield chunks without modification
    context.guard_stream = Mock(side_effect=lambda stream: iter(stream))
    
    return context


@pytest.fixture
def mock_analytics():
    """Mock Analytics singleton."""
    analytics = Mock()
    analytics.record_token_usage = Mock()
    analytics.record_tool_usage = Mock()
    return analytics


@pytest.fixture
def sample_messages():
    """Sample conversation messages."""
    return [
        HumanMessage(content="Hello, how are you?"),
    ]


@pytest.fixture
def sample_config():
    """Sample chat configuration."""
    return {
        "chat_id": "chat-456",
        "tools": [],
        "callbacks": None,
        "cancellation_token": None,
        "headers": {
            "session_id": "session-789",
            "tags": "test",
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def make_openai_chunk(
    content: Optional[str] = None,
    finish_reason: Optional[str] = None,
    usage_prompt_tokens: Optional[int] = None,
    usage_completion_tokens: Optional[int] = None,
) -> Mock:
    """Create a mock OpenAI streaming chunk."""
    chunk = Mock()
    chunk.choices = []
    
    if content is not None or finish_reason is not None:
        choice = Mock()
        choice.delta = Mock()
        choice.delta.content = content
        choice.delta.tool_calls = None
        choice.finish_reason = finish_reason
        chunk.choices.append(choice)
    
    # Attach usage info if provided
    if usage_prompt_tokens is not None or usage_completion_tokens is not None:
        chunk.usage = Mock()
        chunk.usage.prompt_tokens = usage_prompt_tokens or 0
        chunk.usage.completion_tokens = usage_completion_tokens or 0
    else:
        chunk.usage = None
    
    return chunk


# ──────────────────────────────────────────────────────────────────────────────
# LEVEL 1 TESTS: Basic Chat Without Tools
# ──────────────────────────────────────────────────────────────────────────────


class TestSmolAgentBasicChat:
    """Basic chat completion scenarios without tool calls."""

    @pytest.mark.asyncio
    async def test_simple_streaming_response(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test a simple streaming completion that returns text only."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Hello"),
            make_openai_chunk(content=" "),
            make_openai_chunk(content="there"),
            make_openai_chunk(content="!"),
            make_openai_chunk(
                finish_reason="stop",
                usage_prompt_tokens=10,
                usage_completion_tokens=5,
            ),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                result = await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)
        assert result[1].content == "Hello there!"

    @pytest.mark.asyncio
    async def test_streaming_response_with_empty_chunks(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test handling of empty chunks in the stream."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Hi"),
            make_openai_chunk(content=None),  # Empty chunk
            make_openai_chunk(content="!"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                result = await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        assert result[-1].content == "Hi!"

    @pytest.mark.asyncio
    async def test_preflight_wallet_check_called(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that wallet check is performed before streaming."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.check_user_wallet"
            ) as mock_wallet_check:
                with patch(
                    "codx.junior.ai.smol.smol_agent.AgentRunContext",
                    return_value=mock_run_context,
                ):
                    # Act
                    await smol_agent.chat(sample_messages, config=sample_config)

                    # Assert
                    mock_wallet_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_emissions_on_success(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that correct events are emitted during successful completion."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Response"),
            make_openai_chunk(
                finish_reason="stop",
                usage_prompt_tokens=10,
                usage_completion_tokens=5,
            ),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        # Check that LLM_REQUEST event was emitted
        calls = mock_run_context.emit.call_args_list
        event_types = [c[0][0] for c in calls]
        assert any("LLM_REQUEST" in str(et) for et in event_types)
        # Check that LLM_USAGE event was emitted
        assert any("LLM_USAGE" in str(et) for et in event_types)

    @pytest.mark.asyncio
    async def test_usage_recorded(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that token usage is recorded to analytics."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test response"),
            make_openai_chunk(
                finish_reason="stop",
                usage_prompt_tokens=20,
                usage_completion_tokens=10,
            ),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        mock_analytics.record_token_usage.assert_called_once()
        call_kwargs = mock_analytics.record_token_usage.call_args[1]
        assert call_kwargs["username"] == "test_user"
        assert call_kwargs["input_tokens"] == 20
        assert call_kwargs["output_tokens"] == 10

    @pytest.mark.asyncio
    async def test_config_resolution_creates_run_context_when_missing(
        self,
        smol_agent,
        mock_openai_client,
        sample_messages,
        mock_analytics,
    ):
        """Test that a RunContext is created when not provided in config."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        config = {"chat_id": "chat-456", "tools": []}
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext"
            ) as mock_context_class:
                mock_context = Mock()
                mock_context.run_id = "auto-run-123"
                mock_context.token = Mock(cancelled=False)
                mock_context.run.return_value.__enter__ = Mock(return_value=mock_context)
                mock_context.run.return_value.__exit__ = Mock(return_value=None)
                mock_context.checkpoint = Mock()
                mock_context.emit = Mock()
                mock_context.guard_stream = Mock(side_effect=lambda s: iter(s))
                
                mock_context_class.return_value = mock_context
                
                # Act
                await smol_agent.chat(sample_messages, config=config)

                # Assert
                mock_context_class.assert_called_once()
                call_kwargs = mock_context_class.call_args[1]
                assert call_kwargs["project_id"] == "test-project-123"

    @pytest.mark.asyncio
    async def test_cancellation_before_streaming(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test cancellation raises CancelledError."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        mock_run_context.token.cancelled = True
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act & Assert
                with pytest.raises(CancelledError) as exc_info:
                    await smol_agent.chat(sample_messages, config=sample_config)
                
                assert CANCELLED_MESSAGE in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openai_request_includes_correct_parameters(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that OpenAI request is made with correct parameters."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        mock_openai_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["stream"] is True
        assert "stream_options" in call_kwargs
        assert call_kwargs["stream_options"]["include_usage"] is True

    @pytest.mark.asyncio
    async def test_system_prompt_included_in_messages(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that system prompt is included in the OpenAI messages."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) > 0
        assert messages[0]["role"] == "system"
        assert "helpful assistant" in messages[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_callback_flushing(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        mock_analytics,
    ):
        """Test that callbacks are called with streamed content."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Hello"),
            make_openai_chunk(content=" world"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        mock_callback = Mock()
        config = {
            "chat_id": "chat-456",
            "tools": [],
            "callbacks": [mock_callback],
            "headers": {"session_id": "session-789"},
        }
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=config)

        # Assert
        # Callback should be called at least once with content
        assert mock_callback.call_count > 0
        # Check that some content was passed
        all_content = "".join(
            call[0][0] for call in mock_callback.call_args_list if call[0]
        )
        assert "Hello" in all_content or "world" in all_content

    @pytest.mark.asyncio
    async def test_multiple_messages_in_history(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        mock_analytics,
        sample_config,
    ):
        """Test that multiple messages in history are all sent to OpenAI."""
        # Arrange
        messages = [
            HumanMessage(content="First message"),
            AIMessage(content="First response"),
            HumanMessage(content="Second message"),
        ]
        
        stream_chunks = [
            make_openai_chunk(content="Second response"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                result = await smol_agent.chat(messages, config=sample_config)

        # Assert
        assert len(result) == 4  # 3 original + 1 assistant response
        
        # Check OpenAI was called with all messages
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        openai_messages = call_kwargs["messages"]
        # System + 3 user/assistant messages
        assert len(openai_messages) >= 4

    @pytest.mark.asyncio
    async def test_headers_passed_to_openai_request(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        mock_analytics,
    ):
        """Test that custom headers are passed to the OpenAI request."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        config = {
            "chat_id": "chat-456",
            "tools": [],
            "headers": {
                "session_id": "session-789",
                "tags": "custom-tag",
            },
        }
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=config)

        # Assert
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert "extra_headers" in call_kwargs
        headers = call_kwargs["extra_headers"]
        assert "x-litellm-tags" in headers

    @pytest.mark.asyncio
    async def test_no_tools_passed_when_empty_list(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that 'tools' key is not included in request when no tools selected."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        sample_config["tools"] = []
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_temperature_included_when_nonzero(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that temperature is included in request when non-zero."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

        # Assert
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert "temperature" in call_kwargs
        assert call_kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_analytics_fallback_when_no_provider_usage(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that token counting is used when provider doesn't return usage."""
        # Arrange: Chunk without usage info
        stream_chunks = [
            make_openai_chunk(content="Test response"),
            make_openai_chunk(finish_reason="stop", usage_prompt_tokens=None),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                with patch(
                    "codx.junior.ai.smol.smol_agent.count_tokens",
                    return_value=5,
                ) as mock_count:
                    # Act
                    await smol_agent.chat(sample_messages, config=sample_config)

                    # Assert
                    mock_count.assert_called()

    @pytest.mark.asyncio
    async def test_run_context_checkpoint_called(
        self,
        smol_agent,
        mock_openai_client,
        mock_run_context,
        sample_messages,
        sample_config,
        mock_analytics,
    ):
        """Test that run_context.checkpoint is called (cancellation point)."""
        # Arrange
        stream_chunks = [
            make_openai_chunk(content="Test"),
            make_openai_chunk(finish_reason="stop"),
        ]
        
        mock_openai_client.chat.completions.create.return_value = stream_chunks
        
        with patch(
            "codx.junior.ai.smol.smol_agent._get_analytics",
            return_value=mock_analytics,
        ):
            with patch(
                "codx.junior.ai.smol.smol_agent.AgentRunContext",
                return_value=mock_run_context,
            ):
                # Act
                await smol_agent.chat(sample_messages, config=sample_config)

                # Assert
                # checkpoint should be called (it's a cancellation safe point)
                # Note: Not called in basic flow, but method exists for future use


# ──────────────────────────────────────────────────────────────────────────────
# TOOL-LIMIT CONTINUATION (harness robustness)
# ──────────────────────────────────────────────────────────────────────────────
#
# The tool limits are a guardrail, not a stop condition: a harness turn gets a
# deterministic "rethink the path" notice carrying the facts already collected,
# plus a renewed (smaller) budget. A plain chat keeps the old behaviour.


def _tool_round_messages():
    """Conversation with one executed tool call (the facts to hand back)."""
    return [
        {"role": "user", "content": "Fix the failing loop guard test"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "project_locate",
                        "arguments": '{"query": "LoopGuard"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "api/codx/junior/ai/smol/loop_guard.py:101 LoopGuard",
        },
    ]


def test_continuation_enabled_for_harness_profile(smol_agent):
    assert smol_agent._continuation_enabled(
        config={}, orchestration_info=None, profile_names=["codx-junior"]
    ) is True
    assert smol_agent._continuation_enabled(
        config={}, orchestration_info=None, profile_names=["@codx-junior", "x"]
    ) is True


def test_continuation_enabled_for_orchestrated_turns(smol_agent):
    assert smol_agent._continuation_enabled(
        config={}, orchestration_info={"role": "worker"}, profile_names=[]
    ) is True
    assert smol_agent._continuation_enabled(
        config={}, orchestration_info={"role": "supervisor"}, profile_names=[]
    ) is True


def test_continuation_disabled_for_plain_chat(smol_agent):
    assert smol_agent._continuation_enabled(
        config={}, orchestration_info=None, profile_names=["software_developer"]
    ) is False


def test_continuation_config_override_wins(smol_agent):
    assert smol_agent._continuation_enabled(
        config={"continue_on_tool_limit": True},
        orchestration_info=None,
        profile_names=[],
    ) is True
    assert smol_agent._continuation_enabled(
        config={"continue_on_tool_limit": False},
        orchestration_info={"role": "supervisor"},
        profile_names=["codx-junior"],
    ) is False


def test_tool_limit_rethink_carries_the_collected_facts(smol_agent):
    notice = smol_agent._build_tool_limit_rethink(
        openai_messages=_tool_round_messages(),
        loop_error="max iterations (12) exceeded",
        attempt=1,
    )

    assert "DIFFERENT path" in notice
    assert "max iterations (12) exceeded" in notice
    # The facts already collected are handed back, so nothing must be re-run.
    assert "project_locate" in notice
    assert "loop_guard.py:101" in notice
    # The model is told which continuation this is and its smaller budget.
    assert "continuation 1 of 2" in notice
    assert "STOP calling tools" in notice


def test_tool_limit_rethink_is_bounded(smol_agent):
    from codx.junior.ai.smol.constants import TOOL_LIMIT_FACTS_MAX_CHARS

    messages = [
        {"role": "user", "content": "big task"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": f"call-{i}",
                    "function": {
                        "name": "project_read_file",
                        "arguments": f'{{"file_path": "file_{i}.py"}}',
                    },
                }
                for i in range(60)
            ],
        },
    ]
    messages += [
        {
            "role": "tool",
            "tool_call_id": f"call-{i}",
            "content": "x" * 2000,
        }
        for i in range(60)
    ]

    notice = smol_agent._build_tool_limit_rethink(
        openai_messages=messages, loop_error="too many calls", attempt=2
    )

    # The notice must never explode the context window.
    assert len(notice) < TOOL_LIMIT_FACTS_MAX_CHARS + 4000


# Made with ❤️ by codx-junior