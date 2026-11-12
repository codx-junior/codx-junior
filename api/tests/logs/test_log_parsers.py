"""
Tests for the generalised application-log parser.

Pure logic only: no FastAPI, no file system, no log writer involved.
"""

import json
from datetime import datetime, timezone
from itertools import islice

import pytest

from codx.junior.logs.models import LEVEL_ORDER, LogEntry, LogLevel, normalize_level
from codx.junior.logs.parsers import (
    HEADER_PATTERN,
    iter_entries,
    parse_json_record,
    parse_lines,
    parse_timestamp_ms,
)


def _ms(year, month, day, hour, minute, second, micro=0):
    """Epoch milliseconds for a UTC-naive log timestamp."""
    return int(datetime(year, month, day, hour, minute, second, micro, tzinfo=timezone.utc).timestamp() * 1000)


def _sample_lines():
    return [
        "[2026-01-02 10:11:12,345] INFO [codx.junior.chat:42] Chat started",
        "[2026-01-02 10:11:13.500] WARNING [codx.junior.db] Slow query",
        'Traceback (most recent call last):',
        '  File "app.py", line 10, in <module>',
        '    raise RuntimeError("boom")',
        "RuntimeError: boom",
        '[2026-01-02 10:11:15] ERROR [codx.junior.api:7] '
        'Request https://example.test/api - 12.3 ms "POST /api/chat HTTP/1.1" 500',
    ]


# ── Header parsing ──────────────────────────────────────────────────────────


def test_header_with_millis():
    entries = parse_lines(
        ["[2026-01-02 10:11:12,345] INFO [codx.junior.chat:42] Chat started"],
        source="api.log",
    )

    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, LogEntry)
    assert entry.id == "api.log#0"
    assert entry.timestamp == "2026-01-02 10:11:12,345"
    assert entry.ts_ms == _ms(2026, 1, 2, 10, 11, 12, 345000)
    assert entry.level == "info"
    assert entry.logger == "codx.junior.chat"
    assert entry.module == "codx.junior.chat"
    assert entry.line == 42
    assert entry.message == "Chat started"
    assert entry.continuation_lines == 0
    assert entry.source == "api.log"
    assert entry.raw == "[2026-01-02 10:11:12,345] INFO [codx.junior.chat:42] Chat started"


def test_header_without_millis_and_without_line_number():
    entries = parse_lines(
        ["[2026-01-02 10:11:12] DEBUG [codx.junior.cache] cache warmed"],
        source="api.log",
    )

    entry = entries[0]
    assert entry.ts_ms == _ms(2026, 1, 2, 10, 11, 12)
    assert entry.level == "debug"
    assert entry.logger == "codx.junior.cache"
    assert entry.line is None
    assert entry.message == "cache warmed"


def test_header_with_empty_logger():
    entries = parse_lines(["[2026-01-02 10:11:12] INFO [:12] anonymous"], source="api.log")

    entry = entries[0]
    assert entry.logger == ""
    assert entry.module is None
    assert entry.line == 12
    assert entry.message == "anonymous"


def test_header_pattern_accepts_dotted_logger_and_iso_like_timestamp():
    match = HEADER_PATTERN.match("[2026-01-02T10:11:12Z] INFO [pkg.sub.mod:12] hello world")

    assert match is not None
    assert match.group("timestamp") == "2026-01-02T10:11:12Z"
    assert match.group("logger") == "pkg.sub.mod"
    assert match.group("line") == "12"
    assert match.group("message") == "hello world"


# ── Continuations ───────────────────────────────────────────────────────────


def test_multiline_traceback_attaches_to_previous_entry():
    entries = parse_lines(_sample_lines(), source="api.log")

    assert [entry.id for entry in entries] == ["api.log#0", "api.log#1", "api.log#6"]
    warning = entries[1]
    assert warning.level == "warn"
    assert warning.continuation_lines == 4
    assert warning.message.splitlines()[0] == "Slow query"
    assert "Traceback (most recent call last):" in warning.message
    assert "RuntimeError: boom" in warning.message
    assert warning.raw.endswith("RuntimeError: boom")
    assert entries[2].message.startswith("Request https://example.test/api")


def test_headerless_lines_become_unknown_entries():
    entries = parse_lines(["plain line one", "plain line two"], source="api.log")

    assert len(entries) == 2
    assert [entry.id for entry in entries] == ["api.log#0", "api.log#1"]
    assert all(entry.level == "unknown" for entry in entries)
    assert all(entry.logger == "" for entry in entries)
    assert all(entry.ts_ms is None for entry in entries)
    assert entries[0].message == "plain line one"


def test_indented_line_continues_a_headerless_entry():
    entries = parse_lines(["plain line", "    indented detail"], source="api.log")

    assert len(entries) == 1
    assert entries[0].level == "unknown"
    assert entries[0].continuation_lines == 1
    assert entries[0].message == "plain line\n    indented detail"


def test_blank_lines_are_skipped_and_do_not_count_as_continuations():
    lines = [
        "[2026-01-02 10:11:12] INFO [a.b:1] first",
        "",
        "   ",
        "  continuation",
    ]

    entries = parse_lines(lines, source="api.log")

    assert len(entries) == 1
    assert entries[0].continuation_lines == 1
    assert entries[0].message == "first\n  continuation"


# ── JSON lines ──────────────────────────────────────────────────────────────


def test_json_line_is_parsed_into_structured_fields():
    payload = {
        "timestamp": "2026-01-02T10:11:12.345Z",
        "level": "ERROR",
        "logger": "uvicorn.error",
        "message": "connection lost",
        "request_id": "req-1",
        "attempt": 2,
    }

    entries = parse_lines([json.dumps(payload)], source="app.jsonl")

    entry = entries[0]
    assert entry.level == "error"
    assert entry.logger == "uvicorn.error"
    assert entry.message == "connection lost"
    assert entry.ts_ms == _ms(2026, 1, 2, 10, 11, 12, 345000)
    assert entry.line is None
    assert entry.fields["request_id"] == "req-1"
    assert entry.fields["attempt"] == 2
    assert "level" not in entry.fields


def test_json_line_accepts_msg_key_and_epoch_millis():
    payload = {"ts": "1767348672000", "level": "warn", "msg": "slow", "module": "api.routes"}
    entry = parse_lines([json.dumps(payload)], source="app.jsonl")[0]

    assert entry.message == "slow"
    assert entry.level == "warn"
    assert entry.logger == "api.routes"
    assert entry.ts_ms == 1767348672000


def test_json_without_log_keys_is_not_a_json_entry():
    line = json.dumps({"foo": 1})

    assert parse_json_record(line) is None
    entry = parse_lines([line], source="api.log")[0]
    assert entry.level == "unknown"
    assert entry.message == line


# ── Extractors shared with codx.junior.log_parser ───────────────────────────


def test_request_extraction():
    line = (
        "[2026-01-02 10:11:15] INFO [codx.junior.api:9] "
        'Request https://example.test/api - 12.3 ms "POST /api/chat HTTP/1.1" 500'
    )

    entry = parse_lines([line], source="api.log")[0]

    assert entry.fields["request"] == {
        "url": "https://example.test/api",
        "status_code": "500",
        "time_taken": 12.3,
        "method": "POST",
    }


def test_profiler_extraction():
    line = '[2026-01-02 10:11:15] INFO [codx.junior.profiler:3] Profiler: {"total": 1.5, "calls": 2}'

    entry = parse_lines([line], source="api.log")[0]

    assert entry.fields["profiler"] == {"total": 1.5, "calls": 2}


def test_invalid_profiler_json_is_ignored():
    line = '[2026-01-02 10:11:15] INFO [codx.junior.profiler:3] Profiler: {"total": '

    entry = parse_lines([line], source="api.log")[0]

    assert "profiler" not in entry.fields


def test_extractors_see_continuation_lines():
    lines = [
        "[2026-01-02 10:11:15] INFO [codx.junior.api:9] Request https://example.test/api - 5.0 ms",
        '  "POST /api/chat HTTP/1.1" 201',
    ]

    entry = parse_lines(lines, source="api.log")[0]

    assert entry.continuation_lines == 1
    assert entry.fields["request"]["status_code"] == "201"


# ── Levels ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CRITICAL", "critical"),
        ("fatal", "critical"),
        ("ERROR", "error"),
        ("Exception", "error"),
        ("WARNING", "warn"),
        ("warn", "warn"),
        ("INFO", "info"),
        ("DEBUG", "debug"),
        ("TRACE", "trace"),
        ("verbose", "trace"),
        ("", "unknown"),
        (None, "unknown"),
        ("something-else", "unknown"),
    ],
)
def test_normalize_level(raw, expected):
    assert normalize_level(raw) == expected


def test_level_order_and_rank():
    assert LEVEL_ORDER[0] == "critical"
    assert LEVEL_ORDER[-1] == "unknown"
    assert LogLevel.ORDER == LEVEL_ORDER
    assert LogLevel.normalize("FATAL") == "critical"
    assert LogLevel.rank("fatal") == 0
    assert LogLevel.rank("info") < LogLevel.rank("debug") < LogLevel.rank("unknown")


def test_parser_normalises_header_levels():
    entries = parse_lines(
        [
            "[2026-01-02 10:11:12] FATAL [a.b:1] boom",
            "[2026-01-02 10:11:13] EXCEPTION [a.b:2] kaboom",
            "[2026-01-02 10:11:14] WARN [a.b:3] careful",
        ],
        source="api.log",
    )

    assert [entry.level for entry in entries] == ["critical", "error", "warn"]


# ── Ids, timestamps, streaming ──────────────────────────────────────────────


def test_ids_are_unique_and_stable():
    lines = _sample_lines()

    first = parse_lines(lines, source="api.log")
    second = parse_lines(lines, source="api.log")

    assert [entry.id for entry in first] == [entry.id for entry in second]
    assert len({entry.id for entry in first}) == len(first)
    assert all(entry.id.startswith("api.log#") for entry in first)


def test_start_index_shifts_ids():
    entries = parse_lines(
        ["[2026-01-02 10:11:12] INFO [a:1] one", "[2026-01-02 10:11:13] INFO [a:2] two"],
        source="api.log",
        start_index=10,
    )

    assert [entry.id for entry in entries] == ["api.log#10", "api.log#11"]


def test_iter_entries_is_lazy():
    consumed = []

    def generator():
        for index in range(500):
            consumed.append(index)
            yield f"[2026-01-02 10:11:{index % 60:02d},000] INFO [a.b:{index}] line {index}"

    first_five = list(islice(iter_entries(generator(), source="big.log"), 5))

    assert len(first_five) == 5
    assert first_five[0].id == "big.log#0"
    assert first_five[-1].message == "line 4"
    # Only the lines needed to close the fifth entry were read.
    assert len(consumed) <= 6


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        ("2026-01-02 10:11:12,345", _ms(2026, 1, 2, 10, 11, 12, 345000)),
        ("2026-01-02 10:11:12.345", _ms(2026, 1, 2, 10, 11, 12, 345000)),
        ("2026-01-02 10:11:12", _ms(2026, 1, 2, 10, 11, 12)),
        ("2026-01-02T10:11:12Z", _ms(2026, 1, 2, 10, 11, 12)),
        ("1767348672000", 1767348672000),
        ("1767348672", 1767348672000),
    ],
)
def test_parse_timestamp_ms(timestamp, expected):
    assert parse_timestamp_ms(timestamp) == expected


@pytest.mark.parametrize("timestamp", ["", None, "not-a-timestamp", "12345"])
def test_parse_timestamp_ms_returns_none_for_garbage(timestamp):
    assert parse_timestamp_ms(timestamp) is None


def test_unparsable_header_timestamp_keeps_raw_value():
    entry = parse_lines(["[n/a] INFO [a.b:1] message"], source="api.log")[0]

    assert entry.timestamp == "n/a"
    assert entry.ts_ms is None


def test_empty_input_returns_no_entries():
    assert parse_lines([], source="api.log") == []
