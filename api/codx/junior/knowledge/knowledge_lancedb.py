"""
LanceDB knowledge database — the built-in (embedded) knowledge backend.

Motivation
----------
The knowledge base no longer needs a service: LanceDB is an *embedded* vector
database (an on-disk Lance dataset, queried in-process), so knowledge/RAG works
out of the box on the single-container demo installation. There is no Milvus
container to start, no gRPC connection to wait for and nothing to configure.

What this module provides
-------------------------
:class:`KnowledgeDBLanceDB` is a drop-in replacement for
:class:`~codx.junior.knowledge.knowledge_db.KnowledgeDB` (the Milvus backend):
same public surface, same ``<codx_path>/db`` layout, same source-map file, so
``Knowledge``, ``ProfileKnowledge``, the wiki index and the API routers do not
know which backend they are talking to. The backend is selected with
``CODX_JUNIOR_KNOWLEDGE_BACKEND`` (see :mod:`knowledge_backend`); ``lancedb`` is
the default, ``milvus`` remains available for existing data / big corpora.

Retrieval
---------
Two legs, exactly like the Milvus implementation:

* **lexical** — full-text search on ``page_content`` (BM25 in Milvus, the
  LanceDB full-text index here),
* **dense** — cosine similarity over the embedding of the document.

``search_hybrid`` fuses both rankings with Reciprocal Rank Fusion (RRF) in
Python. LanceDB also offers hybrid search natively, but its rerankers require an
external model download (the ``sentence-transformers`` cross encoder or the
Cohere/OpenAI rerank APIs) — that would put a heavy, network dependent step on
the critical path of a trial installation, so the fusion is done here with 20
lines and no dependency.

Embeddings
----------
The dense leg needs an embeddings provider. Three sources, in this order:

``CODX_JUNIOR_EMBEDDINGS_BACKEND``
    ``auto`` (default)  local if ``sentence-transformers`` is installed,
                        otherwise the configured AI provider.
    ``local``           ``sentence-transformers`` (``all-MiniLM-L6-v2``, 384d).
    ``provider``        the project's configured *embeddings* model through any
                        OpenAI-compatible endpoint (``text-embedding-3-small``,
                        ``text-embedding-004``...). This is what makes knowledge
                        work in the slim image, which does not ship torch.
    ``none``            lexical search only.

When no embeddings are available the adapter degrades to full-text search
instead of raising, so a project is always searchable.

Storage layout
--------------
::

    <codx_path>/db/<index_name>/
        lancedb/                      Lance dataset (embedded, no server)
        index_meta.json               backend + embedding dimension + model
        <index_name>_file.json        source map (shared with the Milvus backend)

``index_meta.json`` records the embedding dimension. Embedding dimensions are
not interchangeable, so when the configured provider changes the dimension the
table is rebuilt (and the source map reset) instead of mixing vectors of
different sizes — the knowledge base is then reindexed on next load.

Diagram:
flowchart TD
    A[KnowledgeDBLanceDB] --> B[lancedb.connect db_path/lancedb]
    A --> C[embeddings provider selection]
    C -- local --> D[sentence-transformers]
    C -- provider --> E[OpenAI-compatible embeddings API]
    C -- none --> F[dense leg disabled]
    A --> G[search_hybrid: FTS + vector, RRF fusion]
    A --> H[source map JSON shared with Milvus]

Made with ❤️ by codx-junior
"""

import importlib.util
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from slugify import slugify

from langchain_core.documents import Document

from codx.junior.settings import CODXJuniorSettings
from codx.junior.knowledge.embeddings import (
    DEFAULT_EMBEDDINGS_MODEL,
    get_local_embeddings_model,
    is_embeddings_unavailable,
)
from codx.junior.knowledge.knowledge_source_map import (
    SOURCE_MAP_VERSION,
    build_empty_source_map,
    parse_keywords,
    read_source_map,
    rebuild_categories,
    write_source_map,
)
from codx.junior.profiling.profiler import profile_function

logger = logging.getLogger(__name__)

# ── Backend identity ─────────────────────────────────────────────────────────
KNOWLEDGE_BACKEND = "lancedb"

# ── Column names (mirrors the Milvus field names so callers are unchanged) ───
FIELD_ID = "id"
FIELD_SOURCE = "source"
FIELD_LAST_UPDATE = "last_update"
FIELD_CATEGORY = "category"
FIELD_KEYWORDS = "keywords"
FIELD_PAGE_CONTENT = "page_content"
FIELD_METADATA = "metadata"
FIELD_VECTOR = "vector"

# Metadata key used to surface the relevance score to callers
FIELD_SCORE = "score"

# Default embedding dimension of the local model (all-MiniLM-L6-v2)
DEFAULT_DENSE_EMBEDDING_DIM = int(os.environ.get("CODX_JUNIOR_DENSE_DIM", "384"))

# Embeddings source: auto | local | provider | none
EMBEDDINGS_BACKEND_ENV = "CODX_JUNIOR_EMBEDDINGS_BACKEND"
DEFAULT_EMBEDDINGS_BACKEND = "auto"

# Texts embedded per request (provider APIs limit the batch size)
EMBEDDINGS_BATCH_SIZE = int(os.environ.get("CODX_JUNIOR_EMBEDDINGS_BATCH_SIZE", "64"))

# Reciprocal Rank Fusion constant (the value used by the reference paper/Elasticsearch)
RRF_K = 60

# Column scanned by the full-text index
FTS_COLUMN = FIELD_PAGE_CONTENT

# Safety valve for the pure-python scan used by ``raw_search`` / the FTS fallback
MAX_SCAN_ROWS = int(os.environ.get("CODX_JUNIOR_LANCEDB_MAX_SCAN_ROWS", "200000"))

# File holding the backend metadata (embedding dimension and model)
INDEX_META_FILE_NAME = "index_meta.json"
INDEX_META_VERSION = 1

# Characters allowed in a filter expression evaluated in Python
_FILTER_SAFE_CHARS = re.compile(r"^[\w\s\.\"'\[\],=!<>()\-/:+*]+$")


# ── Embeddings providers ─────────────────────────────────────────────────────


class ProviderEmbeddingsModel:
    """
    Embeddings served by the project's configured AI provider.

    Uses the OpenAI-compatible ``/embeddings`` endpoint, which is implemented by
    OpenAI, DeepSeek, Gemini (compatibility layer), Mistral, Groq, OpenRouter
    and Ollama, so no extra SDK is required: the ``openai`` client is already a
    dependency.

    This is what allows knowledge to work in an installation without
    ``sentence-transformers``/torch (the slim demo image): the embeddings come
    from the same provider the user configured for chat.
    """

    def __init__(self, ai_settings: Any):
        """
        Build the client lazily from resolved AI settings.

        Args:
            ai_settings: Resolved :class:`AISettings` of the embeddings model
                (``api_url``, ``api_key``, ``model``).
        """
        self.api_url = (getattr(ai_settings, "api_url", "") or "").strip()
        self.api_key = (getattr(ai_settings, "api_key", "") or "").strip()
        self.model_name = (getattr(ai_settings, "model", "") or "").strip()
        self._client = None
        self._embedding_dim: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        """Whether enough settings are present to call the endpoint."""
        return bool(self.api_url and self.model_name)

    @property
    def client(self):
        """Lazily create the OpenAI client."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.api_url,
                api_key=self.api_key or "none",
                timeout=60.0,
            )
        return self._client

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a batch of texts.

        Args:
            texts: Texts to embed.

        Returns:
            One float vector per input text.
        """
        response = self.client.embeddings.create(
            model=self.model_name,
            input=list(texts),
        )
        # The API returns the vectors with an index: keep the input order.
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if vectors and vectors[0]:
            self._embedding_dim = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> List[float]:
        """
        Embed a single query string.

        Args:
            text: Query text.

        Returns:
            Float vector for the query.
        """
        return self.embed_documents([text])[0]

    @property
    def embedding_dim(self) -> int:
        """Embedding dimension, resolved with a probe call when unknown."""
        if self._embedding_dim is None:
            self._embedding_dim = len(self.embed_query("dimension probe"))
        return self._embedding_dim


def resolve_embeddings_backend() -> str:
    """
    Return the configured embeddings source.

    Returns:
        One of ``auto``, ``local``, ``provider`` or ``none``.
    """
    backend = (os.environ.get(EMBEDDINGS_BACKEND_ENV) or DEFAULT_EMBEDDINGS_BACKEND).strip().lower()
    if backend in ("auto", "local", "provider", "none", "disabled", "off"):
        return "none" if backend in ("disabled", "off") else backend
    logger.warning(
        "Unknown %s='%s', falling back to '%s'",
        EMBEDDINGS_BACKEND_ENV,
        backend,
        DEFAULT_EMBEDDINGS_BACKEND,
    )
    return DEFAULT_EMBEDDINGS_BACKEND


class KnowledgeDBLanceDB:
    """
    Embedded, LanceDB backed knowledge base for a project.

    Combines a full-text (lexical) index with dense embedding vectors, stored in
    a Lance dataset inside the project ``.codx`` folder. No service, no
    connection, no container: the database is a folder.

    Three retrieval strategies are exposed, mirroring the Milvus backend:

      - ``search_sparse``  → full-text search (lexical).
      - ``search_vector``  → dense embedding semantic search.
      - ``search_hybrid``  → fused sparse + dense search using RRF.

    The source map (``<db_path>/<index_name>_file.json``) is the single source of
    truth for ``get_all_sources`` / ``get_all_categoties``, exactly like the
    Milvus backend, so both backends can coexist and be swapped freely.

    Diagram:
    classDiagram
        class KnowledgeDBLanceDB {
            +str db_path
            +str index_name
            +str index_fulltext_name
            +str db_file_list
            +embeddings_model
            +connect_db()
            +create_db()
            +reset()
            +index_documents(documents)
            +delete_documents(sources)
            +search(query, limit) List~Document~
            +search_sparse(query, limit) List~Document~
            +search_vector(query, limit) List~Document~
            +search_hybrid(query, limit) List~Document~
            +raw_search(filter, output_fields, limit)
            +get_all_sources() Dict
            +get_all_categoties() List~str~
            +get_db_info()
            +get_collection_metrics()
            -_embed_documents(texts)
            -_load_source_map()
            -_save_source_map(source_map)
            -_init_source_map_from_db()
        }

    Note: documents returned by the search methods carry a ``score`` key in their
    metadata. Scores of a single leg are the backend score (higher is better);
    ``search_hybrid`` returns the RRF score, which is comparable across legs.
    """

    def __init__(self, settings: CODXJuniorSettings):
        """
        Open (or create) the LanceDB knowledge base of a project.

        Never raises when LanceDB is unavailable: the instance stays usable and
        every operation degrades to a no-op / empty result, so knowledge can be
        disabled at runtime without breaking the API.

        Args:
            settings: Project-level settings (paths and project identity).
        """
        self.settings = settings
        self.ai = None

        self.path = self.settings.abs_project_path
        self.index_name = re.sub(r"[^a-zA-Z0-9._]", "", slugify(str(self.path))).strip()
        # Kept identical to the Milvus backend: callers/logs refer to it
        self.index_fulltext_name = f"{self.index_name}_full_text"

        self.db_path = f"{settings.codx_path}/db/{self.index_name}"
        os.makedirs(self.db_path, exist_ok=True)

        # Lance dataset folder (embedded database — this *is* the database)
        self.db_file = f"{self.db_path}/lancedb"
        # Source map: same file/format as the Milvus backend
        self.db_file_list = f"{self.db_path}/{self.index_name}_file.json"
        # Backend metadata (embedding dimension / model)
        self.index_meta_file = f"{self.db_path}/{INDEX_META_FILE_NAME}"

        self.last_update: Optional[float] = None
        self.db: Any = None
        self._table: Any = None
        self._lock = threading.RLock()
        # In-memory cache of the source-map JSON, invalidated by file mtime
        self._source_map_cache: Optional[Dict[str, Any]] = None
        self._source_map_mtime: Optional[int] = None

        # Dense (semantic) leg availability: cleared when the embeddings
        # provider turns out to be unusable, searches then use full-text only.
        self._dense_embeddings_available = True
        self._fts_available = True
        self._fts_checked = False
        self._available = True
        self.embeddings_model: Any = None
        self.embeddings_backend = "none"
        self._embedding_dim: Optional[int] = None

        self._init_embeddings_model()
        self.connect_db()
        self.refresh_last_update()

    def __del__(self):
        pass

    # ── Embeddings ────────────────────────────────────────────────────────────

    def _init_embeddings_model(self) -> None:
        """
        Select and configure the embeddings provider (never fatal).

        ``auto`` prefers the local model (backwards compatible: an existing
        installation keeps its 384d index) and falls back to the AI provider,
        which is what a slim installation without ``sentence-transformers``
        uses.
        """
        backend = resolve_embeddings_backend()
        if backend == "none":
            self._dense_embeddings_available = False
            self.embeddings_backend = "none"
            logger.info(
                "Knowledge for '%s': dense embeddings disabled, lexical search only",
                self.settings.project_name,
            )
            return

        if backend in ("auto", "local"):
            try:
                self.embeddings_model = get_local_embeddings_model(DEFAULT_EMBEDDINGS_MODEL)
                self.embeddings_backend = "local"
                return
            except Exception as ex:
                # Not only ImportError: a broken transitive dependency (a
                # mismatched torch/torchvision pair, an unreadable model cache)
                # must also degrade to lexical search instead of disabling the
                # whole knowledge base.
                if backend == "local":
                    self._dense_embeddings_available = False
                    self.embeddings_backend = "none"
                    logger.warning(
                        "Local embeddings unavailable (%s). Install with: "
                        "pip install sentence-transformers — or set %s=provider",
                        ex,
                        EMBEDDINGS_BACKEND_ENV,
                    )
                    return
                logger.info(
                    "Local embeddings not installed for '%s', using the AI provider",
                    self.settings.project_name,
                )
        provider = self._build_provider_embeddings()
        if provider is not None:
            self.embeddings_model = provider
            self.embeddings_backend = "provider"
            return

        self._dense_embeddings_available = False
        self.embeddings_backend = "none"
        logger.info(
            "Knowledge for '%s' has no embeddings provider configured, "
            "using lexical (full-text) search only",
            self.settings.project_name,
        )

    def _build_provider_embeddings(self) -> Optional[ProviderEmbeddingsModel]:
        """
        Resolve the project's embeddings model into a provider client.

        Returns:
            A configured :class:`ProviderEmbeddingsModel`, or None when the
            project has no usable embeddings model.
        """
        try:
            ai_settings = self.settings.get_embeddings_settings()
        except Exception as ex:
            logger.info("No embeddings model configured: %s", ex)
            return None

        provider = ProviderEmbeddingsModel(ai_settings)
        if not provider.is_configured:
            logger.info(
                "Embeddings model is not configured for provider '%s' (model='%s')",
                getattr(ai_settings, "provider", ""),
                getattr(ai_settings, "model", ""),
            )
            return None
        return provider

    @property
    def embedding_dim(self) -> int:
        """
        Embedding dimension of the active provider.

        Loading the local model is lazy, so a missing or broken torch stack
        fails *here* rather than at construction. When that happens the dense
        leg is switched off and the lexical (full-text) leg keeps working
        instead of taking the whole knowledge base down.

        Returns:
            The embedding dimension, or the configured default when unknown.
        """
        if self._embedding_dim is None:
            try:
                if self.embeddings_model is None:
                    self._embedding_dim = DEFAULT_DENSE_EMBEDDING_DIM
                elif hasattr(self.embeddings_model, "embedding_dim"):
                    self._embedding_dim = int(self.embeddings_model.embedding_dim)
                else:
                    self._embedding_dim = DEFAULT_DENSE_EMBEDDING_DIM
            except Exception as ex:
                self._dense_embeddings_available = False
                self.embeddings_model = None
                self.embeddings_backend = "none"
                logger.warning(
                    "Local embeddings unavailable (%s) for '%s'; using "
                    "lexical (full-text) search only. Install with: "
                    "pip install sentence-transformers",
                    ex,
                    self.settings.project_name,
                )
                self._embedding_dim = DEFAULT_DENSE_EMBEDDING_DIM
        return self._embedding_dim

    def _embed_query(self, query: str) -> List[float]:
        """
        Produce a dense embedding vector for a query string.

        Args:
            query: Natural-language query to embed.

        Returns:
            Dense float vector for the query.
        """
        if hasattr(self.embeddings_model, "embed_query"):
            return self.embeddings_model.embed_query(query)
        return self.embeddings_model.embed_documents([query])[0]

    def _embed_documents(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Produce dense embeddings for a batch of texts.

        Provider APIs limit the batch size, so the input is chunked. A failing
        chunk yields ``None`` placeholders instead of failing the whole index
        operation: the affected documents are still stored and stay searchable
        through the lexical leg.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors (or None for the texts that failed).
        """
        vectors: List[Optional[List[float]]] = []
        for start in range(0, len(texts), EMBEDDINGS_BATCH_SIZE):
            chunk = texts[start:start + EMBEDDINGS_BATCH_SIZE]
            try:
                vectors.extend(self._embed_batch(chunk))
            except Exception as ex:
                logger.error(
                    "Error embedding %d document(s) for '%s': %s",
                    len(chunk),
                    self.settings.project_name,
                    ex,
                )
                vectors.extend([None] * len(chunk))
        return vectors

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Embed one batch, normalising the provider interface.

        Args:
            texts: Batch of texts (already within the provider limit).

        Returns:
            List of float vectors.
        """
        if hasattr(self.embeddings_model, "embed_documents"):
            return self.embeddings_model.embed_documents(texts)
        return [self.embeddings_model.embed_query(text) for text in texts]

    def _embed_query_or_none(self, query: str) -> Optional[List[float]]:
        """
        Embed *query*, returning None when the dense leg cannot be used.

        The embeddings provider is optional at runtime. When it is unusable the
        failure is permanent for this instance, so the flag is cleared and the
        caller degrades to the full-text search, which needs no embeddings.
        Any other error (a genuine bug) is re-raised.

        Args:
            query: Natural-language query to embed.

        Returns:
            Dense float vector, or None when dense search is unavailable.
        """
        if not self._dense_embeddings_available or self.embeddings_model is None:
            return None
        try:
            return self._embed_query(query)
        except Exception as ex:
            if not is_embeddings_unavailable(ex):
                logger.error(
                    "Embeddings provider failed for '%s': %s",
                    self.settings.project_name,
                    ex,
                )
            self._dense_embeddings_available = False
            logger.warning(
                "Dense embeddings are unavailable for project '%s'. "
                "Searches will use full-text only. Configure %s or install "
                "sentence-transformers.",
                self.settings.project_name,
                EMBEDDINGS_BACKEND_ENV,
            )
            return None

    # ── Connection / schema ───────────────────────────────────────────────────

    def get_ai(self):
        """
        Lazily initialise and return the AI helper.

        Returns:
            AI instance bound to this project.
        """
        if not self.ai:
            from codx.junior.ai import AI
            from codx.junior.model.model import CodxUser

            self.ai = AI(settings=self.settings, user=CodxUser(username=__name__))
        return self.ai

    def refresh_last_update(self) -> None:
        """Refresh the cached last-update timestamp from the source-map file."""
        if os.path.isfile(self.db_file_list):
            self.last_update = os.path.getmtime(self.db_file_list)

    def connect_db(self) -> None:
        """Open the embedded LanceDB database and ensure the table exists."""
        # Probe the package explicitly: an ImportError raised *inside*
        # lancedb.connect() or create_db() comes from a broken transitive
        # dependency, and reporting it as "not installed" would hide the real
        # cause and silently disable knowledge.
        if importlib.util.find_spec("lancedb") is None:
            self._available = False
            logger.warning(
                "lancedb is not installed: knowledge is disabled for '%s'. "
                "Install it with: pip install lancedb",
                self.settings.project_name,
            )
            return
        try:
            import lancedb

            os.makedirs(self.db_file, exist_ok=True)
            self.db = lancedb.connect(self.db_file)
            self._available = True
            self.create_db()
        except Exception as ex:
            self._available = False
            logger.error(
                "Error opening the LanceDB knowledge base for '%s' (%s): %s",
                self.settings.project_name,
                self.db_file,
                ex,
            )

    def _read_index_meta(self) -> Dict[str, Any]:
        """
        Read the backend metadata file.

        Returns:
            Metadata dict (empty when the file is missing or unreadable).
        """
        if not os.path.isfile(self.index_meta_file):
            return {}
        try:
            with open(self.index_meta_file, "r", encoding="utf-8") as fh:
                return json.load(fh) or {}
        except Exception as ex:
            logger.warning("Could not read '%s': %s", self.index_meta_file, ex)
            return {}

    def _write_index_meta(self, meta: Dict[str, Any]) -> None:
        """
        Persist the backend metadata (embedding dimension and model).

        Args:
            meta: Metadata dict to write.
        """
        meta = {
            **meta,
            "version": INDEX_META_VERSION,
            "backend": KNOWLEDGE_BACKEND,
            "embeddings_backend": self.embeddings_backend,
            "embeddings_model": getattr(self.embeddings_model, "model_name", None)
            or self.embeddings_backend,
            "updated_at": int(time.time()),
        }
        try:
            tmp_path = self.index_meta_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2)
            os.replace(tmp_path, self.index_meta_file)
        except Exception as ex:
            logger.error("Could not write '%s': %s", self.index_meta_file, ex)

    def _build_schema(self, dim: Optional[int]):
        """
        Build the pyarrow schema of the knowledge table.

        Args:
            dim: Embedding dimension, or None for a lexical-only table.

        Returns:
            ``pyarrow.Schema`` describing one document row.
        """
        import pyarrow as pa

        fields = [
            pa.field(FIELD_METADATA, pa.string()),
            pa.field(FIELD_PAGE_CONTENT, pa.string()),
            pa.field(FIELD_SOURCE, pa.string()),
            pa.field(FIELD_KEYWORDS, pa.string()),
            pa.field(FIELD_CATEGORY, pa.string()),
            pa.field(FIELD_LAST_UPDATE, pa.int64()),
        ]
        if dim:
            fields.append(pa.field(FIELD_VECTOR, pa.list_(pa.float32(), int(dim))))
        return pa.schema(fields)

    def _table_names(self) -> List[str]:
        """
        List the tables of the database.

        Supports both the old (``table_names``) and the new (``list_tables``)
        LanceDB API so the adapter is not pinned to a single release.

        Returns:
            List of table names (empty when the database cannot be read).
        """
        try:
            if hasattr(self.db, "list_tables"):
                return list(self.db.list_tables())
            return list(self.db.table_names())
        except Exception as ex:
            logger.debug("Could not list LanceDB tables: %s", ex)
            return []

    def create_db(self) -> None:
        """
        Create the knowledge table (and its full-text index) when missing.

        An existing table is never rebuilt here: the embedding dimension is
        verified against the vectors actually returned by the provider in
        :meth:`index_documents` (see :meth:`_ensure_embedding_dim`), so startup
        only costs one metadata read.
        """
        if self.db is None:
            return

        existing = self._table_names()
        meta = self._read_index_meta()
        project = self.settings.project_name

        if self.index_fulltext_name in existing:
            # An existing table is never rebuilt here: the embedding dimension is
            # verified against the vectors actually returned by the provider in
            # ``index_documents``, so startup costs a single metadata read.
            if not meta:
                self._write_index_meta({"dim": self._table_vector_dim()})
            # The full-text index is not rebuilt when opening an existing table:
            # it is created on first use (see ``_fts_search``) so opening a
            # project stays cheap.
            return

        # Resolve the dimension BEFORE deciding: reading it is what loads the
        # embeddings model, and a failure there turns the dense leg off.
        resolved_dim = self.embedding_dim
        dim = resolved_dim if self._dense_embeddings_available else None
        logger.info(
            "Creating LanceDB knowledge table '%s' for '%s' (%sd embeddings)",
            self.index_fulltext_name,
            project,
            dim or "no",
        )
        self.db.create_table(self.index_fulltext_name, schema=self._build_schema(dim))
        self._table = None
        self._write_index_meta({"dim": dim})

        self._ensure_fts_index()

    def _table_vector_dim(self) -> Optional[int]:
        """
        Return the embedding dimension of the table's vector column.

        Returns:
            The fixed list size, or None when the table has no vector column
            (lexical-only table) or cannot be inspected.
        """
        table = self.get_table()
        if table is None:
            return None
        try:
            schema = table.schema
            if FIELD_VECTOR not in schema.names:
                return None
            return getattr(schema.field(FIELD_VECTOR).type, "list_size", None)
        except Exception as ex:
            logger.debug("Could not read the vector column: %s", ex)
            return None

    def _ensure_embedding_dim(self, dim: int) -> None:
        """
        Make the table compatible with the embeddings dimension *dim*.

        Vectors of different sizes cannot be mixed, so a provider change (for
        example from the 384d local model to a 1536d API model) rebuilds the
        table and resets the source map: the project is reindexed on next load.

        Args:
            dim: Dimension of the vectors returned by the active provider.
        """
        if self.db is None:
            return
        current = self._table_vector_dim()
        if current == int(dim):
            return

        logger.warning(
            "Knowledge index '%s' was built with %sd embeddings and the active "
            "provider returns %sd: rebuilding the index, the project will be "
            "reindexed.",
            self.settings.project_name,
            current or "no",
            dim,
        )
        with self._lock:
            self._drop_table()
            self._remove_source_map_file()
            self._embedding_dim = int(dim)
            self.db.create_table(
                self.index_fulltext_name, schema=self._build_schema(int(dim))
            )
            self._table = None
            self._write_index_meta({"dim": int(dim)})
            self._ensure_fts_index()

    def _ensure_fts_index(self, force: bool = False) -> None:
        """
        Make sure the full-text index exists on ``page_content``.

        Checked at most once per instance: rebuilding the index on every
        instantiation would be expensive on a large project. Both the modern
        (``create_index`` with an ``FTS`` config) and the legacy
        (``create_fts_index``) LanceDB APIs are supported.

        Failure is not fatal: :meth:`_fts_search` then falls back to a scored
        scan, so search keeps working on LanceDB builds without full-text
        support.

        Args:
            force: Rebuild even when it was already checked by this instance.
        """
        if self._fts_checked and not force:
            return
        self._fts_checked = True

        table = self.get_table()
        if table is None:
            return

        # Modern API (lancedb >= 0.25): create_index(column, config=FTS())
        try:
            from lancedb.index import FTS

            table.create_index(FTS_COLUMN, config=FTS(), replace=True)
            self._fts_available = True
            return
        except TypeError:
            # Signature changed across 0.25.x builds.
            pass
        except Exception as ex:
            logger.debug("Modern FTS index unavailable: %s", ex)
        # Legacy API (lancedb < 0.25)
        try:
            try:
                table.create_fts_index(FTS_COLUMN, replace=True)
            except TypeError:
                # Older signature without `replace`
                table.create_fts_index(FTS_COLUMN)
            self._fts_available = True
        except Exception as ex:
            self._fts_available = False
            logger.warning(
                "Could not create the full-text index, searches will scan the table: %s",
                ex,
            )

    def get_table(self):
        """
        Return the LanceDB table handle (opening it once).

        Returns:
            The table, or None when it does not exist / is unavailable.
        """
        if self._table is not None:
            return self._table
        if self.db is None:
            return None
        try:
            self._table = self.db.open_table(self.index_fulltext_name)
        except Exception as ex:
            logger.debug(
                "Knowledge table '%s' not available: %s", self.index_fulltext_name, ex
            )
            self._table = None
        return self._table

    def _drop_table(self) -> None:
        """Drop the knowledge table, ignoring a missing table."""
        self._table = None
        # The rebuilt table needs a new full-text index
        self._fts_checked = False
        try:
            if self.index_fulltext_name in self._table_names():
                self.db.drop_table(self.index_fulltext_name)
        except Exception as ex:
            logger.error(
                "Error dropping knowledge table '%s': %s",
                self.index_fulltext_name,
                ex,
            )

    def _remove_source_map_file(self) -> None:
        """Delete the source map so it is rebuilt from the table."""
        self._source_map_cache = None
        self._source_map_mtime = None
        self.last_update = None
        try:
            if os.path.isfile(self.db_file_list):
                os.remove(self.db_file_list)
        except OSError as ex:
            logger.warning("Could not remove '%s': %s", self.db_file_list, ex)

    def reset(self) -> None:
        """Drop the table and the source map, then recreate everything."""
        logger.info("Deleting index for project '%s'", self.settings.project_name)
        self._drop_table()
        self._remove_source_map_file()
        self.create_db()

    # ── Source map ────────────────────────────────────────────────────────────

    def _load_source_map(self) -> Dict[str, Any]:
        """
        Return the source map, bootstrapping it from the table when absent.

        The parsed map is cached in memory and only re-read when the file's
        ``st_mtime_ns`` changes, avoiding repeated JSON parsing.

        Returns:
            Valid source-map dict (never None).
        """
        current_mtime = (
            os.stat(self.db_file_list).st_mtime_ns
            if os.path.isfile(self.db_file_list)
            else None
        )
        if (
            current_mtime is not None
            and current_mtime == self._source_map_mtime
            and self._source_map_cache is not None
        ):
            return self._source_map_cache

        source_map = read_source_map(self.db_file_list)
        if source_map is None or source_map.get("version") != SOURCE_MAP_VERSION:
            logger.info(
                "Source-map missing or outdated for '%s', initialising from DB.",
                self.index_fulltext_name,
            )
            source_map = self._init_source_map_from_db()
            self._save_source_map(source_map)
        else:
            self._source_map_cache = source_map
            self._source_map_mtime = current_mtime
        return source_map

    def _save_source_map(self, source_map: Dict[str, Any]) -> None:
        """
        Persist the source map and refresh the in-memory cache.

        Args:
            source_map: Source-map dict to write.
        """
        write_source_map(self.db_file_list, source_map)
        self.refresh_last_update()
        self._source_map_cache = source_map
        self._source_map_mtime = (
            os.stat(self.db_file_list).st_mtime_ns
            if os.path.isfile(self.db_file_list)
            else None
        )

    def _init_source_map_from_db(self) -> Dict[str, Any]:
        """
        Bootstrap a source map by scanning every row of the table.

        This is the one-time expensive scan — subsequent calls use the file.

        Returns:
            Fully-populated source-map dict.
        """
        source_map = build_empty_source_map(self.index_fulltext_name)
        try:
            for row in self._scan_rows():
                source = row.get(FIELD_SOURCE) or ""
                if not source:
                    continue
                last_update = int(row.get(FIELD_LAST_UPDATE) or 0)
                existing = source_map["sources"].get(source)
                if existing is None or existing["last_update"] < last_update:
                    source_map["sources"][source] = {
                        "last_update": last_update,
                        "category": row.get(FIELD_CATEGORY) or "",
                        "keywords": parse_keywords(row.get(FIELD_KEYWORDS)),
                    }
            rebuild_categories(source_map)
            logger.info(
                "Source-map bootstrapped: %d sources, %d categories",
                len(source_map["sources"]),
                len(source_map["categories"]),
            )
        except Exception as ex:
            logger.error("Error bootstrapping source-map from DB: %s", ex)
        return source_map

    def _upsert_source_map_entries(self, documents: List[Document]) -> None:
        """
        Add or update the source-map entries of the given documents.

        Args:
            documents: Documents that were just indexed.
        """
        source_map = self._load_source_map()
        now = int(time.time())
        for doc in documents:
            source = doc.metadata.get(FIELD_SOURCE, "")
            if not source:
                continue
            source_map["sources"][source] = {
                "last_update": now,
                "category": doc.metadata.get(FIELD_CATEGORY, "") or "",
                "keywords": parse_keywords(doc.metadata.get(FIELD_KEYWORDS, [])),
            }
        rebuild_categories(source_map)
        self._save_source_map(source_map)

    def _remove_source_map_entries(self, sources: Sequence[str]) -> None:
        """
        Remove the source-map entries of the given sources, then persist.

        Args:
            sources: Source paths removed from the table.
        """
        source_map = self._load_source_map()
        removed = 0
        for source in sources:
            if source in source_map["sources"]:
                del source_map["sources"][source]
                removed += 1
        if removed:
            rebuild_categories(source_map)
            self._save_source_map(source_map)
            logger.debug(
                "Source-map removed %d entries, total sources: %d",
                removed,
                len(source_map["sources"]),
            )

    # ── Write path ────────────────────────────────────────────────────────────

    @staticmethod
    def _escape_sql_literal(value: str) -> str:
        """
        Escape a value for a LanceDB (DataFusion SQL) string literal.

        Args:
            value: Raw string value.

        Returns:
            Value safe to embed between single quotes.
        """
        return str(value).replace("'", "''")

    def _delete_sources(self, sources: Sequence[str]) -> None:
        """
        Delete every row of the given sources.

        Each source is deleted on its own so a single problematic path (a quote,
        a very long list) cannot invalidate the whole statement.

        Args:
            sources: Source paths to delete.
        """
        table = self.get_table()
        if table is None:
            return
        for source in sources:
            try:
                table.delete(f"{FIELD_SOURCE} = '{self._escape_sql_literal(source)}'")
            except Exception as ex:
                logger.error("Error deleting source '%s': %s", source, ex)

    @profile_function
    def index_documents(self, documents: List[Document]) -> None:
        """
        Insert or replace a list of documents in the knowledge table.

        Documents are upserts: the previous rows of every affected source are
        removed first, so reindexing a file never leaves duplicated chunks even
        when the caller did not delete explicitly.

        Documents are stored with their dense embedding when the provider is
        available; when it is not, they are still stored (and searchable through
        the lexical leg).

        Args:
            documents: LangChain Document objects to index.
        """
        table = self.get_table()
        if table is None:
            logger.warning(
                "Knowledge table is not available for '%s', skipping %d document(s)",
                self.settings.project_name,
                len(documents),
            )
            return

        rows: List[Dict[str, Any]] = []
        page_contents: List[str] = []
        for doc in documents:
            try:
                content_parts = [
                    part
                    for part in (
                        doc.metadata.get(FIELD_SOURCE),
                        doc.page_content,
                        doc.metadata.get("summary"),
                        doc.metadata.get("tags"),
                    )
                    if part
                ]
                page_content = "\n".join(str(part) for part in content_parts)
                source = doc.metadata[FIELD_SOURCE]

                rows.append({
                    FIELD_METADATA: json.dumps(doc.metadata, default=str),
                    FIELD_PAGE_CONTENT: page_content,
                    FIELD_SOURCE: source,
                    FIELD_KEYWORDS: ",".join(parse_keywords(doc.metadata.get(FIELD_KEYWORDS, []))),
                    FIELD_CATEGORY: doc.metadata.get(FIELD_CATEGORY, "") or "",
                    FIELD_LAST_UPDATE: int(time.time()),
                })
                page_contents.append(page_content)
            except Exception as ex:
                logger.error(
                    "Error processing document, len %d, %s: %s",
                    len(getattr(doc, "page_content", "") or ""),
                    getattr(doc, "metadata", {}),
                    ex,
                )

        if not rows:
            logger.warning("No documents to index after processing.")
            return

        # Dense leg (optional): the vectors are computed first so the dimension
        # of the active provider can be verified before anything is written.
        if self._dense_embeddings_available and self.embeddings_model is not None:
            vectors = self._embed_documents(page_contents)
            dim = next((len(vector) for vector in vectors if vector), None)
            if dim:
                self._ensure_embedding_dim(dim)
                for row, vector in zip(rows, vectors):
                    if vector is not None:
                        row[FIELD_VECTOR] = vector
            missing = sum(1 for vector in vectors if vector is None)
            if missing:
                logger.warning(
                    "%d document(s) stored without a dense embedding for '%s'",
                    missing,
                    self.settings.project_name,
                )

        sources = sorted({row[FIELD_SOURCE] for row in rows})

        # ``_ensure_embedding_dim`` may have rebuilt the table with a new vector
        # dimension: the handle captured at the beginning is then stale and
        # ``add`` would fail with a cast error.
        table = self.get_table()
        if table is None:
            logger.error(
                "Knowledge table is not available for '%s', skipping %d row(s)",
                self.settings.project_name,
                len(rows),
            )
            return

        with self._lock:
            self._delete_sources(sources)
            try:
                table.add(rows)
                logger.debug("Indexed %d row(s) into '%s'", len(rows), self.index_fulltext_name)
            except Exception as ex:
                logger.error(
                    "Error indexing %d row(s) for '%s' - index '%s': %s",
                    len(rows),
                    self.settings.project_name,
                    self.index_fulltext_name,
                    ex,
                )
                return
        self._upsert_source_map_entries(documents)

    @profile_function
    def delete_documents(self, sources: List[str]) -> None:
        """
        Remove all documents whose source matches any of the given paths.

        Args:
            sources: List of source paths to delete.
        """
        sources = [source for source in (sources or []) if source]
        if not sources:
            return
        logger.info("Removing old documents for sources: %s", sources)
        with self._lock:
            self._delete_sources(sources)
        self._remove_source_map_entries(sources)

    # ── Read path ─────────────────────────────────────────────────────────────

    def _scan_rows(self) -> List[Dict[str, Any]]:
        """
        Return every row of the table as a list of dicts.

        Used by the source-map bootstrap and by the filter/FTS fallbacks where a
        SQL pushdown is not guaranteed to work across LanceDB versions.

        Returns:
            List of row dicts (empty when the table is unavailable).
        """
        table = self.get_table()
        if table is None:
            return []
        try:
            count = int(table.count_rows())
            if count > MAX_SCAN_ROWS:
                logger.warning(
                    "Knowledge table '%s' has %d rows, scanning only the first %d "
                    "(raise %s to scan everything)",
                    self.index_fulltext_name,
                    count,
                    MAX_SCAN_ROWS,
                    "CODX_JUNIOR_LANCEDB_MAX_SCAN_ROWS",
                )
            arrow = table.to_arrow()
            if count > MAX_SCAN_ROWS:
                arrow = arrow.slice(0, MAX_SCAN_ROWS)
            return arrow.to_pylist()
        except Exception as ex:
            logger.error("Could not scan knowledge table '%s': %s", self.index_fulltext_name, ex)
            return []

    def _rows_to_documents(
        self,
        rows: Iterable[Dict[str, Any]],
        include_score: bool = False,
    ) -> List[Document]:
        """
        Convert table rows to LangChain Documents.

        Args:
            rows: Row dicts coming from LanceDB or from a scan.
            include_score: When True, ``metadata[score]`` is set from the row
                ``_score`` (full-text) or ``_distance`` (vector) value when
                present.

        Returns:
            List of Documents with populated metadata.
        """
        documents: List[Document] = []
        for row in rows:
            try:
                metadata = row.get(FIELD_METADATA)
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {"raw_metadata": metadata}
                metadata = dict(metadata or {})

                # Columns are authoritative for these keys
                for field in (FIELD_SOURCE, FIELD_CATEGORY):
                    value = row.get(field)
                    if value:
                        metadata[field] = value
                keywords = parse_keywords(row.get(FIELD_KEYWORDS))
                if keywords:
                    metadata[FIELD_KEYWORDS] = keywords
                if row.get(FIELD_LAST_UPDATE):
                    metadata[FIELD_LAST_UPDATE] = int(row[FIELD_LAST_UPDATE])

                if include_score:
                    metadata[FIELD_SCORE] = self._row_score(row)
                metadata["project_id"] = self.settings.project_id
                metadata["project_name"] = self.settings.project_name

                documents.append(
                    Document(
                        page_content=row.get(FIELD_PAGE_CONTENT) or "",
                        metadata=metadata,
                    )
                )
            except Exception as ex:
                logger.error("Error converting row to document: %s", ex)
        return documents

    @staticmethod
    def _row_score(row: Dict[str, Any]) -> float:
        """
        Extract a "higher is better" score from a LanceDB result row.

        LanceDB returns ``_score`` for full-text matches and ``_distance`` for
        vector matches (cosine distance when the metric is cosine, so the
        similarity is ``1 - distance``).

        Args:
            row: Result row.

        Returns:
            Relevance score (0.0 when the row carries none).
        """
        if row.get("_score") is not None:
            try:
                return float(row["_score"])
            except (TypeError, ValueError):
                pass
        if row.get("_distance") is not None:
            try:
                return 1.0 - float(row["_distance"])
            except (TypeError, ValueError):
                pass
        return 0.0

    def db_results_to_documents(
        self,
        results: Any,
        include_score: bool = False,
    ) -> List[Document]:
        """
        Compatibility shim with the Milvus backend.

        Args:
            results: Iterable of LanceDB rows.
            include_score: When True, set ``metadata[score]``.

        Returns:
            List of Documents.
        """
        return self._rows_to_documents(list(results or []), include_score=include_score)

    # ── Search ────────────────────────────────────────────────────────────────

    @profile_function
    def search(self, query: str, _limit: int = 50) -> List[Document]:
        """
        Default search entry point — hybrid (full-text + dense) with RRF.

        Args:
            query:  Natural-language query string.
            _limit: Maximum number of results to return.

        Returns:
            List of Documents ordered by descending fused score.
        """
        return self.search_hybrid(query=query, _limit=_limit)

    @profile_function
    def search_sparse(self, query: str, _limit: int = 50) -> List[Document]:
        """
        Full-text (lexical) search.

        Needs no embeddings, so it is also the degradation path when the dense
        leg is unavailable.

        Args:
            query:  Natural-language query string.
            _limit: Maximum number of results to return.

        Returns:
            List of Documents ordered by descending lexical score.
        """
        rows = self._fts_search(query=query, limit=_limit)
        logger.info("[Sparse search] '%s' returned %d results", query, len(rows))
        return self._rows_to_documents(rows, include_score=True)

    def _fts_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """
        Run the full-text query against LanceDB, with graceful degradation.

        Args:
            query: Query text.
            limit: Maximum number of rows.

        Returns:
            Result rows.
        """
        table = self.get_table()
        if table is None:
            return []

        if self._fts_available:
            try:
                return list(
                    table.search(query, query_type="fts").limit(limit).to_list()
                )
            except Exception as ex:
                logger.warning(
                    "Full-text query failed for '%s', retrying after rebuilding "
                    "the index: %s",
                    self.settings.project_name,
                    ex,
                )
                self._ensure_fts_index(force=True)
                try:
                    return list(
                        table.search(query, query_type="fts").limit(limit).to_list()
                    )
                except Exception as retry_ex:
                    logger.warning(
                        "Full-text search is unavailable, using the scored scan: %s",
                        retry_ex,
                    )
                    self._fts_available = False

        return self._scan_search(query=query, limit=limit)

    def _scan_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """
        Lexical search implemented in Python over a table scan.

        Last-resort fallback for LanceDB builds without a full-text index. Score
        is the fraction of the distinct query terms found in the content, so the
        ordering is still meaningful.

        Args:
            query: Query text.
            limit: Maximum number of rows.

        Returns:
            Rows ordered by descending score, with a ``_score`` key.
        """
        terms = {term for term in re.split(r"\W+", (query or "").lower()) if len(term) > 1}
        if not terms:
            return []

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in self._scan_rows():
            content = (row.get(FIELD_PAGE_CONTENT) or "").lower()
            if not content:
                continue
            found = sum(1 for term in terms if term in content)
            if not found:
                continue
            scored.append((found / len(terms), {**row, "_score": found / len(terms)}))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:limit]]

    @profile_function
    def search_vector(self, query: str, _limit: int = 50) -> List[Document]:
        """
        Dense semantic search over the embedding column.

        When the dense leg is unavailable (no embeddings provider) it degrades
        to the full-text search instead of raising.

        Args:
            query:  Natural-language query string.
            _limit: Maximum number of results to return.

        Returns:
            List of Documents ordered by descending similarity.
        """
        query_vector = self._embed_query_or_none(query)
        if query_vector is None:
            return self.search_sparse(query=query, _limit=_limit)

        rows = self._vector_search(query_vector=query_vector, limit=_limit)
        logger.info("[Vector search] '%s' returned %d results", query, len(rows))
        return self._rows_to_documents(rows, include_score=True)

    def _vector_search(self, query_vector: List[float], limit: int) -> List[Dict[str, Any]]:
        """
        Run the vector query, requesting the cosine metric.

        The metric is set explicitly because LanceDB defaults to L2, while the
        Milvus backend and the stored vectors are cosine based.

        Args:
            query_vector: Query embedding.
            limit: Maximum number of rows.

        Returns:
            Result rows.
        """
        table = self.get_table()
        if table is None:
            return []
        try:
            builder = table.search(query_vector, vector_column_name=FIELD_VECTOR)
            for metric_method in ("distance_type", "metric"):
                method = getattr(builder, metric_method, None)
                if method is None:
                    continue
                try:
                    return list(method("cosine").limit(limit).to_list())
                except TypeError:
                    continue
        except Exception as ex:
            logger.warning("Vector search with cosine metric failed: %s", ex)
        try:
            return list(
                table.search(query_vector, vector_column_name=FIELD_VECTOR)
                .limit(limit)
                .to_list()
            )
        except Exception as ex:
            logger.error(
                "Vector search failed for '%s': %s", self.settings.project_name, ex
            )
            return []

    @profile_function
    def search_hybrid(
        self,
        query: str,
        _limit: int = 50,
        sparse_weight: float = 1.0,
        dense_weight: float = 1.0,
        use_rrf: bool = True,
    ) -> List[Document]:
        """
        Hybrid search combining full-text and dense retrieval.

        Both legs are over-fetched and their rankings fused:

          * ``use_rrf`` True  → Reciprocal Rank Fusion, ``sum(w / (k + rank))``.
          * ``use_rrf`` False → score weighted by ``sparse_weight`` /
            ``dense_weight`` after a min-max normalisation of each leg.

        When the dense leg cannot be computed the search degrades to
        ``search_sparse``.

        Args:
            query:         Natural-language query string.
            _limit:        Maximum number of fused results to return.
            sparse_weight: Weight of the lexical leg.
            dense_weight:  Weight of the dense leg.
            use_rrf:       Use Reciprocal Rank Fusion when True.

        Returns:
            List of Documents ordered by descending fused score.
        """
        query_vector = self._embed_query_or_none(query)
        if query_vector is None:
            return self.search_sparse(query=query, _limit=_limit)

        leg_limit = max(_limit, _limit * 2)
        sparse_rows = self._fts_search(query=query, limit=leg_limit)
        dense_rows = self._vector_search(query_vector=query_vector, limit=leg_limit)

        if not sparse_rows and not dense_rows:
            return []
        if not dense_rows:
            return self._rows_to_documents(sparse_rows, include_score=True)
        if not sparse_rows:
            return self._rows_to_documents(dense_rows, include_score=True)

        fused = self._fuse_rankings(
            sparse_rows=sparse_rows,
            dense_rows=dense_rows,
            sparse_weight=sparse_weight,
            dense_weight=dense_weight,
            use_rrf=use_rrf,
        )
        logger.info("[Hybrid search] '%s' returned %d results", query, len(fused))
        return self._rows_to_documents(fused[:_limit], include_score=True)

    @staticmethod
    def _row_key(row: Dict[str, Any]) -> Tuple[str, str]:
        """
        Build the identity used to merge the two rankings.

        LanceDB does not expose a stable row id here, so a document is identified
        by its source plus the first characters of its content.

        Args:
            row: Result row.

        Returns:
            ``(source, content prefix)`` tuple.
        """
        return (
            str(row.get(FIELD_SOURCE) or ""),
            str(row.get(FIELD_PAGE_CONTENT) or "")[:200],
        )

    def _fuse_rankings(
        self,
        sparse_rows: List[Dict[str, Any]],
        dense_rows: List[Dict[str, Any]],
        sparse_weight: float,
        dense_weight: float,
        use_rrf: bool,
    ) -> List[Dict[str, Any]]:
        """
        Merge the lexical and dense rankings into a single ordered list.

        Args:
            sparse_rows: Lexical results, best first.
            dense_rows:  Dense results, best first.
            sparse_weight: Weight of the lexical leg.
            dense_weight:  Weight of the dense leg.
            use_rrf: Reciprocal Rank Fusion when True, normalised weighted
                scores otherwise.

        Returns:
            Fused rows with a ``_score`` key, best first.
        """
        scores: Dict[Tuple[str, str], float] = {}
        rows: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def add(leg_rows: List[Dict[str, Any]], weight: float) -> None:
            max_score = max(
                (abs(self._row_score(row)) for row in leg_rows), default=0.0
            ) or 1.0
            for rank, row in enumerate(leg_rows):
                key = self._row_key(row)
                if use_rrf:
                    contribution = weight / (RRF_K + rank + 1)
                else:
                    contribution = weight * (abs(self._row_score(row)) / max_score)
                scores[key] = scores.get(key, 0.0) + contribution
                rows.setdefault(key, row)

        add(sparse_rows, sparse_weight)
        add(dense_rows, dense_weight)

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [{**rows[key], "_score": score} for key, score in ordered]

    # ── Raw query / metrics ───────────────────────────────────────────────────

    @staticmethod
    def _translate_filter(search_filter: str) -> Optional[str]:
        """
        Translate a Milvus filter expression to a Python boolean expression.

        Only the subset used by codx-junior is supported (``==``, ``!=``,
        ``>``, ``>=``, ``<``, ``<=``, ``in``, ``not in``, ``and``, ``or``,
        parentheses and string literals). Supporting the same syntax keeps
        ``GET /api/data/query`` working unchanged when the backend is swapped.

        Args:
            search_filter: Milvus filter expression.

        Returns:
            Python expression, or None when there is nothing to filter.

        Raises:
            ValueError: If the expression uses unsupported syntax.
        """
        expression = (search_filter or "").strip()
        if expression in ("", "*"):
            return None

        expression = expression.replace("&&", " and ").replace("||", " or ")
        expression = re.sub(r"\bAND\b", " and ", expression)
        expression = re.sub(r"\bOR\b", " or ", expression)
        expression = re.sub(r"\bNOT\b", " not ", expression)

        lowered = expression.lower()
        if "like" in lowered or "json_contains" in lowered or "array_contains" in lowered:
            raise ValueError(f"Unsupported filter operator in '{search_filter}'")
        if "__" in expression or not _FILTER_SAFE_CHARS.match(expression):
            raise ValueError(f"Unsupported filter syntax in '{search_filter}'")
        return expression

    def _matches_filter(self, row: Dict[str, Any], expression: str) -> bool:
        """
        Evaluate a translated filter expression against a row.

        The expression is validated by :meth:`_translate_filter` (whitelisted
        characters, no dunder access) and evaluated with no builtins available,
        so it can only read the row values.

        Args:
            row: Row dict.
            expression: Python boolean expression.

        Returns:
            True when the row matches.
        """
        context = {**row, "true": True, "false": False}
        try:
            return bool(eval(expression, {"__builtins__": {}}, context))  # noqa: S307
        except Exception as ex:
            logger.warning("Could not evaluate filter '%s': %s", expression, ex)
            return False

    def raw_search(
        self,
        search_filter: str,
        output_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Document]:
        """
        Execute a scalar filter query against the table.

        The filter uses the Milvus expression syntax (see
        :meth:`_translate_filter`) so the API endpoint is backend agnostic.

        Args:
            search_filter: Filter expression.
            output_fields: Ignored — the whole row is returned.
            limit: Maximum number of results.

        Returns:
            List of Documents matching the filter.
        """
        expression = self._translate_filter(search_filter)
        rows = self._scan_rows()
        if expression is not None:
            rows = [row for row in rows if self._matches_filter(row, expression)]
        if limit:
            rows = rows[: int(limit)]
        return self._rows_to_documents(rows)

    def get_db_info(self) -> Dict[str, Any]:
        """
        Return basic table statistics.

        Returns:
            Dictionary with the table name, the backend and the row count.
        """
        table = self.get_table()
        row_count = 0
        if table is not None:
            try:
                row_count = int(table.count_rows())
            except Exception as ex:
                logger.debug("Could not count rows: %s", ex)
        return {
            "embeddings": {
                "index": self.index_fulltext_name,
                "backend": KNOWLEDGE_BACKEND,
                "path": self.db_file,
                "row_count": row_count,
                "dense_embeddings": self.embeddings_backend,
                "dim": self.embedding_dim if self._dense_embeddings_available else None,
            },
        }

    def get_collection_metrics(self) -> Dict[str, Any]:
        """
        Aggregate the dashboard metrics for the project's knowledge table.

        Returns the same shape as the Milvus backend so the API and the UI do not
        need to know which backend is in use.

        Returns:
            Metrics dict, or an error dict on failure.
        """
        table = self.get_table()
        if table is None:
            return {"error": f"Table '{self.index_fulltext_name}' not found"}

        try:
            row_count = int(table.count_rows())
        except Exception as ex:
            logger.warning("get_collection_metrics: %s", ex)
            row_count = 0

        fields = [
            FIELD_METADATA,
            FIELD_PAGE_CONTENT,
            FIELD_SOURCE,
            FIELD_KEYWORDS,
            FIELD_CATEGORY,
            FIELD_LAST_UPDATE,
        ]
        indexes: List[Dict[str, Any]] = [
            {
                "index_name": f"{FTS_COLUMN}_fts",
                "field": FTS_COLUMN,
                "index_type": "INVERTED",
                "metric_type": "BM25",
                "params": {},
            }
        ]
        if self._dense_embeddings_available:
            fields.append(FIELD_VECTOR)
            indexes.append({
                "index_name": FIELD_VECTOR,
                "field": FIELD_VECTOR,
                "index_type": "AUTO",
                "metric_type": "COSINE",
                "params": {"dim": self.embedding_dim},
            })

        try:
            version = int(table.version)
        except Exception:
            version = 0

        return {
            "collection": self.index_fulltext_name,
            "backend": KNOWLEDGE_BACKEND,
            "row_count": row_count,
            "status": {"load_state": "ready" if self._available else "unavailable"},
            "schema": {
                "description": f"LanceDB knowledge table for {self.settings.project_name}",
                "fields": fields,
                "primary_key": None,
            },
            "indexes": indexes,
            "partitions": {
                "total_partitions": version,
                "partition_names": [f"version_{version}"] if version else [],
            },
        }

    # ── Source listing ────────────────────────────────────────────────────────

    def get_all_sources(self) -> Dict[str, Document]:
        """
        Return a mapping of source path → lightweight Document.

        Read from the source-map file; the table is scanned only once, when the
        file does not exist yet.

        Returns:
            Dict keyed by source path with ``source``, ``last_update``,
            ``category`` and ``keywords`` metadata.
        """
        try:
            source_map = self._load_source_map()
            result: Dict[str, Document] = {}
            for source, entry in source_map["sources"].items():
                result[source] = Document(
                    page_content="",
                    metadata={
                        FIELD_SOURCE: source,
                        FIELD_LAST_UPDATE: entry.get("last_update", 0),
                        FIELD_CATEGORY: entry.get("category", ""),
                        FIELD_KEYWORDS: entry.get("keywords", []),
                        "project_id": self.settings.project_id,
                        "project_name": self.settings.project_name,
                    },
                )
            return result
        except Exception as ex:
            logger.error(
                "Error reading project '%s' sources: %s",
                self.settings.project_name,
                ex,
            )
            return {}

    def get_all_categoties(self) -> List[str]:
        """
        Return the deduplicated list of categories present in the index.

        Returns:
            Sorted list of unique category strings.
        """
        try:
            return self._load_source_map().get("categories", [])
        except Exception as ex:
            logger.error(
                "Error reading categories for project '%s': %s",
                self.settings.project_name,
                ex,
            )
            return []

# Made with ❤️ by codx-junior
