"""Regression tests for the profile-model fallback.

The process contract is: a profile that does not pin an ``llm_model`` inherits
the model configured for the project (which itself inherits the global
default), and the fallback is reported to the user instead of being silent.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from codx.junior.chat.chat_engine import ChatEngine


def make_profile(name: str, model: str = "") -> SimpleNamespace:
    """Return the profile fields ``_resolve_profiles_and_model`` reads."""
    return SimpleNamespace(
        name=name,
        llm_model=model,
        tools=[],
        parsed_content=f"instructions for {name}",
        chat_mode="",
        effort=None,
        project_id="project-1",
        team=[],
    )


def make_engine(default_model: str = "deepseek-flash") -> ChatEngine:
    """Build a ChatEngine without touching knowledge/RAG/analytics."""
    engine = ChatEngine.__new__(ChatEngine)
    engine.settings = Mock()
    engine.settings.get_llm_settings.return_value = SimpleNamespace(
        model=default_model,
        provider="Deepseek",
        api_key="sk-test",
        api_url="https://api.deepseek.com/v1",
    )
    engine.event_manager = Mock()
    engine.tool_allowlist = None
    # Linked-profile expansion and the TEAM table are orthogonal to the model
    # fallback: keep them deterministic and offline.
    engine._resolve_linked_profiles = lambda profiles: list(profiles)
    engine.get_profile_manager = lambda: SimpleNamespace(
        get_team_profiles=lambda **kwargs: []
    )
    return engine


def resolve(engine: ChatEngine, profiles, chat_model=None):
    chat = SimpleNamespace(
        id="chat-1",
        llm_model=chat_model,
        meta_data={},
    )
    return engine._resolve_profiles_and_model(
        chat=chat,
        query_mentions=SimpleNamespace(profiles=profiles),
        chat_files=[],
        is_refine=True,
    )


def test_profiles_without_model_fall_back_to_the_project_default():
    engine = make_engine("deepseek-flash")

    result = resolve(engine, [make_profile("project"), make_profile("analyst")])

    assert result["chat_model"] == "deepseek-flash"
    assert result["chat_model_source"] == "project_default"
    warning = result["model_warning"]
    assert warning is not None
    assert warning["profiles"] == ["analyst", "project"]
    assert warning["fallback_model"] == "deepseek-flash"
    assert "project default model 'deepseek-flash'" in warning["message"]
    assert "'project'" in warning["message"]
    assert "'analyst'" in warning["message"]


def test_a_profile_with_a_model_wins_and_suppresses_the_warning():
    engine = make_engine("deepseek-flash")

    result = resolve(
        engine,
        [make_profile("project"), make_profile("fast", model="deepseek-v4-pro")],
    )

    assert result["chat_model"] == "deepseek-v4-pro"
    assert result["chat_model_source"] == "profile:fast"
    assert result["model_warning"] is None


def test_chat_model_wins_over_profiles():
    engine = make_engine("deepseek-flash")

    result = resolve(
        engine,
        [make_profile("project", model="deepseek-v4-pro")],
        chat_model="gemini-2.5-flash",
    )

    assert result["chat_model"] == "gemini-2.5-flash"
    assert result["chat_model_source"] == "chat"
    assert result["model_warning"] is None


def test_unresolvable_default_model_keeps_the_warning_off():
    engine = make_engine()
    engine.settings.get_llm_settings.side_effect = ValueError("no model configured")

    result = resolve(engine, [make_profile("project")])

    assert result["chat_model"] is None
    assert result["model_warning"] is None
