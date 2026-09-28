"""Context Engine models (Phase 70).

Strongly typed representations for context items, categories, authority
levels, and scoring. These models make it impossible to accidentally
confuse authoritative state with LLM-generated information.
"""

from __future__ import annotations

import enum
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


class AuthorityLevel(int, enum.Enum):
    """Authority hierarchy from Phase 69 specification.

    Level 0: Hard policy — never overridden
    Level 1: Deterministic rules — PURE, no LLM
    Level 2: Deterministic derivation — computed, no LLM
    Level 3: Context assembly — budget-enforced
    Level 4: LLM generation — advisory
    Level 5: Post-generation — deterministic
    """

    HARD_POLICY = 0
    DETERMINISTIC_RULE = 1
    DETERMINISTIC_DERIVATION = 2
    CONTEXT_ASSEMBLY = 3
    LLM_GENERATION = 4
    POST_GENERATION = 5


class ContextCategory(str, enum.Enum):
    """Context categories with separate budget/accounting boundaries.

    Each category has its own token budget. The engine must know
    where each item came from.
    """

    SYSTEM = "system"
    STATE = "state"
    COMMERCE = "commerce"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    TEMPORAL = "temporal"
    CONTENT = "content"
    CONVERSATION = "conversation"
    EMBEDDED = "embedded"


# Phase 69 hard budget: TOTAL = 2600 tokens
TOTAL_CONTEXT_BUDGET = 2600

# Per-category budgets from Phase 69 specification
CATEGORY_BUDGETS: dict[ContextCategory, int] = {
    ContextCategory.SYSTEM: 400,
    ContextCategory.STATE: 200,
    ContextCategory.COMMERCE: 200,
    ContextCategory.MEMORY: 150,
    ContextCategory.KNOWLEDGE: 150,
    ContextCategory.TEMPORAL: 50,
    ContextCategory.CONTENT: 100,
    ContextCategory.CONVERSATION: 800,
    ContextCategory.EMBEDDED: 200,
}

# Note: Per-category budgets sum to 2250 tokens.
# The TOTAL_CONTEXT_BUDGET of 2600 provides a 350-token buffer
# for items that span categories or for dynamic allocation.
# This matches the Phase 69/70 specification exactly.


class ContentTrust(str, enum.Enum):
    """Provenance trust level for prompt-injection boundary."""

    AUTHORITATIVE = "authoritative"  # DB-derived, deterministic
    CONTEXTUAL = "contextual"  # Relevant but not authoritative
    UNTRUSTED = "untrusted"  # User-provided, potential injection


@dataclass(frozen=True)
class RetrievalScore:
    """Score for a retrieved context item.

    All scores are 0.0-1.0. Higher is more relevant/important.
    """

    source_score: float  # How relevant the source is
    topic_overlap: float  # Topic match with current message
    recency_score: float  # How recent the item is
    importance_score: float  # Intrinsic importance
    state_relevance: float  # State-dependent relevance
    authority_score: float  # Authority level bonus
    final_score: float  # Weighted combination

    def __post_init__(self) -> None:
        """Validate score bounds."""
        for name in (
            "source_score",
            "topic_overlap",
            "recency_score",
            "importance_score",
            "state_relevance",
            "authority_score",
            "final_score",
        ):
            val = getattr(self, name)
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be 0.0-1.0, got {val}")


@dataclass(frozen=True)
class ContextItem:
    """A single item in the context window.

    Each item carries:
    - stable identifier
    - source/category
    - content
    - authority level
    - relevance score
    - recency score
    - importance score
    - state-relevance score
    - combined selection score
    - token/character cost
    - timestamp where applicable
    - metadata required for provenance
    - trust/authority information
    """

    item_id: str
    category: ContextCategory
    content: str
    authority: AuthorityLevel
    trust: ContentTrust
    token_cost: int
    retrieval_score: RetrievalScore
    source: str  # Module/function that produced it
    priority: int  # Higher = more important (within category)
    timestamp: float | None = None  # Unix timestamp when relevant
    creator_id: int | None = None  # Creator isolation
    user_id: int | None = None  # Fan isolation
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def generate_id(category: ContextCategory, source: str, content: str) -> str:
        """Generate a stable deterministic ID for an item."""
        raw = f"{category.value}:{source}:{content[:100]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def __post_init__(self) -> None:
        """Validate item invariants."""
        if self.token_cost < 0:
            raise ValueError(f"token_cost must be >= 0, got {self.token_cost}")
        if self.priority < 0:
            raise ValueError(f"priority must be >= 0, got {self.priority}")

    @property
    def is_authoritative(self) -> bool:
        """True if this item carries hard policy or deterministic authority."""
        return self.authority <= AuthorityLevel.DETERMINISTIC_DERIVATION

    @property
    def is_advisory(self) -> bool:
        """True if this item is LLM-derived and advisory only."""
        return self.authority >= AuthorityLevel.LLM_GENERATION

    @property
    def recency_hours(self) -> float:
        """Hours since item was created, or 0 if no timestamp."""
        if self.timestamp is None:
            return 0.0
        return max(0.0, (time.time() - self.timestamp) / 3600.0)


@dataclass(frozen=True)
class AuthoritativeState:
    """Single authoritative state snapshot for one turn.

    ONE TURN = ONE AUTHORITATIVE STATE SNAPSHOT.
    All downstream stages (Context Engine, ranking, conflict resolution,
    OneCall context construction, conversation-state consumers) must reuse
    the SAME instance. No refetch within the same turn.

    This is an assembly object, not a second database model. It holds
    already-proven existing state, normalized for the turn.
    """

    # Identity
    creator_id: int | None
    user_id: int
    generation_id: str | None
    current_message: str
    timestamp: float = field(default_factory=time.time)

    # Core DB state (current)
    user: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    recent_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    summary: str | None = None
    summary_age_days: float | None = None

    # Persona (hard policy)
    persona: str = ""
    structured_persona: dict[str, Any] | None = None
    persona_name: str | None = None
    persona_id: int | None = None
    persona_version: int | None = None

    # Phase 89 grounding — deterministic, pre-generation
    participants: Any | None = None  # ConversationParticipants
    conversation_contract: Any | None = None  # ConversationContract

    # Derived state (once)
    conversation_state: Any | None = None  # ConversationState object
    conversation_state_dict: dict[str, Any] | None = None

    # Commerce authoritative context (deterministic, for rendering only;
    # execution authority remains fangate_products.price_minor via commerce pipeline)
    commerce_context_text: str = ""
    llm_context: Any | None = None  # LLMContext from memory/context_assembler

    # Retrieval-mutable but snapshot-scoped lists (derived, contextual)
    fan_knowledge_snapshot: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    long_term_memories_snapshot: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    # Phase 5 relationship-aware context (advisory, turn-scoped, read-only).
    # Rendered data-only text selected deterministically from already-fetched
    # state; carried here so the production OneCall path — which rebuilds
    # from this snapshot and ignores the legacy context list — receives the
    # same relationship context and strategy block as the legacy path.
    # Empty string means "nothing selected; render nothing".
    relationship_context_text: str = ""
    strategy_block_text: str = ""
    # Phase 5 remediation (Defect 2): persona-behavior realization guidance
    # carrier. Same minimal snapshot-carrier pattern as
    # relationship_context_text / strategy_block_text. Empty means "render
    # nothing"; populated text is advisory realization guidance only, never
    # safety/identity override and never commerce strategy.
    behavior_block_text: str = ""
    # Phase 6 descriptive intimacy context (advisory, turn-scoped,
    # read-only). Same snapshot-carrier pattern. Empty means "render
    # nothing"; populated text is descriptive conversational state only,
    # never permission, consent, safety policy, or commerce authority.
    intimacy_context_text: str = ""
    # Phase 7 boundary constraint context (advisory guidance, turn-scoped,
    # read-only). Same snapshot-carrier pattern. Empty means "render
    # nothing"; populated text lists active user-established behavioral
    # constraints only (never raw wording, never permission/consent
    # scores). Guidance only: deterministic enforcement lives in output
    # validation (commerce/boundary_validation.py) plus routing and the
    # commerce veto, never in this text.
    boundary_context_text: str = ""
    # Phase 8 content-transition guidance (advisory, turn-scoped,
    # read-only). Same snapshot-carrier pattern. Empty means "render
    # nothing"; populated text holds bounded categorical labels only
    # (transition / user_interest / realization / no_offer_from_warmth).
    # Guidance only: never commerce authority (no media/price/
    # eligibility/selection), never a strategy move, never an offer
    # command. The selector fires only on current-turn content
    # interest/request/continuity evidence; relationship warmth,
    # intimacy, desire, temperature, history, and product inventory can
    # never activate it.
    content_transition_context_text: str = ""

    # Phase 5 single authority: deterministic relationship state (AUTHORITATIVE → DERIVED)
    # Derived once from purchase history + recency + funnel, stored here so
    # FanStateSource and deterministic carriers reuse same value instead of
    # recomputing with hard-coded purchase_count=0.
    relationship_state: str | None = None
    has_active_offer: bool = False
    purchase_context: dict[str, Any] = field(default_factory=dict)

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Deep-freeze nested mutable structures (P2 Fix 4A).

        Frozen outer prevents attribute replacement, but nested dicts/lists
        remain mutable. We wrap them in immutable proxies so downstream
        consumers (CE and OneCall sharing same instance) cannot corrupt
        authoritative data.
        """
        from types import MappingProxyType

        def _freeze_dict(d: Any) -> Any:
            if isinstance(d, dict):
                # Recursively freeze values that are dict/list/tuple
                frozen = {k: _freeze_value(v) for k, v in d.items()}
                return MappingProxyType(frozen)
            return d

        def _freeze_value(v: Any) -> Any:
            if isinstance(v, dict):
                return _freeze_dict(v)
            if isinstance(v, list):
                return tuple(_freeze_value(x) for x in v)
            if isinstance(v, tuple):
                return tuple(_freeze_value(x) for x in v)
            return v

        # Freeze shallow dict fields
        if isinstance(self.user, dict) and not isinstance(self.user, MappingProxyType):
            object.__setattr__(self, "user", _freeze_dict(self.user))
        if isinstance(self.profile, dict) and not isinstance(self.profile, MappingProxyType):
            object.__setattr__(self, "profile", _freeze_dict(self.profile))
        if isinstance(self.metadata, dict) and not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", _freeze_dict(self.metadata))
        if isinstance(self.conversation_state_dict, dict) and not isinstance(self.conversation_state_dict, MappingProxyType):
            object.__setattr__(self, "conversation_state_dict", _freeze_dict(self.conversation_state_dict))
        if isinstance(self.structured_persona, dict) and not isinstance(self.structured_persona, MappingProxyType):
            object.__setattr__(self, "structured_persona", _freeze_dict(self.structured_persona))
        # Freeze recent_messages tuple of mappings
        if isinstance(self.recent_messages, (list, tuple)):
            frozen_recent = tuple(
                _freeze_dict(m) if isinstance(m, dict) else m for m in self.recent_messages
            )
            object.__setattr__(self, "recent_messages", frozen_recent)
        if isinstance(self.fan_knowledge_snapshot, (list, tuple)):
            object.__setattr__(self, "fan_knowledge_snapshot", tuple(_freeze_value(x) for x in self.fan_knowledge_snapshot))
        if isinstance(self.long_term_memories_snapshot, (list, tuple)):
            object.__setattr__(self, "long_term_memories_snapshot", tuple(_freeze_value(x) for x in self.long_term_memories_snapshot))
        if isinstance(self.purchase_context, dict) and not isinstance(self.purchase_context, MappingProxyType):
            object.__setattr__(self, "purchase_context", _freeze_dict(self.purchase_context))
        # Freeze conversation_state if it is a dict-like dataclass with __dict__
        # Keep object as-is if already immutable; only wrap dict case handled above.

    def to_conversation_state_dict(self) -> dict[str, Any] | None:
        """Return conversation_state as dict for scorer/ranking."""
        if self.conversation_state_dict is not None:
            return self.conversation_state_dict
        if self.conversation_state is None:
            return None
        if isinstance(self.conversation_state, dict):
            return self.conversation_state
        # ConversationState dataclass -> __dict__
        try:
            return dict(self.conversation_state.__dict__)  # type: ignore[attr-defined]
        except Exception:
            try:
                return vars(self.conversation_state)  # type: ignore[arg-type]
            except Exception:
                return None


@dataclass(frozen=True)
class ContextSnapshot:
    """Immutable snapshot of assembled context.

    Preserves:
    - provenance
    - category
    - authority
    - selection score
    - deterministic ordering
    """

    items: tuple[ContextItem, ...]
    total_tokens: int
    category_tokens: dict[ContextCategory, int]
    degradation_level: int  # 0=none, 1=minor, 2=moderate, 3=severe, 4=critical
    candidate_count: int  # How many candidates were considered
    selected_count: int  # How many items were selected
    deduplication_count: int  # How many duplicates were removed
    assembly_time_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token_utilization(self) -> float:
        """Fraction of total budget used (0.0-1.0)."""
        return self.total_tokens / TOTAL_CONTEXT_BUDGET if TOTAL_CONTEXT_BUDGET > 0 else 0.0

    @property
    def category_utilization(self) -> dict[ContextCategory, float]:
        """Per-category budget utilization (0.0-1.0)."""
        result: dict[ContextCategory, float] = {}
        for cat, budget in CATEGORY_BUDGETS.items():
            used = self.category_tokens.get(cat, 0)
            result[cat] = used / budget if budget > 0 else 0.0
        return result

    def get_items_by_category(self, category: ContextCategory) -> list[ContextItem]:
        """Get items filtered by category, in selection order."""
        return [item for item in self.items if item.category == category]

    def get_authoritative_items(self) -> list[ContextItem]:
        """Get only authoritative items (hard policy + deterministic)."""
        return [item for item in self.items if item.is_authoritative]

    def get_advisory_items(self) -> list[ContextItem]:
        """Get only advisory items (LLM-derived)."""
        return [item for item in self.items if item.is_advisory]
