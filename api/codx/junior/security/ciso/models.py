"""Data models for the CISO security subsystem.

The CISO subsystem answers one question everywhere in the platform: *may this
artifact, command or answer cross the trust boundary?*

Two design rules make the answer trustworthy:

* **Deterministic.** Detectors use regular expressions, entropy and structural
  heuristics. No security decision is delegated to an LLM, so a verdict is
  reproducible, cheap and auditable (it can be replayed against a stored diff).
* **Two stages.** *Detectors* only produce :class:`SecurityFinding` signals.
  The *policy engine* (:mod:`codx.junior.security.ciso.policy`) turns the
  signals into a single :class:`SecurityVerdict`. Severity therefore never
  implies an action by itself: the same HIGH finding may block a git commit and
  only warn inside a document.

Made with ❤️ by codx-junior
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SecuritySeverity(str, Enum):
    """Severity of a single CISO finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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


class SecurityFinding(BaseModel):
    """A single signal produced by a detector.

    ``excerpt`` is ALWAYS already redacted: a finding is copied into logs, chat
    messages and audit records, so it must never carry the secret it reports.
    """

    rule_id: str = Field(description="Stable detector identifier, e.g. 'secret.aws-access-key'")
    title: str = Field(default="", description="Short human readable title")
    severity: SecuritySeverity = SecuritySeverity.MEDIUM
    category: SecurityCategory = SecurityCategory.POLICY
    reason: str = Field(default="", description="Why the signal fired")
    path: Optional[str] = Field(default=None, description="File the finding belongs to")
    line: Optional[int] = Field(default=None, description="1-based line number")
    excerpt: str = Field(default="", description="Redacted excerpt around the match")
    evidence: str = Field(default="", description="Redacted matched value (never the raw secret)")
    remediation: str = Field(default="", description="What to do to fix it")
    confidence: Optional[float] = Field(default=None, description="0..1 detector confidence")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON serialisable representation."""
        return self.model_dump(mode="json")


class SecurityVerdict(BaseModel):
    """Aggregated CISO decision for one artifact, command or answer."""

    domain: SecurityDomain = SecurityDomain.FILE_WRITE
    action: SecurityAction = SecurityAction.ALLOW
    allowed: bool = True
    blocked: bool = False
    requires_approval: bool = False
    risk_score: float = 0.0
    highest_severity: Optional[SecuritySeverity] = None
    findings: List[SecurityFinding] = Field(default_factory=list)
    summary: str = ""
    redacted_text: Optional[str] = Field(
        default=None,
        description="Text with secrets neutralised; None when nothing was redacted",
    )
    rules_evaluated: int = Field(default=0, description="Number of rules that ran")
    truncated: bool = Field(
        default=False, description="True when the scanned payload was cut to the size cap"
    )

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


class CisoPolicy(BaseModel):
    """Security policy used to decide what happens with the findings.

    The defaults are *fail-safe but not hostile*: secrets and clearly malicious
    code are blocked on the paths that persist or execute something, everything
    else warns. Deployments can tighten or relax it through project settings.
    """

    enabled: bool = Field(default=True, description="Master switch of the CISO checks")

    block_severities: List[SecuritySeverity] = Field(
        default_factory=lambda: [SecuritySeverity.CRITICAL],
        description="Severities that always block, in every domain",
    )
    block_categories: List[SecurityCategory] = Field(
        default_factory=lambda: [
            SecurityCategory.SECRET,
            SecurityCategory.MALICIOUS,
            SecurityCategory.DATA_EXFILTRATION,
        ],
        description="Categories blocked when they reach 'block_above' severity",
    )
    block_above: SecuritySeverity = Field(
        default=SecuritySeverity.HIGH,
        description="Least severity that turns a blocked category into a BLOCK",
    )
    approval_above: SecuritySeverity = Field(
        default=SecuritySeverity.MEDIUM,
        description="Least severity that requires human approval instead of blocking",
    )
    warn_above: SecuritySeverity = Field(
        default=SecuritySeverity.LOW,
        description="Least severity that still produces a warning",
    )

    redact_secrets: bool = Field(
        default=True, description="Neutralise detected secrets in outputs and logs"
    )
    fail_closed: bool = Field(
        default=False,
        description=(
            "When True an internal error of the scanner blocks the operation "
            "instead of letting it through (fail-open)."
        ),
    )

    strict_domains: List[SecurityDomain] = Field(
        default_factory=lambda: [
            SecurityDomain.GIT_COMMIT,
            SecurityDomain.PULL_REQUEST,
            SecurityDomain.COMMAND,
            SecurityDomain.CONTAINER,
            SecurityDomain.DEPLOY,
        ],
        description="Domains where any HIGH finding blocks instead of asking for approval",
    )
    enabled_domains: List[SecurityDomain] = Field(
        default_factory=lambda: list(SecurityDomain),
        description="Domains where the policy runs at all",
    )

    ignore_paths: List[str] = Field(
        default_factory=lambda: [
            "node_modules/", "dist/", "build/", ".venv/", "venv/",
            "__pycache__/", ".mypy_cache/", ".pytest_cache/", "package-lock.json",
            "poetry.lock", "yarn.lock",
        ],
        description="Path fragments skipped by the file scanners",
    )
    ignore_rules: List[str] = Field(
        default_factory=list, description="Rule ids silenced by this policy"
    )
    allow_secret_files: List[str] = Field(
        default_factory=list,
        description="Paths allowed to contain secret-looking literals (e.g. '.env.example')",
    )

    entropy_threshold: float = Field(
        default=4.2, description="Shannon entropy above which a token is suspicious"
    )
    min_secret_length: int = Field(
        default=20, description="Least length of a high-entropy token worth flagging"
    )
    max_scan_bytes: int = Field(
        default=1024 * 1024, description="Hard cap on the payload a single scan reads"
    )
    max_excerpt_chars: int = Field(
        default=160, description="Length of the redacted excerpt attached to a finding"
    )

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
