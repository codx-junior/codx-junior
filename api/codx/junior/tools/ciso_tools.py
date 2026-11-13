"""CISO security tools.

These are the tools the ``ciso`` profile (and any profile granted them) uses to
audit a project, review a change before it is committed or merged, validate a
payload before writing it, check a shell command and neutralise secrets in a
text that is about to be shared.

Two of them are also used as gates by other processes: ``security_review_changes``
is the pull-request gate and ``security_check_command`` is the command gate.
Gates that run automatically inside a process live in
:mod:`codx.junior.security.ciso.gate`, not here: a tool is an explicit action,
a gate is an implicit guarantee.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, List, Optional

from .model import ToolResponse
from codx.junior.security.ciso import (
    CisoPolicy,
    SecurityDomain,
    gate,
    policy as ciso_policy,
    render_report,
    resolve_policy,
    scan_content,
    scan_diff,
    scan_project,
)
from codx.junior.security.ciso import secrets as ciso_secrets

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _settings_of(kwargs: Dict[str, Any]):
    """Return the project settings injected by the runtime (or ``None``)."""
    return kwargs.get("settings")


def _project_path(settings: Any) -> str:
    """Return the absolute project path of *settings*."""
    return getattr(settings, "abs_project_path", None) or os.getcwd()


def _verdict_answer(verdict, title: str) -> str:
    """Render a verdict as the user facing markdown report."""
    return render_report(verdict, title=title)


def _llm_summary(verdict, subject: str) -> str:
    """Build the compact machine friendly summary for the model."""
    lines = [
        f"# {subject}",
        f"- decision: {verdict.action.value}",
        f"- blocked: {verdict.blocked}",
        f"- requires_approval: {verdict.requires_approval}",
        f"- highest_severity: {(verdict.highest_severity.value if verdict.highest_severity else 'none')}",
        f"- risk_score: {verdict.risk_score}",
        f"- findings: {len(verdict.findings)}",
    ]
    for finding in verdict.findings[:10]:
        location = finding.path or "-"
        if finding.line:
            location = f"{location}:{finding.line}"
        lines.append(
            f"- [{finding.severity.value}] {finding.rule_id} @ {location}: "
            f"{finding.remediation or finding.reason}"
        )
    if len(verdict.findings) > 10:
        lines.append(f"- … {len(verdict.findings) - 10} more finding(s)")
    return "\n".join(lines)


def _git_diff(settings: Any, base: str, scope: str, paths: Optional[List[str]]) -> str:
    """Return the unified diff requested by the caller.

    Args:
        settings: Project settings.
        base: Git reference to compare against.
        scope: ``all`` | ``staged`` | ``unstaged``.
        paths: Optional path filter.

    Returns:
        str: Unified diff (empty when there is nothing to review).
    """
    from .project_tools import _run_git

    args: List[str] = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "-U3"]
    if scope == "staged":
        args.append("--cached")
    elif scope == "all":
        args.append(base or "HEAD")

    if paths:
        args += ["--", *paths]

    ok, stdout, stderr = _run_git(settings, args)
    if not ok:
        raise ValueError(f"git diff failed: {(stderr or '').strip()}")
    return stdout or ""


# -----------------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------------
def security_audit(
    path: Optional[str] = None,
    max_files: int = 200,
    include_dependency_files: bool = True,
    **kwargs
) -> ToolResponse:
    """Audit a project (or a sub path) for secrets, malicious code and injection.

    Scans the files of the project with every CISO detector and returns a
    Markdown report plus a machine friendly summary.

    Args:
        path: Optional project-relative folder or file to restrict the audit.
        max_files: Maximum number of files read (default 200).
        include_dependency_files: Also scan lock files / manifests
            (still skipped: vendored folders such as ``node_modules``).
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Report, summary and the scan logs.

    Example:
        >>> security_audit(path="api/")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    project_path = _project_path(settings)
    policy = resolve_policy(settings)

    targets = None
    if path:
        absolute = path if os.path.isabs(path) else os.path.join(project_path, path)
        if os.path.isdir(absolute):
            from codx.junior.security.ciso.scanner import iter_project_files

            targets = iter_project_files(absolute, max_files=max_files, policy=policy)
        elif os.path.isfile(absolute):
            targets = [absolute]
        else:
            return ToolResponse(
                user_response=f"⚠️ Path not found: `{path}`",
                llm_response=f"security_audit: path not found: {path}",
            )

    verdict = scan_project(
        project_path,
        max_files=max_files,
        domain=SecurityDomain.FILE_READ,
        policy=policy,
        paths=targets,
    )

    title = f"CISO audit — {getattr(settings, 'project_name', 'project')}"
    if path:
        title += f" — `{path}`"

    logs = [
        f"CISO audit: {verdict.summary}",
        f"CISO audit: decision={verdict.action.value} blocked={verdict.blocked}",
    ]
    for finding in verdict.findings[:25]:
        logs.append(
            f"[{finding.severity.value.upper()}] {finding.rule_id} "
            f"{finding.path}:{finding.line or 0} — {finding.title}"
        )

    return ToolResponse(
        user_response=_verdict_answer(verdict, title),
        llm_response=_llm_summary(verdict, "CISO audit"),
        logs=logs,
    )


def security_scan_content(
    content: str,
    path: Optional[str] = None,
    domain: str = "file_write",
    **kwargs
) -> ToolResponse:
    """Scan a payload before writing, committing or sharing it.

    Use it to validate a file body *before* calling ``project_write_file`` or
    ``apply_file_changes``: it reports whether the content would be blocked and
    returns a redacted version when it carries secrets.

    Args:
        content: Exact payload to inspect.
        path: Target path (drives the reported location).
        domain: Integration point: ``file_write`` (default), ``git_commit``,
            ``model_output``, ``knowledge_ingestion``, ``profile``.

    Returns:
        ToolResponse: Report; the summary includes the redacted payload when
        secrets were neutralised.

    Example:
        >>> security_scan_content('TOKEN="<your-token>"')  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    policy = resolve_policy(settings)

    try:
        domain_value = SecurityDomain(domain)
    except ValueError:
        return ToolResponse(
            user_response=f"⚠️ Unknown domain `{domain}`. Valid: "
                          f"{', '.join(d.value for d in SecurityDomain)}",
            llm_response=f"security_scan_content: invalid domain {domain}",
        )

    verdict = scan_content(content, path=path, domain=domain_value, policy=policy)

    llm_summary = _llm_summary(verdict, "CISO content scan")
    if verdict.redacted_text is not None:
        llm_summary += (
            "\n\n## Redacted payload (safe to write)\n```\n"
            + verdict.redacted_text
            + "\n```\nUse this content instead of the original."
        )

    return ToolResponse(
        user_response=_verdict_answer(verdict, f"CISO scan — `{path or 'inline content'}`"),
        llm_response=llm_summary,
        logs=[f"CISO content scan: {verdict.summary}"],
    )


def security_review_changes(
    base: str = "HEAD",
    scope: str = "all",
    paths: Optional[List[str]] = None,
    **kwargs
) -> ToolResponse:
    """Review a change (working tree, staged or against a ref) before shipping it.

    This is the pull-request gate: only the **added** lines are inspected and
    the reported line numbers are the new file coordinates.

    Args:
        base: Git reference to compare against (default ``HEAD``).
        scope: ``all`` (working tree + staged vs base), ``staged`` (next commit)
            or ``unstaged`` (working tree vs index).
        paths: Optional list of paths to restrict the review.

    Returns:
        ToolResponse: Report of the change.

    Example:
        >>> security_review_changes(scope="staged")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    policy = resolve_policy(settings)
    domain = SecurityDomain.GIT_COMMIT if scope == "staged" else SecurityDomain.PULL_REQUEST

    try:
        patch = _git_diff(settings, base=base, scope=scope, paths=paths)
    except Exception as ex:
        return ToolResponse(
            user_response=f"⚠️ Could not read the change: {ex}",
            llm_response=f"security_review_changes: {ex}",
        )

    if not (patch or "").strip():
        return ToolResponse(
            user_response="✅ No pending changes to review.",
            llm_response="security_review_changes: empty diff, nothing to review.",
        )

    verdict = scan_diff(patch, domain=domain, policy=policy)
    return ToolResponse(
        user_response=_verdict_answer(verdict, f"CISO review — scope `{scope}` vs `{base}`"),
        llm_response=_llm_summary(verdict, "CISO change review"),
        logs=[f"CISO change review: {verdict.summary}"],
    )


def security_check_command(command: str, **kwargs) -> ToolResponse:
    """Check a shell command before running it.

    Args:
        command: Command line about to be executed.

    Returns:
        ToolResponse: Report; ``verdict.blocked`` means it must not run.

    Example:
        >>> security_check_command("git status")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    verdict = gate.guard_command(command, settings=settings)
    return ToolResponse(
        user_response=_verdict_answer(verdict, f"CISO command check — `{command[:80]}`"),
        llm_response=_llm_summary(verdict, "CISO command check"),
        logs=[f"CISO command check: {verdict.summary}"],
    )


def security_redact(text: str, **kwargs) -> ToolResponse:
    """Neutralise every detected secret inside *text*.

    Use it before sharing a log, a configuration file or a diff outside the
    project.

    Args:
        text: Payload to sanitise.

    Returns:
        ToolResponse: user_response carries the redacted text; the summary
        reports how many values were replaced.

    Example:
        >>> security_redact('token: <your-token-here>')  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    policy = resolve_policy(settings)
    redacted, replacements = ciso_secrets.redact_text(text or "", policy)
    return ToolResponse(
        user_response=redacted if replacements else "_No secret detected._\n\n" + (text or ""),
        llm_response=(
            f"{replacements} secret(s) redacted.\n\n```\n{redacted}\n```"
            if replacements
            else "No secret detected."
        ),
        logs=[f"CISO redact: {replacements} replacement(s)"],
    )


def security_policy(action: str = "show", changes: Optional[Dict[str, Any]] = None, **kwargs) -> ToolResponse:
    """Show or adjust the CISO policy of the project.

    The policy is stored in the project settings under ``ciso_policy``. Only the
    fields listed in the report can be changed, and every change is validated
    against the policy model before it is applied.

    Args:
        action: ``show`` (default) to display the effective policy.
        changes: Partial policy to merge (``action='show'`` ignores it). Fields
            include ``enabled``, ``block_above``, ``approval_above``,
            ``warn_above``, ``redact_secrets``, ``fail_closed``,
            ``ignore_paths``, ``ignore_rules``, ``allow_secret_files``.

    Returns:
        ToolResponse: The effective policy as Markdown.

    Example:
        >>> security_policy()  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    stored: Dict[str, Any] = {}
    configured = getattr(settings, "settings", None)
    if isinstance(configured, dict) and isinstance(configured.get(ciso_policy.POLICY_SETTINGS_KEY), dict):
        stored = dict(configured[ciso_policy.POLICY_SETTINGS_KEY])

    if changes and action == "show":
        merged = dict(stored)
        merged.update(changes or {})
        validated = ciso_policy.policy_from_dict(merged)
        try:
            configured = getattr(settings, "settings", None)
            if isinstance(configured, dict):
                configured[ciso_policy.POLICY_SETTINGS_KEY] = validated.to_dict()
                save = getattr(settings, "save", None)
                if callable(save):
                    save()
            else:
                return ToolResponse(
                    user_response="⚠️ This project settings object does not expose a writable `settings` dict.",
                    llm_response="security_policy: unsupported settings object",
                )
        except Exception as ex:
            return ToolResponse(
                user_response=f"⚠️ Could not persist the policy: {ex}",
                llm_response=f"security_policy: persist failed: {ex}",
            )

    policy = resolve_policy(settings)
    markdown = _policy_markdown(policy, stored)
    return ToolResponse(
        user_response=markdown,
        llm_response=f"CISO policy: enabled={policy.enabled} "
                     f"block_above={policy.block_above.value} "
                     f"approval_above={policy.approval_above.value} "
                     f"fail_closed={policy.fail_closed}",
        logs=["CISO policy reported"],
    )


def _policy_markdown(policy: CisoPolicy, stored: Dict[str, Any]) -> str:
    """Render the effective policy as Markdown."""
    def _values(items) -> str:
        return ", ".join(f"`{item.value}`" for item in items) or "-"

    return "\n".join([
        "# CISO policy",
        "",
        f"- Enabled: **{policy.enabled}**",
        f"- Block severities: {_values(policy.block_severities)}",
        f"- Block categories: {_values(policy.block_categories)}",
        f"- Block above: `{policy.block_above.value}`",
        f"- Approval above: `{policy.approval_above.value}`",
        f"- Warn above: `{policy.warn_above.value}`",
        f"- Strict domains: {_values(policy.strict_domains)}",
        f"- Redact secrets: **{policy.redact_secrets}**",
        f"- Fail closed: **{policy.fail_closed}**",
        f"- Entropy threshold: `{policy.entropy_threshold}`",
        f"- Min secret length: `{policy.min_secret_length}`",
        "",
        "## Ignored paths",
        "```",
        "\n".join(policy.ignore_paths) or "-",
        "```",
        "## Silenced rules",
        "```",
        "\n".join(policy.ignore_rules) or "-",
        "```",
        "",
        f"_Stored overrides: {stored or 'none (defaults)'}_",
        "",
        "> Set `CODX_JUNIOR_CISO_DISABLED=1` in the environment for an emergency stop.",
    ])


# -----------------------------------------------------------------------------
# Tool JSON schemas
# -----------------------------------------------------------------------------
SECURITY_AUDIT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_audit",
        "description": (
            "CISO security audit of the project (or a sub path): scans files for "
            "hardcoded secrets and tokens, malicious/obfuscated code, destructive "
            "operations, supply-chain risks and stored prompt injection. Returns a "
            "Markdown report with redacted evidence and remediation per finding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional project-relative folder or file to restrict the audit."
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum number of files read (default 200)."
                },
                "include_dependency_files": {
                    "type": "boolean",
                    "description": "Also scan lock files and manifests (default true)."
                }
            },
            "required": []
        }
    }
}

SECURITY_SCAN_CONTENT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_scan_content",
        "description": (
            "Validate a payload BEFORE writing it (use it before project_write_file "
            "or apply_file_changes). Detects secrets, malicious code and injected "
            "instructions, and returns a redacted version of the content when it "
            "carries credentials."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Exact payload that is about to be written or shared."
                },
                "path": {
                    "type": "string",
                    "description": "Target path (drives the reported location)."
                },
                "domain": {
                    "type": "string",
                    "enum": ["file_write", "git_commit", "model_output", "knowledge_ingestion", "profile"],
                    "description": "Integration point (default file_write)."
                }
            },
            "required": ["content"]
        }
    }
}

SECURITY_REVIEW_CHANGES_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_review_changes",
        "description": (
            "CISO review of a pending change (the pull-request gate). Inspects only "
            "the ADDED lines of the diff for secrets, malicious code and injected "
            "instructions, and reports the new file line numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base": {
                    "type": "string",
                    "description": "Git reference to compare against (default HEAD)."
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "staged", "unstaged"],
                    "description": "Which change to review (default all)."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional path filter."
                }
            },
            "required": []
        }
    }
}

SECURITY_CHECK_COMMAND_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_check_command",
        "description": (
            "Check a shell command before running it: destructive operations, "
            "privilege escalation, reverse shells, credential harvesting, "
            "privileged containers, history tampering and unreviewed remote code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command line about to be executed."
                }
            },
            "required": ["command"]
        }
    }
}

SECURITY_REDACT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_redact",
        "description": (
            "Neutralise every detected secret inside a text. Use it before sharing "
            "a log, a config file or a diff outside the project."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Payload to sanitise."}
            },
            "required": ["text"]
        }
    }
}

SECURITY_POLICY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "security_policy",
        "description": (
            "Show the effective CISO policy of the project (severity thresholds, "
            "strict domains, redaction, silenced rules) and optionally merge "
            "validated changes into the project settings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["show"],
                    "description": "Only 'show' is supported today."
                },
                "changes": {
                    "type": "object",
                    "description": (
                        "Partial policy to persist: enabled, block_above, "
                        "approval_above, warn_above, redact_secrets, fail_closed, "
                        "ignore_paths, ignore_rules, allow_secret_files."
                    )
                }
            },
            "required": []
        }
    }
}
