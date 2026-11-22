"""HTTP contract of the autonomous-ticket endpoints.

The router is mounted in a tiny FastAPI app and the project session is stubbed,
so the test stays offline. It checks the target-side configuration/queue/cancel
surface and the read-only view the requester gets from its own project.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codx.junior.settings import CODXJuniorSettings
from codx.junior.ticketing.service import TicketService


class _FakeEventManager:
    def chat_event(self, chat=None, message=None, event_type=None):
        return None


class _FakeSession:
    def __init__(self, settings):
        self.settings = settings
        self.user = SimpleNamespace(username="tester")


def _project(root, name, project_id):
    (root / ".codx").mkdir(parents=True, exist_ok=True)
    return CODXJuniorSettings(
        project_id=project_id,
        project_name=name,
        abs_project_path=str(root),
        codx_path=str(root / ".codx"),
        task_workspace_isolation=False,
    )


def _child_project(parent_root, name="junior", project_id="junior-id"):
    child_root = parent_root / "plugins" / name
    (child_root / ".codx").mkdir(parents=True, exist_ok=True)
    (child_root / ".codx" / "project.json").write_text(
        json.dumps({"project_id": project_id, "project_name": name}),
        encoding="utf-8",
    )
    return CODXJuniorSettings.from_project_file(
        str(child_root / ".codx" / "project.json")
    )


@pytest.fixture
def projects(tmp_path):
    cloud_root = tmp_path / "cloud"
    cloud = _project(cloud_root, "cloud", "cloud-id")
    child = _child_project(cloud_root)
    child.task_workspace_isolation = False
    return cloud, child


def _place(projects, **kwargs):
    from codx.junior.chat_manager import ChatManager

    cloud, child = projects
    manager = ChatManager(settings=cloud, event_manager=_FakeEventManager())
    service = TicketService(settings=cloud, chat_manager=manager)
    return service.create(
        target=child.project_name,
        name=kwargs.pop("name", "Fix the export"),
        description=kwargs.pop("description", "Exporting an empty board crashes."),
        **kwargs,
    )


@pytest.fixture
def client(projects, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import codx.junior.api.tickets as tickets_api

    cloud, child = projects

    def _session(request):
        # The path decides which side is calling: /api/tickets/{id} on the
        # target project resolves the ticket from its own queue.
        return _FakeSession(child)

    monkeypatch.setattr(tickets_api, "get_current_session", _session)
    app = FastAPI()
    app.include_router(tickets_api.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client, tickets_api, cloud, child


def test_create_ticket_with_autonomy_exposes_the_state(projects, client):
    _client, _api, _cloud, child = client
    record = _place(projects, autonomous=True)
    response = _client.get(f"/api/tickets/{record.ticket_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_id"] == record.ticket_id
    assert payload["autonomy"]["enabled"] is True
    assert payload["autonomy"]["state"] == "queued"


def test_target_can_configure_queue_and_cancel_over_http(projects, client):
    _client, _api, _cloud, _child = client
    record = _place(projects)

    configured = _client.post(
        f"/api/tickets/{record.ticket_id}/autonomy",
        json={
            "enabled": True,
            "profile": "codx-junior",
            "max_rounds": 3,
            "validation": {"source": "auto"},
        },
    )
    assert configured.status_code == 200
    body = configured.json()
    assert body["can_configure"] is True
    assert body["autonomy"]["state"] == "queued"
    assert body["autonomy"]["max_rounds"] == 3

    queued = _client.post(f"/api/tickets/{record.ticket_id}/autonomy/queue")
    assert queued.status_code == 200
    assert queued.json()["autonomy"]["state"] == "queued"

    cancelled = _client.post(f"/api/tickets/{record.ticket_id}/autonomy/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["autonomy"]["state"] == "cancelled"


def test_the_requester_reads_autonomy_but_cannot_configure_it(
    projects, client, monkeypatch
):
    _client, api, cloud, _child = client
    record = _place(projects, autonomous=True)
    monkeypatch.setattr(
        api, "get_current_session", lambda request: _FakeSession(cloud)
    )

    status = _client.get(f"/api/tickets/{record.ticket_id}/autonomy")
    assert status.status_code == 200
    assert status.json()["can_configure"] is False
    assert status.json()["direction"] == "outgoing"

    forbidden = _client.post(
        f"/api/tickets/{record.ticket_id}/autonomy",
        json={"enabled": True},
    )
    assert forbidden.status_code == 400
    assert "only the target" in forbidden.json()["detail"]
