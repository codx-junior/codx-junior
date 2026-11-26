"""Quality guardian policy: what runs, when, and how expensive it may get.

The policy is the answer to "a guardian on every save would be noisy and
expensive". It lives in ``.codx/quality/policy.json`` (project data, never in
``project.json``, which also holds secrets) and every field is optional:

* the **fast lane** (deterministic checks) can run on every change;
* the **deep lane** (LLM review) is opt-in per trigger and rate limited;
* a project can disable one guardian without deleting its profile;
* a project can disable the whole subsystem with the same env kill switch used
  by QA/CISO.

The defaults are intentionally conservative on cost and strict on evidence:
file changes run checks, not AI; a staged change (the next commit) is allowed
an AI review; a manual review always is.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from codx.junior.utils.file_lock import interprocess_lock

logger = logging.getLogger(__name__)

#: Folder that holds all guardian state, relative to the project's ``.codx``.
STATE_FOLDER = "quality"
POLICY_FILE = "policy.json"

#: Emergency stop, same convention as the QA and CISO subsystems.
DISABLED_ENV = "CODX_JUNIOR_GUARDIANS_DISABLED"

#: Seconds a change batch waits before it runs. The wiki uses the same idea:
#: agents write files in sequence, so reviewing each write is wasteful.
DEFAULT_DEBOUNCE_SECONDS = 12

#: Hard cap so a long editing session still gets reviewed.
DEFAULT_MAX_DELAY_SECONDS = 300

#: Maximum number of changed files one run considers.
DEFAULT_MAX_FILES_PER_RUN = 60

#: Maximum number of files sent to one guardian in one AI review.
DEFAULT_MAX_AI_FILES_PER_RUN = 5

#: Maximum number of AI calls one run may make (cost guard).
DEFAULT_MAX_AI_CALLS_PER_RUN = 8

#: Default per-file quiet period for change-triggered runs.
DEFAULT_FILE_COOLDOWN_SECONDS = 300

#: A file bigger than this is never sent to a model or parsed as text.
DEFAULT_MAX_FILE_BYTES = 1_500_000

#: Maximum characters of diff sent to one guardian.
DEFAULT_MAX_DIFF_CHARS = 32_000

#: Globs excluded before a guardian even sees a path. They complement the
#: project's own ignore list and the profile ``guardian.ignore_paths``.
DEFAULT_IGNORE_PATHS: List[str] = [
    ".git/*",
    ".git/**",
    ".codx/*",
    ".codx/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/dist/**",
    "**/build/**",
    "**/coverage/**",
    "**/htmlcov/**",
    "**/vendor/**",
    "**/target/**",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/poetry.lock",
    "**/Cargo.lock",
    "**/go.sum",
    "**/composer.lock",
]

#: Fields written by :func:`save_policy` (unknown keys are ignored).
POLICY_FIELDS = (
    "enabled",
    "auto_bootstrap",
    "run_on_change",
    "ai_on_change",
    "ai_on_staged",
    "ai_on_commit",
    "ai_on_manual",
    "debounce_seconds",
    "max_delay_seconds",
    "max_files_per_run",
    "max_ai_files_per_run",
    "max_ai_calls_per_run",
    "file_cooldown_seconds",
    "block_above",
    "approval_above",
    "warn_above",
    "ignore_paths",
    "disabled_guardians",
    "ignore_rules",
    "auto_fix",
    "max_file_bytes",
    "max_diff_chars",
)


# -----------------------------------------------------------------------------
# Coercion helpers (a UI sends strings; a JSON file may contain anything)
# -----------------------------------------------------------------------------
def _as_bool(value: Any, default: bool) -> bool:
    """Coerce *value* to bool, tolerating the strings a UI sends."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    """Coerce *value* to a bounded int, keeping *default* when invalid."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def _as_list(value: Any) -> List[str]:
    """Coerce *value* to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, Iterable):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return [item for item in items if item]


def _as_severity(value: Any, default: str) -> str:
    """Coerce *value* to a severity string in the guardian vocabulary."""
    from .models import severity_value

    return severity_value(value, default)


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------
@dataclass
class GuardianPolicy:
    """Effective quality-guardian policy of one project."""

    enabled: bool = True
    auto_bootstrap: bool = True
    run_on_change: bool = True
    # AI is expensive: only the staged and manual lanes are on by default.
    ai_on_change: bool = False
    ai_on_staged: bool = True
    ai_on_commit: bool = True
    ai_on_manual: bool = True
    debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS
    max_delay_seconds: int = DEFAULT_MAX_DELAY_SECONDS
    max_files_per_run: int = DEFAULT_MAX_FILES_PER_RUN
    max_ai_files_per_run: int = DEFAULT_MAX_AI_FILES_PER_RUN
    max_ai_calls_per_run: int = DEFAULT_MAX_AI_CALLS_PER_RUN
    file_cooldown_seconds: int = DEFAULT_FILE_COOLDOWN_SECONDS
    block_above: str = "high"
    approval_above: str = "medium"
    warn_above: str = "low"
    ignore_paths: List[str] = field(default_factory=list)
    disabled_guardians: List[str] = field(default_factory=list)
    ignore_rules: List[str] = field(default_factory=list)
    auto_fix: bool = False
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS

    def to_dict(self) -> Dict[str, Any]:
        """Return the policy as a JSON friendly dict."""
        return {
            key: getattr(self, key)
            for key in POLICY_FIELDS
        }

    def is_guardian_enabled(self, name: str) -> bool:
        """Return True when guardian *name* is not disabled by the policy."""
        if not self.enabled:
            return False
        return str(name or "").strip() not in set(self.disabled_guardians)

    def ai_allowed(self, trigger: str) -> bool:
        """Return True when the trigger's lane is allowed to call the LLM."""
        trigger = str(trigger or "manual").strip().lower()
        if not self.enabled:
            return False
        if trigger == "change":
            return bool(self.ai_on_change)
        if trigger == "staged":
            return bool(self.ai_on_staged)
        if trigger == "commit":
            return bool(self.ai_on_commit)
        return bool(self.ai_on_manual)

    def ignored(self, path: str) -> bool:
        """Return True when *path* matches a policy ignore glob."""
        return matches_any(path, self.ignore_paths)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    """Match a project-relative path against glob patterns.

    ``fnmatch`` treats ``*`` as "anything, including /", so ``**/tests/**`` and
    ``tests/*`` behave as a reviewer expects on a posix path. The basename is
    also matched so a bare ``*.lock`` pattern works without a leading ``**/``.

    Args:
        path: Project-relative posix path.
        patterns: Glob patterns.

    Returns:
        bool: True when one pattern matches the path or its basename.
    """
    text = str(path or "").replace("\\", "/").lstrip("./")
    if not text:
        return False
    name = text.rsplit("/", 1)[-1]
    for pattern in patterns or []:
        pattern = str(pattern or "").strip()
        if not pattern:
            continue
        candidates = [pattern]
        # ``**/foo`` means "foo at any depth", including the project root.
        if pattern.startswith("**/"):
            candidates.append(pattern[3:])
        for candidate in candidates:
            if fnmatch.fnmatch(text, candidate) or fnmatch.fnmatch(name, candidate):
                return True
    return False


# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------
def quality_path(settings: Any, *parts: str) -> str:
    """Return a path inside the project's ``.codx/quality`` folder.

    Args:
        settings: Project settings (``codx_path`` is required).
        *parts: Path components below the quality folder.

    Returns:
        str: Absolute path.
    """
    codx_path = getattr(settings, "codx_path", "") or ""
    return os.path.join(codx_path, STATE_FOLDER, *parts)


def policy_path(settings: Any) -> str:
    """Return the absolute path of the project policy file."""
    return quality_path(settings, POLICY_FILE)


def policy_from_dict(data: Optional[Dict[str, Any]], settings: Any = None) -> GuardianPolicy:
    """Build a :class:`GuardianPolicy` from a partial dict.

    Args:
        data: Partial policy (unknown keys are ignored, bad values fall back).
        settings: Project settings used to merge its own ignore patterns.

    Returns:
        GuardianPolicy: The validated policy.
    """
    defaults = GuardianPolicy()
    data = data if isinstance(data, dict) else {}
    policy = GuardianPolicy(
        enabled=_as_bool(data.get("enabled"), defaults.enabled),
        auto_bootstrap=_as_bool(data.get("auto_bootstrap"), defaults.auto_bootstrap),
        run_on_change=_as_bool(data.get("run_on_change"), defaults.run_on_change),
        ai_on_change=_as_bool(data.get("ai_on_change"), defaults.ai_on_change),
        ai_on_staged=_as_bool(data.get("ai_on_staged"), defaults.ai_on_staged),
        ai_on_commit=_as_bool(data.get("ai_on_commit"), defaults.ai_on_commit),
        ai_on_manual=_as_bool(data.get("ai_on_manual"), defaults.ai_on_manual),
        debounce_seconds=_as_int(data.get("debounce_seconds"), defaults.debounce_seconds, 0),
        max_delay_seconds=_as_int(data.get("max_delay_seconds"), defaults.max_delay_seconds, 0),
        max_files_per_run=_as_int(data.get("max_files_per_run"), defaults.max_files_per_run, 1),
        max_ai_files_per_run=_as_int(data.get("max_ai_files_per_run"), defaults.max_ai_files_per_run, 1),
        max_ai_calls_per_run=_as_int(data.get("max_ai_calls_per_run"), defaults.max_ai_calls_per_run, 0),
        file_cooldown_seconds=_as_int(
            data.get("file_cooldown_seconds"), defaults.file_cooldown_seconds, 0
        ),
        block_above=_as_severity(data.get("block_above"), defaults.block_above),
        approval_above=_as_severity(data.get("approval_above"), defaults.approval_above),
        warn_above=_as_severity(data.get("warn_above"), defaults.warn_above),
        ignore_paths=(
            _as_list(data.get("ignore_paths"))
            or list(DEFAULT_IGNORE_PATHS)
        ),
        disabled_guardians=_as_list(data.get("disabled_guardians")),
        ignore_rules=_as_list(data.get("ignore_rules")),
        auto_fix=_as_bool(data.get("auto_fix"), defaults.auto_fix),
        max_file_bytes=_as_int(data.get("max_file_bytes"), defaults.max_file_bytes, 1024),
        max_diff_chars=_as_int(data.get("max_diff_chars"), defaults.max_diff_chars, 1000),
    )

    # The project's existing ignore setting is a comma separated string of
    # substrings used by knowledge indexing; turn the clear glob-like entries
    # into guardian ignores instead of interpreting arbitrary fragments.
    project_ignore = getattr(settings, "knowledge_file_ignore", "") if settings is not None else ""
    for entry in _as_list(project_ignore):
        if any(character in entry for character in "*?[]"):
            policy.ignore_paths.append(entry)
    return policy


def resolve_policy(settings: Any = None) -> GuardianPolicy:
    """Return the effective guardian policy of *settings*.

    Resolution order:
      1. a ``quality_policy`` mapping attached to the settings object,
      2. the same key inside ``settings.settings`` (test/API stubs),
      3. ``.codx/quality/policy.json``,
      4. the defaults.

    The env kill switch disables the subsystem regardless of the stored policy.

    Args:
        settings: Project settings (``None`` returns the defaults).

    Returns:
        GuardianPolicy: The merged policy.
    """
    stored: Optional[Dict[str, Any]] = None
    if settings is not None:
        candidate = getattr(settings, "quality_policy", None)
        if isinstance(candidate, dict):
            stored = candidate
        if stored is None:
            configured = getattr(settings, "settings", None)
            if isinstance(configured, dict) and isinstance(configured.get("quality_policy"), dict):
                stored = configured["quality_policy"]

    if stored is None and settings is not None and getattr(settings, "codx_path", None):
        try:
            with open(policy_path(settings), "r", encoding="utf-8") as handle:
                candidate = json.load(handle)
            if isinstance(candidate, dict):
                stored = candidate
        except (OSError, ValueError):
            stored = None

    policy = policy_from_dict(stored, settings=settings)
    if os.environ.get(DISABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        policy.enabled = False
    return policy


def stored_policy(settings: Any = None) -> Dict[str, Any]:
    """Return the raw policy overrides stored in the project policy file."""
    if settings is None or not getattr(settings, "codx_path", None):
        return {}
    try:
        with open(policy_path(settings), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_policy(settings: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *updates* into the project policy file.

    Only :data:`POLICY_FIELDS` are written; unknown keys are ignored so an old
    client cannot corrupt the file. The write is atomic and serialised across
    processes with the same lock the run store uses.

    Args:
        settings: Project settings.
        updates: Partial policy.

    Returns:
        Dict: The effective (resolved) policy after the save.

    Raises:
        ValueError: When *settings* has no ``codx_path``.
    """
    if settings is None or not getattr(settings, "codx_path", None):
        raise ValueError("Project settings with a codx_path are required")

    updates = updates if isinstance(updates, dict) else {}
    path = policy_path(settings)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = quality_path(settings, ".lock")

    with interprocess_lock(lock_path):
        current = stored_policy(settings)
        merged = dict(current)
        for key in POLICY_FIELDS:
            if key in updates:
                merged[key] = updates[key]
        # Validate through the same coercion used at read time so the file
        # never stores a type the readers cannot handle.
        validated = policy_from_dict(merged, settings=settings).to_dict()
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)

    logger.info("Quality guardian policy saved for %s", getattr(settings, "project_name", ""))
    return resolve_policy(settings).to_dict()


# Made with ❤️ by codx-junior
