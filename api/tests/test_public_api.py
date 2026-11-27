"""
Tests for the PAT-authenticated OpenAI-compatible public API.

The network/engine boundary is replaced with small fakes; the interesting
contract here is token scoping, model resolution and the protection gate.

Run with::

    cd api && pytest tests/test_public_api.py -q
"""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from codx.junior.security.pat import PATPrincipal, PATRecord

# Import the router module by file: importing ``codx.junior.api`` executes the
# package ``__init__`` (session/engine), which needs the full runtime stack.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "codx"
    / "junior"
    / "api"
    / "chatGPTLikeApi.py"
)
_spec = importlib.util.spec_from_file_location("chatGPTLikeApi_under_test", _MODULE_PATH)
public_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(public_api)


def _profile(name="support", active=True, protected=True, max_input=0):
    return SimpleNamespace(
        name=name,
        description=f"{name} profile",
        api_settings=SimpleNamespace(
            active=active,
            model_name=f"acme/{name}",
            description="",
        ),
        guardrails=SimpleNamespace(
            enabled=protected,
            max_input_chars=max_input,
        ),
    )


def _project(name="ACME", project_id="project-1"):
    return SimpleNamespace(project_id=project_id, project_name=name, codx_path=f"/tmp/{name}")


def _entry(project, profile):
    return public_api._entry(project, profile)


def _principal(project_id="project-1", profiles=None, require_protection=True):
    return PATPrincipal(
        token=PATRecord(
            id="token-id",
            name="Support widget",
            username="alice",
            token_hash="x" * 64,
            prefix="cxj_pat_abcdef",
            scopes=["chat:write"],
            project_id=project_id,
            project_name="ACME",
            profiles=list(profiles or []),
            require_public_exposure_protection=require_protection,
        )
    )


# ---------------------------------------------------------------------------
# Message normalization
# ---------------------------------------------------------------------------


def test_messages_accept_the_openai_shape_and_text_blocks():
    messages, error = public_api._normalize_messages(
        [
            {"role": "system", "content": "be nice"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "hello"}],
            },
        ]
    )
    assert error is None
    assert messages == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hello"},
    ]


def test_messages_reject_empty_oversized_and_unknown_roles():
    assert public_api._normalize_messages([])[1]
    assert public_api._normalize_messages([{"role": "tool", "content": "x"}])[1]
    assert public_api._normalize_messages(
        [{"role": "user", "content": "x" * (public_api.MAX_MESSAGE_CHARS + 1)}]
    )[1]
    # The last message must be the user's.
    assert public_api._normalize_messages(
        [{"role": "assistant", "content": "hello"}]
    )[1]


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_resolve_entry_by_exact_model_id():
    project = _project()
    profile = _profile()
    entries = [(project, profile, _entry(project, profile))]
    assert public_api._resolve_entry(entries, "acme/support")[1].name == "support"


def test_resolve_entry_by_project_and_profile_and_bare_name():
    project = _project()
    profile = _profile()
    entries = [(project, profile, _entry(project, profile))]
    assert public_api._resolve_entry(entries, "ACME/support")[1].name == "support"
    assert public_api._resolve_entry(entries, "project-1/support")[1].name == "support"
    assert public_api._resolve_entry(entries, "support")[1].name == "support"
    assert public_api._resolve_entry(entries, "nope") is None


def test_resolve_entry_prefers_the_only_available_model():
    project = _project()
    profile = _profile()
    entries = [(project, profile, _entry(project, profile))]
    assert public_api._resolve_entry(entries, None)[1].name == "support"
    assert public_api._resolve_entry([], None) is None


def test_resolve_entry_requires_a_model_when_ambiguous():
    project = _project()
    first = _profile("support")
    second = _profile("billing")
    entries = [
        (project, first, _entry(project, first)),
        (project, second, _entry(project, second)),
    ]
    assert public_api._resolve_entry(entries, None) is None


# ---------------------------------------------------------------------------
# Protection gate
# ---------------------------------------------------------------------------


def test_prepare_chat_rejects_an_unprotected_profile(monkeypatch):
    project = _project()
    profile = _profile(protected=False)
    entries = [(project, profile, _entry(project, profile))]

    async def fake_entries(principal, **kwargs):
        return entries

    monkeypatch.setattr(public_api, "_available_entries", fake_entries)
    with pytest.raises(public_api._ChatRequestError) as exc:
        asyncio.run(
            public_api._prepare_chat(
                _principal(require_protection=True),
                {"model": "acme/support", "messages": [{"role": "user", "content": "hi"}]},
            )
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "public_exposure_protection_required"


def test_prepare_chat_allows_an_unprotected_profile_when_not_required(monkeypatch):
    project = _project()
    profile = _profile(protected=False)
    entries = [(project, profile, _entry(project, profile))]

    async def fake_entries(principal, **kwargs):
        return entries

    monkeypatch.setattr(public_api, "_available_entries", fake_entries)
    messages, resolved_project, resolved_profile, entry = asyncio.run(
        public_api._prepare_chat(
            _principal(require_protection=False),
            {"model": "acme/support", "messages": [{"role": "user", "content": "hi"}]},
        )
    )
    assert resolved_profile.name == "support"
    assert messages[-1]["content"] == "hi"


def test_prepare_chat_enforces_the_profile_input_limit(monkeypatch):
    project = _project()
    profile = _profile(max_input=5)
    entries = [(project, profile, _entry(project, profile))]

    async def fake_entries(principal, **kwargs):
        return entries

    monkeypatch.setattr(public_api, "_available_entries", fake_entries)
    with pytest.raises(public_api._ChatRequestError) as exc:
        asyncio.run(
            public_api._prepare_chat(
                _principal(),
                {
                    "model": "acme/support",
                    "messages": [{"role": "user", "content": "123456"}],
                },
            )
        )
    assert exc.value.status_code == 413


def test_prepare_chat_reports_when_no_profile_is_reachable(monkeypatch):
    async def fake_entries(principal, **kwargs):
        return []

    monkeypatch.setattr(public_api, "_available_entries", fake_entries)
    with pytest.raises(public_api._ChatRequestError) as exc:
        asyncio.run(
            public_api._prepare_chat(
                _principal(), {"messages": [{"role": "user", "content": "hi"}]}
            )
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "no_exposed_profiles"


# ---------------------------------------------------------------------------
# OpenAI-shaped errors
# ---------------------------------------------------------------------------


def test_error_response_has_the_openai_shape():
    response = public_api._error(403, "insufficient_scope", "no")
    assert response.status_code == 403
    body = response.body.decode("utf-8")
    assert '"code":"insufficient_scope"' in body.replace(" ", "")
    assert '"type":"invalid_request_error"' in body.replace(" ", "")


def test_usage_is_positive_before_the_real_engine_is_used():
    usage = public_api._usage_for([{"role": "user", "content": "hello"}], "hi")
    assert usage["prompt_tokens"] >= 1
    assert usage["completion_tokens"] >= 1
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
