"""
Bootstrap router – reports, lists the provider presets and (optionally applies)
the zero-touch AI configuration described in :mod:`codx.junior.bootstrap`.

Route layout
------------
GET  /api/bootstrap           – current configuration state (read only)
GET  /api/bootstrap/presets   – provider preset catalogue (models included)
POST /api/bootstrap/apply     – apply the environment configuration, or an
                                explicit one sent in the body

The ``apply`` endpoint is restricted to loopback clients (the container's own
``docker compose exec`` / health check) unless
``CODX_JUNIOR_BOOTSTRAP_ALLOW_REMOTE=1`` is set: it rewrites the global AI
settings, so it must not be reachable from the network by default.

Presets are the reason the installer only has to know a provider and a key: the
models for every role come from the same catalogue the UI uses, so "what the
installer configured" and "what the user can change later" are one thing.

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codx.junior.bootstrap import (
    bootstrap_ai_from_env,
    build_provider_and_models,
    list_provider_presets,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bootstrap"])

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class BootstrapConfig(BaseModel):
    """
    Explicit AI configuration (installer / first-run wizard).

    Every field is optional: what is not sent falls back to the provider preset
    and then to the environment, so ``POST /bootstrap/apply`` without a body
    keeps working exactly as before.
    """

    provider: Optional[str] = Field(
        default=None, description="Preset name: openai, deepseek, gemini, openrouter, mistral, groq, ollama, custom"
    )
    api_key: Optional[str] = Field(default=None, description="API key of the provider")
    api_url: Optional[str] = Field(default=None, description="OpenAI-compatible base URL (required for 'custom')")
    model: Optional[str] = Field(default=None, description="Chat model id (default: the preset's)")
    wiki_model: Optional[str] = Field(default=None, description="Wiki model id (default: the preset's)")
    rag_model: Optional[str] = Field(default=None, description="RAG model id (default: the chat model)")
    guardrail_model: Optional[str] = Field(default=None, description="Guardrail model id (default: the chat model)")
    embeddings_model: Optional[str] = Field(
        default=None, description="Embeddings model id (default: the preset's; empty disables RAG)"
    )
    embeddings_dim: Optional[int] = Field(default=None, description="Vector size of the embeddings model")
    context_length: Optional[int] = Field(default=None, description="Context window of the chat model")


def _is_allowed(request: Request) -> bool:
    """Allow the write endpoint from loopback (or explicitly from anywhere)."""
    if (os.environ.get("CODX_JUNIOR_BOOTSTRAP_ALLOW_REMOTE") or "").lower() in ("1", "true"):
        return True
    client = getattr(request, "client", None)
    return bool(client and client.host in LOOPBACK_HOSTS)


@router.get("/bootstrap")
def api_bootstrap_status() -> dict:
    """
    Report how the AI provider/models were configured.

    Dry run: nothing is written, so it is safe to call from the UI or from a
    deployment script that wants to know whether the instance is usable.
    """
    summary = bootstrap_ai_from_env(apply=False)
    return {"status": "ok", "bootstrap": summary}


@router.get("/bootstrap/presets")
def api_bootstrap_presets() -> dict:
    """
    List the provider presets **with the models each one defines**.

    This is what the installer prints ("provider deepseek -> deepseek-chat, no
    embeddings") and what a first-run wizard renders as a provider picker. No
    key is ever returned: only the name of the environment variable expected.
    """
    return {"status": "ok", "presets": list_provider_presets()}


@router.post("/bootstrap/apply")
def api_bootstrap_apply(request: Request, config: Optional[BootstrapConfig] = None) -> dict:
    """
    Apply an AI configuration now.

    Without a body it re-applies the environment configuration (used by the demo
    installer after the container is healthy, and by an admin who rotated a key
    without recreating the volume). With a body it configures exactly what was
    sent — provider, key and the models derived from that provider's preset —
    which is how a user switches provider later from the UI.

    Args:
        request: Used for the loopback check.
        config: Optional explicit configuration (installer/wizard payload).

    Returns:
        The applied bootstrap summary (provider, preset, models, slots).
    """
    if not _is_allowed(request):
        raise HTTPException(status_code=403, detail="Bootstrap can only be applied from localhost")

    payload = config.dict(exclude_none=True) if config else None
    summary = bootstrap_ai_from_env(force=True, apply=True, config=payload)
    logger.info("Bootstrap apply requested: %s", summary.get("reason"))
    return {"status": "ok", "bootstrap": summary}


@router.post("/bootstrap/preview")
def api_bootstrap_preview(config: BootstrapConfig) -> dict:
    """
    Resolve (without writing) the provider + models a configuration would create.

    The installer uses it to show the user what it is about to configure: which
    models come from the preset, and whether knowledge/RAG will be available.
    """
    built = build_provider_and_models(config.dict(exclude_none=True))
    if not built:
        return {
            "status": "ok",
            "resolved": None,
            "reason": "the configuration is incomplete (provider, api key or base URL missing)",
        }
    return {"status": "ok", "resolved": built["description"], "preset": built.get("preset", "")}
