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


def _vault_for(settings: Any, scope: str) -> ciso_vault.SecretVault:
    """Return the vault of *scope* for *settings*."""
    policy = resolve_policy(settings)
    if _scope_of(scope) == ciso_vault.SCOPE_GLOBAL:
        return ciso_vault.global_vault(settings, policy)
    return ciso_vault.SecretVault(
        getattr(settings, "abs_project_path", None), policy, scope=scope
    )


def _vault_state() -> Dict[str, Any]:
    """Report whether the encrypted vault can be opened (never a value)."""
    _, error = ciso_vault.SecretVault.fernet()
    if error is None:
        return {"available": True, "detail": ""}
    return {"available": False, "detail": error.render()}


def _entry(meta: ciso_vault.SecretMeta) -> Dict[str, Any]:
    """Return the value-free projection of one secret."""
    payload = meta.public()
    payload["status"] = meta.status
    return payload


@router.get("")
def list_secrets(
    session: CODXJuniorSession = Depends(get_current_session),
    scope: str = Query("all", description="all | project | global"),
) -> Dict[str, Any]:
    """List the declared secrets of the project, the machine, or both.

    Values are never part of the response: only the name, its scope, whether it
    can be resolved right now (``ready`` / ``pending`` / ``missing`` /
    ``unavailable``) and the tools allowed to use it.
    """
    settings = _settings_of(session)
    wanted = (scope or "all").strip().lower()
    if wanted not in ("all",) + tuple(ciso_vault.SCOPES):
        raise HTTPException(status_code=422, detail=f"Unknown scope '{scope}'")

    entries: List[Dict[str, Any]] = []
    seen = set()
    ordered = [wanted] if wanted != "all" else list(ciso_vault.SCOPES)
    for item in ordered:
        vault = _vault_for(settings, item)
        for meta in vault.list(item):
            if wanted == "all" and meta.name in seen:
                continue
            seen.add(meta.name)
            entries.append(_entry(meta))
    entries.sort(key=lambda entry: entry["name"])
    return {
        "scope": wanted,
        "scopes": list(ciso_vault.SCOPES),
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

    Project scope accepts any authenticated user (they already can edit the
    project settings). Global scope is admin only, because it makes the
    credential available to every project.
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
    vault = _vault_for(settings, scope)
    allow_tools = [str(item).strip() for item in (payload.allow_tools or []) if str(item).strip()]
    value = payload.value or ""

    if value:
        error = vault.set_value(
            name,
            value,
            allow_tools=allow_tools or None,
            description=payload.description or "",
            scope=scope,
        )
        if error is not None:
            raise HTTPException(status_code=503, detail=error.render())
    else:
        vault.declare(
            name,
            source=ciso_vault.SOURCE_ENV if (payload.value is None and not allow_tools) else ciso_vault.SOURCE_VAULT,
            description=payload.description or "",
            allow_tools=allow_tools or None,
            status="pending",
            scope=scope,
        )

    meta = vault.load_meta(scope).get(name)
    if meta is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="The secret could not be stored.")
    meta.status = vault.status_of(meta)
    logger.info(
        "Secrets: '%s' saved in scope '%s' by %s",
        name,
        scope,
        getattr(user, "username", None) or "project user",
    )
    return _entry(meta)


@router.delete("/{name}")
def delete_secret(
    name: str,
    session: CODXJuniorSession = Depends(get_current_session),
    user: Optional[CodxUser] = Depends(get_authenticated_user),
    scope: str = Query(ciso_vault.SCOPE_PROJECT),
) -> Dict[str, Any]:
    """Remove a secret and its declaration from one scope."""
    scope = _scope_of(scope)
    if scope == ciso_vault.SCOPE_GLOBAL and getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Global secrets are admin only.")
    settings = _settings_of(session)
    vault = _vault_for(settings, scope)
    if name not in vault.load_meta(scope):
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found in '{scope}'.")
    vault.delete(name, scope=scope)
    logger.info("Secrets: '%s' deleted from scope '%s'", name, scope)
    return {"deleted": name, "scope": scope}
