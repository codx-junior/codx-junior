"""Models of the project quality guardian subsystem.

A **guardian** is a profile whose ``file_match`` selects the files it owns and
whose Markdown content is its review prompt. This module models the *result* of
running guardians, not their declaration: the declaration lives on
:class:`codx.junior.model.profile.ProfileGuardianSettings` so it travels with
the profile JSON and is edited by the normal profile/team tools.

The design is deliberately evidence-first:

* deterministic checks produce findings with an exact rule id and file/line;
* the AI review produces the same shape, never a free-form essay;
* a *run* is an immutable record (what was reviewed, what ran, what was found);
* a *verdict* is a deterministic ladder over the findings (allow → warn →
  require_approval → block), so two callers can never disagree about the same
  run.

Everything here is JSON serialisable: a run is persisted under
``.codx/quality/runs/`` and consumed by the API, the tools and the UI without a
conversion layer.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Enums and severity helpers
# -----------------------------------------------------------------------------
class GuardianSeverity(str, Enum):
    """How bad a quality finding is."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Ordered severity rank used by the ladder and by ``max`` operations.
SEVERITY_RANK: Dict[GuardianSeverity, int] = {
    GuardianSeverity.INFO: 0,
    GuardianSeverity.LOW: 1,
    GuardianSeverity.MEDIUM: 2,
    GuardianSeverity.HIGH: 3,
    GuardianSeverity.CRITICAL: 4,
}

#: Every alias a model (or a human) may use for a severity.
_SEVERITY_ALIASES: Dict[str, GuardianSeverity] = {
    "info": GuardianSeverity.INFO,
    "information": GuardianSeverity.INFO,
    "note": GuardianSeverity.INFO,
    "nit": GuardianSeverity.LOW,
    "low": GuardianSeverity.LOW,
    "minor": GuardianSeverity.LOW,
    "medium": GuardianSeverity.MEDIUM,
    "moderate": GuardianSeverity.MEDIUM,
    "warning": GuardianSeverity.MEDIUM,
    "warn": GuardianSeverity.MEDIUM,
    "high": GuardianSeverity.HIGH,
    "major": GuardianSeverity.HIGH,
    "error": GuardianSeverity.HIGH,
    "critical": GuardianSeverity.CRITICAL,
    "blocker": GuardianSeverity.CRITICAL,
    "blocking": GuardianSeverity.CRITICAL,
}

#: Severities in increasing order (useful for UI filters).
SEVERITY_ORDER = [
    GuardianSeverity.INFO,
    GuardianSeverity.LOW,
    GuardianSeverity.MEDIUM,
    GuardianSeverity.HIGH,
    GuardianSeverity.CRITICAL,
]


class GuardianAction(str, Enum):
    """What a consumer must do with a guardian verdict."""

    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


def normalize_severity(value: Any, default: GuardianSeverity = GuardianSeverity.MEDIUM) -> GuardianSeverity:
    """Coerce a severity coming from a profile, a check or a model.

    Args:
        value: Severity name or :class:`GuardianSeverity`.
        default: Value used when it cannot be recognised.

    Returns:
        GuardianSeverity: The normalised severity.

    Example:
        >>> normalize_severity("BLOCKER").value
        'critical'
    """
    if isinstance(value, GuardianSeverity):
        return value
    text = str(value or "").strip().lower().replace("-", "_")
    return _SEVERITY_ALIASES.get(text, default)


def severity_value(value: Any, default: str = "medium") -> str:
    """Return the string value of a severity (for profile JSON)."""
    return normalize_severity(value, normalize_severity(default)).value


def highest_severity(findings: List["GuardianFinding"]) -> Optional[GuardianSeverity]:
    """Return the highest severity of *findings* (``None`` when empty)."""
    highest: Optional[GuardianSeverity] = None
    for finding in findings:
        if highest is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[highest]:
            highest = finding.severity
    return highest


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def utc_now() -> str:
    """Return the current UTC instant as an ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, max_length: int = 60) -> str:
    """Turn arbitrary text into a stable identifier fragment.

    Args:
        value: Text to normalise.
        max_length: Maximum length of the result.

    Returns:
        str: Lowercase ASCII words joined by ``-``; ``"finding"`` when empty.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:max_length].strip("-") or "finding")


def finding_fingerprint(guardian: str, rule_id: str, path: str, title: str) -> str:
    """Return the stable id of a finding inside its guardian.

    The fingerprint deliberately excludes the line number and the evidence: the
    same rule on the same file is the same problem when the file moves a few
    lines, which is what dismissal/suppression has to survive.

    Args:
        guardian: Guardian name.
        rule_id: Rule identifier.
        path: Project-relative path.
        title: Finding title (normalised).

    Returns:
        str: 16 hexadecimal characters.
    """
    payload = "|".join(
        [
            str(guardian or "").strip().lower(),
            str(rule_id or "").strip().lower(),
            str(path or "").replace("\\", "/").strip().lstrip("./").lower(),
            " ".join(str(title or "").split()).lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint_text(value: str) -> str:
    """Return a short stable hash of *value* (for signatures and caches)."""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


# -----------------------------------------------------------------------------
# Findings
# -----------------------------------------------------------------------------
class GuardianFinding(dict):
    """A finding as a plain dict with helper properties.

    The run is persisted as JSON, so findings are kept as plain dictionaries
    rather than pydantic models: a stored run from an older version must still
    load, and extra fields written by a newer check must not be dropped.
    """

    def __init__(self, **data: Any) -> None:
        super().__init__()
        self.update(data)
        self.setdefault("id", "")
        self.setdefault("guardian", "")
        self.setdefault("rule_id", "quality")
        self.setdefault("title", "")
        self.setdefault("severity", GuardianSeverity.MEDIUM.value)
        self.setdefault("category", "")
        self.setdefault("path", "")
        self.setdefault("line", None)
        self.setdefault("evidence", "")
        self.setdefault("remediation", "")
        self.setdefault("confidence", 1.0)
        self.setdefault("source", "deterministic")
        self.setdefault("status", "open")
        self.setdefault("created_at", utc_now())

    @property
    def severity_enum(self) -> GuardianSeverity:
        """Severity of the finding as an enum."""
        return normalize_severity(self.get("severity"))


# -----------------------------------------------------------------------------
# Run records
# -----------------------------------------------------------------------------
def finding_to_dict(
    *,
    guardian: str,
    rule_id: str,
    title: str,
    severity: Any,
    path: str = "",
    line: Optional[int] = None,
    category: str = "",
    evidence: str = "",
    remediation: str = "",
    confidence: float = 1.0,
    source: str = "deterministic",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a normalised, fingerprinted finding dictionary.

    Args:
        guardian: Guardian name.
        rule_id: Rule identifier (``syntax``, ``ai:no-any``, ...).
        title: One line description.
        severity: Severity alias or enum.
        path: Project-relative file path.
        line: 1-based new-file line (when known).
        category: Quality dimension (correctness, tests, docs, ...).
        evidence: Short excerpt supporting the finding.
        remediation: Concrete fix.
        confidence: 0..1 confidence.
        source: ``deterministic`` or ``ai``.
        details: Extra machine-readable context (check exit code, ...).

    Returns:
        Dict: A finding ready to persist and render.
    """
    normalised_path = str(path or "").replace("\\", "/").lstrip("./")
    clean_title = " ".join(str(title or "").split())
    normalised_rule = str(rule_id or "quality").strip() or "quality"
    try:
        line_number = int(line) if line is not None and str(line).strip() else None
    except (TypeError, ValueError):
        line_number = None
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 1.0

    finding = GuardianFinding(
        guardian=str(guardian or "").strip(),
        rule_id=normalised_rule,
        title=clean_title or "Quality finding",
        severity=normalize_severity(severity).value,
        category=str(category or "").strip(),
        path=normalised_path,
        line=line_number,
        evidence=str(evidence or "").strip()[:2000],
        remediation=str(remediation or "").strip()[:4000],
        confidence=round(confidence_value, 2),
        source=str(source or "deterministic").strip().lower() or "deterministic",
    )
    if details:
        finding["details"] = details
    finding["id"] = finding_fingerprint(
        finding["guardian"], finding["rule_id"], finding["path"], finding["title"]
    )
    return dict(finding)


def make_verdict(
    findings: List[Dict[str, Any]],
    *,
    block_above: str = "high",
    approval_above: str = "medium",
    warn_above: str = "low",
) -> Dict[str, Any]:
    """Evaluate the deterministic ladder for a list of findings.

    The ladder is the same shape used by QA and CISO so a caller only has to
    read ``action``. Severities are compared by rank, not by name.

    Args:
        findings: Findings of the run.
        block_above: Findings at or above this severity block.
        approval_above: Findings at or above this severity ask for a human.
        warn_above: Findings at or above this severity warn.

    Returns:
        Dict: ``action``, ``blocked``, ``requires_approval``, ``passed``,
        ``highest_severity``, ``counts`` and ``rule_ids``.
    """
    open_findings = [
        finding for finding in findings
        if str(finding.get("status") or "open") == "open"
    ]
    counts = {
        severity.value: 0 for severity in SEVERITY_ORDER
    }
    highest: Optional[GuardianSeverity] = None
    for finding in open_findings:
        severity = normalize_severity(finding.get("severity"))
        counts[severity.value] += 1
        if highest is None or SEVERITY_RANK[severity] > SEVERITY_RANK[highest]:
            highest = severity

    block = SEVERITY_RANK[normalize_severity(block_above, GuardianSeverity.HIGH)]
    approval = SEVERITY_RANK[normalize_severity(approval_above, GuardianSeverity.MEDIUM)]
    warn = SEVERITY_RANK[normalize_severity(warn_above, GuardianSeverity.LOW)]

    action = GuardianAction.ALLOW
    if highest is not None:
        rank = SEVERITY_RANK[highest]
        if rank >= block:
            action = GuardianAction.BLOCK
        elif rank >= approval:
            action = GuardianAction.REQUIRE_APPROVAL
        elif rank >= warn:
            action = GuardianAction.WARN

    rule_ids = sorted({str(finding.get("rule_id") or "") for finding in open_findings if finding.get("rule_id")})
    summary = _verdict_summary(action, open_findings, highest, counts)
    return {
        "action": action.value,
        "blocked": action == GuardianAction.BLOCK,
        "requires_approval": action == GuardianAction.REQUIRE_APPROVAL,
        "passed": action == GuardianAction.ALLOW,
        "highest_severity": highest.value if highest else "",
        "counts": counts,
        "finding_count": len(open_findings),
        "rule_ids": rule_ids,
        "summary": summary,
    }


def _verdict_summary(
    action: GuardianAction,
    findings: List[Dict[str, Any]],
    highest: Optional[GuardianSeverity],
    counts: Dict[str, int],
) -> str:
    """Build the one-line verdict summary shown in reports."""
    if action == GuardianAction.ALLOW:
        return "No quality findings."
    parts = []
    for severity in reversed(SEVERITY_ORDER):
        if counts.get(severity.value):
            parts.append(f"{counts[severity.value]} {severity.value}")
    detail = ", ".join(parts) or f"{len(findings)} finding(s)"
    return f"Quality {action.value}: {detail} (highest: {highest.value if highest else 'none'})."


# -----------------------------------------------------------------------------
# Report rendering
# -----------------------------------------------------------------------------
def render_run_markdown(run: Dict[str, Any], *, title: str = "") -> str:
    """Render a stored run as a Markdown report.

    Args:
        run: Run dict as returned by the runner/store.
        title: Optional title override.

    Returns:
        str: Markdown report ready for a chat, a task or the UI.
    """
    verdict = run.get("verdict") or {}
    findings = run.get("findings") or []
    checks = run.get("checks") or []
    files = run.get("files") or []
    errors = run.get("errors") or []
    action = verdict.get("action") or "allow"
    icon = {"allow": "✅", "warn": "⚠️", "require_approval": "🟠", "block": "⛔"}.get(action, "•")

    lines: List[str] = [
        f"# {title or 'Quality guardian report'}",
        "",
        f"{icon} **{action.replace('_', ' ').upper()}** — {verdict.get('summary') or 'No summary.'}",
        "",
        f"- Trigger: `{run.get('trigger', 'manual')}` · scope `{run.get('scope', '')}` · "
        f"mode `{run.get('mode', '')}`",
        f"- Guardians: {', '.join(f'`{name}`' for name in (run.get('guardians') or [])) or 'none'}",
        f"- Files: **{len(files)}** · checks: **{len(checks)}** · findings: **{len(findings)}**",
        f"- AI calls: {run.get('ai_calls', 0)} · cost: {run.get('ai_cost', 0)}",
    ]
    if run.get("diff_truncated"):
        lines.append("- ⚠️ The reviewed diff was truncated to the prompt budget.")
    if errors:
        lines.append("- Errors: " + " · ".join(str(error) for error in errors[:5]))

    lines += ["", "## Findings", ""]
    if not findings:
        lines.append("_No findings._")
    else:
        lines += ["| Severity | Guardian | File | Problem | Fix |", "| --- | --- | --- | --- | --- |"]
        for finding in findings:
            where = finding.get("path") or "-"
            if finding.get("path") and finding.get("line"):
                where = f"{finding.get('path')}:{finding.get('line')}"
            title_text = str(finding.get("title") or "").replace("|", "\\|")
            remediation = str(finding.get("remediation") or "").replace("|", "\\|")
            lines.append(
                f"| `{finding.get('severity', '')}` | `{finding.get('guardian', '')}` | "
                f"`{where}` | {title_text} | {remediation} |"
            )

    lines += ["", "## Checks", ""]
    if not checks:
        lines.append("_No deterministic checks ran._")
    else:
        lines += ["| Check | Guardian | Status | Files | Message |", "| --- | --- | --- | --- | --- |"]
        for check in checks:
            message = str(check.get("message") or "").replace("|", "\\|").replace("\n", " ")[:240]
            lines.append(
                f"| `{check.get('check_id', '')}` | `{check.get('guardian', '')}` | "
                f"`{check.get('status', '')}` | {len(check.get('files') or [])} | {message} |"
            )

    lines += ["", "## Files", ""]
    if files:
        lines += [f"- `{item.get('path')}` ({item.get('status') or 'changed'})" for item in files[:200]]
        if len(files) > 200:
            lines.append(f"- … {len(files) - 200} more")
    else:
        lines.append("_No files matched a guardian._")

    lines += ["", f"_Generated {run.get('created_at', '')} · run `{run.get('id', '')}`._"]
    return "\n".join(lines)


# Made with ❤️ by codx-junior
