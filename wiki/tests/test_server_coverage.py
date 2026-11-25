"""Coverage tests for the ``server.py`` paths not exercised by ``test_server.py``.

Scope: the retrieval ranker (``_select_pages``), the prompt builder (``_prompt``),
the assistant endpoint (payload validation and the upstream-failure fallback) and
the static-site resolver (directory index, extension fallback, ``api/`` guard and
missing build output). The frontmatter failure path of ``_page`` is covered here
too, because a malformed header is a realistic editor accident.

Constraints match ``tests/test_server.py``: no network, no clock, no shared
mutable state. Every test builds its own tree in ``tmp_path`` and rebinds the
module globals with ``monkeypatch``; the upstream LLM call is replaced by
patching ``urllib.request.urlopen`` on the ``server`` module.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

import server


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    """An isolated wiki with two real pages and one manifest-only page."""
    root = tmp_path / "wiki"
    root.mkdir()

    (root / "guide.md").write_text(
        "---\n"
        "title: Guide Title\n"
        "description: How to build the docs\n"
        "---\n"
        "\n"
        "# Heading One\n"
        "\n"
        "Body text.\n",
        encoding="utf-8",
    )
    (root / "plain.md").write_text(
        "# Plain Title\n\nNo frontmatter here.\n",
        encoding="utf-8",
    )
    (root / "broken.md").write_text(
        "---\n"
        "title: [unclosed\n"
        "---\n"
        "\n"
        "# Recovered Heading\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    (root / "docs.json").write_text(
        json.dumps(
            {
                "version": 2,
                "project": {"title": "Demo Project", "description": "Demo"},
                "pages": {
                    "guide": {
                        "path": "guide",
                        "title": "Guide Title",
                        "description": "How to build the docs",
                        "tags": ["docker"],
                        "updated_at": 10,
                    },
                    "plain": {
                        "path": "plain",
                        "title": "Plain Title",
                        "description": "Something else entirely",
                        "tags": [],
                        "updated_at": 20,
                    },
                    "ghost": {
                        "path": "ghost",
                        "title": "Ghost Page",
                        "description": "Registered but never generated",
                        "tags": [],
                        "updated_at": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "WIKI_ROOT", root)
    monkeypatch.setattr(server, "MANIFEST", root / "docs.json")
    monkeypatch.setattr(server, "WIKI_DIST", root / ".vitepress" / "dist")
    return root


class _FakeAIResponse:
    """Minimal stand-in for the ``urlopen`` context manager."""

    def __init__(self, payload):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _patch_ai(monkeypatch, payload):
    monkeypatch.setattr(
        server.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeAIResponse(payload),
    )


def _patch_ai_error(monkeypatch, error):
    def _raise(*args, **kwargs):
        raise error

    monkeypatch.setattr(server.urllib.request, "urlopen", _raise)


# --------------------------------------------------------------- _select_pages


def test_select_pages_returns_empty_when_manifest_has_no_pages(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    manifest = empty / "docs.json"
    manifest.write_text(json.dumps({"version": 2, "project": {"title": "X"}}), encoding="utf-8")
    monkeypatch.setattr(server, "WIKI_ROOT", empty)
    monkeypatch.setattr(server, "MANIFEST", manifest)

    assert server._select_pages("anything", None) == []


def test_select_pages_ranks_by_tag_match_and_loads_the_page(wiki_root):
    pages = server._select_pages("docker", None)

    assert [page["path"] for page in pages] == ["guide"]
    assert pages[0]["title"] == "Guide Title"


def test_select_pages_honours_the_page_hint(wiki_root):
    pages = server._select_pages("docker", "/plain")

    assert [page["path"] for page in pages] == ["plain", "guide"]


def test_select_pages_falls_back_to_recent_pages_and_skips_missing_files(wiki_root):
    pages = server._select_pages("zzz-no-such-term-zzz", None)

    # plain (updated_at 20) before guide (10); "ghost" is ranked but has no file.
    assert [page["path"] for page in pages] == ["plain", "guide"]


# --------------------------------------------------------------------- _prompt


def test_prompt_renders_project_docs_and_last_eight_messages(wiki_root):
    pages = [server._page("guide")]
    history = [{"role": "user", "content": f"turn-{index}"} for index in range(10)]

    prompt = server._prompt("how do I build?", pages, history)

    assert "<name>Demo Project</name>" in prompt
    assert '<doc path="guide" title="Guide Title">' in prompt
    assert "turn-9" in prompt
    assert "turn-1" not in prompt
    assert "<question>\nhow do I build?\n</question>" in prompt


def test_prompt_degrades_gracefully_without_docs_or_history(wiki_root):
    prompt = server._prompt("anything", [], [])

    assert "(no documentation pages found)" in prompt
    assert "(new conversation)" in prompt


# ------------------------------------------------------------- _page failures


@pytest.mark.parametrize(
    "filename",
    ["broken", "no-frontmatter-dict"],
)
def test_page_survives_unparsable_frontmatter(wiki_root, filename):
    if filename == "no-frontmatter-dict":
        (wiki_root / f"{filename}.md").write_text(
            "---\n- a\n- b\n---\n\n# List Header\n", encoding="utf-8"
        )

    page = server._page(filename)

    assert page is not None
    # The header block is always stripped from the body, even on failure.
    assert not page["content"].lstrip().startswith("---")
    assert page["title"] in {"Recovered Heading", "List Header", "No Frontmatter Dict"}


# ----------------------------------------------------------- /api/assistant


def test_assistant_rejects_a_missing_question(wiki_root):
    response = TestClient(server.app).post("/api/assistant", json={"question": "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == "A question is required"


def test_assistant_returns_the_model_answer_and_citations(wiki_root, monkeypatch):
    _patch_ai(
        monkeypatch,
        {"choices": [{"message": {"content": "  Build it with npm.  "}}]},
    )

    response = TestClient(server.app).post(
        "/api/assistant", json={"question": "how do I build the docs"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Build it with npm."
    assert payload["pages_used"] == ["guide"]
    assert payload["citations"][0]["path"] == "guide"
    assert payload["citations"][0]["title"] == "Guide Title"
    assert payload["model"] == server.AI_MODEL


def test_assistant_falls_back_to_closest_pages_when_the_model_is_down(wiki_root, monkeypatch):
    _patch_ai_error(monkeypatch, urllib.error.URLError("connection refused"))

    response = TestClient(server.app).post(
        "/api/assistant", json={"question": "how do I build the docs"}
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert answer.startswith("The local AI is unavailable.")
    assert "- Guide Title (`guide`)" in answer


def test_assistant_answers_empty_string_for_an_empty_completion(wiki_root, monkeypatch):
    _patch_ai(monkeypatch, {})

    response = TestClient(server.app).post(
        "/api/assistant", json={"question": "how do I build the docs"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] == ""


# -------------------------------------------------------------- static_site


def _build_dist(wiki_root: Path) -> Path:
    dist = wiki_root / ".vitepress" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
    (dist / "guide.html").write_text("<h1>guide</h1>", encoding="utf-8")
    return dist


def test_static_site_serves_the_extension_fallback(wiki_root):
    dist = _build_dist(wiki_root)

    response = asyncio.run(server.static_site("guide"))

    assert isinstance(response, FileResponse)
    assert Path(response.path) == dist / "guide.html"


def test_static_site_serves_the_directory_index(wiki_root):
    dist = _build_dist(wiki_root)

    response = asyncio.run(server.static_site(""))

    assert isinstance(response, FileResponse)
    assert Path(response.path) == dist / "index.html"


def test_static_site_falls_back_to_the_root_index(wiki_root):
    dist = _build_dist(wiki_root)

    response = asyncio.run(server.static_site("does-not-exist"))

    assert isinstance(response, FileResponse)
    assert Path(response.path) == dist / "index.html"


def test_static_site_never_serves_files_outside_the_dist(wiki_root, tmp_path):
    _build_dist(wiki_root)
    (wiki_root / "secret.txt").write_text("do not serve", encoding="utf-8")

    response = asyncio.run(server.static_site("../secret.txt"))

    assert isinstance(response, FileResponse)
    assert Path(response.path).name == "index.html"


def test_static_site_guards_the_api_prefix(wiki_root):
    _build_dist(wiki_root)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(server.static_site("api/whatever"))

    assert excinfo.value.status_code == 404


def test_static_site_404s_without_build_output(wiki_root):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(server.static_site("guide"))

    assert excinfo.value.status_code == 404
