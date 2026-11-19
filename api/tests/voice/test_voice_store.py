"""Unit tests of the voice session store (no HTTP, no chat engine)."""

from pathlib import Path

import pytest

from codx.junior.voice.model import VoiceSessionStatus
from codx.junior.voice.store import VoiceSessionNotFound, VoiceSessionStore


def new_store(tmp_path: Path) -> VoiceSessionStore:
    return VoiceSessionStore(tmp_path / "voice")


def test_create_is_idempotent_per_chat(tmp_path):
    store = new_store(tmp_path)
    session, created = store.create_or_resume("chat-1", title="Chat one")
    assert created is True
    assert session.status == VoiceSessionStatus.ACTIVE
    assert session.chats[0].chat_id == "chat-1"

    again, created_again = store.create_or_resume("chat-1", title="Chat one")
    assert created_again is False
    assert again.session_id == session.session_id
    assert len(again.chats) == 1


def test_session_survives_a_new_store_instance(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1", language="es-ES")

    reloaded = VoiceSessionStore(tmp_path / "voice").get(session.session_id)
    assert reloaded is not None
    assert reloaded.language == "es-ES"
    assert reloaded.chat_id == "chat-1"


def test_switch_closes_the_previous_chat_and_clears_pending(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1", title="First")
    store.request_switch(session.session_id, "chat-2", "Second", reason="test")

    moved = store.switch(session.session_id, "chat-2", "Second")

    assert moved.chat_id == "chat-2"
    assert moved.pending_switch is None
    assert [ref.chat_id for ref in moved.chats] == ["chat-1", "chat-2"]
    assert moved.chats[0].until is not None
    assert moved.chats[1].until is None
    assert moved.chats[1].name == "Second"


def test_request_switch_requires_an_active_session(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1")
    store.end(session.session_id, reason="done")

    with pytest.raises(ValueError):
        store.request_switch(session.session_id, "chat-2")


def test_end_and_resume_keep_the_conversation_ids(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1")
    store.record_turn(
        session.session_id,
        user_message_id="u1",
        assistant_message_id="a1",
        user_text="hello",
        assistant_text="hi",
    )

    ended = store.end(session.session_id, reason="hang up")
    assert ended.status == VoiceSessionStatus.ENDED
    assert ended.ended_at is not None
    assert ended.turn_count == 1

    resumed, created = store.create_or_resume(
        "chat-1", session_id=session.session_id, title="Chat one"
    )
    assert created is False
    assert resumed.status == VoiceSessionStatus.ACTIVE
    assert resumed.resumptions == 1
    assert resumed.message_ids == ["u1", "a1"]


def test_record_turn_deduplicates_message_ids(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1")
    store.record_turn(session.session_id, "u1", "a1", "hello", "hi")
    store.record_turn(session.session_id, "u1", "a1", "hello", "hi")

    reloaded = store.get(session.session_id)
    assert reloaded.message_ids == ["u1", "a1"]
    assert reloaded.turn_count == 2


def test_lists_only_active_sessions_when_filtered(tmp_path):
    store = new_store(tmp_path)
    first, _ = store.create_or_resume("chat-1")
    second, _ = store.create_or_resume("chat-2")
    store.end(first.session_id)

    active = store.list_sessions(status="active")
    assert [session.session_id for session in active] == [second.session_id]


def test_a_corrupt_document_is_treated_as_empty(tmp_path):
    store = new_store(tmp_path)
    store.sessions_path.write_text("{ not json", encoding="utf-8")

    assert store.list_sessions() == []
    session, created = store.create_or_resume("chat-1")
    assert created is True
    assert store.get(session.session_id) is not None


def test_unknown_session_raises_only_when_required(tmp_path):
    store = new_store(tmp_path)
    assert store.get("missing") is None
    with pytest.raises(VoiceSessionNotFound):
        store.get("missing", required=True)


def test_language_can_be_changed_and_persists(tmp_path):
    store = new_store(tmp_path)
    session, _ = store.create_or_resume("chat-1", language="en-US")

    updated = store.set_language(session.session_id, "pt-BR")

    assert updated.language == "pt-BR"
    assert VoiceSessionStore(tmp_path / "voice").get(session.session_id).language == "pt-BR"
