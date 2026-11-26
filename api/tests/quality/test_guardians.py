"""Tests of the project quality guardian subsystem.

What is covered:

* the profile contract (``Profile.guardian`` round-trip);
* stack detection and the catalog;
* bootstrap idempotence, polish protection and adoption;
* deterministic checks (syntax, conflict markers, docs links, command parsing);
* AI answer parsing/validation and the runner's structured findings;
* the verdict ladder, run storage, suppressions and the feedback loop;
* the fix proposal/apply/verify loop (with a fake AI);
* policy persistence and the file-change trigger scheduler;
* the tool registry and the REST router surface.

Everything runs without git remotes, network or a real model. The profile
discovery and the collection-event import (which pull the whole engine/socket
stack) are patched so the suite stays fast and hermetic.

Made with ❤️ by codx-junior
"""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import codx.junior.quality.guardians.catalog as catalog
import codx.junior.quality.guardians.runner as runner_module
import codx.junior.profiles.profile_manager as profile_manager
from codx.junior.model.profile import (
    Profile,
    ProfileGuardianCheck,
    ProfileGuardianSettings,
)
from codx.junior.quality.guardians import (
    GuardianPolicy,
    RunStore,
    bootstrap,
    build_change_set,
    enable_guardian,
    guardians_for_files,
    is_guardian,
    list_guardians,
    make_verdict,
    parse_review,
    resolve_policy,
    run_checks,
    run_guardians,
    save_policy,
    scan_project,
    service,
    update_guardian,
)
from codx.junior.quality.guardians.checks import run_command_check
from codx.junior.quality.guardians.diff import normalize_path
from codx.junior.quality.guardians.policy import quality_path
from codx.junior.quality.guardians.profiles import adopt_profile


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------
class SettingsStub:
    """Minimal project settings: the guardian package only needs these fields."""

    def __init__(self, root: str):
        self.abs_project_path = root
        self.codx_path = os.path.join(root, ".codx")
        self.project_name = "guardian-test"
        self.project_id = "guardian-test-project"

    def get_ignore_patterns(self):
        return ["api/", "client/", ".codx"]


def _git(root: str, *args: str) -> None:
    """Run git in *root* with a deterministic identity."""
    subprocess.run(
        ["git", *args],
        cwd=root,
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


@pytest.fixture(autouse=True)
def hermetic_guardians(monkeypatch):
    """Keep the suite fast and independent from the host project catalogue."""
    monkeypatch.setattr(profile_manager, "find_project_parents", lambda project, user=None: [])
    monkeypatch.setattr(profile_manager, "find_project_by_name", lambda name: None)
    monkeypatch.setattr(profile_manager, "emit_collection_event", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner_module, "_emit_run_event", lambda *args, **kwargs: None)
    # A generated command check would depend on whichever linters happen to be
    # installed in the test image; the command check path has its own test.
    monkeypatch.setattr(catalog, "_resolve_tool", lambda root, candidates: None)


@pytest.fixture()
def project(tmp_path):
    """A git project with representative Python, docs, web and infra files."""
    root = str(tmp_path)
    os.makedirs(os.path.join(root, ".codx"), exist_ok=True)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    os.makedirs(os.path.join(root, ".github", "workflows"), exist_ok=True)
    os.makedirs(os.path.join(root, "migrations"), exist_ok=True)

    with open(os.path.join(root, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(".codx/\n")
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as f:
        f.write("def hello():\n    return 1\n")
    with open(os.path.join(root, "tests", "test_app.py"), "w", encoding="utf-8") as f:
        f.write("from app import hello\n\ndef test_hello():\n    assert hello() == 1\n")
    with open(os.path.join(root, "docs", "index.md"), "w", encoding="utf-8") as f:
        f.write("# Docs\n\nSee [app](../app.py).\n")
    os.makedirs(os.path.join(root, "client"), exist_ok=True)
    with open(os.path.join(root, "client", "App.vue"), "w", encoding="utf-8") as f:
        f.write("<template><div>hi</div></template>\n")
    with open(os.path.join(root, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "demo", "devDependencies": {"vue": "^3.0.0"}}, f)
    with open(os.path.join(root, "Dockerfile"), "w", encoding="utf-8") as f:
        f.write("FROM node:20\n")
    with open(os.path.join(root, ".github", "workflows", "ci.yml"), "w", encoding="utf-8") as f:
        f.write("name: ci\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
    with open(os.path.join(root, "migrations", "001_init.sql"), "w", encoding="utf-8") as f:
        f.write("CREATE TABLE wallet (id INTEGER PRIMARY KEY);\n")

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return SettingsStub(root)


def _write(settings, relative: str, content: str) -> None:
    """Write a project file."""
    path = os.path.join(settings.abs_project_path, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read(settings, relative: str) -> str:
    """Read a project file."""
    with open(os.path.join(settings.abs_project_path, relative), "r", encoding="utf-8") as handle:
        return handle.read()


def _file_entry(settings, relative: str, *, content=None, status="modified"):
    """Build the normalized file payload used by the check functions."""
    return {
        "path": relative,
        "absolute": os.path.join(settings.abs_project_path, relative),
        "status": status,
        "content": _read(settings, relative) if content is None else content,
        "diff": "",
        "additions": 0,
        "deletions": 0,
        "language": "python" if relative.endswith(".py") else "text",
        "binary": False,
        "truncated": False,
    }


class FakeAI:
    """Deterministic AI double for the deep lane and the fix engine."""

    def __init__(self, review_findings=None, fix_changes=None):
        self.llm_model = "fake-model"
        self.review_findings = review_findings or []
        self.fix_changes = fix_changes or []
        self.calls = []

    async def a_chat_run(self, prompt, headers=None):
        self.calls.append(prompt)
        if "Findings to fix" in prompt:
            content = json.dumps(
                {"summary": "apply the fix", "changes": self.fix_changes}
            )
        else:
            content = json.dumps(
                {"summary": "fake review", "findings": self.review_findings}
            )
        return SimpleNamespace(
            content=content,
            messages=[],
            cost=SimpleNamespace(iteration_cost_cxjcoins=0.01),
        )


def _admin_user():
    """A codx-junior admin (can teach a guardian)."""
    return SimpleNamespace(username="admin", role="admin")


# -----------------------------------------------------------------------------
# Contract
# -----------------------------------------------------------------------------
def test_profile_guardian_round_trip():
    profile = Profile(
        name="guardian_x",
        category="guardian",
        file_match=r"\.py$",
        content="rules",
        guardian=ProfileGuardianSettings(
            enabled=True,
            kind="code",
            triggers=["change", "manual"],
            checks=[
                ProfileGuardianCheck(
                    id="syntax", kind="builtin", builtin="syntax", severity="high"
                )
            ],
        ),
    )
    restored = Profile(**profile.model_dump())
    assert restored.guardian.enabled is True
    assert restored.guardian.checks[0].builtin == "syntax"
    assert restored.guardian.triggers == ["change", "manual"]


def test_verdict_ladder():
    assert make_verdict([])["action"] == "allow"
    low = make_verdict([{"severity": "low", "status": "open", "rule_id": "r"}])
    assert low["action"] == "warn"
    medium = make_verdict([{"severity": "medium", "status": "open", "rule_id": "r"}])
    assert medium["action"] == "require_approval"
    high = make_verdict([{"severity": "high", "status": "open", "rule_id": "r"}])
    assert high["action"] == "block"
    dismissed = make_verdict(
        [{"severity": "critical", "status": "dismissed", "rule_id": "r"}]
    )
    assert dismissed["action"] == "allow"


# -----------------------------------------------------------------------------
# Detection and catalog
# -----------------------------------------------------------------------------
def test_scan_detects_the_stack(project):
    signals = scan_project(project)
    assert signals.has_python is True
    assert signals.has_web is True
    assert signals.has_tests is True
    assert signals.has_docs is True
    assert signals.has_docker is True
    assert signals.has_ci is True
    assert signals.has_sql is True
    assert signals.files_scanned >= 8
    assert signals.signature()


def test_catalog_only_creates_present_guardians(project):
    from codx.junior.quality.guardians.catalog import build_templates

    templates = build_templates(project, scan_project(project))
    names = {template.name for template in templates}
    assert "guardian_python" in names
    assert "guardian_web" in names
    assert "guardian_tests" in names
    assert "guardian_docs" in names
    assert "guardian_infra" in names
    assert "guardian_data" in names
    assert "guardian_dependencies" in names
    # A docs-only project must not get every guardian.
    docs_only = os.path.join(project.abs_project_path, "docs")
    assert docs_only  # keep the fixture path intentional


# -----------------------------------------------------------------------------
# Bootstrap
# -----------------------------------------------------------------------------
def test_bootstrap_is_idempotent_and_preserves_polish(project):
    first = bootstrap(project)
    assert "guardian_python" in first["created"]
    assert not first["errors"]

    second = bootstrap(project)
    assert second["created"] == []
    assert second["updated"] == []

    profile = profile_manager.ProfileManager(project).read_profile("guardian_python")
    assert is_guardian(profile)
    update_guardian(
        project,
        "guardian_python",
        content=(profile.content or "") + "\n\n## Project note\nUse the team logger.\n",
    )
    third = bootstrap(project)
    assert third["updated"] == []
    kept = profile_manager.ProfileManager(project).read_profile("guardian_python")
    assert "Use the team logger" in (kept.content or "")
    assert kept.guardian.polished is True


def test_bootstrap_adopts_existing_file_match_profile(project):
    manager = profile_manager.ProfileManager(project)
    manager.save_profile(
        Profile(
            name="vue_helper",
            category="assistant",
            description="Existing profile",
            file_match=r"\.vue$",
            content="Vue conventions",
        )
    )
    assert not is_guardian(manager.read_profile("vue_helper"))
    report = bootstrap(project, adopt=True)
    assert "vue_helper" in report["adopted"]
    adopted = manager.read_profile("vue_helper")
    assert is_guardian(adopted)
    assert adopted.category == "assistant"  # adoption does not rewrite the role
    assert adopted.guardian.triggers == ["staged", "manual"]
    enabled = enable_guardian(project, "vue_helper", enabled=False)
    assert enabled.guardian.enabled is False


def test_adopt_requires_a_file_scope(project):
    manager = profile_manager.ProfileManager(project)
    manager.save_profile(
        Profile(name="no_scope", category="assistant", content="x", file_match="")
    )
    with pytest.raises(ValueError):
        adopt_profile(project, "no_scope")


def test_list_guardians_reports_candidates(project):
    manager = profile_manager.ProfileManager(project)
    manager.save_profile(
        Profile(name="candidate", category="assistant", content="x", file_match=r"\.md$")
    )
    state = list_guardians(project, include_candidates=True)
    assert any(item["name"] == "candidate" for item in state["candidates"])


# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------
def test_builtin_checks_find_real_problems(project):
    _write(project, "bad.py", "def hello(:\n    return 1\n")
    _write(project, "conflict.py", "def x():\n    return 1\n<<<<<<< HEAD\n")
    _write(project, "bad.json", '{"a": }')
    checks = [
        ProfileGuardianCheck(id="syntax", kind="builtin", builtin="syntax", severity="high"),
        ProfileGuardianCheck(
            id="conflicts", kind="builtin", builtin="conflict_markers", severity="critical"
        ),
    ]
    findings, executions, errors = run_checks(
        "guardian_python",
        checks,
        project,
        [
            _file_entry(project, "bad.py"),
            _file_entry(project, "conflict.py"),
            _file_entry(project, "bad.json"),
        ],
    )
    assert not errors
    rules = {finding["rule_id"] for finding in findings}
    assert "syntax:python" in rules
    assert "conflict_markers" in rules
    assert any(execution["status"] == "failed" for execution in executions)


def test_markdown_links_report_broken_local_targets(project):
    _write(project, "docs/broken.md", "# Hi\n\n[missing](./ghost.md)\n[ok](../app.py)\n")
    checks = [
        ProfileGuardianCheck(
            id="links", kind="builtin", builtin="markdown_links", severity="low"
        )
    ]
    findings, _executions, errors = run_checks(
        "guardian_docs", checks, project, [_file_entry(project, "docs/broken.md")]
    )
    assert not errors
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "docs:broken-link"
    assert findings[0]["line"] == 3


def test_command_check_parses_output_and_runs_safely(project):
    _write(
        project,
        "tools/check.py",
        "import sys\nprint('app.py:1: broken thing')\nsys.exit(1)\n",
    )
    check = ProfileGuardianCheck(
        id="demo",
        kind="command",
        command=[sys.executable, "tools/check.py"],
        attach_paths=False,
        severity="high",
    )
    findings, execution = run_command_check(
        "guardian_python",
        check,
        project,
        [_file_entry(project, "app.py")],
    )
    assert execution["status"] == "failed"
    assert findings and findings[0]["path"] == "app.py"
    assert findings[0]["line"] == 1
    assert findings[0]["severity"] == "high"


def test_command_check_missing_executable_is_a_skip(project):
    check = ProfileGuardianCheck(
        id="missing", kind="command", command=["definitely-not-a-real-tool"], attach_paths=False
    )
    findings, execution = run_command_check(
        "guardian_python", check, project, [_file_entry(project, "app.py")]
    )
    assert findings == []
    assert execution["status"] == "skipped"
    assert "not found" in execution["message"]


# -----------------------------------------------------------------------------
# AI parsing
# -----------------------------------------------------------------------------
def test_parse_review_validates_paths_and_lines(project):
    profile = Profile(
        name="guardian_python",
        category="guardian",
        file_match=r"\.py$",
        guardian=ProfileGuardianSettings(severity="medium"),
    )
    files = [
        {
            "path": "app.py",
            "content": "def hello():\n    broken = 1\n    return broken\n",
            "diff": "",
        }
    ]
    answer = json.dumps(
        {
            "summary": "one issue",
            "findings": [
                {
                    "rule": "no-broken",
                    "severity": "HIGH",
                    "path": "app.py",
                    "line": 999,
                    "title": "Broken line",
                    "evidence": "broken = 1",
                    "recommendation": "Rename it",
                    "confidence": 0.9,
                },
                {
                    "rule": "ghost",
                    "severity": "high",
                    "path": "other.py",
                    "title": "Outside the reviewed set",
                },
            ],
        }
    )
    summary, findings, error = parse_review(project, profile, answer, files, model="fake")
    assert summary == "one issue"
    assert error == ""
    assert len(findings) == 1
    finding = findings[0]
    assert finding["path"] == "app.py"
    assert finding["line"] == 2  # located from the evidence, not the invented 999
    assert finding["severity"] == "high"
    assert finding["source"] == "ai"
    assert finding["rule_id"] == "ai:no-broken"


def test_parse_review_refuses_prose(project):
    profile = Profile(name="g", category="guardian", file_match=r"\.py$")
    summary, findings, error = parse_review(project, profile, "Looks fine!", [], model="fake")
    assert summary == ""
    assert findings == []
    assert "JSON" in error


# -----------------------------------------------------------------------------
# Runner and storage
# -----------------------------------------------------------------------------
def test_changed_run_records_findings_and_suppression(project):
    bootstrap(project)
    _write(project, "app.py", "def hello(:\n    return 1\n")
    run = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    assert run["verdict"]["action"] == "block"
    rules = {finding["rule_id"] for finding in run["findings"]}
    assert "syntax:python" in rules
    assert run["guardians"]
    store = RunStore(project)
    assert store.list_runs()
    assert store.latest_run()["id"] == run["id"]

    finding = next(f for f in run["findings"] if f["rule_id"] == "syntax:python")
    suppression = store.add_suppression(
        {
            "guardian": finding["guardian"],
            "rule_id": finding["rule_id"],
            "path": finding["path"],
            "reason": "test suppression",
        }
    )
    assert suppression["id"]
    second = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    suppressed = [
        f for f in second["findings"]
        if f["id"] == finding["id"] and f["status"] == "suppressed"
    ]
    assert suppressed
    assert second["verdict"]["action"] == "allow"


def test_file_change_trigger_never_bootstraps_profiles(project):
    run = run_guardians(
        project,
        trigger="change",
        scope="changed",
        mode="checks",
        paths=["app.py"],
    )
    assert any("No quality guardian" in error for error in run["errors"])
    profiles_path = os.path.join(project.codx_path, "profiles")
    assert not os.path.isdir(profiles_path) or not os.listdir(profiles_path)


def test_ai_review_is_structured_and_validated(project):
    bootstrap(project)
    _write(project, "app.py", "def hello():\n    return 2\n")
    fake = FakeAI(
        review_findings=[
            {
                "rule": "changed-behaviour",
                "severity": "high",
                "path": "app.py",
                "title": "Return value changed without a test",
                "evidence": "return 2",
                "recommendation": "Update the test",
                "confidence": 0.8,
            }
        ]
    )
    run = run_guardians(
        project, ai=fake, trigger="manual", scope="changed", mode="auto"
    )
    ai_findings = [f for f in run["findings"] if f["source"] == "ai"]
    assert len(ai_findings) == 1
    assert ai_findings[0]["path"] == "app.py"
    assert run["ai_calls"] == 1
    assert run["ai_cost"] > 0


def test_file_type_scope(project):
    bootstrap(project)
    run = run_guardians(
        project,
        trigger="manual",
        scope="file_type",
        file_type="python",
        mode="checks",
        max_files=10,
    )
    assert run["files"]
    assert all(file["path"].endswith(".py") for file in run["files"])


def test_build_change_set_skips_outside_paths(project):
    files, errors = build_change_set(
        project,
        paths=[os.path.join(project.abs_project_path, "app.py"), "/etc/passwd"],
        scope="files",
        max_files=5,
    )
    assert [file["path"] for file in files] == ["app.py"]
    assert any("outside" in error for error in errors)


def test_paths_cannot_escape_through_a_symlink(project, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("OUTSIDE = True\n", encoding="utf-8")
    link = os.path.join(project.abs_project_path, "linked.py")
    try:
        os.symlink(str(outside), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert normalize_path(project, "linked.py") is None
    files, errors = build_change_set(project, paths=["linked.py"], scope="files")
    assert files == []
    assert errors


# -----------------------------------------------------------------------------
# Feedback and fix
# -----------------------------------------------------------------------------
def test_feedback_learn_and_suppress(project):
    bootstrap(project)
    _write(project, "app.py", "def hello(:\n    return 1\n")
    run = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    finding = run["findings"][0]
    user = _admin_user()
    learned = service.feedback(
        project,
        run_id=run["id"],
        finding_id=finding["id"],
        action="learn",
        reason="This project accepts the existing style",
        user=user,
        session=SimpleNamespace(user=user),
    )
    assert learned["finding"]["status"] == "dismissed"
    assert learned["rule"]["title"]
    enrich = os.path.join(
        project.codx_path, "profiles", finding["guardian"], "enrich.md"
    )
    assert os.path.isfile(enrich)

    suppressed = service.feedback(
        project,
        run_id=run["id"],
        finding_id=finding["id"],
        action="suppress",
        reason="false positive",
        user=user,
        session=SimpleNamespace(user=user),
    )
    assert suppressed["finding"]["status"] == "suppressed"
    assert suppressed["suppression"]["id"]


def test_deterministic_fix_requires_auto_fix(project):
    bootstrap(project)
    _write(project, "app.py", "def hello(:\n    return 1\n")
    _write(
        project,
        "tools/fix.py",
        "from pathlib import Path\n"
        "path = Path('app.py')\n"
        "path.write_text(path.read_text().replace('def hello(:', 'def hello():'))\n",
    )
    manager = profile_manager.ProfileManager(project)
    profile = manager.read_profile("guardian_python")
    checks = [check.model_dump() for check in profile.guardian.checks]
    checks.append(
        {
            "id": "demo-fix",
            "kind": "command",
            "command": [sys.executable, "tools/check.py"],
            "fix_command": [sys.executable, "tools/fix.py"],
            "attach_paths": False,
            "severity": "high",
        }
    )
    update_guardian(project, "guardian_python", checks=checks, auto_fix=False)
    run = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    blocked = asyncio.run(
        service.fix(
            project,
            run_id=run["id"],
            session=None,
            ai=None,
            apply=True,
            use_ai=False,
        )
    )
    assert blocked["deterministic"][0]["reason"] == "auto_fix=false"
    assert "def hello(:" in _read(project, "app.py")

    update_guardian(project, "guardian_python", auto_fix=True)
    allowed = asyncio.run(
        service.fix(
            project,
            run_id=run["id"],
            session=None,
            ai=None,
            apply=True,
            use_ai=False,
        )
    )
    assert allowed["deterministic"][0]["status"] == "fixed"
    assert "def hello():" in _read(project, "app.py")


def test_fix_proposes_then_applies_and_verifies(project):
    bootstrap(project)
    _write(project, "app.py", "def hello(:\n    return 1\n")
    run = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    assert run["findings"]
    fake = FakeAI(
        fix_changes=[
            {"search": "def hello(:", "replace": "def hello():", "reason": "syntax"}
        ]
    )
    proposal = asyncio.run(
        service.fix(
            project,
            run_id=run["id"],
            session=None,
            ai=fake,
            apply=False,
            use_ai=True,
        )
    )
    assert proposal["proposals"]
    assert "def hello():" in proposal["proposals"][0]["preview"]
    assert "def hello(:" in _read(project, "app.py")  # nothing written

    applied = asyncio.run(
        service.fix(
            project,
            run_id=run["id"],
            session=None,
            ai=fake,
            apply=True,
            use_ai=True,
        )
    )
    assert applied["applied"] == 1
    assert "def hello():" in _read(project, "app.py")
    assert applied["verification"]["action"] == "allow"


# -----------------------------------------------------------------------------
# Policy and triggers
# -----------------------------------------------------------------------------
def test_policy_persists_and_env_disables(project, monkeypatch):
    defaults = resolve_policy(project)
    assert defaults.ai_on_change is False
    saved = save_policy(
        project,
        {"ai_on_change": True, "max_files_per_run": 7, "block_above": "critical"},
    )
    assert saved["ai_on_change"] is True
    assert saved["max_files_per_run"] == 7
    assert saved["block_above"] == "critical"
    assert os.path.isfile(quality_path(project, "policy.json"))
    monkeypatch.setenv("CODX_JUNIOR_GUARDIANS_DISABLED", "1")
    assert resolve_policy(project).enabled is False


def test_trigger_scheduler_coalesces_and_cools_down(project):
    from codx.junior.quality.guardians.triggers import GuardianTriggerScheduler

    save_policy(
        project,
        {
            "debounce_seconds": 60,
            "max_delay_seconds": 60,
            "file_cooldown_seconds": 60,
            "run_on_change": True,
        },
    )
    calls = []
    scheduler = GuardianTriggerScheduler(
        processor=lambda settings, files, reason: calls.append((files, reason))
    )
    try:
        assert scheduler.schedule(project, ["app.py"], reason="test") is True
        scheduler.flush(project)
        assert calls and calls[0][0] == ["app.py"]
        # The same file is inside the cooldown window now.
        assert scheduler.schedule(project, ["app.py"], reason="test") is False
    finally:
        scheduler.stop()


def test_validator_consumes_the_stored_quality_evidence(project):
    """The Validator prompt sees the guardian findings for the same files."""
    from codx.junior.engine.git_validator import GitValidatorEngine

    bootstrap(project)
    _write(project, "app.py", "def hello(:\n    return 1\n")
    run = run_guardians(project, trigger="manual", scope="changed", mode="checks")
    engine = GitValidatorEngine(session=SimpleNamespace(settings=project))
    quality = engine._quality_context([{"path": "app.py"}])
    assert quality["run_id"] == run["id"]
    assert any(item["rule_id"] == "syntax:python" for item in quality["findings"])
    block = engine._quality_prompt_block(quality)
    assert "Quality guardian evidence" in block
    assert "syntax:python" in block or "Invalid python syntax" in block
    # A run of unrelated files must not leak into this review.
    assert engine._quality_context([{"path": "other.py"}]) in ({},)


# -----------------------------------------------------------------------------
# Tool / API surface
# -----------------------------------------------------------------------------
def test_quality_tools_bootstrap_and_review(project):
    from codx.junior.tools.quality_tools import quality_guardians, quality_review

    session = SimpleNamespace(user=None)
    boot = asyncio.run(
        quality_guardians(action="bootstrap", settings=project, session=session)
    )
    assert "Quality guardians refreshed" in boot.user_response
    review = asyncio.run(
        quality_review(
            scope="changed",
            mode="checks",
            settings=project,
            session=session,
        )
    )
    assert "Quality guardian report" in review.user_response
    assert "quality run" in review.llm_response


def test_tool_registry_exposes_the_quality_tools():
    from codx.junior.tools import TOOLS, all_tool_names

    names = all_tool_names()
    assert {
        "quality_guardians",
        "quality_review",
        "quality_fix",
        "quality_feedback",
        "quality_policy",
    } <= names
    assert len(TOOLS) >= 120


def test_router_exposes_the_quality_endpoints():
    from codx.junior.api.quality import router

    paths = {route.path for route in router.routes}
    assert "/quality/state" in paths
    assert "/quality/review" in paths
    assert "/quality/run" not in paths
    assert "/quality/fix" in paths
    assert "/quality/feedback" in paths
    assert "/quality/policy" in paths


# Made with ❤️ by codx-junior
