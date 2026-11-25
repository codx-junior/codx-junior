"""
Browser login store REST API — portable site sessions and credentials.

    GET    /api/browser-store/sessions                    saved site sessions
    POST   /api/browser-store/sessions                    capture from a session
    POST   /api/browser-store/sessions/<id>/apply         restore into a session
    DELETE /api/browser-store/sessions/<id>
    GET    /api/browser-store/credentials
    POST   /api/browser-store/credentials
    PUT    /api/browser-store/credentials/<id>
    DELETE /api/browser-store/credentials/<id>
    POST   /api/browser-store/credentials/<id>/reveal     value for the human UI

The router is discovered automatically by ``codx.junior.api.discover_routers``
(modules in this package exporting ``router``), so ``app.py`` stays untouched.

The store is project-scoped: any session of the project can apply any saved
site session or use any credential, which is the point (switch browser, keep the
login). Values are kept encrypted by ``codx.junior.browser.store``; the AI never
receives a password from the tools (see ``browse_action(action="login")``).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from codx.junior.browser.store import BrowserStore, StoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browser-store", tags=["browser"])


class SavedSession(BaseModel):
    """One saved site session (metadata only, never the cookies)."""

    id: str
    name: str
    origins: List[str] = Field(default_factory=list)
    cookie_count: int = 0
    origin_count: int = 0
    size: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


class SavedSessionsResponse(BaseModel):
    count: int
    sessions: List[SavedSession]


class CaptureSessionRequest(BaseModel):
    session_id: str = Field(description="Running browser session to capture from")
    name: str = Field(description="Name for the saved site session")


class ApplySessionRequest(BaseModel):
    session_id: str = Field(description="Running browser session to restore into")


class ApplySessionResponse(BaseModel):
    applied: bool
    session: str
    state: str
    cookies: int = 0
    localStorage: int = 0


class PreviewResponse(BaseModel):
    """What a capture of the running browser would save right now."""

    page_count: int = 0
    cookie_count: int = 0
    origins: List[str] = Field(default_factory=list)
    suggested_name: str = ""


class Credential(BaseModel):
    """Credential metadata (the password stays in the encrypted payload)."""

    id: str
    name: str
    url: str = ""
    username: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


class CredentialsResponse(BaseModel):
    count: int
    credentials: List[Credential]


class CredentialCreate(BaseModel):
    name: str
    username: str = ""
    password: str
    url: str = ""


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    url: Optional[str] = None


class RevealResponse(BaseModel):
    name: str
    password: str


async def get_session_context(request: Request):
    """Dependency: the project session behind the request."""
    session = getattr(request.state, "codx_junior_session", None)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session",
        )
    return session


def _browser_manager(session):
    manager = getattr(session, "browser_manager", None)
    if manager:
        return manager
    from codx.junior.browser.browser_manager import BrowserManager
    return BrowserManager(session.settings)


def _store(session) -> BrowserStore:
    try:
        return BrowserStore(session.settings)
    except StoreError as ex:
        raise HTTPException(status_code=400, detail=ex.render())


def _username(session) -> Optional[str]:
    user = getattr(session, "user", None)
    return (getattr(user, "username", "") or "") or None


def _running_session(manager, session_id: str):
    browser_session = manager.get_session(session_id)
    if not browser_session:
        raise HTTPException(status_code=404, detail="Session not found")
    if browser_session.status != "running":
        raise HTTPException(status_code=409, detail="The browser session is not running")
    return browser_session


async def _runtime(manager, browser_session):
    try:
        return await manager.get_runtime(browser_session)
    except Exception as ex:
        raise HTTPException(status_code=409, detail=f"The browser is not reachable: {ex}")


@router.get(
    "/preview",
    response_model=PreviewResponse,
    summary="What the running browser would save right now",
)
async def preview_session(request: Request, session_id: str) -> PreviewResponse:
    """
    Read-only preview of the current browser: open sites and cookie count.

    The sessions tab uses it to show what is actually open ("3 sites · 12
    cookies") and to prefill the name, so the user does not have to guess that
    the browser's tabs are not sessions until they press save.
    """
    from codx.junior.browser.store import suggest_name

    session = await get_session_context(request)
    manager = _browser_manager(session)
    browser_session = _running_session(manager, session_id)
    runtime = await _runtime(manager, browser_session)
    try:
        state = await runtime.run("export_state", force=True)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Could not read the browser: {ex}")
    origins: List[str] = []
    for entry in (state or {}).get("origins") or []:
        if isinstance(entry, dict) and entry.get("origin"):
            origin = str(entry["origin"])
            if origin not in origins:
                origins.append(origin)
    return PreviewResponse(
        page_count=len(origins),
        cookie_count=len((state or {}).get("cookies") or []),
        origins=origins,
        suggested_name=suggest_name(origins),
    )


@router.get(
    "/sessions",
    response_model=SavedSessionsResponse,
    summary="List saved site sessions",
)
async def list_saved_sessions(request: Request) -> SavedSessionsResponse:
    """Return the saved site sessions of the project (no cookie values)."""
    session = await get_session_context(request)
    store = _store(session)
    try:
        sessions = store.sessions()
    except StoreError as ex:
        raise HTTPException(status_code=400, detail=ex.render())
    return SavedSessionsResponse(
        count=len(sessions),
        sessions=[SavedSession(**entry) for entry in sessions],
    )


@router.post(
    "/sessions",
    response_model=SavedSession,
    summary="Capture the current login state of a session",
)
async def capture_session(request: Request, payload: CaptureSessionRequest) -> SavedSession:
    """
    Export cookies + localStorage from a running session and save them by name.

    Applying that session later (to the same browser, another Chromium-family
    image, or after a container restart) restores the login without typing the
    credentials again.
    """
    session = await get_session_context(request)
    manager = _browser_manager(session)
    browser_session = _running_session(manager, payload.session_id)
    store = _store(session)
    runtime = await _runtime(manager, browser_session)
    try:
        state = await runtime.run("export_state", force=True)
        meta = store.save_session(payload.name, state, _username(session))
    except StoreError as ex:
        raise HTTPException(status_code=400, detail=ex.detail or ex.code)
    except Exception as ex:
        logger.exception("capture_session failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Could not capture the session: {ex}")
    logger.info(
        "capture_session: %s captured %d cookies / %d origins",
        payload.name, meta.get("cookie_count", 0), meta.get("origin_count", 0),
    )
    return SavedSession(**meta)


@router.post(
    "/sessions/{store_id}/apply",
    response_model=ApplySessionResponse,
    summary="Restore a saved site session into a running browser",
)
async def apply_session(
    request: Request,
    store_id: str,
    payload: ApplySessionRequest,
) -> ApplySessionResponse:
    """Apply cookies + localStorage of a saved session to a running browser."""
    session = await get_session_context(request)
    manager = _browser_manager(session)
    browser_session = _running_session(manager, payload.session_id)
    store = _store(session)
    try:
        meta, state = store.session_state(store_id, _username(session))
    except StoreError as ex:
        raise HTTPException(status_code=404, detail=ex.detail or ex.code)
    runtime = await _runtime(manager, browser_session)
    try:
        receipt = await runtime.run("import_state", state, force=True)
    except Exception as ex:
        logger.exception("apply_session failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Could not restore the session: {ex}")
    logger.info(
        "apply_session: %s restored %s cookies / %s items",
        meta.get("name"), receipt.get("cookies"), receipt.get("localStorage"),
    )
    return ApplySessionResponse(
        applied=True,
        session=meta.get("name") or store_id,
        state=store_id,
        cookies=int(receipt.get("cookies") or 0),
        localStorage=int(receipt.get("localStorage") or 0),
    )


@router.delete(
    "/sessions/{store_id}",
    summary="Delete a saved site session",
)
async def delete_session(request: Request, store_id: str) -> dict:
    session = await get_session_context(request)
    store = _store(session)
    if not store.delete_session(store_id, _username(session)):
        raise HTTPException(status_code=404, detail="Saved session not found")
    return {"deleted": True}


@router.get(
    "/credentials",
    response_model=CredentialsResponse,
    summary="List saved credentials (no passwords)",
)
async def list_credentials(request: Request) -> CredentialsResponse:
    session = await get_session_context(request)
    store = _store(session)
    try:
        credentials = store.credentials()
    except StoreError as ex:
        raise HTTPException(status_code=400, detail=ex.render())
    return CredentialsResponse(
        count=len(credentials),
        credentials=[Credential(**entry) for entry in credentials],
    )


@router.post(
    "/credentials",
    response_model=Credential,
    summary="Store a credential",
)
async def create_credential(request: Request, payload: CredentialCreate) -> Credential:
    session = await get_session_context(request)
    store = _store(session)
    try:
        meta = store.save_credential(
            payload.name,
            payload.username,
            payload.password,
            payload.url,
            _username(session),
        )
    except StoreError as ex:
        raise HTTPException(status_code=400, detail=ex.detail or ex.code)
    return Credential(**meta)


@router.put(
    "/credentials/{credential_id}",
    response_model=Credential,
    summary="Update a credential",
)
async def update_credential(
    request: Request,
    credential_id: str,
    payload: CredentialUpdate,
) -> Credential:
    session = await get_session_context(request)
    store = _store(session)
    try:
        meta = store.update_credential(
            credential_id,
            name=payload.name,
            username=payload.username,
            password=payload.password,
            url=payload.url,
            user=_username(session),
        )
    except StoreError as ex:
        status_code = 404 if ex.code == "not_found" else 400
        raise HTTPException(status_code=status_code, detail=ex.detail or ex.code)
    return Credential(**meta)


@router.delete(
    "/credentials/{credential_id}",
    summary="Delete a credential",
)
async def delete_credential(request: Request, credential_id: str) -> dict:
    session = await get_session_context(request)
    store = _store(session)
    if not store.delete_credential(credential_id, _username(session)):
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"deleted": True}


@router.post(
    "/credentials/{credential_id}/reveal",
    response_model=RevealResponse,
    summary="Reveal a password to the human UI",
)
async def reveal_credential(request: Request, credential_id: str) -> RevealResponse:
    """
    Return the password for the copy button.

    This is a human action (authenticated project session) and it is audited by
    the store; the AI reads credentials through ``browse_action(action="login")``,
    which never returns the value.
    """
    session = await get_session_context(request)
    store = _store(session)
    try:
        meta, password = store.credential(credential_id, _username(session))
    except StoreError as ex:
        raise HTTPException(status_code=404, detail=ex.detail or ex.code)
    return RevealResponse(name=meta.get("name") or credential_id, password=password)


# Made with ❤️ by codx-junior
