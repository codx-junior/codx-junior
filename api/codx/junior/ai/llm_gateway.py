"""
Run-scoped LLM gateway: browser-use's model calls go through codx-junior.

Why
---
The browser-use agent owns its own model conversation.  Letting it call the
provider directly would create a second, invisible AI bill: no analytics event,
no chat cost, no budget.  This module makes every one of those calls pass
through codx-junior instead, so browsing is accounted exactly like chat.

How
---
* When a browser-use run starts, :func:`open_run` mints a **short-lived signed
  run token** (stdlib HMAC, signed with the installation's signing secret) and
  points the worker at ``http://127.0.0.1:<api>/api/ai/llm/v1`` with that token
  as the API key.  The worker never sees the provider key.
* The token carries only identifiers and the budget snapshot: run id, chat id,
  browser session id, project id, username, model, chat budget and the chat's
  cost when the run started, the ledger path and an expiry.
* Every gateway request verifies the token, checks the wallet and the chat/run
  budgets, forwards the OpenAI-compatible call to the project's provider,
  records the token usage in analytics, prices it in cxjcoins and appends it to
  the run ledger.
* At the end of the run the tool sums the ledger and reports it as the tool's
  cost (``ToolResponse.cost``), which SmolAgent adds to the chat exactly like
  any other round cost - one accounting path, no double counting.

The ledger is a small JSONL file under ``.codx/llm_runs/<run_id>.jsonl``, so the
run can be served by any API worker (the token is self-contained and the spend
accumulates across processes).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: ``on``/``off``: when off, browser-use talks to the provider directly (the
#: pre-gateway behaviour) and its cost is NOT accounted.
ENV_ENABLED = "CODX_LLM_GATEWAY"
#: Full client base URL of the gateway (``…/api/ai/llm/v1``); auto-detected from
#: the API port when empty.
ENV_BASE_URL = "CODX_LLM_GATEWAY_URL"
#: API port used to build the loopback base URL.
ENV_API_PORT = "CODX_JUNIOR_API_PORT"
DEFAULT_API_PORT = 19980
GATEWAY_PATH = "/api/ai/llm/v1"
#: Hard cap for one browser-use run in cxjcoins (0/empty disables it). The chat
#: budget is enforced independently and is the one that always applies.
ENV_MAX_RUN_COST = "CODX_BROWSER_AGENT_MAX_COST_CXJ"
#: Upper bound for one upstream model request.
ENV_UPSTREAM_TIMEOUT = "CODX_LLM_GATEWAY_TIMEOUT_SECONDS"

DEFAULT_TTL_SECONDS = 3600
DEFAULT_UPSTREAM_TIMEOUT = 300
RUNS_FOLDER = "llm_runs"
#: Ledgers older than this are pruned when a new run starts.
LEDGER_MAX_AGE_SECONDS = 7 * 24 * 3600
#: Tags attached to the analytics events (and shown in the cost reports).
DEFAULT_TAGS = "browser-use,browser_agent"
TOKEN_VERSION = 1


class LlmGatewayError(RuntimeError):
    """A gateway request that must not reach the provider."""

    def __init__(self, message: str, *, status: int = 400, code: str = "codx_gateway_error"):
        super().__init__(message)
        self.status = int(status)
        self.code = code


# ── Configuration ────────────────────────────────────────────────────────────


def gateway_enabled() -> bool:
    """Return True when model calls must be routed through the gateway."""
    return (os.environ.get(ENV_ENABLED) or "on").strip().lower() not in (
        "0", "false", "no", "off", "direct",
    )


def gateway_base_url() -> Optional[str]:
    """
    Return the OpenAI-compatible client base URL of the gateway.

    ``CODX_LLM_GATEWAY_URL`` wins; otherwise the API loopback port is used (the
    worker runs on the API host, so 127.0.0.1 reaches it even behind nginx).
    """
    configured = (os.environ.get(ENV_BASE_URL) or "").strip()
    if configured:
        return configured.rstrip("/")
    port = (os.environ.get(ENV_API_PORT) or "").strip() or str(DEFAULT_API_PORT)
    try:
        int(port)
    except ValueError:
        return None
    return f"http://127.0.0.1:{port}{GATEWAY_PATH}"


def upstream_timeout() -> float:
    """Return the timeout (seconds) for one upstream model request."""
    raw = os.environ.get(ENV_UPSTREAM_TIMEOUT)
    try:
        value = float(raw) if raw else DEFAULT_UPSTREAM_TIMEOUT
    except ValueError:
        value = DEFAULT_UPSTREAM_TIMEOUT
    return max(30.0, min(900.0, value))


def max_run_cost_cxj() -> Optional[float]:
    """Return the optional per-run cost cap in cxjcoins."""
    raw = (os.environ.get(ENV_MAX_RUN_COST) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# ── Token ────────────────────────────────────────────────────────────────────


def _signing_secret() -> str:
    """Return the installation's signing secret (shared by every worker)."""
    from codx.junior.global_settings import ensure_signing_secret, read_global_settings

    return ensure_signing_secret(settings=read_global_settings())


def _b64encode(raw: bytes) -> str:
    """URL-safe base64 without padding (compact tokens)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    """Decode a :func:`_b64encode` value."""
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def sign_token(payload: Dict[str, Any], secret: str) -> str:
    """Return ``<payload>.<signature>`` for *payload* (stdlib HMAC-SHA256)."""
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_token(token: str, secret: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """
    Verify a run token and return its payload.

    Raises:
        LlmGatewayError: 401 when the token is malformed, forged or expired.
    """
    token = (token or "").strip()
    if not token or token.count(".") != 1:
        raise LlmGatewayError("Missing or malformed run token", status=401, code="invalid_token")
    body, signature = token.split(".", 1)
    expected = _b64encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, signature):
        raise LlmGatewayError("Invalid run token signature", status=401, code="invalid_token")
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, TypeError):
        raise LlmGatewayError("Invalid run token payload", status=401, code="invalid_token")
    expires = payload.get("exp")
    if not isinstance(expires, (int, float)) or expires < (now if now is not None else time.time()):
        raise LlmGatewayError("Run token expired", status=401, code="token_expired")
    return payload


def bearer_token(headers: Any) -> str:
    """Extract the run token from ``Authorization`` or ``x-codx-run-token``."""
    try:
        value = headers.get("x-codx-run-token")
    except Exception:
        value = None
    if value:
        return str(value).strip()
    try:
        authorization = headers.get("authorization") or ""
    except Exception:
        authorization = ""
    scheme, _, token = str(authorization).partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return ""


# ── Runs ─────────────────────────────────────────────────────────────────────


@dataclass
class LlmRun:
    """One browser-use (or other delegated agent) model-call session."""

    run_id: str
    token: str
    model: str
    ledger_path: str
    expires_at: float
    chat_id: Optional[str] = None
    browser_session_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    username: Optional[str] = None
    chat_budget_cxj: Optional[float] = None
    chat_start_cost_cxj: float = 0.0
    max_run_cost_cxj: Optional[float] = None
    tags: str = DEFAULT_TAGS
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> Dict[str, Any]:
        """Return the signed payload carried by :attr:`token`."""
        return {
            "v": TOKEN_VERSION,
            "rid": self.run_id,
            "m": self.model,
            "l": self.ledger_path,
            "chat": self.chat_id,
            "bs": self.browser_session_id,
            "pid": self.project_id,
            "pn": self.project_name,
            "u": self.username,
            "cb": self.chat_budget_cxj,
            "cs": self.chat_start_cost_cxj,
            "mr": self.max_run_cost_cxj,
            "tags": self.tags,
            "exp": self.expires_at,
        }


def _ledger_folder(settings: Any) -> Path:
    """Return the folder holding the run ledgers of a project."""
    base = getattr(settings, "codx_path", None) or getattr(settings, "abs_project_path", None) or "."
    return Path(str(base)) / RUNS_FOLDER


def _prune_ledgers(folder: Path, *, now: Optional[float] = None) -> None:
    """Delete ledgers older than :data:`LEDGER_MAX_AGE_SECONDS` (best effort)."""
    cutoff = (now if now is not None else time.time()) - LEDGER_MAX_AGE_SECONDS
    try:
        for entry in folder.glob("*.jsonl"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        pass


def chat_costs(settings: Any, chat_id: Optional[str]) -> Tuple[Optional[float], float]:
    """
    Return ``(budget, current_cost)`` of a chat, best effort.

    Args:
        settings: Project settings (locates the project's chats).
        chat_id: Chat to inspect (None → no budget).

    Returns:
        tuple: ``Chat.estimation_cost`` (None when unset/unknown) and
        ``Chat.cost`` (0.0 when unknown).
    """
    if not chat_id:
        return None, 0.0
    try:
        from codx.junior.chat_manager import ChatManager

        chat = ChatManager(settings).find_by_id(chat_id)
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("Could not load chat %s for the LLM budget: %s", chat_id, ex)
        return None, 0.0
    if not chat:
        return None, 0.0
    try:
        budget = getattr(chat, "estimation_cost", None)
        budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None
    if budget is not None and budget <= 0:
        budget = None
    try:
        cost = float(getattr(chat, "cost", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return budget, cost


def open_run(
    settings: Any,
    *,
    chat_id: Optional[str] = None,
    browser_session_id: Optional[str] = None,
    username: Optional[str] = None,
    model: Optional[str] = None,
    ttl: Optional[int] = None,
    chat_budget_cxj: Optional[float] = None,
    chat_start_cost_cxj: Optional[float] = None,
    max_run_cost_cxj_value: Optional[float] = None,
    tags: str = DEFAULT_TAGS,
) -> LlmRun:
    """
    Mint the token and ledger for one delegated-agent run.

    Args:
        settings: CODXJuniorSettings of the project (prices/model/api are
            resolved by the gateway from ``project_id``; only the model name is
            carried in the token).
        chat_id: Chat whose budget the run must respect.
        browser_session_id: Session recorded in analytics (cost joins).
        username: User whose wallet is checked on every call.
        model: Model override (default: project model).
        ttl: Token lifetime in seconds (default 1 h).
        chat_budget_cxj: Pre-resolved budget (skips the chat lookup).
        chat_start_cost_cxj: Pre-resolved current chat cost.
        max_run_cost_cxj_value: Per-run cap override.
        tags: Analytics tags.

    Returns:
        LlmRun: signed token, ledger path and the budget snapshot.
    """
    llm = settings.get_llm_settings(llm_model=model)
    resolved_model = getattr(llm, "model", None) or model or ""

    if chat_budget_cxj is None and chat_start_cost_cxj is None:
        budget, current = chat_costs(settings, chat_id)
    else:
        budget, current = chat_budget_cxj, float(chat_start_cost_cxj or 0.0)

    folder = _ledger_folder(settings)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _prune_ledgers(folder)
    except OSError as ex:
        logger.warning("Could not prepare the LLM run folder %s: %s", folder, ex)

    run_id = uuid.uuid4().hex
    run = LlmRun(
        run_id=run_id,
        token="",
        model=resolved_model,
        ledger_path=str(folder / f"{run_id}.jsonl"),
        expires_at=time.time() + int(ttl or DEFAULT_TTL_SECONDS),
        chat_id=str(chat_id) if chat_id else None,
        browser_session_id=str(browser_session_id) if browser_session_id else None,
        project_id=getattr(settings, "project_id", None),
        project_name=getattr(settings, "project_name", None),
        username=username,
        chat_budget_cxj=budget,
        chat_start_cost_cxj=current,
        max_run_cost_cxj=(
            max_run_cost_cxj() if max_run_cost_cxj_value is None else max_run_cost_cxj_value
        ),
        tags=tags,
    )
    run.token = sign_token(run.as_payload(), _signing_secret())
    return run


# ── Ledger ───────────────────────────────────────────────────────────────────


def _lock_file(handle, exclusive: bool) -> None:
    """Take an advisory lock when the platform supports it."""
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except (ImportError, OSError):  # pragma: no cover - non-POSIX filesystems
        pass


def _unlock_file(handle) -> None:
    """Release an advisory lock (best effort)."""
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):  # pragma: no cover
        pass


def append_usage(ledger_path: str, entry: Dict[str, Any]) -> None:
    """Append one priced call to the run ledger (cross-process safe)."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in (entry or {}).items() if value is not None},
    }
    try:
        Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as handle:
            _lock_file(handle, exclusive=True)
            try:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            finally:
                _unlock_file(handle)
    except OSError as ex:
        logger.warning("Could not append LLM usage to %s: %s", ledger_path, ex)


def read_usage(ledger_path: str) -> List[Dict[str, Any]]:
    """Return the ledger entries of a run (oldest first)."""
    if not ledger_path or not os.path.isfile(ledger_path):
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            _lock_file(handle, exclusive=False)
            try:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        entries.append(item)
            finally:
                _unlock_file(handle)
    except OSError as ex:
        logger.warning("Could not read LLM usage from %s: %s", ledger_path, ex)
    return entries


def usage_totals(ledger_path: str) -> Dict[str, Any]:
    """Return ``{cost_cxj, input_tokens, output_tokens, calls}`` of a run."""
    totals = {"cost_cxj": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0}
    for entry in read_usage(ledger_path):
        totals["calls"] += 1
        for key, field_name in (
            ("cost_cxj", "cost_cxj"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
        ):
            try:
                totals[field_name] += float(entry.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
    totals["cost_cxj"] = round(totals["cost_cxj"], 8)
    return totals


def compute_cost_cxj(
    input_tokens: int,
    output_tokens: int,
    input_price: Optional[float],
    output_price: Optional[float],
) -> float:
    """Price one call in cxjcoins (same formula as SmolAgent's round cost)."""
    return round(
        (max(0, int(input_tokens)) / 1000.0) * float(input_price or 0.0)
        + (max(0, int(output_tokens)) / 1000.0) * float(output_price or 0.0),
        8,
    )


def usage_from_response(response: Any) -> Tuple[int, int, bool]:
    """
    Extract ``(input_tokens, output_tokens, from_provider)`` from a completion.

    Falls back to the local token estimator when the provider does not report
    usage (some OpenAI-compatible servers omit it).
    """
    usage = response.get("usage") if isinstance(response, dict) else None
    input_tokens = 0
    output_tokens = 0
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "input_tokens"):
            try:
                input_tokens = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                input_tokens = 0
            if input_tokens:
                break
        for key in ("completion_tokens", "output_tokens"):
            try:
                output_tokens = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                output_tokens = 0
            if output_tokens:
                break
    return input_tokens, output_tokens, bool(input_tokens or output_tokens)


def rough_token_count(text: Any, model: str = "") -> int:
    """Last-resort token estimate (≈4 characters per token)."""
    value = str(text or "")
    if not value:
        return 0
    return max(1, (len(value) + 3) // 4)


def estimate_tokens(
    request_body: Any,
    response: Any,
    model: str,
    counter: Optional[Callable[[str, str], int]] = None,
) -> Tuple[int, int]:
    """Estimate tokens from the request/response text when usage is missing."""
    count_tokens = counter
    if count_tokens is None:
        try:
            from codx.junior.analytics.token_counter import count_tokens as _count_tokens

            count_tokens = _count_tokens
        except Exception:  # pragma: no cover - token_counter always ships with the API
            count_tokens = rough_token_count

    try:
        request_text = json.dumps(request_body or {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        request_text = str(request_body or "")
    output_text = ""
    try:
        choices = (response or {}).get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            output_text = str(message.get("content") or "")
            tool_calls = message.get("tool_calls")
            if tool_calls:
                output_text += json.dumps(tool_calls, ensure_ascii=False, default=str)
    except (AttributeError, TypeError, ValueError):
        output_text = ""
    return count_tokens(request_text, model=model), count_tokens(output_text, model=model)


# ── Gateway ──────────────────────────────────────────────────────────────────


class LlmGateway:
    """
    Serve one ``POST /chat/completions`` for a delegated-agent run.

    All collaborators are injectable so the accounting can be tested without
    FastAPI, httpx or a provider:

    Args:
        analytics: Object with ``record_token_usage(**kwargs)``.
        http_post: ``async (url, headers, payload) -> (status, body)``.
        settings_lookup: ``project_id -> CODXJuniorSettings``.
        user_lookup: ``username -> CodxUser``.
    """

    def __init__(
        self,
        *,
        analytics: Any = None,
        http_post: Optional[Callable[..., Any]] = None,
        settings_lookup: Optional[Callable[[str], Any]] = None,
        user_lookup: Optional[Callable[[str], Any]] = None,
        secret: Optional[str] = None,
        token_counter: Optional[Callable[[str, str], int]] = None,
    ) -> None:
        self._analytics = analytics
        self._http_post = http_post
        self._settings_lookup = settings_lookup
        self._user_lookup = user_lookup
        self._secret = secret
        self._token_counter = token_counter

    # -- collaborators -----------------------------------------------------
    def _secret_value(self) -> str:
        return self._secret if self._secret is not None else _signing_secret()

    def _analytics_client(self) -> Any:
        if self._analytics is not None:
            return self._analytics
        from codx.junior.ai.smol.smol_agent import _get_analytics

        return _get_analytics()

    def _settings_for(self, project_id: Optional[str]) -> Any:
        if self._settings_lookup is not None:
            return self._settings_lookup(project_id)
        from codx.junior.project.project_discover import find_project_by_id

        settings = find_project_by_id(project_id) if project_id else None
        if settings is None:
            raise LlmGatewayError(
                "Unknown project for this run token", status=403, code="unknown_project"
            )
        return settings

    def _user_for(self, username: Optional[str]) -> Any:
        if not username:
            return None
        if self._user_lookup is not None:
            return self._user_lookup(username)
        from codx.junior.security.user_management import UserSecurityManager

        return UserSecurityManager().find_user(username)

    async def _post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]):
        if self._http_post is not None:
            return await self._http_post(url, headers, payload)
        import httpx

        async with httpx.AsyncClient(timeout=upstream_timeout()) as client:
            response = await client.post(url, headers=headers, json=payload)
            try:
                body = response.json()
            except ValueError:
                body = {"error": {"message": response.text[:2000], "type": "upstream_error"}}
            return response.status_code, body

    # -- request -----------------------------------------------------------
    def authorize(self, token: str) -> Dict[str, Any]:
        """Verify the run token (401 on failure)."""
        return verify_token(token, self._secret_value())

    def check_limits(self, run: Dict[str, Any]) -> None:
        """
        Enforce the wallet, the chat budget and the per-run cap.

        Raises:
            LlmGatewayError: 402 when a limit is exhausted.
        """
        user = self._user_for(run.get("u"))
        if user is not None:
            try:
                from codx.junior.ai.wallet_check import check_user_wallet

                check_user_wallet(user)
            except LlmGatewayError:
                raise
            except Exception as ex:
                raise LlmGatewayError(
                    f"AI budget exhausted: {ex}", status=402, code="insufficient_funds"
                )

        totals = usage_totals(run.get("l") or "")
        spent = float(totals["cost_cxj"])

        max_run = run.get("mr")
        if isinstance(max_run, (int, float)) and max_run > 0 and spent >= max_run:
            raise LlmGatewayError(
                f"This browser run reached its {max_run} cxj budget "
                f"({spent:.4f} cxj spent).",
                status=402,
                code="run_budget_exceeded",
            )

        chat_budget = run.get("cb")
        if isinstance(chat_budget, (int, float)) and chat_budget > 0:
            start = float(run.get("cs") or 0.0)
            if start + spent >= chat_budget:
                raise LlmGatewayError(
                    f"The chat reached its budget ({chat_budget} cxj; "
                    f"{start + spent:.4f} cxj spent).",
                    status=402,
                    code="chat_budget_exceeded",
                )

    @staticmethod
    def _refresh_chat_limits(run: Dict[str, Any], settings: Any) -> Dict[str, Any]:
        """Merge the chat's current budget/cost into the token snapshot."""
        chat_id = run.get("chat")
        if not chat_id:
            return run
        try:
            budget, cost = chat_costs(settings, chat_id)
        except Exception:  # pragma: no cover - best effort
            return run
        if budget is None:
            return run
        snapshot = float(run.get("cs") or 0.0)
        return {**run, "cb": budget, "cs": max(snapshot, float(cost or 0.0))}

    async def chat_completions(
        self,
        body: Dict[str, Any],
        token: str,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """
        Serve one OpenAI-compatible chat completion for a run.

        Args:
            body: The OpenAI request body sent by the delegated agent.
            token: The run token from the ``Authorization`` header.

        Returns:
            tuple: ``(response_body, response_headers)``.

        Raises:
            LlmGatewayError: On auth, budget or upstream failures.
        """
        if not isinstance(body, dict):
            raise LlmGatewayError("The request body must be a JSON object", status=400)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise LlmGatewayError("`messages` is required", status=400)
        if body.get("stream"):
            # browser-use 1.0 calls the model without streaming; keep the
            # gateway honest instead of half-forwarding SSE.
            raise LlmGatewayError(
                "Streaming is not supported by the codx-junior LLM gateway; "
                "call it with \"stream\": false.",
                status=400,
                code="streaming_not_supported",
            )

        run = self.authorize(token)
        settings = self._settings_for(run.get("pid"))
        # The chat may have changed since the run started (another run, a new
        # budget): prefer its live cost/budget over the token snapshot.
        run = self._refresh_chat_limits(run, settings)
        self.check_limits(run)

        model = run.get("m") or getattr(settings, "llm_model", None)
        llm = settings.get_llm_settings(llm_model=model)
        api_url = (getattr(llm, "api_url", "") or "").rstrip("/")
        api_key = getattr(llm, "api_key", "") or ""
        if not api_url:
            raise LlmGatewayError(
                "The project has no LLM endpoint configured", status=500, code="no_provider"
            )

        payload = dict(body)
        payload["model"] = model
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        url = f"{api_url}/chat/completions"

        started = time.monotonic()
        try:
            status, response = await self._post(url, headers, payload)
        except Exception as ex:
            raise LlmGatewayError(
                f"The model provider is unreachable: {ex}", status=502, code="provider_unreachable"
            )
        duration = time.monotonic() - started

        if status >= 400 or not isinstance(response, dict):
            message = ""
            if isinstance(response, dict):
                error = response.get("error")
                message = error.get("message") if isinstance(error, dict) else str(error or "")
                message = message or str(response)[:500]
            raise LlmGatewayError(
                message or f"The model provider answered HTTP {status}",
                status=int(status) if 400 <= status < 600 else 502,
                code="provider_error",
            )

        input_tokens, output_tokens, from_provider = usage_from_response(response)
        if not from_provider:
            input_tokens, output_tokens = estimate_tokens(
                payload, response, model, counter=self._token_counter
            )

        cost = compute_cost_cxj(
            input_tokens,
            output_tokens,
            getattr(llm, "input_k_tokens_cxjcoins", None),
            getattr(llm, "output_k_tokens_cxjcoins", None),
        )
        request_id = str(response.get("id") or uuid.uuid4().hex)
        append_usage(run.get("l") or "", {
            "run_id": run.get("rid"),
            "request_id": request_id,
            "model": model,
            "provider": getattr(llm, "provider", "") or "",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cxj": cost,
            "duration_s": round(duration, 3),
            "tags": run.get("tags") or DEFAULT_TAGS,
        })
        self._record_usage(run, llm, model, input_tokens, output_tokens, cost, duration, request_id, from_provider)

        totals = usage_totals(run.get("l") or "")
        return response, {
            "x-codx-run-id": str(run.get("rid") or ""),
            "x-codx-cxj": f"{totals['cost_cxj']:.8f}",
        }

    def _record_usage(
        self,
        run: Dict[str, Any],
        llm: Any,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        duration: float,
        request_id: str,
        from_provider: bool,
    ) -> None:
        """Record the call in analytics (same event shape as a chat round)."""
        try:
            self._analytics_client().record_token_usage(
                username=run.get("u") or "anonymous",
                project_name=run.get("pn") or "",
                project_id=run.get("pid") or "",
                model=model,
                provider=getattr(llm, "provider", "") or "",
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                duration_seconds=float(duration),
                session_id=run.get("bs"),
                tags=run.get("tags") or DEFAULT_TAGS,
                input_k_tokens_cxjcoins=getattr(llm, "input_k_tokens_cxjcoins", 0.0) or 0.0,
                output_k_tokens_cxjcoins=getattr(llm, "output_k_tokens_cxjcoins", 0.0) or 0.0,
                request_id=request_id,
                tokens_from_provider=bool(from_provider),
                chat_id=run.get("chat"),
            )
        except Exception as ex:  # pragma: no cover - analytics is non-fatal
            logger.warning("Could not record browser-use token usage: %s", ex)
        logger.info(
            "LLM gateway: run=%s chat=%s model=%s in=%s out=%s cost=%.6f cxj %.0fms",
            str(run.get("rid") or "")[:8],
            run.get("chat"),
            model,
            input_tokens,
            output_tokens,
            cost,
            duration * 1000,
        )
