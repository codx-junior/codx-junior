"""QA runner: execute a test suite and parse its result.

Design rules
    1. **No shell.** The command is an argv list executed with
       ``shell=False``; nothing the model writes can reach a shell parser.
    2. **Declared commands only.** The argv comes from the detected suite or
       from the project policy, never from free text. ``extra_args`` is
       validated character by character and rejected when it carries a shell
       metacharacter.
    3. **CISO first.** Before executing anything the composed command line is
       handed to the CISO command gate, so the QA subsystem inherits the
       security policy of the project instead of duplicating it.
    4. **Bounded.** Every run has a timeout and its output is truncated: a test
       suite that floods the console must not blow up the chat context.

Made with ❤️ by codx-junior
"""

import logging
import os
import re
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from codx.junior.quality.qa.models import QaSuite, TestResult, TestRun

logger = logging.getLogger(__name__)

#: Output kept from a run (the tail carries the failures).
MAX_OUTPUT_CHARS = 8000

#: Characters an ``extra_args`` entry may contain. Anything else is refused:
#: the runner never needs a semicolon, a redirection or a heredoc.
SAFE_ARG_CHARS = re.compile(r"^[\w@%+=:,./\-\[\]{}()*?~^]+$")

#: Absolute ceiling for a single run, whatever the policy says.
MAX_TIMEOUT_SECONDS = 3600

_PYTEST_SUMMARY = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|warnings?)")
_PYTEST_FAILURE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$", re.MULTILINE)
_NPM_SUMMARY = re.compile(r"Tests:\s*(.*)", re.IGNORECASE)
_NPM_COUNT = re.compile(r"(\d+)\s+(failed|passed|pending|skipped|todo)", re.IGNORECASE)
_NPM_FAILURE = re.compile(r"^\s*(?:✕|×|FAIL)\s+(.+)$", re.MULTILINE)


# -----------------------------------------------------------------------------
# Command composition
# -----------------------------------------------------------------------------
def validate_args(extra_args: Optional[List[str]]) -> Tuple[List[str], Optional[str]]:
    """Validate the extra arguments of a run.

    Args:
        extra_args: Arguments the caller wants to append to the suite command.

    Returns:
        tuple: ``(accepted, error)``; ``error`` is a message when the list is
        refused (the runner then refuses to execute anything).

    Example:
        >>> validate_args(["-k", "login"])
        (['-k', 'login'], None)
        >>> validate_args(["a; rm -rf /"])[1] is not None
        True
    """
    if not extra_args:
        return [], None
    accepted: List[str] = []
    for raw in extra_args:
        item = str(raw)
        if not item or not SAFE_ARG_CHARS.match(item):
            return [], f"unsafe argument refused: {item!r}"
        accepted.append(item)
    return accepted, None


def _cwd_of(project_path: str, suite: QaSuite) -> str:
    """Return the working directory of *suite*."""
    if not suite.cwd or suite.cwd in (".", ""):
        return project_path
    return os.path.join(project_path, suite.cwd)


def _relative(project_path: str, suite: QaSuite, path: str) -> str:
    """Return *path* relative to the suite working directory (posix style)."""
    absolute = path if os.path.isabs(path) else os.path.join(project_path, path)
    cwd = _cwd_of(project_path, suite)
    try:
        relative = os.path.relpath(absolute, cwd)
    except ValueError:  # different drive on Windows
        return absolute
    return relative.replace(os.sep, "/")


def build_command(
    project_path: str,
    suite: QaSuite,
    paths: Optional[List[str]] = None,
    extra_args: Optional[List[str]] = None,
) -> Tuple[List[str], Optional[str]]:
    """Compose the argv of a run.

    Args:
        project_path: Absolute project path.
        suite: Suite to execute.
        paths: Optional test files / folders to restrict the run.
        extra_args: Validated extra arguments.

    Returns:
        tuple: ``(argv, error)``.
    """
    args, error = validate_args(extra_args)
    if error:
        return [], error

    argv = list(suite.command) + args
    if paths:
        argv += [_relative(project_path, suite, path) for path in paths]
    return argv, None


def _ciso_block(command_line: str, settings: Any) -> Optional[str]:
    """Ask the CISO gate whether *command_line* may run.

    Args:
        command_line: Human readable command (for the verdict).
        settings: Project settings.

    Returns:
        Optional[str]: The block reason, or ``None`` when it may run.
    """
    try:
        from codx.junior.security.ciso import gate as ciso_gate

        verdict = ciso_gate.guard_command(command_line, settings=settings)
        if verdict is not None and getattr(verdict, "blocked", False):
            return f"blocked by the CISO command gate ({verdict.summary})"
    except Exception as ex:  # pragma: no cover - the gate must never break QA
        logger.debug("QA runner: CISO command gate unavailable: %s", ex)
    return None


# -----------------------------------------------------------------------------
# Output parsing
# -----------------------------------------------------------------------------
def _output_of(stdout: str, stderr: str) -> str:
    """Join and truncate the process output."""
    merged = "\n".join(part for part in (stdout or "", stderr or "") if part)
    if len(merged) > MAX_OUTPUT_CHARS:
        merged = "…\n" + merged[-MAX_OUTPUT_CHARS:]
    return merged


def parse_pytest(output: str) -> Dict[str, Any]:
    """Parse the counters and the failures of a pytest run.

    Args:
        output: Combined stdout/stderr of the run.

    Returns:
        dict: ``passed``, ``failed``, ``errors``, ``skipped`` and ``failures``.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for match in _PYTEST_SUMMARY.finditer(output or ""):
        number = int(match.group(1))
        kind = match.group(2)
        if kind == "error" or kind == "errors":
            counts["errors"] = max(counts["errors"], number)
        elif kind in counts:
            counts[kind] = max(counts[kind], number)

    failures: List[TestResult] = []
    for match in _PYTEST_FAILURE.finditer(output or ""):
        status = "failed" if match.group(0).startswith("FAILED") else "error"
        failures.append(
            TestResult(id=match.group(1).strip(), status=status, message=(match.group(2) or "").strip())
        )
    return {**counts, "failures": failures}


def parse_npm(output: str) -> Dict[str, Any]:
    """Parse the counters and the failures of an ``npm test`` run."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for line in _NPM_SUMMARY.findall(output or ""):
        for number, kind in _NPM_COUNT.findall(line):
            key = "failed" if kind == "failed" else ("skipped" if kind in ("skipped", "todo", "pending") else "passed")
            counts[key] = max(counts[key], int(number))

    failures: List[TestResult] = [
        TestResult(id=match.strip(), status="failed")
        for match in _NPM_FAILURE.findall(output or "")
    ]
    return {**counts, "failures": failures}


def parse_output(kind: str, output: str) -> Dict[str, Any]:
    """Parse the output of a suite, according to its kind."""
    if kind == "pytest":
        return parse_pytest(output)
    if kind in ("npm", "node"):
        return parse_npm(output)
    parsed = parse_pytest(output)
    if not any(parsed[key] for key in ("passed", "failed", "errors", "skipped")):
        parsed = parse_npm(output)
    return parsed


# -----------------------------------------------------------------------------
# Execution
# -----------------------------------------------------------------------------
def run_suite(
    suite: QaSuite,
    project_path: str,
    paths: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    settings: Any = None,
) -> TestRun:
    """Run one test suite and return its result.

    Args:
        suite: Suite to execute.
        project_path: Absolute project path.
        paths: Optional test files / folders to restrict the run.
        timeout: Seconds before the run is killed (policy default when omitted).
        extra_args: Extra arguments (validated, rejected when unsafe).
        settings: Project settings (used by the CISO command gate).

    Returns:
        TestRun: Result of the execution; ``error`` is set when it never ran.

    Example:
        >>> run_suite(QaSuite(name="s", kind="pytest", command=["true"]), ".")  # doctest: +SKIP
    """
    seconds = min(int(timeout or 900), MAX_TIMEOUT_SECONDS)
    cwd = _cwd_of(project_path, suite)
    run = TestRun(suite=suite.name or suite.kind, cwd=cwd)

    if not suite.available:
        run.ok = False
        run.error = suite.reason or "suite not available"
        return run

    argv, error = build_command(project_path, suite, paths=paths, extra_args=extra_args)
    if error:
        run.ok = False
        run.error = error
        return run
    run.command = argv

    block_reason = _ciso_block(shlex.join(argv), settings)
    if block_reason:
        run.ok = False
        run.error = block_reason
        return run

    started = time.time()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
            argv,
            cwd=cwd if os.path.isdir(cwd) else project_path,
            capture_output=True,
            text=True,
            timeout=seconds,
            shell=False,
            check=False,
        )
        stdout, stderr, code = completed.stdout or "", completed.stderr or "", completed.returncode
    except subprocess.TimeoutExpired as ex:
        run.duration_seconds = time.time() - started
        run.timed_out = True
        run.ok = False
        run.returncode = -1
        run.error = f"timed out after {seconds}s"
        run.output_tail = _output_of(
            ex.stdout.decode(errors="replace") if isinstance(ex.stdout, bytes) else (ex.stdout or ""),
            ex.stderr.decode(errors="replace") if isinstance(ex.stderr, bytes) else (ex.stderr or ""),
        )
        return run
    except FileNotFoundError:
        run.duration_seconds = time.time() - started
        run.ok = False
        run.error = f"command not found: {argv[0] if argv else ''}"
        return run
    except Exception as ex:
        run.duration_seconds = time.time() - started
        run.ok = False
        run.error = f"{type(ex).__name__}: {ex}"
        return run

    run.duration_seconds = time.time() - started
    run.returncode = code
    run.output_tail = _output_of(stdout, stderr)
    parsed = parse_output(suite.kind, run.output_tail)
    run.passed = parsed["passed"]
    run.failed = parsed["failed"]
    run.errors = parsed["errors"]
    run.skipped = parsed["skipped"]
    run.failures = parsed["failures"][:25]
    run.total = run.passed + run.failed + run.errors + run.skipped
    run.ok = code == 0 and run.failed == 0 and run.errors == 0
    if not run.ok and not run.failures and not run.total:
        run.error = f"exited with code {code}"
    return run
