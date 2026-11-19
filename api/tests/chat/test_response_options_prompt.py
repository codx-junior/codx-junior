"""
The chat system prompt must teach every model the suggested-replies protocol
that the web UI renders as clickable option buttons.
"""
import inspect

from codx.junior.chat import chat_engine
from codx.junior.chat.chat_prompt_builder import (
    RESPONSE_OPTIONS_SYSTEM_PROMPT,
    strip_response_options,
)


def test_prompt_documents_the_options_block():
    prompt = RESPONSE_OPTIONS_SYSTEM_PROMPT

    # The exact fence language the client parser looks for.
    assert "```options" in prompt
    # The fields of a rich option.
    assert '"label"' in prompt
    assert '"value"' in prompt
    assert '"description"' in prompt
    assert '"style"' in prompt
    # Usage rules that keep the buttons meaningful.
    assert "END of the response" in prompt
    assert "type any other answer" in prompt


def test_chat_engine_appends_the_protocol_to_the_system_prompt():
    source = inspect.getsource(chat_engine.ChatEngine)
    assert "RESPONSE_OPTIONS_SYSTEM_PROMPT" in source
    # It must be appended after the conversation guardrail, i.e. as the most
    # salient instruction of the turn.
    assert source.index("CONVERSATION_GUARDRAIL_SYSTEM_PROMPT") < source.index(
        "RESPONSE_OPTIONS_SYSTEM_PROMPT"
    )


def test_strip_response_options_keeps_the_prose_only():
    content = """Should I apply the migration?

```options
[{"label": "Yes"}, {"label": "No"}]
```
"""
    assert strip_response_options(content) == "Should I apply the migration?"


def test_strip_response_options_handles_an_unterminated_streamed_block():
    content = "Working on it.\n\n```options\n[{\"label\": \"Yes\"}"
    assert strip_response_options(content) == "Working on it."


def test_strip_response_options_leaves_regular_code_untouched():
    content = "Example:\n\n```python\nprint('hi')\n```"
    assert strip_response_options(content) == content
