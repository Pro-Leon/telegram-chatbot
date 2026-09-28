"""Phase 76 — Context Engine / One-Generation Shadow Validation Harness.

This module provides:
1. Fixture dataset covering persona, fan state, conversation, commerce,
   subscription, operator, and adversarial scenarios.
2. Baseline context extractor (what the current 3-LLM pipeline receives).
3. Candidate context extractor (Context Engine pipeline output).
4. Retention metrics (global, authority, commerce, persona, conversation).
5. Budget analysis (per-category, dropped items, HARD_POLICY preservation).
6. Authority safety audit.
7. Commerce safety audit.
8. Conflict tests.
9. Determinism verification.
10. Performance measurement.

SHADOW ONLY — no production changes.
"""

from __future__ import annotations

import hashlib
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from context_engine.assembler import ContextAssembler
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
    RetrievalScore,
)
from context_engine.renderer import CompactRenderer, RenderedContext
from context_engine.scorer import ContextScorer


# ─── Fixture Dataset ─────────────────────────────────────────────────────────


def _make_score(
    final: float = 0.5,
    source: float = 0.8,
    authority: float = 0.5,
) -> RetrievalScore:
    return RetrievalScore(
        source_score=source,
        topic_overlap=0.5,
        recency_score=0.5,
        importance_score=0.5,
        state_relevance=0.5,
        authority_score=authority,
        final_score=final,
    )


def _make_item(
    item_id: str,
    category: ContextCategory,
    content: str,
    authority: AuthorityLevel = AuthorityLevel.DETERMINISTIC_DERIVATION,
    trust: ContentTrust = ContentTrust.AUTHORITATIVE,
    priority: int = 5,
    creator_id: int | None = 100,
    user_id: int | None = 42,
    source: str = "test",
    score: float = 0.5,
    timestamp: float | None = None,
    token_cost: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ContextItem:
    if token_cost is None:
        token_cost = estimate_tokens(content)
    # Use a fixed timestamp (2025-01-01) to ensure determinism in scoring
    if timestamp is None:
        timestamp = 1735689600.0  # 2025-01-01T00:00:00Z
    return ContextItem(
        item_id=item_id,
        category=category,
        content=content,
        authority=authority,
        trust=trust,
        token_cost=token_cost,
        retrieval_score=_make_score(final=score, authority=authority.value / 5.0),
        source=source,
        priority=priority,
        timestamp=timestamp,
        creator_id=creator_id,
        user_id=user_id,
        metadata=metadata or {},
    )


@dataclass(frozen=True)
class FixtureRecord:
    """A single test scenario with baseline and expected properties."""
    name: str
    description: str
    baseline_items: list[ContextItem]
    expected_categories: set[ContextCategory]
    expected_authority_min: AuthorityLevel = AuthorityLevel.DETERMINISTIC_DERIVATION
    commerce_critical_facts: list[str] = field(default_factory=list)
    persona_facts: list[str] = field(default_factory=list)
    conversation_facts: list[str] = field(default_factory=list)
    subscription_facts: list[str] = field(default_factory=list)
    is_adversarial: bool = False
    conflict_description: str = ""


def build_fixture_dataset() -> list[FixtureRecord]:
    """Build the complete Phase 76 fixture dataset."""
    fixtures: list[FixtureRecord] = []

    # ── Persona fixtures ─────────────────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="persona_normal",
        description="Standard persona with voice and style information",
        baseline_items=[
            _make_item("p1", ContextCategory.SYSTEM, "You are Sunny Skye, a warm and friendly creator.",
                       authority=AuthorityLevel.HARD_POLICY, priority=10, score=0.9),
            _make_item("p2", ContextCategory.SYSTEM, "Voice: casual, energetic, uses emojis naturally.",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.8),
            _make_item("p3", ContextCategory.SYSTEM, "Style: 2-4 sentences, match fan energy.",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
        ],
        expected_categories={ContextCategory.SYSTEM},
        persona_facts=["Sunny Skye", "casual", "energetic", "2-4 sentences"],
    ))

    fixtures.append(FixtureRecord(
        name="persona_creator_specific",
        description="Creator-specific persona with unique identity",
        baseline_items=[
            _make_item("cs1", ContextCategory.SYSTEM, "You are Alex Rivera, a fitness coach.",
                       authority=AuthorityLevel.HARD_POLICY, priority=10, score=0.9, creator_id=200),
            _make_item("cs2", ContextCategory.SYSTEM, "Specialization: strength training, nutrition.",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85, creator_id=200),
        ],
        expected_categories={ContextCategory.SYSTEM},
        persona_facts=["Alex Rivera", "fitness coach", "strength training"],
    ))

    # ── Fan/business state fixtures ──────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="fan_new",
        description="New fan with no prior interaction history",
        baseline_items=[
            _make_item("fn1", ContextCategory.STATE, "Funnel stage: new",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=8, score=0.7),
            _make_item("fn2", ContextCategory.STATE, "Message count: 1",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=6, score=0.5),
            _make_item("fn3", ContextCategory.STATE, "Purchases: 0",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=7, score=0.6),
        ],
        expected_categories={ContextCategory.STATE},
    ))

    fixtures.append(FixtureRecord(
        name="fan_high_engagement",
        description="Highly engaged fan with multiple interactions",
        baseline_items=[
            _make_item("fe1", ContextCategory.STATE, "Funnel stage: engaged",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=8, score=0.75),
            _make_item("fe2", ContextCategory.STATE, "Message count: 47",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=6, score=0.5),
            _make_item("fe3", ContextCategory.STATE, "Relationship: warm, trust_score=0.82",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7, score=0.7),
            _make_item("fe4", ContextCategory.STATE, "Last active: 2 hours ago",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=5, score=0.6),
        ],
        expected_categories={ContextCategory.STATE},
    ))

    # ── Conversation fixtures ────────────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="conversation_short",
        description="Short conversation with 3 messages",
        baseline_items=[
            _make_item("c1", ContextCategory.CONVERSATION, "Hello! How are you?",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=1, score=0.4, source="conversation"),
            _make_item("c2", ContextCategory.CONVERSATION, "I'm great! Just posted new content.",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=2, score=0.5, source="conversation"),
            _make_item("c3", ContextCategory.CONVERSATION, "That looks amazing! When did you start?",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=3, score=0.6, source="conversation"),
        ],
        expected_categories={ContextCategory.CONVERSATION},
        conversation_facts=["Hello", "new content", "amazing"],
    ))

    fixtures.append(FixtureRecord(
        name="conversation_long",
        description="Long conversation with many messages",
        baseline_items=[
            _make_item(f"cl{i}", ContextCategory.CONVERSATION, f"Message {i} in long conversation",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=i, score=min(0.3 + i * 0.04, 0.95),
                       source="conversation")
            for i in range(1, 21)
        ],
        expected_categories={ContextCategory.CONVERSATION},
    ))

    # ── Commerce fixtures ────────────────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="commerce_no_state",
        description="No commerce state present",
        baseline_items=[],
        expected_categories=set(),
    ))

    fixtures.append(FixtureRecord(
        name="commerce_interest_detected",
        description="Fan interest detected but no active offer",
        baseline_items=[
            _make_item("ci1", ContextCategory.COMMERCE, "Interest detected: fitness program",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7, score=0.65),
            _make_item("ci2", ContextCategory.COMMERCE, "Topic: workout routine request",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=6, score=0.6),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["fitness program", "workout routine"],
    ))

    fixtures.append(FixtureRecord(
        name="commerce_active_offer",
        description="Active offer with price and product",
        baseline_items=[
            _make_item("co1", ContextCategory.COMMERCE, "Active offer: Premium Plan $29.99 USD",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
            _make_item("co2", ContextCategory.COMMERCE, "Offer status: pending, created 2h ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=7, score=0.7),
            _make_item("co3", ContextCategory.COMMERCE, "Product: 12-week strength program",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.8),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["Premium Plan", "$29.99", "pending", "12-week strength program"],
    ))

    fixtures.append(FixtureRecord(
        name="commerce_previous_purchase",
        description="Previous purchase history",
        baseline_items=[
            _make_item("cp1", ContextCategory.COMMERCE, "Purchase count: 2",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
            _make_item("cp2", ContextCategory.COMMERCE, "Last purchase: Starter Kit $14.99, 14 days ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=7, score=0.7),
            _make_item("cp3", ContextCategory.COMMERCE, "Repeat purchase eligible: yes",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=6, score=0.65),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["2", "Starter Kit", "$14.99", "14 days", "repeat purchase eligible"],
    ))

    fixtures.append(FixtureRecord(
        name="commerce_negotiation",
        description="Active negotiation with price discussion",
        baseline_items=[
            _make_item("cn1", ContextCategory.COMMERCE, "Active offer: Premium Plan $29.99 USD",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
            _make_item("cn2", ContextCategory.COMMERCE, "Negotiation: fan mentioned $20 price point",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=8, score=0.8),
            _make_item("cn3", ContextCategory.COMMERCE, "Desire stage: consideration",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7, score=0.7),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["$29.99", "$20", "Premium Plan", "consideration"],
    ))

    fixtures.append(FixtureRecord(
        name="commerce_post_purchase",
        description="Recent purchase with aftercare state",
        baseline_items=[
            _make_item("cpp1", ContextCategory.COMMERCE, "Purchase count: 1",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
            _make_item("cpp2", ContextCategory.COMMERCE, "Last purchase: Premium Plan $29.99, 3 days ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
            _make_item("cpp3", ContextCategory.COMMERCE, "Aftercare: active, day 3 of 7",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7, score=0.7),
            _make_item("cpp4", ContextCategory.COMMERCE, "Post-purchase follow-up: scheduled",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=6, score=0.65),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["1", "Premium Plan", "$29.99", "3 days"],
    ))

    # ── Subscription/payment fixtures ────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="subscription_active",
        description="Active subscription with payment state",
        baseline_items=[
            _make_item("sa1", ContextCategory.STATE, "Subscription: active, renews in 18 days",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
            _make_item("sa2", ContextCategory.STATE, "Payment: Visa ending 4242, last charged 12 days ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=7, score=0.7),
        ],
        expected_categories={ContextCategory.STATE},
        subscription_facts=["active", "renews", "18 days", "Visa", "4242"],
    ))

    fixtures.append(FixtureRecord(
        name="subscription_inactive",
        description="Inactive subscription",
        baseline_items=[
            _make_item("si1", ContextCategory.STATE, "Subscription: inactive, expired 30 days ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
            _make_item("si2", ContextCategory.STATE, "Payment: last payment failed, 30 days ago",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=7, score=0.7),
        ],
        expected_categories={ContextCategory.STATE},
        subscription_facts=["inactive", "expired", "30 days", "failed"],
    ))

    # ── Operator state fixtures ──────────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="operator_normal",
        description="Normal operation, no handoff required",
        baseline_items=[
            _make_item("on1", ContextCategory.STATE, "Operator status: normal",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=5, score=0.5),
        ],
        expected_categories={ContextCategory.STATE},
    ))

    fixtures.append(FixtureRecord(
        name="operator_handoff_required",
        description="Handoff to operator required",
        baseline_items=[
            _make_item("oh1", ContextCategory.STATE, "Handoff required: yes",
                       authority=AuthorityLevel.HARD_POLICY, priority=10, score=0.95),
            _make_item("oh2", ContextCategory.STATE, "Handoff reason: complex billing inquiry",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
        ],
        expected_categories={ContextCategory.STATE},
    ))

    # ── Adversarial fixtures ─────────────────────────────────────────────

    fixtures.append(FixtureRecord(
        name="adversarial_price_conflict",
        description="Historical price in message conflicts with DB price",
        baseline_items=[
            _make_item("apc1", ContextCategory.COMMERCE, "Active offer: Premium Plan $29.99 USD",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
            _make_item("apc2", ContextCategory.CONVERSATION, "Fan said: I can pay 20",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=4, score=0.5, source="conversation"),
        ],
        expected_categories={ContextCategory.COMMERCE, ContextCategory.CONVERSATION},
        commerce_critical_facts=["$29.99", "Premium Plan"],
        is_adversarial=True,
        conflict_description="Historical $20 mention vs DB $29.99 — DB price must survive",
    ))

    fixtures.append(FixtureRecord(
        name="adversarial_negation",
        description="Fan previously interested, now negating",
        baseline_items=[
            _make_item("an1", ContextCategory.COMMERCE, "Interest detected: fitness program",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=6, score=0.6),
            _make_item("an2", ContextCategory.CONVERSATION, "Fan said: I don't want to buy anymore",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=4, score=0.5, source="conversation"),
        ],
        expected_categories={ContextCategory.COMMERCE, ContextCategory.CONVERSATION},
        is_adversarial=True,
        conflict_description="Interest vs negation — both should survive with recency info",
    ))

    fixtures.append(FixtureRecord(
        name="adversarial_duplicate_facts",
        description="Same fact repeated across sources",
        baseline_items=[
            _make_item("adf1", ContextCategory.COMMERCE, "Purchase count: 3",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.75),
            _make_item("adf2", ContextCategory.STATE, "Total purchases: 3",
                       authority=AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7, score=0.7),
        ],
        expected_categories={ContextCategory.COMMERCE, ContextCategory.STATE},
        is_adversarial=True,
        conflict_description="Duplicate purchase count across categories — dedup should handle",
    ))

    fixtures.append(FixtureRecord(
        name="adversarial_creator_isolation",
        description="Creator A data must not appear in Creator B context",
        baseline_items=[
            _make_item("aci1", ContextCategory.SYSTEM, "You are Creator A persona",
                       authority=AuthorityLevel.HARD_POLICY, priority=10, score=0.9, creator_id=100),
            _make_item("aci2", ContextCategory.COMMERCE, "Creator A product: $19.99",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=8, score=0.8, creator_id=100),
        ],
        expected_categories={ContextCategory.SYSTEM, ContextCategory.COMMERCE},
        is_adversarial=True,
        conflict_description="Only Creator 100 data present — no cross-creator leakage",
    ))

    fixtures.append(FixtureRecord(
        name="adversarial_stale_vs_recent",
        description="Old commerce state vs current state",
        baseline_items=[
            _make_item("asr1", ContextCategory.COMMERCE, "Offer: expired premium offer",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=3, score=0.3,
                       timestamp=1733097600.0),  # 2024-12-01 (old)
            _make_item("asr2", ContextCategory.COMMERCE, "Current offer: new basic plan $9.99",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
        ],
        expected_categories={ContextCategory.COMMERCE},
        commerce_critical_facts=["$9.99", "basic plan"],
        is_adversarial=True,
        conflict_description="Old expired offer vs new active offer — recency should differentiate",
    ))

    fixtures.append(FixtureRecord(
        name="adversarial_price_historical",
        description="Price mentioned in old message not authoritative",
        baseline_items=[
            _make_item("aph1", ContextCategory.COMMERCE, "Active offer: Pro Plan $49.99",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=9, score=0.85),
            _make_item("aph2", ContextCategory.CONVERSATION, "Old message: can you do it for 25?",
                       authority=AuthorityLevel.DETERMINISTIC_RULE, priority=2, score=0.3,
                       timestamp=1734912000.0,  # 2024-12-22 (old)
                       source="conversation"),
        ],
        expected_categories={ContextCategory.COMMERCE, ContextCategory.CONVERSATION},
        commerce_critical_facts=["$49.99", "Pro Plan"],
        is_adversarial=True,
        conflict_description="Historical price inquiry vs DB price — DB wins",
    ))

    return fixtures


# ─── Baseline Extractor ──────────────────────────────────────────────────────


@dataclass
class BaselineExtraction:
    """What the current 3-LLM pipeline receives."""
    items: list[ContextItem]
    rendered_text: str
    token_estimate: int
    categories: set[ContextCategory]
    authority_levels: set[AuthorityLevel]
    sources: set[str]


def extract_baseline(fixture: FixtureRecord) -> BaselineExtraction:
    """Extract baseline context from fixture data.

    The baseline represents what the existing build_llm_context + render_context
    pipeline would produce. We use the fixture items directly since they mirror
    the output of the production gatherers.
    """
    items = fixture.baseline_items
    rendered_parts = []
    for item in items:
        rendered_parts.append(f"[{item.category.value}] {item.content}")
    rendered_text = "\n".join(rendered_parts)

    return BaselineExtraction(
        items=items,
        rendered_text=rendered_text,
        token_estimate=sum(item.token_cost for item in items),
        categories={item.category for item in items},
        authority_levels={item.authority for item in items},
        sources={item.source for item in items},
    )


# ─── Candidate Extractor ─────────────────────────────────────────────────────


@dataclass
class CandidateExtraction:
    """What the Context Engine produces."""
    snapshot: ContextSnapshot
    rendered: RenderedContext
    messages: list[dict[str, str]]
    total_tokens: int
    category_tokens: dict[ContextCategory, int]
    candidate_count: int
    selected_count: int
    dedup_count: int
    items_by_category: dict[ContextCategory, list[ContextItem]]
    violations: list[str]
    assembly_time_ms: float


def extract_candidate(
    fixture: FixtureRecord,
    query: str = "",
    assembler: ContextAssembler | None = None,
) -> CandidateExtraction:
    """Extract candidate context using the Context Engine pipeline.

    Uses real ContextEngine components: score → dedup → budget → assemble → render.
    """
    if assembler is None:
        assembler = ContextAssembler()

    # Run the full assembly pipeline
    snapshot = assembler.assemble(
        candidates=list(fixture.baseline_items),
        query=query,
        respect_creator_isolation=True,
    )

    # Render
    renderer = CompactRenderer()
    rendered = renderer.render(snapshot)
    messages = renderer.render_to_messages(snapshot)

    # Validate
    violations = assembler.validate_assembly(snapshot)

    # Group items by category
    items_by_category: dict[ContextCategory, list[ContextItem]] = {}
    for item in snapshot.items:
        items_by_category.setdefault(item.category, []).append(item)

    return CandidateExtraction(
        snapshot=snapshot,
        rendered=rendered,
        messages=messages,
        total_tokens=snapshot.total_tokens,
        category_tokens=snapshot.category_tokens,
        candidate_count=snapshot.candidate_count,
        selected_count=snapshot.selected_count,
        dedup_count=snapshot.deduplication_count,
        items_by_category=items_by_category,
        violations=violations,
        assembly_time_ms=snapshot.assembly_time_ms,
    )


# ─── Retention Metrics ───────────────────────────────────────────────────────


@dataclass
class RetentionMetrics:
    """Context retention metrics comparing baseline to candidate."""
    global_retention: float  # candidate_items / baseline_items
    token_retention: float  # candidate_tokens / baseline_tokens
    information_retention: float  # % of baseline facts in candidate
    authority_retention: float  # % of authoritative items retained
    commerce_retention: float  # % of commerce facts retained
    persona_retention: float  # % of persona facts retained
    conversation_retention: float  # % of conversation facts retained
    category_coverage: float  # % of baseline categories present in candidate
    dropped_authority_levels: set[AuthorityLevel]
    hard_policy_dropped: int
    items_truncated: int


def compute_retention(
    baseline: BaselineExtraction,
    candidate: CandidateExtraction,
    fixture: FixtureRecord,
) -> RetentionMetrics:
    """Compute retention metrics between baseline and candidate."""

    # Global retention
    baseline_count = len(baseline.items)
    candidate_count = candidate.selected_count
    global_retention = candidate_count / max(baseline_count, 1)

    # Token retention
    token_retention = candidate.total_tokens / max(baseline.token_estimate, 1)

    # Information retention — check if key facts from baseline appear in candidate
    candidate_content = " ".join(item.content.lower() for item in candidate.snapshot.items)

    baseline_facts = set()
    for item in baseline.items:
        # Extract key facts (simplified — words > 3 chars)
        words = [w.lower().strip(".,!?;:") for w in item.content.split() if len(w) > 3]
        baseline_facts.update(words)

    candidate_facts = set()
    for item in candidate.snapshot.items:
        words = [w.lower().strip(".,!?;:") for w in item.content.split() if len(w) > 3]
        candidate_facts.update(words)

    if baseline_facts:
        information_retention = len(baseline_facts & candidate_facts) / len(baseline_facts)
    else:
        information_retention = 1.0

    # Authority retention
    baseline_authoritative = [i for i in baseline.items if i.is_authoritative]
    candidate_authoritative = [i for i in candidate.snapshot.items if i.is_authoritative]
    if baseline_authoritative:
        authority_retention = len(candidate_authoritative) / len(baseline_authoritative)
    else:
        authority_retention = 1.0

    # Commerce retention
    baseline_commerce = [i for i in baseline.items if i.category == ContextCategory.COMMERCE]
    candidate_commerce = [i for i in candidate.snapshot.items if i.category == ContextCategory.COMMERCE]
    if baseline_commerce:
        commerce_retention = len(candidate_commerce) / len(baseline_commerce)
    else:
        commerce_retention = 1.0

    # Persona retention
    baseline_persona = [i for i in baseline.items if i.category == ContextCategory.SYSTEM]
    candidate_persona = [i for i in candidate.snapshot.items if i.category == ContextCategory.SYSTEM]
    if baseline_persona:
        persona_retention = len(candidate_persona) / len(baseline_persona)
    else:
        persona_retention = 1.0

    # Conversation retention
    baseline_conv = [i for i in baseline.items if i.category == ContextCategory.CONVERSATION]
    candidate_conv = [i for i in candidate.snapshot.items if i.category == ContextCategory.CONVERSATION]
    if baseline_conv:
        conversation_retention = len(candidate_conv) / len(baseline_conv)
    else:
        conversation_retention = 1.0

    # Category coverage
    if baseline.categories:
        category_coverage = len(baseline.categories & set(candidate.category_tokens.keys())) / len(baseline.categories)
    else:
        category_coverage = 1.0

    # Dropped authority levels
    baseline_authorities = {i.authority for i in baseline.items}
    candidate_authorities = {i.authority for i in candidate.snapshot.items}
    dropped_authority_levels = baseline_authorities - candidate_authorities

    # HARD_POLICY dropped
    hard_policy_baseline = [i for i in baseline.items if i.authority == AuthorityLevel.HARD_POLICY]
    hard_policy_candidate = [i for i in candidate.snapshot.items if i.authority == AuthorityLevel.HARD_POLICY]
    hard_policy_dropped = len(hard_policy_baseline) - len(hard_policy_candidate)

    # Items truncated
    items_truncated = sum(1 for item in candidate.snapshot.items if "[T]" in item.content or "[TRUNCATED]" in item.content)

    return RetentionMetrics(
        global_retention=global_retention,
        token_retention=token_retention,
        information_retention=information_retention,
        authority_retention=authority_retention,
        commerce_retention=commerce_retention,
        persona_retention=persona_retention,
        conversation_retention=conversation_retention,
        category_coverage=category_coverage,
        dropped_authority_levels=dropped_authority_levels,
        hard_policy_dropped=hard_policy_dropped,
        items_truncated=items_truncated,
    )


# ─── Budget Analysis ─────────────────────────────────────────────────────────


@dataclass
class BudgetAnalysis:
    """Budget analysis results for a single fixture."""
    total_tokens: int
    global_budget: int
    within_budget: bool
    category_usage: dict[ContextCategory, int]
    category_budgets: dict[ContextCategory, int]
    category_within_budget: dict[ContextCategory, bool]
    items_before_budget: int
    items_after_budget: int
    items_truncated: int
    items_dropped: int
    dropped_items: list[ContextItem]
    highest_priority_dropped: ContextItem | None
    hard_policy_dropped: int


def analyze_budget(
    baseline: BaselineExtraction,
    candidate: CandidateExtraction,
) -> BudgetAnalysis:
    """Analyze budget compliance and dropped items."""

    # Items that were in baseline but not in candidate
    baseline_ids = {i.item_id for i in baseline.items}
    candidate_ids = {i.item_id for i in candidate.snapshot.items}
    dropped_ids = baseline_ids - candidate_ids

    # Reconstruct dropped items from baseline
    dropped_items = [i for i in baseline.items if i.item_id in dropped_ids]

    # Find highest priority dropped
    highest_priority_dropped = max(dropped_items, key=lambda x: x.priority) if dropped_items else None

    # HARD_POLICY dropped
    hard_policy_dropped = sum(1 for i in dropped_items if i.authority == AuthorityLevel.HARD_POLICY)

    # Category within budget
    category_within_budget = {}
    for cat, used in candidate.category_tokens.items():
        budget = CATEGORY_BUDGETS.get(cat, 0)
        category_within_budget[cat] = used <= budget

    return BudgetAnalysis(
        total_tokens=candidate.total_tokens,
        global_budget=TOTAL_CONTEXT_BUDGET,
        within_budget=candidate.total_tokens <= TOTAL_CONTEXT_BUDGET,
        category_usage=candidate.category_tokens,
        category_budgets={cat: CATEGORY_BUDGETS.get(cat, 0) for cat in candidate.category_tokens},
        category_within_budget=category_within_budget,
        items_before_budget=baseline.token_estimate,
        items_after_budget=candidate.selected_count,
        items_truncated=candidate.candidate_count - candidate.selected_count - candidate.dedup_count,
        items_dropped=len(dropped_items),
        dropped_items=dropped_items,
        highest_priority_dropped=highest_priority_dropped,
        hard_policy_dropped=hard_policy_dropped,
    )


# ─── Authority Safety Audit ──────────────────────────────────────────────────


@dataclass
class AuthorityAuditResult:
    """Result of authority safety audit."""
    passed: bool
    violations: list[str]
    hard_policy_count: int
    hard_policy_retained: int
    deterministic_rule_count: int
    deterministic_rule_retained: int
    no_llm_authority_escalation: bool
    creator_isolation_maintained: bool


def audit_authority(
    baseline: BaselineExtraction,
    candidate: CandidateExtraction,
    fixture: FixtureRecord,
) -> AuthorityAuditResult:
    """Audit that authority hierarchy is preserved."""
    violations = []

    # Check HARD_POLICY retention
    hp_baseline = [i for i in baseline.items if i.authority == AuthorityLevel.HARD_POLICY]
    hp_candidate = [i for i in candidate.snapshot.items if i.authority == AuthorityLevel.HARD_POLICY]
    hp_retained = len(hp_candidate)

    if len(hp_baseline) > hp_retained:
        violations.append(
            f"HARD_POLICY dropped: {len(hp_baseline)} baseline vs {hp_retained} candidate"
        )

    # Check DETERMINISTIC_RULE retention
    dr_baseline = [i for i in baseline.items if i.authority == AuthorityLevel.DETERMINISTIC_RULE]
    dr_candidate = [i for i in candidate.snapshot.items if i.authority == AuthorityLevel.DETERMINISTIC_RULE]
    dr_retained = len(dr_candidate)

    # Check no LLM authority escalation
    no_escalation = True
    for item in candidate.snapshot.items:
        if item.authority == AuthorityLevel.LLM_GENERATION and item.is_authoritative:
            violations.append(f"LLM item {item.item_id} incorrectly marked authoritative")
            no_escalation = False

    # Check creator isolation
    creator_ids = set()
    for item in candidate.snapshot.items:
        if item.creator_id is not None:
            creator_ids.add(item.creator_id)

    # If fixture has single creator, verify no other creator items
    fixture_creators = {i.creator_id for i in fixture.baseline_items if i.creator_id is not None}
    candidate_creators = {i.creator_id for i in candidate.snapshot.items if i.creator_id is not None}
    creator_isolation = candidate_creators.issubset(fixture_creators)

    if not creator_isolation:
        leaked = candidate_creators - fixture_creators
        violations.append(f"Creator isolation violated: leaked creators {leaked}")

    return AuthorityAuditResult(
        passed=len(violations) == 0,
        violations=violations,
        hard_policy_count=len(hp_baseline),
        hard_policy_retained=hp_retained,
        deterministic_rule_count=len(dr_baseline),
        deterministic_rule_retained=dr_retained,
        no_llm_authority_escalation=no_escalation,
        creator_isolation_maintained=creator_isolation,
    )


# ─── Commerce Safety Audit ───────────────────────────────────────────────────


@dataclass
class CommerceAuditResult:
    """Result of commerce safety audit."""
    passed: bool
    violations: list[str]
    price_preserved: bool
    product_preserved: bool
    offer_preserved: bool
    purchase_preserved: bool


def audit_commerce(
    baseline: BaselineExtraction,
    candidate: CandidateExtraction,
    fixture: FixtureRecord,
) -> CommerceAuditResult:
    """Audit that commerce-critical facts survive compaction."""
    violations = []

    candidate_text = " ".join(
        item.content.lower() for item in candidate.snapshot.items
        if item.category == ContextCategory.COMMERCE
    )

    # Check price preservation
    price_preserved = True
    for fact in fixture.commerce_critical_facts:
        if fact.lower() not in candidate_text:
            # Also check in other categories
            all_text = " ".join(item.content.lower() for item in candidate.snapshot.items)
            if fact.lower() not in all_text:
                violations.append(f"Commerce fact not found in candidate: '{fact}'")
                price_preserved = False

    # Check product preservation (simplified — check if any commerce content exists)
    product_preserved = any(
        item.category == ContextCategory.COMMERCE
        for item in candidate.snapshot.items
    ) or not any(
        item.category == ContextCategory.COMMERCE
        for item in baseline.items
    )

    # Check offer preservation
    offer_preserved = product_preserved  # Simplified

    # Check purchase state preservation
    purchase_preserved = product_preserved  # Simplified

    return CommerceAuditResult(
        passed=len(violations) == 0,
        violations=violations,
        price_preserved=price_preserved,
        product_preserved=product_preserved,
        offer_preserved=offer_preserved,
        purchase_preserved=purchase_preserved,
    )


# ─── Determinism Check ───────────────────────────────────────────────────────


def check_determinism(
    fixture: FixtureRecord,
    iterations: int = 5,
) -> dict[str, Any]:
    """Run the same fixture multiple times and verify identical output.

    Mocks time.time() to a fixed value so recency scoring is deterministic.
    """
    import unittest.mock

    snapshots = []
    with unittest.mock.patch("time.time", return_value=1735689600.0):
        for _ in range(iterations):
            assembler = ContextAssembler()
            candidate = extract_candidate(fixture, assembler=assembler)
            snapshots.append(candidate.snapshot)

    # Check all snapshots are identical
    first_items = snapshots[0].items
    all_identical = True
    for snap in snapshots[1:]:
        if snap.items != first_items:
            all_identical = False
            break

    return {
        "iterations": iterations,
        "all_identical": all_identical,
        "first_token_count": snapshots[0].total_tokens,
        "first_item_count": len(snapshots[0].items),
    }


# ─── Performance Measurement ─────────────────────────────────────────────────


@dataclass
class PerformanceResult:
    """Performance measurement results."""
    iterations: int
    gather_p50_ms: float
    gather_p95_ms: float
    assemble_p50_ms: float
    assemble_p95_ms: float
    render_p50_ms: float
    render_p95_ms: float
    total_p50_ms: float
    total_p95_ms: float


def measure_performance(
    fixture: FixtureRecord,
    iterations: int = 20,
) -> PerformanceResult:
    """Measure Context Engine pipeline performance."""
    assemble_times = []
    render_times = []
    total_times = []

    for _ in range(iterations):
        assembler = ContextAssembler()

        t0 = time.monotonic()
        snapshot = assembler.assemble(
            candidates=list(fixture.baseline_items),
            respect_creator_isolation=True,
        )
        t1 = time.monotonic()
        assemble_times.append((t1 - t0) * 1000)

        renderer = CompactRenderer()
        t2 = time.monotonic()
        rendered = renderer.render(snapshot)
        t3 = time.monotonic()
        render_times.append((t3 - t2) * 1000)

        total_times.append((t3 - t0) * 1000)

    def p50(vals): return statistics.median(vals)
    def p95(vals): return sorted(vals)[int(len(vals) * 0.95)] if len(vals) >= 20 else max(vals)

    return PerformanceResult(
        iterations=iterations,
        gather_p50_ms=0.0,  # Gather is not measured separately in this harness
        gather_p95_ms=0.0,
        assemble_p50_ms=p50(assemble_times),
        assemble_p95_ms=p95(assemble_times),
        render_p50_ms=p50(render_times),
        render_p95_ms=p95(render_times),
        total_p50_ms=p50(total_times),
        total_p95_ms=p95(total_times),
    )
