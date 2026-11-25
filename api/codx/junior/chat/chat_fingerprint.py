"""Stable fingerprint of a chat message document.

Both the write path (``chat_document``) and the disposable index
(``chat_index``) must agree on what "this exact message" means, so the
fingerprint lives in its own stdlib-only module: the index must stay importable
without pydantic or the rest of codx-junior.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def message_fingerprint(message: Dict[str, Any]) -> str:
    """Return a deterministic SHA-256 of a JSON message document.

    Keys are sorted so the same message serialised in a different order (or
    loaded from a file written by another version) gets the same fingerprint.

    :param message: JSON-serialisable message dict.
    :return: Hex-encoded SHA-256 digest.
    """
    canonical = json.dumps(
        message, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
