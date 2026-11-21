"""
Compatibility helpers for the stop event shared by background workers.

The API process starts the background services with an ``asyncio.Event``
(``app.APP_STOP_EVENT``), while the worker threads are synchronous and were
written against ``threading.Event``. ``asyncio.Event.wait()`` takes no timeout,
so a worker that called ``stop_event.wait(30)`` raised ``TypeError`` and its
thread died silently on the first tick — that is how the company autopilot and
the media worker stopped running while their container stayed "up".

``wait_for_stop`` accepts either kind: it waits with a timeout when the object
supports it and falls back to polling ``is_set`` otherwise. New code should pass
a :class:`threading.Event` (``background.py`` does), but this helper keeps every
existing caller safe.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import inspect
import time
from typing import Any

#: How often the polling fallback checks ``is_set`` (seconds).
POLL_SECONDS: float = 0.2


def wait_for_stop(event: Any, timeout: float) -> bool:
    """
    Wait up to *timeout* seconds for *event* to be set.

    Args:
        event:   ``threading.Event``, ``asyncio.Event`` or ``None``.
        timeout: Maximum wait in seconds (``<= 0`` only checks the flag once).

    Returns:
        ``True`` when the event is set, ``False`` on timeout.
    """
    if event is None:
        if timeout > 0:
            time.sleep(timeout)
        return False

    wait = getattr(event, "wait", None)
    # ``asyncio.Event.wait`` is a coroutine method: calling it with a timeout
    # raises TypeError, and calling it without one returns a coroutine that
    # would have to be awaited. Skip it and poll ``is_set`` instead.
    if callable(wait) and not inspect.iscoroutinefunction(wait):
        try:
            return bool(wait(timeout))
        except TypeError:
            pass

    is_set = getattr(event, "is_set", None)
    if not callable(is_set):
        if timeout > 0:
            time.sleep(timeout)
        return False

    deadline = time.monotonic() + max(0.0, float(timeout))
    while not is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))
    return True


__all__ = ["POLL_SECONDS", "wait_for_stop"]
