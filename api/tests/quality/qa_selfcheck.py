"""Self-check of the QA subsystem, runnable on a bare interpreter.

It has no dependency on the API venv (no pydantic, no pytest, no LLM): every
check builds its own fixture in a temporary folder and asserts the deterministic
behaviour of the quality control plane.

    python3 api/tests/quality/qa_selfcheck.py

Exit code 0 means every check passed; 1 means at least one failed (the failing
check names are printed).

Made with ❤️ by codx-junior
"""

import json
import os
import sys
import tempfile
import traceback
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
API_ROOT = os.path.dirname(os.path.dirname(HERE))
if API_ROOT not in sys.path:
    sys.path.insert(0, API_ROOT)

from codx.junior.quality.qa import (  # noqa: E402
    QaDomain,
    QaFinding,
    QaSeverity,
    QaSuite,
    affected_tests,
    evaluate,
    guard_change,
    guard_tests,
    flaky_results,
    is_source_file,
    is_test_file,
    map_tests,
    notice,
    parse_report,
    policy_from_dict,
    render_report,
    run_suite,
    validate_args,
)
from codx.junior.quality.qa.discovery import detect_suites, select_suite  # noqa: E402
from codx.junior.quality.qa.models import TestResult, TestRun  # noqa: E402

RESULTS = []


def check(name):
    """Decorator that runs a check and records its outcome."""

    def wrapper(function):
        try:
            function()
        except Exception:
            RESULTS.append((name, False, traceback.format_exc(limit=3)))
        else:
            RESULTS.append((name, True, ""))
        return function

    return wrapper


# -----------------------------------------------------------------------------
# Policy ladder
# -----------------------------------------------------------------------------
@check("policy: defaults and coercion")
def _policy():
    policy = policy_from_dict({"min_coverage": "80", "enabled": "false", "block_above": "critical"})
    assert policy.min_coverage == 80.0, policy.min_coverage
    assert policy.enabled is False
    assert policy.block_above == QaSeverity.CRITICAL
    # Unknown values fall back to the defaults instead of raising.
    fallback = policy_from_dict({"block_above": "nonsense", "min_coverage": "abc"})
    assert fallback.block_above == QaSeverity.HIGH
    assert fallback.min_coverage == 70.0


@check("ladder: block/approval/warn/allow")
def _ladder():
    high = [QaFinding(rule_id="tests.failed", title="x", severity=QaSeverity.HIGH)]
    verdict = evaluate(QaDomain.TEST_RUN, high)
    assert verdict.action.value == "block", verdict.action
    assert verdict.blocked is True and verdict.passed is False

    medium = [QaFinding(rule_id="change.missing-tests", title="x", severity=QaSeverity.MEDIUM)]
    assert evaluate(QaDomain.CHANGE_REVIEW, medium).action.value == "require_approval"

    low = [QaFinding(rule_id="change.no-tests-changed", title="x", severity=QaSeverity.LOW)]
    assert evaluate(QaDomain.CHANGE_REVIEW, low).action.value == "warn"

    info = [QaFinding(rule_id="note", title="x", severity=QaSeverity.INFO)]
    assert evaluate(QaDomain.PLAN, info).action.value == "allow"

    clean = evaluate(QaDomain.TEST_RUN, [])
    assert clean.action.value == "allow" and clean.passed is True
    assert "allow" in render_report(clean, "t")


# -----------------------------------------------------------------------------
# Runner safety
# -----------------------------------------------------------------------------
@check("runner: refuses shell metacharacters")
def _unsafe_args():
    accepted, error = validate_args(["-k", "login"])
    assert accepted == ["-k", "login"] and error is None
    for bad in ["a;rm -rf /", "$(id)", "x|y", "`id`", "a>b", "line\nbreak"]:
        accepted, error = validate_args([bad])
        assert accepted == [] and error, bad


@check("runner: executes and parses a passing command")
def _run_ok():
    suite = QaSuite(name="trivial", kind="pytest", command=[sys.executable, "-c", "print('3 passed')"])
    run = run_suite(suite, os.getcwd())
    assert run.ok is True, run.error
    assert run.passed == 3, run.passed


@check("runner: executes and parses a failing command")
def _run_failed():
    code = "print('1 failed, 2 passed');print('FAILED tests/test_x.py::test_a - boom');raise SystemExit(1)"
    suite = QaSuite(name="trivial", kind="pytest", command=[sys.executable, "-c", code])
    run = run_suite(suite, os.getcwd())
    assert run.ok is False
    assert run.failed == 1 and run.passed == 2, (run.failed, run.passed)
    assert run.failures and "test_a" in run.failures[0].id, run.failures


@check("runner: missing command never raises")
def _run_missing():
    suite = QaSuite(name="nope", kind="custom", command=["definitely-not-a-command-xyz"])
    run = run_suite(suite, os.getcwd())
    assert run.ok is False and "not found" in run.error, run.error


# -----------------------------------------------------------------------------
# Mapping
# -----------------------------------------------------------------------------
@check("mapping: source vs test classification")
def _classification():
    assert is_test_file("api/tests/test_wallet.py") is True
    assert is_test_file("client/src/components/Wallet.spec.ts") is True
    assert is_source_file("api/codx/junior/wallet.py") is True
    assert is_source_file("api/tests/test_wallet.py") is False
    assert is_source_file("docs/readme.md") is False
    assert is_source_file("node_modules/lib/index.js") is False


@check("mapping: changed files to tests")
def _mapping():
    changed = ["api/codx/wallet.py", "api/codx/ghost_module.py", "api/tests/test_wallet.py", "README.md"]
    tests = ["api/tests/test_wallet.py", "api/tests/test_other.py"]
    mapping, unmapped = map_tests(changed, tests)
    assert mapping.get("api/codx/wallet.py") == ["api/tests/test_wallet.py"], mapping
    assert "api/codx/ghost_module.py" in unmapped, unmapped
    assert "README.md" not in unmapped
    assert affected_tests(mapping, tests) == ["api/tests/test_wallet.py"]


# -----------------------------------------------------------------------------
# Coverage
# -----------------------------------------------------------------------------
@check("coverage: xml and json reports")
def _coverage():
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = os.path.join(tmp, "coverage.xml")
        with open(xml_path, "w", encoding="utf-8") as handle:
            handle.write('<coverage line-rate="0.42" version="7"></coverage>')
        report = parse_report(xml_path)
        assert report and report.percent == 42.0, report

        json_path = os.path.join(tmp, "coverage.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"totals": {"percent_covered": 88.5}}, handle)
        report = parse_report(json_path)
        assert report and report.percent == 88.5, report

        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("not json")
        assert parse_report(broken) is None


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------
@check("discovery: pytest and npm suites")
def _discovery():
    with tempfile.TemporaryDirectory() as tmp:
        package = os.path.join(tmp, "api")
        os.makedirs(os.path.join(package, "tests"))
        with open(os.path.join(package, "pyproject.toml"), "w", encoding="utf-8") as handle:
            handle.write("[project]\nname='x'\n")

        client = os.path.join(tmp, "client")
        os.makedirs(client)
        with open(os.path.join(client, "package.json"), "w", encoding="utf-8") as handle:
            json.dump({"scripts": {"test": "vitest run"}}, handle)

        suites = detect_suites(tmp)
        names = [suite.name for suite in suites]
        assert "pytest:api" in names, names
        assert "npm:client" in names, names

        pytest_suite = select_suite(suites, "pytest")
        assert pytest_suite and pytest_suite.kind == "pytest"
        assert select_suite(suites, "pytest:api").cwd == "api"
        assert select_suite(suites, "auto") is not None


# -----------------------------------------------------------------------------
# Flakiness and notices
# -----------------------------------------------------------------------------
@check("flaky: detects unstable tests")
def _flaky():
    def _run(ids):
        return TestRun(suite="s", failures=[TestResult(id=i, status="failed") for i in ids])

    unstable = flaky_results([_run(["a"]), _run([]), _run(["a"])])
    assert [result.id for result in unstable] == ["a"], unstable
    # Failing in every run is broken, not flaky.
    assert flaky_results([_run(["b"]), _run(["b"])]) == []
    assert flaky_results([]) == []


@check("report: notice is empty when clean")
def _notice():
    clean = evaluate(QaDomain.TEST_RUN, [])
    assert notice(clean) == ""
    blocked = evaluate(QaDomain.TEST_RUN, [QaFinding(rule_id="tests.failed", title="x", severity=QaSeverity.HIGH)])
    assert "block" in notice(blocked, "suite")


# -----------------------------------------------------------------------------
# Gates (the change list is injected: no git, no runtime)
# -----------------------------------------------------------------------------
def _settings(project_path, **qa_policy):
    """Build the minimal settings object the gates read."""
    codx_path = os.path.join(project_path, ".codx")
    os.makedirs(codx_path, exist_ok=True)
    return SimpleNamespace(
        abs_project_path=project_path,
        codx_path=codx_path,
        project_name="qa-selfcheck",
        settings={"qa_policy": qa_policy} if qa_policy else {},
    )


@check("gate: changed source without a test asks for approval")
def _gate_missing_tests():
    from codx.junior.quality.qa import mapping as qa_mapping

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "wallet.py"), "w", encoding="utf-8") as handle:
            handle.write("def pay():\n    return 1\n")
        original = qa_mapping.changed_files
        qa_mapping.changed_files = lambda settings, base="HEAD", scope="all": ["wallet.py"]
        try:
            verdict = guard_change(_settings(tmp), run_tests=False)
        finally:
            qa_mapping.changed_files = original
        assert "change.missing-tests" in verdict.rule_ids, verdict.rule_ids
        assert verdict.action.value == "require_approval", verdict.action
        assert verdict.passed is False and verdict.blocked is False


@check("gate: mapped test + passing suite allows the change")
def _gate_clean():
    from codx.junior.quality.qa import mapping as qa_mapping

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "tests"))
        with open(os.path.join(tmp, "wallet.py"), "w", encoding="utf-8") as handle:
            handle.write("def pay():\n    return 2\n")
        with open(os.path.join(tmp, "tests", "test_wallet.py"), "w", encoding="utf-8") as handle:
            handle.write("def test_pay():\n    assert True\n")
        settings = _settings(
            tmp,
            suites={"unit": {"command": [sys.executable, "-c", "print('1 passed')"], "kind": "custom"}},
        )
        original = qa_mapping.changed_files
        qa_mapping.changed_files = lambda settings, base="HEAD", scope="all": [
            "wallet.py",
            "tests/test_wallet.py",
        ]
        try:
            verdict = guard_change(settings, suite_name="unit")
        finally:
            qa_mapping.changed_files = original
        assert "change.missing-tests" not in verdict.rule_ids, verdict.rule_ids
        assert verdict.blocked is False, verdict.summary
        assert verdict.runs and verdict.runs[0].passed == 1, verdict.runs
        assert verdict.action.value == "allow", verdict.action


@check("gate: a failing suite blocks")
def _gate_failing_suite():
    with tempfile.TemporaryDirectory() as tmp:
        settings = _settings(
            tmp,
            suites={
                "unit": {
                    "command": [sys.executable, "-c", "print('1 failed');raise SystemExit(1)"],
                    "kind": "custom",
                }
            },
        )
        verdict = guard_tests(settings, suite_name="unit")
        assert verdict.action.value == "block", verdict.action
        assert "tests.failed" in verdict.rule_ids, verdict.rule_ids


def main() -> int:
    """Run every check and print the summary."""
    failures = [item for item in RESULTS if not item[1]]
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        if not ok:
            print(detail)
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} QA checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
