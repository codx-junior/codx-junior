"""The JWT/HMAC signing secret must be >= 32 bytes and never a shipped default.

Regression guard for the ``InsecureKeyLengthWarning`` PyJWT logged on every
login: ``GlobalSettings.secret`` used to default to the literal
``"codx-junior-rules"`` — 17 bytes, published in the source tree and therefore
shared by every installation (a token forged on one deployment validated on all
of them). RFC 7518 §3.2 requires at least 32 bytes for HS256.
"""

import json
import warnings

import jwt
import pytest

from codx.junior import global_settings as global_settings_module
from codx.junior.security.secret_key import (
    LEGACY_INSECURE_SECRETS,
    MIN_SECRET_BYTES,
    REASON_LEGACY,
    REASON_MISSING,
    REASON_TOO_SHORT,
    assess_secret,
    generate_secret,
)

LEGACY_PLACEHOLDER = "codx-junior-rules"


def _pyjwt_insecure_warnings(recorded):
    """The PyJWT warnings that motivated this fix (matched by class name)."""
    return [
        record
        for record in recorded
        if type(record.message).__name__ == "InsecureKeyLengthWarning"
    ]


# ---------------------------------------------------------------------------
# Policy: what may be used to sign tokens
# ---------------------------------------------------------------------------

def test_generated_secret_is_long_enough():
    secret = generate_secret()
    assert len(secret.encode("utf-8")) >= MIN_SECRET_BYTES
    assert assess_secret(secret).ok


def test_two_generated_secrets_differ():
    assert generate_secret() != generate_secret()


def test_the_old_17_byte_placeholder_is_rejected():
    assessment = assess_secret(LEGACY_PLACEHOLDER)
    assert assessment.byte_length == 17
    assert assessment.legacy
    assert not assessment.ok
    assert assessment.reason == REASON_LEGACY
    # Never log the secret itself, only its fingerprint.
    assert LEGACY_PLACEHOLDER not in assessment.describe()
    for legacy in LEGACY_INSECURE_SECRETS:
        assert not assess_secret(legacy).ok


def test_missing_secret_is_rejected():
    for empty in (None, ""):
        assessment = assess_secret(empty)
        assert not assessment.ok
        assert assessment.reason == REASON_MISSING


def test_short_secret_is_rejected():
    assessment = assess_secret("x" * (MIN_SECRET_BYTES - 1))
    assert not assessment.ok
    assert assessment.reason == REASON_TOO_SHORT


def test_minimum_length_secret_is_accepted():
    assert assess_secret("x" * MIN_SECRET_BYTES).ok


# ---------------------------------------------------------------------------
# The warning itself
# ---------------------------------------------------------------------------

def test_generated_secret_does_not_trigger_pyjwt_warning():
    secret = generate_secret()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        token = jwt.encode({"username": "admin"}, secret, algorithm="HS256")
        assert jwt.decode(token, secret, algorithms=["HS256"])["username"] == "admin"
    assert _pyjwt_insecure_warnings(recorded) == []


def test_legacy_placeholder_triggers_pyjwt_warning():
    """The premise of the fix: PyJWT warns on the old 17-byte default."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        jwt.encode({"username": "admin"}, LEGACY_PLACEHOLDER, algorithm="HS256")
    assert _pyjwt_insecure_warnings(recorded)


# ---------------------------------------------------------------------------
# Migration / startup behaviour (isolated settings store)
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_settings(tmp_path, monkeypatch):
    """Settings store in a temporary folder, seeded with the legacy secret."""
    monkeypatch.setenv("CODX_JUNIOR_CONFIG_FOLDER", str(tmp_path))
    monkeypatch.setattr(global_settings_module, "_manager", None)
    monkeypatch.setattr(global_settings_module, "_GLOBAL_SETTINGS_CACHE", None)
    monkeypatch.setattr(global_settings_module, "_GLOBAL_SETTINGS_CACHED_AT", 0.0)
    monkeypatch.setattr(global_settings_module, "GLOBAL_SETTINGS", None)
    manager = global_settings_module.get_manager()
    manager.write_section("secret", LEGACY_PLACEHOLDER)
    global_settings_module.invalidate_global_settings_cache()
    return manager


def _stored_secret(manager):
    return json.loads(
        (manager.settings_dir / "secret.json").read_text(encoding="utf-8")
    )


def test_ensure_signing_secret_replaces_and_persists_the_placeholder(isolated_settings):
    secret = global_settings_module.ensure_signing_secret(
        persist=True, raise_on_persist_failure=True
    )

    assert assess_secret(secret).ok
    assert _stored_secret(isolated_settings) == secret
    # Idempotent: a stored secret is reused, never rotated on every call.
    assert global_settings_module.ensure_signing_secret(persist=True) == secret


def test_ensure_signing_secret_updates_the_caller_snapshot(isolated_settings):
    """A long-lived settings snapshot (UserSecurityManager) must not stay weak."""
    settings = global_settings_module.read_global_settings(refresh=True)
    assert not assess_secret(settings.secret).ok

    secret = global_settings_module.ensure_signing_secret(
        settings=settings, persist=True
    )

    assert settings.secret == secret
    assert assess_secret(settings.secret).ok
    assert global_settings_module.ensure_signing_secret(settings=settings) == secret


def test_write_global_settings_never_persists_a_placeholder(isolated_settings):
    """Saving all sections used to write the default secret back to disk."""
    settings = global_settings_module.read_global_settings(refresh=True)
    settings.secret = LEGACY_PLACEHOLDER

    global_settings_module.write_global_settings(settings)

    stored = _stored_secret(isolated_settings)
    assert assess_secret(stored).ok
    assert stored == settings.secret


def test_validate_signing_secret_at_startup_returns_a_usable_secret(isolated_settings):
    secret = global_settings_module.validate_signing_secret_at_startup()

    assert assess_secret(secret).ok
    assert _stored_secret(isolated_settings) == secret
