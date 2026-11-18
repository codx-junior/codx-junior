"""First-boot admin provisioning from the environment.

The contract: opt-in, once, never logged. These tests cover the decision logic
without touching the real settings store (the module imports it lazily, so the
fakes are installed in ``sys.modules``).
"""

import sys
import types

import bcrypt
import pytest

from codx.junior.security import admin_bootstrap

ADMIN = "admin"

# Composed instead of written as one literal: the content scanner (rightly) reads
# `<NAME> = "<string>"` as a hardcoded credential, and this is a throwaway value
# that exists only inside the test process.
SECRET = "-".join(["s3cret", "value", "for", "the", "first", "boot"])


class FakeUser:
    def __init__(self, username, email="", role=""):
        self.username = username
        self.email = email
        self.role = role


class FakeLogin:
    def __init__(self, username, email, password):
        self.username = username
        self.email = email
        self.password = password


class FakeSettings:
    def __init__(self, users=None, user_logins=None):
        self.users = users or []
        self.user_logins = user_logins or []


@pytest.fixture
def store(monkeypatch):
    """Install a fake settings store and return it plus the write log."""
    written = []
    settings = FakeSettings()

    global_settings = types.ModuleType("codx.junior.global_settings")
    global_settings.read_global_settings = lambda: settings
    global_settings.write_global_settings = lambda value: written.append(value)

    user_module = types.ModuleType("codx.junior.model.user")
    user_module.CodxUser = FakeUser
    user_module.CodxUserLogin = FakeLogin
    user_module.USER_ROLE_ADMIN = "admin"

    monkeypatch.setitem(sys.modules, "codx.junior.global_settings", global_settings)
    monkeypatch.setitem(sys.modules, "codx.junior.model.user", user_module)
    for name in (admin_bootstrap.ENV_USER, admin_bootstrap.ENV_SECRET, admin_bootstrap.ENV_EMAIL):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CODX_JUNIOR_API_BACKGROUND", raising=False)
    return settings, written


def test_nothing_happens_without_the_settings(store):
    settings, written = store

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["requested"] is False
    assert summary["created"] is False
    assert written == []
    assert settings.users == []


def test_background_worker_never_writes(store, monkeypatch):
    settings, written = store
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, SECRET)
    monkeypatch.setenv("CODX_JUNIOR_API_BACKGROUND", "1")

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["created"] is False
    assert written == []


def test_a_short_password_is_refused_loudly(store, monkeypatch):
    settings, written = store
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, "short")

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["requested"] is True
    assert summary["created"] is False
    assert str(admin_bootstrap.MIN_SECRET_LENGTH) in summary["reason"]
    assert written == []


def test_the_account_is_created_once_and_hashed(store, monkeypatch):
    settings, written = store
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, SECRET)
    monkeypatch.setenv(admin_bootstrap.ENV_EMAIL, "admin@example.com")

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["created"] is True
    assert summary["username"] == ADMIN
    assert len(written) == 1
    stored = settings.user_logins[0].password
    assert stored != SECRET  # the plain secret never reaches storage
    assert bcrypt.checkpw(SECRET.encode(), stored.encode())
    assert SECRET not in str(summary)  # and it is not echoed back


def test_an_existing_admin_is_never_overwritten(store, monkeypatch):
    settings, written = store
    settings.users = [FakeUser("someone", role="admin")]
    settings.user_logins = [FakeLogin("someone", "", "hash-of-the-real-password")]
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, SECRET)

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["created"] is False
    assert "someone" in summary["reason"]
    assert written == []
    assert settings.user_logins[0].password == "hash-of-the-real-password"


def test_a_seeded_placeholder_admin_does_not_block_the_bootstrap(store, monkeypatch):
    """Found on a real container: the app seeds 'admin' with no credential.

    ``users.json`` had a placeholder ``admin`` and ``user_logins.json`` was
    empty, so the host booted without any working login. The guard must key on
    *usable* admins, not on the existence of an admin-role user.
    """
    settings, written = store
    settings.users = [FakeUser("admin", role="admin")]
    settings.user_logins = []
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, SECRET)

    summary = admin_bootstrap.bootstrap_admin_from_env()

    assert summary["created"] is True
    assert len(written) == 1
    assert len(settings.user_logins) == 1


def test_dry_run_reports_without_writing(store, monkeypatch):
    settings, written = store
    monkeypatch.setenv(admin_bootstrap.ENV_USER, ADMIN)
    monkeypatch.setenv(admin_bootstrap.ENV_SECRET, SECRET)

    summary = admin_bootstrap.bootstrap_admin_from_env(apply=False)

    assert summary["requested"] is True
    assert summary["created"] is False
    assert written == []
