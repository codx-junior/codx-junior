"""Per-task git workspaces: isolated checkouts for autonomous agent work.

The problem this module solves is not git: it is *ownership*. An autonomous
agent that writes into the main checkout mixes its changes with every other
task, with the human's edits and with runtime state. By the time a reviewer
looks at `git status`, the diff is a single pile and no commit can be attributed
to the work that produced it.

Each task tree (a chat and its descendants, identified by ``root_id``) gets:

* a dedicated branch ``codx/task/<slug>-<hash>`` cut from the current branch,
* a git worktree under ``<git_worktrees_path>/tasks/<slug>-<hash>``,
* a registry entry in the project data plane (``.codx/task_workspaces``).

Periodic autonomy shifts are **not** task trees. Every shift creates a new card,
so a card-derived key produced a new branch and a full worktree on every run.
Actor shifts instead use one stable scope:

* branch ``agent/shift/<actor>``,
* worktree ``<git_worktrees_path>/tasks/shifts/<actor>``,
* one registry entry keyed by the actor.

At the end of a successful shift :meth:`TaskWorkspaceManager.finalize_actor`
commits any remaining work, merges that branch into its base with ``--no-ff``
and resets the checkout to the merged base. A conflict keeps the single actor
branch for review; it never creates a second one.

An autonomous turn runs with a *clone* of the project settings whose
``abs_project_path`` is the worktree, so every file and git tool sees the
isolated checkout. The main checkout stays exactly as it was. At the end of the
turn the workspace is committed on its branch, which is what the Git view
reviews, merges or discards. The clone keeps ``codx_path`` untouched: chats,
task cards, settings and the audit journal stay in the project.

Safety rules:
    * Disabled by ``task_workspace_isolation=False`` (settings) or a non-git
      project: the caller falls back to the main checkout.
    * A workspace is never created for a chat that already has one; the same
      task keeps its branch across turns.
    * Heavy dependency folders (``node_modules``, ``.venv``…) are symlinked from
      the main checkout so a turn can run the test suite without reinstalling;
      gitignored env files are copied so the agent has the same runtime
      configuration. Nothing is copied that git would track.
    * Commits never use ``--no-verify``: the CISO pre-commit gate still runs.
    * Registry writes are atomic and guarded by a cross-process lock, so several
      API workers can request the same workspace safely.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codx.junior.runtime import data_dir
from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso import secrets as ciso_secrets

logger = logging.getLogger(__name__)

#: Folder under the project data plane holding the workspace registry.
REGISTRY_DIR_NAME = "task_workspaces"
REGISTRY_FILE_NAME = "index.json"
LOCK_FILE_NAME = "index.lock"

#: Branch namespace of the per-task workspaces.
BRANCH_PREFIX = "codx/task"

#: Branch namespace of the stable per-actor shift workspaces. Every shift of one
#: actor commits to the same branch instead of creating a branch per shift card.
ACTOR_BRANCH_PREFIX = "agent/shift"

#: Folder under ``settings.get_worktrees_path()`` holding the checkouts.
WORKTREES_SUBDIR = "tasks"

#: Folder under the worktrees base holding the stable actor checkouts.
ACTOR_WORKTREES_SUBDIR = "shifts"

#: Workspace scopes. ``task`` keeps the historical per-task-card branch;
#: ``actor`` is the bounded, reusable branch of a periodic actor shift.
SCOPE_TASK = "task"
SCOPE_ACTOR = "actor"

#: Dependency folders that are safe to share through a symlink. Caches are not
#: linked: they are generated on every test run and used to be committed as
#: absolute symlinks by ``git add -A``.
LINKED_DEPENDENCY_DIRS = (
    "node_modules",
    ".venv",
    "venv",
)

#: Generated paths that are ignored in every workspace checkout. The patterns
#: are written to the worktree's ``info/exclude`` because a repository cannot
#: ship a .gitignore for a checkout it does not know about.
CACHE_EXCLUDES = (
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pnpm-store/",
    ".yarn/",
    ".tox/",
    "__pycache__/",
    ".coverage",
    "htmlcov/",
)

#: Gitignored runtime configuration copied into the worktree.
COPIED_ENV_FILES = (".env", ".env.local", ".env.development", ".env.test")

#: Bound for the git subprocesses.
GIT_TIMEOUT_SECONDS = 120

#: A turn commit subject longer than this is truncated (50/72 rule).
COMMIT_SUBJECT_MAX = 50

_MODULE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
@dataclass
class TaskWorkspace:
    """One task checkout, as registered for the project."""

    key: str
    chat_id: str = ""
    root_id: str = ""
    name: str = ""
    scope: str = SCOPE_TASK
    actor: str = ""
    branch: str = ""
    path: str = ""
    base_ref: str = ""
    created_at: str = ""
    last_used_at: str = ""
    last_commit: str = ""
    turns: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe registry entry."""
        return {
            "key": self.key,
            "chat_id": self.chat_id,
            "root_id": self.root_id,
            "name": self.name,
            "scope": self.scope,
            "actor": self.actor,
            "branch": self.branch,
            "path": self.path,
            "base_ref": self.base_ref,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "last_commit": self.last_commit,
            "turns": self.turns,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskWorkspace":
        """Build a record from persisted JSON."""
        known = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in (data or {}).items() if key in known})

    def public(self) -> Dict[str, Any]:
        """Return the entry plus its on-disk state, for the UI."""
        payload = self.to_dict()
        payload["exists"] = bool(self.path and os.path.isdir(self.path))
        return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    """Return the current UTC timestamp as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, limit: int = 40) -> str:
    """Return a filesystem/branch safe slug of *value*."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "task")).strip("-._")
    text = text or "task"
    return text[:limit].strip("-._") or "task"


def _hash(value: str) -> str:
    """Return a short stable hash used to keep workspace names unique."""
    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:8]


def _run_git(cwd: str, args: List[str], timeout: int = GIT_TIMEOUT_SECONDS) -> tuple:
    """Run git in *cwd* without a shell.

    Returns:
        tuple: ``(ok, stdout, stderr)``.
    """
    command = ["git", "--no-pager", "-c", "core.quotepath=false", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        return False, "", str(ex)
    return completed.returncode == 0, completed.stdout, completed.stderr


def _git_ok(cwd: str, args: List[str]) -> bool:
    """Return True when a quiet git command succeeds."""
    ok, _stdout, _stderr = _run_git(cwd, args)
    return ok


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class TaskWorkspaceManager:
    """Create, resolve and commit the isolated worktree of a task tree."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.codx_path = str(getattr(settings, "codx_path", "") or "")
        self.project_path = str(getattr(settings, "abs_project_path", "") or "")
        self.registry_dir = (
            data_dir(self.codx_path, REGISTRY_DIR_NAME) if self.codx_path else ""
        )
        self.registry_path = (
            os.path.join(self.registry_dir, REGISTRY_FILE_NAME)
            if self.registry_dir
            else ""
        )
        self.lock_path = (
            os.path.join(self.registry_dir, LOCK_FILE_NAME)
            if self.registry_dir
            else ""
        )

    # -- configuration -----------------------------------------------------
    @property
    def enabled(self) -> bool:
        """Return True when per-task isolation is enabled for this project."""
        if not bool(getattr(self.settings, "task_workspace_isolation", True)):
            return False
        return bool(self.codx_path and self.project_path)

    def repo_root(self) -> str:
        """Return the git root of the project checkout, or ``""``."""
        ok, stdout, _stderr = _run_git(
            self.project_path, ["rev-parse", "--show-toplevel"]
        )
        return stdout.strip() if ok and stdout.strip() else ""

    def _worktrees_base(self) -> str:
        """Return the folder that holds all task worktrees."""
        getter = getattr(self.settings, "get_worktrees_path", None)
        if callable(getter):
            try:
                base = str(getter() or "")
            except Exception:
                base = ""
        else:
            base = str(getattr(self.settings, "git_worktrees_path", "") or "")
        if not base:
            project = self.project_path.rstrip("/") or "project"
            base = os.path.join(
                os.path.dirname(project), f"{os.path.basename(project)}-worktrees"
            )
        return os.path.join(base, WORKTREES_SUBDIR)

    # -- registry ----------------------------------------------------------
    def _load_registry(self) -> Dict[str, Any]:
        """Load the registry (empty when missing or corrupt)."""
        if not self.registry_path:
            return {}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as ex:
            logger.warning("Task workspaces: unreadable registry: %s", ex)
            return {}
        workspaces = data.get("workspaces") if isinstance(data, dict) else None
        return workspaces if isinstance(workspaces, dict) else {}

    def _save_registry(self, workspaces: Dict[str, Any]) -> None:
        """Persist the registry atomically."""
        if not self.registry_path:
            return
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        payload = {"version": 1, "updated_at": _utcnow(), "workspaces": workspaces}
        temporary = f"{self.registry_path}.tmp-{os.getpid()}-{threading.get_ident()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.registry_path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass

    @contextmanager
    def _registry_lock(self):
        """Serialise registry access inside and across processes."""
        handle = None
        try:
            import fcntl  # Linux containers; the feature is Linux-only by design
        except ImportError:  # pragma: no cover - Windows
            fcntl = None
        if fcntl is not None and self.lock_path:
            try:
                os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
                handle = open(self.lock_path, "a+", encoding="utf-8")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                if handle is not None:
                    handle.close()
                handle = None
        with _MODULE_LOCK:
            try:
                yield
            finally:
                if handle is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except Exception:  # pragma: no cover
                        pass
                    try:
                        handle.close()
                    except Exception:  # pragma: no cover
                        pass

    # -- naming ------------------------------------------------------------
    def key_for(self, chat: Any) -> str:
        """Return the stable workspace key of *chat* (its task tree root)."""
        root_id = str(getattr(chat, "root_id", "") or "").strip()
        chat_id = str(getattr(chat, "id", "") or "").strip()
        key = root_id or chat_id or str(getattr(chat, "name", "") or "")
        return key or "task"

    @staticmethod
    def actor_key(actor: str) -> str:
        """Return the stable workspace key of one actor.

        The key is deliberately **not** derived from the shift card: every
        shift of an actor must resolve to the same workspace and branch.
        """
        return f"{SCOPE_ACTOR}:{_slug(actor or 'shift', 80).replace('..', '-')}"

    def _branch_for(self, chat: Any, key: str) -> str:
        """Return the branch name of the task workspace."""
        name = _slug(getattr(chat, "name", "") or "task")
        return f"{BRANCH_PREFIX}/{name}-{_hash(key)}"

    def _path_for(self, chat: Any, key: str) -> str:
        """Return the worktree path of the task workspace."""
        name = _slug(getattr(chat, "name", "") or "task")
        return os.path.join(self._worktrees_base(), f"{name}-{_hash(key)}")

    def _identity(
        self, chat: Any, scope: str = SCOPE_TASK, actor: str = ""
    ) -> tuple:
        """Return ``(key, branch, path, name)`` for a workspace request.

        Task workspaces keep the historical branch per task tree. Actor
        workspaces are one stable branch and checkout per actor, so a periodic
        shift can never add another branch to the repository.
        """
        if scope == SCOPE_ACTOR:
            owner = str(actor or "shift").strip() or "shift"
            slug = _slug(owner, 80).replace("..", "-") or "actor"
            return (
                self.actor_key(owner),
                f"{ACTOR_BRANCH_PREFIX}/{slug}",
                os.path.join(
                    self._worktrees_base(), ACTOR_WORKTREES_SUBDIR, slug
                ),
                f"Shift · {owner}",
            )
        key = self.key_for(chat)
        return (
            key,
            self._branch_for(chat, key),
            self._path_for(chat, key),
            str(getattr(chat, "name", "") or ""),
        )

    # -- base ref ----------------------------------------------------------
    def _base_ref(self, repo_root: str, requested: Optional[str] = None) -> str:
        """Return the ref a new task branch is cut from."""
        if requested:
            return requested
        ok, stdout, _stderr = _run_git(repo_root, ["branch", "--show-current"])
        current = stdout.strip() if ok else ""
        if current and not current.startswith(
            (f"{BRANCH_PREFIX}/", f"{ACTOR_BRANCH_PREFIX}/")
        ):
            return current
        # The main checkout is already on a task branch: use the repository's
        # default branch rather than stacking one task on another.
        for candidate in ("origin/HEAD", "main", "master"):
            if _git_ok(repo_root, ["rev-parse", "--verify", f"{candidate}^{{commit}}"]):
                return candidate
        return "HEAD"

    # -- checkout preparation ---------------------------------------------
    def _ignore_worktree_caches(self, worktree_path: str) -> None:
        """Exclude generated caches from every commit made in *worktree_path*.

        The repository cannot carry an ignore rule for a checkout it did not
        create, and ``LINKED_DEPENDENCY_DIRS`` used to link ``.pytest_cache``
        into the worktree. ``git add -A`` then committed that absolute symlink
        on every shift. The worktree-local exclude makes the rule durable even
        for caches that a test run creates later.
        """
        ok, stdout, _stderr = _run_git(
            worktree_path, ["rev-parse", "--git-path", "info/exclude"]
        )
        relative = stdout.strip() if ok else ""
        if not relative:
            return
        exclude_path = (
            relative
            if os.path.isabs(relative)
            else os.path.join(worktree_path, relative)
        )
        header = "# codx-junior task workspace: generated caches are never committed"
        try:
            os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
            existing = ""
            if os.path.isfile(exclude_path):
                with open(exclude_path, "r", encoding="utf-8") as handle:
                    existing = handle.read()
            missing = [
                pattern
                for pattern in CACHE_EXCLUDES
                if pattern not in existing.splitlines()
            ]
            if not missing:
                return
            with open(exclude_path, "a", encoding="utf-8") as handle:
                if header not in existing:
                    handle.write(f"\n{header}\n")
                for pattern in missing:
                    handle.write(f"{pattern}\n")
        except OSError as ex:  # pragma: no cover - best effort
            logger.debug(
                "Task workspaces: cannot write exclude for %s: %s",
                worktree_path,
                ex,
            )

    def _prepare_checkout(self, worktree_path: str) -> None:
        """Link dependencies and copy gitignored env files into the worktree.

        The worktree shares history with the main checkout but not the ignored
        artefacts that make it runnable. Without this, an agent asked to run the
        tests would have to reinstall the world on every task.
        """
        # Older workspaces linked .pytest_cache/.mypy_cache into the checkout.
        # Remove those links so they can never be staged again; the cache is
        # created locally by the test run and is covered by the exclude below.
        for name in CACHE_EXCLUDES:
            target = os.path.join(worktree_path, name.rstrip("/"))
            if os.path.islink(target):
                try:
                    os.unlink(target)
                except OSError as ex:  # pragma: no cover - best effort
                    logger.debug(
                        "Task workspaces: cannot remove stale cache link %s: %s",
                        target,
                        ex,
                    )
        for name in LINKED_DEPENDENCY_DIRS:
            source = os.path.join(self.project_path, name)
            target = os.path.join(worktree_path, name)
            if not os.path.isdir(source) or os.path.lexists(target):
                continue
            try:
                os.symlink(source, target, target_is_directory=True)
            except OSError as ex:
                logger.debug("Task workspaces: cannot link %s: %s", source, ex)
        for name in COPIED_ENV_FILES:
            source = os.path.join(self.project_path, name)
            target = os.path.join(worktree_path, name)
            if not os.path.isfile(source) or os.path.lexists(target):
                continue
            # Copy only what git ignores: a tracked .env would be committed by
            # the turn commit and that is exactly the incident CISO prevents.
            if not _git_ok(worktree_path, ["check-ignore", "-q", name]):
                logger.debug(
                    "Task workspaces: not copying %s (not ignored by git)", name
                )
                continue
            try:
                # Create the destination with owner-only permissions: the file
                # holds credentials and the worktree may be readable by other
                # local users. ``copyfile`` would inherit the umask instead.
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with open(source, "rb") as source_handle, os.fdopen(
                    descriptor, "wb"
                ) as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
            except OSError as ex:
                logger.debug("Task workspaces: cannot copy %s: %s", name, ex)
        self._ignore_worktree_caches(worktree_path)

    # -- creation ----------------------------------------------------------
    def ensure(
        self,
        chat: Any,
        base_branch: Optional[str] = None,
        *,
        scope: str = SCOPE_TASK,
        actor: str = "",
    ) -> Optional["TaskWorkspace"]:
        """Return (creating it when needed) the workspace of *chat*.

        Args:
            chat: Chat/task card. ``root_id`` identifies the task tree, so every
                step/delegated card of the same run shares one workspace.
            base_branch: Optional base ref (defaults to the current branch).
            scope: ``task`` for a per-task-card workspace, ``actor`` for the
                stable workspace reused by every shift of one actor.
            actor: Actor/profile name; required for ``scope="actor"``.

        Returns:
            TaskWorkspace | None: ``None`` when isolation is disabled, the
            project is not a git repository or the worktree cannot be created.
            The caller then falls back to the main checkout.
        """
        if not self.enabled:
            return None
        repo_root = self.repo_root()
        if not repo_root:
            return None
        # Only isolate a project that IS the repository root. A project nested
        # inside a bigger repository would get a worktree of the whole repo and
        # its "isolated" changes would include every sibling project; falling
        # back to the main checkout is the safe behaviour there.
        if os.path.realpath(repo_root) != os.path.realpath(self.project_path):
            logger.debug(
                "Task workspaces: %s is a subdirectory of %s; not isolating",
                self.project_path,
                repo_root,
            )
            return None
        scope = scope if scope in (SCOPE_TASK, SCOPE_ACTOR) else SCOPE_TASK
        actor = str(actor or "").strip()
        key, branch, path, name = self._identity(chat, scope, actor)
        with self._registry_lock():
            registry = self._load_registry()
            record = registry.get(key)
            if record:
                workspace = TaskWorkspace.from_dict(record)
                if workspace.path and os.path.isdir(workspace.path) and _git_ok(
                    workspace.path, ["rev-parse", "--is-inside-work-tree"]
                ):
                    # The main checkout may have installed dependencies since
                    # the worktree was created: make sure the links exist.
                    self._prepare_checkout(workspace.path)
                    if workspace.scope == SCOPE_ACTOR:
                        self._sync_actor_checkout(workspace)
                    workspace.last_used_at = _utcnow()
                    workspace.chat_id = str(getattr(chat, "id", "") or workspace.chat_id)
                    registry[key] = workspace.to_dict()
                    self._save_registry(registry)
                    return workspace
                # The registry survived but the checkout is gone: recreate it
                # from the branch when it still exists.
                registry.pop(key, None)

            base = self._base_ref(repo_root, base_branch)
            if os.path.exists(path):
                # Never delete an unknown directory: a stale worktree is a
                # human decision in the Git view, not something a turn may wipe.
                logger.warning(
                    "Task workspaces: %s already exists but is not a registered "
                    "worktree; falling back to the main checkout", path
                )
                return None
            os.makedirs(os.path.dirname(path), exist_ok=True)
            branch_exists = _git_ok(
                repo_root, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"]
            )
            if branch_exists:
                ok, _stdout, error = _run_git(repo_root, ["worktree", "add", path, branch])
                # A task branch is its own base after a crash (historical
                # behaviour). An actor branch always keeps the project base so
                # finalize knows where to merge it.
                if scope != SCOPE_ACTOR:
                    base = branch
            else:
                ok, _stdout, error = _run_git(
                    repo_root, ["worktree", "add", "-b", branch, path, base]
                )
            if not ok:
                logger.warning(
                    "Task workspaces: cannot create %s (%s): %s", branch, path, error
                )
                return None

            self._prepare_checkout(path)
            workspace = TaskWorkspace(
                key=key,
                chat_id=str(getattr(chat, "id", "") or ""),
                root_id=str(getattr(chat, "root_id", "") or key),
                name=str(name or getattr(chat, "name", "") or "")[:200],
                scope=scope,
                actor=actor if scope == SCOPE_ACTOR else "",
                branch=branch,
                path=path,
                base_ref=base,
                created_at=_utcnow(),
                last_used_at=_utcnow(),
            )
            registry[key] = workspace.to_dict()
            self._save_registry(registry)
            logger.info(
                "Task workspaces: %s runs in %s (branch %s)",
                workspace.name or key,
                path,
                branch,
            )
            return workspace

    def _sync_actor_checkout(self, workspace: "TaskWorkspace") -> None:
        """Fast-forward an idle actor checkout to its base branch.

        Between two shifts the main checkout advances. When the actor branch
        holds no unmerged commit (the previous shift was merged and reset), it
        must start from the current base instead of stale code. A branch with
        unmerged work is left untouched: that work is still waiting to be
        finalized or reviewed.
        """
        if workspace.scope != SCOPE_ACTOR or not workspace.path:
            return
        ok, status, _stderr = _run_git(workspace.path, ["status", "--porcelain"])
        if not ok or status.strip():
            return
        base = workspace.base_ref or "main"
        ok, ahead, _stderr = _run_git(
            self.repo_root(), ["rev-list", "--count", f"{base}..{workspace.branch}"]
        )
        if not ok or str(ahead).strip() not in ("", "0"):
            return
        if _git_ok(workspace.path, ["rev-parse", "--verify", f"{base}^{{commit}}"]):
            _run_git(workspace.path, ["reset", "--hard", base])

    def find(
        self,
        chat: Any,
        *,
        scope: str = SCOPE_TASK,
        actor: str = "",
    ) -> Optional["TaskWorkspace"]:
        """Return the registered workspace of *chat* without creating it."""
        key = self._identity(chat, scope, actor)[0]
        return self._find_by_key(key)

    def find_actor(self, actor: str) -> Optional["TaskWorkspace"]:
        """Return the stable workspace of *actor* without creating it."""
        return self._find_by_key(self.actor_key(actor))

    def _find_by_key(self, key: str) -> Optional["TaskWorkspace"]:
        """Return the registered workspace identified by *key*."""
        record = self._load_registry().get(key)
        if not record:
            return None
        workspace = TaskWorkspace.from_dict(record)
        if not workspace.path or not os.path.isdir(workspace.path):
            return None
        return workspace

    def list(self) -> List[Dict[str, Any]]:
        """Return the registered workspaces with their on-disk state."""
        registry = self._load_registry()
        entries = []
        for record in registry.values():
            try:
                entries.append(TaskWorkspace.from_dict(record).public())
            except (TypeError, ValueError):
                continue
        entries.sort(key=lambda item: item.get("last_used_at") or "", reverse=True)
        return entries

    # -- settings clone ----------------------------------------------------

    # Attributes the agent runtime attaches to the settings object for the span
    # of one turn (see ``ai/smol/smol_agent.py``): the cancellation token owns a
    # live ``asyncio.Event`` and the session owns live futures, so a deep copy of
    # them raises ``TypeError: cannot pickle '_asyncio.Future' object``. They must
    # never travel into a clone -- and because ``settings_for`` clones the parent
    # settings, an active token would otherwise kill the FIRST turn of every
    # delegated card and every run step with exactly that error.
    RUNTIME_SETTINGS_ATTRS = (
        "_active_cancellation_token",
        "_active_session",
    )

    def _detach_runtime_attrs(self, source: Any) -> Dict[str, Any]:
        """Detach the runtime-only settings attributes so *source* can be copied."""
        detached: Dict[str, Any] = {}
        for attr in self.RUNTIME_SETTINGS_ATTRS:
            try:
                if not hasattr(source, attr):
                    continue
                detached[attr] = getattr(source, attr)
                delattr(source, attr)
            except Exception:  # pragma: no cover - defensive
                continue
        return detached

    def _reattach_runtime_attrs(self, source: Any, detached: Dict[str, Any]) -> None:
        """Restore the runtime-only settings attributes on the original object."""
        for attr, value in detached.items():
            try:
                setattr(source, attr, value)
            except Exception:  # pragma: no cover - defensive
                logger.warning(
                    "TaskWorkspace: could not restore settings attribute %s", attr
                )

    def settings_for(self, workspace: "TaskWorkspace", project_settings: Any = None) -> Any:
        """Return a settings clone whose project root is the worktree.

        The clone keeps ``codx_path`` (chats, boards, settings, audit) and the
        project identity. Only the checkout root changes, which is what makes
        every file/git tool operate on the isolated tree. Runtime-only turn
        state (cancellation token, session) is never copied: the worker turn
        installs its own.
        """
        source = project_settings or self.settings
        detached = self._detach_runtime_attrs(source)
        try:
            try:
                clone = source.model_copy(deep=True)
            except Exception:  # pragma: no cover - non-pydantic test doubles
                clone = copy.deepcopy(source)
        finally:
            self._reattach_runtime_attrs(source, detached)
        clone.abs_project_path = workspace.path
        clone.project_path = workspace.path
        # The worktree is the root of its own checkout: without this the git
        # engine would resolve the parent project and write in the main tree.
        clone.is_git_root = True
        clone.watching = False
        # A nested workspace would be nonsense and could recurse.
        clone.task_workspace_isolation = False
        return clone

    # -- committing a turn -------------------------------------------------
    def _commit_message(self, workspace: "TaskWorkspace", chat: Any, message: str = "") -> str:
        """Build the commit message of one agent turn."""
        policy = ciso_policy.resolve_policy(self.settings)
        task_name = str(getattr(chat, "name", "") or workspace.name or "task")
        subject = message.strip().splitlines()[0] if message.strip() else ""
        subject = subject or f"agent: {task_name}"
        try:
            subject, _replacements = ciso_secrets.redact_text(
                subject.replace("\n", " "), policy
            )
        except Exception:  # pragma: no cover - redaction must never block a commit
            subject = task_name
        subject = subject.strip()[:COMMIT_SUBJECT_MAX] or "agent: task"
        body = [
            f"Task: {task_name}",
            f"Chat: {getattr(chat, 'id', '') or workspace.chat_id}",
            f"Workspace: {workspace.branch}",
            f"Turn: {workspace.turns + 1}",
        ]
        if message.strip():
            body += ["", message.strip()[:4000]]
        full_message = subject + "\n\n" + "\n".join(body)
        try:
            full_message, _replacements = ciso_secrets.redact_text(full_message, policy)
        except Exception:  # pragma: no cover
            pass
        return full_message

    def _identity_args(self) -> List[str]:
        """Return ``-c user.name/user.email`` for the platform identity."""
        name = ""
        email = ""
        getter = getattr(self.settings, "get_git_identity", None)
        if callable(getter):
            try:
                identity = getter() or {}
                name = str(identity.get("name") or "")
                email = str(identity.get("email") or "")
            except Exception:
                pass
        name = name or str(getattr(self.settings, "git_user_name", "") or "")
        email = email or str(getattr(self.settings, "git_user_email", "") or "")
        if not name:
            name = "codx-junior"
        if not email:
            email = "codx-junior@localhost"
        return ["-c", f"user.name={name}", "-c", f"user.email={email}"]

    def commit(
        self,
        chat: Any,
        message: str = "",
        *,
        allow_empty: bool = False,
        scope: str = SCOPE_TASK,
        actor: str = "",
    ) -> Dict[str, Any]:
        """Commit the current worktree state on the task or actor branch.

        Args:
            chat: Chat/task card that owns the workspace.
            message: Optional extra context appended to the commit body.
            allow_empty: Commit even when there is nothing to stage.
            scope: ``task`` (default) or ``actor``.
            actor: Actor/profile name for ``scope="actor"``.

        Returns:
            dict: ``{committed, branch, commit, error, workspace}``. Commit
            failures are reported, never raised: the turn already happened and
            a dirty worktree is reviewable in the Git view.
        """
        workspace = self.find(chat, scope=scope, actor=actor)
        if workspace is None:
            return {"committed": False, "error": "no workspace registered"}
        ok, status, error = _run_git(workspace.path, ["status", "--porcelain"])
        if not ok:
            return {"committed": False, "error": error, "workspace": workspace.to_dict()}
        if not status.strip() and not allow_empty:
            return {
                "committed": False,
                "branch": workspace.branch,
                "error": "",
                "workspace": workspace.to_dict(),
            }
        ok, _stdout, error = _run_git(workspace.path, ["add", "-A"])
        if not ok:
            return {"committed": False, "error": error, "workspace": workspace.to_dict()}
        # ``git add -A`` is intentionally broad (the agent can create files),
        # but generated caches are excluded in the worktree. Keep the check as a
        # second line of defence: a cache-only turn must not create a commit.
        staged_ok, staged, staged_error = _run_git(
            workspace.path, ["diff", "--cached", "--name-only"]
        )
        if not staged_ok:
            return {
                "committed": False,
                "error": staged_error,
                "workspace": workspace.to_dict(),
            }
        if not staged.strip() and not allow_empty:
            return {
                "committed": False,
                "branch": workspace.branch,
                "error": "",
                "workspace": workspace.to_dict(),
            }
        commit_message = self._commit_message(workspace, chat, message)
        args = [*self._identity_args(), "commit", "-m", commit_message]
        if allow_empty:
            args.append("--allow-empty")
        ok, _stdout, error = _run_git(workspace.path, args)
        if not ok:
            logger.warning(
                "Task workspaces: commit failed in %s: %s", workspace.branch, error
            )
            return {
                "committed": False,
                "branch": workspace.branch,
                "error": (error or "").strip(),
                "workspace": workspace.to_dict(),
            }
        head_ok, head_out, _ = _run_git(workspace.path, ["rev-parse", "HEAD"])
        commit = head_out.strip() if head_ok else ""
        with self._registry_lock():
            registry = self._load_registry()
            record = registry.get(workspace.key)
            if record:
                current = TaskWorkspace.from_dict(record)
                current.turns += 1
                current.last_commit = commit
                current.last_used_at = _utcnow()
                registry[workspace.key] = current.to_dict()
                self._save_registry(registry)
                workspace = current
        return {
            "committed": True,
            "branch": workspace.branch,
            "commit": commit,
            "error": "",
            "workspace": workspace.to_dict(),
        }

    # -- actor shift lifecycle ---------------------------------------------
    def finalize_actor(
        self,
        actor: str,
        chat: Any = None,
        *,
        merge: bool = True,
    ) -> Dict[str, Any]:
        """Settle the stable branch of *actor* after a successful shift.

        The work is committed (when a dirty checkout is left), merged into the
        workspace base branch with ``--no-ff`` and the actor checkout is reset to
        the merged base. That keeps exactly one branch and one worktree per
        actor, with no per-shift branches and no stranded commits.

        A conflict or a missing workspace is **not** destructive: the branch
        and worktree are kept and the error is returned so the shift can be
        reported as failed instead of silently losing work.

        Args:
            actor: Actor/profile name.
            chat: Shift card used for the commit message; optional when clean.
            merge: Merge the branch into its base when it has unique commits.

        Returns:
            dict: ``{merged, branch, base, commit, ahead, reset, error}``.
        """
        workspace = self.find_actor(actor)
        if workspace is None:
            return {"merged": False, "error": "", "branch": "", "base": ""}
        result: Dict[str, Any] = {
            "merged": False,
            "branch": workspace.branch,
            "base": workspace.base_ref,
            "commit": "",
            "ahead": 0,
            "reset": False,
            "error": "",
        }

        ok, status, error = _run_git(workspace.path, ["status", "--porcelain"])
        if not ok:
            result["error"] = error
            return result
        if status.strip():
            if chat is None:
                result["error"] = "actor workspace has uncommitted changes"
                return result
            committed = self.commit(
                chat, scope=SCOPE_ACTOR, actor=actor
            )
            if committed.get("error"):
                result["error"] = committed["error"]
                return result

        base = workspace.base_ref or "main"
        result["base"] = base
        ok, ahead_out, error = _run_git(
            self.repo_root(), ["rev-list", "--count", f"{base}..{workspace.branch}"]
        )
        if not ok:
            result["error"] = error
            return result
        try:
            ahead = int(str(ahead_out).strip() or 0)
        except ValueError:
            ahead = 0
        result["ahead"] = ahead

        if ahead == 0:
            # The branch is already part of the base (or still unborn): move
            # the checkout to the base so the next shift starts from current
            # code. A dirty worktree was committed above, so this is safe.
            if _git_ok(workspace.path, ["rev-parse", "--verify", f"{base}^{{commit}}"]):
                reset_ok, _stdout, _stderr = _run_git(
                    workspace.path, ["reset", "--hard", base]
                )
                result["reset"] = reset_ok
            return result

        if not merge:
            return result

        repo_root = self.repo_root()
        ok, current, _stderr = _run_git(repo_root, ["branch", "--show-current"])
        if not ok or current.strip() != base:
            result["error"] = (
                f"base branch '{base}' is not checked out; actor branch kept "
                "for review"
            )
            return result

        message = (
            f"agent: merge shift {actor} "
            f"({_utcnow()[:16].replace('T', ' ')})"
        )
        ok, _stdout, error = _run_git(
            repo_root,
            [
                *self._identity_args(),
                "merge",
                "--no-ff",
                "-m",
                message,
                workspace.branch,
            ],
        )
        if not ok:
            # Never leave a half-finished merge in the user's checkout.
            _run_git(repo_root, ["merge", "--abort"])
            result["error"] = (error or "").strip() or "merge failed"
            return result

        head_ok, head_out, _stderr = _run_git(repo_root, ["rev-parse", "HEAD"])
        merge_commit = head_out.strip() if head_ok else ""
        result.update({"merged": True, "commit": merge_commit})

        # The merge commit makes the actor commits reachable from base. Move
        # the reusable branch and checkout onto that base so the next shift
        # starts clean and the branch never accumulates stale history.
        reset_ok, _stdout, _stderr = _run_git(
            workspace.path, ["reset", "--hard", base]
        )
        result["reset"] = reset_ok
        if not reset_ok:
            logger.warning(
                "Task workspaces: merged %s but could not reset its checkout",
                workspace.branch,
            )

        with self._registry_lock():
            registry = self._load_registry()
            record = registry.get(workspace.key)
            if record:
                current = TaskWorkspace.from_dict(record)
                current.last_commit = merge_commit
                current.last_used_at = _utcnow()
                current.error = ""
                registry[workspace.key] = current.to_dict()
                self._save_registry(registry)
        return result

    # -- removal -----------------------------------------------------------
    def remove(
        self,
        chat: Any,
        *,
        delete_branch: bool = False,
        scope: str = SCOPE_TASK,
        actor: str = "",
    ) -> Dict[str, Any]:
        """Remove the workspace checkout (and optionally its branch).

        A dirty worktree is never removed implicitly: the caller must go through
        the Git view, which shows the pending files and asks for confirmation.
        """
        workspace = self.find(chat, scope=scope, actor=actor)
        if workspace is None:
            return {"removed": False, "error": "no workspace registered"}
        ok, status, _error = _run_git(workspace.path, ["status", "--porcelain"])
        if ok and status.strip():
            return {
                "removed": False,
                "error": "workspace has uncommitted changes",
                "workspace": workspace.to_dict(),
            }
        repo_root = self.repo_root()
        ok, _stdout, error = _run_git(
            repo_root, ["worktree", "remove", workspace.path]
        )
        if not ok:
            return {"removed": False, "error": error}
        if delete_branch:
            _run_git(repo_root, ["branch", "-D", workspace.branch])
        with self._registry_lock():
            registry = self._load_registry()
            registry.pop(workspace.key, None)
            self._save_registry(registry)
        return {"removed": True, "branch": workspace.branch}
