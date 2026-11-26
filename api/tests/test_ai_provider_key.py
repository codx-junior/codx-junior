"""API-key resolution guards.

A placeholder key (the literal ``none``, ``sk-REPLACE-ME``...) used to be sent
as a bearer token, producing DeepSeek's opaque
``401 - Authentication Fails, Your api key: none is invalid``. These tests pin
the two safety nets:

* ``get_provider_settings`` normalises placeholders and falls back to the
  provider's environment variable;
* ``SmolAgent`` refuses to call a remote provider without a key instead of
  letting the provider answer an authentication error.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from codx.junior.ai.smol.smol_agent import AIConfigurationError, SmolAgent
from codx.junior.global_settings import (
    get_provider_settings,
    is_placeholder_api_key,
)
from codx.junior.model.ai_model import AILLMModelSettings
from codx.junior.model.model import AIModel, AIProvider, GlobalSettings


def make_settings(api_key: str) -> GlobalSettings:
    return GlobalSettings(
        ai_providers=[
            AIProvider(
                name="deepseek",
                provider="openai",
                api_url="https://api.deepseek.com/v1",
                api_key=api_key,
            )
        ],
        ai_models=[
            AIModel(
                name="deepseek-flash",
                ai_provider="deepseek",
                ai_model="deepseek-flash",
                settings=AILLMModelSettings(),
            )
        ],
    )


@pytest.mark.parametrize(
    "value",
    ["none", "None", "null", "undefined", "sk-REPLACE-ME", "changeme", "${DEEPSEEK_API_KEY}"],
)
def test_placeholder_detection(value):
    assert is_placeholder_api_key(value) is True


@pytest.mark.parametrize("value", ["", "sk-24ec9658d4294dd583a36ab84374a561", "lai-abc"])
def test_real_values_are_not_placeholders(value):
    assert is_placeholder_api_key(value) is False


def test_placeholder_is_never_used_as_a_key(monkeypatch):
    for env_name in ("DEEPSEEK_API_KEY", "CODX_JUNIOR_AI_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)

    provider = get_provider_settings("deepseek", make_settings("none"))

    assert provider.api_key == ""


def test_env_key_is_the_fallback(monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_AI_API_KEY", "sk-from-env")

    provider = get_provider_settings("deepseek", make_settings(""))

    assert provider.api_key == "sk-from-env"


def test_provider_specific_env_key_wins(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-provider-env")

    provider = get_provider_settings("deepseek", make_settings("null"))

    assert provider.api_key == "sk-provider-env"


def test_a_real_key_is_kept(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")

    provider = get_provider_settings("deepseek", make_settings("sk-real"))

    assert provider.api_key == "sk-real"


# ── SmolAgent: fail fast, never send an empty credential ──────────────────────


def agent_settings(api_key: str, api_url: str = "https://api.deepseek.com/v1"):
    settings = Mock()
    settings.project_id = "project-1"
    settings.project_name = "Project"
    settings.get_llm_settings.return_value = SimpleNamespace(
        model="deepseek-flash",
        provider="Deepseek",
        api_key=api_key,
        api_url=api_url,
        system="",
        temperature=0.7,
        tool_timeout_seconds=None,
        tool_parallelism=None,
        input_k_tokens_cxjcoins=0.0,
        output_k_tokens_cxjcoins=0.0,
    )
    return settings


def test_remote_provider_without_a_key_fails_before_the_request(monkeypatch):
    for env_name in ("DEEPSEEK_API_KEY", "CODX_JUNIOR_AI_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)

    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as client:
        with pytest.raises(AIConfigurationError) as error:
            SmolAgent(settings=agent_settings(""), user=None, harness=False)

    client.assert_not_called()
    assert "No API key is configured" in str(error.value)
    assert "Settings > AI Models" in str(error.value)


def test_a_placeholder_key_is_rejected(monkeypatch):
    for env_name in ("DEEPSEEK_API_KEY", "CODX_JUNIOR_AI_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)

    with patch("codx.junior.ai.smol.smol_agent.OpenAI"):
        with pytest.raises(AIConfigurationError):
            SmolAgent(settings=agent_settings("none"), user=None, harness=False)


def test_local_provider_may_run_without_a_key(monkeypatch):
    for env_name in ("DEEPSEEK_API_KEY", "CODX_JUNIOR_AI_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)

    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as client:
        SmolAgent(
            settings=agent_settings("", api_url="http://localai-codx-junior:8080/v1"),
            user=None,
            harness=False,
        )

    assert client.call_args.kwargs["api_key"] == ""


def test_a_valid_key_reaches_the_client():
    with patch("codx.junior.ai.smol.smol_agent.OpenAI") as client:
        SmolAgent(settings=agent_settings("sk-valid"), user=None, harness=False)

    assert client.call_args.kwargs["api_key"] == "sk-valid"


def test_remote_embeddings_without_a_key_are_not_configured():
    from codx.junior.knowledge.knowledge_lancedb import ProviderEmbeddingsModel

    model = ProviderEmbeddingsModel(
        SimpleNamespace(
            api_url="https://api.deepseek.com/v1",
            api_key="",
            model="deepseek-embed",
        )
    )

    assert model.is_configured is False


def test_local_embeddings_may_run_without_a_key():
    from codx.junior.knowledge.knowledge_lancedb import ProviderEmbeddingsModel

    model = ProviderEmbeddingsModel(
        SimpleNamespace(
            api_url="http://localai-codx-junior:8080/v1",
            api_key="",
            model="local-embed",
        )
    )

    assert model.is_configured is True
