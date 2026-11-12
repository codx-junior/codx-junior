"""
Log explorer router – Loki/Graylog-style exploration of the application logs.

This is the REST surface of :mod:`codx.junior.logs` (parser + query language)
and :mod:`codx.junior.logs.service` (query engine). Everything is server-side:
the browser sends filters and receives at most one page of entries plus the
aggregations it needs to render facets and the histogram, so it never
downloads a whole log file.

The legacy ``/api/system/logs`` endpoints and the AI request/response logs
(``/api/logs/*``) are untouched and keep working.

Route layout
------------
GET /api/logs/explorer/sources            – list the queryable log files
GET /api/logs/explorer/query              – filtered, paged query + histogram + facets
GET /api/logs/explorer/entry/{id}         – one entry with its verbatim raw text
GET /api/logs/explorer/context/{id}       – entries surrounding an entry
GET /api/logs/explorer/tail               – poll for new content (cursor based)

Diagram
-------
```mermaid
graph TD
    Client -->|GET /sources| Sources
    Client -->|GET /query| Query
    Client -->|GET /entry/:id| Entry
    Client -->|GET /context/:id| Context
    Client -->|GET /tail| Tail

    Sources --> list_sources["logs/sources.list_sources"]
    Query --> service["logs/service.query_logs"]
    Entry --> service
    Context --> service
    Tail --> service
    service --> sources["bounded tail window"]
    sources --> parsers["logs/parsers.parse_lines"]
    service --> query["logs/query.compile_query"]
```
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from codx.junior.logs import insights as log_insights
from codx.junior.logs import service
from codx.junior.model.model import CodxUser
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs/explorer", tags=["logs"])

# ── Response models (the frozen contract) ─────────────────────────────────────


class LogSourceModel(BaseModel):
    """A log file the explorer can query."""

    id: str = Field(description="Source id, sent back as the 'source' query param.")
    name: str = Field(description="Display name of the source.")
    path: str = Field(description="Absolute path of the log file.")
    size_bytes: int = Field(default=0, description="File size in bytes.")
    mtime: float = Field(default=0.0, description="Last modification time, epoch seconds.")
    kind: str = Field(default="log", description="Source kind: jsonl | log | text.")
    entries_estimate: int = Field(default=0, description="Estimated number of lines.")


class SourcesResponse(BaseModel):
    """Payload of ``GET /sources``."""

    sources: List[LogSourceModel] = Field(default_factory=list, description="Queryable sources.")


class LogEntryModel(BaseModel):
    """One parsed log entry as returned by the list endpoints."""

    id: str = Field(description="Stable synthetic id: '<source>#<line index>'.")
    timestamp: str = Field(default="", description="Timestamp as printed in the log.")
    ts_ms: Optional[int] = Field(default=None, description="Timestamp in epoch milliseconds.")
    level: str = Field(default="unknown", description="Canonical level name.")
    logger: str = Field(default="", description="Logger name from the header.")
    module: Optional[str] = Field(default=None, description="Module from the header.")
    line: Optional[int] = Field(default=None, description="Source line number.")
    message: str = Field(default="", description="Message body, continuations included.")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Structured extras.")
    continuation_lines: int = Field(default=0, description="Number of continuation lines.")
    source: str = Field(default="", description="Source id the entry comes from.")
    raw_preview: str = Field(default="", description="First raw line, truncated.")


class LogEntryDetailModel(LogEntryModel):
    """One entry with its verbatim raw text (``GET /entry/{id}``)."""

    raw: str = Field(default="", description="Verbatim text of the entry.")


class HistogramBucketModel(BaseModel):
    """One time bucket of the query histogram."""

    ts_ms: int = Field(description="Bucket start, epoch milliseconds.")
    total: int = Field(default=0, description="Entries in the bucket.")
    by_level: Dict[str, int] = Field(default_factory=dict, description="Count per level.")


class FacetValueModel(BaseModel):
    """A facet value with its number of entries."""

    value: str = Field(description="Facet value (level, logger or module).")
    count: int = Field(default=0, description="Number of matched entries.")


class FacetsModel(BaseModel):
    """Aggregations over the matched set."""

    level: List[FacetValueModel] = Field(default_factory=list)
    logger: List[FacetValueModel] = Field(default_factory=list)
    module: List[FacetValueModel] = Field(default_factory=list)


class QueryStatsModel(BaseModel):
    """Cost and completeness of the scan behind a query response."""

    scanned_lines: int = Field(default=0, description="Lines parsed for this query.")
    scanned_bytes: int = Field(default=0, description="Bytes read for this query.")
    matched: int = Field(default=0, description="Entries matching the query.")
    duration_ms: float = Field(default=0.0, description="Server processing time, milliseconds.")
    truncated: bool = Field(
        default=False,
        description="True when only the tail window of a bigger file was scanned.",
    )


class QueryResponse(BaseModel):
    """Payload of ``GET /query``."""

    entries: List[LogEntryModel] = Field(default_factory=list)
    total: int = Field(default=0, description="Total number of matched entries.")
    page: int = Field(default=1, description="Current page (1-based).")
    page_size: int = Field(default=service.DEFAULT_PAGE_SIZE, description="Entries per page.")
    has_more: bool = Field(default=False, description="True when another page exists.")
    histogram: List[HistogramBucketModel] = Field(default_factory=list)
    facets: FacetsModel = Field(default_factory=FacetsModel)
    stats: QueryStatsModel = Field(default_factory=QueryStatsModel)


class ContextResponse(BaseModel):
    """Payload of ``GET /context/{id}``."""

    entries: List[LogEntryModel] = Field(default_factory=list)


class TailResponse(BaseModel):
    """Payload of ``GET /tail``."""

    cursor: str = Field(default="0", description="Opaque cursor to send back on the next poll.")
    entries: List[LogEntryModel] = Field(default_factory=list)


class InsightSummaryModel(BaseModel):
    """API metrics of the matched window."""

    requests: int = Field(default=0, description="Entries carrying a parsed request.")
    errors: int = Field(default=0, description="Failed requests (5xx or error level).")
    error_rate: float = Field(default=0.0, description="errors / requests (0 when no request).")
    avg_ms: float = Field(default=0.0, description="Average request duration, milliseconds.")
    p50_ms: float = Field(default=0.0, description="Median request duration, milliseconds.")
    p95_ms: float = Field(default=0.0, description="95th percentile duration, milliseconds.")
    p99_ms: float = Field(default=0.0, description="99th percentile duration, milliseconds.")
    max_ms: float = Field(default=0.0, description="Slowest request, milliseconds.")


class InsightEndpointModel(BaseModel):
    """One endpoint (method + path) with its latency summary."""

    method: str = Field(default="", description="HTTP method.")
    path: str = Field(default="", description="URL path.")
    endpoint: str = Field(default="", description="Display label, e.g. 'GET /api/chat'.")
    count: int = Field(default=0, description="Number of calls.")
    errors: int = Field(default=0, description="Number of failed calls.")
    avg_ms: float = Field(default=0.0, description="Average duration, milliseconds.")
    p95_ms: float = Field(default=0.0, description="95th percentile duration, milliseconds.")
    max_ms: float = Field(default=0.0, description="Slowest call, milliseconds.")


class InsightSlowestModel(BaseModel):
    """One slow request, with the entry id needed to open it."""

    id: str = Field(description="Entry id, usable with /entry/{id}.")
    timestamp: str = Field(default="", description="Timestamp as printed in the log.")
    ts_ms: Optional[int] = Field(default=None, description="Timestamp in epoch milliseconds.")
    method: str = Field(default="", description="HTTP method.")
    path: str = Field(default="", description="URL path.")
    status: Optional[int] = Field(default=None, description="HTTP status code.")
    duration_ms: float = Field(default=0.0, description="Duration, milliseconds.")
    url: str = Field(default="", description="Full request URL.")


class InsightProfilerModel(BaseModel):
    """One profiler method with its latency summary."""

    method: str = Field(default="", description="module.method")
    count: int = Field(default=0, description="Number of samples.")
    avg_ms: float = Field(default=0.0, description="Average duration, milliseconds.")
    p95_ms: float = Field(default=0.0, description="95th percentile duration, milliseconds.")
    max_ms: float = Field(default=0.0, description="Slowest sample, milliseconds.")


class InsightsResponse(BaseModel):
    """Payload of ``GET /insights`` (API analysis)."""

    summary: InsightSummaryModel = Field(default_factory=InsightSummaryModel)
    histogram: List[HistogramBucketModel] = Field(default_factory=list)
    endpoints: List[InsightEndpointModel] = Field(default_factory=list)
    slowest: List[InsightSlowestModel] = Field(default_factory=list)
    status: List[FacetValueModel] = Field(default_factory=list)
    profiler: List[InsightProfilerModel] = Field(default_factory=list)
    stats: QueryStatsModel = Field(default_factory=QueryStatsModel)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _username(user: Optional[CodxUser]) -> str:
    """
    Return the username of *user*, tolerating an unauthenticated session.

    ``get_authenticated_user`` returns ``None`` (it does not raise) when the
    request carries no valid ``authentication`` header, so the explorer must
    never dereference the user blindly: doing so turned every route into a
    500 ``AttributeError: 'NoneType' object has no attribute 'username'`` and
    the UI rendered an empty source list.

    Args:
        user: Authenticated user, or ``None`` for an anonymous request.

    Returns:
        The username, or ``"anonymous"`` when there is none.
    """
    return getattr(user, "username", None) or "anonymous"


def _existing_sources(source: List[str], user: CodxUser) -> List[str]:
    """
    Validate the requested source ids against the available log files.

    Args:
        source: Source ids requested by the client (may be empty).
        user:   Authenticated user (for the log line).

    Returns:
        The requested ids (empty when the client did not filter by source).

    Raises:
        HTTPException 404: When one of the requested sources does not exist.
    """
    requested = [source_id for source_id in (source or []) if source_id]
    if not requested:
        return []
    available = {entry["id"] for entry in service.list_available_sources()}
    missing = [source_id for source_id in requested if source_id not in available]
    if missing:
        logger.warning("Log explorer: user %s asked for unknown sources %s", _username(user), missing)
        raise HTTPException(status_code=404, detail=f"Unknown log source: {', '.join(missing)}")
    return requested


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/sources", response_model=SourcesResponse, summary="List queryable application log sources")
def api_log_explorer_sources(
    user: CodxUser = Depends(get_authenticated_user),
) -> SourcesResponse:
    """
    List the application log files the explorer can query.

    The folder is the same one the legacy ``/api/system/logs`` endpoint lists
    (``CODX_JUNIOR_API_LOGS``). Sources are returned newest first.

    Returns:
        ``{"sources": [{id, name, path, size_bytes, mtime, kind, entries_estimate}]}``
    """
    sources = service.list_available_sources()
    logger.debug("Log explorer: %d sources for user %s", len(sources), _username(user))
    return SourcesResponse(sources=[LogSourceModel(**source) for source in sources])


@router.get("/query", response_model=QueryResponse, summary="Query log entries (server-side filtering and pagination)")
def api_log_explorer_query(
    source: List[str] = Query(
        default=[],
        description="Source id, repeatable. Defaults to the newest log file.",
    ),
    q: str = Query(
        "",
        description=(
            "Query text: free text, \"exact phrase\", level=error,warn, "
            "logger~\"regex\", field=value and -negation, AND-joined."
        ),
    ),
    start: Optional[str] = Query(None, description="Inclusive start: epoch ms/s or ISO-8601."),
    end: Optional[str] = Query(None, description="Inclusive end: epoch ms/s or ISO-8601."),
    level: List[str] = Query(
        default=[],
        description="Level filter, repeatable (critical|error|warn|info|debug|trace).",
    ),
    min_ms: Optional[float] = Query(
        None,
        description=(
            "Keep only entries slower than this, in milliseconds "
            "(fields.request/profiler time_taken)."
        ),
    ),
    page: int = Query(1, ge=1, description="Page number (1-based)."),
    page_size: int = Query(
        service.DEFAULT_PAGE_SIZE,
        ge=1,
        le=service.MAX_PAGE_SIZE,
        description="Entries per page (max 1000).",
    ),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order by timestamp."),
    histogram_buckets: int = Query(
        service.DEFAULT_HISTOGRAM_BUCKETS,
        ge=1,
        le=service.MAX_HISTOGRAM_BUCKETS,
        description="Number of histogram buckets.",
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> QueryResponse:
    """
    Query one or more log sources.

    Filtering, sorting, aggregation and pagination are all server-side: the
    response never carries more than ``page_size`` entries.

    Returns:
        ``{entries, total, page, page_size, has_more, histogram, facets, stats}``

    Raises:
        HTTPException 404: When a requested source does not exist.
    """
    requested = _existing_sources(source, user)
    result = service.query_logs(
        requested,
        q=q,
        start=start,
        end=end,
        levels=level,
        min_ms=min_ms,
        page=page,
        page_size=page_size,
        order=order,
        histogram_buckets=histogram_buckets,
    )
    logger.info(
        "Log explorer: user %s queried %s (q=%r) -> %d/%d entries in %.2f ms",
        _username(user), requested or "newest", q, len(result["entries"]),
        result["total"], result["stats"]["duration_ms"],
    )
    return QueryResponse(**result)


@router.get("/insights", response_model=InsightsResponse, summary="API insights: latency, endpoints, errors")
def api_log_explorer_insights(
    source: List[str] = Query(
        default=[],
        description="Source id, repeatable. Defaults to the newest source.",
    ),
    q: str = Query("", description="Query text (same language as /query)."),
    start: Optional[str] = Query(None, description="Inclusive start: epoch ms/s or ISO-8601."),
    end: Optional[str] = Query(None, description="Inclusive end: epoch ms/s or ISO-8601."),
    level: List[str] = Query(
        default=[],
        description="Level filter, repeatable (critical|error|warn|info|debug|trace).",
    ),
    min_ms: Optional[float] = Query(
        None,
        description="Keep only entries slower than this, in milliseconds.",
    ),
    top: int = Query(
        log_insights.DEFAULT_TOP,
        ge=1,
        le=log_insights.MAX_TOP,
        description="Rows returned by the ranked lists (endpoints, slowest, profiler).",
    ),
    histogram_buckets: int = Query(
        service.DEFAULT_HISTOGRAM_BUCKETS,
        ge=1,
        le=service.MAX_HISTOGRAM_BUCKETS,
        description="Number of histogram buckets.",
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> InsightsResponse:
    """
    Aggregate the API metrics of the matched log entries.

    Uses the same bounded window, filters and query language as ``/query`` and
    returns request counts, error rate, latency percentiles, the busiest and
    slowest endpoints, the status-code distribution and the profiler summary.

    Returns:
        ``{summary, histogram, endpoints, slowest, status, profiler, stats}``

    Raises:
        HTTPException 404: When a requested source does not exist.
    """
    requested = _existing_sources(source, user)
    result = log_insights.build_insights(
        requested,
        q=q,
        start=start,
        end=end,
        levels=level,
        min_ms=min_ms,
        top=top,
        histogram_buckets=histogram_buckets,
    )
    logger.info(
        "Log explorer: user %s insights %s (q=%r) -> %d requests, %d errors in %.2f ms",
        _username(user), requested or "newest", q,
        result["summary"]["requests"], result["summary"]["errors"],
        result["stats"]["duration_ms"],
    )
    return InsightsResponse(**result)


@router.get("/entry/{id}", response_model=LogEntryDetailModel, summary="Full log entry with its raw text")
def api_log_explorer_entry(
    id: str,
    source: Optional[str] = Query(
        None,
        description="Source id; taken from the entry id when omitted.",
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> LogEntryDetailModel:
    """
    Return one log entry including its verbatim ``raw`` text.

    Args:
        id:     Entry id (``<source>#<line index>``) as returned by ``/query``.
        source: Optional source id, when the id lost its source part.

    Returns:
        The full entry.

    Raises:
        HTTPException 404: When the entry is not in the scanned window.
    """
    entry = service.get_entry(entry_id=id, source=source)
    if entry is None:
        logger.info("Log explorer: user %s requested a missing entry %r", _username(user), id)
        raise HTTPException(status_code=404, detail=f"Log entry '{id}' not found.")
    return LogEntryDetailModel(**entry)


@router.get("/context/{id}", response_model=ContextResponse, summary="Entries surrounding a log entry")
def api_log_explorer_context(
    id: str,
    source: Optional[str] = Query(
        None,
        description="Source id; taken from the entry id when omitted.",
    ),
    before: int = Query(10, ge=0, le=500, description="Preceding entries."),
    after: int = Query(10, ge=0, le=500, description="Following entries."),
    user: CodxUser = Depends(get_authenticated_user),
) -> ContextResponse:
    """
    Return the entries surrounding *id* (in ascending file order).

    Args:
        id:     Entry id used as the anchor.
        source: Optional source id, when the id lost its source part.
        before: How many preceding entries to include.
        after:  How many following entries to include.

    Returns:
        ``{"entries": [...]}``

    Raises:
        HTTPException 404: When the anchor entry is not in the scanned window.
    """
    context = service.get_context(entry_id=id, source=source, before=before, after=after)
    if context is None:
        logger.info("Log explorer: user %s requested context of a missing entry %r", _username(user), id)
        raise HTTPException(status_code=404, detail=f"Log entry '{id}' not found.")
    return ContextResponse(**context)


@router.get("/tail", response_model=TailResponse, summary="Poll a log source for new entries")
def api_log_explorer_tail(
    source: Optional[str] = Query(
        None,
        description="Source id; defaults to the newest log file.",
    ),
    since_cursor: Optional[str] = Query(
        None,
        description="Cursor returned by the previous poll (byte offset).",
    ),
    user: CodxUser = Depends(get_authenticated_user),
) -> TailResponse:
    """
    Poll a log source.

    Without ``since_cursor`` the last window of the file is returned. With a
    cursor only the bytes appended after it are parsed; send the returned
    ``cursor`` back on the next poll.

    Args:
        source:       Source id (defaults to the newest log file).
        since_cursor: Cursor returned by the previous poll.

    Returns:
        ``{cursor, entries}``

    Raises:
        HTTPException 404: When no source (or the requested one) exists.
    """
    if source:
        _existing_sources([source], user)
    result = service.tail(source=source, since_cursor=since_cursor)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown log source: {source or '(none)'}")
    logger.debug(
        "Log explorer: tail %s since %r -> %d entries (cursor %s)",
        source or "newest", since_cursor, len(result["entries"]), result["cursor"],
    )
    return TailResponse(**result)

# Made with ❤️ by codx-junior
