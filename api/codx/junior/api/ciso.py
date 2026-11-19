"""CISO API: the security posture, scans, findings, rules and policy of a project.

This router is the backend of the CISO view. It is a thin, typed layer over
:mod:`codx.junior.security.ciso.service` — the service owns persistence,
background execution and policy validation, so an HTTP request can never apply
a policy the gates would refuse to load.

Endpoints
    ``GET    /api/ciso/overview``                 posture, gates, secrets, repo
    ``GET    /api/ciso/scans``                    scan history (newest first)
    ``POST   /api/ciso/scans``                    start a scan (202)
    ``GET    /api/ciso/scans/{scan_id}``          one scan (with findings)
    ``DELETE /api/ciso/scans/{scan_id}``          delete a scan record
    ``POST   /api/ciso/scans/{scan_id}/cancel``   cooperative cancellation
    ``GET    /api/ciso/scans/{scan_id}/findings`` filtered/paged findings
    ``GET    /api/ciso/rules``                    detector catalogue + state
    ``PUT    /api/ciso/rules/{rule_id}``          enable/silence one rule
    ``GET    /api/ciso/policy``                   effective + stored + defaults
    ``PUT    /api/ciso/policy``                   merge/validate/persist policy
    ``POST   /api/ciso/policy/reset``             drop project overrides
    ``POST   /api/ciso/chat``                     open a chat with the ciso profile
    ``GET    /api/ciso/activity``                 gate journal + statistics

Scans are project-scoped (``?codx_path=`` or the ``x-codx-path`` header, as every
other router). Mutations are authenticated when authentication is configured;
the project settings are the security boundary, like the secrets screen.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from codx.junior.model.model import CodxUser
from codx.junior.security.ciso import service as ciso_service
from codx.junior.security.ciso.models import parse_severity, severity_rank
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ciso", tags=["ciso"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    """One security analysis request."""

    scope: str = Field(
        default="project",
        description="project | changes | staged | paths",
    )
    paths: Optional[List[str]] = Field(
        default=None,
        description="Project-relative files or folders for the 'paths' scope",
    )
    max_files: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Upper bound of files read by this scan",
    )
    min_severity: Optional[str] = Field(
        default=None,
        description="Severity floor recorded with the scan (info..critical)",
    )


class RuleToggle(BaseModel):
    """Enable or silence one detector rule."""

    enabled: bool = Field(description="False adds the rule to the policy ignore list")


class CisoChatRequest(BaseModel):
    """Open (or seed) a conversation with the CISO profile."""

    scan_id: str = Field(default="", description="Scan to discuss; empty uses the latest completed scan")
    finding_keys: List[str] = Field(
        default_factory=list,
        description=(
            "Selected findings as 'rule_id|path|line' (the identity shown by the "
            "findings panel). Empty discusses the scan summary / project posture."
        ),
    )
    question: str = Field(
        default="",
        description="What to ask the CISO. Empty uses the assessment prompt.",
    )
    auto_analyze: bool = Field(
        default=True,
        description="Start the CISO turn immediately instead of waiting for the user message",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _session(request: Request):
    """Return the session injected by the codx_path middleware."""
    session = getattr(request.state, "codx_junior_session", None)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="codx_path is required (query string or x-codx-path header)",
        )
    return session


def _settings(request: Request):
    """Return the project settings of the request session."""
    return _session(request).settings


def _map_error(ex: Exception) -> HTTPException:
    """Translate a service error into the matching HTTP response."""
    if isinstance(ex, ciso_service.ScanNotFound):
        return HTTPException(status_code=404, detail=str(ex))
    if isinstance(ex, ciso_service.ScanBusyError):
        return HTTPException(status_code=409, detail=str(ex))
    if isinstance(ex, ciso_service.UnknownRuleError):
        return HTTPException(status_code=404, detail=str(ex))
    if isinstance(ex, ciso_service.CisoServiceError):
        return HTTPException(status_code=400, detail=str(ex))
    logger.exception("CISO API: unexpected error: %s", ex)
    return HTTPException(status_code=500, detail="The security service failed")


#: Background CISO assessment tasks started by a chat request. Keeping a
#: reference prevents the asyncio task from being garbage-collected mid-turn.
_CISO_CHAT_TASKS: set = set()


def _finding_key(finding: Dict[str, Any]) -> str:
    """Return the stable identity used by the findings panel."""
    return f"{finding.get('rule_id', '')}|{finding.get('path') or ''}|{finding.get('line') or 0}"


def _selected_scan_findings(
    settings: Any,
    scan_id: str,
    finding_keys: List[str],
) -> tuple:
    """Return ``(record, findings)`` for a CISO chat request.

    The scan store is the authority: the client sends keys, never the finding
    bodies (which could be stale or edited). When no keys are supplied the high
    and critical findings of the latest completed scan are used.
    """
    store = ciso_service.ScanStore(settings)
    record = None
    if scan_id:
        record = store.load(scan_id)
    else:
        for candidate in store.list(limit=10):
            if candidate.status == ciso_service.SCAN_STATUS_COMPLETED:
                record = candidate
                break
    if record is None:
        return None, []
    findings = list(record.findings or [])
    if not findings:
        return record, []
    wanted = {str(key) for key in (finding_keys or []) if key}
    if wanted:
        selected = [finding for finding in findings if _finding_key(finding) in wanted]
        findings = selected
    if not wanted or not findings:
        findings = [
            finding
            for finding in findings
            if severity_rank(parse_severity(finding.get("severity"))) >= 3
        ][:40]
    findings.sort(key=lambda item: -severity_rank(parse_severity(item.get("severity"))))
    return record, findings[:40]


def _ciso_chat_context(
    settings: Any,
    scan_id: str,
    finding_keys: List[str],
    question: str,
) -> str:
    """Build the user message that starts a CISO review conversation."""
    from codx.junior.security.ciso import rules as ciso_rules

    record, findings = _selected_scan_findings(settings, scan_id, finding_keys)
    policy = ciso_service.ciso_policy.resolve_policy(settings)
    lines = [
        "# CISO review request",
        "",
        f"Project: **{settings.project_name or 'project'}**",
        f"Policy: block >= `{policy.block_above.value}`, approval >= `{policy.approval_above.value}`, "
        f"warn >= `{policy.warn_above.value}`, fail_closed={policy.fail_closed}",
    ]
    if record is not None:
        summary = record.summary or {}
        counts = summary.get("counts") or {}
        lines += [
            f"Scan: `{record.scan_id}` · {record.scope} · {record.status}",
            f"Scanned: {summary.get('files_scanned', 0)} file(s); findings: "
            f"critical={counts.get('critical', 0)}, high={counts.get('high', 0)}, "
            f"medium={counts.get('medium', 0)}, low={counts.get('low', 0)}",
        ]
    else:
        try:
            overview = ciso_service.overview(settings)
            posture = overview.get("posture") or {}
            counts = posture.get("counts") or {}
            lines += [
                "No completed scan is available; this is a general project security review.",
                f"Posture: score={posture.get('score')}, grade={posture.get('grade')}, "
                f"critical={counts.get('critical', 0)}, high={counts.get('high', 0)}",
            ]
        except Exception:
            lines.append("No scan or posture snapshot is available.")
    lines += [
        "",
        "## What I need from you",
        "For each finding below, decide whether it is:",
        "",
        "1. **Real issue** — the code/config is wrong; explain the risk and the smallest safe fix.",
        "2. **Rule defect / false positive** — the detector is too broad or mis-scoped; name the rule,",
        "   the path pattern and the policy change (ignore path/rule or threshold) that would fix it.",
        "3. **Uncertain** — state what evidence is missing and which project/security tool to run next.",
        "",
        "Use the security tools to verify instead of guessing. Do not modify files or rules yet:",
        "first give your triage and recommendation. Only create a task when I explicitly ask.",
        "",
    ]
    if findings:
        lines.append("## Findings to review")
        for index, finding in enumerate(findings, start=1):
            location = finding.get("path") or "unknown path"
            if finding.get("line"):
                location += f":{finding['line']}"
            lines += [
                "",
                f"### {index}. [{finding.get('severity', 'unknown')}] `{finding.get('rule_id', 'unknown')}` — {location}",
                f"- Title: {finding.get('title') or finding.get('reason') or '—'}",
                f"- Category: {finding.get('category') or '—'}",
                f"- Reason: {finding.get('reason') or '—'}",
            ]
            if finding.get("excerpt"):
                excerpt = str(finding["excerpt"]).replace("`", "'")
                lines.append(f"- Redacted excerpt: `{excerpt[:800]}`")
            if finding.get("remediation"):
                lines.append(f"- Detector remediation: {finding['remediation']}")
        lines += [
            "",
            "## Output format",
            "A table with columns: finding, verdict (real/rule/uncertain), confidence,",
            "evidence and recommended action. Then list the policy/rule changes you would make,",
            "separated from the code fixes.",
        ]
    else:
        lines += [
            "## No individual findings selected",
            "Review the project's current posture and the detector catalogue, then tell me which",
            "rules or policy thresholds look wrong for this project and what you would verify first.",
        ]
    question = str(question or "").strip()
    if question:
        lines += ["", "## My question", question[:4000]]
    else:
        lines += [
            "",
            "## My question",
            "Are these real issues, or are any of them problems with the CISO rules/policy?",
        ]
    return "\n".join(lines)[:60000]


async def _run_ciso_turn(session: Any, chat: Any) -> None:
    """Run the first CISO turn in the background (best effort)."""
    try:
        await session.chat_with_project(chat=chat)
    except Exception as ex:  # pragma: no cover - background safety
        logger.exception("CISO chat turn failed: %s", ex)
    finally:
        try:
            await session.save_chat(chat)
        except Exception:  # pragma: no cover
            logger.debug("CISO chat: could not persist the final chat", exc_info=True)


@router.post("/chat", status_code=201)
async def ciso_chat(
    request: Request,
    payload: CisoChatRequest,
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Open a conversation with the **ciso** profile about findings/rules.

    The chat is a real project chat with ``profiles=['ciso']``: it appears in
    the chat list and the CISO profile has its security tools and instructions.
    The selected findings are embedded (already redacted) so the conversation
    starts with evidence instead of a screenshot. By default the first CISO
    turn starts immediately; the response returns the chat id so the UI can
    open it while the assessment streams.
    """
    from codx.junior.db import Chat, Message

    session = _session(request)
    settings = session.settings
    context = _ciso_chat_context(
        settings,
        payload.scan_id,
        payload.finding_keys,
        payload.question,
    )
    if payload.scan_id:
        short = payload.scan_id.replace("scan-", "")[-18:]
        name = f"CISO review · {short}"
    else:
        name = "CISO security review"
    if payload.question:
        name = f"{name} · {payload.question[:60]}"
    # Assign the doc_id BEFORE the first save. ChatManager merges message lists
    # by doc_id on every save; a seeded message without a stable id would be
    # saved once with a generated id and then appended again as a second copy
    # when the background turn persists the chat.
    seed_message = Message(role="user", content=context)
    seed_message.doc_id = seed_message.doc_id or str(uuid.uuid4())
    chat = Chat(
        name=name[:160],
        mode="chat",
        board="CISO",
        column="Review",
        profiles=["ciso"],
        tags=["ciso", "security", "review"],
        description="Conversation with the CISO profile about findings and rule quality.",
        owner_project_id=settings.project_id,
        project_id=settings.project_id,
        users=[user.username] if user and getattr(user, "username", "") else [],
        messages=[seed_message],
    )
    chat = session.get_chat_manager().save_chat(chat)
    if payload.auto_analyze:
        task = asyncio.create_task(_run_ciso_turn(session, chat))
        _CISO_CHAT_TASKS.add(task)
        task.add_done_callback(_CISO_CHAT_TASKS.discard)
    try:
        chat_payload = chat.model_dump(mode="json")
    except TypeError:  # pragma: no cover - pydantic v1 compatibility
        chat_payload = chat.dict()
    return {"chat": chat_payload}


# ---------------------------------------------------------------------------
# Overview / activity
# ---------------------------------------------------------------------------
@router.get("/overview")
def ciso_overview(
    request: Request,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Security posture of the project in one request.

    Returns the latest scan and its severity/category counters, the posture
    score and trend, the gate journal statistics, the value-free secret
    inventory and the repository data-plane hygiene.
    """
    try:
        return ciso_service.overview(_settings(request))
    except Exception as ex:
        raise _map_error(ex)


@router.get("/activity")
def ciso_activity(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Recent CISO gate decisions (``ciso_verdict`` journal entries)."""
    try:
        return ciso_service.activity(_settings(request), limit=limit)
    except Exception as ex:
        raise _map_error(ex)


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------
@router.get("/scans")
def ciso_scans(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Scan history of the project, newest first."""
    try:
        return ciso_service.list_scans(_settings(request), limit=limit, offset=offset)
    except Exception as ex:
        raise _map_error(ex)


@router.post("/scans", status_code=202)
def ciso_start_scan(
    request: Request,
    payload: Optional[ScanRequest] = Body(default=None),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Start a background security analysis and return its queued record."""
    try:
        request_payload = payload or ScanRequest()
        return ciso_service.start_scan(
            _settings(request),
            scope=request_payload.scope,
            paths=request_payload.paths,
            max_files=request_payload.max_files,
            min_severity=request_payload.min_severity,
        )
    except Exception as ex:
        raise _map_error(ex)


@router.get("/scans/{scan_id}")
def ciso_scan(
    request: Request,
    scan_id: str,
    include_findings: bool = Query(default=True),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Return one scan record (findings included unless ``include_findings=0``)."""
    try:
        return ciso_service.get_scan(
            _settings(request), scan_id, include_findings=include_findings
        )
    except Exception as ex:
        raise _map_error(ex)


@router.delete("/scans/{scan_id}")
def ciso_delete_scan(
    request: Request,
    scan_id: str,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Delete a scan record (a running scan is cancelled first)."""
    try:
        deleted = ciso_service.delete_scan(_settings(request), scan_id)
        return {"deleted": deleted, "scan_id": scan_id}
    except Exception as ex:
        raise _map_error(ex)


@router.post("/scans/{scan_id}/cancel")
def ciso_cancel_scan(
    request: Request,
    scan_id: str,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Request cancellation of a running scan."""
    try:
        return ciso_service.cancel_scan(_settings(request), scan_id)
    except Exception as ex:
        raise _map_error(ex)


@router.get("/scans/{scan_id}/findings")
def ciso_scan_findings(
    request: Request,
    scan_id: str,
    severity: Optional[str] = Query(default=None, description="Minimum severity"),
    category: Optional[str] = Query(default=None),
    query: str = Query(default="", description="Free text over rule, path and reason"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Filtered and paged findings of one scan."""
    try:
        return ciso_service.scan_findings(
            _settings(request),
            scan_id,
            severity=severity,
            category=category,
            query=query,
            limit=limit,
            offset=offset,
        )
    except Exception as ex:
        raise _map_error(ex)


# ---------------------------------------------------------------------------
# Rules & policy
# ---------------------------------------------------------------------------
@router.get("/rules")
def ciso_rules(
    request: Request,
    include_disabled: bool = Query(default=True),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Detector catalogue with the policy state of every rule."""
    try:
        return ciso_service.rule_catalog(
            _settings(request), include_disabled=include_disabled
        )
    except Exception as ex:
        raise _map_error(ex)


@router.put("/rules/{rule_id}")
def ciso_toggle_rule(
    request: Request,
    rule_id: str,
    payload: RuleToggle,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Enable or silence one rule in the project policy."""
    try:
        return ciso_service.set_rule_enabled(
            _settings(request), rule_id, payload.enabled
        )
    except Exception as ex:
        raise _map_error(ex)


@router.get("/policy")
def ciso_policy(
    request: Request,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Effective policy, stored overrides, defaults and editable options."""
    try:
        return ciso_service.policy_state(_settings(request))
    except Exception as ex:
        raise _map_error(ex)


@router.put("/policy")
def ciso_update_policy(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Merge a partial policy into the project settings and persist it."""
    try:
        return ciso_service.update_policy(_settings(request), payload)
    except Exception as ex:
        raise _map_error(ex)


@router.post("/policy/reset")
def ciso_reset_policy(
    request: Request,
    _user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Remove the project policy overrides (back to the defaults)."""
    try:
        return ciso_service.reset_policy(_settings(request))
    except Exception as ex:
        raise _map_error(ex)
