"""
FastAPI-level tests for the PAT authentication dependency.

The public router is mounted in a throwaway app so the test exercises the real
dependency, headers and error bodies without booting the whole codx-junior
engine.

Run with::

    cd api && pytest tests/test_pat_auth_dependency.py -q
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.security import pat


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "codx"
    / "junior"
    / "api"
    / "chatGPTLikeApi.py"
)
_spec = importlib.util.spec_from_file_location("public_api_under_test", _MODULE_PATH)
public_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(public_api)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = pat.PATStore(path=str(tmp_path / "pat_tokens.json"))
    monkeypatch.setattr(pat, "PATStore", lambda *args, **kwargs: store)

    class _Manager:
        def find_user(self, username=None):
            return types.SimpleNamespace(username=username, disabled=False, role="user")

    fake_user_management = types.ModuleType("codx.junior.security.user_management")
    fake_user_management.UserSecurityManager = _Manager
    monkeypatch.setitem(
        sys.modules, "codx.junior.security.user_management", fake_user_management
    )

    async def no_entries(principal, **kwargs):
        return []

    monkeypatch.setattr(public_api, "_available_entries", no_entries)

    app = FastAPI()
    app.include_router(public_api.router)
    return TestClient(app), store


def _create(store, scopes):
    token, _record = store.create(
        username="alice",
        name="Support widget",
        scopes=scopes,
        project_id="project-1",
        project_name="ACME",
    )
    return token


def test_models_requires_a_pat(client):
    http, _store = client
    response = http.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_credentials"


def test_models_accepts_a_valid_pat(client):
    http, store = client
    response = http.get(
        "/v1/models", headers={"Authorization": f"Bearer {_create(store, [pat.SCOPE_MODELS])}"}
    )
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


def test_models_rejects_a_token_without_the_scope(client):
    http, store = client
    token = _create(store, [pat.SCOPE_PROFILES])
    response = http.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_scope"


def test_invalid_pat_is_a_401_not_a_500(client):
    http, _store = client
    response = http.get(
        "/v1/models", headers={"Authorization": "Bearer cxj_pat_deadbeef_not-a-real-secret"}
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_revoked_pat_is_rejected(client):
    http, store = client
    token = _create(store, [pat.SCOPE_MODELS])
    token_id, _secret = pat.parse_pat(token)
    store.revoke(token_id, username="alice")
    response = http.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_me_returns_public_metadata_only(client):
    http, store = client
    token = _create(store, [pat.SCOPE_MODELS])
    response = http.get("/v1/me", headers={"x-api-key": token})
    assert response.status_code == 200
    body = response.json()
    assert body["prefix"].startswith(pat.PAT_PREFIX)
    assert "token_hash" not in body
    assert token not in str(body)


def test_session_style_token_is_not_accepted_as_a_pat(client):
    http, _store = client
    # A JWT-looking bearer is not a PAT: the public endpoints never inherit the
    # owner's browser session.
    response = http.get("/v1/models", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.x.y"})
    assert response.status_code == 401
