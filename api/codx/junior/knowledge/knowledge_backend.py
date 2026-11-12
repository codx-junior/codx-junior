"""
Knowledge backend selection — one switch, two interchangeable implementations.

Knowledge is a **built-in** feature of codx-junior: the default backend is
LanceDB, an embedded (on-disk, in-process) vector database, so there is nothing
to install, start or configure beyond an embeddings provider. Milvus stays
available for installations that already have data in it or that outgrow an
embedded index.

Selection
---------
``CODX_JUNIOR_KNOWLEDGE_BACKEND``
    ``lancedb`` (default)  embedded, no service — the demo/trial tier
    ``milvus``             Milvus server (``CODX_JUNIOR_MILVUS_URL``)
    ``none``/``off``       knowledge disabled entirely

Both implementations expose the same public surface
(``index_documents``, ``delete_documents``, ``search*``, ``raw_search``,
``get_all_sources``, ``get_all_categoties``, ``get_db_*``, ``reset``) and share
the source-map file format, so switching the variable only changes *where* the
vectors live. The Milvus implementation is imported lazily: a LanceDB
installation never imports ``pymilvus`` (which can be installed on demand with
``pip install codx-junior[milvus]``).

Diagram:
flowchart TD
    A[create_knowledge_db settings] --> B{CODX_JUNIOR_KNOWLEDGE_BACKEND}
    B -- lancedb --> C[KnowledgeDBLanceDB]
    B -- milvus --> D[KnowledgeDB - pymilvus]
    B -- none --> E[NullKnowledgeDB]
    C -- lancedb missing --> D
    D -- pymilvus missing --> E

Made with ❤️ by codx-junior
"""

import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document

from codx.junior.settings import CODXJuniorSettings

logger = logging.getLogger(__name__)

# Environment variable holding the backend name
KNOWLEDGE_BACKEND_ENV = "CODX_JUNIOR_KNOWLEDGE_BACKEND"

# Backend used when the variable is not set
DEFAULT_KNOWLEDGE_BACKEND = "lancedb"

# Backend names and their aliases
BACKEND_ALIASES = {
    "lancedb": "lancedb",
    "lance": "lancedb",
    "embedded": "lancedb",
    "milvus": "milvus",
    "none": "none",
    "off": "none",
    "disabled": "none",
}

SUPPORTED_KNOWLEDGE_BACKENDS = ("lancedb", "milvus")


def get_knowledge_backend() -> str:
    """
    Return the configured knowledge backend.

    Returns:
        ``lancedb``, ``milvus`` or ``none``.
    """
    raw = (os.environ.get(KNOWLEDGE_BACKEND_ENV) or DEFAULT_KNOWLEDGE_BACKEND).strip().lower()
    backend = BACKEND_ALIASES.get(raw)
    if backend is None:
        logger.warning(
            "Unknown %s='%s', falling back to '%s'",
            KNOWLEDGE_BACKEND_ENV,
            raw,
            DEFAULT_KNOWLEDGE_BACKEND,
        )
        return DEFAULT_KNOWLEDGE_BACKEND
    return backend


def knowledge_backend_info() -> Dict[str, Any]:
    """
    Describe the knowledge configuration, for diagnostics and the API.

    Returns:
        Dict with the active backend, the default one, the accepted values and
        which optional packages are importable.
    """
    def is_importable(module: str) -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    return {
        "backend": get_knowledge_backend(),
        "default": DEFAULT_KNOWLEDGE_BACKEND,
        "supported": list(SUPPORTED_KNOWLEDGE_BACKENDS) + ["none"],
        "env": KNOWLEDGE_BACKEND_ENV,
        "lancedb_installed": is_importable("lancedb"),
        "pymilvus_installed": is_importable("pymilvus"),
    }


class NullKnowledgeDB:
    """
    Knowledge database used when knowledge is explicitly disabled.

    Implements the same public surface as the real backends and returns empty
    results, so callers never have to check whether knowledge is enabled.
    """

    backend = "none"

    def __init__(self, settings: CODXJuniorSettings, reason: str = ""):
        """
        Args:
            settings: Project settings (kept for parity with the real backends).
            reason: Why knowledge is disabled (logged once, for support).
        """
        self.settings = settings
        self.reason = reason
        self.ai = None
        self.path = settings.abs_project_path
        self.db_path = f"{settings.codx_path}/db/{settings.project_id or 'project'}"
        self.db_file_list = ""
        self.index_name = ""
        self.index_fulltext_name = ""
        self.last_update = None
        self.embeddings_model = None
        logger.info(
            "Knowledge disabled for '%s' (%s)", settings.project_name, reason or "backend=none"
        )

    def refresh_last_update(self) -> None:
        """No-op."""

    def reset(self) -> None:
        """No-op."""

    def index_documents(self, documents: List[Document]) -> None:
        """Ignore the documents."""

    def delete_documents(self, sources: List[str]) -> None:
        """Nothing to delete."""

    def search(self, query: str, _limit: int = 50) -> List[Document]:
        """Always empty."""
        return []

    def search_sparse(self, query: str, _limit: int = 50) -> List[Document]:
        """Always empty."""
        return []

    def search_vector(self, query: str, _limit: int = 50) -> List[Document]:
        """Always empty."""
        return []

    def search_hybrid(self, query: str, _limit: int = 50, **kwargs: Any) -> List[Document]:
        """Always empty."""
        return []

    def raw_search(
        self,
        search_filter: str,
        output_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Document]:
        """Always empty."""
        return []

    def get_all_sources(self) -> Dict[str, Document]:
        """Always empty."""
        return {}

    def get_all_categoties(self) -> List[str]:
        """Always empty."""
        return []

    def get_db_info(self) -> Dict[str, Any]:
        """Report the disabled state."""
        return {"embeddings": {"backend": "none", "reason": self.reason}}

    def get_collection_metrics(self) -> Dict[str, Any]:
        """Report the disabled state."""
        return {"backend": "none", "error": self.reason or "knowledge disabled"}


def create_knowledge_db(settings: CODXJuniorSettings):
    """
    Build the knowledge database configured for this installation.

    This is the only place that decides which implementation is used, so every
    caller (``Knowledge``, ``ProfileKnowledge``, the wiki index, the API
    routers) gets the right backend without knowing about it.

    Falls back gracefully: LanceDB missing → Milvus, Milvus missing → disabled.
    Knowledge never prevents the API from starting.

    Args:
        settings: Project settings.

    Returns:
        ``KnowledgeDBLanceDB``, ``KnowledgeDB`` (Milvus) or ``NullKnowledgeDB``.
    """
    backend = get_knowledge_backend()

    if backend == "none":
        return NullKnowledgeDB(settings=settings, reason=f"{KNOWLEDGE_BACKEND_ENV}=none")

    if backend == "lancedb":
        # Only the *module* import means "lancedb is not installed". An
        # ImportError raised while building the database (a broken torch stack
        # under a lazily loaded embeddings model) must NOT be reported as a
        # missing package nor downgrade the installation to Milvus.
        try:
            from codx.junior.knowledge.knowledge_lancedb import KnowledgeDBLanceDB
        except ImportError as ex:
            logger.warning(
                "LanceDB is not available (%s), falling back to the Milvus backend. "
                "Install it with: pip install lancedb",
                ex,
            )
        else:
            try:
                return KnowledgeDBLanceDB(settings=settings)
            except Exception as ex:
                logger.exception("Error creating the LanceDB knowledge base: %s", ex)
                return NullKnowledgeDB(settings=settings, reason=f"lancedb error: {ex}")

    try:
        from codx.junior.knowledge.knowledge_db import KnowledgeDB

        return KnowledgeDB(settings=settings)
    except ImportError as ex:
        logger.warning(
            "Milvus backend is not available (%s). Install it with: "
            "pip install 'codx-junior[milvus]' or set %s=lancedb",
            ex,
            KNOWLEDGE_BACKEND_ENV,
        )
    except Exception as ex:
        logger.exception("Error creating the Milvus knowledge base: %s", ex)

    return NullKnowledgeDB(
        settings=settings,
        reason=f"{KNOWLEDGE_BACKEND_ENV}={backend} but the backend is not installed",
    )

# Made with ❤️ by codx-junior
