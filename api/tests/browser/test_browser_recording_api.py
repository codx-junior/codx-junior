"""Integration tests for the recording/recipe REST endpoints.

The browser manager and runtime are faked: these tests lock the route
contract (start → stop → save, update-in-place, list, run) without Docker.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.api.browser import router
from codx.junior.browser.model import BrowserSession


def _now():
    return datetime.now(timezone.utc).isoformat()


class FakeRuntime:
    def __init__(self, session, activity):
        self.session = session
        self.activity = activity
        self.ran = []

    async def drain(self):
        return {"url": self.session.current_url, "title": self.session.current_title}

    async def run_steps(self, steps, variables, force=False):
        self.ran.append((steps, variables, force))
        return {
            "ok": True,
            "steps": [{"index": i, "action": step.get("action"), "ok": True, "ms": 1} for i, step in enumerate(steps)],
            "outputs": {},
            "failed_step": None,
            "error": None,
            "ms": 1,
        }


class FakeManager:
    def __init__(self, root, session, activity):
        self.settings = SimpleNamespace(abs_project_path=str(root), project_path=str(root), codx_path=str(root))
        self.session = session
        self.activity = activity
        self.logged = []
        self.runtime = FakeRuntime(session, activity)

    def _session_folder_path(self, session_id):
        return str(self.settings.abs_project_path) + f"/sessions/{session_id}"

    def get_session(self, session_id):
        return self.session if session_id == self.session.id else None

    async def get_runtime(self, session):
        return self.runtime

    def peek_runtime(self, session_id):
        return self.runtime

    def get_activity(self, session_id, limit=100):
        return self.activity[-limit:]

    def log_activity(self, session, **kwargs):
        self.logged.append(kwargs)
        return kwargs


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    session = BrowserSession(
        name="Record me",
        kind="browser",
        status="running",
        current_url="https://example.test/posts/new",
        current_title="New post",
    )
    manager = FakeManager(root, session, [])
    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.middleware("http")
    async def inject_session(request, call_next):
        request.state.codx_junior_session = SimpleNamespace(browser_manager=manager)
        return await call_next(request)

    client = TestClient(app)
    return SimpleNamespace(client=client, manager=manager, session=session, root=root)


def _recorded_events(api):
    api.manager.activity.extend([
        {
            "ts": _now(),
            "actor": "user",
            "action": "click",
            "success": True,
            "params": {"target": {"selector": "#title", "tag": "input", "text": "Title"}},
        },
        {
            "ts": _now(),
            "actor": "user",
            "action": "change",
            "success": True,
            "params": {
                "target": {"selector": "#title", "tag": "input", "text": "Title"},
                "value": "My first post",
            },
        },
        {
            "ts": _now(),
            "actor": "user",
            "action": "click",
            "success": True,
            "params": {"target": {"selector": "button.publish", "tag": "button", "text": "Publish"}},
        },
    ])


def test_record_stop_save_and_replay(api):
    base = f"/api/browsers/{api.session.id}"

    initial = api.client.get(f"{base}/recording")
    assert initial.status_code == 200
    assert initial.json()["recording"]["active"] is False

    started = api.client.post(
        f"{base}/recording/start",
        json={"title": "Create a post", "description": "Publish a post", "tags": ["post"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["recording"]["active"] is True

    _recorded_events(api)
    stopped = api.client.post(f"{base}/recording/stop")
    assert stopped.status_code == 200, stopped.text
    payload = stopped.json()
    assert payload["recording"]["active"] is False
    actions = [step["action"] for step in payload["steps"]]
    assert actions == ["navigate", "fill", "click"]
    assert payload["steps"][1]["value"] == "My first post"

    saved = api.client.post(
        f"{base}/recording/save",
        json={"title": "Create a post", "steps": payload["steps"], "tags": ["post"]},
    )
    assert saved.status_code == 200, saved.text
    script = saved.json()["script"]
    assert script["title"] == "Create a post"
    assert script["version"] == 1
    assert script["step_count"] == 3

    listed = api.client.get(f"{base}/scripts")
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()["scripts"]] == [script["id"]]

    run = api.client.post(f"{base}/scripts/{script['id']}/run", json={"variables": {"value": "x"}})
    assert run.status_code == 200, run.text
    assert run.json()["ok"] is True
    assert api.manager.runtime.ran[-1][2] is True  # the human clicked Run => force
    assert api.manager.runtime.ran[-1][1] == {"value": "x"}


def test_reselecting_a_recipe_updates_it_in_place(api):
    base = f"/api/browsers/{api.session.id}"
    api.client.post(f"{base}/recording/start", json={"title": "Create a post"})
    _recorded_events(api)
    stopped = api.client.post(f"{base}/recording/stop").json()
    script = api.client.post(
        f"{base}/recording/save",
        json={"title": "Create a post", "steps": stopped["steps"]},
    ).json()["script"]

    # Record the same flow again, targeting the existing recipe.
    api.client.post(
        f"{base}/recording/start",
        json={"title": "Create a post", "script_id": script["id"]},
    )
    _recorded_events(api)
    stopped2 = api.client.post(f"{base}/recording/stop").json()
    updated = api.client.post(
        f"{base}/recording/save",
        json={
            "title": "Create a post",
            "steps": stopped2["steps"],
            "script_id": script["id"],
        },
    ).json()["script"]

    assert updated["id"] == script["id"]
    assert updated["version"] == 2
    assert len(api.client.get(f"{base}/scripts").json()["scripts"]) == 1


def test_script_can_be_edited_and_soft_deleted(api):
    base = f"/api/browsers/{api.session.id}"
    api.client.post(f"{base}/recording/start", json={"title": "Flow"})
    _recorded_events(api)
    stopped = api.client.post(f"{base}/recording/stop").json()
    script = api.client.post(
        f"{base}/recording/save",
        json={"title": "Flow", "steps": stopped["steps"]},
    ).json()["script"]

    edited = api.client.put(
        f"{base}/scripts/{script['id']}",
        json={"title": "Flow v2", "steps": [{"action": "reload"}]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["script"]["version"] == 2
    assert edited.json()["script"]["steps"] == [{"action": "reload"}]

    removed = api.client.delete(f"{base}/scripts/{script['id']}")
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert api.client.get(f"{base}/scripts").json()["scripts"] == []


def test_recording_rejects_non_browser_sessions(api):
    api.session.kind = "linux"
    response = api.client.post(f"/api/browsers/{api.session.id}/recording/start", json={})
    assert response.status_code == 400
    assert "browser" in response.json()["detail"].lower()


def test_analyze_reviews_and_updates_the_recipe(api, monkeypatch):
    base = f"/api/browsers/{api.session.id}"

    async def fake_analysis(settings, state, events, baseline, warnings, existing_script=None, use_ai=True, **kwargs):
        assert events, "the endpoint must pass the recorded transcript"
        assert existing_script is None
        return {
            "steps": [
                {"action": "navigate", "url": state.start_url},
                {"action": "fill", "selector": "#title", "value": "AI reviewed text"},
                {"action": "click", "selector": "button.publish"},
            ],
            "title": "AI title",
            "description": "AI description",
            "tags": ["ai", "post"],
            "warnings": ["verify publish"],
            "notes": "Dropped the focus click and merged the edit.",
            "used_ai": True,
            "error": None,
            "model": "test-model",
        }

    monkeypatch.setattr(
        "codx.junior.api.browser.analyze_recording", fake_analysis
    )

    api.client.post(f"{base}/recording/start", json={"title": "Create a post"})
    _recorded_events(api)
    api.client.post(f"{base}/recording/stop")

    analyzed = api.client.post(f"{base}/recording/analyze", json={"save": True})
    assert analyzed.status_code == 200, analyzed.text
    payload = analyzed.json()
    assert payload["analysis"]["used_ai"] is True
    assert payload["analysis"]["saved"] is True
    assert payload["analysis"]["notes"].startswith("Dropped")
    assert payload["script"]["title"] == "Create a post"  # the recipe title wins
    assert payload["script"]["description"] == "AI description"
    assert payload["script"]["tags"] == ["ai", "post"]
    assert payload["script"]["step_count"] == 3
    assert payload["recording"]["saved_script_id"] == payload["script"]["id"]


def test_analyze_without_save_only_returns_steps(api, monkeypatch):
    base = f"/api/browsers/{api.session.id}"

    async def fake_analysis(settings, state, events, baseline, warnings, existing_script=None, use_ai=True, **kwargs):
        return {
            "steps": [{"action": "reload"}],
            "title": "",
            "description": "",
            "tags": [],
            "warnings": [],
            "notes": "ok",
            "used_ai": True,
            "error": None,
            "model": "test-model",
        }

    monkeypatch.setattr(
        "codx.junior.api.browser.analyze_recording", fake_analysis
    )
    api.client.post(f"{base}/recording/start", json={"title": "Create a post"})
    _recorded_events(api)
    api.client.post(f"{base}/recording/stop")

    analyzed = api.client.post(f"{base}/recording/analyze", json={"save": False})
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["script"] is None
    assert analyzed.json()["steps"] == [{"action": "reload"}]
    assert api.client.get(f"{base}/scripts").json()["scripts"] == []
