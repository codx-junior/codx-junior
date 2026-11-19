"""Tests for recording a co-browsing session into replayable script steps."""

import json
import os

from codx.junior.browser.recording import (
    RecordingState,
    RecordingStore,
    build_steps,
    suggested_title,
)


def _event(action, ts, **params):
    return {
        "ts": ts,
        "actor": "user",
        "action": action,
        "success": True,
        "params": params,
        "url": params.pop("url", None),
    }


def _target(selector=None, **extra):
    data = {"selector": selector}
    data.update(extra)
    return data


def test_build_steps_converts_click_and_typing_and_start_url():
    events = [
        _event("navigate", "2026-01-01T00:00:01+00:00", url="https://example.test/new"),
        _event(
            "click",
            "2026-01-01T00:00:02+00:00",
            target=_target("#title", tag="input", text="Title"),
        ),
        _event(
            "change",
            "2026-01-01T00:00:02.400000+00:00",
            target=_target("#title", tag="input", text="Title"),
            value="Hello world",
        ),
        _event(
            "click",
            "2026-01-01T00:00:04+00:00",
            target=_target("button.publish", tag="button", text="Publish"),
        ),
        _event("navigate", "2026-01-01T00:00:05+00:00", url="https://example.test/posts/1"),
    ]

    result = build_steps(events, start_url="https://example.test/new")

    assert result["steps"] == [
        {"action": "navigate", "url": "https://example.test/new"},
        {"action": "fill", "selector": "#title", "value": "Hello world"},
        {"action": "click", "selector": "button.publish"},
        {"action": "navigate", "url": "https://example.test/posts/1"},
    ]
    assert result["events"] == 5
    assert result["truncated"] is False


def test_focus_click_is_dropped_before_fill_on_same_selector():
    events = [
        _event("click", "2026-01-01T00:00:01+00:00", target=_target("#body")),
        _event("change", "2026-01-01T00:00:01.500000+00:00", target=_target("#body"), value="text"),
    ]
    steps = build_steps(events)["steps"]
    assert steps == [{"action": "fill", "selector": "#body", "value": "text"}]


def test_select_and_sensitive_values():
    events = [
        _event(
            "change",
            "2026-01-01T00:00:01+00:00",
            target=_target("#country", tag="select"),
            value="ES",
        ),
        _event(
            "change",
            "2026-01-01T00:00:02+00:00",
            target=_target("#password", tag="input", sensitive=True),
            value_redacted=True,
            value_length=8,
        ),
    ]
    result = build_steps(events)
    assert {"action": "select", "selector": "#country", "value": "ES"} in result["steps"]
    assert {"action": "fill", "selector": "#password", "value": "{{value}}"} in result["steps"]
    assert result["variables"] == ["value"]


def test_contenteditable_input_event_becomes_fill():
    events = [
        _event(
            "input",
            "2026-01-01T00:00:01+00:00",
            target=_target("div[contenteditable]", tag="div"),
            value="A long note",
        )
    ]
    assert build_steps(events)["steps"] == [
        {"action": "fill", "selector": "div[contenteditable]", "value": "A long note"}
    ]


def test_repeated_identical_events_collapse_and_scroll_is_ignored():
    events = [
        _event("click", "2026-01-01T00:00:01+00:00", target=_target("#save")),
        _event("click", "2026-01-01T00:00:01.200000+00:00", target=_target("#save")),
        _event("scroll", "2026-01-01T00:00:02+00:00", x=0, y=800),
    ]
    result = build_steps(events)
    assert result["steps"] == [{"action": "click", "selector": "#save"}]
    assert result["skipped"] >= 1


def test_legacy_stringified_target_is_understood():
    events = [
        {
            "ts": "2026-01-01T00:00:01+00:00",
            "actor": "user",
            "action": "click",
            "success": True,
            "params": {"target": "{'selector': '#legacy', 'tag': 'button'}"},
        }
    ]
    assert build_steps(events)["steps"][0]["selector"] == "#legacy"


def test_non_user_events_are_ignored():
    events = [
        {"ts": "2026-01-01T00:00:01+00:00", "actor": "ai", "action": "click", "params": {"target": _target("#x")}},
        {"ts": "2026-01-01T00:00:02+00:00", "actor": "user", "action": "click", "success": False, "params": {"target": _target("#x")}},
    ]
    result = build_steps(events)
    assert result["steps"] == []
    assert "nothing replayable" in result["warnings"][0]


def test_recording_state_store_round_trip(tmp_path):
    store = RecordingStore(str(tmp_path))
    state = RecordingState(
        session_id="session-1",
        active=True,
        start_url="https://example.test/new",
        title="Create a post",
        script_id="existing-id",
        tags=["post", "cms"],
    )
    store.save(state)

    loaded = store.load()
    assert loaded.active is True
    assert loaded.title == "Create a post"
    assert loaded.site == "example.test"
    assert loaded.script_id == "existing-id"
    assert loaded.tags == ["post", "cms"]
    assert loaded.elapsed_seconds >= 0

    # The file is a single JSON document next to the activity log.
    assert os.path.isfile(os.path.join(str(tmp_path), "recording.json"))
    with open(store.path, encoding="utf-8") as handler:
        payload = json.load(handler)
    assert payload["session_id"] == "session-1"

    store.clear()
    assert store.load() is None


def test_suggested_title_uses_host():
    assert suggested_title("https://www.example.com/post") == "Recorded flow — example.com"
    assert suggested_title("https://example.com", existing="Keep me") == "Keep me"


def test_activity_log_keeps_the_recorder_target_shape():
    """Recording conversion needs target as data, not ``str(dict)``."""
    from codx.junior.browser.activity import _safe_params

    safe = _safe_params(
        {
            "target": {
                "selector": "#body",
                "tag": "textarea",
                "text": "Post body",
                "sensitive": False,
            },
            "value": "Hello",
        },
        action="change",
    )
    assert isinstance(safe["target"], dict)
    assert safe["target"]["selector"] == "#body"
    assert safe["target"]["text"] == "Post body"
    # A non-sensitive change keeps the value (the recorder already redacts
    # password fields before sending it).
    assert safe["value"] == "Hello"


def test_activity_log_still_redacts_sensitive_actions():
    from codx.junior.browser.activity import _safe_params

    safe = _safe_params({"value": "hunter2", "target": {"selector": "#pass"}}, action="fill")
    assert "value" not in safe
    assert safe["value_redacted"] is True
    assert safe["value_length"] == 7


def test_checkbox_change_becomes_one_click():
    events = [
        _event(
            "click",
            "2026-01-01T00:00:01+00:00",
            target=_target("#publish", tag="input", type="checkbox"),
        ),
        _event(
            "change",
            "2026-01-01T00:00:01.200000+00:00",
            target=_target("#publish", tag="input", type="checkbox"),
            value="on",
        ),
    ]
    assert build_steps(events)["steps"] == [{"action": "click", "selector": "#publish"}]


def test_file_input_is_not_replayed():
    events = [
        _event(
            "change",
            "2026-01-01T00:00:01+00:00",
            target=_target("#image", tag="input", type="file"),
            value="C:\\\\fakepath\\\\image.png",
        )
    ]
    result = build_steps(events)
    assert result["steps"] == []
    assert any("file upload" in warning for warning in result["warnings"])
