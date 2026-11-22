"""
The workspace API contract for machine clients.

A hosted deployment (a provisioning pipeline, a console, a CI job) does not have
a browser session: it has a URL, a bearer token and a project id. This module is
the single place where that contract is defined, so the API, ``/api/health`` and
the tests all describe the same surface.

What it owns
------------
* **Auth headers.** ``Authorization: Bearer …`` is the standard form and the one
  a gateway/APM proxy preserves; ``authentication: Bearer …`` stays as the
  compatibility alias used by the bundled UI. When both are present
  ``Authorization`` wins (see :func:`extract_auth_token`).
* **The scoped workspace credential.** ``CODX_JUNIOR_WORKSPACE_TOKEN`` is an
  opaque, per-instance API key the hosted console uses to act on the workspace
  without holding the owner's password. It is compared in constant time and
  resolved to the workspace owner (or to a service principal) by
  ``codx.junior.security.user_management``.
* **``codx_path`` resolution.** The task endpoints expect the project's
  ``.codx`` configuration folder. This module also accepts the project root
  (``<project>/.codx`` is resolved internally) and the workspace's default
  project when the parameter is absent, so a client that only knows the instance
  URL can create the first task.
* **The capability block published on ``GET /api/health``** so a pipeline can
  assert the contract instead of hard-coding it.

Nothing here imports the FastAPI application or the project store at module
time: the functions are pure (or do a lazy, optional import) so they can be
tested without booting the API.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication headers
# ---------------------------------------------------------------------------
# ``Authorization`` is the standard spelling and the only one a generic HTTP
# client / reverse proxy / APM agent is guaranteed to forward. ``authentication``
# is what the bundled UI has always sent; it is kept as a compatibility alias.
AUTH_HEADER_PRIMARY = "authorization"
AUTH_HEADER_ALIAS = "authentication"
AUTH_HEADERS: Tuple[str, ...] = (AUTH_HEADER_PRIMARY, AUTH_HEADER_ALIAS)

# Header spellings that a client may mean "there is no token" with. The bundled
# client sends ``Authorization: Bearer undefined`` before the first login.
_MISSING_TOKEN_VALUES = {"", "undefined", "null", "none"}

# Stable machine-readable error codes. Clients branch on these instead of on
# the human message (which may be translated or improved).
AUTH_ERROR_MISSING = "missing_credentials"
AUTH_ERROR_INVALID = "invalid_credentials"

# ---------------------------------------------------------------------------
# Scoped workspace credential
# ---------------------------------------------------------------------------
WORKSPACE_TOKEN_ENV = "CODX_JUNIOR_WORKSPACE_TOKEN"

# Username used for the service principal when no owner account can be resolved.
WORKSPACE_SERVICE_USERNAME = "workspace-service"

# ---------------------------------------------------------------------------
# Documented endpoints
# ---------------------------------------------------------------------------
LOGIN_ENDPOINT = "/api/users/login"
SESSION_ENDPOINT = "/api/users/me/refresh"
PROJECTS_ENDPOINT = "/api/projects"
TASKS_ENDPOINT = "/api/chats/tasks"

# Version of the capability block. Bump it when a field is removed or its
# meaning changes; adding an optional field does not require a bump.
WORKSPACE_API_CONTRACT_VERSION = 1

# ---------------------------------------------------------------------------
# codx_path error payloads (4xx, never a 500)
# ---------------------------------------------------------------------------
CODEX_PATH_REQUIRED = "codx_path_required"
CODEX_PATH_NOT_FOUND = "codx_path_not_found"

CODEX_PATH_REQUIRED_ERROR = {
    "code": CODEX_PATH_REQUIRED,
    "message": (
        "A project is required: pass ?codx_path=<project> or "
        "?codx_path=<project>/.codx, or create a project with "
        f"POST {PROJECTS_ENDPOINT}?project_path=<folder> first. "
        "GET /api/projects lists the codx_path of every project."
    ),
}

CODEX_PATH_NOT_FOUND_ERROR = {
    "code": CODEX_PATH_NOT_FOUND,
    "message": (
        "codx_path does not point to a codx-junior project. Pass either the "
        "project folder or its .codx folder; GET /api/projects returns the "
        "codx_path of every project."
    ),
}


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def workspace_token() -> str:
    """
    Return the configured scoped workspace token (``""`` when disabled).

    The environment is read on every call on purpose: rotating the token is a
    deployment concern (rewrite the settings file and restart the container),
    and reading it here means there is no stale in-process copy.
    """
    return (os.environ.get(WORKSPACE_TOKEN_ENV) or "").strip()


def workspace_token_configured() -> bool:
    """``True`` when a scoped workspace credential is configured."""
    return bool(workspace_token())


def is_workspace_token(token: Optional[str]) -> bool:
    """
    ``True`` when *token* is the configured scoped workspace credential.

    The comparison is constant time (``hmac.compare_digest``) so the response
    time cannot be used to guess the token byte by byte.
    """
    configured = workspace_token()
    candidate = str(token or "").strip()
    if not configured or not candidate:
        return False
    return hmac.compare_digest(
        candidate.encode("utf-8"), configured.encode("utf-8")
    )


def normalize_token(value: Optional[str]) -> str:
    """
    Return the token carried by a header value, or ``""`` when there is none.

    Accepts ``Bearer <token>`` (any case, the standard form), a bare token (the
    historical codx-junior spelling) and the ``Bearer undefined``/``null``
    placeholder the client sends before a login.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split(None, 1)
    if parts and parts[0].lower() in ("bearer", "token"):
        text = parts[1].strip() if len(parts) > 1 else ""
    if text.lower() in _MISSING_TOKEN_VALUES:
        return ""
    return text


def _header_value(headers: Any, name: str) -> str:
    """Read one header from a Starlette request, a ``Headers`` or a mapping."""
    if headers is None:
        return ""
    source = getattr(headers, "headers", headers)
    try:
        return str(source.get(name) or "")
    except (AttributeError, TypeError):
        return ""


def extract_auth_token(request: Any) -> Tuple[str, str]:
    """
    Return ``(token, header_name)`` for the request's credential.

    Precedence: ``Authorization`` (standard) wins over ``authentication`` (the
    UI's historical spelling). A header that is present but carries no usable
    token falls through to the alias, so ``Authorization: Bearer undefined``
    does not mask a valid ``authentication`` header.

    Args:
        request: A Starlette/FastAPI ``Request`` or any object with a
            ``headers`` mapping.

    Returns:
        The token and the header it came from, or ``("", "")``.
    """
    for name in AUTH_HEADERS:
        token = normalize_token(_header_value(request, name))
        if token:
            return token, name
    return "", ""


def auth_error_detail(code: str, message: str) -> Dict[str, str]:
    """Build the stable error body used by every 401 answer."""
    return {"code": code, "message": message}


# ---------------------------------------------------------------------------
# codx_path resolution
# ---------------------------------------------------------------------------


def _clean_path(value: Any) -> str:
    """Normalize a path parameter; ``""`` means "not provided"."""
    raw = str(value or "").strip()
    if raw.lower() in _MISSING_TOKEN_VALUES:
        return ""
    return os.path.expanduser(raw)


def resolve_codx_path(value: Any) -> Optional[str]:
    """
    Resolve a user-supplied path to an existing project ``.codx`` folder.

    Accepted forms, in order:

    * ``<project>/.codx`` (the documented value returned by ``GET /api/projects``)
    * ``<project>/.codx/project.json``
    * ``<project>`` (the project root: ``<project>/.codx`` is resolved here)

    Args:
        value: The ``codx_path`` parameter as received.

    Returns:
        The normalized ``.codx`` path, or ``None`` when it is not a project.
    """
    path = _clean_path(value)
    if not path:
        return None

    # <project>/.codx/project.json
    if os.path.isfile(path) and os.path.basename(path) == "project.json":
        return os.path.normpath(os.path.dirname(path))

    # <project>/.codx  (the folder itself)
    if os.path.isfile(os.path.join(path, "project.json")):
        return os.path.normpath(path)

    # <project>  (the project root)
    nested = os.path.join(path, ".codx")
    if os.path.isfile(os.path.join(nested, "project.json")):
        return os.path.normpath(nested)

    return None


def _codx_path_of(project: Any) -> str:
    """Return the ``codx_path`` of a settings object, or ``""``."""
    return str(getattr(project, "codx_path", "") or "")


def _project_sort_key(project: Any) -> Tuple[int, str, str]:
    """Deterministic order for default-project selection."""
    return (
        0,
        str(getattr(project, "project_name", "") or ""),
        _codx_path_of(project),
    )


def default_codx_path(
    projects: Optional[Mapping[str, Any]] = None,
    allow_scan: bool = False,
) -> Optional[str]:
    """
    Return the workspace's default project ``.codx`` folder.

    The default project is the only project when there is one; with several it
    is the first one (deterministically, by name/path) under
    ``CODX_JUNIOR_PROJECTS_PATH``, falling back to the first visible project.

    Args:
        projects: Pre-loaded ``project_id -> settings`` mapping. When ``None``
            the discovery cache is used (and a scan is started only when
            *allow_scan* is true).
        allow_scan: Wait for a discovery scan when the cache is empty. Request
            handlers may do this; ``/api/health`` must not (it is polled).

    Returns:
        A ``.codx`` path that exists on disk, or ``None``.
    """
    projects_root = ""
    if projects is None:
        try:
            from codx.junior.project.project_discover import (
                find_all_projects,
                get_all_projects_snapshot,
                get_projects_root_path,
                is_project_visible,
            )

            projects_root = get_projects_root_path()
            snapshot = get_all_projects_snapshot()
            if not snapshot and allow_scan:
                snapshot = find_all_projects()
            projects = {
                key: value
                for key, value in (snapshot or {}).items()
                if is_project_visible(value)
            }
        except Exception as ex:  # pragma: no cover - discovery must never raise
            logger.warning("default_codx_path: project discovery failed: %s", ex)
            return None

    candidates = [
        project
        for project in (projects or {}).values()
        if _codx_path_of(project) and resolve_codx_path(_codx_path_of(project))
    ]
    if not candidates:
        return None

    if projects_root:
        root = os.path.normpath(os.path.abspath(projects_root))
        inside = [
            project
            for project in candidates
            if os.path.normpath(os.path.abspath(_codx_path_of(project))).startswith(
                root + os.sep
            )
        ]
        if inside:
            candidates = inside

    chosen = sorted(candidates, key=_project_sort_key)[0]
    return os.path.normpath(_codx_path_of(chosen))


def _is_projects_root(path: str) -> bool:
    """``True`` when *path* is the configured projects root, not a project."""
    if not path:
        return False
    try:
        from codx.junior.project.project_discover import get_projects_root_path

        return os.path.normpath(os.path.abspath(path)) == os.path.normpath(
            os.path.abspath(get_projects_root_path())
        )
    except Exception:  # pragma: no cover - defensive
        return False


def resolve_workspace_codx_path(
    value: Any = None,
    projects: Optional[Mapping[str, Any]] = None,
    allow_scan: bool = False,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Resolve the effective project of a request.

    Args:
        value: The ``codx_path`` parameter (``None``/empty when absent).
        projects: Optional pre-loaded project catalogue (tests, callers that
            already have it).
        allow_scan: Allow a project-discovery scan when resolving the default.

    Returns:
        ``(codx_path, error)``. Exactly one of the two is ``None``: a resolved
        ``.codx`` path, or an actionable 4xx payload (:data:`CODEX_PATH_REQUIRED_ERROR`
        / :data:`CODEX_PATH_NOT_FOUND_ERROR`).
    """
    raw = _clean_path(value)
    if not raw:
        resolved = default_codx_path(projects=projects, allow_scan=allow_scan)
        if resolved:
            return resolved, None
        return None, dict(CODEX_PATH_REQUIRED_ERROR)

    resolved = resolve_codx_path(raw)
    if resolved:
        return resolved, None

    # The hosted console may pass the projects root (the folder that holds every
    # project) instead of a project. When that root is configured, treat it as
    # "use the default project" rather than as an error.
    if _is_projects_root(raw):
        resolved = default_codx_path(projects=projects, allow_scan=allow_scan)
        if resolved:
            return resolved, None

    return None, dict(CODEX_PATH_NOT_FOUND_ERROR)


# ---------------------------------------------------------------------------
# Capability block
# ---------------------------------------------------------------------------


def workspace_api_capabilities() -> Dict[str, Any]:
    """
    Return the capability block published on ``GET /api/health``.

    The block must describe what the *running build* supports, so a hosted
    pipeline can detect an older image instead of guessing. It is computed from
    the same constants the endpoints use, and it never raises: a failure to
    resolve the default project only makes ``default_codx_path`` null.
    """
    try:
        default_path = default_codx_path(allow_scan=False)
    except Exception as ex:  # pragma: no cover - health must never fail
        logger.warning("workspace_api_capabilities: %s", ex)
        default_path = None

    return {
        "contract_version": WORKSPACE_API_CONTRACT_VERSION,
        "auth_headers": list(AUTH_HEADERS),
        "auth_header_precedence": AUTH_HEADER_PRIMARY,
        "scoped_token": True,
        "scoped_token_configured": workspace_token_configured(),
        "scoped_token_env": WORKSPACE_TOKEN_ENV,
        "login_by_email": True,
        "login_endpoint": LOGIN_ENDPOINT,
        "session_endpoint": SESSION_ENDPOINT,
        "projects_endpoint": PROJECTS_ENDPOINT,
        "tasks_endpoint": TASKS_ENDPOINT,
        "codx_path_accepts_project_root": True,
        "tasks_default_project": True,
        "default_codx_path": default_path,
    }
