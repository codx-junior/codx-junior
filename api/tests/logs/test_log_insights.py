"""
API insights aggregation (``/api/logs/explorer/insights``).

The aggregation is derived from the parsed ``fields.request`` /
``fields.profiler`` of the matched entries, so these tests build a small
synthetic log file and assert the numbers the UI shows.
"""

import json

import pytest

from codx.junior.logs import insights


def _line(timestamp, level, logger, number, message):
    """Render one application-log line."""
    return f"[{timestamp}] {level} [{logger}:{number}] {message}"


def _request(timestamp, level, url, duration, method, path, status, number=1):
    """Render one API request line the parser understands."""
    return _line(
        timestamp,
        level,
        "codx.junior.app",
        number,
        f'Request {url} - {duration} ms "{method} {path} HTTP/1.1" {status}',
    )


REQUEST_LINES = [
    _request("2026-01-02 10:00:00,000", "INFO", "http://host/api/chat", "10.0", "GET", "/api/chat", 200, 10),
    _request("2026-01-02 10:00:10,000", "INFO", "http://host/api/chat", "30.0", "GET", "/api/chat", 200, 11),
    _request("2026-01-02 10:00:20,000", "INFO", "http://host/api/chat", "20.0", "GET", "/api/chat", 200, 12),
    _request("2026-01-02 10:00:30,000", "ERROR", "http://host/api/chat?x=1", "500.0", "POST", "/api/chat", 500, 13),
    _request("2026-01-02 10:00:40,000", "INFO", "http://host/api/logs/explorer", "40.0", "GET", "/api/logs/explorer", 200, 14),
    _line(
        "2026-01-02 10:00:50,000",
        "DEBUG",
        "codx.junior.profiler",
        99,
        "Profiler: " + json.dumps({"module": "chat_manager", "method": "load", "time_taken": 3.5}),
    ),
]


@pytest.fixture()
def log_folder(tmp_path, monkeypatch):
    """Write the synthetic log and point the explorer at it."""
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "api.log").write_text("\n".join(REQUEST_LINES) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODX_JUNIOR_API_LOGS", str(folder))
    return folder


def test_summary_counts_requests_errors_and_latency(log_folder):
    payload = insights.build_insights(folder=str(log_folder))

    summary = payload["summary"]
    assert summary["requests"] == 5
    assert summary["errors"] == 1
    assert summary["error_rate"] == round(1 / 5, 4)
    assert summary["avg_ms"] == 120.0
    assert summary["p50_ms"] == 30.0
    assert summary["max_ms"] == 500.0
    assert summary["p95_ms"] == 500.0


def test_endpoints_are_grouped_and_ranked(log_folder):
    payload = insights.build_insights(folder=str(log_folder))

    endpoints = {item["endpoint"]: item for item in payload["endpoints"]}
    assert set(endpoints) == {"GET /api/chat", "POST /api/chat", "GET /api/logs/explorer"}
    assert endpoints["GET /api/chat"]["count"] == 3
    assert endpoints["GET /api/chat"]["errors"] == 0
    assert endpoints["GET /api/chat"]["avg_ms"] == 20.0
    assert endpoints["POST /api/chat"]["errors"] == 1
    assert payload["endpoints"][0]["endpoint"] == "GET /api/chat"


def test_slowest_requests_expose_openable_ids(log_folder):
    payload = insights.build_insights(folder=str(log_folder), top=2)

    assert [item["duration_ms"] for item in payload["slowest"]] == [500.0, 40.0]
    assert payload["slowest"][0]["status"] == 500
    assert payload["slowest"][0]["id"].endswith("#3")


def test_status_distribution_and_profiler(log_folder):
    payload = insights.build_insights(folder=str(log_folder))

    status = {item["value"]: item["count"] for item in payload["status"]}
    assert status == {"200": 4, "500": 1}

    assert payload["profiler"][0]["method"] == "chat_manager.load"
    assert payload["profiler"][0]["count"] == 1
    assert payload["profiler"][0]["max_ms"] == 3.5


def test_histogram_and_stats_are_present(log_folder):
    payload = insights.build_insights(folder=str(log_folder), histogram_buckets=4)

    assert len(payload["histogram"]) <= 4
    assert set(payload["stats"]) == {
        "scanned_lines", "scanned_bytes", "matched", "duration_ms", "truncated",
    }
    assert payload["stats"]["matched"] >= 6


def test_min_ms_keeps_only_slow_requests(log_folder):
    payload = insights.build_insights(folder=str(log_folder), min_ms=100)

    assert payload["summary"]["requests"] == 1
    assert payload["summary"]["max_ms"] == 500.0


def test_query_min_ms_filter(log_folder):
    from codx.junior.logs import service

    payload = service.query_logs(folder=str(log_folder), min_ms=100)

    assert payload["total"] == 1
    assert payload["entries"][0]["fields"]["request"]["time_taken"] == 500.0


def test_empty_window_returns_zeroed_payload(tmp_path, monkeypatch):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "api.log").write_text("[2026-01-02 10:00:00,000] INFO [m:1] hello\n", encoding="utf-8")
    monkeypatch.setenv("CODX_JUNIOR_API_LOGS", str(folder))

    payload = insights.build_insights(folder=str(folder))

    assert payload["summary"] == {
        "requests": 0,
        "errors": 0,
        "error_rate": 0.0,
        "avg_ms": 0.0,
        "p50_ms": 0.0,
        "p95_ms": 0.0,
        "p99_ms": 0.0,
        "max_ms": 0.0,
    }
    assert payload["endpoints"] == []
    assert payload["slowest"] == []
    assert payload["profiler"] == []


def test_level_filter_is_applied(log_folder):
    payload = insights.build_insights(folder=str(log_folder), levels=["error"])

    assert payload["summary"]["requests"] == 1
    assert payload["summary"]["errors"] == 1
