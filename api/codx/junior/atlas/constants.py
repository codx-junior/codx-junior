"""
Constants and tool policy for the project atlas.

The atlas is the codebase's navigation index and the **only** discovery layer:
``project_map`` / ``project_locate`` / ``project_trace`` / ``project_impact``
replaced grep, glob, semantic file search and the folder tree, which are no
longer registered as tools. There is therefore no legacy fallback to keep and
no "which discovery flavour is active" flag.

Tool policy lives here (and not in ``codx.junior.tools``) so the agent runtime
can enforce atlas-first discovery without importing the whole tool registry.
"""

from __future__ import annotations

from typing import Any, Iterable

ATLAS_DIRNAME = "atlas"
ATLAS_DB_FILENAME = "atlas.db"
#: 2: the index now honors ``settings.get_ignore_patterns()``.
ATLAS_INDEX_VERSION = 2

#: Read-only tools that replace grep/glob/search for project discovery.
ATLAS_TOOL_NAMES: tuple[str, ...] = (
    "project_map",
    "project_locate",
    "project_trace",
    "project_impact",
    "project_health",
)

#: Tools a profile needs to be eligible for the atlas tools. The atlas only
#: reads project files, so it is never granted to a profile that cannot.
PROJECT_READ_TOOL_NAMES: tuple[str, ...] = (
    "project_read_file",
    "read_folder",
)

#: Default cap. Above this the atlas reports ``truncated`` in its health output.
DEFAULT_MAX_FILES = 5000
#: How long a freshness check is reused within one process.
DEFAULT_TTL_SECONDS = 5


def atlas_enabled(settings: Any) -> bool:
    """Return True when the project atlas should be available for ``settings``."""
    if settings is None:
        return False
    return bool(getattr(settings, "project_atlas_enabled", False))


def apply_atlas_policy(
    tool_names: Iterable[str],
    settings: Any,
) -> set[str]:
    """
    Apply the atlas tool policy to a concrete tool-name set.

    The atlas tools are always added when the atlas is enabled, even when a
    profile allowlist predates them: discovery is atlas-only, so a profile that
    can read project files must be able to search.
    """
    names = {str(name) for name in (tool_names or []) if name}
    if not atlas_enabled(settings):
        return names

    if not names.intersection(PROJECT_READ_TOOL_NAMES):
        return names

    names.update(ATLAS_TOOL_NAMES)
    return names
