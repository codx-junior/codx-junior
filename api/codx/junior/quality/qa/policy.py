"""QA policy: the deterministic ladder that turns findings into a decision.

``ALLOW → WARN → REQUIRE_APPROVAL → BLOCK``, exactly like the CISO ladder, so a
process only has to read ``verdict.action`` instead of re-implementing
thresholds.

The policy is stored in the project settings under ``qa_policy`` and every field
is optional: a project that configures nothing gets the defaults of
:class:`~codx.junior.quality.qa.models.QaPolicy`.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from codx.junior.quality.qa.models import (
    QaAction,
    QaDomain,
    QaFinding,
    QaPolicy,
    QaSeverity,
    QaVerdict,
)

logger = logging.getLogger(__name__)

#: Key of the project settings that holds the QA policy overrides.
POLICY_SETTINGS_KEY = "qa_policy"

#: Emergency stop, same convention as the CISO subsystem.
DISABLED_ENV = "CODX_JUNIOR_QA_DISABLED"

#: Severity ordering used by the ladder.
SEVERITY_RANK: Dict[QaSeverity, int] = {
    QaSeverity.INFO: 0,
    QaSeverity.LOW: 1,
    QaSeverity.MEDIUM: 2,
    QaSeverity.HIGH: 3,
    QaSeverity.CRITICAL: 4,
}

#: Fields of a stored policy dict that are understood.
POLICY_FIELDS = (
    "enabled",
    "min_coverage",
    "block_above",
    "approval_above",
    "warn_above",
    "require_tests_for_changes",
    "block_on_failures",
    "block_missing_tests",
    "fail_closed",
    "max_test_seconds",
    "flaky_repeats",
    "ignore_paths",
    "suites",
)


# -----------------------------------------------------------------------------
# Coercion helpers
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


def _as_float(value: Any, default: float) -> float:
    """Coerce *value* to float, keeping *default* when it is not a number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # NaN guard


def _as_int(value: Any, default: int) -> int:
    """Coerce *value* to int, keeping *default* when it is not a number."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_severity(value: Any, default: QaSeverity) -> QaSeverity:
    """Coerce *value* to a severity, keeping *default* when it is unknown."""
    try:
        return QaSeverity(str(value).strip().lower())
    except ValueError:
        return default


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


# -----------------------------------------------------------------------------
# Policy resolution
# -----------------------------------------------------------------------------
def policy_from_dict(data: Optional[Dict[str, Any]]) -> QaPolicy:
    """Build a :class:`QaPolicy` from a partial dict.

    Args:
        data: Partial policy (unknown keys are ignored, bad values fall back to
            the default).

    Returns:
        QaPolicy: The validated policy.

    Example:
        >>> policy_from_dict({"min_coverage": 80, "enabled": "true"}).min_coverage
        80.0
    """
    defaults = QaPolicy()
    if not isinstance(data, dict):
        return defaults

    suites = data.get("suites")
    return QaPolicy(
        enabled=_as_bool(data.get("enabled"), defaults.enabled),
        min_coverage=_as_float(data.get("min_coverage"), defaults.min_coverage),
        block_above=_as_severity(data.get("block_above"), defaults.block_above),
        approval_above=_as_severity(data.get("approval_above"), defaults.approval_above),
        warn_above=_as_severity(data.get("warn_above"), defaults.warn_above),
        require_tests_for_changes=_as_bool(
            data.get("require_tests_for_changes"), defaults.require_tests_for_changes
        ),
        block_on_failures=_as_bool(data.get("block_on_failures"), defaults.block_on_failures),
        block_missing_tests=_as_bool(data.get("block_missing_tests"), defaults.block_missing_tests),
        fail_closed=_as_bool(data.get("fail_closed"), defaults.fail_closed),
        max_test_seconds=_as_int(data.get("max_test_seconds"), defaults.max_test_seconds),
        flaky_repeats=_as_int(data.get("flaky_repeats"), defaults.flaky_repeats),
        ignore_paths=_as_list(data.get("ignore_paths")),
        suites=dict(suites) if isinstance(suites, dict) else {},
    )


def resolve_policy(settings: Any = None) -> QaPolicy:
    """Return the effective QA policy of *settings*.

    Args:
        settings: Project settings (``None`` returns the defaults).

    Returns:
        QaPolicy: Stored overrides merged over the defaults; disabled when
        ``CODX_JUNIOR_QA_DISABLED=1``.
    """
    stored: Optional[Dict[str, Any]] = None
    configured = getattr(settings, "settings", None) if settings is not None else None
    if isinstance(configured, dict) and isinstance(configured.get(POLICY_SETTINGS_KEY), dict):
        stored = configured[POLICY_SETTINGS_KEY]

    policy = policy_from_dict(stored)
    if os.environ.get(DISABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        policy.enabled = False
    return policy


def stored_policy(settings: Any = None) -> Dict[str, Any]:
    """Return the raw policy overrides stored in the project settings."""
    configured = getattr(settings, "settings", None) if settings is not None else None
    if isinstance(configured, dict) and isinstance(configured.get(POLICY_SETTINGS_KEY), dict):
        return dict(configured[POLICY_SETTINGS_KEY])
    return {}


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------
def highest_severity(findings: Iterable[QaFinding]) -> Optional[QaSeverity]:
    """Return the highest severity of *findings* (``None`` when empty)."""
    highest: Optional[QaSeverity] = None
    for finding in findings:
        if highest is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[highest]:
            highest = finding.severity
    return highest


def risk_score(findings: Iterable[QaFinding]) -> int:
    """Return a 0-100 score used only to order findings in a report."""
    weights = {
        QaSeverity.INFO: 1,
        QaSeverity.LOW: 5,
        QaSeverity.MEDIUM: 12,
        QaSeverity.HIGH: 25,
        QaSeverity.CRITICAL: 40,
    }
    return min(100, sum(weights.get(finding.severity, 0) for finding in findings))


def action_for(findings: Iterable[QaFinding], policy: QaPolicy) -> QaAction:
    """Return the action the ladder prescribes for *findings*.

    Args:
        findings: Findings to evaluate.
        policy: Effective policy.

    Returns:
        QaAction: The decision.
    """
    findings = list(findings)
    if not findings:
        return QaAction.ALLOW
    rank = SEVERITY_RANK[highest_severity(findings)]
    if rank >= SEVERITY_RANK[policy.block_above]:
        return QaAction.BLOCK
    if rank >= SEVERITY_RANK[policy.approval_above]:
        return QaAction.REQUIRE_APPROVAL
    if rank >= SEVERITY_RANK[policy.warn_above]:
        return QaAction.WARN
    return QaAction.ALLOW


def evaluate(
    domain: QaDomain,
    findings: List[QaFinding],
    policy: Optional[QaPolicy] = None,
    runs: Optional[List[Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> QaVerdict:
    """Build a :class:`QaVerdict` from a list of findings.

    Args:
        domain: Process that produced the findings.
        findings: Findings to evaluate.
        policy: Effective policy (defaults when omitted).
        runs: Test runs backing the findings.
        metrics: Extra numbers to attach to the report.

    Returns:
        QaVerdict: Verdict carrying the decision and the summary line.

    Example:
        >>> verdict = evaluate(QaDomain.TEST_RUN, [])
        >>> verdict.action.value
        'allow'
    """
    policy = policy or QaPolicy()
    verdict = QaVerdict(
        domain=domain,
        findings=list(findings),
        runs=list(runs or []),
        metrics=dict(metrics or {}),
    )
    verdict.highest_severity = highest_severity(verdict.findings)
    verdict.action = action_for(verdict.findings, policy)
    verdict.blocked = verdict.action == QaAction.BLOCK
    verdict.allowed = not verdict.blocked
    verdict.requires_approval = verdict.action == QaAction.REQUIRE_APPROVAL
    verdict.passed = verdict.action in (QaAction.ALLOW, QaAction.WARN)
    verdict.summary = summarize(verdict)
    return verdict


def summarize(verdict: QaVerdict) -> str:
    """Return the one line summary stored in the verdict and the audit."""
    rules = ", ".join(verdict.rule_ids[:6]) or "clean"
    return (
        f"{verdict.action.value}: {len(verdict.findings)} finding(s) "
        f"[{rules}] highest={verdict.highest_severity.value if verdict.highest_severity else 'none'}"
    )


# -----------------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------------
def render_report(verdict: QaVerdict, title: str = "QA report") -> str:
    """Render a verdict as the Markdown report shown to the user.

    Args:
        verdict: Verdict to render.
        title: Report title.

    Returns:
        str: Markdown report.
    """
    icon = {
        QaAction.ALLOW: "✅",
        QaAction.WARN: "⚠️",
        QaAction.REQUIRE_APPROVAL: "🟠",
        QaAction.BLOCK: "⛔",
    }.get(verdict.action, "•")

    lines: List[str] = [
        f"# {title}",
        "",
        f"{icon} **Decision: `{verdict.action.value}`** · domain `{verdict.domain.value}` · "
        f"highest severity `{verdict.highest_severity.value if verdict.highest_severity else 'none'}` · "
        f"findings: **{len(verdict.findings)}**",
        "",
    ]

    if verdict.findings:
        lines += [
            "| Rule | Severity | Where | What |",
            "| --- | --- | --- | --- |",
        ]
        for finding in verdict.findings[:20]:
            where = finding.path or "-"
            if finding.path and finding.line:
                where = f"`{finding.path}:{finding.line}`"
            elif finding.path:
                where = f"`{finding.path}`"
            lines.append(
                f"| `{finding.rule_id}` | {finding.severity.value} | {where} | "
                f"{finding.title} |"
            )
        if len(verdict.findings) > 20:
            lines.append(f"| … | | | {len(verdict.findings) - 20} more finding(s) |")
        lines.append("")

        details = [finding for finding in verdict.findings if finding.remediation or finding.evidence]
        if details:
            lines.append("## Evidence and remediation")
            for finding in details[:20]:
                lines.append(f"- **`{finding.rule_id}`** — {finding.title}")
                if finding.evidence:
                    lines.append(f"  - Evidence: {finding.evidence}")
                if finding.remediation:
                    lines.append(f"  - Fix: {finding.remediation}")
            lines.append("")

    if verdict.runs:
        lines.append("## Test runs")
        for run in verdict.runs:
            lines.append(f"- `{run.suite}` — {run.summary}")
            if run.command:
                lines.append(f"  - Command: `{' '.join(run.command)}`")
            for failure in run.failures[:10]:
                message = f" — {failure.message}" if failure.message else ""
                lines.append(f"  - {failure.status}: `{failure.id}`{message}")
        lines.append("")

    if verdict.metrics:
        lines.append("## Metrics")
        for key, value in verdict.metrics.items():
            lines.append(f"- {key}: **{value}**")
        lines.append("")

    if verdict.action == QaAction.ALLOW:
        lines.append("Clean: nothing to fix in the inspected scope.")
    elif verdict.action == QaAction.WARN:
        lines.append("Accepted with warnings: record them in the task and keep going.")
    elif verdict.action == QaAction.REQUIRE_APPROVAL:
        lines.append("Needs a human decision: the QA gate cannot pass this on its own.")
    else:
        lines.append("Blocked: fix the findings above (or raise the policy) before shipping.")

    return "\n".join(lines)


def policy_markdown(policy: QaPolicy, stored: Dict[str, Any]) -> str:
    """Render the effective policy as Markdown."""

    def _values(items: Iterable[QaSeverity]) -> str:
        return ", ".join(f"`{item.value}`" for item in items) or "-"

    suites = ", ".join(f"`{name}`" for name in policy.suites) or "-"
    return "\n".join(
        [
            "# QA policy",
            "",
            f"- Enabled: **{policy.enabled}**",
            f"- Block above: `{policy.block_above.value}`",
            f"- Approval above: `{policy.approval_above.value}`",
            f"- Warn above: `{policy.warn_above.value}`",
            f"- Min coverage: **{policy.min_coverage}%**",
            f"- Require tests for changes: **{policy.require_tests_for_changes}**",
            f"- Block on test failures: **{policy.block_on_failures}**",
            f"- Block missing tests: **{policy.block_missing_tests}**",
            f"- Fail closed: **{policy.fail_closed}**",
            f"- Max test seconds: `{policy.max_test_seconds}`",
            f"- Flaky repeats: `{policy.flaky_repeats}`",
            "",
            "## Severities used by the ladder",
            _values([QaSeverity.INFO, QaSeverity.LOW, QaSeverity.MEDIUM, QaSeverity.HIGH, QaSeverity.CRITICAL]),
            "",
            "## Ignored paths",
            "```",
            "\n".join(policy.ignore_paths) or "-",
            "```",
            "",
            "## Custom suites",
            "```",
            suites,
            "```",
            "",
            f"_Stored overrides: {stored or 'none (defaults)'}_",
            "",
            f"> Set `{DISABLED_ENV}=1` in the environment for an emergency stop.",
        ]
    )
