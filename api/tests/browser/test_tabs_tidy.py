"""
Tests for the tab-strip hygiene: blank tabs and failed navigations.

Opening a tab and failing to navigate leaves an ``about:blank`` behind, so a
session used for a couple of days ended up with a tab strip full of them (20
blank tabs over 22 was observed in production). These tests drive the tab layer
with fake Playwright pages, so no browser is needed.
"""

import asyncio

import pytest

from codx.junior.browser import tabs as browser_tabs


class _FakeSession:
    """Minimal BrowserSession stand-in (only the fields the tab layer touches)."""

    def __init__(self, current_url=None):
        self.id = "12345678-aaaa-bbbb-cccc-ddddeeeeffff"
        self.current_url = current_url
        self.current_title = None

    def mark_active(self):
        pass


class _FakePage:
    def __init__(self, url="about:blank", title="", context=None):
        self.url = url
        self._title = title
        self._closed = False
        self.context = context

    async def title(self):
        return self._title

    def is_closed(self):
        return self._closed

    async def bring_to_front(self):
        pass

    async def close(self):
        self._closed = True
        if self.context and self in self.context.pages:
            self.context.pages.remove(self)


class _FakeContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])

    async def new_page(self):
        page = _FakePage(context=self)
        self.pages.append(page)
        return page


class _FakeBrowser:
    """Connected Browser double; ``navigate`` fails for a URL containing "boom"."""

    def __init__(self, pages=None):
        pages = list(pages) if pages else [_FakePage(url="https://a.test", title="A")]
        self.context = _FakeContext(pages)
        for page in pages:
            page.context = self.context
        self.page = pages[0]
        self.session = _FakeSession(current_url="https://a.test")

    async def navigate(self, url):
        if "boom" in url:
            raise RuntimeError(f"navigation to {url} failed")
        self.page.url = url
        return url


def _run(coroutine):
    return asyncio.run(coroutine)


def _blank_pages(count):
    return [_FakePage(url="about:blank") for _ in range(count)]


# ---------------------------------------------------------------------------
# tidy_blank_tabs
# ---------------------------------------------------------------------------

def test_blank_tabs_are_closed_and_the_working_tab_is_kept():
    working = _FakePage(url="https://a.test", title="A")
    keep_alive = _FakePage(url=browser_tabs.KEEP_ALIVE_URL, title=browser_tabs.KEEP_ALIVE_TITLE)
    blanks = _blank_pages(5)
    browser = _FakeBrowser([working, keep_alive, *blanks])

    result = _run(browser_tabs.tidy_blank_tabs(browser))

    assert result["closed"] == 5
    assert result["blank"] == 5
    assert browser.context.pages == [working, keep_alive]
    assert browser.page is working
    assert not any(page.is_closed() for page in (working, keep_alive))


def test_one_or_two_blank_tabs_are_left_alone():
    working = _FakePage(url="https://a.test", title="A")
    blanks = _blank_pages(2)
    browser = _FakeBrowser([working, *blanks])

    result = _run(browser_tabs.tidy_blank_tabs(browser))

    assert result["closed"] == 0
    assert result["threshold"] == browser_tabs.BLANK_TAB_THRESHOLD
    assert len(browser.context.pages) == 3
    assert not any(page.is_closed() for page in blanks)


def test_the_active_tab_is_never_closed_even_when_it_is_blank():
    blank_active = _FakePage(url="about:blank")
    browser = _FakeBrowser([blank_active, *_blank_pages(4)])
    browser.page = blank_active

    result = _run(browser_tabs.tidy_blank_tabs(browser))

    assert result["closed"] == 4
    assert not blank_active.is_closed()
    assert browser.page is blank_active


def test_a_tidy_pass_is_capped():
    working = _FakePage(url="https://a.test", title="A")
    blanks = _blank_pages(browser_tabs.MAX_TIDY_CLOSES + 10)
    browser = _FakeBrowser([working, *blanks])

    result = _run(browser_tabs.tidy_blank_tabs(browser))

    assert result["closed"] == browser_tabs.MAX_TIDY_CLOSES
    assert len(browser.context.pages) == 11


def test_tidy_requires_a_connected_browser():
    browser = _FakeBrowser()
    browser.context = None

    with pytest.raises(RuntimeError):
        _run(browser_tabs.tidy_blank_tabs(browser))


# ---------------------------------------------------------------------------
# open_tab
# ---------------------------------------------------------------------------

def test_open_tab_does_not_leave_a_blank_tab_when_the_navigation_fails():
    working = _FakePage(url="https://a.test", title="A")
    browser = _FakeBrowser([working])

    with pytest.raises(RuntimeError):
        _run(browser_tabs.open_tab(browser, "https://boom.test"))

    # The failed tab is gone and the previous one is active again
    assert browser.context.pages == [working]
    assert browser.page is working


def test_open_tab_opens_and_navigates():
    working = _FakePage(url="https://a.test", title="A")
    browser = _FakeBrowser([working])

    tab = _run(browser_tabs.open_tab(browser, "https://b.test"))

    assert tab["url"] == "https://b.test"
    assert tab["active"] is True
    assert len(browser.context.pages) == 2
    assert browser.page.url == "https://b.test"
