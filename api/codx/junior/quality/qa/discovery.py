"""QA discovery: which test suites exist in this project, and can they run?

The catalog is intentionally small and deterministic: pytest for Python
packages, ``npm test`` for JavaScript packages, plus whatever the project
policy declares explicitly. Discovery never guesses a command, never installs
anything and never touches the network.

Made with ❤️ by codx-junior
"""

import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

from codx.junior.quality.qa.models import QaPolicy, QaSuite

logger = logging.getLogger(__name__)

#: Folders never scanned for suites.
IGNORED_DIRS = {
    ".git",
    ".codx",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".next",
    ".nuxt",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    "vendor",
    "target",
}

#: Files that announce a Python package with a pytest configuration.
PYTEST_MARKERS = ("pytest.ini", "tox.ini", "setup.cfg", "conftest.py", "pyproject.toml")

#: Manifests that announce a JavaScript package.
NPM_MARKERS = ("package.json",)


def _which(command: str) -> bool:
    """Return True when *command* can be executed on this machine."""
    if not command:
        return False
    if os.path.sep in command:
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def _python_interpreter(project_path: str, directory: str) -> str:
    """Return the interpreter used to run pytest for *directory*.

    Args:
        project_path: Absolute project path.
        directory: Absolute directory of the suite.

    Returns:
        str: Path of a virtualenv interpreter when one exists, else
        ``python3``.
    """
    for candidate in (
        os.path.join(directory, ".venv", "bin", "python"),
        os.path.join(project_path, ".venv", "bin", "python"),
        os.path.join(directory, ".venv", "bin", "python3"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return "python3"


def _scan_dirs(project_path: str, max_depth: int = 1) -> List[str]:
    """Return the directories to inspect (project root + first levels)."""
    directories: List[str] = [project_path]
    frontier = [project_path]
    for _ in range(max(0, max_depth)):
        next_frontier: List[str] = []
        for directory in frontier:
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError:  # pragma: no cover - unreadable folder
                continue
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                    continue
                next_frontier.append(entry.path)
        directories += next_frontier
        frontier = next_frontier
    return directories


def _pytest_suite(project_path: str, directory: str) -> Optional[QaSuite]:
    """Build the pytest suite of *directory* (``None`` when it is not one)."""
    has_marker = any(
        os.path.isfile(os.path.join(directory, marker)) for marker in PYTEST_MARKERS
    )
    has_tests = os.path.isdir(os.path.join(directory, "tests"))
    if not has_marker and not has_tests:
        return None

    relative = os.path.relpath(directory, project_path).replace(os.sep, "/")
    name = "pytest" if relative == "." else f"pytest:{relative}"
    interpreter = _python_interpreter(project_path, directory)
    available = _which(interpreter)
    return QaSuite(
        name=name,
        kind="pytest",
        command=[interpreter, "-m", "pytest"],
        cwd="" if relative == "." else relative,
        description=f"pytest suite of `{relative}`",
        available=available,
        reason="" if available else f"interpreter not found: {interpreter}",
    )


def _npm_suite(project_path: str, directory: str) -> Optional[QaSuite]:
    """Build the ``npm test`` suite of *directory* (``None`` when it is not one)."""
    manifest = os.path.join(directory, "package.json")
    if not os.path.isfile(manifest):
        return None

    try:
        with open(manifest, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except Exception as ex:  # pragma: no cover - broken manifest
        logger.debug("QA discovery: unreadable %s: %s", manifest, ex)
        return None

    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict) or not scripts.get("test"):
        return None

    relative = os.path.relpath(directory, project_path).replace(os.sep, "/")
    name = "npm" if relative == "." else f"npm:{relative}"
    available = _which("npm")
    return QaSuite(
        name=name,
        kind="npm",
        command=["npm", "test", "--silent"],
        cwd="" if relative == "." else relative,
        description=f"`npm test` of `{relative}`",
        available=available,
        reason="" if available else "npm not found in PATH",
    )


def _policy_suites(project_path: str, policy: QaPolicy) -> List[QaSuite]:
    """Build the suites declared in the QA policy."""
    suites: List[QaSuite] = []
    for name, definition in (policy.suites or {}).items():
        if isinstance(definition, list):
            command, cwd, description = definition, "", ""
        elif isinstance(definition, dict):
            command = definition.get("command")
            cwd = str(definition.get("cwd") or "")
            description = str(definition.get("description") or "")
        else:
            continue
        if not isinstance(command, list) or not command:
            continue
        command = [str(part) for part in command]
        suites.append(
            QaSuite(
                name=str(name),
                kind=str(definition.get("kind", "") if isinstance(definition, dict) else "") or "custom",
                command=command,
                cwd=cwd,
                description=description or f"custom suite `{name}`",
                available=_which(command[0]),
                reason="" if _which(command[0]) else f"command not found: {command[0]}",
            )
        )
    return suites


def detect_suites(project_path: str, policy: Optional[QaPolicy] = None, max_depth: int = 1) -> List[QaSuite]:
    """Detect the test suites of a project.

    Args:
        project_path: Absolute project path.
        policy: Effective QA policy (its ``suites`` are added).
        max_depth: How many folder levels below the root are inspected.

    Returns:
        List[QaSuite]: Detected suites, project root first, deduplicated by name.

    Example:
        >>> [suite.name for suite in detect_suites("/repo/api")]  # doctest: +SKIP
        ['pytest']
    """
    policy = policy or QaPolicy()
    suites: List[QaSuite] = []
    seen: set = set()

    for directory in _scan_dirs(project_path, max_depth=max_depth):
        for factory in (_pytest_suite, _npm_suite):
            suite = factory(project_path, directory)
            if suite and suite.name not in seen:
                seen.add(suite.name)
                suites.append(suite)

    for suite in _policy_suites(project_path, policy):
        if suite.name in seen:
            suites = [suite if existing.name == suite.name else existing for existing in suites]
            continue
        seen.add(suite.name)
        suites.append(suite)

    return suites


def select_suite(suites: List[QaSuite], name: Optional[str] = None) -> Optional[QaSuite]:
    """Return the suite to run.

    Args:
        suites: Detected suites.
        name: Suite name, suite kind or ``None``/``auto`` for the default.

    Returns:
        Optional[QaSuite]: The suite, or ``None`` when nothing matches.
    """
    if not suites:
        return None

    wanted = (name or "auto").strip()
    if wanted and wanted != "auto":
        for suite in suites:
            if suite.name == wanted:
                return suite
        for suite in suites:
            if suite.kind == wanted:
                return suite
        for suite in suites:
            if suite.name.split(":")[0] == wanted:
                return suite
        return None

    for suite in suites:
        if suite.available and suite.kind == "pytest":
            return suite
    for suite in suites:
        if suite.available:
            return suite
    return suites[0]


def suite_catalog(suites: List[QaSuite]) -> List[Dict[str, Any]]:
    """Return a serialisable view of *suites* for a report."""
    return [
        {
            "name": suite.name,
            "kind": suite.kind,
            "cwd": suite.cwd or ".",
            "command": suite.command,
            "available": suite.available,
            "reason": suite.reason,
            "description": suite.description,
        }
        for suite in suites
    ]
