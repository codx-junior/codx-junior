"""
GitHub integration tests.

The foundation is only useful if the promises around the token hold, so these
tests cover exactly that (no network is ever hit):

* the repository listing maps the GitHub payload and follows the pages,
* a disabled/misconfigured integration answers a clear 400,
* the API never returns the token (only a masked hint),
* connecting a repository writes a **credential-free** remote URL, so
  ``.git/config`` never holds a token.
"""

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from fastapi import HTTPException

from codx.junior.api import github as github_api
from codx.junior.engine.git_engine import has_embedded_credentials
from codx.junior.engine.git_graph import GitGraphEngine
from codx.junior.misc.github_client import (
    GitHubClient,
    GitHubError,
    GitHubRepository,
    normalize_repo_name,
)

TOKEN = "ghp_1234567890abcdefghijklmnopqrstu"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeGitHubSettings:
    """The settings subset used by the GitHub endpoints and the git engine."""

    def __init__(self, path: str, enabled: bool = True, token: str = TOKEN):
        self.abs_project_path = path
        self.codx_path = os.path.join(path, ".codx")
        self.project_name = "github-integration-test"
        self.project_id = "project-1"
        self.is_git_root = True
        self.git_user_name = "GitHub Integration Test"
        self.git_user_email = "github@example.com"
        self.git_access_token = ""
        self.git_worktrees_path = ""
        self.github_enabled = enabled
        self.github_api_base = "https://api.github.com"
        self._github_token = token

    def is_github_enabled(self) -> bool:
        return bool(self.github_enabled)

    def get_github_api_base(self) -> str:
        return self.github_api_base.rstrip("/")

    def get_github_token(self) -> str:
        return self._github_token

    def has_github_token(self) -> bool:
        return bool(self._github_token)

    def masked_github_token(self) -> str:
        if not self._github_token:
            return ""
        return f"••••••••{self._github_token[-4:]}"


class FakeSession:
    """Session facade: the engines only need `settings`."""

    def __init__(self, settings):
        self.settings = settings


def make_request(settings):
    """Build the request object the endpoints read the session from."""
    return SimpleNamespace(
        state=SimpleNamespace(codx_junior_session=FakeSession(settings))
    )


def run_git(repo, *args):
    """Run git in *repo* with a deterministic identity."""
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "GitHub Integration Test",
        "GIT_AUTHOR_EMAIL": "github@example.com",
        "GIT_COMMITTER_NAME": "GitHub Integration Test",
        "GIT_COMMITTER_EMAIL": "github@example.com",
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
    """An empty repository with no remote configured."""
    path = str(tmp_path / "repo")
    os.makedirs(path)
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.name", "GitHub Integration Test")
    run_git(path, "config", "user.email", "github@example.com")
    with open(os.path.join(path, "app.py"), "w", encoding="utf-8") as handle:
        handle.write("print('one')\n")
    run_git(path, "add", "app.py")
    run_git(path, "commit", "-m", "first commit")
    return path


def repo_payload(name: str, owner: str = "acme", private: bool = True) -> dict:
    """Build a GitHub API repository payload."""
    return {
        "id": abs(hash(f"{owner}/{name}")) % 100000,
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": private,
        "default_branch": "main",
        "clone_url": f"https://github.com/{owner}/{name}.git",
        "ssh_url": f"git@github.com:{owner}/{name}.git",
        "html_url": f"https://github.com/{owner}/{name}",
        "description": f"{name} repository",
        "owner": {"login": owner},
        # Never mapped: the model must not carry a token field
        "token": TOKEN,
    }


def make_client(handler) -> GitHubClient:
    """Build a client whose HTTP layer is a mocked transport."""
    return GitHubClient(
        token=TOKEN, transport=httpx.MockTransport(handler)
    )


# ---------------------------------------------------------------------------
# Repository listing
# ---------------------------------------------------------------------------


def test_list_repositories_maps_the_payload_and_follows_pages():
    """One page per 100 entries until a short page comes back."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(
                200, json=[repo_payload("app"), repo_payload("api", private=False)]
            )
        return httpx.Response(200, json=[repo_payload("worker")])

    async def scenario():
        client = make_client(handler)
        try:
            return await client.list_repositories(per_page=2, max_pages=3)
        finally:
            await client.aclose()

    repos = asyncio.run(scenario())

    assert [repo.full_name for repo in repos] == ["acme/app", "acme/api", "acme/worker"]
    assert [repo.private for repo in repos] == [True, False, True]
    assert repos[0].default_branch == "main"
    assert repos[0].clone_url == "https://github.com/acme/app.git"
    assert repos[0].owner == "acme"
    assert repos[0].description == "app repository"
    # The listing stops after the first short page
    assert len(calls) == 2
    assert calls[0].url.params["per_page"] == "2"
    assert calls[0].url.params["page"] == "1"
    # The token travels in the header only, never in the URL
    assert calls[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(calls[0].url)
    # And it is never part of the mapped model
    assert "token" not in repos[0].model_dump()


def test_list_repositories_raises_a_typed_error_without_the_token():
    """A rejected token surfaces as a GitHubError that never echoes it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async def scenario():
        client = make_client(handler)
        try:
            await client.list_repositories()
        finally:
            await client.aclose()

    with pytest.raises(GitHubError) as error:
        asyncio.run(scenario())

    assert error.value.status_code == 401
    assert "Bad credentials" in str(error.value)
    assert TOKEN not in str(error.value)


def test_normalize_repo_name_accepts_what_users_paste():
    """owner/name, .git suffix, clone and browser URLs share one form."""
    assert normalize_repo_name("acme/app") == "acme/app"
    assert normalize_repo_name("acme/app.git") == "acme/app"
    assert normalize_repo_name("https://github.com/acme/app.git") == "acme/app"
    assert normalize_repo_name("https://github.com/acme/app/") == "acme/app"
    assert normalize_repo_name("git@github.com:acme/app.git") == "acme/app"
    # Credentials never survive the parsing
    assert (
        normalize_repo_name("https://x-access-token:ghp_1@github.com/acme/app.git")
        == "acme/app"
    )
    with pytest.raises(GitHubError):
        normalize_repo_name("app")


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_disabled_integration_answers_a_clear_400(repo):
    """Nothing is sent to GitHub while the feature flag is off."""
    settings = FakeGitHubSettings(repo, enabled=False)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            github_api.api_github_repos(
                request=make_request(settings), per_page=100, max_pages=1,
                visibility=None, user=None,
            )
        )

    assert error.value.status_code == 400
    assert "disabled" in error.value.detail


def test_missing_token_answers_a_clear_400(repo):
    """An enabled integration without a token is a configuration error."""
    settings = FakeGitHubSettings(repo, enabled=True, token="")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            github_api.api_github_repos(
                request=make_request(settings), per_page=100, max_pages=1,
                visibility=None, user=None,
            )
        )

    assert error.value.status_code == 400
    assert "token" in error.value.detail.lower()


def test_status_endpoint_only_exposes_a_masked_token(repo):
    """The status answer never carries the token itself."""
    settings = FakeGitHubSettings(repo)

    status = github_api.api_github_status(request=make_request(settings), user=None)
    payload = json.dumps(status.model_dump())

    assert status.enabled is True
    assert status.has_github_token is True
    assert status.masked_github_token.endswith(TOKEN[-4:])
    assert TOKEN not in payload


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def test_connect_writes_a_credential_free_remote(repo, monkeypatch):
    """The remote URL is stored without credentials and .git/config has no token."""
    settings = FakeGitHubSettings(repo)

    class StubClient:
        """Client stub: GitHub is never called by the test."""

        async def get_repository(self, name):
            assert name == "acme/app"
            return GitHubRepository.from_api(repo_payload("app"))

        async def aclose(self):
            return None

    monkeypatch.setattr(github_api, "_build_client", lambda _settings: StubClient())

    response = asyncio.run(
        github_api.api_github_connect(
            request=make_request(settings),
            payload=github_api.GitHubConnectRequest(repo="acme/app"),
            user=None,
        )
    )

    assert response.success is True
    assert response.remote_name == "origin"
    assert response.url == "https://github.com/acme/app.git"
    assert response.action == "added"
    assert response.branch == "main"
    assert response.repo.full_name == "acme/app"

    url = run_git(repo, "remote", "get-url", "origin").strip()
    assert url == "https://github.com/acme/app.git"
    assert "@" not in url

    config = open(os.path.join(repo, ".git", "config"), encoding="utf-8").read()
    assert TOKEN not in config
    assert "extraheader" not in config
    assert "credential" not in config.lower()


def test_connect_strips_credentials_from_the_clone_url(repo, monkeypatch):
    """A pasted clone URL carrying a token is stripped before being stored."""
    settings = FakeGitHubSettings(repo)

    class StubClient:
        async def get_repository(self, name):
            return GitHubRepository.from_api(
                {
                    **repo_payload("app"),
                    "clone_url": f"https://x-access-token:{TOKEN}@github.com/acme/app.git",
                }
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(github_api, "_build_client", lambda _settings: StubClient())

    response = asyncio.run(
        github_api.api_github_connect(
            request=make_request(settings),
            payload=github_api.GitHubConnectRequest(repo="https://github.com/acme/app"),
            user=None,
        )
    )

    assert response.url == "https://github.com/acme/app.git"
    config = open(os.path.join(repo, ".git", "config"), encoding="utf-8").read()
    assert TOKEN not in config


def test_engine_refuses_to_store_a_remote_url_with_credentials(repo):
    """The last line of defence: the engine never persists a credential."""
    settings = FakeGitHubSettings(repo)
    engine = GitGraphEngine(session=FakeSession(settings))

    result = engine.set_remote_url(
        remote="origin",
        url=f"https://x-access-token:{TOKEN}@github.com/acme/app.git",
    )

    assert result["success"] is False
    assert "credentials" in result["errors"][0]
    config_path = os.path.join(repo, ".git", "config")
    config = open(config_path, encoding="utf-8").read()
    assert TOKEN not in config
    assert "acme/app.git" not in config


def test_set_remote_url_updates_an_existing_remote(repo):
    """A second connect updates the URL instead of adding a duplicate remote."""
    settings = FakeGitHubSettings(repo)
    engine = GitGraphEngine(session=FakeSession(settings))

    first = engine.set_remote_url("origin", "https://github.com/acme/app.git")
    second = engine.set_remote_url("origin", "https://github.com/acme/other.git")

    assert first["action"] == "added"
    assert second["action"] == "updated"
    assert second["previous_url"] == "https://github.com/acme/app.git"
    assert run_git(repo, "remote", "get-url", "origin").strip() == (
        "https://github.com/acme/other.git"
    )


def test_has_embedded_credentials_classifies_remote_urls():
    """Only URLs with user info are considered credentialed."""
    assert has_embedded_credentials("https://user:pass@github.com/a/b.git")
    assert has_embedded_credentials(f"https://{TOKEN}@github.com/a/b.git")
    assert not has_embedded_credentials("https://github.com/a/b.git")
    assert not has_embedded_credentials("git@github.com:a/b.git")
    assert not has_embedded_credentials("")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_resolve_the_token_from_the_environment(monkeypatch):
    """`github_token` may be a ${ENV_VAR} reference (nothing secret on disk)."""
    from codx.junior.settings import CODXJuniorSettings

    monkeypatch.setenv("CODX_JUNIOR_TEST_GITHUB_TOKEN", TOKEN)
    settings = CODXJuniorSettings(github_token="${CODX_JUNIOR_TEST_GITHUB_TOKEN}")

    assert settings.get_github_token() == TOKEN
    assert settings.has_github_token() is True
    assert settings.masked_github_token().endswith(TOKEN[-4:])
    assert TOKEN not in settings.masked_github_token()
    assert settings.get_github_api_base() == "https://api.github.com"

    # An unresolved reference is ignored instead of being sent to GitHub
    for name in (
        "CODX_JUNIOR_TEST_GITHUB_TOKEN",
        "CODX_JUNIOR_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    unresolved = CODXJuniorSettings(github_token="${CODX_JUNIOR_TEST_GITHUB_TOKEN}")
    assert unresolved.get_github_token() == ""
    assert unresolved.has_github_token() is False
    assert unresolved.masked_github_token() == ""
