"""Tests of voice session operations against an in-memory chat manager."""

import uuid
from types import SimpleNamespace

import pytest

from codx.junior.db import Chat, Message
from codx.junior.voice.model import (
    VoiceSessionCreateRequest,
    VoiceSessionEndRequest,
    VoiceSessionSwitchRequest,
    VoiceTurnRecord,
)
from codx.junior.voice.service import VoiceConversationService


class FakeChatManager:
    """Minimal ChatManager: just enough for the voice service."""

    def __init__(self, chats):
        self.chats = {chat.id: chat for chat in chats}

    def find_by_id(self, chat_id):
        chat = self.chats.get(str(chat_id))
        if chat is None:
            return None
        # Return a copy so callers cannot mutate the stored object accidentally.
        return chat.model_copy(deep=True)

    def list_chats(self, **kwargs):
        return [chat.model_copy(deep=True) for chat in self.chats.values()]

    def update_chat_metadata(self, chat, metadata):
        stored = self.chats[chat.id]
        for key, value in metadata.items():
            if key != "messages" and hasattr(stored, key):
                setattr(stored, key, value)
        return stored.model_copy(deep=True)

    def add_message(self, chat, message):
        stored = self.chats[chat.id]
        if not message.doc_id:
            message.doc_id = str(uuid.uuid4())
        stored.messages.append(message)
        return stored.model_copy(deep=True)

    def save_chat(self, chat, chat_only=False):
        self.chats[chat.id] = chat
        return chat


class FakeSession:
    def __init__(self, tmp_path, chats, project_id="project-1"):
        self.settings = SimpleNamespace(
            codx_path=str(tmp_path / ".codx"),
            project_id=project_id,
            project_name="Test project",
        )
        self.project_id = project_id
        self.manager = FakeChatManager(chats)
        self.switched_to = []

    def get_chat_manager(self):
        return self.manager

    def switch_project(self, project_id):
        self.switched_to.append(project_id)
        return self


def test_start_creates_a_session_and_informs_the_chat(tmp_path):
    chats = [
        Chat(id="chat-1", name="First chat", owner_project_id="project-1"),
        Chat(id="chat-2", name="Payments task", owner_project_id="project-1"),
    ]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)

    response, voice_session, created = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=True)
    )

    assert created is True
    assert response["session"]["chat_id"] == "chat-1"
    assert response["notice"]
    chat = session.manager.chats["chat-1"]
    assert chat.meta_data["voice_session"]["status"] == "active"
    assert chat.messages[-1].role == "assistant"
    assert chat.messages[-1].meta_data["voice_notice"] == "started"
    assert chat.messages[-1].doc_id in voice_session.message_ids


def test_starting_twice_is_idempotent_and_does_not_duplicate_notices(tmp_path):
    chats = [Chat(id="chat-1", name="First chat", owner_project_id="project-1")]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)

    first, first_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1")
    )
    count = len(session.manager.chats["chat-1"].messages)
    second, second_session, created = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1")
    )

    assert created is False
    assert second_session.session_id == first_session.session_id
    assert len(session.manager.chats["chat-1"].messages) == count


def test_switch_moves_the_session_and_notifies_both_chats(tmp_path):
    chats = [
        Chat(id="chat-1", name="First chat", owner_project_id="project-1"),
        Chat(id="chat-2", name="Payments task", owner_project_id="project-1"),
    ]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )

    result = service.switch_session(
        voice_session.session_id,
        VoiceSessionSwitchRequest(chat_id="chat-2", reason="the right task"),
    )

    assert result["switched"] is True
    assert result["session"]["chat_id"] == "chat-2"
    assert result["previous_chat_id"] == "chat-1"
    assert "First chat" in result["notice"]
    assert "the right task" in result["notice"]
    assert session.manager.chats["chat-2"].meta_data["voice_session"]["status"] == "active"
    assert session.manager.chats["chat-1"].meta_data["voice_session"]["status"] == "switched"
    assert "First chat" in session.manager.chats["chat-2"].messages[-1].content
    assert "Payments task" in session.manager.chats["chat-1"].messages[-1].content


def test_switching_to_the_current_chat_is_a_noop(tmp_path):
    chats = [Chat(id="chat-1", name="First chat", owner_project_id="project-1")]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )

    result = service.switch_session(
        voice_session.session_id,
        VoiceSessionSwitchRequest(chat_id="chat-1"),
    )

    assert result["switched"] is False
    assert "already" in result["notice"].lower()


def test_end_marks_the_session_and_the_chat(tmp_path):
    chats = [Chat(id="chat-1", name="First chat", owner_project_id="project-1")]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )

    result = service.end_session(
        voice_session.session_id, VoiceSessionEndRequest(reason="done")
    )

    assert result["session"]["status"] == "ended"
    assert result["session"]["meta"]["end_reason"] == "done"
    assert session.manager.chats["chat-1"].meta_data["voice_session"]["status"] == "ended"
    assert session.manager.chats["chat-1"].messages[-1].meta_data["voice_notice"] == "ended"


def test_record_turn_tracks_the_chat_messages(tmp_path):
    chats = [Chat(id="chat-1", name="First chat", owner_project_id="project-1")]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )

    result = service.record_turn(
        voice_session.session_id,
        VoiceTurnRecord(
            user_message_id="u1",
            assistant_message_id="a1",
            user_text="show me the status",
            assistant_text="all green",
        ),
    )

    assert result["session"]["turn_count"] == 1
    assert result["session"]["last_user_text"] == "show me the status"
    assert result["session"]["message_ids"] == ["u1", "a1"]


def test_unknown_chat_is_rejected(tmp_path):
    session = FakeSession(tmp_path, [])
    service = VoiceConversationService(session)

    with pytest.raises(ValueError, match="not found"):
        service.start_session(VoiceSessionCreateRequest(chat_id="missing"))


def test_harness_chat_cannot_start_a_voice_call(tmp_path):
    chats = [
        Chat(
            id="chat-1",
            name="Harness task",
            owner_project_id="project-1",
            harness="dsh",
        )
    ]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)

    with pytest.raises(ValueError, match="external harness"):
        service.start_session(VoiceSessionCreateRequest(chat_id="chat-1"))


def test_call_cannot_switch_into_a_harness_chat(tmp_path):
    chats = [
        Chat(id="chat-1", name="Normal", owner_project_id="project-1"),
        Chat(
            id="chat-2",
            name="Harness task",
            owner_project_id="project-1",
            external_source="dsh",
        ),
    ]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )

    with pytest.raises(ValueError, match="external harness"):
        service.switch_session(
            voice_session.session_id,
            VoiceSessionSwitchRequest(chat_id="chat-2"),
        )


def test_ended_call_can_be_resumed(tmp_path):
    chats = [Chat(id="chat-1", name="First chat", owner_project_id="project-1")]
    session = FakeSession(tmp_path, chats)
    service = VoiceConversationService(session)
    _, voice_session, _ = service.start_session(
        VoiceSessionCreateRequest(chat_id="chat-1", announce=False)
    )
    service.end_session(voice_session.session_id, VoiceSessionEndRequest())

    response, resumed, created = service.start_session(
        VoiceSessionCreateRequest(
            chat_id="chat-1",
            session_id=voice_session.session_id,
            announce=True,
        )
    )

    assert created is False
    assert response["resumed"] is True
    assert response["session"]["status"] == "active"
    assert response["session"]["resumptions"] == 1
    assert response["notice"]
