"""
First-run workspace setup API (``/api/setup``).

The ``core`` install has no company shell, so the first-run experience is a
workspace wizard: add a project, optionally connect an AI provider, and land in
the project workspace. This router powers that wizard and the always-available
setup assistant.

Endpoints
---------
``GET  /setup/status``     project count, AI readiness, active feature profile
``POST /setup/ai``         save a minimal OpenAI-compatible provider + LLM model
``POST /setup/assistant``  guided answer; AI when configured, preconfigured QA
                           otherwise

The QA fallback is deliberately deterministic: a fresh install must be usable
(and help the user finish setup) before any API key exists.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from codx.junior.api.global_settings import is_admin_user
from codx.junior.features import describe as describe_features
from codx.junior.global_settings import (
    get_model,
    get_provider_settings,
    read_global_settings,
    save_model,
    write_settings_section,
)
from codx.junior.model.ai_model import (
    AILLMModelSettings,
    AIModel,
    AIModelType,
    AIProvider,
)
from codx.junior.model.model import CodxUser
from codx.junior.project.project_discover import find_all_projects
from codx.junior.security.user_management import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

PROVIDER_PRESETS = {
    "deepseek": {"api_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai": {"api_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "openrouter": {"api_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "mistral": {"api_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest"},
    "groq": {"api_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "custom": {"api_url": "", "model": ""},
}


class AISetupRequest(BaseModel):
    provider: str = Field(description="Provider preset or protocol (deepseek, openai, custom, ...)")
    api_key: str = Field(description="Provider API key")
    api_url: str = Field(default="", description="OpenAI-compatible base URL")
    model: str = Field(default="", description="Model id (chat)")


class AssistantRequest(BaseModel):
    message: str = Field(description="User message")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Wizard step, project count, ...")
    history: Optional[List[Dict[str, str]]] = Field(default=None, description="Previous turns")


def _project_count() -> int:
    try:
        return len(list(find_all_projects().values()))
    except Exception as ex:
        logger.warning("setup status: cannot list projects: %s", ex)
        return 0


def _ai_state() -> Dict[str, Any]:
    settings = read_global_settings()
    providers = [p for p in (settings.ai_providers or []) if (p.api_key or "").strip()]
    llm_models = [
        m for m in (settings.ai_models or [])
        if getattr(m, "model_type", None) in (AIModelType.llm, "llm")
    ]
    model_name = (settings.llm_model or "").strip()
    model = get_model(model_name, settings) if model_name else None
    if model is None and llm_models:
        model = llm_models[0]
    provider = None
    if model is not None:
        try:
            provider = get_provider_settings(model.ai_provider, settings)
        except Exception:
            provider = None
    configured = bool(model is not None and provider is not None and (provider.api_key or "").strip())
    return {
        "configured": configured,
        "provider": getattr(provider, "name", "") or "",
        "model": getattr(model, "name", "") or "",
        "provider_count": len(providers),
        "model_count": len(llm_models),
    }


@router.get("/status")
def setup_status(user: CodxUser = Depends(get_authenticated_user)):
    """Return the state the workspace wizard needs to decide what to show."""
    projects = _project_count()
    features = describe_features()
    return {
        "needs_setup": projects == 0,
        "projects": projects,
        "ai": _ai_state(),
        "profile": features.get("profile"),
        "version": features.get("version"),
        "company": features.get("company"),
    }


@router.post("/ai")
def setup_ai(payload: AISetupRequest, user: CodxUser = Depends(is_admin_user)):
    """Save one OpenAI-compatible provider and its chat model.

    This is deliberately the smallest useful configuration: the wizard does not
    need the full provider/model editor, only a working default for chats.
    """
    provider_type = (payload.provider or "").strip().lower()
    api_key = (payload.api_key or "").strip()
    if not provider_type:
        raise HTTPException(status_code=422, detail="Choose a provider.")
    if not api_key:
        raise HTTPException(status_code=422, detail="The provider API key is required.")

    preset = PROVIDER_PRESETS.get(provider_type, PROVIDER_PRESETS["custom"])
    api_url = (payload.api_url or preset["api_url"]).strip()
    model_name = (payload.model or preset["model"]).strip()
    if provider_type == "custom" and not api_url:
        raise HTTPException(status_code=422, detail="A custom provider needs an API URL (ending in /v1).")
    if not model_name:
        raise HTTPException(status_code=422, detail="A chat model id is required.")

    settings = read_global_settings()
    providers = [p for p in (settings.ai_providers or []) if p.name != provider_type]
    providers.append(AIProvider(
        name=provider_type,
        provider=provider_type,
        api_url=api_url,
        api_key=api_key,
    ))
    write_settings_section("ai_providers", [p.dict() for p in providers])

    model = AIModel(
        name=model_name,
        model_type=AIModelType.llm,
        ai_provider=provider_type,
        ai_model=model_name,
        settings=AILLMModelSettings(temperature=1, context_length=0, merge_messages=False),
    )
    save_model(model)
    # Make it the default for chats and knowledge answers. Embeddings stay
    # unset: configuring them is a separate, explicit choice.
    write_settings_section("llm_model", model_name)
    write_settings_section("rag_model", model_name)

    logger.info("setup: AI provider '%s' / model '%s' configured", provider_type, model_name)
    return {"configured": True, "provider": provider_type, "model": model_name, "api_url": api_url}


def _qa_answer(message: str, context: Optional[Dict[str, Any]] = None) -> str:
    """Deterministic setup help used before an AI provider exists."""
    text = (message or "").lower()
    context = context or {}
    step = str(context.get("step") or "")

    def has(*words: str) -> bool:
        return any(word in text for word in words)

    if has("ai", "model", "provider", "api key", "key", "llm"):
        return (
            "To connect a model:\n"
            "1. Pick a provider preset (DeepSeek, OpenAI, OpenRouter, Mistral, Groq) or Custom.\n"
            "2. Paste its API key. The key is sent only to your own codx-junior instance.\n"
            "3. Keep the suggested chat model or type the provider's model id.\n"
            "4. Save. The first model becomes the default for chats and knowledge.\n\n"
            "You can skip this now and use the setup assistant (me) without a model. "
            "Configure AI later from Settings → AI Models."
        )
    if has("project", "repo", "repository", "folder", "clone"):
        return (
            "To add a project:\n"
            "1. Enter a Git URL (for example https://github.com/you/repo.git) or an "
            "absolute folder path on this server.\n"
            "2. Press Add project. codx-junior creates the .codx workspace and "
            "initialises Git if the folder is not a repository yet.\n"
            "3. Select the new project as the workspace and continue.\n\n"
            "The project list updates immediately; if it does not, reload the page. "
            "A project without a model can still be browsed and configured."
        )
    if has("install", "docker", "start", "setup", "begin", "next"):
        return (
            "The first-run flow is: add a project → optionally connect a model → "
            "open the project workspace.\n\n"
            "A project is required. An AI provider is optional; without one the "
            "workspace, files and Git review work, and setup help stays available "
            "here. With a provider, chats can edit and test code."
        )
    if has("error", "500", "empty", "not working", "undefined", "broken"):
        return (
            "If a fresh install looks empty or an API returns 500:\n"
            "1. Check `/api/health` and `/api/setup/status`.\n"
            "2. The project list refreshes after Add project; if it stays empty, "
            "reload and check that the folder exists and is writable.\n"
            "3. Settings need a project. With no project the API now returns a "
            "clear 422 instead of a 500.\n"
            "4. Still blocked? Check `docker logs <instance>` and the API log for "
            "the request id."
        )
    if step == "project":
        return (
            "Enter a Git URL or an absolute server folder in the field, then press "
            "Add project. If you already have a project, select it from the list."
        )
    if step == "ai":
        return (
            "Choose a provider and paste the API key, or skip this step. You can "
            "connect a model later from Settings → AI Models."
        )
    return (
        "I can help with three things:\n"
        "• **Add a project** — Git URL or server folder.\n"
        "• **Connect a model** — provider, API key and chat model.\n"
        "• **Troubleshoot** — empty lists, settings 500s, first-run issues.\n\n"
        "Ask in your own words, or use the quick prompts below."
    )


def _ai_answer(message: str, history: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
    """Ask the configured model for a setup answer. Returns None if unavailable."""
    settings = read_global_settings()
    model_name = (settings.llm_model or "").strip()
    model = get_model(model_name, settings) if model_name else None
    if model is None:
        models = [
            m for m in (settings.ai_models or [])
            if getattr(m, "model_type", None) in (AIModelType.llm, "llm")
        ]
        model = models[0] if models else None
    if model is None:
        return None
    provider = get_provider_settings(model.ai_provider, settings)
    if not (provider.api_key or "").strip():
        return None

    from openai import OpenAI

    client = OpenAI(
        api_key=provider.api_key,
        base_url=provider.api_url or None,
        timeout=45.0,
    )
    messages = [{
        "role": "system",
        "content": (
            "You are codx-junior's setup assistant. You help a developer install "
            "and configure a self-hosted codx-junior workspace: adding a project, "
            "connecting an OpenAI-compatible model, and troubleshooting first-run "
            "issues. Be concise (under 180 words), concrete and friendly. Never "
            "invent API keys or share secrets. A project is required; an AI "
            "provider is optional."
        ),
    }]
    for item in (history or [])[-6:]:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:4000]})
    messages.append({"role": "user", "content": message})
    response = client.chat.completions.create(
        model=model.ai_model or model.name,
        messages=messages,
        temperature=0.2,
    )
    answer = response.choices[0].message.content if response.choices else ""
    return (answer or "").strip() or None


@router.post("/assistant")
async def setup_assistant(
    payload: AssistantRequest,
    user: CodxUser = Depends(get_authenticated_user),
):
    """Answer a setup question with AI when configured, deterministic QA otherwise."""
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="Write a question first.")
    ai = _ai_state()
    if ai["configured"]:
        try:
            answer = await asyncio.to_thread(_ai_answer, message, payload.history)
            if answer:
                return {"answer": answer, "source": "ai", "configured": True}
        except Exception as ex:
            logger.warning("setup assistant AI call failed, using QA: %s", ex)
    return {
        "answer": _qa_answer(message, payload.context),
        "source": "qa",
        "configured": bool(ai["configured"]),
    }
