"""
Transient (one-shot) git credentials tests.

A remote operation can be given a username/password (or token) by the user: it
authenticates that single git command and is never stored. These tests cover
the promises that make it safe:

* the credential reaches git as a per-command ``http.extraheader`` Basic header,
* it never comes back in the returned dicts, the command echo or the logs,
* ``.git/config`` (and any credential store) is untouched after a call,
* an auth failure without a credential is flagged with ``requires_credentials``
  so the client can ask the user and retry once.
"""

import base64
import json
import logging
import os
import subprocess

import pytest

from codx.junior.engine import git_engine as git_engine_module
from codx.junior.engine.git_engine import GitEngine

USERNAME = "jane-doe"
PASSWORD = "s3cr3t-token-value"
AUTH_FAILURE = (
    "fatal: could not read Username for 'https://github.com': "
    "No such device or address"
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeSettings:
    """The subset of CODXJuniorSettings used by the git engine."""

    def __init__(self, path: str):
        self.abs_project_path = path
        self.codx_path = os.path.join(path, ".codx")
        self.project_name = "git-credentials-test"
        self.project_id = "project-1"
        self.is_git_root = True
        self.git_user_name = "Git Credentials Test"
        self.git_user_email = "git-credentials@example.com"
        # No stored token: the credential must come from the request
        self.git_access_token = ""
        self.git_worktrees_path = ""


class FakeSession:
    """Session facade used by the engine constructor."""

    def __init__(self, settings):
        self.settings = settings


def run_git(repo, *args):
    """Run git in *repo* with a deterministic identity."""
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Git Credentials Test",
        "GIT_AUTHOR_EMAIL": "git-credentials@example.com",
        "GIT_COMMITTER_NAME": "Git Credentials Test",
        "GIT_COMMITTER_EMAIL": "git-credentials@example.com",
    })
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    ).stdout


@pytest.fixture()
def repo(tmp_path):
    """A repository with one commit and an HTTPS remote (never contacted)."""
    path = str(tmp_path / "repo")
    os.makedirs(path)
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.name", "Git Credentials Test")
    run_git(path, "config", "user.email", "git-credentials@example.com")
    with open(os.path.join(path, "app.py"), "w", encoding="utf-8") as handle:
        handle.write("print('one')\n")
    run_git(path, "add", "app.py")
    run_git(path, "commit", "-m", "first commit")
    run_git(path, "remote", "add", "origin", "https://github.com/acme/private.git")
    return path


def make_engine(repo):
    settings = FakeSettings(repo)
    return GitEngine(session=FakeSession(settings))


def spy_git(monkeypatch, returncode, stdout="", stderr=""):
    """
    Record every git command and simulate the remote operation outcome.

    Only push/pull/fetch are simulated: real git keeps running for the other
    commands, so the fixture repository behaves normally.
    """
    recorded = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        recorded.append({"command": list(command), "kwargs": dict(kwargs)})
        if any(part in ("push", "pull", "fetch") for part in command[1:]):
            return subprocess.CompletedProcess(
                command, returncode, stdout=stdout, stderr=stderr
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(git_engine_module.subprocess, "run", fake_run)
    return recorded


def remote_command(recorded, name):
    """Return the recorded command that ran the given remote operation."""
    for entry in recorded:
        if name in entry["command"]:
            return entry
    raise AssertionError(f"'{name}' was never run: {recorded}")


def auth_header(command):
    """Return the http.extraheader arg of a recorded command."""
    for arg in command:
        if arg.startswith("http.extraheader=Authorization: Basic "):
            return arg
    return ""


def encoded_credential(username=USERNAME, password=PASSWORD):
    """Return the base64 payload git receives for a credential."""
    return base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# The credential reaches git for that single command
# ---------------------------------------------------------------------------


def test_credential_is_sent_as_a_per_command_basic_header(repo, monkeypatch):
    """The credential is a `-c http.extraheader` override, not a stored one."""
    engine = make_engine(repo)
    recorded = spy_git(monkeypatch, returncode=0)

    result = engine.push_remote(
        credential={"username": USERNAME, "password": PASSWORD}
    )

    assert result["success"] is True
    entry = remote_command(recorded, "push")
    header = auth_header(entry["command"])
    assert header, "the credential must reach git as an http.extraheader arg"
    payload = header.partition("Basic ")[2]
    assert base64.b64decode(payload).decode("utf-8") == f"{USERNAME}:{PASSWORD}"
    # `-c http.extraheader=...` keeps it on the command line only
    position = entry["command"].index(header)
    assert entry["command"][position - 1] == "-c"
    # git can never prompt for (nor cache) a credential
    env = entry["kwargs"]["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"]
    assert env["GCM_INTERACTIVE"] == "never"


def test_token_only_credential_uses_the_placeholder_user(repo, monkeypatch):
    """A bare token authenticates as `x-access-token`, like the project PAT."""
    engine = make_engine(repo)
    recorded = spy_git(monkeypatch, returncode=0)

    result = engine.push_remote(credential={"token": PASSWORD})

    assert result["success"] is True
    payload = auth_header(remote_command(recorded, "push")["command"]).partition(
        "Basic "
    )[2]
    assert base64.b64decode(payload).decode("utf-8") == f"x-access-token:{PASSWORD}"


def test_the_configured_token_path_still_works_without_a_credential(repo, monkeypatch):
    """Omitting the credential keeps the stored-token behaviour unchanged."""
    engine = make_engine(repo)
    engine.settings.git_access_token = "ghp_stored_token"
    recorded = spy_git(monkeypatch, returncode=0)

    result = engine.fetch_remote()

    assert result["success"] is True
    payload = auth_header(remote_command(recorded, "fetch")["command"]).partition(
        "Basic "
    )[2]
    assert base64.b64decode(payload).decode("utf-8") == "x-access-token:ghp_stored_token"


# ---------------------------------------------------------------------------
# The credential never comes back
# ---------------------------------------------------------------------------


def test_credential_never_leaks_in_results_or_logs(repo, monkeypatch, caplog):
    """Nothing the client (or the log file) sees carries the credential."""
    engine = make_engine(repo)
    spy_git(monkeypatch, returncode=128, stderr=AUTH_FAILURE)

    with caplog.at_level(logging.DEBUG, logger="codx.junior.engine.git_engine"):
        result = engine.push_remote(
            credential={"username": USERNAME, "password": PASSWORD}
        )

    encoded = encoded_credential()
    payload = json.dumps(result)
    assert PASSWORD not in payload
    assert encoded not in payload
    assert PASSWORD not in result["command"]
    assert encoded not in result["command"]
    assert "http.extraheader=********" in result["command"]

    assert caplog.text, "the command is logged, and it must be masked"
    assert PASSWORD not in caplog.text
    assert encoded not in caplog.text
    assert "http.extraheader=********" in caplog.text

    assert result["success"] is False
    # A credential was supplied: asking for it again would be pointless
    assert not result.get("requires_credentials")


def test_an_incomplete_credential_is_ignored(repo, monkeypatch):
    """A username without password never reaches git as an empty header."""
    engine = make_engine(repo)
    recorded = spy_git(monkeypatch, returncode=0)

    engine.push_remote(credential={"username": USERNAME})

    assert auth_header(remote_command(recorded, "push")["command"]) == ""


# ---------------------------------------------------------------------------
# Nothing is persisted
# ---------------------------------------------------------------------------


def test_git_config_is_unchanged_after_authenticated_calls(repo, monkeypatch):
    """No config write, no credential helper and no credential store file."""
    config_path = os.path.join(repo, ".git", "config")
    before = read_file(config_path)
    engine = make_engine(repo)
    spy_git(monkeypatch, returncode=0)

    engine.push_remote(credential={"username": USERNAME, "password": PASSWORD})
    engine.fetch_remote(credential={"username": USERNAME, "password": PASSWORD})
    engine.pull_remote(credential={"token": PASSWORD})

    after = read_file(config_path)
    assert after == before
    assert "extraheader" not in after
    assert "credential" not in after.lower()
    assert PASSWORD not in after
    assert USERNAME not in after
    assert not os.path.exists(os.path.join(repo, ".git", "credentials"))
    # The one-shot credential never reaches the project settings either
    assert engine.settings.git_access_token == ""


# ---------------------------------------------------------------------------
# Auth failure -> the client can ask the user
# ---------------------------------------------------------------------------


def test_auth_failure_without_credential_requires_credentials(repo, monkeypatch):
    """The result tells the client a credential is needed (nothing stored)."""
    engine = make_engine(repo)
    spy_git(monkeypatch, returncode=128, stderr=AUTH_FAILURE)

    result = engine.push_remote()

    assert result["success"] is False
    assert result["auth_error"] is True
    assert result["requires_credentials"] is True
    assert result["remote"] == "origin"
    assert result["operation"] == "push"
    # The human readable hint is still part of the errors
    assert len(result["errors"]) >= 2


def test_api_turns_the_auth_failure_into_a_machine_readable_400():
    """`/git/repo/push` answers a dict detail the client can act on."""
    from fastapi import HTTPException

    from codx.junior.api.git import _ensure_remote_success

    result = {
        "success": False,
        "requires_credentials": True,
        "remote": "origin",
        "operation": "push",
        "errors": [AUTH_FAILURE, "The remote rejected the git credentials."],
    }
    with pytest.raises(HTTPException) as error:
        _ensure_remote_success(result, operation="push")

    assert error.value.status_code == 400
    assert error.value.detail["requires_credentials"] is True
    assert error.value.detail["remote"] == "origin"
    assert error.value.detail["operation"] == "push"
    assert "could not read Username" in error.value.detail["message"]


def test_api_keeps_the_plain_error_when_credentials_are_not_the_issue():
    """A non-auth failure still answers the plain error string."""
    from fastapi import HTTPException

    from codx.junior.api.git import _ensure_remote_success

    with pytest.raises(HTTPException) as error:
        _ensure_remote_success(
            {"success": False, "errors": ["No remote 'origin' configured"]},
            operation="fetch",
        )

    assert error.value.status_code == 400
    assert error.value.detail == "No remote 'origin' configured"


def test_the_request_model_keeps_credentials_out_of_its_repr():
    """The body model is the only credential entry and hides them in the repr."""
    from codx.junior.api.git import RemoteCredentialsRequest

    payload = RemoteCredentialsRequest(username=USERNAME, password=PASSWORD)
    assert PASSWORD not in repr(payload)
    assert payload.to_credential()["password"] == PASSWORD
    assert RemoteCredentialsRequest().to_credential() is None
