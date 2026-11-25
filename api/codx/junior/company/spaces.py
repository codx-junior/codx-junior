"""
Coworking spaces: company-owned files plus virtual project folders.

A space is stored inside the **company** document (``Company.spaces``), not in
a project: the company is the owner of the shared workspace. Its physical files
live in one folder per space::

    <companies_root>/<company_id>/spaces/<space_id>/files/

The browser is virtual: it shows the company files of that folder **and** a
read-only folder for every project/folder the company mapped into the space::

    /                       company files + the "projects" / "folders" roots
    /projects/<project_id>/  the files of a linked company project
    /folders/<folder_id>/    an explicitly mapped host folder

Nothing is copied. Every listing resolves the project/host folder on the fly,
so the company browser always reflects the disk and a project keeps a single
source of truth. Writes are only allowed inside the company's own files folder;
mapped projects/folders are read-only through the browser.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time

from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Folder under a company that holds every space of the company.
SPACES_FOLDER = "spaces"

#: Folder that holds the physical files of one space.
FILES_FOLDER = "files"

#: Name of the physical folder of every space.
TRASH_FOLDER = ".trash"

#: Virtual root that groups the mapped projects.
PROJECTS_ROOT = "projects"

#: Virtual root that groups the explicitly mapped host folders.
FOLDERS_ROOT = "folders"

#: Segments the virtual browser owns; a real company folder with the same name
#: is only shadowed while the space actually maps a project/folder.
VIRTUAL_ROOTS = (PROJECTS_ROOT, FOLDERS_ROOT)

#: Mount points that must never be mapped into a company space.
FORBIDDEN_PATHS = ("/", "/proc", "/sys", "/dev", "/run")

_SPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class SpacePathError(ValueError):
    """Raised when a virtual path is invalid or escapes its root."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _safe_space_id(space_id: str) -> str:
    """Validate a space id before using it as a folder name."""
    value = str(space_id or "").strip()
    if not _SPACE_ID_RE.match(value):
        raise SpacePathError(f"invalid space id: {space_id!r}")
    return value


def space_root(company_dir: str, space_id: str) -> str:
    """Return the folder that holds a space (manifest, files, trash)."""
    return os.path.join(company_dir, SPACES_FOLDER, _safe_space_id(space_id))


def space_files_root(company_dir: str, space_id: str) -> str:
    """Return the folder holding the company files of a space."""
    return os.path.join(space_root(company_dir, space_id), FILES_FOLDER)


def ensure_space_files_root(company_dir: str, space_id: str) -> str:
    """Create (when needed) and return the company files folder of a space."""
    root = space_files_root(company_dir, space_id)
    os.makedirs(root, exist_ok=True)
    return root


def cleanup_space_files(company_dir: str, space_id: str) -> None:
    """
    Move the files of a deleted space into the company trash.

    Deleting a space is an explicit admin action, but moving the folder instead
    of ``rmtree`` keeps a mistake recoverable by hand (the folder keeps the
    timestamp of the deletion).
    """
    root = space_root(company_dir, space_id)
    if not os.path.isdir(root):
        return
    trash = os.path.join(company_dir, SPACES_FOLDER, TRASH_FOLDER)
    os.makedirs(trash, exist_ok=True)
    destination = os.path.join(
        trash, f"{_safe_space_id(space_id)}-{time.time_ns()}"
    )
    try:
        os.replace(root, destination)
    except OSError as ex:  # pragma: no cover - cross-device or permissions
        logger.warning("Could not move space %s to the trash: %s", space_id, ex)
        shutil.rmtree(root, ignore_errors=True)


def clean_path(path: Optional[str]) -> str:
    """
    Normalize a virtual browser path.

    Args:
        path: Raw path (``""`` is the space root).

    Returns:
        Slash separated relative path without empty/``.`` segments.

    Raises:
        SpacePathError: When the path tries to walk above the space root.
    """
    raw = str(path or "").replace("\\", "/").strip()
    parts: List[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise SpacePathError("a path cannot walk above the space root")
        if "\x00" in part:
            raise SpacePathError("invalid path")
        parts.append(part)
    return "/".join(parts)


def _safe_join(root: str, relative: str) -> str:
    """Join *relative* to *root*, refusing anything that escapes it."""
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise SpacePathError("path escapes its root")
    return target


def _clean_name(name: Optional[str]) -> str:
    """Validate one file/folder name typed by a user."""
    value = str(name or "").strip()
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise SpacePathError(f"invalid file name: {name!r}")
    return value


# ---------------------------------------------------------------------------
# Project catalog
# ---------------------------------------------------------------------------


def _project_by_id(
    projects: Iterable[Dict[str, Any]], project_id: str
) -> Optional[Dict[str, Any]]:
    """Find a project catalog entry by id."""
    for project in projects or []:
        if str(project.get("project_id") or "") == str(project_id or ""):
            return project
    return None


def project_root(project: Optional[Dict[str, Any]]) -> str:
    """Return the absolute root folder of a project catalog entry."""
    path = str(
        (project or {}).get("project_path")
        or (project or {}).get("abs_project_path")
        or ""
    ).strip()
    return os.path.abspath(path) if path else ""


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _entry(
    *,
    path: str,
    name: str,
    is_dir: bool,
    kind: str,
    readonly: bool,
    virtual: bool = False,
    size: Optional[int] = None,
    modified: Optional[float] = None,
    project_id: str = "",
    folder_id: str = "",
    available: bool = True,
) -> Dict[str, Any]:
    """Build one browser entry."""
    return {
        "path": path,
        "name": name,
        "is_dir": bool(is_dir),
        "kind": kind,
        "readonly": bool(readonly),
        "virtual": bool(virtual),
        "size": size,
        "modified": modified,
        "project_id": project_id,
        "folder_id": folder_id,
        "available": bool(available),
    }


def _breadcrumbs(
    space: Any,
    path: str,
    projects: Iterable[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build the breadcrumb trail of a virtual path."""
    crumbs: List[Dict[str, str]] = [
        {"name": str(getattr(space, "name", "") or "Company files"), "path": ""}
    ]
    clean = clean_path(path)
    if not clean:
        return crumbs
    parts = clean.split("/")
    if parts[0] == PROJECTS_ROOT and getattr(space, "project_ids", None):
        crumbs.append({"name": "Projects", "path": PROJECTS_ROOT})
        if len(parts) >= 2:
            project = _project_by_id(projects, parts[1])
            label = str((project or {}).get("project_name") or parts[1])
            crumbs.append(
                {"name": label, "path": f"{PROJECTS_ROOT}/{parts[1]}"}
            )
            for index in range(2, len(parts)):
                crumbs.append(
                    {
                        "name": parts[index],
                        "path": f"{PROJECTS_ROOT}/{'/'.join(parts[1:index + 1])}",
                    }
                )
        return crumbs
    if parts[0] == FOLDERS_ROOT and getattr(space, "folders", None):
        crumbs.append({"name": "Folders", "path": FOLDERS_ROOT})
        if len(parts) >= 2:
            folder = space.folder(parts[1]) if hasattr(space, "folder") else None
            label = str(getattr(folder, "name", "") or parts[1])
            crumbs.append(
                {"name": label, "path": f"{FOLDERS_ROOT}/{parts[1]}"}
            )
            for index in range(2, len(parts)):
                crumbs.append(
                    {
                        "name": parts[index],
                        "path": f"{FOLDERS_ROOT}/{'/'.join(parts[1:index + 1])}",
                    }
                )
        return crumbs
    for index, part in enumerate(parts):
        crumbs.append({"name": part, "path": "/".join(parts[: index + 1])})
    return crumbs


def _scandir_entries(
    abs_dir: str,
    virtual_prefix: str,
    *,
    readonly: bool,
    kind: str,
    project_id: str = "",
    folder_id: str = "",
) -> List[Dict[str, Any]]:
    """List one real folder as browser entries, directories first."""
    entries: List[Dict[str, Any]] = []
    with os.scandir(abs_dir) as iterator:
        for item in iterator:
            try:
                is_dir = item.is_dir()
                stat = item.stat()
            except OSError:
                continue
            name = item.name
            entry_kind = kind
            if kind == "company":
                entry_kind = "folder" if is_dir else "file"
            entries.append(
                _entry(
                    path=f"{virtual_prefix}/{name}" if virtual_prefix else name,
                    name=name,
                    is_dir=is_dir,
                    kind=entry_kind,
                    readonly=readonly,
                    size=None if is_dir else stat.st_size,
                    modified=stat.st_mtime,
                    project_id=project_id,
                    folder_id=folder_id,
                )
            )
    entries.sort(
        key=lambda entry: (
            not entry["is_dir"],
            str(entry["name"]).lower(),
        )
    )
    return entries


def _resolve_project_dir(
    space: Any, path: str, projects: Iterable[Dict[str, Any]]
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Resolve ``projects/<id>[/...]`` to a real folder."""
    parts = path.split("/")
    project_id = parts[1] if len(parts) > 1 else ""
    project = _project_by_id(projects, project_id)
    if not project:
        raise FileNotFoundError(
            f"Project '{project_id}' is not available in this company"
        )
    root = project_root(project)
    if not root or not os.path.isdir(root):
        raise FileNotFoundError(
            f"Project folder of '{project.get('project_name') or project_id}' does not exist"
        )
    relative = "/".join(parts[2:])
    abs_path = _safe_join(root, relative)
    return abs_path, project, {"project_id": project_id, "kind": "project"}


def _resolve_folder_dir(
    space: Any, path: str
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Resolve ``folders/<id>[/...]`` to a mapped host folder."""
    parts = path.split("/")
    folder_id = parts[1] if len(parts) > 1 else ""
    folder = space.folder(folder_id) if hasattr(space, "folder") else None
    if not folder:
        raise FileNotFoundError(
            f"Folder '{folder_id}' is not mapped into this space"
        )
    root = str(folder.path or "").strip()
    if not root or not os.path.isdir(root):
        raise FileNotFoundError(
            f"Mapped folder '{getattr(folder, 'name', folder_id)}' does not exist"
        )
    relative = "/".join(parts[2:])
    abs_path = _safe_join(root, relative)
    return abs_path, folder, {"folder_id": folder_id, "kind": "mapped_folder"}


def list_path(
    company_dir: str,
    space: Any,
    path: Optional[str],
    projects: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    List one virtual folder of a company space.

    Args:
        company_dir: Folder of the company (``CompanyStore.company_dir``).
        space: :class:`CompanySpace` being browsed.
        path: Virtual path (``""`` is the root).
        projects: Project catalog (``project_id``/``project_name``/``project_path``).

    Returns:
        ``{path, entries, readonly, container, breadcrumbs, space_id, space_name}``.

    Raises:
        FileNotFoundError: When the folder does not exist.
        SpacePathError: When the path is invalid/escapes its root.
    """
    clean = clean_path(path)
    mapped_projects = list(getattr(space, "project_ids", None) or [])
    mapped_folders = list(getattr(space, "folders", None) or [])
    projects_root = clean == PROJECTS_ROOT and bool(mapped_projects)
    folders_root = clean == FOLDERS_ROOT and bool(mapped_folders)
    entries: List[Dict[str, Any]] = []
    readonly = False
    container = False

    if not clean or projects_root or folders_root:
        container = True
        # The company files are writable; the virtual containers only navigate.
        readonly = bool(projects_root or folders_root)
        # Virtual roots first, so they are easy to find.
        if mapped_projects and clean in ("", PROJECTS_ROOT):
            if clean == "":
                entries.append(
                    _entry(
                        path=PROJECTS_ROOT,
                        name="Projects",
                        is_dir=True,
                        kind="projects_root",
                        readonly=True,
                        virtual=True,
                    )
                )
            else:
                for project_id in mapped_projects:
                    project = _project_by_id(projects, project_id)
                    root = project_root(project)
                    entries.append(
                        _entry(
                            path=f"{PROJECTS_ROOT}/{project_id}",
                            name=str(
                                (project or {}).get("project_name") or project_id
                            ),
                            is_dir=True,
                            kind="project",
                            readonly=True,
                            virtual=True,
                            project_id=project_id,
                            available=bool(root and os.path.isdir(root)),
                        )
                    )
        if mapped_folders and clean in ("", FOLDERS_ROOT):
            if clean == "":
                entries.append(
                    _entry(
                        path=FOLDERS_ROOT,
                        name="Folders",
                        is_dir=True,
                        kind="folders_root",
                        readonly=True,
                        virtual=True,
                    )
                )
            else:
                for folder in mapped_folders:
                    entries.append(
                        _entry(
                            path=f"{FOLDERS_ROOT}/{folder.id}",
                            name=str(folder.name or folder.id),
                            is_dir=True,
                            kind="mapped_folder",
                            readonly=bool(folder.readonly),
                            virtual=True,
                            folder_id=folder.id,
                            available=os.path.isdir(str(folder.path or "")),
                        )
                    )
        if clean == "":
            root = ensure_space_files_root(company_dir, space.id)
            entries.extend(
                _scandir_entries(
                    root,
                    "",
                    readonly=False,
                    kind="company",
                )
            )
        return {
            "path": clean,
            "entries": entries,
            "readonly": readonly,
            "container": container,
            "breadcrumbs": _breadcrumbs(space, clean, projects),
            "space_id": space.id,
            "space_name": space.name,
        }

    if mapped_projects and clean.startswith(PROJECTS_ROOT + "/"):
        abs_path, project, meta = _resolve_project_dir(space, clean, projects)
        readonly = True
        kind = "project"
        project_id = meta["project_id"]
        folder_id = ""
    elif mapped_folders and clean.startswith(FOLDERS_ROOT + "/"):
        abs_path, folder, meta = _resolve_folder_dir(space, clean)
        readonly = bool(folder.readonly)
        kind = "mapped_folder"
        project_id = ""
        folder_id = meta["folder_id"]
    else:
        root = ensure_space_files_root(company_dir, space.id)
        abs_path = _safe_join(root, clean)
        readonly = False
        kind = "company"
        project_id = ""
        folder_id = ""

    if not os.path.isdir(abs_path):
        raise FileNotFoundError(clean or "/")

    entries = _scandir_entries(
        abs_path,
        clean,
        readonly=readonly,
        kind=kind,
        project_id=project_id,
        folder_id=folder_id,
    )
    return {
        "path": clean,
        "entries": entries,
        "readonly": readonly,
        "container": False,
        "breadcrumbs": _breadcrumbs(space, clean, projects),
        "space_id": space.id,
        "space_name": space.name,
    }


# ---------------------------------------------------------------------------
# Entry resolution and writes
# ---------------------------------------------------------------------------


def resolve_entry(
    company_dir: str,
    space: Any,
    path: Optional[str],
    projects: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve one virtual path to a real file/folder.

    Returns:
        ``{abs_path, path, name, is_dir, readonly, kind, project_id, folder_id}``.

    Raises:
        FileNotFoundError: When the entry does not exist.
        SpacePathError: When the path is invalid/escapes its root.
    """
    clean = clean_path(path)
    if not clean:
        root = ensure_space_files_root(company_dir, space.id)
        return {
            "abs_path": root,
            "path": "",
            "name": str(getattr(space, "name", "") or "Company files"),
            "is_dir": True,
            "readonly": False,
            "kind": "company",
            "project_id": "",
            "folder_id": "",
        }
    if clean in VIRTUAL_ROOTS:
        mapped = (
            getattr(space, "project_ids", None)
            if clean == PROJECTS_ROOT
            else getattr(space, "folders", None)
        )
        if mapped:
            raise SpacePathError(f"'{clean}' is a virtual container, not a file")

    if getattr(space, "project_ids", None) and clean.startswith(PROJECTS_ROOT + "/"):
        abs_path, project, meta = _resolve_project_dir(space, clean, projects)
        readonly = True
        kind = "project"
        project_id = meta["project_id"]
        folder_id = ""
    elif getattr(space, "folders", None) and clean.startswith(FOLDERS_ROOT + "/"):
        abs_path, folder, meta = _resolve_folder_dir(space, clean)
        readonly = bool(folder.readonly)
        kind = "mapped_folder"
        project_id = ""
        folder_id = meta["folder_id"]
    else:
        root = ensure_space_files_root(company_dir, space.id)
        abs_path = _safe_join(root, clean)
        readonly = False
        kind = "company"
        project_id = ""
        folder_id = ""

    if not os.path.exists(abs_path):
        raise FileNotFoundError(clean)
    return {
        "abs_path": abs_path,
        "path": clean,
        "name": os.path.basename(abs_path.rstrip(os.sep)) or clean,
        "is_dir": os.path.isdir(abs_path),
        "readonly": readonly,
        "kind": kind,
        "project_id": project_id,
        "folder_id": folder_id,
    }


def _writable_target(
    company_dir: str, space: Any, path: Optional[str]
) -> Tuple[str, str]:
    """
    Resolve a path a write may touch.

    Company files are always writable. A mapped host folder is writable only
    when the mapping explicitly allows it. Project virtual folders are managed
    by the project itself and are never written through the company space.
    """
    clean = clean_path(path)
    mapped_projects = getattr(space, "project_ids", None)
    mapped_folders = getattr(space, "folders", None)
    if clean in VIRTUAL_ROOTS:
        mapped = mapped_projects if clean == PROJECTS_ROOT else mapped_folders
        if mapped:
            raise SpacePathError(
                "company files are the only writable area of a space"
            )
    if mapped_projects and clean.startswith(PROJECTS_ROOT + "/"):
        raise SpacePathError(
            "project files are read only from the company space"
        )
    if mapped_folders and clean.startswith(FOLDERS_ROOT + "/"):
        abs_path, folder, _meta = _resolve_folder_dir(space, clean)
        if folder.readonly:
            raise SpacePathError(
                f"Mapped folder '{folder.name}' is read only"
            )
        return clean, abs_path
    root = ensure_space_files_root(company_dir, space.id)
    target = _safe_join(root, clean)
    return clean, target


def create_folder(
    company_dir: str, space: Any, path: Optional[str], name: str
) -> Dict[str, Any]:
    """Create one folder under a writable folder of a space."""
    clean, parent = _writable_target(company_dir, space, path)
    folder_name = _clean_name(name)
    if not os.path.isdir(parent):
        raise FileNotFoundError(clean or "/")
    target = _safe_join(parent, folder_name)
    if os.path.exists(target):
        raise FileExistsError(folder_name)
    os.makedirs(target, exist_ok=False)
    return {
        "name": folder_name,
        "path": f"{clean}/{folder_name}".strip("/"),
        "is_dir": True,
    }


def delete_entry(company_dir: str, space: Any, path: Optional[str]) -> Dict[str, Any]:
    """Delete one writable file, or an empty writable folder."""
    clean, target = _writable_target(company_dir, space, path)
    if not clean:
        raise SpacePathError("the space root cannot be deleted")
    if (
        getattr(space, "folders", None)
        and clean.startswith(FOLDERS_ROOT + "/")
        and clean.count("/") == 1
    ):
        raise SpacePathError(
            "remove the folder mapping from the space settings instead"
        )
    if not os.path.exists(target):
        raise FileNotFoundError(clean)
    if os.path.isdir(target):
        try:
            os.rmdir(target)
        except OSError as ex:
            raise SpacePathError(
                "only an empty folder can be deleted"
            ) from ex
    else:
        os.remove(target)
    return {"path": clean, "deleted": True}


def write_text(
    company_dir: str, space: Any, path: Optional[str], content: str
) -> Dict[str, Any]:
    """Create or replace one text file inside a writable folder of a space."""
    clean, target = _writable_target(company_dir, space, path)
    if not clean:
        raise SpacePathError("a file path is required")
    if os.path.isdir(target):
        raise SpacePathError("the target is a folder")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(str(content or ""))
    return {"path": clean, "size": os.path.getsize(target)}


def save_upload(
    company_dir: str,
    space: Any,
    path: Optional[str],
    filename: str,
    content: bytes,
) -> Dict[str, Any]:
    """Store one uploaded file inside a writable folder of a space."""
    clean, parent = _writable_target(company_dir, space, path)
    if not os.path.isdir(parent):
        raise FileNotFoundError(clean or "/")
    name = _clean_name(os.path.basename(str(filename or "").replace("\\", "/")))
    target = _safe_join(parent, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(content or b"")
    return {
        "name": name,
        "path": f"{clean}/{name}".strip("/"),
        "size": os.path.getsize(target),
    }


def space_stats(company_dir: str, space: Any) -> Dict[str, Any]:
    """Return the file count/size of the company files of a space."""
    root = space_files_root(company_dir, space.id)
    files = 0
    total = 0
    if os.path.isdir(root):
        for current, _dirs, names in os.walk(root):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(current, name))
                    files += 1
                except OSError:
                    continue
    return {"files": files, "size_bytes": total}


def ensure_space_folder(company_dir: str, space: Any) -> str:
    """Make sure the physical folder of a space exists."""
    return ensure_space_files_root(company_dir, space.id)


def validate_mapped_folder(path: str, company_dir: str) -> str:
    """
    Validate a host folder before it is mapped into a space.

    Args:
        path: Absolute host path.
        company_dir: Company folder (its internal metadata is off limits).

    Returns:
        The absolute, normalized path.

    Raises:
        SpacePathError: When the folder is missing, absolute-only or internal.
    """
    value = str(path or "").strip()
    if not value:
        raise SpacePathError("a mapped folder needs a path")
    if not os.path.isabs(value):
        raise SpacePathError("a mapped folder path must be absolute")
    resolved = os.path.realpath(value)
    if resolved in FORBIDDEN_PATHS:
        raise SpacePathError(f"'{value}' cannot be mapped into a space")
    if not os.path.isdir(resolved):
        raise SpacePathError(f"'{value}' is not an existing folder")
    company_real = os.path.realpath(company_dir)
    if resolved == company_real or resolved.startswith(company_real + os.sep):
        raise SpacePathError(
            "the company folder and its internal files cannot be mapped into a space"
        )
    return resolved
