"""Fetch pipeline of the model facing ``fetch_webpage`` tool.

:func:`fetch_page` is the single entry point, and the only place where the
pieces of the web stack are assembled:

1. :func:`codx.junior.web.guard.validate_url` rejects bad schemes and private /
   reserved targets before any socket is opened;
2. :func:`codx.junior.web.guard.safe_get` performs the bounded request (body
   size, wall-clock deadline, redirects re-validated on every hop);
3. :mod:`codx.junior.web.extract` turns the HTML into chrome free markdown;
4. when the page is a JavaScript shell, a cookie wall or an anti-bot
   interstitial and ``browser_escalation`` is enabled, ``browser_fetch`` renders
   it once with Chromium and the extraction is retried;
5. the markdown is capped to the requested number of characters, so the model
   never pays for content it did not ask for.

``validate_url`` and ``safe_get`` are module level names on purpose: tests
replace them to exercise the pipeline without network access.

Made with ❤️ by codx-junior
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .extract import (
    bot_wall_reason,
    clean_markdown,
    extract_content,
    is_thin,
    truncate_markdown,
)
from .guard import FetchedPage, is_html, is_pdf, safe_get, validate_url
from .settings import WebSettings, load_web_settings

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Readable content of one URL.

    Attributes:
        url: URL that was requested.
        final_url: URL after redirects.
        status: HTTP status of the last hop.
        title: Document title, when the page declares one.
        markdown: Chrome free article, already capped.
        extractor: Strategy that produced ``markdown`` (``trafilatura``,
            ``reader``, ``empty`` or ``pdf``).
        content_type: ``Content-Type`` of the response.
        rendered: True when Chromium had to render the page.
        truncated: True when the body or the markdown was cut.
        bytes_read: Body bytes read from the socket.
        wall_reason: Anti-bot marker that was detected, if any.
        note: Human readable remark about the result (empty when all went well).
        redirects: Redirect hops that were followed.
    """

    url: str
    final_url: str = ""
    status: int = 0
    title: str = ""
    markdown: str = ""
    extractor: str = ""
    content_type: str = ""
    rendered: bool = False
    truncated: bool = False
    bytes_read: int = 0
    wall_reason: Optional[str] = None
    note: str = ""
    redirects: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when no readable text could be extracted."""
        return not (self.markdown or "").strip()


def _load_settings(settings: Optional[WebSettings]) -> WebSettings:
    """Return the given settings, or the project ones (never failing)."""
    if settings is not None:
        return settings
    try:
        return load_web_settings()
    except Exception:  # pragma: no cover - defensive, fetch must not break
        return WebSettings()


def _escalate(page: FetchedPage, settings: WebSettings) -> Optional[FetchedPage]:
    """Render ``page`` with Chromium, when the browser module is installed.

    Args:
        page: Page that came back as a JavaScript shell or a browser wall.
        settings: Resolved web settings (timeouts, private host policy).

    Returns:
        Optional[FetchedPage]: The rendered page, or ``None`` when escalation is
        unavailable or failed (the caller then returns the static extraction).
    """
    try:
        from . import browser_fetch
    except ImportError:
        logger.debug("browser escalation requested but browser_fetch is missing")
        return None

    renderer = getattr(browser_fetch, "render_page", None) or getattr(
        browser_fetch, "fetch_rendered", None
    )
    if renderer is None:
        logger.debug("browser_fetch exposes no renderer")
        return None

    try:
        return renderer(
            page.final_url or page.url,
            timeout=settings.timeout,
            allow_private=settings.allow_private_hosts,
        )
    except Exception as ex:  # pragma: no cover - depends on Chromium
        logger.warning("browser escalation failed for %s: %s", page.url, ex)
        return None


def _limit_for(max_chars: Optional[int], settings: WebSettings) -> Optional[int]:
    """Resolve the markdown cap: explicit value first, settings as fallback."""
    if max_chars is None:
        return settings.max_output_chars
    try:
        value = int(max_chars)
    except (TypeError, ValueError):
        return settings.max_output_chars
    return value if value > 0 else None


def fetch_page(
    url: str,
    max_chars: Optional[int] = None,
    settings: Optional[WebSettings] = None,
    allow_private: Optional[bool] = None,
    headers: Optional[Dict[str, str]] = None,
) -> FetchResult:
    """Fetch a URL and return its readable content as markdown.

    Args:
        url: Target URL (untrusted, validated by the guard).
        max_chars: Cap for ``markdown``; defaults to
            ``settings.max_output_chars``. ``<= 0`` means no cap.
        settings: Resolved web settings; loaded from the project when omitted.
        allow_private: Override of ``settings.allow_private_hosts``.
        headers: Extra request headers merged over the guard defaults.

    Returns:
        FetchResult: Markdown, metadata and the diagnostics of the pipeline.

    Raises:
        UnsafeUrlError: The URL is unsafe, unresolvable or redirects too much.
        FetchError: The server answered with a 4xx/5xx status.

    Example:
        >>> fetch_page("https://example.com").markdown[:20]  # doctest: +SKIP
        'Example Domain'
    """
    resolved = _load_settings(settings)
    private_ok = resolved.allow_private_hosts if allow_private is None else bool(allow_private)

    target = validate_url(url, allow_private=private_ok)
    page = safe_get(
        target,
        headers=headers,
        timeout=resolved.timeout,
        max_bytes=resolved.max_bytes,
        allow_private=private_ok,
    )

    if is_pdf(page):
        return FetchResult(
            url=url,
            final_url=page.final_url or target,
            status=page.status,
            content_type=page.content_type,
            extractor="pdf",
            markdown="",
            bytes_read=page.bytes_read,
            truncated=page.truncated,
            note="PDF document: only the link is returned, no text is extracted",
            redirects=list(page.redirects),
        )

    html = page.text or ""
    content = extract_content(html, url=page.final_url or target)
    wall = bot_wall_reason(html)

    rendered = False
    note = ""
    if (
        resolved.browser_escalation
        and is_html(page)
        and (wall or content.is_empty or is_thin(content, html_len=len(html)))
    ):
        rendered_page = _escalate(page, resolved)
        if rendered_page is not None:
            page = rendered_page
            html = page.text or ""
            content = extract_content(html, url=page.final_url or target)
            wall = bot_wall_reason(html)
            rendered = True
        else:
            note = "the page looks like a browser wall and no browser is available"

    markdown = clean_markdown(content.markdown)
    markdown, cut = truncate_markdown(markdown, _limit_for(max_chars, resolved))

    return FetchResult(
        url=url,
        final_url=page.final_url or target,
        status=page.status,
        title=content.title or page.url,
        markdown=markdown,
        extractor=content.extractor,
        content_type=page.content_type,
        rendered=rendered,
        truncated=bool(cut or page.truncated),
        bytes_read=page.bytes_read,
        wall_reason=wall,
        note=note,
        redirects=list(page.redirects),
    )
