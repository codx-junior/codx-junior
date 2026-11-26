"""High-level quality-guardian service shared by the tools and the REST API.

The tools and the router are thin adapters: both call these functions so a
guardian run started from a chat and one started from the Quality panel produce
the same run, the same verdict and the same stored history.

Everything here is best-effort except the explicit errors (unknown run, unknown
guardian, invalid policy): a quality feature must never take the chat or the
file-change pipeline down because a linter is missing.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from .diff import build_change_set
from .fix import apply_changes_to_content, _diff_preview, propose_fixes
from .models import render_run_markdown, utc_now
from .policy import resolve_policy, save_policy, stored_policy
from .profiles import (
    adopt_profile,
    bootstrap,
    enable_guardian,
    get_guardian,
    guardian_settings,
    has_guardians,
    list_guardians,
    remember,
    update_guardian,
)
from .runner import GuardianRunner
from .store import RunStore

logger = logging.getLogger(__name__)

#: Maximum findings fixed in one call (cost/risk guard).
MAX_FIX_FINDINGS = 20

#: Maximum files fixed in one call.
MAX_FIX_FILES = 10


# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------
def state(settings: Any, *, auto_bootstrap: bool = True) -> Dict[str, Any]:
    """Return everything the Quality panel needs for a project.

    Args:
        settings: Project settings.
        auto_bootstrap: Create the missing guardians on first access. This is
            what makes the feature automatic: the first time a user opens the
            panel (or asks for a review) the guardians exist.

    Returns:
        Dict: policy, guardians, candidates, runs, latest run and counters.
    """
    bootstrap_report: Dict[str, Any] = {}
    if auto_bootstrap and not has_guardians(settings):
        try:
            bootstrap_report = bootstrap(settings)
        except Exception as ex:
            logger.exception("Quality guardian auto-bootstrap failed: %s", ex)
            bootstrap_report = {"errors": [str(ex)]}

    store = RunStore(settings)
    result = store.summary()
    result.update(
        list_guardians(settings, include_disabled=True, include_candidates=True)
    )
    result["bootstrap"] = {
        "created": bootstrap_report.get("created") or [],
        "updated": bootstrap_report.get("updated") or [],
        "errors": bootstrap_report.get("errors") or [],
        "signals": bootstrap_report.get("signals") or {},
        "signature": bootstrap_report.get("signature") or "",
    }
    try:
        from .triggers import guardian_trigger_status

        result["trigger"] = guardian_trigger_status(settings)
    except Exception:  # pragma: no cover - the panel works without the scheduler
        result["trigger"] = {}
    return result


def bootstrap_guardians(
    settings: Any,
    *,
    force: bool = False,
    adopt: bool = False,
) -> Dict[str, Any]:
    """Create/refresh the guardians of the project (see :func:`profiles.bootstrap`)."""
    report = bootstrap(settings, force=force, adopt=adopt)
    report["state"] = state(settings, auto_bootstrap=False)
    return report


# -----------------------------------------------------------------------------
# Review
# -----------------------------------------------------------------------------
async def review(
    settings: Any,
    *,
    session: Any = None,
    ai: Any = None,
    trigger: str = "manual",
    scope: str = "changed",
    mode: str = "auto",
    paths: Optional[List[str]] = None,
    file_type: Any = None,
    guardians: Optional[List[str]] = None,
    base: str = "HEAD",
    change_scope: Optional[str] = None,
    max_files: Optional[int] = None,
    user: str = "",
) -> Dict[str, Any]:
    """Run a quality review.

    Args:
        settings: Project settings.
        session: Active session (AI access).
        ai: Explicit AI instance (tests/delegation).
        trigger/scope/mode/paths/file_type/guardians/base/change_scope/max_files:
            See :meth:`GuardianRunner.run_async`.
        user: User that triggered the review.

    Returns:
        Dict: The persisted run, with a ``markdown`` report added.
    """
    runner = GuardianRunner(settings, session=session, ai=ai)
    run = await runner.run_async(
        trigger=trigger,
        scope=scope,
        mode=mode,
        paths=paths,
        file_type=file_type,
        guardians=guardians,
        base=base,
        change_scope=change_scope,
        max_files=max_files,
        user=user,
    )
    run["markdown"] = render_run_markdown(run)
    return run


def latest(settings: Any, *, run_id: str = "") -> Optional[Dict[str, Any]]:
    """Return one run by id, or the latest run of the project."""
    store = RunStore(settings)
    if run_id:
        return store.read_run(run_id)
    return store.latest_run()


# -----------------------------------------------------------------------------
# Feedback (dismiss / learn / suppress / reopen)
# -----------------------------------------------------------------------------
def _find_finding(run: Dict[str, Any], finding_id: str) -> Optional[Dict[str, Any]]:
    """Return one finding of a run by fingerprint."""
    for finding in run.get("findings") or []:
        if str(finding.get("id") or "") == str(finding_id or ""):
            return finding
    return None


def _current_username(settings: Any, session: Any = None) -> str:
    """Return the username behind a session, or an empty string."""
    user = getattr(session, "user", None) if session is not None else None
    if not user:
        active = getattr(settings, "_active_session", None)
        user = getattr(active, "user", None) if active else None
    return str(getattr(user, "username", "") or "")


def _require_learning_permission(settings: Any, user: Any) -> None:
    """Raise PermissionError unless *user* can change what profiles learn."""
    if user is None or not getattr(user, "username", ""):
        raise PermissionError(
            "Learning a guardian rule requires an authenticated user with "
            "profile knowledge administration rights."
        )
    from codx.junior.security.user_management import UserSecurityManager

    allowed = UserSecurityManager().can_manage_profile_knowledge(
        user=user, settings=settings
    )
    if not allowed:
        raise PermissionError(
            f"User '{user.username}' cannot change what profiles learn in "
            f"project '{getattr(settings, 'project_name', '')}'. Only a project "
            "admin, a project knowledge admin or a codx-junior admin can do it."
        )


def feedback(
    settings: Any,
    *,
    run_id: str = "",
    finding_id: str = "",
    action: str = "dismiss",
    reason: str = "",
    title: str = "",
    content: str = "",
    scope: str = "finding",
    learn: bool = False,
    suppress: bool = False,
    session: Any = None,
    user: Any = None,
    expires_in_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Record what the user decided about one finding.

    Actions:

    * ``dismiss`` — close the finding with a reason (no future suppression);
    * ``suppress`` — close it and add a suppression so the same finding does not
      come back on the next run;
    * ``learn`` — write a durable guardian rule (``enrich.md``) and optionally
      suppress the finding too;
    * ``reopen`` — mark a dismissed/suppressed finding open again.

    Args:
        settings: Project settings.
        run_id: Run owning the finding (latest run when empty).
        finding_id: Finding fingerprint.
        action: ``dismiss``, ``suppress``, ``learn`` or ``reopen``.
        reason: Human explanation (required for dismiss/suppress/learn).
        title: Rule title for ``learn``.
        content: Rule body for ``learn`` (a default is built from the finding).
        scope: Suppression scope: ``finding``, ``rule`` or ``path``.
        learn: When true with dismiss/suppress, also learn a rule.
        suppress: When true with learn/dismiss, also add a suppression.
        session: Active session (used for the user).
        user: Explicit user (API tools).
        expires_in_days: Optional suppression expiry.

    Returns:
        Dict: ``action``, ``finding``, ``run``, ``suppression`` and ``rule``.

    Raises:
        ValueError: Unknown run/finding/action.
        PermissionError: When learning is requested without permission.
    """
    action = str(action or "dismiss").strip().lower()
    if action not in ("dismiss", "suppress", "learn", "reopen"):
        raise ValueError("action must be dismiss, suppress, learn or reopen")

    store = RunStore(settings)
    run = store.read_run(run_id) if run_id else store.latest_run()
    if not run:
        raise ValueError("No quality run found: run a review first")
    finding = _find_finding(run, finding_id)
    if not finding:
        raise ValueError(f"Finding not found in run {run.get('id')}: {finding_id}")

    guardian_name = str(finding.get("guardian") or "")
    rule_id = str(finding.get("rule_id") or "")
    path = str(finding.get("path") or "")
    finding_title = str(finding.get("title") or "")
    reason = " ".join(str(reason or "").split())[:1000]
    if action in ("dismiss", "suppress", "learn") and not reason:
        raise ValueError("A reason is required: it is the evidence for the decision")

    username = _current_username(settings, session=session) or str(
        getattr(user, "username", "") or ""
    )
    suppression: Optional[Dict[str, Any]] = None
    rule: Optional[Dict[str, Any]] = None

    do_learn = action == "learn" or learn
    do_suppress = action == "suppress" or suppress

    if action == "reopen":
        updated = store.update_finding(
            run.get("id", ""), finding_id, "open", reason=reason, user=username
        )
        suppression_id = str(finding.get("suppression_id") or "")
        if suppression_id:
            store.remove_suppression(suppression_id)
        return {
            "action": action,
            "finding": _find_finding(updated or run, finding_id),
            "run": updated or run,
            "suppression": None,
            "rule": None,
        }

    if do_learn:
        _require_learning_permission(settings, user)
        rule_title = " ".join(str(title or "").split())[:120] or (
            f"Quality: {finding_title[:100]}"
        )
        rule_body = str(content or "").strip() or (
            f"Do not report `{rule_id}` on `{path}` when the change matches the "
            f"project's accepted convention. Reason: {reason}. "
            f"Finding: {finding_title}."
        )
        rule = remember(settings, guardian_name, rule_title, rule_body)

    if do_suppress:
        suppression_scope = str(scope or "finding").strip().lower()
        matcher: Dict[str, Any] = {}
        if suppression_scope == "rule":
            matcher = {"guardian": guardian_name, "rule_id": rule_id}
        elif suppression_scope == "path":
            matcher = {"guardian": guardian_name, "rule_id": rule_id, "path": path}
        else:
            matcher = {"finding_id": finding_id}
        matcher.update(
            {
                "reason": reason,
                "created_by": username,
                "source": "quality_feedback",
                "title": finding_title[:300],
            }
        )
        if expires_in_days is not None:
            try:
                days = int(expires_in_days)
            except (TypeError, ValueError):
                days = 0
            if days > 0:
                from datetime import datetime, timedelta, timezone

                matcher["expires_at"] = (
                    datetime.now(timezone.utc) + timedelta(days=days)
                ).replace(microsecond=0).isoformat()
        suppression = store.add_suppression(matcher)

    status = "suppressed" if suppression else "dismissed"
    updated = store.update_finding(
        run.get("id", ""),
        finding_id,
        status,
        reason=reason,
        user=username,
        suppression_id=str((suppression or {}).get("id") or ""),
    )
    return {
        "action": action,
        "finding": _find_finding(updated or run, finding_id),
        "run": updated or run,
        "suppression": suppression,
        "rule": rule,
    }


# -----------------------------------------------------------------------------
# Fix
# -----------------------------------------------------------------------------
async def fix(
    settings: Any,
    *,
    run_id: str = "",
    finding_ids: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    guardians: Optional[List[str]] = None,
    apply: bool = False,
    use_ai: bool = True,
    session: Any = None,
    ai: Any = None,
    user: str = "",
) -> Dict[str, Any]:
    """Fix the open findings of a run.

    The deterministic fix commands run first (when ``apply`` is true), then the
    AI proposes exact search/replace changes for the remaining findings. With
    ``apply=False`` nothing is written: the caller gets the plan, the commands
    and a diff preview. After an apply, the affected guardians run again in
    ``checks`` mode and the verification is returned.

    Args:
        settings: Project settings.
        run_id: Run to fix (latest when empty).
        finding_ids: Only these findings (all open when empty).
        paths: Only findings on these paths.
        guardians: Only findings from these guardians.
        apply: Write the fixes (default false, proposal only).
        use_ai: Ask the model for search/replace proposals.
        session: Active session (AI + user).
        ai: Explicit AI instance (tests).
        user: User that triggered the fix.

    Returns:
        Dict: ``run_id``, ``findings``, ``deterministic``, ``proposals``,
        ``verification``, ``errors``, ``cost`` and ``applied``.

    Raises:
        ValueError: When there is no run or no open finding to fix.
    """
    store = RunStore(settings)
    run = store.read_run(run_id) if run_id else store.latest_run()
    if not run:
        raise ValueError("No quality run found: run a review first")

    selected = []
    wanted_ids = {str(item) for item in (finding_ids or []) if item}
    wanted_paths = {
        str(path).replace("\\", "/").lstrip("./")
        for path in (paths or [])
        if path
    }
    wanted_guardians = {str(item) for item in (guardians or []) if item}
    for finding in run.get("findings") or []:
        if str(finding.get("status") or "open") != "open":
            continue
        if wanted_ids and str(finding.get("id") or "") not in wanted_ids:
            continue
        if wanted_paths and str(finding.get("path") or "") not in wanted_paths:
            continue
        if wanted_guardians and str(finding.get("guardian") or "") not in wanted_guardians:
            continue
        selected.append(finding)
        if len(selected) >= MAX_FIX_FINDINGS:
            break

    if not selected:
        return {
            "run_id": run.get("id", ""),
            "findings": [],
            "deterministic": [],
            "proposals": [],
            "verification": {},
            "errors": [],
            "cost": 0.0,
            "applied": 0,
            "message": "No open finding matches the request",
        }

    policy = resolve_policy(settings)
    result: Dict[str, Any] = {
        "run_id": run.get("id", ""),
        "findings": [
            {"id": finding.get("id"), "path": finding.get("path"), "title": finding.get("title")}
            for finding in selected
        ],
        "deterministic": [],
        "proposals": [],
        "verification": {},
        "errors": [],
        "cost": 0.0,
        "applied": 0,
    }

    # ── Deterministic fix commands ───────────────────────────────────────
    from .checks import run_fix_commands

    by_guardian: Dict[str, List[Dict[str, Any]]] = {}
    for finding in selected:
        by_guardian.setdefault(str(finding.get("guardian") or ""), []).append(finding)

    fixed_paths: List[str] = []
    touched_guardians: List[str] = []
    touched_paths = sorted({str(finding.get("path") or "") for finding in selected if finding.get("path")})
    if touched_paths:
        # The file entries are needed for both the plan and the apply path:
        # fix commands receive the existing files and their absolute paths.
        file_entries, file_errors = build_change_set(
            settings,
            paths=touched_paths,
            scope="files",
            policy=resolve_policy(settings),
            max_files=MAX_FIX_FILES,
            include_content=True,
        )
        result["errors"].extend(file_errors or [])
        for guardian_name, findings in by_guardian.items():
            profile = get_guardian(settings, guardian_name)
            block = guardian_settings(profile) if profile else None
            if not block:
                continue
            guardian_files = [
                entry for entry in file_entries
                if any(
                    str(finding.get("path") or "") == entry.get("path")
                    for finding in findings
                )
            ]
            commands = [
                {
                    "guardian": guardian_name,
                    "check_id": check.id,
                    "command": list(check.fix_command or []),
                }
                for check in (block.checks or [])
                if check.fix_command
            ]
            if not commands:
                continue
            touched_guardians.append(guardian_name)
            allowed = bool(block.auto_fix or policy.auto_fix)
            if not apply or not allowed:
                reason = "apply=false" if not apply else "auto_fix=false"
                result["deterministic"].extend(
                    {**command, "applied": False, "reason": reason}
                    for command in commands
                )
                continue
            executions, command_errors = run_fix_commands(
                guardian_name,
                block.checks or [],
                settings,
                guardian_files,
                policy=policy,
            )
            result["deterministic"].extend(executions)
            result["errors"].extend(command_errors)
            fixed_paths.extend(
                str(entry.get("path") or "") for entry in guardian_files
            )

    # ── AI search/replace proposals ─────────────────────────────────────
    if use_ai:
        findings_by_path: Dict[str, List[Dict[str, Any]]] = {}
        for finding in selected:
            path = str(finding.get("path") or "")
            if not path:
                continue
            findings_by_path.setdefault(path, []).append(finding)
        for path, path_findings in list(findings_by_path.items())[:MAX_FIX_FILES]:
            guardian_name = str(path_findings[0].get("guardian") or "")
            proposal = await propose_fixes(
                settings,
                session,
                path=path,
                findings=path_findings,
                guardian=guardian_name,
                apply=apply,
                ai=ai,
            )
            result["cost"] = round(result["cost"] + float(proposal.get("cost") or 0.0), 6)
            if apply:
                result["applied"] += int(proposal.get("applied") or 0)
            result["errors"].extend(proposal.get("errors") or [])
            entry = {
                "path": path,
                "guardian": guardian_name,
                "summary": proposal.get("summary") or "",
                "changes": proposal.get("changes") or [],
                "applied": int(proposal.get("applied") or 0),
                "cost": float(proposal.get("cost") or 0.0),
                "model": proposal.get("model") or "",
            }
            if not apply and entry["changes"]:
                absolute = os.path.join(
                    getattr(settings, "abs_project_path", "") or os.getcwd(), path
                )
                try:
                    with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                        original = handle.read()
                    proposed, preview_errors = apply_changes_to_content(
                        original, entry["changes"]
                    )
                    entry["preview"] = _diff_preview(path, original, proposed)
                    result["errors"].extend(preview_errors)
                except OSError as ex:
                    result["errors"].append(f"{path}: {ex}")
            result["proposals"].append(entry)
            touched_paths.append(path)

    # ── Verify the touched guardians after an apply ─────────────────────
    if apply:
        unique_paths = sorted(set(path for path in touched_paths if path))
        unique_guardians = sorted(set(touched_guardians + [
            str(finding.get("guardian") or "") for finding in selected
            if finding.get("guardian")
        ]))
        if unique_paths:
            try:
                verification = await GuardianRunner(
                    settings, session=session, ai=None
                ).run_async(
                    trigger="manual",
                    scope="files",
                    mode="checks",
                    paths=unique_paths,
                    guardians=unique_guardians,
                    user=user,
                )
                result["verification"] = {
                    "run_id": verification.get("id"),
                    "action": (verification.get("verdict") or {}).get("action"),
                    "findings": len(
                        [
                            finding for finding in (verification.get("findings") or [])
                            if str(finding.get("status") or "open") == "open"
                        ]
                    ),
                    "checks": [
                        {
                            "guardian": check.get("guardian"),
                            "check_id": check.get("check_id"),
                            "status": check.get("status"),
                        }
                        for check in (verification.get("checks") or [])
                    ],
                }
            except Exception as ex:
                logger.exception("Quality fix verification failed: %s", ex)
                result["errors"].append(f"Verification failed: {ex}")
    return result


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------
def get_policy(settings: Any) -> Dict[str, Any]:
    """Return the effective quality policy and the raw stored overrides."""
    return {
        "policy": resolve_policy(settings).to_dict(),
        "stored": stored_policy(settings),
    }


def update_policy(settings: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *updates* into the project quality policy."""
    saved = save_policy(settings, updates or {})
    return {"policy": saved, "stored": stored_policy(settings)}


# -----------------------------------------------------------------------------
# Report helpers
# -----------------------------------------------------------------------------
def report_markdown(run: Dict[str, Any], *, title: str = "") -> str:
    """Render a run as Markdown (tool/API convenience)."""
    return render_run_markdown(run, title=title)


def summarize(run: Dict[str, Any]) -> str:
    """Return the compact machine summary used by the LLM tool response."""
    verdict = run.get("verdict") or {}
    lines = [
        f"quality run {run.get('id', '')}",
        f"- trigger: {run.get('trigger', '')} · scope: {run.get('scope', '')} · mode: {run.get('mode', '')}",
        f"- action: {verdict.get('action', '')} · findings: {verdict.get('finding_count', 0)} "
        f"· highest: {verdict.get('highest_severity', 'none')}",
        f"- guardians: {', '.join(run.get('guardians') or []) or 'none'}",
        f"- files: {len(run.get('files') or [])} · checks: {len(run.get('checks') or [])} "
        f"· ai calls: {run.get('ai_calls', 0)} · cost: {run.get('ai_cost', 0)}",
    ]
    for finding in (run.get("findings") or [])[:12]:
        location = finding.get("path") or "-"
        if finding.get("path") and finding.get("line"):
            location += f":{finding.get('line')}"
        lines.append(
            f"- [{finding.get('severity')}] {finding.get('rule_id')} @ {location}: "
            f"{finding.get('title')} → {finding.get('remediation') or 'fix it'}"
        )
    if len(run.get("findings") or []) > 12:
        lines.append(f"- … {len(run['findings']) - 12} more finding(s)")
    for error in (run.get("errors") or [])[:5]:
        lines.append(f"- error: {error}")
    return "\n".join(lines)


# Made with ❤️ by codx-junior
