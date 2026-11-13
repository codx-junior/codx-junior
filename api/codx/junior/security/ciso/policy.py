"""CISO policy engine: turn findings into one deterministic decision.

Detectors produce signals; this module decides what happens with them. Keeping
the decision here means every integration point (file write, commit, command,
model output, knowledge ingestion) applies the *same* rule set, and a policy
change is a single edit.

Decision ladder
    ALLOW             nothing reached ``warn_above``
    WARN              findings above ``warn_above`` only, the operation proceeds
    REQUIRE_APPROVAL  findings above ``approval_above``: a human must confirm
    BLOCK             a ``block_severities`` finding, a blocked category above
                      ``block_above``, or any HIGH inside a ``strict_domain``
    REDACT            secrets were found and ``redact_secrets`` is on: the
                      payload may proceed only after the redacted text is used

The engine is *fail-safe on the payload, fail-open on the platform*: a detector
crash never breaks a chat (the operation proceeds and the error is logged),
unless ``policy.fail_closed`` is set.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityAction,
    SecurityCategory,
    SecurityDomain,
    SecurityFinding,
    SecuritySeverity,
    SecurityVerdict,
)

logger = logging.getLogger(__name__)

# Numeric rank of each severity, used for threshold comparisons.
SEVERITY_RANK: Dict[SecuritySeverity, int] = {
    SecuritySeverity.INFO: 0,
    SecuritySeverity.LOW: 1,
    SecuritySeverity.MEDIUM: 2,
    SecuritySeverity.HIGH: 3,
    SecuritySeverity.CRITICAL: 4,
}

# Weight of each severity in the risk score of a verdict.
SEVERITY_WEIGHTS: Dict[SecuritySeverity, float] = {
    SecuritySeverity.INFO: 0.0,
    SecuritySeverity.LOW: 1.0,
    SecuritySeverity.MEDIUM: 3.0,
    SecuritySeverity.HIGH: 7.0,
    SecuritySeverity.CRITICAL: 25.0,
}

# Environment variable to disable the whole subsystem in an emergency.
POLICY_DISABLE_ENV = "CODX_JUNIOR_CISO_DISABLED"

# Settings key (project or global settings JSON) holding the policy overrides.
POLICY_SETTINGS_KEY = "ciso_policy"


def severity_rank(severity: Optional[SecuritySeverity]) -> int:
    """Return the numeric rank of *severity* (``-1`` when unknown)."""
    if severity is None:
        return -1
    return SEVERITY_RANK.get(severity, -1)


def highest_severity(findings: Iterable[SecurityFinding]) -> Optional[SecuritySeverity]:
    """Return the highest severity among *findings*, or ``None``."""
    best: Optional[SecuritySeverity] = None
    for finding in findings:
        if best is None or severity_rank(finding.severity) > severity_rank(best):
            best = finding.severity
    return best


def risk_score(findings: Iterable[SecurityFinding]) -> float:
    """Return the aggregated risk score of *findings*."""
    return round(sum(SEVERITY_WEIGHTS.get(f.severity, 0.0) for f in findings), 2)


def _coerce_enum_values(values: Any, enum_cls) -> List[Any]:
    """Coerce a list of strings/enums into *enum_cls*, ignoring invalid items."""
    coerced = []
    for value in values or []:
        try:
            coerced.append(value if isinstance(value, enum_cls) else enum_cls(value))
        except (ValueError, TypeError):
            logger.warning("CISO policy: ignoring unknown %s value %r", enum_cls.__name__, value)
    return coerced


def policy_from_dict(data: Optional[Dict[str, Any]]) -> CisoPolicy:
    """Build a :class:`CisoPolicy` from a partial settings dictionary.

    Unknown keys are ignored so a settings file written for a newer version
    never breaks the platform.

    Args:
        data: Partial policy (e.g. ``{"block_above": "medium"}``).

    Returns:
        CisoPolicy: Policy with the defaults merged with *data*.
    """
    policy = CisoPolicy()
    if not isinstance(data, dict):
        return policy

    simple_fields = {
        "enabled", "redact_secrets", "fail_closed", "entropy_threshold",
        "min_secret_length", "max_scan_bytes", "max_excerpt_chars",
    }
    for field in simple_fields:
        if field in data and data[field] is not None:
            setattr(policy, field, data[field])

    for field in ("ignore_paths", "ignore_rules", "allow_secret_files"):
        if isinstance(data.get(field), list):
            setattr(policy, field, [str(item) for item in data[field]])

    for field, enum_cls in (
        ("block_severities", SecuritySeverity),
        ("block_categories", SecurityCategory),
        ("strict_domains", SecurityDomain),
        ("enabled_domains", SecurityDomain),
    ):
        if isinstance(data.get(field), list):
            setattr(policy, field, _coerce_enum_values(data[field], enum_cls))

    for field, enum_cls in (
        ("block_above", SecuritySeverity),
        ("approval_above", SecuritySeverity),
        ("warn_above", SecuritySeverity),
    ):
        if data.get(field):
            coerced = _coerce_enum_values([data[field]], enum_cls)
            if coerced:
                setattr(policy, field, coerced[0])

    return policy


def resolve_policy(settings: Any = None) -> CisoPolicy:
    """Return the effective policy for *settings*.

    Resolution order: environment kill-switch, project settings, global
    settings, defaults. Any error degrades to the default policy.

    Args:
        settings: Optional ``CODXJuniorSettings`` (or any object exposing
            ``get_project_config``/``settings``).

    Returns:
        CisoPolicy: Effective policy.
    """
    if (os.environ.get(POLICY_DISABLE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}:
        policy = CisoPolicy()
        policy.enabled = False
        return policy

    data: Optional[Dict[str, Any]] = None
    if settings is not None:
        try:
            project_config = getattr(settings, "settings", None)
            if isinstance(project_config, dict):
                candidate = project_config.get(POLICY_SETTINGS_KEY)
                if isinstance(candidate, dict):
                    data = candidate
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("CISO policy: could not read project settings: %s", ex)

    return policy_from_dict(data)


def evaluate(
    findings: List[SecurityFinding],
    domain: SecurityDomain = SecurityDomain.FILE_WRITE,
    policy: Optional[CisoPolicy] = None,
    redacted_text: Optional[str] = None,
    rules_evaluated: int = 0,
    truncated: bool = False,
) -> SecurityVerdict:
    """Apply the policy to *findings* and return the verdict.

    Args:
        findings: Signals produced by the detectors.
        domain: Integration point the check ran in.
        policy: Effective policy (defaults when omitted).
        redacted_text: Sanitised payload when secrets were neutralised.
        rules_evaluated: Number of rules that ran (reported for transparency).
        truncated: True when the scanned payload was cut to the size cap.

    Returns:
        SecurityVerdict: The deterministic decision.
    """
    policy = policy or CisoPolicy()
    severity = highest_severity(findings)
    score = risk_score(findings)

    verdict = SecurityVerdict(
        domain=domain,
        findings=findings,
        risk_score=score,
        highest_severity=severity,
        rules_evaluated=rules_evaluated,
        truncated=truncated,
        redacted_text=redacted_text,
    )

    if not findings:
        verdict.action = SecurityAction.ALLOW
        verdict.allowed = True
        verdict.summary = "No security findings."
        return verdict

    ranking = severity_rank(severity)
    strict = domain in policy.strict_domains
    has_secret = any(f.category == SecurityCategory.SECRET for f in findings)
    # A finding that blocks in ANY domain: the severity the policy declares as
    # blocking, for a category that is not a secret (secrets have their own
    # treatment, because they can be neutralised instead of refused).
    hard_non_secret = any(
        f.severity in policy.block_severities
        and f.category != SecurityCategory.SECRET
        for f in findings
    )
    # 1. Strict domains (commit, file write, command, container, deploy) block
    #    every finding at or above `block_above`: nothing is persisted or
    #    executed there, so there is no "approve and continue" shortcut.
    if strict and ranking >= severity_rank(policy.block_above):
        verdict.action = SecurityAction.BLOCK
        verdict.blocked = True
        verdict.allowed = False

    # 2. A neutralised secret may proceed with the redacted payload. Blocking a
    #    model answer, a document or a log line because it quoted a key that has
    #    already been replaced costs the user the content and protects nothing.
    elif has_secret and policy.redact_secrets and redacted_text is not None:
        if hard_non_secret:
            verdict.action = SecurityAction.BLOCK
            verdict.blocked = True
            verdict.allowed = False
        else:
            verdict.action = SecurityAction.REDACT
            verdict.allowed = True
            verdict.requires_approval = True

    # 3. Destructive or exfiltrating content, and a CRITICAL secret we could not
    #    neutralise (no redacted payload), block everywhere.
    elif hard_non_secret or (
        has_secret and ranking >= severity_rank(SecuritySeverity.CRITICAL)
    ):
        verdict.action = SecurityAction.BLOCK
        verdict.blocked = True
        verdict.allowed = False

    # 4. Ask a human.
    elif ranking >= severity_rank(policy.approval_above):
        verdict.action = SecurityAction.REQUIRE_APPROVAL
        verdict.allowed = True
        verdict.requires_approval = True

    # 5. Warn.
    elif ranking >= severity_rank(policy.warn_above):
        verdict.action = SecurityAction.WARN
        verdict.allowed = True

    else:
        verdict.action = SecurityAction.ALLOW
        verdict.allowed = True

    verdict.summary = summarize(verdict)
    return verdict


def summarize(verdict: SecurityVerdict) -> str:
    """Return a one line, human readable description of *verdict*.

    Args:
        verdict: Verdict to describe.

    Returns:
        str: Summary line, e.g. ``block | critical | 2 finding(s) | secret``.
    """
    if not verdict.findings:
        return "clean"
    categories = sorted({f.category.value for f in verdict.findings})
    severity = verdict.highest_severity.value if verdict.highest_severity else "unknown"
    return (
        f"{verdict.action.value} | {severity} | "
        f"{len(verdict.findings)} finding(s) | {', '.join(categories)}"
    )


def render_report(
    verdict: SecurityVerdict,
    title: str = "CISO security report",
    max_findings: int = 50,
    min_severity: Optional[SecuritySeverity] = None,
) -> str:
    """Render *verdict* as a Markdown report.

    Args:
        verdict: Verdict to render.
        title: Report heading.
        max_findings: Maximum findings listed.
        min_severity: Severity floor; findings below it are not listed. The
            header keeps the totals of the unfiltered scan plus the number of
            findings displayed, so a filtered report can never look cleaner
            than the scan that produced it.

    Returns:
        str: Markdown report with a redacted evidence column only.
    """
    total_findings = len(verdict.findings)
    if min_severity is not None:
        verdict = verdict.filtered(min_severity)
    icon = {
        SecurityAction.ALLOW: "✅",
        SecurityAction.WARN: "⚠️",
        SecurityAction.REDACT: "🟡",
        SecurityAction.REQUIRE_APPROVAL: "🟠",
        SecurityAction.BLOCK: "⛔",
    }.get(verdict.action, "•")

    lines = [
        f"# {title}",
        "",
        f"- Domain: `{verdict.domain.value}`",
        f"- Decision: {icon} **{verdict.action.value}**",
        f"- Highest severity: **{(verdict.highest_severity or SecuritySeverity.INFO).value}**",
        f"- Risk score: {verdict.risk_score}",
        f"- Findings: {len(verdict.findings)} ({verdict.findings_by_severity() or 'none'})",
        f"- Rules evaluated: {verdict.rules_evaluated}",
    ]
    if min_severity is not None:
        lines.insert(
            5,
            f"- Severity floor: **{min_severity.value}** "
            f"({len(verdict.findings)} of {total_findings} finding(s) shown)",
        )
    if verdict.truncated:
        lines.append("- ⚠️ The payload was truncated: only the beginning was scanned")
    lines.append("")

    if not verdict.findings:
        lines += ["No findings: the payload matches no CISO rule.", ""]
        return "\n".join(lines)

    lines += [
        "| Severity | Rule | Location | Evidence (redacted) | Remediation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in verdict.findings[:max_findings]:
        location = finding.path or "-"
        if finding.line:
            location = f"{location}:{finding.line}"
        evidence = (finding.evidence or finding.excerpt or "").replace("|", "\\|")
        remediation = (finding.remediation or "").replace("|", "\\|")
        lines.append(
            f"| {finding.severity.value} | `{finding.rule_id}` | `{location}` | "
            f"{evidence} | {remediation} |"
        )
    if len(verdict.findings) > max_findings:
        lines.append(f"\n_… {len(verdict.findings) - max_findings} more finding(s) omitted._")

    lines += [
        "",
        "> Evidence is redacted: a detected secret is never reproduced in a report.",
        "",
    ]
    return "\n".join(lines)
