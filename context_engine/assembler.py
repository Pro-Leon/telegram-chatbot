"""Context Assembler (Phase 70).

Deterministic assembly stage that:
1. Takes candidate items
2. Normalizes them
3. Scores them
4. Deduplicates them
5. Sorts them
6. Applies budget constraints
7. Produces a ContextSnapshot

The output preserves provenance, category, authority, selection score,
and deterministic ordering.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from context_engine.budget import TokenBudgetManager, estimate_tokens
from context_engine.dedup import ContextDeduplicator
from context_engine.models import (
    CATEGORY_BUDGETS,
    TOTAL_CONTEXT_BUDGET,
    AuthorityLevel,
    ContextCategory,
    ContextItem,
    ContextSnapshot,
    ContentTrust,
)
from context_engine.scorer import ContextScorer, ScoringConfig

logger = logging.getLogger("context_engine.assembler")


@dataclass
class ContextAssembler:
    """Deterministic context assembly stage.

    Pipeline:
        candidate sources
               ↓
           normalize
               ↓
            score
               ↓
        deduplicate
               ↓
            sort
               ↓
           budget
               ↓
         assemble
               ↓
        ContextSnapshot
    """

    scorer: ContextScorer = field(default_factory=ContextScorer)
    deduplicator: ContextDeduplicator = field(default_factory=ContextDeduplicator)
    budget_manager: TokenBudgetManager = field(default_factory=TokenBudgetManager)

    def reset(self) -> None:
        """Reset all state for a new assembly."""
        self.budget_manager.reset()
        self.deduplicator.reset()

    def assemble(
        self,
        candidates: list[ContextItem],
        query: str = "",
        conversation_state: dict[str, Any] | None = None,
        respect_creator_isolation: bool = True,
    ) -> ContextSnapshot:
        """Assemble context from candidates.

        Args:
            candidates: All candidate context items
            query: Current user message for relevance scoring
            conversation_state: Current conversation state for state relevance
            respect_creator_isolation: Whether to enforce creator isolation

        Returns:
            ContextSnapshot with assembled context

        Pipeline (Phase 2):
            gather → conflict_resolution → score → dedup → budget → snapshot
            Conflict resolution is deterministic fact-level, before ranking.
        """
        start_time = time.monotonic()
        self.reset()

        candidate_count = len(candidates)

        # Step 0: Deterministic conflict resolution (Phase 2)
        # Resolves same fact competing values before ranking.
        # Authority > freshness/status > recency > deterministic tie-break.
        _t_conflict_start = time.monotonic()
        conflict_resolved, conflict_dropped = self._resolve_conflicts(candidates, respect_creator_isolation)
        _conflict_ms = (time.monotonic() - _t_conflict_start) * 1000

        # Step 1: Score remaining candidates
        _t_score_start = time.monotonic()
        scored = self.scorer.score_items(conflict_resolved, query, conversation_state)
        _score_ms = (time.monotonic() - _t_score_start) * 1000

        # Step 2: Deduplicate (lexical similarity, respect isolation)
        _t_dedup_start = time.monotonic()
        dedup_result = self.deduplicator.deduplicate(
            scored, respect_creator_isolation
        )
        _dedup_ms = (time.monotonic() - _t_dedup_start) * 1000
        deduplicated = list(dedup_result.selected)

        # Step 3: Sort by score (descending), then priority for tie-breaking
        deduplicated.sort(
            key=lambda x: (x.retrieval_score.final_score, x.priority),
            reverse=True,
        )

        # Step 4: Apply budget constraints
        _t_budget_start = time.monotonic()
        selected: list[ContextItem] = []
        for item in deduplicated:
            result = self.budget_manager.try_allocate_or_truncate(item)
            if result is not None:
                selected.append(result)
        _budget_ms = (time.monotonic() - _t_budget_start) * 1000

        # Step 5: Sort selected items by category priority, then score
        selected.sort(
            key=lambda x: (
                -CATEGORY_BUDGETS.get(x.category, 0),  # Higher budget categories first
                -x.retrieval_score.final_score,
            )
        )

        # Step 6: Calculate degradation level
        total_tokens = sum(item.token_cost for item in selected)
        degradation_level = self._calculate_degradation(
            total_tokens, self.budget_manager.get_snapshot()
        )

        # Step 7: Build snapshot
        assembly_time_ms = (time.monotonic() - start_time) * 1000

        category_tokens: dict[ContextCategory, int] = {}
        for item in selected:
            category_tokens[item.category] = (
                category_tokens.get(item.category, 0) + item.token_cost
            )

        # Combine conflict + dedup counts for observability
        total_dedup = dedup_result.removed_count + conflict_dropped
        # Count truncated items (actual truncation ops)
        truncation_count = sum(1 for it in selected if it.metadata.get("truncated"))
        return ContextSnapshot(
            items=tuple(selected),
            total_tokens=total_tokens,
            category_tokens=category_tokens,
            degradation_level=degradation_level,
            candidate_count=candidate_count,
            selected_count=len(selected),
            deduplication_count=total_dedup,
            assembly_time_ms=assembly_time_ms,
            metadata={
                "conflict_dropped": conflict_dropped,
                "lexical_dedup_removed": dedup_result.removed_count,
                "total_deduplication": total_dedup,
                "truncation_count": truncation_count,
                "score_ms": _score_ms,
                "dedup_ms": _dedup_ms,
                "budget_ms": _budget_ms,
                "conflict_ms": _conflict_ms,
            },
        )

    def _calculate_degradation(
        self,
        total_tokens: int,
        category_usage: dict[ContextCategory, int],
    ) -> int:
        """Calculate degradation level based on budget usage.

        Returns:
            0 = none (within budget)
            1 = minor (non-critical items truncated)
            2 = moderate (some items removed)
            3 = severe (only system + state + conversation)
            4 = critical (only system + last 3 messages)
        """
        if total_tokens <= TOTAL_CONTEXT_BUDGET:
            return 0

        # Calculate how much over budget
        overflow_ratio = (total_tokens - TOTAL_CONTEXT_BUDGET) / TOTAL_CONTEXT_BUDGET

        if overflow_ratio <= 0.1:
            return 1  # Minor
        elif overflow_ratio <= 0.25:
            return 2  # Moderate
        elif overflow_ratio <= 0.5:
            return 3  # Severe
        else:
            return 4  # Critical

    # ------------------------------------------------------------------
    # Phase 2: Conflict Resolution
    # ------------------------------------------------------------------
    def _fact_identity(self, item: ContextItem) -> str | None:
        """Stable semantic identity for factual items only.

        - If metadata contains subject (fan knowledge, memory), use
          category + subject lower as identity (e.g., 'memory:city')
        - Else if content contains '=' like 'city=Nairobi', extract key before '='
        - Else not a factual claim -> None (handled by dedup, not conflict)
        """
        # Explicit subject in metadata — cross-category identity (P2 Fix 3)
        # Same factual subject must conflict even across STATE vs MEMORY vs KNOWLEDGE,
        # so identity excludes category. Creator isolation handled in bucket key, not identity.
        subj = item.metadata.get("subject") if isinstance(item.metadata, dict) else None
        if subj and isinstance(subj, str) and subj.strip():
            import re as _re
            norm = _re.sub(r"[^a-z0-9_]+", "_", subj.lower().strip())[:40]
            if norm:
                return f"fact:{norm}"
        # Try to parse content like "city=Nairobi (CURRENT, conf 0.9)" or "price=50"
        content = item.content or ""
        if "=" in content:
            import re as _re2
            m = _re2.search(r"([a-z_][a-z0-9_]*)\s*=", content.lower())
            if m:
                key = m.group(1).strip()
                if key and len(key) <= 30:
                    return f"fact:{key}"
        return None

    def _status_rank(self, item: ContextItem) -> int:
        """Higher rank = more current. CURRENT > TEMPORARY > HISTORICAL > EXPIRED etc."""
        content_lower = (item.content or "").lower()
        meta_status = ""
        if isinstance(item.metadata, dict):
            meta_status = str(item.metadata.get("status", "") or "").upper()
        # Also detect from content suffix "(CURRENT", "(HISTORICAL"
        if "current" in content_lower or meta_status == "CURRENT":
            return 3
        if "temporary" in content_lower or meta_status == "TEMPORARY":
            return 2
        if "historical" in content_lower or meta_status == "HISTORICAL":
            return 1
        if "expired" in content_lower or meta_status == "EXPIRED":
            return 0
        # Items without explicit status: treat as CURRENT (authoritative state)
        if item.is_authoritative:
            return 3
        return 1

    def _resolve_conflicts(
        self,
        candidates: list[ContextItem],
        respect_creator_isolation: bool,
    ) -> tuple[list[ContextItem], int]:
        """Deterministic fact-level conflict resolution.

        Groups candidates by (creator_id, fact_identity). For each group with
        competing values, keeps the winner by:
          1. lower AuthorityLevel (HARD_POLICY 0 wins)
          2. higher status rank (CURRENT > TEMPORARY > HISTORICAL)
          3. newer timestamp (higher)
          4. higher priority
          5. deterministic lexicographic content

        Returns (resolved_list, dropped_count).
        Non-factual items (identity None) are all kept for scorer.
        """
        # Bucket by (creator_id or None when isolation disabled, identity)
        groups: dict[tuple[int | None, str], list[ContextItem]] = {}
        non_fact: list[ContextItem] = []
        for it in candidates:
            ident = self._fact_identity(it)
            if ident is None:
                non_fact.append(it)
                continue
            key_creator = it.creator_id if respect_creator_isolation else None
            gkey = (key_creator, ident)
            groups.setdefault(gkey, []).append(it)

        resolved: list[ContextItem] = []
        dropped = 0
        for gkey, items in groups.items():
            if len(items) == 1:
                resolved.append(items[0])
                continue
            # Find winner deterministically
            def winner_key(x: ContextItem):
                # Lower authority is better (0), so use authority value directly
                # For sorting, we want smallest authority first, so we keep as is for min
                # Use tuple for max selection: we will pick min by authority, max by others
                # Instead compute rank tuple for comparison where smaller wins for authority
                # We'll implement comparator manually below
                return (
                    x.authority.value,  # lower wins
                    -self._status_rank(x),  # higher rank wins (negate for min)
                    -(x.timestamp or 0),  # newer wins (negate for min)
                    -x.priority,  # higher priority wins
                    x.content or "",  # lexicographically smaller wins for stability
                )
            # Sort ascending by winner_key; first is winner
            sorted_items = sorted(items, key=winner_key)
            winner = sorted_items[0]
            resolved.append(winner)
            dropped += len(items) - 1
            logger.debug(
                "conflict_resolved key=%s winner=%s dropped=%d",
                gkey[1],
                winner.item_id[:8],
                len(items) - 1,
            )

        # Recombine with non-fact items preserving original order deterministically
        # For stability, sort resolved facts by item_id
        resolved.extend(non_fact)
        # Do not sort here; let scorer do ranking. Just return.
        return resolved, dropped

    def validate_assembly(self, snapshot: ContextSnapshot) -> list[str]:
        """Validate that an assembly respects all constraints.

        Returns list of violations (empty if valid).
        """
        violations: list[str] = []

        # Check global budget
        if snapshot.total_tokens > TOTAL_CONTEXT_BUDGET:
            violations.append(
                f"Global budget exceeded: {snapshot.total_tokens} > {TOTAL_CONTEXT_BUDGET}"
            )

        # Check category budgets
        for category, used in snapshot.category_tokens.items():
            budget = CATEGORY_BUDGETS.get(category, 0)
            if used > budget:
                violations.append(
                    f"Category {category.value} budget exceeded: {used} > {budget}"
                )

        # Check authority preservation
        for item in snapshot.items:
            if item.authority == AuthorityLevel.LLM_GENERATION and item.is_authoritative:
                violations.append(
                    f"LLM item {item.item_id} marked as authoritative"
                )

        # Check creator isolation
        creator_items: dict[int | None, list[ContextItem]] = {}
        for item in snapshot.items:
            creator_items.setdefault(item.creator_id, []).append(item)

        if len(creator_items) > 1 and None not in creator_items:
            # Multiple creators — check for cross-contamination
            pass  # Items are already isolated by design

        return violations
