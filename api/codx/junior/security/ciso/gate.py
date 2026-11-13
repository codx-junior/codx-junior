"""CISO gates: the single call a process makes before crossing a boundary.

Every integration point of the platform uses one of these functions instead of
talking to the detectors directly. That is what keeps the security behaviour
consistent: change the gate, every process changes.

| Gate                    | Process it protects                          |
| ----------------------- | -------------------------------------------- |
| ``guard_file_write``    | writing / editing a project file             |
| ``guard_diff``          | committing or merging a change               |
| ``guard_command``       | running a shell command or a container exec  |
| ``guard_container``     | building / running a container               |
| ``guard_output``        | returning model output or tool output to a user |
| ``guard_knowledge``     | ingesting external content into the RAG index |
| ``sanitize_for_log``    | writing a log line that may carry a secret   |

Failure model
    A gate never raises. When the scanner itself fails the operation is allowed
    and a warning is logged (``policy.fail_closed`` reverses this for
    deployments that prefer availability of security over availability of
    service). Auditing is best effort: a broken audit file must never block a
    legitimate operation.

Made with ❤️ by codx-junior
"""

import logging
from typing import Any, Optional

from codx.junior.security.ciso import malicious
from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso import scanner, secrets
from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityAction,
    SecurityDomain,
    SecurityVerdict,
)

logger = logging.getLogger(__name__)

#: Audit event name used for every CISO decision.
AUDIT_EVENT = "ciso_verdict"

#: Maximum number of finding summaries recorded per audit entry.
AUDIT_MAX_FINDINGS = 20


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def is_enabled(settings: Any = None) -> bool:
    """Return True when the CISO subsystem is active for *settings*.

    Args:
        settings: Optional project settings.

    Returns:
        bool: ``policy.enabled`` of the effective policy.
    """
    try:
        return ciso_policy.resolve_policy(settings).enabled
    except Exception:  # pragma: no cover - defensive
        return True


def _audit(
    settings: Any,
    verdict: SecurityVerdict,
    subject: str,
    extra: Optional[dict] = None,
) -> None:
    """Append the verdict to the project audit journal (best effort).

    Args:
        settings: Project settings (used to locate ``.codx``).
        verdict: Decision to record.
        subject: What was inspected (path, command, domain).
        extra: Extra fields to attach.
    """
    codx_path = getattr(settings, "codx_path", None)
    if not codx_path:
        return
    try:
        from codx.junior.harness.audit import record_audit

        record_audit(
            codx_path,
            AUDIT_EVENT,
            domain=verdict.domain.value,
            action=verdict.action.value,
            risk_score=verdict.risk_score,
            highest_severity=(verdict.highest_severity.value if verdict.highest_severity else None),
            subject=subject,
            blocked=verdict.blocked,
            requires_approval=verdict.requires_approval,
            rules=verdict.rules_evaluated,
            findings=[
                {
                    "rule": finding.rule_id,
                    "severity": finding.severity.value,
                    "category": finding.category.value,
                    "path": finding.path,
                    "line": finding.line,
                }
                for finding in verdict.findings[:AUDIT_MAX_FINDINGS]
            ],
            **(extra or {}),
        )
    except Exception as ex:  # pragma: no cover - audit must never break work
        logger.debug("CISO: could not record the audit entry: %s", ex)


def _fail(policy: CisoPolicy, domain: SecurityDomain, error: Exception) -> SecurityVerdict:
    """Build the verdict used when a scanner raised."""
    logger.warning("CISO gate (%s) failed: %s", domain.value, error)
    verdict = SecurityVerdict(domain=domain)
    if policy.fail_closed:
        verdict.action = SecurityAction.BLOCK
        verdict.blocked = True
        verdict.allowed = False
        verdict.summary = f"blocked: the security scanner failed ({type(error).__name__})"
    else:
        verdict.action = SecurityAction.WARN
        verdict.allowed = True
        verdict.summary = f"scanner error, operation allowed (fail-open): {error}"
    return verdict


def notice(verdict: SecurityVerdict, subject: str = "") -> str:
    """Return a one line, user facing notice describing *verdict*.

    Args:
        verdict: Decision to describe.
        subject: What was inspected (shown when the verdict is not clean).

    Returns:
        str: ``""`` when clean, otherwise a Markdown line with the decision.
    """
    if verdict.action == SecurityAction.ALLOW:
        return ""
    icon = {
        SecurityAction.WARN: "⚠️",
        SecurityAction.REDACT: "🟡",
        SecurityAction.REQUIRE_APPROVAL: "🟠",
        SecurityAction.BLOCK: "⛔",
    }.get(verdict.action, "•")
    target = f" `{subject}`" if subject else ""
    rules = ", ".join(sorted({f.rule_id for f in verdict.findings})[:5])
    return (
        f"{icon} **CISO {verdict.action.value}**{target}: {verdict.summary}"
        + (f" — {rules}" if rules else "")
    )


# -----------------------------------------------------------------------------
# Gates
# -----------------------------------------------------------------------------
def guard_file_write(
    file_path: str,
    content: str,
    settings: Any = None,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect a payload before it is written to *file_path*.

    Args:
        file_path: Target path (relative to the project).
        content: Exact content that would be written.
        settings: Project settings (for the policy and the audit journal).
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the write must not happen; when
        ``redacted_text`` is set the caller may write that instead.

    Example:
        >>> verdict = guard_file_write(".env", 'DB_PASSWORD="hunter2-hunter2-hunter2"')
        >>> verdict.blocked
        True
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        verdict = scanner.scan_content(
            content, path=file_path, domain=SecurityDomain.FILE_WRITE, policy=policy
        )
        _audit(settings, verdict, file_path)
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, SecurityDomain.FILE_WRITE, ex)


def guard_diff(
    patch: str,
    settings: Any = None,
    domain: SecurityDomain = SecurityDomain.GIT_COMMIT,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect a unified diff before it is committed, pushed or merged.

    Args:
        patch: ``git diff`` / ``git show`` output.
        settings: Project settings.
        domain: ``git_commit`` or ``pull_request``.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the change must not be committed.
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        verdict = scanner.scan_diff(patch, domain=domain, policy=policy)
        _audit(settings, verdict, "diff")
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, domain, ex)


def guard_command(
    command: str,
    settings: Any = None,
    domain: SecurityDomain = SecurityDomain.COMMAND,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect a shell command before it is executed.

    Args:
        command: Command line about to run.
        settings: Project settings.
        domain: ``command`` or ``container``.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the command must not run.
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        findings, truncated = malicious.scan_command(command, policy=policy)
        verdict = ciso_policy.evaluate(
            findings,
            domain=domain,
            policy=policy,
            rules_evaluated=scanner.RULE_COUNT,
            truncated=truncated,
        )
        _audit(settings, verdict, command[:200])
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, domain, ex)


def guard_container(
    command: str,
    settings: Any = None,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect a container build/run command.

    Args:
        command: Docker/compose command line.
        settings: Project settings.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the container operation is refused.
    """
    return guard_command(
        command, settings=settings, domain=SecurityDomain.CONTAINER, policy=policy
    )


def guard_output(
    text: str,
    settings: Any = None,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect content that is about to be returned to a user or stored.

    Args:
        text: Answer, tool output, log excerpt or exported file body.
        settings: Project settings.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: when ``redacted_text`` is set the caller MUST return
        that text instead of the original.
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        verdict = scanner.scan_content(
            text, path=None, domain=SecurityDomain.MODEL_OUTPUT, policy=policy
        )
        if verdict.findings:
            _audit(settings, verdict, "output")
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, SecurityDomain.MODEL_OUTPUT, ex)


def redact_output(text: str, settings: Any = None, policy: Optional[CisoPolicy] = None) -> str:
    """Return *text* with secrets neutralised (convenience over :func:`guard_output`).

    Most call sites only need the safe string, not the decision: they render a
    snapshot, a log excerpt or a prompt and must never emit a credential. This
    wrapper keeps them to one line while still recording the audit entry and
    honouring ``policy``.

    Args:
        text: Content about to be returned, stored or sent.
        settings: Project settings (for the policy and the audit journal).
        policy: Explicit policy override.

    Returns:
        str: The redacted text, or *text* unchanged when nothing matched.

    Example:
        >>> 'ghp_' in redact_output('remote: https://x:ghp_' + 'a' * 36 + '@github.com/o/r')
        False
    """
    verdict = guard_output(text, settings=settings, policy=policy)
    if verdict.redacted_text is None:
        return text
    return verdict.redacted_text


def guard_knowledge(
    text: str,
    source: Optional[str] = None,
    settings: Any = None,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect external content before it is indexed for retrieval.

    A poisoned chunk is retrieved into every later prompt, so ingestion is the
    most valuable place to stop a stored injection.

    Args:
        text: Document content.
        source: Document origin (URL, path, task id).
        settings: Project settings.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the chunk must not be indexed.
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        verdict = scanner.scan_content(
            text,
            path=source,
            domain=SecurityDomain.KNOWLEDGE_INGESTION,
            policy=policy,
        )
        if verdict.findings:
            _audit(settings, verdict, source or "knowledge")
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, SecurityDomain.KNOWLEDGE_INGESTION, ex)


def guard_profile(
    definition: Any,
    settings: Any = None,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Inspect a profile definition before it is saved or activated.

    A profile carries instructions and an allowlist: it is code for the agent.
    Two things are checked, the serialised definition and the instructions.

    Args:
        definition: ``Profile`` instance or its serialised form.
        settings: Project settings.
        policy: Explicit policy override.

    Returns:
        SecurityVerdict: ``blocked`` means the profile must not be saved.
    """
    policy = policy or ciso_policy.resolve_policy(settings)
    try:
        payload = definition if isinstance(definition, str) else str(
            definition.model_dump(mode="json")
            if hasattr(definition, "model_dump") else definition
        )
        verdict = scanner.scan_content(
            payload, path="<profile>", domain=SecurityDomain.PROFILE, policy=policy
        )
        _audit(settings, verdict, "profile")
        return verdict
    except Exception as ex:  # pragma: no cover - defensive
        return _fail(policy, SecurityDomain.PROFILE, ex)


def sanitize_for_log(text: str, limit: int = 2000) -> str:
    """Return *text* with secrets neutralised and the length bounded.

    Use it in every log statement that may carry user or model content.

    Args:
        text: Text about to be logged.
        limit: Maximum length of the returned line.

    Returns:
        str: Safe, bounded text.

    Example:
        >>> sanitize_for_log('DB_PASSWORD="hunter2-hunter2-hunter2"')[:10]
        'DB_PASSWOR'
    """
    try:
        redacted, _count = secrets.redact_text(text or "")
        return redacted[:limit]
    except Exception:  # pragma: no cover - defensive
        return "[CISO: log sanitisation failed]"
