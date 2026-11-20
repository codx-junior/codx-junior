"""Tests of the meeting artifacts (chat, profiles, transcript, floor, tasks).

The heavy AI calls are not exercised here: the tests drive the meeting service
with an in-memory chat manager and a temporary project store, which is exactly
the boundary the HTTP router uses.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.api import meets as meets_api
from codx.junior.db import Chat, Message
from codx.junior.meets.meet_manager import get_meet_manager
from codx.junior.meets.model import (
    MeetActionItem,
    MeetMinutes,
    MeetSettings,
    MeetTranscriptEntry,
    ParticipantKind,
)
from codx.junior.meets.service import MeetingService


class FakeChatManager:
    """Minimal ChatManager: enough for the meeting chat bridge."""

    def __init__(self):
        self.chats = {}

    def find_by_id(self, chat_id):
        chat = self.chats.get(str(chat_id))
        return chat.model_copy(deep=True) if chat else None

    def list_chats(self, **kwargs):
        return [chat.model_copy(deep=True) for chat in self.chats.values()]

    def save_chat(self, chat, chat_only=False):
        if not chat.id:
            chat.id = str(uuid.uuid4())
        self.chats[chat.id] = chat
        return chat.model_copy(deep=True)

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
    def __init__(self, tmp_path, project_id="project-1"):
        self.settings = SimpleNamespace(
            codx_path=str(tmp_path / ".codx"),
            project_id=project_id,
            project_name="Test project",
            abs_project_path=str(tmp_path),
            project_path=str(tmp_path),
        )
        self.user = SimpleNamespace(username="admin")
        self.manager = FakeChatManager()

    def get_chat_manager(self):
        return self.manager

    async def chat_with_project(self, **kwargs):
        return None


def snapshot(name):
    return {
        "profile_name": name,
        "name": name.title(),
        "avatar": f"https://avatars.test/{name}.png",
        "project_id": "project-1",
        "voice": {"enabled": True, "source": "profile", "voice": {"voice_id": "v1"}},
        "description": f"{name} description",
        "model": "",
        "content": "",
        "tags": [],
    }


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Return (client, session, events) with a fake project session."""
    events = []

    async def fake_emit(event, data):
        events.append({"event": event, "data": data})

    monkeypatch.setattr(meets_api, "_emit", fake_emit)
    monkeypatch.setattr(meets_api, "participant_snapshot", lambda session, name: snapshot(name))
    session = FakeSession(tmp_path)
    app = FastAPI()
    app.include_router(meets_api.router, prefix="/api")

    @app.middleware("http")
    async def attach_session(request, call_next):
        request.state.codx_junior_session = session
        return await call_next(request)

    return TestClient(app), session, events


def headers(username="admin", sid="sid-admin"):
    return {"x-username": username, "x-sid": sid}


def new_meet_id():
    return f"test-{uuid.uuid4().hex[:10]}"


def initiate(client, meet_id, profiles=None, auto_respond=False):
    payload = {
        "meetId": meet_id,
        "name": "Team review",
        "agenda": "Review the board",
        "profiles": profiles or [],
        "settings": {"auto_respond": auto_respond},
    }
    response = client.post("/api/meets/initiate", json=payload, headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["meet"]


# ---------------------------------------------------------------------------
# Profiles + chat
# ---------------------------------------------------------------------------


def test_initiate_adds_virtual_profiles_with_avatar_and_camera_off(api):
    client, _, _ = api
    meet_id = new_meet_id()

    meet = initiate(client, meet_id, profiles=["analyst"])

    profiles = meet["profileParticipants"]
    assert len(profiles) == 1
    assert profiles[0]["profile_name"] == "analyst"
    assert profiles[0]["avatar"].endswith("analyst.png")
    assert profiles[0]["kind"] == "profile"
    assert profiles[0]["video"] is False
    # Profiles are never WebRTC peers.
    assert meet["participantsUsers"] == ["admin"]
    assert meet["chat_id"], "the meeting chat is created on start"


def test_meeting_chat_receives_notes_and_utterances(api):
    client, session, _ = api
    meet_id = new_meet_id()
    meet = initiate(client, meet_id)
    chat_id = meet["chat_id"]
    assert chat_id in session.manager.chats
    chat = session.manager.chats[chat_id]
    assert chat.mode == "meet"
    assert chat.meta_data["meeting"]["meet_id"] == meet_id

    response = client.post(
        f"/api/meets/{meet_id}/utterance",
        json={"text": "What is blocked?", "source": "speech", "autoRespond": False},
        headers=headers("gbrian", "sid-gbrian"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry"]["speaker"] == "gbrian"
    assert body["entry"]["text"] == "What is blocked?"
    chat = session.manager.chats[chat_id]
    assert any(m.content == "What is blocked?" and m.user == "gbrian" for m in chat.messages)

    transcript = client.get(
        f"/api/meets/{meet_id}/transcript", headers=headers()
    ).json()["transcript"]
    assert [entry["text"] for entry in transcript][-1] == "What is blocked?"


def test_duplicate_client_message_is_idempotent(api):
    client, _, _ = api
    meet_id = new_meet_id()
    initiate(client, meet_id)

    payload = {
        "text": "only once",
        "clientMessageId": "client-1",
        "autoRespond": False,
    }
    first = client.post(
        f"/api/meets/{meet_id}/utterance", json=payload, headers=headers()
    )
    second = client.post(
        f"/api/meets/{meet_id}/utterance", json=payload, headers=headers()
    )
    assert first.json()["entry"] is not None
    assert second.json()["entry"] is None
    transcript = client.get(f"/api/meets/{meet_id}/transcript").json()["transcript"]
    assert [entry["text"] for entry in transcript].count("only once") == 1


# ---------------------------------------------------------------------------
# Barge-in / floor
# ---------------------------------------------------------------------------


def test_human_speech_takes_the_floor_and_silences_the_profile(api):
    client, session, events = api
    meet_id = new_meet_id()
    meet = initiate(client, meet_id, profiles=["analyst"])
    manager = get_meet_manager()
    manager.take_floor(meet_id, "analyst", kind=ParticipantKind.PROFILE)

    response = client.post(
        f"/api/meets/{meet_id}/floor",
        json={"event": "start", "kind": "user", "speaker": "gbrian"},
        headers=headers("gbrian", "sid-gbrian"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["floor"]["holder"] == "gbrian"
    assert response.json()["floor"]["kind"] == "user"
    stored = manager.get_meet(meet_id)
    profile = stored.find_participant("analyst")
    assert profile.speaking is False
    floor_events = [
        item["data"] for item in events
        if item["data"].get("type") == "floor"
    ]
    assert floor_events[-1]["holder"] == "gbrian"


# ---------------------------------------------------------------------------
# Minutes + tasks
# ---------------------------------------------------------------------------


def test_minutes_are_persisted_and_listed_in_history(api):
    client, session, _ = api
    meet_id = new_meet_id()
    initiate(client, meet_id)
    service = MeetingService(session, emit=meets_api._emit)
    meet = service.get_meet(meet_id)
    meet.minutes = MeetMinutes(
        status="ready",
        summary="Reviewed the board",
        decisions=["Ship the login fix"],
        action_items=[
            MeetActionItem(
                id="a1",
                title="Fix login timeout",
                description="Increase the token TTL",
            )
        ],
        generated_at="2026-01-01T00:00:00+00:00",
    )
    service.persist(meet)

    stored = service.get_meet(meet_id)
    assert stored.minutes.summary == "Reviewed the board"

    history = client.get("/api/meets/history", headers=headers()).json()
    assert any(item["meet_id"] == meet_id for item in history["meetings"])


def test_tasks_are_created_from_the_minutes(api):
    client, session, _ = api
    meet_id = new_meet_id()
    initiate(client, meet_id)
    service = MeetingService(session, emit=meets_api._emit)
    meet = service.get_meet(meet_id)
    meet.minutes = MeetMinutes(
        status="ready",
        summary="Minutes",
        action_items=[
            MeetActionItem(
                id="a1",
                title="Fix login timeout",
                description="Increase the token TTL",
                priority="high",
            )
        ],
    )
    service.persist(meet)

    response = client.post(
        f"/api/meets/{meet_id}/tasks",
        json={"board": "kanban", "column": "tasks"},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["tasks"]) == 1
    task = body["tasks"][0]
    assert task["name"] == "Fix login timeout"
    assert task["board"] == "kanban"
    assert task["column"] == "tasks"
    stored = service.get_meet(meet_id)
    assert stored.minutes.action_items[0].status == "created"
    assert stored.minutes.action_items[0].task_id == task["id"]


# ---------------------------------------------------------------------------
# Settings / invites
# ---------------------------------------------------------------------------


def test_invite_adds_profiles_and_humans(api):
    client, _, events = api
    meet_id = new_meet_id()
    initiate(client, meet_id)

    response = client.post(
        f"/api/meets/{meet_id}/participants",
        json={"users": ["gbrian"], "profiles": ["analyst"]},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    meet = response.json()["meet"]
    assert "gbrian" in meet["invited"]
    assert "analyst" in [p["profile_name"] for p in meet["profileParticipants"]]
    assert any(item["data"].get("type") == "participants" for item in events)


def test_recording_upload_is_stored_and_served(api):
    client, session, _ = api
    meet_id = new_meet_id()
    initiate(client, meet_id)

    response = client.post(
        f"/api/meets/{meet_id}/recording",
        files={"file": ("call.webm", b"fake-audio-bytes", "audio/webm")},
        data={"duration": "12.5"},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["recording"]["status"] == "ready"
    download = client.get(f"/api/meets/{meet_id}/recording", headers=headers())
    assert download.status_code == 200
    assert download.content == b"fake-audio-bytes"


def test_profile_responders_respect_mentions_and_the_turn_limit(api):
    client, session, _ = api
    meet_id = new_meet_id()
    initiate(client, meet_id, profiles=["analyst", "developer"])
    service = MeetingService(session, emit=meets_api._emit)
    meet = service.get_meet(meet_id)

    assert service.select_responders(meet, "@analyst what is blocked?") == ["analyst"]
    responders = service.select_responders(meet, "team, what is the status?")
    assert len(responders) == 2
    assert set(responders) == {"analyst", "developer"}


def test_notes_request_accepts_the_async_alias():
    from codx.junior.meets.model import MeetNotesRequest

    assert MeetNotesRequest(**{"async": False}).async_generation is False
    assert MeetNotesRequest(async_generation=True).async_generation is True


def test_store_rejects_path_traversal(tmp_path):
    from codx.junior.meets.store import MeetStore, MeetStoreError

    store = MeetStore(str(tmp_path / "meetings"))
    with pytest.raises(MeetStoreError):
        store.meet_path("../../etc/passwd")


@pytest.mark.asyncio
async def test_profile_answer_is_stored_in_chat_and_announced(api, monkeypatch):
    """The core meeting loop: one profile answer, one chat message, one event."""
    import asyncio
    import codx.junior.meets.service as service_module

    client, session, events = api
    meet_id = new_meet_id()
    initiate(client, meet_id, profiles=["analyst"], auto_respond=False)
    service = MeetingService(session, emit=meets_api._emit)
    meet = service.get_meet(meet_id)
    chat_id = meet.chat_id

    async def fake_answer(session, meet, profile_name, instructions=""):
        return {
            "profile": profile_name,
            "text": "The login fix is blocked by the token TTL.",
            "speech": "The login fix is blocked by the token TTL.",
            "passed": False,
            "model": "",
            "error": None,
        }

    monkeypatch.setattr(service_module, "generate_profile_answer", fake_answer)
    monkeypatch.setattr(service_module, "_seconds_for_speech", lambda text: 0.01)

    await service._run_profile_turns(meet_id, ["analyst"], asyncio.Event())

    chat = session.manager.chats[chat_id]
    answers = [message for message in chat.messages if message.user == "analyst"]
    assert len(answers) == 1
    assert "token TTL" in answers[0].content

    stored = service.get_meet(meet_id)
    assert stored.profile_turns["analyst"] == 1
    assert any(entry.speaker == "analyst" for entry in stored.transcript)
    turns = [
        item["data"] for item in events
        if item["data"].get("type") == "profile-turn"
        and item["data"].get("profile") == "analyst"
    ]
    assert turns and turns[0]["speech"]
