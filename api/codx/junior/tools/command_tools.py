"""Command execution tools.

One capability only: run a single non-interactive shell command inside the
project and return its output as text. Interactive shells, background
processes and long-running services are explicitly out of scope.

Two guards wrap every run:
    * the CISO command gate (the same one ``security_check_command`` exposes)
      refuses ``block`` and ``require_approval`` verdicts before execution;
    * the working directory is confined to the project root and ``sudo`` is
      always refused.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from typing import Any

logger = logging.getLogger(__name__)

# Each output section is capped so a chatty command cannot flood the model
# context (and so the response stays a single readable string).
MAX_SECTION_CHARS = 8000

# ``sudo`` used as a command token: never matches inside another word ("pseudo").
SUDO_PATTERN = re.compile(r"(?:^|[;&|(`\n]|\s)sudo(?:\s|$)")


def _truncate(text: str, limit: int = MAX_SECTION_CHARS) -> str:
    """Truncate ``text`` to ``limit`` characters, noting how much was dropped."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"{text[:limit]}\n...[truncated {dropped} characters]"


def _as_text(value) -> str:
    """Coerce a captured stream (str or bytes) into text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _settings_of(kwargs) -> Any:
    """Return the project settings injected by the runtime (may be ``None``)."""
    settings = kwargs.get("settings")
    if settings is None:
        settings = getattr(kwargs.get("session"), "settings", None)
    return settings


def _project_root(settings) -> str:
    """Return the real path of the project root, falling back to the process cwd."""
    root = ""
    if settings is not None:
        root = (
            getattr(settings, "abs_project_path", "")
            or getattr(settings, "project_path", "")
            or ""
        )
    return os.path.realpath(root or os.getcwd())


def _resolve_cwd(cwd: str | None, root: str) -> str:
    """
    Resolve ``cwd`` against the project root and confine it there.

    Args:
        cwd: Requested working directory, absolute or project relative. ``None``
            means the project root.
        root: Real path of the project root.

    Returns:
        str: Real path of the working directory, inside ``root``.

    Raises:
        ValueError: When the resolved directory escapes the project root.
    """
    if not cwd:
        return root
    target = cwd if os.path.isabs(cwd) else os.path.join(root, cwd)
    target = os.path.realpath(target)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError(f"`{cwd}` is outside the project root `{root}`")
    return target


def _refuse_reason(command: str, settings) -> str | None:
    """
    Pre-flight check: the CISO command gate plus the local ``sudo`` ban.

    The gate is the same one ``security_check_command`` exposes
    (``codx.junior.security.ciso.gate.guard_command``): there is no second policy
    engine here. A command is refused when the verdict is ``blocked`` or
    ``requires_approval``, and also when the gate cannot evaluate it at all
    (fail closed: an unguarded command never runs).

    Args:
        command: Command line about to run.
        settings: Project settings injected by the runtime (may be ``None``).

    Returns:
        ``None`` when the command may run, otherwise the refusal report.
    """
    if SUDO_PATTERN.search(command or ""):
        return (
            "⛔ run_command refused: `sudo` is not allowed. The tool runs "
            "non-interactive commands as the runtime user only."
        )

    try:
        from codx.junior.security.ciso import gate

        verdict = gate.guard_command(command, settings=settings)
    except Exception as error:  # pragma: no cover - defensive, fail closed
        logger.warning("run_command gate error for %r: %s", command, error)
        return (
            "⛔ run_command refused: the CISO command gate could not evaluate "
            f"the command ({error}). Nothing was executed."
        )

    blocked = bool(getattr(verdict, "blocked", False))
    needs_approval = bool(getattr(verdict, "requires_approval", False))
    if not blocked and not needs_approval:
        return None

    action = getattr(verdict, "action", "")
    action = getattr(action, "value", action)
    summary = getattr(verdict, "summary", "") or "refused by the CISO command gate"
    return (
        f"⛔ run_command {'blocked' if blocked else 'requires approval'}: {summary}\n"
        f"- action: {action}\n"
        f"- risk_score: {getattr(verdict, 'risk_score', 0.0)}\n"
        f"- command (not executed): `{(command or '')[:300]}`"
    )


def run_command(command: str, cwd: str | None = None, timeout_s: int = 120, **kwargs) -> str:
    """
    Run one non-interactive shell command and return its result as text.

    The command is checked by the CISO command gate before anything runs: a
    ``block`` or ``require_approval`` verdict — or a command containing
    ``sudo`` — is returned as text and never executed. ``cwd`` is resolved
    against the project root and refused when it escapes it.

    Args:
        command: Command line to execute.
        cwd: Optional working directory, absolute or project relative. Defaults
            to the project root and must stay inside it.
        timeout_s: Seconds before the command is killed (default 120).
        **kwargs: Additional arguments including:
            - settings (CODXJuniorSettings): Project settings, injected by the runtime.
            - session: Project session, injected by the runtime when available.

    Returns:
        A single string with the exit code, then stdout, then stderr. A
        timeout, a refused command and a working directory outside the project
        are reported as text instead of raising.
    """
    settings = _settings_of(kwargs)

    refusal = _refuse_reason(command, settings)
    if refusal:
        logger.warning("run_command refused for %r", command)
        return f"{refusal}\nSTDOUT:\n\nSTDERR:\n"

    root = _project_root(settings)
    try:
        workdir = _resolve_cwd(cwd, root)
    except ValueError as error:
        logger.warning("run_command refused cwd %r: %s", cwd, error)
        return f"⛔ run_command refused: {error}\nSTDOUT:\n\nSTDERR:\n"

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as error:
        logger.warning("run_command timed out after %ss: %s", timeout_s, command)
        return (
            f"Exit code: (none) command timed out after {timeout_s}s\n"
            f"STDOUT:\n{_truncate(_as_text(error.stdout))}\n"
            f"STDERR:\n{_truncate(_as_text(error.stderr))}"
        )
    except OSError as error:
        logger.warning("run_command could not start %r: %s", command, error)
        return f"Exit code: (none) command could not be started: {error}\nSTDOUT:\n\nSTDERR:\n"

    return (
        f"Exit code: {completed.returncode}\n"
        f"STDOUT:\n{_truncate(completed.stdout)}\n"
        f"STDERR:\n{_truncate(completed.stderr)}"
    )


RUN_COMMAND_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run one non-interactive shell command (git, tar, pytest, ls, wc…) "
            "and return its exit code, stdout and stderr. Use it to measure the "
            "working tree, create archives or run a test suite. It is not an "
            "interactive shell: background processes and REPLs are not supported. "
            "Every command is checked by the CISO command gate before it runs: "
            "blocked and require-approval commands are refused, as is `sudo`, and "
            "the working directory is confined to the project root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command line to execute."
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Optional working directory, absolute or project relative. "
                        "It must stay inside the project root (the default)."
                    )
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120).",
                    "default": 120
                }
            },
            "required": ["command"]
        }
    }
}
