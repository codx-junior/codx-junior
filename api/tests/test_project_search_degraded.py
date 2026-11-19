"""GET /project/search must degrade, never 500, when embeddings are unavailable.

The endpoint delegates to ``CODXJuniorSession.project_search`` which needs the
local embeddings provider. A stub session is enough to exercise the handler: it
raises the exact error the provider raises when ``sentence-transformers`` is not
installed.
"""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.api.project_search import DEGRADED_RESULT_LIMIT, router

EMBEDDINGS_ERROR = "sentence-transformers is required for local embeddings"


class _UnavailableEmbeddingsSession:
    """Session stub whose embeddings provider cannot be loaded."""

    def __init__(self, files=None, search_error=None):
        self.files = files if files is not None else []
        self.search_error = search_error
        self.project_search_calls = []
        self.search_files_calls = []

    def project_search(self, query: str):
        self.project_search_calls.append(query)
        raise RuntimeError(EMBEDDINGS_ERROR)

    def search_files(self, search: str):
        self.search_files_calls.append(search)
        if self.search_error:
            raise self.search_error
        return list(self.files)


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def attach_session(request, call_next):
        request.state.codx_junior_session = session
        return await call_next(request)

    return TestClient(app)


def test_degrades_to_lexical_items_instead_of_500():
    session = _UnavailableEmbeddingsSession(
        files=["codx/junior/ai/smol/effort.py", "docs/effort-policy.md"]
    )

    response = _client(session).get("/project/search", params={"query": "effort"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert EMBEDDINGS_ERROR in body["reason"]
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert item["id"]
        assert item["name"]
        assert item["resource_type"] == "file"
    assert body["items"][0]["name"] == "effort.py"
    # the lexical path was taken, not a silent empty answer
    assert session.search_files_calls == ["effort"]


def test_degraded_items_are_bounded():
    session = _UnavailableEmbeddingsSession(
        files=[f"docs/file_{i}.md" for i in range(DEGRADED_RESULT_LIMIT + 70)]
    )

    response = _client(session).get("/project/search", params={"query": "file"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert len(body["items"]) == DEGRADED_RESULT_LIMIT


def test_degraded_response_is_logged_as_warning(caplog):
    session = _UnavailableEmbeddingsSession(files=["README.md"])

    with caplog.at_level(logging.WARNING, logger="codx.junior.api.project_search"):
        response = _client(session).get("/project/search", params={"query": "readme"})

    assert response.status_code == 200
    assert any(
        "degraded to lexical scan" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_lexical_fallback_failure_still_returns_200():
    session = _UnavailableEmbeddingsSession(search_error=OSError("file engine offline"))

    response = _client(session).get("/project/search", params={"query": "effort"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["items"] == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_query_is_rejected_without_calling_embeddings(blank):
    session = _UnavailableEmbeddingsSession(files=["README.md"])

    response = _client(session).get("/project/search", params={"query": blank})

    assert response.status_code == 422
    assert session.project_search_calls == []
    assert session.search_files_calls == []
