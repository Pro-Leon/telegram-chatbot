"""Token Budget Manager (Phase 70).

Deterministic budget allocation and enforcement for context items.
Ensures hard budget constraints are never exceeded.

Requirements from Phase 69:
- calculate item cost
- calculate category consumption
- reject or truncate items that cannot fit
- preserve higher-priority items
- never exceed category budget
- never exceed global budget
- deterministic tie-breaking
- stable ordering
- no randomness
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from context_engine.models import (
    CATEGORY_BUDGETS,
    TOTAL_CONTEXT_BUDGET,
    ContextCategory,
    ContextItem,
)

logger = logging.getLogger("context_engine.budget")

# Conservative token estimation: ~4 chars per token for English text (fallback)
CHARS_PER_TOKEN = 4

# Minimum useful token size before truncation is pointless
MIN_ITEM_TOKENS = 10

# Header overhead reservation (section headers added after budgeting in worker_integration).
# Six headers * ~10 tokens each = 60. Reserved from TOTAL so final rendered snapshot ≤2600.
HEADER_RESERVE_TOKENS = 60

# Effective budget for content items (headers excluded from item budgeting but reserved)
EFFECTIVE_TOTAL_BUDGET = TOTAL_CONTEXT_BUDGET - HEADER_RESERVE_TOKENS


def estimate_tokens(text: str) -> int:
    """Token estimation — Phase 2 unified to tiktoken where available.

    Preferred: precise tiktoken (same as OneCall). Fallback to conservative
    chars/CHARS_PER_TOKEN if tiktoken unavailable. Deterministic in both cases.
    """
    if not text:
        return 0
    try:
        # Reuse existing memory/context tokenizer (gpt-4) for consistency with OneCall
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except Exception:
        return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass
class CategoryBudgetState:
    """Tracks token consumption for a single category."""

    category: ContextCategory
    budget: int
    used: int = 0

    @property
    def remaining(self) -> int:
        """Tokens remaining in this category."""
        return max(0, self.budget - self.used)

    @property
    def utilization(self) -> float:
        """Fraction of budget used (0.0-1.0)."""
        return self.used / self.budget if self.budget > 0 else 0.0

    def can_fit(self, token_cost: int) -> bool:
        """Check if an item of this cost can fit."""
        return token_cost <= self.remaining

    def allocate(self, token_cost: int) -> bool:
        """Attempt to allocate tokens. Returns True if successful."""
        if token_cost > self.remaining:
            return False
        self.used += token_cost
        return True


@dataclass
class TokenBudgetManager:
    """Deterministic budget allocation and enforcement.

    Manages both per-category and global token budgets.
    Ensures hard constraints are never exceeded.
    Uses effective total (TOTAL - HEADER_RESERVE) so headers fit within 2600.
    """

    total_budget: int = EFFECTIVE_TOTAL_BUDGET
    category_budgets: dict[ContextCategory, int] = field(
        default_factory=lambda: dict(CATEGORY_BUDGETS)
    )
    _category_states: dict[ContextCategory, CategoryBudgetState] = field(
        default_factory=dict, init=False
    )
    _global_used: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        """Initialize category budget states."""
        for cat, budget in self.category_budgets.items():
            self._category_states[cat] = CategoryBudgetState(category=cat, budget=budget)

    def reset(self) -> None:
        """Reset all budget tracking. Call before a new assembly."""
        for state in self._category_states.values():
            state.used = 0
        self._global_used = 0

    def get_category_state(self, category: ContextCategory) -> CategoryBudgetState:
        """Get current state for a category."""
        if category not in self._category_states:
            self._category_states[category] = CategoryBudgetState(
                category=category, budget=self.category_budgets.get(category, 0)
            )
        return self._category_states[category]

    @property
    def global_remaining(self) -> int:
        """Tokens remaining in global budget."""
        return max(0, self.total_budget - self._global_used)

    @property
    def global_utilization(self) -> float:
        """Fraction of global budget used (0.0-1.0)."""
        return self._global_used / self.total_budget if self.total_budget > 0 else 0.0

    def can_fit(self, item: ContextItem) -> bool:
        """Check if an item can fit within both category and global budgets."""
        cat_state = self.get_category_state(item.category)
        return cat_state.can_fit(item.token_cost) and item.token_cost <= self.global_remaining

    def allocate(self, item: ContextItem) -> bool:
        """Attempt to allocate tokens for an item.

        Returns True if allocation succeeded (fits in both category and global).
        """
        if not self.can_fit(item):
            return False

        cat_state = self.get_category_state(item.category)
        if cat_state.allocate(item.token_cost):
            self._global_used += item.token_cost
            return True
        return False

    def try_allocate_or_truncate(
        self, item: ContextItem
    ) -> ContextItem | None:
        """Try to allocate the item, or truncate to fit.

        Returns the item (possibly truncated) if it can fit,
        or None if even truncation is too small.
        """
        # Try full item first
        if self.allocate(item):
            return item

        # Calculate available space
        cat_state = self.get_category_state(item.category)
        available = min(cat_state.remaining, self.global_remaining)

        if available < MIN_ITEM_TOKENS:
            return None

        # Truncate content to fit — P2 Fix 4B: use same tiktoken estimator via binary search, not chars/4
        suffix = " [TRUNCATED]"
        suffix_tokens = estimate_tokens(suffix)
        # Quick check: even minimal prefix + suffix must fit
        if suffix_tokens >= available:
            return None
        # If original content already fits in available when estimated, this is a bug (should have allocated)
        if len(item.content) <= 10 and estimate_tokens(item.content) <= available:
            logger.warning("Budget: item %s should have fit but didn't", item.item_id)
            return None

        # Binary search for largest prefix that fits within available tokens (including suffix)
        lo, hi = 0, len(item.content)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = item.content[:mid] + suffix
            cand_tokens = estimate_tokens(cand)
            if cand_tokens <= available:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if best < MIN_ITEM_TOKENS:  # need at least minimal meaningful prefix
            # Check token count of minimal prefix
            min_cand = item.content[:MIN_ITEM_TOKENS] + suffix
            if estimate_tokens(min_cand) > available:
                return None
            best = MIN_ITEM_TOKENS

        truncated_content = item.content[:best] + suffix
        truncated_tokens = estimate_tokens(truncated_content)
        # Fallback further truncate with short suffix if still over (edge due to tokenization non-monotonic)
        if truncated_tokens > available:
            short_suffix = " [T]"
            short_tokens = estimate_tokens(short_suffix)
            if short_tokens >= available:
                return None
            lo, hi = 0, best
            best2 = 0
            while lo <= hi:
                mid = (lo + hi) // 2
                cand = item.content[:mid] + short_suffix
                cand_tokens = estimate_tokens(cand)
                if cand_tokens <= available:
                    best2 = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best2 == 0:
                return None
            truncated_content = item.content[:best2] + short_suffix
            truncated_tokens = estimate_tokens(truncated_content)

        # Create truncated item
        truncated = ContextItem(
            item_id=item.item_id + "_truncated",
            category=item.category,
            content=truncated_content,
            authority=item.authority,
            trust=item.trust,
            token_cost=truncated_tokens,
            retrieval_score=item.retrieval_score,
            source=item.source,
            priority=item.priority,
            timestamp=item.timestamp,
            creator_id=item.creator_id,
            user_id=item.user_id,
            metadata={**item.metadata, "truncated": True, "original_tokens": item.token_cost},
        )

        if self.allocate(truncated):
            return truncated

        return None

    def get_snapshot(self) -> dict[ContextCategory, int]:
        """Get current token usage per category."""
        return {cat: state.used for cat, state in self._category_states.items()}

    def validate_budgets(self) -> list[str]:
        """Validate that budgets are not exceeded. Returns list of violations."""
        violations: list[str] = []

        # Check global budget
        if self._global_used > self.total_budget:
            violations.append(
                f"Global budget exceeded: {self._global_used} > {self.total_budget}"
            )

        # Check category budgets
        for cat, state in self._category_states.items():
            if state.used > state.budget:
                violations.append(
                    f"Category {cat.value} budget exceeded: {state.used} > {state.budget}"
                )

        return violations


def truncate_item_to_tokens(item: ContextItem, max_tokens: int) -> ContextItem | None:
    """Truncate an item to fit within a token budget.

    Returns truncated item or None if truncation is too small.
    """
    if max_tokens < MIN_ITEM_TOKENS:
        return None

    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(item.content) <= max_chars:
        return item  # Already fits

    truncated_content = item.content[:max_chars] + " [TRUNCATED]"
    truncated_tokens = estimate_tokens(truncated_content)

    return ContextItem(
        item_id=item.item_id + "_t",
        category=item.category,
        content=truncated_content,
        authority=item.authority,
        trust=item.trust,
        token_cost=truncated_tokens,
        retrieval_score=item.retrieval_score,
        source=item.source,
        priority=item.priority,
        timestamp=item.timestamp,
        creator_id=item.creator_id,
        user_id=item.user_id,
        metadata={**item.metadata, "truncated": True, "original_tokens": item.token_cost},
    )
