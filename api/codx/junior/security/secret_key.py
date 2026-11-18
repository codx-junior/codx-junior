"""
JWT/HMAC signing secret policy for codx-junior.

``GlobalSettings.secret`` signs every session token (HS256, see
``UserSecurityManager.get_user_token``). It used to default to the literal
``"codx-junior-rules"``: 17 bytes, below the 32 bytes RFC 7518 §3.2 requires for
HMAC-SHA256 and — worse — a value shipped in the source tree, so every
installation shared the same signing key. PyJWT emits
``InsecureKeyLengthWarning`` on every encode/decode with it.

This module owns the *what*: what a usable secret is, how a good one is
generated, and how it is described in logs (fingerprint only, never the value).
Persistence stays in ``codx.junior.global_settings`` so this module has no
dependency on the settings layer and can be imported from anywhere.

Made with ❤️ by codx-junior
"""

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

# RFC 7518 §3.2: "A key of the same size as the hash output (for instance, 256
# bits for HS256) or larger MUST be used". 256 bits == 32 bytes. PyJWT warns
# (InsecureKeyLengthWarning) below this length.
MIN_SECRET_BYTES = 32

# ``secrets.token_urlsafe`` yields ~1.33 ASCII chars per entropy byte, so 48
# random bytes produce a 64 character secret: comfortably above the minimum and
# long enough that two independently generated secrets never collide.
GENERATED_SECRET_ENTROPY_BYTES = 48

# Values that shipped as a default in this project (or that are obvious
# placeholders). They are rejected regardless of length: a value that is
# published in the source tree is not a secret. The 17-byte
# "codx-junior-rules" is the one that produced the original
# InsecureKeyLengthWarning.
LEGACY_INSECURE_SECRETS = frozenset(
    {
        "codx-junior-rules",
        "codx-junior",
        "changeme",
        "change-me",
        "secret",
    }
)

# Hex chars of the sha256 kept for logs: enough to tell two secrets apart when
# debugging key rotation, useless for recovering the key.
FINGERPRINT_CHARS = 12

REASON_OK = "ok"
REASON_MISSING = "missing"
REASON_LEGACY = "legacy-default"
REASON_TOO_SHORT = "too-short"


@dataclass(frozen=True)
class SecretAssessment:
    """Verdict about one secret value. Never carries the secret itself."""

    ok: bool
    reason: str
    byte_length: int
    legacy: bool
    fingerprint: str

    def describe(self) -> str:
        """One-line, log-safe description including the minimum length."""
        return (
            f"{self.byte_length} bytes (minimum {MIN_SECRET_BYTES}), "
            f"fingerprint sha256:{self.fingerprint or '-'}, {self.reason}"
        )


def generate_secret() -> str:
    """Return a fresh signing secret well above :data:`MIN_SECRET_BYTES`."""
    return secrets.token_urlsafe(GENERATED_SECRET_ENTROPY_BYTES)


def secret_byte_length(secret: Optional[str]) -> int:
    """Length of *secret* in UTF-8 bytes (0 for None/empty)."""
    return len((secret or "").encode("utf-8"))


def secret_fingerprint(secret: Optional[str]) -> str:
    """Short sha256 of *secret*, safe to log; ``""`` when there is no secret."""
    if not secret:
        return ""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def is_legacy_secret(secret: Optional[str]) -> bool:
    """True when *secret* is one of the shipped/placeholder values."""
    return (secret or "").strip().lower() in LEGACY_INSECURE_SECRETS


def assess_secret(secret: Optional[str]) -> SecretAssessment:
    """Decide whether *secret* may be used to sign session tokens."""
    length = secret_byte_length(secret)
    legacy = is_legacy_secret(secret)
    if not secret:
        reason, ok = REASON_MISSING, False
    elif legacy:
        reason, ok = REASON_LEGACY, False
    elif length < MIN_SECRET_BYTES:
        reason, ok = REASON_TOO_SHORT, False
    else:
        reason, ok = REASON_OK, True
    return SecretAssessment(
        ok=ok,
        reason=reason,
        byte_length=length,
        legacy=legacy,
        fingerprint=secret_fingerprint(secret),
    )


def is_secret_usable(secret: Optional[str]) -> bool:
    """Shortcut for :func:`assess_secret`."""
    return assess_secret(secret).ok
