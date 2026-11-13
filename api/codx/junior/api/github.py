"""
GitHub integration API.

Connects a codx-junior project to a GitHub repository:

- Read: the integration status (enabled, token configured, masked token) and
  the repositories the configured token can see.
- Write: ``POST /github/connect`` points a git remote of the workspace to a
  GitHub repository.

The integration is **feature flagged**: ``github_enabled`` is false by default
and every endpoint answers a clear 400 until the project enables it, so an
installation that does not use GitHub never calls ``api.github.com``.

The GitHub token is a secret:

- it is read from the project settings (``github_token``, which may be a
  ``${ENV_VAR}`` reference) or from the environment,
- it is never returned by the API (only ``has_github_token`` and
  ``masked_github_token`` are exposed),
- it is never written to ``.git/config``: ``connect`` stores the plain HTTPS
  URL and remote operations authenticate with the token per command (see
  ``GitEngine._token_args`` / ``_credential_args``).

Routes are mounted under ``/api`` by the dynamic router loader
(``codx.junior.api.discover_routers``), so the final paths look like
``/api/github/repos``.

Made with ❤️ by codx-junior
"""

import logging

from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from codx.junior.engine.git_conflicts import GitConflictEngine
from codx.junior.misc.github_client import (
    GITHUB_API_BASE,
    MAX_PAGES,
    PER_PAGE,
    GitHubClient,
    GitHubError,
    GitHubRepository,
)
from codx.junior.model.model import CodxUser
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter()


# -----------------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------------


class GitHubConnectRequest(BaseModel):
    """Payload to connect a workspace remote to a GitHub repository.

    ``repo`` accepts ``owner/name``, a clone URL or a browser URL. The remote
    URL is always stored credential free: the token travels with each remote
    command, never in ``.git/config``.
    """

    repo: str = Field(description="Repository: owner/name, clone URL or browser URL")
    remote_name: str = Field(
        default="origin", description="Git remote to point at the repository"
    )
    branch: Optional[str] = Field(
        default=None, description="Default branch (defaults to the repository one)"
    )


class GitHubConnectResponse(BaseModel):
    """Result of connecting a workspace remote to a GitHub repository.

    ``url`` is the credential-free URL that was written to the git config:
    there is deliberately no field carrying a token.
    """

    success: bool = True
    remote_name: str = ""
    url: str = ""
    previous_url: str = ""
    action: str = ""
    branch: str = ""
    repo: Optional[GitHubRepository] = None
    output: str = ""
    errors: List[str] = []


class GitHubReposResponse(BaseModel):
    """The repositories the configured token can see."""

    success: bool = True
    enabled: bool = True
    total: int = 0
    repos: List[GitHubRepository] = []


class GitHubStatusResponse(BaseModel):
    """GitHub integration status, safe to expose to any client.

    The token itself is never part of the answer: ``has_github_token`` tells
    whether credentials are configured and ``masked_github_token`` only keeps
    the last 4 characters so an admin can recognize the stored token.
    """

    success: bool = True
    enabled: bool = False
    has_github_token: bool = False
    masked_github_token: str = ""
    api_base: str = GITHUB_API_BASE


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _settings(request: Request):
    """
    Return the settings of the project bound to the request.

    Args:
        request: Incoming HTTP request holding the session in its state.

    Returns:
        The project settings.

    Raises:
        HTTPException: 400 when the request has no project session/settings.
    """
    session = getattr(request.state, "codx_junior_session", None)
    if not session:
        raise HTTPException(
            status_code=400,
            detail="CODXJuniorSession not found in request state.",
        )
    settings = getattr(session, "settings", None)
    if not settings:
        raise HTTPException(status_code=400, detail="Project settings not available.")
    return settings


def _ensure_github_ready(settings) -> None:
    """
    Reject the request when the GitHub integration cannot be used.

    Args:
        settings: Project settings.

    Raises:
        HTTPException: 400 with an actionable message when the integration is
            disabled or when no token is configured.
    """
    if not settings.is_github_enabled():
        raise HTTPException(
            status_code=400,
            detail=(
                "The GitHub integration is disabled for this project. Enable "
                "'github_enabled' in the project settings to use it."
            ),
        )
    if not settings.has_github_token():
        raise HTTPException(
            status_code=400,
            detail=(
                "No GitHub token is configured. Set the project 'github_token' "
                "setting (a ${ENV_VAR} reference is recommended) to use the "
                "GitHub integration."
            ),
        )


def _build_client(settings) -> GitHubClient:
    """
    Build the GitHub client for the given project settings.

    Args:
        settings: Project settings.

    Returns:
        Client authenticated with the project GitHub token.
    """
    return GitHubClient(
        token=settings.get_github_token(), api_base=settings.get_github_api_base()
    )


def _git_engine(request: Request) -> GitConflictEngine:
    """
    Build the git engine (which owns the remote helpers) for the session.

    Args:
        request: Incoming HTTP request holding the session in its state.

    Returns:
        Engine bound to the request session.
    """
    session = getattr(request.state, "codx_junior_session", None)
    if not session:
        raise HTTPException(
            status_code=400,
            detail="CODXJuniorSession not found in request state.",
        )
    return GitConflictEngine(session=session)


def credential_free_url(url: str) -> str:
    """
    Return a remote URL with any user info (credential) removed.

    GitHub clone URLs do not embed a token, but a user may paste one that does:
    the credential is stripped instead of being written to ``.git/config``.

    Args:
        url: Remote URL.

    Returns:
        The same URL without ``user:password@``.

    Example:
        >>> credential_free_url("https://x-access-token:ghp_1@github.com/a/b.git")
        'https://github.com/a/b.git'
        >>> credential_free_url("git@github.com:a/b.git")
        'git@github.com:a/b.git'
    """
    value = (url or "").strip()
    if not value or "://" not in value:
        return value

    parts = urlsplit(value)
    if not parts.netloc:
        return value
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _http_error_from_github(error: GitHubError) -> HTTPException:
    """
    Convert a client error into the HTTP error answered by the API.

    Args:
        error: Error raised by the GitHub client (never carries the token).

    Returns:
        HTTPException with the GitHub status when it is a client one, 502
        otherwise.
    """
    status_code = error.status_code
    if not 400 <= status_code <= 499:
        status_code = 502
    return HTTPException(status_code=status_code, detail=error.message)


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.get("/github/status", response_model=GitHubStatusResponse)
def api_github_status(
    request: Request, user: CodxUser = Depends(get_authenticated_user)
):
    """
    Return the GitHub integration status of the project.

    Safe to expose to any client: the token is never part of the answer, only
    whether it is configured and a masked hint of it.

    Returns:
        Dict with 'success', 'enabled', 'has_github_token',
        'masked_github_token' and 'api_base'.

    Example:
        GET /api/github/status

        200 OK
        {"success": true, "enabled": true, "has_github_token": true,
         "masked_github_token": "••••••••a1b2",
         "api_base": "https://api.github.com"}
    """
    settings = _settings(request)
    return GitHubStatusResponse(
        enabled=settings.is_github_enabled(),
        has_github_token=settings.has_github_token(),
        masked_github_token=settings.masked_github_token(),
        api_base=settings.get_github_api_base(),
    )


@router.get("/github/repos", response_model=GitHubReposResponse)
async def api_github_repos(
    request: Request,
    per_page: int = Query(PER_PAGE, ge=1, le=PER_PAGE),
    max_pages: int = Query(1, ge=1, le=MAX_PAGES),
    visibility: Optional[str] = Query(
        None, description="Optional filter: all, public or private"
    ),
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    List the repositories the configured GitHub token can see.

    Pages are requested with ``per_page`` entries (100 by default, GitHub's
    maximum) until a short page is returned or ``max_pages`` is reached.

    Args:
        per_page: Entries per page (1-100).
        max_pages: Maximum pages requested, so the listing is always bounded.
        visibility: Optional ``all``/``public``/``private`` filter.

    Returns:
        Dict with 'success', 'enabled', 'total' and 'repos' (owner/name, private
        flag, default branch, clone URL...). No token field is ever returned.

    Raises:
        HTTPException: 400 when the integration is disabled or has no token,
            4xx/502 when GitHub rejects the request.

    Example:
        GET /api/github/repos?per_page=100&max_pages=2

        200 OK
        {"success": true, "enabled": true, "total": 1, "repos": [{
          "full_name": "acme/app", "private": true, "default_branch": "main",
          "clone_url": "https://github.com/acme/app.git"}]}
    """
    settings = _settings(request)
    _ensure_github_ready(settings)

    if visibility and visibility not in ("all", "public", "private"):
        raise HTTPException(
            status_code=400, detail="visibility must be 'all', 'public' or 'private'"
        )

    client = _build_client(settings)
    try:
        repos = await client.list_repositories(
            per_page=per_page, max_pages=max_pages, visibility=visibility
        )
    except GitHubError as error:
        raise _http_error_from_github(error)
    finally:
        await client.aclose()

    return GitHubReposResponse(total=len(repos), repos=repos)


@router.post("/github/connect", response_model=GitHubConnectResponse)
async def api_github_connect(
    request: Request,
    payload: GitHubConnectRequest,
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Point a workspace git remote at a GitHub repository.

    The repository metadata is read with the project token (which validates the
    access), then the remote is created/updated with the **credential-free**
    HTTPS URL. Nothing is pushed, fetched or staged, and the token is never
    written to ``.git/config``: remote operations authenticate per command.

    Args:
        payload: Repository (``owner/name``, clone URL or browser URL), the
            remote name (``origin`` by default) and an optional default branch.

    Returns:
        Dict with 'success', 'remote_name', 'url' (credential free),
        'previous_url', 'action' ('added'/'updated'), 'branch', 'repo',
        'output' and 'errors'.

    Raises:
        HTTPException: 400 when the integration is disabled/has no token, the
            repository reference is invalid or the git config write fails;
            4xx/502 when GitHub rejects the request.

    Example:
        POST /api/github/connect {"repo": "acme/app", "remote_name": "origin"}

        200 OK
        {
          "success": true,
          "remote_name": "origin",
          "url": "https://github.com/acme/app.git",
          "previous_url": "",
          "action": "added",
          "branch": "main",
          "repo": {"full_name": "acme/app", "private": true, "default_branch": "main"},
          "output": "",
          "errors": []
        }
    """
    settings = _settings(request)
    _ensure_github_ready(settings)

    remote_name = (payload.remote_name or "origin").strip() or "origin"

    client = _build_client(settings)
    try:
        repo = await client.get_repository(payload.repo)
    except GitHubError as error:
        raise _http_error_from_github(error)
    finally:
        await client.aclose()

    url = credential_free_url(repo.clone_url)
    if not url:
        raise HTTPException(
            status_code=502,
            detail="GitHub did not return a clone URL for the repository.",
        )

    result = _git_engine(request).set_remote_url(remote=remote_name, url=url)
    if not result.get("success"):
        detail = "; ".join(
            str(error) for error in (result.get("errors") or []) if error
        ) or "Could not configure the git remote"
        raise HTTPException(status_code=400, detail=detail)

    return GitHubConnectResponse(
        remote_name=result.get("remote", remote_name),
        url=result.get("url", url),
        previous_url=result.get("previous_url", ""),
        action=result.get("action", ""),
        branch=(payload.branch or repo.default_branch or ""),
        repo=repo,
        output=result.get("output", ""),
        errors=[],
    )


# Made with ❤️ by codx-junior
