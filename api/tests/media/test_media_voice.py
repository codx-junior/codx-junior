"""Offline tests for media voice resolution (profile / project / system / disabled)."""

import json

import pytest

from codx.junior.media.model import VoiceProfile
from codx.junior.media.voice import (
    DEFAULT_VOICE,
    PROJECT_STORE_KEY,
    ResolvedVoice,
    resolve_voice,
    voice_snapshot,
)


class FakeProfile:
    """Minimal profile stand-in: ``resolve_voice`` only reads name + voice."""

    def __init__(self, name, voice=None):
        self.name = name
        self.voice = voice


def make_voice(**kwargs):
    """Build a voice from the system default so unknown fields never leak in."""
    return DEFAULT_VOICE.model_copy(deep=True, update=kwargs)


def test_resolve_voice_uses_the_profile_voice():
    profile_voice = make_voice(provider="local-tts", voice_id="alice", speed=1.2)
    profiles = {"alice": FakeProfile("alice", profile_voice)}

    resolved = resolve_voice(
        "alice", profiles=profiles, project_default=make_voice(voice_id="project")
    )

    assert isinstance(resolved, ResolvedVoice)
    assert resolved.source == "profile"
    assert resolved.profile == "alice"
    assert resolved.voice.voice_id == "alice"
    assert resolved.voice.speed == pytest.approx(1.2)
    assert "alice" in resolved.reason


def test_resolve_voice_falls_back_to_the_project_voice():
    project_voice = make_voice(provider="local-tts", voice_id="project-voice")
    profiles = {"alice": FakeProfile("alice", None)}

    resolved = resolve_voice("alice", profiles=profiles, project_default=project_voice)

    assert resolved.source == "project"
    assert resolved.profile == "alice"
    assert resolved.voice.voice_id == "project-voice"
    assert resolved.reason == "inherited from project"

    # The project default is also accepted as a plain mapping
    as_mapping = resolve_voice(
        "alice", profiles=profiles, project_default={"enabled": True, "voice_id": "mapped"}
    )
    assert as_mapping.source == "project"
    assert as_mapping.voice.voice_id == "mapped"

    # An unknown profile is not an error: it resolves through the project too
    unknown = resolve_voice("bob", profiles=profiles, project_default=project_voice)
    assert unknown.source == "project"
    assert unknown.profile == "bob"


def test_resolve_voice_falls_back_to_the_system_default():
    # No profile and no project voice at all
    resolved = resolve_voice("alice", profiles={}, project_default=None)
    assert resolved.source == "system"
    assert resolved.profile == "alice"
    assert resolved.voice.voice_id == DEFAULT_VOICE.voice_id
    assert resolved.voice.provider == DEFAULT_VOICE.provider
    assert "system default" in resolved.reason

    # Defaults of the signature: no arguments at all
    bare = resolve_voice()
    assert bare.source == "system"
    assert bare.profile is None

    # A disabled project voice is ignored and resolution drops to the system one
    disabled_project = make_voice(voice_id="muted-project", enabled=False)
    resolved = resolve_voice(
        "alice", profiles={"alice": FakeProfile("alice")}, project_default=disabled_project
    )
    assert resolved.source == "system"
    assert resolved.voice.voice_id == DEFAULT_VOICE.voice_id
    assert PROJECT_STORE_KEY == "project"


def test_disabled_profile_voice_is_silence_without_fallback():
    silence = make_voice(provider="local-tts", voice_id="alice", enabled=False)
    profiles = {"alice": FakeProfile("alice", silence)}
    project_voice = make_voice(voice_id="project-voice")

    resolved = resolve_voice("alice", profiles=profiles, project_default=project_voice)

    assert resolved.source == "disabled"
    assert resolved.profile == "alice"
    assert resolved.voice.enabled is False
    assert resolved.voice.voice_id == "alice"
    assert "disabled" in resolved.reason
    # No fallback: neither the project nor the system voice leaks in
    assert resolved.voice.voice_id != project_voice.voice_id
    assert resolved.voice.voice_id != DEFAULT_VOICE.voice_id

    # And it is never overridden even without a project voice
    no_project = resolve_voice("alice", profiles=profiles, project_default=None)
    assert no_project.source == "disabled"
    assert no_project.voice.enabled is False


def test_voice_snapshot_serializes_plain_json():
    profiles = {
        "alice": FakeProfile("alice", make_voice(provider="local-tts", voice_id="alice")),
        "bob": FakeProfile("bob", None),
        "muted": FakeProfile("muted", make_voice(voice_id="muted", enabled=False)),
    }

    snapshot = voice_snapshot(profiles, project_default=make_voice(voice_id="project-voice"))

    # Speaking profiles only: the disabled one must not leak a fallback voice
    assert set(snapshot) == {"alice", "bob"}
    assert snapshot["alice"].voice_id == "alice"
    assert snapshot["bob"].voice_id == "project-voice"
    assert all(isinstance(voice, VoiceProfile) for voice in snapshot.values())

    # Plain json: survives model_dump(json) and a json.dumps roundtrip
    payload = {name: voice.model_dump(mode="json") for name, voice in snapshot.items()}
    assert json.loads(json.dumps(payload))["alice"]["voice_id"] == "alice"
    assert isinstance(payload["bob"]["enabled"], bool)

    # No profiles at all -> empty snapshot (nothing to speak)
    assert voice_snapshot() == {}
