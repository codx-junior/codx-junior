"""Search providers for the model facing ``web_search`` tool.

The tool must answer even when a provider is down, so :func:`search_web` walks
the configured providers in order and never lets a failure escape: the first
provider that returns results wins and the errors of the providers tried before
it travel with the answer, so the model can tell "nothing found" apart from
"the backends are broken".

Providers:

* ``searxng`` - self hosted meta search, JSON API, no key;
* ``brave``   - Brave Search API, key;
* ``tavily``  - Tavily search API, key;
* ``ddgs``    - DuckDuckGo through the ``ddgs`` package (the default).

Every provider is called with ``(config, query, max_results, search_type)`` and
returns a list of hits (dicts or :class:`SearchHit`), which keeps the chain
trivial to extend and to test.

Made with ❤️ by codx-junior
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from .settings import IntegrationConfig, WebSettings, load_web_settings

logger = logging.getLogger(__name__)

#: Search flavours understood by :func:`search_web`.
SEARCH_TYPES = ("text", "news", "images")

#: Results requested when the caller does not say.
DEFAULT_MAX_RESULTS = 8

#: Hard cap on the number of results handed to the model.
MAX_RESULTS_LIMIT = 20

#: Endpoint of the key based providers.
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/{kind}/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"

#: SearXNG category per search flavour.
_SEARXNG_CATEGORIES = {"text": "general", "news": "news", "images": "images"}

#: Brave endpoint per search flavour.
_BRAVE_KINDS = {"text": "web", "news": "news", "images": "images"}


class SearchBackendError(ValueError):
    """Raised when a provider cannot answer a query."""


@dataclass
class SearchHit:
    """One search result.

    Attributes:
        title: Result title.
        url: Result URL.
        snippet: Short description handed to the model.
    """

    title: str = ""
    url: str = ""
    snippet: str = ""

    def as_dict(self) -> Dict[str, str]:
        """Return the plain mapping used by the tool layer."""
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass
class SearchResult:
    """Outcome of a whole search chain.

    Attributes:
        query: Query that was searched (already stripped).
        backend: Provider that produced ``hits`` (empty when nobody did).
        hits: Results, in provider order.
        errors: ``"<backend>: <reason>"`` entries for the providers that failed
            or returned nothing, in the order they were tried.
        search_type: Flavour that was requested.
    """

    query: str = ""
    backend: str = ""
    hits: List[SearchHit] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    search_type: str = "text"

    @property
    def is_empty(self) -> bool:
        """True when no provider returned a single result."""
        return not self.hits

    def as_dict(self) -> Dict[str, Any]:
        """Return the serialisable payload used by the tool layer."""
        return {
            "query": self.query,
            "backend": self.backend,
            "search_type": self.search_type,
            "results": [hit.as_dict() for hit in self.hits],
            "errors": list(self.errors),
        }


def _timeout(config: IntegrationConfig) -> float:
    """Return the per request timeout declared by an integration."""
    try:
        return float(getattr(config, "timeout", None) or 20.0)
    except (TypeError, ValueError):
        return 20.0


def _json(response: requests.Response) -> Dict[str, Any]:
    """Decode a JSON body, raising :class:`SearchBackendError` when it is not one."""
    try:
        data = response.json()
    except ValueError:
        raise SearchBackendError(f"invalid JSON response (HTTP {response.status_code})")
    if not isinstance(data, dict):
        raise SearchBackendError("unexpected response shape")
    return data


def _get(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 20.0) -> requests.Response:
    """Perform a GET request and turn transport errors into backend errors."""
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as ex:
        raise SearchBackendError(str(ex) or ex.__class__.__name__)
    if response.status_code >= 400:
        raise SearchBackendError(f"HTTP {response.status_code}")
    return response


def _searxng(config: IntegrationConfig, query: str, max_results: int, search_type: str) -> List[Dict[str, Any]]:
    """Search a self hosted SearXNG instance through its JSON API."""
    base = (getattr(config, "url", "") or "").strip().rstrip("/")
    if not base:
        raise SearchBackendError("not configured (url)")
    response = _get(
        f"{base}/search",
        params={
            "q": query,
            "format": "json",
            "categories": _SEARXNG_CATEGORIES.get(search_type, "general"),
        },
        headers={"Accept": "application/json"},
        timeout=_timeout(config),
    )
    return _json(response).get("results") or []


def _brave(config: IntegrationConfig, query: str, max_results: int, search_type: str) -> List[Dict[str, Any]]:
    """Search through the Brave Search API (subscription token required)."""
    api_key = (getattr(config, "api_key", "") or "").strip()
    if not api_key:
        raise SearchBackendError("not configured (api_key)")
    kind = _BRAVE_KINDS.get(search_type, "web")
    response = _get(
        BRAVE_ENDPOINT.format(kind=kind),
        params={"q": query, "count": max_results},
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=_timeout(config),
    )
    data = _json(response)
    for name in ("web", "news", "results"):
        block = data.get(name)
        if isinstance(block, dict):
            block = block.get("results")
        if block:
            return block
    return []


def _tavily(config: IntegrationConfig, query: str, max_results: int, search_type: str) -> List[Dict[str, Any]]:
    """Search through the Tavily API (key required)."""
    api_key = (getattr(config, "api_key", "") or "").strip()
    if not api_key:
        raise SearchBackendError("not configured (api_key)")
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "topic": "news" if search_type == "news" else "general",
        "include_images": search_type == "images",
    }
    try:
        response = requests.post(TAVILY_ENDPOINT, json=payload, timeout=_timeout(config))
    except requests.RequestException as ex:
        raise SearchBackendError(str(ex) or ex.__class__.__name__)
    if response.status_code >= 400:
        raise SearchBackendError(f"HTTP {response.status_code}")
    return _json(response).get("results") or []


def _ddgs(config: IntegrationConfig, query: str, max_results: int, search_type: str) -> List[Dict[str, Any]]:
    """Search DuckDuckGo through the optional ``ddgs`` package."""
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - optional dependency
        raise SearchBackendError("ddgs package is not installed")

    options = dict(getattr(config, "options", None) or {})
    try:
        client = DDGS()
        if search_type == "images":
            return client.images(query, max_results=max_results, **options)
        if search_type == "news":
            return client.news(query, max_results=max_results, **options)
        return client.text(query, max_results=max_results, **options)
    except Exception as ex:  # the library raises its own exception types
        raise SearchBackendError(str(ex) or ex.__class__.__name__)


#: Provider implementations by name. Tests replace entries to simulate failures.
_BACKENDS: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "searxng": _searxng,
    "brave": _brave,
    "tavily": _tavily,
    "ddgs": _ddgs,
}


def available_backends() -> List[str]:
    """Return the provider names this installation understands."""
    return sorted(_BACKENDS)


def _normalize_hits(raw: Any) -> List[SearchHit]:
    """Turn whatever a provider returned into a list of usable :class:`SearchHit`."""
    hits: List[SearchHit] = []
    for item in raw or []:
        if isinstance(item, SearchHit):
            hit = item
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("href") or item.get("link") or "").strip()
            hit = SearchHit(
                title=str(item.get("title") or item.get("name") or "").strip(),
                url=url,
                snippet=str(
                    item.get("snippet")
                    or item.get("content")
                    or item.get("body")
                    or item.get("description")
                    or ""
                ).strip(),
            )
        else:
            continue
        if not hit.url:
            continue
        hits.append(hit)
    return hits


def _resolve_configs(settings: WebSettings) -> Dict[str, IntegrationConfig]:
    """Map the enabled integrations by lower case name."""
    configs: Dict[str, IntegrationConfig] = {}
    for entry in getattr(settings, "search_backends", None) or []:
        name = (getattr(entry, "name", "") or "").strip().lower()
        if name:
            configs[name] = entry
    return configs


def _normalize_max_results(max_results: Any) -> int:
    """Clamp the requested number of results into the accepted range."""
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESULTS
    return max(1, min(value, MAX_RESULTS_LIMIT))


def search_web(
    query: str,
    backends: Optional[Sequence[str]] = None,
    settings: Optional[WebSettings] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_type: str = "text",
) -> SearchResult:
    """Search the web, falling back through the providers until one answers.

    Args:
        query: Text to search; blank values are rejected.
        backends: Provider names to try, in order. Defaults to the enabled
            integrations of ``settings``.
        settings: Resolved web settings; loaded from the project when omitted.
        max_results: Results requested per provider (clamped).
        search_type: One of :data:`SEARCH_TYPES`.

    Returns:
        SearchResult: Hits of the first provider that answered, plus the errors
        of the providers tried before it.

    Raises:
        ValueError: The query is blank or ``search_type`` is not supported.

    Example:
        >>> search_web("codx junior", backends=["ddgs"]).backend  # doctest: +SKIP
        'ddgs'
    """
    text = (query or "").strip()
    if not text:
        raise ValueError("A search query is required")

    kind = (search_type or "text").strip().lower()
    if kind not in SEARCH_TYPES:
        raise ValueError(
            f"Unsupported search_type {search_type!r}, expected one of {SEARCH_TYPES}"
        )

    if settings is None:
        try:
            settings = load_web_settings()
        except Exception:  # pragma: no cover - defensive, search must not break
            settings = WebSettings()

    configs = _resolve_configs(settings)

    names = [str(name).strip().lower() for name in (backends or []) if str(name).strip()]
    if not names:
        names = [
            name
            for name, entry in configs.items()
            if getattr(entry, "enabled", True)
        ]
    if not names:
        names = ["ddgs"]

    limit = _normalize_max_results(max_results)
    errors: List[str] = []

    for name in names:
        runner = _BACKENDS.get(name)
        if runner is None:
            errors.append(f"{name}: unknown backend")
            continue

        config = configs.get(name) or IntegrationConfig(name=name)
        try:
            raw_hits = runner(config, text, limit, kind)
        except Exception as ex:
            errors.append(f"{name}: {ex}")
            continue

        hits = _normalize_hits(raw_hits)
        if not hits:
            errors.append(f"{name}: no results")
            continue

        return SearchResult(
            query=text,
            backend=name,
            hits=hits,
            errors=errors,
            search_type=kind,
        )

    return SearchResult(query=text, backend="", hits=[], errors=errors, search_type=kind)
