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


def test_run_suite_reports_the_reason_when_nothing_ran():
    """A suite that dies before pytest starts echoes stderr, not a bare exit code."""
    code = (
        "import sys\n"
        "print(\"No module named 'pytest'\", file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )
    suite = QaSuite(name="unit", kind="pytest", command=[sys.executable, "-c", code])
    run = run_suite(suite, os.getcwd())
    assert run.ok is False
    assert run.total == 0
    assert "exited with code 1" in run.error
    assert "No module named 'pytest'" in run.error


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
    # Either segment of a composed name selects the suite: asking for the
    # folder must not fall through to a `tests.no-suite` block.
    assert select_suite(suites, "api").cwd == "api"
    assert select_suite(suites, "client").cwd == "client"


def test_detect_suites_uses_an_interpreter_that_exists(tmp_path):
    """Without a project virtualenv the suite runs with this very interpreter."""
    api = tmp_path / "api"
    (api / "tests").mkdir(parents=True)
    (api / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    suite = select_suite(detect_suites(str(tmp_path)), "pytest")
    assert suite.available is True
    assert suite.command[0] == sys.executable


def test_detect_suites_skips_a_venv_without_pytest(tmp_path):
    """A project virtualenv only wins when it can really import pytest."""
    api = tmp_path / "api"
    (api / "tests").mkdir(parents=True)
    (api / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    venv_bin = api / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    suite = select_suite(detect_suites(str(tmp_path)), "pytest")
    assert str(fake_python) not in suite.command
    assert suite.available is True
    assert suite.command[0] == sys.executable


def test_pytest_suite_is_unavailable_when_nothing_has_pytest(tmp_path, monkeypatch):
    """No candidate can import pytest -> the suite explains why instead of dying."""
    from codx.junior.quality.qa import discovery

    api = tmp_path / "api"
    (api / "tests").mkdir(parents=True)
    (api / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    monkeypatch.setattr(
        discovery, "_can_import_pytest", lambda interpreter: (False, f"{interpreter}: boom")
    )

    suite = select_suite(detect_suites(str(tmp_path)), "pytest")
    assert suite.available is False
    assert "pytest is not available" in suite.reason
    assert "boom" in suite.reason


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


def test_guard_change_never_allows_a_filter_that_matches_nothing(git_project):
    """A ``paths`` filter matching no changed file is a false green, not a clean change."""
    settings = _settings(str(git_project))
    (git_project / "wallet.py").write_text("def pay():\n    return 4\n", encoding="utf-8")

    verdict = guard_change(settings, paths=["api/ledger.py"], run_tests=False)

    assert "paths.matched-nothing" in verdict.rule_ids, verdict.summary
    assert verdict.action.value in ("require_approval", "block"), verdict.summary
    assert verdict.passed is False
    assert "nothing was reviewed" in verdict.summary
    assert verdict.metrics["changed_files"] == 0
    assert verdict.metrics["total_changed_files"] == 1
    assert verdict.metrics["unmatched_paths"] == ["api/ledger.py"]
    assert verdict.metrics["resolved_paths"] == []


def test_guard_change_resolves_a_filter_rooted_above_the_project(git_project):
    """``api/wallet.py`` and ``wallet.py`` review the same diff instead of one a false green."""
    settings = _settings(str(git_project))
    (git_project / "wallet.py").write_text("def pay():\n    return 6\n", encoding="utf-8")

    above = guard_change(settings, paths=["api/wallet.py"], run_tests=False)
    rooted = guard_change(settings, paths=["wallet.py"], run_tests=False)

    assert "paths.matched-nothing" not in above.rule_ids, above.summary
    assert above.metrics["changed_files"] == rooted.metrics["changed_files"] == 1
    assert above.metrics["resolved_paths"] == ["wallet.py"]
    assert above.metrics["unmatched_paths"] == []


def test_guard_change_reviews_a_filter_that_matches(git_project):
    """The same file is reviewed when the filter is rooted like the diff."""
    settings = _settings(str(git_project))
    (git_project / "wallet.py").write_text("def pay():\n    return 5\n", encoding="utf-8")

    verdict = guard_change(settings, paths=["wallet.py"], run_tests=False)

    assert "paths.matched-nothing" not in verdict.rule_ids, verdict.summary
    assert verdict.metrics["changed_files"] == 1
    assert verdict.metrics["unmatched_paths"] == []


def test_guard_change_reports_clean_when_no_filter_matches_an_empty_tree(git_project):
    """Without a filter an untouched tree is still a clean, allowed verdict."""
    settings = _settings(str(git_project))

    verdict = guard_change(settings, run_tests=False)

    assert verdict.action.value == "allow", verdict.summary
    assert verdict.summary == "clean: no pending change to review"
    assert "paths" not in verdict.metrics


def test_qa_policy_printed_keys_cover_the_documented_changes_schema(tmp_path):
    """Every field the ``changes`` schema documents must be printed by ``qa_policy``.

    A field that the tool schema advertises but the report never prints is a silent
    no-op: the caller sets it, the tool accepts it and the verdict says nothing
    about it.
    """
    from pathlib import Path

    from codx.junior.quality.qa.policy import POLICY_FIELDS, policy_markdown

    labels = {
        "enabled": "Enabled",
        "min_coverage": "Min coverage",
        "block_above": "Block above",
        "approval_above": "Approval above",
        "warn_above": "Warn above",
        "require_tests_for_changes": "Require tests for changes",
        "block_on_failures": "Block on test failures",
        "block_missing_tests": "Block missing tests",
        "fail_closed": "Fail closed",
        "max_test_seconds": "Max test seconds",
        "flaky_repeats": "Flaky repeats",
        "ignore_paths": "Ignored paths",
        "suites": "Custom suites",
    }

    unlabelled = sorted(set(POLICY_FIELDS) - set(labels))
    assert not unlabelled, f"add a printed label for {unlabelled}"

    printed = policy_markdown(resolve_policy(_settings(str(tmp_path))), {})
    unprinted = [field for field in POLICY_FIELDS if labels[field] not in printed]
    assert not unprinted, f"qa_policy does not print {unprinted}:\n{printed}"

    tools_source = (
        Path(__file__).resolve().parents[2] / "codx" / "junior" / "tools" / "qa_tools.py"
    ).read_text(encoding="utf-8")
    schema = tools_source.split("QA_POLICY_TOOL_JSON", 1)[-1]
    undocumented = [field for field in POLICY_FIELDS if field not in schema]
    assert not undocumented, f"the `changes` schema does not document {undocumented}"
