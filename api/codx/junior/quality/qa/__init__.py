"""QA — the quality control plane of codx-junior.

A deterministic quality layer for everything codx-junior writes: it discovers
the test suites of the project, runs them safely, maps a change to the tests
that name it, reads the coverage report, detects flaky tests and turns all of it
into one verdict a process can act on.

Public API
    Models
        :class:`QaPolicy`, :class:`QaVerdict`, :class:`QaFinding`,
        :class:`QaSeverity`, :class:`QaCategory`, :class:`QaDomain`,
        :class:`QaAction`, :class:`QaSuite`, :class:`TestRun`, :class:`TestResult`,
        :class:`CoverageReport`.
    Discovery
        :func:`detect_suites`, :func:`select_suite`.
    Execution
        :func:`run_suite`, :func:`build_command`, :func:`validate_args`.
    Analysis
        :func:`map_tests`, :func:`changed_files`, :func:`read_coverage`,
        :func:`detect_flaky`.
    Policy
        :func:`resolve_policy`, :func:`evaluate`, :func:`render_report`.
    Gates (the call sites use these)
        :func:`guard_tests`, :func:`guard_change`, :func:`guard_flaky`.

The map of every process that calls a gate, and the state of each call site, is
in ``codx/junior/quality/qa/README.md``.

Made with ❤️ by codx-junior
"""

from codx.junior.quality.qa.models import (
    CoverageReport,
    QaAction,
    QaCategory,
    QaDomain,
    QaFinding,
    QaPolicy,
    QaSeverity,
    QaSuite,
    QaVerdict,
    TestResult,
    TestRun,
)
from codx.junior.quality.qa.policy import (
    POLICY_SETTINGS_KEY,
    SEVERITY_RANK,
    action_for,
    evaluate,
    highest_severity,
    policy_from_dict,
    policy_markdown,
    render_report,
    resolve_policy,
    risk_score,
    stored_policy,
    summarize,
)
from codx.junior.quality.qa.discovery import (
    detect_suites,
    select_suite,
    suite_catalog,
)
from codx.junior.quality.qa.mapping import (
    affected_tests,
    changed_files,
    collect_test_files,
    is_ignored,
    is_source_file,
    is_test_file,
    map_tests,
)
from codx.junior.quality.qa.runner import build_command, run_suite, validate_args
from codx.junior.quality.qa.coverage import coverage_summary, parse_report, read_coverage
from codx.junior.quality.qa.flaky import detect_flaky, flaky_results
from codx.junior.quality.qa.gate import (
    AUDIT_EVENT,
    guard_change,
    guard_flaky,
    guard_tests,
    is_enabled,
    notice,
    record_verdict,
)

__all__ = [
    # models
    "CoverageReport",
    "QaAction",
    "QaCategory",
    "QaDomain",
    "QaFinding",
    "QaPolicy",
    "QaSeverity",
    "QaSuite",
    "QaVerdict",
    "TestResult",
    "TestRun",
    # policy
    "POLICY_SETTINGS_KEY",
    "SEVERITY_RANK",
    "action_for",
    "evaluate",
    "highest_severity",
    "policy_from_dict",
    "policy_markdown",
    "render_report",
    "resolve_policy",
    "risk_score",
    "stored_policy",
    "summarize",
    # discovery
    "detect_suites",
    "select_suite",
    "suite_catalog",
    # mapping
    "affected_tests",
    "changed_files",
    "collect_test_files",
    "is_ignored",
    "is_source_file",
    "is_test_file",
    "map_tests",
    # execution
    "build_command",
    "run_suite",
    "validate_args",
    # coverage / flaky
    "coverage_summary",
    "parse_report",
    "read_coverage",
    "detect_flaky",
    "flaky_results",
    # gates
    "AUDIT_EVENT",
    "guard_change",
    "guard_flaky",
    "guard_tests",
    "is_enabled",
    "notice",
    "record_verdict",
]
