"""Context Deduplicator (Phase 70).

Deterministic context deduplication that prevents multiple representations
of essentially identical information from consuming the context budget.

Deduplication must:
- preserve the strongest/most authoritative representation
- not collapse genuinely distinct events
- not use aggressive semantic deduplication that can erase meaningful state
- be deterministic
- respect creator isolation
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from context_engine.models import AuthorityLevel, ContextCategory, ContextItem

logger = logging.getLogger("context_engine.dedup")


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for deduplication comparison.

    Applies conservative normalization:
    - lowercase
    - collapse whitespace
    - strip punctuation
    - strip common filler words
    """
    text = text.lower().strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove common punctuation
    text = re.sub(r"[^\w\s]", "", text)
    return text


def _compute_content_hash(text: str) -> str:
    """Compute deterministic content hash for deduplication."""
    normalized = _normalize_for_dedup(text)
    import hashlib
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _are_lexically_similar(text1: str, text2: str, threshold: float = 0.85) -> bool:
    """Check if two texts are lexically similar.

    Uses character-level Jaccard similarity for deterministic comparison.
    """
    if not text1 or not text2:
        return False

    # Use RapidFuzz if available for better accuracy
    try:
        from rapidfuzz import fuzz
        score = fuzz.WRatio(text1, text2) / 100.0
        return score >= threshold
    except ImportError:
        pass

    # Fallback to character-level Jaccard
    set1 = set(text1.lower().split())
    set2 = set(text2.lower().split())

    if not set1 or not set2:
        return False

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return (intersection / union) >= threshold if union > 0 else False


@dataclass(frozen=True)
class DeduplicationResult:
    """Result of deduplication operation."""

    selected: tuple[ContextItem, ...]
    removed_count: int
    removed_ids: tuple[str, ...]


@dataclass
class ContextDeduplicator:
    """Deterministic context deduplication.

    Prevents multiple representations of essentially identical information
    from consuming the context budget.
    """

    similarity_threshold: float = 0.85
    _content_hashes: dict[str, ContextItem] = field(
        default_factory=dict, init=False, repr=False
    )
    _lexical_hashes: dict[str, list[ContextItem]] = field(
        default_factory=dict, init=False, repr=False
    )

    def reset(self) -> None:
        """Reset deduplication state. Call before a new assembly."""
        self._content_hashes.clear()
        self._lexical_hashes.clear()

    def _get_dedup_key(self, item: ContextItem) -> str:
        """Get deduplication key for an item.

        Uses content hash for exact duplicates, category + source for
        lexical similarity.
        """
        return _compute_content_hash(item.content)

    def _should_keep(self, existing: ContextItem, candidate: ContextItem) -> bool:
        """Determine which item to keep when duplicates are found.

        Rules:
        1. Higher authority wins
        2. Higher priority wins
        3. More recent wins
        4. Longer content wins (more information)
        """
        # Higher authority wins
        if candidate.authority < existing.authority:
            return True  # Keep candidate (higher authority)
        if candidate.authority > existing.authority:
            return False  # Keep existing (higher authority)

        # Higher priority wins
        if candidate.priority > existing.priority:
            return True
        if candidate.priority < existing.priority:
            return False

        # More recent wins
        if candidate.timestamp and existing.timestamp:
            if candidate.timestamp > existing.timestamp:
                return True
            if candidate.timestamp < existing.timestamp:
                return False

        # Longer content wins (more information)
        if len(candidate.content) > len(existing.content):
            return True

        return False

    def deduplicate(
        self,
        items: list[ContextItem],
        respect_creator_isolation: bool = True,
    ) -> DeduplicationResult:
        """Deduplicate context items.

        Args:
            items: Items to deduplicate (should be pre-sorted by score)
            respect_creator_isolation: If True, items from different creators
                are never considered duplicates

        Returns:
            DeduplicationResult with selected items and statistics
        """
        self.reset()

        selected: list[ContextItem] = []
        removed_ids: list[str] = []

        for item in items:
            # Generate dedup key
            dedup_key = self._get_dedup_key(item)

            # Check for exact duplicate
            if dedup_key in self._content_hashes:
                existing = self._content_hashes[dedup_key]

                # Creator isolation: skip if different creator
                if (
                    respect_creator_isolation
                    and item.creator_id is not None
                    and existing.creator_id is not None
                    and item.creator_id != existing.creator_id
                ):
                    # Different creators — treat as distinct
                    selected.append(item)
                    self._content_hashes[dedup_key + f"_c{item.creator_id}"] = item
                    continue

                # Keep the better item
                if self._should_keep(existing, item):
                    # Remove existing, add candidate
                    selected = [x for x in selected if x.item_id != existing.item_id]
                    selected.append(item)
                    self._content_hashes[dedup_key] = item
                    removed_ids.append(existing.item_id)
                else:
                    # Keep existing, skip candidate
                    removed_ids.append(item.item_id)
                continue

            # Check for lexical similarity
            lexical_key = f"{item.category.value}:{item.source}"
            if lexical_key in self._lexical_hashes:
                for existing in self._lexical_hashes[lexical_key]:
                    # Creator isolation check
                    if (
                        respect_creator_isolation
                        and item.creator_id is not None
                        and existing.creator_id is not None
                        and item.creator_id != existing.creator_id
                    ):
                        continue

                    if _are_lexically_similar(
                        item.content, existing.content, self.similarity_threshold
                    ):
                        # Keep the better item
                        if self._should_keep(existing, item):
                            selected = [
                                x for x in selected if x.item_id != existing.item_id
                            ]
                            selected.append(item)
                            # Update lexical hash
                            self._lexical_hashes[lexical_key] = [
                                x
                                for x in self._lexical_hashes[lexical_key]
                                if x.item_id != existing.item_id
                            ]
                            self._lexical_hashes[lexical_key].append(item)
                            removed_ids.append(existing.item_id)
                        else:
                            removed_ids.append(item.item_id)
                        break
                else:
                    # No lexical match found
                    selected.append(item)
                    self._content_hashes[dedup_key] = item
                    if lexical_key not in self._lexical_hashes:
                        self._lexical_hashes[lexical_key] = []
                    self._lexical_hashes[lexical_key].append(item)
            else:
                # No previous items in this category/source
                selected.append(item)
                self._content_hashes[dedup_key] = item
                self._lexical_hashes[lexical_key] = [item]

        return DeduplicationResult(
            selected=tuple(selected),
            removed_count=len(removed_ids),
            removed_ids=tuple(removed_ids),
        )
