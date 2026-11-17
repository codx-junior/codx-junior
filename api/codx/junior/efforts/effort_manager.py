"""
Effort storage and resolution for codx-junior.

An **effort** is a named, editable budget that profiles are linked to. The team
view only links a profile to an effort; the values are edited in Global Settings
where each one explains what it changes in the profile's work.

Storage layout (one folder per effort, mirroring ``.codx/profiles`` and
``.codx/teams``)::

    .codx/efforts/<Name>/<Name>.effort   # JSON definition (name, description, values)

Resolution
----------
``effort_for`` (the runtime policy) already accepts a ``settings`` mapping of
overrides. This module produces that mapping from the effort a profile is
linked to, and materializes it into ``Profile.effort`` when a profile is saved,
so every consumer of the profile effort keeps working unchanged.

The three built-in efforts (``agile``, ``max``, ``default``) are seeded from the
hardcoded tiers the first time they are read: a project that never touches this
feature behaves exactly as before.

Made with ❤️ by codx-junior
"""

import glob
import json
import logging
import os
import re
import shutil

from datetime import datetime
from typing import Any, Dict, List, Optional

from codx.junior.ai.smol.effort import (
    AGILE_NAME,
    DEFAULT_NAME,
    MAX_NAME,
    EffortPolicy,
    agile_policy,
    default_policy,
    max_policy,
)
from codx.junior.model.effort import (
    BUILTIN_DESCRIPTIONS,
    BUILTIN_EFFORTS,
    EFFORT_FIELD_DOCS,
    EFFORT_FIELD_GROUPS,
    EFFORT_TIERS,
    EffortDefinition,
    EffortValues,
)

logger = logging.getLogger(__name__)

#: Extension of the JSON definition (the charter pattern uses ``.md``).
EFFORT_EXTENSION = ".effort"

#: A name is also the folder name, so it must be filesystem friendly.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

#: Base tier factories, in the order they are documented.
_TIER_FACTORIES = {
    AGILE_NAME: agile_policy,
    MAX_NAME: max_policy,
    DEFAULT_NAME: default_policy,
}


def _now() -> str:
    """Return the current timestamp in ISO format (UTC naive)."""
    return datetime.utcnow().replace(microsecond=0).isoformat()


class EffortManager:
    """
    CRUD and resolution for the efforts of a project.

    Attributes:
        settings: Project settings.
        efforts_path: Absolute path of the project's ``.codx/efforts`` folder.
    """

    def __init__(self, settings: Any):
        """
        Initialize the manager.

        Args:
            settings: Project settings (anything exposing ``codx_path``).
        """
        self.settings = settings
        self.efforts_path = f"{settings.codx_path}/efforts"
        os.makedirs(self.efforts_path, exist_ok=True)
        self.profiles_path = f"{settings.codx_path}/profiles"

    # ── Paths ────────────────────────────────────────────────────────────────

    def effort_folder(self, name: str) -> str:
        """Absolute folder of an effort."""
        return os.path.join(self.efforts_path, name)

    def definition_path(self, name: str) -> str:
        """Absolute path of an effort definition (``<Name>.effort``)."""
        return os.path.join(self.effort_folder(name), f"{name}{EFFORT_EXTENSION}")

    # ── Seeding ──────────────────────────────────────────────────────────────

    @staticmethod
    def _values_from_policy(policy: EffortPolicy) -> EffortValues:
        """
        Convert a runtime policy into effort values.

        Args:
            policy: The resolved tier policy.

        Returns:
            The values, with the tool allowlist as a plain list.
        """
        return EffortValues(
            max_iterations=policy.max_iterations,
            max_tool_calls=policy.max_tool_calls,
            max_tool_rounds=policy.max_tool_rounds,
            continue_on_tool_limit=policy.continue_on_tool_limit,
            continuations=policy.continuations,
            continuation_max_iterations=policy.continuation_max_iterations,
            continuation_max_calls=policy.continuation_max_calls,
            wall_clock_seconds=policy.wall_clock_seconds,
            target_seconds=policy.target_seconds,
            stop_on_wall_clock=policy.stop_on_wall_clock,
            validation=policy.validation,
            tool_allowlist=list(policy.tool_allowlist) if policy.tool_allowlist else None,
        )

    def ensure_builtins(self) -> None:
        """
        Seed the built-in efforts (``agile``, ``max``, ``default``) once.

        The values come from the runtime tier policies, so seeding is never a
        behaviour change: it only turns hardcoded numbers into editable data.
        """
        for name in BUILTIN_EFFORTS:
            if os.path.isfile(self.definition_path(name)):
                continue
            factory = _TIER_FACTORIES[name]
            definition = EffortDefinition(
                name=name,
                description=BUILTIN_DESCRIPTIONS.get(name, ""),
                tier=name,
                values=self._values_from_policy(factory()),
                builtin=True,
            )
            definition.stamp_created()
            self._write(definition)
            logger.info("Seeded built-in effort '%s'", name)

    # ── Read ─────────────────────────────────────────────────────────────────

    def _read_raw(self, name: str) -> Optional[EffortDefinition]:
        """Load a definition from disk without resolving its profiles."""
        path = self.definition_path(name)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError) as error:
            logger.warning("Invalid effort file %s: %s", path, error)
            return None
        definition = EffortDefinition(**data)
        definition.name = definition.name or name
        return definition

    def read_effort(self, name: str, with_profiles: bool = True) -> Optional[EffortDefinition]:
        """
        Read one effort.

        Args:
            name: Effort name.
            with_profiles: Also compute the profiles linked to it.

        Returns:
            The definition or ``None`` when it does not exist.
        """
        definition = self._read_raw(name)
        if definition and with_profiles:
            definition.profiles = self.profiles_using(definition.name)
        return definition

    def list_efforts(self, ensure_builtins: bool = True) -> List[EffortDefinition]:
        """
        List every effort of the project.

        Args:
            ensure_builtins: Seed the built-in efforts when they are missing.

        Returns:
            The definitions, ordered by name (built-ins first).
        """
        if ensure_builtins:
            self.ensure_builtins()

        efforts: List[EffortDefinition] = []
        pattern = os.path.join(self.efforts_path, "*", f"*{EFFORT_EXTENSION}")
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)[: -len(EFFORT_EXTENSION)]
            definition = self.read_effort(name)
            if definition:
                efforts.append(definition)
        efforts.sort(key=lambda item: (item.name not in BUILTIN_EFFORTS, item.name))
        return efforts

    # ── Write ────────────────────────────────────────────────────────────────

    @staticmethod
    def validate_name(name: str) -> str:
        """
        Normalize and validate an effort name.

        Args:
            name: Raw name.

        Returns:
            The trimmed name.

        Raises:
            ValueError: When the name is empty or not filesystem friendly.
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("The effort name is required.")
        if not _NAME_RE.match(clean):
            raise ValueError(
                "Invalid effort name: use letters, digits, '_', '-' or '.' "
                "(it is also the folder name)."
            )
        return clean

    def _write(self, definition: EffortDefinition) -> None:
        """Write the definition to ``.codx/efforts/<Name>/<Name>.effort``."""
        folder = self.effort_folder(definition.name)
        os.makedirs(folder, exist_ok=True)
        payload = definition.model_dump(exclude={"profiles"})
        with open(self.definition_path(definition.name), "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")

    def save_effort(self, definition: EffortDefinition) -> EffortDefinition:
        """
        Create or replace an effort, then refresh the profiles linked to it.

        Args:
            definition: The effort to persist.

        Returns:
            The persisted definition, with timestamps and linked profiles.

        Raises:
            ValueError: When the name or the base tier is invalid.
        """
        definition.name = self.validate_name(definition.name)
        tier = (definition.tier or AGILE_NAME).strip().lower()
        if tier not in EFFORT_TIERS:
            raise ValueError(
                f"Invalid base tier '{tier}': use one of {', '.join(EFFORT_TIERS)}."
            )
        definition.tier = tier

        existing = self._read_raw(definition.name)
        definition.builtin = bool(existing and existing.builtin) or (
            definition.name in BUILTIN_EFFORTS
        )
        definition.created_at = (existing.created_at if existing else "") or _now()
        definition.stamp_updated()
        definition.profiles = []

        self._write(definition)
        self.sync_profiles_using(definition.name)
        logger.info("Saved effort '%s' (tier=%s)", definition.name, definition.tier)
        return self.read_effort(definition.name)

    def delete_effort(self, name: str) -> bool:
        """
        Delete an effort.

        Args:
            name: Effort name.

        Returns:
            ``True`` when something was deleted.

        Raises:
            ValueError: When the effort is built-in or still linked to profiles.
        """
        if name in BUILTIN_EFFORTS:
            raise ValueError(f"'{name}' is a built-in effort and cannot be deleted.")
        linked = self.profiles_using(name)
        if linked:
            raise ValueError(
                f"'{name}' is linked to {', '.join(sorted(linked))}: "
                "link those profiles to another effort first."
            )
        folder = self.effort_folder(name)
        if not os.path.isdir(folder):
            return False
        shutil.rmtree(folder)
        logger.info("Deleted effort '%s'", name)
        return True

    # ── Profiles ↔ efforts ───────────────────────────────────────────────────

    def _profile_files(self) -> List[str]:
        """Return the ``.profile`` files of the project."""
        pattern = os.path.join(self.profiles_path, "*", "*.profile")
        return sorted(glob.glob(pattern))

    def profiles_using(self, name: str) -> List[str]:
        """
        List the profiles linked to one effort.

        Args:
            name: Effort name.

        Returns:
            The profile names whose ``effort_name`` matches.
        """
        linked: List[str] = []
        for path in self._profile_files():
            try:
                with open(path, "r", encoding="utf-8") as file:
                    data = json.load(file)
            except (OSError, ValueError):
                continue
            if (data.get("effort_name") or "").strip() == name:
                linked.append(data.get("name") or os.path.basename(path)[: -len(".profile")])
        return linked

    def overrides_for(self, name: str) -> Dict[str, Any]:
        """
        Return the runtime overrides of one effort.

        Args:
            name: Effort name.

        Returns:
            The non-``None`` values, ready for ``effort_for(settings=...)``.
            An empty mapping when the effort does not exist.
        """
        definition = self._read_raw(name)
        return definition.to_overrides() if definition else {}

    def apply_to_profile(self, profile: Any) -> Any:
        """
        Materialize the linked effort into ``profile.effort``.

        Args:
            profile: A :class:`~codx.junior.model.profile.Profile`.

        Returns:
            The same profile, with ``effort`` set from its effort definition.
        """
        effort_name = (getattr(profile, "effort_name", None) or "").strip()
        if not effort_name:
            return profile

        definition = self._read_raw(effort_name)
        if not definition:
            logger.warning(
                "Profile '%s' is linked to effort '%s' which does not exist.",
                getattr(profile, "name", "?"),
                effort_name,
            )
            return profile

        # Local import: model.profile must stay importable from here without a
        # package cycle (profiles -> efforts -> profiles).
        from codx.junior.model.profile import ProfileEffortSettings

        overrides = definition.to_overrides()
        profile.effort = ProfileEffortSettings(**overrides) if overrides else None
        return profile

    def sync_profiles_using(self, name: str) -> List[str]:
        """
        Refresh ``effort`` in every profile linked to an effort.

        Args:
            name: Effort name.

        Returns:
            The profile names that were refreshed.
        """
        definition = self._read_raw(name)
        if not definition:
            return []
        overrides = definition.to_overrides()

        refreshed: List[str] = []
        for path in self._profile_files():
            try:
                with open(path, "r", encoding="utf-8") as file:
                    data = json.load(file)
            except (OSError, ValueError):
                continue
            if (data.get("effort_name") or "").strip() != name:
                continue
            data["effort"] = overrides or None
            try:
                with open(path, "w", encoding="utf-8") as file:
                    json.dump(data, file, indent=2, ensure_ascii=False)
                    file.write("\n")
            except OSError as error:
                logger.warning("Could not refresh profile %s: %s", path, error)
                continue
            refreshed.append(data.get("name") or os.path.basename(path)[: -len(".profile")])

        if refreshed:
            logger.info("Effort '%s' applied to %s", name, ", ".join(refreshed))
        return refreshed

    # ── Documentation ────────────────────────────────────────────────────────

    @staticmethod
    def field_docs() -> Dict[str, Any]:
        """
        Return the documentation of every value the editor can set.

        Returns:
            ``{"groups": [...], "fields": [...], "tiers": [...]}``.
        """
        return {
            "groups": list(EFFORT_FIELD_GROUPS),
            "fields": EFFORT_FIELD_DOCS,
            "tiers": list(EFFORT_TIERS),
        }


def apply_effort_to_profile(settings: Any, profile: Any) -> Any:
    """
    Materialize the effort of a profile before it is persisted.

    Called from :meth:`codx.junior.profiles.profile_manager.ProfileManager.save_profile`
    so a profile linked to an effort always carries that effort's values, no
    matter which caller (API, tool, generation) saved it.

    Args:
        settings: Project settings (may be ``None``).
        profile: The profile being saved.

    Returns:
        The profile, with ``effort`` resolved from ``effort_name``.
    """
    if not settings or profile is None:
        return profile
    if not (getattr(profile, "effort_name", None) or "").strip():
        return profile
    return EffortManager(settings).apply_to_profile(profile)


__all__ = [
    "EFFORT_EXTENSION",
    "EffortManager",
    "apply_effort_to_profile",
]

# Made with ❤️ by codx-junior
