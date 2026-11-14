"""Offline tests of the profile voice API (resolve + preview).

No server, no session state, no network and no TTS engine: the tests check the
declared routes, the resolution contract (``profile`` / ``project`` / ``system``
/ ``disabled``) and that a preview without a speech provider answers 503 instead
of returning fake audio. The profile loader is patched, so the endpoints are
exercised on in-memory profiles; only the project store is real (``tmp_path``).
"""

import json
from pathlib import Path
from typing import Dict, Set, Tuple

import pytest
from fastapi import FastAPI, HTTPException

from codx.junior.api import profile_voice as profile_voice_api
from codx.junior.media import providers as media_providers
from codx.junior.media.model import ProviderInfo, VoiceProfile
from codx.junior.media.providers import ProviderRegistry
from codx.junior.media.store import MediaStore
from codx.junior.media.voice import PROJECT_STORE_KEY
from codx.junior.model.profile import Profile

EXPECTED_ROUTES = {
    ("GET", "/profiles/{profile_name}/voice"),
    ("POST", "/profiles/{profile_name}/voice/preview"),
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


def make_voice(**overrides) -> VoiceProfile:
    data = {
        "enabled": True,
        "provider": "local",
        "voice_id": "voice-1",
        "model": None,
        "speed": 1.0,
    }
    data.update(overrides)
    return VoiceProfile(**data)


class _FakeSettings:
    def __init__(self, project_path: Path) -> None:
        self.abs_project_path = str(project_path)


class _FakeSession:
    """Minimal stand-in for CODXJuniorSession: only the project path is used."""

    def __init__(self, project_path: Path) -> None:
        self.settings = _FakeSettings(project_path)


class _FakeTtsProvider:
    """In-memory speech provider, enough to prove the endpoint is vendor free."""

    def __init__(self, name: str = "fake-tts") -> None:
        self._name = name

    def info(self) -> ProviderInfo:
        return ProviderInfo(name=self._name, capability="voice", available=True, local=True)

    def available(self) -> bool:
        return True

    def estimate(self, request: dict) -> float:
        return 0.0

    def run(self, request: dict) -> dict:
        return {"mime": "audio/wav", "bytes": 0, "text": request.get("text")}


@pytest.fixture
def profiles(monkeypatch) -> Dict[str, Profile]:
    """Patch the profile loader so the API works on in-memory profiles."""
    store: Dict[str, Profile] = {}

    def load_profile(session, profile_name):
        profile = store.get(profile_name)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Unknown profile '{profile_name}'")
        return profile

    monkeypatch.setattr(profile_voice_api, "load_profile", load_profile)
    return store


@pytest.fixture
def client(tmp_path, profiles):
    """TestClient of the voice router with the project session overridden."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(profile_voice_api.router, prefix="/api")
    app.dependency_overrides[profile_voice_api.get_current_session] = lambda: _FakeSession(
        tmp_path
    )
    with TestClient(app) as test_client:
        test_client.project_dir = tmp_path
        test_client.profiles = profiles
        yield test_client


@pytest.fixture(autouse=True)
def empty_provider_registry():
    """No speech provider by default: M0 ships no TTS engine."""
    with media_providers.use_registry(ProviderRegistry()):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Declared surface
# ─────────────────────────────────────────────────────────────────────────────


def test_router_prefix_and_tags():
    assert profile_voice_api.router.prefix == "/profiles"
    assert "profiles" in profile_voice_api.router.tags


def test_router_declares_expected_endpoints():
    assert declared_routes(profile_voice_api.router) == EXPECTED_ROUTES


def test_every_endpoint_requires_the_project_session():
    for route in profile_voice_api.router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        assert "get_current_session" in dependency_names(dependant), route.path


# ─────────────────────────────────────────────────────────────────────────────
# Resolution contract
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_reports_the_voice_declared_by_the_profile(client):
    client.profiles["speaker"] = Profile(name="speaker", voice=make_voice(voice_id="v-speaker"))

    response = client.get("/api/profiles/speaker/voice")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "profile"
    assert body["profile"] == "speaker"
    assert body["voice"]["voice_id"] == "v-speaker"
    assert body["voice"]["enabled"] is True
    assert "speaker" in body["reason"]


def test_resolve_reports_a_disabled_voice_without_fallback(client):
    client.profiles["muted"] = Profile(name="muted", voice=make_voice(enabled=False))

    body = client.get("/api/profiles/muted/voice").json()
    assert body["source"] == "disabled"
    assert body["voice"]["enabled"] is False
    assert "no fallback" in body["reason"]


def test_resolve_falls_back_to_the_system_voice(client):
    client.profiles["plain"] = Profile(name="plain")

    body = client.get("/api/profiles/plain/voice").json()
    assert body["source"] == "system"
    assert body["voice"]["provider"] == "local"
    assert body["profile"] == "plain"


def test_resolve_inherits_the_project_default_voice(client):
    store = MediaStore.for_project(client.project_dir)
    settings_path = store.root / profile_voice_api.SETTINGS_FILENAME
    settings_path.write_text(
        json.dumps({PROJECT_STORE_KEY: {"enabled": True, "provider": "local", "voice_id": "v-project"}}),
        encoding="utf-8",
    )
    client.profiles["plain"] = Profile(name="plain")

    body = client.get("/api/profiles/plain/voice").json()
    assert body["source"] == "project"
    assert body["voice"]["voice_id"] == "v-project"


def test_resolve_reports_the_system_source_when_the_store_has_no_settings(client):
    client.profiles["plain"] = Profile(name="plain")
    assert profile_voice_api.project_default_voice(
        MediaStore.for_project(client.project_dir)
    ) is None
    assert client.get("/api/profiles/plain/voice").json()["source"] == "system"


def test_unknown_profile_is_404(client):
    assert client.get("/api/profiles/nobody/voice").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Preview contract
# ─────────────────────────────────────────────────────────────────────────────


def test_preview_answers_503_without_a_tts_provider(client):
    client.profiles["speaker"] = Profile(name="speaker", voice=make_voice())

    response = client.post("/api/profiles/speaker/voice/preview")
    assert response.status_code == 503
    body = response.json()
    assert body["reason"] == "no tts provider"
    assert body["voice"]["voice_id"] == "voice-1"
    assert body["provider"] is None
    # No fake/silent audio is ever produced without an engine
    assert "sample" not in body


def test_preview_is_409_for_a_disabled_profile(client):
    client.profiles["muted"] = Profile(name="muted", voice=make_voice(enabled=False))

    response = client.post("/api/profiles/muted/voice/preview")
    assert response.status_code == 409
    assert "disabled" in response.json()["reason"]


def test_preview_uses_a_registered_voice_provider(client):
    client.profiles["speaker"] = Profile(name="speaker", voice=make_voice())
    registry = ProviderRegistry()
    registry.register(_FakeTtsProvider())

    with media_providers.use_registry(registry):
        response = client.post("/api/profiles/speaker/voice/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake-tts"
    assert body["sample"]["mime"] == "audio/wav"
    assert body["sample"]["text"] == profile_voice_api.PREVIEW_SAMPLE


def test_preview_unknown_profile_is_404(client):
    assert client.post("/api/profiles/nobody/voice/preview").status_code == 404
