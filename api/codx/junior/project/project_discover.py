import os
import logging
import subprocess
from datetime import datetime, timedelta
from threading import Lock, Thread
from typing import Dict, Generator, List, Optional

from codx.junior.profiling.profiler import profile_function
from codx.junior.security.user_management import UserSecurityManager
from codx.junior.settings import CODXJuniorSettings, CODXJuniorProject
from codx.junior.model.model import CodxUser
from codx.junior.global_settings import read_global_settings
from codx.junior.globals import CODX_JUNIOR_HIDDEN

from codx.junior.globals import CODX_JUNIOR_PROJECTS_PATH


logger = logging.getLogger(__name__)

_ALL_PROJECTS: Optional[Dict[str, CODXJuniorSettings]] = None
_ALL_PROJECTS_PROC: Optional[Thread] = None

# ── Discovery single-flight and freshness ──────────────────────────────────────
# ``_DISCOVERY_LOCK`` guards the "start exactly one scan" decision. Before, the
# guard was ``if not _ALL_PROJECTS_PROC or force``: every cold caller and every
# ``force=True`` caller spawned its own thread running its own ``find /``, so N
# concurrent callers produced N concurrent full-filesystem walks. That is what
# pinned the CPU at 380 % and wrote gigabytes of block I/O.
_DISCOVERY_LOCK = Lock()
_SCAN_RUNNING = False
_ALL_PROJECTS_UPDATED_AT: Optional[datetime] = None

# How long a discovery snapshot is trusted before a background refresh is due.
# A full scan can take tens of seconds on a slow bind mount (88 s measured) and
# it competes with the chat/tool traffic for the same disk. New projects created
# through the API are inserted into the snapshot directly
# (``update_project_snapshot``), so a longer TTL only delays the discovery of a
# project created by ANOTHER process.
DISCOVERY_TTL_SECONDS = int(
    os.environ.get("CODX_JUNIOR_PROJECT_DISCOVERY_TTL_SECONDS", "600")
)
# Upper bound a cold-start caller waits for the first snapshot.
DISCOVERY_WAIT_SECONDS = float(
    os.environ.get("CODX_JUNIOR_PROJECT_DISCOVERY_WAIT_SECONDS", "5")
)

# Bounded filesystem scan used instead of ``find /``.
MAX_SCAN_DEPTH = int(os.environ.get("CODX_JUNIOR_PROJECT_SCAN_DEPTH", "6"))
MAX_SCAN_DIRS = int(os.environ.get("CODX_JUNIOR_PROJECT_SCAN_DIRS", "20000"))
SCAN_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", "site-packages", "dist", "build", "target", "vendor",
    "bower_components", ".worktrees", ".next", ".nuxt", ".cache",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea",
    ".gradle", "Pods",
}

# Fragment of the git dir a linked worktree points to:
# ``<main repo>/.git/worktrees/<name>``
GIT_WORKTREE_GITDIR_MARKER = "/worktrees/"

# Folder name used to hold PR worktrees inside (or next to) a repository:
# ``<repo>/.worktrees/<branch>``
WORKTREE_FOLDER_NAME = ".worktrees"

def is_project_visible(settings: CODXJuniorSettings) -> bool:
    project_is_visible = not CODX_JUNIOR_HIDDEN or \
        "/codx-junior/" not in settings.codx_path
    return project_is_visible

def get_projects_root_path() -> str:
    """
    Return the root path under which all projects are stored.

    Reads from global settings first, then falls back to the
    ``CODX_JUNIOR_PROJECTS_PATH`` environment variable, and finally
    defaults to ``~/projects``.

    Returns:
        Absolute path string for the projects root directory.
    """
    global_settings = read_global_settings()
    return (
        global_settings.projects_root_path
        or CODX_JUNIOR_PROJECTS_PATH
        or f"{os.environ['HOME']}/projects"
    )


def is_path_child_of(child_path: str, parent_path: str) -> bool:
    """
    Return True only when ``child_path`` is a genuine subdirectory of ``parent_path``.

    Uses ``os.path.commonpath`` to avoid false positives caused by shared string
    prefixes (e.g. ``/home/foo-projects/bar`` vs ``/home/foo``).

    Args:
        child_path:  Absolute path that may be a child.
        parent_path: Absolute path of the candidate parent directory.

    Returns:
        True if child_path is strictly inside parent_path, False otherwise.

    Examples:
        >>> is_path_child_of('/home/projects/foo/sub', '/home/projects/foo')
        True
        >>> is_path_child_of('/home/projects/foobar', '/home/projects/foo')
        False
        >>> is_path_child_of('/home/projects/foo', '/home/projects/foo')
        False
    """
    # Normalise both paths to remove any trailing slashes / double separators
    child_path = os.path.normpath(child_path)
    parent_path = os.path.normpath(parent_path)

    if child_path == parent_path:
        return False

    try:
        common = os.path.commonpath([child_path, parent_path])
    except ValueError:
        # commonpath raises ValueError on mixed absolute/relative on Windows
        return False

    return common == parent_path


def is_git_worktree(project_path: str) -> bool:
    """
    Return whether a project folder is a worktree.

    Two layouts are recognised:

    1. **Path based** — the folder lives under a ``.worktrees`` directory
       (``/projects/app/.worktrees/fix-login``). These copies may even keep a
       regular ``.git`` directory, so the path is checked first.
    2. **Linked worktree** — ``git worktree add`` keeps a ``.git`` **file**
       with a ``gitdir:`` line pointing to ``<main repo>/.git/worktrees/<name>``,
       whereas a regular repository keeps a ``.git`` **directory**.

    codx-junior creates a worktree per PR branch, and the worktree carries a copy
    of the tracked ``.codx/project.json`` (same ``project_id`` as its main
    repository). Discovering it would register a duplicate project serving a
    temporary branch checkout, so worktrees are always ignored.

    Args:
        project_path: Absolute path of the project root.

    Returns:
        True when the folder is a worktree, False otherwise.

    Examples:
        >>> is_git_worktree('/projects/app/.worktrees/codx-junior-fix-login')
        True
        >>> is_git_worktree('/projects/app-worktrees/codx-junior-fix-login')
        True
        >>> is_git_worktree('/projects/app')
        False
    """
    if not project_path:
        return False

    normalized_path = os.path.normpath(project_path)

    # Worktrees created under a ``.worktrees`` folder are ignored by path, so
    # they are skipped even when their ``.git`` looks like a regular repository
    path_segments = normalized_path.replace("\\", "/").split("/")
    if WORKTREE_FOLDER_NAME in path_segments:
        return True

    git_path = os.path.join(normalized_path, ".git")
    if not os.path.isfile(git_path):
        # No ``.git`` at all, or a regular repository (a directory)
        return False

    try:
        with open(git_path, "r", encoding="utf-8", errors="replace") as git_file:
            content = git_file.read().strip()
    except (IOError, OSError) as ex:
        logger.warning("Could not read %s: %s", git_path, ex)
        return False

    if not content.lower().startswith("gitdir:"):
        return False

    git_dir = content.split(":", 1)[1].strip().replace("\\", "/")
    # Submodules point to ``<main repo>/.git/modules/<name>``: they are real
    # projects, only the linked worktrees are ignored
    return GIT_WORKTREE_GITDIR_MARKER in f"/{git_dir}"


def find_all_projects(force: bool = False) -> Dict[str, CODXJuniorSettings]:
    """
    Return a mapping of project_id → CODXJuniorSettings for all discovered projects.

    Discovery runs in a background thread on the first call (or when ``force``
    is True).  Subsequent calls return the cached result immediately unless
    a refresh is forced.

    Args:
        force: When True, trigger a fresh discovery scan even if results are cached.

    Returns:
        Dictionary mapping project_id to CODXJuniorSettings.
    """
    global _ALL_PROJECTS_PROC
    global _ALL_PROJECTS

    with _DISCOVERY_LOCK:
        if _needs_scan(force):
            _start_scan_locked()
        proc = _ALL_PROJECTS_PROC

    if _ALL_PROJECTS is None and proc is not None:
        # Cold start: wait for the very first snapshot, but never for a minute.
        # The scan is bounded now and the caller is usually an HTTP request.
        logger.info("Waiting for the first project discovery snapshot.")
        proc.join(timeout=DISCOVERY_WAIT_SECONDS)

    return _ALL_PROJECTS


def get_all_projects_snapshot() -> Optional[Dict[str, CODXJuniorSettings]]:
    """
    Return the current discovery cache **without** starting or waiting for a scan.

    This is the safe entry point for request paths that must never block (for
    example the Traefik HTTP provider, which Traefik polls every 10 seconds
    with a 5 second timeout).  Callers that need fresh data should use
    :func:`find_all_projects` or schedule it in a background thread.
    """
    return _ALL_PROJECTS


def update_project_snapshot(settings: CODXJuniorSettings) -> None:
    """
    Insert or replace one project in the discovery cache.

    Used after a project's settings are saved so the Traefik route builder sees
    the new rules immediately, without waiting for a full discovery scan.
    Settings loaded from a worktree are ignored: they describe the main
    repository with the same ``project_id`` and must never replace it.
    """
    global _ALL_PROJECTS
    if settings is None or not getattr(settings, "project_id", None):
        return
    if is_git_worktree(getattr(settings, "abs_project_path", "")):
        logger.info(
            "Ignoring project snapshot for git worktree %s",
            settings.abs_project_path,
        )
        return
    if _ALL_PROJECTS is None:
        _ALL_PROJECTS = {}
    _ALL_PROJECTS[settings.project_id] = settings


def find_project_from_file_path(file_path: str) -> Optional[CODXJuniorSettings]:
    """
    Given an absolute file path, return the most-specific project that contains it.

    When multiple projects match (nested projects), the one with the longest
    ``abs_project_path`` (i.e. the deepest / most specific) is returned.

    Args:
        file_path: Absolute path to a file.

    Returns:
        The matching CODXJuniorSettings, or None if no project matches.
    """
    all_projects = find_all_projects().values()
    matches = [
        p for p in all_projects
        if is_path_child_of(file_path, p.abs_project_path)
        or is_path_child_of(file_path, p.codx_path)
    ]
    if matches:
        logger.info(
            "Find projects for file %s: %s",
            file_path,
            [m.project_name for m in matches],
        )
        return sorted(matches, key=lambda p: len(p.abs_project_path))[-1]
    return None


def find_project_by_id(project_id: str) -> Optional[CODXJuniorSettings]:
    """
    Look up a project by its unique identifier.

    Args:
        project_id: The project UUID string.

    Returns:
        Matching CODXJuniorSettings, or None if not found.
    """
    all_projects = find_all_projects().values()
    matches = [p for p in all_projects if p.project_id == project_id]
    return matches[0] if matches else None


def find_project_by_project_path(project_path: str) -> Optional[CODXJuniorSettings]:
    """
    Look up a project by its absolute filesystem path.

    Args:
        project_path: Absolute path to the project root.

    Returns:
        Matching CODXJuniorSettings, or None if not found.
    """
    all_projects = find_all_projects().values()
    matches = [p for p in all_projects if p.abs_project_path == project_path]
    return matches[0] if matches else None


def find_project_by_name(project_name: str) -> Optional[CODXJuniorSettings]:
    """
    Look up a project by its display name.

    Args:
        project_name: Human-readable project name.

    Returns:
        Matching CODXJuniorSettings, or None if not found.
    """
    all_projects = find_all_projects().values()
    matches = [p for p in all_projects if p.project_name == project_name]
    project = matches[0] if matches else None
    if not project:
        logger.warning("find_project_by_name '%s' not found", project_name)
    return project


def find_all_user_projects(user: CodxUser) -> Generator[CODXJuniorProject, None, None]:
    """
    Yield all projects accessible to the given user.

    For each project, the permission walk climbs parent projects until an
    authorised ancestor is found.  If no ancestor grants access, the project
    is skipped.

    Args:
        user: The authenticated user whose permissions are checked.

    Yields:
        CODXJuniorProject instances enriched with permission information.
    """
    user_security_manager = UserSecurityManager()
    all_projects = list(find_all_projects().values())

    def find_parent(project_path: str) -> Optional[CODXJuniorSettings]:
        """Return the most-specific parent project for the given path."""
        all_parents = [
            p for p in all_projects
            if is_path_child_of(project_path, p.abs_project_path)
        ]
        all_parents.sort(key=lambda p: len(p.abs_project_path))
        return all_parents[-1] if all_parents else None

    for settings in all_projects:
        current_settings: Optional[CODXJuniorSettings] = settings
        while current_settings:
            permissions = user_security_manager.get_user_project_access(
                user=user, settings=current_settings
            )
            if permissions:
                yield CODXJuniorProject(**{
                    **settings.__dict__,
                    "permissions": permissions,
                })
                current_settings = None
            else:
                current_settings = find_parent(current_settings.abs_project_path)


def get_project_dependencies(settings: CODXJuniorSettings) -> List[CODXJuniorSettings]:
    """
    Return all projects searched together with the given project.

    Child projects and declared dependency projects are resolved by
    ``CODXJuniorSettings.get_related_projects``: it honours
    ``search_related_projects``, resolves names on disk (sibling folder,
    absolute path or a folder inside the project) and deduplicates by
    ``codx_path``. Names that only the project catalogue knows (a dependency
    registered outside the project tree) are looked up as a fallback.

    Args:
        settings: The project whose related projects should be resolved.

    Returns:
        List of resolved CODXJuniorSettings, the current project excluded.
    """
    if not settings.search_related_projects:
        return []

    try:
        related: List[CODXJuniorSettings] = list(settings.get_related_projects())
    except Exception as ex:
        logger.warning(
            "Cannot resolve related projects for '%s': %s",
            settings.project_name,
            ex,
        )
        related = []

    seen = {settings.codx_path}
    seen.update(project.codx_path for project in related)
    for project_name in settings.get_dependency_names():
        project = find_project_by_name(project_name)
        if project and project.codx_path not in seen:
            seen.add(project.codx_path)
            related.append(project)

    logger.info(
        "get_project_dependencies for '%s': resolved %d dependencies.",
        settings.project_name,
        len(related),
    )
    return related


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _needs_scan(force: bool) -> bool:
    """Return True when a new discovery scan should be scheduled."""
    if force or _ALL_PROJECTS is None or _ALL_PROJECTS_UPDATED_AT is None:
        return True
    age = (datetime.now() - _ALL_PROJECTS_UPDATED_AT).total_seconds()
    return age >= DISCOVERY_TTL_SECONDS


def _start_scan_locked() -> None:
    """Start one scan. Must be called with ``_DISCOVERY_LOCK`` held."""
    global _ALL_PROJECTS_PROC, _SCAN_RUNNING
    if _SCAN_RUNNING:
        # A scan is already in flight: every caller shares its result instead of
        # launching a second filesystem walk.
        return
    _SCAN_RUNNING = True
    _ALL_PROJECTS_PROC = Thread(
        target=_run_discovery,
        name="ProjectDiscovery",
        daemon=True,
    )
    _ALL_PROJECTS_PROC.start()


def _run_discovery() -> None:
    """Thread target: run one scan and always release the single-flight flag."""
    global _ALL_PROJECTS_PROC, _SCAN_RUNNING, _ALL_PROJECTS_UPDATED_AT
    try:
        _update_all_projects()
    except Exception as ex:  # pragma: no cover - a scan must never kill the flag
        logger.exception("Project discovery failed: %s", ex)
    finally:
        with _DISCOVERY_LOCK:
            _ALL_PROJECTS_PROC = None
            _SCAN_RUNNING = False
            _ALL_PROJECTS_UPDATED_AT = datetime.now()


def get_project_scan_roots() -> List[str]:
    """
    Return the directories scanned for ``.codx/project.json``.

    The configured projects root, its parent and ``$HOME`` are scanned by
    default; extra roots can be added with ``CODX_JUNIOR_PROJECT_SCAN_ROOTS``
    (comma or path-separator separated). Roots already covered by a shallower
    one are dropped so a project is never walked twice.
    """
    candidates: List[str] = []

    configured = os.environ.get("CODX_JUNIOR_PROJECT_SCAN_ROOTS", "")
    for raw in configured.replace(os.pathsep, ",").split(","):
        raw = raw.strip()
        if raw:
            candidates.append(os.path.abspath(os.path.expanduser(raw)))

    projects_root = get_projects_root_path()
    if projects_root:
        projects_root = os.path.abspath(os.path.expanduser(projects_root))
        candidates.append(projects_root)
        candidates.append(os.path.dirname(projects_root))

    if CODX_JUNIOR_PROJECTS_PATH:
        candidates.append(os.path.abspath(CODX_JUNIOR_PROJECTS_PATH))

    home = os.environ.get("HOME")
    if home:
        candidates.append(os.path.abspath(home))

    roots: List[str] = []
    for root in candidates:
        if not root or not os.path.isdir(root) or root == os.sep:
            continue
        if root in roots:
            continue
        if any(is_path_child_of(root, existing) for existing in roots):
            continue
        roots.append(root)
    return roots


def _scan_codx_paths_roots() -> List[str]:
    """
    Walk the configured roots and return every ``<project>/.codx`` directory.

    Depth and directory count are bounded and heavy folders (``node_modules``,
    VCS metadata, virtualenvs, dot-directories) are pruned, so the scan costs a
    few milliseconds instead of the 10.7 s measured for ``find /``.
    """
    roots = get_project_scan_roots()
    found: List[str] = []
    seen = set()
    visited = 0

    for root in roots:
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dir_path, dir_names, _file_names in os.walk(root, followlinks=False):
            visited += 1
            if visited > MAX_SCAN_DIRS:
                logger.warning(
                    "Project discovery stopped after %d directories "
                    "(CODX_JUNIOR_PROJECT_SCAN_DIRS); configure "
                    "CODX_JUNIOR_PROJECT_SCAN_ROOTS to narrow the scan.",
                    MAX_SCAN_DIRS,
                )
                return found

            if ".codx" in dir_names:
                codx_path = os.path.join(dir_path, ".codx")
                if codx_path not in seen and os.path.isfile(
                    os.path.join(codx_path, "project.json")
                ):
                    seen.add(codx_path)
                    found.append(codx_path)

            depth = dir_path.rstrip(os.sep).count(os.sep) - root_depth
            if depth >= MAX_SCAN_DEPTH:
                dir_names[:] = []
                continue

            # ``.codx`` was already captured above: never descend into it
            dir_names[:] = [
                name for name in dir_names
                if name not in SCAN_SKIP_DIRS and not name.startswith(".")
            ]

    logger.info(
        "Project discovery: %d project files under %d roots (%d directories walked)",
        len(found),
        len(roots),
        visited,
    )
    return found


def _scan_codx_paths_with_find() -> List[str]:
    """
    Legacy full-filesystem scan (``find / -name .codx``).

    Kept only as an escape hatch for installations whose projects live outside
    the configured roots: it blocks for ~10 s and saturates the disk, so it is
    opt-in through ``CODX_JUNIOR_PROJECT_SCAN_MODE=find``.
    """
    import subprocess

    logger.warning(
        "Project discovery is using the legacy 'find /' scan "
        "(CODX_JUNIOR_PROJECT_SCAN_MODE=find); this blocks the filesystem."
    )
    result = subprocess.run(
        ["find", "/", "-name", ".codx"],
        cwd="/",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    all_codx_paths = result.stdout.decode("utf-8", errors="replace").split("\n")
    return [
        path for path in all_codx_paths
        if path and os.path.isfile(f"{path}/project.json")
    ]


def _scan_codx_paths() -> List[str]:
    """Return the ``.codx`` directories that hold a ``project.json``."""
    mode = os.environ.get("CODX_JUNIOR_PROJECT_SCAN_MODE", "roots").strip().lower()
    if mode == "find":
        return _scan_codx_paths_with_find()
    return _scan_codx_paths_roots()


@profile_function
def _update_all_projects() -> None:
    """
    Background worker that scans the filesystem for all ``.codx/project.json``
    files and populates the global ``_ALL_PROJECTS`` cache.

    Worktrees are skipped (see :func:`is_git_worktree`): they hold a copy of
    the tracked ``.codx/project.json`` with the same ``project_id`` as their
    main repository, so registering them would shadow the real project with a
    temporary PR branch checkout.

    Diagram:
    flowchart TD
        A[_update_all_projects] --> B[find / -name .codx]
        B --> C[Filter paths with project.json]
        C --> D{Worktree (.worktrees / linked)?}
        D -- Yes --> D1[Ignore path]
        D -- No --> E{R/W permissions?}
        E -- No --> E1[Skip path]
        E -- Yes --> F[Parse CODXJuniorSettings]
        F --> G{Valid project?}
        G -- Yes --> H[Register in all_projects dict]
        G -- No  --> I[Log and skip]
        H --> J[_ALL_PROJECTS = all_projects]
    """
    global _ALL_PROJECTS_PROC
    global _ALL_PROJECTS

    all_projects: Dict[str, CODXJuniorSettings] = {}
    projects_root_path = get_projects_root_path()
    user_security_manager = UserSecurityManager()

    paths = _scan_codx_paths()

    logger.info("[find_all_projects] projects root path: %s", projects_root_path)
    logger.info("[find_all_projects] found %d project files", len(paths))

    def is_valid_project(candidate: CODXJuniorSettings) -> bool:
        """
        Return True when the candidate project should be added to the registry.

        Duplicates that live outside the configured root are rejected in favour
        of the canonical copy inside the root.
        """
        if not candidate or not candidate.project_name:
            return False

        existing_project = all_projects.get(candidate.project_id)
        if existing_project:
            if not is_path_child_of(candidate.abs_project_path, projects_root_path):
                # Prefer projects inside the configured root on duplicates
                return False
        return True

    for codx_path in paths:
        try:
            project_root_path = os.path.dirname(os.path.normpath(codx_path))
            if is_git_worktree(project_root_path):
                # PR worktrees are temporary branch checkouts of a project that
                # is already registered: never treat them as projects
                logger.info(
                    "[find_all_projects] ignoring worktree at %s",
                    project_root_path,
                )
                continue

            project_file_path = f"{codx_path}/project.json"
            # Verify that the process has read/write access before loading
            if not (
                os.access(project_file_path, os.R_OK)
                and os.access(project_file_path, os.W_OK)
            ):
                logger.warning(
                    "Skipping %s due to insufficient permissions.", project_file_path
                )
                continue

            settings = CODXJuniorProject(
                **CODXJuniorSettings.from_project_file(project_file_path).__dict__
            )
            if is_valid_project(settings):
                project_users = user_security_manager.get_users_with_project_access(
                    project_id=settings.project_id
                )
                settings.users = project_users
                all_projects[settings.project_id] = settings
            else:
                logger.error(
                    "Invalid or duplicate project at: %s", settings.abs_project_path
                )
        except Exception as exc:
            logger.exception("Error loading project %s: %s", codx_path, exc)

    _ALL_PROJECTS = all_projects


def find_project_parents(
    project: CODXJuniorSettings,
    user: Optional[CodxUser] = None,
) -> List[CODXJuniorSettings]:
    """
    Return all ancestor projects for the given project, ordered from root to leaf.

    When a ``user`` is provided only projects accessible to that user are
    considered as potential ancestors.

    Args:
        project: The project whose ancestors should be found.
        user:    Optional user for permission-filtered ancestor discovery.

    Returns:
        List of ancestor CODXJuniorSettings sorted by path length (shallowest first).
    """
    all_candidates = (
        list(find_all_user_projects(user))
        if user
        else list(find_all_projects().values())
    )
    project_path = project.abs_project_path
    all_parents = [
        p for p in all_candidates
        if is_path_child_of(project_path, p.abs_project_path)
    ]
    return sorted(all_parents, key=lambda p: len(p.abs_project_path))


def find_active_projects() -> List[CODXJuniorSettings]:
    """
    Return all projects that have been accessed within the last 30 minutes.

    Returns:
        List of recently active CODXJuniorSettings instances.
    """
    last_access = datetime.now() - timedelta(minutes=30)
    return [
        p for p in find_all_projects().values()
        if getattr(p, "last_access_time", None)
        and p.last_access_time >= str(last_access)
    ]

# Made with ❤️ by codx-junior