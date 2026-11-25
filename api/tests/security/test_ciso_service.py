"""Tests for the CISO scan service and policy persistence.

The CISO package is deliberately dependency-free: the same code runs in a git
hook and in a slim container. These tests therefore use a plain settings stub,
not the pydantic settings object, and build a repository by hand so they also
run in filesystems where ``git init`` cannot chmod its own config (the API
service tests run in that kind of overlay in some environments).

Covered:
    * a project scan finds a committed credential and a `.env` file;
    * a working-tree scan analyses untracked files (the diff alone would miss
      them, which is exactly how an agent-introduced file escapes review);
    * the overview derives the posture and repository hygiene;
    * rules can be silenced/enabled and the change is persisted to
      ``project.json`` and effective immediately;
    * a path outside the project is rejected;
    * scan retention keeps the newest records.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

API_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if API_ROOT not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, API_ROOT)

from codx.junior.security.ciso import service  # noqa: E402


class FakeSettings:
    """Minimal stand-in for ``CODXJuniorSettings`` (no pydantic import)."""

    def __init__(self, project_path: str) -> None:
        self.abs_project_path = project_path
        self.codx_path = os.path.join(project_path, ".codx")
        self.project_name = "ciso-test-project"
        self.project_id = "project-1"
        self.ciso_policy = None
        self.settings: dict = {}

    def get_project_settings_file(self) -> str:
        return os.path.join(self.codx_path, "project.json")


def write(path: str, content: str) -> None:
    """Write a text file, creating its parent folders."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def init_repository(project_path: str) -> None:
    """Create a minimal git repository without ``git init``.

    ``git init`` writes then chmods ``.git/config``; some container overlays
    return ENOSYS for chmod, which would make these tests fail for an
    environmental reason. The metadata below is all git needs for add/commit.
    """
    git_dir = os.path.join(project_path, ".git")
    os.makedirs(os.path.join(git_dir, "objects"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
    write(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")
    write(
        os.path.join(git_dir, "config"),
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = false\n"
        "\tbare = false\n"
        "\tlogallrefupdates = true\n",
    )


def git(project_path: str, *args: str) -> subprocess.CompletedProcess:
    """Run git in the test project."""
    return subprocess.run(
        ["git", *args],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def project(tmp_path):
    """A project with one clean committed file and one committed `.env`."""
    project_path = str(tmp_path / "project")
    write(os.path.join(project_path, "src", "app.py"), "print('hello')\n")
    # A provider token, assembled at runtime: the detector must find it, while
    # the test source itself never contains a realistic live credential that a
    # repository scan would have to flag.
    fake_stripe_key = "sk_" + "live_" + "51H8xQ2K9mN4pR7sT0vW3yZ6aB1cD5eF8gH2jK4lM"
    write(os.path.join(project_path, ".env"), f"API_KEY={fake_stripe_key}\n")
    write(os.path.join(project_path, ".gitignore"), ".codx/\n")
    write(
        os.path.join(project_path, ".codx", "project.json"),
        json.dumps({"project_name": "ciso-test-project"}, indent=2),
    )
    init_repository(project_path)
    git(project_path, "add", "src/app.py", ".env", ".gitignore")
    git(
        project_path,
        "-c",
        "user.email=ciso@example.com",
        "-c",
        "user.name=CISO",
        "commit",
        "-qm",
        "initial",
    )
    return project_path


def wait_for(settings: FakeSettings, scan_id: str, timeout: float = 20.0) -> dict:
    """Poll a scan until it reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = service.get_scan(settings, scan_id)
        if record["status"] not in (service.SCAN_STATUS_QUEUED, service.SCAN_STATUS_RUNNING):
            return record
        time.sleep(0.02)
    raise AssertionError(f"scan {scan_id} did not finish in {timeout}s")


def test_project_scan_finds_credential(project):
    """A project scan reports the committed token and the `.env` file."""
    settings = FakeSettings(project)
    started = service.start_scan(settings, scope="project", max_files=50)
    record = wait_for(settings, started["scan_id"])

    assert record["status"] == service.SCAN_STATUS_COMPLETED, record["error"]
    rules = {finding["rule_id"] for finding in record["findings"]}
    assert "secret.stripe-key" in rules or "secret.hardcoded-credential" in rules
    assert "configuration.secret-file" in rules
    assert record["summary"]["highest_severity"] in ("critical", "high")
    assert record["summary"]["total_findings"] >= 2
    assert record["duration_seconds"] is not None

    # The persisted file is inside the data plane and the list omits findings.
    stored = json.loads(
        open(
            os.path.join(service.scans_dir(settings), f"{record['scan_id']}.json"),
            "r",
            encoding="utf-8",
        ).read()
    )
    assert stored["findings"]
    listing = service.list_scans(settings)
    assert listing["scans"][0]["scan_id"] == record["scan_id"]
    assert "findings" not in listing["scans"][0]


def test_changes_scan_includes_untracked_files(project):
    """The working-tree scan catches a new untracked file (agent loot)."""
    settings = FakeSettings(project)
    write(
        os.path.join(project, "src", "introduced.py"),
        "import os\nos.system('rm -rf /')\n",
    )
    started = service.start_scan(settings, scope="changes", max_files=50)
    record = wait_for(settings, started["scan_id"])

    assert record["status"] == service.SCAN_STATUS_COMPLETED, record["error"]
    paths = {finding["path"] for finding in record["findings"]}
    assert "src/introduced.py" in paths
    assert any(
        finding["rule_id"] == "dangerous.os-system" for finding in record["findings"]
    )


def test_overview_posture_and_repo_hygiene(project):
    """The overview aggregates posture, rules, secrets and repository hygiene."""
    settings = FakeSettings(project)
    wait_for(settings, service.start_scan(settings, scope="project")["scan_id"])
    overview = service.overview(settings)

    assert overview["project"]["name"] == "ciso-test-project"
    assert overview["posture"]["available"] is True
    assert 0 <= overview["posture"]["score"] <= 100
    assert overview["posture"]["grade"] in ("A", "B", "C", "D", "F")
    assert overview["policy"]["rules"]["total"] >= 90
    assert overview["scans"]["total"] == 1
    assert overview["repo"]["checked"] is True
    # The fixture ignores the whole .codx folder, so the data plane is covered.
    assert overview["repo"]["ignore_rules_ok"] is True
    assert overview["repo"]["tracked_data_plane_count"] == 0
    assert overview["secrets"]["available"] is True


def test_rule_toggle_is_validated_and_persisted(project):
    """Silencing a rule updates the policy and the effective rule state."""
    settings = FakeSettings(project)
    before = service.rule_catalog(settings)
    assert next(
        rule for rule in before["rules"] if rule["rule_id"] == "secret.stripe-key"
    )["enabled"]

    result = service.set_rule_enabled(settings, "secret.stripe-key", False)
    assert result["rule"]["enabled"] is False
    assert "secret.stripe-key" in result["policy"]["ignored_rules"]
    stored = json.loads(
        open(os.path.join(settings.codx_path, "project.json"), "r", encoding="utf-8").read()
    )
    assert "secret.stripe-key" in stored["ciso_policy"]["ignore_rules"]

    # A silenced rule no longer produces findings: the policy is the one the
    # scanner resolves at scan time.
    record = wait_for(settings, service.start_scan(settings, scope="project")["scan_id"])
    assert all(finding["rule_id"] != "secret.stripe-key" for finding in record["findings"])

    enabled = service.set_rule_enabled(settings, "secret.stripe-key", True)
    assert enabled["rule"]["enabled"] is True
    assert "secret.stripe-key" not in enabled["policy"]["ignored_rules"]

    with pytest.raises(service.UnknownRuleError):
        service.set_rule_enabled(settings, "does-not-exist", False)


def test_scan_rejects_paths_outside_the_project(project):
    """A scan cannot be pointed at the host through `..` or an absolute path."""
    settings = FakeSettings(project)
    with pytest.raises(service.CisoServiceError):
        service.start_scan(settings, scope="paths", paths=["../etc/passwd"])
    with pytest.raises(service.CisoServiceError):
        service.start_scan(settings, scope="paths", paths=["/etc/passwd"])


def test_policy_update_validates_and_resets(project):
    """Policy changes are validated, persisted and reset to the defaults."""
    settings = FakeSettings(project)
    updated = service.update_policy(settings, {"block_above": "medium", "fail_closed": True})
    assert updated["policy"]["block_above"] == "medium"
    # The canonical field is populated in memory: the next gate sees it without
    # re-reading project.json.
    assert settings.ciso_policy["block_above"] == "medium"
    from codx.junior.security.ciso.policy import resolve_policy

    assert resolve_policy(settings).block_above.value == "medium"
    assert updated["policy"]["fail_closed"] is True

    with pytest.raises(service.CisoServiceError):
        service.update_policy(settings, {"not_a_policy_field": True})
    with pytest.raises(service.CisoServiceError):
        service.update_policy(settings, {"block_above": "impossible"})
    with pytest.raises(service.CisoServiceError):
        service.update_policy(settings, {"block_categories": ["not-a-category"]})

    reset = service.reset_policy(settings)
    assert reset["ignored_rules"] == []
    assert reset["policy"]["block_above"] == "high"
    assert reset["stored"] == {}

def test_paths_scan_expands_directories(project):
    """A selected folder is expanded to its files, not ignored as a folder."""
    settings = FakeSettings(project)
    write(
        os.path.join(project, "src", "nested", "danger.py"),
        "import subprocess\nsubprocess.run('ls', shell=True)\n",
    )
    started = service.start_scan(settings, scope="paths", paths=["src"])
    record = wait_for(settings, started["scan_id"])
    assert record["status"] == service.SCAN_STATUS_COMPLETED, record["error"]
    assert "src/nested/danger.py" in {finding["path"] for finding in record["findings"]}


def test_project_scan_skips_git_ignored_files(project):
    """A file git discards is generated, not repository content: never scanned."""
    settings = FakeSettings(project)
    write(os.path.join(project, ".gitignore"), ".codx/\ntmp/\n")
    write(
        os.path.join(project, "tmp", "scratch.py"),
        "import os\nos.system('rm -rf /')\n",
    )
    started = service.start_scan(settings, scope="project", max_files=50)
    record = wait_for(settings, started["scan_id"])

    assert record["status"] == service.SCAN_STATUS_COMPLETED, record["error"]
    paths = {finding["path"] for finding in record["findings"]}
    assert not any(path.startswith("tmp/") for path in paths)
    # Skipping ignored files must not turn the scan into a no-op: the tracked
    # files are still analysed (the fixture commits a .env with a token).
    rules = {finding["rule_id"] for finding in record["findings"]}
    assert "configuration.secret-file" in rules


def test_iter_project_files_lists_only_what_git_ships(project):
    """The lister returns tracked files plus new ones git does not ignore."""
    from codx.junior.security.ciso import scanner

    write(os.path.join(project, ".gitignore"), ".codx/\ntmp/\n")
    write(os.path.join(project, "tmp", "scratch.py"), "print('generated')\n")
    write(os.path.join(project, "src", "new_module.py"), "print('new')\n")

    files = {
        os.path.relpath(path, project)
        for path in scanner.iter_project_files(project, max_files=50)
    }
    assert "src/app.py" in files  # tracked
    assert "src/new_module.py" in files  # new, not ignored
    assert "tmp/scratch.py" not in files  # ignored by .gitignore
    assert ".gitignore" in files


def test_activity_reads_the_bounded_gate_tail(project):
    """The activity panel aggregates ciso_verdict entries from the audit journal."""
    settings = FakeSettings(project)
    now = datetime.now(timezone.utc)
    stamps = [
        (now - timedelta(minutes=3)).isoformat(),
        (now - timedelta(minutes=2)).isoformat(),
        (now - timedelta(minutes=1)).isoformat(),
    ]
    write(
        os.path.join(settings.codx_path, "harness", "audit.jsonl"),
        "\n".join(
            [
                json.dumps({"ts": stamps[0], "event": "run_created"}),
                json.dumps(
                    {
                        "ts": stamps[1],
                        "event": "ciso_verdict",
                        "domain": "file_write",
                        "action": "block",
                        "blocked": True,
                        "highest_severity": "critical",
                        "subject": "src/app.py",
                        "findings": [{"rule": "secret.stripe-key", "severity": "critical"}],
                    }
                ),
                json.dumps(
                    {
                        "ts": stamps[2],
                        "event": "ciso_verdict",
                        "domain": "command",
                        "action": "warn",
                        "blocked": False,
                        "highest_severity": "medium",
                        "subject": "pytest",
                        "findings": [],
                    }
                ),
            ]
        )
        + "\n",
    )
    payload = service.activity(settings)
    assert payload["stats"]["total"] == 2
    assert payload["stats"]["blocked"] == 1
    assert payload["stats"]["actions"]["block"] == 1
    assert payload["stats"]["top_rules"][0]["rule_id"] == "secret.stripe-key"
    assert len(payload["entries"]) == 2

def test_rule_catalog_has_unique_ids(project):
    """The catalogue is the scanner's rule list: no gaps, no duplicated ids."""
    from codx.junior.security.ciso import rules as ciso_rules

    entries = ciso_rules.catalog()
    ids = [entry["rule_id"] for entry in entries]
    assert len(ids) == len(set(ids))
    assert "secret.stripe-key" in ids
    assert "dangerous.os-system" in ids
    assert "injection.instruction-override" in ids
    assert all(entry["category"] for entry in entries)
    assert all(entry["severity"] for entry in entries)
