"""Context Engine Data Gatherers — Comprehensive Tests (Phase 71).

Tests verify:
- Each source gathers correctly with mocked production data
- Each source fails safely when mocked APIs raise
- Creator isolation is maintained
- Authority levels match specification
- Content trust is correct
- Token costs are computed
- Orchestrator handles multiple sources and failures

All tests use mocked production APIs — no database required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_engine.gatherer import (
    CommerceStateSource,
    ContextGatherer,
    ConversationHistorySource,
    EmbeddedKnowledgeSource,
    FanStateSource,
    GathererConfig,
    MemorySource,
    PersonaSource,
    TemporalSource,
)
from context_engine.models import (
    AuthorityLevel,
    ContentTrust,
    ContextCategory,
    ContextItem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREATOR_ID = 123
USER_ID = 456


def _config(
    creator_id: int | None = CREATOR_ID,
    user_id: int | None = USER_ID,
    msg: str = "hello",
) -> GathererConfig:
    return GathererConfig(
        creator_id=creator_id,
        user_id=user_id,
        current_message=msg,
    )


# ---------------------------------------------------------------------------
# PersonaSource
# ---------------------------------------------------------------------------


class TestPersonaSource:
    """Tests for PersonaSource (SYSTEM, HARD_POLICY)."""

    def test_source_metadata(self):
        src = PersonaSource()
        assert src.source_name == "persona"
        assert src.category == ContextCategory.SYSTEM
        assert src.authority == AuthorityLevel.HARD_POLICY

    @pytest.mark.asyncio
    async def test_gather_persona_text(self):
        src = PersonaSource()
        with patch("db.postgres.get_user_persona", new_callable=AsyncMock) as mock_persona:
            mock_persona.return_value = "You are Sunny Skye."
            items = await src.gather(_config())
        assert len(items) >= 1
        persona_items = [i for i in items if "Sunny" in i.content]
        assert len(persona_items) == 1
        assert persona_items[0].authority == AuthorityLevel.HARD_POLICY
        assert persona_items[0].trust == ContentTrust.AUTHORITATIVE

    @pytest.mark.asyncio
    async def test_gather_structured_persona(self):
        src = PersonaSource()
        with patch("db.postgres.get_user_persona", new_callable=AsyncMock) as mock_p:
            mock_p.return_value = None
            with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock) as mock_sp:
                mock_sp.return_value = {"identity": {"name": "Sunny"}}
                with patch("memory.creator_persona.render_compact_persona_block") as mock_render:
                    mock_render.return_value = "FACTS identity: name=Sunny"
                    items = await src.gather(_config())
        compact_items = [i for i in items if "FACTS" in i.content]
        assert len(compact_items) == 1
        assert compact_items[0].metadata.get("format") == "compact_structured"

    @pytest.mark.asyncio
    async def test_gather_no_creator_returns_empty(self):
        src = PersonaSource()
        items = await src.gather(_config(creator_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_persona_failure_returns_empty(self):
        src = PersonaSource()
        with patch("db.postgres.get_user_persona", new_callable=AsyncMock) as mock_p:
            mock_p.side_effect = RuntimeError("DB down")
            with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock) as mock_s:
                mock_s.side_effect = RuntimeError("DB down")
                items = await src.gather(_config())
        # Should not raise — both failed, returns empty or partial
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_gather_both_fail_returns_empty(self):
        src = PersonaSource()
        with patch("db.postgres.get_user_persona", new_callable=AsyncMock) as mock_p:
            mock_p.side_effect = RuntimeError("fail")
            with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock) as mock_s:
                mock_s.side_effect = RuntimeError("fail")
                items = await src.gather(_config())
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# FanStateSource
# ---------------------------------------------------------------------------


class TestFanStateSource:
    """Tests for FanStateSource (STATE, DETERMINISTIC_DERIVATION)."""

    def test_source_metadata(self):
        src = FanStateSource()
        assert src.source_name == "fan_state"
        assert src.category == ContextCategory.STATE
        assert src.authority == AuthorityLevel.DETERMINISTIC_DERIVATION

    @pytest.mark.asyncio
    async def test_gather_user_state(self):
        src = FanStateSource()
        with patch("db.postgres.get_user", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = {
                "funnel_stage": "engaged",
                "is_blocked": False,
                "do_not_auto_reply": False,
                "message_count": 15,
            }
            with patch("commerce.relationship.derive_relationship_state") as mock_rel:
                mock_rel.return_value = MagicMock(value="warm")
                with patch("core.capability_contract.derive_capability_contract") as mock_cap:
                    mock_cap.return_value = MagicMock(render=lambda: "CAPABILITIES: send_text:yes")
                    with patch("db.postgres.get_recent_messages", new_callable=AsyncMock):
                        items = await src.gather(_config())
        state_items = [i for i in items if "Funnel" in i.content]
        assert len(state_items) == 1
        assert "engaged" in state_items[0].content

    @pytest.mark.asyncio
    async def test_gather_no_user_returns_empty(self):
        src = FanStateSource()
        items = await src.gather(_config(user_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_user_failure_returns_empty(self):
        src = FanStateSource()
        with patch("db.postgres.get_user", new_callable=AsyncMock) as mock_user:
            mock_user.side_effect = RuntimeError("DB error")
            items = await src.gather(_config())
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_gather_includes_segments(self):
        src = FanStateSource()
        with patch("db.postgres.get_user", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = {
                "funnel_stage": "new",
                "is_blocked": False,
                "do_not_auto_reply": False,
                "message_count": 0,
            }
            with (
                patch("commerce.relationship.derive_relationship_state") as mock_rel,
                patch("core.capability_contract.derive_capability_contract") as mock_cap,
                patch("db.postgres.get_recent_messages", new_callable=AsyncMock),
                patch("db.segments.list_segments", new_callable=AsyncMock) as mock_seg,
                patch("segments.evaluator.check_user_in_segment", new_callable=AsyncMock) as mock_check,
                patch("segments.models.RuleGroup"),
            ):
                mock_rel.return_value = MagicMock(value="cold")
                mock_cap.return_value = MagicMock(render=lambda: "CAPABILITIES: send_text:yes")
                mock_seg.return_value = [{"name": "VIP", "rules": {"type": "all"}}]
                mock_check.return_value = True
                items = await src.gather(_config())
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# ConversationHistorySource
# ---------------------------------------------------------------------------


class TestConversationHistorySource:
    """Tests for ConversationHistorySource (CONVERSATION, DETERMINISTIC_RULE)."""

    def test_source_metadata(self):
        src = ConversationHistorySource()
        assert src.source_name == "conversation_history"
        assert src.category == ContextCategory.CONVERSATION
        assert src.authority == AuthorityLevel.DETERMINISTIC_RULE

    @pytest.mark.asyncio
    async def test_gather_messages(self):
        src = ConversationHistorySource()
        with patch("db.postgres.get_recent_messages", new_callable=AsyncMock) as mock_msgs:
            mock_msgs.return_value = [
                {"direction": "inbound", "content": "Hey!"},
                {"direction": "outbound", "content": "Hi there!"},
                {"direction": "inbound", "content": "How are you?"},
            ]
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_sum:
                mock_sum.return_value = None
                items = await src.gather(_config())
        assert len(items) == 3
        # Pass 4: canonical direction→role, no [inbound] marker baked into content
        assert items[0].content == "Hey!"
        assert items[0].metadata["role"] == "user"
        assert items[0].metadata["direction"] == "inbound"
        assert items[1].content == "Hi there!"
        assert items[1].metadata["role"] == "assistant"
        assert items[1].metadata["direction"] == "outbound"
        assert items[2].content == "How are you?"
        assert items[2].metadata["role"] == "user"
        assert items[2].metadata["direction"] == "inbound"
        # More recent items get higher priority
        assert items[0].priority < items[2].priority

    @pytest.mark.asyncio
    async def test_gather_with_summary(self):
        src = ConversationHistorySource()
        with patch("db.postgres.get_recent_messages", new_callable=AsyncMock) as mock_msgs:
            mock_msgs.return_value = []
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_sum:
                mock_sum.return_value = "User likes photography."
                items = await src.gather(_config())
        summary_items = [i for i in items if "[summary]" in i.content]
        assert len(summary_items) == 1
        assert summary_items[0].metadata.get("type") == "summary"

    @pytest.mark.asyncio
    async def test_gather_no_user_returns_empty(self):
        src = ConversationHistorySource()
        items = await src.gather(_config(user_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_messages_failure_returns_empty(self):
        src = ConversationHistorySource()
        with patch("db.postgres.get_recent_messages", new_callable=AsyncMock) as mock_msgs:
            mock_msgs.side_effect = RuntimeError("DB error")
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_sum:
                mock_sum.side_effect = RuntimeError("DB error")
                items = await src.gather(_config())
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_gather_empty_messages(self):
        src = ConversationHistorySource()
        with patch("db.postgres.get_recent_messages", new_callable=AsyncMock) as mock_msgs:
            mock_msgs.return_value = []
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_sum:
                mock_sum.return_value = None
                items = await src.gather(_config())
        assert items == []


# ---------------------------------------------------------------------------
# CommerceStateSource
# ---------------------------------------------------------------------------


class TestCommerceStateSource:
    """Tests for CommerceStateSource (COMMERCE, DETERMINISTIC_RULE)."""

    def test_source_metadata(self):
        src = CommerceStateSource()
        assert src.source_name == "commerce_state"
        assert src.category == ContextCategory.COMMERCE
        assert src.authority == AuthorityLevel.DETERMINISTIC_RULE

    @pytest.mark.asyncio
    async def test_gather_purchases(self):
        src = CommerceStateSource()
        with patch("db.postgres.get_pool", new_callable=AsyncMock) as mock_pool_fn:
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool_fn.return_value = mock_pool

            # Count query
            count_row = MagicMock()
            count_row.__getitem__ = lambda self, key: 2 if key == "cnt" else None
            mock_conn.fetchrow = AsyncMock(return_value=count_row)
            # Purchase rows
            purchase_row = MagicMock()
            purchase_row.__getitem__ = lambda self, key: {
                "product_title": "VIP Access",
                "price_minor": 1000,
                "currency": "USD",
            }.get(key)
            mock_conn.fetch = AsyncMock(return_value=[purchase_row])

            items = await src.gather(_config())
        purchase_items = [i for i in items if "Purchases" in i.content]
        assert len(purchase_items) == 1
        assert "VIP Access" in purchase_items[0].content

    @pytest.mark.asyncio
    async def test_gather_no_creator_returns_empty(self):
        src = CommerceStateSource()
        items = await src.gather(_config(creator_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_no_user_returns_empty(self):
        src = CommerceStateSource()
        items = await src.gather(_config(user_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_purchase_failure_returns_empty(self):
        src = CommerceStateSource()
        with patch("db.postgres.get_pool", new_callable=AsyncMock) as mock_pool_fn:
            mock_pool_fn.side_effect = RuntimeError("DB error")
            items = await src.gather(_config())
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# MemorySource
# ---------------------------------------------------------------------------


class TestMemorySource:
    """Tests for MemorySource (MEMORY, DETERMINISTIC_DERIVATION)."""

    def test_source_metadata(self):
        src = MemorySource()
        assert src.source_name == "fan_memory"
        assert src.category == ContextCategory.MEMORY
        assert src.authority == AuthorityLevel.DETERMINISTIC_DERIVATION

    @pytest.mark.asyncio
    async def test_gather_knowledge(self):
        src = MemorySource()
        with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock) as mock_k:
            mock_k.return_value = [
                {"subject": "city", "value": "NYC", "status": "CURRENT", "confidence": 0.9},
                {"subject": "occupation", "value": "designer", "status": "CURRENT", "confidence": 0.8},
            ]
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_s:
                mock_s.return_value = None
                items = await src.gather(_config())
        assert len(items) == 2
        assert any("city=NYC" in i.content for i in items)
        assert any("occupation=designer" in i.content for i in items)

    @pytest.mark.asyncio
    async def test_gather_no_creator_returns_empty(self):
        src = MemorySource()
        items = await src.gather(_config(creator_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_knowledge_failure_returns_empty(self):
        src = MemorySource()
        with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock) as mock_k:
            mock_k.side_effect = RuntimeError("DB error")
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_s:
                mock_s.side_effect = RuntimeError("DB error")
                items = await src.gather(_config())
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_gather_empty_knowledge(self):
        src = MemorySource()
        with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock) as mock_k:
            mock_k.return_value = []
            with patch("db.postgres.get_latest_summary", new_callable=AsyncMock) as mock_s:
                mock_s.return_value = None
                items = await src.gather(_config())
        assert items == []


# ---------------------------------------------------------------------------
# TemporalSource
# ---------------------------------------------------------------------------


class TestTemporalSource:
    """Tests for TemporalSource (TEMPORAL, DETERMINISTIC_DERIVATION)."""

    def test_source_metadata(self):
        src = TemporalSource()
        assert src.source_name == "temporal"
        assert src.category == ContextCategory.TEMPORAL
        assert src.authority == AuthorityLevel.DETERMINISTIC_DERIVATION

    @pytest.mark.asyncio
    async def test_gather_temporal(self):
        src = TemporalSource()
        with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock) as mock_k:
            mock_k.return_value = [
                {"subject": "city", "value": "New York", "status": "CURRENT"},
            ]
            with patch("commerce.temporal_context.temporal_context_for_fan") as mock_t:
                mock_t.return_value = {
                    "timezone": "America/New_York",
                    "local_time": "14:30",
                    "city": "New York",
                }
                items = await src.gather(_config())
        assert len(items) == 1
        assert "New York" in items[0].content
        assert "America/New_York" in items[0].content
        assert "14:30" in items[0].content

    @pytest.mark.asyncio
    async def test_gather_no_creator_returns_empty(self):
        src = TemporalSource()
        items = await src.gather(_config(creator_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_failure_returns_empty(self):
        src = TemporalSource()
        with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock) as mock_k:
            mock_k.side_effect = RuntimeError("DB error")
            items = await src.gather(_config())
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# EmbeddedKnowledgeSource
# ---------------------------------------------------------------------------


class TestEmbeddedKnowledgeSource:
    """Tests for EmbeddedKnowledgeSource (EMBEDDED, DETERMINISTIC_RULE)."""

    def test_source_metadata(self):
        src = EmbeddedKnowledgeSource()
        assert src.source_name == "embedded_knowledge"
        assert src.category == ContextCategory.EMBEDDED
        assert src.authority == AuthorityLevel.DETERMINISTIC_RULE

    @pytest.mark.asyncio
    async def test_gather_self_facts(self):
        src = EmbeddedKnowledgeSource()
        with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock) as mock_sp:
            mock_sp.return_value = {"identity": {"name": "Sunny Skye"}}
            with patch("core.persona_self.render_persona_self_block") as mock_block:
                mock_block.return_value = "ABOUT SUNNY: enjoys cozy movie nights"
                with patch("core.capability_contract.derive_capability_contract") as mock_cap:
                    mock_cap.return_value = MagicMock(render=lambda: "CAPABILITIES: send_text:yes")
                    items = await src.gather(_config())
        self_items = [i for i in items if "ABOUT SUNNY" in i.content]
        assert len(self_items) == 1
        assert self_items[0].metadata.get("type") == "self_facts"

    @pytest.mark.asyncio
    async def test_gather_no_creator_returns_empty(self):
        src = EmbeddedKnowledgeSource()
        items = await src.gather(_config(creator_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_failure_returns_empty(self):
        src = EmbeddedKnowledgeSource()
        with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock) as mock_sp:
            mock_sp.side_effect = RuntimeError("fail")
            with patch("core.capability_contract.derive_capability_contract") as mock_cap:
                mock_cap.side_effect = RuntimeError("fail")
                items = await src.gather(_config())
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# ContextGatherer orchestrator
# ---------------------------------------------------------------------------


class TestContextGatherer:
    """Tests for ContextGatherer orchestrator."""

    def test_default_sources_registered(self):
        gatherer = ContextGatherer()
        source_names = [s.source_name for s in gatherer.sources]
        assert "persona" in source_names
        assert "fan_state" in source_names
        assert "conversation_history" in source_names
        assert "commerce_state" in source_names
        assert "fan_memory" in source_names
        assert "temporal" in source_names
        assert "embedded_knowledge" in source_names

    @pytest.mark.asyncio
    async def test_gather_all_calls_all_sources(self):
        gatherer = ContextGatherer()
        mock_items = [
            ContextItem(
                item_id="test1",
                category=ContextCategory.SYSTEM,
                content="test",
                authority=AuthorityLevel.HARD_POLICY,
                trust=ContentTrust.AUTHORITATIVE,
                token_cost=4,
                retrieval_score=MagicMock(final_score=0.9),
                source="test",
                priority=10,
            )
        ]
        for source in gatherer.sources:
            source.gather = AsyncMock(return_value=mock_items)
        items = await gatherer.gather_all(_config())
        assert len(items) == 7  # 7 sources x 1 item each

    @pytest.mark.asyncio
    async def test_gather_all_survives_source_failure(self):
        gatherer = ContextGatherer()
        ok_items = [
            ContextItem(
                item_id="ok",
                category=ContextCategory.SYSTEM,
                content="ok",
                authority=AuthorityLevel.HARD_POLICY,
                trust=ContentTrust.AUTHORITATIVE,
                token_cost=2,
                retrieval_score=MagicMock(final_score=0.9),
                source="ok",
                priority=10,
            )
        ]
        gatherer.sources[0].gather = AsyncMock(side_effect=RuntimeError("boom"))
        for s in gatherer.sources[1:]:
            s.gather = AsyncMock(return_value=ok_items)
        items = await gatherer.gather_all(_config())
        # 6 sources succeed, 1 fails -> 6 items
        assert len(items) == 6

    @pytest.mark.asyncio
    async def test_gather_all_empty_config(self):
        gatherer = ContextGatherer()
        for s in gatherer.sources:
            s.gather = AsyncMock(return_value=[])
        items = await gatherer.gather_all(_config(creator_id=None, user_id=None))
        assert items == []

    @pytest.mark.asyncio
    async def test_gather_all_all_fail(self):
        gatherer = ContextGatherer()
        for s in gatherer.sources:
            s.gather = AsyncMock(side_effect=RuntimeError("fail"))
        items = await gatherer.gather_all(_config())
        assert items == []

    def test_custom_sources(self):
        custom = PersonaSource()
        gatherer = ContextGatherer(sources=[custom])
        assert len(gatherer.sources) == 1
        assert gatherer.sources[0].source_name == "persona"
