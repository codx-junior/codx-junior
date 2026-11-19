"""Tests for the AI review of recorded browser sessions."""

from codx.junior.browser.recipe_ai import (
    build_prompt,
    parse_analysis,
    transcript,
)
from codx.junior.browser.recording import RecordingState


def _events():
    return [
        {
            "ts": "2026-01-01T00:00:01+00:00",
            "actor": "user",
            "action": "navigate",
            "url": "https://example.test/posts/new",
            "params": {},
        },
        {
            "ts": "2026-01-01T00:00:02+00:00",
            "actor": "user",
            "action": "click",
            "params": {
                "target": {
                    "selector": "#title",
                    "tag": "input",
                    "type": "text",
                    "name": "Title",
                }
            },
        },
        {
            "ts": "2026-01-01T00:00:03+00:00",
            "actor": "user",
            "action": "change",
            "params": {
                "target": {"selector": "#title", "tag": "input", "name": "Title"},
                "value": "My first post",
            },
        },
        {
            "ts": "2026-01-01T00:00:04+00:00",
            "actor": "user",
            "action": "change",
            "params": {
                "target": {"selector": "#password", "tag": "input", "sensitive": True},
                "value_redacted": True,
            },
        },
    ]


def test_transcript_is_selenium_like_and_redacts_secrets():
    text = transcript(_events())
    assert "navigate" in text
    assert "#title" in text and "My first post" in text
    assert "#password" in text
    assert "{{value}} (redacted)" in text
    assert "My first post" not in text.split("#password")[-1]


def test_build_prompt_includes_existing_recipe_and_baseline():
    state = RecordingState(
        session_id="s1",
        title="Create a post",
        site="example.test",
        start_url="https://example.test/posts/new",
    )

    class Script:
        title = "Create a post"
        site = "example.test"
        description = "old"
        version = 3
        steps = [{"action": "click", "selector": "old"}]

    prompt = build_prompt(
        state,
        _events(),
        [{"action": "fill", "selector": "#title", "value": "My first post"}],
        existing_script=Script(),
    )
    assert "CURRENT RECIPE" in prompt
    assert '"selector": "old"' in prompt
    assert "DETERMINISTIC BASELINE" in prompt
    assert "HUMAN ACTION TRANSCRIPT" in prompt


def test_parse_analysis_accepts_fenced_json_and_normalizes_steps():
    answer = """
```json
{
  "title": "Create a post",
  "description": "Publishes a post",
  "tags": ["cms", "post"],
  "steps": [
    {"action": "navigate", "url": "https://example.test/posts/new"},
    {"action": "fill", "target": {"selector": "#title"}, "value": "Hello"},
    {"action": "click", "selector": "button.publish"}
  ],
  "warnings": ["verify the publish button"],
  "notes": "Dropped the focus click."
}
```
"""
    result = parse_analysis(answer, [{"action": "reload"}])
    assert result["used_ai"] is True
    assert result["title"] == "Create a post"
    assert result["tags"] == ["cms", "post"]
    assert [step["action"] for step in result["steps"]] == ["navigate", "fill", "click"]
    assert result["warnings"] == ["verify the publish button"]
    assert "Dropped" in result["notes"]


def test_parse_analysis_falls_back_on_invalid_json_or_empty_steps():
    fallback = [{"action": "click", "selector": "#save"}]
    for answer in ("not json", '{"steps": []}', '{"steps": "nope"}'):
        result = parse_analysis(answer, fallback, ["baseline warning"])
        assert result["used_ai"] is False
        assert result["steps"] == fallback
        assert result["warnings"] == ["baseline warning"]
        assert result["error"]


def test_analyze_recording_falls_back_when_ai_is_disabled(monkeypatch):
    import asyncio

    from codx.junior.browser import recipe_ai

    monkeypatch.setattr(recipe_ai, "ANALYSIS_ENABLED", False)
    state = RecordingState(session_id="s1", title="Flow", start_url="https://example.test")
    baseline = [{"action": "reload"}]
    result = asyncio.run(
        recipe_ai.analyze_recording(
            settings=object(),
            state=state,
            events=_events(),
            baseline_steps=baseline,
        )
    )
    assert result["used_ai"] is False
    assert result["steps"] == baseline
    assert "disabled" in result["error"]
