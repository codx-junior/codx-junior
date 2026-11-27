"""
Personal access tokens (PATs) for third-party applications.

A PAT is an opaque credential a third-party application (a support widget, a
CRM, an automation) presents to codx-junior instead of a browser session. The
token is *scoped*: it names what the application may do (chat, list models,
read profiles/projects) and, for chat, which project and profiles it may use.
It is never a substitute for the owner's password or workspace token.

Storage
-------
Tokens are persisted in ``<config folder>/pat_tokens.json`` with mode ``0600``.
Only the SHA-256 digest of the token is stored; the clear value is returned
exactly once, when it is created. The file is written atomically and guarded by
a process-wide lock, so an API worker cannot truncate another worker's write.

Token format
------------
``cxj_pat_<token id>_<secret>``

The id is a stable handle used to look a token up (and to revoke it) without
scanning hashes; the secret is 32 random bytes rendered as hex. Verification
hashes the whole token and compares it in constant time.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from codx.junior.security.workspace_api import normalize_token
from codx.junior.utils.file_lock import interprocess_lock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token shape and scopes
# ---------------------------------------------------------------------------

#: Prefix that makes a PAT distinguishable from a session JWT or the scoped
#: workspace token, both in logs and in the authentication code.
PAT_PREFIX = "cxj_pat_"

#: File that stores the token catalogue, relative to the config folder.
PAT_STORE_FILE = "pat_tokens.json"

# Permissions. They are deliberately small and resource-oriented: a token for a
# support assistant only needs ``chat:write`` (and possibly ``models:read``).
SCOPE_CHAT = "chat:write"
SCOPE_MODELS = "models:read"
SCOPE_PROFILES = "profiles:read"
SCOPE_PROJECTS = "projects:read"
SCOPE_WILDCARD = "*"

#: Every scope this build understands. ``*`` grants all of them.
PAT_SCOPES: Tuple[str, ...] = (
    SCOPE_CHAT,
    SCOPE_MODELS,
    SCOPE_PROFILES,
    SCOPE_PROJECTS,
)
DEFAULT_PAT_SCOPES: Tuple[str, ...] = (SCOPE_CHAT,)

#: Scope descriptions used by the UI/API catalogue.
PAT_SCOPE_CATALOG: Tuple[Dict[str, str], ...] = (
    {
        "id": SCOPE_CHAT,
        "label": "Chat with profiles",
        "description": "Call the OpenAI-compatible chat endpoint with an exposed profile.",
    },
    {
        "id": SCOPE_MODELS,
        "label": "List models",
        "description": "Discover the profiles exposed as models.",
    },
    {
        "id": SCOPE_PROFILES,
        "label": "Read profiles",
        "description": "Read the public definition of the exposed profiles.",
    },
    {
        "id": SCOPE_PROJECTS,
        "label": "Read projects",
        "description": "Read the project the token is bound to.",
    },
)

#: Header aliases accepted for a PAT. ``Authorization`` stays the standard one;
#: ``x-api-key`` is what many third-party SDKs use when they cannot set a
#: bearer value.
PAT_HEADERS: Tuple[str, ...] = ("authorization", "authentication", "x-api-key")

#: Reasons reported by :meth:`PATStore.verify_with_reason`.
PAT_INVALID = "invalid"
PAT_REVOKED = "revoked"
PAT_EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    """Serialize an aware datetime in a stable, sortable ISO form."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp; return ``None`` for empty/invalid values."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PATRecord(BaseModel):
    """One stored token. ``token_hash`` never leaves the server."""

    id: str = Field(description="Stable token handle (also embedded in the token).")
    name: str = Field(default="", description="Human label chosen by the owner.")
    username: str = Field(description="Owner account.")
    token_hash: str = Field(description="SHA-256 digest of the whole token.")
    prefix: str = Field(
        default="",
        description="Short, non-secret prefix shown by the UI.",
    )
    scopes: List[str] = Field(
        default_factory=list,
        description="Permissions granted to the application.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Project the token is bound to (None = any project the owner can see).",
    )
    project_name: Optional[str] = Field(default="")
    profiles: List[str] = Field(
        default_factory=list,
        description="Profile names the token may use (empty = any exposed profile).",
    )
    require_public_exposure_protection: bool = Field(
        default=True,
        description=(
            "Refuse to use a profile whose Public Exposure Protection is disabled."
        ),
    )
    created_at: str = Field(default="")
    expires_at: Optional[str] = Field(default=None)
    last_used_at: Optional[str] = Field(default=None)
    revoked_at: Optional[str] = Field(default=None)

    @field_validator("scopes", "profiles", mode="before")
    @classmethod
    def _normalize_string_list(cls, value):
        """Accept ``None`` and de-duplicate values while preserving order."""
        if value is None:
            return []
        result: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    def is_revoked(self) -> bool:
        """``True`` when the token was revoked."""
        return bool(self.revoked_at)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """``True`` when the token has an expiry in the past (or is unreadable)."""
        expires = _parse_iso(self.expires_at)
        if self.expires_at and expires is None:
            # A corrupted expiry must fail closed: never turn a dated token
            # into a permanent one.
            return True
        return bool(expires and (now or utc_now()) >= expires)

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """``True`` when the token may still authenticate."""
        return not self.is_revoked() and not self.is_expired(now)

    def has_scope(self, scope: str) -> bool:
        """``True`` when this token grants *scope* (or the wildcard)."""
        return SCOPE_WILDCARD in self.scopes or scope in self.scopes

    def public_dict(self) -> Dict[str, Any]:
        """Return the token metadata safe to send to a client."""
        data = self.model_dump(mode="json", exclude={"token_hash"})
        data["active"] = self.is_active()
        return data


class PATPrincipal(BaseModel):
    """Authenticated caller of a public API request."""

    token: PATRecord
    user: Optional[Any] = Field(default=None, exclude=True)

    @property
    def username(self) -> str:
        """Owner of the token."""
        return self.token.username

    @property
    def project_id(self) -> Optional[str]:
        """Project bound to the token, if any."""
        return self.token.project_id

    def require_project(self) -> str:
        """Return the bound project id or raise an actionable 403."""
        if not self.token.project_id:
            raise HTTPException(
                status_code=403,
                detail=pat_error(
                    "project_binding_required",
                    "This token is not bound to a project. Recreate it from the "
                    "project where the public assistant lives.",
                ),
            )
        return self.token.project_id


class PATCreate(BaseModel):
    """Body of ``POST /api/users/tokens``."""

    name: str = Field(default="", max_length=120)
    scopes: List[str] = Field(default_factory=lambda: list(DEFAULT_PAT_SCOPES))
    project_id: Optional[str] = None
    profiles: List[str] = Field(default_factory=list)
    expires_in_days: Optional[int] = Field(default=90, ge=0, le=3650)
    require_public_exposure_protection: bool = True

    @field_validator("scopes", "profiles", mode="before")
    @classmethod
    def _normalize_string_list(cls, value):
        """Accept a single string as a one-element list and dedupe values."""
        if value is None:
            return []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        result: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def pat_error(code: str, message: str) -> Dict[str, str]:
    """Build the stable error body used by the PAT endpoints."""
    return {"code": code, "message": message}


def _unauthorized(code: str, message: str) -> HTTPException:
    """Build a 401 that advertises the bearer scheme."""
    return HTTPException(
        status_code=401,
        detail=pat_error(code, message),
        headers={"WWW-Authenticate": 'Bearer realm="codx-junior"'},
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

#: One lock for every store instance in this process: the API builds a store per
#: request, but the file is shared.
_STORE_LOCK = threading.RLock()

#: Re-writing the file on every request would turn a chat into disk I/O.
LAST_USED_WRITE_INTERVAL_SECONDS = 60


def default_pat_store_path() -> str:
    """Return the token catalogue path for the current installation."""
    folder = (
        os.environ.get("CODX_JUNIOR_PAT_FOLDER")
        or os.environ.get("CODX_JUNIOR_CONFIG_FOLDER")
        or os.environ.get("HOME", "/root")
    )
    return os.path.join(os.path.expanduser(folder), PAT_STORE_FILE)


def is_pat_token(token: Optional[str]) -> bool:
    """``True`` when *token* has the PAT prefix."""
    return str(token or "").strip().startswith(PAT_PREFIX)


def parse_pat(token: str) -> Optional[Tuple[str, str]]:
    """Return ``(token_id, secret)`` from a PAT, or ``None`` when malformed."""
    text = str(token or "").strip()
    if not text.startswith(PAT_PREFIX) or len(text) > 512:
        return None
    remainder = text[len(PAT_PREFIX):]
    token_id, separator, secret = remainder.partition("_")
    if not separator or not token_id or not secret:
        return None
    return token_id, secret


def token_digest(token: str) -> str:
    """Return the SHA-256 digest stored for *token*."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def normalize_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    """
    Validate and normalize a requested scope list.

    ``*`` is expanded to the concrete scopes except for the public response,
    where it is kept as ``*``. Unknown scopes are rejected so a typo cannot
    silently produce a token with fewer permissions than the user expects.
    """
    requested = [str(scope or "").strip() for scope in (scopes or [])]
    requested = [scope for scope in requested if scope]
    if not requested:
        requested = list(DEFAULT_PAT_SCOPES)
    if SCOPE_WILDCARD in requested:
        return [SCOPE_WILDCARD]
    result: List[str] = []
    for scope in requested:
        if scope not in PAT_SCOPES:
            raise ValueError(
                f"unknown scope '{scope}' (expected one of "
                f"{', '.join(PAT_SCOPES)} or '{SCOPE_WILDCARD}')"
            )
        if scope not in result:
            result.append(scope)
    return result


class PATStore:
    """Read/write access to the PAT catalogue."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_pat_store_path()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> List[PATRecord]:
        """Read every record; a missing/corrupt file means "no tokens"."""
        with _STORE_LOCK:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except FileNotFoundError:
                return []
            except (OSError, json.JSONDecodeError) as ex:
                logger.error("Cannot read PAT store %s: %s", self.path, ex)
                return []

        raw = payload.get("tokens") if isinstance(payload, dict) else payload
        records: List[PATRecord] = []
        for item in raw or []:
            try:
                records.append(PATRecord(**item))
            except Exception as ex:  # forward/backward compatible catalogue
                logger.warning("Ignoring invalid PAT record: %s", ex)
        return records

    def _save(self, records: List[PATRecord]) -> None:
        """Atomically write *records* with owner-only permissions."""
        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(folder, exist_ok=True)
        payload = {
            "version": 1,
            "tokens": [record.model_dump(mode="json") for record in records],
        }
        with _STORE_LOCK:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".pat-tokens-", suffix=".json", dir=folder, text=True
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                # ``mkstemp`` already creates the file with mode 0600; the
                # explicit chmod is best effort because some sandboxes do not
                # implement it.
                try:
                    os.chmod(temporary, 0o600)
                except OSError:  # pragma: no cover - platform dependent
                    pass
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    try:
                        os.unlink(temporary)
                    except OSError:  # pragma: no cover - best effort cleanup
                        pass
        # Repair permissions on an existing file written by an older build.
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass

    # ── CRUD ───────────────────────────────────────────────────────────────

    def list(self, username: Optional[str] = None) -> List[PATRecord]:
        """Return tokens (optionally for one owner), newest first."""
        records = self._load()
        if username:
            records = [record for record in records if record.username == username]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def find(self, token_id: str, username: Optional[str] = None) -> Optional[PATRecord]:
        """Return one token by id (optionally constrained to its owner)."""
        for record in self._load():
            if record.id != token_id:
                continue
            if username and record.username != username:
                return None
            return record
        return None

    def create(
        self,
        username: str,
        name: str = "",
        scopes: Optional[Iterable[str]] = None,
        project_id: Optional[str] = None,
        project_name: str = "",
        profiles: Optional[Iterable[str]] = None,
        expires_in_days: Optional[int] = 90,
        require_public_exposure_protection: bool = True,
    ) -> Tuple[str, PATRecord]:
        """
        Create a token and return ``(clear_token, record)``.

        The clear token is not recoverable afterwards: only its digest is
        stored.
        """
        owner = str(username or "").strip()
        if not owner:
            raise ValueError("username is required")
        normalized_scopes = normalize_scopes(scopes)
        if SCOPE_CHAT in normalized_scopes and not project_id:
            raise ValueError(
                "a project_id is required when the token can chat with profiles"
            )

        now = utc_now()
        token_id = secrets.token_hex(8)
        secret = secrets.token_hex(32)
        token = f"{PAT_PREFIX}{token_id}_{secret}"
        expires_at = None
        days = int(expires_in_days or 0)
        if days > 0:
            expires_at = _iso(now + timedelta(days=days))

        record = PATRecord(
            id=token_id,
            name=str(name or "").strip()[:120] or "API token",
            username=owner,
            token_hash=token_digest(token),
            prefix=f"{PAT_PREFIX}{token_id[:6]}",
            scopes=normalized_scopes,
            project_id=str(project_id or "").strip() or None,
            project_name=str(project_name or "").strip(),
            # The UI and the tools accept the historical ``@profile`` spelling.
            profiles=[
                str(name or "").strip().lstrip("@")
                for name in (profiles or [])
                if str(name or "").strip().lstrip("@")
            ],
            require_public_exposure_protection=bool(
                require_public_exposure_protection
            ),
            created_at=_iso(now),
            expires_at=expires_at,
        )
        with _STORE_LOCK, interprocess_lock(f"{self.path}.lock"):
            records = self._load()
            records.append(record)
            self._save(records)
        return token, record

    def revoke(
        self,
        token_id: str,
        username: Optional[str] = None,
        allow_any: bool = False,
    ) -> bool:
        """Mark one token as revoked. Returns ``False`` when it is not found."""
        with _STORE_LOCK, interprocess_lock(f"{self.path}.lock"):
            records = self._load()
            changed = False
            for record in records:
                if record.id != token_id:
                    continue
                if not allow_any and username and record.username != username:
                    return False
                if not record.revoked_at:
                    record.revoked_at = _iso(utc_now())
                    changed = True
                    break
            if changed:
                self._save(records)
            return changed

    # ── Verification ───────────────────────────────────────────────────────

    def verify_with_reason(self, token: str) -> Tuple[Optional[PATRecord], str]:
        """
        Resolve *token* to its record.

        Returns ``(record, "")`` on success or ``(None, reason)`` with one of
        :data:`PAT_INVALID`, :data:`PAT_REVOKED`, :data:`PAT_EXPIRED`.
        """
        parsed = parse_pat(token)
        if not parsed:
            return None, PAT_INVALID
        token_id, _secret = parsed
        record = self.find(token_id)
        if record is None:
            return None, PAT_INVALID
        if record.is_revoked():
            return None, PAT_REVOKED
        if record.is_expired():
            return None, PAT_EXPIRED
        # Constant-time comparison of the digest: a timing side channel must not
        # reveal how many bytes of the secret matched.
        if not hmac.compare_digest(record.token_hash, token_digest(token)):
            return None, PAT_INVALID
        try:
            self._touch(record)
        except OSError as ex:  # pragma: no cover - read-only store
            # ``last_used_at`` is telemetry: a read-only token store must not
            # turn a valid credential into a 500.
            logger.warning("Cannot update PAT last_used_at: %s", ex)
        return record, ""

    def verify(self, token: str) -> Optional[PATRecord]:
        """Resolve *token* to its active record, or ``None``."""
        record, _reason = self.verify_with_reason(token)
        return record

    def _touch(self, record: PATRecord) -> None:
        """Record the last use without writing the file on every request."""
        now = utc_now()
        previous = _parse_iso(record.last_used_at)
        if previous and (now - previous).total_seconds() < LAST_USED_WRITE_INTERVAL_SECONDS:
            return
        with _STORE_LOCK, interprocess_lock(f"{self.path}.lock"):
            records = self._load()
            for stored in records:
                if stored.id == record.id:
                    stored.last_used_at = _iso(now)
                    record.last_used_at = stored.last_used_at
                    self._save(records)
                    break


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------


def _header_value(request: Any, name: str) -> str:
    """Read a header from a Starlette request or a mapping."""
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or "")
    except (AttributeError, TypeError):
        return ""


def extract_pat_token(request: Any) -> str:
    """
    Return the PAT carried by the request, or ``""``.

    ``Authorization`` wins over the compatibility aliases, matching the rest of
    the API. A session JWT found in ``Authorization`` is not a PAT and is
    returned as-is only when it has the PAT prefix; the caller decides.
    """
    for name in PAT_HEADERS:
        token = normalize_token(_header_value(request, name))
        if token and is_pat_token(token):
            return token
    return ""


def get_pat_principal(request: Any, required_scopes: Optional[Iterable[str]] = None) -> PATPrincipal:
    """
    Authenticate the request as a PAT holder.

    Raises:
        HTTPException: 401 when the token is absent/invalid/revoked/expired,
            403 when a required scope is missing or the owner is disabled.
    """
    token = extract_pat_token(request)
    if not token:
        raise _unauthorized(
            "missing_credentials",
            "A personal access token is required: send "
            "'Authorization: Bearer cxj_pat_...'.",
        )

    store = PATStore()
    record, reason = store.verify_with_reason(token)
    if record is None:
        messages = {
            PAT_REVOKED: "This personal access token has been revoked.",
            PAT_EXPIRED: "This personal access token has expired.",
        }
        raise _unauthorized(
            "invalid_credentials",
            messages.get(reason, "Invalid personal access token."),
        )

    from codx.junior.security.user_management import UserSecurityManager

    user = UserSecurityManager().find_user(username=record.username)
    if user is None:
        raise _unauthorized(
            "invalid_credentials",
            "The account that owns this token no longer exists.",
        )
    if getattr(user, "disabled", False):
        raise HTTPException(
            status_code=403,
            detail=pat_error(
                "pat_owner_disabled",
                "The account that owns this token is disabled.",
            ),
        )

    required = [scope for scope in (required_scopes or []) if scope]
    missing = [scope for scope in required if not record.has_scope(scope)]
    if missing:
        raise HTTPException(
            status_code=403,
            detail=pat_error(
                "insufficient_scope",
                "This token lacks the required scope(s): " + ", ".join(missing),
            ),
        )
    return PATPrincipal(token=record, user=user)


def require_pat_scope(*scopes: str):
    """
    Build a FastAPI dependency that requires a PAT with every scope in *scopes*.

    Example:
        >>> @router.post("/chat")
        ... def chat(principal = Depends(require_pat_scope(SCOPE_CHAT))):
        ...     ...
    """

    def dependency(request: Request) -> PATPrincipal:
        return get_pat_principal(request, required_scopes=scopes)

    return dependency


def scope_catalog() -> List[Dict[str, str]]:
    """Return the scope catalogue for the UI."""
    return [dict(item) for item in PAT_SCOPE_CATALOG]
