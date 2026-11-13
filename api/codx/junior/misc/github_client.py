"""
Thin, async client for the GitHub REST API.

Scope: the repository listing and repository metadata the GitHub integration
needs to connect a codx-junior project to a GitHub repository. Nothing else
(no issues, no webhooks, no pull requests) lives here yet.

The token is a secret: it is only sent in the ``Authorization`` header of the
request, it is never logged nor included in an error message (see
:func:`mask_token`), and every request is HTTPS-only unless the caller points
``api_base`` to another host explicitly.

Example:
    >>> client = GitHubClient(token="ghp_1234567890abcdef")
    >>> repos = await client.list_repositories()
    >>> [(r.full_name, r.private) for r in repos][:1]
    [('acme/app', True)]
    >>> await client.aclose()

Made with ❤️ by codx-junior
"""

import logging

from typing import List, Optional

import httpx

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Public GitHub REST API. A project on GitHub Enterprise overrides it with the
# `github_api_base` setting.
GITHUB_API_BASE = "https://api.github.com"

# GitHub caps the page size at 100 entries
PER_PAGE = 100

# Safety net: a listing never walks an unbounded number of pages
MAX_PAGES = 20

# Timeout (seconds) applied to every request
DEFAULT_TIMEOUT = 15.0

# What a token is replaced with when it has to be shown (never the value)
TOKEN_MASK = "***"

# Response body characters kept in an error message
MAX_ERROR_CHARS = 300


def mask_token(token: Optional[str]) -> str:
    """
    Return a redacted placeholder for a token.

    Args:
        token: Token value (or None).

    Returns:
        ``'***'`` when a token is set, an empty string otherwise, so a log line
        can say whether credentials were sent without ever showing them.

    Example:
        >>> mask_token("ghp_1234567890abcdef")
        '***'
        >>> mask_token("")
        ''
    """
    return TOKEN_MASK if token else ""


class GitHubError(Exception):
    """
    Error raised by every GitHub client call.

    Carries the HTTP status (0 when the request never reached GitHub) so the
    API can answer a meaningful 4xx/5xx instead of a generic failure. The
    message never contains the token.
    """

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code:
            return f"GitHub API error {self.status_code}: {self.message}"
        return self.message


class GitHubRepository(BaseModel):
    """
    The repository metadata the integration exposes.

    Only repository identity and URLs are kept: there is deliberately no field
    carrying a credential, and ``clone_url``/``ssh_url`` are the public URLs
    GitHub returns (they never embed a token).
    """

    id: Optional[int] = Field(default=None, description="GitHub repository id")
    owner: str = Field(default="", description="Repository owner (user or org)")
    name: str = Field(default="", description="Repository name")
    full_name: str = Field(default="", description="owner/name")
    private: bool = Field(default=False, description="Private repository")
    default_branch: str = Field(default="", description="Default branch name")
    clone_url: str = Field(default="", description="HTTPS clone URL")
    ssh_url: str = Field(default="", description="SSH clone URL")
    html_url: str = Field(default="", description="Browser URL")
    description: Optional[str] = Field(default=None, description="Description")

    @classmethod
    def from_api(cls, data: dict) -> "GitHubRepository":
        """
        Map a GitHub API repository payload to the model.

        Args:
            data: One entry of ``GET /user/repos`` or ``GET /repos/{owner}/{name}``.

        Returns:
            The mapped repository.

        Example:
            >>> GitHubRepository.from_api({"name": "app", "private": True}).full_name
            ''
        """
        owner = data.get("owner") or {}
        return cls(
            id=data.get("id"),
            owner=str(owner.get("login") or ""),
            name=str(data.get("name") or ""),
            full_name=str(data.get("full_name") or ""),
            private=bool(data.get("private")),
            default_branch=str(data.get("default_branch") or ""),
            clone_url=str(data.get("clone_url") or ""),
            ssh_url=str(data.get("ssh_url") or ""),
            html_url=str(data.get("html_url") or ""),
            description=data.get("description"),
        )


class GitHubClient:
    """
    Async GitHub REST client (repository listing + repository metadata).

    The instance owns one ``httpx.AsyncClient``; call :meth:`aclose` (or use it
    as an async context manager) when done.

    Args:
        token: GitHub token sent as a Bearer credential. Never logged.
        api_base: REST API base, ``https://api.github.com`` by default.
        timeout: Request timeout in seconds.
        transport: Optional ``httpx`` transport (tests inject a MockTransport).

    Example:
        >>> async with GitHubClient(token="ghp_1234567890abcdef") as client:
        ...     repo = await client.get_repository("acme/app")
        ...     repo.default_branch
        'main'
    """

    def __init__(
        self,
        token: Optional[str] = None,
        api_base: str = GITHUB_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.token = (token or "").strip()
        self.api_base = (api_base or GITHUB_API_BASE).rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    # -------------------------------------------------------------------------
    # HTTP plumbing
    # -------------------------------------------------------------------------

    def _headers(self) -> dict:
        """
        Return the request headers (the token only travels in the header).

        Returns:
            Headers dict with the Accept/User-Agent always and the
            Authorization header only when a token is configured.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codx-junior",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _http(self) -> httpx.AsyncClient:
        """Return (creating it on first use) the underlying httpx client."""
        if self._client is None:
            kwargs = {"timeout": self.timeout, "headers": self._headers()}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.aclose()

    async def _request(self, path: str, params: Optional[dict] = None):
        """
        Perform a GET against the GitHub API and return the decoded payload.

        Args:
            path: API path, relative to ``api_base`` (e.g. ``/user/repos``).
            params: Optional query parameters.

        Returns:
            Decoded JSON payload.

        Raises:
            GitHubError: With the HTTP status when GitHub answers an error, or
                with status 0 when the request never completed.
        """
        url = f"{self.api_base}{path}"
        logger.info(
            "GitHub API GET %s (token=%s)", path, mask_token(self.token) or "none"
        )
        try:
            response = await self._http().get(
                url, params={k: v for k, v in (params or {}).items() if v is not None}
            )
        except httpx.HTTPError as ex:
            raise GitHubError(f"GitHub request failed: {ex.__class__.__name__}") from ex

        if response.status_code >= 400:
            raise GitHubError(
                self._error_message(response), status_code=response.status_code
            )

        try:
            return response.json()
        except ValueError as ex:
            raise GitHubError("GitHub returned a non JSON response") from ex

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """
        Build a readable error message from a failed GitHub response.

        Only the ``message`` field GitHub returns (or the raw body, truncated)
        is used, so no request header can end up in an error message.

        Args:
            response: Failed HTTP response.

        Returns:
            Message such as 'Bad credentials'.
        """
        message = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")
        except ValueError:
            message = (response.text or "").strip()[:MAX_ERROR_CHARS]
        return message or response.reason_phrase or "GitHub request failed"

    # -------------------------------------------------------------------------
    # Repositories
    # -------------------------------------------------------------------------

    async def list_repositories(
        self,
        per_page: int = PER_PAGE,
        max_pages: int = MAX_PAGES,
        visibility: Optional[str] = None,
        affiliation: Optional[str] = None,
    ) -> List[GitHubRepository]:
        """
        List the repositories the token can see, newest updated first.

        Pages are requested with ``per_page`` entries (100 by default, GitHub's
        maximum) until GitHub returns a short page or ``max_pages`` is reached.

        Args:
            per_page: Entries per page (capped at 100).
            max_pages: Maximum pages requested, so a listing is always bounded.
            visibility: Optional ``all``, ``public`` or ``private`` filter.
            affiliation: Optional affiliation filter passed to GitHub.

        Returns:
            Repositories, de-duplicated by ``full_name``.

        Example:
            >>> repos = await client.list_repositories(visibility="private")
            >>> [r.full_name for r in repos]
            ['acme/private-app']
        """
        page_size = max(1, min(int(per_page or PER_PAGE), PER_PAGE))
        pages = max(1, min(int(max_pages or 1), MAX_PAGES))

        repos: List[GitHubRepository] = []
        seen = set()
        for page in range(1, pages + 1):
            payload = await self._request(
                "/user/repos",
                params={
                    "per_page": page_size,
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                    "visibility": visibility,
                    "affiliation": affiliation,
                },
            )
            if not isinstance(payload, list):
                raise GitHubError("GitHub returned an unexpected repository list")

            for entry in payload:
                repo = GitHubRepository.from_api(entry or {})
                if not repo.full_name or repo.full_name in seen:
                    continue
                seen.add(repo.full_name)
                repos.append(repo)

            if len(payload) < page_size:
                break

        return repos

    async def get_repository(self, repo: str) -> GitHubRepository:
        """
        Return the metadata of a single repository.

        Args:
            repo: Repository identifier as ``owner/name`` (or a clone URL, from
                which ``owner/name`` is extracted).

        Returns:
            The repository metadata.

        Raises:
            GitHubError: 404 when the repository is not visible with the token.

        Example:
            >>> repo = await client.get_repository("acme/app")
            >>> repo.default_branch
            'main'
        """
        full_name = normalize_repo_name(repo)
        payload = await self._request(f"/repos/{full_name}")
        if not isinstance(payload, dict):
            raise GitHubError("GitHub returned an unexpected repository payload")
        repository = GitHubRepository.from_api(payload)
        if not repository.full_name:
            repository = repository.model_copy(update={"full_name": full_name})
        return repository


def normalize_repo_name(repo: str) -> str:
    """
    Normalize a repository reference to ``owner/name``.

    Accepts what a user (or the UI) is likely to paste: ``owner/name``,
    ``owner/name.git``, a clone URL or a browser URL.

    Args:
        repo: Repository reference.

    Returns:
        ``owner/name``.

    Raises:
        GitHubError: 400 when the reference does not identify a repository.

    Example:
        >>> normalize_repo_name("https://github.com/acme/app.git")
        'acme/app'
        >>> normalize_repo_name("acme/app")
        'acme/app'
    """
    value = (repo or "").strip()
    if not value:
        raise GitHubError("A repository (owner/name) is required", status_code=400)

    if "://" in value or value.startswith("git@"):
        value = value.rstrip("/")
        # Drop the scheme (https://) and the user info (git@)
        if "://" in value:
            value = value.split("://", 1)[1]
        # Drop a user info (user:token@ or git@)
        if "@" in value.split("/", 1)[0]:
            value = value.split("@", 1)[1]
        host, separator, rest = value.partition(":")
        if separator and "/" not in host:
            # scp-like remote (host:owner/name)
            value = rest
        else:
            # host/owner/name: keep the last two segments
            value = value.split("/", 1)[1] if "/" in value else ""

    value = value.strip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]

    parts = [part for part in value.split("/") if part]
    if len(parts) != 2:
        raise GitHubError(
            "The repository must be identified as owner/name", status_code=400
        )
    return f"{parts[0]}/{parts[1]}"
