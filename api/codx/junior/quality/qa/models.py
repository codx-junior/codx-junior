"""QA models — the vocabulary of the quality subsystem.

Plain dataclasses on purpose: the QA checks must run on a bare interpreter
(a CI step, a pre-commit hook, the self-check script in ``api/tests/quality``)
without pydantic, without the API stack and without a LLM.

Made with ❤️ by codx-junior
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Base
# -----------------------------------------------------------------------------
class QaModel:
    """Base class of the QA models: plain dataclasses, no third-party import.

    It exposes ``to_dict`` and ``get`` so the same object can be rendered as
    Markdown for a user, serialised into the audit journal and consumed by a
    tool without a conversion layer.
    """

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON friendly dict (enums become their value)."""
        return {key: _plain(value) for key, value in self.__dict__.items()}

    def get(self, key: str, default: Any = None) -> Any:
        """Return an attribute like a dict (kept for template style access)."""
        return getattr(self, key, default)


def _plain(value: Any) -> Any:
    """Recursively convert a value to something json.dumps understands."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, QaModel):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------
class QaSeverity(str, Enum):
    """How bad a quality finding is."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QaAction(str, Enum):
    """What the caller must do with a verdict."""

    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class QaCategory(str, Enum):
    """Which quality dimension a finding belongs to."""

    TESTS = "tests"
    COVERAGE = "coverage"
    CHANGE = "change"
    FLAKY = "flaky"
    ENVIRONMENT = "environment"
    POLICY = "policy"


class QaDomain(str, Enum):
    """The process that produced the verdict."""

    PLAN = "plan"
    TEST_RUN = "test_run"
    COVERAGE = "coverage"
    CHANGE_REVIEW = "change_review"
    FLAKY_CHECK = "flaky_check"


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------
@dataclass
class QaPolicy(QaModel):
    """Quality policy of a project.

    The defaults are *useful but not hostile*: a failing test always blocks, a
    change without tests asks for a human opinion but does not block, and a
    coverage threshold only warns until the project raises it.
    """

    enabled: bool = True
    min_coverage: float = 70.0
    block_above: QaSeverity = QaSeverity.HIGH
    approval_above: QaSeverity = QaSeverity.MEDIUM
    warn_above: QaSeverity = QaSeverity.LOW
    require_tests_for_changes: bool = True
    block_on_failures: bool = True
    block_missing_tests: bool = False
    fail_closed: bool = False
    max_test_seconds: int = 900
    flaky_repeats: int = 3
    ignore_paths: List[str] = field(default_factory=list)
    suites: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Suites and test runs
# -----------------------------------------------------------------------------
@dataclass
class QaSuite(QaModel):
    """A test suite that can be executed in the project."""

    name: str = ""
    kind: str = ""
    command: List[str] = field(default_factory=list)
    cwd: str = ""
    description: str = ""
    available: bool = True
    reason: str = ""

    @property
    def label(self) -> str:
        """Return a short ``name (kind)`` label for reports."""
        return f"{self.name} ({self.kind})" if self.kind else self.name


@dataclass
class TestResult(QaModel):
    """One failing, erroring or flaky test."""

    id: str = ""
    status: str = "failed"
    message: str = ""


@dataclass
class TestRun(QaModel):
    """The outcome of one suite execution."""

    suite: str = ""
    command: List[str] = field(default_factory=list)
    cwd: str = ""
    ok: bool = True
    returncode: int = 0
    duration_seconds: float = 0.0
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: List[TestResult] = field(default_factory=list)
    output_tail: str = ""
    error: str = ""
    timed_out: bool = False

    @property
    def summary(self) -> str:
        """Return a one line human summary of the run."""
        if self.error:
            return f"{self.suite}: {self.error}"
        if self.timed_out:
            return f"{self.suite}: timed out after {round(self.duration_seconds)}s"
        return (
            f"{self.suite}: {self.passed} passed, {self.failed} failed, "
            f"{self.errors} errors, {self.skipped} skipped "
            f"({round(self.duration_seconds, 1)}s)"
        )


@dataclass
class CoverageReport(QaModel):
    """Coverage read from a report the project already generated."""

    percent: float = 0.0
    source: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Findings and verdicts
# -----------------------------------------------------------------------------
@dataclass
class QaFinding(QaModel):
    """One quality finding, with the evidence and the fix."""

    rule_id: str = ""
    title: str = ""
    severity: QaSeverity = QaSeverity.MEDIUM
    category: QaCategory = QaCategory.TESTS
    path: Optional[str] = None
    line: Optional[int] = None
    evidence: Optional[str] = None
    remediation: Optional[str] = None


@dataclass
class QaVerdict(QaModel):
    """The deterministic answer of the QA subsystem."""

    domain: QaDomain = QaDomain.CHANGE_REVIEW
    action: QaAction = QaAction.ALLOW
    passed: bool = True
    blocked: bool = False
    allowed: bool = True
    requires_approval: bool = False
    highest_severity: Optional[QaSeverity] = None
    findings: List[QaFinding] = field(default_factory=list)
    runs: List[TestRun] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    @property
    def rule_ids(self) -> List[str]:
        """Return the distinct rule ids of the findings, in order."""
        seen: List[str] = []
        for finding in self.findings:
            if finding.rule_id not in seen:
                seen.append(finding.rule_id)
        return seen
