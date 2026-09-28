"""Deterministic memory extraction (Phase 3).

Caller: FUTURE conversation engine per inbound turn (Phase 4).
Producer of: MemoryCandidate rows (validated in memory_validation before
persistence). No direct DB writes here — pure functions.

Model-assisted candidates are FUTURE (Phase 7). This module is the
deterministic baseline: explicit statements extract with high confidence;
weak inferences stay below the promotion gate and never become hard facts.
"""

from __future__ import annotations

import logging
import re
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.memory_extraction")

EXTRACTOR_VERSION = "v2-deterministic-3.0"

# Confidence bands (sunny_upgrade_v2 §41): <0.60 never promoted to durable fact.
CONFIDENCE_EXPLICIT_SINGLE = 0.85
CONFIDENCE_EXPLICIT_CONFIRMED = 0.96
CONFIDENCE_INFERENCE = 0.55
PROMOTION_GATE = 0.60

# Creator identity must come from system config, never conversation text.
CREATOR_IDENTITY_KEYS = frozenset(
    {
        "creator_name",
        "creator_identity",
        "creator_biography",
        "creator_account",
        "creator_content",
    }
)

# Commerce truth must come from confirmations, never extracted prose.
COMMERCE_KEYS = frozenset(
    {
        "price",
        "product",
        "purchase_status",
        "ownership",
        "offer_eligibility",
        "transaction",
    }
)

MAX_VALUE_CHARS = 280


class CandidateKind(str, Enum):
    FACT = "fact"
    EPISODE = "episode"
    SIGNAL = "signal"


class MemoryCandidate(BaseModel):
    """One extraction proposal. Advisory until validated + persisted."""

    kind: CandidateKind
    category: str = Field(min_length=1, max_length=64)
    memory_key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=1024)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: str = Field(default="normal")
    explicitly_stated: bool = False
    extractor_version: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}


# Explicit-statement patterns: "I am X", "I work as X", "my Y is Z", ...
_EXPLICIT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\bi\s+work\s+as\s+(?:an?\s+)?([^.,!?;]{2,60})", "occupation", "job"),
    (r"\bi['’]m\s+(?:an?\s+)?([^.,!?;]{2,60})", "identity", "self_description"),
    (
        r"\bmy\s+(?:daughter|son|wife|husband|partner|mom|dad|mother|father)\b([^.,!?;]{0,80})",
        "family",
        "family",
    ),
    (r"\bmy\s+(dog|cat|pet)\b([^.,!?;]{0,80})", "family", "pets"),
    (r"\bi\s+like\s+([^.,!?;]{2,60})", "preferences", "likes"),
    (r"\bi\s+love\s+([^.,!?;]{2,60})", "preferences", "loves"),
    (r"\bi\s+(?:hate|dislike)\s+([^.,!?;]{2,60})", "preferences", "dislikes"),
    (r"\bmy\s+birthday\s+is\s+([^.,!?;]{2,40})", "important_dates", "birthday"),
    (
        r"\b(?:i\s+have|there\s+is)\s+(?:a\s+)?(job interview|soccer game|presentation|vacation|trip|appointment|deadline|exam)\b([^.,!?;]{0,80})",
        "life_event",
        "upcoming_event",
    ),
    (
        r"\b(?:has|have)\s+(?:a\s+)?(job interview|soccer game|presentation|vacation|trip|appointment|deadline|exam)\b([^.,!?;]{0,80})",
        "life_event",
        "upcoming_event",
    ),
    (
        r"\b(?:don['’]t|do\s+not)\s+(?:ask\s+me\s+about|talk\s+about)\s+([^.,!?;]{2,60})",
        "conversational_preferences",
        "boundary",
    ),
    (
        r"\bplease\s+(?:don['’]t|remember\s+to)\s+([^.,!?;]{2,80})",
        "commitment",
        "promise_or_request",
    ),
)

_TOPIC_SIGNAL_WORDS: tuple[tuple[str, str], ...] = (
    ("football", "topic_affinity"),
    ("soccer", "topic_affinity"),
    ("work", "topic_affinity"),
    ("music", "topic_affinity"),
    ("teas", "style_affinity"),
)


def _clean(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip().strip("\"'")).strip()
    return cleaned[:MAX_VALUE_CHARS]


def _is_intimate_explicit(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in ("horny", "sexy", "naked", "nude", "kiss", "flirt", "tease"))


def extract_candidates(
    text: str,
    source_event_id: str,
    provenance: str,
    extractor_version: str = EXTRACTOR_VERSION,
) -> list[MemoryCandidate]:
    """Deterministic extraction over one inbound turn. Never raises on prose."""
    out: list[MemoryCandidate] = []
    if not text or not text.strip() or not source_event_id or not provenance:
        return out
    try:
        lowered = text.lower()
        for pattern, category, key in _EXPLICIT_PATTERNS:
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if not m:
                continue
            raw = _clean(m.group(1) if m.lastindex else m.group(0))
            if not raw:
                continue
            # Intimate history stores abstract signals, never verbatim explicit content.
            if _is_intimate_explicit(text) and category not in (
                "conversational_preferences",
                "commitment",
            ):
                out.append(
                    MemoryCandidate(
                        kind=CandidateKind.SIGNAL,
                        category="intimate_signal",
                        memory_key="intimate_comfort_abstract",
                        value="responsive to playful tone (abstract; no verbatim stored)",
                        confidence=CONFIDENCE_INFERENCE,
                        importance="normal",
                        explicitly_stated=False,
                        extractor_version=extractor_version,
                        source_event_id=source_event_id,
                        provenance=provenance,
                    )
                )
                continue
            out.append(
                MemoryCandidate(
                    kind=CandidateKind.FACT,
                    category=category,
                    memory_key=key,
                    value=raw,
                    confidence=CONFIDENCE_EXPLICIT_SINGLE,
                    importance="high"
                    if category in ("occupation", "family", "important_dates", "life_event")
                    else "normal",
                    explicitly_stated=True,
                    extractor_version=extractor_version,
                    source_event_id=source_event_id,
                    provenance=provenance,
                )
            )
        # Episodic event detection: one compact episode per turn with substance.
        if len(text.strip()) >= 12:
            out.append(
                MemoryCandidate(
                    kind=CandidateKind.EPISODE,
                    category="turn",
                    memory_key="turn_episode",
                    value=_clean(text)[:200],
                    confidence=CONFIDENCE_INFERENCE,
                    importance="low",
                    explicitly_stated=True,
                    extractor_version=extractor_version,
                    source_event_id=source_event_id,
                    provenance=provenance,
                )
            )
        # Engagement signal detection (weak, needs recurrence before promotion).
        for word, behavior in _TOPIC_SIGNAL_WORDS:
            if word in lowered:
                out.append(
                    MemoryCandidate(
                        kind=CandidateKind.SIGNAL,
                        category="engagement",
                        memory_key=f"topic_{word}",
                        value=f"mentioned {word}",
                        confidence=CONFIDENCE_INFERENCE,
                        importance="low",
                        explicitly_stated=True,
                        extractor_version=extractor_version,
                        source_event_id=source_event_id,
                        provenance=provenance,
                    )
                )
    except Exception:
        logger.exception(
            "extraction failed (fail-open candidates empty)", extra={"provenance": provenance}
        )
        return []
    return out


def is_creator_identity_claim(candidate: MemoryCandidate) -> bool:
    return candidate.memory_key in CREATOR_IDENTITY_KEYS or candidate.category.startswith("creator")


def is_commerce_claim(candidate: MemoryCandidate) -> bool:
    return candidate.memory_key in COMMERCE_KEYS or candidate.category.startswith("commerce")
