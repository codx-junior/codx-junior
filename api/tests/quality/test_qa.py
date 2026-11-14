"""Unit tests of the QA subsystem (pytest).

The bare-interpreter checks live in ``qa_selfcheck.py``; this module adds the
parts that need a real git repository and the filesystem fixtures pytest gives
us.

Made with ❤️ by codx-junior
"""

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from codx.junior.quality.qa import (
    QaDomain,
    QaFinding,
    QaSeverity,
    QaSuite,
    coverage_summary,
    detect_suites,
    evaluate,
    guard_change,
    map_tests,
    read_coverage,
    resolve_policy,
    run_suite,
    select_suite,
    validate_args,
)

GIT = shutil.which("git")


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------
def _settings(project_path: str, **qa_policy) -> SimpleNamespace:
    """Build a minimal project settings object for the QA gates."""
    codx_path = os.path.join(project_path, ".codx")
    os.makedirs(os.path.join(codx_path, "harness"), exist_ok=True)
    return SimpleNamespace(
        abs_project_path=project_path,
        codx_path=codx_path,
        project_name="qa-test",
        settings={"qa_policy": qa_policy} if qa_policy else {},
    )


def _git(project_path: str, *args: str) -> None:
    """Run git in *project_path*, failing the test on error."""
    subprocess.run(
        ["git", *args],
        cwd=project_path,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "QA",
            "GIT_AUTHOR_EMAIL": "qa@example.com",
            "GIT_COMMITTER_NAME": "QA",
            "GIT_COMMITTER_EMAIL": "qa@example.com",
        },
    )


@pytest.fixture()
def git_project(tmp_path):
    """Return a git repository with one committed source file."""
    if not GIT:
        pytest.skip("git is not available")
    _git(str(tmp_path), "init", "-q")
    source = tmp_path / "wallet.py"
    source.write_text("def pay():\n    return 1\n", encoding="utf-8")
    _git(str(tmp_path), "add", "-A")
    _git(str(tmp_path), "commit", "-qm", "initial")
    return tmp_path


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------
def test_policy_defaults_and_overrides():
    policy = resolve_policy(_settings("/tmp", min_coverage=55))
    assert policy.enabled is True
    assert policy.min_coverage == 55.0


def test_ladder_orders_severities():
    assert evaluate(QaDomain.TEST_RUN, []).action.value == "allow"
    high = evaluate(
        QaDomain.TEST_RUN,
        [QaFinding(rule_id="tests.failed", title="x", severity=QaSeverity.HIGH)],
    )
    assert high.blocked is True and high.passed is False
    medium = evaluate(
        QaDomain.CHANGE_REVIEW,
        [QaFinding(rule_id="change.missing-tests", title="x", severity=QaSeverity.MEDIUM)],
    )
    assert medium.requires_approval is True


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
def test_validate_args_refuses_shell_metacharacters():
    assert validate_args(["-k", "login"]) == (["-k", "login"], None)
    for bad in ("a;b", "$(id)", "a|b", "a>b"):
        accepted, error = validate_args([bad])
        assert accepted == [] and error


def test_run_suite_parses_a_real_command():
    suite = QaSuite(name="unit", kind="pytest", command=[sys.executable, "-c", "print('2 passed')"])
    run = run_suite(suite, os.getcwd())
    assert run.ok is True
    assert run.passed == 2


def test_run_suite_reports_failures():
    code = "print('1 failed, 1 passed');print('FAILED t.py::test_x - nope');raise SystemExit(1)"
    suite = QaSuite(name="unit", kind="pytest", command=[sys.executable, "-c", code])
    run = run_suite(suite, os.getcwd())
    assert run.ok is False
    assert run.failed == 1 and run.failures[0].id == "t.py::test_x"


# -----------------------------------------------------------------------------
# Mapping and coverage
# -----------------------------------------------------------------------------
def test_map_tests_reports_unmapped_sources():
    mapping, unmapped = map_tests(
        ["app/wallet.py", "app/ghost.py", "app/readme.md"],
        ["tests/test_wallet.py"],
    )
    assert mapping == {"app/wallet.py": ["tests/test_wallet.py"]}
    assert unmapped == ["app/ghost.py"]


def test_read_coverage_prefers_the_newest_report(tmp_path):
    (tmp_path / "coverage.xml").write_text('<coverage line-rate="0.5"></coverage>', encoding="utf-8")
    report = read_coverage(str(tmp_path))
    assert report is not None and report.percent == 50.0
    assert "50.0" in coverage_summary(report)


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------
def test_detect_suites_finds_pytest_and_npm(tmp_path):
    api = tmp_path / "api"
    (api / "tests").mkdir(parents=True)
    (api / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    client = tmp_path / "client"
    client.mkdir()
    (client / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")

    suites = detect_suites(str(tmp_path))
    names = {suite.name for suite in suites}
    assert {"pytest:api", "npm:client"} <= names
    assert select_suite(suites, "pytest").cwd == "api"


# -----------------------------------------------------------------------------
# Gate
# -----------------------------------------------------------------------------
def test_guard_change_flags_a_source_without_tests(git_project):
    settings = _settings(str(git_project))
    (git_project / "wallet.py").write_text("def pay():\n    return 2\n", encoding="utf-8")

    verdict = guard_change(settings, run_tests=False)
    assert verdict.domain == QaDomain.CHANGE_REVIEW
    assert "change.missing-tests" in verdict.rule_ids
    assert verdict.action.value in ("require_approval", "block")


def test_guard_change_passes_when_the_mapped_test_runs(git_project):
    settings = _settings(
        str(git_project),
        suites={"unit": {"command": [sys.executable, "-c", "print('1 passed')"], "kind": "custom"}},
        require_tests_for_changes=True,
    )
    tests = git_project / "tests"
    tests.mkdir()
    (tests / "test_wallet.py").write_text("def test_pay():\n    assert True\n", encoding="utf-8")
    (git_project / "wallet.py").write_text("def pay():\n    return 3\n", encoding="utf-8")

    verdict = guard_change(settings, suite_name="unit")
    assert "tests.failed" not in verdict.rule_ids, verdict.summary
    assert verdict.blocked is False
    assert verdict.runs and verdict.runs[0].suite == "unit"
    assert verdict.runs[0].passed == 1
