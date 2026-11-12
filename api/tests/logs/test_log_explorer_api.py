"""
API tests for the application-log explorer (``/api/logs/explorer/*``).

The router is mounted on a minimal FastAPI app (the full ``codx.junior.app``
pulls in the whole engine) and pointed at a temporary log folder through
``CODX_JUNIOR_API_LOGS``, which is exactly the variable the real deployment
uses. Auth is stubbed with ``dependency_overrides`` so the tests stay offline.
"""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codx.junior.model.model import CodxUser
from codx.junior.security.user_management import get_authenticated_user

API_PREFIX = "/api"
BASE_URL = f"{API_PREFIX}/logs/explorer"

#: Router source file, loaded directly: importing ``codx.junior.api`` pulls in the
#: whole engine (heavy and network dependent), while these tests only need the
#: router plus the pure ``codx.junior.logs`` modules.
_ROUTER_PATH = Path(__file__).resolve().parents[2] / "codx" / "junior" / "api" / "log_explorer.py"


def _load_router_module():
    """Load ``codx/junior/api/log_explorer.py`` without importing its package."""
    spec = importlib.util.spec_from_file_location("log_explorer_under_test", _ROUTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


log_explorer = _load_router_module()


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _ms(year, month, day, hour, minute, second):
    """Epoch milliseconds for a UTC log timestamp."""
    return int(datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp() * 1000)


def _line(timestamp, level, logger, line_number, message):
    """Render one application-log line in the format the parser understands."""
    return f"[{timestamp}] {level} [{logger}:{line_number}] {message}"


def _api_log_lines():
    """Five entries: two errors (one with a traceback), one warning, two infos."""
    return [
        _line("2026-01-02 10:11:12,000", "INFO", "codx.junior.chat", 10, "Chat started for session 1"),
        _line("2026-01-02 10:11:13,000", "WARN", "codx.junior.db", 42, "Slow query took 1200ms"),
        "  Traceback (most recent call last):",
        '    raise RuntimeError("db is slow")',
        "RuntimeError: db is slow",
        _line("2026-01-02 10:11:14,000", "ERROR", "codx.junior.api", 7, "Request failed with timeout"),
        _line("2026-01-02 10:11:15,000", "ERROR", "codx.junior.chat", 88, "Chat turn crashed: timeout"),
        _line("2026-01-02 10:11:16,000", "INFO", "codx.junior.worker", 3, "Worker heartbeat"),
    ]


@pytest.fixture()
def log_folder(tmp_path, monkeypatch):
    """Create a temporary log folder and point the explorer at it."""
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "api.log").write_text("\n".join(_api_log_lines()) + "\n", encoding="utf-8")
    (folder / "worker.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-01-02T10:11:20Z",
                        "level": "INFO",
                        "logger": "worker.tasks",
                        "message": "task picked",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-02T10:11:21Z",
                        "level": "ERROR",
                        "logger": "worker.tasks",
                        "message": "task failed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODX_JUNIOR_API_LOGS", str(folder))
    return folder


@pytest.fixture()
def client(log_folder):
    """TestClient over a minimal app that only mounts the explorer router."""
    app = FastAPI()
    app.include_router(log_explorer.router, prefix=API_PREFIX)
    app.dependency_overrides[get_authenticated_user] = lambda: CodxUser(
        username="tester", role="admin"
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _query(client, **params):
    """GET /query with ``source=api.log`` unless the caller says otherwise."""
    params.setdefault("source", "api.log")
    response = client.get(f"{BASE_URL}/query", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ── Contract ──────────────────────────────────────────────────────────────────


def test_router_exposes_the_frozen_paths():
    paths = {route.path for route in log_explorer.router.routes}

    assert paths == {
        "/logs/explorer/sources",
        "/logs/explorer/query",
        "/logs/explorer/insights",
        "/logs/explorer/entry/{id}",
        "/logs/explorer/context/{id}",
        "/logs/explorer/tail",
    }


def test_query_response_matches_the_contract(client):
    payload = _query(client)

    assert set(payload) == {
        "entries", "total", "page", "page_size", "has_more",
        "histogram", "facets", "stats",
    }
    assert set(payload["entries"][0]) == {
        "id", "timestamp", "ts_ms", "level", "logger", "module", "line",
        "message", "fields", "continuation_lines", "source", "raw_preview",
    }
    assert set(payload["stats"]) == {
        "scanned_lines", "scanned_bytes", "matched", "duration_ms", "truncated",
    }
    assert set(payload["facets"]) == {"level", "logger", "module"}
    assert set(payload["histogram"][0]) == {"ts_ms", "total", "by_level"}


# ── Sources ───────────────────────────────────────────────────────────────────


def test_sources_lists_the_log_files(client, log_folder):
    response = client.get(f"{BASE_URL}/sources")

    assert response.status_code == 200, response.text
    sources = {source["id"]: source for source in response.json()["sources"]}
    assert set(sources) == {"api.log", "worker.jsonl"}

    api_log = sources["api.log"]
    assert api_log["path"] == str(log_folder / "api.log")
    assert api_log["size_bytes"] == (log_folder / "api.log").stat().st_size
    assert api_log["kind"] == "log"
    assert sources["worker.jsonl"]["kind"] == "jsonl"
    assert api_log["entries_estimate"] >= 5


def test_anonymous_request_does_not_500(log_folder):
    """
    Regression: ``get_authenticated_user`` RETURNS None (it does not raise)
    when the ``authentication`` header is missing, and every route dereferenced
    ``user.username`` -> 500 on the whole explorer, which the UI showed as
    "No log sources". No dependency override here: the real dependency runs and
    yields None.
    """
    app = FastAPI()
    app.include_router(log_explorer.router, prefix=API_PREFIX)
    with TestClient(app) as anonymous:
        sources = anonymous.get(f"{BASE_URL}/sources")
        assert sources.status_code == 200, sources.text
        assert sources.json()["sources"]

        query = anonymous.get(f"{BASE_URL}/query", params={"source": "api.log"})
        assert query.status_code == 200, query.text

        insights = anonymous.get(f"{BASE_URL}/insights", params={"source": "api.log"})
        assert insights.status_code == 200, insights.text


def test_query_with_unknown_source_returns_404(client):
    response = client.get(f"{BASE_URL}/query", params={"source": "missing.log"})

    assert response.status_code == 404
    assert "missing.log" in response.json()["detail"]


# ── Query: filtering ──────────────────────────────────────────────────────────


def test_query_free_text_matches_message_and_raw_text(client):
    payload = _query(client, q="timeout")

    assert payload["total"] == 2
    assert [entry["level"] for entry in payload["entries"]] == ["error", "error"]
    assert all("timeout" in entry["raw_preview"] for entry in payload["entries"])


def test_query_exact_phrase_and_negation(client):
    assert _query(client, q='"slow query"')["total"] == 1
    # "heartbeat" minus the worker logger leaves nothing.
    assert _query(client, q="heartbeat -logger=codx.junior.worker")["total"] == 0
    assert _query(client, q="heartbeat -level=error")["total"] == 1


def test_query_level_filter_accepts_several_levels(client):
    errors = _query(client, level="error")
    assert errors["total"] == 2
    assert {entry["level"] for entry in errors["entries"]} == {"error"}

    response = client.get(
        f"{BASE_URL}/query",
        params=[("source", "api.log"), ("level", "error"), ("level", "info")],
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 4


def test_query_time_range_filters_entries(client):
    payload = _query(
        client,
        start=_ms(2026, 1, 2, 10, 11, 14),
        end=_ms(2026, 1, 2, 10, 11, 15),
    )

    assert payload["total"] == 2
    assert {entry["level"] for entry in payload["entries"]} == {"error"}
    assert payload["histogram"]


def test_query_keeps_continuation_lines(client):
    entry = next(
        item for item in _query(client, q="db is slow")["entries"] if item["level"] == "warn"
    )

    assert entry["continuation_lines"] == 3
    assert "RuntimeError: db is slow" in entry["message"]
    assert "RuntimeError" not in entry["raw_preview"]


# ── Query: ordering, pagination, aggregations ─────────────────────────────────


def test_query_paginates_and_reports_has_more(client):
    first = _query(client, page=1, page_size=2)
    assert first["total"] == 5
    assert first["page"] == 1
    assert first["page_size"] == 2
    assert len(first["entries"]) == 2
    assert first["has_more"] is True

    last = _query(client, page=3, page_size=2)
    assert len(last["entries"]) == 1
    assert last["has_more"] is False
    assert not {entry["id"] for entry in first["entries"]} & {
        entry["id"] for entry in last["entries"]
    }


def test_query_merges_several_sources(client):
    response = client.get(
        f"{BASE_URL}/query",
        params=[("source", "api.log"), ("source", "worker.jsonl")],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 7
    assert {entry["source"] for entry in payload["entries"]} == {"api.log", "worker.jsonl"}
    timestamps = [entry["ts_ms"] for entry in payload["entries"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_query_rejects_a_page_size_above_the_cap(client):
    response = client.get(f"{BASE_URL}/query", params={"source": "api.log", "page_size": 5000})

    assert response.status_code == 422


def test_query_order_desc_and_asc(client):
    desc = [entry["ts_ms"] for entry in _query(client, order="desc")["entries"]]
    asc = [entry["ts_ms"] for entry in _query(client, order="asc")["entries"]]

    assert desc == sorted(desc, reverse=True)
    assert asc == sorted(asc)
    assert desc == list(reversed(asc))


def test_query_histogram_bucket_count(client):
    payload = _query(client, histogram_buckets=5)

    assert len(payload["histogram"]) == 5
    assert sum(bucket["total"] for bucket in payload["histogram"]) == payload["total"]
    by_level = {}
    for bucket in payload["histogram"]:
        for level, count in bucket["by_level"].items():
            by_level[level] = by_level.get(level, 0) + count
    assert by_level == {"info": 2, "warn": 1, "error": 2}


def test_query_facets_count_matched_entries(client):
    facets = _query(client)["facets"]

    assert {"value": "info", "count": 2} in facets["level"]
    assert {"value": "error", "count": 2} in facets["level"]
    assert {"value": "codx.junior.chat", "count": 2} in facets["logger"]
    assert {"value": "codx.junior.api", "count": 1} in facets["logger"]
    assert facets["module"]


def test_query_stats_report_the_scan(client):
    stats = _query(client)["stats"]

    assert stats["matched"] == 5
    assert stats["scanned_lines"] >= 5
    assert stats["scanned_bytes"] > 0
    assert stats["duration_ms"] >= 0
    assert stats["truncated"] is False


def test_query_marks_truncated_when_only_the_tail_is_scanned(client, log_folder, monkeypatch):
    big_log = log_folder / "big.log"
    big_log.write_text(
        "\n".join(
            _line(f"2026-01-02 10:{(index // 60) % 60:02d}:{index % 60:02d},000",
                  "ERROR", "codx.junior.big", index, f"line {index}")
            for index in range(3000)
        )
        + "\n",
        encoding="utf-8",
    )
    assert big_log.stat().st_size > 100_000
    monkeypatch.setenv("CODX_JUNIOR_LOG_EXPLORER_SCAN_BYTES", "4096")

    payload = _query(client, source="big.log", page_size=5)

    assert payload["stats"]["truncated"] is True
    assert payload["stats"]["scanned_bytes"] <= 4096
    assert payload["stats"]["scanned_lines"] > 0
    assert payload["total"] >= 1
    assert len(payload["entries"]) <= 5


# ── Entry & context ───────────────────────────────────────────────────────────


def test_entry_by_id_returns_raw_text(client):
    entry_id = _query(client, q="timeout", order="asc")["entries"][0]["id"]

    response = client.get(f"{BASE_URL}/entry/{quote(entry_id, safe='')}")

    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["id"] == entry_id
    assert entry["level"] == "error"
    assert entry["source"] == "api.log"
    assert "Request failed with timeout" in entry["raw"]
    assert entry["fields"] == {}
    assert entry["module"] == "codx.junior.api"


def test_entry_by_id_accepts_an_explicit_source(client):
    entry = _query(client, q="heartbeat")["entries"][0]

    response = client.get(
        f"{BASE_URL}/entry/{quote(entry['id'], safe='')}", params={"source": "api.log"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == entry["id"]


def test_entry_by_id_returns_404_when_missing(client):
    response = client.get(f"{BASE_URL}/entry/{quote('api.log#9999', safe='')}")

    assert response.status_code == 404


def test_context_returns_the_surrounding_window(client):
    entries = _query(client, order="asc")["entries"]
    anchor = entries[1]

    response = client.get(
        f"{BASE_URL}/context/{quote(anchor['id'], safe='')}",
        params={"before": 1, "after": 1},
    )

    assert response.status_code == 200, response.text
    context = response.json()["entries"]
    assert [entry["id"] for entry in context] == [entry["id"] for entry in entries[0:3]]
    assert context[1]["id"] == anchor["id"]


# ── Tail ──────────────────────────────────────────────────────────────────────


def test_tail_returns_a_cursor_and_only_new_entries(client, log_folder):
    first = client.get(f"{BASE_URL}/tail", params={"source": "api.log"})
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["cursor"].isdigit()
    assert len(first_payload["entries"]) == 5

    with open(log_folder / "api.log", "a", encoding="utf-8") as log_file:
        log_file.write(
            _line("2026-01-02 10:11:30,000", "CRITICAL", "codx.junior.api", 99, "Everything is on fire")
            + "\n"
        )

    second = client.get(
        f"{BASE_URL}/tail",
        params={"source": "api.log", "since_cursor": first_payload["cursor"]},
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()

    assert int(second_payload["cursor"]) > int(first_payload["cursor"])
    assert len(second_payload["entries"]) == 1
    assert second_payload["entries"][0]["level"] == "critical"
    assert "on fire" in second_payload["entries"][0]["message"]

    # Polling again without new writes returns nothing.
    third = client.get(
        f"{BASE_URL}/tail",
        params={"source": "api.log", "since_cursor": second_payload["cursor"]},
    )
    assert third.json()["entries"] == []


def test_tail_defaults_to_the_newest_source(client):
    response = client.get(f"{BASE_URL}/tail")

    assert response.status_code == 200, response.text
    assert response.json()["cursor"].isdigit()


def test_tail_with_unknown_source_returns_404(client):
    response = client.get(f"{BASE_URL}/tail", params={"source": "missing.log"})

    assert response.status_code == 404
