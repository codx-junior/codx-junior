"""
Streaming-friendly parser for application log files.

This is a generalisation of :mod:`codx.junior.log_parser`: the header format,
the continuation-line handling and the request/profiler extractors are the
same ones, but an entry is produced as a :class:`~codx.junior.logs.models.LogEntry`
with a stable id, a normalised level, a parsed timestamp and JSON-logger
support. The extractors are imported from the legacy module (never duplicated),
so ``log_parser.parse_logs`` keeps working for any caller that still uses it.

Primary line format::

    [2026-01-02 10:11:12,345] INFO [logger.name:42] message goes here

Diagram:
--------
```mermaid
flowchart TD
    lines[lines iterable] --> header{header?}
    header -- yes --> new[new entry]
    header -- no --> json{json record?}
    json -- yes --> jsonEntry[json entry]
    json -- no --> cont{continuation?}
    cont -- yes --> append[append to previous entry]
    cont -- no --> unknown[unknown entry]
    new --> extract[request/profiler extractors]
    jsonEntry --> extract
    append --> extract
    unknown --> extract
```
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from codx.junior.log_parser import EXTRACTORS
from codx.junior.logs.models import LogEntry, normalize_level

logger = logging.getLogger(__name__)

#: ``[timestamp] LEVEL [logger:line] message`` — the line number and the logger
#: are both optional, the logger may contain dots.
HEADER_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\]\s+"
    r"(?P<level>[A-Za-z]+)\s+"
    r"\[(?P<logger>[^\[\]:]*)(?::(?P<line>\d+))?\]"
    r"\s?(?P<message>.*)$"
)

#: Prefixes of a line that must be appended to the previous entry, even when
#: the previous entry has no logger (free-form block at the top of a file).
_CONTINUATION_HINTS = (
    "Traceback (most recent call last)",
    "During handling of the above exception",
    "The above exception was the direct cause",
    "Caused by:",
    'File "',
    "Stack trace:",
    "... ",
)

#: Keys that make a JSON line a structured log record.
_JSON_MARKER_KEYS = ("level", "levelname", "severity", "message", "msg", "timestamp", "time")

#: JSON keys consumed by the entry itself (everything else goes to ``fields``).
_JSON_CORE_KEYS = {
    "level",
    "levelname",
    "severity",
    "logger",
    "name",
    "message",
    "msg",
    "timestamp",
    "time",
    "ts",
}

#: ``strptime`` formats accepted for the header/JSON timestamp.
_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S,%f",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y/%m/%d %H:%M:%S,%f",
    "%Y/%m/%d %H:%M:%S",
)


def parse_timestamp_ms(timestamp: Optional[str]) -> Optional[int]:
    """
    Convert a log timestamp to epoch milliseconds.

    Naive timestamps are interpreted as UTC (log files usually carry no zone
    and the explorer only needs a consistent ordering). ISO-8601 values with an
    explicit offset or a trailing ``Z`` are supported as well as bare epoch
    seconds/milliseconds.

    Args:
        timestamp: Timestamp string as printed in the log.

    Returns:
        Epoch milliseconds, or ``None`` when the value cannot be parsed.
    """
    if not timestamp:
        return None
    text = str(timestamp).strip()
    if not text:
        return None

    # Bare epoch values: 13 digits (or more) are milliseconds, 10 are seconds.
    if text.isdigit():
        if len(text) >= 13:
            return int(text)
        if len(text) == 10:
            return int(text) * 1000
        return None

    iso_text = text.replace("Z", "+0000")
    for candidate in (text, iso_text):
        for fmt in _TS_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt)
            except (ValueError, TypeError):
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)

    # datetime.fromisoformat covers offsets written as "+00:00".
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        logger.debug("parse_timestamp_ms: cannot parse timestamp %r", timestamp)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _as_text(value: Any) -> str:
    """Return *value* as text (``""`` for ``None``), JSON-encoding containers."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def parse_json_record(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse *line* as a JSON log record.

    Args:
        line: A single raw log line.

    Returns:
        The decoded dict when the whole line is a JSON object that looks like a
        log record (it carries at least one of ``level``/``message``/``msg``/
        ``timestamp``), otherwise ``None``.
    """
    text = line.strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        record = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    if not any(key in record for key in _JSON_MARKER_KEYS):
        return None
    return record


class _EntryBuilder:
    """Mutable accumulator for the entry that is currently being read."""

    __slots__ = (
        "columns",
        "continuation_lines",
        "fields",
        "message_parts",
        "offset",
        "raw_parts",
        "source",
    )

    def __init__(
        self,
        source: str,
        offset: int,
        timestamp: str = "",
        level: str = "unknown",
        logger_name: str = "",
        module: Optional[str] = None,
        line: Optional[int] = None,
    ) -> None:
        self.source = source
        self.offset = offset
        self.columns = {
            "timestamp": timestamp,
            "level": level,
            "logger": logger_name,
            "module": module,
            "line": line,
        }
        self.message_parts: List[str] = []
        self.raw_parts: List[str] = []
        self.continuation_lines = 0
        self.fields: Dict[str, Any] = {}

    # ── Content ─────────────────────────────────────────────────────────────

    def append_message(self, text: str) -> None:
        """Append the first (header) line of the entry."""
        self.message_parts.append(text)

    def append_raw(self, text: str) -> None:
        """Append a raw line exactly as read from the file."""
        self.raw_parts.append(text)

    def add_continuation(self, text: str) -> None:
        """Attach a continuation line (traceback, wrapped message, …)."""
        self.message_parts.append(text)
        self.raw_parts.append(text)
        self.continuation_lines += 1

    @property
    def logger_name(self) -> str:
        """Logger name of the entry (``""`` when the header had none)."""
        return self.columns["logger"]

    # ── Result ──────────────────────────────────────────────────────────────

    def build(self) -> LogEntry:
        """Materialise the accumulated entry, running the shared extractors."""
        message = "\n".join(self.message_parts)
        raw = "\n".join(self.raw_parts)
        fields: Dict[str, Any] = dict(self.fields)

        for extractor in EXTRACTORS:
            try:
                data = extractor(message)
            except Exception as exc:  # noqa: BLE001 - extractors must never break parsing
                extractor_name = getattr(extractor, "__name__", str(extractor))
                logger.debug("%s extractor failed: %s", extractor_name, exc)
                data = None
            if data:
                fields.update(data)

        timestamp = self.columns["timestamp"] or ""
        return LogEntry(
            id=f"{self.source}#{self.offset}",
            timestamp=timestamp,
            ts_ms=parse_timestamp_ms(timestamp),
            level=self.columns["level"] or "unknown",
            logger=self.columns["logger"] or "",
            module=self.columns["module"],
            line=self.columns["line"],
            message=message,
            fields=fields,
            continuation_lines=self.continuation_lines,
            source=self.source,
            raw=raw,
        )


def _build_json_entry(
    record: Dict[str, Any],
    source: str,
    offset: int,
    raw_line: str,
) -> LogEntry:
    """Build an entry from a structured (JSON logger) record."""
    timestamp = _as_text(
        record.get("timestamp") or record.get("time") or record.get("ts") or ""
    )
    level = normalize_level(
        _as_text(record.get("level") or record.get("levelname") or record.get("severity"))
    )
    logger_name = _as_text(record.get("logger") or record.get("name") or "")
    if not logger_name:
        module_candidate = record.get("module")
        if isinstance(module_candidate, str):
            logger_name = module_candidate
    message = _as_text(record.get("message") or record.get("msg") or "")

    module = record.get("module")
    module = module if isinstance(module, str) and module else None
    line = record.get("line")
    line = line if isinstance(line, int) and not isinstance(line, bool) else None

    fields = {key: value for key, value in record.items() if key not in _JSON_CORE_KEYS}

    return LogEntry(
        id=f"{source}#{offset}",
        timestamp=timestamp,
        ts_ms=parse_timestamp_ms(timestamp),
        level=level,
        logger=logger_name,
        module=module,
        line=line,
        message=message,
        fields=fields,
        continuation_lines=0,
        source=source,
        raw=raw_line,
    )


def _is_continuation(line: str, current: Optional[_EntryBuilder]) -> bool:
    """
    Decide whether *line* continues the entry held by *current*.

    Indented lines always continue. A headerless entry (free-form text at the
    top of a file, ``level='unknown'``) only absorbs traceback-shaped lines so
    that a block of unrelated lines does not collapse into one huge entry.
    """
    if current is None:
        return False
    if line[:1] in (" ", "\t"):
        return True
    stripped = line.strip()
    for hint in _CONTINUATION_HINTS:
        if stripped.startswith(hint):
            return True
    if not current.logger_name:
        return False
    return True


def iter_entries(
    lines: Iterable[str],
    source: str,
    start_index: int = 0,
) -> Iterator[LogEntry]:
    """
    Yield :class:`LogEntry` objects as the lines are consumed.

    Only one entry is held in memory at a time, so this is the entry point for
    huge files: pass an open file object or any line iterator and stop early
    when enough entries were collected.

    Args:
        lines:       Iterable of raw lines (without trailing newlines preferred).
        source:      Logical source id, used to build stable ids.
        start_index: Line index of the first line (for tailing a growing file).

    Yields:
        One :class:`LogEntry` per header line (plus headerless/JSON entries).
    """
    current: Optional[_EntryBuilder] = None

    for offset, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            # Blank lines carry no information; skipping them keeps messages tidy.
            continue
        index = start_index + offset

        header = HEADER_PATTERN.match(line)
        if header:
            if current is not None:
                yield current.build()
            logger_name = (header.group("logger") or "").strip()
            raw_number = header.group("line")
            current = _EntryBuilder(
                source=source,
                offset=index,
                timestamp=header.group("timestamp").strip(),
                level=normalize_level(header.group("level")),
                logger_name=logger_name,
                module=logger_name or None,
                line=int(raw_number) if raw_number else None,
            )
            current.append_message(header.group("message"))
            current.append_raw(line)
            continue

        record = parse_json_record(line)
        if record is not None:
            if current is not None:
                yield current.build()
                current = None
            yield _build_json_entry(record, source, index, line)
            continue

        if _is_continuation(line, current):
            current.add_continuation(line)
            continue

        if current is not None:
            yield current.build()
        current = _EntryBuilder(source=source, offset=index, level="unknown", logger_name="")
        current.append_message(line)
        current.append_raw(line)

    if current is not None:
        yield current.build()


def parse_lines(
    lines: Iterable[str],
    source: str,
    start_index: int = 0,
) -> List[LogEntry]:
    """
    Parse an iterable of log lines into a list of entries.

    Thin wrapper around :func:`iter_entries` used by the callers that need the
    whole slice (for example a bounded page of a file).

    Args:
        lines:       Iterable of raw lines.
        source:      Logical source id, used to build stable ids.
        start_index: Line index of the first line.

    Returns:
        The parsed entries, in file order.
    """
    return list(iter_entries(lines, source=source, start_index=start_index))
