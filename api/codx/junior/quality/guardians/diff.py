"""Change-set helpers for the quality guardians.

Guardians run on a *change*: the file was saved, the index was staged, a commit
is being reviewed, or the user asked for a project-wide audit. This module turns
those four situations into one normalized payload:

    {"path", "absolute", "status", "content", "diff", "additions", "deletions",
     "language", "truncated", "binary"}

The rules are boring on purpose:

* paths are project-relative, posix style, and can never escape the project;
* the diff is produced by git with ``--relative`` so a project nested in a
  bigger repository does not see its siblings;
* an untracked file gets a synthetic diff (it has no git history yet);
* binaries and oversized files are skipped without crashing the run;
* reading a file never raises: a permission error becomes an empty payload with
  an error attached to the run.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .policy import GuardianPolicy, matches_any

logger = logging.getLogger(__name__)

#: Extensions never treated as reviewable source.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".class",
    ".jar", ".war", ".wasm", ".sqlite", ".db", ".lockb",
}

#: Extensions considered source code by the generic guardian.
SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue",
    ".svelte", ".astro", ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php",
    ".cs", ".swift", ".m", ".mm", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx",
    ".ex", ".exs", ".scala", ".clj", ".cljs", ".lua", ".dart", ".sh", ".bash",
    ".zsh", ".fish", ".sql", ".graphql", ".gql", ".proto", ".css", ".scss",
    ".sass", ".less", ".html", ".htm",
}

#: Extensions considered documentation.
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}

#: Files never sent to a model even when a guardian matches them.
GENERATED_MARKERS = (
    ".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
    "Cargo.lock", "go.sum", "composer.lock",
)

#: Folder names never walked by project scans.
IGNORED_DIRS = {
    ".git", ".codx", ".venv", "venv", "env", "node_modules", "dist", "build",
    "__pycache__", ".next", ".nuxt", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "coverage", "htmlcov", "vendor", "target", "tmp",
    ".turbo", ".cache", "bower_components",
}

#: Maximum characters of a single file diff kept in the payload.
MAX_FILE_DIFF_CHARS = 60_000

#: Maximum number of paths passed to a single git command.
MAX_GIT_PATHS = 120

#: Seconds a git read may take.
GIT_TIMEOUT_SECONDS = 60


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
def project_root(settings: Any) -> str:
    """Return the absolute project root of *settings*."""
    return os.path.abspath(
        getattr(settings, "abs_project_path", None)
        or getattr(settings, "project_path", None)
        or os.getcwd()
    )


def _is_inside(root: str, target: str) -> bool:
    """Return True when *target* is *root* or one of its descendants."""
    root = os.path.normcase(os.path.normpath(root))
    target = os.path.normcase(os.path.normpath(target))
    return target == root or target.startswith(root + os.sep)


def normalize_path(settings: Any, path: str) -> Optional[str]:
    """Normalize *path* to a project-relative posix path.

    A path outside the project returns ``None``: a guardian must never review
    a sibling project or an arbitrary file on the machine, even when a tool
    passes an absolute path. Symlinks are resolved too, so a link inside the
    project cannot be used to read/write outside of it.

    Args:
        settings: Project settings.
        path: Absolute or project-relative path.

    Returns:
        Optional[str]: Normalized relative path, or ``None`` when outside.
    """
    root = project_root(settings)
    raw = str(path or "").strip()
    if not raw:
        return None
    absolute = raw if os.path.isabs(raw) else os.path.join(root, raw)
    absolute = os.path.abspath(absolute)
    try:
        relative = os.path.relpath(absolute, root)
    except ValueError:  # different drives on Windows
        return None
    relative = relative.replace("\\", "/")
    if relative == ".." or relative.startswith("../"):
        return None
    # Resolve symlinks for the containment check. ``realpath`` also resolves
    # the existing parents of a not-yet-created file, which keeps new files
    # working while refusing a symlinked directory that points outside.
    real_root = os.path.realpath(root)
    real_absolute = os.path.realpath(absolute)
    if not _is_inside(real_root, real_absolute):
        return None
    return relative.lstrip("./") or "."


def absolute_path(settings: Any, path: str) -> Optional[str]:
    """Resolve a project-relative path to an absolute path inside the project.

    ``normalize_path`` already resolved the symlinks; this function only joins
    the normalized relative path with the project root.
    """
    relative = normalize_path(settings, path)
    if relative is None:
        return None
    return os.path.join(project_root(settings), relative.replace("/", os.sep))


def language_for_path(path: str) -> str:
    """Return a short language tag for *path* used by prompts and UI."""
    extension = os.path.splitext(str(path or "").lower())[1]
    return {
        ".py": "python", ".pyi": "python",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
        ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
        ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
        ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
        ".sh": "shell", ".bash": "shell", ".zsh": "shell",
        ".sql": "sql", ".graphql": "graphql", ".gql": "graphql",
        ".proto": "protobuf", ".md": "markdown", ".mdx": "mdx",
        ".rst": "restructuredtext", ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".ini": "ini", ".cfg": "ini", ".tf": "terraform", ".tfvars": "terraform",
        ".dockerfile": "dockerfile",
    }.get(extension, extension.lstrip(".") or "text")


def is_binary_path(path: str) -> bool:
    """Return True when the extension marks *path* as binary."""
    extension = os.path.splitext(str(path or "").lower())[1]
    if extension in BINARY_EXTENSIONS:
        return True
    name = str(path or "").lower()
    return any(marker in name for marker in GENERATED_MARKERS if marker.endswith(".map"))


def is_generated_path(path: str) -> bool:
    """Return True when *path* is generated/minified content."""
    name = str(path or "").replace("\\", "/").lower()
    return any(marker in name for marker in GENERATED_MARKERS)


def is_ignored_path(path: str, policy: Optional[GuardianPolicy] = None, extra: Optional[Iterable[str]] = None) -> bool:
    """Return True when a guardian must not review *path*.

    Args:
        path: Project-relative path.
        policy: Effective guardian policy.
        extra: Additional globs (the guardian's own ``ignore_paths``).

    Returns:
        bool: Whether the path is ignored.
    """
    text = str(path or "").replace("\\", "/").lstrip("./")
    if not text:
        return True
    parts = text.split("/")
    if any(part in IGNORED_DIRS for part in parts[:-1]):
        return True
    if parts and parts[-1] in (".DS_Store", "Thumbs.db"):
        return True
    if is_binary_path(text) or is_generated_path(text):
        return True
    if policy is not None and policy.ignored(text):
        return True
    if matches_any(text, extra or []):
        return True
    return False


# -----------------------------------------------------------------------------
# File reading
# -----------------------------------------------------------------------------
def read_text_file(path: str, max_bytes: int = 1_500_000) -> Tuple[str, str]:
    """Read a text file without raising.

    Args:
        path: Absolute file path.
        max_bytes: Files bigger than this are rejected.

    Returns:
        Tuple[str, str]: ``(content, error)``. Content is empty on error.
    """
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return "", f"file is too large ({size} bytes > {max_bytes})"
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as ex:
        return "", str(ex)
    if b"\x00" in raw[:8192]:
        return "", "binary file"
    if len(raw) > max_bytes:
        return "", f"file is too large (> {max_bytes} bytes)"
    try:
        return raw.decode("utf-8"), ""
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1"), ""
        except Exception:  # pragma: no cover - defensive
            return "", "cannot decode file as text"


def line_count(text: str) -> int:
    """Return the number of lines in *text*."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


# -----------------------------------------------------------------------------
# Git
# -----------------------------------------------------------------------------
def _git(settings: Any, args: List[str], timeout: int = GIT_TIMEOUT_SECONDS) -> Tuple[bool, str, str]:
    """Run git in the project root (never through a shell)."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root(settings),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        return False, "", str(ex)
    return completed.returncode == 0, completed.stdout or "", completed.stderr or ""


def is_git_project(settings: Any) -> bool:
    """Return True when the project is inside a git working tree."""
    ok, output, _ = _git(settings, ["rev-parse", "--is-inside-work-tree"], timeout=10)
    return bool(ok and output.strip() == "true")


def _git_status(settings: Any) -> Dict[str, str]:
    """Return ``{path: status}`` for the working tree/index."""
    status: Dict[str, str] = {}
    ok, output, _ = _git(
        settings,
        ["status", "--porcelain=v1", "--no-renames", "--untracked-files=all"],
    )
    if not ok:
        return status
    for line in output.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:].strip().strip('"')
        if not path:
            continue
        if code == "??":
            status[path] = "untracked"
        elif "D" in code:
            status[path] = "deleted"
        elif "A" in code:
            status[path] = "added"
        elif "R" in code:
            status[path] = "renamed"
        elif "U" in code:
            status[path] = "conflict"
        else:
            status[path] = "modified"
    return status


def _changed_paths(settings: Any, base: str, scope: str) -> List[str]:
    """Return the project-relative paths changed by *scope*."""
    scope = str(scope or "all").strip().lower()
    status = _git_status(settings)

    if scope == "staged":
        ok, output, _ = _git(settings, ["diff", "--cached", "--name-only", "--no-renames", "--relative"])
    elif scope == "unstaged":
        ok, output, _ = _git(settings, ["diff", "--name-only", "--no-renames", "--relative"])
    else:
        ok, output, _ = _git(
            settings, ["diff", "--name-only", "--no-renames", "--relative", base or "HEAD"]
        )

    paths: List[str] = []
    if ok:
        paths.extend(line.strip() for line in output.splitlines() if line.strip())

    if scope == "all":
        paths.extend(
            path for path, value in status.items()
            if value == "untracked"
        )
    elif scope == "staged":
        paths.extend(path for path, value in status.items() if value == "added")
    elif scope == "unstaged":
        paths.extend(
            path for path, value in status.items()
            if value in ("modified", "untracked", "deleted")
        )

    seen: List[str] = []
    for path in paths:
        relative = normalize_path(settings, path)
        if relative and relative not in seen:
            seen.append(relative)
    return seen


def _file_status(status_map: Dict[str, str], path: str) -> str:
    """Return the change status of one path from a precomputed status map."""
    return status_map.get(path, "modified")


def _diff_for_paths(settings: Any, paths: List[str], base: str, scope: str) -> Dict[str, str]:
    """Return ``{path: diff}`` for the given paths."""
    if not paths:
        return {}
    selected = paths[:MAX_GIT_PATHS]
    args = ["diff", "--no-ext-diff", "--no-renames", "--unified=6", "--relative"]
    if scope == "staged":
        args.append("--cached")
    elif scope == "all":
        args.append(base or "HEAD")
    # scope == "unstaged" has no extra revision argument.
    args.append("--")
    args.extend(selected)
    ok, output, _ = _git(settings, args)
    if not ok or not output.strip():
        return {}

    diffs: Dict[str, str] = {}
    current: Optional[str] = None
    buffer: List[str] = []
    for line in output.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current is not None:
                diffs[current] = "".join(buffer).rstrip("\n") + "\n"
            buffer = [line]
            # ``diff --git a/<path> b/<path>``: parse the b/ side.
            match = re.match(r"diff --git a/(.*) b/(.*)\n?$", line)
            current = match.group(2) if match else None
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        diffs[current] = "".join(buffer).rstrip("\n") + "\n"

    normalized: Dict[str, str] = {}
    for path, diff in diffs.items():
        relative = normalize_path(settings, path)
        if relative:
            normalized[relative] = diff[:MAX_FILE_DIFF_CHARS]
    return normalized


def synthetic_diff(path: str, content: str) -> str:
    """Build a new-file diff for a path git does not track yet."""
    lines = str(content or "").splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


def count_diff_lines(diff: str) -> Tuple[int, int]:
    """Return ``(additions, deletions)`` of a unified diff."""
    additions = 0
    deletions = 0
    for line in str(diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def added_line_numbers(diff: str) -> List[int]:
    """Return the new-file line numbers of the added lines in a unified diff."""
    numbers: List[int] = []
    current: Optional[int] = None
    for line in str(diff or "").splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            current = int(match.group(1)) if match else None
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            numbers.append(current)
            current += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            current += 1
    return numbers


def build_change_set(
    settings: Any,
    *,
    paths: Optional[List[str]] = None,
    base: str = "HEAD",
    scope: str = "all",
    policy: Optional[GuardianPolicy] = None,
    max_files: int = 60,
    include_content: bool = True,
    explicit_paths_are_new: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Build the normalized change payload of a run.

    Args:
        settings: Project settings.
        paths: Optional explicit paths. When omitted, git decides (``scope``).
        base: Git reference for ``scope='all'``.
        scope: ``all``, ``staged``, ``unstaged`` or ``files`` (explicit paths,
            no diff unless git can produce one).
        policy: Effective guardian policy (file size/ignore filters).
        max_files: Maximum files returned.
        include_content: Read the current content of every file.
        explicit_paths_are_new: Treat explicit paths as new files (synthetic
            diff) when git has no diff for them.

    Returns:
        Tuple[List[dict], List[str]]: ``(files, errors)``. Errors are non-fatal
        conditions (an unreadable file, a truncated git command).
    """
    errors: List[str] = []
    policy = policy or GuardianPolicy()
    git_available = is_git_project(settings)

    normalized_paths: List[str] = []
    for path in paths or []:
        relative = normalize_path(settings, path)
        if relative is None:
            errors.append(f"Ignored path outside the project: {path}")
            continue
        if relative not in normalized_paths:
            normalized_paths.append(relative)

    if scope == "files" or (paths and not git_available):
        selected = normalized_paths
    elif normalized_paths:
        selected = normalized_paths
    elif git_available:
        selected = _changed_paths(settings, base=base, scope=scope)
    else:
        return [], ["The project is not a git repository and no paths were supplied"]

    selected = [
        path for path in selected
        if not is_ignored_path(path, policy=policy, extra=[])
    ]
    if len(selected) > max_files:
        errors.append(
            f"{len(selected) - max_files} changed file(s) skipped: the run limit is {max_files}"
        )
        selected = selected[:max_files]

    diffs: Dict[str, str] = {}
    if git_available and scope in ("all", "staged", "unstaged") and selected:
        diffs = _diff_for_paths(settings, selected, base=base, scope=scope)
    status_map = _git_status(settings) if git_available else {}

    files: List[Dict[str, Any]] = []
    for path in selected:
        absolute = absolute_path(settings, path)
        if not absolute:
            continue
        exists = os.path.isfile(absolute)
        status = _file_status(status_map, path) if git_available else "untracked"
        if not exists:
            status = "deleted"
        content = ""
        read_error = ""
        if include_content and exists:
            content, read_error = read_text_file(absolute, max_bytes=policy.max_file_bytes)
            if read_error:
                errors.append(f"{path}: {read_error}")
        diff = diffs.get(path, "")
        if not diff and exists and (scope == "files" or explicit_paths_are_new or status == "untracked"):
            if content:
                diff = synthetic_diff(path, content)
        if not diff and not content:
            # A deleted file has a diff from git; a binary file has neither.
            if status != "deleted":
                continue
        additions, deletions = count_diff_lines(diff)
        language = language_for_path(path)
        if language == "dockerfile" or os.path.basename(path).lower().startswith("dockerfile"):
            language = "dockerfile"
        files.append(
            {
                "path": path,
                "absolute": absolute,
                "status": status,
                "content": content,
                "diff": diff,
                "additions": additions,
                "deletions": deletions,
                "language": language,
                "truncated": bool(diff) and len(str(diff)) >= MAX_FILE_DIFF_CHARS,
                "binary": bool(read_error == "binary file" or is_binary_path(path)),
            }
        )
    return files, errors


# -----------------------------------------------------------------------------
# Project walk (global review)
# -----------------------------------------------------------------------------
def walk_project(
    settings: Any,
    *,
    policy: Optional[GuardianPolicy] = None,
    extensions: Optional[Iterable[str]] = None,
    max_files: int = 200,
) -> List[str]:
    """Return project-relative files for a project-wide review.

    Args:
        settings: Project settings.
        policy: Effective guardian policy.
        extensions: Optional set of accepted extensions (``.py``, ``.vue``...).
        max_files: Walk limit (the runner applies a second, smaller limit).

    Returns:
        List[str]: Sorted project-relative paths.
    """
    policy = policy or GuardianPolicy()
    accepted = {str(extension).lower() for extension in (extensions or [])}
    root = project_root(settings)
    found: List[str] = []

    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(
            name for name in dirs
            if name not in IGNORED_DIRS
            and (not name.startswith(".") or name in (".github", ".gitlab"))
        )
        for name in sorted(names):
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            if is_ignored_path(relative, policy=policy):
                continue
            if accepted and os.path.splitext(name)[1].lower() not in accepted:
                continue
            found.append(relative)
            if len(found) >= max_files:
                return found
    return found


# Made with ❤️ by codx-junior
