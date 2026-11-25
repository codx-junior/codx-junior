"""
Project relations for the atlas.

An atlas indexes exactly one project, but a task rarely stops at that boundary:
a project can contain **child** projects (a nested ``.codx/project.json``) and
it can declare **dependency** projects by name (``project_dependencies``).

Those relations already live in the settings layer
(``CODXJuniorSettings.get_related_projects``) and are used by the knowledge
engine and the chat RAG pipeline. This module exposes the same resolution to the
atlas tools without importing ``codx.junior.settings`` (no import cycle and no
heavy dependencies), so a model can be told *which* projects exist and search
the right one instead of guessing.

The structure is resolved once per project and cached for
``CACHE_TTL_SECONDS``: ``get_sub_projects`` walks the tree with ``rglob`` and the
atlas tools are called in a loop, so the lookup must not run on every call.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CHILD = "child"
DEPENDENCY = "dependency"

#: How long the resolved project structure is reused.
CACHE_TTL_SECONDS = 60.0

#: A project fan-out must stay bounded: every related project is opened,
#: searched and (when stale) refreshed. More than this is a configuration
#: accident, not a useful search.
MAX_RELATED_PROJECTS = 24

#: Path components that only contain runtime/VCS data. A "project" living
#: inside them is a pytest fixture or a control-plane artifact, never a child.
_CONTROL_PLANE_PARTS = frozenset({".codx", ".git", ".tmp", "node_modules"})

_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()


def _root(settings: Any) -> str:
    root = getattr(settings, "abs_project_path", "") or ""
    return os.path.abspath(root) if root else ""


def _cache_key(settings: Any) -> str:
    return str(
        getattr(settings, "codx_path", None)
        or getattr(settings, "project_id", None)
        or _root(settings)
        or getattr(settings, "project_name", "")
    )


def _inside(path: str, root: str) -> bool:
    """True when ``path`` lives under ``root`` (component-wise comparison)."""
    try:
        return os.path.commonpath([path, root]) == root
    except (ValueError, OSError):
        return False


def _has_index(settings: Any) -> bool:
    """Whether the related project already has a completed atlas build."""
    try:
        from .query import atlas_ready

        return atlas_ready(settings)
    except Exception:  # pragma: no cover - never break a tool call
        return False


def _catalogue_project(name: str) -> Optional[Any]:
    """
    Resolve a declared dependency name through the project catalogue.

    ``CODXJuniorSettings.get_related_projects`` resolves a name on disk only (a
    sibling folder, a folder inside the project or an absolute path). A project
    linked from the settings page usually lives somewhere else entirely, so the
    name is looked up in the project catalogue too - the same fallback
    ``project_discover.get_project_dependencies`` already applies.

    Imported lazily: the catalogue pulls the settings module.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    try:
        from ..project.project_discover import find_all_projects

        projects = find_all_projects() or {}
    except Exception:  # pragma: no cover - catalogue unavailable
        logger.debug("Atlas: project catalogue unavailable", exc_info=True)
        return None
    for project in projects.values():
        candidates = {
            str(getattr(project, "project_name", "") or "").lower(),
            str(getattr(project, "project_id", "") or "").lower(),
            os.path.basename(_root(project)).lower(),
        }
        if wanted in candidates:
            return project
    return None


def _is_control_plane_path(path: str) -> bool:
    """True when *path* lives inside ``.codx``/``.git``/``node_modules``."""
    parts = os.path.normpath(str(path or "")).replace(os.sep, "/").split("/")
    return any(part in _CONTROL_PLANE_PARTS for part in parts if part)


def _row(project: Any, own_root: str) -> Optional[Dict[str, Any]]:
    """Describe one related project (``None`` when it has no usable root)."""
    root = _root(project)
    if not root or not os.path.isdir(root):
        return None
    # Runtime/test fixtures under the control plane are not child projects.
    if _is_control_plane_path(root):
        logger.debug("Atlas: ignoring control-plane related project %s", root)
        return None
    return {
        "name": getattr(project, "project_name", "") or os.path.basename(root),
        "root": root,
        # A project nested in this tree is a child; anything else was
        # declared as a dependency (next to the project or elsewhere).
        "kind": CHILD if own_root and _inside(root, own_root) else DEPENDENCY,
        "settings": project,
    }


def _resolve_structure(settings: Any) -> List[Dict[str, Any]]:
    """Resolve children + dependencies (uncached, without index probing)."""
    # Related search disabled: every search stays local.
    if getattr(settings, "search_related_projects", True) is False:
        return []
    getter = getattr(settings, "get_related_projects", None)
    if not callable(getter):
        return []
    try:
        candidates = getter() or []
    except Exception as ex:  # pragma: no cover - best effort
        logger.warning("Atlas: cannot resolve related projects: %s", ex)
        return []

    own_root = _root(settings)
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for project in candidates:
        row = _row(project, own_root)
        if row is None or row["root"] in seen:
            continue
        seen.add(row["root"])
        rows.append(row)

    # Names the on-disk lookup could not resolve: a dependency linked from the
    # project settings may live outside this tree, so try the catalogue.
    declared = getattr(settings, "get_dependency_names", None)
    if callable(declared):
        try:
            names = declared() or []
        except Exception:  # pragma: no cover - best effort
            names = []
        known = set()
        for row in rows:
            known.add(str(row["name"]).lower())
            known.add(os.path.basename(str(row["root"])).lower())
        for name in names:
            wanted = (name or "").strip()
            if not wanted or wanted.lower() in known:
                continue
            project = _catalogue_project(wanted)
            row = _row(project, own_root) if project is not None else None
            if row is None or row["root"] in seen:
                continue
            seen.add(row["root"])
            known.add(wanted.lower())
            known.add(os.path.basename(str(row["root"])).lower())
            rows.append(row)
    return rows


def _structure(settings: Any) -> List[Dict[str, Any]]:
    """Cached project structure for ``settings``."""
    if settings is None:
        return []
    key = _cache_key(settings)
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    rows = _resolve_structure(settings)
    if len(rows) > MAX_RELATED_PROJECTS:
        logger.warning(
            "Atlas: %d related projects for %s; searching the first %d",
            len(rows),
            _root(settings) or getattr(settings, "project_name", ""),
            MAX_RELATED_PROJECTS,
        )
        rows = rows[:MAX_RELATED_PROJECTS]
    with _CACHE_LOCK:
        _CACHE[key] = (now, rows)
    return rows


def clear_cache() -> None:
    """Drop the cached structure (tests and project settings reload)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def related_projects(settings: Any) -> List[Dict[str, Any]]:
    """
    Return the projects searched together with ``settings``.

    Each entry carries ``name``, ``root``, ``kind`` (``child`` or
    ``dependency``), ``settings`` and ``indexed`` (a completed atlas build
    exists). Settings without ``get_related_projects`` yield an empty list, so
    the atlas keeps working for partial settings (tests, CLI, sub-agents).
    """
    rows = _structure(settings)
    return [dict(row, indexed=_has_index(row["settings"])) for row in rows]


def child_paths(settings: Any, root: str) -> Tuple[str, ...]:
    """
    Relative paths of the child projects nested in ``root``.

    A child project has its own atlas, so indexing it here as well duplicates
    the same files and, in a large repository, pushes this index over
    ``project_atlas_max_files``. Children are therefore excluded from the
    parent index and reached with ``project_locate(..., project='<name>')``.

    Returns an empty tuple when related search is disabled
    (``search_related_projects = False``): in that case the children are not
    reachable through another project, so they must stay in this index.
    """
    if settings is None or not root:
        return ()
    base = os.path.abspath(root)
    prefixes: List[str] = []
    for row in _structure(settings):
        if row["kind"] != CHILD:
            continue
        try:
            rel = os.path.relpath(row["root"], base)
        except ValueError:
            continue
        if rel in (".", "") or rel.startswith(os.pardir):
            continue
        prefixes.append(rel.replace(os.sep, "/"))
    return tuple(sorted(set(prefixes)))


def find_related_project(settings: Any, name: str) -> Optional[Dict[str, Any]]:
    """Resolve one project by name (or folder name) among the related ones."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for row in related_projects(settings):
        candidates = {
            str(row["name"]).lower(),
            os.path.basename(str(row["root"])).lower(),
        }
        if wanted in candidates:
            return row
    return None
