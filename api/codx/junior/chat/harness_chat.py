"""
Harness-chat classification shared by the chat engine.

A harness session is a normal task chat, but it is a *durable, multi-turn*
transcript rendered in the harness mode UI. Task-mode cleanup (archive prior
messages, feed only ``is_answer`` messages and hide suggested replies) is
wrong for it, while the autonomous task contract still applies. Keeping the
predicate here (instead of inside :mod:`codx.junior.chat.chat_engine`) makes it
pure, cheap to import and directly testable without loading the whole engine.
"""

from __future__ import annotations

from typing import Any

from codx.junior.harness.prompts import (
    HARNESS_META_KEY,
    HARNESS_TAG,
    is_harness_meta,
)


def is_harness_chat(chat: Any) -> bool:
    """
    Tell whether *chat* is a harness-mode session.

    Args:
        chat: The chat about to run a turn (may be ``None``).

    Returns:
        True when the chat carries the harness tag or the enabled descriptor in
        ``meta_data``.
    """
    tags = getattr(chat, "tags", None) or []
    if HARNESS_TAG in tags:
        return True
    return is_harness_meta(getattr(chat, "meta_data", None))
