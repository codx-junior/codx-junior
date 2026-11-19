"""Turn a human co-browsing session into a reusable browser script.

The coworking screen already records every human action in the in-page
recorder and writes it to ``activity.jsonl``.  That trail is an audit log: it
is ordered, redacted and optimized for reading, not for replay.  This module
adds the missing bridge:

* :class:`RecordingState` remembers that a recording is running and which
  metadata (title, site, target script) the user chose;
* :func:`build_steps` converts the user's activity entries into the same JSON
  step vocabulary used by ``run_browser_script``;
* :class:`RecordingStore` persists the state next to the session so a page
  reload does not lose an in-flight recording.

Conversion rules
----------------
========================  ====================================================
Recorded activity         Script step
========================  ====================================================
``navigate``              ``{"action": "navigate", "url": ...}``
``click``                 ``{"action": "click", "selector": ...}``
``change`` / ``input``    ``{"action": "fill", "selector": ..., "value": ...}``
``change`` on ``select``  ``{"action": "select", "selector": ..., "value": ...}``
``key`` (Enter)           ``{"action": "press", "selector": ..., "key": ...}``
``submit``                (ignored: the click/Enter already submits)
``scroll``               (ignored by default; noisy during normal reading)
``switch_tab``           (ignored; selectors remain valid in the same page)
========================  ====================================================

Secrets never reach the script: the recorder omits password values and the
activity log redacts values of input actions, so a redacted field becomes a
``{{value}}`` placeholder the user can fill at replay time.

The module is deliberately dependency-free (no Playwright, no FastAPI) so the
conversion can be unit tested and reused by tools, the API and the UI.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from codx.junior.browser.scripts import normalize_site

logger = logging.getLogger(__name__)

#: File stored inside ``.codx/browser_sessions/<id>/``.
RECORDING_FILE = "recording.json"

#: A script never needs more steps than the browser batch runner accepts.
MAX_RECORDED_STEPS = 100

#: Two identical events closer than this are one interaction (label + input).
DEDUPE_WINDOW_SECONDS = 1.5

#: A click followed by a change on the same field is just focusing the field.
FOCUS_CLICK_WINDOW_SECONDS = 2.0

#: Activity actions worth converting. Everything else is audit-only.
RECORDABLE_ACTIONS = {
    "navigate",
    "click",
    "change",
    "input",
    "key",
    "press",
    "select",
    "scroll",
    "submit",
    "switch_tab",
}

#: Actions that are intentionally dropped (documented in ``warnings``).
IGNORED_ACTIONS = {"scroll", "switch_tab", "submit", "record_start", "record_stop"}


def _now() -> str:
    """Return a timezone-aware ISO timestamp (microseconds keep ordering)."""
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return *value* as a dict, tolerating the legacy stringified form.

    Older activity logs stored the recorder ``target`` object as a Python
    ``str(dict)``.  ``ast.literal_eval`` recovers those entries without
    executing anything.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _selector(target: Dict[str, Any]) -> Optional[str]:
    selector = target.get("selector")
    if isinstance(selector, str) and selector.strip():
        return selector.strip()
    return None


def _target_label(target: Dict[str, Any]) -> str:
    for key in ("text", "name", "id", "role"):
        value = target.get(key)
        if value:
            return str(value)[:80]
    return ""


def _event_seconds(entry: Dict[str, Any]) -> float:
    """Parse the activity timestamp into epoch seconds (0 when invalid)."""
    raw = entry.get("ts") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _same_step(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    keys = ("action", "selector", "url", "value", "key", "x", "y")
    return all(left.get(key) == right.get(key) for key in keys)


def _extract_events(entries: Iterable[Dict[str, Any]], since: Optional[str]) -> List[Dict[str, Any]]:
    """Keep successful human events newer than *since*."""
    events: List[Dict[str, Any]] = []
    since_value = (since or "").strip()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("actor") or "") != "user":
            continue
        if entry.get("success") is False:
            continue
        if since_value and (entry.get("ts") or "") < since_value:
            continue
        events.append(entry)
    return events


def build_steps(
    entries: Iterable[Dict[str, Any]],
    start_url: Optional[str] = None,
    *,
    include_scroll: bool = False,
    max_steps: int = MAX_RECORDED_STEPS,
) -> Dict[str, Any]:
    """Convert activity *entries* into a replayable script preview.

    Args:
        entries: Activity entries (``manager.get_activity`` output).
        start_url: URL the recording started on.  It becomes the first
            ``navigate`` step so the replay starts from a known page.
        include_scroll: Keep human scrolls as optional steps.
        max_steps: Safety cap for the generated script.

    Returns:
        Dict with ``steps``, ``warnings``, ``events`` (converted count),
        ``skipped``, ``variables`` and ``truncated``.
    """
    events = _extract_events(entries, since=None)
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    variables: List[str] = []
    skipped = 0
    last_step: Optional[Dict[str, Any]] = None
    last_ts = 0.0

    for entry in events:
        action = str(entry.get("action") or "").strip().lower()
        params = _as_dict(entry.get("params"))
        target = _as_dict(params.get("target"))
        selector = _selector(target)
        ts = _event_seconds(entry)
        step: Optional[Dict[str, Any]] = None

        if action == "navigate":
            url = _first(params.get("url"), entry.get("url"))
            if url and not str(url).startswith(("chrome://", "devtools://")):
                step = {"action": "navigate", "url": str(url)}
        elif action == "click":
            if selector:
                step = {"action": "click", "selector": selector}
            else:
                warnings.append(f"click without a stable selector ignored ({_target_label(target) or 'unknown'})")
        elif action in ("change", "input", "select"):
            if not selector:
                warnings.append(f"{action} without a selector ignored ({_target_label(target) or 'unknown'})")
            else:
                raw_value = params.get("value")
                redacted = bool(params.get("value_redacted") or params.get("sensitive")) or target.get("sensitive")
                value = "{{value}}" if redacted or raw_value is None else str(raw_value)
                if "{{value}}" in value or redacted:
                    if "value" not in variables:
                        variables.append("value")
                tag = str(target.get("tag") or "").lower()
                input_type = str(target.get("type") or "").lower()
                if input_type in ("checkbox", "radio"):
                    # ``change`` is the state after the human's click; replay
                    # the click (Playwright cannot "fill" a checkbox).
                    step = {"action": "click", "selector": selector}
                elif input_type == "file":
                    warnings.append("file upload change ignored (replay it manually)")
                else:
                    step_action = "select" if action == "select" or tag == "select" else "fill"
                    step = {"action": step_action, "selector": selector, "value": value}
        elif action in ("key", "press"):
            key = _first(params.get("key"), "Enter")
            if selector and key:
                # Enter may submit a form and change the page; never fail the
                # whole replay if the navigation already happened.
                step = {
                    "action": "press",
                    "selector": selector,
                    "key": str(key),
                    "optional": True,
                }
            else:
                warnings.append("key press without selector ignored")
        elif action == "scroll":
            if include_scroll:
                step = {
                    "action": "scroll",
                    "x": int(params.get("x") or 0),
                    "y": int(params.get("y") or 0),
                    "optional": True,
                }
            else:
                skipped += 1
        elif action in IGNORED_ACTIONS:
            skipped += 1
        else:
            skipped += 1

        if step is None:
            continue

        # The recorder emits label + input clicks; keep the field interaction.
        if step["action"] in ("fill", "select", "press") and last_step:
            if (
                last_step.get("action") == "click"
                and last_step.get("selector") == step.get("selector")
                and ts - last_ts <= FOCUS_CLICK_WINDOW_SECONDS
            ):
                steps.pop()
                last_step = steps[-1] if steps else None

        # Debounced typing / double clicks can look identical: keep the last.
        if last_step and _same_step(last_step, step) and ts - last_ts <= DEDUPE_WINDOW_SECONDS:
            skipped += 1
            continue

        steps.append(step)
        last_step = step
        last_ts = ts

    converted = len(steps)

    # Start each replay from the page where the recording began.
    start_url = (start_url or "").strip()
    if converted and start_url and not start_url.startswith(("about:", "chrome://", "devtools://")):
        first = steps[0] if steps else None
        if not first or first.get("action") != "navigate" or first.get("url") != start_url:
            steps.insert(0, {"action": "navigate", "url": start_url})

    truncated = len(steps) > max_steps
    if truncated:
        warnings.append(
            f"recording longer than {max_steps} steps; kept the first {max_steps} (split it into smaller recipes)"
        )
        steps = steps[:max_steps]

    if not steps or converted == 0:
        warnings.append("nothing replayable was captured; interact with the page and try again")

    return {
        "steps": steps,
        "warnings": warnings,
        "events": len(events),
        "skipped": skipped,
        "variables": variables,
        "truncated": truncated,
    }


def suggested_title(start_url: Optional[str] = None, existing: Optional[str] = None) -> str:
    """Return a short, human title for a recording."""
    existing = (existing or "").strip()
    if existing:
        return existing
    try:
        host = urlparse(start_url or "").hostname or ""
    except Exception:
        host = ""
    host = host[4:] if host.startswith("www.") else host
    return f"Recorded flow — {host}" if host else "Recorded flow"


@dataclass
class RecordingState:
    """Metadata of one in-flight (or last stopped) recording."""

    session_id: str
    active: bool = False
    started_at: str = ""
    stopped_at: Optional[str] = None
    start_url: Optional[str] = None
    start_title: Optional[str] = None
    title: str = ""
    description: str = ""
    site: str = ""
    tags: List[str] = field(default_factory=list)
    script_id: Optional[str] = None
    saved_script_id: Optional[str] = None
    event_count: int = 0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = _now()
        if not self.site:
            self.site = normalize_site(self.start_url)

    @property
    def elapsed_seconds(self) -> int:
        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            ended = (
                datetime.fromisoformat(self.stopped_at.replace("Z", "+00:00"))
                if self.stopped_at
                else datetime.now(timezone.utc)
            )
            return max(0, int((ended - started).total_seconds()))
        except (ValueError, TypeError):
            return 0

    def summary(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "active": bool(self.active),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "start_url": self.start_url,
            "start_title": self.start_title,
            "title": self.title,
            "description": self.description,
            "site": self.site,
            "tags": list(self.tags),
            "script_id": self.script_id,
            "saved_script_id": self.saved_script_id,
            "event_count": int(self.event_count or 0),
            "elapsed_seconds": self.elapsed_seconds,
            "step_count": len(self.steps or []),
        }

    def to_dict(self) -> Dict[str, Any]:
        data = self.summary()
        data["steps"] = list(self.steps or [])
        data["warnings"] = list(self.warnings or [])
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecordingState":
        known = {item.name for item in fields(cls)}
        payload = {key: value for key, value in (data or {}).items() if key in known}
        payload.setdefault("session_id", "")
        payload.setdefault("warnings", [])
        payload.setdefault("steps", [])
        payload.setdefault("tags", [])
        return cls(**payload)


class RecordingStore:
    """Persist recording state in a session folder."""

    def __init__(self, session_folder: str):
        self.session_folder = session_folder
        self.path = os.path.join(session_folder, RECORDING_FILE)
        self._lock = threading.RLock()

    def load(self) -> Optional[RecordingState]:
        try:
            with open(self.path, "r", encoding="utf-8") as handler:
                data = json.load(handler)
            if isinstance(data, dict):
                return RecordingState.from_dict(data)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Invalid recording state %s", self.path, exc_info=True)
        return None

    def save(self, state: RecordingState) -> RecordingState:
        with self._lock:
            os.makedirs(self.session_folder, exist_ok=True)
            descriptor, tmp_path = tempfile.mkstemp(
                dir=self.session_folder, prefix=".recording-", suffix=".json"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handler:
                    json.dump(state.to_dict(), handler, indent=2, ensure_ascii=False)
                    handler.flush()
                    os.fsync(handler.fileno())
                os.replace(tmp_path, self.path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            return state

    def clear(self) -> None:
        with self._lock:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except OSError as ex:
                logger.warning("Could not remove recording state %s: %s", self.path, ex)


def merge_steps_into_state(
    state: Optional[RecordingState],
    entries: Iterable[Dict[str, Any]],
    *,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """Stop helper: build the preview and update *state* in place."""
    events = _extract_events(entries, since=since)
    preview = build_steps(
        events,
        start_url=state.start_url if state else None,
    )
    if state is not None:
        state.active = False
        state.stopped_at = _now()
        state.event_count = preview["events"]
        state.steps = preview["steps"]
        state.warnings = preview["warnings"]
    return preview
