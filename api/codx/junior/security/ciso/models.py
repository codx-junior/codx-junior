"""Data models for the CISO security subsystem.

The CISO subsystem answers one question everywhere in the platform: *may this
artifact, command or answer cross the trust boundary?*

Three design rules make the answer trustworthy:

* **Deterministic.** Detectors use regular expressions, entropy and structural
  heuristics. No security decision is delegated to an LLM, so a verdict is
  reproducible, cheap and auditable (it can be replayed against a stored diff).
* **Two stages.** *Detectors* only produce :class:`SecurityFinding` signals.
  The *policy engine* (:mod:`codx.junior.security.ciso.policy`) turns the
  signals into a single :class:`SecurityVerdict`. Severity therefore never
  implies an action by itself: the same HIGH finding may block a git commit and
  only warn inside a document.
* **Dependency-free.** These models are plain dataclasses on purpose: the
  package must import on *any* interpreter, because the very same code runs
  inside the API, in a git hook (often from a worktree with no virtualenv), in
  a container image and in CI. A security gate that needs the application
  dependency tree is a gate that stops running exactly when it is needed: it
  dies with an ``ImportError`` at commit time instead of scanning.

Made with ❤️ by codx-junior
"""

import copy as _copy
from dataclasses import dataclass, fields as _dataclass_fields
from enum import Enum
from typing import Any, Dict, List, Optional, get_args, get_origin

_NONE_TYPE = type(None)


@dataclass
class CisoModel:
    """Base class of the CISO models: plain dataclasses, no third-party import.

    It exposes the three attributes the rest of the package (and the tools)
    expect from a model, so replacing the previous pydantic base changed no
    call site:

    * ``model_dump(mode="json")`` -> JSON-safe ``dict`` (enums become values),
    * ``to_dict()`` / ``dict()`` -> the same, for older callers,
    * ``model_copy(update={...})`` -> a copy with fields overridden.

    Values are coerced to their declared enum or nested model on construction,
    which keeps a verdict loaded from ``.codx`` JSON working when its enums come
    back as plain strings.
    """

    def model_dump(self, mode: str = "python", **_: Any) -> Dict[str, Any]:
        """Return the model as a ``dict`` (``mode="json"`` stringifies enums)."""
        return _plain(self, json_mode=(mode == "json"))

    def dict(self, **kwargs: Any) -> Dict[str, Any]:
        """Alias of :meth:`model_dump`, kept for pydantic-v1 style callers."""
        return self.model_dump(**kwargs)

    def model_copy(
        self, update: Optional[Dict[str, Any]] = None, deep: bool = False
    ) -> "CisoModel":
        """Return a copy of this model, optionally with *update* applied."""
        clone = _copy.deepcopy(self) if deep else _copy.copy(self)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone

    def __post_init__(self) -> None:
        """Coerce every field to its declared enum/nested-model type."""
        for model_field in _dataclass_fields(self):
            value = getattr(self, model_field.name)
            setattr(self, model_field.name, _coerce(value, model_field.type))


def _coerce_enum(value: Any, enum_type: type) -> Any:
    """Return *value* as a member of *enum_type* when it can be resolved."""
    if value is None or isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        for member in enum_type:
            if value == member.value or value.lower() == member.name.lower():
                return member
    return value


def _coerce(value: Any, hint: Any) -> Any:
    """Coerce *value* to *hint* (enum, nested model or list of them)."""
    if value is None or hint is Any:
        return value
    if hint is int or hint is float:
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return hint(value)
        return value
    origin = get_origin(hint)
    if origin is not None:
        args = get_args(hint)
        if origin is list or origin is List:
            item_hint = args[0] if args else Any
            if isinstance(value, (list, tuple)):
                return [_coerce(item, item_hint) for item in value]
            return value
        # Optional[X] / Union[...]: coerce with the first matching member.
        for arg in args:
            if arg is _NONE_TYPE or arg is type(None):
                continue
            coerced = _coerce(value, arg)
            if coerced is not value:
                return coerced
        return value
    if isinstance(hint, type):
        if issubclass(hint, Enum):
            return _coerce_enum(value, hint)
        if issubclass(hint, CisoModel) and isinstance(value, dict):
            return hint(**value)
    return value


def _plain(value: Any, json_mode: bool) -> Any:
    """Return *value* with models expanded to dicts (and enums to values)."""
    if isinstance(value, Enum):
        return value.value if json_mode else value
    if isinstance(value, CisoModel):
        return {
            model_field.name: _plain(getattr(value, model_field.name), json_mode)
            for model_field in _dataclass_fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item, json_mode) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item, json_mode) for key, item in value.items()}
    return value


class SecuritySeverity(str, Enum):
    """Severity of a single CISO finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Ordering of the severities (higher is worse), used by report filters.
SEVERITY_RANK: Dict[SecuritySeverity, int] = {
    SecuritySeverity.INFO: 0,
    SecuritySeverity.LOW: 1,
    SecuritySeverity.MEDIUM: 2,
    SecuritySeverity.HIGH: 3,
    SecuritySeverity.CRITICAL: 4,
}


def severity_rank(severity: Optional[SecuritySeverity]) -> int:
    """Return the numeric rank of *severity* (``0`` when unknown)."""
    return SEVERITY_RANK.get(severity, 0) if severity else 0


def parse_severity(value: Any) -> Optional[SecuritySeverity]:
    """
    Coerce *value* to a :class:`SecuritySeverity`.

    Args:
        value: A ``SecuritySeverity``, its string name/value, or ``None``.

    Returns:
        The severity, or ``None`` when the value is empty or unknown.
    """
    if isinstance(value, SecuritySeverity):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    for severity in SecuritySeverity:
        if raw in (severity.value, severity.name.lower()):
            return severity
    return None


def meets_minimum(
    severity: Optional[SecuritySeverity],
    minimum: Optional[SecuritySeverity],
) -> bool:
    """Return True when *severity* is at least *minimum*."""
    if minimum is None:
        return True
    return severity_rank(severity) >= severity_rank(minimum)


class SecurityCategory(str, Enum):
    """Family of risk a finding belongs to.

    The category drives the remediation advice and lets a policy treat a
    ``secret`` differently from a ``malicious`` finding of the same severity.
    """

    SECRET = "secret"
    MALICIOUS = "malicious"
    INJECTION = "injection"
    DANGEROUS_OPERATION = "dangerous_operation"
    DATA_EXFILTRATION = "data_exfiltration"
    SUPPLY_CHAIN = "supply_chain"
    POLICY = "policy"
    CONFIGURATION = "configuration"


class SecurityDomain(str, Enum):
    """Process (integration point) where the check runs.

    Every value maps to a real choke point of the platform; see
    ``codx/junior/security/ciso/README.md`` for the full map.
    """

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    GIT_COMMIT = "git_commit"
    PULL_REQUEST = "pull_request"
    COMMAND = "command"
    CONTAINER = "container"
    NETWORK = "network"
    MODEL_OUTPUT = "model_output"
    KNOWLEDGE_INGESTION = "knowledge_ingestion"
    PROFILE = "profile"
    DEPLOY = "deploy"


class SecurityAction(str, Enum):
    """Decision the policy engine took for a verdict."""

    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_APPROVAL = "require_approval"
    REDACT = "redact"
    BLOCK = "block"


@dataclass
class SecurityFinding(CisoModel):
    """A single signal produced by a detector.

    ``excerpt`` is ALWAYS already redacted: a finding is copied into logs, chat
    messages and audit records, so it must never carry the secret it reports.
    """

    rule_id: str
    title: str = ""
    severity: SecuritySeverity = SecuritySeverity.MEDIUM
    category: SecurityCategory = SecurityCategory.POLICY
    reason: str = ""
    path: Optional[str] = None
    line: Optional[int] = None
    excerpt: str = ""
    evidence: str = ""
    remediation: str = ""
    confidence: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON serialisable representation."""
        return self.model_dump(mode="json")


@dataclass
class SecurityVerdict(CisoModel):
    """Aggregated CISO decision for one artifact, command or answer."""

    domain: SecurityDomain = SecurityDomain.FILE_WRITE
    action: SecurityAction = SecurityAction.ALLOW
    allowed: bool = True
    blocked: bool = False
    requires_approval: bool = False
    risk_score: float = 0.0
    highest_severity: Optional[SecuritySeverity] = None
    findings: List[SecurityFinding] = None
    summary: str = ""
    redacted_text: Optional[str] = None
    rules_evaluated: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        """Coerce the fields and guarantee the mutable default of findings."""
        if self.findings is None:
            self.findings = []
        super().__post_init__()

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON serialisable representation."""
        return self.model_dump(mode="json")

    def findings_by_severity(self) -> Dict[str, int]:
        """Count the findings per severity (useful for reports)."""
        counts: Dict[str, int] = {}
        for finding in self.findings:
            key = finding.severity.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def findings_at_least(
        self, minimum: Optional[SecuritySeverity]
    ) -> List[SecurityFinding]:
        """
        Return the findings whose severity is at least *minimum*.

        Args:
            minimum: Severity floor; ``None`` keeps every finding.

        Returns:
            List[SecurityFinding]: Findings sorted by descending severity.
        """
        kept = [f for f in self.findings if meets_minimum(f.severity, minimum)]
        return sorted(kept, key=lambda f: severity_rank(f.severity), reverse=True)

    def filtered(self, minimum: Optional[SecuritySeverity]) -> "SecurityVerdict":
        """
        Return a copy of this verdict keeping only findings >= *minimum*.

        The risk score, the action and the domain are preserved (they were
        computed from the whole payload); only the finding list and the highest
        severity are recomputed, so a filtered verdict can be rendered safely.
        """
        if minimum is None:
            return self
        kept = self.findings_at_least(minimum)
        highest = max(
            (finding.severity for finding in kept),
            key=severity_rank,
            default=None,
        )
        return self.model_copy(
            update={"findings": kept, "highest_severity": highest}
        )


def _default_block_severities() -> List[SecuritySeverity]:
    """Severities that always block, in every domain."""
    return [SecuritySeverity.CRITICAL]


def _default_block_categories() -> List[SecurityCategory]:
    """Categories blocked once they reach ``block_above``."""
    return [
        SecurityCategory.SECRET,
        SecurityCategory.MALICIOUS,
        SecurityCategory.DATA_EXFILTRATION,
    ]


def _default_strict_domains() -> List[SecurityDomain]:
    """Domains where a finding at ``block_above`` blocks instead of asking."""
    # Every domain that PERSISTS or EXECUTES an artifact. A file write belongs
    # here with the git ones: writing the file is what puts the secret on disk,
    # so redacting it and continuing would leave the repository one `git add`
    # away from the incident.
    return [
        SecurityDomain.FILE_WRITE,
        SecurityDomain.GIT_COMMIT,
        SecurityDomain.PULL_REQUEST,
        SecurityDomain.COMMAND,
        SecurityDomain.CONTAINER,
        SecurityDomain.DEPLOY,
    ]


def _default_ignore_paths() -> List[str]:
    """Path fragments skipped by the file scanners (vendored/generated)."""
    return [
        "node_modules/", "dist/", "build/", ".venv/", "venv/",
        "__pycache__/", ".mypy_cache/", ".pytest_cache/", "package-lock.json",
        "poetry.lock", "yarn.lock",
    ]


@dataclass
class CisoPolicy(CisoModel):
    """Security policy used to decide what happens with the findings.

    The defaults are *fail-safe but not hostile*: secrets and clearly malicious
    code are blocked on the paths that persist or execute something, everything
    else warns. Deployments can tighten or relax it through project settings.
    """

    enabled: bool = True

    block_severities: List[SecuritySeverity] = None
    block_categories: List[SecurityCategory] = None
    block_above: SecuritySeverity = SecuritySeverity.HIGH
    approval_above: SecuritySeverity = SecuritySeverity.MEDIUM
    warn_above: SecuritySeverity = SecuritySeverity.LOW

    redact_secrets: bool = True
    fail_closed: bool = False

    strict_domains: List[SecurityDomain] = None
    enabled_domains: List[SecurityDomain] = None

    ignore_paths: List[str] = None
    ignore_rules: List[str] = None
    allow_secret_files: List[str] = None

    entropy_threshold: float = 4.2
    min_secret_length: int = 20
    max_scan_bytes: int = 1024 * 1024
    max_excerpt_chars: int = 160

    def __post_init__(self) -> None:
        """Materialise the list defaults and coerce the enum fields."""
        if self.block_severities is None:
            self.block_severities = _default_block_severities()
        if self.block_categories is None:
            self.block_categories = _default_block_categories()
        if self.strict_domains is None:
            self.strict_domains = _default_strict_domains()
        if self.enabled_domains is None:
            self.enabled_domains = list(SecurityDomain)
        if self.ignore_paths is None:
            self.ignore_paths = _default_ignore_paths()
        if self.ignore_rules is None:
            self.ignore_rules = []
        if self.allow_secret_files is None:
            self.allow_secret_files = []
        super().__post_init__()

    def is_domain_enabled(self, domain: SecurityDomain) -> bool:
        """Return True when the policy runs for *domain*."""
        return self.enabled and domain in self.enabled_domains

    def ignores_path(self, path: Optional[str]) -> bool:
        """Return True when *path* must be skipped (vendored/generated code)."""
        if not path:
            return False
        normalised = path.replace("\\", "/")
        return any(fragment and fragment in normalised for fragment in self.ignore_paths)

    def ignores_rule(self, rule_id: str) -> bool:
        """Return True when *rule_id* is silenced by this policy."""
        return rule_id in (self.ignore_rules or [])

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON serialisable representation."""
        return self.model_dump(mode="json")
