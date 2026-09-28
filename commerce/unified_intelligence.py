"""Unified Local Intelligence — Phase 48
Hybrid RapidFuzz (lexical) + Sentence Transformers (semantic) + brute-force cosine.

Architecture:
    MESSAGE
       ├─ RapidFuzz lexical (WRatio)
       └─ SentenceTransformer semantic (384, cosine vs 110 examples, brute-force)
                └─ unified signals → CommerceSignals

Isolated behind clear abstraction, not scattered.
Does NOT call LLM, not authoritative for price/product/offer.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("commerce.unified_intelligence")

# Provisional thresholds — centralized, configurable, BENCHMARK REQUIRED
LEXICAL_CUTOFF = 80  # WRatio
SEMANTIC_THRESHOLD = 0.65
MARGIN_THRESHOLD = 0.10
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MEDIUM = 0.5

# Normalization — shared for lexical and semantic (do not duplicate)
def normalize_message(text: str) -> str:
    """Shared normalization for lexical and semantic."""
    if not text:
        return ""
    # Lowercase, strip, collapse whitespace, keep alnum and basic punctuation
    t = text.strip().lower()
    # Unicode NFC via casefold already lower, keep as is
    t = re.sub(r"\s+", " ", t)
    return t

# RapidFuzz lexical evidence
def _lexical_scores(message: str, corpus_texts: list[str]) -> list[tuple[str, float, str]]:
    """Return list of (example_text, score, intent) for top lexical matches."""
    try:
        from rapidfuzz import fuzz, process
        # Use WRatio, score_cutoff 80
        results = process.extract(
            normalize_message(message),
            [normalize_message(t) for t in corpus_texts],
            scorer=fuzz.WRatio,
            score_cutoff=LEXICAL_CUTOFF,
            limit=3,
        )
        # results: (matched, score, idx)
        out = []
        for matched, score, idx in results:
            out.append((corpus_texts[idx], float(score), ""))  # intent filled by caller
        return out
    except Exception as e:
        logger.debug("lexical scores failed: %s", e)
        return []

@dataclass
class UnifiedSignals:
    primary_intent: str = "uncertain"
    intent_confidence: float = 0.0  # 0-1
    purchase_intent: float = 0.0
    content_interest: float = 0.0
    price_interest: float = 0.0
    explicit_purchase: bool = False
    explicit_price: bool = False
    asks_question: bool = False
    lexical_evidence: list[dict[str, Any]] = field(default_factory=list)
    semantic_evidence: list[dict[str, Any]] = field(default_factory=list)
    top_intent: str = "uncertain"
    top_score: float = 0.0
    second_score: float = 0.0
    margin: float = 0.0
    uncertainty: bool = True
    total_latency_ms: float = 0.0
    lexical_latency_ms: float = 0.0
    semantic_latency_ms: float = 0.0
    similarity_latency_ms: float = 0.0

# Brute-force cosine (no HNSW, <1k vectors)
def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x*y for x,y in zip(a,b))
    # Assume normalized (L2 1.0), so cosine = dot
    # But compute norm just in case
    return dot  # normalized

# Cache reference embeddings (global, per-process, loaded once)
_reference_texts: list[str] | None = None
_reference_intents: list[str] | None = None
_reference_vectors: list[list[float]] | None = None
_reference_loaded: bool = False

def _ensure_reference_cache() -> bool:
    global _reference_texts, _reference_intents, _reference_vectors, _reference_loaded
    if _reference_loaded:
        return _reference_vectors is not None
    try:
        from commerce.intent_corpus import INTENT_CORPUS
        texts = [e["example_text"] for e in INTENT_CORPUS]
        intents = [e["intent"] for e in INTENT_CORPUS]
        # Try to load cached vectors if model available, else keep texts for lexical only
        from commerce.embedding_model import get_model, encode_messages_sync
        model = get_model()
        if model is not None:
            vecs = encode_messages_sync(texts)
            if vecs:
                _reference_texts = texts
                _reference_intents = intents
                _reference_vectors = vecs
                _reference_loaded = True
                logger.info("Unified intelligence reference cache loaded: %d vectors", len(vecs))
                return True
        # Fallback: lexical only
        _reference_texts = texts
        _reference_intents = intents
        _reference_vectors = None
        _reference_loaded = True
        logger.info("Unified intelligence lexical-only cache loaded: %d texts", len(texts))
        return False
    except Exception as e:
        logger.warning("Failed to load reference cache: %s", e, exc_info=True)
        return False

async def analyze_message(
    message: str,
    product_titles: list[str] | None = None,
    recent_context: str | None = None,
) -> UnifiedSignals:
    """Analyze a single incoming message deterministically.

    Returns UnifiedSignals with confidence/abstention.
    Never raises, never calls LLM, never mutates commerce authority.
    """
    start = time.monotonic()
    norm = normalize_message(message)
    # Quick deterministic checks before ML
    asks_q = message.strip().endswith("?") or bool(re.search(r"\b(what|how|when|where|who|why|can you|could you)\b", norm))
    explicit_purchase = bool(re.search(r"\bi want to buy\b", norm))
    explicit_price = bool(re.search(r"\$|price|cost", norm))

    # Ensure cache
    t0 = time.monotonic()
    has_semantic = _ensure_reference_cache()
    t_cache = (time.monotonic() - t0) * 1000

    # Lexical
    t_lex_start = time.monotonic()
    lexical_evidence: list[dict] = []
    if _reference_texts:
        try:
            from rapidfuzz import fuzz, process
            # Quick lexical top 3
            res = process.extract(
                norm,
                [normalize_message(t) for t in _reference_texts],
                scorer=fuzz.WRatio,
                score_cutoff=LEXICAL_CUTOFF,
                limit=3,
            )
            for matched, score, idx in res:
                lexical_evidence.append({"text": _reference_texts[idx], "intent": _reference_intents[idx] if _reference_intents else "unknown", "score": float(score), "source": "lexical"})
        except Exception:
            pass
    lexical_ms = (time.monotonic() - t_lex_start) * 1000

    # Semantic
    t_sem_start = time.monotonic()
    semantic_evidence: list[dict] = []
    top_intent = "uncertain"
    top_score = 0.0
    second_score = 0.0
    if has_semantic and _reference_vectors is not None:
        try:
            from commerce.embedding_model import encode_message
            vec = await encode_message(message)
            if vec is not None:
                # Brute-force cosine
                t_sim_start = time.monotonic()
                scores: list[tuple[float, int]] = []
                for idx, ref_vec in enumerate(_reference_vectors):
                    s = _cosine(vec, ref_vec)
                    scores.append((s, idx))
                scores.sort(key=lambda x: x[0], reverse=True)
                if scores:
                    top_score, top_idx = scores[0]
                    top_intent = _reference_intents[top_idx] if _reference_intents else "uncertain"
                    if len(scores) > 1:
                        second_score = scores[1][0]
                    # Top 3 semantic evidence
                    for s, idx in scores[:3]:
                        semantic_evidence.append({"text": _reference_texts[idx], "intent": _reference_intents[idx], "score": float(s), "source": "semantic"})
                similarity_ms = (time.monotonic() - t_sim_start) * 1000
            else:
                similarity_ms = 0.0
        except Exception as e:
            logger.debug("semantic failed: %s", e)
            similarity_ms = 0.0
    else:
        similarity_ms = 0.0
    semantic_ms = (time.monotonic() - t_sem_start) * 1000

    # Confidence: max(lexical/100, semantic) + margin
    lex_max = max((e["score"]/100.0 for e in lexical_evidence), default=0.0)
    sem_max = top_score
    # Semantic is cosine 0-1, lexical 0-100 -> normalize
    combined = max(lex_max, sem_max)
    margin = top_score - second_score if top_score and second_score else top_score
    # Confidence consider absolute + margin + lexical
    confidence = combined
    if margin < MARGIN_THRESHOLD:
        confidence *= 0.7  # low margin -> lower confidence
    if not lexical_evidence and not semantic_evidence:
        confidence = 0.0

    # Abstention
    uncertainty = False
    primary_intent = top_intent if top_score >= SEMANTIC_THRESHOLD and confidence >= 0.5 else "uncertain"
    if confidence < 0.4 or (top_score < SEMANTIC_THRESHOLD and lex_max < 0.8):
        primary_intent = "uncertain"
        uncertainty = True
        # Conservative: uncertain -> no purchase
        purchase_intent = 0.0
    else:
        # Map primary_intent to purchase_intent etc. (simple)
        if primary_intent in ("purchase_intent", "repeat_purchase_intent"):
            purchase_intent = combined
        elif primary_intent in ("price_inquiry", "negotiation"):
            purchase_intent = combined * 0.5
        else:
            purchase_intent = 0.0

    # Content interest via product titles semantic? Use lexical/semantic already
    content_interest = 0.0
    if primary_intent in ("content_curiosity", "content_request", "custom_request"):
        content_interest = combined
    price_interest = 1.0 if explicit_price else 0.0

    total_ms = (time.monotonic() - start) * 1000
    return UnifiedSignals(
        primary_intent=primary_intent,
        intent_confidence=confidence,
        purchase_intent=purchase_intent,
        content_interest=content_interest,
        price_interest=price_interest,
        explicit_purchase=explicit_purchase,
        explicit_price=explicit_price,
        asks_question=asks_q,
        lexical_evidence=lexical_evidence,
        semantic_evidence=semantic_evidence,
        top_intent=top_intent,
        top_score=top_score,
        second_score=second_score,
        margin=margin,
        uncertainty=uncertainty,
        total_latency_ms=total_ms,
        lexical_latency_ms=lexical_ms,
        semantic_latency_ms=semantic_ms,
        similarity_latency_ms=similarity_ms if 'similarity_ms' in locals() else 0.0,
    )

def map_to_commerce_signals(unified: UnifiedSignals) -> dict[str, Any]:
    """Map UnifiedSignals to CommerceSignals fields (deterministic, conservative)."""
    # Preserve existing CommerceSignals semantics: purchase_intent 0-1, confidence 0-1, etc.
    # Uncertain -> low_information (0.0, uncertain, confidence 0)
    if unified.uncertainty or unified.primary_intent == "uncertain":
        return {
            "purchase_intent": 0.0,
            "content_interest": 0.0,
            "relationship_engagement": 0.0,
            "price_interest": unified.price_interest,
            "explicit_purchase_request": unified.explicit_purchase,
            "explicit_content_request": False,
            "requested_price": None,
            "declined_recent_offer": False,
            "negative_sentiment": 0.0,
            "confidence": 0.0,
            "evidence": [],
            "model_uncertainty": 1.0,
            "primary_intent": "uncertain",
            "intent_tags": [],
            "negative_intent_tags": [],
            "fan_asks_question": unified.asks_question,
            "accepted_recent_offer": False,
            "asks_for_free_content": False,
            "conversation_relevance": 0.5,
            "topic_continuity": None,
        }
    return {
        "purchase_intent": unified.purchase_intent,
        "content_interest": unified.content_interest,
        "relationship_engagement": 0.0,
        "price_interest": unified.price_interest,
        "explicit_purchase_request": unified.explicit_purchase,
        "explicit_content_request": unified.primary_intent == "content_request",
        "requested_price": None,
        "declined_recent_offer": False,
        "negative_sentiment": 0.0,
        "confidence": unified.intent_confidence,
        "evidence": [e["text"][:80] for e in (unified.lexical_evidence[:2] + unified.semantic_evidence[:2])],
        "model_uncertainty": 1.0 - unified.intent_confidence,
        "primary_intent": unified.primary_intent,
        "intent_tags": [unified.primary_intent],
        "negative_intent_tags": [],
        "fan_asks_question": unified.asks_question,
        "accepted_recent_offer": False,
        "asks_for_free_content": False,
        "conversation_relevance": 0.8,
        "topic_continuity": None,
    }
