"""
Tests of the harness-aware behaviours of the chat engine.

The engine itself needs a provider and a database, but the classification that
decides whether a turn keeps a durable transcript is pure. It guards the three
task-mode behaviours that broke harness sessions (archived transcript, history
filtered to ``is_answer`` and suggested replies removed).

Run with::

    cd api && pytest tests/test_harness_engine.py -q
"""

from __future__ import annotations

from codx.junior.chat.harness_chat import is_harness_chat as _is_harness_chat
from codx.junior.db import Chat
from codx.junior.harness import prompts


def test_detects_a_harness_chat_by_tag():
    chat = Chat(id="c1", tags=[prompts.HARNESS_TAG])
    assert _is_harness_chat(chat) is True


def test_detects_a_harness_chat_by_meta_descriptor():
    chat = Chat(id="c1", meta_data={prompts.HARNESS_META_KEY: {"enabled": True}})
    assert _is_harness_chat(chat) is True

    disabled = Chat(id="c2", meta_data={prompts.HARNESS_META_KEY: {"enabled": False}})
    assert _is_harness_chat(disabled) is False


def test_a_regular_task_chat_is_not_a_harness_transcript():
    assert _is_harness_chat(Chat(id="c1", mode="task")) is False
    assert _is_harness_chat(None) is False
