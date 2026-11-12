"""
Tests for the Loki/Graylog-style query language (``codx.junior.logs.query``).
"""

import pytest

from codx.junior.logs.models import LogEntry, normalize_level
from codx.junior.logs.query import compile_query, tokenize


def _entry(
    message="",
    level="info",
    logger="codx.junior.chat",
    module="codx.junior.chat",
    line=10,
    fields=None,
    source="api.log",
    raw=None,
    timestamp="2026-01-02 10:11:12",
):
    """Build a LogEntry for query tests."""
    return LogEntry(
        id=f"{source}#1",
        timestamp=timestamp,
        ts_ms=1767348672000,
        level=normalize_level(level),
        logger=logger,
        module=module,
        line=line,
        message=message,
        fields=dict(fields or {}),
        continuation_lines=0,
        source=source,
        raw=message if raw is None else raw,
    )


# ── Empty queries ───────────────────────────────────────────────────────────


def test_empty_query_matches_everything():
    for text in ("", "   ", None):
        query = compile_query(text)
        assert query.is_empty
        assert query.text == (text or "")
        assert query.matches(_entry("anything at all"))


def test_query_truthiness_follows_is_empty():
    assert not compile_query("")
    assert compile_query("error")


# ── Free text and phrases ───────────────────────────────────────────────────


def test_free_text_is_a_case_insensitive_substring():
    query = compile_query("CHAT")

    assert not query.is_empty
    assert query.terms[0].kind == "text"
    assert query.matches(_entry("Chat started"))
    assert not query.matches(_entry("database started"))


def test_quoted_phrase_is_a_single_term():
    query = compile_query('"chat started"')

    assert len(query.terms) == 1
    assert query.terms[0].kind == "phrase"
    assert query.matches(_entry("Chat started ok"))
    assert not query.matches(_entry("chat stopped"))


def test_tokenizer_keeps_quoted_terms_whole():
    assert tokenize('a "b c" -d') == ["a", '"b c"', "-d"]
    assert tokenize('  level=error   ') == ["level=error"]


def test_free_text_searches_the_raw_text():
    entry = _entry(message="body", raw="[ts] ERROR [a:1] body")

    assert compile_query("ERROR").matches(entry)
    assert not compile_query("body-only").matches(entry)


# ── Level terms ─────────────────────────────────────────────────────────────


def test_level_term():
    query = compile_query("level=error")

    assert query.terms[0].kind == "level"
    assert query.terms[0].field == "level"
    assert query.terms[0].values == ["error"]
    assert query.matches(_entry("boom", level="ERROR"))
    assert not query.matches(_entry("fine", level="info"))


def test_level_term_with_comma_list():
    query = compile_query("level=error,warn")

    assert query.terms[0].values == ["error", "warn"]
    assert query.matches(_entry(level="warn"))
    assert query.matches(_entry(level="error"))
    assert not query.matches(_entry(level="debug"))


def test_level_term_uses_level_normalisation():
    query = compile_query("level=fatal")

    assert query.terms[0].values == ["critical"]
    assert query.matches(_entry(level="critical"))


# ── Regex terms ─────────────────────────────────────────────────────────────


def test_logger_regex():
    query = compile_query('logger~"chat_.*"')

    assert query.terms[0].kind == "regex"
    assert query.terms[0].field == "logger"
    assert query.matches(_entry(logger="chat_engine"))
    assert not query.matches(_entry(logger="codx.junior.chat"))


def test_regex_is_case_insensitive():
    assert compile_query('logger~"CHAT_.*"').matches(_entry(logger="chat_engine"))


def test_field_regex():
    query = compile_query('message~"fail.*timeout"')

    assert query.matches(_entry("request fail with timeout after 30s"))
    assert not query.matches(_entry("all good"))


def test_invalid_regex_falls_back_to_substring():
    query = compile_query('logger~"[unclosed"')
    term = query.terms[0]

    assert term.kind == "regex"
    assert term.error
    assert query.matches(_entry(logger="weird [unclosed logger"))
    assert not query.matches(_entry(logger="codx.junior.chat"))


def test_invalid_regex_without_a_field_searches_the_raw_text():
    query = compile_query('message~"*bad"')

    assert query.terms[0].error
    assert query.matches(_entry("prefix *bad suffix"))
    assert not query.matches(_entry("prefix good suffix"))


# ── Field terms ─────────────────────────────────────────────────────────────


def test_field_exact_matches_a_custom_field():
    query = compile_query("request_id=abc-1")

    assert query.terms[0].kind == "exact"
    assert query.terms[0].field == "request_id"
    assert query.matches(_entry(fields={"request_id": "abc-1"}))
    assert not query.matches(_entry(fields={"request_id": "abc-2"}))


def test_field_exact_matches_top_level_attributes():
    entry = _entry("Chat started", module="codx.junior.chat", line=10, source="api.log")

    assert compile_query("module=codx.junior.chat").matches(entry)
    assert compile_query("line=10").matches(entry)
    assert compile_query("source=api.log").matches(entry)
    assert compile_query('message="Chat started"').matches(entry)
    assert not compile_query("line=11").matches(entry)


def test_field_exact_matches_a_list_value():
    query = compile_query("tag=sql")

    assert query.matches(_entry(fields={"tag": ["sql", "slow"]}))
    assert not query.matches(_entry(fields={"tag": ["http"]}))


def test_missing_field_does_not_match():
    assert not compile_query("request_id=abc").matches(_entry())


# ── Negation ────────────────────────────────────────────────────────────────


def test_negated_text_term():
    query = compile_query("-timeout")

    assert query.terms[0].negated is True
    assert query.matches(_entry("all good"))
    assert not query.matches(_entry("request timeout"))


def test_negated_level_term():
    query = compile_query("-level=debug")

    assert query.matches(_entry(level="info"))
    assert not query.matches(_entry(level="debug"))


def test_negated_phrase():
    query = compile_query('-"chat started"')

    assert query.matches(_entry("chat stopped"))
    assert not query.matches(_entry("chat started"))


# ── Combined queries and metadata ───────────────────────────────────────────


def test_terms_are_and_joined():
    query = compile_query('level=error logger~"chat_.*" -timeout')

    assert len(query.terms) == 3
    assert query.matches(_entry("boom", level="error", logger="chat_engine"))
    assert not query.matches(_entry("boom timeout", level="error", logger="chat_engine"))
    assert not query.matches(_entry("boom", level="info", logger="chat_engine"))


def test_query_text_and_terms_are_echoed():
    text = 'level=error  "exact phrase" -noise'
    query = compile_query(text)

    assert query.text == text
    assert [term.raw for term in query.terms] == ["level=error", '"exact phrase"', "-noise"]

    payload = query.to_dict()
    assert payload["text"] == text
    assert payload["is_empty"] is False
    assert [item["raw"] for item in payload["terms"]] == ["level=error", '"exact phrase"', "-noise"]


def test_matches_plain_dict_entries():
    query = compile_query("level=error timeout")

    assert query.matches({"level": "ERROR", "raw": "request timeout", "fields": {}})
    assert not query.matches({"level": "INFO", "raw": "request timeout", "fields": {}})


def test_matches_reads_nested_request_fields():
    entry = _entry(
        "Request https://example.test/api - 12.3 ms",
        fields={"request": {"url": "https://example.test/api", "method": "POST"}},
    )
    query = compile_query('request.url~"example\\.test"')

    assert query.matches(entry)
    assert not compile_query('request.url~"other-host"').matches(entry)


@pytest.mark.parametrize(
    "query_text,entry_level,expected",
    [
        ("level=info", "INFO", True),
        ("level=warning", "warn", True),
        ("level=DEBUG", "debug", True),
        ("level=critical,error", "trace", False),
        ("-level=info", "info", False),
    ],
)
def test_level_matching_matrix(query_text, entry_level, expected):
    assert compile_query(query_text).matches(_entry(level=entry_level)) is expected
