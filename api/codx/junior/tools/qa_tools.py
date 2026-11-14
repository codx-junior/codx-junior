"""QA tools.

These are the tools the ``qa`` profiles (and any profile granted them) use to
validate a change before it is delivered: plan the verification, run the suites,
review the diff, read the coverage, look for flaky tests and inspect the policy.

The deterministic machinery lives in :mod:`codx.junior.quality.qa`; a tool is an
explicit action a profile takes, while :func:`codx.junior.quality.qa.guard_change`
is the guarantee a process can rely on.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, List, Optional

from .model import ToolResponse
from codx.junior.quality.qa import (
    QaCategory,
    QaDomain,
    QaFinding,
    QaSeverity,
    coverage_summary,
    detect_suites,
    evaluate,
    guard_change,
    guard_flaky,
    guard_tests,
    map_tests,
    affected_tests,
    changed_files,
    collect_test_files,
    is_ignored,
    is_test_file,
    policy_markdown,
    read_coverage,
    record_verdict,
    render_report,
    resolve_policy,
    select_suite,
    stored_policy,
    suite_catalog,
)
from codx.junior.quality.qa import policy as qa_policy_module

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _settings_of(kwargs: Dict[str, Any]):
    """Return the project settings injected by the runtime (or ``None``)."""
    return kwargs.get("settings")


def _project_path(settings: Any) -> str:
    """Return the absolute project path of *settings*."""
    return getattr(settings, "abs_project_path", None) or os.getcwd()


def _verdict_answer(verdict, title: str) -> str:
    """Render a verdict as the user facing markdown report."""
    return render_report(verdict, title=title)


def _llm_summary(verdict, subject: str) -> str:
    """Build the compact machine friendly summary for the model."""
    lines = [
        f"# {subject}",
        f"- decision: {verdict.action.value}",
        f"- passed: {verdict.passed}",
        f"- blocked: {verdict.blocked}",
        f"- requires_approval: {verdict.requires_approval}",
        f"- highest_severity: {(verdict.highest_severity.value if verdict.highest_severity else 'none')}",
        f"- findings: {len(verdict.findings)}",
    ]
    for run in verdict.runs:
        lines.append(f"- run `{run.suite}`: {run.summary}")
    for key, value in verdict.metrics.items():
        lines.append(f"- {key}: {value}")
    for finding in verdict.findings[:10]:
        where = finding.path or "-"
        if finding.path and finding.line:
            where = f"{finding.path}:{finding.line}"
        lines.append(
            f"- [{finding.severity.value}] {finding.rule_id} @ {where}: "
            f"{finding.remediation or finding.title}"
        )
    if len(verdict.findings) > 10:
        lines.append(f"- … {len(verdict.findings) - 10} more finding(s)")
    return "\n".join(lines)


def _logs_of(verdict) -> List[str]:
    """Build the forensic log lines of a verdict."""
    logs = [f"QA verdict: {verdict.summary}"]
    for run in verdict.runs:
        logs.append(f"QA run ({run.suite}): {' '.join(run.command)} -> {run.summary}")
    for finding in verdict.findings[:25]:
        logs.append(
            f"[{finding.severity.value.upper()}] {finding.rule_id} "
            f"{finding.path or '-'}:{finding.line or 0} — {finding.title}"
        )
    return logs


# -----------------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------------
def qa_plan(
    base: str = "HEAD",
    scope: str = "all",
    paths: Optional[List[str]] = None,
    **kwargs
) -> ToolResponse:
    """Plan the verification of a change: suites, tests and coverage.

    Tells the caller what can be executed (and what cannot), which tests map to
    the pending change, which changed files have no test at all and whether a
    coverage report is available. It never executes anything.

    Args:
        base: Git reference to compare against (default ``HEAD``).
        scope: ``all``, ``staged`` or ``unstaged``.
        paths: Optional path filter for the change.
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Markdown plan plus a compact summary.

    Example:
        >>> qa_plan()  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    policy = resolve_policy(settings)
    project_path = _project_path(settings)

    suites = detect_suites(project_path, policy)
    default_suite = select_suite(suites)

    try:
        changed = changed_files(settings, base=base, scope=scope)
    except Exception as ex:
        changed = []
        change_error = str(ex)
    else:
        change_error = ""

    if paths:
        filters = [path.replace("\\", "/").lstrip("./") for path in paths]
        changed = [
            path for path in changed
            if any(path == f or path.startswith(f.rstrip("/") + "/") for f in filters)
        ]
    changed = [path for path in changed if not is_ignored(path, policy.ignore_paths)]

    test_files = collect_test_files(project_path)
    mapped, unmapped = map_tests(changed, test_files)
    selected = affected_tests(mapped, test_files)
    report = read_coverage(project_path)

    lines: List[str] = [
        "# QA plan",
        "",
        f"- Policy: **{'enabled' if policy.enabled else 'disabled'}** · "
        f"coverage threshold **{policy.min_coverage}%** · "
        f"block above `{policy.block_above.value}`",
        f"- Suites detected: **{len(suites)}** · default: "
        f"`{default_suite.name if default_suite else 'none'}`",
        "",
        "## Suites",
    ]
    if suites:
        lines += ["| Suite | Kind | cwd | Runs here | Command |", "| --- | --- | --- | --- | --- |"]
        for suite in suites:
            available = "yes" if suite.available else f"no ({suite.reason})"
            lines.append(
                f"| `{suite.name}` | `{suite.kind}` | `{suite.cwd or '.'}` | {available} | "
                f"`{' '.join(suite.command)}` |"
            )
    else:
        lines.append("_No test suite detected._")

    lines += ["", "## Change", ""]
    if change_error:
        lines.append(f"⚠️ Could not read the change: {change_error}")
    elif not changed:
        lines.append("_No pending change._")
    else:
        lines += [
            f"- Changed files: **{len(changed)}** ({sum(1 for c in changed if is_test_file(c))} test file(s))",
            f"- Source files with a mapped test: **{len(mapped)}**",
            f"- Source files without a test: **{len(unmapped)}**",
        ]
        if mapped:
            lines += ["", "| Source | Tests |", "| --- | --- |"]
            for source, tests in list(mapped.items())[:15]:
                lines.append(f"| `{source}` | {', '.join(f'`{t}`' for t in tests)} |")
        if unmapped:
            lines += ["", "Files with no mapped test:"]
            lines += [f"- `{path}`" for path in unmapped[:15]]
            if len(unmapped) > 15:
                lines.append(f"- … {len(unmapped) - 15} more")

    lines += ["", "## Coverage", "", f"- {coverage_summary(report)}"]
    lines += [
        "",
        "## Recommended next steps",
        "",
        (
            f"1. `qa_run_tests` with `paths` = the {len(selected)} mapped test(s)."
            if selected else "1. `qa_run_tests` on the default suite."
        ),
        "2. `qa_review_changes` to get the gate verdict (tests + mapping + coverage).",
        "3. `qa_flaky_check` when a failure does not reproduce.",
    ]

    llm = "\n".join(
        [
            "# QA plan",
            f"- suites: {', '.join(s.name for s in suites) or 'none'}",
            f"- default_suite: {default_suite.name if default_suite else 'none'}",
            f"- changed_files: {len(changed)}",
            f"- mapped_tests: {len(selected)}",
            f"- unmapped_sources: {len(unmapped)}",
            f"- coverage: {coverage_summary(report)}",
            f"- suites_json: {suite_catalog(suites)}",
            f"- unmapped: {unmapped[:20]}",
        ]
    )
    return ToolResponse(
        user_response="\n".join(lines),
        llm_response=llm,
        logs=[f"QA plan: {len(suites)} suite(s), {len(changed)} changed file(s)"],
    )


def qa_run_tests(
    suite: str = "auto",
    paths: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    **kwargs
) -> ToolResponse:
    """Run a test suite and return the verdict of the run.

    The command is a declared argv (detected suite or ``qa_policy.suites``),
    executed without a shell, bounded by a timeout and validated by the CISO
    command gate.

    Args:
        suite: Suite name, kind or ``auto`` for the project default.
        paths: Optional test files or folders to restrict the run.
        timeout: Seconds before the run is killed (policy default when omitted).
        extra_args: Extra arguments (validated; shell metacharacters refused).
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Report with the failures and the counters.

    Example:
        >>> qa_run_tests(paths=["api/tests/test_atlas.py"])  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    verdict = guard_tests(
        settings,
        suite_name=suite,
        paths=paths,
        timeout=timeout,
        extra_args=extra_args,
    )
    title = f"QA tests — `{suite or 'auto'}`"
    return ToolResponse(
        user_response=_verdict_answer(verdict, title),
        llm_response=_llm_summary(verdict, "QA test run"),
        logs=_logs_of(verdict),
    )


def qa_review_changes(
    base: str = "HEAD",
    scope: str = "all",
    paths: Optional[List[str]] = None,
    run_tests: bool = True,
    suite: str = "auto",
    **kwargs
) -> ToolResponse:
    """Review a change against the QA gate (tests, mapping, coverage).

    This is the gate a profile must call before it reports a change as verified:
    it reads the diff, maps every changed source file to a test, runs the mapped
    tests and checks the coverage threshold.

    Args:
        base: Git reference to compare against (default ``HEAD``).
        scope: ``all`` (default), ``staged`` or ``unstaged``.
        paths: Optional path filter for the change.
        run_tests: Execute the mapped tests (default True).
        suite: Suite to execute (``auto`` uses the detected one).
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Verdict of the change, ready to paste in a delivery report.

    Example:
        >>> qa_review_changes(scope="staged")  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    verdict = guard_change(
        settings,
        base=base,
        scope=scope,
        paths=paths,
        run_tests=run_tests,
        suite_name=suite,
    )
    title = f"QA change review — scope `{scope}` vs `{base}`"
    return ToolResponse(
        user_response=_verdict_answer(verdict, title),
        llm_response=_llm_summary(verdict, "QA change review"),
        logs=_logs_of(verdict),
    )


def qa_coverage(
    min_percent: Optional[float] = None,
    paths: Optional[List[str]] = None,
    **kwargs
) -> ToolResponse:
    """Read the coverage report of the project and compare it with the threshold.

    Args:
        min_percent: Override the threshold of the policy for this check.
        paths: Optional explicit report paths (``coverage.xml``, ``coverage.json``).
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Coverage verdict.

    Example:
        >>> qa_coverage()  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    policy = resolve_policy(settings)
    project_path = _project_path(settings)
    threshold = float(min_percent if min_percent is not None else policy.min_coverage)

    report = read_coverage(project_path, paths=paths)
    findings: List[QaFinding] = []
    metrics: Dict[str, Any] = {"threshold": threshold}

    if not report:
        findings.append(
            QaFinding(
                rule_id="coverage.unavailable",
                title="No coverage report found in the project",
                severity=QaSeverity.LOW,
                category=QaCategory.COVERAGE,
                remediation=(
                    "Generate a report in the test step: `pytest --cov=app "
                    "--cov-report=xml` or `npm test -- --coverage`."
                ),
            )
        )
    else:
        metrics["coverage"] = report.percent
        metrics["source"] = report.source
        if report.percent < threshold:
            findings.append(
                QaFinding(
                    rule_id="coverage.below-threshold",
                    title=f"Coverage {report.percent}% is below the {threshold}% threshold",
                    severity=QaSeverity.MEDIUM,
                    category=QaCategory.COVERAGE,
                    path=report.source,
                    evidence=coverage_summary(report),
                    remediation="Cover the new branches, or lower the threshold with a dated decision.",
                )
            )

    verdict = evaluate(QaDomain.COVERAGE, findings, policy, metrics=metrics)
    record_verdict(settings, verdict, f"coverage:{report.source if report else 'none'}")
    if not findings:
        verdict.summary = f"clean: coverage {report.percent}% >= {threshold}%"

    return ToolResponse(
        user_response=_verdict_answer(verdict, "QA coverage"),
        llm_response=_llm_summary(verdict, "QA coverage"),
        logs=_logs_of(verdict),
    )


def qa_flaky_check(
    suite: str = "auto",
    paths: Optional[List[str]] = None,
    repeat: Optional[int] = None,
    **kwargs
) -> ToolResponse:
    """Re-run a suite several times and report the unstable tests.

    Args:
        suite: Suite name, kind or ``auto`` for the project default.
        paths: Optional test files or folders to restrict the runs.
        repeat: Number of runs (policy ``flaky_repeats`` when omitted).
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: Verdict listing the tests that do not reproduce.

    Example:
        >>> qa_flaky_check(repeat=3)  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    verdict = guard_flaky(settings, suite_name=suite, paths=paths, repeats=repeat)
    return ToolResponse(
        user_response=_verdict_answer(verdict, f"QA flaky check — `{suite or 'auto'}`"),
        llm_response=_llm_summary(verdict, "QA flaky check"),
        logs=_logs_of(verdict),
    )


def qa_policy(
    action: str = "show",
    changes: Optional[Dict[str, Any]] = None,
    **kwargs
) -> ToolResponse:
    """Show or adjust the QA policy of the project.

    The policy is stored in the project settings under ``qa_policy``. Every
    field is validated against the model before it is written.

    Args:
        action: ``show`` (default) to display the effective policy.
        changes: Partial policy to merge (``action='show'`` ignores it). Fields:
            ``enabled``, ``min_coverage``, ``block_above``, ``approval_above``,
            ``warn_above``, ``require_tests_for_changes``, ``block_on_failures``,
            ``block_missing_tests``, ``fail_closed``, ``max_test_seconds``,
            ``flaky_repeats``, ``ignore_paths``, ``suites``.
        **kwargs: Runtime arguments; ``settings`` is injected automatically.

    Returns:
        ToolResponse: The effective policy as Markdown.

    Example:
        >>> qa_policy()  # doctest: +SKIP
    """
    settings = _settings_of(kwargs)
    if not settings:
        raise Exception("Invalid project settings")

    stored = stored_policy(settings)
    if changes and action == "show":
        merged = dict(stored)
        merged.update(changes or {})
        validated = qa_policy_module.policy_from_dict(merged)
        configured = getattr(settings, "settings", None)
        if isinstance(configured, dict):
            configured[qa_policy_module.POLICY_SETTINGS_KEY] = validated.to_dict()
            save = getattr(settings, "save", None)
            if callable(save):
                try:
                    save()
                except Exception as ex:
                    return ToolResponse(
                        user_response=f"⚠️ Could not persist the policy: {ex}",
                        llm_response=f"qa_policy: persist failed: {ex}",
                    )
            stored = dict(merged)
        else:
            return ToolResponse(
                user_response="⚠️ This project settings object does not expose a writable `settings` dict.",
                llm_response="qa_policy: unsupported settings object",
            )

    policy = resolve_policy(settings)
    return ToolResponse(
        user_response=policy_markdown(policy, stored),
        llm_response=(
            f"QA policy: enabled={policy.enabled} min_coverage={policy.min_coverage} "
            f"block_above={policy.block_above.value} fail_closed={policy.fail_closed}"
        ),
        logs=["QA policy reported"],
    )


# -----------------------------------------------------------------------------
# Tool JSON schemas
# -----------------------------------------------------------------------------
QA_PLAN_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_plan",
        "description": (
            "Plan the verification of a change without executing anything: which test "
            "suites exist (and which can actually run), why, which tests map to the "
            "pending change, which changed files have no test, and whether a coverage "
            "report is available. Call it before qa_run_tests / qa_review_changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base": {
                    "type": "string",
                    "description": "Git reference to compare against (default HEAD)."
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "staged", "unstaged"],
                    "description": "Which change to inspect (default all)."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional path filter for the change."
                }
            },
            "required": []
        }
    }
}

QA_RUN_TESTS_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_run_tests",
        "description": (
            "Run a test suite of the project and return a QA verdict: counters, failing "
            "test ids, timeout and duration. The command is a declared argv executed "
            "without a shell and validated by the CISO command gate. Use 'paths' to run "
            "only the tests mapped to a change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite": {
                    "type": "string",
                    "description": "Suite name, suite kind or 'auto' for the project default."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional test files or folders to restrict the run."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before the run is killed (default: QA policy)."
                },
                "extra_args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Extra arguments appended to the suite command (e.g. ['-k','login']). "
                        "Shell metacharacters are refused."
                    )
                }
            },
            "required": []
        }
    }
}

QA_REVIEW_CHANGES_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_review_changes",
        "description": (
            "The QA gate for a change: maps every changed source file to the tests that "
            "name it, runs those tests, checks the coverage threshold and returns one "
            "verdict (allow / warn / require_approval / block). Call it before reporting "
            "a change as done or merging a PR."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base": {
                    "type": "string",
                    "description": "Git reference to compare against (default HEAD)."
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "staged", "unstaged"],
                    "description": "Which change to review (default all)."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional path filter for the change."
                },
                "run_tests": {
                    "type": "boolean",
                    "description": "Execute the mapped tests (default true)."
                },
                "suite": {
                    "type": "string",
                    "description": "Suite to execute (default 'auto')."
                }
            },
            "required": []
        }
    }
}

QA_COVERAGE_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_coverage",
        "description": (
            "Read the coverage report the project already generated (coverage.xml, "
            "coverage.json or an istanbul coverage-summary.json) and compare it with the "
            "threshold of the QA policy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "min_percent": {
                    "type": "number",
                    "description": "Override the coverage threshold for this check."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit report paths."
                }
            },
            "required": []
        }
    }
}

QA_FLAKY_CHECK_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_flaky_check",
        "description": (
            "Re-run a suite several times and list the tests that fail in some runs but "
            "not in all of them. Use it when a reported failure does not reproduce."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite": {
                    "type": "string",
                    "description": "Suite name, suite kind or 'auto' for the project default."
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional test files or folders to restrict the runs."
                },
                "repeat": {
                    "type": "integer",
                    "description": "Number of runs (default: QA policy flaky_repeats)."
                }
            },
            "required": []
        }
    }
}

QA_POLICY_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "qa_policy",
        "description": (
            "Show the effective QA policy of the project (thresholds, severity ladder, "
            "ignored paths, custom suites) and optionally merge validated changes into "
            "the project settings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["show"],
                    "description": "Only 'show' is supported today."
                },
                "changes": {
                    "type": "object",
                    "description": (
                        "Partial policy to persist: enabled, min_coverage, block_above, "
                        "approval_above, warn_above, require_tests_for_changes, "
                        "block_on_failures, block_missing_tests, fail_closed, "
                        "max_test_seconds, flaky_repeats, ignore_paths, suites."
                    )
                }
            },
            "required": []
        }
    }
}
