"""Shared co-working: participant presence and multi-human control lease."""

from datetime import datetime, timedelta, timezone

import pytest

from codx.junior.api.session_access import (
    Requester,
    apply_control_action,
    require_user_control,
)
from codx.junior.browser.model import (
    BrowserSession,
    normalize_session_control_mode,
)


def _requester(username="alice", participant_id="tab-alice", admin=False):
    return Requester(
        participant_id=participant_id,
        username=username,
        display_name=username.title() if username else "Guest",
        role="admin" if admin else "user",
        is_admin=admin,
        authenticated=bool(username),
    )


def test_control_mode_defaults_to_auto_and_normalizes():
    assert BrowserSession(name="Linux", kind="linux").control_mode == "auto"
    assert normalize_session_control_mode(None) == "auto"
    assert normalize_session_control_mode("REQUEST") == "request"
    assert normalize_session_control_mode("nonsense") == "auto"


def test_join_is_idempotent_and_heartbeat_refreshes():
    session = BrowserSession(name="Linux", kind="linux")
    first = session.ensure_participant("tab-1", username="alice")
    stamp = first.joined_at
    first.last_seen = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    again = session.ensure_participant("tab-1", username="alice", name="Alice A.")
    assert len(session.participants) == 1
    assert again.id == "tab-1"
    assert again.joined_at == stamp
    assert again.name == "Alice A."
    assert not again.is_stale()


def test_auto_mode_click_takes_the_lease_and_second_click_steals_it():
    session = BrowserSession(name="Linux", kind="linux", control_mode="auto")
    alice = _requester()
    bob = _requester("bob", "tab-bob")

    first = apply_control_action(session, "take_control", alice)
    assert first["state"]["control_owner"] == "alice"
    assert session.controller == "user"
    assert session.user_may_control("alice", "tab-alice")
    assert not session.user_may_control("bob", "tab-bob")

    second = apply_control_action(session, "take_control", bob)
    assert second["state"]["control_owner"] == "bob"
    assert session.user_may_control("bob", "tab-bob")
    assert not session.user_may_control("alice", "tab-alice")


def test_request_mode_asks_the_owner_and_only_owner_approves():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    alice = _requester()
    bob = _requester("bob", "tab-bob")
    apply_control_action(session, "take_control", alice)

    pending = apply_control_action(session, "request_control", bob)
    assert "request sent" in pending["message"].lower()
    assert session.control_request.username == "bob"
    assert session.control_owner == "alice"

    # A third participant cannot resolve it.
    mallory = _requester("mallory", "tab-mallory")
    denied = apply_control_action(session, "approve_request", mallory)
    assert "only alice" in denied["message"].lower()
    assert session.control_owner == "alice"

    approved = apply_control_action(session, "approve_request", alice)
    assert session.control_owner == "bob"
    assert session.control_request is None
    assert session.user_may_control("bob", "tab-bob")
    assert "bob" in approved["message"].lower()


def test_request_mode_when_ai_drives_grants_at_once():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    result = apply_control_action(session, "request_control", _requester("bob", "tab-bob"))
    assert result["state"]["control_owner"] == "bob"
    assert session.control_request is None


def test_deny_keeps_the_current_owner():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    alice = _requester()
    bob = _requester("bob", "tab-bob")
    apply_control_action(session, "take_control", alice)
    apply_control_action(session, "request_control", bob)

    result = apply_control_action(session, "deny_request", alice)
    assert result["state"]["control_request"] is None
    assert session.control_owner == "alice"
    assert not session.user_may_control("bob", "tab-bob")


def test_owner_can_switch_the_mode_and_auto_grants_pending_request():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    alice = _requester()
    bob = _requester("bob", "tab-bob")
    apply_control_action(session, "take_control", alice)
    apply_control_action(session, "request_control", bob)

    result = apply_control_action(session, "set_mode", alice, {"mode": "auto"})
    assert result["state"]["control_mode"] == "auto"
    # The pending request was the click the owner just allowed.
    assert session.control_owner == "bob"
    assert session.control_request is None


def test_give_back_returns_the_lease_to_the_ai():
    session = BrowserSession(name="Linux", kind="linux")
    alice = _requester()
    apply_control_action(session, "take_control", alice)
    result = apply_control_action(session, "give_back", alice)
    assert result["state"]["controller"] == "ai"
    assert session.control_owner is None
    assert session.user_may_control("bob", "tab-bob")


def test_closed_owner_tab_releases_the_lease_after_the_stale_window():
    session = BrowserSession(name="Linux", kind="linux")
    alice = _requester()
    apply_control_action(session, "take_control", alice)
    # Simulate 60 s of silence for alice while bob keeps heartbeating.
    session.participants[0].last_seen = (
        datetime.now(timezone.utc) - timedelta(seconds=60)
    ).isoformat()
    session.ensure_participant("tab-bob", username="bob")
    assert session.controller == "ai"
    assert session.control_owner is None


def test_leaving_owner_releases_immediately():
    session = BrowserSession(name="Linux", kind="linux")
    alice = _requester()
    apply_control_action(session, "take_control", alice)
    session.ensure_participant("tab-bob", username="bob")
    session.leave_participant("tab-alice")
    assert session.controller == "ai"
    assert session.control_owner is None
    assert [p.username for p in session.participants] == ["bob"]


def test_admin_can_act_and_resolve_a_foreign_lease():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    alice = _requester()
    apply_control_action(session, "take_control", alice)
    admin = _requester("root", "tab-root", admin=True)
    assert session.user_may_control("root", "tab-root", is_admin=True)
    result = apply_control_action(session, "give_back", admin)
    assert result["state"]["controller"] == "ai"


def test_require_user_control_rejects_a_second_human():
    from fastapi import HTTPException

    session = BrowserSession(name="Linux", kind="linux")
    apply_control_action(session, "take_control", _requester())
    require_user_control(session, _requester())  # owner passes
    with pytest.raises(HTTPException) as ex:
        require_user_control(session, _requester("bob", "tab-bob"))
    assert ex.value.status_code == 409
    assert "alice" in ex.value.detail.lower()


def test_view_password_is_never_in_the_public_record():
    session = BrowserSession(
        name="Linux",
        kind="linux",
        viewer_password="control-secret",
        viewer_view_password="view-secret",
    )
    public = session.to_public_dict()
    assert "viewer_password" not in public
    assert "viewer_view_password" not in public


def test_a_tab_id_cannot_impersonate_another_account():
    """The x-participant-id header must never authorize a different account."""
    session = BrowserSession(name="Linux", kind="linux", control_mode="auto")
    apply_control_action(session, "take_control", _requester("alice", "tab-alice"))
    impostor = _requester("bob", "tab-alice")  # same tab id, different account
    assert not session.user_may_control(impostor.username, impostor.participant_id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        require_user_control(session, impostor)


def test_control_state_survives_a_session_reload():
    session = BrowserSession(name="Linux", kind="linux", control_mode="request")
    apply_control_action(session, "take_control", _requester("alice", "tab-alice"))
    apply_control_action(session, "request_control", _requester("bob", "tab-bob"))

    reloaded = BrowserSession.from_dict(session.to_dict())
    assert reloaded.control_mode == "request"
    assert reloaded.control_owner == "alice"
    assert reloaded.control_request.username == "bob"
    assert [p.username for p in reloaded.participants] == ["alice", "bob"]
