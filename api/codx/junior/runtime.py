"""
Ephemeral runtime folders shared by the platform subsystems.

``<project>/.codx`` is *durable* state: project settings, profiles and task
cards a human edits. Everything the platform writes while it runs — locks,
worker heartbeats, atomic temp files and rebuildable caches such as the project
atlas or the dependency graph — must not land there, otherwise the repository
working tree churns on its own and every ``git status`` is noise.

Those files live in a runtime folder instead::

    <CODX_JUNIOR_RUNTIME_PATH>/<project-slug>-<hash>/
        atlas/atlas.db
        board_manager/{worker.json,.state.lock,locks/}
        orchestration/{worker.json,locks/}
        dependency_graph.json
        domain_map.json
        project_summary.md
        wiki_settings.json

The folder is expendable: removing it only costs a rebuild. Its location is
``CODX_JUNIOR_RUNTIME_PATH`` (default ``<tmp>/codx-junior-runtime``, so ``/tmp``
on Linux) because nothing there has to survive a restart.
"""

from __future__ import annotations

import hashlib
import os

from codx.junior.globals import RUNTIME_FOLDER

#: Generated wiki category index (source file -> wiki page mapping).
WIKI_SETTINGS_FILE = "wiki_settings.json"


def project_runtime_dir(codx_path: str) -> str:
    """
    Return the runtime folder of the project owning ``codx_path``.

    The name is human readable (project folder name plus a short hash of the
    absolute ``codx_path``) so two projects sharing a folder name never collide.

    Example (doctest: +SKIP):
        >>> project_runtime_dir("/projects/app/.codx")
        '/tmp/codx-junior-runtime/app-1a2b3c4d5e'
    """
    absolute = os.path.abspath(str(codx_path or "."))
    name = os.path.basename(os.path.dirname(absolute)) or "project"
    safe = "".join(char if char.isalnum() or char in "-_." else "-" for char in name)
    digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()[:10]
    return os.path.join(RUNTIME_FOLDER, f"{safe}-{digest}")


def runtime_dir(codx_path: str, *parts: str) -> str:
    """
    Return (creating it) a folder inside the project's runtime folder.

    Example (doctest: +SKIP):
        >>> runtime_dir("/projects/app/.codx", "board_manager")
        '/tmp/codx-junior-runtime/app-1a2b3c4d5e/board_manager'
    """
    path = os.path.join(project_runtime_dir(codx_path), *[p for p in parts if p])
    os.makedirs(path, exist_ok=True)
    return path


def runtime_file(codx_path: str, *parts: str) -> str:
    """
    Return a file path inside the project's runtime folder.

    The parent folder is created when ``parts`` has more than one element, so
    callers can hand the path straight to ``open(..., "a")`` or to a lock
    helper without extra bookkeeping.

    Example (doctest: +SKIP):
        >>> runtime_file("/projects/app/.codx", "atlas", "atlas.db")
        '/tmp/codx-junior-runtime/app-1a2b3c4d5e/atlas/atlas.db'
    """
    parts = [p for p in parts if p]
    path = os.path.join(project_runtime_dir(codx_path), *parts)
    if len(parts) > 1:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    else:
        os.makedirs(project_runtime_dir(codx_path), exist_ok=True)
    return path
