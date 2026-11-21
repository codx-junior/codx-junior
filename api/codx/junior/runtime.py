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
``CODX_JUNIOR_RUNTIME_PATH`` (``set_env.sh`` points it at a folder inside the
shared projects mount in the Compose deployment; otherwise it defaults to
``<tmp>/codx-junior-runtime``) because nothing there has to survive a restart.
The per-project folder is derived from ``project.json`` (project id), never from
the path string: the API and the background worker can see the same project
through different mount aliases, and a path-derived name made them miss each
other's locks and worker heartbeat.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional, Tuple

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
    "coworking",
    "harness",
    "orchestration",
    "board_manager",
    "autonomy",
    "atlas",
    "ciso",
    "index",
    "task_workspaces",
    "db",
    "logs",
    "media",
    "voice",
    "tmp",
    "cache",
    "dependency_graph.json",
    "domain_map.json",
    "project_summary.md",
    "wiki_settings.json",
)


def _project_identity(codx_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Return ``(project_id, project_name)`` from ``<codx_path>/project.json``.

    The identity is read from the project document — never derived from the
    path string — because the same project can be reached through different
    mount aliases (``/home/codx-junior/codx-junior/.codx`` and
    ``/home/codx-junior-projects/.../.codx`` are the same bind-mounted folder
    in the Compose deployment). Hashing the alias produced two runtime folders
    for one project, so the API could never read the worker heartbeat written by
    the background process and the harness reported "worker offline" forever.
    """
    absolute = os.path.abspath(str(codx_path or "."))
    try:
        with open(
            os.path.join(absolute, "project.json"), "r", encoding="utf-8"
        ) as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    project_id = str(data.get("project_id") or "").strip() or None
    project_name = str(data.get("project_name") or "").strip() or None
    return project_id, project_name


def _fallback_identity(absolute: str) -> str:
    """
    Return a stable identity for a project without a readable ``project.json``.

    The inode/device pair is stable across bind-mount aliases (the path aliases
    are the failure mode this function exists to survive); the resolved path is
    only the last resort for platforms/filesystems without useful stat data.
    """
    try:
        stat = os.stat(absolute)
        return f"inode:{stat.st_dev}:{stat.st_ino}"
    except OSError:
        return f"path:{os.path.realpath(absolute)}"


def _project_slug(codx_path: str) -> str:
    """Return a filesystem-safe, alias-independent identifier for a project."""
    absolute = os.path.abspath(str(codx_path or "."))
    project_id, project_name = _project_identity(absolute)
    identity = project_id or _fallback_identity(absolute)
    # Without a project document there is no stable human name across mount
    # aliases: a fixed prefix plus the inode hash keeps the slug identical.
    name = project_name or "project"
    safe = "".join(char if char.isalnum() or char in "-_." else "-" for char in name)
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
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
