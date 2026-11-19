"""The secrets syntax must reach the model through the system prompt.

A tool schema is only visible to a model that already decided to call the tool.
The handle syntax therefore belongs to the global system instructions
(``SECRETS_SYSTEM_RULES`` + ``_build_secrets_instructions``), otherwise the
model asks the user for the credential and the value lands in the transcript.
"""

import inspect

import pytest

from codx.junior.security.ciso import vault as secret_vault

FAKE = "prompt-test-value-9876"


def _instructions():
    """Import the prompt builder (heavy module) only for these tests."""
    from codx.junior.ai.smol.smol_agent import _build_secrets_instructions

    return _build_secrets_instructions


def test_rules_document_the_handle_syntax():
    from codx.junior.ai.smol.smol_agent import SECRETS_SYSTEM_RULES

    # Both sources and the only accepted destination must be spelled out.
    assert "{{secret:NAME}}" in SECRETS_SYSTEM_RULES
    assert "{{env:NAME}}" in SECRETS_SYSTEM_RULES
    assert "stdin" in SECRETS_SYSTEM_RULES
    assert "denied_sink" in SECRETS_SYSTEM_RULES
    # And the two tools the model is told to use instead of asking for a value.
    assert "secrets_list" in SECRETS_SYSTEM_RULES
    assert "secrets_request" in SECRETS_SYSTEM_RULES


def test_rules_are_wired_into_the_system_message():
    """``_build_system_message`` is the only place the model reads instructions."""
    from codx.junior.ai.smol import smol_agent

    build = inspect.getsource(smol_agent.SmolAgent._build_system_message)
    assert "_build_secrets_instructions" in build


def test_no_secret_declared_points_at_secrets_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(secret_vault.GLOBAL_SECRETS_ENV, str(tmp_path / "global"))
    text = _instructions()(None)
    assert "No secret is declared" in text
    assert "secrets_request" in text


def test_declared_names_are_listed_without_any_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(secret_vault.GLOBAL_SECRETS_ENV, str(tmp_path / "global"))
    vault = secret_vault.SecretVault(str(tmp_path))
    vault.declare("PROMPT_TEST_TOKEN", allow_tools=["execute_command"])

    text = _instructions()(None)
    assert "PROMPT_TEST_TOKEN" in text
    assert "execute_command" in text
    assert FAKE not in text


def test_a_stored_value_never_reaches_the_prompt(tmp_path, monkeypatch):
    """Even with a real value in the vault, the prompt carries the name only."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(secret_vault.GLOBAL_SECRETS_ENV, str(tmp_path / "global"))
    vault = secret_vault.SecretVault(str(tmp_path))
    error = vault.set_value("PROMPT_STORED_TOKEN", FAKE, allow_tools=["execute_command"])
    if error is not None:
        pytest.skip(f"vault source unavailable here: {error.code}")

    text = _instructions()(None)
    assert "PROMPT_STORED_TOKEN" in text
    assert FAKE not in text


def test_policy_disabled_removes_the_block(tmp_path, monkeypatch):
    from codx.junior.security.ciso.models import CisoPolicy, SecretPolicy

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(secret_vault.GLOBAL_SECRETS_ENV, str(tmp_path / "global"))
    monkeypatch.setattr(
        secret_vault,
        "vault_for",
        lambda settings=None, policy=None: secret_vault.SecretVault(
            str(tmp_path), CisoPolicy(secrets=SecretPolicy(enabled=False))
        ),
    )
    assert _instructions()(None) == ""
