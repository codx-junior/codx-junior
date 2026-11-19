"""HTTP contract tests for the voice router.

The router is mounted on a bare FastAPI app and the project session is faked,
so these tests exercise request/response shapes, status codes and the switch
notices without the engine, a socket server or a real chat store.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.api import voice as voice_api
from codx.junior.db import Chat
from codx.junior.voice.service import VoiceConversationService
from codx.junior.voice.speech import SpeechUnavailable


class FakeChatManager:
    def __init__(self, chats):
        self.chats = {chat.id: chat for chat in chats}

    def find_by_id(self, chat_id):
        chat = self.chats.get(str(chat_id))
        return chat.model_copy(deep=True) if chat else None

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


class FakeSession:
    def __init__(self, tmp_path):
        self.settings = SimpleNamespace(
            codx_path=str(tmp_path / ".codx"),
            project_id="project-1",
            project_name="Test project",
        )
        self.manager = FakeChatManager(
            [
                Chat(id="chat-1", name="First chat", owner_project_id="project-1"),
                Chat(id="chat-2", name="Payments task", owner_project_id="project-1"),
            ]
        )

    def get_chat_manager(self):
        return self.manager

    def switch_project(self, project_id):
        return self


class FakeStt:
    def info(self):
        return {"available": False, "provider": "faster-whisper", "reason": "missing"}

    def transcribe(self, content, filename=None, content_type=None, language=None):
        return {"text": "hello from audio", "language": language or "en"}


class FakeSynthesizerOk:
    def info(self):
        return {
            "available": True,
            "providers": [{"name": "fake", "available": True}],
            "reason": None,
        }

    def synthesize(self, **kwargs):
        return {
            "content": b"ID3fake-mp3",
            "mime": "audio/mpeg",
            "provider": "fake",
            "model": "fake-tts",
        }


class FakeSynthesizer:
    def info(self):
        return {
            "available": True,
            "providers": [{"name": "fake", "available": True}],
            "reason": None,
        }

    def synthesize(self, **kwargs):
        raise SpeechUnavailable("synthesis", "fake provider offline")


@pytest.fixture
def client(tmp_path):
    session = FakeSession(tmp_path)
    app = FastAPI()
    app.include_router(voice_api.router, prefix="/api")
    service = VoiceConversationService(session)
    app.dependency_overrides[voice_api.get_voice_service] = lambda: service
    app.dependency_overrides[voice_api.get_speech_to_text] = lambda: FakeStt()
    app.dependency_overrides[voice_api.get_synthesizer] = lambda: FakeSynthesizer()
    return TestClient(app)


def create(client, chat_id="chat-1", **overrides):
    payload = {"chat_id": chat_id, "announce": True, **overrides}
    response = client.post("/api/voice/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_capabilities_reports_both_directions(client):
    response = client.get("/api/voice/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["transcription"]["available"] is False
    assert body["synthesis"]["available"] is True


def test_session_lifecycle_switches_and_persists_notices(client):
    created = create(client)
    session_id = created["session"]["session_id"]
    assert created["created"] is True
    assert created["chat"]["id"] == "chat-1"
    assert created["chat"]["meta_data"]["voice_session"]["status"] == "active"

    listed = client.get("/api/voice/sessions")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    switched = client.post(
        f"/api/voice/sessions/{session_id}/switch",
        json={"chat_id": "chat-2", "reason": "the payments task"},
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["switched"] is True
    assert body["session"]["chat_id"] == "chat-2"
    assert body["chat"]["meta_data"]["voice_session"]["status"] == "active"
    assert body["chat"]["meta_data"]["voice_session"]["moved_from"]["chat_id"] == "chat-1"

    ended = client.post(
        f"/api/voice/sessions/{session_id}/end",
        json={"reason": "done"},
    )
    assert ended.status_code == 200
    assert ended.json()["session"]["status"] == "ended"

    loaded = client.get(f"/api/voice/sessions/{session_id}")
    assert loaded.status_code == 200
    assert loaded.json()["session"]["status"] == "ended"


def test_unknown_session_returns_404(client):
    response = client.get("/api/voice/sessions/does-not-exist")
    assert response.status_code == 404


def test_unknown_chat_returns_404(client):
    response = client.post(
        "/api/voice/sessions", json={"chat_id": "missing-chat"}
    )
    assert response.status_code == 404


def test_turn_recording_updates_the_session(client):
    created = create(client)
    session_id = created["session"]["session_id"]

    response = client.post(
        f"/api/voice/sessions/{session_id}/turns",
        json={
            "user_message_id": "u1",
            "assistant_message_id": "a1",
            "user_text": "what is the status?",
            "assistant_text": "everything is green",
        },
    )
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    assert session["turn_count"] == 1
    assert session["last_user_text"] == "what is the status?"
    # The start notice is already part of the call trail.
    assert session["message_ids"][-2:] == ["u1", "a1"]


def test_transcription_uses_the_injected_backend(client):
    response = client.post(
        "/api/voice/transcribe",
        files={"file": ("utterance.webm", b"fake audio", "audio/webm")},
        data={"language": "en-US"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "hello from audio"


def test_speech_returns_503_when_no_provider_can_speak(client):
    response = client.post("/api/voice/speech", json={"text": "hello"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["capability"] == "synthesis"


def test_speech_streams_audio_when_a_provider_is_available(client):
    client.app.dependency_overrides[voice_api.get_synthesizer] = lambda: FakeSynthesizerOk()

    response = client.post("/api/voice/speech", json={"text": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-voice-provider"] == "fake"
    assert response.content == b"ID3fake-mp3"


def test_oversized_audio_is_rejected_with_413(client, monkeypatch):
    monkeypatch.setattr(voice_api, "MAX_AUDIO_BYTES", 4)
    response = client.post(
        "/api/voice/transcribe",
        files={"file": ("utterance.webm", b"way too big", "audio/webm")},
    )
    assert response.status_code == 413


def test_call_language_can_be_switched_from_the_call_ui(client):
    created = create(client)
    session_id = created["session"]["session_id"]

    response = client.post(
        f"/api/voice/sessions/{session_id}/language",
        json={"language": "pt-BR"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session"]["language"] == "pt-BR"
    assert body["chat"]["meta_data"]["voice_session"]["language"] == "pt-BR"
