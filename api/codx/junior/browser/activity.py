"""
Browser activity log — append-only audit trail for co-browsing sessions.

Every action performed on a browser session is written as one JSON object per
line to::

    .codx/browser_sessions/<session_id>/activity.jsonl

Both actors are recorded:

* ``ai``   – a tool call executed by SmolAgent (or another agent).
* ``user`` – an action sent from the web UI / REST API.
* ``system`` – lifecycle events (create / stop / restart / delete / errors).

The log is intentionally independent from the AI analytics store: analytics
tracks LLM token usage and cost, while this file answers "what happened in this
browser, when, and who did it".  The API exposes it through
``GET /api/browsers/{id}/activity`` and joins it with the AI cost of every chat
connected to the session in ``GET /api/browsers/{id}/cost``.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACTIVITY_FILE = "activity.jsonl"
MAX_READ_LINES = 2000


def _now() -> str:
    """Return a timezone-aware ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


class BrowserActivityLog:
    """Append-only per-session activity log."""

    def __init__(self, session_folder: str):
        """
        Args:
            session_folder: Folder that owns the ``activity.jsonl`` file.
        """
        self.session_folder = session_folder
        self.path = os.path.join(session_folder, ACTIVITY_FILE)

    def record(
        self,
        *,
        actor: str,
        action: str,
        success: bool = True,
        session_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        title: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        result: Any = None,
    ) -> Dict[str, Any]:
        """
        Append one activity entry.

        The method is best-effort: logging failures are reported but never
        break a browser action.

        Returns:
            The entry that was written.
        """
        entry: Dict[str, Any] = {
            "ts": _now(),
            "session_id": session_id,
            "chat_id": chat_id,
            "actor": actor or "system",
            "action": action,
            "success": bool(success),
            "params": _safe_params(params, action),
            "url": url,
            "title": title,
            "duration_ms": round(float(duration_ms), 2) if duration_ms is not None else None,
            "error": error,
        }
        if result is not None:
            try:
                serialized = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                serialized = str(result)
            entry["result"] = serialized[:4000]

        try:
            os.makedirs(self.session_folder, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as ex:
            logger.warning("Could not write browser activity log '%s': %s", self.path, ex)
        return entry

    def read(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Return the most recent *limit* entries (oldest → newest).

        The file is read tail-first: opening it and calling ``readlines()``
        loads the whole audit trail, which grows with every click the user makes
        in the live view.  This implementation seeks from the end and reads
        only as many blocks as needed.
        """
        if not os.path.isfile(self.path):
            return []
        limit = max(1, min(int(limit or 100), MAX_READ_LINES))
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                block = min(64 * 1024, max(1, end))
                data = b""
                while end > 0 and data.count(b"\n") <= limit:
                    end -= block
                    handle.seek(end)
                    data = handle.read(block) + data
                    block = min(block * 2, 4 * 1024 * 1024)
            lines = data.decode("utf-8", errors="replace").splitlines()[-limit:]
        except Exception as ex:
            logger.warning("Could not read browser activity log '%s': %s", self.path, ex)
            return []

        entries: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Old logs may predate value redaction: sanitize on read too.
            entry["params"] = _safe_params(entry.get("params"), entry.get("action"))
            entries.append(entry)
        return entries


#: Actions whose ``value`` is user input and may contain a password/token.
_SENSITIVE_VALUE_ACTIONS = {"fill", "type", "select", "press", "insert_text", "clipboard_set"}


def _safe_nested(value: Any, depth: int = 0) -> Any:
    """Sanitize a nested value while keeping its JSON shape.

    The recorder's ``target`` is an object.  It used to be stored as
    ``str(dict)``, which made the audit trail unreadable for tools and made
    recording→script conversion parse Python literals.  Keep dicts/lists as
    data, bound the string lengths and recurse a few levels.
    """
    if depth > 4:
        return str(value)[:2000]
    if isinstance(value, dict):
        return {
            str(key): _safe_nested(item, depth + 1)
            for key, item in list(value.items())[:40]
            if key not in ("settings", "session", "tool_cache")
        }
    if isinstance(value, (list, tuple)):
        return [_safe_nested(item, depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:2000]


def _safe_params(params: Optional[Dict[str, Any]], action: str = "") -> Dict[str, Any]:
    """Trim/redact action parameters before persisting them.

    A password typed by the AI must never be written to ``activity.jsonl`` in
    plaintext.  For input actions the value is replaced with ``"***"`` and its
    length is kept so the audit trail still shows that something was entered.

    Nested objects (the recorder ``target``) keep their shape: tools and the
    recording→script converter need the selector, tag and element text.
    """
    if not params:
        return {}
    action = str(action or "").lower()
    safe: Dict[str, Any] = {}
    for key, value in params.items():
        if key in ("settings", "session", "tool_cache"):
            continue
        if key in ("value", "text") and action in _SENSITIVE_VALUE_ACTIONS:
            text = "" if value is None else str(value)
            safe["value_redacted"] = True
            safe["value_length"] = len(text)
            continue
        if key == "script" and isinstance(value, str):
            # JS may embed credentials; keep enough to audit, not the secret.
            safe[key] = value[:500]
            if len(value) > 500:
                safe["script_truncated"] = True
            continue
        safe[key] = _safe_nested(value)
    return safe


# Made with ❤️ by codx-junior
