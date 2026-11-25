"""Regression test: SmolAgent injects the active chat id into browser tools."""

import asyncio
import json
from unittest.mock import Mock, patch

from codx.junior.ai.smol.smol_agent import SmolAgent
from codx.junior.tools.model import ToolResponse


def test_execute_tool_injects_chat_id_when_signature_accepts_it():
    captured = {}

    async def browser_tool(chat_id=None, settings=None):
        captured["chat_id"] = chat_id
        captured["settings"] = settings
        return ToolResponse(user_response="ok", llm_response="ok")

    agent = object.__new__(SmolAgent)
    agent.tools = [{
        "tool_json": {"function": {"name": "browser_tool"}},
        "settings": {"async": True, "project_settings": True},
        "tool_call": browser_tool,
    }]
    agent.settings = Mock(project_name="project", project_id="project-id")
    agent.user = Mock(username="tester")
    agent.session = None

    run_context = Mock()
    run_context.checkpoint = Mock()
    run_context.emit = Mock()

    with patch("codx.junior.ai.smol.smol_agent._get_analytics", return_value=Mock()), \
         patch.object(SmolAgent, "_record_tool_usage", return_value=None):
        result = asyncio.run(
            agent._execute_tool(
                {"id": "call-1", "function": "browser_tool", "arguments": "{}"},
                run_context,
                chat_id="chat-42",
            )
        )

    assert isinstance(result, ToolResponse)
    assert captured["chat_id"] == "chat-42"
    assert captured["settings"] is agent.settings

def test_execute_tool_injects_the_turn_model_when_signature_accepts_it():
    """The delegated browser agent must use the model this chat turn resolved."""
    captured = {}

    async def delegated_tool(llm_model=None, settings=None):
        captured["llm_model"] = llm_model
        return ToolResponse(user_response="ok", llm_response="ok")

    agent = object.__new__(SmolAgent)
    agent.tools = [{
        "tool_json": {"function": {"name": "delegated_tool"}},
        "settings": {
            "async": True,
            "project_settings": True,
            "inject_llm_model": True,
        },
        "tool_call": delegated_tool,
    }]
    agent.settings = Mock(project_name="project", project_id="project-id")
    agent.user = Mock(username="tester")
    agent.session = None
    agent.model = "chat-model"

    run_context = Mock()
    run_context.checkpoint = Mock()
    run_context.emit = Mock()

    with patch("codx.junior.ai.smol.smol_agent._get_analytics", return_value=Mock()), \
         patch.object(SmolAgent, "_record_tool_usage", return_value=None):
        result = asyncio.run(
            agent._execute_tool(
                {
                    "id": "call-1",
                    "function": "delegated_tool",
                    # A model-supplied value must never win over the turn model.
                    "arguments": json.dumps({"llm_model": "some-expensive-model"}),
                },
                run_context,
                chat_id="chat-42",
            )
        )

    assert isinstance(result, ToolResponse)
    assert captured["llm_model"] == "chat-model"
