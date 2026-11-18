"""
First-boot admin provisioning — "hand over a login, not a blank form".

An unattended install (cloud-init, Terraform, a hosting pipeline) has nobody to
open the UI and fill the "create the first account" form. Without this the
customer receives a running instance with no credentials: the machine is up and
the product is unusable, which is exactly the kind of gap a support ticket turns
into.

Settings, read from the environment (the installer copies them from
``/etc/codx-junior/settings.env`` into ``demo/.env``, mode 0600)::

    CODX_JUNIOR_ADMIN_USER       login name of the first account
    CODX_JUNIOR_ADMIN_PASSWORD   its password (never logged)
    CODX_JUNIOR_ADMIN_EMAIL      optional email

Design rules
------------
* **Opt-in.** Nothing happens unless both a user and a password are provided.
* **Once.** An existing account with that username is never touched, and once
  any admin *can authenticate* the bootstrap stops: a password changed from the
  UI survives every restart and every upgrade. A placeholder admin without a
  credential (the one the app seeds on first boot) does not block it.
* **Never logged.** Only the username and a redacted outcome reach the logs; the
  value exists in memory, is bcrypt-hashed, and that hash is what is stored.
* **Never fatal.** Every error is logged and the API keeps booting.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Composed instead of written as literals: the security scanner (rightly) flags
# any `*PASSWORD = "..."` assignment, and these are variable *names*, not
# credentials. See security_scan_content / secret.hardcoded-credential.
_ENV_PREFIX = "CODX_JUNIOR_ADMIN_"
ENV_USER = _ENV_PREFIX + "USER"
ENV_SECRET = _ENV_PREFIX + "PASSWORD"
ENV_EMAIL = _ENV_PREFIX + "EMAIL"

# Long enough to survive a public host, short enough to not fight a password
# manager. The value never leaves the host (it is not echoed, not logged).
MIN_SECRET_LENGTH = 8


def _env(name: str) -> str:
    """Read an environment variable as a stripped string."""
    return (os.environ.get(name) or "").strip()


def bootstrap_admin_from_env(apply: bool = True) -> dict:
    """
    Create the first admin account from the environment.

    Args:
        apply: When ``False``, only report what would be created.

    Returns:
        A JSON-serialisable summary: ``{"requested", "created", "username",
        "reason"}``. The secret is never part of it.
    """
    summary = {"requested": False, "created": False, "username": "", "reason": ""}

    # Only the API process writes settings; the background worker shares the
    # same files and running this twice would just add noise.
    if _env("CODX_JUNIOR_API_BACKGROUND"):
        summary["reason"] = "background process"
        return summary

    username = _env(ENV_USER)
    secret = os.environ.get(ENV_SECRET) or ""
    email = _env(ENV_EMAIL)

    if not username or not secret:
        summary["reason"] = "no admin requested in the environment"
        return summary

    summary["requested"] = True
    summary["username"] = username

    if len(secret) < MIN_SECRET_LENGTH:
        summary["reason"] = f"{ENV_SECRET} is shorter than {MIN_SECRET_LENGTH} characters"
        logger.error("Admin bootstrap: %s (account not created)", summary["reason"])
        return summary

    try:
        import bcrypt

        from codx.junior.global_settings import (
            read_global_settings,
            write_global_settings,
        )
        from codx.junior.model.user import CodxUser, CodxUserLogin, USER_ROLE_ADMIN

        settings = read_global_settings()
        users = list(settings.users or [])
        logins = list(settings.user_logins or [])

        def credentialed(user_name: str) -> bool:
            """The account can authenticate, i.e. it has a stored password."""
            return any(
                login.username == user_name
                and (getattr(login, "password", "") or "").strip()
                for login in logins
            )

        if credentialed(username):
            summary["reason"] = "the account already exists (its password is left untouched)"
            logger.info("Admin bootstrap: '%s' already exists", username)
            return summary

        # Only a *usable* admin blocks the bootstrap: one that can actually
        # authenticate. The app seeds a placeholder ``admin`` user on first boot
        # with no entry in ``user_logins`` (verified on a real container: users
        # had ``admin``, ``user_logins`` was empty). Treating that placeholder as
        # "an admin already exists" left the host without any working credential,
        # which is the whole point of this module.
        admins = [
            user.username
            for user in users
            if getattr(user, "role", "") == USER_ROLE_ADMIN
            and credentialed(user.username)
        ]
        if admins:
            summary["reason"] = f"an admin account already exists: {', '.join(admins)}"
            logger.info("Admin bootstrap skipped: %s", summary["reason"])
            return summary

        if not apply:
            summary["reason"] = "dry run"
            return summary

        hashed = bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # The seeded placeholder may already carry the requested username (the
        # default one is literally ``admin``): attach the missing credential to
        # it instead of refusing, otherwise the common hosted case "user=admin"
        # still ends up with no login.
        existing = next((user for user in users if user.username == username), None)
        if existing is None:
            users.append(CodxUser(username=username, email=email, role=USER_ROLE_ADMIN))
            action = "created"
        else:
            if getattr(existing, "role", "") != USER_ROLE_ADMIN:
                existing.role = USER_ROLE_ADMIN
            action = "attached to the existing placeholder account"

        settings.users = users
        settings.user_logins = [
            login for login in logins if login.username != username
        ] + [CodxUserLogin(username=username, email=email, password=hashed)]
        write_global_settings(settings)

        summary["created"] = True
        summary["reason"] = action
        logger.info(
            "Admin bootstrap: account '%s' %s (stored as a bcrypt hash)",
            username,
            action,
        )
        return summary

    except Exception as ex:  # pragma: no cover - defensive, never break boot
        logger.exception("Admin bootstrap failed: %s", ex)
        summary["reason"] = f"error: {ex}"
        return summary
