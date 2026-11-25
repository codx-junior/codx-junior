"""
OpenAI-compatible LLM gateway for delegated agents (browser-use).

The browser-use worker is given a short-lived **run token** instead of the
provider key and points its OpenAI client at::

    http://127.0.0.1:19980/api/ai/llm/v1

so the agent's model conversation is served by the API process that owns the
project's settings.  Every call is:

* authorized by the signed run token (no session cookie, no provider key in the
  worker);
* bounded by the user's wallet, the chat budget and the optional per-run cap
  (``CODX_BROWSER_AGENT_MAX_COST_CXJ``);
* recorded in analytics with the chat id, the browser session and the
  ``browser-use`` tag, priced in cxjcoins;
* appended to the run ledger, which the tool reports as ``ToolResponse.cost`` so
  the browsing spend lands in the chat exactly like a normal round.

See ``codx.junior.ai.llm_gateway`` for the implementation.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from codx.junior.ai.llm_gateway import (
    LlmGateway,
    LlmGatewayError,
    bearer_token,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _error(status: int, message: str, code: str) -> JSONResponse:
    """Return an OpenAI-shaped error so the agent can surface it to the model."""
    return JSONResponse(
        status_code=int(status),
        content={
            "error": {
                "message": str(message),
                "type": "codx_gateway_error",
                "param": None,
                "code": code or "codx_gateway_error",
            }
        },
    )


@router.get(
    "/ai/llm/v1/models",
    summary="Models exposed by the delegated-agent LLM gateway",
)
async def list_models(request: Request):
    """Answer model discovery for OpenAI-compatible clients (free)."""
    token = bearer_token(request.headers)
    try:
        run = LlmGateway().authorize(token)
    except LlmGatewayError as ex:
        return _error(ex.status, str(ex), ex.code)
    return {
        "object": "list",
        "data": [
            {
                "id": run.get("m") or "",
                "object": "model",
                "created": 0,
                "owned_by": "codx-junior",
            }
        ],
    }


@router.post(
    "/ai/llm/v1/chat/completions",
    summary="Chat completions for a delegated agent run (accounted + limited)",
)
async def chat_completions(request: Request):
    """
    Serve one chat completion for a browser-use run.

    The run token travels in ``Authorization: Bearer <token>`` (the OpenAI
    client's api key) or in ``x-codx-run-token``.
    """
    token = bearer_token(request.headers)
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Invalid JSON body", "invalid_request")
    try:
        response, headers = await LlmGateway().chat_completions(body, token)
    except LlmGatewayError as ex:
        if ex.status >= 500:
            logger.warning("LLM gateway: %s (%s)", ex, ex.code)
        return _error(ex.status, str(ex), ex.code)
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("LLM gateway failure")
        return _error(500, f"LLM gateway failure: {ex}", "gateway_error")
    headers = {**(headers or {}), "cache-control": "no-store"}
    return JSONResponse(content=response, headers=headers)
