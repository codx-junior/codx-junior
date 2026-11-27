"""
Personal access token management (authenticated, owner-scoped).

Routes
------
``GET    /api/users/tokens``               list the caller's tokens
``POST   /api/users/tokens``               create a token (returned once)
``DELETE /api/users/tokens/{token_id}``    revoke a token

The token itself is never authenticated here: these endpoints use the normal
session/workspace credential. The public API that *consumes* a PAT lives in
``codx/junior/api/chatGPTLikeApi.py``.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from codx.junior.model.model import CodxUser
from codx.junior.security.pat import (
    PATCreate,
    PATStore,
    SCOPE_CHAT,
    scope_catalog,
)
from codx.junior.security.user_management import (
    UserSecurityManager,
    get_authenticated_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/tokens", tags=["personal-access-tokens"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(status_code: int, code: str, message: str) -> HTTPException:
    """Build the same ``{code, message}`` detail used across the API."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _project_for_token(project_id: str, user: CodxUser):
    """Return the project when the caller may bind a token to it."""
    from codx.junior.project.project_discover import find_project_by_id

    project = find_project_by_id(project_id=project_id)
    if project is None:
        raise _error(404, "project_not_found", "Project not found.")
    access = UserSecurityManager().get_user_project_access(
        user=user, settings=project
    )
    if getattr(user, "role", "") != "admin" and not access:
        raise _error(
            403,
            "project_access_denied",
            "You do not have access to that project.",
        )
    return project


def _validate_profiles(project, profile_names: List[str], require_protection: bool):
    """Ensure the selected profiles exist, are exposed and are protected."""
    if not profile_names:
        return
    from codx.junior.profiles.profile_manager import ProfileManager

    manager = ProfileManager(settings=project)
    missing: List[str] = []
    not_exposed: List[str] = []
    unprotected: List[str] = []
    for name in profile_names:
        profile = manager.read_profile(name)
        if profile is None:
            missing.append(name)
            continue
        if not getattr(profile.api_settings, "active", False):
            not_exposed.append(name)
        if require_protection and not getattr(profile.guardrails, "enabled", False):
            unprotected.append(name)
    if missing:
        raise _error(
            400,
            "profile_not_found",
            "Unknown profile(s): " + ", ".join(sorted(missing)),
        )
    if not_exposed:
        raise _error(
            400,
            "profile_not_exposed",
            "Profile(s) are not exposed through the API: "
            + ", ".join(sorted(not_exposed))
            + ". Enable 'Shareable via API' first.",
        )
    if unprotected:
        raise _error(
            400,
            "public_exposure_protection_required",
            "Profile(s) must enable Public Exposure Protection: "
            + ", ".join(sorted(unprotected)),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
def list_tokens(
    username: Optional[str] = Query(
        default=None,
        description="Admin only: list another account's tokens.",
    ),
    include_revoked: bool = Query(default=True),
    user: CodxUser = Depends(get_authenticated_user),
):
    """List token metadata (never the token or its digest)."""
    target = str(username or user.username or "").strip()
    if not target:
        raise _error(400, "invalid_user", "The authenticated account has no username.")
    if target != user.username and getattr(user, "role", "") != "admin":
        raise _error(403, "access_denied", "Admins only can list another account's tokens.")
    records = PATStore().list(target)
    if not include_revoked:
        records = [record for record in records if not record.is_revoked()]
    return {
        "tokens": [record.public_dict() for record in records],
        "scopes": scope_catalog(),
    }


class TokenResponse(BaseModel):
    """Response of a successful creation: the only time the secret is shown."""

    token: str = Field(description="Clear token. Store it now: it is not recoverable.")
    pat: dict


@router.post("", response_model=TokenResponse)
def create_token(
    payload: PATCreate,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Create a personal access token for the authenticated account."""
    if not str(user.username or "").strip():
        raise _error(400, "invalid_user", "The authenticated account has no username.")
    project = None
    if payload.project_id:
        project = _project_for_token(payload.project_id, user)
    elif SCOPE_CHAT in payload.scopes or "*" in payload.scopes:
        raise _error(
            400,
            "project_required",
            "A token with chat permission must be bound to a project.",
        )

    if payload.profiles and project is None:
        raise _error(
            400,
            "project_required",
            "Selecting profiles requires a project binding.",
        )

    if project is not None:
        _validate_profiles(
            project,
            payload.profiles,
            require_protection=payload.require_public_exposure_protection,
        )

    try:
        token, record = PATStore().create(
            username=user.username,
            name=payload.name,
            scopes=payload.scopes,
            project_id=payload.project_id,
            project_name=project.project_name if project else "",
            profiles=payload.profiles,
            expires_in_days=payload.expires_in_days,
            require_public_exposure_protection=(
                payload.require_public_exposure_protection
            ),
        )
    except ValueError as ex:
        raise _error(400, "invalid_token_request", str(ex)) from ex
    except OSError as ex:
        logger.error("Cannot persist the personal access token: %s", ex)
        raise _error(
            500,
            "token_store_unavailable",
            "The token store is not writable. Check the config folder permissions.",
        ) from ex

    logger.info(
        "PAT created id=%s user=%s project=%s profiles=%s",
        record.id,
        record.username,
        record.project_id or "-",
        ",".join(record.profiles) or "*",
    )
    return {"token": token, "pat": record.public_dict()}


@router.delete("/{token_id}")
def revoke_token(
    token_id: str,
    username: Optional[str] = Query(default=None),
    user: CodxUser = Depends(get_authenticated_user),
):
    """Revoke one token. Admins may revoke another account's token."""
    target = str(username or user.username or "").strip()
    if not target:
        raise _error(400, "invalid_user", "The authenticated account has no username.")
    if target != user.username and getattr(user, "role", "") != "admin":
        raise _error(403, "access_denied", "Admins only can revoke another account's tokens.")

    store = PATStore()
    record = store.find(token_id, username=target)
    if record is None:
        # Do not distinguish "not yours" from "does not exist" for non-admins.
        raise _error(404, "token_not_found", "Token not found.")
    try:
        revoked = store.revoke(
            token_id,
            username=target if getattr(user, "role", "") != "admin" else None,
            allow_any=getattr(user, "role", "") == "admin",
        )
    except OSError as ex:
        logger.error("Cannot revoke the personal access token: %s", ex)
        raise _error(
            500,
            "token_store_unavailable",
            "The token store is not writable. Check the config folder permissions.",
        ) from ex
    if not revoked:
        # Already revoked is a successful no-op: the caller's intent is met.
        return {"revoked": True, "already_revoked": True, "id": token_id}
    logger.info("PAT revoked id=%s user=%s", token_id, record.username)
    return {"revoked": True, "already_revoked": False, "id": token_id}
