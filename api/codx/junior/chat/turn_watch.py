"""
Live state of the project's chat turns, pushed to clients over Socket.IO.

A chat message renders as *"Processing request, please wait..."* while its turn
record is active. The client cannot tell a slow turn from a DEAD one (the
process is alive, the lock is still held, nothing is being produced), so it used
to stay "processing" forever.

This module is the single source of that state:

* :func:`turn_snapshot` builds the state of every active turn of a project
  (including ``stalled`` — no progress for ``stale_seconds`` — and ``alive``),
* :func:`publish_turn_states` broadcasts it as the ``codx-junior-chat-turns``
  event, **only when it changed**, so the browser needs no polling timer.

The event is emitted from the places that move a turn (turn began / finished)
and from every recovery sweep, which is what makes the *stall* transition
visible: nothing else happens inside a hung turn.

Payload::

    {
        "codx_path": "<project .codx path>",
        "stale_seconds": 600.0,
        "ts": 1712345678,          # added by SessionChannel
        "turns": [
            {"turn_id", "chat_id", "status", "attempts", "max_attempts",
             "started_at", "updated_at", "heartbeat_at", "heartbeat_age",
             "stale_seconds", "stalled", "alive", "error", "pid", "host"}
        ]
    }

A turn with ``stalled: true`` (or ``alive: false``) is dead: the client offers
a **re-run** action (``POST /api/chat/turn/rerun``).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Socket.IO event carrying the turn state of a project.
CHAT_TURN_EVENT = "codx-junior-chat-turns"

# Last broadcast payload per project, used to keep quiet when nothing changed.
_SIGNATURES: Dict[str, str] = {}
_SIGNATURES_LOCK = threading.Lock()


def turn_snapshot(codx_path: str) -> Dict[str, Any]:
    """
    Build the live turn state of one project.

    Args:
        codx_path: Project ``.codx`` path.

    Returns:
        ``{"codx_path", "stale_seconds", "turns": [...]}``. ``alive`` is a
        cross-process verdict: a free lock means the owning process is gone.
    """
    from .turn_store import get_turn_store

    store = get_turn_store(codx_path)
    # One probe per turn: `live_turn_ids` claims/releases the free locks, so it
    # answers "is somebody executing this right now?" for any process.
    live = set(store.live_turn_ids())
    turns = [
        store.describe(record, alive=record.turn_id in live)
        for record in store.list_active()
    ]
    return {
        "codx_path": store.codx_path,
        "stale_seconds": round(float(store.stale_seconds), 1),
        "turns": turns,
    }


def publish_turn_states(codx_path: str, *, force: bool = False) -> bool:
    """
    Broadcast the turn state of *codx_path* to every connected client.

    The payload is only emitted when it changed (unless *force*), so a client
    that keeps the socket open receives exactly one event per real transition:
    turn started, progress stopped (stalled), turn finished.

    Args:
        codx_path: Project ``.codx`` path.
        force: Emit even when the payload is identical to the last one.

    Returns:
        True when an event was queued.
    """
    path = str(codx_path or "").strip()
    if not path:
        return False
    try:
        snapshot = turn_snapshot(path)
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("Chat turns: cannot read the state of '%s': %s", path, ex)
        return False

    signature = json.dumps(snapshot, sort_keys=True, default=str)
    key = snapshot["codx_path"]
    with _SIGNATURES_LOCK:
        if not force and _SIGNATURES.get(key) == signature:
            return False
        _SIGNATURES[key] = signature

    try:
        from codx.junior.events.event_manager import EventManager

        EventManager(codx_path=key).chat_turn_event(
            {
                "turns": snapshot["turns"],
                "stale_seconds": snapshot["stale_seconds"],
            }
        )
    except Exception as ex:  # pragma: no cover - never break a turn
        logger.warning("Chat turns: could not publish the state of '%s': %s", key, ex)
        return False
    return True


def reset_turn_signatures(codx_path: Optional[str] = None) -> None:
    """
    Forget the last broadcast payload (used by tests and by a fresh login).

    Args:
        codx_path: Project to forget (all projects when omitted).
    """
    with _SIGNATURES_LOCK:
        if codx_path:
            _SIGNATURES.pop(str(codx_path), None)
        else:
            _SIGNATURES.clear()


# Made with ❤️ by codx-junior
