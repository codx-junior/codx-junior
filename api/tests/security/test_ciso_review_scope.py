"""Regression tests for the scope of the CISO change-review gate.

The gate must review the **commit**, not the sub directory the project happens
to live in: ``git diff`` run from ``<repo>/api`` ignores files staged elsewhere
in the repository, and the gate used to answer "No pending changes to review"
(a false ``allow``) over a dirty tree.

Covered behaviour:

* files staged outside the project sub directory are reviewed,
* an empty diff over a dirty tree is reported as *unverifiable*, never clean,
* a genuinely clean tree still reports nothing to review.
"""

import os
import subprocess

import pytest

from codx.junior.tools import ciso_tools

GITHUB_TOKEN = "ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"


class _Settings:
    """Minimal project settings: only the project path is used by the gate."""

    def __init__(self, path):
        self.abs_project_path = str(path)


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _init_repo(repo):
    (repo / "api").mkdir(parents=True)
    (repo / "api" / "README.md").write_text("api\n")
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


@pytest.fixture()
def mono_repo(tmp_path):
    """A monorepo whose project root is ``<repo>/api`` and a staged change outside it."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "client").mkdir()
    (repo / "client" / "credentials.py").write_text(f'TOKEN = "{GITHUB_TOKEN}"\n')
    _git(repo, "add", "client/credentials.py")
    return repo


def test_staged_review_sees_changes_outside_the_project_subdir(mono_repo):
    response = ciso_tools.security_review_changes(
        scope="staged", settings=_Settings(mono_repo / "api")
    )
    text = f"{response.user_response}\n{response.llm_response}"

    assert "No pending changes" not in text
    assert "secret.github-token" in text
    assert "decision: allow" not in response.llm_response.lower()


def test_verdict_states_how_much_it_read(mono_repo):
    """The verdict must prove the diff was read, not merely that it had no hits."""
    response = ciso_tools.security_review_changes(
        scope="staged", settings=_Settings(mono_repo / "api")
    )

    assert "files_inspected: 1" in response.llm_response
    assert "added_lines_inspected: 1" in response.llm_response
    assert "repo_root: " in response.llm_response
    assert "Inspected 1 file(s)" in response.user_response


def test_patch_stats_counts_files_and_added_lines():
    patch = """diff --git a/x.py b/x.py
index 1..2 100644
--- a/x.py
+++ b/x.py
@@ -1,0 +1,2 @@
+one
+two
"""

    assert ciso_tools._patch_stats(patch) == (1, 2)
    assert ciso_tools._patch_stats("") == (0, 0)


def test_empty_diff_over_a_dirty_tree_is_not_a_green(mono_repo, monkeypatch):
    monkeypatch.setattr(ciso_tools, "_git_diff", lambda *a, **k: "")

    response = ciso_tools.security_review_changes(
        scope="staged", settings=_Settings(mono_repo / "api")
    )

    assert "No pending changes" not in response.user_response
    assert "Unverifiable" in response.user_response
    assert "client/credentials.py" in response.user_response
    assert "UNVERIFIABLE" in response.llm_response
    assert any("refusing to report clean" in line for line in response.logs)


def test_clean_tree_still_reports_nothing_to_review(tmp_path):
    repo = tmp_path / "clean"
    _init_repo(repo)

    response = ciso_tools.security_review_changes(
        scope="staged", settings=_Settings(repo / "api")
    )

    assert "No pending changes" in response.user_response
    assert "pending_files=0" in response.llm_response
