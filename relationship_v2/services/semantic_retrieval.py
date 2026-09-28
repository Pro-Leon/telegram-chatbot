"""Hybrid retrieval fusion (Stage F5). Lexical + semantic in, order out.

Caller: FUTURE assembly/recall (F9+). The embedding model is caller-supplied
(`EmbeddingPort`, like `GenerationPort` — no provider imports here); fact
embeddings live in `v2_memory_facts.embedding` (migration 007); cosine
nearest-neighbors come from `repository.search_facts_semantic`. This module
is the pure fusion layer: normalized score blending plus budget-capped
reranking. Lexical retrieval never depends on embeddings being present.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.semantic_retrieval")

DEFAULT_ALPHA = 0.5

EmbeddingPort = Callable[[str], Awaitable[list[float]]]


class FusedHit(BaseModel):
    ref_id: str = Field(min_length=1)
    lexical: float = Field(ge=0.0)
    semantic: float = Field(ge=0.0, le=1.0)
    fused: float = Field(ge=0.0)

    model_config = {"frozen": True}


def fuse_scores(lexical: float, semantic: float, alpha: float = DEFAULT_ALPHA) -> float:
    """Blend normalized lexical rank-score with cosine similarity."""
    if lexical < 0.0:
        raise ValueError("lexical must be >= 0")
    if not 0.0 <= semantic <= 1.0:
        raise ValueError("semantic must be in [0, 1]")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    return round(alpha * lexical + (1.0 - alpha) * semantic, 6)


def rerank(
    lexical: dict[str, float],
    semantic: dict[str, float],
    alpha: float = DEFAULT_ALPHA,
    top_k: int = 10,
) -> list[FusedHit]:
    """Fuse two score maps over fact ids. Deterministic, budget-capped."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    hits = [
        FusedHit(
            ref_id=ref,
            lexical=lexical.get(ref, 0.0),
            semantic=semantic.get(ref, 0.0),
            fused=fuse_scores(lexical.get(ref, 0.0), semantic.get(ref, 0.0), alpha),
        )
        for ref in set(lexical) | set(semantic)
    ]
    hits.sort(key=lambda h: (-h.fused, h.ref_id))
    return hits[:top_k]


async def embed_query(text: str, embed: EmbeddingPort | None) -> list[float]:
    """Embed one query via the caller-supplied port. Port required."""
    if not text or not text.strip():
        raise ValueError("text required")
    if embed is None:
        raise ValueError("embedding port required (no provider inside V2)")
    vector = await embed(text.strip())
    if not vector:
        raise ValueError("embedding port returned empty vector")
    return vector
