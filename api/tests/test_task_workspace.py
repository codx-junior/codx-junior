"""Tests for per-task git workspaces.

An autonomous turn must not write into the main checkout: it runs in a
worktree, is committed on its own branch, and leaves the main tree untouched.
These tests build a real (minimal) git repository and exercise the manager
end to end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_ROOT not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, API_ROOT)

from codx.junior.task_workspace import TaskWorkspaceManager  # noqa: E402


class FakeSettings:
    """Stand-in for CODXJuniorSettings (no pydantic import)."""

    def __init__(self, project_path: str, worktrees_path: str, enabled: bool = True):
        self.abs_project_path = project_path
        self.project_path = project_path
        self.codx_path = os.path.join(project_path, ".codx")
        self.project_name = "task-workspace-test"
        self.project_id = "project-1"
        self.git_worktrees_path = worktrees_path
        self.task_workspace_isolation = enabled
        self.watching = True
        self.is_git_root = True
        self.settings: dict = {}

    def get_worktrees_path(self) -> str:
        return self.git_worktrees_path


class FakeChat:
    """Stand-in for the Chat/task card."""

    def __init__(self, chat_id: str, root_id: str, name: str):
        self.id = chat_id
        self.root_id = root_id
        self.name = name


def write(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def create_repository(project_path: str) -> None:
    """Create a minimal git repository with one commit."""
    git_dir = os.path.join(project_path, ".git")
    os.makedirs(os.path.join(git_dir, "objects"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
    write(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")
    write(
        os.path.join(git_dir, "config"),
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n\tlogallrefupdates = true\n",
    )
    write(os.path.join(project_path, "app.py"), "print('hello')\n")
    write(os.path.join(project_path, ".gitignore"), ".codx/\n.env\nnode_modules/\n")
    git(project_path, "add", "app.py", ".gitignore")
    git(
        project_path,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-qm",
        "initial",
    )


@pytest.fixture()
def project(tmp_path):
    path = str(tmp_path / "project")
    os.makedirs(path, exist_ok=True)
    create_repository(path)
    return path


def test_autonomous_turn_is_isolated_and_committed(project, tmp_path):
    """The worktree gets the edit, the main checkout stays clean."""
    settings = FakeSettings(project, str(tmp_path / "worktrees"))
    manager = TaskWorkspaceManager(settings)
    chat = FakeChat("chat-1", "root-1", "Fix login timeout")

    workspace = manager.ensure(chat)
    assert workspace is not None
    assert workspace.branch.startswith("codx/task/")
    assert os.path.isdir(workspace.path)
    assert workspace.path != project

    # A file created by the agent lands in the worktree, never in main.
    write(os.path.join(workspace.path, "login.py"), "TIMEOUT = 10\n")
    assert not os.path.exists(os.path.join(project, "login.py"))
    assert git(project, "status", "--porcelain").stdout.strip() == ""

    # The engine receives settings whose project root is the worktree but whose
    # control plane (codx_path, project id) stays in the project.
    run_settings = manager.settings_for(workspace)
    assert run_settings.abs_project_path == workspace.path
    assert run_settings.codx_path == settings.codx_path
    assert run_settings.project_id == settings.project_id
    assert run_settings.task_workspace_isolation is False

    result = manager.commit(chat)
    assert result["committed"] is True, result
    assert result["commit"]
    # The agent change is now a commit on the task branch, not in main.
    log = git(workspace.path, "log", "-1", "--pretty=%s%n%b").stdout
    assert "agent: Fix login timeout" in log
    assert "root-1" not in log  # the workspace key is internal, not the message
    assert git(project, "status", "--porcelain").stdout.strip() == ""
    assert git(project, "log", "-1", "--pretty=%s").stdout.strip() == "initial"

    # A second turn reuses the same checkout and branch.
    again = manager.ensure(chat)
    assert again.path == workspace.path
    assert again.branch == workspace.branch


def test_workspace_links_dependencies_and_copies_ignored_env(project, tmp_path):
    """Heavy deps are linked; gitignored env files are copied, tracked ones not."""
    os.makedirs(os.path.join(project, "node_modules", "lib"), exist_ok=True)
    write(os.path.join(project, "node_modules", "lib", "index.js"), "module.exports=1\n")
    write(os.path.join(project, ".env"), "TOKEN=local\n")

    settings = FakeSettings(project, str(tmp_path / "worktrees"))
    workspace = TaskWorkspaceManager(settings).ensure(
        FakeChat("chat-2", "root-2", "Run tests")
    )

    linked = os.path.join(workspace.path, "node_modules")
    assert os.path.islink(linked)
    assert os.path.realpath(linked) == os.path.realpath(
        os.path.join(project, "node_modules")
    )
    with open(os.path.join(workspace.path, ".env"), "r", encoding="utf-8") as handle:
        assert handle.read() == "TOKEN=local\n"


def test_non_git_project_and_disabled_setting_fall_back(project, tmp_path):
    """No repository or the opt-out must never break a turn."""
    non_git = str(tmp_path / "plain")
    os.makedirs(non_git, exist_ok=True)
    write(os.path.join(non_git, "file.txt"), "x")
    manager = TaskWorkspaceManager(FakeSettings(non_git, str(tmp_path / "wt1")))
    assert manager.ensure(FakeChat("c", "r", "Task")) is None

    disabled = TaskWorkspaceManager(
        FakeSettings(project, str(tmp_path / "wt2"), enabled=False)
    )
    assert disabled.ensure(FakeChat("c", "r", "Task")) is None


def test_remove_requires_a_clean_workspace(project, tmp_path):
    """A dirty workspace is kept for review; a committed one is removable."""
    settings = FakeSettings(project, str(tmp_path / "worktrees"))
    manager = TaskWorkspaceManager(settings)
    chat = FakeChat("chat-3", "root-3", "Cleanup")
    workspace = manager.ensure(chat)
    write(os.path.join(workspace.path, "pending.py"), "x = 1\n")

    blocked = manager.remove(chat)
    assert blocked["removed"] is False
    assert "uncommitted" in blocked["error"]

    manager.commit(chat)
    removed = manager.remove(chat)
    assert removed["removed"] is True
    assert not os.path.isdir(workspace.path)
    assert manager.list() == []


def test_registry_is_valid_json(project, tmp_path):
    """The registry lives in the data plane and is valid, versioned JSON."""
    settings = FakeSettings(project, str(tmp_path / "worktrees"))
    TaskWorkspaceManager(settings).ensure(FakeChat("chat-4", "root-4", "Report"))
    path = os.path.join(settings.codx_path, "task_workspaces", "index.json")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["version"] == 1
    assert len(payload["workspaces"]) == 1
