"""Effort is configured on profiles, not on models/providers.

The active profiles are the effort selectors (``codx-junior`` → agile,
``codx-junior-max`` → max). Their optional ``effort`` block overrides the tier
defaults, so a project can raise/lower the round and tool budgets without
touching model/provider settings.
"""

import json
from pathlib import Path

import pytest

from codx.junior.ai.smol.effort import effort_for
from codx.junior.chat.chat_engine import ChatEngine
from codx.junior.model.profile import Profile, ProfileEffortSettings


PROFILES = Path(__file__).resolve().parents[3] / "codx" / "junior" / "profiles"


def load_profile(name: str) -> Profile:
    path = PROFILES / name / f"{name}.profile"
    return Profile(**json.loads(path.read_text()))


def test_shipped_agile_profile_defines_its_budget():
    profile = load_profile("codx-junior")
    assert isinstance(profile.effort, ProfileEffortSettings)
    assert profile.effort.max_iterations == 30
    assert profile.effort.max_tool_calls == 8
    assert profile.effort.continuations == 3
    assert profile.effort.reasoning_effort == "high"


def test_shipped_max_profile_defines_a_bigger_budget():
    profile = load_profile("codx-junior-max")
    assert profile.effort.max_iterations == 80
    assert profile.effort.max_tool_calls == 12
    assert profile.effort.validation == "full+coverage+security"
    assert profile.effort.reasoning_effort == "max"


def test_profile_budget_overrides_the_tier_defaults():
    profile = load_profile("codx-junior-max")
    policy = effort_for(
        ["codx-junior-max"],
        settings=profile.effort.model_dump(exclude_none=True),
    )
    assert policy.name == "max"
    assert policy.max_iterations == 80
    assert policy.max_tool_calls == 12
    assert policy.reasoning_effort == "max"


def test_a_custom_profile_can_define_its_own_budget():
    custom = ProfileEffortSettings(max_iterations=50, max_tool_calls=9)
    policy = effort_for([], settings=custom.model_dump(exclude_none=True))
    assert policy.max_iterations == 50
    assert policy.max_tool_calls == 9


def test_max_profile_wins_when_both_harness_profiles_are_active():
    budgets = ChatEngine._resolve_effort_budget(
        [load_profile("codx-junior"), load_profile("codx-junior-max")]
    )
    assert budgets["max_iterations"] == 80
    assert budgets["max_tool_calls"] == 12
    assert budgets["reasoning_effort"] == "max"


def test_profiles_without_effort_contribute_nothing():
    assert ChatEngine._resolve_effort_budget([Profile(name="JobSeeker")]) == {}


def test_ai_headers_carry_the_profile_effort_budget():
    from types import SimpleNamespace

    engine = ChatEngine.__new__(ChatEngine)
    headers = engine._build_ai_headers(
        chat=SimpleNamespace(mode="chat", id="chat-1", run_id=None, run_step_id=None),
        chat_mode="chat",
        is_vibe=False,
        query_mentions=SimpleNamespace(profiles=[]),
        chat_tools=[],
        chat_profile_names=["codx-junior-max"],
        chat_effort={
            "max_iterations": 80,
            "max_tool_calls": 12,
            "reasoning_effort": "max",
        },
    )
    assert json.loads(headers["effort"]) == {
        "max_iterations": 80,
        "max_tool_calls": 12,
        "reasoning_effort": "max",
    }
