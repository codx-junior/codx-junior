"""
API-analysis aggregation for the application-log explorer.

The explorer already answers "show me the lines". This module answers the
questions you actually ask about an API: how many requests, how many failed,
how slow they are (avg / p50 / p95 / p99 / max), which endpoints are the
busiest or the slowest, which status codes show up, and which profiler methods
dominate.

Everything is derived from the **already parsed** entries (``fields['request']``
and ``fields['profiler']``, produced by :mod:`codx.junior.logs.parsers`), so it
costs a single pass over the bounded window shared with the paged query.

Diagram:
--------
```mermaid
flowchart LR
    scan[service.scan_entries] --> entries[matched LogEntry list]
    entries --> req["fields.request<br/>(url, method, status, time_taken)"]
    entries --> prof["fields.profiler<br/>(module, method, time_taken)"]
    req --> summary["summary + endpoints + slowest + status"]
    prof --> profiler[profiler table]
    entries --> hist[histogram]
    summary --> api[/api/logs/explorer/insights/]
    profiler --> api
    hist --> api
```
"""

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from codx.junior.logs.models import LogEntry
from codx.junior.logs import service

logger = logging.getLogger(__name__)

#: Number of rows returned by the ranked lists when ``top`` is not given.
DEFAULT_TOP = 10

#: Hard cap of ``top``.
MAX_TOP = 100

#: Levels counted as errors even without an HTTP status code.
ERROR_LEVELS = {"error", "critical"}


def _clamp_top(top: Optional[int]) -> int:
    """Clamp ``top`` to ``[1, MAX_TOP]`` (default :data:`DEFAULT_TOP`)."""
    try:
        value = int(top) if top is not None else DEFAULT_TOP
    except (TypeError, ValueError):
        value = DEFAULT_TOP
    return max(1, min(value, MAX_TOP))


def _percentile(values: Sequence[float], fraction: float) -> float:
    """
    Nearest-rank percentile of an ASCENDING list of values.

    Args:
        values:   Sorted durations (may be empty).
        fraction: ``0.95`` for p95, ``0.5`` for the median.

    Returns:
        The percentile, rounded to 2 decimals (``0.0`` when empty).
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return round(float(values[0]), 2)
    index = int(round(fraction * (len(values) - 1)))
    index = max(0, min(len(values) - 1, index))
    return round(float(values[index]), 2)


def _average(values: Sequence[float]) -> float:
    """Average of *values*, rounded to 2 decimals (``0.0`` when empty)."""
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _latency_stats(values: Sequence[float]) -> Dict[str, float]:
    """Build ``{avg_ms, p95_ms, max_ms}`` from raw durations."""
    ordered = sorted(values)
    return {
        "avg_ms": _average(ordered),
        "p95_ms": _percentile(ordered, 0.95),
        "max_ms": round(float(ordered[-1]), 2) if ordered else 0.0,
    }


def _request_fields(entry: LogEntry) -> Optional[Dict[str, Any]]:
    """Return ``fields['request']`` when it is a usable mapping."""
    fields = entry.fields if isinstance(entry.fields, dict) else {}
    data = fields.get("request")
    return data if isinstance(data, dict) else None


def _profiler_fields(entry: LogEntry) -> Optional[Dict[str, Any]]:
    """Return ``fields['profiler']`` when it is a usable mapping."""
    fields = entry.fields if isinstance(entry.fields, dict) else {}
    data = fields.get("profiler")
    return data if isinstance(data, dict) else None


def _path_of(url: Any) -> str:
    """
    Return the path of a request URL (scheme, host and query dropped).

    Args:
        url: The URL printed in the log line.

    Returns:
        The path, or the raw value when it cannot be parsed.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        return urlparse(text).path or text
    except ValueError:
        return text


def _status_of(request: Dict[str, Any]) -> Optional[int]:
    """Return the HTTP status code of a request field, when present."""
    try:
        return int(request.get("status_code"))
    except (TypeError, ValueError):
        return None


def _is_error(entry: LogEntry, request: Optional[Dict[str, Any]]) -> bool:
    """Return ``True`` when the entry is an error (5xx or error/critical level)."""
    if request is not None:
        status = _status_of(request)
        if status is not None and status >= 500:
            return True
        if status is not None:
            return entry.level in ERROR_LEVELS
    return entry.level in ERROR_LEVELS


def build_insights(
    sources: Optional[Sequence[str]] = None,
    q: str = "",
    start: Any = None,
    end: Any = None,
    levels: Optional[Sequence[str]] = None,
    min_ms: Any = None,
    top: Optional[int] = None,
    histogram_buckets: int = service.DEFAULT_HISTOGRAM_BUCKETS,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aggregate the API metrics of the matched log entries.

    Args:
        sources:           Source ids to scan (empty = the newest source).
        q:                 Query text (same language as ``/query``).
        start:             Inclusive time-range start.
        end:               Inclusive time-range end.
        levels:            Level filter.
        min_ms:            Keep only entries slower than this (milliseconds).
        top:               Rows returned by the ranked lists.
        histogram_buckets: Number of histogram buckets.
        folder:            Log folder override.

    Returns:
        ``{summary, histogram, endpoints, slowest, status, profiler, stats}``
        exactly as documented by the ``/api/logs/explorer/insights`` contract.
    """
    limit = _clamp_top(top)
    buckets = service._clamp_buckets(histogram_buckets)
    scan = service.scan_entries(
        sources=sources,
        q=q,
        start=start,
        end=end,
        levels=levels,
        min_ms=min_ms,
        folder=folder,
    )
    entries: List[LogEntry] = scan["entries"]

    durations: List[float] = []
    endpoints: Dict[str, Dict[str, Any]] = {}
    status_counter: Counter = Counter()
    slowest: List[Dict[str, Any]] = []
    requests = 0
    errors = 0

    for entry in entries:
        request = _request_fields(entry)
        duration = service.entry_duration_ms(entry)

        if request is not None:
            requests += 1
            path = _path_of(request.get("url"))
            method = str(request.get("method") or "").upper()
            endpoint = f"{method} {path}".strip()
            is_error = _is_error(entry, request)
            if is_error:
                errors += 1
            status = _status_of(request)
            if status is not None:
                status_counter[str(status)] += 1

            bucket = endpoints.setdefault(
                endpoint,
                {
                    "method": method,
                    "path": path,
                    "endpoint": endpoint,
                    "count": 0,
                    "errors": 0,
                    "durations": [],
                },
            )
            bucket["count"] += 1
            if is_error:
                bucket["errors"] += 1
            if duration is not None:
                bucket["durations"].append(duration)
                durations.append(duration)
                slowest.append(
                    {
                        "id": entry.id,
                        "timestamp": entry.timestamp,
                        "ts_ms": entry.ts_ms,
                        "method": method,
                        "path": path,
                        "status": status,
                        "duration_ms": round(float(duration), 2),
                        "url": str(request.get("url") or ""),
                    }
                )
        elif _is_error(entry, None):
            errors += 1

    profilers: Dict[str, List[float]] = {}
    for entry in entries:
        profiler = _profiler_fields(entry)
        if profiler is None:
            continue
        name = ".".join(
            str(part) for part in (profiler.get("module"), profiler.get("method")) if part
        ) or str(profiler.get("name") or "profiler")
        try:
            profilers.setdefault(name, []).append(float(profiler.get("time_taken")))
        except (TypeError, ValueError):
            continue

    ordered = sorted(durations)
    slowest.sort(key=lambda item: item["duration_ms"], reverse=True)

    payload = {
        "summary": {
            "requests": requests,
            "errors": errors,
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "avg_ms": _average(ordered),
            "p50_ms": _percentile(ordered, 0.5),
            "p95_ms": _percentile(ordered, 0.95),
            "p99_ms": _percentile(ordered, 0.99),
            "max_ms": round(float(ordered[-1]), 2) if ordered else 0.0,
        },
        "histogram": service._build_histogram(
            entries, scan["start_ms"], scan["end_ms"], buckets
        ),
        "endpoints": [
            {
                "method": item["method"],
                "path": item["path"],
                "endpoint": item["endpoint"],
                "count": item["count"],
                "errors": item["errors"],
                **_latency_stats(item["durations"]),
            }
            for item in sorted(
                endpoints.values(), key=lambda item: item["count"], reverse=True
            )[:limit]
        ],
        "slowest": slowest[:limit],
        "status": [
            {"value": value, "count": count}
            for value, count in sorted(status_counter.items(), key=lambda pair: -pair[1])
        ],
        "profiler": [
            {"method": name, "count": len(values), **_latency_stats(values)}
            for name, values in sorted(
                profilers.items(), key=lambda pair: len(pair[1]), reverse=True
            )[:limit]
        ],
        "stats": {
            "scanned_lines": scan["scanned_lines"],
            "scanned_bytes": scan["scanned_bytes"],
            "matched": len(entries),
            "duration_ms": scan["duration_ms"],
            "truncated": scan["truncated"],
        },
    }
    logger.debug(
        "insights: %d entries, %d requests, %d errors, top=%d in %.2f ms",
        len(entries), requests, errors, limit, scan["duration_ms"],
    )
    return payload
