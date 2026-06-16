"""RAG: embeddings, semantic (not keyword) retrieval, tenant isolation, idempotent re-index.

Integration tests — skipped (not failed) if the local Ollama embedding model
(nomic-embed-text) isn't available, so an Ollama hiccup never red-flags the suite.
"""
import pytest
import rag
from database.core import SessionLocal
from database.models import KnowledgeEmbedding

CO, TT = 1, "_pytest_rag"


def _embeddings_available():
    try:
        return len(rag.embed_text("ping")) == rag.EMBED_DIM
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _embeddings_available(),
    reason="Ollama embedding model (nomic-embed-text) not available — run: ollama pull nomic-embed-text",
)


def _wipe():
    s = SessionLocal()
    try:
        s.query(KnowledgeEmbedding).filter(
            KnowledgeEmbedding.company_id == CO,
            KnowledgeEmbedding.source_type == TT,
        ).delete()
        s.commit()
    finally:
        s.close()


def test_embedding_dimension():
    v = rag.embed_text("hello world, this is a test")
    assert len(v) == rag.EMBED_DIM


def test_semantic_search_and_tenant_isolation():
    _wipe()
    rag.index_content(CO, TT, 1, "Maya is leading the React migration for the customer dashboard.", meta={"title": "a"})
    rag.index_content(CO, TT, 2, "The office coffee machine is broken again and needs servicing.", meta={"title": "b"})
    try:
        # semantic match — no shared keywords with the query
        hits = rag.search(CO, "who is doing the frontend rewrite?", k=2, source_types=[TT], min_score=0.0)
        assert hits and hits[0]["source_id"] == 1
        # tenant isolation — another company sees none of company 1's rows
        assert rag.search(CO + 1, "frontend rewrite", k=2, source_types=[TT], min_score=0.0) == []
    finally:
        _wipe()


def test_idempotent_reindex():
    _wipe()
    try:
        rag.index_content(CO, TT, 1, "the first version of this content, long enough to embed", meta={})
        rag.index_content(CO, TT, 1, "the first version of this content, long enough to embed", meta={})
        s = SessionLocal()
        try:
            n = s.query(KnowledgeEmbedding).filter(
                KnowledgeEmbedding.company_id == CO,
                KnowledgeEmbedding.source_type == TT,
                KnowledgeEmbedding.source_id == 1,
            ).count()
            assert n == 1
        finally:
            s.close()
    finally:
        _wipe()
