"""
Tests for effort definitions: storage, resolution and the profile link.

An effort is the named budget a profile is linked to
(``Profile.effort_name``). These tests cover the three pieces of the feature:

* :class:`~codx.junior.efforts.effort_manager.EffortManager` — seeding, CRUD,
  the guards (built-in / still linked) and the materialization into
  ``Profile.effort``;
* :func:`codx.junior.ai.smol.effort.effort_for` — resolving the tier from the
  associated effort and applying its values as overrides.

Made with ❤️ by codx-junior
"""
import json
import os

import pytest

from codx.junior.ai.smol.effort import (
    AGILE_NAME,
    MAX_NAME,
    agile_policy,
    effort_for,
)
from codx.junior.efforts.effort_manager import EffortManager
from codx.junior.model.effort import BUILTIN_EFFORTS, EffortDefinition, EffortValues
from codx.junior.model.profile import Profile


class SettingsStub:
    """Minimal project settings: the manager only needs ``codx_path``."""

    def __init__(self, codx_path: str):
        self.codx_path = codx_path


@pytest.fixture()
def manager(tmp_path):
    """An EffortManager over an empty project folder."""
    os.makedirs(os.path.join(tmp_path, "profiles"), exist_ok=True)
    return EffortManager(SettingsStub(str(tmp_path)))


def write_profile(manager: EffortManager, name: str, effort_name: str = None) -> str:
    """Create a profile file on disk, as ProfileManager would."""
    folder = os.path.join(manager.profiles_path, name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.profile")
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"name": name, "effort_name": effort_name, "effort": None}, file)
    return path


def test_builtins_are_seeded_with_the_tier_values(manager):
    """The first read seeds agile/max/default from the runtime policies."""
    efforts = manager.list_efforts()
    names = [effort.name for effort in efforts]

    assert set(names) == set(BUILTIN_EFFORTS)
    # Built-ins come first so the picker shows them at the top.
    assert names[: len(BUILTIN_EFFORTS)] == sorted(BUILTIN_EFFORTS)

    agile = manager.read_effort("agile", with_profiles=False)
    assert agile.builtin is True
    assert agile.values.max_iterations == agile_policy().max_iterations
    assert manager.read_effort("max", with_profiles=False).values.max_iterations > (
        agile.values.max_iterations
    )


def test_save_read_and_overrides(manager):
    """A saved effort keeps its values and exposes only the set ones."""
    definition = EffortDefinition(
        name="careful",
        description="Small steps, lots of verification",
        tier=MAX_NAME,
        values=EffortValues(max_iterations=7, validation="full"),
    )

    saved = manager.save_effort(definition)

    assert saved.name == "careful"
    assert saved.builtin is False
    assert saved.created_at and saved.updated_at
    assert saved.to_overrides() == {"max_iterations": 7, "validation": "full"}

    stored = manager.read_effort("careful", with_profiles=False)
    assert stored.description == "Small steps, lots of verification"
    assert stored.tier == MAX_NAME
    assert stored.values.max_iterations == 7


def test_invalid_name_or_tier_is_rejected(manager):
    """Names are folder names and tiers are a closed set."""
    with pytest.raises(ValueError):
        manager.save_effort(EffortDefinition(name="", values=EffortValues()))
    with pytest.raises(ValueError):
        manager.save_effort(EffortDefinition(name="bad name!", values=EffortValues()))
    with pytest.raises(ValueError):
        manager.save_effort(
            EffortDefinition(name="weird", tier="turbo", values=EffortValues())
        )


def test_builtin_cannot_be_deleted(manager):
    """The seeded efforts are editable but never deletable."""
    manager.ensure_builtins()
    with pytest.raises(ValueError):
        manager.delete_effort(AGILE_NAME)


def test_effort_in_use_cannot_be_deleted(manager):
    """An effort referenced by a profile is protected until it is unlinked."""
    manager.save_effort(EffortDefinition(name="careful", values=EffortValues()))
    path = write_profile(manager, "software_developer", effort_name="careful")

    assert manager.profiles_using("careful") == ["software_developer"]
    with pytest.raises(ValueError):
        manager.delete_effort("careful")

    write_profile(manager, "software_developer")
    assert manager.profiles_using("careful") == []
    assert manager.delete_effort("careful") is True
    assert manager.read_effort("careful", with_profiles=False) is None
    assert os.path.isfile(path)  # the profile is untouched


def test_saving_an_effort_refreshes_the_linked_profiles(manager):
    """Editing an effort pushes its values to the profiles linked to it."""
    manager.save_effort(
        EffortDefinition(name="careful", values=EffortValues(max_iterations=5))
    )
    path = write_profile(manager, "software_developer", effort_name="careful")

    manager.save_effort(
        EffortDefinition(name="careful", values=EffortValues(max_iterations=11))
    )

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    assert data["effort"] == {"max_iterations": 11}


def test_apply_to_profile_materializes_the_effort(manager):
    """``apply_to_profile`` turns the link into real profile overrides."""
    manager.save_effort(
        EffortDefinition(
            name="careful",
            values=EffortValues(max_iterations=7, continuations=2),
        )
    )

    profile = Profile(name="software_developer", effort_name="careful")
    manager.apply_to_profile(profile)

    assert profile.effort is not None
    assert profile.effort.max_iterations == 7
    assert profile.effort.continuations == 2

    # No link and no definition: the profile keeps its own values untouched.
    untouched = Profile(name="analyst")
    manager.apply_to_profile(untouched)
    assert untouched.effort is None

    missing = Profile(name="analyst", effort_name="does-not-exist")
    manager.apply_to_profile(missing)
    assert missing.effort is None


def test_effort_for_uses_the_associated_tier():
    """The associated effort wins over the tier inferred from profile names."""
    assert effort_for(profile_names=["codx-junior"]).name == AGILE_NAME
    assert effort_for(profile_names=["codx-junior"], effort_name=MAX_NAME).name == MAX_NAME
    assert effort_for(effort_name="agile").name == AGILE_NAME
    # An unknown effort name falls back to the profile names.
    assert effort_for(profile_names=["codx-junior"], effort_name="nope").name == AGILE_NAME


def test_effort_for_applies_the_definition_and_lets_the_profile_win():
    """Values apply on top of the tier; profile overrides beat the definition."""
    policy = effort_for(effort_values={"max_iterations": 3, "continuations": 4})

    assert policy.max_iterations == 3
    assert policy.continuations == 4

    overridden = effort_for(
        settings={"max_iterations": 9},
        effort_values={"max_iterations": 3},
    )
    assert overridden.max_iterations == 9


def test_field_docs_document_every_value():
    """The advanced editor explains every value, in a stable order."""
    docs = EffortManager.field_docs()
    keys = [field["key"] for field in docs["fields"]]

    assert docs["tiers"] == ["agile", "max", "default"]
    assert len(keys) == len(set(keys))
    for field in docs["fields"]:
        assert field["label"] and field["summary"] and field["effect"]
        assert field["group"] in docs["groups"]

    # Every value the model accepts is documented: no silent option.
    assert set(keys) == set(EffortValues.model_fields.keys())


# Made with ❤️ by codx-junior
