"""Browser login store: site sessions (cookies/localStorage) and credentials.

Loaded by path with an injected cipher, so the tests do not need the
``cryptography`` package or a real vault key (the production cipher is the
project's Fernet; the store only needs ``encrypt``/``decrypt``).

Run with pytest, or directly: ``python tests/browser/test_browser_store.py``.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = API_ROOT / "codx" / "junior" / "browser" / "store.py"


def _load():
    spec = importlib.util.spec_from_file_location("cxj_store", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cxj_store"] = module
    spec.loader.exec_module(module)
    return module


store_module = _load()


class XorCipher:
    """Deterministic stand-in for Fernet: never stores the plaintext bytes."""

    def __init__(self, key: int = 0x5A):
        self.key = key

    def _flip(self, data: bytes) -> bytes:
        return bytes(byte ^ self.key for byte in data)

    def encrypt(self, data: bytes) -> bytes:
        return self._flip(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._flip(token)


def _store(tmp_path):
    return store_module.BrowserStore(project_path=str(tmp_path), cipher=XorCipher())


STATE = {
    "cookies": [
        {
            "name": "li_at",
            "value": "super-secret-cookie",
            "domain": ".linkedin.com",
            "path": "/",
            "expires": 1893456000.0,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        {"name": "no-domain", "value": "x"},
        "junk",
    ],
    "origins": [
        {"origin": "https://www.linkedin.com", "localStorage": {"token": "abc", "theme": "dark"}},
        {"origin": "https://www.linkedin.com", "localStorage": {"theme": "light"}},
        {"origin": "file:///tmp/x", "localStorage": {"nope": "1"}},
        {"origin": "https://mail.google.com", "localStorage": {}},
    ],
}


def test_normalize_state_keeps_cookies_and_merges_origins():
    state = store_module.normalize_state(STATE)
    assert [cookie["name"] for cookie in state["cookies"]] == ["li_at"]
    assert state["cookies"][0]["httpOnly"] is True
    assert state["origins"] == [
        {"origin": "https://www.linkedin.com", "localStorage": {"token": "abc", "theme": "light"}}
    ]


def test_normalize_state_rejects_empty_and_oversized_payloads():
    try:
        store_module.normalize_state({"cookies": [], "origins": []})
    except store_module.StoreError as ex:
        assert ex.code == "empty_state"
    else:  # pragma: no cover
        raise AssertionError("expected StoreError")

    huge = {
        "cookies": [],
        "origins": [{
            "origin": "https://x.test",
            # Each value is capped at 64 KB by normalize_state; many keys push
            # the whole state over the store limit.
            "localStorage": {f"k{index}": "v" * (64 * 1024) for index in range(40)},
        }],
    }
    try:
        store_module.normalize_state(huge)
    except store_module.StoreError as ex:
        assert ex.code == "state_too_large"
    else:  # pragma: no cover
        raise AssertionError("expected StoreError")


def test_save_session_roundtrip_and_encrypted_payload(tmp_path):
    store = _store(tmp_path)
    meta = store.save_session("LinkedIn + Gmail", STATE, "alice")

    assert meta["name"] == "LinkedIn + Gmail"
    assert meta["cookie_count"] == 1
    assert meta["origin_count"] == 1
    assert meta["created_by"] == "alice"
    assert store.sessions() == [meta]

    stored_meta, state = store.session_state(meta["id"])
    assert stored_meta["name"] == "LinkedIn + Gmail"
    assert state["cookies"][0]["value"] == "super-secret-cookie"
    assert state["origins"][0]["localStorage"] == {"token": "abc", "theme": "light"}

    # The cipher token (and the index) never contain the cookie value.
    payload = (tmp_path / ".codx" / "browser_store" / "sessions" / f"{meta['id']}.enc").read_bytes()
    assert b"super-secret-cookie" not in payload
    index = (tmp_path / ".codx" / "browser_store" / "index.json").read_text()
    assert "super-secret-cookie" not in index


def test_saving_the_same_name_updates_in_place(tmp_path):
    store = _store(tmp_path)
    first = store.save_session("Work", STATE)
    updated_state = {
        "cookies": [{"name": "a", "value": "b", "domain": ".x.test"}],
        "origins": [{"origin": "https://x.test", "localStorage": {"k": "v"}}],
    }
    second = store.save_session("work", updated_state)

    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["cookie_count"] == 1
    assert len(store.sessions()) == 1


def test_delete_session_removes_file_and_entry(tmp_path):
    store = _store(tmp_path)
    meta = store.save_session("Temp", STATE)
    assert store.delete_session(meta["name"]) is True
    assert store.sessions() == []
    assert not (tmp_path / ".codx" / "browser_store" / "sessions" / f"{meta['id']}.enc").exists()
    assert store.delete_session("missing") is False


def test_credentials_never_expose_the_password_in_metadata(tmp_path):
    store = _store(tmp_path)
    meta = store.save_credential(
        "LinkedIn", "alice@example.com", "hunter2", "https://www.linkedin.com/login", "alice"
    )

    assert "password" not in meta
    listed = store.credentials()
    assert listed == [meta]
    assert "hunter2" not in json.dumps(listed)

    found_meta, password = store.credential("linkedin")
    assert found_meta["username"] == "alice@example.com"
    assert password == "hunter2"

    payload = (tmp_path / ".codx" / "browser_store" / "credentials" / f"{meta['id']}.enc").read_bytes()
    assert b"hunter2" not in payload


def test_update_and_delete_a_credential(tmp_path):
    store = _store(tmp_path)
    meta = store.save_credential("GitHub", "", "old-pass")

    updated = store.update_credential(meta["id"], username="alice", password="new-pass")
    assert updated["username"] == "alice"
    assert store.credential(meta["id"])[1] == "new-pass"

    renamed = store.update_credential(meta["id"], name="GitHub work")
    assert renamed["name"] == "GitHub work"
    assert store.delete_credential("GitHub work") is True
    assert store.credentials() == []


def test_credential_names_are_unique_and_resolvable(tmp_path):
    store = _store(tmp_path)
    first = store.save_credential("Mail", "a@b.c", "p1")
    duplicate = store.save_credential("mail", "a@b.c", "p2")
    assert duplicate["id"] == first["id"], "same name replaces the credential"

    other = store.save_credential("Other", "", "p3")
    try:
        store.update_credential(other["id"], name="Mail")
    except store_module.StoreError as ex:
        assert ex.code == "duplicate_name"
    else:  # pragma: no cover
        raise AssertionError("expected duplicate_name")

    by_id = store.credential(other["id"])[0]
    assert by_id["name"] == "Other"
    try:
        store.credential("does-not-exist")
    except store_module.StoreError as ex:
        assert ex.code == "not_found"
    else:  # pragma: no cover
        raise AssertionError("expected not_found")


def test_store_files_are_private(tmp_path):
    store = _store(tmp_path)
    store.save_session("Work", STATE)
    store.save_credential("Mail", "", "pw")
    root = tmp_path / ".codx" / "browser_store"
    for path in [root / "index.json", *root.glob("sessions/*.enc"), *root.glob("credentials/*.enc")]:
        assert (path.stat().st_mode & 0o777) == 0o600, path


def test_audit_rows_record_names_not_values(tmp_path):
    store = _store(tmp_path)
    meta = store.save_session("Work", STATE, "alice")
    store.session_state(meta["id"], "alice")
    store.save_credential("Mail", "", "pw")
    store.credential("Mail", "alice")
    rows = [
        json.loads(line)
        for line in (tmp_path / ".codx" / "browser_store" / "audit.jsonl").read_text().splitlines()
    ]
    assert {row["action"] for row in rows} >= {"save", "apply", "reveal"}
    assert all(row["kind"] in ("session", "credential") for row in rows)
    text = json.dumps(rows)
    assert "super-secret-cookie" not in text and "pw" not in text


def test_store_needs_a_project_path():
    try:
        store_module.BrowserStore(settings=None)
    except store_module.StoreError as ex:
        assert ex.code == "no_project"
    else:  # pragma: no cover
        raise AssertionError("expected no_project")


def test_suggest_name_from_origins():
    suggest = store_module.suggest_name
    assert suggest([]) == ""
    assert suggest(["https://www.linkedin.com"]) == "linkedin.com"
    assert suggest(["https://www.linkedin.com", "https://mail.google.com"]) == "linkedin.com + mail.google.com"
    assert suggest([
        "https://www.linkedin.com", "https://mail.google.com", "https://github.com",
        "https://www.linkedin.com",
    ]) == "linkedin.com + 2 more"
    assert suggest(["not a url"]) == ""


def test_preview_endpoint_exists():
    api = (API_ROOT / "codx" / "junior" / "api" / "browser_store.py").read_text()
    assert '@router.get(' in api and '"/preview"' in api
    assert "suggest_name(origins)" in api
    assert "page_count" in api


def test_tools_api_and_runtime_are_wired():
    tools = (API_ROOT / "codx" / "junior" / "tools" / "browser_tools.py").read_text()
    for action in ("save_session", "restore_session", "list_sessions", "login"):
        assert f'"{action}"' in tools, action
    assert 'runtime.run("export_state")' in tools
    assert 'runtime.run("import_state", state)' in tools

    api = (API_ROOT / "codx" / "junior" / "api" / "browser_store.py").read_text()
    assert 'router = APIRouter(prefix="/browser-store"' in api
    assert '"/sessions/{store_id}/apply"' in api
    assert '"/credentials/{credential_id}/reveal"' in api

    runtime = (API_ROOT / "codx" / "junior" / "browser" / "browser.py").read_text()
    for method in ("async def export_state", "async def import_state", "async def login"):
        assert method in runtime, method


if __name__ == "__main__":  # pragma: no cover - ad-hoc runner
    import inspect
    import tempfile

    passed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        if inspect.signature(function).parameters:
            with tempfile.TemporaryDirectory() as folder:
                function(Path(folder))
        else:
            function()
        passed += 1
        print("ok -", name)
    print(f"\n{passed} browser store tests passed")
