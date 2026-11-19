"""Behaviour tests for the standalone wiki site server (``server.py``).

Scope: the pure helpers (tokeniser, page resolution, page loading, JSON reading)
and the read-only HTTP surface (``/api/health``, ``/api/site``, ``/api/page``).

Constraints honoured here: no network, no LLM call, no clock, no shared mutable
state. Every test that touches the filesystem builds its own tree in ``tmp_path``
and rebinds the module globals with ``monkeypatch``, because ``server`` resolves
``WIKI_ROOT`` / ``WIKI_DIST`` / ``MANIFEST`` at call time.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    """Point ``server`` at an isolated wiki folder with two pages."""
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
    (root / "docs.json").write_text(
        json.dumps(
            {
                "version": 2,
                "project": {"title": "Demo Project", "description": "Demo"},
                "pages": {"guide": {"path": "guide", "title": "Guide Title"}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "WIKI_ROOT", root)
    monkeypatch.setattr(server, "MANIFEST", root / "docs.json")
    monkeypatch.setattr(server, "WIKI_DIST", root / ".vitepress" / "dist")
    return root


def test_tokens_lowercases_and_drops_stopwords():
    tokens = server._tokens("How do I configure the Docker build?")

    assert tokens == ["configure", "docker", "build"]
    assert "the" not in tokens
    assert server._tokens("") == []


def test_tokens_keeps_path_like_identifiers():
    tokens = server._tokens("api/codx/junior/efforts")

    assert tokens == ["api/codx/junior/efforts"]
    assert "/" in tokens[0]


def test_page_path_appends_markdown_suffix(wiki_root):
    resolved = server._page_path("guide")

    assert resolved is not None
    assert resolved == (wiki_root / "guide.md").resolve()
    assert resolved.is_file()


def test_page_path_normalises_leading_slash(wiki_root):
    assert server._page_path("/guide") == server._page_path("guide")


def test_page_path_rejects_parent_traversal(wiki_root):
    (wiki_root.parent / "secret.md").write_text("# Secret\n", encoding="utf-8")

    assert server._page_path("../secret") is None
    assert server._page_path("guide/../../secret") is None


def test_page_path_returns_none_for_missing_or_empty_page(wiki_root):
    assert server._page_path("does-not-exist") is None
    assert server._page_path("") is None


def test_read_json_returns_content_and_falls_back_to_default(wiki_root):
    assert server._read_json(wiki_root / "docs.json", {})["version"] == 2
    assert server._read_json(wiki_root / "missing.json", {"fallback": True}) == {"fallback": True}


def test_page_parses_frontmatter_and_strips_it_from_content(wiki_root):
    page = server._page("guide")

    assert page is not None
    assert page["path"] == "guide"
    assert page["title"] == "Guide Title"
    assert page["description"] == "How to build the docs"
    assert page["content"].lstrip().startswith("# Heading One")
    assert "title: Guide Title" not in page["content"]


def test_page_falls_back_to_first_heading_for_plain_markdown(wiki_root):
    page = server._page("plain")

    assert page is not None
    assert page["title"] == "Plain Title"
    assert page["description"] == ""


def test_site_drops_pages_but_keeps_project_metadata(wiki_root):
    data = server._site()

    assert data["project"]["title"] == "Demo Project"
    assert "pages" not in data


def test_health_endpoint_reports_wiki_root(wiki_root):
    response = TestClient(server.app).get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["wiki"] == str(wiki_root)
    assert payload["dist"] is False
    assert payload["model"] == server.AI_MODEL


def test_site_endpoint_returns_manifest_without_pages(wiki_root):
    response = TestClient(server.app).get("/api/site")

    assert response.status_code == 200
    assert response.json()["project"]["title"] == "Demo Project"
    assert "pages" not in response.json()


def test_page_endpoint_returns_parsed_page(wiki_root):
    response = TestClient(server.app).get("/api/page", params={"path": "guide"})

    assert response.status_code == 200
    assert response.json()["title"] == "Guide Title"


def test_page_endpoint_returns_404_for_unknown_page(wiki_root):
    response = TestClient(server.app).get("/api/page", params={"path": "missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Page not found"


def test_static_site_route_does_not_shadow_api_prefix(wiki_root):
    response = TestClient(server.app).get("/api/unknown")

    assert response.status_code == 404


def test_static_site_route_falls_back_to_404_without_build_output(wiki_root):
    response = TestClient(server.app).get("/guide")

    assert response.status_code == 404
