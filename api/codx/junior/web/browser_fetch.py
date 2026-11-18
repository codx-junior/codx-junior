"""Chromium escalation for JavaScript rendered pages.

The static pipeline (:mod:`codx.junior.web.guard` + :mod:`codx.junior.web.extract`)
is enough for server rendered pages, but a single page application answers with
an empty shell and a few ``<script>`` tags. :func:`render_page` takes over in
that case: it loads the URL in a headless Chromium (Playwright), waits for the
network to settle and returns the *rendered* DOM as a :class:`FetchedPage`, so
the caller keeps using exactly the same extraction path.

Playwright is an optional dependency (the ``browser`` extra of
``api/pyproject.toml``), and the browser runtime is not present in the slim
image. The module therefore imports it lazily: when it is missing
:class:`BrowserUnavailable` is raised, and
:func:`codx.junior.web.fetch._escalate` falls back to the static extraction
instead of failing the tool.

The session oriented automation lives in :mod:`codx.junior.browser` (containers,
CDP, audit); this module is deliberately stateless and session free, because the
``fetch_webpage`` tool must work without a shared computer session.
"""

import asyncio
import concurrent.futures
import logging
from typing import Any, Dict, Optional

from .guard import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    FetchedPage,
    validate_url,
)

logger = logging.getLogger(__name__)

#: Wall-clock budget of a render, in seconds. Rendering is slower than a GET.
DEFAULT_RENDER_TIMEOUT_SECONDS = 30.0

#: Wait after ``load`` for pending XHR/fetch calls, in milliseconds.
NETWORK_IDLE_TIMEOUT_MS = 5_000

#: Default viewport: a desktop size, so responsive sites serve their full layout.
DEFAULT_VIEWPORT = {"width": 1366, "height": 900}

#: Desktop user agent; some sites serve a degraded page to obvious headless UAs.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

#: Resource types dropped before rendering: they never carry readable text and
#: they are the bulk of the transfer on media heavy pages.
BLOCKED_RESOURCES = ("media", "font", "websocket")


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright, or its Chromium build, is not installed."""


class RenderError(RuntimeError):
    """Raised when Chromium could not load the page."""


def is_available() -> bool:
    """Return True when the Playwright client can be imported.

    The check is cheap and side effect free, so callers can decide whether
    escalation is worth attempting at all.

    Returns:
        bool: True when ``playwright.sync_api`` is importable.
    """
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def _timeout_seconds(timeout: Optional[float]) -> float:
    """Resolve the render timeout, falling back to the module default."""
    try:
        value = float(timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_RENDER_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_RENDER_TIMEOUT_SECONDS


def _bound(text: str, max_bytes: Optional[int]) -> tuple:
    """Cut ``text`` to ``max_bytes`` UTF-8 bytes.

    Args:
        text: Rendered markup.
        max_bytes: Byte budget (``None`` or ``<= 0`` disables the cap).

    Returns:
        tuple: ``(text, truncated)`` with the same contract as
        :func:`codx.junior.web.guard.safe_get`.
    """
    try:
        limit = int(max_bytes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_BYTES
    if limit <= 0:
        return text, False
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= limit:
        return text, False
    cut = encoded[:limit].decode("utf-8", errors="ignore")
    return cut, True


def _render_sync(
    url: str,
    timeout: float,
    allow_private: bool,
    max_bytes: Optional[int],
    user_agent: str,
) -> FetchedPage:
    """Render ``url`` with the synchronous Playwright API.

    This helper must run in a thread without a running event loop: the
    synchronous API refuses to start otherwise.

    Args:
        url: Already validated URL.
        timeout: Render budget in seconds.
        allow_private: Whether private hosts are acceptable.
        max_bytes: Markup budget in bytes.
        user_agent: User agent sent by the browser context.

    Returns:
        FetchedPage: The rendered page.

    Raises:
        BrowserUnavailable: Playwright or Chromium is missing.
        RenderError: Chromium could not load the page.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as ex:  # pragma: no cover - depends on the installation
        raise BrowserUnavailable("playwright is not installed") from ex

    timeout_ms = int(timeout * 1000)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                context = browser.new_context(
                    user_agent=user_agent,
                    viewport=DEFAULT_VIEWPORT,
                    ignore_https_errors=False,
                )
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in BLOCKED_RESOURCES
                        else route.continue_()
                    ),
                )
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                try:
                    page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
                except PlaywrightError:
                    # Long polling / analytics keep the network busy: the DOM we
                    # already have is good enough.
                    logger.debug("networkidle not reached for %s", url)
                html = page.content()
                final_url = page.url or url
                status = int(getattr(response, "status", 0) or 0)
                headers: Dict[str, Any] = getattr(response, "headers", {}) or {}
                try:
                    content_type = headers.get("content-type", "text/html") or "text/html"
                except AttributeError:  # pragma: no cover - defensive
                    content_type = "text/html"
            finally:
                browser.close()
    except BrowserUnavailable:
        raise
    except PlaywrightError as ex:
        raise RenderError(f"chromium could not render {url}: {ex}") from ex

    # A client side redirect may land on an internal address: re-validate the
    # URL Chromium actually visited, exactly like the static pipeline does.
    validate_url(final_url, allow_private=allow_private)

    text, truncated = _bound(html, max_bytes)
    return FetchedPage(
        url=url,
        final_url=final_url,
        status=status,
        content_type=content_type,
        text=text,
        bytes_read=len(text.encode("utf-8", errors="ignore")),
        truncated=truncated,
        redirects=[],
    )


def render_page(
    url: str,
    timeout: Optional[float] = DEFAULT_TIMEOUT_SECONDS,
    allow_private: bool = False,
    max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
) -> FetchedPage:
    """Load ``url`` in headless Chromium and return the rendered DOM.

    Args:
        url: Page to render.
        timeout: Render budget in seconds (defaults to the static fetch budget).
        allow_private: Whether private hosts are acceptable. ``False`` keeps the
            SSRF policy of :mod:`codx.junior.web.guard`.
        max_bytes: Markup budget in bytes; the body is cut and ``truncated`` set.
        user_agent: User agent used by the browser context.

    Returns:
        FetchedPage: Rendered page, ready for
        :func:`codx.junior.web.extract.extract_content`.

    Raises:
        UnsafeUrlError: When the URL (or its final redirect target) is not
            acceptable for an outbound request.
        BrowserUnavailable: When Playwright or its Chromium build is missing.
        RenderError: When Chromium could not load the page.
    """
    validate_url(url, allow_private=allow_private)
    budget = _timeout_seconds(timeout)
    args = (url, budget, allow_private, max_bytes, user_agent)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop in this thread: the synchronous API is safe to use directly.
        return _render_sync(*args)

    # The tool layer is async: the synchronous Playwright API refuses to start
    # inside a running loop, so it gets a worker thread of its own.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_render_sync, *args).result()


#: Alias kept for callers (and older integrations) using the other name.
fetch_rendered = render_page

__all__ = [
    "BrowserUnavailable",
    "RenderError",
    "DEFAULT_RENDER_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "DEFAULT_VIEWPORT",
    "fetch_rendered",
    "is_available",
    "render_page",
]
