"""
Application-log explorer core (Loki/Graylog style).

Pure library code, no FastAPI here: the router in
``codx.junior.api.log_explorer`` (see ``api/docs/log-explorer.md``) wires these
helpers to ``/api/logs/explorer/*``.

Typical use::

    from codx.junior.logs import compile_query, parse_lines

    entries = parse_lines(lines, source="api.log")
    query = compile_query('level=error,warn -timeout')
    matched = [entry for entry in entries if query.matches(entry)]
"""

from codx.junior.logs.models import LEVEL_ORDER, LEVEL_RANK, LogEntry, LogLevel, normalize_level
from codx.junior.logs.parsers import (
    HEADER_PATTERN,
    iter_entries,
    parse_json_record,
    parse_lines,
    parse_timestamp_ms,
)
from codx.junior.logs.query import (
    LogQuery,
    QueryTerm,
    compile_query,
    tokenize,
)

__all__ = [
    "HEADER_PATTERN",
    "LEVEL_ORDER",
    "LEVEL_RANK",
    "LogEntry",
    "LogLevel",
    "LogQuery",
    "QueryTerm",
    "compile_query",
    "iter_entries",
    "normalize_level",
    "parse_json_record",
    "parse_lines",
    "parse_timestamp_ms",
    "tokenize",
]
