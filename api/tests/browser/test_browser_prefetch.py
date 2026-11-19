"""Tests for the browser prefetch cache and inline page brief."""

import asyncio

from codx.junior.browser.model import BrowserSession
from codx.junior.browser.runtime import BrowserRuntime


class _FakePage:
    url = "https://example.test/posts/new"


class _FakeBrowser:
    def __init__(self):
        self.page = _FakePage()
        self.snapshot_calls = 0
        self.screenshot_calls = 0

    def is_alive(self):
        return True

    async def get_snapshot(self):
        self.snapshot_calls += 1
        return {
            "url": "https://example.test/posts/new",
            "title": "New post",
            "interactive": [
                {"ref": "e1", "tag": "input", "name": "Title", "text": "", "selector": "#title"},
                {"ref": "e2", "tag": "button", "name": "Publish", "text": "Publish"},
            ],
            "text": "Create a post " * 300,
            "headings": [{"tag": "h1", "text": "New post"}],
        }

    async def get_screenshot(self, full_page=False):
        self.screenshot_calls += 1
        return "data:image/png;base64,AAAA"


def _runtime(browser):
    runtime = object.__new__(BrowserRuntime)
    runtime._browser = browser
    runtime.session = BrowserSession(name="prefetch", current_url=_FakePage.url)
    runtime.manager = None
    runtime._lock = asyncio.Lock()
    runtime._prefetch = {}
    runtime._closed = False
    return runtime


def test_prefetch_captures_and_serves_snapshot_and_screenshot():
    browser = _FakeBrowser()
    runtime = _runtime(browser)

    asyncio.run(runtime._capture_prefetch_locked())

    assert browser.snapshot_calls == 1
    assert browser.screenshot_calls == 1
    assert runtime.prefetch_info()["has_snapshot"] is True
    assert runtime.prefetch_info()["has_screenshot"] is True

    snapshot = asyncio.run(runtime.snapshot())
    assert snapshot["cached"] is True
    assert snapshot["title"] == "New post"
    # Served from memory: no extra browser call.
    assert browser.snapshot_calls == 1

    image = asyncio.run(runtime.screenshot(full_page=False))
    assert image.startswith("data:image/png")
    assert browser.screenshot_calls == 1


def test_prefetch_is_invalidated_when_the_url_changes():
    browser = _FakeBrowser()
    runtime = _runtime(browser)
    asyncio.run(runtime._capture_prefetch_locked())

    browser.page.url = "https://example.test/posts/1"
    assert runtime._cached_prefetch() == {}


def test_page_brief_is_small_and_reusable():
    browser = _FakeBrowser()
    runtime = _runtime(browser)
    asyncio.run(runtime._capture_prefetch_locked())

    brief = asyncio.run(runtime.page_brief())

    assert brief["url"] == "https://example.test/posts/new"
    assert [item["ref"] for item in brief["interactive"]] == ["e1", "e2"]
    assert len(brief["text"]) <= 1200
    assert brief["cached"] is True
    # The brief reused the prefetched snapshot instead of evaluating the page.
    assert browser.snapshot_calls == 1


def test_page_brief_captures_when_cache_is_empty():
    browser = _FakeBrowser()
    runtime = _runtime(browser)

    async def fake_run(method, *args, **kwargs):
        return await browser.get_snapshot()

    runtime.run = fake_run

    brief = asyncio.run(runtime.page_brief())

    assert brief["cached"] is False
    assert browser.snapshot_calls == 1
    assert len(brief["interactive"]) == 2
