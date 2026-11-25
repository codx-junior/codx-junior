"""Run-scoped LLM gateway: signed run tokens, budget and cost accounting.

Loaded by path: the module is stdlib-only (the provider call imports httpx
lazily) so the accounting can be tested without the API dependency tree.

Run with pytest, or directly: ``python tests/browser/test_llm_gateway.py``.
"""

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = API_ROOT / "codx" / "junior" / "ai" / "llm_gateway.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gateway = _load("cxj_llm_gateway", MODULE_PATH)

SECRET = "test-secret"


class FakeLLM:
    provider = "openai"
    api_url = "http://provider.local/v1"
    api_key = "sk-provider-secret"
    input_k_tokens_cxjcoins = 0.01
    output_k_tokens_cxjcoins = 0.02

    def __init__(self, model="test-model", input_price=0.01, output_price=0.02):
        self.model = model
        self.input_k_tokens_cxjcoins = input_price
        self.output_k_tokens_cxjcoins = output_price


class FakeSettings:
    project_id = "project-1"
    project_name = "project"
    llm_model = "test-model"

    def __init__(self, codx_path):
        self.codx_path = str(codx_path)

    def get_llm_settings(self, llm_model=None):
        # A different model can have different prices: the gateway must use the
        # one the chat chose.
        if llm_model == "expensive-model":
            return FakeLLM(model=llm_model, input_price=1.0, output_price=2.0)
        return FakeLLM(model=llm_model or self.llm_model)


class FakeAnalytics:
    def __init__(self):
        self.calls = []

    def record_token_usage(self, **kwargs):
        self.calls.append(kwargs)


def _run(tmp_path, **overrides):
    settings = FakeSettings(tmp_path)
    values = {
        "chat_id": "chat-1",
        "browser_session_id": "session-1",
        "username": "alice",
        "chat_budget_cxj": None,
        "chat_start_cost_cxj": 0.0,
    }
    values.update(overrides)
    with _patch_secret():
        return gateway.open_run(settings, **values)


class _patch_secret:
    """Temporarily replace the signing secret helper."""

    def __enter__(self):
        self.original = gateway._signing_secret
        gateway._signing_secret = lambda: SECRET
        return self

    def __exit__(self, *exc):
        gateway._signing_secret = self.original
        return False


def _gateway(tmp_path, *, http_post=None, analytics=None):
    settings = FakeSettings(tmp_path)
    return gateway.LlmGateway(
        analytics=analytics or FakeAnalytics(),
        http_post=http_post,
        settings_lookup=lambda project_id: settings,
        user_lookup=lambda username: None,
        secret=SECRET,
        token_counter=lambda text, model: max(1, len(str(text or "")) // 4),
    )


def test_token_round_trip_and_tamper(tmp_path):
    run = _run(tmp_path)
    payload = gateway.verify_token(run.token, SECRET)

    assert payload["rid"] == run.run_id
    assert payload["chat"] == "chat-1"
    assert payload["bs"] == "session-1"
    assert payload["m"] == "test-model"
    assert payload["pid"] == "project-1"
    assert "sk-provider-secret" not in run.token, "the provider key must never travel in the token"

    body, signature = run.token.split(".")
    forged = body + "." + ("A" if signature[-1] != "A" else "B") + signature[1:]
    try:
        gateway.verify_token(forged, SECRET)
    except gateway.LlmGatewayError as ex:
        assert ex.status == 401
    else:  # pragma: no cover
        raise AssertionError("a tampered token must be rejected")


def test_token_expiry_and_bad_secret(tmp_path):
    run = _run(tmp_path)
    for token, secret, now in (
        (run.token, SECRET, time.time() + 10 ** 6),
        (run.token, "other-secret", time.time()),
    ):
        try:
            gateway.verify_token(token, secret, now=now)
        except gateway.LlmGatewayError as ex:
            assert ex.status == 401
        else:  # pragma: no cover
            raise AssertionError("expected the token to be rejected")


def test_open_run_uses_the_chat_model_and_ledger(tmp_path):
    default_run = _run(tmp_path)
    assert default_run.model == "test-model", "no model given: the project default"

    chat_run = _run(tmp_path, model="chat-model")
    assert chat_run.model == "chat-model", "the chat/profile model is what the token carries"

    assert chat_run.ledger_path.startswith(str(tmp_path / gateway.RUNS_FOLDER))
    assert Path(chat_run.ledger_path).parent.is_dir()
    assert chat_run.chat_budget_cxj is None
    assert chat_run.max_run_cost_cxj is None


def test_cost_formula_matches_the_chat_round_cost():
    assert gateway.compute_cost_cxj(1000, 500, 0.01, 0.02) == 0.02
    assert gateway.compute_cost_cxj(0, 0, 0.01, 0.02) == 0.0
    assert gateway.compute_cost_cxj(1000, 500, None, None) == 0.0


def test_ledger_totals(tmp_path):
    run = _run(tmp_path)
    gateway.append_usage(run.ledger_path, {"input_tokens": 100, "output_tokens": 50, "cost_cxj": 0.1})
    gateway.append_usage(run.ledger_path, {"input_tokens": 10, "output_tokens": 5, "cost_cxj": 0.02})

    totals = gateway.usage_totals(run.ledger_path)
    assert totals["calls"] == 2
    assert totals["input_tokens"] == 110
    assert totals["output_tokens"] == 55
    assert round(totals["cost_cxj"], 6) == 0.12
    assert gateway.usage_totals(str(tmp_path / "missing.jsonl"))["calls"] == 0


def test_chat_completion_is_forwarded_priced_and_recorded(tmp_path):
    seen = {}

    async def http_post(url, headers, payload):
        seen.update(url=url, headers=headers, payload=payload)
        return 200, {
            "id": "completion-1",
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }

    analytics = FakeAnalytics()
    gw = _gateway(tmp_path, http_post=http_post, analytics=analytics)
    run = _run(tmp_path, chat_budget_cxj=None)

    response, headers = asyncio.run(gw.chat_completions(
        {"messages": [{"role": "user", "content": "hi"}], "model": "ignored-model"},
        run.token,
    ))

    assert response["choices"][0]["message"]["content"] == "done"
    assert seen["url"] == "http://provider.local/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-provider-secret"
    assert seen["payload"]["model"] == "test-model", "the token's model wins"
    assert headers["x-codx-cxj"] == "0.02000000"
    assert headers["x-codx-run-id"] == run.run_id

    totals = gateway.usage_totals(run.ledger_path)
    assert totals == {"cost_cxj": 0.02, "input_tokens": 1000, "output_tokens": 500, "calls": 1}

    assert len(analytics.calls) == 1
    event = analytics.calls[0]
    assert event["chat_id"] == "chat-1"
    assert event["session_id"] == "session-1"
    assert event["username"] == "alice"
    assert event["tags"] == gateway.DEFAULT_TAGS
    assert event["input_tokens"] == 1000
    assert event["output_tokens"] == 500
    assert event["tokens_from_provider"] is True
    assert event["input_k_tokens_cxjcoins"] == 0.01


def test_the_chat_model_is_used_and_priced(tmp_path):
    """The token carries the chat/profile model; the gateway prices that model."""
    seen = {}

    async def http_post(url, headers, payload):
        seen["payload"] = payload
        return 200, {"usage": {"prompt_tokens": 1000, "completion_tokens": 1000}}

    analytics = FakeAnalytics()
    gw = _gateway(tmp_path, http_post=http_post, analytics=analytics)
    run = _run(tmp_path, model="expensive-model")

    asyncio.run(gw.chat_completions({"messages": [{"role": "user", "content": "hi"}]}, run.token))

    assert run.model == "expensive-model"
    assert seen["payload"]["model"] == "expensive-model"
    # 1000 in * 1.0/1k + 1000 out * 2.0/1k = 3.0 cxj
    assert gateway.usage_totals(run.ledger_path)["cost_cxj"] == 3.0
    assert analytics.calls[0]["model"] == "expensive-model"
    assert analytics.calls[0]["input_k_tokens_cxjcoins"] == 1.0
    assert analytics.calls[0]["output_k_tokens_cxjcoins"] == 2.0


def test_chat_budget_blocks_the_next_call(tmp_path):
    called = {"count": 0}

    async def http_post(url, headers, payload):
        called["count"] += 1
        return 200, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    gw = _gateway(tmp_path, http_post=http_post)
    run = _run(tmp_path, chat_budget_cxj=0.01, chat_start_cost_cxj=0.0)
    gateway.append_usage(run.ledger_path, {"cost_cxj": 0.02})

    try:
        asyncio.run(gw.chat_completions({"messages": [{"role": "user", "content": "hi"}]}, run.token))
    except gateway.LlmGatewayError as ex:
        assert ex.status == 402
        assert ex.code == "chat_budget_exceeded"
    else:  # pragma: no cover
        raise AssertionError("expected the chat budget to block the call")
    assert called["count"] == 0, "the provider must not be called when the budget is gone"


def test_live_chat_cost_is_used_for_the_budget(tmp_path):
    called = {"count": 0}

    async def http_post(url, headers, payload):
        called["count"] += 1
        return 200, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    gw = _gateway(tmp_path, http_post=http_post)
    run = _run(tmp_path, chat_budget_cxj=1.0, chat_start_cost_cxj=0.0)

    from unittest.mock import patch

    with patch.object(gateway, "chat_costs", lambda settings, chat_id: (1.0, 2.0)):
        try:
            asyncio.run(gw.chat_completions({"messages": [{"role": "user", "content": "hi"}]}, run.token))
        except gateway.LlmGatewayError as ex:
            assert ex.status == 402
        else:  # pragma: no cover
            raise AssertionError("expected the live chat cost to block the call")
    assert called["count"] == 0


def test_run_cap_blocks_the_next_call(tmp_path):
    gw = _gateway(tmp_path, http_post=None)
    run = _run(tmp_path)
    run_payload = gateway.verify_token(run.token, SECRET)
    ledger = run_payload["l"]
    gateway.append_usage(ledger, {"cost_cxj": 5.0})

    try:
        gw.check_limits({**run_payload, "mr": 5.0})
    except gateway.LlmGatewayError as ex:
        assert ex.status == 402
        assert ex.code == "run_budget_exceeded"
    else:  # pragma: no cover
        raise AssertionError("expected the run cap to block the call")


def test_streaming_is_rejected_explicitly(tmp_path):
    gw = _gateway(tmp_path)
    run = _run(tmp_path)

    try:
        asyncio.run(gw.chat_completions(
            {"messages": [{"role": "user", "content": "hi"}], "stream": True}, run.token
        ))
    except gateway.LlmGatewayError as ex:
        assert ex.status == 400
        assert ex.code == "streaming_not_supported"
    else:  # pragma: no cover
        raise AssertionError("expected streaming to be rejected")


def test_missing_usage_is_estimated(tmp_path):
    async def http_post(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "some answer text"}}]}

    analytics = FakeAnalytics()
    gw = _gateway(tmp_path, http_post=http_post, analytics=analytics)
    run = _run(tmp_path)

    asyncio.run(gw.chat_completions({"messages": [{"role": "user", "content": "hello world"}]}, run.token))

    totals = gateway.usage_totals(run.ledger_path)
    assert totals["input_tokens"] > 0
    assert totals["output_tokens"] > 0
    assert analytics.calls[0]["tokens_from_provider"] is False


def test_provider_error_is_reported_and_not_charged(tmp_path):
    async def http_post(url, headers, payload):
        return 429, {"error": {"message": "rate limited"}}

    analytics = FakeAnalytics()
    gw = _gateway(tmp_path, http_post=http_post, analytics=analytics)
    run = _run(tmp_path)

    try:
        asyncio.run(gw.chat_completions({"messages": [{"role": "user", "content": "hi"}]}, run.token))
    except gateway.LlmGatewayError as ex:
        assert ex.status == 429
        assert "rate limited" in str(ex)
    else:  # pragma: no cover
        raise AssertionError("expected a provider error")
    assert gateway.usage_totals(run.ledger_path)["calls"] == 0
    assert analytics.calls == []


def test_bearer_token_extraction():
    assert gateway.bearer_token({"authorization": "Bearer abc.def"}) == "abc.def"
    assert gateway.bearer_token({"x-codx-run-token": "abc.def"}) == "abc.def"
    assert gateway.bearer_token({"authorization": "Basic xyz"}) == ""
    assert gateway.bearer_token({}) == ""


def test_gateway_base_url_from_api_port():
    import os

    with _env({gateway.ENV_API_PORT: "19980"}, clear=(gateway.ENV_BASE_URL,)):
        assert gateway.gateway_base_url() == "http://127.0.0.1:19980/api/ai/llm/v1"
    with _env({gateway.ENV_BASE_URL: "https://cj.example/api/ai/llm/v1"}, clear=()):
        assert gateway.gateway_base_url() == "https://cj.example/api/ai/llm/v1"
    with _env({gateway.ENV_API_PORT: "not-a-port"}, clear=(gateway.ENV_BASE_URL,)):
        assert gateway.gateway_base_url() is None


class _env:
    """Tiny env patch (unittest.mock.patch.dict without the import)."""

    def __init__(self, values, clear=()):
        self.values = dict(values)
        self.clear = tuple(clear)
        self.saved = {}

    def __enter__(self):
        for key in self.clear:
            self.saved[key] = os.environ.pop(key, None)
        for key, value in self.values.items():
            self.saved.setdefault(key, os.environ.get(key))
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False



if __name__ == "__main__":  # pragma: no cover - ad-hoc runner
    import tempfile

    passed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        if "tmp_path" in function.__code__.co_varnames[: function.__code__.co_argcount]:
            with tempfile.TemporaryDirectory() as folder:
                function(Path(folder))
        else:
            function()
        passed += 1
        print("ok -", name)
    print(f"\n{passed} llm gateway tests passed")
