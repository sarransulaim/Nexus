"""
rag.py — Retrieval-Augmented Generation for the digital twin (Stage 1)
======================================================================
Tenant-scoped semantic memory. Embeddings are stored in Postgres
(knowledge_embeddings) and similarity is computed in-process with numpy.

Why no pgvector: the target Postgres (Windows, PG18) has no pgvector binary,
and compiling a native extension there is fragile. At pilot scale (thousands
of chunks) a brute-force cosine in numpy is sub-100ms and dependency-free.
Everything vector-specific lives behind `search()` / `_top_k()`, so swapping
in pgvector or a dedicated vector DB later is a localized change.

Embeddings come from local Ollama (free): model `nomic-embed-text` (768-dim).
Pull it once with:  ollama pull nomic-embed-text
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from database.core import SessionLocal
from database.models import KnowledgeEmbedding, UploadedFile, Task
from api.ollama_client import OllamaClient

log = logging.getLogger("nexus.rag")

# ── Config (swappable via env) ────────────────────────────────
# EMBED_PROVIDER: "ollama" (local, free), "gemini" (hosted — for cloud deploys
# with no Ollama sidecar), or "auto" (prefer Ollama if reachable, else Gemini
# if GEMINI_API_KEY is set). Resolved ONCE per process: rows are tagged with
# the model that made them and search filters on it, so mid-process flapping
# between providers would split the index.
EMBED_PROVIDER     = os.getenv("EMBED_PROVIDER", "auto").lower()
EMBED_MODEL        = os.getenv("EMBED_MODEL", "nomic-embed-text")   # ollama model
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM          = int(os.getenv("EMBED_DIM", "768"))  # both providers emit 768 (gemini via output_dimensionality)
CHUNK_CHARS        = 1200    # ~300 tokens per chunk
CHUNK_OVERLAP      = 200     # carry context across chunk boundaries
MAX_EMBED_CHARS    = 8000    # hard cap per embed call (safety)
DEFAULT_COMPANY_ID = 1       # single-tenant per instance for the pilot

_ollama = OllamaClient()

# Bounded worker pool for background indexing. Embeds are serialized through a
# single local Ollama anyway, so a couple of workers is plenty — and it stops an
# upload / chat burst from spawning an unbounded number of daemon threads (#9),
# which would explode thread + Ollama load and memory. Excess work queues.
_INDEX_WORKERS = int(os.getenv("RAG_INDEX_WORKERS", "2"))
_index_pool = ThreadPoolExecutor(max_workers=_INDEX_WORKERS, thread_name_prefix="rag-index")


# ── Embedding + chunking ──────────────────────────────────────

# ── Provider resolution (once per process) ────────────────────
_resolved_provider = None
_gemini_client = None


def _provider() -> str:
    """ollama | gemini — resolved once. 'auto' prefers the free local Ollama and
    falls back to Gemini (hosted) so RAG keeps working on cloud deploys."""
    global _resolved_provider
    if _resolved_provider:
        return _resolved_provider
    if EMBED_PROVIDER in ("ollama", "gemini"):
        _resolved_provider = EMBED_PROVIDER
    elif _ollama.is_available():
        _resolved_provider = "ollama"
    elif os.getenv("GEMINI_API_KEY"):
        _resolved_provider = "gemini"
    else:
        _resolved_provider = "ollama"   # nothing reachable — keep trying local
    log.info(f"RAG embedding provider: {_resolved_provider}")
    return _resolved_provider


def active_embed_model() -> str:
    """The model rows are tagged with (and search filters on). Switching
    providers changes this, so old rows simply stop matching — re-run
    `python rag.py backfill` after a switch to re-index under the new model."""
    return GEMINI_EMBED_MODEL if _provider() == "gemini" else EMBED_MODEL


def _gemini_embed(text: str) -> list:
    global _gemini_client
    if _gemini_client is None:
        import google.genai as genai
        from google.genai import types as _gt
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                                      http_options=_gt.HttpOptions(timeout=30_000))
    from google.genai import types as _gt
    r = _gemini_client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=text,
        config=_gt.EmbedContentConfig(output_dimensionality=EMBED_DIM),
    )
    return list(r.embeddings[0].values)


def embed_text(text: str, timeout: int = None) -> list:
    """Return the embedding vector for `text`, or [] if empty/failed.

    Pass `timeout` (seconds) for latency-sensitive callers (e.g. an interactive
    search) so a slow/hung Ollama can't block for the full default 60s.
    (The Gemini path has a fixed 30s HTTP timeout; `timeout` applies to Ollama.)"""
    text = (text or "").strip()
    if not text:
        return []
    if _provider() == "gemini":
        return _gemini_embed(text[:MAX_EMBED_CHARS])
    client = OllamaClient(timeout=timeout) if timeout is not None else _ollama
    return client.embed(model=EMBED_MODEL, text=text[:MAX_EMBED_CHARS])


def backend_available() -> bool:
    """Fast probe of the embedding backend — lets interactive callers fail fast
    with a clear message instead of waiting out a full embed timeout. The
    hosted Gemini path is assumed up (no cheap probe; its own 30s cap applies)."""
    try:
        if _provider() == "gemini":
            return True
        return _ollama.is_available()
    except Exception:
        return False


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list:
    """Split text into overlapping windows. Short text → a single chunk."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks = []
    for start in range(0, len(text), step):
        chunk = text[start:start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


# ── Indexing ──────────────────────────────────────────────────

def index_content(company_id: int, source_type: str, source_id, text: str, meta: dict = None) -> int:
    """
    Chunk, embed, and store a source's content. Idempotent for real sources:
    re-indexing the same (company_id, source_type, source_id) replaces its
    prior chunks. Returns the number of chunks stored.
    """
    chunks = chunk_text(text)
    if not chunks:
        return 0

    db = SessionLocal()
    try:
        # idempotent re-index: drop prior chunks for this specific source
        if source_id is not None:
            db.query(KnowledgeEmbedding).filter(
                KnowledgeEmbedding.company_id  == company_id,
                KnowledgeEmbedding.source_type == source_type,
                KnowledgeEmbedding.source_id   == source_id,
            ).delete(synchronize_session=False)

        stored = 0
        for i, ch in enumerate(chunks):
            try:
                vec = embed_text(ch)
            except Exception as e:
                log.warning(f"embed failed for {source_type}:{source_id} chunk {i}: {e}")
                continue
            if not vec:
                continue
            if len(vec) != EMBED_DIM:
                # guard the table against off-dimension vectors (truncated/empty
                # embeds) so retrieval can never build a ragged matrix
                log.warning(f"skip {source_type}:{source_id} chunk {i} — "
                            f"dim {len(vec)} != EMBED_DIM {EMBED_DIM}")
                continue
            db.add(KnowledgeEmbedding(
                company_id=company_id,
                source_type=source_type,
                source_id=source_id,
                chunk_index=i,
                content=ch,
                embedding=vec,
                embed_model=active_embed_model(),
                meta=meta or {},
            ))
            stored += 1
        db.commit()
        return stored
    finally:
        db.close()


def index_async(company_id: int, source_type: str, source_id, text: str, meta: dict = None):
    """
    Fire-and-forget indexing in a daemon thread. Embeds are slow (~100ms+ each),
    so request handlers (upload, chat send) use this to avoid blocking the
    response. Failures are logged, never raised.
    """
    def _run():
        try:
            index_content(company_id, source_type, source_id, text, meta=meta)
        except Exception as e:
            log.warning(f"async index failed for {source_type}:{source_id}: {e}")
    try:
        _index_pool.submit(_run)
    except RuntimeError as e:
        # Pool shut down (interpreter exiting) — indexing is best-effort, so
        # just skip rather than block the caller with an inline embed.
        log.warning(f"index pool unavailable ({e}); skipped async index for {source_type}:{source_id}")


# ── Retrieval ─────────────────────────────────────────────────

def _top_k(query_vec, rows, k: int):
    """Brute-force cosine similarity. The one place to swap for a real ANN index."""
    q = np.asarray(query_vec, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return []
    q = q / qn
    mat = np.asarray([r.embedding for r in rows], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    scores = (mat / norms[:, None]) @ q
    order = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in order]


def search(company_id: int, query: str, k: int = 5,
           source_types: list = None, min_score: float = 0.30,
           query_timeout: int = None) -> list:
    """
    Semantic search over a company's knowledge. ALWAYS scoped to company_id
    (tenant isolation) and to the active embed model (dimension safety).
    Returns a list of {score, content, source_type, source_id, meta}.

    `query_timeout` bounds the query-embed for interactive callers.
    """
    try:
        qvec = embed_text(query, timeout=query_timeout)
    except Exception as e:
        log.warning(f"query embed failed: {e}")
        return []
    if not qvec:
        return []

    db = SessionLocal()
    try:
        q = db.query(KnowledgeEmbedding).filter(
            KnowledgeEmbedding.company_id  == company_id,
            KnowledgeEmbedding.embed_model == active_embed_model(),
        )
        if source_types:
            q = q.filter(KnowledgeEmbedding.source_type.in_(source_types))
        rows = q.all()
        # Defensive: only compare against vectors matching the query length, so a
        # stray off-dimension row can never make the similarity matrix ragged.
        dim = len(qvec)
        rows = [r for r in rows if isinstance(r.embedding, list) and len(r.embedding) == dim]
        if not rows:
            return []

        hits = []
        for idx, score in _top_k(qvec, rows, k):
            if score < min_score:
                continue
            r = rows[idx]
            hits.append({
                "score":       round(score, 4),
                "content":     r.content,
                "source_type": r.source_type,
                "source_id":   r.source_id,
                "meta":        r.meta or {},
            })
        return hits
    finally:
        db.close()


# ── Backfill (index what's already in the DB) ─────────────────

def backfill(company_id: int = DEFAULT_COMPANY_ID) -> dict:
    """Index existing content already sitting in the DB. Idempotent."""
    summary = {"uploaded_files": 0, "tasks": 0, "chunks": 0}

    # Pull source data first, release the session, THEN embed (embeds are slow).
    db = SessionLocal()
    try:
        files = [
            (f.id, f.original_filename, f.extracted_text)
            for f in db.query(UploadedFile).filter(
                UploadedFile.company_id == company_id,
                UploadedFile.extracted_text.isnot(None),
            ).all()
        ]
        tasks = [
            (t.id, t.title, t.description)
            for t in db.query(Task).filter(Task.company_id == company_id).all()
        ]
    finally:
        db.close()

    for fid, fname, txt in files:
        n = index_content(company_id, "uploaded_file", fid, txt, meta={"filename": fname})
        if n:
            summary["uploaded_files"] += 1
            summary["chunks"] += n

    for tid, title, desc in tasks:
        body = f"{title}\n\n{desc or ''}".strip()
        n = index_content(company_id, "task", tid, body, meta={"title": title})
        if n:
            summary["tasks"] += 1
            summary["chunks"] += n

    return summary


# ── CLI: `python rag.py [backfill|search <query>|stats]` ──────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"

    if cmd == "backfill":
        print("Indexing existing content...")
        print("Done:", backfill())
    elif cmd == "search":
        query = " ".join(sys.argv[2:]) or "project deadline"
        print(f"Searching: {query!r}\n")
        for h in search(DEFAULT_COMPANY_ID, query, k=5, min_score=0.0):
            tag = h["meta"].get("filename") or h["meta"].get("title") or h["source_type"]
            print(f"  [{h['score']}] ({tag}) {h['content'][:120].strip()}...")
    else:  # stats
        db = SessionLocal()
        try:
            total = db.query(KnowledgeEmbedding).count()
            print(f"knowledge_embeddings rows: {total}")
        finally:
            db.close()
