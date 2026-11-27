"""
Tests for personal access tokens (creation, storage, scopes and verification).

Run with::

    cd api && pytest tests/security/test_pat.py -q
"""

import json
import os
import stat
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from codx.junior.security import pat


PROJECT_ID = "project-1"


@pytest.fixture()
def store(tmp_path):
    return pat.PATStore(path=str(tmp_path / "pat_tokens.json"))


def _token(store, **overrides):
    values = {
        "username": "alice",
        "name": "Support widget",
        "scopes": [pat.SCOPE_CHAT, pat.SCOPE_MODELS],
        "project_id": PROJECT_ID,
        "project_name": "Support",
        "profiles": ["support"],
        "expires_in_days": 30,
    }
    values.update(overrides)
    return store.create(**values)


# ---------------------------------------------------------------------------
# Creation and storage
# ---------------------------------------------------------------------------


def test_create_returns_the_secret_once_and_stores_only_a_digest(store, tmp_path):
    token, record = _token(store)

    assert token.startswith(pat.PAT_PREFIX)
    assert pat.parse_pat(token) is not None
    assert record.id in token
    assert record.token_hash != token
    assert record.token_hash == pat.token_digest(token)
    assert record.prefix.startswith(pat.PAT_PREFIX)
    assert len(record.prefix) < len(token)

    payload = (tmp_path / "pat_tokens.json").read_text(encoding="utf-8")
    assert token not in payload
    assert record.token_hash in payload

    listed = store.list("alice")
    assert [item.id for item in listed] == [record.id]
    assert "token_hash" not in listed[0].public_dict()


def test_token_file_is_owner_only(store):
    _token(store)
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600


def test_chat_scope_requires_a_project_binding(store):
    with pytest.raises(ValueError, match="project_id is required"):
        store.create(username="alice", scopes=[pat.SCOPE_CHAT])


def test_unknown_scope_is_rejected(store):
    with pytest.raises(ValueError, match="unknown scope"):
        store.create(username="alice", scopes=["root:everything"], project_id=PROJECT_ID)


def test_wildcard_scope_grants_everything(store):
    token, record = _token(store, scopes=[pat.SCOPE_WILDCARD])
    assert record.has_scope(pat.SCOPE_CHAT)
    assert record.has_scope(pat.SCOPE_PROJECTS)
    assert pat.PATStore(path=store.path).verify(token) is not None


def test_duplicate_scopes_and_profiles_are_deduplicated(store):
    _secret, record = _token(
        store,
        scopes=[pat.SCOPE_CHAT, pat.SCOPE_CHAT, pat.SCOPE_MODELS],
        profiles=["support", "support", "billing"],
    )
    assert record.scopes == [pat.SCOPE_CHAT, pat.SCOPE_MODELS]
    assert record.profiles == ["support", "billing"]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_accepts_the_created_token_and_updates_last_used(store):
    token, record = _token(store)
    verified = store.verify(token)
    assert verified is not None
    assert verified.id == record.id
    assert verified.last_used_at


def test_verify_rejects_a_tampered_secret(store):
    token, record = _token(store)
    token_id, _secret = pat.parse_pat(token)
    tampered = f"{pat.PAT_PREFIX}{token_id}_{'0' * 64}"
    assert store.verify(tampered) is None
    assert store.verify_with_reason(tampered)[1] == pat.PAT_INVALID


def test_verify_rejects_an_unknown_token(store):
    assert store.verify(f"{pat.PAT_PREFIX}{'a' * 16}_{'b' * 64}") is None
    assert store.verify("not-a-token") is None


def test_expired_token_is_rejected(store):
    token, record = _token(store, expires_in_days=1)
    # Move the expiry into the past without sleeping.
    records = store._load()
    for item in records:
        if item.id == record.id:
            item.expires_at = "2000-01-01T00:00:00Z"
    store._save(records)

    assert store.verify(token) is None
    assert store.verify_with_reason(token)[1] == pat.PAT_EXPIRED


def test_revoked_token_is_rejected(store):
    token, record = _token(store)
    assert store.revoke(record.id, username="alice") is True
    assert store.verify(token) is None
    assert store.verify_with_reason(token)[1] == pat.PAT_REVOKED


def test_revoke_is_owner_scoped(store):
    _secret, record = _token(store, username="alice")
    assert store.revoke(record.id, username="bob") is False
    assert store.find(record.id, username="bob") is None
    assert store.revoke(record.id, username="bob", allow_any=True) is True


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


def test_extract_pat_token_accepts_the_standard_and_api_key_headers():
    token = f"{pat.PAT_PREFIX}abc_def"
    assert pat.extract_pat_token(FakeRequest({"authorization": f"Bearer {token}"})) == token
    assert pat.extract_pat_token(FakeRequest({"x-api-key": token})) == token
    # A session JWT is not mistaken for a PAT.
    assert pat.extract_pat_token(FakeRequest({"authorization": "Bearer eyJhbGciOi"})) == ""


def test_get_principal_requires_a_token():
    with pytest.raises(HTTPException) as exc:
        pat.get_pat_principal(FakeRequest())
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "missing_credentials"


def test_get_principal_checks_scopes(store, monkeypatch):
    token, _record = _token(store, scopes=[pat.SCOPE_MODELS])
    monkeypatch.setattr(pat, "PATStore", lambda *args, **kwargs: store)

    # Avoid importing the real user store (it needs bcrypt/JWT and reads the
    # global settings): the dependency only needs ``find_user`` here.
    import sys
    import types

    class _Manager:
        def find_user(self, username=None):
            return SimpleNamespace(username=username, disabled=False, role="user")

    fake_user_management = types.ModuleType("codx.junior.security.user_management")
    fake_user_management.UserSecurityManager = _Manager
    monkeypatch.setitem(
        sys.modules, "codx.junior.security.user_management", fake_user_management
    )

    with pytest.raises(HTTPException) as exc:
        pat.get_pat_principal(
            FakeRequest({"authorization": f"Bearer {token}"}),
            required_scopes=[pat.SCOPE_CHAT],
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "insufficient_scope"

    principal = pat.get_pat_principal(
        FakeRequest({"authorization": f"Bearer {token}"}),
        required_scopes=[pat.SCOPE_MODELS],
    )
    assert principal.username == "alice"
    assert principal.project_id == PROJECT_ID


def test_store_is_reloadable_and_keeps_metadata(store):
    token, record = _token(store)
    fresh = pat.PATStore(path=store.path)
    loaded = fresh.verify(token)
    assert loaded is not None
    assert loaded.name == record.name
    assert loaded.project_id == PROJECT_ID
    assert loaded.profiles == ["support"]
    assert loaded.require_public_exposure_protection is True


def test_corrupt_store_is_treated_as_empty(tmp_path):
    path = tmp_path / "pat_tokens.json"
    path.write_text("{not json", encoding="utf-8")
    store = pat.PATStore(path=str(path))
    assert store.list() == []
    # A create repairs it.
    token, _record = _token(store)
    assert store.verify(token) is not None
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
