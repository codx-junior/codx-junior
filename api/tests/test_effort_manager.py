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
    MAX_VALIDATION,
    agile_policy,
    effort_for,
    max_policy,
)
from codx.junior.efforts.effort_manager import EffortManager
from codx.junior.model.effort import (
    BUILTIN_DESCRIPTIONS,
    BUILTIN_EFFORTS,
    BUILTIN_REVISION,
    EffortDefinition,
    EffortValues,
)
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
def test_max_tier_does_not_inherit_agile():
    """codx-junior-max is a waterfall/long-horizon method, not a bigger agile."""
    agile = agile_policy()
    maximum = max_policy()

    # Bigger budget on every axis.
    assert maximum.max_iterations > agile.max_iterations
    assert maximum.max_tool_calls > agile.max_tool_calls
    assert maximum.max_tool_rounds > agile.max_tool_rounds

    # Its own course corrections: a limit must not collapse a phased plan.
    assert maximum.continuations > agile.continuations
    assert maximum.continuation_max_iterations > agile.continuation_max_iterations
    assert maximum.continuation_max_calls > agile.continuation_max_calls

    # Long horizon: a much later promise, still advisory (nothing stops it).
    assert maximum.target_seconds > agile.target_seconds
    assert maximum.wall_clock_seconds > agile.wall_clock_seconds
    assert maximum.stop_on_wall_clock is False

    # The heaviest validation breadth is the one of max.
    assert maximum.validation == MAX_VALIDATION


def test_builtins_are_refreshed_when_the_revision_changes(manager):
    """A built-in is owned by codx-junior: a bumped revision refreshes it."""
    manager.ensure_builtins()
    path = manager.definition_path("max")
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)

    # Simulate a definition written by an older release.
    payload["revision"] = BUILTIN_REVISION - 1
    payload["description"] = "old description"
    payload["values"]["max_iterations"] = 1
    created_at = payload["created_at"]
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file)

    manager.ensure_builtins()

    refreshed = manager.read_effort("max", with_profiles=False)
    assert refreshed.revision == BUILTIN_REVISION
    assert refreshed.description == BUILTIN_DESCRIPTIONS["max"]
    assert refreshed.values.max_iterations == max_policy().max_iterations
    # Refreshing is not a new effort: the creation stamp is preserved.
    assert refreshed.created_at == created_at


def test_save_effort_keeps_a_customized_builtin(manager):
    """An edited built-in is not clobbered by the next seed."""
    manager.ensure_builtins()
    definition = manager.read_effort("max", with_profiles=False)
    definition.description = "my tuning"
    definition.values.max_iterations = 7
    definition.revision = 0  # the editor does not send the revision back

    manager.save_effort(definition)
    manager.ensure_builtins()

    kept = manager.read_effort("max", with_profiles=False)
    assert kept.revision == BUILTIN_REVISION
    assert kept.description == "my tuning"
    assert kept.values.max_iterations == 7


# Made with ❤️ by codx-junior
