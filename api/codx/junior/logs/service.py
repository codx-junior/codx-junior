"""
Query engine of the application-log explorer (Loki/Graylog style).

The engine turns a bounded byte window of one or more log files into the
payload the frozen ``/api/logs/explorer/*`` contract describes: parsed entries,
pagination, a histogram, facets and query statistics.

Pipeline::

    source files ──► bounded tail window (sources.py)
                 ──► parse_lines (parsers.py)  ──► LogEntry list
                 ──► level / time-range / query predicates (query.py)
                 ──► sort ──► histogram + facets ──► page slice

Diagram:
--------
```mermaid
flowchart LR
    src[sources.py] -->|byte window| parse[parsers.parse_lines]
    parse --> entries[LogEntry list]
    entries --> filters["level + start/end + compile_query"]
    filters --> sort["sort by ts_ms/line"]
    sort --> agg["histogram + facets"]
    sort --> page["page slice (<= page_size)"]
    agg --> api[/api/logs/explorer/query/]
    page --> api
```
"""

import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codx.junior.logs.models import LogEntry, normalize_level
from codx.junior.logs.parsers import parse_lines, parse_timestamp_ms
from codx.junior.logs.query import compile_query
from codx.junior.logs.sources import (
    LogSource,
    container_name,
    estimate_line_index,
    get_source,
    is_docker_source,
    list_all_sources,
    list_sources,
    read_container_tail,
    read_range,
    read_tail,
    split_log_lines,
)

logger = logging.getLogger(__name__)

#: Default number of entries per page.
DEFAULT_PAGE_SIZE = 100

#: Hard cap of entries per page: the browser never receives more than this.
MAX_PAGE_SIZE = 1000

#: Default number of histogram buckets.
DEFAULT_HISTOGRAM_BUCKETS = 24

#: Hard cap of histogram buckets.
MAX_HISTOGRAM_BUCKETS = 200

#: How many values each facet (level/logger/module) returns.
FACET_LIMIT = 25

#: Characters of the entry raw first line kept in ``raw_preview``.
RAW_PREVIEW_CHARS = 300

#: Bytes of a log file scanned per query when the file is bigger.
DEFAULT_SCAN_BYTES = 8 * 1024 * 1024

#: Environment variable that overrides :data:`DEFAULT_SCAN_BYTES`.
SCAN_BYTES_ENV = "CODX_JUNIOR_LOG_EXPLORER_SCAN_BYTES"

#: Minimal scan window (protects against a misconfigured environment).
MIN_SCAN_BYTES = 1024

#: How many sources a single query may scan.
MAX_SOURCES_PER_QUERY = 8

#: Maximum number of entries returned by a ``/tail`` poll.
MAX_TAIL_ENTRIES = 500


# ── Tunables ──────────────────────────────────────────────────────────────────


def _scan_limit() -> int:
    """
    Return the byte window scanned per query/tail call.

    The value comes from ``CODX_JUNIOR_LOG_EXPLORER_SCAN_BYTES`` (read on every
    call so it can be tuned without a restart) and falls back to
    :data:`DEFAULT_SCAN_BYTES`.

    Returns:
        The scan window in bytes, never smaller than :data:`MIN_SCAN_BYTES`.
    """
    raw = os.environ.get(SCAN_BYTES_ENV)
    try:
        value = int(raw) if raw else DEFAULT_SCAN_BYTES
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using %d bytes", SCAN_BYTES_ENV, raw, DEFAULT_SCAN_BYTES)
        value = DEFAULT_SCAN_BYTES
    return max(MIN_SCAN_BYTES, value)


def _clamp_page_size(page_size: Optional[int]) -> int:
    """Clamp ``page_size`` to ``[1, MAX_PAGE_SIZE]`` (default :data:`DEFAULT_PAGE_SIZE`)."""
    try:
        value = int(page_size) if page_size is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        value = DEFAULT_PAGE_SIZE
    return max(1, min(value, MAX_PAGE_SIZE))


def _clamp_buckets(buckets: Optional[int]) -> int:
    """Clamp the histogram bucket count to ``[1, MAX_HISTOGRAM_BUCKETS]``."""
    try:
        value = int(buckets) if buckets is not None else DEFAULT_HISTOGRAM_BUCKETS
    except (TypeError, ValueError):
        value = DEFAULT_HISTOGRAM_BUCKETS
    return max(1, min(value, MAX_HISTOGRAM_BUCKETS))


def to_epoch_ms(value: Any) -> Optional[int]:
    """
    Convert a time bound to epoch milliseconds.

    Accepts epoch seconds, epoch milliseconds, numeric strings and ISO-8601
    timestamps, so the frontend can send whatever a date picker produced.

    Args:
        value: Time bound (``None``/``""`` means "no bound").

    Returns:
        The bound in epoch milliseconds, or ``None`` when it is absent or
        cannot be parsed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return parse_timestamp_ms(text)

    if number <= 0:
        return None
    # 1e11 ms is 1973: anything below is epoch seconds.
    return int(number if number >= 1e11 else number * 1000)


# ── Serialisation ─────────────────────────────────────────────────────────────


def _raw_preview(entry: LogEntry) -> str:
    """Return the first line of the entry, truncated to ``RAW_PREVIEW_CHARS``."""
    text = entry.raw or entry.message or ""
    first_line = text.split("\n", 1)[0]
    if len(first_line) > RAW_PREVIEW_CHARS:
        return first_line[:RAW_PREVIEW_CHARS] + "..."
    return first_line


def entry_to_dict(entry: LogEntry, include_raw: bool = False) -> Dict[str, Any]:
    """
    Serialise an entry to the API shape.

    Args:
        entry:       The parsed entry.
        include_raw: Also include the verbatim ``raw`` text (detail endpoint).

    Returns:
        A JSON-friendly dict following the frozen contract.
    """
    data: Dict[str, Any] = {
        "id": entry.id,
        "timestamp": entry.timestamp,
        "ts_ms": entry.ts_ms,
        "level": entry.level,
        "logger": entry.logger,
        "module": entry.module,
        "line": entry.line,
        "message": entry.message,
        "fields": entry.fields,
        "continuation_lines": entry.continuation_lines,
        "source": entry.source,
        "raw_preview": _raw_preview(entry),
    }
    if include_raw:
        data["raw"] = entry.raw
    return data


# ── Scanning ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Window:
    """Parsed snapshot of one source: the bounded tail window as entries."""

    source: LogSource
    entries: List[LogEntry]
    scanned_lines: int
    scanned_bytes: int
    truncated: bool


def _read_window(source: LogSource, max_bytes: int) -> _Window:
    """
    Read and parse the bounded tail window of *source*.

    File sources are read with a byte window from the end of the file; docker
    sources are read with ``docker logs --tail N`` (same bounded-window idea,
    applied to the container log stream).

    Args:
        source:    The log source to read.
        max_bytes: Size of the byte window taken from the end of the file.

    Returns:
        The parsed :class:`_Window` (empty when the file vanished).
    """
    if is_docker_source(source):
        lines = read_container_tail(container_name(source))
        entries = parse_lines(lines, source=source.id, start_index=0)
        return _Window(
            source=source,
            entries=entries,
            scanned_lines=len(lines),
            scanned_bytes=sum(len(line) + 1 for line in lines),
            truncated=False,
        )

    try:
        read = read_tail(source.path, max_bytes=max_bytes)
    except OSError as ex:
        logger.warning("Log explorer: cannot read %s: %s", source.path, ex)
        return _Window(source=source, entries=[], scanned_lines=0, scanned_bytes=0, truncated=False)

    start_index = estimate_line_index(source.path, read.start_offset)
    entries = parse_lines(read.lines, source=source.id, start_index=start_index)
    return _Window(
        source=source,
        entries=entries,
        scanned_lines=len(read.lines),
        scanned_bytes=read.bytes_read,
        truncated=read.truncated,
    )


def _resolve_sources(
    sources: Optional[Sequence[str]],
    folder: Optional[str] = None,
) -> List[LogSource]:
    """
    Resolve the requested source ids to :class:`LogSource` objects.

    An empty selection means "the newest log file", which is what an explorer
    opens by default. Unknown ids are skipped with a warning (the file may have
    rotated away between two polls) instead of failing the whole query.

    Args:
        sources: Requested source ids (file names), possibly empty.
        folder:  Folder to scan; defaults to the configured log folder.

    Returns:
        The sources to scan, at most :data:`MAX_SOURCES_PER_QUERY`.
    """
    available = list_all_sources(folder)
    if not available:
        return []

    wanted = [source_id for source_id in (sources or []) if source_id]
    if not wanted:
        return available[:1]

    by_id = {source.id: source for source in available}
    selected: List[LogSource] = []
    for source_id in wanted[:MAX_SOURCES_PER_QUERY]:
        source = by_id.get(source_id)
        if source is None:
            logger.warning("Log explorer: source %r not found, skipping it", source_id)
            continue
        if source not in selected:
            selected.append(source)
    return selected


# ── Predicates & aggregations ─────────────────────────────────────────────────


def _sort_key(entry: LogEntry) -> Tuple[int, int, str]:
    """Sort key: timestamp (entries without one go last), then header line."""
    return (
        entry.ts_ms if entry.ts_ms is not None else -1,
        entry.line if entry.line is not None else 0,
        entry.id,
    )


def _in_range(entry: LogEntry, start_ms: Optional[int], end_ms: Optional[int]) -> bool:
    """
    Return ``True`` when *entry* falls inside the ``[start_ms, end_ms]`` range.

    Entries without a parsable timestamp are only kept when no bound was given:
    they cannot be placed on the timeline.
    """
    if start_ms is None and end_ms is None:
        return True
    if entry.ts_ms is None:
        return False
    if start_ms is not None and entry.ts_ms < start_ms:
        return False
    if end_ms is not None and entry.ts_ms > end_ms:
        return False
    return True


def _build_histogram(
    entries: Sequence[LogEntry],
    start_ms: Optional[int],
    end_ms: Optional[int],
    buckets: int,
) -> List[Dict[str, Any]]:
    """
    Build evenly spaced time buckets over the matched entries.

    Args:
        entries: Entries matched by the query (already filtered).
        start_ms: Range start (``None`` → earliest matched timestamp).
        end_ms:   Range end (``None`` → latest matched timestamp).
        buckets:  Number of buckets.

    Returns:
        One ``{ts_ms, total, by_level}`` dict per bucket, oldest first; an empty
        list when no entry carries a timestamp.
    """
    timestamps = [entry.ts_ms for entry in entries if entry.ts_ms is not None]
    if not timestamps:
        return []

    buckets = _clamp_buckets(buckets)
    low = start_ms if start_ms is not None else min(timestamps)
    high = end_ms if end_ms is not None else max(timestamps)
    if high < low:
        low, high = high, low

    span = max(1, high - low)
    width = max(1.0, span / buckets)
    histogram: List[Dict[str, Any]] = [
        {"ts_ms": int(low + index * width), "total": 0, "by_level": {}}
        for index in range(buckets)
    ]

    for entry in entries:
        if entry.ts_ms is None:
            continue
        position = int((entry.ts_ms - low) / width)
        position = 0 if position < 0 else (buckets - 1 if position >= buckets else position)
        bucket = histogram[position]
        bucket["total"] += 1
        bucket["by_level"][entry.level] = bucket["by_level"].get(entry.level, 0) + 1

    return histogram


def _facet_values(counter: Counter) -> List[Dict[str, Any]]:
    """Return the ``FACET_LIMIT`` most frequent values of *counter*."""
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{"value": value, "count": count} for value, count in ordered[:FACET_LIMIT]]


def _build_facets(entries: Sequence[LogEntry]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Build the level/logger/module facets over the matched entries.

    Args:
        entries: Entries matched by the query (before pagination).

    Returns:
        ``{"level": [...], "logger": [...], "module": [...]}`` with
        ``{value, count}`` items, most frequent first.
    """
    counters: Dict[str, Counter] = {
        "level": Counter(),
        "logger": Counter(),
        "module": Counter(),
    }
    for entry in entries:
        counters["level"][entry.level or "unknown"] += 1
        if entry.logger:
            counters["logger"][entry.logger] += 1
        if entry.module:
            counters["module"][entry.module] += 1
    return {name: _facet_values(counter) for name, counter in counters.items()}


def _min_ms_value(value: Any) -> Optional[float]:
    """
    Parse the ``min_ms`` filter.

    Args:
        value: Raw value from the query string (``None``/``""`` disables it).

    Returns:
        The minimum duration in milliseconds, or ``None`` when there is no
        usable bound.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def entry_duration_ms(entry: LogEntry) -> Optional[float]:
    """
    Return the measured duration of *entry*, in milliseconds.

    API request lines carry ``fields['request']['time_taken']`` and profiler
    lines ``fields['profiler']['time_taken']``; anything else has no duration.

    Args:
        entry: Parsed log entry.

    Returns:
        The duration in ms, or ``None`` when the entry carries none.
    """
    fields = entry.fields if isinstance(entry.fields, dict) else {}
    for key in ("request", "profiler"):
        data = fields.get(key)
        if not isinstance(data, dict):
            continue
        try:
            return float(data.get("time_taken"))
        except (TypeError, ValueError):
            continue
    return None


def scan_entries(
    sources: Optional[Sequence[str]] = None,
    q: str = "",
    start: Any = None,
    end: Any = None,
    levels: Optional[Sequence[str]] = None,
    min_ms: Any = None,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scan the bounded windows of *sources* and return the matched entries.

    Shared by the paged query (:func:`query_logs`) and the API insights
    aggregation (:mod:`codx.junior.logs.insights`) so both always agree on what
    "matches".

    Args:
        sources: Source ids to scan; empty means "the newest source".
        q:       Query text (see :mod:`codx.junior.logs.query`).
        start:   Inclusive time-range start (epoch s/ms or ISO-8601).
        end:     Inclusive time-range end (epoch s/ms or ISO-8601).
        levels:  Level filter (canonical or raw level names).
        min_ms:  Keep only entries whose measured duration is >= ``min_ms``.
        folder:  Log folder override.

    Returns:
        ``{entries, scanned_lines, scanned_bytes, truncated, start_ms, end_ms,
        duration_ms}``; the entries are NOT sorted (callers decide the order).
    """
    started = time.perf_counter()
    query = compile_query(q)
    level_filter = {
        normalize_level(level) for level in (levels or []) if str(level).strip()
    }
    start_ms = to_epoch_ms(start)
    end_ms = to_epoch_ms(end)
    min_duration = _min_ms_value(min_ms)

    matched: List[LogEntry] = []
    scanned_lines = 0
    scanned_bytes = 0
    truncated = False

    for source in _resolve_sources(sources, folder):
        window = _read_window(source, _scan_limit())
        scanned_lines += window.scanned_lines
        scanned_bytes += window.scanned_bytes
        truncated = truncated or window.truncated
        for entry in window.entries:
            if level_filter and entry.level not in level_filter:
                continue
            if not _in_range(entry, start_ms, end_ms):
                continue
            if min_duration is not None:
                duration = entry_duration_ms(entry)
                if duration is None or duration < min_duration:
                    continue
            if not query.matches(entry):
                continue
            matched.append(entry)

    return {
        "entries": matched,
        "scanned_lines": scanned_lines,
        "scanned_bytes": scanned_bytes,
        "truncated": truncated,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }


# ── Public API ────────────────────────────────────────────────────────────────


def query_logs(
    sources: Optional[Sequence[str]] = None,
    q: str = "",
    start: Any = None,
    end: Any = None,
    levels: Optional[Sequence[str]] = None,
    min_ms: Any = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    order: str = "desc",
    histogram_buckets: int = DEFAULT_HISTOGRAM_BUCKETS,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query one or more log sources and return the frozen response payload.

    Filtering, sorting, aggregation and pagination all happen here: the client
    never receives more than ``page_size`` entries (capped at
    :data:`MAX_PAGE_SIZE`) and never a whole log file.

    Args:
        sources:          Source ids to scan; empty means "the newest file".
        q:                Query text (see :mod:`codx.junior.logs.query`).
        start:            Inclusive time-range start (epoch s/ms or ISO-8601).
        end:              Inclusive time-range end (epoch s/ms or ISO-8601).
        levels:           Level filter (canonical or raw level names).
        page:             1-based page number.
        page_size:        Entries per page (clamped to ``[1, MAX_PAGE_SIZE]``).
        order:            ``desc`` (newest first, default) or ``asc``.
        histogram_buckets: Number of histogram buckets.
        folder:           Log folder override (defaults to the configured one).

    Returns:
        ``{entries, total, page, page_size, has_more, histogram, facets, stats}``
        exactly as documented by the ``/api/logs/explorer/query`` contract.
    """
    started = time.perf_counter()

    page = max(1, int(page or 1))
    page_size = _clamp_page_size(page_size)
    buckets = _clamp_buckets(histogram_buckets)
    descending = str(order or "desc").lower() != "asc"

    scan = scan_entries(
        sources=sources,
        q=q,
        start=start,
        end=end,
        levels=levels,
        min_ms=min_ms,
        folder=folder,
    )
    matched: List[LogEntry] = scan["entries"]
    scanned_lines = scan["scanned_lines"]
    scanned_bytes = scan["scanned_bytes"]
    truncated = scan["truncated"]
    start_ms = scan["start_ms"]
    end_ms = scan["end_ms"]

    matched.sort(key=_sort_key, reverse=descending)
    total = len(matched)
    offset = (page - 1) * page_size
    page_entries = matched[offset:offset + page_size]
    duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

    logger.debug(
        "query_logs: q=%r levels=%s range=%s..%s matched=%d scanned=%d lines/%d bytes "
        "page=%d/%d in %.2f ms",
        q, sorted(levels or []) or "-", start_ms, end_ms, total, scanned_lines,
        scanned_bytes, page, page_size, duration_ms,
    )

    return {
        "entries": [entry_to_dict(entry) for entry in page_entries],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + len(page_entries) < total,
        "histogram": _build_histogram(matched, start_ms, end_ms, buckets),
        "facets": _build_facets(matched),
        "stats": {
            "scanned_lines": scanned_lines,
            "scanned_bytes": scanned_bytes,
            "matched": total,
            "duration_ms": duration_ms,
            "truncated": truncated,
        },
    }


def _split_entry_id(entry_id: str) -> Tuple[str, Optional[int]]:
    """
    Split ``"<source>#<index>"`` into its parts.

    Args:
        entry_id: Entry id produced by :func:`codx.junior.logs.parsers.parse_lines`.

    Returns:
        ``(source_id, index)`` — ``index`` is ``None`` when it is not numeric.
    """
    source_id, _separator, raw_index = (entry_id or "").rpartition("#")
    try:
        return source_id, int(raw_index)
    except (TypeError, ValueError):
        return (entry_id or ""), None


def _find_entry(
    entry_id: str,
    source: Optional[str] = None,
    folder: Optional[str] = None,
) -> Optional[LogEntry]:
    """
    Resolve an entry id against a freshly parsed window of its source.

    Args:
        entry_id: The entry id (``<source>#<line index>``).
        source:   Optional source override (when the id lost its source part).
        folder:   Log folder override.

    Returns:
        The matching entry, or ``None`` when it is not in the scanned window.
    """
    source_id, _index = _split_entry_id(entry_id)
    source_id = source or source_id
    log_source = get_source(source_id, folder)
    if log_source is None:
        logger.debug("_find_entry: unknown source %r for id %r", source_id, entry_id)
        return None

    window = _read_window(log_source, _scan_limit())
    for entry in window.entries:
        if entry.id == entry_id:
            return entry
    logger.debug("_find_entry: id %r not found in %s", entry_id, log_source.path)
    return None


def get_entry(
    entry_id: str,
    source: Optional[str] = None,
    folder: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return one entry with its verbatim ``raw`` text.

    Args:
        entry_id: The entry id (``<source>#<line index>``).
        source:   Optional source override.
        folder:   Log folder override.

    Returns:
        The full entry dict, or ``None`` when it cannot be found.
    """
    entry = _find_entry(entry_id, source=source, folder=folder)
    if entry is None:
        return None
    return entry_to_dict(entry, include_raw=True)


def get_context(
    entry_id: str,
    source: Optional[str] = None,
    before: int = 10,
    after: int = 10,
    folder: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return the entries surrounding *entry_id* (in file order).

    Args:
        entry_id: The anchor entry id.
        source:   Optional source override.
        before:   How many preceding entries to include.
        after:    How many following entries to include.
        folder:   Log folder override.

    Returns:
        ``{"entries": [...]}`` in ascending file order, or ``None`` when the
        anchor entry is not in the scanned window.
    """
    source_id, _index = _split_entry_id(entry_id)
    log_source = get_source(source or source_id, folder)
    if log_source is None:
        logger.debug("get_context: unknown source for id %r", entry_id)
        return None

    window = _read_window(log_source, _scan_limit())
    position = next(
        (index for index, entry in enumerate(window.entries) if entry.id == entry_id),
        None,
    )
    if position is None:
        logger.debug("get_context: id %r not found in %s", entry_id, log_source.path)
        return None

    before = max(0, int(before or 0))
    after = max(0, int(after or 0))
    selection = window.entries[max(0, position - before):position + after + 1]
    return {"entries": [entry_to_dict(entry) for entry in selection]}


def _to_cursor(since_cursor: Any) -> Optional[int]:
    """
    Parse a ``/tail`` cursor (a byte offset, optionally with a ``b`` prefix).

    Args:
        since_cursor: Cursor returned by a previous ``/tail`` call.

    Returns:
        The byte offset, or ``None`` when the cursor is absent/unusable.
    """
    if since_cursor is None:
        return None
    text = str(since_cursor).strip().lstrip("b")
    if not text:
        return None
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        logger.debug("tail: ignoring invalid cursor %r", since_cursor)
        return None


def _tail_container(log_source: LogSource, since_cursor: Any) -> Dict[str, Any]:
    """
    Poll a docker container for new entries.

    A container log has no byte offset the caller can trust, so the cursor is
    the number of entries already returned: the next poll returns everything
    after it.

    Args:
        log_source:   The container source.
        since_cursor: Cursor returned by the previous poll (entry count).

    Returns:
        ``{"cursor": "<entry count>", "entries": [...]}``.
    """
    entries = _read_window(log_source, 0).entries
    total = len(entries)
    cursor = _to_cursor(since_cursor)
    if cursor is None or cursor < 0 or cursor > total:
        selected = entries[-MAX_TAIL_ENTRIES:]
    else:
        selected = entries[cursor:][-MAX_TAIL_ENTRIES:]
    return {
        "cursor": str(total),
        "entries": [entry_to_dict(entry) for entry in selected],
    }


def tail(
    source: Optional[str] = None,
    since_cursor: Any = None,
    folder: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Poll a log source for new content (byte-offset cursor based).

    Without a cursor the last window of the file is returned (``tail -n``
    behaviour). With a cursor only the bytes written after it are parsed, and a
    partially written last line is left for the next poll. A cursor greater than
    the file size means the file was rotated/truncated: the cursor is reset and
    the current tail is returned.

    Args:
        source:       Source id; defaults to the newest log file.
        since_cursor: Cursor returned by the previous poll.
        folder:       Log folder override.

    Returns:
        ``{"cursor": "<new offset>", "entries": [...]}``, or ``None`` when the
        source does not exist.
    """
    if source:
        log_source = get_source(source, folder)
    else:
        available = list_all_sources(folder)
        log_source = available[0] if available else None
    if log_source is None:
        return None

    if is_docker_source(log_source):
        return _tail_container(log_source, since_cursor)

    try:
        size = os.path.getsize(log_source.path)
    except OSError as ex:
        logger.warning("tail: cannot stat %s: %s", log_source.path, ex)
        return None

    cursor = _to_cursor(since_cursor)
    max_bytes = _scan_limit()

    if cursor is None or cursor <= 0 or cursor > size:
        if cursor is not None and cursor > size:
            logger.info(
                "tail: %s rotated or truncated (cursor %d > size %d), resetting",
                log_source.id, cursor, size,
            )
        read = read_tail(log_source.path, max_bytes=max_bytes)
        start_index = estimate_line_index(log_source.path, read.start_offset)
        entries = parse_lines(read.lines, source=log_source.id, start_index=start_index)
        logger.debug("tail: initial read of %s returned %d entries", log_source.id, len(entries))
        return {
            "cursor": str(size),
            "entries": [entry_to_dict(entry) for entry in entries[-MAX_TAIL_ENTRIES:]],
        }

    start = max(cursor, size - max_bytes)
    try:
        data = read_range(log_source.path, start, size - start)
    except OSError as ex:
        logger.warning("tail: cannot read %s: %s", log_source.path, ex)
        return {"cursor": str(cursor), "entries": []}

    cut = data.rfind(b"\n")
    if cut < 0:
        # Only a partially written line: keep the cursor where it was.
        logger.debug("tail: no complete line after %d in %s", start, log_source.id)
        return {"cursor": str(start), "entries": []}

    consumed = data[:cut + 1]
    lines = split_log_lines(consumed)
    start_index = estimate_line_index(log_source.path, start)
    entries = parse_lines(lines, source=log_source.id, start_index=start_index)
    new_cursor = start + len(consumed)
    logger.debug(
        "tail: %s %d new bytes -> %d entries", log_source.id, len(consumed), len(entries)
    )
    return {
        "cursor": str(new_cursor),
        "entries": [entry_to_dict(entry) for entry in entries[-MAX_TAIL_ENTRIES:]],
    }


def list_available_sources(folder: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return the serialised list of queryable sources.

    Args:
        folder: Log folder override.

    Returns:
        A list of ``LogSource`` dicts, newest first.
    """
    return [source.model_dump() for source in list_all_sources(folder)]
