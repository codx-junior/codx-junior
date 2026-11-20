"""CISO screening for knowledge ingestion.

A knowledge chunk is not inert: it is retrieved and replayed into later prompts.
A stored instruction is therefore *executed by reading it*. The CISO gate for
knowledge checks the two risks that cross this boundary:

* a stored injection payload (``injection.*``),
* a credential that would be copied into every later prompt (``secret.*``).

Code snippets inside a document are data; they are quarantined by the file
write and commit gates, not by the knowledge importer. This module is the wiring
between the indexers and :func:`codx.junior.security.ciso.gate.guard_knowledge`,
so a document the gate blocks never reaches the vector store.

Made with ❤️ by codx-junior
"""

import logging
from typing import Any, Iterable, List

from codx.junior.security.ciso import gate as ciso_gate
from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso.models import SecurityDomain

logger = logging.getLogger(__name__)


def screen_documents(documents: Iterable[Any], settings: Any = None) -> List[Any]:
    """Filter and redact knowledge documents before they are indexed.

    Args:
        documents: LangChain ``Document``-like objects exposing
            ``page_content`` and ``metadata``.
        settings: Project settings used to resolve the CISO policy.

    Returns:
        List[Any]: The documents that may be indexed. A blocked document is
        dropped; a document with a credential is replaced by its redacted text.
    """
    docs = list(documents or [])
    if not docs:
        return []
    policy = ciso_policy.resolve_policy(settings)
    if not policy.is_domain_enabled(SecurityDomain.KNOWLEDGE_INGESTION):
        return docs

    kept: List[Any] = []
    for doc in docs:
        content = getattr(doc, "page_content", "") or ""
        metadata = getattr(doc, "metadata", None) or {}
        source = str(metadata.get("source") or "")
        try:
            verdict = ciso_gate.guard_knowledge(
                content, source=source, settings=settings, policy=policy
            )
        except Exception as ex:  # pragma: no cover - the gate itself is fail-safe
            logger.warning(
                "CISO: knowledge gate failed open for %s: %s", source or "<unknown>", ex
            )
            kept.append(doc)
            continue
        if verdict.blocked:
            logger.warning(
                "CISO: blocked knowledge document %s: %s",
                source or "<unknown>",
                verdict.summary,
            )
            continue
        redacted = verdict.redacted_text
        if redacted is not None and redacted != content:
            try:
                doc.page_content = redacted
            except Exception:
                logger.warning(
                    "CISO: cannot redact knowledge document %s; dropping it",
                    source or "<unknown>",
                )
                continue
        kept.append(doc)
    if len(kept) != len(docs):
        logger.warning(
            "CISO: knowledge screening kept %d of %d document(s)",
            len(kept),
            len(docs),
        )
    return kept
