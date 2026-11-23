"""
Identity and access helpers for shared computer sessions.

A co-working session is watched by several people at once.  The AI lease on
``BrowserSession`` only separates the AI from the human; these helpers add the
missing human-to-human layer:

* :class:`Requester` is the identity of one browser tab/device (authenticated
  account plus the ``x-participant-id`` header the client sends).
* :func:`apply_control_action` implements the ``/control`` actions shared by
  the ``/api/computers`` and legacy ``/api/browsers`` routes.
* :func:`require_user_control` rejects input from a human that does not own the
  lease, so the UI overlay is not the only protection.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException

from codx.junior.browser.model import DEFAULT_SESSION_CONTROL_MODE

logger = logging.getLogger(__name__)

#: Header the client sends on every request so the backend can tell two tabs
#: of the same account apart (and two guests apart).
PARTICIPANT_HEADER = "x-participant-id"
#: Header used by clients that are not logged in yet.
USERNAME_HEADER = "x-username"


def _payload_value(payload: Any, key: str) -> Any:
    """Read *key* from a pydantic request or a plain dict."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


@dataclass
class Requester:
    """Identity of the human performing one request."""

    participant_id: str
    username: str
    display_name: str
    avatar: Optional[str] = None
    role: str = "user"
    is_admin: bool = False
    authenticated: bool = False

    @property
    def owner_key(self) -> str:
        """Key stored as ``control_owner`` (username, or guest participant id)."""
        return self.username or self.participant_id

    @property
    def lookup_username(self) -> Optional[str]:
        """Username used to match the owner/participants (None for guests)."""
        return self.username or None


def requester_from_request(
    request: Any,
    session_ctx: Any = None,
    payload: Any = None,
) -> Requester:
    """
    Build the caller identity from the authenticated session and headers.

    The token/user on the request is authoritative; the ``x-username`` header
    is only a fallback for anonymous/open installations.  ``x-participant-id``
    is never trusted for authorization: it only distinguishes tabs of the same
    authenticated account (or separate guests).
    """
    user = getattr(session_ctx, "user", None) if session_ctx is not None else None
    username = str(getattr(user, "username", "") or "").strip()
    display_name = str(_payload_value(payload, "display_name") or "").strip()
    avatar = str(getattr(user, "avatar", "") or "").strip() or None
    role = str(getattr(user, "role", "") or "user").strip() or "user"
    authenticated = bool(username)
    if not username and request is not None:
        username = str(request.headers.get(USERNAME_HEADER) or "").strip()
    participant_id = str(_payload_value(payload, "participant_id") or "").strip()
    if not participant_id and request is not None:
        participant_id = str(request.headers.get(PARTICIPANT_HEADER) or "").strip()
    if not participant_id:
        # Stable enough for a single client that never sent the header; two
        # such clients of the same account share the lease, which is the
        # pre-existing behaviour.
        participant_id = username or "anonymous"
    if not display_name:
        display_name = username or "Guest"
    return Requester(
        participant_id=participant_id,
        username=username,
        display_name=display_name,
        avatar=avatar,
        role=role,
        is_admin=(role == "admin"),
        authenticated=authenticated,
    )


def require_user_control(
    session: Any,
    requester: Requester,
    allow_unclaimed: bool = False,
) -> None:
    """
    Raise ``409`` when *requester* may not change the machine right now.

    Read-only endpoints do not call this; it is for actions that change the
    machine (click, type, navigate...).  In ``request`` mode a human must claim
    or be approved before sending input, even while the AI drives; in ``auto``
    mode the click itself is the claim, so unclaimed input is allowed (the UI
    still asks for the lease first).  ``allow_unclaimed`` is for non-input
    operations such as resizing the framebuffer.
    """
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if requester.is_admin or session.user_may_control(
        requester.username,
        requester.participant_id,
        is_admin=requester.is_admin,
    ):
        if requester.is_admin:
            return
        if (
            session.controller != "user"
            and not allow_unclaimed
            and session.control_mode == "request"
        ):
            raise HTTPException(
                status_code=409,
                detail="This session uses request control: take or request control before sending input.",
            )
        return
    owner = session.control_owner_name or session.control_owner or "another participant"
    raise HTTPException(
        status_code=409,
        detail=(
            f"{owner} is controlling this session. "
            "Request control and wait for approval, or take control in auto mode."
        ),
    )


def _status_message(status: str, session: Any, requester: Requester) -> str:
    """Human readable result of a control action."""
    owner = session.control_owner_name or session.control_owner or "another participant"
    messages = {
        "granted": f"{requester.display_name} now controls the session",
        "already_owner": f"{requester.display_name} already controls the session",
        "pending": f"Control request sent to {owner}",
        "approved": f"Control passed to {session.control_owner_name or 'the requester'}",
        "denied": "Control request rejected",
        "not_pending": "There is no pending control request",
        "not_allowed": f"Only {owner} (or an admin) can resolve the control request",
        "requester_gone": "The requester is no longer connected",
        "released": "Control returned to the AI",
        "updated": f"Control mode is now '{session.control_mode}'",
        "status": (
            f"controller={session.controller} mode={session.control_mode} "
            f"owner={session.control_owner or '-'}"
        ),
    }
    return messages.get(status, status)


def apply_control_action(
    session: Any,
    action: str,
    requester: Requester,
    payload: Any = None,
) -> Dict[str, Any]:
    """
    Apply one ``/control`` action and return ``{message, state}``.

    The caller persists the session and broadcasts the change; this function is
    pure so the model rules are easy to test.
    """
    action = str(action or "").strip().lower()
    reason = str(_payload_value(payload, "reason") or "").strip() or None
    request_id = str(_payload_value(payload, "request_id") or "").strip() or None
    mode = _payload_value(payload, "mode")

    if action in ("status", "info", "state"):
        return {
            "message": _status_message("status", session, requester),
            "state": session.control_state(),
        }

    if action in ("set_mode", "control_mode", "mode"):
        result = session.set_session_control_mode(
            mode or DEFAULT_SESSION_CONTROL_MODE,
            username=requester.username,
            participant_id=requester.participant_id,
            is_admin=requester.is_admin,
        )
        status = result.get("status", "updated")
        return {
            "message": _status_message(status, session, requester),
            "state": session.control_state(),
            "result": result,
        }

    if action in ("take_control", "take", "pause", "request_control", "request"):
        result = session.request_session_control(
            requester.participant_id,
            username=requester.username,
            name=requester.display_name,
            reason=reason,
        )
        status = result.get("status", "pending")
        return {
            "message": _status_message(status, session, requester),
            "state": session.control_state(),
            "result": result,
        }

    if action in ("approve_request", "approve", "allow"):
        result = session.approve_session_control(
            username=requester.username,
            participant_id=requester.participant_id,
            is_admin=requester.is_admin,
            request_id=request_id,
        )
        status = result.get("status", "not_pending")
        return {
            "message": _status_message(status, session, requester),
            "state": session.control_state(),
            "result": result,
        }

    if action in ("deny_request", "deny", "reject"):
        result = session.deny_session_control(
            username=requester.username,
            participant_id=requester.participant_id,
            is_admin=requester.is_admin,
            request_id=request_id,
        )
        status = result.get("status", "not_pending")
        return {
            "message": _status_message(status, session, requester),
            "state": session.control_state(),
            "result": result,
        }

    if action in ("give_back", "release", "resume"):
        result = session.release_session_control(
            username=requester.username,
            participant_id=requester.participant_id,
            is_admin=requester.is_admin,
        )
        status = result.get("status", "released")
        return {
            "message": _status_message(status, session, requester),
            "state": session.control_state(),
            "result": result,
        }

    raise HTTPException(status_code=400, detail=f"Unknown control action: {action}")
