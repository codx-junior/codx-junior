"""QA mapping: which tests belong to a change?

This is the part that makes the QA gate cheap: instead of running the whole
suite, the gate maps every changed source file to the test files that mention
its stem, runs only those, and reports the source files that have no test at
all. It is a heuristic, and it is deliberately simple so a human can predict it.

Made with ❤️ by codx-junior
"""

import fnmatch
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Extensions that carry behaviour and therefore deserve a test.
SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".vue",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".swift",
}

#: Folders never walked when collecting test files.
IGNORED_DIRS = {
    ".git",
    ".codx",
    ".venv",
    "venv",
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
    "tmp",
}

#: Prefixes/suffixes that mark a file as a test.
TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.", "spec.")

#: Seconds a git read may take before it is abandoned.
GIT_TIMEOUT = 60


def _git(project_path: str, args: List[str], timeout: int = GIT_TIMEOUT) -> Tuple[bool, str, str]:
    """Run git inside *project_path* (no shell, bounded).

    Args:
        project_path: Directory the command runs in.
        args: Git arguments without the leading ``git``.
        timeout: Seconds before the command is abandoned.

    Returns:
        tuple: ``(ok, stdout, stderr)``.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        return False, "", str(ex)
    return completed.returncode == 0, completed.stdout or "", completed.stderr or ""


def normalize(path: str) -> str:
    """Return *path* as a posix style, project relative path."""
    return str(path or "").replace("\\", "/").lstrip("./")


def is_test_file(path: str) -> bool:
    """Return True when *path* is a test file.

    Args:
        path: Project relative or absolute path.

    Returns:
        bool: Whether the file is a test.
    """
    text = "/" + normalize(path).lower()
    name = text.rsplit("/", 1)[-1]
    if "/tests/" in text or "/test/" in text or "/__tests__/" in text or "/spec/" in text:
        return True
    return any(name.startswith(marker) or marker in name for marker in TEST_MARKERS)


def is_source_file(path: str) -> bool:
    """Return True when *path* carries behaviour and is not a test."""
    text = normalize(path)
    if not text or is_test_file(text):
        return False
    extension = os.path.splitext(text)[1].lower()
    if extension not in SOURCE_EXTENSIONS:
        return False
    parts = text.lower().split("/")
    return not any(part in IGNORED_DIRS for part in parts[:-1])


def is_ignored(path: str, patterns: Optional[List[str]]) -> bool:
    """Return True when *path* matches one of the policy ignore patterns."""
    text = normalize(path)
    for pattern in patterns or []:
        if fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(os.path.basename(text), pattern):
            return True
    return False


def collect_test_files(project_path: str, limit: int = 3000) -> List[str]:
    """Collect the test files of a project.

    Args:
        project_path: Absolute project path.
        limit: Maximum number of test files collected.

    Returns:
        List[str]: Project relative, posix style paths, sorted.
    """
    found: List[str] = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in IGNORED_DIRS and not directory.startswith(".")
        )
        for name in sorted(files):
            relative = os.path.relpath(os.path.join(root, name), project_path).replace(os.sep, "/")
            if is_test_file(relative):
                found.append(relative)
                if len(found) >= limit:
                    return found
    return found


def changed_files(settings: Any, base: str = "HEAD", scope: str = "all") -> List[str]:
    """Return the files changed in the working tree, the index or against a ref.

    Args:
        settings: Project settings (the project path is the working dir).
        base: Git reference to compare against.
        scope: ``all`` (working tree + staged vs base + untracked),
            ``staged`` (next commit) or ``unstaged`` (working tree vs index).

    Returns:
        List[str]: Project relative paths.

    Raises:
        ValueError: When git cannot read the change.
    """
    project_path = getattr(settings, "abs_project_path", None) or os.getcwd()

    args: List[str] = ["diff", "--name-only", "--no-renames"]
    if scope == "staged":
        args.append("--cached")
    elif scope == "unstaged":
        pass
    else:
        args.append(base or "HEAD")

    ok, stdout, stderr = _git(project_path, args)
    if not ok:
        raise ValueError(f"git diff failed: {(stderr or '').strip()}")

    files = [normalize(line.strip()) for line in (stdout or "").splitlines() if line.strip()]

    if scope == "all":
        ok, others, _ = _git(project_path, ["ls-files", "--others", "--exclude-standard"])
        if ok:
            files += [normalize(line.strip()) for line in (others or "").splitlines() if line.strip()]

    seen: List[str] = []
    for path in files:
        if path and path not in seen:
            seen.append(path)
    return seen


def map_tests(changed: List[str], test_files: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Map changed source files to the tests that mention their stem.

    Args:
        changed: Changed paths.
        test_files: Known test files (project relative).

    Returns:
        tuple: ``(mapping, unmapped)`` where ``mapping`` is
        ``{source_path: [test_path, ...]}`` and ``unmapped`` lists the source
        files with no test at all.

    Example:
        >>> map_tests(["api/codx/wallet.py"], ["api/tests/test_wallet.py"])
        ({'api/codx/wallet.py': ['api/tests/test_wallet.py']}, [])
    """
    mapping: Dict[str, List[str]] = {}
    unmapped: List[str] = []
    lowered = [(test, test.lower()) for test in test_files]

    for path in changed:
        if not is_source_file(path):
            continue
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if not stem or len(stem) < 3:
            unmapped.append(path)
            continue
        candidates = [test for test, text in lowered if stem in os.path.basename(text)]
        if candidates:
            mapping[path] = candidates[:5]
        else:
            unmapped.append(path)
    return mapping, unmapped


def affected_tests(mapping: Dict[str, List[str]], test_files: List[str]) -> List[str]:
    """Return the sorted test files touched by a mapping."""
    selected: List[str] = []
    for tests in mapping.values():
        for test in tests:
            if test not in selected:
                selected.append(test)
    return sorted(selected)


def mapping_summary(mapping: Dict[str, List[str]], unmapped: List[str]) -> Dict[str, Any]:
    """Return the numbers used by the reports and the audit entry."""
    return {
        "mapped_sources": len(mapping),
        "unmapped_sources": len(unmapped),
        "tests_selected": len(affected_tests(mapping, [])),
    }
