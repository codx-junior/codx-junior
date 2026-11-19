"""
Global settings module for codx-junior.

Provides read/write access to GlobalSettings using GlobalSettingsManager,
which stores each section independently and maintains version history.

Made with ❤️ by codx-junior
"""

import os
import json
import logging
import pathlib
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

from codx.junior.utils.utils import exec_command
from codx.junior.model.model import (
    GlobalSettings,
    AISettings,
    AIModel,
    AIProvider,
    OAuthProvider,
)
# Import from top-level settings_manager to avoid conflict with settings.py package
from codx.junior.settings_manager import GlobalSettingsManager
from codx.junior.security.secret_key import (
    MIN_SECRET_BYTES,
    assess_secret,
    generate_secret,
    secret_fingerprint,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global in-memory state
# ---------------------------------------------------------------------------
GLOBAL_SETTINGS: Optional[GlobalSettings] = None

# ``read_global_settings()`` is called on EVERY authenticated request (see
# ``UserSecurityManager.__init__``) and twice per project discovery. Assembling
# GlobalSettings means reading every section file from disk, so the API paid a
# multi-file read per request just to decode a JWT.
#
# The cache is deliberately short lived (2 s by default) and is invalidated by
# the writers, so a change made by another process (the background worker) is
# picked up almost immediately while the hot path stays in memory.
GLOBAL_SETTINGS_CACHE_SECONDS = float(
    os.environ.get("CODX_JUNIOR_GLOBAL_SETTINGS_CACHE_SECONDS", "2")
)
_GLOBAL_SETTINGS_LOCK = threading.RLock()
_GLOBAL_SETTINGS_CACHE: Optional[GlobalSettings] = None
_GLOBAL_SETTINGS_CACHED_AT = 0.0


def invalidate_global_settings_cache() -> None:
    """Drop the cached GlobalSettings so the next read hits the section files."""
    global _GLOBAL_SETTINGS_CACHE, _GLOBAL_SETTINGS_CACHED_AT
    with _GLOBAL_SETTINGS_LOCK:
        _GLOBAL_SETTINGS_CACHE = None
        _GLOBAL_SETTINGS_CACHED_AT = 0.0

# ---------------------------------------------------------------------------
# Manager singleton
# ---------------------------------------------------------------------------
_manager: Optional[GlobalSettingsManager] = None


def get_manager() -> GlobalSettingsManager:
    """Return the singleton GlobalSettingsManager instance."""
    global _manager
    if _manager is None:
        _manager = GlobalSettingsManager()
    return _manager


# ---------------------------------------------------------------------------
# Legacy single-file path (kept for migration support)
# ---------------------------------------------------------------------------
HOME = os.environ.get("HOME", "/root")
GLOBAL_SETTINGS_FOLDER = os.environ.get("CODX_JUNIOR_CONFIG_FOLDER", HOME)
GLOBAL_SETTINGS_PATH = f"{GLOBAL_SETTINGS_FOLDER}/global_settings.json"

logger.info("GLOBAL_SETTINGS_PATH is: %s", GLOBAL_SETTINGS_PATH)


# ---------------------------------------------------------------------------
# Migration: import legacy single-file settings if section files don't exist
# ---------------------------------------------------------------------------
def _migrate_legacy_settings() -> None:
    """
    If the legacy global_settings.json exists and no section files exist yet,
    split it into section files and preserve the original as a backup.
    """
    manager = get_manager()

    if not os.path.exists(GLOBAL_SETTINGS_PATH):
        return

    # Skip if section files already exist — migration already done
    existing_sections = list(manager.settings_dir.glob("*.json"))
    if existing_sections:
        logger.debug("Section files already exist, skipping legacy migration.")
        return

    logger.info("Migrating legacy global_settings.json to sectioned storage.")
    try:
        with open(GLOBAL_SETTINGS_PATH, encoding="utf-8") as f:
            legacy_data: dict = json.loads(f.read())

        settings = GlobalSettings(**legacy_data)
        write_global_settings(settings)
        logger.info("Migration complete. Legacy file preserved at %s", GLOBAL_SETTINGS_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as ex:
        logger.error("Failed to migrate legacy settings: %s", ex)


# ---------------------------------------------------------------------------
# Core read / write
# ---------------------------------------------------------------------------

def read_global_settings(refresh: bool = False) -> GlobalSettings:
    """
    Read GlobalSettings by assembling all section files via the manager.

    Falls back to defaults for any missing sections. Successful reads are
    memoised for ``GLOBAL_SETTINGS_CACHE_SECONDS`` (see the module constant):
    the result is shared, never copied, and every writer invalidates the cache
    so callers cannot observe a stale object after saving.

    Args:
        refresh: Bypass the cache and read the section files again.

    Returns:
        Assembled GlobalSettings instance.
    """
    global GLOBAL_SETTINGS, _GLOBAL_SETTINGS_CACHE, _GLOBAL_SETTINGS_CACHED_AT

    with _GLOBAL_SETTINGS_LOCK:
        now = time.monotonic()
        if (
            not refresh
            and _GLOBAL_SETTINGS_CACHE is not None
            and GLOBAL_SETTINGS_CACHE_SECONDS > 0
            and (now - _GLOBAL_SETTINGS_CACHED_AT) < GLOBAL_SETTINGS_CACHE_SECONDS
        ):
            GLOBAL_SETTINGS = _GLOBAL_SETTINGS_CACHE
            return _GLOBAL_SETTINGS_CACHE

        manager = get_manager()
        all_data = manager.read_all()

        try:
            settings = GlobalSettings(**all_data) if all_data else GlobalSettings()
        except (ValueError, TypeError) as ex:
            logger.error("Error assembling GlobalSettings from sections: %s", ex)
            settings = GlobalSettings()

        GLOBAL_SETTINGS = settings
        _GLOBAL_SETTINGS_CACHE = settings
        _GLOBAL_SETTINGS_CACHED_AT = now
        return settings


def write_global_settings(global_settings: GlobalSettings) -> None:
    """
    Persist GlobalSettings by writing each field as its own section file.

    Also applies git config side effects when git credentials are present.

    Args:
        global_settings: The settings object to persist.
    """
    global GLOBAL_SETTINGS
    manager = get_manager()

    logger.info("Writing all GlobalSettings sections to %s", manager.settings_dir)

    try:
        # Never persist a weak signing secret: writing the sections is exactly
        # how the old 17-byte "codx-junior-rules" default reached
        # settings/secret.json, and a stale in-memory snapshot must not put it
        # back after ensure_signing_secret() rotated it.
        global_settings.secret = ensure_signing_secret(
            settings=global_settings, persist=False
        )
        settings_dict = global_settings.dict()
        for section, value in settings_dict.items():
            manager.write_section(section, value)

        _apply_git_config(global_settings)
        GLOBAL_SETTINGS = global_settings
        # The object we just wrote IS the fresh state: cache it instead of
        # forcing the next read to go back to disk.
        global _GLOBAL_SETTINGS_CACHE, _GLOBAL_SETTINGS_CACHED_AT
        with _GLOBAL_SETTINGS_LOCK:
            _GLOBAL_SETTINGS_CACHE = global_settings
            _GLOBAL_SETTINGS_CACHED_AT = time.monotonic()
    except (OSError, TypeError) as ex:
        logger.exception(
            "Error saving global settings: %s\n%s", ex, traceback.format_exc()
        )


def write_settings_section(section: str, data: any) -> None:
    """
    Write a single section of GlobalSettings without touching other sections.

    Args:
        section: The field name in GlobalSettings to update.
        data: The new value (Pydantic model, dict, list, or scalar).
    """
    global GLOBAL_SETTINGS
    manager = get_manager()
    logger.info("Writing section '%s'", section)
    manager.write_section(section, data)

    # Refresh in-memory global settings after partial update
    GLOBAL_SETTINGS = read_global_settings(refresh=True)


# ---------------------------------------------------------------------------
# JWT/HMAC signing secret
# ---------------------------------------------------------------------------

# Serialises the (rare) generate-and-persist sequence so the API and the
# background worker, starting at the same time, cannot overwrite each other's
# fresh secret.
_SECRET_LOCK = threading.RLock()


def ensure_signing_secret(
    settings: Optional[GlobalSettings] = None,
    persist: bool = True,
    raise_on_persist_failure: bool = False,
) -> str:
    """
    Return a signing secret that satisfies :mod:`codx.junior.security.secret_key`.

    A missing, shipped-placeholder or too-short secret is replaced by a random
    one and (by default) written to the ``secret`` settings section, so every
    process signs and validates with the same key.

    ``settings`` is updated in place, which keeps the function idempotent for
    long-lived holders such as ``UserSecurityManager``, which keeps a settings
    snapshot for the lifetime of a request.

    Args:
        settings: Settings to inspect; defaults to the cached global settings.
        persist: Store the generated secret so all processes share it.
        raise_on_persist_failure: Raise ``RuntimeError`` when the generated
            secret could not be stored (an ephemeral secret invalidates every
            token on the next restart).

    Returns:
        A secret that assesses as valid.

    Raises:
        RuntimeError: only with ``raise_on_persist_failure`` and a settings
            store that dropped the write.
    """
    global _GLOBAL_SETTINGS_CACHE, _GLOBAL_SETTINGS_CACHED_AT

    with _SECRET_LOCK:
        current = settings or read_global_settings()
        assessment = assess_secret(current.secret)
        if assessment.ok:
            return current.secret

        new_secret = generate_secret()
        logger.warning(
            "JWT signing secret is not usable (%s): generating a random secret of "
            "at least %d bytes. Tokens issued with the previous secret stop "
            "validating, so sessions have to log in again once.",
            assessment.describe(),
            MIN_SECRET_BYTES,
        )
        current.secret = new_secret
        if not persist:
            return new_secret

        get_manager().write_section("secret", new_secret)
        # Drop the cache so the re-read returns what is actually on disk.
        with _GLOBAL_SETTINGS_LOCK:
            _GLOBAL_SETTINGS_CACHE = None
            _GLOBAL_SETTINGS_CACHED_AT = 0.0

        refreshed = read_global_settings(refresh=True)
        if refreshed.secret == new_secret:
            # Keep the caller's snapshot in sync with what was persisted.
            current.secret = new_secret
            logger.info(
                "JWT signing secret generated and persisted (sha256:%s)",
                secret_fingerprint(new_secret),
            )
            return new_secret

        logger.error(
            "JWT signing secret could not be persisted to %s: this process runs "
            "with an ephemeral secret and tokens will not validate after a "
            "restart.",
            get_manager().settings_dir,
        )
        if raise_on_persist_failure:
            raise RuntimeError(
                "JWT signing secret could not be persisted to "
                f"{get_manager().settings_dir}; refusing to run with an "
                "ephemeral signing key (set CODX_JUNIOR_ALLOW_UNPERSISTED_SECRET=1 "
                "to override)."
            )
        return new_secret


def validate_signing_secret_at_startup() -> str:
    """
    Startup guard: never serve requests while signing with a weak key.

    A weak secret is replaced (see :func:`ensure_signing_secret`); the process
    refuses to start only when the replacement cannot be stored and
    ``CODX_JUNIOR_ALLOW_UNPERSISTED_SECRET`` is not set.

    Returns:
        The validated signing secret.

    Raises:
        RuntimeError: when no valid, persisted secret can be obtained.
    """
    allow_unpersisted = os.environ.get(
        "CODX_JUNIOR_ALLOW_UNPERSISTED_SECRET", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    secret = ensure_signing_secret(
        persist=True, raise_on_persist_failure=not allow_unpersisted
    )
    assessment = assess_secret(secret)
    if not assessment.ok:  # defensive: generation cannot produce this
        raise RuntimeError(
            "Refusing to start: JWT signing secret is invalid "
            f"({assessment.describe()})"
        )
    logger.info("JWT signing secret validated: %s", assessment.describe())
    return secret


def get_global_settings() -> Optional[GlobalSettings]:
    """
    Return the current in-memory GlobalSettings instance.

    Returns:
        Current GlobalSettings or None if not yet loaded.
    """
    return GLOBAL_SETTINGS


# ---------------------------------------------------------------------------
# AI provider / model helpers
# ---------------------------------------------------------------------------

def get_provider_settings(ai_provider: str, global_settings: Optional[GlobalSettings] = None) -> AIProvider:
    """
    Retrieve AIProvider settings by provider name.

    Args:
        ai_provider: Name of the AI provider.
        global_settings: Optional settings override; uses global if omitted.

    Returns:
        AIProvider instance with env vars expanded.

    Raises:
        ValueError: If the provider is not found.
    """
    resolved_settings = global_settings or GLOBAL_SETTINGS
    matching = [p for p in resolved_settings.ai_providers if p.name == ai_provider]
    if not matching:
        raise ValueError(f"LLM AI provider not found: {ai_provider}")

    provider = matching[0]
    provider.api_url = os.path.expandvars(provider.api_url or "")
    provider.api_key = os.path.expandvars(provider.api_key or "")
    return provider


def get_model(llm_model: str, global_settings: Optional[GlobalSettings] = None) -> Optional[AIModel]:
    """
    Find an AIModel by name or ai_model identifier.

    Args:
        llm_model: Model name or ai_model identifier.
        global_settings: Optional settings override.

    Returns:
        Matching AIModel or None.
    """
    resolved_settings = global_settings or GLOBAL_SETTINGS
    return next(
        (m for m in resolved_settings.ai_models if m.name == llm_model or m.ai_model == llm_model),
        None,
    )


def save_model(model: AIModel) -> None:
    """
    Upsert an AIModel into the ai_models section.

    Args:
        model: The AIModel to save or update.
    """
    current_settings = read_global_settings()
    updated_models = [m for m in current_settings.ai_models if m.name != model.name] + [model]
    write_settings_section("ai_models", [m.dict() for m in updated_models])


def _resolve_model_price(model: AIModel, provider: AIProvider):
    """
    Resolve token prices with the following priority:
    1. Provider's price_list entry matching the model's ai_model or name.
    2. Provider-level price.
    3. None.

    Args:
        model: The AIModel.
        provider: The AIProvider.

    Returns:
        Tuple of (input_price_per_1k, output_price_per_1k).
    """
    model_id = model.ai_model or model.name
    price_list_entry = next(
        (p for p in (provider.price_list or []) if p.model_name == model_id),
        None,
    )
    if price_list_entry:
        return price_list_entry.input_price_per_1k_tokens, price_list_entry.output_price_per_1k_tokens

    return provider.input_k_tokens_cxjcoins, provider.output_k_tokens_cxjcoins


def _resolve_tool_timeout(model: AIModel, provider: AIProvider) -> Optional[float]:
    """
    Resolve the per-tool execution timeout with priority Model > Provider > None.

    Tool-call/iteration budgets are NOT model settings anymore: they are agent
    effort policy and live on the profile's ``effort`` block (see
    ``codx.junior.ai.smol.effort``). Only the timeout remains a model/provider
    runtime property.

    Args:
        model: The AIModel.
        provider: The AIProvider.

    Returns:
        The configured timeout in seconds, or None when neither level defines it.
    """
    return (
        model.tool_timeout_seconds
        if model.tool_timeout_seconds is not None
        else provider.tool_timeout_seconds
    )


def get_model_settings(llm_model: str, global_settings: Optional[GlobalSettings] = None) -> AISettings:
    """
    Build a fully-resolved AISettings object for the given model name.

    Resolves pricing and tool limits with the following priority:
    - Pricing: Model > Provider price_list > Provider default > None
    - Tool timeout: Model > Provider > None (runtime default applies)
    - Tool/iteration budgets: NOT resolved here; they belong to the effort
      policy (profile ``effort`` block > tier constants).

    Args:
        llm_model: Model name or ai_model identifier.
        global_settings: Optional settings override.

    Returns:
        Populated AISettings instance.

    Raises:
        ValueError: If the model or its provider is not found.
    """
    resolved_settings = global_settings or GLOBAL_SETTINGS
    model = get_model(llm_model, resolved_settings)
    if not model:
        raise ValueError(f"LLM model not found: {llm_model}")

    provider = get_provider_settings(model.ai_provider, global_settings=resolved_settings)
    input_price, output_price = _resolve_model_price(model, provider)
    tool_timeout_seconds = _resolve_tool_timeout(model, provider)

    # Reasoning controls: model > provider > environment > provider default
    # (None). Keeping the fallback empty preserves the previous behaviour for
    # every deployment that does not opt in; DeepSeek users can set
    # ``reasoning_effort="low"`` for a noticeably shorter first token.
    def _first_non_empty(*values) -> Optional[str]:
        for value in values:
            text = str(value or "").strip().lower()
            if text:
                return text
        return None

    reasoning_effort = _first_non_empty(
        getattr(model.settings, "reasoning_effort", None),
        provider.reasoning_effort,
        os.environ.get("CODX_JUNIOR_REASONING_EFFORT"),
    )
    thinking = _first_non_empty(
        getattr(model.settings, "thinking", None),
        provider.thinking,
        os.environ.get("CODX_JUNIOR_THINKING"),
    )

    model_settings = dict(model.settings.__dict__)
    # The resolved values below take precedence; passing the model dict AND the
    # keyword would be a duplicate keyword argument.
    model_settings.pop("reasoning_effort", None)
    model_settings.pop("thinking", None)

    return AISettings(
        **model_settings,
        provider=provider.name,
        provider_type=provider.provider,
        api_url=provider.api_url,
        api_key=provider.api_key,
        model=model.ai_model or model.name,
        model_type=model.model_type,
        system=model.system,
        prompt_template=model.prompt_template,
        input_k_tokens_cxjcoins=input_price,
        output_k_tokens_cxjcoins=output_price,
        url=model.url,
        tool_timeout_seconds=tool_timeout_seconds,
        reasoning_effort=reasoning_effort,
        thinking=thinking,
    )


def _get_oauth_provider_from_env(provider_name: str) -> Optional[OAuthProvider]:
    """
    Attempt to read OAuth provider settings from environment variables.

    Supports the following environment variable naming conventions:
    - CODX_{PROVIDER_NAME}_CLIENT_ID
    - CODX_{PROVIDER_NAME}_SECRET
    - CODX_{PROVIDER_NAME}_TOKEN_URL

    Example for GitHub:
    - CODX_GITHUB_CLIENT_ID
    - CODX_GITHUB_SECRET
    - CODX_GITHUB_TOKEN_URL

    Args:
        provider_name: Name of the OAuth provider (e.g., 'github').

    Returns:
        OAuthProvider instance with environment variables filled, or None if required fields are missing.
    """
    env_prefix = f"CODX_{provider_name.upper()}"
    client_id = os.environ.get(f"{env_prefix}_CLIENT_ID", "")
    secret = os.environ.get(f"{env_prefix}_SECRET", "")
    token_url = os.environ.get(f"{env_prefix}_TOKEN_URL", "")

    # Only return a provider if at least client_id and secret are provided
    if client_id and secret:
        logger.info("Loading OAuth provider '%s' from environment variables", provider_name)
        return OAuthProvider(
            name=provider_name,
            client_id=client_id,
            secret=secret,
            token_url=token_url,
        )

    return None


def get_oauth_provider(oauth_provider: str) -> Optional[OAuthProvider]:
    """
    Retrieve an OAuth provider by name, with environment variable support.

    Priority order:
    1. Environment variables (CODX_{PROVIDER}_CLIENT_ID, CODX_{PROVIDER}_SECRET, CODX_{PROVIDER}_TOKEN_URL)
    2. Settings file (oauth_providers list)

    Args:
        oauth_provider: Name of the OAuth provider (e.g., 'github').

    Returns:
        Matching OAuthProvider with environment variables taking precedence, or None if not found.

    Example:
        # Using environment variables:
        export CODX_GITHUB_CLIENT_ID=your_client_id
        export CODX_GITHUB_SECRET=your_secret
        export CODX_GITHUB_TOKEN_URL=https://github.com/login/oauth/access_token

        # Using settings file (fallback):
        # Define in oauth_providers section of global settings
    """
    # First, try to load from environment variables
    env_provider = _get_oauth_provider_from_env(oauth_provider)
    if env_provider:
        return env_provider

    # Fall back to settings file
    current_settings = read_global_settings()
    return next(
        (p for p in current_settings.oauth_providers if p.name == oauth_provider),
        None,
    )


# ---------------------------------------------------------------------------
# Git config side effects
# ---------------------------------------------------------------------------

def _apply_git_config(global_settings: GlobalSettings) -> None:
    """
    Apply git username and email from settings to the global git config.

    Args:
        global_settings: Settings containing git configuration.
    """
    if global_settings.git.username:
        exec_command(f'git config --global user.name "{global_settings.git.username}"')
    if global_settings.git.email:
        exec_command(f'git config --global user.email "{global_settings.git.email}"')


# ---------------------------------------------------------------------------
# Bootstrap: migrate legacy file then load settings into memory
# ---------------------------------------------------------------------------
_migrate_legacy_settings()
read_global_settings()
# Replace a missing, placeholder or too-short signing secret before any token is
# issued. The background worker imports this module too, so it reads the same
# persisted value instead of generating a secret of its own.
ensure_signing_secret()