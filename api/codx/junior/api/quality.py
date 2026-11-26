"""Quality guardian REST API.

This router is the server side of the Quality panel and the guardian section of
the team view. It is deliberately thin: every decision (matching, checks, AI
review, suppression ladder) is made by
:mod:`codx.junior.quality.guardians`, the same code the tools and the
background file-change pipeline use.

Endpoints:

    GET    /quality/state                         guardians + runs + policy
    GET    /quality/guardians                     guardian list
    POST   /quality/guardians/bootstrap           create/refresh the guardians
    POST   /quality/guardians/{name}/adopt        adopt an existing profile
    POST   /quality/guardians/{name}/enable       enable/disable
    PATCH  /quality/guardians/{name}              edit a guardian
    POST   /quality/guardians/{name}/learn        add a durable rule
    POST   /quality/review                        run a review
    GET    /quality/runs                          run history (summaries)
    GET    /quality/runs/{run_id}                 one full run
    POST   /quality/fix                           fix/propose fixes for a run
    POST   /quality/feedback                      dismiss/suppress/learn/reopen
    GET    /quality/policy                        effective policy
    PUT    /quality/policy                        update the policy

Every run is stored under ``.codx/quality/runs`` so the panel can reload history
after a restart; ``quality_review``/``quality_fix`` return the stored run (with
a Markdown report) instead of inventing a second response shape.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from codx.junior.model.model import CodxUser
from codx.junior.quality.guardians import service
from codx.junior.quality.guardians.profiles import (
    adopt_profile,
    enable_guardian,
    remember,
    update_guardian,
)
from codx.junior.security.user_management import (
    UserSecurityManager,
    get_authenticated_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["quality"])

#: Maximum number of runs returned by the history endpoint.
MAX_RUNS = 100


# -----------------------------------------------------------------------------
# Request bodies
# -----------------------------------------------------------------------------
class GuardianReviewRequest(BaseModel):
    """Body of ``POST /quality/review``."""

    trigger: str = Field(default="manual", description="manual, change, staged or commit")
    scope: str = Field(default="changed", description="changed, project, files or file_type")
    mode: str = Field(default="auto", description="auto, checks or review")
    paths: Optional[List[str]] = Field(default=None, description="Explicit files/filter")
    file_type: Optional[str] = Field(
        default=None,
        description="python, vue, docs, tests, .ts... (comma separated)",
    )
    guardians: Optional[List[str]] = Field(default=None, description="Guardian names")
    base: str = Field(default="HEAD", description="Git reference for scope=changed")
    change_scope: Optional[str] = Field(default=None, description="all, staged or unstaged")
    max_files: Optional[int] = Field(default=None, description="File cap")


class GuardianUpdateRequest(BaseModel):
    """Body of ``PATCH /quality/guardians/{name}`` (all fields optional)."""

    description: Optional[str] = None
    content: Optional[str] = None
    file_match: Optional[str] = None
    tags: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    llm_model: Optional[str] = None
    use_knowledge: Optional[bool] = None
    enabled: Optional[bool] = None
    kind: Optional[str] = None
    triggers: Optional[List[str]] = None
    checks: Optional[List[Dict[str, Any]]] = None
    severity: Optional[str] = None
    blocking_severity: Optional[str] = None
    auto_fix: Optional[bool] = None
    ignore_paths: Optional[List[str]] = None
    polished: bool = True


class GuardianEnableRequest(BaseModel):
    """Body of ``POST /quality/guardians/{name}/enable``."""

    enabled: bool = True


class GuardianLearnRequest(BaseModel):
    """Body of ``POST /quality/guardians/{name}/learn``."""

    title: str = Field(default="", description="Rule title (upsert key)")
    content: str = Field(description="Durable instruction for the guardian")


class GuardianFixRequest(BaseModel):
    """Body of ``POST /quality/fix``."""

    run_id: str = Field(default="", description="Run to fix (latest when empty)")
    finding_ids: Optional[List[str]] = None
    paths: Optional[List[str]] = None
    guardians: Optional[List[str]] = None
    apply: bool = Field(default=False, description="Write the fixes (default false)")
    use_ai: bool = Field(default=True, description="Ask the model for proposals")


class GuardianFeedbackRequest(BaseModel):
    """Body of ``POST /quality/feedback``."""

    run_id: str = ""
    finding_id: str
    action: str = Field(default="dismiss", description="dismiss, suppress, learn or reopen")
    reason: str = ""
    title: str = ""
    content: str = ""
    scope: str = Field(default="finding", description="finding, rule or path")
    learn: bool = False
    suppress: bool = False
    expires_in_days: Optional[int] = None


class GuardianPolicyRequest(BaseModel):
    """Body of ``PUT /quality/policy``: a partial policy."""

    updates: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _session(request: Request):
    """Return the project session attached to the request."""
    session = getattr(request.state, "codx_junior_session", None)
    if session is None or getattr(session, "settings", None) is None:
        raise HTTPException(status_code=400, detail="No active project session")
    return session


def _user_name(session: Any) -> str:
    """Return the username behind a session (or an empty string)."""
    return str(getattr(getattr(session, "user", None), "username", "") or "")


def _bad_request(ex: Exception) -> HTTPException:
    """Map a service exception to an HTTP error (without leaking a traceback)."""
    return HTTPException(status_code=400, detail=str(ex))


def _not_found(message: str) -> HTTPException:
    """Return a 404."""
    return HTTPException(status_code=404, detail=message)


def _require_quality_admin(session: Any, user: CodxUser) -> None:
    """Raise 403 unless *user* may edit guardian profiles/policy.

    The same permission as profile learning: a codx-junior admin, a project
    admin or a project knowledge admin. Reviews and finding dismissals do not
    need it (they are review decisions, not profile changes).
    """
    if not user or not getattr(user, "username", ""):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not UserSecurityManager().can_manage_profile_knowledge(
        user=user, settings=session.settings
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only a project admin, a project knowledge admin or a codx-junior "
                "admin can teach a guardian."
            ),
        )


# -----------------------------------------------------------------------------
# State / guardians
# -----------------------------------------------------------------------------
@router.get("/state")
def api_quality_state(
    request: Request,
    auto_bootstrap: bool = Query(
        True,
        description="Create the missing guardians from the detected stack on first access",
    ),
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Return everything the Quality panel needs.

    The first call of a project auto-creates the guardians from its detected
    stack (unless ``auto_bootstrap=false``). The response carries the guardian
    profiles, their checks, the candidates to adopt, the effective policy and
    the latest runs/findings.

    Args:
        auto_bootstrap: Create the missing guardians on first access.
    """
    session = _session(request)
    try:
        return service.state(session.settings, auto_bootstrap=auto_bootstrap)
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Quality state failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Could not load quality state: {ex}")


@router.get("/guardians")
def api_quality_guardians(
    request: Request,
    include_disabled: bool = Query(True),
    include_candidates: bool = Query(True),
    user: CodxUser = Depends(get_authenticated_user),
):
    """List the guardians and the existing profiles that can be adopted."""
    from codx.junior.quality.guardians.profiles import list_guardians

    session = _session(request)
    return list_guardians(
        session.settings,
        include_disabled=include_disabled,
        include_candidates=include_candidates,
    )


@router.post("/guardians/bootstrap")
def api_quality_bootstrap(
    request: Request,
    adopt: bool = Query(False, description="Adopt existing file_match profiles too"),
    force: bool = Query(False, description="Refresh auto-generated guardians even when unchanged"),
    user: CodxUser = Depends(get_authenticated_user),
):
    """Create/refresh the guardians required by the detected stack."""
    session = _session(request)
    _require_quality_admin(session, user)
    try:
        return service.bootstrap_guardians(session.settings, force=force, adopt=adopt)
    except Exception as ex:
        logger.exception("Quality bootstrap failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Bootstrap failed: {ex}")


@router.post("/guardians/{name}/adopt")
def api_quality_adopt(
    name: str,
    request: Request,
    enabled: bool = Query(True),
    user: CodxUser = Depends(get_authenticated_user),
):
    """Adopt an existing ``file_match`` profile as a quality guardian."""
    session = _session(request)
    _require_quality_admin(session, user)
    try:
        profile = adopt_profile(session.settings, name, enabled=enabled)
        return {"name": name, "guardian": profile.guardian.model_dump() if profile.guardian else {}}
    except ValueError as ex:
        raise _bad_request(ex)


@router.post("/guardians/{name}/enable")
def api_quality_enable(
    name: str,
    payload: GuardianEnableRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Enable or disable a guardian without touching its rules."""
    session = _session(request)
    _require_quality_admin(session, user)
    try:
        profile = enable_guardian(session.settings, name, enabled=payload.enabled)
        return {"name": name, "enabled": bool(profile.guardian and profile.guardian.enabled)}
    except ValueError as ex:
        raise _bad_request(ex)


@router.patch("/guardians/{name}")
def api_quality_update_guardian(
    name: str,
    payload: GuardianUpdateRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Edit a guardian profile (merge semantics; omitted fields are untouched)."""
    session = _session(request)
    _require_quality_admin(session, user)
    values = payload.model_dump(exclude_unset=True)
    try:
        profile = update_guardian(session.settings, name, **values)
        return {
            "name": name,
            "file_match": profile.file_match,
            "guardian": profile.guardian.model_dump() if profile.guardian else {},
        }
    except ValueError as ex:
        raise _bad_request(ex)


@router.post("/guardians/{name}/learn")
def api_quality_learn(
    name: str,
    payload: GuardianLearnRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Teach one guardian a durable rule (``enrich.md``)."""
    session = _session(request)
    _require_quality_admin(session, user)
    try:
        return remember(session.settings, name, payload.title, payload.content)
    except ValueError as ex:
        raise _bad_request(ex)


# -----------------------------------------------------------------------------
# Review / runs
# -----------------------------------------------------------------------------
@router.post("/review")
async def api_quality_review(
    payload: GuardianReviewRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Run a quality review and return the stored run (with a Markdown report)."""
    session = _session(request)
    try:
        return await service.review(
            session.settings,
            session=session,
            trigger=payload.trigger,
            scope=payload.scope,
            mode=payload.mode,
            paths=payload.paths,
            file_type=payload.file_type,
            guardians=payload.guardians,
            base=payload.base,
            change_scope=payload.change_scope,
            max_files=payload.max_files,
            user=_user_name(session) or user.username,
        )
    except ValueError as ex:
        raise _bad_request(ex)
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Quality review failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Review failed: {ex}")


@router.get("/runs")
def api_quality_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=MAX_RUNS),
    user: CodxUser = Depends(get_authenticated_user),
):
    """Return the newest run summaries of the project."""
    from codx.junior.quality.guardians import RunStore

    session = _session(request)
    return {"runs": RunStore(session.settings).list_runs(limit=limit)}


@router.get("/runs/{run_id}")
def api_quality_run(
    run_id: str,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Return one full run (files, checks, findings, verdict)."""
    from codx.junior.quality.guardians import RunStore

    session = _session(request)
    run = RunStore(session.settings).read_run(run_id)
    if not run:
        raise _not_found(f"Quality run not found: {run_id}")
    return run


# -----------------------------------------------------------------------------
# Fix / feedback
# -----------------------------------------------------------------------------
@router.post("/fix")
async def api_quality_fix(
    payload: GuardianFixRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Fix/propose fixes for the open findings of a run."""
    session = _session(request)
    try:
        return await service.fix(
            session.settings,
            run_id=payload.run_id,
            finding_ids=payload.finding_ids,
            paths=payload.paths,
            guardians=payload.guardians,
            apply=payload.apply,
            use_ai=payload.use_ai,
            session=session,
            user=_user_name(session) or user.username,
        )
    except ValueError as ex:
        raise _bad_request(ex)
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("Quality fix failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Fix failed: {ex}")


@router.post("/feedback")
def api_quality_feedback(
    payload: GuardianFeedbackRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Dismiss, suppress, learn from or reopen a finding."""
    session = _session(request)
    try:
        return service.feedback(
            session.settings,
            run_id=payload.run_id,
            finding_id=payload.finding_id,
            action=payload.action,
            reason=payload.reason,
            title=payload.title,
            content=payload.content,
            scope=payload.scope,
            learn=payload.learn,
            suppress=payload.suppress,
            session=session,
            user=user,
            expires_in_days=payload.expires_in_days,
        )
    except PermissionError as ex:
        raise HTTPException(status_code=403, detail=str(ex))
    except ValueError as ex:
        raise _bad_request(ex)


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------
@router.get("/policy")
def api_quality_policy(
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Return the effective guardian policy and the stored overrides."""
    session = _session(request)
    return service.get_policy(session.settings)


@router.put("/policy")
def api_quality_update_policy(
    payload: GuardianPolicyRequest,
    request: Request,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Merge updates into the project guardian policy."""
    session = _session(request)
    _require_quality_admin(session, user)
    try:
        return service.update_policy(session.settings, payload.updates)
    except (ValueError, OSError) as ex:
        raise _bad_request(ex)


# Made with ❤️ by codx-junior
