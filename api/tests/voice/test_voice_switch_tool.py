"""Tests of the ``voice_switch_chat`` assistant tool."""

from types import SimpleNamespace

import pytest

from codx.junior.db import Chat
from codx.junior.tools.voice_tools import voice_switch_chat
from codx.junior.voice.store import VoiceSessionStore


class FakeManager:
    def __init__(self, chats):
        self.chats = chats

    def list_chats(self, **kwargs):
        return list(self.chats)


class FakeSession:
    def __init__(self, tmp_path, chats):
        self.settings = SimpleNamespace(codx_path=str(tmp_path / ".codx"))
        self.manager = FakeManager(chats)

    def get_chat_manager(self):
        return self.manager


@pytest.fixture
def context(tmp_path):
    chats = [
        Chat(id="chat-1", name="Voice UI"),
        Chat(id="chat-2", name="Payments task"),
        Chat(id="chat-3", name="Payments design"),
    ]
    session = FakeSession(tmp_path, chats)
    store = VoiceSessionStore.for_settings(session.settings)
    voice_session, _ = store.create_or_resume("chat-1")
    return session, store, voice_session


def test_switch_tool_records_a_pending_switch(context):
    session, store, voice_session = context

    result = voice_switch_chat(
        chat="Payments task",
        reason="the user asked for it",
        chat_id="chat-1",
        session=session,
        settings=session.settings,
    )

    assert "Payments task" in result.user_response
    pending = store.get(voice_session.session_id).pending_switch
    assert pending is not None
    assert pending.chat_id == "chat-2"
    assert pending.requested_by == "assistant"


def test_switch_tool_matches_by_id(context):
    session, store, voice_session = context

    voice_switch_chat(
        chat="chat-2", chat_id="chat-1", session=session, settings=session.settings
    )

    pending = store.get(voice_session.session_id).pending_switch
    assert pending.chat_id == "chat-2"


def test_ambiguous_name_is_not_switched(context):
    session, store, voice_session = context

    result = voice_switch_chat(
        chat="Payments", chat_id="chat-1", session=session, settings=session.settings
    )

    assert "more than one" in result.user_response
    assert store.get(voice_session.session_id).pending_switch is None
    assert "candidates" in result.llm_response


def test_missing_name_is_reported(context):
    session, store, voice_session = context

    result = voice_switch_chat(
        chat="does not exist",
        chat_id="chat-1",
        session=session,
        settings=session.settings,
    )

    assert "could not find" in result.user_response
    assert store.get(voice_session.session_id).pending_switch is None


def test_no_active_call_is_reported(tmp_path):
    session = FakeSession(tmp_path, [Chat(id="chat-1", name="Voice UI")])

    result = voice_switch_chat(
        chat="Voice UI",
        chat_id="chat-1",
        session=session,
        settings=session.settings,
    )

    assert "no active voice call" in result.user_response.lower()


def test_switching_to_the_current_chat_does_nothing(context):
    session, store, voice_session = context

    result = voice_switch_chat(
        chat="Voice UI", chat_id="chat-1", session=session, settings=session.settings
    )

    assert "already" in result.user_response
    assert store.get(voice_session.session_id).pending_switch is None
