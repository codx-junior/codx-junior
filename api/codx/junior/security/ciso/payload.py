"""Recursive payload sanitisation.

Two boundaries of the platform do not handle a single string: they handle a
*structure* that is serialised (or sent) whole.

* **Persistence into ``.codx``** — a task card is
  ``<project>/.codx/tasks/<board>/<column>/<slug>.<id>.json``, a git-tracked
  artifact. Everything the conversation touched ends up inside it: messages,
  streamed answers, and the ``tool_events`` carrying the output of a tool that
  printed ``.git/config``, an environment dump or a container log. A single
  credential in any of those strings becomes permanent repository history.
* **The prompt sent to the AI** — the same structure, in the form of
  LangChain messages, leaves the platform to a third party model provider.

``redact_payload`` walks such a structure and neutralises every detected
secret, so the caller keeps one line of code instead of a per-field audit.

Binary payloads (``base64_data`` of a chat attachment) are skipped: they are
image/audio bodies, and running the detector over megabytes of base64 would
cost more than it protects. Oversized strings are skipped for the same reason;
the file-write and diff gates cover those artifacts.

Made with ❤️ by codx-junior
"""

from typing import Any, Optional, Tuple

from codx.junior.security.ciso.models import CisoPolicy
from codx.junior.security.ciso.secrets import redact_text

#: Longest string inspected by :func:`redact_payload`.
#: Beyond this the cost of the detector outweighs its value; oversized
#: artifacts must be checked by the file/diff gates.
MAX_STRING_CHARS = 400_000

#: Keys holding binary bodies (base64 images/audio) that are never text.
_BINARY_KEYS = frozenset({"base64_data"})


def redact_payload(
    value: Any,
    policy: Optional[CisoPolicy] = None,
    max_string_chars: int = MAX_STRING_CHARS,
) -> Tuple[Any, int]:
    """Return a copy of *value* with every detected secret neutralised.

    Dicts and lists are rebuilt (the input is never mutated) and every string
    is passed through :func:`codx.junior.security.ciso.secrets.redact_text`.
    Non-text scalars (numbers, booleans, ``None``, datetimes) are returned
    unchanged so the result stays JSON-serialisable.

    Args:
        value: JSON-like structure (``model_dump()`` output, message list,
            tool event payload...).
        policy: Optional CISO policy (size and excerpt limits).
        max_string_chars: Strings longer than this are returned unchanged.

    Returns:
        tuple: ``(sanitised_value, replacements)``. When nothing matched the
        structure is a copy equal to the input and replacements is ``0``.

    Example:
        >>> payload, count = redact_payload({"content": 'TOKEN="ghp_' + "a" * 36 + '"'})
        >>> count
        1
        >>> "ghp_" in payload["content"]
        False
    """
    replacements = 0

    def _walk(node: Any, key: Optional[str] = None) -> Any:
        nonlocal replacements
        if isinstance(node, str):
            if not node or len(node) > max_string_chars or key in _BINARY_KEYS:
                return node
            redacted, found = redact_text(node, policy)
            replacements += found
            return redacted
        if isinstance(node, dict):
            return {name: _walk(child, name) for name, child in node.items()}
        if isinstance(node, (list, tuple)):
            return [_walk(item, key) for item in node]
        return node

    return _walk(value), replacements
