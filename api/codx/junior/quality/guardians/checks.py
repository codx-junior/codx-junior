"""Deterministic checks for quality guardians.

The fast lane of a guardian is a small set of checks that need no model:
syntax validity, merge-conflict markers, local documentation links and the
commands the project already has (ruff/eslint/prettier/shellcheck/...).

Safety rules:

* a builtin check is pure Python and never executes anything;
* a command check is a fixed argv, executed with ``shell=False``, inside the
  project, with a timeout and through the CISO command gate;
* the matched paths are appended (or expand a ``{paths}`` placeholder) after
  being normalized to project-relative paths;
* a missing executable is a **skip**, not a failure: a generated guardian can
  mention a linter that is not installed in this environment;
* output is parsed into the same finding shape as the AI review, so the UI,
  the store and the gate never need two code paths.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .diff import (
    absolute_path,
    is_binary_path,
    is_ignored_path,
    line_count,
    normalize_path,
    project_root,
)
from .models import finding_to_dict, normalize_severity, slugify
from .policy import GuardianPolicy

logger = logging.getLogger(__name__)

#: Maximum number of paths handed to one command (argv/size guard).
MAX_COMMAND_PATHS = 60

#: Maximum characters of command output kept for reports/findings.
MAX_OUTPUT_CHARS = 20_000

#: Maximum seconds a check may run, regardless of the profile.
MAX_COMMAND_TIMEOUT_SECONDS = 600

#: Builtin checks implemented below.
BUILTIN_SYNTAX = "syntax"
BUILTIN_CONFLICT_MARKERS = "conflict_markers"
BUILTIN_MARKDOWN_LINKS = "markdown_links"

BUILTIN_CHECKS = (
    BUILTIN_SYNTAX,
    BUILTIN_CONFLICT_MARKERS,
    BUILTIN_MARKDOWN_LINKS,
)

#: Severity assigned to a syntax error.
SYNTAX_SEVERITY = "high"

#: Patterns parsed from linter output: ``file:line[:col]: message``.
_LINE_OUTPUT = re.compile(
    r"^(?P<path>[^\s][^:\n]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<message>.+)$"
)


# -----------------------------------------------------------------------------
# Profile value helpers (checks are pydantic models, but tests may pass dicts)
# -----------------------------------------------------------------------------
def _value(check: Any, key: str, default: Any = None) -> Any:
    """Read a check field from a pydantic model or a plain dict."""
    if isinstance(check, dict):
        return check.get(key, default)
    return getattr(check, key, default)


def check_enabled(check: Any) -> bool:
    """Return whether *check* is enabled."""
    return bool(_value(check, "enabled", True))


def check_id(check: Any) -> str:
    """Return the stable id of a check (falling back to its kind)."""
    return str(
        _value(check, "id", "")
        or _value(check, "builtin", "")
        or _value(check, "kind", "check")
    ).strip()


# -----------------------------------------------------------------------------
# Builtin checks
# -----------------------------------------------------------------------------
def _finding(
    guardian: str,
    check: Any,
    *,
    rule_id: str,
    title: str,
    severity: Any,
    path: str,
    line: Optional[int] = None,
    category: str = "correctness",
    evidence: str = "",
    remediation: str = "",
) -> Dict[str, Any]:
    """Build a deterministic finding attributed to *check*."""
    return finding_to_dict(
        guardian=guardian,
        rule_id=rule_id,
        title=title,
        severity=severity,
        path=path,
        line=line,
        category=category,
        evidence=evidence,
        remediation=remediation,
        source="deterministic",
        details={"check": check_id(check)},
    )


def _syntax_error(
    guardian: str,
    check: Any,
    file_entry: Dict[str, Any],
    error: Exception,
    line: Optional[int],
    column: Optional[int],
    message: str,
) -> Dict[str, Any]:
    """Build the finding of an invalid file."""
    path = file_entry.get("path", "")
    location = f"line {line}" if line else "unknown line"
    if column:
        location += f", column {column}"
    return _finding(
        guardian,
        check,
        rule_id=f"{BUILTIN_SYNTAX}:{file_entry.get('language') or 'file'}",
        title=f"Invalid {file_entry.get('language') or 'file'} syntax at {location}",
        severity=_value(check, "severity", SYNTAX_SEVERITY),
        path=path,
        line=line,
        evidence=message,
        remediation=(
            "Fix the syntax error before the change is committed. A parser must "
            "be able to read the file in a clean checkout."
        ),
    )


def check_python_syntax(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a Python source file with :mod:`ast`."""
    import ast

    content = file_entry.get("content") or ""
    try:
        ast.parse(content, filename=file_entry.get("path") or "<file>")
    except SyntaxError as ex:
        return [
            _syntax_error(
                guardian, check, file_entry, ex,
                line=ex.lineno, column=ex.offset, message=str(ex.msg or ex),
            )
        ]
    except (ValueError, TypeError) as ex:  # pragma: no cover - defensive
        return [
            _syntax_error(guardian, check, file_entry, ex, line=None, column=None, message=str(ex))
        ]
    return []


def check_json_syntax(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a JSON document."""
    try:
        json.loads(file_entry.get("content") or "")
    except json.JSONDecodeError as ex:
        return [
            _syntax_error(
                guardian, check, file_entry, ex,
                line=ex.lineno, column=ex.colno, message=ex.msg,
            )
        ]
    return []


def check_yaml_syntax(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a YAML document (skipped when PyYAML is unavailable)."""
    try:
        import yaml  # type: ignore
    except ImportError:  # pragma: no cover - PyYAML ships with the API image
        return []
    try:
        yaml.safe_load(file_entry.get("content") or "")
    except yaml.YAMLError as ex:
        mark = getattr(ex, "problem_mark", None)
        line = getattr(mark, "line", None)
        column = getattr(mark, "column", None)
        return [
            _syntax_error(
                guardian, check, file_entry, ex,
                line=(line + 1) if isinstance(line, int) else None,
                column=(column + 1) if isinstance(column, int) else None,
                message=str(getattr(ex, "problem", None) or ex),
            )
        ]
    return []


def check_toml_syntax(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a TOML document with the stdlib parser (3.11+)."""
    try:
        import tomllib  # type: ignore
    except ImportError:  # pragma: no cover - Python < 3.11
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return []
    try:
        tomllib.loads(file_entry.get("content") or "")
    except Exception as ex:
        line = getattr(ex, "lineno", None)
        column = getattr(ex, "colno", None)
        message = getattr(ex, "msg", None) or str(ex)
        return [
            _syntax_error(
                guardian, check, file_entry, ex,
                line=line, column=column, message=message,
            )
        ]
    return []


#: Extension -> syntax checker. Extensions not listed are not parsed.
SYNTAX_CHECKERS = {
    ".py": check_python_syntax,
    ".pyi": check_python_syntax,
    ".json": check_json_syntax,
    ".yaml": check_yaml_syntax,
    ".yml": check_yaml_syntax,
    ".toml": check_toml_syntax,
}


def check_syntax(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate the syntax of a file according to its extension."""
    if not file_entry.get("content") or file_entry.get("binary"):
        return []
    extension = os.path.splitext(str(file_entry.get("path") or "").lower())[1]
    checker = SYNTAX_CHECKERS.get(extension)
    if checker is None:
        return []
    return checker(guardian, check, file_entry)


def check_conflict_markers(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Report unresolved merge conflict markers."""
    content = file_entry.get("content") or ""
    if "<<" not in content and ">>" not in content and "||" not in content:
        return []
    findings: List[Dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.rstrip()
        if (
            stripped.startswith("<<<<<<< ")
            or stripped == "<<<<<<<"
            or stripped.startswith(">>>>>>> ")
            or stripped == ">>>>>>>"
            or stripped == "======="
            or stripped.startswith("||||||| ")
        ):
            findings.append(
                _finding(
                    guardian,
                    check,
                    rule_id=BUILTIN_CONFLICT_MARKERS,
                    title="Unresolved merge conflict marker",
                    severity=_value(check, "severity", "critical"),
                    path=file_entry.get("path", ""),
                    line=line_number,
                    category="correctness",
                    evidence=line.strip()[:200],
                    remediation=(
                        "Resolve the conflict and remove every marker before "
                        "committing. A file with conflict markers is broken by "
                        "definition."
                    ),
                )
            )
            # One marker is enough to make the file unusable; one finding per
            # file keeps the report readable.
            break
    return findings


#: Markdown inline link/image: ![alt](target "title") / [text](target).
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?P<target><[^>]+>|[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")


def _resolve_markdown_target(source_path: str, target: str) -> Optional[str]:
    """Resolve a local Markdown link to a project-relative path.

    Absolute site links (``/features/x``) and anchors are intentionally left
    alone: in VitePress they are routes, not files on disk.
    """
    target = target.strip().strip("<>").strip()
    if not target:
        return None
    lowered = target.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:", "#")):
        return None
    if target.startswith("/"):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    base = os.path.dirname(source_path)
    joined = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
    if joined == ".." or joined.startswith("../"):
        return joined
    return joined


def check_markdown_links(guardian: str, check: Any, file_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Report local Markdown links that point to a missing file."""
    path = str(file_entry.get("path") or "")
    content = file_entry.get("content") or ""
    if not path or not content:
        return []
    root = project_root_from_entry(file_entry)
    findings: List[Dict[str, Any]] = []
    seen: set = set()
    for match in _MARKDOWN_LINK.finditer(content):
        target = match.group("target")
        resolved = _resolve_markdown_target(path, target)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        candidates = [resolved]
        if not os.path.splitext(resolved)[1]:
            candidates += [
                f"{resolved}.md",
                f"{resolved}.mdx",
                f"{resolved}/index.md",
                f"{resolved}/README.md",
            ]
        if any(
            os.path.isfile(os.path.join(root, candidate))
            for candidate in candidates
        ):
            continue
        line = content[: match.start()].count("\n") + 1
        findings.append(
            _finding(
                guardian,
                check,
                rule_id="docs:broken-link",
                title=f"Broken local link: {target}",
                severity=_value(check, "severity", "low"),
                path=path,
                line=line,
                category="docs",
                evidence=match.group(0)[:240],
                remediation=(
                    "Point the link at an existing file (or remove it). Broken "
                    "links are visible to every reader and in the generated site."
                ),
            )
        )
        if len(findings) >= 25:
            break
    return findings


def project_root_from_entry(file_entry: Dict[str, Any]) -> str:
    """Return the project root carried by a file entry.

    The runner attaches ``_project_root`` to each entry so the pure checks do
    not need the settings object; tests can pass it explicitly.
    """
    return str(file_entry.get("_project_root") or os.getcwd())


#: Builtin id -> callable.
BUILTIN_RUNNERS = {
    BUILTIN_SYNTAX: check_syntax,
    BUILTIN_CONFLICT_MARKERS: check_conflict_markers,
    BUILTIN_MARKDOWN_LINKS: check_markdown_links,
}


# -----------------------------------------------------------------------------
# Command checks
# -----------------------------------------------------------------------------
def _output_text(value: Any) -> str:
    """Return *value* as bounded text."""
    text = (value or "").strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
    return text


def _resolve_executable(settings: Any, executable: str) -> Optional[str]:
    """Resolve the first token of a check command.

    Relative paths are resolved from the project root; an absolute path must
    exist. A bare name is looked up in ``PATH``.
    """
    executable = str(executable or "").strip()
    if not executable:
        return None
    if os.path.isabs(executable):
        return executable if os.path.isfile(executable) else None
    if "/" in executable or "\\" in executable:
        candidate = os.path.join(project_root(settings), executable)
        if os.path.isfile(candidate):
            return candidate
        return None
    found = shutil.which(executable)
    return found


def resolve_command(
    settings: Any,
    command: Sequence[str],
    paths: Sequence[str],
    *,
    attach_paths: bool = True,
    cwd: str = "",
) -> Tuple[List[str], List[str], Optional[str]]:
    """Build the argv of a command check.

    Args:
        settings: Project settings.
        command: Declared argv.
        paths: Existing project-relative paths the check applies to.
        attach_paths: Append the paths (or expand ``{paths}``).
        cwd: Project-relative working directory.

    Returns:
        Tuple[list, list, str]: ``(argv, paths_used, error)``. Error is set when
        the command cannot be built (missing executable, cwd outside project).
    """
    raw = [str(part) for part in (command or []) if str(part).strip()]
    if not raw:
        return [], [], "empty command"

    resolved = _resolve_executable(settings, raw[0])
    if not resolved:
        return [], [], f"executable not found: {raw[0]}"

    selected = list(paths)[:MAX_COMMAND_PATHS]
    argv: List[str] = [resolved]
    replaced = False
    for part in raw[1:]:
        if "{paths}" in part:
            replaced = True
            template = part
            for path in selected:
                argv.append(template.replace("{paths}", path))
        else:
            argv.append(part)
    if attach_paths and not replaced:
        argv.extend(selected)

    working_directory = project_root(settings)
    if cwd:
        relative = normalize_path(settings, cwd)
        if relative is None:
            return [], [], f"working directory outside the project: {cwd}"
        working_directory = os.path.join(project_root(settings), relative.replace("/", os.sep))
        if not os.path.isdir(working_directory):
            return [], [], f"working directory does not exist: {cwd}"
    return argv, selected, None if working_directory else "invalid working directory"


def _redact_output(text: str, settings: Any) -> str:
    """Redact secrets from command output before storing/showing it.

    A linter can print a file path, an environment value or an accidental
    credential. The CISO redactor is the project's single secret boundary; when
    it is unavailable the output is kept as-is (the command ran inside the
    project and the run is project-local), but the failure is logged.
    """
    value = str(text or "")
    if not value:
        return value
    try:
        from codx.junior.security.ciso.gate import redact_output

        return str(redact_output(value, settings=settings) or value)
    except Exception as ex:  # pragma: no cover - depends on the install profile
        logger.debug("Quality check output redaction unavailable: %s", ex)
        return value


def _guard_command(settings: Any, argv: Sequence[str]) -> Optional[str]:
    """Return a block reason when the CISO command gate rejects *argv*.

    The gate is fail-closed: if it cannot decide, the command is not executed.
    A missing CISO installation is treated as a gate failure, not as a pass.
    """
    try:
        from codx.junior.security.ciso.gate import guard_command

        command = shlex.join([str(part) for part in argv])
        verdict = guard_command(command, settings=settings)
    except Exception as ex:  # pragma: no cover - depends on the install profile
        logger.warning("Quality check command gate failed: %s", ex)
        return f"security command gate failed: {ex}"
    if getattr(verdict, "blocked", False):
        rules = getattr(verdict, "rule_ids", None) or []
        reason = ", ".join(str(rule) for rule in rules[:5]) or "blocked by CISO policy"
        return f"blocked by the security command gate ({reason})"
    return None


def _parse_json_output(output: str, project_root_path: str) -> List[Dict[str, Any]]:
    """Parse eslint/ruff style JSON output into ``{path,line,message}`` entries."""
    text = (output or "").strip()
    if not text.startswith(("[", "{")):
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("files") or data.get("results") or data.get("diagnostics") or [data]
    if not isinstance(data, list):
        return []

    entries: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = item.get("filePath") or item.get("filename") or item.get("path") or ""
        messages = item.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                entries.append(
                    {
                        "path": path,
                        "line": message.get("line"),
                        "message": message.get("message") or message.get("text") or "",
                    }
                )
        elif item.get("message"):
            location = item.get("location")
            line = item.get("line")
            if line is None and isinstance(location, dict):
                line = location.get("row") or location.get("line")
            entries.append(
                {
                    "path": path,
                    "line": line,
                    "message": item.get("message"),
                }
            )
    return entries


def _parse_output(output: str, project_root_path: str) -> List[Dict[str, Any]]:
    """Parse common linter output into ``{path,line,message}`` entries."""
    entries = _parse_json_output(output, project_root_path)
    if entries:
        return entries

    for line in (output or "").splitlines():
        match = _LINE_OUTPUT.match(line.strip())
        if not match:
            continue
        path = match.group("path").strip()
        if path.startswith(project_root_path):
            path = os.path.relpath(path, project_root_path)
        path = path.replace("\\", "/").lstrip("./")
        message = match.group("message").strip()
        if not path or not message:
            continue
        try:
            line_number = int(match.group("line"))
        except (TypeError, ValueError):
            line_number = None
        entries.append({"path": path, "line": line_number, "message": message})
    return entries


def run_command_check(
    guardian: str,
    check: Any,
    settings: Any,
    files: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Execute one command check over the matched files.

    Returns:
        Tuple[list, dict]: ``(findings, execution)`` where execution is the
        record shown in the run report (status, command, output excerpt).
    """
    identifier = check_id(check)
    execution: Dict[str, Any] = {
        "guardian": guardian,
        "check_id": identifier,
        "title": str(_value(check, "title", "") or identifier),
        "kind": "command",
        "status": "skipped",
        "command": [],
        "files": [],
        "exit_code": None,
        "duration_seconds": 0.0,
        "message": "",
    }

    existing = [
        file_entry for file_entry in files
        if file_entry.get("status") != "deleted" and os.path.isfile(file_entry.get("absolute") or "")
    ]
    if not existing:
        execution["message"] = "no existing file matched"
        return [], execution

    paths = [str(file_entry.get("path") or "") for file_entry in existing]
    argv, selected, error = resolve_command(
        settings,
        _value(check, "command", []) or [],
        paths,
        attach_paths=bool(_value(check, "attach_paths", True)),
        cwd=str(_value(check, "cwd", "") or ""),
    )
    execution["files"] = selected
    if error:
        execution["message"] = error
        return [], execution
    execution["command"] = argv

    blocked = _guard_command(settings, argv)
    if blocked:
        execution["status"] = "blocked"
        execution["message"] = blocked
        return [
            _finding(
                guardian,
                check,
                rule_id=f"check:{identifier}:blocked",
                title=f"Check `{identifier}` was blocked by the security gate",
                severity="high",
                path=selected[0] if selected else "",
                category="security",
                evidence=blocked,
                remediation=(
                    "Review the command declared by this guardian. Quality "
                    "checks run through the CISO command gate; a blocked "
                    "command is never executed."
                ),
            )
        ], execution

    timeout = int(_value(check, "timeout_seconds", 120) or 120)
    timeout = max(1, min(timeout, MAX_COMMAND_TIMEOUT_SECONDS))
    cwd = project_root(settings)
    relative_cwd = str(_value(check, "cwd", "") or "")
    if relative_cwd:
        normalized = normalize_path(settings, relative_cwd)
        if normalized:
            cwd = os.path.join(project_root(settings), normalized.replace("/", os.sep))

    import time
    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env={
                **os.environ,
                "CI": "1",
                "NO_COLOR": "1",
                "FORCE_COLOR": "0",
                "PYTHONIOENCODING": "utf-8",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except subprocess.TimeoutExpired:
        execution.update(
            {
                "status": "timeout",
                "duration_seconds": round(time.time() - started, 2),
                "message": f"timed out after {timeout}s",
            }
        )
        return [
            _finding(
                guardian,
                check,
                rule_id=f"check:{identifier}:timeout",
                title=f"Check `{identifier}` timed out after {timeout}s",
                severity="medium",
                path=selected[0] if selected else "",
                category="environment",
                remediation=(
                    "Run the check manually, reduce the file set or raise the "
                    "check timeout in the guardian profile."
                ),
            )
        ], execution
    except OSError as ex:
        execution.update({"status": "error", "message": str(ex)})
        return [], execution

    duration = round(time.time() - started, 2)
    output = _output_text((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))
    output = _redact_output(output, settings)
    execution.update(
        {
            "status": "passed" if completed.returncode == 0 else "failed",
            "duration_seconds": duration,
            "exit_code": completed.returncode,
            "message": output[:4000],
        }
    )
    if completed.returncode == 0:
        return [], execution

    severity = _value(check, "severity", "medium")
    findings: List[Dict[str, Any]] = []
    for entry in _parse_output(output, project_root(settings)):
        path = normalize_path(settings, entry.get("path") or "")
        if not path:
            continue
        message = " ".join(str(entry.get("message") or "").split())
        findings.append(
            _finding(
                guardian,
                check,
                rule_id=f"check:{identifier}",
                title=message[:300] or f"Check `{identifier}` reported a problem",
                severity=severity,
                path=path,
                line=entry.get("line"),
                category="lint",
                evidence=message[:1000],
                remediation=f"Fix the issue reported by `{identifier}` and re-run the guardian.",
            )
        )
        if len(findings) >= 50:
            break

    if not findings:
        findings.append(
            _finding(
                guardian,
                check,
                rule_id=f"check:{identifier}:failed",
                title=f"Check `{identifier}` failed with exit code {completed.returncode}",
                severity=severity,
                path=selected[0] if selected else "",
                category="lint",
                evidence=output[-1500:],
                remediation=f"Run `{' '.join(argv[:4])}` locally and fix the reported problems.",
            )
        )
    return findings, execution


def run_builtin_check(
    guardian: str,
    check: Any,
    file_entry: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Run one builtin check on one file.

    Returns:
        Tuple[list, Optional[str]]: ``(findings, error)``. An unknown builtin is
        an error (the profile declares something this version cannot run), not
        a silent pass.
    """
    builtin = str(_value(check, "builtin", "") or _value(check, "id", "")).strip()
    runner = BUILTIN_RUNNERS.get(builtin)
    if runner is None:
        return [], f"unknown builtin check: {builtin or '(empty)'}"
    try:
        return runner(guardian, check, file_entry), None
    except Exception as ex:  # pragma: no cover - a check must never stop a run
        logger.exception("Builtin check %s failed for %s", builtin, file_entry.get("path"))
        return [], f"{builtin}: {ex}"


# -----------------------------------------------------------------------------
# Guardian check orchestration
# -----------------------------------------------------------------------------
def run_checks(
    guardian: str,
    checks: Iterable[Any],
    settings: Any,
    files: List[Dict[str, Any]],
    *,
    policy: Optional[GuardianPolicy] = None,
    extra_ignore_paths: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Run every enabled check of a guardian over its matched files.

    Builtin checks run once per file; command checks run once for their whole
    matching file set (a linter startup is the expensive part).

    Args:
        guardian: Guardian profile name.
        checks: Declared checks (pydantic or dicts).
        settings: Project settings.
        files: Matched file payloads.
        policy: Effective guardian policy.
        extra_ignore_paths: Guardian-specific ignores.

    Returns:
        Tuple[list, list, list]: ``(findings, executions, errors)``.
    """
    policy = policy or GuardianPolicy()
    findings: List[Dict[str, Any]] = []
    executions: List[Dict[str, Any]] = []
    errors: List[str] = []

    matched = []
    for file_entry in files or []:
        path = str(file_entry.get("path") or "")
        if not path or is_ignored_path(path, policy=policy, extra=extra_ignore_paths):
            continue
        if file_entry.get("binary"):
            continue
        enriched = dict(file_entry)
        enriched["_project_root"] = project_root(settings)
        matched.append(enriched)

    if not matched:
        return [], [], []

    for check in checks or []:
        if not check_enabled(check):
            continue
        kind = str(_value(check, "kind", "builtin") or "builtin").strip().lower()
        identifier = check_id(check)
        if kind == "builtin":
            before = len(findings)
            check_errors: List[str] = []
            for file_entry in matched:
                produced, error = run_builtin_check(guardian, check, file_entry)
                findings.extend(produced)
                if error:
                    check_errors.append(error)
                    errors.append(f"{guardian}/{identifier}: {error}")
            executions.append(
                {
                    "guardian": guardian,
                    "check_id": identifier,
                    "title": str(_value(check, "title", "") or identifier),
                    "kind": "builtin",
                    "status": (
                        "error" if check_errors
                        else "failed" if len(findings) > before
                        else "passed"
                    ),
                    "command": [],
                    "files": [file_entry.get("path") for file_entry in matched],
                    "exit_code": None,
                    "duration_seconds": 0.0,
                    "message": "; ".join(check_errors)[:4000],
                }
            )
        elif kind == "command":
            produced, execution = run_command_check(guardian, check, settings, matched)
            findings.extend(produced)
            executions.append(execution)
            if execution.get("status") in ("error", "blocked", "timeout"):
                errors.append(f"{guardian}/{identifier}: {execution.get('message')}")
        else:
            errors.append(f"{guardian}/{identifier}: unknown check kind {kind!r}")

    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for finding in findings:
        identifier = finding.get("id") or ""
        if identifier in seen:
            continue
        seen.add(identifier)
        deduped.append(finding)
    return deduped, executions, errors


def run_fix_commands(
    guardian: str,
    checks: Iterable[Any],
    settings: Any,
    files: List[Dict[str, Any]],
    *,
    policy: Optional[GuardianPolicy] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Run the declared deterministic fix commands (explicit call only).

    Args:
        guardian: Guardian name.
        checks: Declared checks.
        settings: Project settings.
        files: Files to fix.
        policy: Effective policy.

    Returns:
        Tuple[list, list]: ``(executions, errors)``.
    """
    policy = policy or GuardianPolicy()
    executions: List[Dict[str, Any]] = []
    errors: List[str] = []
    existing = [
        file_entry for file_entry in files or []
        if file_entry.get("status") != "deleted" and os.path.isfile(file_entry.get("absolute") or "")
    ]
    if not existing:
        return [], ["No existing file to fix"]

    for check in checks or []:
        if not check_enabled(check):
            continue
        fix_command = _value(check, "fix_command", []) or []
        if not fix_command:
            continue
        identifier = check_id(check)
        argv, selected, error = resolve_command(
            settings,
            fix_command,
            [str(file_entry.get("path") or "") for file_entry in existing],
            attach_paths=bool(_value(check, "attach_paths", True)),
            cwd=str(_value(check, "cwd", "") or ""),
        )
        if error:
            errors.append(f"{guardian}/{identifier}: {error}")
            continue
        blocked = _guard_command(settings, argv)
        if blocked:
            errors.append(f"{guardian}/{identifier}: {blocked}")
            continue
        fix_cwd = project_root(settings)
        relative_cwd = str(_value(check, "cwd", "") or "")
        if relative_cwd:
            normalized = normalize_path(settings, relative_cwd)
            if normalized:
                fix_cwd = os.path.join(project_root(settings), normalized.replace("/", os.sep))
        try:
            completed = subprocess.run(
                argv,
                cwd=fix_cwd,
                capture_output=True,
                text=True,
                timeout=max(1, min(int(_value(check, "timeout_seconds", 120) or 120), MAX_COMMAND_TIMEOUT_SECONDS)),
                shell=False,
                check=False,
                env={**os.environ, "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"},
            )
            executions.append(
                {
                    "guardian": guardian,
                    "check_id": identifier,
                    "command": argv,
                    "files": selected,
                    "status": "fixed" if completed.returncode == 0 else "failed",
                    "exit_code": completed.returncode,
                    "message": _redact_output(
                        _output_text((completed.stdout or "") + (completed.stderr or "")), settings
                    )[:4000],
                }
            )
            if completed.returncode != 0:
                errors.append(
                    f"{guardian}/{identifier}: fix command exited with {completed.returncode}"
                )
        except (OSError, subprocess.SubprocessError) as ex:
            errors.append(f"{guardian}/{identifier}: {ex}")
    return executions, errors


# Made with ❤️ by codx-junior
