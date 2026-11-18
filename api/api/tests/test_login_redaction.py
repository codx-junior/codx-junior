"""Regression tests: a login must never write the credential to the log.

Found by testing the built image, not by unit tests: ``users.py`` logged the
request body and ``user_management.py`` logged the whole user model, so every
successful login wrote the plaintext password -- including the one provisioned
from ``settings.env`` -- into the container log. These tests keep it out.
"""
import inspect
import json
import uuid

from codx.junior.model.user import CodxUserLogin
from codx.junior.security import user_management
from codx.junior.security.user_management import redact_user
from codx.junior.api import users as users_module

# Generated, never a real credential: the value only has to be recognisable.
SECRET = f"test-{uuid.uuid4().hex}"
SESSION = f"session-{uuid.uuid4().hex}"


def test_redact_user_hides_the_password():
    data = redact_user(CodxUserLogin(username="admin", password=SECRET, email="a@b.c"))

    assert data["password"] == "<redacted>"
    assert SECRET not in json.dumps(data)


def test_redact_user_hides_the_token():
    data = redact_user(CodxUserLogin(username="admin", token=SESSION))

    assert data["token"].startswith("<token sha256:")
    assert SESSION not in json.dumps(data)


def test_redact_user_tolerates_missing_values():
    assert redact_user(None) == {}
    data = redact_user(CodxUserLogin(username="admin"))
    assert data["username"] == "admin"
    assert data.get("password") in (None, "", "<redacted>")


def test_no_log_statement_renders_a_raw_user():
    """A bare ``{user}`` in a log line reintroduces the leak; the redacted
    helper must be used instead."""
    offenders = []
    for module in (user_management, users_module):
        for number, line in enumerate(inspect.getsource(module).splitlines(), 1):
            if "logger." in line or "logging.error" in line or "logging.info" in line:
                if "{user}" in line:
                    offenders.append(f"{module.__name__}:{number}: {line.strip()}")
    assert offenders == []
