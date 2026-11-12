"""
Pydantic models and level helpers for the application-log explorer.

Every parsed application-log line travels through this module, so the contract
shared with the ``/api/logs/explorer/*`` endpoints (and therefore with the
Logs Explorer UI) lives in :class:`LogEntry`.

Diagram:
--------
```mermaid
flowchart LR
    raw[raw log line] --> parser[logs/parsers.py]
    parser --> entry[LogEntry]
    entry --> query[logs/query.py]
    query --> api[/api/logs/explorer/query/]
```
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

#: Canonical level names, most severe first. The index is the severity rank:
#: clients sort and colour the entries from this single ordering.
LEVEL_ORDER = [
    "critical",
    "error",
    "warn",
    "info",
    "debug",
    "trace",
    "unknown",
]

#: ``level -> severity rank`` map built from :data:`LEVEL_ORDER`.
LEVEL_RANK = {level: index for index, level in enumerate(LEVEL_ORDER)}

#: Raw level names found in the wild mapped to the canonical ones.
_LEVEL_ALIASES = {
    "critical": "critical",
    "crit": "critical",
    "fatal": "critical",
    "panic": "critical",
    "error": "error",
    "err": "error",
    "exception": "error",
    "warning": "warn",
    "warn": "warn",
    "info": "info",
    "information": "info",
    "notice": "info",
    "debug": "debug",
    "trace": "trace",
    "verbose": "trace",
}


def normalize_level(level: Optional[str]) -> str:
    """
    Map a raw log level onto one of the canonical values in :data:`LEVEL_ORDER`.

    ``CRITICAL``/``FATAL`` become ``critical``, ``ERROR``/``EXCEPTION`` become
    ``error``, ``WARNING``/``WARN`` become ``warn``, ``INFO``/``DEBUG``/``TRACE``
    are kept, and anything else (including empty input) becomes ``unknown``.

    Args:
        level: Raw level name as found in a log line or a JSON log record.

    Returns:
        One of the values in :data:`LEVEL_ORDER`.
    """
    if not level:
        return "unknown"
    key = str(level).strip().lower().strip("[]():'\"")
    if not key:
        return "unknown"
    return _LEVEL_ALIASES.get(key, "unknown")


class LogLevel:
    """
    Namespace for the canonical level names.

    Kept as a class so callers can write ``LogLevel.ERROR`` instead of a raw
    string while :func:`normalize_level` stays a plain function.
    """

    CRITICAL = "critical"
    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    DEBUG = "debug"
    TRACE = "trace"
    UNKNOWN = "unknown"

    #: Severity ordering, most severe first.
    ORDER = LEVEL_ORDER

    @staticmethod
    def normalize(value: Optional[str]) -> str:
        """Alias of :func:`normalize_level`."""
        return normalize_level(value)

    @staticmethod
    def rank(value: Optional[str]) -> int:
        """Return the severity rank of *value* (``unknown`` when unrecognised)."""
        return LEVEL_RANK.get(normalize_level(value), LEVEL_RANK["unknown"])


class LogEntry(BaseModel):
    """
    A single parsed application-log entry.

    An entry owns its main line plus every continuation line that belonged to
    it (tracebacks, wrapped messages). ``id`` is synthetic but stable, so the
    detail/context endpoints can resolve an entry without an index.

    Attributes:
        id:               ``<source>#<line index>`` — stable per file position.
        timestamp:        Raw timestamp as printed in the log header (``""`` if none).
        ts_ms:            ``timestamp`` in epoch milliseconds (UTC-naive treated
                          as UTC), or ``None`` when it cannot be parsed.
        level:            Canonical level, see :func:`normalize_level`.
        logger:           Logger name from the ``[logger:line]`` header (``""`` if none).
        module:           Module/logger part of the header (``None`` if none).
        line:             Source line number from the header (``None`` if none).
        message:          Message body, continuation lines joined with ``\\n``.
        fields:           Structured extras (``request``, ``profiler``, JSON keys, …).
        continuation_lines: How many lines were appended to ``message``.
        source:           Logical source id the entry was read from (file name).
        raw:              Verbatim text of the entry (main + continuation lines).
    """

    id: str = Field(description="Stable synthetic id: '<source>#<line index>'.")
    timestamp: str = Field(default="", description="Timestamp as printed in the log.")
    ts_ms: Optional[int] = Field(
        default=None,
        description="Timestamp in epoch milliseconds (UTC), None when unparsable.",
    )
    level: str = Field(default="unknown", description="Canonical level name.")
    logger: str = Field(default="", description="Logger name from the header.")
    module: Optional[str] = Field(default=None, description="Module from the header.")
    line: Optional[int] = Field(default=None, description="Source line number.")
    message: str = Field(default="", description="Message body with continuations.")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured extras extracted from the entry.",
    )
    continuation_lines: int = Field(
        default=0,
        description="Number of lines appended to the message.",
    )
    source: str = Field(default="", description="Source id (log file name).")
    raw: str = Field(default="", description="Verbatim text of the entry.")
