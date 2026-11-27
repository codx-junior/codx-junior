"""
PAT-authenticated, OpenAI-compatible public API.

A third-party application (a support widget, a CRM, an automation) is given a
personal access token instead of a browser session. The token carries the
permissions and the project/profile binding; this module is the only surface
that consumes it.

Routes
------
``GET  /api/v1/models``            list profiles exposed as models
``GET  /api/v1/profiles``          public definition of those profiles
``GET  /api/v1/projects``          the project bound to the token
``GET  /api/v1/me``                token metadata (handy for a connection test)
``POST /api/v1/chat/completions``  OpenAI-compatible chat completion
``POST /api/v1/completions``       legacy alias (single prompt -> completion)

Every chat goes through :class:`CODXJuniorSession` and therefore through the
same chat engine, profile composition, tools and guardrails as the UI. Public
Exposure Protection is enforced by the token: when
``require_public_exposure_protection`` is set (the default) a profile whose
protection is disabled is never reachable through the token.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from codx.junior.security.pat import (
    PATPrincipal,
    SCOPE_CHAT,
    SCOPE_MODELS,
    SCOPE_PROFILES,
    SCOPE_PROJECTS,
    require_pat_scope,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Bounds on a public request. They protect the process from a single abusive
# client; the profile's own Public Exposure Protection is a separate, semantic
# layer.
MAX_MESSAGES = 200
MAX_MESSAGE_CHARS = 100_000
MAX_TOTAL_CHARS = 300_000


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    error_type: str = "invalid_request_error",
) -> JSONResponse:
    """Return an OpenAI-shaped error so SDK clients surface it correctly."""
    return JSONResponse(
        status_code=int(status_code),
        content={
            "error": {
                "message": str(message),
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
    )


# ---------------------------------------------------------------------------
# Project / profile discovery
# ---------------------------------------------------------------------------


def _project_allowed(principal: PATPrincipal, project) -> bool:
    """Re-check that the token owner may still see *project*."""
    user = principal.user
    if user is None or getattr(user, "role", "") == "admin":
        return True
    from codx.junior.security.user_management import UserSecurityManager

    return bool(
        UserSecurityManager().get_user_project_access(user=user, settings=project)
    )


def _candidate_projects(principal: PATPrincipal) -> List[Any]:
    """Return the projects the token may read (one when it is bound)."""
    from codx.junior.project.project_discover import (
        find_all_projects,
        find_project_by_id,
    )

    if principal.token.project_id:
        project = find_project_by_id(project_id=principal.token.project_id)
        return [project] if project is not None and _project_allowed(principal, project) else []

    projects = list(find_all_projects().values())
    return [project for project in projects if _project_allowed(principal, project)]


def _model_name(project, profile) -> str:
    """Stable model id for a profile, matching the historic API naming."""
    configured = str(getattr(profile.api_settings, "model_name", "") or "").strip()
    return configured or f"{project.project_name}/{profile.name}"


def _is_protected(profile) -> bool:
    """``True`` when the profile has Public Exposure Protection enabled."""
    return bool(getattr(getattr(profile, "guardrails", None), "enabled", False))


def _entry(project, profile) -> Dict[str, Any]:
    """Build the public view of one exposed profile."""
    return {
        "id": _model_name(project, profile),
        "object": "model",
        "created": 0,
        "owned_by": "codx-junior",
        "codx": {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "profile": profile.name,
            "description": getattr(profile, "description", "") or "",
            "public_exposure_protection": _is_protected(profile),
        },
    }


async def _available_entries(
    principal: PATPrincipal,
    include_unprotected: bool = False,
) -> List[Tuple[Any, Any, Dict[str, Any]]]:
    """
    Return ``(project, profile, public_entry)`` for every reachable profile.

    Reads the project/profile catalogues off the event loop: discovery can scan
    the filesystem and profile composition reads Markdown from disk.

    ``include_unprotected`` is used by the chat path so it can answer with a
    specific 403 instead of a generic "model not found"; the discovery routes
    keep unprotected profiles hidden.
    """

    def collect():
        from codx.junior.profiles.profile_manager import ProfileManager

        result: List[Tuple[Any, Any, Dict[str, Any]]] = []
        allowed_profiles = set(principal.token.profiles or [])
        for project in _candidate_projects(principal):
            try:
                profiles = ProfileManager(settings=project).list_all_profiles()
            except Exception as ex:  # a broken profile must not hide the rest
                logger.warning(
                    "Public API: cannot list profiles of %s: %s",
                    getattr(project, "project_name", ""),
                    ex,
                )
                continue
            for profile in profiles:
                if not getattr(profile.api_settings, "active", False):
                    continue
                if allowed_profiles and profile.name not in allowed_profiles:
                    continue
                if (
                    not include_unprotected
                    and principal.token.require_public_exposure_protection
                    and not _is_protected(profile)
                ):
                    # Never advertise a model the token is not allowed to call.
                    continue
                result.append((project, profile, _entry(project, profile)))
        return result

    return await asyncio.to_thread(collect)


def _resolve_entry(
    entries: List[Tuple[Any, Any, Dict[str, Any]]],
    requested: Optional[str],
) -> Optional[Tuple[Any, Any, Dict[str, Any]]]:
    """Resolve the ``model`` field to one exposed profile."""
    wanted = str(requested or "").strip()
    if not wanted:
        return entries[0] if len(entries) == 1 else None

    # Exact public model id.
    exact = [entry for entry in entries if entry[2]["id"] == wanted]
    if len(exact) == 1:
        return exact[0]

    # "project/profile": match the project by name or id, then the profile.
    if "/" in wanted:
        project_part, _, profile_part = wanted.partition("/")
        for project, profile, entry in entries:
            if profile.name != profile_part:
                continue
            if project_part in (
                project.project_id,
                project.project_name,
            ):
                return project, profile, entry

    # Bare profile name when it is unambiguous.
    by_profile = [entry for entry in entries if entry[1].name == wanted]
    if len(by_profile) == 1:
        return by_profile[0]
    return None


# ---------------------------------------------------------------------------
# Message normalization
# ---------------------------------------------------------------------------


def _content_text(content: Any) -> str:
    """Flatten an OpenAI message content (string or text blocks) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # OpenAI multimodal: {"type":"text","text":"..."}; images are not
                # supported by the public endpoint yet.
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _normalize_messages(messages: Any) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Validate the OpenAI ``messages`` array and return a safe copy."""
    if not isinstance(messages, list) or not messages:
        return [], "messages must be a non-empty array"
    if len(messages) > MAX_MESSAGES:
        return [], f"too many messages (limit {MAX_MESSAGES})"
    normalized: List[Dict[str, str]] = []
    total = 0
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            return [], f"messages[{index}] must be an object"
        role = str(item.get("role") or "").strip()
        if role not in ("system", "user", "assistant"):
            return [], f"messages[{index}].role must be system, user or assistant"
        text = _content_text(item.get("content"))
        if len(text) > MAX_MESSAGE_CHARS:
            return [], f"messages[{index}] is too large"
        total += len(text)
        if total > MAX_TOTAL_CHARS:
            return [], "conversation is too large"
        normalized.append({"role": role, "content": text})
    if normalized[-1]["role"] != "user":
        return [], "the last message must be from the user"
    return normalized, None


def _usage_for(messages: Iterable[Dict[str, str]], content: str) -> Dict[str, Any]:
    """Approximate token usage for OpenAI clients that display it."""
    prompt = sum(len(message.get("content") or "") for message in messages)
    prompt_tokens = max(1, prompt // 4)
    completion_tokens = max(1, len(content or "") // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    }


def _guardrail_of(message: Any) -> Optional[Dict[str, Any]]:
    """Return the guardrail verdict stored on the assistant message, if any."""
    meta = getattr(message, "meta_data", None) or {}
    if not isinstance(meta, dict):
        return None
    output = meta.get("guardrail_output")
    verdict = output if isinstance(output, dict) and output.get("action") in (
        "block",
        "refuse",
    ) else meta.get("guardrail")
    if not isinstance(verdict, dict):
        return None
    # The public caller sees the decision, not the detector internals (patterns,
    # excerpts, categories): those stay in the server-side audit trail.
    return {
        key: verdict.get(key)
        for key in (
            "action",
            "blocked",
            "risk_score",
            "highest_severity",
            "refusal_message",
        )
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/v1/models", tags=["public-api"])
async def list_models(
    principal: PATPrincipal = Depends(require_pat_scope(SCOPE_MODELS)),
):
    """List the profiles this token may call, shaped as OpenAI models."""
    entries = await _available_entries(principal)
    return {"object": "list", "data": [entry[2] for entry in entries]}


@router.get("/v1/profiles", tags=["public-api"])
async def list_profiles(
    principal: PATPrincipal = Depends(require_pat_scope(SCOPE_PROFILES)),
):
    """Public definitions of the exposed profiles (no prompt/knowledge)."""
    entries = await _available_entries(principal)
    return {
        "object": "list",
        "data": [
            {
                **entry[2],
                "description": entry[1].description,
                "guardrails": getattr(entry[1].guardrails, "enabled", False),
            }
            for entry in entries
        ],
    }


@router.get("/v1/projects", tags=["public-api"])
async def list_projects(
    principal: PATPrincipal = Depends(require_pat_scope(SCOPE_PROJECTS)),
):
    """The project(s) this token can use."""
    def collect():
        return [
            {
                "project_id": project.project_id,
                "project_name": project.project_name,
            }
            for project in _candidate_projects(principal)
        ]

    return {"object": "list", "data": await asyncio.to_thread(collect)}


@router.get("/v1/me", tags=["public-api"])
async def token_info(principal: PATPrincipal = Depends(require_pat_scope())):
    """Describe the token: a cheap connection test for a third-party app."""
    data = principal.token.public_dict()
    data["project"] = principal.token.project_name or principal.token.project_id
    return {"object": "token", **data}


class _ChatRequestError(Exception):
    """A validation error with an HTTP status and stable code."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def _prepare_chat(principal: PATPrincipal, body: Dict[str, Any]):
    """Resolve and validate the chat request, or raise ``_ChatRequestError``."""
    messages, error = _normalize_messages(body.get("messages"))
    if error:
        raise _ChatRequestError(400, "invalid_messages", error)

    entries = await _available_entries(principal, include_unprotected=True)
    if not entries:
        raise _ChatRequestError(
            403,
            "no_exposed_profiles",
            "This token has no reachable profile. Check the token's project and "
            "profile binding and that the profile is shareable via API.",
        )

    protected_entries = [entry for entry in entries if _is_protected(entry[1])]
    requested = body.get("model")
    if requested:
        entry = _resolve_entry(entries, requested)
    else:
        # No model is only unambiguous when exactly one profile is protected.
        entry = _resolve_entry(protected_entries, None)
    if entry is None:
        if not protected_entries and principal.token.require_public_exposure_protection:
            raise _ChatRequestError(
                403,
                "public_exposure_protection_required",
                "Every exposed profile of this token must enable Public Exposure "
                "Protection before it can be used through a public token.",
            )
        raise _ChatRequestError(
            404,
            "model_not_found",
            "Unknown or unavailable model. Call GET /api/v1/models with this "
            "token to list the models you may use.",
        )
    project, profile, public_entry = entry

    if principal.token.require_public_exposure_protection and not _is_protected(profile):
        raise _ChatRequestError(
            403,
            "public_exposure_protection_required",
            f"Profile '{profile.name}' must enable Public Exposure Protection "
            "before it can be used through a public token.",
        )

    max_input = int(getattr(profile.guardrails, "max_input_chars", 0) or 0)
    user_text = "\n".join(
        message["content"] for message in messages if message["role"] == "user"
    )
    if max_input and len(user_text) > max_input:
        raise _ChatRequestError(
            413,
            "message_too_large",
            f"Profile '{profile.name}' accepts at most {max_input} characters "
            "per request.",
        )
    return messages, project, profile, public_entry


async def _run_chat(principal: PATPrincipal, messages, project, profile, model_id):
    """Run one chat through the normal session/engine and render the answer."""
    from codx.junior.engine import CODXJuniorSession

    session = CODXJuniorSession(settings=project, user=principal.user)
    chat_name = f"api-{profile.name}-{uuid.uuid4().hex[:8]}"
    chat = await session.api_chat_with_project(
        profile_name=profile.name,
        messages=messages,
        chat_name=chat_name,
    )
    if chat is None:
        raise RuntimeError("the chat engine returned no conversation")
    assistant = next(
        (message for message in reversed(chat.messages) if message.role == "assistant"),
        None,
    )
    if assistant is None:
        raise RuntimeError("the assistant produced no answer")
    content = assistant.content or ""
    guardrail = _guardrail_of(assistant)
    response = {
        "id": getattr(chat, "id", "") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": None,
                    "annotations": [],
                },
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_for(messages, content),
        "codx": {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "profile": profile.name,
            "guardrail": guardrail,
            "refused": bool(guardrail and guardrail.get("action") in ("refuse", "block")),
        },
    }
    return response


@router.post("/v1/chat/completions", tags=["public-api"])
async def chat_completions(
    request: Request,
    principal: PATPrincipal = Depends(require_pat_scope(SCOPE_CHAT)),
):
    """OpenAI-compatible chat completion powered by an exposed profile."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json", "The request body is not valid JSON.")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "The request body must be a JSON object.")
    if body.get("stream"):
        return _error(
            400,
            "stream_not_supported",
            "Streaming is not supported on this endpoint yet; send stream=false.",
        )

    try:
        messages, project, profile, public_entry = await _prepare_chat(principal, body)
    except _ChatRequestError as ex:
        return _error(ex.status_code, ex.code, ex.message)

    try:
        response = await _run_chat(
            principal,
            messages,
            project,
            profile,
            body.get("model") or public_entry["id"],
        )
    except Exception as ex:
        # Never leak an internal traceback to a public caller; the server log
        # keeps the detail for the operator.
        logger.exception(
            "Public API chat failed (token=%s project=%s profile=%s): %s",
            principal.token.id,
            getattr(project, "project_id", ""),
            getattr(profile, "name", ""),
            ex,
        )
        return _error(
            500,
            "chat_failed",
            "The assistant could not answer this request.",
            error_type="server_error",
        )
    return JSONResponse(content=response, headers={"cache-control": "no-store"})


@router.post("/v1/completions", tags=["public-api"])
async def legacy_completion(
    request: Request,
    principal: PATPrincipal = Depends(require_pat_scope(SCOPE_CHAT)),
):
    """Legacy completion alias: ``prompt`` is a single user message."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json", "The request body is not valid JSON.")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "The request body must be a JSON object.")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _error(400, "invalid_prompt", "prompt is required.")
    # Reuse the chat path with one user message.
    body = {**body, "messages": [{"role": "user", "content": prompt}]}
    try:
        messages, project, profile, public_entry = await _prepare_chat(principal, body)
    except _ChatRequestError as ex:
        return _error(ex.status_code, ex.code, ex.message)
    try:
        response = await _run_chat(
            principal,
            messages,
            project,
            profile,
            body.get("model") or public_entry["id"],
        )
    except Exception as ex:
        logger.exception("Public API completion failed: %s", ex)
        return _error(
            500,
            "chat_failed",
            "The assistant could not answer this request.",
            error_type="server_error",
        )
    # ``/completions`` clients expect ``text`` rather than a chat choice.
    content = response["choices"][0]["message"]["content"]
    return JSONResponse(
        content={
            "id": response["id"],
            "object": "text_completion",
            "created": response["created"],
            "model": response["model"],
            "choices": [
                {
                    "index": 0,
                    "text": content,
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": response["usage"],
            "codx": response["codx"],
        },
        headers={"cache-control": "no-store"},
    )
