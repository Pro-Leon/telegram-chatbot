"""Context Scorer (Phase 70).

Relevance scoring for context items based on:
- topical relevance
- recency
- importance
- state relevance
- authority
- source priority

All scoring is deterministic. Same inputs → same scores.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from context_engine.models import (
    AuthorityLevel,
    ContextCategory,
    ContextItem,
    ContentTrust,
    RetrievalScore,
)

logger = logging.getLogger("context_engine.scorer")


# Scoring weights from Phase 69 specification
SCORING_WEIGHTS = {
    "source": 0.15,
    "topic": 0.30,
    "recency": 0.20,
    "importance": 0.20,
    "state_relevance": 0.10,
    "authority": 0.05,
}

# Source reliability scores (deterministic, documented)
SOURCE_SCORES = {
    "postgres": 1.0,
    "redis": 0.85,
    "in_memory": 0.7,
    "derived": 0.9,
    "llm_derived": 0.5,
    "user_input": 0.3,
}

# Authority level scores (higher authority = higher score)
AUTHORITY_SCORES = {
    AuthorityLevel.HARD_POLICY: 1.0,
    AuthorityLevel.DETERMINISTIC_RULE: 0.9,
    AuthorityLevel.DETERMINISTIC_DERIVATION: 0.8,
    AuthorityLevel.CONTEXT_ASSEMBLY: 0.6,
    AuthorityLevel.LLM_GENERATION: 0.4,
    AuthorityLevel.POST_GENERATION: 0.5,
}

# Category base priorities (higher = more important)
CATEGORY_PRIORITIES = {
    ContextCategory.SYSTEM: 10,
    ContextCategory.STATE: 9,
    ContextCategory.COMMERCE: 8,
    ContextCategory.MEMORY: 7,
    ContextCategory.KNOWLEDGE: 6,
    ContextCategory.TEMPORAL: 5,
    ContextCategory.CONTENT: 4,
    ContextCategory.CONVERSATION: 3,
    ContextCategory.EMBEDDED: 2,
}


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for scoring weights and parameters."""

    weights: dict[str, float] = field(default_factory=lambda: dict(SCORING_WEIGHTS))
    source_scores: dict[str, float] = field(default_factory=lambda: dict(SOURCE_SCORES))
    authority_scores: dict[AuthorityLevel, float] = field(
        default_factory=lambda: dict(AUTHORITY_SCORES)
    )
    recency_decay_hours: float = 168.0  # 1 week half-life

    def __post_init__(self) -> None:
        """Validate weights sum to 1.0."""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Scoring weights must sum to 1.0, got {total}")


def compute_topic_overlap(content: str, query: str) -> float:
    """Compute topic overlap between content and query.

    Uses simple word overlap for deterministic, fast scoring.
    Returns 0.0-1.0.
    """
    if not content or not query:
        return 0.0

    content_words = set(content.lower().split())
    query_words = set(query.lower().split())

    if not query_words:
        return 0.0

    overlap = content_words & query_words
    return len(overlap) / len(query_words)


def compute_recency_score(
    timestamp: float | None,
    decay_hours: float = 168.0,
) -> float:
    """Compute recency score with exponential decay.

    Returns 0.0-1.0 where 1.0 is most recent.
    """
    if timestamp is None:
        return 0.5  # Neutral score for items without timestamp

    import time
    hours_ago = max(0.0, (time.time() - timestamp) / 3600.0)

    # Exponential decay: score = e^(-hours / decay_hours)
    return math.exp(-hours_ago / decay_hours)


def compute_importance_score(
    category: ContextCategory,
    priority: int,
    metadata: dict[str, Any] | None = None,
) -> float:
    """Compute importance score based on category and priority.

    Returns 0.0-1.0.
    """
    # Base from category (0.0-0.7)
    cat_priority = CATEGORY_PRIORITIES.get(category, 5)
    cat_score = cat_priority / 10.0 * 0.7

    # Priority boost (0.0-0.3)
    priority_score = min(1.0, priority / 10.0) * 0.3

    return min(1.0, cat_score + priority_score)


def compute_state_relevance(
    item: ContextItem,
    conversation_state: dict[str, Any] | None = None,
) -> float:
    """Compute state-dependent relevance.

    Certain context becomes more relevant depending on current state:
    - repeat purchase context
    - post purchase context
    - aftercare context
    - hesitation context
    - negotiation context

    Returns 0.0-1.0.
    """
    if conversation_state is None:
        return 0.5  # Neutral when no state

    relevance = 0.5  # Base

    # State-sensitive categories get boost
    current_topic = conversation_state.get("current_topic")
    if current_topic and current_topic.lower() in item.content.lower():
        relevance += 0.2

    # Relationship state relevance
    relationship = conversation_state.get("relationship_state")
    if relationship:
        if item.category == ContextCategory.COMMERCE and relationship in (
            "buying_signal", "purchased", "repeat_buyer"
        ):
            relevance += 0.15
        elif item.category == ContextCategory.MEMORY and relationship in (
            "warm", "engaged"
        ):
            relevance += 0.1

    return min(1.0, relevance)


@dataclass
class ContextScorer:
    """Scores context items for relevance and importance.

    All scoring is deterministic and documented.
    """

    config: ScoringConfig = field(default_factory=ScoringConfig)

    def score_item(
        self,
        item: ContextItem,
        query: str = "",
        conversation_state: dict[str, Any] | None = None,
    ) -> RetrievalScore:
        """Compute retrieval score for a context item.

        Returns deterministic RetrievalScore.
        """
        # Source score
        source_name = item.source.split(".")[0] if item.source else "unknown"
        source_score = self.config.source_scores.get(source_name, 0.5)

        # Topic overlap
        topic_overlap = compute_topic_overlap(item.content, query)

        # Recency
        recency_score = compute_recency_score(
            item.timestamp, self.config.recency_decay_hours
        )

        # Importance
        importance_score = compute_importance_score(
            item.category, item.priority, item.metadata
        )

        # State relevance
        state_relevance = compute_state_relevance(item, conversation_state)

        # Authority score
        authority_score = self.config.authority_scores.get(item.authority, 0.5)

        # Weighted combination
        final_score = (
            self.config.weights["source"] * source_score
            + self.config.weights["topic"] * topic_overlap
            + self.config.weights["recency"] * recency_score
            + self.config.weights["importance"] * importance_score
            + self.config.weights["state_relevance"] * state_relevance
            + self.config.weights["authority"] * authority_score
        )

        return RetrievalScore(
            source_score=source_score,
            topic_overlap=topic_overlap,
            recency_score=recency_score,
            importance_score=importance_score,
            state_relevance=state_relevance,
            authority_score=authority_score,
            final_score=min(1.0, final_score),
        )

    def score_items(
        self,
        items: list[ContextItem],
        query: str = "",
        conversation_state: dict[str, Any] | None = None,
    ) -> list[ContextItem]:
        """Score all items and return them sorted by final_score (descending).

        Items with higher scores are more relevant.
        """
        scored = []
        for item in items:
            score = self.score_item(item, query, conversation_state)
            # Create new item with updated score
            scored_item = ContextItem(
                item_id=item.item_id,
                category=item.category,
                content=item.content,
                authority=item.authority,
                trust=item.trust,
                token_cost=item.token_cost,
                retrieval_score=score,
                source=item.source,
                priority=item.priority,
                timestamp=item.timestamp,
                creator_id=item.creator_id,
                user_id=item.user_id,
                metadata=item.metadata,
            )
            scored.append(scored_item)

        # Sort by final_score descending, then by priority for tie-breaking
        scored.sort(
            key=lambda x: (x.retrieval_score.final_score, x.priority),
            reverse=True,
        )

        return scored
