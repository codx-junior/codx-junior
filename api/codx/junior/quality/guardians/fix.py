"""Fix quality findings with deterministic commands or an AI patch proposal.

Two different kinds of fix exist, and mixing them would be unsafe:

* **Deterministic fixes** are declared by the guardian itself
  (``ProfileGuardianCheck.fix_command``: ruff --fix, prettier --write, gofmt -w).
  They are reproducible and are still executed through the CISO command gate.
* **AI fixes** are a proposal until a human or an explicit ``quality_fix`` call
  accepts them. The model returns exact search/replace pairs, never a free-form
  diff; every search must match **exactly once** in the current file, otherwise
  the whole file is left untouched.

The apply step writes atomically, so a failed search never leaves a half-edited
file behind. A fix is not "done" because a model says so: the caller re-runs the
guardian checks afterwards (see :func:`codx.junior.quality.guardians.runner`).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .ai import _call_model, _extract_json
from .diff import absolute_path, line_count, read_text_file
from .models import finding_fingerprint

logger = logging.getLogger(__name__)

#: Maximum characters of a file sent to the fix model.
MAX_FIX_FILE_CHARS = 30_000

#: Maximum search/replace changes accepted for one file.
MAX_FIX_CHANGES = 20

#: Maximum files fixed in one call.
MAX_FIX_FILES = 10


# -----------------------------------------------------------------------------
# Deterministic apply
# -----------------------------------------------------------------------------
def apply_search_replace(
    settings: Any,
    path: str,
    changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply exact search/replace changes to one project file.

    Args:
        settings: Project settings.
        path: Project-relative path.
        changes: ``{"search": str, "replace": str, "reason"?: str}`` entries.

    Returns:
        Dict: ``applied``, ``path``, ``content``, ``errors``.

    Raises:
        ValueError: When the path is outside the project.
        OSError: When the file cannot be read/written.
    """
    absolute = absolute_path(settings, path)
    if not absolute:
        raise ValueError(f"Path outside the project: {path}")
    if not os.path.isfile(absolute):
        raise OSError(f"File does not exist: {path}")
    if len(changes or []) > MAX_FIX_CHANGES:
        raise ValueError(f"At most {MAX_FIX_CHANGES} changes per file are accepted")

    content = read_text_file(absolute, max_bytes=5_000_000)[0]
    errors: List[str] = []
    applied = 0
    for index, change in enumerate(changes or []):
        if not isinstance(change, dict):
            errors.append(f"change #{index}: not an object")
            continue
        search = change.get("search")
        replace = change.get("replace")
        if not isinstance(search, str) or not search:
            errors.append(f"change #{index}: missing/empty search")
            continue
        if not isinstance(replace, str):
            errors.append(f"change #{index}: missing replace")
            continue
        occurrences = content.count(search)
        if occurrences != 1:
            errors.append(
                f"change #{index}: search must match exactly once, found {occurrences}"
            )
            continue
        content = content.replace(search, replace, 1)
        applied += 1

    if errors:
        return {
            "applied": 0,
            "path": str(path),
            "content": "",
            "errors": errors,
        }

    tmp = f"{absolute}.quality-fix.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp, absolute)
    return {
        "applied": applied,
        "path": str(path),
        "content": content,
        "errors": [],
    }


# -----------------------------------------------------------------------------
# AI proposal
# -----------------------------------------------------------------------------
def _numbered_content(content: str, limit: int = MAX_FIX_FILE_CHARS) -> str:
    """Return line-numbered content bounded by *limit*."""
    text = str(content or "")
    if len(text) > limit:
        text = text[:limit] + "\n... (file truncated)"
    return "\n".join(
        f"{index:>5}: {line}"
        for index, line in enumerate(text.splitlines(), start=1)
    )


def build_fix_prompt(
    settings: Any,
    path: str,
    content: str,
    findings: List[Dict[str, Any]],
    *,
    guardian: str = "",
) -> str:
    """Build the prompt that asks the model for exact search/replace fixes."""
    project = str(getattr(settings, "project_name", "") or "this project")
    finding_lines = []
    for finding in findings:
        line = finding.get("line")
        finding_lines.append(
            "- [{severity}] {title}{line} — {remediation}".format(
                severity=finding.get("severity", "medium"),
                title=finding.get("title", ""),
                line=f" (line {line})" if line else "",
                remediation=finding.get("remediation") or finding.get("evidence") or "",
            )
        )
    return f"""You are fixing quality findings in **{project}** as the `{guardian or 'quality'}` guardian.

## File
`{path}` (line numbered):

```
{_numbered_content(content)}
```

## Findings to fix
{chr(10).join(finding_lines) or "- (no finding detail)"}

## Rules
- Fix only these findings. Do not refactor, rename or reformat unrelated code.
- Preserve behaviour and the project's style.
- Each `search` must be an exact, unique substring of the current file (include enough context to be unique).
- `replace` must contain the corrected text and keep the surrounding indentation exactly.
- If a finding cannot be fixed safely, omit it instead of guessing.
- Return only one JSON object:

```json
{{
  "summary": "one sentence describing the fix",
  "changes": [
    {{"search": "exact current text", "replace": "corrected text", "reason": "what it fixes"}}
  ]
}}
```
"""


async def propose_fixes(
    settings: Any,
    session: Any,
    *,
    path: str,
    findings: List[Dict[str, Any]],
    guardian: str = "",
    apply: bool = False,
    ai: Any = None,
) -> Dict[str, Any]:
    """Ask the model for exact fixes of one file, optionally applying them.

    Args:
        settings: Project settings.
        session: Active session (for the AI).
        path: Project-relative file path.
        findings: Findings to fix.
        guardian: Guardian name (prompt attribution).
        apply: Write the accepted changes (default false = proposal only).
        ai: Explicit AI instance (tests).

    Returns:
        Dict: ``path``, ``summary``, ``changes``, ``applied``, ``errors``,
        ``cost``, ``model`` and ``content`` (after apply).
    """
    result: Dict[str, Any] = {
        "path": path,
        "summary": "",
        "changes": [],
        "applied": 0,
        "errors": [],
        "cost": 0.0,
        "model": "",
        "content": "",
    }
    absolute = absolute_path(settings, path)
    if not absolute or not os.path.isfile(absolute):
        result["errors"].append(f"File does not exist: {path}")
        return result
    content, read_error = read_text_file(absolute, max_bytes=5_000_000)
    if read_error:
        result["errors"].append(f"{path}: {read_error}")
        return result

    if ai is None and session is not None:
        get_ai = getattr(session, "get_ai", None)
        if callable(get_ai):
            try:
                ai = get_ai()
            except Exception as ex:
                result["errors"].append(f"AI is not available: {ex}")
                return result
    if ai is None:
        result["errors"].append("AI is not available in this session")
        return result

    prompt = build_fix_prompt(settings, path, content, findings, guardian=guardian)
    try:
        answer, cost, model = await _call_model(ai, prompt)
    except Exception as ex:
        logger.exception("Quality fix model call failed: %s", ex)
        result["errors"].append(str(ex) or "The fix model failed")
        return result

    result["cost"] = round(cost or 0.0, 6)
    result["model"] = model
    data = _extract_json(answer)
    if not isinstance(data, dict):
        result["errors"].append("The fix model did not return JSON")
        return result

    raw_changes = data.get("changes")
    if not isinstance(raw_changes, list):
        result["errors"].append("The fix model returned no changes list")
        return result
    changes: List[Dict[str, Any]] = []
    for raw in raw_changes[:MAX_FIX_CHANGES]:
        if not isinstance(raw, dict):
            continue
        search = raw.get("search")
        replace = raw.get("replace")
        if not isinstance(search, str) or not search or not isinstance(replace, str):
            continue
        changes.append(
            {
                "search": search,
                "replace": replace,
                "reason": str(raw.get("reason") or "").strip()[:500],
            }
        )
    result["changes"] = changes
    result["summary"] = " ".join(str(data.get("summary") or "").split())[:1000]

    if apply and changes:
        try:
            applied = apply_search_replace(settings, path, changes)
        except (OSError, ValueError) as ex:
            result["errors"].append(str(ex))
            return result
        result["applied"] = applied.get("applied", 0)
        result["errors"].extend(applied.get("errors") or [])
        result["content"] = applied.get("content", "")
    return result


def _diff_preview(path: str, original: str, proposed: str) -> str:
    """Build a compact before/after preview for a proposed fix."""
    import difflib

    diff = difflib.unified_diff(
        original.splitlines(),
        proposed.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=3,
    )
    return "\n".join(list(diff)[:200])


def apply_changes_to_content(content: str, changes: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Apply search/replace changes to an in-memory string (preview helper)."""
    errors: List[str] = []
    for index, change in enumerate(changes or []):
        search = str(change.get("search") or "")
        replace = str(change.get("replace") or "")
        occurrences = content.count(search)
        if not search or occurrences != 1:
            errors.append(f"change #{index}: search matches {occurrences} time(s)")
            continue
        content = content.replace(search, replace, 1)
    return content, errors


# Made with ❤️ by codx-junior
