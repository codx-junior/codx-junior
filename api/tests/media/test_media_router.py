"""Offline tests of the media API router.

No server, no session, no network and no media provider is exercised: the
tests check the declared routes, the project store binding, the asset path
traversal guard and — when ``httpx`` is installed — the json contract of the
endpoints through a ``TestClient`` with the session dependency overridden.
"""

from pathlib import Path
from typing import List, Set, Tuple

import pytest
from fastapi import FastAPI, HTTPException

from codx.junior.api import media as media_api
from codx.junior.media.model import MediaJob
from codx.junior.media.store import MediaStore

EXPECTED_ROUTES = {
    ("GET", "/media/providers"),
    ("GET", "/media/queue"),
    ("POST", "/media/episodes"),
    ("GET", "/media/episodes/{episode_id}"),
    ("GET", "/media/assets/{asset_id}"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def declared_routes(router) -> Set[Tuple[str, str]]:
    """(method, path) pairs declared by ``router``, ignoring HEAD/OPTIONS."""
    declared: Set[Tuple[str, str]] = set()
    for route in getattr(router, "routes", []):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            declared.add((method.upper(), path))
    return declared


def dependency_names(dependant) -> Set[str]:
    """Names of every callable used as a dependency of ``dependant``."""
    names: Set[str] = set()
    for sub in getattr(dependant, "dependencies", None) or []:
        call = getattr(sub, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", str(call)))
        names |= dependency_names(sub)
    return names


class _FakeSettings:
    def __init__(self, project_path: Path) -> None:
        self.abs_project_path = str(project_path)


class _FakeSession:
    """Minimal stand-in for CODXJuniorSession: only the settings path is used."""

    def __init__(self, project_path: Path) -> None:
        self.settings = _FakeSettings(project_path)


@pytest.fixture
def client(tmp_path):
    """TestClient of the media router with the project session overridden."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(media_api.router, prefix="/api")
    app.dependency_overrides[media_api.get_current_session] = lambda: _FakeSession(
        tmp_path
    )
    with TestClient(app) as test_client:
        test_client.project_dir = tmp_path
        yield test_client


# ─────────────────────────────────────────────────────────────────────────────
# Declared surface
# ─────────────────────────────────────────────────────────────────────────────


def test_router_prefix_and_tags():
    assert media_api.router.prefix == "/media"
    assert "media" in media_api.router.tags


def test_router_declares_expected_endpoints():
    assert declared_routes(media_api.router) == EXPECTED_ROUTES


def test_every_endpoint_requires_the_project_session():
    for route in media_api.router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        assert "get_current_session" in dependency_names(dependant), route.path


# ─────────────────────────────────────────────────────────────────────────────
# Store binding
# ─────────────────────────────────────────────────────────────────────────────


def test_store_is_bound_to_the_project_media_folder(tmp_path):
    store = media_api.store_for_project(tmp_path)
    assert isinstance(store, MediaStore)
    assert store.root == tmp_path / ".codx" / "media"
    assert store.jobs_dir.is_dir()
    assert store.episodes_dir.is_dir()
    assert store.assets_dir.is_dir()


def test_store_uses_for_project(tmp_path, monkeypatch):
    calls: List[Path] = []
    original = MediaStore.for_project.__func__

    def spy(cls, project_path):
        calls.append(Path(project_path))
        return original(cls, project_path)

    monkeypatch.setattr(MediaStore, "for_project", classmethod(spy))
    store = media_api.store_for_project(tmp_path)
    assert calls == [tmp_path]
    assert store.root == tmp_path / ".codx" / "media"


def test_budget_is_bound_to_the_store_root(tmp_path):
    store = media_api.store_for_project(tmp_path)
    gate = media_api.budget_for_store(store)
    assert gate.path == store.root / media_api.BUDGET_FILENAME
    snapshot = gate.snapshot()
    assert snapshot["path"] == str(store.root / media_api.BUDGET_FILENAME)


# ─────────────────────────────────────────────────────────────────────────────
# Assets: path traversal guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "asset_id",
    ["../etc/passwd", "..", "../../secret.png", "a/b", "assets/../../x", "", "   "],
)
def test_asset_path_traversal_is_rejected(tmp_path, asset_id):
    store = media_api.store_for_project(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        media_api.asset_file(store, asset_id)
    assert excinfo.value.status_code == 400


def test_unknown_asset_is_not_found(tmp_path):
    store = media_api.store_for_project(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        media_api.asset_file(store, "ab" + "0" * 30)
    assert excinfo.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint contract through a TestClient
# ─────────────────────────────────────────────────────────────────────────────


def test_providers_endpoint_returns_registry_and_budget(client):
    response = client.get("/api/media/providers")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["providers"], list)
    assert isinstance(body["capabilities"], list)
    assert "run_budget" in body["budget"]
    assert body["budget"]["currency"]


def test_queue_endpoint_counts_oldest_jobs(client):
    store = media_api.store_for_project(client.project_dir)
    store.put_job(MediaJob(kind="cover", state="queued"))
    store.put_job(MediaJob(kind="episode", state="failed"))

    body = client.get("/api/media/queue").json()
    assert body["counts"] == {"failed": 1, "queued": 1}
    assert len(body["jobs"]) == 2
    assert {job["state"] for job in body["jobs"]} == {"failed", "queued"}


def test_queue_endpoint_is_empty_on_a_fresh_project(client):
    body = client.get("/api/media/queue").json()
    assert body == {"counts": {}, "jobs": []}


def test_create_and_fetch_episode(client):
    payload = {
        "title": "Channel M0",
        "format": "9:16",
        "shots": [{"shot_id": "s1", "kind": "title", "narration": "hello"}],
    }
    created = client.post("/api/media/episodes", json=payload)
    assert created.status_code == 200
    ir = created.json()
    assert ir["episode_id"]
    assert ir["title"] == "Channel M0"
    assert ir["format"] == "9:16"

    fetched = client.get(f"/api/media/episodes/{ir['episode_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == ir


def test_create_episode_rejects_an_invalid_payload(client):
    response = client.post("/api/media/episodes", json={"shots": "not-a-list"})
    assert response.status_code == 400
    assert response.json()["detail"]


def test_unknown_episode_is_404(client):
    assert client.get("/api/media/episodes/does-not-exist").status_code == 404


def test_asset_is_served_from_the_store(client):
    store = media_api.store_for_project(client.project_dir)
    asset = store.put_asset(b"cover-bytes", kind="image", mime="image/png", ext=".png")

    response = client.get(f"/api/media/assets/{asset.asset_id}")
    assert response.status_code == 200
    assert response.content == b"cover-bytes"


def test_asset_traversal_through_the_endpoint_is_rejected(client):
    response = client.get("/api/media/assets/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)
