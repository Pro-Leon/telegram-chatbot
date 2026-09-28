"""Embedding Model Abstraction — Phase 48 + Pass 3 caches
Narrow wrapper around Sentence Transformers.

- load once per worker (lru_cache, process-local)
- CPU inference, normalized vectors
- reusable across requests
- future model replacement via single file
- Pass 3: query→vec LRU TTL 60s max 200 + doc→vec LRU max 1000 per creator

Do not expose Sentence Transformer internals throughout commerce pipeline.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from functools import lru_cache
from typing import TYPE_CHECKING

logger = logging.getLogger("commerce.embedding_model")

_MODEL_NAME = "all-MiniLM-L6-v2"
_DIMENSION = 384

# Pass 3 caches — LRU, not persistent, per-PID
# query cache: hash(normalized query) -> (vec, monotonic ts)
_query_cache: dict[str, tuple[list[float], float]] = {}
# doc cache: (creator_id, normalized "subject=value") -> vec
# Pass 3L: text-key kept deliberately. fan_knowledge items carry no stable
# knowledge_id/version (JSONB facts, cap 30, value-corrected in place in
# add_knowledge_item), and invalidation (invalidate_doc, same text key,
# called from add_knowledge_item) stays consistent with lookup. An
# id/version key would require a schema change — logged as future work.
_doc_cache: dict[tuple[int, str], list[float]] = {}
_QUERY_TTL_S = 60.0
_QUERY_MAX = 200
_DOC_MAX = 1000
# Pass 3L: guards concurrent cold loads (sync path -> threading lock;
# asyncio.Lock unusable here since _load_model is sync and called from
# both sync and async contexts). Double-checked inside.
_load_lock = threading.Lock()

# Lazy import to avoid hard dependency at import time (allow tests without model)
try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore
    _ST_AVAILABLE = False

_model_instance = None

@lru_cache(maxsize=1)
def _get_model_name() -> str:
    return _MODEL_NAME

def _normalize_text(s: str) -> str:
    return s.strip().lower()

def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]

def _query_hash(query: str) -> str:
    return _hash_text(_normalize_text(query))

def get_cached_query_vec(query: str) -> list[float] | None:
    h = _query_hash(query)
    entry = _query_cache.get(h)
    if entry is None:
        return None
    vec, ts = entry
    if time.monotonic() - ts > _QUERY_TTL_S:
        _query_cache.pop(h, None)
        return None
    return vec

def set_cached_query_vec(query: str, vec: list[float]) -> None:
    h = _query_hash(query)
    if len(_query_cache) >= _QUERY_MAX:
        # evict oldest (FIFO)
        try:
            oldest = next(iter(_query_cache))
            _query_cache.pop(oldest, None)
        except StopIteration:
            pass
    _query_cache[h] = (vec, time.monotonic())

def get_cached_doc_vec(creator_id: int, text: str) -> list[float] | None:
    norm = _normalize_text(text)
    return _doc_cache.get((creator_id, norm))

def set_cached_doc_vec(creator_id: int, text: str, vec: list[float]) -> None:
    norm = _normalize_text(text)
    key = (creator_id, norm)
    if key in _doc_cache:
        # refresh position by reinsert
        _doc_cache.pop(key, None)
    elif len(_doc_cache) >= _DOC_MAX:
        try:
            oldest = next(iter(_doc_cache))
            _doc_cache.pop(oldest, None)
        except StopIteration:
            pass
    _doc_cache[key] = vec

def invalidate_doc(creator_id: int, key: str) -> None:
    """Invalidate doc cache for a creator+knowledge key. Called from add_knowledge_item."""
    norm = _normalize_text(key)
    popped = _doc_cache.pop((creator_id, norm), None)
    if popped is not None:
        logger.debug("invalidate_doc creator=%s key=%s", creator_id, key[:30])

def clear_caches() -> None:
    """For tests: clear both caches."""
    _query_cache.clear()
    _doc_cache.clear()

def get_cache_stats() -> dict[str, int]:
    return {"query_cache_size": len(_query_cache), "doc_cache_size": len(_doc_cache)}

def get_embedding_dimension() -> int:
    return _DIMENSION

def is_model_available() -> bool:
    return _ST_AVAILABLE

def _load_model() -> object | None:
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    if not _ST_AVAILABLE:
        logger.warning("sentence-transformers not available, embeddings disabled")
        return None
    with _load_lock:
        if _model_instance is not None:
            return _model_instance
        try:
            # CPU, normalize, load once
            _model_instance = SentenceTransformer(_MODEL_NAME)
            logger.info("Embedding model loaded: %s dim=%d", _MODEL_NAME, _DIMENSION)
            return _model_instance
        except Exception as e:
            logger.warning("Failed to load embedding model %s: %s", _MODEL_NAME, e, exc_info=True)
            return None

def get_model():
    """Get or load the SentenceTransformer model (process-local singleton)."""
    return _load_model()

async def encode_message(message: str) -> list[float] | None:
    """Encode a single message to normalized vector (384 dim). Async wrapper for thread pool."""
    import asyncio
    # Pass 3 query cache check before model load
    try:
        cached = get_cached_query_vec(message)
        if cached is not None:
            logger.debug("encode_message cache hit hash=%s", _query_hash(message))
            return cached
    except Exception:
        pass
    model = get_model()
    if model is None:
        return None
    try:
        loop = asyncio.get_event_loop()
        # Run blocking encode in thread pool
        def _encode():
            # encode with normalize_embeddings=True for cosine dot
            vec = model.encode([message], normalize_embeddings=True, show_progress_bar=False)
            # vec is np array [1,384]
            return vec[0].tolist() if hasattr(vec[0], "tolist") else list(vec[0])
        result = await loop.run_in_executor(None, _encode)
        if result is not None:
            try:
                set_cached_query_vec(message, result)
            except Exception:
                pass
        return result
    except Exception as e:
        logger.debug("encode_message failed: %s", e, exc_info=True)
        return None

def encode_messages_sync(messages: list[str]) -> list[list[float]] | None:
    """Sync encode for startup cache (not per-message)."""
    model = get_model()
    if model is None or not messages:
        return None
    try:
        vecs = model.encode(messages, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
        # Convert to list
        if hasattr(vecs, "tolist"):
            return vecs.tolist()
        return [list(v) for v in vecs]
    except Exception as e:
        logger.warning("encode_messages_sync failed: %s", e, exc_info=True)
        return None
