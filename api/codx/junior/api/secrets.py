"""Secrets API router: add, list and remove secrets from the settings UI.

The browser is the out-of-band channel. An operator types the value in the
settings page; it goes straight into the encrypted vault and **no endpoint ever
returns it** -- the responses carry names, scopes and statuses only. So the value
never reaches the chat history, the knowledge index, the tool cache or a log
line, and an LLM can only ever name it.

Scopes:

``project``  ``<project>/.codx/secrets``  per project, any authenticated user.
``global``   ``~/.codx-junior/secrets``   every project, admins only.

A project secret of the same name wins over a global one, so a project can
override an inherited credential.

Usage from a chat or a tool: the name goes as the WHOLE value of the ``env`` map
entry or of the ``stdin`` argument and the dispatcher resolves it right before
the tool runs (``codx/junior/security/ciso/vault.py``).

Mounted automatically by ``codx.junior.api.discover_routers()`` under ``/api``,
so this router serves ``/api/secrets``.

Made with ❤️ by codx-junior
"""

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from codx.junior.api import get_current_session
from codx.junior.engine import CODXJuniorSession
from codx.junior.model.model import CodxUser
from codx.junior.project.project_discover import get_all_projects_snapshot
from codx.junior.security.ciso import vault as ciso_vault
from codx.junior.security.ciso.policy import resolve_policy
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/secrets", tags=["secrets"])

#: A secret name is an identifier, never a path or a sentence.
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:@\-]{0,127}$")
#: Tool grants offered by default: what an operator usually needs to authenticate.
DEFAULT_ALLOW_TOOLS = [
    "execute_command",
    "docker_exec",
    "docker_run",
    "docker_container",
    "docker_compose_service",
]


class SecretPayload(BaseModel):
    """One secret written from the settings UI.

    ``value`` is optional: without it the secret stays ``pending`` and is reported
    as such, which is how an operator declares a name before wiring the value.
    """

    name: str
    value: Optional[str] = None
    scope: str = ciso_vault.SCOPE_PROJECT
    description: str = ""
    allow_tools: Optional[List[str]] = None
    # Project scope only: the projects that receive the secret. Empty keeps the
    # historical behaviour (the project of the current session).
    projects: Optional[List[str]] = None

    # Project scope only: let the projects INSIDE this one read the secret. Off
    # by default -- a credential is not inherited down the folder tree unless the
    # operator says so. Ignored for the global scope, which every project reads.
    share_with_children: bool = False


def _settings_of(session: Optional[CODXJuniorSession]) -> Any:
    """Return the project settings of *session* (``None`` when unavailable)."""
    return getattr(session, "settings", None) if session is not None else None


def _scope_of(scope: str) -> str:
    """Validate and normalise a scope name."""
    candidate = (scope or ciso_vault.SCOPE_PROJECT).strip().lower()
    if candidate not in ciso_vault.SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scope '{scope}'. Use one of: {', '.join(ciso_vault.SCOPES)}.",
        )
    return candidate


def _known_projects() -> Dict[str, Any]:
    """Return the discovered projects by id (never raises)."""
    try:
        return dict(get_all_projects_snapshot() or {})
    except Exception as ex:
        logger.warning("Secrets: cannot read the project snapshot: %s", ex)
        return {}


def _project_targets(settings: Any, project_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Resolve the projects a project-scoped secret is written to.

    Args:
        settings: Settings of the current session; its project is the default.
        project_ids: Ids chosen in the settings UI.

    Returns:
        One ``project_id`` / ``project_name`` / ``abs_project_path`` per target.

    Raises:
        HTTPException: 422 when an id is not a discovered project. Writing into
            an arbitrary path would let a request create a vault anywhere on the
            filesystem, so the id must resolve through project discovery.
    """
    wanted = [str(item).strip() for item in (project_ids or []) if str(item or "").strip()]
    if not wanted:
        return [{
            "project_id": getattr(settings, "project_id", "") or "",
            "project_name": getattr(settings, "project_name", "") or "",
            "abs_project_path": getattr(settings, "abs_project_path", None),
        }]
    known = _known_projects()
    targets: List[Dict[str, Any]] = []
    for project_id in wanted:
        found = known.get(project_id)
        if found is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown project '{project_id}'. Pick one of the discovered projects.",
            )
        targets.append({
            "project_id": project_id,
            "project_name": getattr(found, "project_name", "") or project_id,
            "abs_project_path": getattr(found, "abs_project_path", None),
        })
    return targets


def _vault_for(settings: Any, scope: str,
               project_path: Optional[str] = None) -> ciso_vault.SecretVault:
    """Return the vault of *scope*, optionally rooted at another project."""
    policy = resolve_policy(settings)
    if _scope_of(scope) == ciso_vault.SCOPE_GLOBAL:
        return ciso_vault.global_vault(settings, policy)
    return ciso_vault.SecretVault(
        project_path or getattr(settings, "abs_project_path", None), policy, scope=scope
    )


def _vault_state() -> Dict[str, Any]:
    """Report whether the encrypted vault can be opened (never a value)."""
    _, error = ciso_vault.SecretVault.fernet()
    if error is None:
        return {"available": True, "detail": ""}
    return {"available": False, "detail": error.render()}


def _entry(meta: ciso_vault.SecretMeta,
           project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the value-free projection of one secret."""
    payload = meta.public()
    payload["status"] = meta.status
    if meta.inherited_from:
        payload["inherited_from"] = meta.inherited_from
    if project:
        payload["project_id"] = project.get("project_id", "")
        payload["project_name"] = project.get("project_name", "")
    return payload


@router.get("/tools")
def list_secret_tools(
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """List every tool a secret can be granted to (name, description, tags).

    The settings UI shows this catalogue instead of asking the operator to type a
    tool name: nobody knows the whole registry by heart, and a typo silently
    grants nothing.
    """
    from codx.junior.tools import TOOLS

    tools = []
    for entry in TOOLS:
        function = (entry.get("tool_json") or {}).get("function") or {}
        name = function.get("name") or ""
        if not name:
            continue
        tools.append({
            "name": name,
            "description": " ".join(str(function.get("description") or "").split())[:200],
            "tags": list(entry.get("tags") or []),
        })
    tools.sort(key=lambda item: item["name"])
    policy = getattr(resolve_policy(_settings_of(session)), "secrets", None)
    return {
        "tools": tools,
        "denied_tools": list(getattr(policy, "denied_tools", None) or []),
        "default_allow_tools": DEFAULT_ALLOW_TOOLS,
    }


@router.get("/projects")
def list_secret_projects() -> Dict[str, Any]:
    """List the projects a project-scoped secret can be written to."""
    projects = [
        {
            "project_id": project_id,
            "project_name": getattr(settings, "project_name", "") or project_id,
            "abs_project_path": getattr(settings, "abs_project_path", ""),
        }
        for project_id, settings in sorted(
            _known_projects().items(),
            key=lambda item: (getattr(item[1], "project_name", "") or item[0]).lower(),
        )
    ]
    return {"projects": projects}


@router.get("")
def list_secrets(
    session: CODXJuniorSession = Depends(get_current_session),
    scope: str = Query("all", description="all | project | global"),
    projects: str = Query("", description="comma separated project ids (project scope)"),
) -> Dict[str, Any]:
    """List the declared secrets of the project(s), the machine, or both.

    Values are never part of the response: only the name, its scope, whether it
    can be resolved right now (``ready`` / ``pending`` / ``missing`` /
    ``unavailable``) and the tools allowed to use it.
    """
    settings = _settings_of(session)
    wanted = (scope or "all").strip().lower()
    if wanted not in ("all",) + tuple(ciso_vault.SCOPES):
        raise HTTPException(status_code=422, detail=f"Unknown scope '{scope}'")

    selected = [item.strip() for item in (projects or "").split(",") if item.strip()]
    entries: List[Dict[str, Any]] = []

    # Project rows first: a project secret shadows a global one with the same
    # name, so the shadowed global row is dropped when both scopes are listed.
    if wanted in ("all", ciso_vault.SCOPE_PROJECT):
        for target in _project_targets(settings, selected):
            vault = _vault_for(settings, ciso_vault.SCOPE_PROJECT, target["abs_project_path"])
            own = {meta.name for meta in vault.list(ciso_vault.SCOPE_PROJECT)}
            for meta in vault.list(ciso_vault.SCOPE_PROJECT):
                entries.append(_entry(meta, target))
            # Secrets a PARENT project shares with this one. Shown as inherited
            # rows so the operator sees what this project can actually use; a
            # local declaration of the same name wins and is not repeated.
            for name, inherited in sorted(vault.inherited_meta().items()):
                if name in own:
                    continue
                inherited.status = vault.status_of(inherited)
                payload = _entry(inherited, target)
                payload["inherited"] = True
                entries.append(payload)

    if wanted in ("all", ciso_vault.SCOPE_GLOBAL):
        shadowed = {entry["name"] for entry in entries} if wanted == "all" else set()
        vault = _vault_for(settings, ciso_vault.SCOPE_GLOBAL)
        for meta in vault.list(ciso_vault.SCOPE_GLOBAL):
            if meta.name in shadowed:
                continue
            entries.append(_entry(meta))

    entries.sort(key=lambda entry: (entry["name"], entry.get("project_name", "")))
    return {
        "scope": wanted,
        "scopes": list(ciso_vault.SCOPES),
        "projects": selected,
        "secrets": entries,
        "vault": _vault_state(),
        "default_allow_tools": DEFAULT_ALLOW_TOOLS,
        "resolve_help": (
            "Use the name as a handle in a tool call: the WHOLE value of the "
            "'env' map entry or of the 'stdin' argument. A handle anywhere else "
            "in a call that carries such a parameter is refused, and the tool "
            "does not run."
        ),
    }


@router.post("")
def save_secret(
    payload: SecretPayload,
    session: CODXJuniorSession = Depends(get_current_session),
    user: Optional[CodxUser] = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """Create or update one secret. The value is written, never returned.

    Project scope writes to every selected project (one vault each); global scope
    is a single machine-wide store. Project scope accepts any authenticated user
    (they already can edit the project settings). Global scope is admin only,
    because it makes the credential available to every project.
    """
    name = (payload.name or "").strip()
    if not NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=422,
            detail="A secret name must look like an identifier: letters, digits, "
                   "'_', '-', '.', ':' or '@' (e.g. REGISTRY_PASSWORD).",
        )
    scope = _scope_of(payload.scope)
    if scope == ciso_vault.SCOPE_GLOBAL and getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Global secrets are admin only.")

    settings = _settings_of(session)
    allow_tools = [str(item).strip() for item in (payload.allow_tools or []) if str(item).strip()]
    value = payload.value or ""
    # Only a project secret has children to share with: a global secret is
    # already readable from every project, so the flag is dropped there.
    share_with_children = (
        bool(payload.share_with_children) and scope == ciso_vault.SCOPE_PROJECT
    )
    targets: List[Optional[Dict[str, Any]]] = [None]
    if scope == ciso_vault.SCOPE_PROJECT:
        targets = list(_project_targets(settings, payload.projects))

    saved: List[Dict[str, Any]] = []
    for target in targets:
        vault = _vault_for(settings, scope, (target or {}).get("abs_project_path"))
        if value:
            error = vault.set_value(
                name,
                value,
                allow_tools=allow_tools or None,
                description=payload.description or "",
                scope=scope,
                share_with_children=share_with_children,
            )
            if error is not None:
                raise HTTPException(status_code=503, detail=error.render())
        else:
            # Declared without a value: always a vault secret, left pending until
            # the operator stores the value. Never the environment source, or the
            # name would look resolvable when nothing backs it.
            vault.declare(
                name,
                source=ciso_vault.SOURCE_VAULT,
                description=payload.description or "",
                allow_tools=allow_tools or None,
                status="pending",
                scope=scope,
                share_with_children=share_with_children,
            )
        meta = vault.load_meta(scope).get(name)
        if meta is None:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail="The secret could not be stored.")
        meta.status = vault.status_of(meta)
        saved.append(_entry(meta, target))

    logger.info(
        "Secrets: '%s' saved in scope '%s' for %s by %s",
        name,
        scope,
        ", ".join(entry.get("project_name") or "machine" for entry in saved),
        getattr(user, "username", None) or "project user",
    )
    return {"secrets": saved, "saved": [entry["name"] for entry in saved]}


@router.delete("/{name}")
def delete_secret(
    name: str,
    session: CODXJuniorSession = Depends(get_current_session),
    user: Optional[CodxUser] = Depends(get_authenticated_user),
    scope: str = Query(ciso_vault.SCOPE_PROJECT),
    project_id: str = Query("", description="project id (project scope)"),
) -> Dict[str, Any]:
    """Remove a secret and its declaration from one scope."""
    scope = _scope_of(scope)
    if scope == ciso_vault.SCOPE_GLOBAL and getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Global secrets are admin only.")
    settings = _settings_of(session)
    target = None
    if scope == ciso_vault.SCOPE_PROJECT and project_id:
        target = _project_targets(settings, [project_id])[0]
    vault = _vault_for(settings, scope, (target or {}).get("abs_project_path"))
    if name not in vault.load_meta(scope):
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found in '{scope}'.")
    vault.delete(name, scope=scope)
    logger.info(
        "Secrets: '%s' deleted from scope '%s' (%s)",
        name,
        scope,
        (target or {}).get("project_name") or "machine",
    )
    return {
        "deleted": name,
        "scope": scope,
        "project_id": (target or {}).get("project_id", ""),
    }
