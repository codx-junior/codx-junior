"""Offline tests of the ``voice`` field of the profile tools (create/update).

No project, no session, no network: a tiny manager stands in for
:class:`~codx.junior.profiles.profile_manager.ProfileManager` and records what
the tool saves. Covers:

* the three write schemas expose the optional ``voice`` object with exactly the
  ``VoiceProfile`` fields, and never require it;
* ``_normalize_voice`` accepts those fields and rejects unknown ones;
* ``create`` / ``update`` persist the voice;
* an update that omits the voice leaves the stored profile untouched: no
  ``voice`` key is added to a profile that had none, and an existing voice is
  preserved.
"""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codx.junior.media.model import VoiceProfile
from codx.junior.model.profile import Profile
from codx.junior.tools import profile_tools

VOICE_FIELDS = {
    "enabled",
    "provider",
    "voice_id",
    "model",
    "speed",
    "style",
    "language",
    "reference_asset",
}


def make_voice(**overrides):
    """A complete voice mapping, as the model sends it in the tool arguments."""
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
    return data


class FakeManager:
    """Minimal ProfileManager: one project profile, persisted like the real one."""

    def __init__(self, root: Path, profile: Profile) -> None:
        self.profiles_path = str(root)
        self.settings = SimpleNamespace(
            project_id="test-project",
            abs_project_path=str(root.parent),
        )
        self.profile = profile
        self.saved = []

    def list_profiles(self):
        return [self.profile]

    def list_all_profiles(self):
        return [self.profile]

    def save_profile(self, profile: Profile):
        folder = Path(self.profiles_path) / profile.name
        folder.mkdir(parents=True, exist_ok=True)
        payload = profile.model_dump()
        payload["content"] = None
        payload["parsed_content"] = None
        (folder / f"{profile.name}.profile").write_text(json.dumps(payload, indent=2))
        self.profile = profile
        self.saved.append(profile)

    def delete_profile(self, name: str):
        raise AssertionError("delete is out of scope for these tests")

    def stored(self, name: str = "speaker"):
        path = Path(self.profiles_path) / name / f"{name}.profile"
        return json.loads(path.read_text())


@pytest.fixture
def manager(tmp_path):
    return FakeManager(tmp_path / "profiles", Profile(name="speaker"))


# ---------------------------------------------------------------------------
# Schema surface
# ---------------------------------------------------------------------------


def test_write_schemas_expose_the_voice_object():
    schemas = (
        profile_tools.PROFILE_MANAGEMENT_TOOL_JSON,
        profile_tools.PROFILE_CREATE_TOOL_JSON,
        profile_tools.PROFILE_UPDATE_TOOL_JSON,
    )
    for schema in schemas:
        parameters = schema["function"]["parameters"]
        assert "voice" in parameters["properties"], schema["function"]["name"]
        assert set(parameters["properties"]["voice"]["properties"]) == VOICE_FIELDS
        # Optional and backward compatible: never required.
        assert "voice" not in parameters.get("required", [])


def test_tool_functions_accept_the_voice_argument():
    for function in (
        profile_tools.profile_management,
        profile_tools.create_profile,
        profile_tools.update_profile,
    ):
        parameter = inspect.signature(function).parameters.get("voice")
        assert parameter is not None, function.__name__
        assert parameter.default is None, function.__name__


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_voice_accepts_every_field():
    voice = profile_tools._normalize_voice(make_voice())

    assert isinstance(voice, VoiceProfile)
    assert voice.voice_id == "en_US-amy"
    assert voice.speed == 1.1
    assert profile_tools._normalize_voice(None) is None


def test_normalize_voice_rejects_unknown_fields_and_non_objects():
    with pytest.raises(ValueError) as error:
        profile_tools._normalize_voice({"vodice_id": "typo"})
    assert "vodice_id" in str(error.value)

    with pytest.raises(ValueError):
        profile_tools._normalize_voice("piper")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_create_persists_the_voice(manager):
    response = profile_tools._action_create(
        manager=manager, name="narrator", content="# Narrator", voice=make_voice()
    )

    assert manager.saved[-1].voice.voice_id == "en_US-amy"
    assert "Voice:" in response.user_response
    assert json.loads(response.llm_response)["profile"]["voice"]["voice_id"] == "en_US-amy"


def test_create_without_voice_writes_no_voice_key(manager):
    profile_tools._action_create(manager=manager, name="narrator", content="# Narrator")

    assert manager.saved[-1].voice is None
    assert "voice" not in manager.stored("narrator")


def test_update_persists_the_voice(manager):
    response = profile_tools._action_update(
        manager=manager, name="speaker", voice=make_voice(voice_id="voice-2")
    )

    assert manager.saved[-1].voice.voice_id == "voice-2"
    payload = json.loads(response.llm_response)
    assert payload["changed_fields"] == ["voice"]
    assert manager.stored()["voice"]["voice_id"] == "voice-2"


def test_update_without_voice_adds_no_voice_key(manager):
    profile_tools._action_update(manager=manager, name="speaker", description="Narrates.")

    stored = manager.stored()
    assert "voice" not in stored
    assert stored["description"] == "Narrates."


def test_update_without_voice_keeps_an_existing_voice(manager):
    profile_tools._action_update(manager=manager, name="speaker", voice=make_voice())
    profile_tools._action_update(manager=manager, name="speaker", tags=["narration"])

    assert manager.stored()["voice"]["voice_id"] == "en_US-amy"


def test_update_disables_the_voice(manager):
    profile_tools._action_update(manager=manager, name="speaker", voice={"enabled": False})

    assert manager.stored()["voice"]["enabled"] is False
