"""Offline tests for the voice property of a profile and its resolution.

Covers:
* a profile without a voice serializes exactly as before (no ``voice`` key);
* a profile with a voice roundtrips through ``model_dump`` / ``model_validate``;
* ``enabled: False`` means silence and never falls back;
* profile -> project -> system precedence, including unknown profiles;
* ``voice_snapshot`` for ``EpisodeIR.voice_snapshot``.
"""

import json
from pathlib import Path

import pytest

from codx.junior.media.model import VoiceProfile
from codx.junior.media.voice import (
    DEFAULT_VOICE,
    PROJECT_STORE_KEY,
    ResolvedVoice,
    resolve_voice,
    voice_snapshot,
)
from codx.junior.model.profile import Profile

API_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_PROFILES = API_ROOT / "codx" / "junior" / "profiles"


def make_voice(**overrides) -> VoiceProfile:
    data = {
        "enabled": True,
        "provider": "piper",
        "voice_id": "en_US-amy",
        "model": "medium",
        "speed": 1.1,
        "style": "calm",
        "language": "en",
        "reference_asset": None,
    }
    data.update(overrides)
    return VoiceProfile(**data)


SPEAKER = Profile(name="speaker", voice=make_voice())
MUTED = Profile(name="muted", voice=make_voice(enabled=False))
SILENT_BY_DEFAULT = Profile(name="plain")


def test_profile_without_voice_dumps_without_voice_key():
    profile = Profile(name="no-voice")

    assert profile.voice is None
    dumped = profile.model_dump()
    assert "voice" not in dumped
    assert "voice" not in json.loads(profile.model_dump_json())
    # The rest of the payload is untouched
    assert dumped["name"] == "no-voice"


def test_profile_with_voice_roundtrips():
    profile = Profile(name="speaker", voice=make_voice())

    dumped = profile.model_dump()
    assert "voice" in dumped
    assert dumped["voice"]["voice_id"] == "en_US-amy"

    reloaded = Profile.model_validate(json.loads(json.dumps(dumped)))
    assert reloaded.voice == profile.voice
    assert reloaded == profile


def test_real_profile_file_still_loads_and_saves_unchanged():
    known = []
    for folder in (BUNDLED_PROFILES, API_ROOT.parent / ".codx" / "profiles"):
        if folder.is_dir():
            known.extend(sorted(folder.glob("*.profile")))
    if not known:
        pytest.skip("no .profile file available")

    for path in known[:5]:
        with open(path) as handle:
            data = json.load(handle)
        profile = Profile.model_validate(data)
        dumped = profile.model_dump()
        assert "voice" not in dumped, f"{path} should not gain a voice key"
        assert "voice" not in json.loads(profile.model_dump_json())


def test_disabled_profile_voice_is_silence_without_fallback():
    resolved = resolve_voice(
        "muted",
        profiles={"muted": MUTED},
        project_default=make_voice(voice_id="project-voice"),
    )

    assert isinstance(resolved, ResolvedVoice)
    assert resolved.source == "disabled"
    assert resolved.profile == "muted"
    assert resolved.voice.enabled is False
    assert resolved.voice.voice_id == "en_US-amy"
    assert "disabled" in resolved.reason


def test_disabled_profile_voice_never_uses_system_default():
    resolved = resolve_voice("muted", profiles=[MUTED])

    assert resolved.source == "disabled"
    assert resolved.voice != DEFAULT_VOICE


def test_resolution_precedence_profile_then_project_then_system():
    project_voice = make_voice(provider="local", voice_id="project-voice", model=None)
    profiles = {"speaker": SPEAKER}

    from_profile = resolve_voice(
        "speaker", profiles=profiles, project_default=project_voice
    )
    assert from_profile.source == "profile"
    assert from_profile.voice.voice_id == "en_US-amy"

    from_project = resolve_voice(
        "plain", profiles=profiles, project_default=project_voice
    )
    assert from_project.source == "project"
    assert from_project.voice.voice_id == "project-voice"
    assert from_project.reason == "inherited from project"

    from_system = resolve_voice("plain", profiles=profiles)
    assert from_system.source == "system"
    assert from_system.voice == DEFAULT_VOICE
    assert resolve_voice("plain", profiles=profiles, project_default=make_voice(enabled=False)).source == "system"


def test_project_default_accepts_a_mapping():
    project_voice = {"enabled": True, "provider": "local", "voice_id": "from-dict"}

    resolved = resolve_voice("plain", profiles={"plain": SILENT_BY_DEFAULT}, project_default=project_voice)

    assert resolved.source == "project"
    assert resolved.voice.voice_id == "from-dict"


def test_unknown_profile_falls_through_and_never_raises():
    project_voice = make_voice(voice_id="project-voice")

    resolved = resolve_voice(
        "ghost", profiles={"speaker": SPEAKER}, project_default=project_voice
    )
    assert resolved.source == "project"
    assert resolved.voice.voice_id == "project-voice"

    orphan = resolve_voice("ghost", profiles={"speaker": SPEAKER})
    assert orphan.source == "system"
    assert orphan.voice == DEFAULT_VOICE

    assert resolve_voice(None).source == "system"


def test_voice_snapshot_resolves_speaking_profiles_and_skips_disabled():
    project_voice = make_voice(provider="local", voice_id="project-voice", model=None)
    profiles = {"speaker": SPEAKER, "muted": MUTED, "plain": SILENT_BY_DEFAULT}

    snapshot = voice_snapshot(profiles, project_default=project_voice)

    assert set(snapshot) == {"speaker", "plain"}
    assert snapshot["speaker"].voice_id == "en_US-amy"
    assert snapshot["plain"].voice_id == "project-voice"
    assert all(isinstance(voice, VoiceProfile) for voice in snapshot.values())


def test_voice_snapshot_accepts_a_profile_list_and_is_serializable():
    snapshot = voice_snapshot([SPEAKER, MUTED])

    assert set(snapshot) == {"speaker"}
    assert snapshot["speaker"].voice_id == "en_US-amy"
    assert json.loads(json.dumps({k: v.model_dump() for k, v in snapshot.items()}))["speaker"]["provider"] == "piper"


def test_project_store_key():
    assert PROJECT_STORE_KEY == "project"
