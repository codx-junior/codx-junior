"""QA gates: the single call a process makes before it claims a change works.

| Gate             | Process it protects                                   |
| ---------------- | ----------------------------------------------------- |
| ``guard_tests``  | running a suite (test run domain)                     |
| ``guard_change`` | reviewing a change before it is merged or delivered   |

Every gate returns a :class:`~codx.junior.quality.qa.models.QaVerdict` and never
raises. When a check itself fails the verdict is a warning, unless the policy
sets ``fail_closed``.

The verdict is recorded in ``.codx/harness/audit.jsonl`` with the event
``qa_verdict``, so the delivery report and the harness journal tell the same
story.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, List, Optional

from codx.junior.quality.qa import coverage as qa_coverage
from codx.junior.quality.qa import discovery, flaky as qa_flaky, mapping, policy as qa_policy, runner
from codx.junior.quality.qa.models import (
    QaAction,
    QaCategory,
    QaDomain,
    QaFinding,
    QaPolicy,
    QaSeverity,
    QaVerdict,
    TestRun,
)

logger = logging.getLogger(__name__)

#: Audit event name used for every QA decision.
AUDIT_EVENT = "qa_verdict"

#: Maximum number of finding summaries recorded per audit entry.
AUDIT_MAX_FINDINGS = 20


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _project_path(settings: Any) -> str:
    """Return the absolute project path of *settings*."""
    return getattr(settings, "abs_project_path", None) or os.getcwd()


def is_enabled(settings: Any = None) -> bool:
    """Return True when the QA subsystem is active for *settings*."""
    try:
        return qa_policy.resolve_policy(settings).enabled
    except Exception:  # pragma: no cover - defensive
        return True


def _audit(
    settings: Any,
    verdict: QaVerdict,
    subject: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append the verdict to the project audit journal (best effort)."""
    codx_path = getattr(settings, "codx_path", None)
    if not codx_path:
        return
    try:
        from codx.junior.harness.audit import record_audit

        record_audit(
            codx_path,
            AUDIT_EVENT,
            domain=verdict.domain.value,
            action=verdict.action.value,
            passed=verdict.passed,
            blocked=verdict.blocked,
            requires_approval=verdict.requires_approval,
            highest_severity=(verdict.highest_severity.value if verdict.highest_severity else None),
            subject=subject,
            summary=verdict.summary,
            runs=[run.summary for run in verdict.runs],
            findings=[
                {
                    "rule": finding.rule_id,
                    "severity": finding.severity.value,
                    "category": finding.category.value,
                    "path": finding.path,
                    "line": finding.line,
                }
                for finding in verdict.findings[:AUDIT_MAX_FINDINGS]
            ],
            **(extra or {}),
        )
    except Exception as ex:  # pragma: no cover - audit must never break work
        logger.debug("QA: could not record the audit entry: %s", ex)


def record_verdict(
    settings: Any,
    verdict: QaVerdict,
    subject: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a verdict produced outside this module (best effort).

    Args:
        settings: Project settings (used to locate ``.codx``).
        verdict: Verdict to record.
        subject: What was inspected.
        extra: Extra fields to attach to the audit entry.
    """
    _audit(settings, verdict, subject, extra)


def _fail(policy: QaPolicy, domain: QaDomain, error: Exception) -> QaVerdict:
    """Build the verdict used when a check raised."""
    logger.warning("QA gate (%s) failed: %s", domain.value, error)
    finding = QaFinding(
        rule_id="qa.check-failed",
        title=f"The QA check could not run: {error}",
        severity=QaSeverity.HIGH if policy.fail_closed else QaSeverity.LOW,
        category=QaCategory.ENVIRONMENT,
        remediation="Fix the environment (git, interpreter, permissions) and run the check again.",
    )
    verdict = qa_policy.evaluate(domain, [finding], policy)
    if policy.fail_closed:
        verdict.action = QaAction.BLOCK
        verdict.blocked = True
        verdict.allowed = False
        verdict.passed = False
        verdict.summary = f"blocked: the QA check failed ({type(error).__name__})"
    return verdict


def notice(verdict: QaVerdict, subject: str = "") -> str:
    """Return a one line, user facing notice describing *verdict*.

    Args:
        verdict: Decision to describe.
        subject: What was inspected (shown when the verdict is not clean).

    Returns:
        str: ``""`` when the verdict is clean, otherwise a Markdown line.
    """
    if verdict.action == QaAction.ALLOW:
        return ""
    icon = {
        QaAction.WARN: "⚠️",
        QaAction.REQUIRE_APPROVAL: "🟠",
        QaAction.BLOCK: "⛔",
    }.get(verdict.action, "•")
    target = f" `{subject}`" if subject else ""
    rules = ", ".join(f"`{rule}`" for rule in verdict.rule_ids[:5])
    return f"{icon} QA `{verdict.action.value}`{target} — {rules}"


def _finding_for_run(run: TestRun, policy: QaPolicy) -> List[QaFinding]:
    """Turn one test run into findings."""
    findings: List[QaFinding] = []

    if run.timed_out:
        findings.append(
            QaFinding(
                rule_id="tests.timeout",
                title=f"`{run.suite}` timed out after {round(run.duration_seconds)}s",
                severity=QaSeverity.HIGH,
                category=QaCategory.TESTS,
                remediation=(
                    "Split the suite, mark the slow tests, or raise `max_test_seconds` "
                    "in the QA policy."
                ),
            )
        )
        return findings

    if run.error:
        no_runner = any(
            token in run.error.lower()
            for token in ("not found", "no module named", "command not found", "not available")
        )
        findings.append(
            QaFinding(
                rule_id="tests.no-runner" if no_runner else "tests.run-error",
                title=f"`{run.suite}` did not run: {run.error}",
                severity=QaSeverity.HIGH if no_runner else QaSeverity.MEDIUM,
                category=QaCategory.ENVIRONMENT if no_runner else QaCategory.TESTS,
                remediation=(
                    "Install the suite's dependencies (e.g. `pip install -e '.[test]'`) or "
                    "declare the right command in `qa_policy.suites`."
                    if no_runner
                    else "Inspect the command output and fix the environment, then re-run."
                ),
            )
        )
        return findings

    if run.failed or run.errors:
        severity = QaSeverity.CRITICAL if policy.block_on_failures else QaSeverity.MEDIUM
        for failure in run.failures[:10] or [None]:
            identifier = failure.id if failure else run.suite
            message = (failure.message if failure else "") or f"{run.failed} failed, {run.errors} errors"
            findings.append(
                QaFinding(
                    rule_id="tests.failed",
                    title=f"`{identifier}` fails: {message}",
                    severity=severity,
                    category=QaCategory.TESTS,
                    evidence=message,
                    remediation=(
                        "Fix the behaviour or update the expectation, then re-run the suite. "
                        "Never delete a failing test to pass the gate."
                    ),
                )
            )
    elif run.total == 0:
        findings.append(
            QaFinding(
                rule_id="tests.no-tests",
                title=f"`{run.suite}` ran without executing any test",
                severity=QaSeverity.LOW,
                category=QaCategory.TESTS,
                evidence=" ".join(run.command),
                remediation="Point the suite at a real test folder or remove it from the policy.",
            )
        )
    return findings


# -----------------------------------------------------------------------------
# Gates
# -----------------------------------------------------------------------------
def guard_tests(
    settings: Any,
    suite_name: Optional[str] = None,
    paths: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> QaVerdict:
    """Run a suite and return the verdict of the run.

    Args:
        settings: Project settings.
        suite_name: Suite name, kind or ``None``/``auto`` for the default.
        paths: Optional test files / folders to restrict the run.
        timeout: Seconds before the run is killed (policy default when omitted).
        extra_args: Extra arguments (validated by the runner).

    Returns:
        QaVerdict: Verdict of domain ``test_run``.
    """
    policy = qa_policy.resolve_policy(settings)
    if not policy.enabled:
        return QaVerdict(domain=QaDomain.TEST_RUN, summary="disabled: QA policy is off")

    project_path = _project_path(settings)
    try:
        suites = discovery.detect_suites(project_path, policy)
        suite = discovery.select_suite(suites, suite_name)
        if not suite:
            finding = QaFinding(
                rule_id="tests.no-suite",
                title="No test suite could be detected in this project",
                severity=QaSeverity.HIGH,
                category=QaCategory.ENVIRONMENT,
                remediation=(
                    "Add a pytest/npm test setup, or declare the command in "
                    "``qa_policy.suites``: {\"unit\": {\"command\": [\"make\", \"test\"]}}."
                ),
            )
            verdict = qa_policy.evaluate(QaDomain.TEST_RUN, [finding], policy)
            _audit(settings, verdict, "suite:auto")
            return verdict

        run = runner.run_suite(
            suite,
            project_path,
            paths=paths,
            timeout=timeout or policy.max_test_seconds,
            extra_args=extra_args,
            settings=settings,
        )
        findings = _finding_for_run(run, policy)
        verdict = qa_policy.evaluate(
            QaDomain.TEST_RUN,
            findings,
            policy,
            runs=[run],
            metrics={
                "suite": run.suite,
                "passed": run.passed,
                "failed": run.failed,
                "errors": run.errors,
                "skipped": run.skipped,
                "seconds": round(run.duration_seconds, 1),
            },
        )
        _audit(settings, verdict, f"suite:{run.suite}")
        return verdict
    except Exception as ex:
        return _fail(policy, QaDomain.TEST_RUN, ex)


def guard_flaky(
    settings: Any,
    suite_name: Optional[str] = None,
    paths: Optional[List[str]] = None,
    repeats: Optional[int] = None,
) -> QaVerdict:
    """Re-run a suite and report the unstable tests.

    Args:
        settings: Project settings.
        suite_name: Suite name, kind or ``None``/``auto`` for the default.
        paths: Optional test files / folders to restrict the runs.
        repeats: Number of runs (policy ``flaky_repeats`` when omitted).

    Returns:
        QaVerdict: Verdict of domain ``flaky_check``.
    """
    policy = qa_policy.resolve_policy(settings)
    if not policy.enabled:
        return QaVerdict(domain=QaDomain.FLAKY_CHECK, summary="disabled: QA policy is off")

    project_path = _project_path(settings)
    try:
        suites = discovery.detect_suites(project_path, policy)
        suite = discovery.select_suite(suites, suite_name)
        if not suite:
            finding = QaFinding(
                rule_id="tests.no-suite",
                title="No test suite could be detected in this project",
                severity=QaSeverity.HIGH,
                category=QaCategory.ENVIRONMENT,
                remediation="Add a test setup or declare the command in ``qa_policy.suites``.",
            )
            verdict = qa_policy.evaluate(QaDomain.FLAKY_CHECK, [finding], policy)
            record_verdict(settings, verdict, "suite:auto")
            return verdict

        runs = qa_flaky.detect_flaky(
            suite,
            project_path,
            paths=paths,
            repeats=repeats or policy.flaky_repeats,
            timeout=policy.max_test_seconds,
            settings=settings,
        )
        unstable = qa_flaky.flaky_results(runs)

        findings: List[QaFinding] = [
            QaFinding(
                rule_id="flaky.detected",
                title=f"`{result.id}` is unstable: {result.message}",
                severity=QaSeverity.MEDIUM,
                category=QaCategory.FLAKY,
                evidence=result.message,
                remediation=(
                    "Find the shared state, the clock, the network or the ordering that "
                    "makes the test non deterministic, or quarantine it with a dated task."
                ),
            )
            for result in unstable[:10]
        ]
        if not unstable and runs:
            findings += _finding_for_run(runs[-1], policy)

        verdict = qa_policy.evaluate(
            QaDomain.FLAKY_CHECK,
            findings,
            policy,
            runs=runs,
            metrics={"suite": suite.name, "runs": len(runs), "flaky": len(unstable)},
        )
        if not unstable and verdict.action == QaAction.ALLOW:
            verdict.summary = f"clean: {len(runs)} consecutive run(s) of `{suite.name}` are stable"
        record_verdict(settings, verdict, f"suite:{suite.name}")
        return verdict
    except Exception as ex:
        return _fail(policy, QaDomain.FLAKY_CHECK, ex)


def guard_change(
    settings: Any,
    base: str = "HEAD",
    scope: str = "all",
    paths: Optional[List[str]] = None,
    run_tests: bool = True,
    suite_name: Optional[str] = None,
) -> QaVerdict:
    """Review a change: tests present, tests pass, coverage on target.

    Args:
        settings: Project settings.
        base: Git reference to compare against.
        scope: ``all``, ``staged`` or ``unstaged``.
        paths: Optional path filter for the change.
        run_tests: Also execute the tests mapped to the change.
        suite_name: Suite to execute (default: the detected one).

    Returns:
        QaVerdict: Verdict of domain ``change_review``.
    """
    policy = qa_policy.resolve_policy(settings)
    if not policy.enabled:
        return QaVerdict(domain=QaDomain.CHANGE_REVIEW, summary="disabled: QA policy is off")

    project_path = _project_path(settings)
    try:
        changed = mapping.changed_files(settings, base=base, scope=scope)
        if paths:
            filters = [mapping.normalize(item) for item in paths]
            changed = [
                path for path in changed if any(path == f or path.startswith(f.rstrip("/") + "/") for f in filters)
            ]
        changed = [path for path in changed if not mapping.is_ignored(path, policy.ignore_paths)]

        metrics: Dict[str, Any] = {"changed_files": len(changed)}
        if not changed:
            verdict = qa_policy.evaluate(QaDomain.CHANGE_REVIEW, [], policy, metrics=metrics)
            verdict.summary = "clean: no pending change to review"
            return verdict

        test_files = mapping.collect_test_files(project_path)
        mapped, unmapped = mapping.map_tests(changed, test_files)
        metrics.update(
            {
                "test_files": len(test_files),
                "mapped_sources": len(mapped),
                "unmapped_sources": len(unmapped),
            }
        )

        findings: List[QaFinding] = []
        if policy.require_tests_for_changes:
            severity = QaSeverity.HIGH if policy.block_missing_tests else QaSeverity.MEDIUM
            for path in unmapped[:10]:
                findings.append(
                    QaFinding(
                        rule_id="change.missing-tests",
                        title="Changed behaviour without a test that names it",
                        severity=severity,
                        category=QaCategory.CHANGE,
                        path=path,
                        remediation=(
                            f"Add or extend a test for `{os.path.basename(path)}` "
                            "(name the file after the module) or declare the path in "
                            "`qa_policy.ignore_paths`."
                        ),
                    )
                )
            if len(unmapped) > 10:
                findings.append(
                    QaFinding(
                        rule_id="change.missing-tests",
                        title=f"{len(unmapped) - 10} more changed file(s) without a mapped test",
                        severity=severity,
                        category=QaCategory.CHANGE,
                        remediation="Work through the list file by file; add the tests in the same change.",
                    )
                )

        if not any(mapping.is_test_file(path) for path in changed) and mapped:
            findings.append(
                QaFinding(
                    rule_id="change.no-tests-changed",
                    title="The change touches no test file",
                    severity=QaSeverity.LOW,
                    category=QaCategory.CHANGE,
                    remediation="Update at least one test in the same change: it is the proof the change works.",
                )
            )

        runs: List[TestRun] = []
        if run_tests:
            suites = discovery.detect_suites(project_path, policy)
            suite = discovery.select_suite(suites, suite_name)
            if not suite:
                findings.append(
                    QaFinding(
                        rule_id="tests.no-suite",
                        title="No test suite could be detected: the change is unverified",
                        severity=QaSeverity.HIGH,
                        category=QaCategory.ENVIRONMENT,
                        remediation=(
                            "Add a pytest/npm test setup or declare the command in "
                            "``qa_policy.suites``."
                        ),
                    )
                )
            else:
                selected = mapping.affected_tests(mapped, test_files)
                run = runner.run_suite(
                    suite,
                    project_path,
                    paths=selected or None,
                    timeout=policy.max_test_seconds,
                    settings=settings,
                )
                runs.append(run)
                metrics["tests_selected"] = len(selected)
                findings += _finding_for_run(run, policy)

        report = qa_coverage.read_coverage(project_path)
        if report:
            metrics["coverage"] = report.percent
            if report.percent < policy.min_coverage:
                findings.append(
                    QaFinding(
                        rule_id="coverage.below-threshold",
                        title=(
                            f"Coverage {report.percent}% is below the {policy.min_coverage}% threshold"
                        ),
                        severity=QaSeverity.MEDIUM,
                        category=QaCategory.COVERAGE,
                        path=report.source,
                        evidence=qa_coverage.coverage_summary(report),
                        remediation="Cover the new branches, or lower the threshold with a dated decision.",
                    )
                )

        verdict = qa_policy.evaluate(QaDomain.CHANGE_REVIEW, findings, policy, runs=runs, metrics=metrics)
        _audit(settings, verdict, f"change:{scope}:{base}", extra={"changed_files": len(changed)})
        return verdict
    except Exception as ex:
        return _fail(policy, QaDomain.CHANGE_REVIEW, ex)
