"""Quality-guardian tools.

These are the actions a profile (or the user through a chat) takes on the
quality subsystem. The deterministic machinery and the stored runs live in
:mod:`codx.junior.quality.guardians`; the tools only choose the action, call
the service and render a dual response.

The five tools cover the whole loop:

* ``quality_guardians`` — list/create/refresh/edit the guardian profiles;
* ``quality_review`` — analyze the change, the project or one file type;
* ``quality_fix`` — fix the open findings (proposal by default, apply on request);
* ``quality_feedback`` — dismiss / suppress / learn from a finding;
* ``quality_policy`` — inspect and change the cost/trigger policy.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from codx.junior.model.profile import ProfileGuardianCheck
from codx.junior.quality.guardians import service
from codx.junior.quality.guardians.profiles import (
    adopt_profile,
    enable_guardian,
    get_guardian,
    remember,
    update_guardian,
)

from .model import ToolResponse

logger = logging.getLogger(__name__)

#: Guardian list rendering: how many checks/details to show per guardian.
MAX_RENDERED_CHECKS = 8


def _settings_of(kwargs: Dict[str, Any]):
    """Return the project settings injected by the runtime."""
    return kwargs.get("settings")


def _session_of(kwargs: Dict[str, Any]):
    """Return the active session injected by the runtime (or ``None``)."""
    return kwargs.get("session")


def _user_of(kwargs: Dict[str, Any]):
    """Return the user behind the tool call (or ``None``)."""
    session = _session_of(kwargs)
    user = getattr(session, "user", None) if session is not None else None
    if user:
        return user
    settings = _settings_of(kwargs)
    active = getattr(settings, "_active_session", None) if settings is not None else None
    return getattr(active, "user", None) if active else None


def _is_quality_admin(settings: Any, kwargs: Dict[str, Any]) -> bool:
    """Return True when the caller may edit guardian profiles/policy."""
    user = _user_of(kwargs)
    if user is None or not getattr(user, "username", ""):
        return False
    try:
        from codx.junior.security.user_management import UserSecurityManager

        return UserSecurityManager().can_manage_profile_knowledge(
            user=user, settings=settings
        )
    except Exception:
        return False


def _quality_admin_error() -> ToolResponse:
    """Return the standard permission error of a guardian edit."""
    return _error(
        "Editing a quality guardian or its policy requires a project admin, "
        "a project knowledge admin or a codx-junior admin."
    )


def _error(message: str) -> ToolResponse:
    """Return a failed tool response (user + LLM)."""
    return ToolResponse(
        user_response=f"⚠️ {message}",
        llm_response=f"quality tool failed: {message}",
        logs=[f"quality tool failed: {message}"],
    )


def _as_list(value: Any) -> List[str]:
    """Coerce a tool argument to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _guardian_table(state: Dict[str, Any]) -> str:
    """Render the guardians of a project as a Markdown table."""
    guardians = state.get("guardians") or []
    if not guardians:
        return "_No guardian profiles yet._"
    lines = [
        "| Guardian | Kind | Files | Checks | Triggers | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for guardian in guardians:
        checks = guardian.get("checks") or []
        check_labels = []
        for check in checks[:MAX_RENDERED_CHECKS]:
            if check.get("kind") == "command":
                check_labels.append(f"`{check.get('id')}` (cmd)")
            else:
                check_labels.append(f"`{check.get('id')}`")
        if len(checks) > len(check_labels):
            check_labels.append(f"… +{len(checks) - len(check_labels)}")
        status = "enabled" if guardian.get("enabled") else "disabled"
        if guardian.get("auto_generated") and not guardian.get("polished"):
            status += " · auto"
        elif guardian.get("polished"):
            status += " · polished"
        lines.append(
            f"| `{guardian.get('name')}` | `{guardian.get('kind') or '-'}` | "
            f"`{guardian.get('file_match')}` | {', '.join(check_labels) or 'none'} | "
            f"{', '.join(guardian.get('triggers') or []) or '-'} | {status} |"
        )
    return "\n".join(lines)


def _render_state(state: Dict[str, Any]) -> str:
    """Render the full quality state for a user-facing answer."""
    latest = state.get("latest") or {}
    verdict = latest.get("verdict") or {}
    policy = state.get("policy") or {}
    lines = [
        "# Project quality guardians",
        "",
        f"- Guardians: **{state.get('guardian_count', 0)}** "
        f"({sum(1 for guardian in (state.get('guardians') or []) if guardian.get('enabled'))} enabled)",
        f"- Family: {len(state.get('guardians') or [])} · candidates: **{state.get('candidate_count', 0)}**",
        f"- Open findings (latest run): **{state.get('open_findings', 0)}**",
        f"- Policy: auto-bootstrap **{policy.get('auto_bootstrap')}** · checks on change **{policy.get('run_on_change')}** "
        f"· AI on change **{policy.get('ai_on_change')}** · AI on staged **{policy.get('ai_on_staged')}**",
        "",
        "## Guardians",
        "",
        _guardian_table(state),
    ]
    if latest:
        lines += [
            "",
            "## Latest run",
            "",
            f"- Run `{latest.get('id')}` · {latest.get('trigger')} · {latest.get('scope')} · "
            f"verdict **{verdict.get('action', 'allow')}**",
            f"- {verdict.get('summary') or 'No summary.'}",
        ]
        for finding in (latest.get("findings") or [])[:8]:
            location = finding.get("path") or "-"
            if finding.get("path") and finding.get("line"):
                location += f":{finding.get('line')}"
            lines.append(
                f"- [{finding.get('severity')}] `{finding.get('guardian')}` {location}: "
                f"{finding.get('title')}"
            )
    candidates = state.get("candidates") or []
    if candidates:
        lines += [
            "",
            "## Existing file-matching profiles you can adopt",
            "",
            ", ".join(f"`{item.get('name')}`" for item in candidates[:20]),
            "",
            "Use `quality_guardians(action='adopt', name=...)` to use one as a guardian.",
        ]
    errors = (state.get("bootstrap") or {}).get("errors") or []
    if errors:
        lines += ["", "## Bootstrap errors", ""] + [f"- {error}" for error in errors]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# quality_guardians
# -----------------------------------------------------------------------------
async def quality_guardians(
    action: str = "list",
    name: str = "",
    adopt: bool = False,
    force: bool = False,
    enabled: Optional[bool] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
    file_match: Optional[str] = None,
    tags: Optional[List[str]] = None,
    triggers: Optional[List[str]] = None,
    checks: Optional[List[Dict[str, Any]]] = None,
    ignore_paths: Optional[List[str]] = None,
    severity: Optional[str] = None,
    blocking_severity: Optional[str] = None,
    title: str = "",
    rule: str = "",
    **kwargs
) -> ToolResponse:
    """List, create, refresh, adopt, enable or edit the project's quality guardians.

    A guardian is a profile with a ``file_match`` scope, a review prompt and
    optional deterministic checks. Actions:

    - ``list`` — show the guardians, their checks and the latest findings.
    - ``bootstrap`` — detect the stack and create the missing guardians. Pass
      ``adopt=true`` to also turn existing file-matching profiles into guardians.
    - ``adopt`` — use one existing profile (``name``) as a guardian.
    - ``enable`` / ``disable`` — change whether a guardian runs.
    - ``update`` — edit ``description``, ``content``, ``file_match``,
      ``triggers``, ``checks``, ``ignore_paths``, severities, etc.
    - ``learn`` — add a durable rule (``title`` + ``rule``) to a guardian.

    Args:
        action: One of the actions above.
        name: Guardian/profile name (required except for list/bootstrap).
        adopt: Adopt existing file-match profiles on bootstrap.
        force: Refresh auto-generated guardians even when the signature is unchanged.
        enabled: New enabled state.
        description/content/file_match/tags: Profile fields.
        triggers: ``change``, ``staged``, ``commit``, ``manual``.
        checks: Check declarations (objects with ``id``, ``kind``, ``builtin`` or
            ``command``, ``severity``, ``fix_command``...).
        ignore_paths: Globs this guardian never reviews.
        severity/blocking_severity: Default finding severity and gate threshold.
        title/rule: Rule title and body for ``learn``.
        **kwargs: Runtime arguments (``settings``/``session`` injected).

    Returns:
        ToolResponse: Rendered state/change and a compact LLM summary.

    Example:
        >>> quality_guardians(action="bootstrap")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        return _error("Project settings are not available")

    action = str(action or "list").strip().lower()
    try:
        if action == "list":
            state = service.state(settings, auto_bootstrap=True)
            return ToolResponse(
                user_response=_render_state(state),
                llm_response=json.dumps(
                    {
                        "guardian_count": state.get("guardian_count"),
                        "candidates": state.get("candidate_count"),
                        "open_findings": state.get("open_findings"),
                        "latest_run": (state.get("latest") or {}).get("id", ""),
                        "policy": state.get("policy"),
                    },
                    indent=2,
                ),
                logs=[f"Quality guardians listed ({state.get('guardian_count', 0)})"],
            )

        if action == "bootstrap":
            report = service.bootstrap_guardians(settings, force=bool(force), adopt=bool(adopt))
            state = report.get("state") or service.state(settings, auto_bootstrap=False)
            lines = [
                "# Quality guardians refreshed",
                "",
                f"- Created: {', '.join(f'`{item}`' for item in report.get('created') or []) or 'none'}",
                f"- Updated: {', '.join(f'`{item}`' for item in report.get('updated') or []) or 'none'}",
                f"- Adopted: {', '.join(f'`{item}`' for item in report.get('adopted') or []) or 'none'}",
                f"- Kept: {', '.join(f'`{item}`' for item in report.get('kept') or []) or 'none'}",
                "",
                _guardian_table(state),
            ]
            if report.get("errors"):
                lines += ["", "## Errors", ""] + [f"- {error}" for error in report["errors"]]
            return ToolResponse(
                user_response="\n".join(lines),
                llm_response=json.dumps(
                    {
                        "created": report.get("created"),
                        "updated": report.get("updated"),
                        "adopted": report.get("adopted"),
                        "kept": report.get("kept"),
                        "guardian_count": report.get("guardian_count"),
                        "signature": report.get("signature"),
                        "errors": report.get("errors"),
                    },
                    indent=2,
                ),
                logs=["Quality guardians bootstrapped"],
            )

        if action in ("adopt", "enable", "disable", "update", "learn"):
            if not _is_quality_admin(settings, kwargs):
                return _quality_admin_error()

        if not str(name or "").strip():
            return _error(f"`name` is required for action '{action}'")

        if action == "adopt":
            profile = adopt_profile(settings, name, enabled=bool(enabled if enabled is not None else True))
            return ToolResponse(
                user_response=f"✅ `{name}` is now a quality guardian (triggers: staged + manual).",
                llm_response=f"Adopted profile '{name}' as guardian",
                logs=[f"Guardian adopted: {name}"],
            )

        if action in ("enable", "disable"):
            profile = enable_guardian(settings, name, enabled=action == "enable")
            state = service.state(settings, auto_bootstrap=False)
            return ToolResponse(
                user_response=(
                    f"✅ Guardian `{name}` **{'enabled' if action == 'enable' else 'disabled'}**.\n\n"
                    + _guardian_table(state)
                ),
                llm_response=f"Guardian '{name}' {action}d",
                logs=[f"Guardian {action}: {name}"],
            )

        if action == "learn":
            if not str(rule or "").strip():
                return _error("`rule` is required for action 'learn'")
            result = remember(settings, name, title or "", rule)
            return ToolResponse(
                user_response=(
                    f"🧠 Guardian `{name}` learned rule **{result.get('title')}** "
                    f"({'created' if result.get('created') else 'updated'}).\n\n"
                    "It will be applied to every AI review of this guardian."
                ),
                llm_response=json.dumps(result, indent=2),
                logs=[f"Guardian rule learned: {name}/{result.get('title')}"],
            )

        if action == "update":
            if not any(
                value is not None for value in (
                    description, content, file_match, tags, triggers, checks,
                    ignore_paths, severity, blocking_severity, enabled,
                )
            ):
                return _error("No field to update was provided")
            parsed_checks = None
            if checks is not None:
                parsed_checks = [
                    check if isinstance(check, ProfileGuardianCheck) else dict(check)
                    for check in checks
                ]
            update_guardian(
                settings,
                name,
                description=description,
                content=content,
                file_match=file_match,
                tags=tags,
                triggers=triggers,
                checks=parsed_checks,
                ignore_paths=ignore_paths,
                severity=severity,
                blocking_severity=blocking_severity,
                enabled=enabled,
                polished=True,
            )
            profile = get_guardian(settings, name)
            state = service.state(settings, auto_bootstrap=False)
            return ToolResponse(
                user_response=(
                    f"✅ Guardian `{name}` updated.\n\n" + _guardian_table(state)
                ),
                llm_response=json.dumps(
                    {
                        "name": name,
                        "file_match": getattr(profile, "file_match", ""),
                        "triggers": getattr(getattr(profile, "guardian", None), "triggers", []),
                    },
                    indent=2,
                ),
                logs=[f"Guardian updated: {name}"],
            )

        return _error(
            f"Unknown action `{action}`. Use list, bootstrap, adopt, enable, "
            "disable, update or learn."
        )
    except (ValueError, PermissionError) as ex:
        return _error(str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("quality_guardians failed: %s", ex)
        return _error(f"Unexpected error: {ex}")


# -----------------------------------------------------------------------------
# quality_review
# -----------------------------------------------------------------------------
async def quality_review(
    scope: str = "changed",
    trigger: str = "manual",
    mode: str = "auto",
    paths: Optional[List[str]] = None,
    file_type: Any = None,
    guardians: Optional[List[str]] = None,
    base: str = "HEAD",
    change_scope: Optional[str] = None,
    max_files: Optional[int] = None,
    **kwargs
) -> ToolResponse:
    """Review project quality with the guardian system.

    Use ``scope='changed'`` for the working tree, ``scope='staged'`` semantics
    via ``trigger='staged'`` for the next commit, ``scope='files'`` with
    ``paths`` for specific files, ``scope='file_type'`` with ``file_type`` for
    one language/type, and ``scope='project'`` for a bounded global audit.

    Args:
        scope: ``changed``, ``project``, ``files`` or ``file_type``.
        trigger: ``manual``, ``change``, ``staged`` or ``commit``. Manual runs
            every selected guardian; the other triggers require the guardian to
            declare them.
        mode: ``auto`` (checks + AI when the policy allows), ``checks`` or ``review``.
        paths: Explicit files (or a filter).
        file_type: For example ``python``, ``vue``, ``docs``, ``tests``, ``.py``.
        guardians: Only these guardian names.
        base: Git reference for a changed review.
        change_scope: ``all``, ``staged`` or ``unstaged`` override.
        max_files: File cap (policy default when omitted).
        **kwargs: Runtime arguments (``settings``/``session`` injected).

    Returns:
        ToolResponse: Markdown run report plus a compact summary.

    Example:
        >>> quality_review(scope="file_type", file_type="python")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        return _error("Project settings are not available")
    session = _session_of(kwargs)
    try:
        run = await service.review(
            settings,
            session=session,
            trigger=trigger,
            scope=scope,
            mode=mode,
            paths=paths,
            file_type=file_type,
            guardians=guardians,
            base=base,
            change_scope=change_scope,
            max_files=max_files,
            user=str(getattr(getattr(session, "user", None), "username", "") or ""),
        )
    except ValueError as ex:
        return _error(str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("quality_review failed: %s", ex)
        return _error(f"Review failed: {ex}")

    markdown = run.get("markdown") or service.report_markdown(run)
    llm = service.summarize(run)
    return ToolResponse(
        user_response=markdown,
        llm_response=llm,
        logs=[
            f"Quality review {run.get('id')}: "
            f"{(run.get('verdict') or {}).get('action')} "
            f"({len(run.get('findings') or [])} finding(s))"
        ],
        cost=float(run.get("ai_cost") or 0.0),
    )


# -----------------------------------------------------------------------------
# quality_fix
# -----------------------------------------------------------------------------
async def quality_fix(
    run_id: str = "",
    finding_ids: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    guardians: Optional[List[str]] = None,
    apply: bool = False,
    use_ai: bool = True,
    **kwargs
) -> ToolResponse:
    """Fix the open quality findings of a run.

    Nothing is written unless ``apply=true``. The deterministic fix commands
    declared by the guardians run first; the AI then proposes exact
    search/replace changes for the remaining findings. After applying, the
    affected guardians run again in ``checks`` mode and the verification is
    returned, so "fixed" is evidenced, not assumed.

    Args:
        run_id: Run to fix (latest when empty).
        finding_ids: Only these finding fingerprints.
        paths: Only findings on these paths.
        guardians: Only findings from these guardians.
        apply: Write the changes (default false = plan + diff preview).
        use_ai: Ask the model for search/replace proposals.
        **kwargs: Runtime arguments (``settings``/``session`` injected).

    Returns:
        ToolResponse: What was applied/proposed and the verification run.

    Example:
        >>> quality_fix(apply=False)  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        return _error("Project settings are not available")
    session = _session_of(kwargs)
    try:
        result = await service.fix(
            settings,
            run_id=run_id,
            finding_ids=finding_ids,
            paths=paths,
            guardians=guardians,
            apply=bool(apply),
            use_ai=bool(use_ai),
            session=session,
            user=str(getattr(getattr(session, "user", None), "username", "") or ""),
        )
    except ValueError as ex:
        return _error(str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("quality_fix failed: %s", ex)
        return _error(f"Fix failed: {ex}")

    lines = [
        "# Quality fixes",
        "",
        f"- Run: `{result.get('run_id')}` · findings: **{len(result.get('findings') or [])}** "
        f"· applied: **{result.get('applied', 0)}** · mode: "
        f"**{'apply' if apply else 'proposal'}**",
    ]
    deterministic = result.get("deterministic") or []
    if deterministic:
        lines += ["", "## Deterministic fix commands", ""]
        for execution in deterministic:
            command = " ".join(execution.get("command") or []) or "(not run)"
            status = execution.get("status") or ("planned" if execution.get("applied") is False else "?")
            lines.append(f"- `{execution.get('guardian')}` / `{execution.get('check_id')}`: "
                         f"**{status}** — `{command}`")
    proposals = result.get("proposals") or []
    if proposals:
        lines += ["", "## AI fixes", ""]
        for proposal in proposals:
            lines.append(
                f"### `{proposal.get('path')}` ({proposal.get('guardian')})\n"
                f"{proposal.get('summary') or 'Proposed changes.'}\n"
            )
            if proposal.get("applied"):
                lines.append(f"_Applied {proposal.get('applied')} change(s)._")
            elif proposal.get("preview"):
                lines += ["```diff", proposal["preview"], "```"]
    if result.get("verification"):
        verification = result["verification"]
        lines += [
            "",
            "## Verification",
            "",
            f"- Run `{verification.get('run_id')}` · action **{verification.get('action')}** "
            f"· open findings: {verification.get('findings')}",
        ]
        for check in verification.get("checks") or []:
            lines.append(
                f"- `{check.get('guardian')}` / `{check.get('check_id')}`: **{check.get('status')}**"
            )
    if result.get("errors"):
        lines += ["", "## Errors", ""] + [f"- {error}" for error in result["errors"]]
    if not apply:
        lines += [
            "",
            "> Nothing was written. Call `quality_fix(apply=true, ...)` to apply the "
            "fixes above (the deterministic commands still run through the CISO gate).",
        ]
    return ToolResponse(
        user_response="\n".join(lines),
        llm_response=json.dumps(
            {
                "run_id": result.get("run_id"),
                "findings": len(result.get("findings") or []),
                "applied": result.get("applied", 0),
                "deterministic": len(deterministic),
                "proposals": len(proposals),
                "verification": result.get("verification"),
                "errors": result.get("errors"),
                "cost": result.get("cost"),
            },
            indent=2,
        ),
        logs=[f"Quality fix on run {result.get('run_id')}: applied {result.get('applied', 0)}"],
        cost=float(result.get("cost") or 0.0),
    )


# -----------------------------------------------------------------------------
# quality_feedback
# -----------------------------------------------------------------------------
def quality_feedback(
    finding_id: str,
    run_id: str = "",
    action: str = "dismiss",
    reason: str = "",
    title: str = "",
    content: str = "",
    scope: str = "finding",
    learn: bool = False,
    suppress: bool = False,
    expires_in_days: Optional[int] = None,
    **kwargs
) -> ToolResponse:
    """Dismiss, suppress, learn from or reopen a guardian finding.

    This is how a guardian is polished: the user explains why a finding is a
    false positive or an accepted convention. ``learn`` writes a durable rule
    to the guardian profile (it reaches every future AI review); ``suppress``
    stores a matcher so the same finding does not come back. Both can be
    combined.

    Args:
        finding_id: Finding fingerprint (from ``quality_review``).
        run_id: Run owning the finding (latest when empty).
        action: ``dismiss``, ``suppress``, ``learn`` or ``reopen``.
        reason: Why the decision was made (required except for reopen).
        title/content: Rule title and body for ``learn``.
        scope: Suppression scope: ``finding``, ``rule`` or ``path``.
        learn: Also learn a rule (with dismiss/suppress).
        suppress: Also add a suppression.
        expires_in_days: Optional suppression expiry.
        **kwargs: Runtime arguments (``settings``/``session`` injected).

    Returns:
        ToolResponse: The decision and the updated finding.

    Example:
        >>> quality_feedback(finding_id="abc123", action="learn",
        ...                  reason="this project uses JSON logs")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        return _error("Project settings are not available")
    session = _session_of(kwargs)
    user = _user_of(kwargs)
    try:
        result = service.feedback(
            settings,
            run_id=run_id,
            finding_id=finding_id,
            action=action,
            reason=reason,
            title=title,
            content=content,
            scope=scope,
            learn=bool(learn),
            suppress=bool(suppress),
            session=session,
            user=user,
            expires_in_days=expires_in_days,
        )
    except (ValueError, PermissionError) as ex:
        return _error(str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("quality_feedback failed: %s", ex)
        return _error(f"Feedback failed: {ex}")

    finding = result.get("finding") or {}
    lines = [
        f"# Quality feedback: `{result.get('action')}`",
        "",
        f"- Finding: `{finding.get('id')}` — {finding.get('title')}",
        f"- Location: `{finding.get('path') or '-'}"
        + (f":{finding.get('line')}" if finding.get("line") else "")
        + "`",
        f"- Status: **{finding.get('status', 'open')}**",
    ]
    if result.get("suppression"):
        lines.append(f"- Suppression: `{result['suppression'].get('id')}` ({scope})")
    if result.get("rule"):
        lines.append(f"- Learned rule: **{result['rule'].get('title')}**")
    lines += ["", "The decision is stored in the quality run history."]
    return ToolResponse(
        user_response="\n".join(lines),
        llm_response=json.dumps(
            {
                "action": result.get("action"),
                "finding_id": finding.get("id"),
                "status": finding.get("status"),
                "suppression": bool(result.get("suppression")),
                "rule": (result.get("rule") or {}).get("title", ""),
            },
            indent=2,
        ),
        logs=[f"Quality feedback: {result.get('action')} {finding_id}"],
    )


# -----------------------------------------------------------------------------
# quality_policy
# -----------------------------------------------------------------------------
def quality_policy(
    action: str = "get",
    updates: Optional[Dict[str, Any]] = None,
    **kwargs
) -> ToolResponse:
    """Read or update the project quality-guardian policy.

    The policy controls cost and triggers, not the guardian rules: guardian
    prompts and checks live in the profiles.

    Args:
        action: ``get`` or ``update``.
        updates: Partial policy for ``update``. Keys include ``enabled``,
            ``auto_bootstrap``, ``run_on_change``, ``ai_on_change``,
            ``ai_on_staged``, ``ai_on_commit``, ``ai_on_manual``,
            ``debounce_seconds``, ``max_files_per_run``,
            ``max_ai_files_per_run``, ``max_ai_calls_per_run``,
            ``file_cooldown_seconds``, ``block_above``, ``approval_above``,
            ``warn_above``, ``ignore_paths``, ``disabled_guardians``,
            ``ignore_rules`` and ``auto_fix``.
        **kwargs: Runtime arguments (``settings`` injected).

    Returns:
        ToolResponse: The effective policy.
    """
    settings = _settings_of(kwargs)
    if not settings:
        return _error("Project settings are not available")
    action = str(action or "get").strip().lower()
    try:
        if action == "update":
            if not isinstance(updates, dict) or not updates:
                return _error("`updates` must be a non-empty object")
            if not _is_quality_admin(settings, kwargs):
                return _quality_admin_error()
            result = service.update_policy(settings, updates)
        else:
            result = service.get_policy(settings)
    except (ValueError, OSError) as ex:
        return _error(str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("quality_policy failed: %s", ex)
        return _error(f"Policy operation failed: {ex}")

    policy = result.get("policy") or {}
    lines = [
        "# Quality guardian policy",
        "",
        f"- Enabled: **{policy.get('enabled')}** · auto-bootstrap: **{policy.get('auto_bootstrap')}**",
        f"- Run on change: **{policy.get('run_on_change')}** · file cooldown: "
        f"**{policy.get('file_cooldown_seconds')}s** · debounce: **{policy.get('debounce_seconds')}s**",
        f"- AI lanes: change **{policy.get('ai_on_change')}** · staged **{policy.get('ai_on_staged')}** "
        f"· commit **{policy.get('ai_on_commit')}** · manual **{policy.get('ai_on_manual')}**",
        f"- AI budget: **{policy.get('max_ai_files_per_run')}** file(s) per guardian, "
        f"**{policy.get('max_ai_calls_per_run')}** call(s) per run",
        f"- Ladder: block ≥ **{policy.get('block_above')}** · approval ≥ "
        f"**{policy.get('approval_above')}** · warn ≥ **{policy.get('warn_above')}**",
        f"- Suppressions: ignore rules {policy.get('ignore_rules') or []} · "
        f"disabled guardians {policy.get('disabled_guardians') or []}",
        f"- Auto-fix: **{policy.get('auto_fix')}** · max file: "
        f"**{policy.get('max_file_bytes')}** bytes · max diff: **{policy.get('max_diff_chars')}** chars",
    ]
    return ToolResponse(
        user_response="\n".join(lines),
        llm_response=json.dumps(result, indent=2),
        logs=[f"Quality policy {action}"],
    )


# -----------------------------------------------------------------------------
# JSON schemas
# -----------------------------------------------------------------------------
_CHECK_SCHEMA = {
    "type": "object",
    "description": "Deterministic check declaration",
    "properties": {
        "id": {"type": "string", "description": "Stable check id"},
        "title": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["builtin", "command"],
            "description": "builtin runs in-process; command runs a fixed argv",
        },
        "builtin": {
            "type": "string",
            "enum": ["syntax", "conflict_markers", "markdown_links"],
        },
        "command": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Fixed argv; matched paths are appended unless attach_paths=false",
        },
        "fix_command": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional deterministic fix argv (used only by quality_fix)",
        },
        "cwd": {"type": "string", "description": "Project-relative working directory"},
        "attach_paths": {"type": "boolean"},
        "timeout_seconds": {"type": "integer"},
        "severity": {
            "type": "string",
            "enum": ["info", "low", "medium", "high", "critical"],
        },
        "enabled": {"type": "boolean"},
    },
    "required": ["id"],
}

QUALITY_GUARDIANS_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "quality_guardians",
        "description": (
            "Manage the project's quality guardians. A guardian is a profile with a "
            "file_match scope whose instructions are the review rules and whose "
            "guardian block declares deterministic checks and triggers. Actions: "
            "list (state + latest findings), bootstrap (auto-create from the detected "
            "stack; adopt=true converts existing file_match profiles), adopt, enable, "
            "disable, update (content/file_match/triggers/checks/ignore_paths/severity), "
            "learn (add a durable rule)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "bootstrap", "adopt", "enable", "disable", "update", "learn"],
                    "description": "Action to perform",
                },
                "name": {"type": "string", "description": "Guardian/profile name"},
                "adopt": {"type": "boolean", "description": "Adopt existing file-match profiles on bootstrap"},
                "force": {"type": "boolean", "description": "Refresh auto-generated guardians even when unchanged"},
                "enabled": {"type": "boolean"},
                "description": {"type": "string"},
                "content": {"type": "string", "description": "Review rules (Markdown)"},
                "file_match": {"type": "string", "description": "Regex selecting the files this guardian owns"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "triggers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["change", "staged", "commit", "manual"]},
                },
                "checks": {"type": "array", "items": _CHECK_SCHEMA},
                "ignore_paths": {"type": "array", "items": {"type": "string"}},
                "severity": {
                    "type": "string",
                    "enum": ["info", "low", "medium", "high", "critical"],
                },
                "blocking_severity": {
                    "type": "string",
                    "enum": ["info", "low", "medium", "high", "critical"],
                },
                "title": {"type": "string", "description": "Rule title for action=learn"},
                "rule": {"type": "string", "description": "Rule body for action=learn"},
            },
            "required": ["action"],
        },
    },
}

QUALITY_REVIEW_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "quality_review",
        "description": (
            "Review project quality with the guardian system and return a structured "
            "verdict. Use scope=changed for the working tree, scope=files with paths, "
            "scope=file_type with file_type (python, vue, docs, tests...), or "
            "scope=project for a bounded global audit. mode=checks is deterministic "
            "(no AI/cost); mode=auto also runs the AI review when the policy allows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["changed", "project", "files", "file_type"],
                    "description": "What to review",
                },
                "trigger": {
                    "type": "string",
                    "enum": ["manual", "change", "staged", "commit"],
                    "description": "manual runs every selected guardian; the others respect its declared triggers",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "checks", "review"],
                    "description": "checks = deterministic only; review = AI only; auto = both when allowed",
                },
                "paths": {"type": "array", "items": {"type": "string"}},
                "file_type": {
                    "type": "string",
                    "description": "Language/extension/domain filter, e.g. python, vue, docs, tests, .ts (comma separated)",
                },
                "guardians": {"type": "array", "items": {"type": "string"}},
                "base": {"type": "string", "description": "Git ref for scope=changed"},
                "change_scope": {
                    "type": "string",
                    "enum": ["all", "staged", "unstaged"],
                },
                "max_files": {"type": "integer"},
            },
            "required": [],
        },
    },
}

QUALITY_FIX_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "quality_fix",
        "description": (
            "Fix the open findings of a quality run. Proposal by default: set "
            "apply=true to write. Runs the guardians' deterministic fix commands, "
            "then asks the model for exact search/replace fixes for the remaining "
            "findings, and re-runs the affected guardians to verify."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run to fix (latest when empty)"},
                "finding_ids": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}},
                "guardians": {"type": "array", "items": {"type": "string"}},
                "apply": {"type": "boolean", "description": "Write the fixes (default false)"},
                "use_ai": {"type": "boolean", "description": "Use the model for fixes the commands do not cover"},
            },
            "required": [],
        },
    },
}

QUALITY_FEEDBACK_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "quality_feedback",
        "description": (
            "Record a decision about a guardian finding: dismiss it, suppress it "
            "(no future reports), learn a durable rule for the guardian, or reopen it. "
            "Use learn to polish a guardian when a finding is a false positive or an "
            "accepted project convention."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string", "description": "Finding fingerprint from quality_review"},
                "run_id": {"type": "string", "description": "Run id (latest when empty)"},
                "action": {
                    "type": "string",
                    "enum": ["dismiss", "suppress", "learn", "reopen"],
                },
                "reason": {"type": "string", "description": "Why (required except reopen)"},
                "title": {"type": "string", "description": "Rule title for learn"},
                "content": {"type": "string", "description": "Rule body for learn"},
                "scope": {
                    "type": "string",
                    "enum": ["finding", "rule", "path"],
                    "description": "Suppression scope",
                },
                "learn": {"type": "boolean", "description": "Also learn a rule"},
                "suppress": {"type": "boolean", "description": "Also add a suppression"},
                "expires_in_days": {"type": "integer"},
            },
            "required": ["finding_id"],
        },
    },
}

QUALITY_POLICY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "quality_policy",
        "description": (
            "Read or update the project quality-guardian policy: which triggers run "
            "checks/AI, the cost limits and the verdict ladder. Guardian rules live "
            "in the profiles; this is the runtime policy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "update"]},
                "updates": {
                    "type": "object",
                    "description": "Partial policy for action=update",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "auto_bootstrap": {"type": "boolean"},
                        "run_on_change": {"type": "boolean"},
                        "ai_on_change": {"type": "boolean"},
                        "ai_on_staged": {"type": "boolean"},
                        "ai_on_commit": {"type": "boolean"},
                        "ai_on_manual": {"type": "boolean"},
                        "debounce_seconds": {"type": "integer"},
                        "max_files_per_run": {"type": "integer"},
                        "max_ai_files_per_run": {"type": "integer"},
                        "max_ai_calls_per_run": {"type": "integer"},
                        "file_cooldown_seconds": {"type": "integer"},
                        "block_above": {"type": "string"},
                        "approval_above": {"type": "string"},
                        "warn_above": {"type": "string"},
                        "ignore_paths": {"type": "array", "items": {"type": "string"}},
                        "disabled_guardians": {"type": "array", "items": {"type": "string"}},
                        "ignore_rules": {"type": "array", "items": {"type": "string"}},
                        "auto_fix": {"type": "boolean"},
                    },
                },
            },
            "required": ["action"],
        },
    },
}


# Made with ❤️ by codx-junior
