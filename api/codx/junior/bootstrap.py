"""
Zero-touch AI configuration ("first run bootstrap").

A demo/trial installation must be usable right after

    docker run ... -e CODX_JUNIOR_AI_API_KEY=... codxjunior/codx-junior:demo

Without this module that is not possible: a fresh installation has no usable
model, because the default :class:`GlobalSettings` points at the bundled
``llmfactory``/Ollama provider, which only exists when the ``local-models``
profile (LocalAI) is running.

This module reads provider settings from the environment, creates the
:class:`AIProvider` and its :class:`AIModel` entries and fills the five global
model slots (``llm``/``rag``/``wiki``/``guardrail``/``embeddings``).

Design rules
------------
* **Idempotent and non destructive.** It only runs when no provider other than
  the built-in ``llmfactory`` default has been configured, and never touches an
  installation that was already configured from the UI. ``CODX_JUNIOR_AI_FORCE=1``
  re-applies it (useful to rotate a key).
* **Offline.** No HTTP validation here: the container may start without network
  access. The UI validates the provider when a chat is sent, and
  ``POST /api/bootstrap/apply`` can be used to retry.
* **Never fatal.** Every error is logged; the API keeps booting.

Environment variables (all optional except one provider + one key)
------------------------------------------------------------------
CODX_JUNIOR_AI_PROVIDER              openai | deepseek | gemini | openrouter |
                                     mistral | groq | ollama | anthropic | custom
CODX_JUNIOR_AI_API_KEY               API key. Falls back to the provider's
                                     conventional variable (OPENAI_API_KEY,
                                     DEEPSEEK_API_KEY, GEMINI_API_KEY, ...).
CODX_JUNIOR_AI_API_URL               Override the preset base URL (required for
                                     custom / self-hosted gateways).
CODX_JUNIOR_AI_MODEL                 LLM model id (defaults to the preset).
CODX_JUNIOR_AI_RAG_MODEL             Optional, defaults to the LLM model.
CODX_JUNIOR_AI_WIKI_MODEL            Optional, defaults to the LLM model.
CODX_JUNIOR_AI_GUARDRAIL_MODEL       Optional, defaults to the LLM model.
CODX_JUNIOR_AI_EMBEDDINGS_MODEL      Optional, defaults to the preset. Empty
                                     string disables the local embeddings slot.
CODX_JUNIOR_AI_EMBEDDINGS_DIM        Vector size of the embeddings model.
CODX_JUNIOR_AI_CONTEXT_LENGTH        Context window for the LLM model.
CODX_JUNIOR_AI_BOOTSTRAP             0 disables the automatic bootstrap.
CODX_JUNIOR_AI_FORCE                 1 re-applies even if already configured.

Provider presets
----------------
The provider comes from a **preset catalogue** (:data:`PROVIDER_PRESETS`) that
defines, per provider, the OpenAI-compatible base URL, where to get the key and
**which models to use** for each role (chat / wiki / embeddings) together with
the vector size of the embeddings. The installer therefore only passes
``provider`` + ``api_key``: the models are derived from the preset instead of
being guessed. The same catalogue is exposed to the UI through
``GET /api/bootstrap/presets`` and ``POST /api/bootstrap/apply`` accepts an
explicit configuration, so the user can switch provider or change a model later
without recreating the installation.

Made with ❤️ by codx-junior
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from codx.junior.model.ai_model import (
    AIEmbeddingModelSettings,
    AILLMModelSettings,
    AIModel,
    AIModelType,
    AIProvider,
)

logger = logging.getLogger(__name__)

#: Environment flag that disables the automatic bootstrap.
ENV_ENABLED = "CODX_JUNIOR_AI_BOOTSTRAP"
#: Environment flag that forces the bootstrap on an already configured instance.
ENV_FORCE = "CODX_JUNIOR_AI_FORCE"

#: Provider name used by the built-in default settings (see model/ai_model.py).
DEFAULT_PROVIDER = "llmfactory"

#: Names of the two model entries this module creates. They are also the values
#: written into the global model slots, so the same names must be reused
#: everywhere (see ``global_settings.get_model``: it matches ``name`` or
#: ``ai_model``).
LLM_MODEL_NAME = "knowledge"
EMBEDDINGS_MODEL_NAME = "embeddings"

#: OpenAI-compatible presets. Every entry is reachable through the single
#: ``openai`` python client already used by the API (``AIProvider.api_url`` +
#: ``api_key``), so no provider-specific code path is needed.
PROVIDER_PRESETS: Dict[str, dict] = {
    "openai": {
        "api_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "llm": "gpt-4.1-mini",
        "wiki": "gpt-4.1",
        "embeddings": "text-embedding-3-small",
        "embeddings_dim": 1536,
        "documentation": "https://platform.openai.com/api-keys",
    },
    "deepseek": {
        "api_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "llm": "deepseek-chat",
        "embeddings": "",
        "embeddings_dim": 0,
        "documentation": "https://platform.deepseek.com/api_keys",
    },
    "gemini": {
        "api_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GEMINI_API_KEY",
        "llm": "gemini-2.0-flash",
        "embeddings": "text-embedding-004",
        "embeddings_dim": 768,
        "documentation": "https://aistudio.google.com/app/apikey",
    },
    "openrouter": {
        "api_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "llm": "openai/gpt-4o-mini",
        "embeddings": "",
        "embeddings_dim": 0,
        "documentation": "https://openrouter.ai/keys",
    },
    "mistral": {
        "api_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "llm": "mistral-large-latest",
        "embeddings": "mistral-embed",
        "embeddings_dim": 1024,
        "documentation": "https://console.mistral.ai/api-keys",
    },
    "groq": {
        "api_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "llm": "llama-3.3-70b-versatile",
        "embeddings": "",
        "embeddings_dim": 0,
        "documentation": "https://console.groq.com/keys",
    },
    "ollama": {
        "api_url": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "llm": "llama3.1",
        "embeddings": "nomic-embed-text",
        "embeddings_dim": 768,
        "documentation": "https://ollama.com/download",
    },
}


def provider_preset(name: str) -> Dict[str, Any]:
    """
    Return the preset of a provider (empty dict when unknown).

    Args:
        name: Preset/provider name (``openai``, ``deepseek``, ``custom`` ...).

    Returns:
        The preset dictionary, or an empty dict for an unknown provider.
    """
    return PROVIDER_PRESETS.get((name or "").strip().lower(), {})


def list_provider_presets() -> List[dict]:
    """
    Describe every provider preset, **including the models it will define**.

    This is what an installer or the first-run wizard shows after the user picks
    a provider: the models are not guessed by the caller, they come from the
    preset. No key is ever included.

    Returns:
        One entry per provider with ``name``, ``api_url``, ``api_key_env``,
        ``provider``, ``documentation`` and the ``models`` it configures
        (chat / wiki / embeddings + vector size).
    """
    presets: List[dict] = []
    for name, preset in PROVIDER_PRESETS.items():
        llm = preset.get("llm", "")
        wiki = preset.get("wiki", "") or llm
        embeddings = preset.get("embeddings", "")
        models = [
            {
                "role": "chat",
                "slot": "llm_model",
                "entry": LLM_MODEL_NAME,
                "ai_model": llm,
                "type": AIModelType.llm.value,
            }
        ]
        if wiki and wiki != llm:
            models.append(
                {
                    "role": "wiki",
                    "slot": "wiki_model",
                    "entry": "wiki",
                    "ai_model": wiki,
                    "type": AIModelType.llm.value,
                }
            )
        if embeddings:
            models.append(
                {
                    "role": "embeddings",
                    "slot": "embeddings_model",
                    "entry": EMBEDDINGS_MODEL_NAME,
                    "ai_model": embeddings,
                    "type": AIModelType.embeddings.value,
                    "vector_size": preset.get("embeddings_dim", 0),
                }
            )
        presets.append(
            {
                "name": name,
                "api_url": preset.get("api_url", ""),
                "api_key_env": preset.get("api_key_env", ""),
                "documentation": preset.get("documentation", ""),
                "requires_api_url": name == "custom",
                "knowledge_enabled": bool(embeddings),
                "models": models,
            }
        )
    return presets


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    """Read and strip an environment variable, returning ``default`` if empty."""
    return (os.environ.get(name) or default).strip()


def _value(
    config: Optional[Dict[str, Any]], key: str, env_name: str, default: str = ""
) -> str:
    """
    Resolve one setting, letting an explicit configuration win over the env.

    The installer and the first-run wizard send their settings in the request
    body, while a container started with ``-e`` keeps working unchanged because
    the environment is the fallback.

    Args:
        config: Explicit configuration (may be ``None``).
        key: Key inside ``config`` (e.g. ``"provider"``).
        env_name: Environment variable used as fallback.
        default: Value returned when neither is set.

    Returns:
        The stripped value; never ``None``.
    """
    if config:
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return _env(env_name, default)


def _detect_provider(config: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolve which provider the caller is asking to configure.

    Priority: explicit configuration, ``CODX_JUNIOR_AI_PROVIDER``, then the
    first well-known provider whose API key is present, then nothing.
    """
    name = _value(config, "provider", "CODX_JUNIOR_AI_PROVIDER").lower()
    if name:
        return name

    for preset_name, preset in PROVIDER_PRESETS.items():
        if preset_name == "ollama":
            continue
        if _env(preset["api_key_env"]):
            logger.info("AI bootstrap: detected provider '%s' from %s", preset_name, preset["api_key_env"])
            return preset_name

    if _env("CODX_JUNIOR_AI_API_KEY") or _env("CODX_JUNIOR_AI_API_URL"):
        return "custom"

    return ""


def _resolve_api_key(
    provider: str, preset: dict, config: Optional[Dict[str, Any]] = None
) -> str:
    """Resolve the API key: explicit configuration first, then the env."""
    return _value(config, "api_key", "CODX_JUNIOR_AI_API_KEY") or _env(preset.get("api_key_env", ""))


def _resolve_api_url(
    provider: str, preset: dict, config: Optional[Dict[str, Any]] = None
) -> str:
    """Resolve the base URL: explicit configuration, then the env, then the preset."""
    return _value(config, "api_url", "CODX_JUNIOR_AI_API_URL") or preset.get("api_url", "")


# ---------------------------------------------------------------------------
# Settings builder
# ---------------------------------------------------------------------------

def build_provider_and_models(config: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """
    Build the provider/models description requested by the caller.

    The models are **derived from the provider preset**: the installer only says
    which provider and key to use, and every role (chat / wiki / embeddings) is
    filled from :data:`PROVIDER_PRESETS`. Any role can still be overridden
    explicitly with ``model``, ``wiki_model`` or ``embeddings_model``.

    Args:
        config: Optional explicit configuration (installer/wizard payload).
                Recognised keys: ``provider``, ``api_key``, ``api_url``,
                ``model``, ``wiki_model``, ``rag_model``, ``guardrail_model``,
                ``embeddings_model``, ``embeddings_dim``, ``context_length``.

    Returns:
        ``None`` when nothing asks for a bootstrap, otherwise a dict with the
        ``provider`` (AIProvider), ``models`` (List[AIModel]), the ``slots`` to
        apply on :class:`GlobalSettings` and a readable ``description``.
    """
    provider_name = _detect_provider(config)
    if not provider_name:
        return None

    preset = provider_preset(provider_name)
    api_url = _resolve_api_url(provider_name, preset, config)
    api_key = _resolve_api_key(provider_name, preset, config)

    if not api_url:
        logger.warning(
            "AI bootstrap: provider '%s' needs CODX_JUNIOR_AI_API_URL (OpenAI-compatible '/v1' endpoint). Skipping.",
            provider_name,
        )
        return None

    if provider_name == "anthropic":
        # Anthropic does not expose an OpenAI-compatible endpoint: it needs the
        # litellm sidecar (or any compatible gateway) in front of it.
        logger.warning(
            "AI bootstrap: 'anthropic' has no OpenAI-compatible endpoint. "
            "Run an OpenAI-compatible gateway (litellm) and set CODX_JUNIOR_AI_API_URL."
        )
        return None

    if not api_key and provider_name != "ollama":
        logger.warning(
            "AI bootstrap: no API key for provider '%s'. Set CODX_JUNIOR_AI_API_KEY. Skipping.",
            provider_name,
        )
        return None

    context_length = int(_env("CODX_JUNIOR_AI_CONTEXT_LENGTH", "0") or 0)

    llm_model = _value(config, "model", "CODX_JUNIOR_AI_MODEL") or preset.get("llm", "")
    if not llm_model:
        logger.warning("AI bootstrap: provider '%s' needs CODX_JUNIOR_AI_MODEL. Skipping.", provider_name)
        return None

    wiki_model = (
        _value(config, "wiki_model", "CODX_JUNIOR_AI_WIKI_MODEL")
        or preset.get("wiki", "")
        or llm_model
    )
    rag_model = _value(config, "rag_model", "CODX_JUNIOR_AI_RAG_MODEL") or llm_model
    guardrail_model = (
        _value(config, "guardrail_model", "CODX_JUNIOR_AI_GUARDRAIL_MODEL") or llm_model
    )
    embeddings_model = _value(
        config, "embeddings_model", "CODX_JUNIOR_AI_EMBEDDINGS_MODEL"
    ) or preset.get("embeddings", "")
    embeddings_dim = int(
        _value(
            config,
            "embeddings_dim",
            "CODX_JUNIOR_AI_EMBEDDINGS_DIM",
            str(preset.get("embeddings_dim", 0)),
        )
        or 0
    )
    context_length = int(_value(config, "context_length", "CODX_JUNIOR_AI_CONTEXT_LENGTH", "0") or 0)

    provider = AIProvider(
        name=provider_name,
        provider=provider_name,
        api_url=api_url,
        api_key=api_key,
        admin_url=preset.get("admin_url", ""),
    )

    models: List[AIModel] = [
        AIModel(
            name=LLM_MODEL_NAME,
            ai_model=llm_model,
            model_type=AIModelType.llm,
            ai_provider=provider_name,
            settings=AILLMModelSettings(context_length=context_length or 0),
        )
    ]

    slots = {
        "llm_model": LLM_MODEL_NAME,
        "rag_model": LLM_MODEL_NAME,
        "wiki_model": LLM_MODEL_NAME,
        "guardrail_model": LLM_MODEL_NAME,
    }

    # Optional: one model entry per role, so the wiki can use a bigger model
    # than the chat without editing the settings from the UI.
    if wiki_model != llm_model:
        models.append(
            AIModel(
                name="wiki",
                ai_model=wiki_model,
                model_type=AIModelType.llm,
                ai_provider=provider_name,
                settings=AILLMModelSettings(context_length=context_length or 0),
            )
        )
        slots["wiki_model"] = "wiki"

    if embeddings_model:
        models.append(
            AIModel(
                name=EMBEDDINGS_MODEL_NAME,
                ai_model=embeddings_model,
                model_type=AIModelType.embeddings,
                ai_provider=provider_name,
                settings=AIEmbeddingModelSettings(vector_size=embeddings_dim or 1536),
            )
        )
        slots["embeddings_model"] = EMBEDDINGS_MODEL_NAME
    else:
        logger.warning(
            "AI bootstrap: provider '%s' has no embeddings model configured: "
            "knowledge/RAG search stays disabled until one is set "
            "(CODX_JUNIOR_AI_EMBEDDINGS_MODEL).",
            provider_name,
        )

    return {
        "provider": provider,
        "models": models,
        "slots": slots,
        "preset": provider_name if provider_name in PROVIDER_PRESETS else "custom",
        "description": {
            "provider": provider_name,
            "api_url": api_url,
            "llm_model": llm_model,
            "rag_model": rag_model,
            "wiki_model": wiki_model,
            "guardrail_model": guardrail_model,
            "embeddings_model": embeddings_model,
            "embeddings_dim": embeddings_dim,
        },
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def bootstrap_ai_from_env(
    force: bool = False,
    apply: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Configure the AI provider/models from the environment or a explicit config.

    Args:
        force: Re-apply even when a provider is already configured.
        apply: When ``False`` only report what would be configured (dry run).
        config: Optional explicit configuration (installer/wizard payload) that
                takes precedence over the environment variables.

    Returns:
        A JSON-serialisable summary, also persisted for ``GET /api/bootstrap``.
    """
    summary: dict = {
        "enabled": _env(ENV_ENABLED, "1") not in ("0", "false", "no"),
        "applied": False,
        "reason": "",
        "provider": "",
        "preset": "",
        "models": [],
        "models_detail": [],
        "slots": {},
    }

    if not summary["enabled"]:
        summary["reason"] = f"{ENV_ENABLED}=0"
        return summary

    # Only the API process writes settings: the background worker shares the
    # same files and running the bootstrap twice would just add noise.
    if _env("CODX_JUNIOR_API_BACKGROUND"):
        summary["reason"] = "background process"
        return summary

    try:
        from codx.junior.global_settings import (
            read_global_settings,
            write_global_settings,
        )

        settings = read_global_settings()

        already_configured = [
            provider.name
            for provider in settings.ai_providers or []
            if provider.name != DEFAULT_PROVIDER
        ]
        force = force or bool(config) or _env(ENV_FORCE) == "1"

        if already_configured and not force:
            summary["reason"] = f"already configured: {', '.join(already_configured)}"
            summary["provider"] = already_configured[0]
            return summary

        built = build_provider_and_models(config)
        if not built:
            summary["reason"] = "no provider requested in the environment"
            return summary

        provider: AIProvider = built["provider"]
        models: List[AIModel] = built["models"]
        slots: dict = built["slots"]

        summary["provider"] = provider.name
        summary["preset"] = built.get("preset", provider.name)
        summary["models"] = [model.name for model in models]
        summary["models_detail"] = [
            {
                "entry": model.name,
                "ai_model": model.ai_model,
                "type": model.model_type.value,
            }
            for model in models
        ]
        summary["slots"] = slots

        if not apply:
            summary["applied"] = False
            summary["reason"] = "dry run"
            logger.info("AI bootstrap (dry run): %s", json.dumps(built["description"]))
            return summary

        settings.ai_providers = [
            existing for existing in (settings.ai_providers or [])
            if existing.name not in (provider.name, DEFAULT_PROVIDER)
        ] + [provider]

        settings.ai_models = [
            existing for existing in (settings.ai_models or [])
            if existing.ai_provider not in (provider.name, DEFAULT_PROVIDER)
        ] + models

        for slot, value in slots.items():
            setattr(settings, slot, value)

        write_global_settings(settings)

        summary["applied"] = True
        summary["reason"] = "configured from environment"
        logger.info("AI bootstrap applied: %s", json.dumps(built["description"]))
        _write_marker(summary)
        return summary

    except Exception as ex:  # pragma: no cover - defensive, never break boot
        logger.exception("AI bootstrap failed: %s", ex)
        summary["reason"] = f"error: {ex}"
        return summary


def _marker_path() -> str:
    """Path of the last bootstrap summary (inside the settings folder)."""
    home = os.environ.get("HOME", "/tmp")
    folder = os.environ.get("CODX_JUNIOR_CONFIG_FOLDER", home)
    return os.path.join(folder, "bootstrap.json")


def _write_marker(summary: dict) -> None:
    """Persist the summary so the UI/tools can report how the demo was configured."""
    try:
        path = _marker_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    except OSError as ex:
        logger.warning("AI bootstrap: could not write %s: %s", _marker_path(), ex)
