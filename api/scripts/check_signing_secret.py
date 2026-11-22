"""
Runtime check of the JWT/HMAC signing secret.

Prints ONLY lengths and a fingerprint: the secret itself is never written to
stdout, so the output can be pasted in a ticket or a chat.

Usage (inside the API container):

    python3 api/scripts/check_signing_secret.py

Exit code 0 when the secret is usable (>= 32 bytes, not a shipped legacy
placeholder), 1 otherwise.
"""
import io
import json
import os
import sys
import warnings

import jwt

LEGACY = "codx-junior-rules"
DEFAULT_PATH = "/home/codx-junior/.codx-junior/settings/secret.json"


def main(path: str) -> int:
    """Report the persisted signing secret health.

    Args:
        path: Path of ``settings/secret.json``.

    Returns:
        0 when the secret is usable, 1 otherwise.
    """
    if not os.path.exists(path):
        print(f"secret file not found: {path}")
        return 1

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    # The file is either a bare JSON string (the current format) or an object
    # with a ``secret`` key (older/newer writers).
    if isinstance(data, str):
        data = {"secret": data}
    elif not isinstance(data, dict):
        data = {"secret": ""}

    secret = (data.get("secret") or "").strip()
    size = len(secret.encode("utf-8"))
    legacy = secret == LEGACY
    fingerprint = __import__("hashlib").sha256(
        secret.encode("utf-8")
    ).hexdigest()[:12]

    print(f"file          : {path} ({os.path.getsize(path)} bytes on disk)")
    print(f"secret bytes  : {size}")
    print(f"fingerprint   : sha256:{fingerprint}")
    print(f"legacy default: {legacy}")
    print(f"file secret   : {'uuid' if 'uuid' in data else '-'}")
    print(f"sections      : {sorted(data.keys())}")

    ok = size >= 32 and not legacy

    # A short key makes pyjwt emit InsecureKeyLengthWarning on encode/decode.
    caught = []
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        token = jwt.encode({"sub": "probe"}, secret, algorithm="HS256")
        back = jwt.decode(token, secret, algorithms=["HS256"])
        caught = [str(item.message) for item in recorded]

    print(f"token roundtrip: sub={back.get('sub')!r} len={len(token)}")
    print(f"jwt warnings  : {caught or 'none'}")
    print(f"verdict       : {'OK' if ok and not caught else 'INSECURE'}")
    return 0 if ok and not caught else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH))
