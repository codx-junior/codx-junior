"""Docker Hub credential diagnostics (``codx.junior.tools.dockerhub_tools``).

The failure an operator must be able to tell apart is not "the push failed":
it is *nothing is configured* (store a secret) versus *a value is configured and
the registry refused it* (rotate it) versus *the hub could not be reached*
(nobody's fault). These tests pin those three messages, pin that a dry run
needs no value at all, and pin the vault source name -- the bug that made a
secret stored in Settings -> Secrets invisible to every docker tool.

The fixture value is obviously fake on purpose: a test must never need a real
credential.
"""

import pytest

from codx.junior.tools import dockerhub_tools as hub

FAKE_VALUE = "unit-test-value-not-a-real-token"

# The dev machine's vault may hold a real (or stale) value; the vault tests
# restore this to exercise the real lookup.
_ORIGINAL_VAULT_TOKEN = hub._vault_token


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test without a value in the environment, the vault or the cache."""
    monkeypatch.delenv(hub.TOKEN_NAME, raising=False)
    monkeypatch.delenv("DOCKERHUB_USER", raising=False)
    hub._TOKEN_CACHE.clear()
    # Hermetic: a value stored in this host's vault (even a stale one) would
    # make the outcome depend on the machine instead of on the code.
    monkeypatch.setattr(hub, "_vault_token", lambda settings=None: "")
    yield
    hub._TOKEN_CACHE.clear()


class _FakeProcess:
    """Stand-in for the child ``dockerhub_publish`` spawns with ``Popen``.

    The tool streams the build into its own log file instead of a pipe, so a test
    must replace ``subprocess.Popen`` (not ``subprocess.run``) to observe what the
    child actually receives.
    """

    pid = 4242

    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode


def _fake_popen(on_spawn=None):
    """Build a ``Popen`` replacement that records the spawn and exits 0."""

    def spawn(*args, **kwargs):
        if on_spawn is not None:
            on_spawn(*args, **kwargs)
        return _FakeProcess()

    return spawn


def test_missing_value_points_at_the_secrets_ui():
    """Nothing configured -> the message names where to store it."""
    message = hub._require_token("publishing")
    assert message is not None
    assert "is not configured" in message
    assert "Settings > Secrets" in message


def test_rejected_value_is_a_different_message(monkeypatch):
    """Configured but refused -> 'rejected', never 'not configured'."""
    monkeypatch.setenv(hub.TOKEN_NAME, FAKE_VALUE)

    def boom(*args, **kwargs):
        raise hub.HubAuthError("Docker Hub API GET user/ -> HTTP 401: unauthorized")

    monkeypatch.setattr(hub, "_api", boom)
    message = hub._require_token("publishing")
    assert "rejected" in message
    assert "is not configured" not in message
    assert "401" in message
    assert FAKE_VALUE not in message


def test_accepted_value_needs_no_error(monkeypatch):
    """A healthy value produces no complaint and no API noise."""
    monkeypatch.setenv(hub.TOKEN_NAME, FAKE_VALUE)
    monkeypatch.setattr(hub, "_api", lambda *args, **kwargs: {"username": "someone"})
    assert hub._require_token("publishing") is None


def test_unreachable_hub_does_not_block(monkeypatch):
    """An unreachable hub is not evidence against the value."""
    monkeypatch.setenv(hub.TOKEN_NAME, FAKE_VALUE)

    def down(*args, **kwargs):
        raise hub.HubNetworkError("Docker Hub API unreachable: timed out")

    monkeypatch.setattr(hub, "_api", down)
    assert hub._require_token("publishing") is None


def test_vault_lookup_uses_the_vault_source_name(monkeypatch, tmp_path):
    """The vault must be read with SOURCE_VAULT ('secret'), not the word 'vault'."""
    seen = {}

    class FakeVault:
        def __init__(self, *args, **kwargs):
            pass

        def get_value(self, source, name):
            seen["source"] = source
            seen["name"] = name
            return FAKE_VALUE, None

    from codx.junior.security.ciso import vault as secret_vault

    monkeypatch.setattr(hub, "_vault_token", _ORIGINAL_VAULT_TOKEN)
    monkeypatch.setattr(secret_vault, "SecretVault", FakeVault)
    monkeypatch.setattr(hub, "_project_dir", lambda settings: str(tmp_path))
    assert hub._vault_token(None) == FAKE_VALUE
    assert seen == {"source": secret_vault.SOURCE_VAULT, "name": hub.TOKEN_NAME}


def test_a_vault_error_reads_as_missing_not_as_a_value(monkeypatch, tmp_path):
    """An unreadable vault must degrade to empty, never to a stray value."""

    class BrokenVault:
        def __init__(self, *args, **kwargs):
            pass

        def get_value(self, source, name):
            return None, object()

    from codx.junior.security.ciso import vault as secret_vault

    monkeypatch.setattr(hub, "_vault_token", _ORIGINAL_VAULT_TOKEN)
    monkeypatch.setattr(secret_vault, "SecretVault", BrokenVault)
    monkeypatch.setattr(hub, "_project_dir", lambda settings: str(tmp_path))
    assert hub._vault_token(None) == ""


def test_a_miss_is_not_cached(monkeypatch):
    """A secret added after a failed attempt must be seen by the next call."""
    calls = []

    def lazy_vault(settings=None):
        calls.append(1)
        return FAKE_VALUE if len(calls) > 1 else ""

    monkeypatch.setattr(hub, "_vault_token", lazy_vault)
    assert hub._token() == ""
    assert hub._token() == FAKE_VALUE


def test_dry_run_never_asks_for_a_value(monkeypatch, tmp_path):
    """Regression: dry_run=true used to fail demanding the value."""
    script = tmp_path / "scripts" / "dockerhub_publish.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(hub, "_project_dir", lambda settings: str(tmp_path))
    monkeypatch.setattr(hub, "_require_token", lambda action: "must not be called")

    monkeypatch.setattr(hub.subprocess, "Popen", _fake_popen())
    response = hub.dockerhub_publish(tags=["v0.0.1"], dry_run=True)
    assert "must not be called" not in response.llm_response
    assert "Dry run" in response.user_response
    assert "not needed for a dry run" in response.user_response


def test_publish_reports_the_missing_value_and_does_not_build(monkeypatch, tmp_path):
    """A refused preflight must not reach subprocess at all."""
    script = tmp_path / "scripts" / "dockerhub_publish.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(hub, "_project_dir", lambda settings: str(tmp_path))

    def no_build(*args, **kwargs):
        raise AssertionError("the build must not run without a value")

    monkeypatch.setattr(hub.subprocess, "Popen", no_build)
    response = hub.dockerhub_publish(tags=["v0.0.1"])
    assert "Cannot publish" in response.user_response
    assert "is not configured" in response.user_response


def test_publish_hands_the_vault_value_to_the_script(monkeypatch, tmp_path):
    """Regression: a value stored in Settings never reached `docker login`.

    The script authenticates with ``DOCKERHUB_TOKEN`` taken from its environment,
    but the tool built that environment from ``os.environ`` only. A value living
    in the vault was therefore invisible to the child, `login()` skipped and the
    push ran anonymously.
    """
    script = tmp_path / "scripts" / "dockerhub_publish.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(hub, "_project_dir", lambda settings: str(tmp_path))
    monkeypatch.setattr(hub, "_vault_token", lambda settings=None: FAKE_VALUE)
    monkeypatch.setattr(hub, "_require_token", lambda action: None)
    # Hermetic: the registry exchange is not what this test is about.
    monkeypatch.setattr(hub, "_registry_check", lambda value: ("ok", "accepted"))
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        return _FakeProcess()

    monkeypatch.setattr(hub.subprocess, "Popen", capture)
    hub.dockerhub_publish(tags=["v0.0.1"])
    assert seen.get(hub.TOKEN_NAME) == FAKE_VALUE


def test_a_healthy_value_is_never_truncated(monkeypatch):
    """The stored value wins whenever the registry accepts it as it is."""
    monkeypatch.setenv(hub.TOKEN_NAME, FAKE_VALUE)
    monkeypatch.setattr(hub, "_registry_check", lambda value: ("ok", "accepted"))
    assert hub._registry_verdict()[0] == "ok"
    assert hub._token() == FAKE_VALUE


def test_trailing_junk_is_dropped_only_when_the_short_value_is_accepted(monkeypatch):
    """A pasted value can keep 4 trailing characters: drop them, but verify."""
    stored = FAKE_VALUE + "j7OA"
    probed = []

    def check(value):
        probed.append(value)
        return ("ok", "accepted") if value == FAKE_VALUE else ("refused", "HTTP 401")

    monkeypatch.setenv(hub.TOKEN_NAME, stored)
    monkeypatch.setattr(hub, "_registry_check", check)
    verdict, detail = hub._registry_verdict()
    assert verdict == "ok"
    assert "last 4" in detail
    assert probed == [stored, FAKE_VALUE]
    assert hub._token() == FAKE_VALUE
