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
    return os.path.join(RUNTIME_FOLDER, _project_slug(codx_path))


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


# ── Generated data plane ─────────────────────────────────────────────────────
#
# ``.codx`` holds two very different things:
#
# * **Shared artifacts** a human edits and the team shares through git: profiles,
#   task cards, boards, teams, workspaces. These stay tracked.
# * **The data plane**: everything the platform generates while it runs — chat
#   turn records, browser sessions, the harness audit journal, orchestration and
#   board state, rebuildable indexes. It is unbounded, model-influenced and
#   *untrusted*, so it must never be committed: a single tool output that prints
#   a token would otherwise enter git history forever.
#
# The data plane is addressed through :func:`data_dir` / :func:`data_file` so it
# can be relocated OUTSIDE the repository with one environment variable, and so
# the ignore rules and the CISO repo check can be driven by one list
# (:data:`DATA_PLANE_ENTRIES`) instead of a hand-maintained copy.

#: Environment variable that moves the generated data plane outside the repo.
ENV_DATA_PATH = "CODX_JUNIOR_CODX_DATA_PATH"

#: Entries below ``.codx`` that belong to the generated data plane and are never
#: tracked by git. ``.gitignore`` mirrors this list and
#: ``api/tests/security/ciso_repo_check.py`` verifies both stay in sync.
DATA_PLANE_ENTRIES = (
    "chat_turns",
    "browser_sessions",
    "browser_downloads",
    "computer_downloads",
    "computer_library",
    "harness",
    "orchestration",
    "board_manager",
    "autonomy",
    "atlas",
    "db",
    "logs",
    "media",
    "tmp",
    "cache",
    "dependency_graph.json",
    "domain_map.json",
    "project_summary.md",
    "wiki_settings.json",
)


def _project_slug(codx_path: str) -> str:
    """Return a filesystem-safe, collision-free name for a project."""
    absolute = os.path.abspath(str(codx_path or "."))
    name = os.path.basename(os.path.dirname(absolute)) or "project"
    safe = "".join(char if char.isalnum() or char in "-_." else "-" for char in name)
    digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}"


def data_root(codx_path: str) -> str:
    """
    Return the root of the project's generated data plane.

    Defaults to the project's ``.codx`` folder, which keeps every existing
    install (and every bind mount that points inside it) working unchanged.
    Setting ``CODX_JUNIOR_CODX_DATA_PATH`` relocates the whole data plane to an
    external folder, one subfolder per project, so a deployment can keep
    generated runtime state completely outside the repository::

        CODX_JUNIOR_CODX_DATA_PATH=/var/lib/codx-junior/data
        >>> data_root("/projects/app/.codx")            # doctest: +SKIP
        '/var/lib/codx-junior/data/app-1a2b3c4d5e'
    """
    raw = str(codx_path or "").strip()
    override = (os.environ.get(ENV_DATA_PATH) or "").strip()
    if override:
        return os.path.join(
            os.path.abspath(os.path.expanduser(override)), _project_slug(raw or ".")
        )
    return os.path.abspath(raw) if raw else ""


def data_dir(codx_path: str, *parts: str) -> str:
    """
    Return (creating it) a folder of the generated data plane.

    Example (doctest: +SKIP):
        >>> data_dir("/projects/app/.codx", "chat_turns")
        '/projects/app/.codx/chat_turns'
    """
    kept = [part for part in parts if part]
    path = os.path.join(data_root(codx_path), *kept) if kept else data_root(codx_path)
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def data_file(codx_path: str, *parts: str) -> str:
    """
    Return a file path inside the generated data plane (parent created).

    Example (doctest: +SKIP):
        >>> data_file("/projects/app/.codx", "harness", "audit.jsonl")
        '/projects/app/.codx/harness/audit.jsonl'
    """
    kept = [part for part in parts if part]
    if not kept:
        return data_root(codx_path)
    path = os.path.join(data_root(codx_path), *kept) if data_root(codx_path) else os.path.join(*kept)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
