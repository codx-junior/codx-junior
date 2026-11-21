"""
Tests for the stop-event compatibility helper.

The background services are started with an ``asyncio.Event`` but several
workers are plain threads written against ``threading.Event``. Calling
``event.wait(timeout)`` on an ``asyncio.Event`` raises ``TypeError`` and killed
those worker threads; ``wait_for_stop`` must support both kinds without raising.

Run with::

    cd api && pytest tests/test_stop_event.py -q
"""

import asyncio
import threading
import time

from codx.junior.utils.stop_event import wait_for_stop


def test_threading_event_timeout_then_set():
    event = threading.Event()

    assert wait_for_stop(event, 0.01) is False
    event.set()
    assert wait_for_stop(event, 0.01) is True


def test_asyncio_event_does_not_raise_and_stops():
    event = asyncio.Event()

    assert wait_for_stop(event, 0.01) is False
    event.set()
    assert wait_for_stop(event, 0.01) is True


def test_asyncio_event_is_waited_with_a_bounded_timeout():
    event = asyncio.Event()
    started = time.monotonic()

    wait_for_stop(event, 0.05)

    assert time.monotonic() - started < 2.0


def test_none_event_sleeps_for_the_timeout():
    started = time.monotonic()

    assert wait_for_stop(None, 0.01) is False
    assert time.monotonic() - started >= 0.005


def test_background_services_hand_workers_a_threading_event(monkeypatch):
    """The synchronous workers must not receive the API's ``asyncio.Event``."""
    monkeypatch.setenv("CODX_JUNIOR_STATIC_FOLDER", "/tmp/codx-junior-static-tests")
    import codx.junior.main  # noqa: F401  (sets the import order the app uses)
    from codx.junior import background

    assert isinstance(background._worker_stop_event(), threading.Event)
