"""Incremental, streaming serialisation of chat documents.

Why this module exists
----------------------
A chat (task card) is ONE JSON file under
``<codx_path>/tasks/<board>/<column>/<slug>.<id>.json``. It is the canonical,
git-tracked, human/LLM-readable artifact, so it must stay a single pretty JSON
document. But the write path used to re-sanitise (CISO) and re-serialise the
WHOLE conversation on every streamed chunk, tool event and read receipt:
``store_chat`` was O(chat size) per message. Measured on real task cards:

* 6 MB card  -> ~1.3 s per ``update_message`` (cold redaction up to 17 s),
* 14 MB card -> ~0.5 s per update with a warm string-level redaction cache.

This module keeps the canonical file **byte-for-byte identical** to
``json.dumps(payload, indent=2)`` while paying only for what changed:

* chat-level fields are sanitised once and memoised,
* every message is sanitised once and memoised as a *pretty JSON fragment*,
* the document is streamed to disk from those cached fragments.

Correctness / safety
--------------------
* The canonical file is still written on every call: this module accelerates
  the work, it never defers or skips it (there is no write-behind window).
* The cache is keyed by the file ``(mtime_ns, size)`` signature, so an
  external change (``git checkout``, editor, another worker, another process)
  invalidates it automatically.
* A memoised value is always the result of :func:`redact_payload`. An incoming
  message is reused only when

  1. it compares EQUAL to the previously *sanitised* dict (it is clean and
     unchanged), or
  2. it compares EQUAL to the previously *raw* input that produced that
     sanitised dict (redaction is deterministic), or
  3. its fingerprint matches the one the index recorded for the message when it
     was sanitised (``known_clean``, survives a process restart).

  A message carrying a secret matches none of them, so it is always sanitised
  again: the cache can never hide a secret.
* Only sanitised JSON documents are cached, never mutable ``Chat``/``Message``
  objects. The cache is populated only *after* a successful write, so it can
  never contain content that is not on disk.
"""
from __future__ import annotations

import copy
import io
import json
import os
import threading
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from codx.junior.chat.chat_fingerprint import message_fingerprint
from codx.junior.security.ciso.payload import redact_payload

#: ``"messages"`` is the only list streamed from per-message fragments.
MESSAGES_KEY = "messages"

#: Environment switch for the per-message sanitisation/serialisation cache.
#: ``0`` disables it (every save sanitises and serialises the whole chat, the
#: pre-refactor behaviour) which is useful to compare timings.
CACHE_ENV = "CODX_JUNIOR_CHAT_DOC_CACHE"
#: Total bytes of memoised fragments/chat fields kept per process.
CACHE_MAX_ENV = "CODX_JUNIOR_CHAT_DOC_CACHE_BYTES"
DEFAULT_CACHE_MAX_BYTES = 64 * 1024 * 1024

#: Environment switch for ``fsync`` on chat writes. Off by default: the write
#: is still atomic (temp file + ``os.replace``) and reaches the page cache, so
#: a process crash can neither corrupt nor lose it; only a machine/power loss
#: can. ``fsync`` measured ~40-120 ms per multi-MB save on the deployment
#: filesystem, which dominated the hot path.
FSYNC_ENV = "CODX_JUNIOR_CHAT_FSYNC"


def fsync_enabled(default: bool = False) -> bool:
    """Return whether chat writes must be ``fsync``-ed to stable storage."""
    raw = os.environ.get(FSYNC_ENV)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def cache_enabled() -> bool:
    """Return whether the per-message document cache is enabled."""
    raw = os.environ.get(CACHE_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "disabled")


def thaw_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a cached payload that a caller may safely mutate.

    Pydantic copies the typed fields (lists of models, scalars) but NOT the
    nested contents of free-form fields (``meta_data``, ``pr_view``,
    ``cost_info``): those would be shared with the cached document, so a caller
    editing ``chat.meta_data`` would silently corrupt the cache and every later
    read. Only those free-form values are deep-copied; the conversation itself
    is passed through untouched (pydantic builds fresh ``Message`` objects).

    :param payload: Document returned by :meth:`ChatDocumentCache.payload`.
    :return: A shallow copy safe to hand to ``Chat(**...)``.
    """
    clone = dict(payload)
    for key in ("meta_data", "pr_view", "check_lists"):
        value = clone.get(key)
        if isinstance(value, (dict, list)):
            clone[key] = copy.deepcopy(value)
    messages = clone.get(MESSAGES_KEY)
    if isinstance(messages, list):
        cloned_messages = []
        for message in messages:
            if not isinstance(message, dict):
                cloned_messages.append(message)
                continue
            item = message
            for key in ("meta_data", "cost_info"):
                value = message.get(key)
                if isinstance(value, (dict, list)):
                    if item is message:
                        item = dict(message)
                    item[key] = copy.deepcopy(value)
            cloned_messages.append(item)
        clone[MESSAGES_KEY] = cloned_messages
    return clone


def file_signature(path: str) -> Optional[Tuple[int, int]]:
    """Return the cheap ``(mtime_ns, size)`` change signature of *path*.

    :param path: File to stat.
    :return: ``(mtime_ns, size)`` or ``None`` when the file cannot be stat'ed
        (missing/permission), which disables caching for it.
    """
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


class EncodedChat(NamedTuple):
    """Result of :meth:`ChatDocumentCache.encode`.

    :ivar payload: Sanitised, JSON-serialisable chat document (messages in
        order).
    :ivar fragments: Pretty JSON fragment of each message, already indented for
        its position inside the ``messages`` array of the document.
    :ivar changed_messages: ``doc_id`` values whose message row must be
        re-indexed, or ``None`` when the whole conversation must be re-indexed
        (cold cache / file changed under us / duplicated message id).
    :ivar redactions: Number of secrets redacted during this encoding.
    """

    payload: Dict[str, Any]
    fragments: List[str]
    changed_messages: Optional[set]
    redactions: int
    raw_messages: Dict[str, Dict[str, Any]]


class _Entry(NamedTuple):
    """A cached, sanitised document and the data needed to reuse it."""

    signature: Tuple[int, int]
    chat_fields: Dict[str, Any]
    messages: List[Tuple[Optional[str], Dict[str, Any], str]]
    payload: Dict[str, Any]
    raw_messages: Dict[str, Dict[str, Any]]
    #: True only when this entry was produced by ``encode``/``write_document``
    #: (i.e. every string in it was sanitised by us). A document READ from disk
    #: is cached for reads with ``verified=False``: it may have been edited
    #: outside the platform, so the write path must not reuse its strings.
    verified: bool = True


def _sanitize_fields(
    mapping: Dict[str, Any], previous_sanitised: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Sanitise only the fields of *mapping* that changed.

    Used for the chat-level fields: a save always changes ``updated_at``, and
    re-scanning the (potentially large) description/history/status history on
    every streamed update was pure waste.
    """
    sanitised: Dict[str, Any] = {}
    redactions = 0
    for field, value in mapping.items():
        if previous_sanitised is not None and previous_sanitised.get(field) == value:
            sanitised[field] = previous_sanitised[field]
            continue
        clean, count = redact_payload(value)
        sanitised[field] = clean
        redactions += count
    return sanitised, redactions


def _sanitize_message_fields(
    message: Dict[str, Any],
    previous_sanitised: Optional[Dict[str, Any]],
    previous_raw: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    """Sanitise only the fields of *message* that actually changed.

    ``tool_events`` (and their inline data URLs, which the string-level
    redaction cache refuses to memoise because copying them verbatim is the
    only correct behaviour) can be megabytes; a streamed update changes
    ``content``. Comparing each field against the previous sanitised/raw copy
    lets the other fields be reused untouched.

    Equality against a *sanitised* previous value means the incoming value is
    already clean; equality against a *raw* previous value means it is the same
    input that produced the sanitised value. Anything else is re-sanitised.

    :param message: Incoming (unsanitised) message dict.
    :param previous_sanitised: Previous sanitised dict of the same message, or
        ``None`` when it is new/changed beyond recognition.
    :param previous_raw: Previous raw (unsanitised) dict, or ``None``.
    :return: ``(sanitised_message, redactions)``.
    """
    if previous_sanitised is None and previous_raw is None:
        return redact_payload(message)
    sanitised: Dict[str, Any] = {}
    redactions = 0
    for field, value in message.items():
        if previous_sanitised is not None and previous_sanitised.get(field) == value:
            sanitised[field] = previous_sanitised[field]
            continue
        if (
            previous_raw is not None
            and previous_raw.get(field) == value
            and previous_sanitised is not None
            and field in previous_sanitised
        ):
            sanitised[field] = previous_sanitised[field]
            continue
        clean, count = redact_payload(value)
        sanitised[field] = clean
        redactions += count
    return sanitised, redactions


def _fragment(message: Dict[str, Any]) -> str:
    """Return the pretty JSON of *message*, indented for the document array.

    ``json.dumps`` writes a message object with a 2-space indent (keys at 2,
    nested values at 4...). Inside the chat document a message sits at depth 2
    (object -> ``messages`` array element), so its keys must be at 6 and its
    first line at 4: prefixing every line by 4 reproduces exactly what
    ``json.dumps(payload, indent=2)`` writes for the same message.
    """
    text = json.dumps(message, indent=2)
    return "\n".join("    " + line for line in text.splitlines())


def _write_messages(handle, messages: Sequence[Any], fragments: Sequence[str]) -> None:
    """Stream the ``messages`` array using pre-rendered fragments."""
    if not messages:
        handle.write("[]")
        return
    handle.write("[")
    for index in range(len(messages)):
        if index:
            handle.write(",")
        handle.write("\n")
        fragment = fragments[index] if index < len(fragments) else _fragment(messages[index])
        handle.write(fragment)
    handle.write("\n  ]")


def _write_document(handle, payload: Dict[str, Any], fragments: Sequence[str]) -> None:
    """Stream *payload* as ``json.dumps(payload, indent=2)`` to *handle*."""
    items = list(payload.items())
    if not items:
        handle.write("{}")
        return
    handle.write("{")
    for index, (key, value) in enumerate(items):
        if index:
            handle.write(",")
        handle.write("\n  ")
        handle.write(json.dumps(key))
        handle.write(": ")
        if key == MESSAGES_KEY and isinstance(value, list):
            _write_messages(handle, value, fragments)
        else:
            # Nested values are indented one level deeper than the key.
            handle.write(json.dumps(value, indent=2).replace("\n", "\n  "))
    handle.write("\n}")


def dump_document(payload: Dict[str, Any], fragments: Sequence[str]) -> str:
    """Return the document text (the same bytes :func:`write_document` writes)."""
    buffer = io.StringIO()
    _write_document(buffer, payload, fragments)
    return buffer.getvalue()


def write_document(path: str, encoded: EncodedChat, fsync: bool = False) -> int:
    """Atomically write *encoded* to *path*.

    The write goes to a sibling temp file and is ``os.replace``-d into place, so
    a concurrent reader sees either the old or the new document, never a
    truncated one.

    :param path: Canonical chat file path.
    :param encoded: Result of :meth:`ChatDocumentCache.encode`.
    :param fsync: Flush to stable storage before the rename (see
        :data:`FSYNC_ENV`).
    :return: Number of bytes written.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    written = 0
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            _write_document(handle, encoded.payload, encoded.fragments)
            handle.flush()
            written = handle.tell()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return written
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:  # pragma: no cover - best effort cleanup
                pass


class ChatDocumentCache:
    """Per-process cache of sanitised chat documents and message fragments.

    Entries are keyed by absolute file path and validated by the file
    signature. The cache is bounded by total fragment/chat-field bytes; it is
    only an accelerator, so evicting an entry costs one full encode and never
    loses data.
    """

    def __init__(self, max_bytes: Optional[int] = None) -> None:
        if max_bytes is None:
            try:
                max_bytes = int(os.environ.get(CACHE_MAX_ENV, DEFAULT_CACHE_MAX_BYTES))
            except (TypeError, ValueError):
                max_bytes = DEFAULT_CACHE_MAX_BYTES
        self._max_bytes = max(0, int(max_bytes))
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._bytes = 0

    # -- introspection -----------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """Return cache diagnostics (enabled, entries, bytes, budget)."""
        with self._lock:
            return {
                "enabled": cache_enabled(),
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_bytes": self._max_bytes,
            }

    def clear(self) -> None:
        """Drop every cached document (tests / cache budget changes)."""
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def invalidate(self, path: str) -> None:
        """Drop the entry of *path* (the file changed under us)."""
        key = os.path.abspath(path)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                self._bytes = max(0, self._bytes - self._cost(entry))

    # -- read path ---------------------------------------------------------
    def payload(
        self, path: str, signature: Optional[Tuple[int, int]]
    ) -> Optional[Dict[str, Any]]:
        """Return the last sanitised payload written to *path*, or ``None``.

        The signature must match the file's current one, so an external change
        automatically invalidates the entry. The returned dict is the cached
        instance: callers MUST treat it as read-only (``load_chat_from_path``
        validates it into a fresh ``Chat`` and pydantic copies the data).
        """
        if signature is None or not cache_enabled():
            return None
        key = os.path.abspath(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.signature != signature:
                return None
            self._entries.move_to_end(key)
            return entry.payload

    def is_warm(self, path: str, signature: Optional[Tuple[int, int]]) -> bool:
        """True when the cache can reuse the messages of *path* on a write.

        Only an entry produced by :meth:`encode`/``remember_written`` counts:
        a document cached from a disk read (``remember_loaded``) is not trusted
        for writes. When this is False the caller may pass ``known_clean`` from
        the index to skip re-sanitising unchanged messages.
        """
        entry = self._entry(path, signature)
        return bool(entry is not None and entry.verified and entry.messages)

    # -- write path --------------------------------------------------------
    def encode(
        self,
        payload: Dict[str, Any],
        path: str,
        signature: Optional[Tuple[int, int]],
        known_clean: Optional[Dict[str, str]] = None,
    ) -> EncodedChat:
        """Sanitise *payload* and render its reusable message fragments.

        :param payload: ``chat.model_dump()`` of the chat being written.
        :param path: Canonical chat file path.
        :param signature: Signature of the file **before** this write (``None``
            when it does not exist yet), used to find the previous encoding.
        :param known_clean: Optional ``{doc_id: fingerprint}`` of messages the
            index already holds in sanitised form. It lets a restarted process
            reuse messages written before it started, without re-scanning them.
            A fingerprint is only trusted when it matches the incoming message,
            so an externally edited (or dirty) message is still sanitised.
        :return: The sanitised document plus everything needed to write it.
        """
        previous = self._entry(path, signature) if self._usable() else None
        if previous is not None and not previous.verified:
            # Cached from a disk read: good for reads, never for reuse when
            # writing (the file could contain externally injected secrets).
            previous = None

        # Chat-level fields (everything but messages). Reuse only when the
        # incoming (unsanitised) fields are EQUAL to the previous sanitised
        # ones: an equal dict is clean, so reusing it cannot hide a secret.
        chat_fields_in = {
            key: value for key, value in payload.items() if key != MESSAGES_KEY
        }
        redactions = 0
        if previous is not None and previous.chat_fields == chat_fields_in:
            chat_fields = previous.chat_fields
        elif previous is not None:
            # Only the fields that changed (typically ``updated_at``) are
            # re-sanitised; the description/history/metadata are reused.
            chat_fields, redactions = _sanitize_fields(
                chat_fields_in, previous.chat_fields
            )
        else:
            chat_fields, redactions = redact_payload(chat_fields_in)

        messages = payload.get(MESSAGES_KEY) or []
        message_dicts = [
            message if isinstance(message, dict) else {} for message in messages
        ]

        # Reuse is keyed by doc_id; duplicated ids are pathological but must
        # not make two different messages share one fragment.
        counts: Dict[str, int] = {}
        for message in message_dicts:
            doc_id = message.get("doc_id")
            if doc_id:
                counts[str(doc_id)] = counts.get(str(doc_id), 0) + 1
        previous_by_id: Dict[str, Tuple[Dict[str, Any], str]] = {}
        if previous is not None:
            for doc_id, sanitised, fragment in previous.messages:
                if doc_id and counts.get(str(doc_id), 0) == 1:
                    previous_by_id[str(doc_id)] = (sanitised, fragment)

        sanitised_messages: List[Dict[str, Any]] = []
        fragments: List[str] = []
        changed: set = set()
        # Raw (unsanitised) inputs that produced a *different* sanitised dict.
        # Keeping them lets the next save recognise an unchanged dirty message
        # (one that carries a secret) instead of re-scanning it every time.
        # They live only in memory and are never written.
        raw_messages: Dict[str, Dict[str, Any]] = {}
        for message in message_dicts:
            doc_id = message.get("doc_id")
            key = str(doc_id) if doc_id else None
            reuse = previous_by_id.get(key) if key else None
            previous_sanitised = reuse[0] if reuse is not None else None
            previous_raw = (
                previous.raw_messages.get(key)
                if previous is not None and key
                else None
            )
            if reuse is not None and (
                previous_sanitised == message or previous_raw == message
            ):
                # Whole message unchanged: reuse its fragment.
                sanitised, fragment = reuse
            elif (
                known_clean is not None
                and key
                and known_clean.get(key) == message_fingerprint(message)
            ):
                # Already sanitised by a previous process (manifest hit): the
                # index has this exact message, so it needs no re-scan and no
                # row update. Render the fragment for the file.
                sanitised = message
                fragment = _fragment(sanitised)
            else:
                # The message changed. Redact only its changed FIELDS: a
                # streamed update touches ``content`` while ``tool_events``
                # (often megabytes, with inline data URLs that the string-level
                # cache refuses to memoise) stay untouched.
                sanitised, count = _sanitize_message_fields(
                    message, previous_sanitised, previous_raw
                )
                redactions += count
                if (
                    previous_sanitised is not None
                    and sanitised == previous_sanitised
                    and reuse is not None
                ):
                    # Same sanitised content, different raw input: the
                    # rendered fragment is still valid.
                    fragment = reuse[1]
                else:
                    fragment = _fragment(sanitised)
                    if key:
                        changed.add(key)
            if key and sanitised != message:
                # Raw (unsanitised) input that produced a different sanitised
                # dict. Keeping it lets the next save recognise an unchanged
                # dirty message instead of re-scanning it. It lives only in
                # memory and is never written.
                raw_messages[key] = message
            sanitised_messages.append(sanitised)
            fragments.append(fragment)

        sanitised_payload: Dict[str, Any] = {}
        for key in payload:
            if key == MESSAGES_KEY:
                sanitised_payload[key] = sanitised_messages
            else:
                sanitised_payload[key] = chat_fields.get(key)

        duplicated = any(count > 1 for count in counts.values())
        # A cold cache without a manifest cannot tell which messages are stale
        # in the index: ask for a full message re-sync. With a manifest (or a
        # warm cache) only the messages we actually sanitised need a row update.
        if duplicated:
            changed_messages = None
        elif previous is None and known_clean is None:
            changed_messages = None
        else:
            changed_messages = changed

        return EncodedChat(
            payload=sanitised_payload,
            fragments=fragments,
            changed_messages=changed_messages,
            redactions=redactions,
            raw_messages=raw_messages,
        )

    def remember_loaded(
        self,
        path: str,
        payload: Dict[str, Any],
        signature: Optional[Tuple[int, int]],
    ) -> None:
        """Cache a document read from disk so the next read skips JSON parsing.

        The document is cached for **reads only**: its messages carry no
        rendered fragments, so the next write still sanitises every message
        (the file could have been edited outside the platform, and the cache
        must never let an unsanitised string be written back). What this buys
        is a fast ``load_chat_from_path`` (the ``GET /api/chats?id=`` path)
        without weakening the CISO boundary.
        """
        if signature is None or not self._usable() or not isinstance(payload, dict):
            return
        chat_fields = {
            key: value for key, value in payload.items() if key != MESSAGES_KEY
        }
        self._store(
            path,
            _Entry(
                signature=signature,
                chat_fields=chat_fields,
                messages=[],
                payload=payload,
                raw_messages={},
                verified=False,
            ),
        )

    def remember_written(
        self,
        path: str,
        encoded: EncodedChat,
        signature: Optional[Tuple[int, int]],
    ) -> None:
        """Cache the encoding of *path* under its **new** file signature.

        Call this only after the document has been written. ``signature`` must
        be the signature the file has now (after ``os.replace``), otherwise
        reads would never hit the entry.
        """
        if signature is None or not self._usable():
            return
        messages = encoded.payload.get(MESSAGES_KEY) or []
        fragments = encoded.fragments
        rows: List[Tuple[Optional[str], Dict[str, Any], str]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                message = {}
            doc_id = message.get("doc_id")
            fragment = (
                fragments[index]
                if index < len(fragments)
                else _fragment(message)
            )
            rows.append((str(doc_id) if doc_id else None, message, fragment))
        chat_fields = {
            key: value for key, value in encoded.payload.items() if key != MESSAGES_KEY
        }
        self._store(
            path,
            _Entry(
                signature=signature,
                chat_fields=chat_fields,
                messages=rows,
                payload=encoded.payload,
                raw_messages=dict(encoded.raw_messages),
            ),
        )

    # -- cache internals ---------------------------------------------------
    def _usable(self) -> bool:
        return cache_enabled() and self._max_bytes > 0

    @staticmethod
    def _cost(entry: _Entry) -> int:
        # Strings dominate both fragments and raw dirty messages; a length-based
        # estimate is enough to bound an accelerator cache.
        raw_bytes = 0
        for raw in entry.raw_messages.values():
            raw_bytes += sum(
                len(value) for value in raw.values() if isinstance(value, str)
            )
        return (
            sum(len(fragment) for _, _, fragment in entry.messages)
            + len(json.dumps(entry.chat_fields))
            + raw_bytes
        )

    def _entry(
        self, path: str, signature: Optional[Tuple[int, int]]
    ) -> Optional[_Entry]:
        if signature is None or not self._usable():
            return None
        key = os.path.abspath(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.signature != signature:
                return None
            self._entries.move_to_end(key)
            return entry

    def _store(self, path: str, entry: _Entry) -> None:
        """Insert *entry* under *path*, evicting the oldest entries if needed."""
        key = os.path.abspath(path)
        cost = self._cost(entry)
        if cost > self._max_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= self._cost(previous)
            self._entries[key] = entry
            self._bytes += cost
            while self._bytes > self._max_bytes and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= self._cost(evicted)
            self._bytes = max(0, self._bytes)


#: Process-wide cache shared by every :class:`~codx.junior.chat_manager.ChatManager`
#: (managers are created per request/session, the files they touch are shared).
DOCUMENT_CACHE = ChatDocumentCache()
