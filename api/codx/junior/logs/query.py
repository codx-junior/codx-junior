"""
Loki/Graylog-style query language for the application-log explorer.

The language is deliberately small: every whitespace-separated term is
AND-joined and may be negated with a leading ``-``.

=====================  ======================================================
Term                   Meaning
=====================  ======================================================
``error``              case-insensitive substring on the entry raw text
``"exact phrase"``     case-insensitive substring on the entry raw text
``level=error,warn``   level set membership (levels are normalised)
``logger~"chat_.*"``   case-insensitive regex against the logger name
``field=value``        exact match on ``fields[field]`` or a top-level attribute
``field~"regex"``      regex match on ``fields[field]`` or a top-level attribute
``-<term>``            negates any of the terms above
=====================  ======================================================

A regex that does not compile never raises: a warning is logged, the term is
flagged with its error and it degrades to a case-insensitive substring match.

Diagram:
--------
```mermaid
flowchart LR
    q[query text] --> tokenizer
    tokenizer --> terms
    terms --> predicates
    predicates --> LogQuery
    LogQuery -->|matches| entry[LogEntry]
```
"""

import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from codx.junior.logs.models import normalize_level

logger = logging.getLogger(__name__)

#: Term kinds reported back to the UI.
KIND_TEXT = "text"
KIND_PHRASE = "phrase"
KIND_LEVEL = "level"
KIND_EXACT = "exact"
KIND_REGEX = "regex"

#: Attributes that can be addressed directly with ``name=value``.
TOP_LEVEL_FIELDS = (
    "id",
    "timestamp",
    "ts_ms",
    "level",
    "logger",
    "module",
    "line",
    "message",
    "source",
    "raw",
)

_FIELD_TERM = re.compile(
    r"^(?P<field>[A-Za-z_][\w.\-]*)\s*(?P<op>=|~)\s*(?P<value>.+)$",
    re.DOTALL,
)

Predicate = Callable[[Any], bool]


@dataclass
class QueryTerm:
    """
    A single compiled query term.

    Exposed so the UI can render the parsed query (chips, level filter, …).

    Attributes:
        raw:     The term exactly as written (including the ``-`` and quotes).
        kind:    One of ``text``, ``phrase``, ``level``, ``exact``, ``regex``.
        negated: Whether the term was prefixed with ``-``.
        field:   Field name for ``field=value`` / ``field~regex`` terms.
        value:   Raw value (comma list for level terms).
        values:  Normalised level names for level terms.
        error:   Regex compile error, when the term fell back to a substring.
    """

    raw: str
    kind: str
    negated: bool = False
    field: Optional[str] = None
    value: str = ""
    values: List[str] = dataclasses.field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly representation (used by the API/UI)."""
        return {
            "raw": self.raw,
            "kind": self.kind,
            "negated": self.negated,
            "field": self.field,
            "value": self.value,
            "values": list(self.values),
            "error": self.error,
        }


def tokenize(query: str) -> List[str]:
    """
    Split a query into terms, keeping quoted sections (with their quotes) whole.

    Args:
        query: Raw query text.

    Returns:
        The terms in the order they were written.
    """
    tokens: List[str] = []
    current: List[str] = []
    in_quotes = False
    escaped = False

    for char in query or "":
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and in_quotes:
            current.append(char)
            escaped = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
            continue
        if char.isspace() and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if current:
        tokens.append("".join(current))
    return tokens


def _unquote(value: str) -> str:
    """Strip the surrounding quotes (if any) and unescape ``\\"``."""
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"')


def _lookup_simple(entry: Any, name: str) -> Any:
    """Read a single (non-nested) name from a :class:`LogEntry` or a dict."""
    if isinstance(entry, dict):
        fields = entry.get("fields")
        if isinstance(fields, dict) and name in fields:
            return fields[name]
        return entry.get(name)
    fields = getattr(entry, "fields", None)
    if isinstance(fields, dict) and name in fields:
        return fields[name]
    return getattr(entry, name, None)


def _lookup(entry: Any, name: str) -> Any:
    """
    Read *name* from a :class:`LogEntry` (or a plain dict).

    Structured fields win over attributes, and dotted names (
    ``request.url``) descend into nested dicts.
    """
    value = _lookup_simple(entry, name)
    if value is not None or "." not in name:
        return value

    first, *rest = name.split(".")
    current = _lookup_simple(entry, first)
    for part in rest:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _entry_level(entry: Any) -> str:
    """Return the canonical level of *entry*."""
    return normalize_level(_lookup(entry, "level"))


def _entry_raw(entry: Any) -> str:
    """Return the searchable raw text of *entry*."""
    raw = _lookup(entry, "raw")
    if raw:
        return str(raw)
    message = _lookup(entry, "message")
    return str(message or "")


def _as_strings(value: Any) -> List[str]:
    """Flatten a field value into the strings it can be matched against."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _text_matcher(needle: str) -> Predicate:
    """Case-insensitive substring match on the entry raw text."""
    wanted = needle.casefold()

    def _match(entry: Any) -> bool:
        return wanted in _entry_raw(entry).casefold()

    return _match


def _substring_field_matcher(name: Optional[str], needle: str) -> Predicate:
    """Case-insensitive substring match on a field (raw text when no field)."""
    if not name:
        return _text_matcher(needle)
    wanted = needle.casefold()

    def _match(entry: Any) -> bool:
        candidates = _as_strings(_lookup(entry, name))
        return any(wanted in candidate.casefold() for candidate in candidates)

    return _match


def _exact_matcher(name: str, wanted: str) -> Predicate:
    """Exact (case-insensitive) match on a field or a top-level attribute."""
    target = wanted.casefold()

    def _match(entry: Any) -> bool:
        candidates = _as_strings(_lookup(entry, name))
        return any(candidate.casefold() == target for candidate in candidates)

    return _match


def _regex_matcher(name: str, pattern: "re.Pattern[str]") -> Predicate:
    """Regex search on a field or a top-level attribute."""

    def _match(entry: Any) -> bool:
        return any(pattern.search(candidate) for candidate in _as_strings(_lookup(entry, name)))

    return _match


def _level_matcher(levels: List[str]) -> Predicate:
    """Membership of the canonical levels in *levels*."""
    wanted = set(levels)

    def _match(entry: Any) -> bool:
        return _entry_level(entry) in wanted

    return _match


def _negate(predicate: Predicate) -> Predicate:
    """Invert a predicate (for terms prefixed with ``-``)."""

    def _match(entry: Any) -> bool:
        return not predicate(entry)

    return _match


def _compile_term(token: str) -> Optional[Any]:
    """
    Compile one query token.

    Args:
        token: A token produced by :func:`tokenize`.

    Returns:
        ``(QueryTerm, predicate)`` or ``None`` for an empty token.
    """
    raw = token
    negated = raw.startswith("-") and len(raw) > 1
    body = raw[1:] if negated else raw
    if not body:
        return None

    # Quoted phrase: "exact phrase"
    if body.startswith('"') and body.endswith('"') and len(body) >= 2:
        value = _unquote(body)
        term = QueryTerm(raw=raw, kind=KIND_PHRASE, negated=negated, value=value)
        predicate: Optional[Predicate] = _text_matcher(value) if value else None
        return term, (_negate(predicate) if predicate and negated else predicate)

    match = _FIELD_TERM.match(body)
    if match:
        name = match.group("field")
        operator = match.group("op")
        value = _unquote(match.group("value"))

        if operator == "=" and name.lower() in ("level", "levels"):
            levels = [normalize_level(part.strip()) for part in value.split(",") if part.strip()]
            term = QueryTerm(
                raw=raw,
                kind=KIND_LEVEL,
                negated=negated,
                field="level",
                value=value,
                values=levels,
            )
            predicate = _level_matcher(levels) if levels else None
            return term, (_negate(predicate) if predicate and negated else predicate)

        if operator == "=":
            term = QueryTerm(
                raw=raw,
                kind=KIND_EXACT,
                negated=negated,
                field=name,
                value=value,
            )
            predicate = _exact_matcher(name, value) if value else None
            return term, (_negate(predicate) if predicate and negated else predicate)

        # operator == "~"
        term = QueryTerm(raw=raw, kind=KIND_REGEX, negated=negated, field=name, value=value)
        if not value:
            return term, None
        try:
            predicate = _regex_matcher(name, re.compile(value, re.IGNORECASE))
        except re.error as exc:
            logger.warning("compile_query: invalid regex %r (%s), using substring", value, exc)
            term.error = str(exc)
            predicate = _substring_field_matcher(name, value)
        return term, (_negate(predicate) if negated else predicate)

    # Bare word / free text
    value = _unquote(body)
    term = QueryTerm(raw=raw, kind=KIND_TEXT, negated=negated, value=value)
    predicate = _text_matcher(value) if value else None
    return term, (_negate(predicate) if predicate and negated else predicate)


class LogQuery:
    """
    A compiled log query: the original text, its terms and the predicates.

    Attributes:
        text:  The query exactly as the user typed it (echoed back to the UI).
        terms: The parsed :class:`QueryTerm` objects, in order.
    """

    def __init__(
        self,
        text: str = "",
        terms: Optional[List[QueryTerm]] = None,
        predicates: Optional[List[Predicate]] = None,
    ) -> None:
        self.text = text or ""
        self.terms: List[QueryTerm] = list(terms or [])
        self._predicates: List[Predicate] = list(predicates or [])

    @property
    def is_empty(self) -> bool:
        """``True`` when the query has no effective term (matches everything)."""
        return not self._predicates

    def matches(self, entry: Any) -> bool:
        """
        Return ``True`` when *entry* satisfies every term.

        Args:
            entry: A :class:`~codx.junior.logs.models.LogEntry` or a dict with
                   the same keys (``level``, ``raw``, ``fields``, …).
        """
        return all(predicate(entry) for predicate in self._predicates)

    def __bool__(self) -> bool:
        return not self.is_empty

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"LogQuery(text={self.text!r}, terms={len(self.terms)})"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly representation (echoed by the API)."""
        return {
            "text": self.text,
            "is_empty": self.is_empty,
            "terms": [term.to_dict() for term in self.terms],
        }


def compile_query(query: Optional[str]) -> LogQuery:
    """
    Compile a Loki/Graylog-style query string.

    Args:
        query: Query text, e.g. ``'level=error,warn logger~"chat_.*" -timeout'``.

    Returns:
        A :class:`LogQuery`; an empty or blank query matches every entry.
    """
    text = query or ""
    terms: List[QueryTerm] = []
    predicates: List[Predicate] = []

    for token in tokenize(text):
        compiled = _compile_term(token)
        if compiled is None:
            continue
        term, predicate = compiled
        terms.append(term)
        if predicate is not None:
            predicates.append(predicate)

    return LogQuery(text=text, terms=terms, predicates=predicates)
