"""Tests for the model facing web stack (guard, extraction, search, fetch)."""

import sys
import types

import pytest

from codx.junior.web import guard
from codx.junior.web.extract import (
    clean_markdown,
    extract_content,
    is_thin,
    text_from_markdown,
    truncate_markdown,
)
from codx.junior.web.settings import IntegrationConfig, WebSettings

ARTICLE = """
<html><head><title>Reader mode</title></head><body>
<nav>NAVTOKEN menu</nav>
<article><h1>Reader mode</h1>
<p>{body}</p>
</article>
<footer>FOOTERTOKEN</footer>
<script>var x = 1;</script>
</body></html>
""".format(body="Sentence with real content. " * 40)


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

def test_guard_rejects_private_and_local_targets():
    for url in (
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5:2375/containers/json",
        "http://localhost/admin",
    ):
        with pytest.raises(guard.UnsafeUrlError):
            guard.validate_url(url)


def test_guard_rejects_other_schemes():
    with pytest.raises(guard.UnsafeUrlError):
        guard.validate_url("file:///etc/passwd")
    with pytest.raises(guard.UnsafeUrlError):
        guard.validate_url("gopher://example.com")


def test_guard_normalizes_public_urls(monkeypatch):
    monkeypatch.setattr(guard, "resolve_host", lambda host, port=None: ["93.184.216.34"])
    assert guard.validate_url("https://example.com/a#frag") == "https://example.com/a"


def test_guard_allows_private_targets_when_asked():
    assert guard.validate_url("http://127.0.0.1/x", allow_private=True) == "http://127.0.0.1/x"


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def test_extraction_drops_page_chrome():
    content = extract_content(ARTICLE, url="https://example.com/post")
    assert content.extractor
    assert "Sentence with real content" in content.markdown
    assert "NAVTOKEN" not in content.markdown
    assert "FOOTERTOKEN" not in content.markdown
    assert "var x" not in content.markdown


def test_extraction_of_empty_document():
    content = extract_content("", url="https://example.com")
    assert content.extractor == "empty"
    assert content.markdown == ""
    assert is_thin(content)


def test_thin_detection_uses_text_length():
    content = extract_content("<html><body><p>hi</p></body></html>")
    assert is_thin(content)
    assert not is_thin(extract_content(ARTICLE), html_len=len(ARTICLE))


def test_markdown_helpers():
    assert text_from_markdown("See [docs](https://x) ![i](a.png) **now**") == "See docs now"
    assert clean_markdown("a\n\n\n\nb") == "a\n\nb"
    assert truncate_markdown("abcdef", 3) == ("abc", True)
    assert truncate_markdown("abc", None) == ("abc", False)


def test_bot_wall_detection():
    assert guard  # keeps the import used if hints move
    from codx.junior.web.extract import bot_wall_reason

    assert bot_wall_reason("<html>Just a moment...</html>")
    assert bot_wall_reason(ARTICLE) is None


# ---------------------------------------------------------------------------
# search chain
# ---------------------------------------------------------------------------

def _web_settings(*names):
    return WebSettings(search_backends=[IntegrationConfig(name=name) for name in names])


def test_search_falls_back_and_keeps_errors(monkeypatch):
    from codx.junior.web import search_backends

    def boom(config, query, max_results, search_type):
        raise search_backends.SearchBackendError("not configured")

    def ok(config, query, max_results, search_type):
        return [search_backends.SearchHit(title="T", url="https://example.com/x", snippet="S")]

    monkeypatch.setitem(search_backends._BACKENDS, "searxng", boom)
    monkeypatch.setitem(search_backends._BACKENDS, "ddgs", ok)

    result = search_backends.search_web(
        "query",
        backends=["searxng", "ddgs"],
        settings=_web_settings("searxng", "ddgs"),
    )

    assert result.backend == "ddgs"
    assert len(result.hits) == 1
    assert any("searxng" in error for error in result.errors)


def test_search_without_results_reports_every_backend(monkeypatch):
    from codx.junior.web import search_backends

    monkeypatch.setitem(
        search_backends._BACKENDS, "ddgs", lambda *args, **kwargs: []
    )

    result = search_backends.search_web(
        "nothing",
        backends=["ddgs"],
        settings=_web_settings("ddgs"),
    )

    assert result.is_empty
    assert result.errors == ["ddgs: no results"]


def test_search_validates_input():
    from codx.junior.web import search_backends

    with pytest.raises(ValueError):
        search_backends.search_web("  ", settings=_web_settings("ddgs"))
    with pytest.raises(ValueError):
        search_backends.search_web("q", search_type="videos", settings=_web_settings("ddgs"))


# ---------------------------------------------------------------------------
# fetch pipeline
# ---------------------------------------------------------------------------

def test_fetch_page_extracts_static_html(monkeypatch):
    from codx.junior.web import fetch as web_fetch

    page = guard.FetchedPage(
        url="https://example.com/post",
        final_url="https://example.com/post",
        status=200,
        content_type="text/html; charset=utf-8",
        text=ARTICLE,
    )
    monkeypatch.setattr(web_fetch, "validate_url", lambda url, allow_private=False: url)
    monkeypatch.setattr(web_fetch, "safe_get", lambda *args, **kwargs: page)

    result = web_fetch.fetch_page(
        "https://example.com/post",
        settings=WebSettings(browser_escalation=False, max_output_chars=100_000),
    )

    assert "Sentence with real content" in result.markdown
    assert "NAVTOKEN" not in result.markdown
    assert result.rendered is False
    assert result.final_url == "https://example.com/post"


def test_fetch_page_caps_the_output(monkeypatch):
    from codx.junior.web import fetch as web_fetch

    page = guard.FetchedPage(
        url="https://example.com/post",
        final_url="https://example.com/post",
        status=200,
        content_type="text/html",
        text=ARTICLE,
    )
    monkeypatch.setattr(web_fetch, "validate_url", lambda url, allow_private=False: url)
    monkeypatch.setattr(web_fetch, "safe_get", lambda *args, **kwargs: page)

    result = web_fetch.fetch_page(
        "https://example.com/post",
        max_chars=100,
        settings=WebSettings(browser_escalation=False),
    )

    assert len(result.markdown) == 100
    assert result.truncated is True


def test_fetch_page_refuses_private_targets():
    from codx.junior.web.fetch import fetch_page

    with pytest.raises(guard.UnsafeUrlError):
        fetch_page("http://169.254.169.254/latest/meta-data")


# ---------------------------------------------------------------------------
# browser escalation (no network, no Chromium: everything is faked)
# ---------------------------------------------------------------------------

class _FakeResponse:
    status = 200
    headers = {"content-type": "text/html"}


class _FakeChromiumPage:
    """Minimal stand-in for ``playwright.sync_api.Page``."""

    def __init__(self, html, final_url):
        self._html = html
        self.url = final_url
        self.routes = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def goto(self, url, wait_until=None, timeout=None):
        self.requested = (url, wait_until, timeout)
        return _FakeResponse()

    def wait_for_load_state(self, state, timeout=None):
        return None

    def content(self):
        return self._html


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = self
        self._browser = browser

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self._browser


def _fake_playwright(monkeypatch, html, final_url):
    """Install a fake ``playwright.sync_api`` so no browser runtime is needed."""
    page = _FakeChromiumPage(html, final_url)
    browser = _FakeBrowser(page)
    playwright = _FakePlaywright(browser)

    class _PlaywrightError(Exception):
        pass

    class _Manager:
        def __enter__(self):
            return playwright

        def __exit__(self, *exc_info):
            return False

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _Manager()
    module.Error = _PlaywrightError
    package = types.ModuleType("playwright")
    package.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return page


#: A page that only makes sense once JavaScript filled the ``#root`` div.
JS_SHELL = (
    "<html><head><title>App</title></head><body>"
    '<div id="root"></div><script src="/app.js"></script>'
    "</body></html>"
)


def test_fetch_page_escalates_to_the_browser(monkeypatch):
    from codx.junior.web import browser_fetch
    from codx.junior.web import fetch as web_fetch

    static_page = guard.FetchedPage(
        url="https://example.com/app",
        final_url="https://example.com/app",
        status=200,
        content_type="text/html",
        text=JS_SHELL,
    )
    rendered_page = guard.FetchedPage(
        url="https://example.com/app",
        final_url="https://example.com/app",
        status=200,
        content_type="text/html",
        text=ARTICLE,
    )
    monkeypatch.setattr(web_fetch, "validate_url", lambda url, allow_private=False: url)
    monkeypatch.setattr(web_fetch, "safe_get", lambda *args, **kwargs: static_page)
    monkeypatch.setattr(browser_fetch, "render_page", lambda *args, **kwargs: rendered_page)

    result = web_fetch.fetch_page(
        "https://example.com/app",
        max_chars=0,
        settings=WebSettings(browser_escalation=True),
    )

    assert result.rendered is True
    assert "Sentence with real content" in result.markdown
    assert "NAVTOKEN" not in result.markdown
    assert result.note == ""


def test_fetch_page_fails_closed_when_the_browser_is_missing(monkeypatch):
    from codx.junior.web import browser_fetch
    from codx.junior.web import fetch as web_fetch

    static_page = guard.FetchedPage(
        url="https://example.com/app",
        final_url="https://example.com/app",
        status=200,
        content_type="text/html",
        text='<html><body><p>STATICTOKEN</p><div id="root"></div></body></html>',
    )
    monkeypatch.setattr(web_fetch, "validate_url", lambda url, allow_private=False: url)
    monkeypatch.setattr(web_fetch, "safe_get", lambda *args, **kwargs: static_page)

    for error in (browser_fetch.BrowserUnavailable, browser_fetch.RenderError):
        def explode(*args, **kwargs):
            raise error("no chromium here")

        monkeypatch.setattr(browser_fetch, "render_page", explode)

        result = web_fetch.fetch_page(
            "https://example.com/app",
            max_chars=0,
            settings=WebSettings(browser_escalation=True),
        )

        assert result.rendered is False
        assert "STATICTOKEN" in result.markdown
        assert result.note == (
            "the page looks like a browser wall and no browser is available"
        )


def test_render_page_validates_the_url_before_and_after_the_load(monkeypatch):
    from codx.junior.web import browser_fetch

    calls = []
    real_validate = guard.validate_url

    def recording_validate(url, allow_private=False):
        calls.append((url, allow_private))
        return real_validate(url, allow_private=allow_private)

    def fake_resolve(host, port=None):
        # IP literals keep their own address; public hostnames stay public.
        return [host] if host == "169.254.169.254" else ["93.184.216.34"]

    monkeypatch.setattr(guard, "resolve_host", fake_resolve)
    monkeypatch.setattr(browser_fetch, "validate_url", recording_validate)
    _fake_playwright(
        monkeypatch,
        ARTICLE,
        final_url="http://169.254.169.254/latest/meta-data",
    )

    with pytest.raises(guard.UnsafeUrlError):
        browser_fetch.render_page("https://example.com/post")

    assert calls == [
        ("https://example.com/post", False),
        ("http://169.254.169.254/latest/meta-data", False),
    ]
