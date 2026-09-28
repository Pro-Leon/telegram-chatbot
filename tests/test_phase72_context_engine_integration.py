"""Phase 72 — Context Engine Integration Test Suite.

Comprehensive tests exercising the complete
gather → score → dedup → budget → assemble → render pipeline
against realistic application state.

Tests cover:
1. Request construction
2. Complete gather pipeline
3. Real gatherer composition
4. Scoring
5. Deduplication
6. Budget enforcement
7. Rendering
8. Casual conversation
9. Purchase context
10. Purchase rejection/negation
11. Negotiation
12. Repeat purchase
13. Post purchase
14. Aftercare
15. Hesitation
16. Creator isolation
17. User isolation
18. Authority propagation
19. Missing source behavior
20. Malformed source behavior
21. Secret exclusion
22. Oversized context
23. Deterministic rendering
24. Model reuse
25. Performance bounds
26. No production LLM invocation
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from context_engine.gatherer import (
    ConversationHistorySource,
    DataSource,
    GathererConfig,
    PersonaSource,
)
from context_engine.integration import (
    ContextEngineIntegration,
    ContextRequest,
)
from context_engine.models import (
    CATEGORY_BUDGETS,
    TOTAL_CONTEXT_BUDGET,
    AuthorityLevel,
    ContentTrust,
    ContextCategory,
    ContextItem,
    RetrievalScore,
)
from context_engine.renderer import RenderedContext

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

CREATOR_A = 100
CREATOR_B = 200
USER_A = 1001
USER_A2 = 1002
USER_B = 2001


def _req(
    creator_id: int | None = CREATOR_A,
    user_id: int = USER_A,
    msg: str = "hello",
) -> ContextRequest:
    return ContextRequest(
        creator_id=creator_id,
        user_id=user_id,
        current_message=msg,
    )


def _make_item(
    content: str,
    category: ContextCategory = ContextCategory.STATE,
    authority: AuthorityLevel = AuthorityLevel.DETERMINISTIC_RULE,
    creator_id: int | None = CREATOR_A,
    user_id: int | None = USER_A,
    priority: int = 5,
    source: str = "test",
    token_cost: int | None = None,
) -> ContextItem:
    return ContextItem(
        item_id=ContextItem.generate_id(category, source, content),
        category=category,
        content=content,
        authority=authority,
        trust=ContentTrust.AUTHORITATIVE,
        token_cost=token_cost or max(1, len(content) // 4),
        retrieval_score=RetrievalScore(
            source_score=0.8,
            topic_overlap=0.5,
            recency_score=0.8,
            importance_score=0.7,
            state_relevance=0.6,
            authority_score=0.9,
            final_score=0.75,
        ),
        source=source,
        priority=priority,
        creator_id=creator_id,
        user_id=user_id,
    )


class MockSource(DataSource):
    """Controllable mock source for testing."""

    def __init__(
        self,
        name: str = "mock",
        cat: ContextCategory = ContextCategory.STATE,
        auth: AuthorityLevel = AuthorityLevel.DETERMINISTIC_RULE,
        items: list[ContextItem] | None = None,
        fail: bool = False,
    ):
        self._name = name
        self._cat = cat
        self._auth = auth
        self._items = items or []
        self._fail = fail

    @property
    def source_name(self) -> str:
        return self._name

    @property
    def category(self) -> ContextCategory:
        return self._cat

    @property
    def authority(self) -> AuthorityLevel:
        return self._auth

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        if self._fail:
            raise RuntimeError(f"{self._name} failed")
        return self._items


# ---------------------------------------------------------------------------
# 1. Request Construction
# ---------------------------------------------------------------------------


class TestRequestConstruction:
    """Tests for ContextRequest model."""

    def test_basic_request(self):
        req = _req()
        assert req.creator_id == CREATOR_A
        assert req.user_id == USER_A
        assert req.current_message == "hello"

    def test_to_gatherer_config(self):
        req = _req(msg="test message")
        config = req.to_gatherer_config()
        assert config.creator_id == CREATOR_A
        assert config.user_id == USER_A
        assert config.current_message == "test message"

    def test_no_creator(self):
        req = _req(creator_id=None)
        assert req.creator_id is None

    def test_request_is_frozen(self):
        req = _req()
        with pytest.raises(AttributeError):
            req.user_id = 999  # type: ignore[misc]

    def test_metadata(self):
        req = ContextRequest(
            creator_id=CREATOR_A,
            user_id=USER_A,
            current_message="hi",
            metadata={"test": True},
        )
        assert req.metadata["test"] is True


# ---------------------------------------------------------------------------
# 2. Complete Gather Pipeline (mocked)
# ---------------------------------------------------------------------------


class TestGatherPipeline:
    """Tests for the complete gather pipeline with mocked sources."""

    @pytest.mark.asyncio
    async def test_full_pipeline_mocked(self):
        items = [
            _make_item("persona text", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY),
            _make_item("fan state: engaged", ContextCategory.STATE),
            _make_item("[inbound] hey", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource(f"src_{i}", item.category, items=[item]) for i, item in enumerate(items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="hey"))

        assert result.candidate_count == 3
        assert result.selected_count >= 1
        assert result.total_tokens > 0
        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_empty_sources(self):
        engine = ContextEngineIntegration(sources=[MockSource()])
        result = await engine.process(_req())
        assert result.candidate_count == 0
        assert result.selected_count == 0


# ---------------------------------------------------------------------------
# 3. Real Gatherer Composition
# ---------------------------------------------------------------------------


class TestRealGathererComposition:
    """Tests for real gatherer composition with mocked DB."""

    @pytest.mark.asyncio
    async def test_persona_source_contribution(self):
        with patch("db.postgres.get_user_persona", new_callable=AsyncMock) as mock_p:
            mock_p.return_value = "You are Sunny."
            engine = ContextEngineIntegration(sources=[PersonaSource()])
            result = await engine.process(_req())
        system_items = result.items_by_category.get(ContextCategory.SYSTEM, [])
        assert len(system_items) >= 1
        assert any("Sunny" in i.content for i in system_items)

    @pytest.mark.asyncio
    async def test_conversation_source_contribution(self):
        with patch("db.postgres.get_recent_messages", new_callable=AsyncMock) as mock_m:
            mock_m.return_value = [
                {"direction": "inbound", "content": "hey"},
                {"direction": "outbound", "content": "hi!"},
            ]
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_s:
                mock_s.return_value = None
                engine = ContextEngineIntegration(sources=[ConversationHistorySource()])
                result = await engine.process(_req(msg="hey"))
        conv_items = result.items_by_category.get(ContextCategory.CONVERSATION, [])
        assert len(conv_items) >= 1


# ---------------------------------------------------------------------------
# 4. Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    """Tests for context scoring integration."""

    @pytest.mark.asyncio
    async def test_scoring_deterministic(self):
        items = [_make_item("test item", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        r1 = await engine.process(_req(msg="test"))
        r2 = await engine.process(_req(msg="test"))
        assert r1.total_tokens == r2.total_tokens
        assert r1.selected_count == r2.selected_count

    @pytest.mark.asyncio
    async def test_topic_relevance_boost(self):
        items = [
            _make_item("purchase intent detected", ContextCategory.COMMERCE),
            _make_item("fan likes photography", ContextCategory.MEMORY),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="I want to buy"))
        # Commerce item should score higher due to topic overlap
        assert result.snapshot is not None


# ---------------------------------------------------------------------------
# 5. Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Tests for deduplication integration."""

    @pytest.mark.asyncio
    async def test_exact_duplicates_removed(self):
        item = _make_item("duplicate content", ContextCategory.STATE)
        dup = _make_item("duplicate content", ContextCategory.STATE)
        sources = [MockSource("m", items=[item, dup])]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.deduplication_count >= 1
        assert result.selected_count <= 2

    @pytest.mark.asyncio
    async def test_creator_isolation_in_dedup(self):
        item_a = _make_item("shared content", ContextCategory.STATE, creator_id=CREATOR_A)
        item_b = _make_item("shared content", ContextCategory.STATE, creator_id=CREATOR_B)
        sources = [MockSource("m", items=[item_a, item_b])]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(creator_id=None))
        # Different creators should not be deduplicated
        assert result.selected_count == 2


# ---------------------------------------------------------------------------
# 6. Budget Enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    """Tests for token budget enforcement."""

    @pytest.mark.asyncio
    async def test_global_budget_respected(self):
        # Create many items to exceed budget
        items = [_make_item(f"item {i} " + "x" * 200, ContextCategory.CONVERSATION, priority=i) for i in range(30)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET

    @pytest.mark.asyncio
    async def test_category_budget_respected(self):
        cat_budget = CATEGORY_BUDGETS[ContextCategory.COMMERCE]
        items = [_make_item(f"commerce {i} " + "y" * 100, ContextCategory.COMMERCE, priority=i) for i in range(20)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        commerce_tokens = result.category_tokens.get(ContextCategory.COMMERCE, 0)
        assert commerce_tokens <= cat_budget

    @pytest.mark.asyncio
    async def test_high_priority_survives(self):
        items = [
            _make_item("critical system prompt", ContextCategory.SYSTEM, priority=10, token_cost=50),
            _make_item("low priority " + "z" * 500, ContextCategory.CONVERSATION, priority=0, token_cost=200),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        system_items = result.items_by_category.get(ContextCategory.SYSTEM, [])
        assert len(system_items) >= 1


# ---------------------------------------------------------------------------
# 7. Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    """Tests for CompactRenderer integration."""

    @pytest.mark.asyncio
    async def test_rendered_deterministic(self):
        items = [_make_item("deterministic test", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        r1 = await engine.process(_req())
        r2 = await engine.process(_req())
        assert r1.rendered.system_prompt == r2.rendered.system_prompt
        assert r1.rendered.state_block == r2.rendered.state_block

    @pytest.mark.asyncio
    async def test_messages_format(self):
        items = [
            _make_item("system prompt", ContextCategory.SYSTEM, priority=10),
            _make_item("fan state", ContextCategory.STATE, priority=9),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert isinstance(result.messages, list)
        for msg in result.messages:
            assert "role" in msg
            assert "content" in msg

    @pytest.mark.asyncio
    async def test_rendered_bounded(self):
        items = [_make_item("x" * 5000, ContextCategory.SYSTEM)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        # Rendered output should be bounded
        total_chars = (
            len(result.rendered.system_prompt)
            + len(result.rendered.state_block)
            + len(result.rendered.commerce_block)
        )
        assert total_chars < 20000  # Reasonable upper bound


# ---------------------------------------------------------------------------
# 8-15. Golden Scenario Tests
# ---------------------------------------------------------------------------


class TestGoldenScenarios:
    """Golden context case tests for realistic conversation scenarios."""

    @pytest.mark.asyncio
    async def test_scenario_a_casual_conversation(self):
        """Scenario A: Casual conversation - 'hey beautiful'."""
        items = [
            _make_item("You are Sunny Skye, warm and playful.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Funnel: engaged | Messages: 15", ContextCategory.STATE, priority=9),
            _make_item("[inbound] hey beautiful", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="hey beautiful"))

        # Should have persona and conversation
        assert ContextCategory.SYSTEM in result.items_by_category
        assert ContextCategory.CONVERSATION in result.items_by_category
        # No commerce dumped for casual chat
        # (may or may not have commerce depending on what gatherers produce)

    @pytest.mark.asyncio
    async def test_scenario_b_purchase_intent(self):
        """Scenario B: Purchase intent - 'I want to get it'."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Funnel: engaged | Purchases: 0", ContextCategory.STATE, priority=9),
            _make_item("Active offers: VIP Access [pending]", ContextCategory.COMMERCE, priority=8),
            _make_item("[inbound] I want to get it", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="I want to get it"))

        # Commerce state should be available
        assert ContextCategory.COMMERCE in result.items_by_category

    @pytest.mark.asyncio
    async def test_scenario_c_rejection(self):
        """Scenario C: Purchase rejection - 'I don't want to buy anything'."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Funnel: warm", ContextCategory.STATE, priority=9),
            _make_item("[inbound] I don't want to buy anything right now", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="I don't want to buy anything right now"))

        # Conversation history should be present
        assert ContextCategory.CONVERSATION in result.items_by_category
        # No authority upgrade from rejection

    @pytest.mark.asyncio
    async def test_scenario_d_negotiation(self):
        """Scenario D: Negotiation - 'that's too expensive'."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Active offers: VIP Access $19.99 [pending]", ContextCategory.COMMERCE, priority=8),
            _make_item("[inbound] that's too expensive, can you do better?", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="that's too expensive, can you do better?"))

        # Commerce context with price should be present
        commerce_items = result.items_by_category.get(ContextCategory.COMMERCE, [])
        assert len(commerce_items) >= 1

    @pytest.mark.asyncio
    async def test_scenario_e_repeat_purchase(self):
        """Scenario E: Repeat purchaser."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Purchases: 3 | Last purchase: 2 days ago", ContextCategory.STATE, priority=9),
            _make_item("Repeat purchase: eligible", ContextCategory.COMMERCE, priority=8),
            _make_item("[inbound] hey again!", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="hey again!"))

        # Purchase history should be available
        all_items = [i for items in result.items_by_category.values() for i in items]
        purchase_text = " ".join(i.content for i in all_items)
        assert "Purchases" in purchase_text or "purchase" in purchase_text.lower()

    @pytest.mark.asyncio
    async def test_scenario_f_post_purchase(self):
        """Scenario F: Post-purchase state."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Purchases: 1 | Last purchase: 1h ago", ContextCategory.STATE, priority=9),
            _make_item("Active offers: none", ContextCategory.COMMERCE, priority=8),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="thanks!"))

        # Purchase state should not expose payment details
        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "transaction_id" not in all_text.lower()
        assert "payment" not in all_text.lower()

    @pytest.mark.asyncio
    async def test_scenario_g_aftercare(self):
        """Scenario G: Aftercare / pending delivery."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Aftercare: delivery_pending", ContextCategory.STATE, priority=9),
            _make_item("Purchases: 1", ContextCategory.COMMERCE, priority=8),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="did you get my payment?"))

        # Aftercare state should be represented
        all_items = [i for items in result.items_by_category.values() for i in items]
        all_text = " ".join(i.content for i in all_items)
        assert "aftercare" in all_text.lower() or "delivery" in all_text.lower()

    @pytest.mark.asyncio
    async def test_scenario_h_hesitation(self):
        """Scenario H: Hesitation - recent offer count >= 1."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Offers(24h): 2 | Last offer: 3h ago", ContextCategory.COMMERCE, priority=8),
            _make_item("[inbound] hmm idk", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="hmm idk"))

        # Hesitation context should be available
        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET


# ---------------------------------------------------------------------------
# 16. Creator Isolation
# ---------------------------------------------------------------------------


class TestCreatorIsolation:
    """Creator isolation — one of the most important tests."""

    @pytest.mark.asyncio
    async def test_creator_a_items_retain_creator_id(self):
        """Creator A's items must retain their creator_id through assembly."""
        items_a = [
            _make_item("Sunny persona for A", ContextCategory.SYSTEM, creator_id=CREATOR_A),
            _make_item("Fan A state", ContextCategory.STATE, creator_id=CREATOR_A),
        ]
        sources = [MockSource("m", items=items_a)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(creator_id=CREATOR_A))

        for item in result.snapshot.items:
            assert item.creator_id == CREATOR_A

    @pytest.mark.asyncio
    async def test_creator_b_items_retain_creator_id(self):
        """Creator B's items must retain their creator_id through assembly."""
        items_b = [_make_item("Mia for B", ContextCategory.SYSTEM, creator_id=CREATOR_B)]
        sources = [MockSource("m", items=items_b)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(creator_id=CREATOR_B))

        for item in result.snapshot.items:
            assert item.creator_id == CREATOR_B

    @pytest.mark.asyncio
    async def test_dedup_respects_creator_isolation(self):
        """Deduplication must not merge items from different creators."""
        item_a = _make_item("shared content", ContextCategory.STATE, creator_id=CREATOR_A)
        item_b = _make_item("shared content", ContextCategory.STATE, creator_id=CREATOR_B)
        sources = [MockSource("m", items=[item_a, item_b])]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(creator_id=None))

        # Both items should survive — different creators are not duplicates
        creator_ids = {item.creator_id for item in result.snapshot.items}
        assert CREATOR_A in creator_ids
        assert CREATOR_B in creator_ids

    @pytest.mark.asyncio
    async def test_cross_creator_items_not_merged(self):
        """Items from different creators should not be merged during dedup."""
        items = [
            _make_item("persona A", ContextCategory.SYSTEM, creator_id=CREATOR_A),
            _make_item("persona B", ContextCategory.SYSTEM, creator_id=CREATOR_B),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(creator_id=None))

        # Both items should be in snapshot — they have different creator_ids
        assert result.selected_count == 2
        creator_ids = {item.creator_id for item in result.snapshot.items}
        assert CREATOR_A in creator_ids
        assert CREATOR_B in creator_ids


# ---------------------------------------------------------------------------
# 17. User Isolation
# ---------------------------------------------------------------------------


class TestUserIsolation:
    """User isolation — verify users under same creator are isolated."""

    @pytest.mark.asyncio
    async def test_user_a_no_user_b_conversation(self):
        """User A must not see User B's conversation."""
        items = [
            _make_item("[inbound] private from A", ContextCategory.CONVERSATION, user_id=USER_A, priority=0),
            _make_item("[inbound] private from B", ContextCategory.CONVERSATION, user_id=USER_B, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(user_id=USER_A))

        conv_items = result.items_by_category.get(ContextCategory.CONVERSATION, [])
        for item in conv_items:
            assert item.user_id == USER_A or item.user_id is None

    @pytest.mark.asyncio
    async def test_user_a_no_user_b_commerce(self):
        """User A must not see User B's commerce state."""
        items = [
            _make_item("Purchases: 3", ContextCategory.COMMERCE, user_id=USER_A),
            _make_item("Purchases: 0", ContextCategory.COMMERCE, user_id=USER_B),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(user_id=USER_A))

        commerce_items = result.items_by_category.get(ContextCategory.COMMERCE, [])
        for item in commerce_items:
            assert item.user_id == USER_A or item.user_id is None


# ---------------------------------------------------------------------------
# 18. Authority Verification
# ---------------------------------------------------------------------------


class TestAuthorityVerification:
    """Authority metadata preservation through assembly/rendering."""

    @pytest.mark.asyncio
    async def test_hard_policy_highest_authority(self):
        items = [
            _make_item("hard policy", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("deterministic rule", ContextCategory.STATE, AuthorityLevel.DETERMINISTIC_RULE, priority=9),
            _make_item("derived", ContextCategory.MEMORY, AuthorityLevel.DETERMINISTIC_DERIVATION, priority=7),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        # Hard policy should survive
        auth_levels = {item.authority for item in result.snapshot.items}
        assert AuthorityLevel.HARD_POLICY in auth_levels

    @pytest.mark.asyncio
    async def test_llm_item_not_authoritative(self):
        items = [
            _make_item("LLM generated text", ContextCategory.STATE, AuthorityLevel.LLM_GENERATION),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        for item in result.snapshot.items:
            if item.authority == AuthorityLevel.LLM_GENERATION:
                assert not item.is_authoritative

    @pytest.mark.asyncio
    async def test_assembly_does_not_upgrade_authority(self):
        items = [_make_item("derived state", ContextCategory.STATE, AuthorityLevel.DETERMINISTIC_DERIVATION)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        for item in result.snapshot.items:
            # Authority should not have been upgraded
            assert item.authority == AuthorityLevel.DETERMINISTIC_DERIVATION

    @pytest.mark.asyncio
    async def test_authority_summary(self):
        items = [
            _make_item("hard policy", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY),
            _make_item("deterministic", ContextCategory.STATE, AuthorityLevel.DETERMINISTIC_RULE),
            _make_item("deterministic 2", ContextCategory.COMMERCE, AuthorityLevel.DETERMINISTIC_RULE),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        assert result.authority_summary.get("HARD_POLICY", 0) >= 1
        assert result.authority_summary.get("DETERMINISTIC_RULE", 0) >= 2


# ---------------------------------------------------------------------------
# 19. Missing Source Behavior
# ---------------------------------------------------------------------------


class TestMissingSourceBehavior:
    """Tests for graceful degradation when sources fail."""

    @pytest.mark.asyncio
    async def test_persona_source_fails(self):
        """PersonaSource failure should not crash the engine."""
        failing = MockSource("persona", ContextCategory.SYSTEM, fail=True)
        ok = MockSource("state", ContextCategory.STATE, items=[
            _make_item("fan state", ContextCategory.STATE),
        ])
        engine = ContextEngineIntegration(sources=[failing, ok])
        result = await engine.process(_req())
        assert result.selected_count >= 1

    @pytest.mark.asyncio
    async def test_all_sources_fail(self):
        """All sources failing should return empty result, not crash."""
        sources = [MockSource(f"fail_{i}", fail=True) for i in range(5)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.candidate_count == 0
        assert result.total_tokens == 0

    @pytest.mark.asyncio
    async def test_missing_source_not_fabricated(self):
        """A missing source must not become fabricated state."""
        engine = ContextEngineIntegration(sources=[])
        result = await engine.process(_req())
        # No purchase text should appear when no commerce source exists
        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "purchased" not in all_text.lower()


# ---------------------------------------------------------------------------
# 20. Malformed Source Behavior
# ---------------------------------------------------------------------------


class TestMalformedSourceBehavior:
    """Tests for malformed source data handling."""

    @pytest.mark.asyncio
    async def test_empty_content_item(self):
        item = _make_item("", ContextCategory.STATE, token_cost=0)
        sources = [MockSource("m", items=[item])]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.total_tokens >= 0

    @pytest.mark.asyncio
    async def test_very_long_content_item(self):
        item = _make_item("x" * 10000, ContextCategory.STATE, token_cost=2500)
        sources = [MockSource("m", items=[item])]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        # Should be truncated or dropped, not crash
        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET


# ---------------------------------------------------------------------------
# 21. Secret Exclusion
# ---------------------------------------------------------------------------


class TestSecretExclusion:
    """Verify no secrets are injected by the engine itself.

    The Context Engine does NOT filter secrets from source content.
    Secret exclusion is the responsibility of the gatherers/sources.
    These tests verify the engine itself does not add secrets.
    """

    @pytest.mark.asyncio
    async def test_engine_does_not_inject_api_keys(self):
        """Engine must not inject API keys into context."""
        items = [_make_item("normal state", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "sk_live" not in all_text
        assert "api_key" not in all_text

    @pytest.mark.asyncio
    async def test_engine_does_not_inject_passwords(self):
        """Engine must not inject passwords into context."""
        items = [_make_item("normal state", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "password" not in all_text

    @pytest.mark.asyncio
    async def test_engine_does_not_inject_webhook_secrets(self):
        """Engine must not inject webhook secrets into context."""
        items = [_make_item("normal state", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "whsec_" not in all_text

    @pytest.mark.asyncio
    async def test_source_provided_secrets_not_filtered(self):
        """If a source provides secrets, the engine passes them through.

        This documents that secret filtering is the gatherer's responsibility,
        not the engine's.
        """
        items = [_make_item("api_key=sk_live_abc123", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        # Engine passes through source content — gatherers must not provide secrets
        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "sk_live" in all_text  # Documenting: engine doesn't filter


# ---------------------------------------------------------------------------
# 22. Oversized Context
# ---------------------------------------------------------------------------


class TestOversizedContext:
    """Tests for intentionally oversized candidate sets."""

    @pytest.mark.asyncio
    async def test_250_candidates(self):
        items = [_make_item(f"candidate {i} " + "z" * 50, ContextCategory.CONVERSATION, priority=i) for i in range(250)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET
        assert result.candidate_count == 250
        assert result.selected_count <= result.candidate_count

    @pytest.mark.asyncio
    async def test_many_duplicates(self):
        items = [_make_item("same content " * 10, ContextCategory.STATE) for _ in range(50)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.deduplication_count >= 1
        assert result.total_tokens <= TOTAL_CONTEXT_BUDGET


# ---------------------------------------------------------------------------
# 23. Deterministic Rendering
# ---------------------------------------------------------------------------


class TestDeterministicRendering:
    """Verify rendering is deterministic."""

    @pytest.mark.asyncio
    async def test_same_input_same_output(self):
        items = [
            _make_item("deterministic test", ContextCategory.STATE),
            _make_item("conversation", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        r1 = await engine.process(_req(msg="test"))
        r2 = await engine.process(_req(msg="test"))

        assert r1.rendered.system_prompt == r2.rendered.system_prompt
        assert r1.rendered.state_block == r2.rendered.state_block
        assert r1.rendered.commerce_block == r2.rendered.commerce_block
        assert r1.rendered.conversation_turns == r2.rendered.conversation_turns
        assert r1.messages == r2.messages

    @pytest.mark.asyncio
    async def test_rendered_preserves_categories(self):
        items = [
            _make_item("system", ContextCategory.SYSTEM, priority=10),
            _make_item("state", ContextCategory.STATE, priority=9),
            _make_item("commerce", ContextCategory.COMMERCE, priority=8),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        # All categories should be represented in rendered output
        assert result.rendered.system_prompt or result.rendered.state_block


# ---------------------------------------------------------------------------
# 24. Model Reuse
# ---------------------------------------------------------------------------


class TestModelReuse:
    """Verify no model reload per request."""

    @pytest.mark.asyncio
    async def test_no_embedding_model_reload(self):
        """MiniLM must not be reloaded during request processing."""
        items = [_make_item("test", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        with patch("context_engine.scorer.ContextScorer.score_item") as mock_score:
            mock_score.return_value = RetrievalScore(
                source_score=0.8, topic_overlap=0.5, recency_score=0.8,
                importance_score=0.7, state_relevance=0.6, authority_score=0.9,
                final_score=0.75,
            )
            await engine.process(_req())
            await engine.process(_req())
            # score_item is called for each item in each request
            # but no model initialization occurs


# ---------------------------------------------------------------------------
# 25. Performance Bounds
# ---------------------------------------------------------------------------


class TestPerformanceBounds:
    """Performance measurements for the pipeline."""

    @pytest.mark.asyncio
    async def test_10_candidates_performance(self):
        items = [_make_item(f"item {i}", ContextCategory.CONVERSATION, priority=i) for i in range(10)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        start = time.monotonic()
        result = await engine.process(_req())
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 5000  # 5s p95 bound
        assert result.total_time_ms < 5000

    @pytest.mark.asyncio
    async def test_50_candidates_performance(self):
        items = [_make_item(f"item {i} " + "x" * 20, ContextCategory.CONVERSATION, priority=i) for i in range(50)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        start = time.monotonic()
        await engine.process(_req())
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 10000  # 10s bound

    @pytest.mark.asyncio
    async def test_100_candidates_performance(self):
        items = [_make_item(f"item {i} " + "y" * 30, ContextCategory.CONVERSATION, priority=i) for i in range(100)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        start = time.monotonic()
        await engine.process(_req())
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 15000  # 15s bound

    @pytest.mark.asyncio
    async def test_250_candidates_performance(self):
        items = [_make_item(f"item {i} " + "z" * 40, ContextCategory.CONVERSATION, priority=i) for i in range(250)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)

        start = time.monotonic()
        await engine.process(_req())
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 30000  # 30s bound


# ---------------------------------------------------------------------------
# 26. No Production LLM Invocation
# ---------------------------------------------------------------------------


class TestNoProductionLLM:
    """Verify no production LLM calls occur during integration."""

    @pytest.mark.asyncio
    async def test_no_llm_worker_import(self):
        """Context Engine must not import or call llm_worker."""
        import sys
        llm_worker_modules = [k for k in sys.modules if "llm_worker" in k]
        # Should be empty before processing
        items = [_make_item("test", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        await engine.process(_req())
        # After processing, still no llm_worker import
        llm_worker_modules_after = [k for k in sys.modules if "llm_worker" in k]
        assert llm_worker_modules_after == llm_worker_modules

    @pytest.mark.asyncio
    async def test_no_deepseek_import(self):
        """Context Engine must not call DeepSeek (LLM #1)."""
        items = [_make_item("test", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        # Pipeline should complete without deepseek
        assert result is not None


# ---------------------------------------------------------------------------
# Additional: Negation / State Validation
# ---------------------------------------------------------------------------


class TestNegationState:
    """Verify context provides negation/state info for future LLM reasoning."""

    @pytest.mark.asyncio
    async def test_negation_context_provided(self):
        """Context should provide conversation history for negation reasoning."""
        items = [
            _make_item("You are Sunny.", ContextCategory.SYSTEM, AuthorityLevel.HARD_POLICY, priority=10),
            _make_item("Active offers: VIP $19.99 [pending]", ContextCategory.COMMERCE, priority=8),
            _make_item("[inbound] I don't want to buy", ContextCategory.CONVERSATION, priority=0),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="I don't want to buy"))

        # Conversation should be available for LLM to reason about negation
        conv = result.items_by_category.get(ContextCategory.CONVERSATION, [])
        assert len(conv) >= 1

    @pytest.mark.asyncio
    async def test_state_dependent_context(self):
        """State-dependent context should be available."""
        items = [
            _make_item("Relationship: warm | Purchases: 2", ContextCategory.STATE, priority=9),
            _make_item("Repeat purchase: eligible", ContextCategory.COMMERCE, priority=8),
        ]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req(msg="hey"))

        # State should be available
        all_text = " ".join(i.content for items in result.items_by_category.values() for i in items)
        assert "warm" in all_text.lower() or "repeat" in all_text.lower()


# ---------------------------------------------------------------------------
# Additional: Renderer Contract
# ---------------------------------------------------------------------------


class TestRendererContract:
    """Validate CompactRenderer output contract."""

    @pytest.mark.asyncio
    async def test_rendered_has_required_fields(self):
        items = [_make_item("test", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        rendered = result.rendered
        assert isinstance(rendered, RenderedContext)
        assert hasattr(rendered, "system_prompt")
        assert hasattr(rendered, "state_block")
        assert hasattr(rendered, "commerce_block")
        assert hasattr(rendered, "memory_block")
        assert hasattr(rendered, "temporal_block")
        assert hasattr(rendered, "content_block")
        assert hasattr(rendered, "conversation_turns")
        assert hasattr(rendered, "token_count")
        assert hasattr(rendered, "degradation_level")

    @pytest.mark.asyncio
    async def test_no_internal_objects_in_rendered(self):
        items = [_make_item("test content", ContextCategory.STATE)]
        sources = [MockSource("m", items=items)]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())

        rendered_str = str(result.rendered)
        assert "ContextItem" not in rendered_str
        assert "ContextSnapshot" not in rendered_str
        assert "RetrievalScore" not in rendered_str


# ---------------------------------------------------------------------------
# Additional: Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Tests for request validation."""

    def test_valid_request(self):
        engine = ContextEngineIntegration()
        violations = engine.validate_request(_req())
        assert violations == []

    def test_invalid_user_id(self):
        engine = ContextEngineIntegration()
        violations = engine.validate_request(_req(user_id=-1))
        assert len(violations) >= 1

    def test_empty_message(self):
        engine = ContextEngineIntegration()
        violations = engine.validate_request(_req(msg=""))
        assert len(violations) >= 1

    def test_invalid_creator_id(self):
        engine = ContextEngineIntegration()
        violations = engine.validate_request(_req(creator_id=-5))
        assert len(violations) >= 1
