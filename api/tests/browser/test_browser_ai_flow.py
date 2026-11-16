"""Tests for the AI-access fixes that keep a co-driving session fluent.

Covers:
* the short "quiet gap" debounce vs. the 10 s UI cooldown;
* the model-facing decision helper (``ai_may_act_now``);
* top-level ``await`` / statement scripts in ``normalize_script``;
* ``BrowserRuntime.wait_for_change`` (one call instead of polling turns);
* the desktop runtime letting the AI act as soon as the human pauses.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from codx.junior.browser.browser import normalize_script
from codx.junior.browser.model import BrowserSession
from codx.junior.browser.runtime import BrowserRuntime
from codx.junior.computer import runtime as computer_runtime


def _seconds_ago(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def test_quiet_gap_is_shorter_than_the_ui_cooldown():
    session = BrowserSession(name="co-driving")
    session.mark_user_active(seconds=10)
    # The broad cooldown is still open...
    assert session.user_is_active() is True
    # ...but the human stopped 2 s ago, so the AI may interleave one action.
    session.last_user_event_at = _seconds_ago(2.0)
    assert session.user_is_typing(quiet_seconds=1.5) is False
    assert session.ai_may_act_now(quiet_seconds=1.5) is True

    # A key pressed 0.3 s ago still counts as "typing".
    session.last_user_event_at = _seconds_ago(0.3)
    assert session.user_is_typing(quiet_seconds=1.5) is True
    assert session.ai_may_act_now(quiet_seconds=1.5) is False


def test_ai_may_act_now_respects_the_control_lease():
    session = BrowserSession(name="co-driving")
    assert session.ai_may_act_now() is True

    session.set_controller("user", reason="MFA")
    assert session.ai_may_act_now() is False

    session.set_controller("none")
    assert session.ai_may_act_now() is False

    session.set_controller("ai")
    assert session.ai_may_act_now() is True


def test_give_back_to_ai_is_immediate():
    session = BrowserSession(name="co-driving")
    session.mark_user_active(seconds=10)
    assert session.ai_may_act_now() is False

    # "Give back to AI" clears the cooldown; the AI must not wait 1.5 s more.
    session.set_controller("ai")
    assert session.ai_may_act_now() is True


def test_normalize_script_supports_top_level_await():
    expression = normalize_script(
        "const r = await fetch('/api'); return r.status"
    )
    assert expression.startswith("async () =>")
    assert "await fetch" in expression

    expression = normalize_script("await fetch('/api')")
    assert expression == "async () => { await fetch('/api') }"


def test_normalize_script_wraps_single_line_statements():
    # A single-line declaration used to be passed to page.evaluate as an
    # expression and raised "SyntaxError: Unexpected token 'const'".
    assert normalize_script("const x = 1") == "() => { const x = 1 }"
    assert normalize_script("return document.title") == "() => { return document.title }"
    # Functions/arrows are still passed through untouched.
    assert normalize_script("() => Reveal.slide(0)") == "() => Reveal.slide(0)"
    assert normalize_script("async () => document.title") == "async () => document.title"


class _FakeManager:
    def __init__(self, entries=None):
        self.entries = entries or []

    def get_activity(self, session_id, limit=100):
        return self.entries[-limit:]


def _bare_runtime(drain, *, entries=None):
    """Build a BrowserRuntime without connecting Playwright."""
    runtime = object.__new__(BrowserRuntime)
    runtime.session = BrowserSession(name="co-driving")
    runtime.manager = _FakeManager(entries)
    runtime._user_event_seq = 0
    runtime.drain = drain
    return runtime


def test_wait_for_change_returns_as_soon_as_the_human_acts():
    calls = {"n": 0}

    async def drain():
        calls["n"] += 1
        if calls["n"] >= 2:
            runtime._user_event_seq += 1
            runtime.session.mark_user_active(seconds=10)
        return {
            "url": runtime.session.current_url,
            "title": runtime.session.current_title,
            "controller": runtime.session.controller,
        }

    runtime = _bare_runtime(drain, entries=[{"ts": "2000-01-01T00:00:00", "actor": "user"}])
    started = time.monotonic()
    result = asyncio.run(runtime.wait_for_change(timeout_ms=5000))
    elapsed = time.monotonic() - started

    assert result["changed"] is True
    assert result["reason"] == "user"
    assert result["waited_ms"] >= 0
    # It must not sit for the whole timeout once the human acted.
    assert elapsed < 2.0


def test_format_wait_includes_activity_and_page():
    from codx.junior.tools.browser_tools import _format_wait

    text = _format_wait({
        "changed": True,
        "reason": "user",
        "waited_ms": 1200,
        "url": "https://example.test/dashboard",
        "title": "Dashboard",
        "controller": "ai",
        "activity": [{
            "actor": "user", "action": "click", "ts": "2026-01-01T00:00:01+00:00",
            "params": {"target": {"text": "Sign in"}},
        }],
        "page": {"url": "https://example.test/dashboard", "title": "Dashboard",
                 "interactive": [{"ref": "e1", "text": "Save"}], "text": "Hello"},
    })

    assert "page changed (user) after 1200 ms" in text
    assert "user: click · Sign in" in text
    assert "page: " in text and "e1" in text


def test_wait_for_change_times_out_without_failing():
    async def drain():
        return {"url": "", "title": "", "controller": "ai"}

    runtime = _bare_runtime(drain)
    result = asyncio.run(runtime.wait_for_change(timeout_ms=250))

    assert result["changed"] is False
    assert result["reason"] == "timeout"
    assert result["waited_ms"] >= 200


def test_desktop_runtime_acts_after_a_short_pause(monkeypatch):
    class _Backend:
        def __init__(self):
            self.calls = []

        async def run(self, action, **params):
            self.calls.append((action, params))
            return {"action": action}

        async def close(self):
            pass

    class _Manager:
        def __init__(self):
            self.saved = 0
            self.activity = []

        def log_activity(self, session, **kwargs):
            self.activity.append(kwargs)

        def save_session(self, session):
            self.saved += 1

    backend = _Backend()
    monkeypatch.setattr(computer_runtime, "get_backend", lambda session, runtime=None: backend)
    session = BrowserSession(name="desktop", kind="linux")
    session.mark_user_active(seconds=10)
    session.last_user_event_at = _seconds_ago(3.0)

    runtime = computer_runtime.DesktopRuntime(_Manager(), session)
    asyncio.run(runtime.run("click", x=10, y=20, user_wait=1))

    assert backend.calls == [("click", {"x": 10, "y": 20})]
