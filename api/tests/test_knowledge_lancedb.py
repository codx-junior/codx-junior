"""
Tests for the embedded LanceDB knowledge backend.

They run against a real LanceDB dataset in a temporary folder — no server, no
network — which is the point of the backend: knowledge works with nothing
installed but the ``lancedb`` package.

Made with ❤️ by codx-junior
"""

import hashlib
import os
import sys

import pytest

from langchain_core.documents import Document

from codx.junior.knowledge.knowledge_lancedb import (
    DEFAULT_DENSE_EMBEDDING_DIM,
    FIELD_VECTOR,
    KnowledgeDBLanceDB,
)


class FakeEmbeddings:
    """Deterministic 64d embeddings (md5 bag of words)."""

    model_name = "fake-64d"

    @property
    def embedding_dim(self) -> int:
        """Dimension of the produced vectors."""
        return 64

    def _vector(self, text: str):
        """Map every word into a stable bucket and normalise."""
        vector = [0.0] * 64
        for word in (text or "").lower().split():
            digest = hashlib.md5(word.encode()).digest()
            vector[digest[0] % 64] += 1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    def embed_query(self, text: str):
        """Embed a query."""
        return self._vector(text)

    def embed_documents(self, texts):
        """Embed documents."""
        return [self._vector(text) for text in texts]


class FakeSettings:
    """Minimal settings surface used by the backend."""

    def __init__(self, root: str):
        self.abs_project_path = root
        self.codx_path = os.path.join(root, ".codx")
        self.project_name = "test"
        self.project_id = "test-id"
        os.makedirs(self.codx_path, exist_ok=True)

    def get_embeddings_settings(self):
        """No embeddings model: exercise the lexical-only path."""
        raise Exception("no embeddings model configured")

    def model_copy(self, update=None):
        """Return a copy with the given attributes overridden."""
        clone = FakeSettings(self.abs_project_path)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def build_documents():
    """Three documents with distinctive content and categories."""
    return [
        Document(
            page_content="wallet spending limits and balance",
            metadata={"source": "/p/wallet.py", "category": "code", "keywords": ["wallet", "money"]},
        ),
        Document(
            page_content="database migrations for milvus collections",
            metadata={"source": "/p/db.py", "category": "code", "keywords": ["db"]},
        ),
        Document(
            page_content="installer documentation quick start guide",
            metadata={"source": "/p/README.md", "category": "docs", "keywords": []},
        ),
    ]


@pytest.fixture
def lexical_db(tmp_path, monkeypatch):
    """A knowledge DB with the dense leg disabled (no embeddings provider)."""
    monkeypatch.setenv("CODX_JUNIOR_EMBEDDINGS_BACKEND", "none")
    db = KnowledgeDBLanceDB(settings=FakeSettings(str(tmp_path)))
    db.reset()
    db.index_documents(build_documents())
    return db


@pytest.fixture
def dense_db(tmp_path, monkeypatch):
    """A knowledge DB indexing with a (fake) embeddings provider."""
    monkeypatch.setenv("CODX_JUNIOR_EMBEDDINGS_BACKEND", "none")
    db = KnowledgeDBLanceDB(settings=FakeSettings(str(tmp_path)))
    db.embeddings_model = FakeEmbeddings()
    db.embeddings_backend = "provider"
    db._dense_embeddings_available = True
    db._embedding_dim = 64
    db.reset()
    db.index_documents(build_documents())
    return db


def test_indexes_documents_and_lists_sources(lexical_db):
    """Documents are indexed and listed from the source map."""
    sources = lexical_db.get_all_sources()
    assert set(sources) == {"/p/wallet.py", "/p/db.py", "/p/README.md"}
    assert sources["/p/wallet.py"].metadata["category"] == "code"
    assert sources["/p/wallet.py"].metadata["keywords"] == ["wallet", "money"]
    assert lexical_db.get_all_categoties() == ["code", "docs"]


def test_full_text_search_finds_the_best_match(lexical_db):
    """Lexical search works without any embeddings provider."""
    results = lexical_db.search("wallet limits", _limit=5)
    assert results
    assert results[0].metadata["source"] == "/p/wallet.py"
    assert results[0].metadata["score"] is not None


def test_hybrid_degrades_to_lexical_without_embeddings(lexical_db):
    """Without an embeddings provider the hybrid search still answers."""
    assert lexical_db.search_hybrid("wallet", _limit=5)


class BrokenEmbeddings:
    """Local model whose torch stack is installed but broken.

    This is the demo-image scenario: ``sentence-transformers`` is absent (or a
    transitive dependency fails to import) and the model only loads lazily, so
    the failure surfaces when the dimension is read inside ``create_db``.
    """

    model_name = "broken"

    @property
    def embedding_dim(self) -> int:
        """Fail the way a missing torch/sentence-transformers does."""
        raise ImportError("No module named 'torchvision'")

    def embed_query(self, text: str):
        """Never reached: the dense leg is switched off first."""
        raise ImportError("No module named 'torchvision'")

    def embed_documents(self, texts):
        """Never reached: the dense leg is switched off first."""
        raise ImportError("No module named 'torchvision'")


def test_broken_embeddings_stack_degrades_instead_of_raising(tmp_path, monkeypatch):
    """A broken embeddings stack must not take the knowledge base down."""
    monkeypatch.setenv("CODX_JUNIOR_EMBEDDINGS_BACKEND", "none")
    db = KnowledgeDBLanceDB(settings=FakeSettings(str(tmp_path)))
    db.embeddings_model = BrokenEmbeddings()
    db.embeddings_backend = "local"
    db._dense_embeddings_available = True
    db._embedding_dim = None

    # ``reset`` rebuilds the table, which is when the embedding dimension is
    # resolved (loading the model lazily) — the exact point a broken torch
    # stack used to raise from.
    db.reset()
    db.index_documents(build_documents())

    assert db._dense_embeddings_available is False
    assert db.embeddings_backend == "none"
    assert db.embedding_dim == DEFAULT_DENSE_EMBEDDING_DIM
    fields = db.get_collection_metrics()["schema"]["fields"]
    assert FIELD_VECTOR not in fields
    assert db.search_hybrid("wallet", _limit=5)


def test_raw_search_accepts_milvus_filter_syntax(lexical_db):
    """The Milvus filter syntax used by /api/data/query keeps working."""
    docs = lexical_db.raw_search('category == "docs"')
    assert [doc.metadata["source"] for doc in docs] == ["/p/README.md"]
    assert len(lexical_db.raw_search('source != ""')) == 3


def test_raw_search_rejects_unsupported_filters(lexical_db):
    """Unsupported syntax raises instead of returning everything."""
    with pytest.raises(ValueError):
        lexical_db.raw_search("json_contains(tags, \"a\")")


def test_delete_documents_removes_rows_and_source_map_entries(lexical_db):
    """Deleting a source removes its rows and its source-map entry."""
    lexical_db.delete_documents(sources=["/p/db.py"])
    assert set(lexical_db.get_all_sources()) == {"/p/wallet.py", "/p/README.md"}
    assert not lexical_db.search("migrations", _limit=5)


def test_reindexing_a_source_does_not_duplicate_rows(lexical_db):
    """index_documents behaves as an upsert."""
    lexical_db.index_documents([build_documents()[0]])
    assert len(lexical_db.get_all_sources()) == 3
    assert lexical_db.get_db_info()["embeddings"]["row_count"] == 3


def test_metrics_are_backend_agnostic(lexical_db):
    """The metrics payload keeps the shape the UI expects."""
    metrics = lexical_db.get_collection_metrics()
    assert metrics["row_count"] == 3
    assert metrics["backend"] == "lancedb"
    assert metrics["indexes"]


def test_vector_and_hybrid_search_with_embeddings(dense_db):
    """Both legs run and the fused score is comparable across them."""
    assert dense_db._table_vector_dim() == 64

    vector = dense_db.search_vector("wallet balance", _limit=5)
    assert vector and vector[0].metadata["source"] == "/p/wallet.py"

    hybrid = dense_db.search_hybrid("wallet balance", _limit=5)
    assert hybrid and hybrid[0].metadata["source"] == "/p/wallet.py"
    assert isinstance(hybrid[0].metadata["score"], float)


def test_embedding_dimension_change_rebuilds_the_table(dense_db):
    """A provider change with another dimension rebuilds instead of mixing."""
    dense_db.embeddings_model._vector = lambda text: [0.25] * 32
    dense_db.index_documents(build_documents())

    assert dense_db._table_vector_dim() == 32
    assert len(dense_db.get_all_sources()) == 3
    assert dense_db.get_db_info()["embeddings"]["row_count"] == 3


def test_reset_empties_the_index(lexical_db):
    """Reset drops the table and the source map."""
    lexical_db.reset()
    assert lexical_db.get_all_sources() == {}
    assert lexical_db.get_all_categoties() == []


def test_full_text_index_is_native_fts(lexical_db):
    """The lexical leg uses a real FTS index, not a table scan."""
    assert lexical_db._fts_available is True
    index_types = {str(index.index_type).lower() for index in lexical_db.get_table().list_indices()}
    assert any("fts" in index_type for index_type in index_types), index_types


def test_backend_selector_defaults_to_lancedb(monkeypatch):
    """Knowledge is built in: no environment variable needed."""
    from codx.junior.knowledge.knowledge_backend import get_knowledge_backend

    monkeypatch.delenv("CODX_JUNIOR_KNOWLEDGE_BACKEND", raising=False)
    assert get_knowledge_backend() == "lancedb"


def test_backend_selector_accepts_aliases(monkeypatch):
    """Aliases and the explicit off switch are understood."""
    from codx.junior.knowledge.knowledge_backend import get_knowledge_backend

    for raw, expected in (
        ("lancedb", "lancedb"),
        ("lance", "lancedb"),
        ("embedded", "lancedb"),
        ("milvus", "milvus"),
        ("none", "none"),
        ("off", "none"),
        ("nonsense", "lancedb"),
    ):
        monkeypatch.setenv("CODX_JUNIOR_KNOWLEDGE_BACKEND", raw)
        assert get_knowledge_backend() == expected, raw


def test_default_backend_is_the_embedded_one(tmp_path, monkeypatch):
    """With no configuration the embedded backend is what you get."""
    from codx.junior.knowledge.knowledge_backend import create_knowledge_db

    monkeypatch.delenv("CODX_JUNIOR_KNOWLEDGE_BACKEND", raising=False)
    monkeypatch.setenv("CODX_JUNIOR_EMBEDDINGS_BACKEND", "none")

    db = create_knowledge_db(FakeSettings(str(tmp_path)))

    assert isinstance(db, KnowledgeDBLanceDB)


def test_default_backend_never_imports_pymilvus(tmp_path, monkeypatch):
    """A LanceDB installation must not pull the Milvus client."""
    from codx.junior.knowledge.knowledge_backend import create_knowledge_db

    monkeypatch.delenv("CODX_JUNIOR_KNOWLEDGE_BACKEND", raising=False)
    monkeypatch.setenv("CODX_JUNIOR_EMBEDDINGS_BACKEND", "none")
    monkeypatch.delitem(sys.modules, "pymilvus", raising=False)

    create_knowledge_db(FakeSettings(str(tmp_path)))

    assert "pymilvus" not in sys.modules

# Made with ❤️ by codx-junior
