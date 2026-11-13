"""CISO — Chief Information Security Officer subsystem.

A deterministic security layer for everything codx-junior reads, writes,
executes and returns. It exists because two things went wrong in practice:
credentials ended up inside the repository and generated files, and generated
content can carry malicious code or instructions aimed at the agent itself.

Public API
    Models
        :class:`CisoPolicy`, :class:`SecurityFinding`, :class:`SecurityVerdict`,
        :class:`SecuritySeverity`, :class:`SecurityCategory`,
        :class:`SecurityDomain`, :class:`SecurityAction`.
    Detectors
        :mod:`codx.junior.security.ciso.secrets` — credentials and tokens.
        :mod:`codx.junior.security.ciso.malicious` — malicious, obfuscated and
        destructive code.
        :mod:`codx.junior.security.ciso.injection` — stored prompt injection.
    Scanners
        :func:`scan_content`, :func:`scan_file`, :func:`scan_diff`,
        :func:`scan_project`.
    Policy
        :func:`resolve_policy`, :func:`evaluate`, :func:`render_report`.
    Gates (the call sites use these)
        :func:`guard_file_write`, :func:`guard_diff`, :func:`guard_command`,
        :func:`guard_container`, :func:`guard_output`, :func:`guard_knowledge`,
        :func:`guard_profile`, :func:`sanitize_for_log`, :func:`redact_output`.
    Payload
        :func:`redact_payload` — recursion for the structures we persist (task
        cards) and send (prompt messages).

The map of every process that calls a gate, and the state of each call site,
is in ``codx/junior/security/ciso/README.md``.

Made with ❤️ by codx-junior
"""

from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityAction,
    SecurityCategory,
    SecurityDomain,
    SecurityFinding,
    SecuritySeverity,
    SecurityVerdict,
)
from codx.junior.security.ciso.policy import (
    SEVERITY_RANK,
    SEVERITY_WEIGHTS,
    evaluate,
    highest_severity,
    policy_from_dict,
    render_report,
    resolve_policy,
    risk_score,
    severity_rank,
    summarize,
)
from codx.junior.security.ciso.scanner import (
    RULE_COUNT,
    collect_findings,
    findings_table,
    iter_project_files,
    read_text_file,
    scan_content,
    scan_diff,
    scan_file,
    scan_project,
)
from codx.junior.security.ciso.gate import (
    AUDIT_EVENT,
    guard_command,
    guard_container,
    guard_diff,
    guard_file_write,
    guard_knowledge,
    guard_output,
    guard_profile,
    is_enabled,
    notice,
    redact_output,
    sanitize_for_log,
)
from codx.junior.security.ciso.payload import redact_payload

__all__ = [
    # models
    "CisoPolicy",
    "SecurityAction",
    "SecurityCategory",
    "SecurityDomain",
    "SecurityFinding",
    "SecuritySeverity",
    "SecurityVerdict",
    # policy
    "SEVERITY_RANK",
    "SEVERITY_WEIGHTS",
    "evaluate",
    "highest_severity",
    "policy_from_dict",
    "render_report",
    "resolve_policy",
    "risk_score",
    "severity_rank",
    "summarize",
    # scanners
    "RULE_COUNT",
    "collect_findings",
    "findings_table",
    "iter_project_files",
    "read_text_file",
    "scan_content",
    "scan_diff",
    "scan_file",
    "scan_project",
    # gates
    "AUDIT_EVENT",
    "guard_command",
    "guard_container",
    "guard_diff",
    "guard_file_write",
    "guard_knowledge",
    "guard_output",
    "guard_profile",
    "is_enabled",
    "notice",
    "redact_output",
    "sanitize_for_log",
    # payload sanitisers
    "redact_payload",
]
