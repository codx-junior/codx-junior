"""
Tests for ``switch_tab``: the tab the AI drives must follow the selection.

A reported symptom was "switch_tab reports success but the page is still the file
listing": the command layer is what decides whether the AI drives the tab it
selected, so these tests pin the contract with fake Playwright pages (no browser
needed):

- the returned descriptor is the selected tab, flagged ``active``;
- ``browser.page`` (what every later action and the live view follow) points at
  it, not at the previous one;
- ``list_tabs`` agrees, so the tab strip the user watches shows the same state.
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
        self.brought_to_front = 0

    async def title(self):
        return self._title

    def is_closed(self):
        return self._closed

    async def bring_to_front(self):
        self.brought_to_front += 1

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
    """Connected Browser double: ``page`` is the tab the AI drives."""

    def __init__(self, pages):
        self.context = _FakeContext(pages)
        for page in pages:
            page.context = self.context
        self.page = pages[0]
        self.session = _FakeSession(current_url=pages[0].url)


def _run(coroutine):
    return asyncio.run(coroutine)


def _listing():
    return _FakePage(url="file:///home/project/", title="File listing")


def _docs():
    return _FakePage(url="https://docs.test/start", title="Docs")


def _other():
    return _FakePage(url="https://other.test/", title="Other")


def test_switch_tab_moves_the_ai_to_the_selected_tab():
    listing, docs = _listing(), _docs()
    browser = _FakeBrowser([listing, docs])

    descriptor = _run(browser_tabs.switch_tab(browser, 1))

    assert descriptor["index"] == 1
    assert descriptor["url"] == "https://docs.test/start"
    assert descriptor["title"] == "Docs"
    assert descriptor["active"] is True
    # The next action (and the live view) follows browser.page.
    assert browser.page is docs
    assert docs.brought_to_front == 1
    assert browser.session.current_url == "https://docs.test/start"


def test_switch_tab_flips_the_active_flags_from_the_listing():
    listing, docs, other = _listing(), _docs(), _other()
    browser = _FakeBrowser([listing, docs, other])
    _run(browser_tabs.switch_tab(browser, 0))

    _run(browser_tabs.switch_tab(browser, 2))

    tabs = _run(browser_tabs.list_tabs(browser))
    assert [tab["active"] for tab in tabs] == [False, False, True]
    assert [tab["url"] for tab in tabs] == [
        "file:///home/project/",
        "https://docs.test/start",
        "https://other.test/",
    ]


def test_switch_tab_without_index_keeps_the_current_tab():
    listing, docs = _listing(), _docs()
    browser = _FakeBrowser([listing, docs])
    _run(browser_tabs.switch_tab(browser, 1))

    descriptor = _run(browser_tabs.switch_tab(browser))

    assert descriptor["index"] == 1
    assert browser.page is docs


def test_switch_tab_rejects_an_index_out_of_range():
    browser = _FakeBrowser([_listing(), _docs()])

    with pytest.raises(ValueError) as error:
        _run(browser_tabs.switch_tab(browser, 5))

    assert "out of range (0-1)" in str(error.value)
    assert browser.page is browser.context.pages[0]


def test_switch_tab_rejects_a_non_numeric_index():
    browser = _FakeBrowser([_listing(), _docs()])

    with pytest.raises(ValueError):
        _run(browser_tabs.switch_tab(browser, "the docs one"))


# Made with ❤️ by codx-junior
