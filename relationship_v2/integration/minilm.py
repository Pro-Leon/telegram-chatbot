"""Local MiniLM embedder (Stage F5 wiring). Text in, vectors out.

Boundary to ML infra: sentence-transformers (already a project dependency
pattern via context_engine) with all-MiniLM-L6-v2 (384 dims, matching
migration 008). Model loads lazily once per process; workers each hold
their own instance (no shared mutable state). Normalized embeddings so
cosine similarity is meaningful. No providers, no network at call time
(weights cached locally after first download).
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("sunny.v2.minilm")

MODEL_NAME = "all-MiniLM-L6-v2"
EXPECTED_DIMS = 384

_lock = threading.Lock()
_model = None


def _load():
    global _model
    from sentence_transformers import SentenceTransformer

    with _lock:
        if _model is None:
            logger.info("loading MiniLM model %s", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME)
            dims = _model.get_embedding_dimension()
            if dims != EXPECTED_DIMS:
                raise ValueError(f"MiniLM dims {dims} != migration 008 ({EXPECTED_DIMS})")
        return _model


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed one batch. Fail-closed (empty input / load failure raises)."""
    if not texts or not all(t and t.strip() for t in texts):
        raise ValueError("non-empty texts required")
    model = _load()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [[float(x) for x in row] for row in vectors]


async def embed_query(text: str) -> list[float]:
    """Single-query port matching `services.semantic_retrieval.embed_query`."""
    rows = await embed_texts([text])
    return rows[0]
