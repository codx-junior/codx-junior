"""Tests for the secrets resolver (``codx.junior.security.ciso.vault``).

The property under test is narrow and absolute: a resolved value must never
appear in the model-visible tool arguments, in a redacted result, in an audit
row, in ``repr()``/``str()`` or in the tool cache key. The fixtures are
obviously fake on purpose -- a test file must never need a real secret.
"""

import base64
import json
import os
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
    # No sink anywhere in this call, so the handle stays INERT text: the tool
    # would run with an unusable literal and no value is ever resolved, so
    # nothing can leak. The refusal happens as soon as a sink exists.
    assert result.error is None
    assert result.params["command"] == params["command"]
    assert FAKE not in str(result.params)
    assert result.notices

    # One usable sink in the call makes every other handle a mistake.
    both = {
        "command": f"curl https://example.test/{{{{env:{VAR}}}}}",
        "stdin": f"{{{{env:{VAR}}}}}",
    }
    refused = secret_vault.resolve_for_tool(both, "execute_command", policy=_policy())
    assert "denied_sink" in (refused.error or "")
    assert FAKE not in (refused.error or "")


def test_handle_mixed_with_text_is_denied(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"env": {"A": f"prefix-{{{{env:{VAR}}}}}"}}
    result = secret_vault.resolve_for_tool(params, "execute_command", policy=_policy())
    assert "denied_sink" in (result.error or "")


def test_denied_tool_never_receives_a_secret(monkeypatch):
    monkeypatch.setenv(VAR, FAKE)
    params = {"text": f"{{{{env:{VAR}}}}}"}
    result = secret_vault.resolve_for_tool(params, "fetch_webpage", policy=_policy())
    # A web tool has no sink: the handle stays inert, no value is resolved.
    assert result.error is None
    assert FAKE not in str(result.params)
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


def test_key_file_is_created_and_used_when_the_env_var_is_unset(monkeypatch, tmp_path):
    """A fresh install must work without anyone exporting a key."""
    key_file = tmp_path / "keys" / "vault.key"
    monkeypatch.delenv(secret_vault.VAULT_KEY_ENV, raising=False)
    monkeypatch.setenv(secret_vault.VAULT_KEY_FILE_ENV, str(key_file))
    vault = secret_vault.SecretVault(str(tmp_path), _policy())
    assert vault.set_value("ROUNDTRIP", FAKE, allow_tools=["execute_command"]) is None
    assert key_file.exists()
    assert (key_file.stat().st_mode & 0o777) == 0o600
    value, error = vault.get_value("secret", "ROUNDTRIP")
    assert error is None
    assert value == FAKE
    assert FAKE not in open(vault.vault_path, "r", encoding="utf-8").read()


def test_missing_key_degrades_instead_of_storing_plaintext(monkeypatch, tmp_path):
    monkeypatch.delenv(secret_vault.VAULT_KEY_ENV, raising=False)
    monkeypatch.setenv(secret_vault.VAULT_KEY_FILE_ENV, str(tmp_path / "absent.key"))
    vault = secret_vault.SecretVault(str(tmp_path), _policy())
    # The application READ path never invents a key: it degrades.
    assert vault.fernet()[0] is None
    value, error = vault.get_value("secret", "ANYTHING")
    assert value is None
    assert error.code == "vault_unavailable"
    assert not os.path.exists(vault.vault_path)


def _make_project(root, name: str):
    """Create a project marker so the vault treats the folder as a project."""
    project = root / name
    (project / ".codx").mkdir(parents=True, exist_ok=True)
    (project / ".codx" / "project.json").write_text("{}", encoding="utf-8")
    return project


def _isolated_vault_env(monkeypatch, tmp_path) -> None:
    """Keep the key file and the global store inside *tmp_path*."""
    monkeypatch.delenv(secret_vault.VAULT_KEY_ENV, raising=False)
    monkeypatch.setenv(secret_vault.VAULT_KEY_FILE_ENV, str(tmp_path / "keys" / "vault.key"))
    monkeypatch.setenv(secret_vault.GLOBAL_SECRETS_ENV, str(tmp_path / "global"))


def test_child_project_sees_a_parent_secret_only_when_shared(monkeypatch, tmp_path):
    _isolated_vault_env(monkeypatch, tmp_path)
    parent = _make_project(tmp_path, "parent")
    child = _make_project(parent, "child")
    sibling = _make_project(tmp_path, "sibling")

    parent_vault = secret_vault.SecretVault(str(parent), _policy())
    assert parent_vault.set_value(
        "PARENT_TOKEN", FAKE, allow_tools=["execute_command"], share_with_children=False
    ) is None

    child_vault = secret_vault.SecretVault(str(child), _policy())
    assert child_vault.parent_projects() == [str(parent)]
    assert child_vault.inherited_meta() == {}
    assert [meta.name for meta in child_vault.list("all")] == []
    value, error = child_vault.get_value(secret_vault.SOURCE_VAULT, "PARENT_TOKEN")
    assert value is None
    assert error.code == "missing"

    # The flag lives on the PARENT declaration; flipping it is all the child needs.
    parent_vault.declare("PARENT_TOKEN", allow_tools=["execute_command"], status="ready",
                         share_with_children=True)
    inherited = child_vault.inherited_meta()
    assert list(inherited) == ["PARENT_TOKEN"]
    assert inherited["PARENT_TOKEN"].inherited_from == "parent"
    value, error = child_vault.get_value(secret_vault.SOURCE_VAULT, "PARENT_TOKEN")
    assert error is None
    assert value == FAKE

    # A sibling folder is not a child: it must not see the shared secret either.
    sibling_vault = secret_vault.SecretVault(str(sibling), _policy())
    value, error = sibling_vault.get_value(secret_vault.SOURCE_VAULT, "PARENT_TOKEN")
    assert value is None
    assert error.code == "missing"


def test_child_declaration_wins_over_the_inherited_one(monkeypatch, tmp_path):
    _isolated_vault_env(monkeypatch, tmp_path)
    parent = _make_project(tmp_path, "parent")
    child = _make_project(parent, "child")
    parent_vault = secret_vault.SecretVault(str(parent), _policy())
    assert parent_vault.set_value("PARENT_TOKEN", FAKE, allow_tools=["execute_command"],
                                  share_with_children=True) is None

    child_vault = secret_vault.SecretVault(str(child), _policy())
    assert child_vault.set_value("PARENT_TOKEN", "child-value-9876") is None
    value, error = child_vault.get_value(secret_vault.SOURCE_VAULT, "PARENT_TOKEN")
    assert error is None
    assert value == "child-value-9876"
    # One row: the child's own declaration, not a duplicate inherited one.
    assert [(meta.name, meta.inherited_from) for meta in child_vault.list("all")] == [
        ("PARENT_TOKEN", "")
    ]


def test_inherited_secret_keeps_the_parent_grant(monkeypatch, tmp_path):
    _isolated_vault_env(monkeypatch, tmp_path)
    parent = _make_project(tmp_path, "parent")
    child = _make_project(parent, "child")
    parent_vault = secret_vault.SecretVault(str(parent), _policy())
    assert parent_vault.set_value("PARENT_TOKEN", FAKE, allow_tools=["execute_command"],
                                  share_with_children=True) is None

    child_vault = secret_vault.SecretVault(str(child), _policy())
    resolved = child_vault.resolve("PARENT_TOKEN", tool="execute_command", step_id="s1",
                                   sink="env", source="secret")
    assert resolved.value == FAKE
    denied = child_vault.resolve("PARENT_TOKEN", tool="fetch_webpage", step_id="s2",
                                 sink="env", source="secret")
    assert isinstance(denied, secret_vault.SecretError)
    assert denied.code == "denied_tool"
