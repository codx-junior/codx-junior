"""
Portable login state for coworking browser sessions.

Two stores, both project-scoped and encrypted at rest:

* **Site sessions** — the cookies and localStorage of the sites a session is
  logged into. Capture them once, then apply them to *another* browser session
  (a different browser image included) and the sites open logged in. This is
  what makes switching from Chromium to Brave a one-click move instead of a
  round of logins.
* **Credentials** — site, URL, username and password. The human can copy them
  from the UI; the AI can fill a login form with them without ever receiving
  the password (the resolved value never enters the tool result or the chat).

Layout inside the project (``.codx/browser_store/``):

    index.json                 metadata only: names, origins, users, dates
    sessions/<id>.enc          encrypted state (cookies + localStorage)
    credentials/<id>.enc       encrypted password
    audit.jsonl                reveal / fill decisions (names and users, no values)

Encryption reuses the CISO secrets vault key (``SecretVault.fernet``): one key
per project, no second key management. ``cryptography`` is optional in this
codebase, so when it is missing (or the vault key is not there) the store raises
``StoreError`` instead of ever writing plaintext.

The module is transport agnostic: the API captures/applies state through the
browser runtime; this module owns validation, storage, naming and the audit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

STORE_DIRNAME = "browser_store"
INDEX_FILENAME = "index.json"
SESSIONS_DIRNAME = "sessions"
CREDENTIALS_DIRNAME = "credentials"
AUDIT_FILENAME = "audit.jsonl"
STORE_MODE = 0o600

#: A cookie jar + local storage is small; anything bigger is a sign of a bug or
#: of a page dumping a huge blob into localStorage.
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_NAME_CHARS = 120
_COOKIE_FIELDS = (
    "name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite",
)


class StoreError(Exception):
    """A store operation that cannot be completed (never a silent fallback)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)

    def render(self) -> str:
        return f"Browser store unavailable: {self.detail or self.code}"


class _FernetCipher:
    """The production cipher: Fernet from the project secrets vault."""

    def __init__(self, fernet: Any) -> None:
        self._fernet = fernet

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)


def project_of(settings: Any) -> str:
    """Project root used to locate the store."""
    for attribute in ("abs_project_path", "project_path"):
        value = getattr(settings, attribute, None)
        if isinstance(value, str) and value:
            return value
    return ""


def store_root(settings: Any) -> str:
    """Absolute path of the store folder (``<project>/.codx/browser_store``)."""
    project = project_of(settings)
    if not project:
        raise StoreError("no_project", "the session has no project path")
    return os.path.join(project, ".codx", STORE_DIRNAME)


def default_cipher(project: str) -> _FernetCipher:
    """Build the cipher from the vault key, refusing to continue without it."""
    try:
        from codx.junior.security.ciso.vault import SecretVault
    except Exception as ex:  # pragma: no cover - import guard
        raise StoreError("store_unavailable", f"the secrets vault is not importable: {ex}")
    vault = SecretVault(project)
    fernet, error = vault.fernet(create=True)
    if not fernet:
        detail = getattr(error, "detail", "") or getattr(error, "code", "vault unavailable")
        raise StoreError(
            "store_unavailable",
            f"no encryption key: {detail}. Install 'cryptography' and set "
            "CODX_JUNIOR_VAULT_KEY (or run the vault keygen) to store passwords.",
        )
    return _FernetCipher(fernet)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _clean_name(value: Any) -> str:
    name = " ".join(str(value or "").split()).strip()
    if not name:
        raise StoreError("name_required", "a name is required")
    if len(name) > MAX_NAME_CHARS:
        raise StoreError("name_too_long", f"use at most {MAX_NAME_CHARS} characters")
    return name


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except Exception as ex:
        logger.warning("Browser store: cannot read %s: %s", path, ex)
        return default


def _write_json(path: str, data: Any) -> None:
    """Atomic 0600 write: a half-written store is worse than no store."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        os.fchmod(handle, STORE_MODE)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, STORE_MODE)
    except OSError:
        pass


def _write_encrypted(path: str, cipher: Any, data: Dict[str, Any]) -> int:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    token = cipher.encrypt(raw)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        os.fchmod(handle, STORE_MODE)
        with os.fdopen(handle, "wb") as stream:
            stream.write(token)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, STORE_MODE)
    except OSError:
        pass
    return len(token)


def _read_encrypted(path: str, cipher: Any) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "rb") as handle:
            token = handle.read()
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(cipher.decrypt(token).decode("utf-8"))
    except Exception as ex:
        raise StoreError(
            "cannot_decrypt",
            f"the stored payload cannot be decrypted (vault key changed?): {ex}",
        )
    return payload if isinstance(payload, dict) else None


def normalize_state(raw: Any, *, max_bytes: int = MAX_STATE_BYTES) -> Dict[str, Any]:
    """
    Sanitize a runtime export before storing it.

    Keeps only the portable, well-known cookie fields and one entry per origin.
    Unknown extras are dropped, duplicates are merged and the total size is
    capped, so a hostile page cannot stuff the store.
    """
    if not isinstance(raw, dict):
        raise StoreError("invalid_state", "the browser returned no state")
    cookies: List[Dict[str, Any]] = []
    for cookie in raw.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        entry = {field: cookie[field] for field in _COOKIE_FIELDS if field in cookie}
        if not entry.get("name") or not entry.get("domain"):
            continue
        if entry.get("expires") in (None, 0):
            entry.pop("expires", None)
        cookies.append(entry)

    origins: Dict[str, Dict[str, str]] = {}
    for entry in raw.get("origins") or []:
        if not isinstance(entry, dict):
            continue
        origin = str(entry.get("origin") or "").strip()
        if not origin.startswith(("http://", "https://")):
            continue
        items = entry.get("localStorage") or {}
        if not isinstance(items, dict):
            continue
        bucket = origins.setdefault(origin, {})
        for key, value in items.items():
            if key is None or value is None:
                continue
            bucket[str(key)] = str(value)[:64 * 1024]

    state = {
        "cookies": cookies,
        "origins": [
            {"origin": origin, "localStorage": items}
            for origin, items in origins.items()
            if items
        ],
    }
    size = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
    if size > max_bytes:
        raise StoreError(
            "state_too_large",
            f"the session state is {size} bytes; the limit is {max_bytes}",
        )
    if not cookies and not state["origins"]:
        raise StoreError(
            "empty_state",
            "there is nothing to save: the browser has no cookies and no local storage "
            "on this page (open the logged-in site first)",
        )
    return state


def suggest_name(origins: Iterable[str]) -> str:
    """
    A short, human name for a captured cookie jar (``a.com + b.com``).

    Used to prefill the save dialog so a capture is one click: the user should
    not have to invent a name before the browser's open sites can be saved.
    """
    hosts: List[str] = []
    for origin in origins or []:
        try:
            parsed = urllib.parse.urlparse(str(origin))
        except Exception:
            continue
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in hosts:
            hosts.append(host)
    if not hosts:
        return ""
    if len(hosts) <= 2:
        return " + ".join(hosts)
    return f"{hosts[0]} + {len(hosts) - 1} more"


class BrowserStore:
    """Site-session and credential storage for one project."""

    def __init__(
        self,
        settings: Any = None,
        *,
        project_path: Optional[str] = None,
        cipher: Any = None,
    ) -> None:
        project = project_path or project_of(settings)
        if not project:
            raise StoreError("no_project", "the session has no project path")
        self.project_path = project
        self.root = os.path.join(project, ".codx", STORE_DIRNAME)
        self._cipher = cipher
        self._cipher_error: Optional[StoreError] = None

    # ── infrastructure ────────────────────────────────────────────────────
    @property
    def cipher(self):
        if self._cipher is not None:
            return self._cipher
        if self._cipher_error is not None:
            raise self._cipher_error
        try:
            self._cipher = default_cipher(self.project_path)
        except StoreError as ex:
            self._cipher_error = ex
            raise
        return self._cipher

    @property
    def index_path(self) -> str:
        return os.path.join(self.root, INDEX_FILENAME)

    def _load_index(self) -> Dict[str, List[Dict[str, Any]]]:
        data = _read_json(self.index_path, {})
        return {
            "sessions": list(data.get("sessions") or []),
            "credentials": list(data.get("credentials") or []),
        }

    def _save_index(self, index: Dict[str, List[Dict[str, Any]]]) -> None:
        _write_json(self.index_path, index)

    def _audit(self, action: str, *, kind: str, name: str, user: Optional[str]) -> None:
        row = {
            "ts": _now(),
            "action": action,
            "kind": kind,
            "name": name,
            "user": user or "",
        }
        try:
            os.makedirs(self.root, exist_ok=True)
            with open(os.path.join(self.root, AUDIT_FILENAME), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.chmod(os.path.join(self.root, AUDIT_FILENAME), STORE_MODE)
        except OSError:
            pass

    @staticmethod
    def _match(entries: Iterable[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
        wanted = str(key or "").strip().lower()
        if not wanted:
            return None
        for entry in entries:
            if str(entry.get("id", "")).lower() == wanted:
                return entry
        for entry in entries:
            if str(entry.get("name", "")).strip().lower() == wanted:
                return entry
        return None

    # ── site sessions ─────────────────────────────────────────────────────
    def sessions(self) -> List[Dict[str, Any]]:
        """Saved site sessions, most recently updated first (no state)."""
        entries = self._load_index()["sessions"]
        entries.sort(key=lambda entry: str(entry.get("updated_at") or ""), reverse=True)
        return entries

    def save_session(self, name: Any, state: Any, user: Optional[str] = None) -> Dict[str, Any]:
        """Store a captured state under *name* (replacing a same-named one)."""
        clean = _clean_name(name)
        payload = normalize_state(state)
        index = self._load_index()
        existing = self._match(index["sessions"], clean)
        session_id = existing["id"] if existing else _new_id()
        path = os.path.join(self.root, SESSIONS_DIRNAME, f"{session_id}.enc")
        size = _write_encrypted(path, self.cipher, payload)
        origins = [entry["origin"] for entry in payload["origins"]]
        meta = {
            "id": session_id,
            "name": clean,
            "origins": origins,
            "cookie_count": len(payload["cookies"]),
            "origin_count": len(origins),
            "size": size,
            "created_at": existing.get("created_at") if existing else _now(),
            "updated_at": _now(),
            "created_by": (existing or {}).get("created_by") or (user or ""),
        }
        index["sessions"] = [entry for entry in index["sessions"] if entry.get("id") != session_id]
        index["sessions"].append(meta)
        self._save_index(index)
        self._audit("save", kind="session", name=clean, user=user)
        return meta

    def session_state(self, key: str, user: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return ``(meta, state)`` for an id or name, raising when absent."""
        meta = self._match(self.sessions(), key)
        if not meta:
            raise StoreError("not_found", f"no saved session matches '{key}'")
        path = os.path.join(self.root, SESSIONS_DIRNAME, f"{meta['id']}.enc")
        state = _read_encrypted(path, self.cipher)
        if state is None:
            raise StoreError("missing_payload", f"the state of '{meta['name']}' is missing")
        self._audit("apply", kind="session", name=meta["name"], user=user)
        return meta, state

    def delete_session(self, key: str, user: Optional[str] = None) -> bool:
        index = self._load_index()
        meta = self._match(index["sessions"], key)
        if not meta:
            return False
        index["sessions"] = [entry for entry in index["sessions"] if entry.get("id") != meta["id"]]
        self._save_index(index)
        try:
            os.unlink(os.path.join(self.root, SESSIONS_DIRNAME, f"{meta['id']}.enc"))
        except FileNotFoundError:
            pass
        self._audit("delete", kind="session", name=meta["name"], user=user)
        return True

    # ── credentials ───────────────────────────────────────────────────────
    def credentials(self) -> List[Dict[str, Any]]:
        """Credential metadata (never the password), most recent first."""
        entries = self._load_index()["credentials"]
        entries.sort(key=lambda entry: str(entry.get("updated_at") or ""), reverse=True)
        return entries

    def save_credential(
        self,
        name: Any,
        username: Any,
        password: Any,
        url: Optional[str] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or replace a credential (same name updates it)."""
        clean = _clean_name(name)
        secret = str(password or "")
        if not secret:
            raise StoreError("password_required", "a password is required")
        if len(secret.encode("utf-8")) > MAX_CREDENTIAL_BYTES:
            raise StoreError("password_too_large", "the password is too large")
        account = str(username or "").strip()
        site = str(url or "").strip()
        index = self._load_index()
        existing = self._match(index["credentials"], clean)
        credential_id = existing["id"] if existing else _new_id()
        path = os.path.join(self.root, CREDENTIALS_DIRNAME, f"{credential_id}.enc")
        _write_encrypted(path, self.cipher, {"password": secret})
        meta = {
            "id": credential_id,
            "name": clean,
            "url": site,
            "username": account,
            "created_at": existing.get("created_at") if existing else _now(),
            "updated_at": _now(),
            "created_by": (existing or {}).get("created_by") or (user or ""),
        }
        index["credentials"] = [entry for entry in index["credentials"] if entry.get("id") != credential_id]
        index["credentials"].append(meta)
        self._save_index(index)
        self._audit("save", kind="credential", name=clean, user=user)
        return meta

    def update_credential(
        self,
        key: str,
        *,
        name: Any = None,
        username: Any = None,
        password: Any = None,
        url: Any = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Patch a credential; the password is only rewritten when provided."""
        index = self._load_index()
        existing = self._match(index["credentials"], key)
        if not existing:
            raise StoreError("not_found", f"no credential matches '{key}'")
        meta = dict(existing)
        if name is not None:
            clean = _clean_name(name)
            duplicate = self._match(index["credentials"], clean)
            if duplicate and duplicate.get("id") != meta["id"]:
                raise StoreError("duplicate_name", f"'{clean}' already exists")
            meta["name"] = clean
        if username is not None:
            meta["username"] = str(username or "").strip()
        if url is not None:
            meta["url"] = str(url or "").strip()
        if password is not None:
            secret = str(password)
            if not secret:
                raise StoreError("password_required", "a password is required")
            _write_encrypted(
                os.path.join(self.root, CREDENTIALS_DIRNAME, f"{meta['id']}.enc"),
                self.cipher,
                {"password": secret},
            )
        meta["updated_at"] = _now()
        index["credentials"] = [entry for entry in index["credentials"] if entry.get("id") != meta["id"]]
        index["credentials"].append(meta)
        self._save_index(index)
        self._audit("update", kind="credential", name=meta["name"], user=user)
        return meta

    def credential(self, key: str, user: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
        """Return ``(meta, password)`` for an id or name."""
        meta = self._match(self.credentials(), key)
        if not meta:
            raise StoreError("not_found", f"no credential matches '{key}'")
        path = os.path.join(self.root, CREDENTIALS_DIRNAME, f"{meta['id']}.enc")
        payload = _read_encrypted(path, self.cipher)
        password = (payload or {}).get("password")
        if not password:
            raise StoreError("missing_payload", f"the password of '{meta['name']}' is missing")
        self._audit("reveal", kind="credential", name=meta["name"], user=user)
        return meta, str(password)

    def delete_credential(self, key: str, user: Optional[str] = None) -> bool:
        index = self._load_index()
        meta = self._match(index["credentials"], key)
        if not meta:
            return False
        index["credentials"] = [entry for entry in index["credentials"] if entry.get("id") != meta["id"]]
        self._save_index(index)
        try:
            os.unlink(os.path.join(self.root, CREDENTIALS_DIRNAME, f"{meta['id']}.enc"))
        except FileNotFoundError:
            pass
        self._audit("delete", kind="credential", name=meta["name"], user=user)
        return True
