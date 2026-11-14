"""Tests for the ``needs_user`` attention flag and its one-shot notification.

Covers:
* the persisted ``needs_user`` flag raised by ``request_handoff``;
* the ``False -> True`` guard that keeps one hand-off to ONE notification;
* clearing the flag whenever the control moves (release / take / pause);
* the payload of ``BrowserManager.notify_needs_user``.
"""

import types

from codx.junior.browser.model import BrowserSession
from codx.junior.browser.runtime import BrowserRuntime


def test_needs_user_defaults_to_false():
    session = BrowserSession(name="attention")
    assert session.needs_user is False
    # The flag travels with the session payload used by the client.
    assert session.to_public_dict()["needs_user"] is False


def test_request_handoff_sets_needs_user_only_on_the_transition():
    session = BrowserSession(name="attention")
    assert session.request_handoff("MFA required", chat_id="chat-1") is True
    assert session.needs_user is True
    assert session.controller == "user"
    # Already waiting for the human: no second notification.
    assert session.request_handoff("MFA required", chat_id="chat-1") is False
    assert session.needs_user is True


def test_needs_user_clears_when_control_returns():
    session = BrowserSession(name="attention")
    session.request_handoff("captcha")
    session.set_controller("ai")
    assert session.needs_user is False

    session.request_handoff("captcha")
    session.set_controller("user")  # the human took control
    assert session.needs_user is False

    session.request_handoff("captcha")
    session.set_controller("none")  # paused
    assert session.needs_user is False


def test_needs_user_survives_a_reload():
    session = BrowserSession(name="attention")
    session.request_handoff("payment approval", chat_id="chat-1")
    restored = BrowserSession.from_json(session.to_json())
    assert restored.needs_user is True
    assert restored.handoff_reason == "payment approval"
    # Legacy records without the field stay valid and do not blink.
    legacy = BrowserSession.from_dict({"id": "s1", "name": "old", "controller": "ai"})
    assert legacy.needs_user is False


class _FakeManager:
    """Minimal manager surface used by ``BrowserRuntime.request_handoff``."""

    def __init__(self):
        self.notifications = []
        self.saved = 0
        self.activity = []

    def save_session(self, session):
        self.saved += 1
        return session

    def log_activity(self, session, **kwargs):
        self.activity.append(kwargs)

    def notify_needs_user(self, session, reason=None, chat_id=None):
        self.notifications.append((session.id, reason, chat_id))
        return True

    def get_session(self, session_id):
        return None


def test_runtime_raises_one_notification_per_handoff():
    session = BrowserSession(name="attention")
    manager = _FakeManager()
    runtime = BrowserRuntime(manager, session)

    runtime.request_handoff("MFA required", chat_id="chat-1")
    runtime.request_handoff("MFA required", chat_id="chat-1")
    assert len(manager.notifications) == 1
    assert manager.notifications[0] == (session.id, "MFA required", "chat-1")

    # Control back: the next hand-off is a new transition and notifies again.
    runtime.release(chat_id="chat-1")
    assert session.needs_user is False
    runtime.request_handoff("captcha", chat_id="chat-1")
    assert len(manager.notifications) == 2
    assert session.needs_user is True


def test_notify_needs_user_payload(monkeypatch):
    """``notify_needs_user`` raises one warning notification with the session id."""
    from codx.junior.browser import browser_manager as module

    sent = []

    class FakeEventManager:
        def __init__(self, codx_path=None):
            self.codx_path = codx_path

        def send_notification(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(
        "codx.junior.events.event_manager.EventManager", FakeEventManager
    )

    session = BrowserSession(name="Attention session")
    manager = types.SimpleNamespace(settings=types.SimpleNamespace(codx_path="/tmp/proj"))
    assert module.BrowserManager.notify_needs_user(
        manager, session, reason="captcha", chat_id="chat-1"
    ) is True

    assert len(sent) == 1
    payload = sent[0]
    assert payload["type"] == "notification"
    assert payload["level"] == "warning"
    assert payload["session"]["id"] == session.id
    assert payload["session"]["needs_user"] is True
    assert payload["chat"] == {"id": "chat-1"}
    assert "Attention session" in payload["text"]
    assert "captcha" in payload["text"]
