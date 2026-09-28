"""P3.1 - LLM Context Enrichment Tests."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _user_row(**overrides):
    base = {
        "id": 12345, "username": "testuser", "first_name": "Alice",
        "message_count": 10, "funnel_stage": "new",
        "is_blocked": False, "do_not_auto_reply": False,
    }
    base.update(overrides)
    return base


_CTX_PATCHES = [
    ("memory.context_assembler._get_user_safe", "_user"),
    ("memory.context_assembler._get_recent_messages_safe", "_msgs"),
    ("memory.context_assembler._get_summary_safe", "_summary"),
    ("memory.context_assembler._get_purchases_safe", "_purchases"),
    ("memory.context_assembler._get_active_offers_safe", "_offers"),
    ("memory.context_assembler._get_product_info_safe", "_product"),
    ("memory.context_assembler._get_creator_info_safe", "_creator"),
    ("memory.context_assembler._get_last_purchase_at_safe", "_last_purchase"),
    ("memory.context_assembler._get_last_followup_at_safe", "_last_followup"),
]

_DEFAULTS = {
    "_user": None, "_msgs": None, "_summary": None,
    "_purchases": (0, []), "_offers": (False, None),
    "_product": (None, None, None, None), "_creator": (None, False),
    "_last_purchase": None, "_last_followup": None,
}


async def _build(creator_id=1, user_id=12345, **overrides):
    from memory.context_assembler import build_llm_context
    vals = {**_DEFAULTS, **overrides}
    vals["_user"] = vals["_user"] or _user_row()
    if vals["_msgs"] is None:
        vals["_msgs"] = []
    # Phase 2.2: history safe-wrappers return (value, degraded) tuples.
    vals["_user"] = (vals["_user"], False)
    vals["_msgs"] = (vals["_msgs"], False)
    vals["_summary"] = (vals["_summary"], False)
    mocks = []
    for path, key in _CTX_PATCHES:
        m = patch(path, new_callable=AsyncMock, return_value=vals[key])
        mocks.append(m)
        m.start()
    try:
        return await build_llm_context(creator_id, user_id)
    finally:
        for m in mocks:
            m.stop()


# A - User Context
class TestUserContext:
    @pytest.mark.asyncio
    async def test_funnel_stage_loaded(self):
        ctx = await _build(_user=_user_row(funnel_stage="converted"))
        assert ctx.funnel_stage == "converted"

    @pytest.mark.asyncio
    async def test_blocked_state_loaded(self):
        ctx = await _build(_user=_user_row(is_blocked=True))
        assert ctx.is_blocked is True

    @pytest.mark.asyncio
    async def test_do_not_auto_reply_loaded(self):
        ctx = await _build(_user=_user_row(do_not_auto_reply=True))
        assert ctx.do_not_auto_reply is True

    @pytest.mark.asyncio
    async def test_first_name_loaded(self):
        ctx = await _build(_user=_user_row(first_name="Bob"))
        assert ctx.first_name == "Bob"

    @pytest.mark.asyncio
    async def test_message_count_loaded(self):
        ctx = await _build(_user=_user_row(message_count=42))
        assert ctx.message_count == 42


# B - Conversation
class TestConversationContext:
    @pytest.mark.asyncio
    async def test_recent_messages_loaded(self):
        msgs = [{"direction": "inbound", "content": "hello"}]
        ctx = await _build(_msgs=msgs)
        assert len(ctx.recent_messages) == 1
        assert ctx.recent_messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_summary_loaded(self):
        ctx = await _build(_summary="Earlier topic")
        assert ctx.conversation_summary == "Earlier topic"

    @pytest.mark.asyncio
    async def test_summary_none_when_empty(self):
        ctx = await _build(_summary=None)
        assert ctx.conversation_summary is None


# C - Commerce
class TestCommerceContext:
    @pytest.mark.asyncio
    async def test_purchase_count_correct(self):
        ctx = await _build(_purchases=(3, []))
        assert ctx.purchase_count == 3

    @pytest.mark.asyncio
    async def test_recent_purchases_returned(self):
        p = {"product_title": "X", "price_minor": 850, "currency": "USD", "occurred_at": "2026-08-22T10:00:00"}
        ctx = await _build(_purchases=(1, [p]))
        assert ctx.recent_purchases[0]["product_title"] == "X"
        assert ctx.recent_purchases[0]["price_minor"] == 850

    @pytest.mark.asyncio
    async def test_active_offer_correct(self):
        o = {"product_title": "Y", "price_minor": 500, "currency": "EUR", "state": "pending", "created_at": "2026-08-22T10:00:00"}
        ctx = await _build(_offers=(True, o))
        assert ctx.has_active_offer is True
        assert ctx.active_offer["state"] == "pending"

    @pytest.mark.asyncio
    async def test_no_active_offer(self):
        ctx = await _build(_offers=(False, None))
        assert ctx.has_active_offer is False
        assert ctx.active_offer is None

    @pytest.mark.asyncio
    async def test_price_from_authoritative_source(self):
        p = {"product_title": "Z", "price_minor": 1200, "currency": "GBP", "occurred_at": None}
        ctx = await _build(_purchases=(1, [p]))
        assert ctx.recent_purchases[0]["price_minor"] == 1200
        assert ctx.recent_purchases[0]["currency"] == "GBP"

    @pytest.mark.asyncio
    async def test_product_resolved(self):
        ctx = await _build(_product=("ProdX", 850, "USD", "https://example.com"))
        assert ctx.product_title == "ProdX"
        assert ctx.product_price_minor == 850
        assert ctx.product_currency == "USD"
        assert ctx.product_sales_url == "https://example.com"

    @pytest.mark.asyncio
    async def test_product_ambiguous_returns_none(self):
        ctx = await _build(_product=(None, None, None, None))
        assert ctx.product_title is None


# D - Security
class TestSecurity:
    @pytest.mark.asyncio
    async def test_buyer_email_never_appears(self):
        from memory.context_assembler import render_context
        p = {"product_title": "X", "price_minor": 100, "currency": "USD", "occurred_at": None}
        ctx = await _build(_purchases=(1, [p]), _user=_user_row())
        rendered = render_context(ctx)
        assert "buyer_email" not in rendered.lower()
        assert "@" not in rendered

    @pytest.mark.asyncio
    async def test_transaction_id_never_appears(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row())
        rendered = render_context(ctx)
        assert "transaction_id" not in rendered.lower()

    @pytest.mark.asyncio
    async def test_internal_ids_never_appears(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row())
        rendered = render_context(ctx)
        assert "offer_id" not in rendered.lower()
        assert "fangate" not in rendered.lower()

    @pytest.mark.asyncio
    async def test_api_key_never_appears(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row())
        rendered = render_context(ctx)
        assert "api_key" not in rendered.lower()
        assert "sk-" not in rendered
        assert "password" not in rendered.lower()


# E - Limits
class TestLimits:
    @pytest.mark.asyncio
    async def test_purchase_history_bounded(self):
        purchases = [
            {"product_title": f"P{i}", "price_minor": i * 100, "currency": "USD", "occurred_at": None}
            for i in range(10)
        ]
        ctx = await _build(_purchases=(10, purchases[:5]))
        assert len(ctx.recent_purchases) == 5

    @pytest.mark.asyncio
    async def test_active_offers_returns_most_recent(self):
        o = {"product_title": "Latest", "price_minor": 100, "currency": "USD", "state": "pending", "created_at": "2026-08-22T10:00:00"}
        ctx = await _build(_offers=(True, o))
        assert ctx.active_offer["product_title"] == "Latest"


# F - Prompt Injection
class TestPromptInjection:
    @pytest.mark.asyncio
    async def test_malicious_text_does_not_override_context(self):
        from memory.context_assembler import render_context
        ctx = await _build(
            _user=_user_row(funnel_stage="converted"),
            _purchases=(0, []),
        )
        rendered = render_context(ctx)
        assert "converted" in rendered
        assert "Purchases: 0" in rendered

    @pytest.mark.asyncio
    async def test_application_facts_separated(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row())
        rendered = render_context(ctx)
        assert "Funnel stage:" in rendered
        assert "Auto-reply allowed:" in rendered


# G - Failure Isolation
class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_user_query_failure_uses_defaults(self):
        from memory.context_assembler import build_llm_context
        with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock, return_value=([], False)):
                with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock, return_value=(None, False)):
                    with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock, return_value=(0, [])):
                        with patch("memory.context_assembler._get_active_offers_safe", new_callable=AsyncMock, return_value=(False, None)):
                            with patch("memory.context_assembler._get_product_info_safe", new_callable=AsyncMock, return_value=(None, None, None, None)):
                                with patch("memory.context_assembler._get_creator_info_safe", new_callable=AsyncMock, return_value=(None, False)):
                                    with patch("memory.context_assembler._get_last_purchase_at_safe", new_callable=AsyncMock, return_value=None):
                                        with patch("memory.context_assembler._get_last_followup_at_safe", new_callable=AsyncMock, return_value=None):
                                            ctx = await build_llm_context(1, 12345)
        assert ctx.first_name == "there"
        assert ctx.funnel_stage == "new"

    @pytest.mark.asyncio
    async def test_purchase_query_failure_returns_empty(self):
        with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.purchase_count == 0
        assert ctx.recent_purchases == []

    @pytest.mark.asyncio
    async def test_offer_query_failure_returns_false(self):
        with patch("memory.context_assembler._get_active_offers_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.has_active_offer is False
        assert ctx.active_offer is None

    @pytest.mark.asyncio
    async def test_product_query_failure_returns_none(self):
        with patch("memory.context_assembler._get_product_info_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.product_title is None

    @pytest.mark.asyncio
    async def test_creator_query_failure_returns_none(self):
        with patch("memory.context_assembler._get_creator_info_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.creator_name is None
        assert ctx.creator_sales_enabled is False

    @pytest.mark.asyncio
    async def test_followup_query_failure_returns_none(self):
        with patch("memory.context_assembler._get_last_followup_at_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.last_followup_at is None

    @pytest.mark.asyncio
    async def test_last_purchase_query_failure_returns_none(self):
        with patch("memory.context_assembler._get_last_purchase_at_safe", new_callable=AsyncMock, side_effect=Exception("db down")):
            ctx = await _build()
        assert ctx.last_purchase_at is None


# H - Creator Isolation
class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_id_passed_to_purchase_query(self):
        from memory.context_assembler import build_llm_context
        with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock, return_value=(0, [])) as m:
            with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock, return_value=(_user_row(), False)):
                with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock, return_value=([], False)):
                    with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock, return_value=(None, False)):
                        with patch("memory.context_assembler._get_active_offers_safe", new_callable=AsyncMock, return_value=(False, None)):
                            with patch("memory.context_assembler._get_product_info_safe", new_callable=AsyncMock, return_value=(None, None, None, None)):
                                with patch("memory.context_assembler._get_creator_info_safe", new_callable=AsyncMock, return_value=(None, False)):
                                    with patch("memory.context_assembler._get_last_purchase_at_safe", new_callable=AsyncMock, return_value=None):
                                        with patch("memory.context_assembler._get_last_followup_at_safe", new_callable=AsyncMock, return_value=None):
                                            await build_llm_context(42, 12345)
        m.assert_called_once()
        args = m.call_args
        assert args[0][0] == 42

    @pytest.mark.asyncio
    async def test_creator_id_passed_to_offer_query(self):
        from memory.context_assembler import build_llm_context
        with patch("memory.context_assembler._get_active_offers_safe", new_callable=AsyncMock, return_value=(False, None)) as m:
            with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock, return_value=(_user_row(), False)):
                with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock, return_value=([], False)):
                    with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock, return_value=(None, False)):
                        with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock, return_value=(0, [])):
                            with patch("memory.context_assembler._get_product_info_safe", new_callable=AsyncMock, return_value=(None, None, None, None)):
                                with patch("memory.context_assembler._get_creator_info_safe", new_callable=AsyncMock, return_value=(None, False)):
                                    with patch("memory.context_assembler._get_last_purchase_at_safe", new_callable=AsyncMock, return_value=None):
                                        with patch("memory.context_assembler._get_last_followup_at_safe", new_callable=AsyncMock, return_value=None):
                                            await build_llm_context(42, 12345)
        m.assert_called_once()
        args = m.call_args
        assert args[0][0] == 42

    @pytest.mark.asyncio
    async def test_creator_a_cannot_see_creator_b_data(self):
        from memory.context_assembler import build_llm_context
        async def _isolated_build(creator_id):
            with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock, return_value=(_user_row(), False)):
                with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock, return_value=([], False)):
                    with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock, return_value=(None, False)):
                        with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock, return_value=(0, [])):
                            with patch("memory.context_assembler._get_active_offers_safe", new_callable=AsyncMock, return_value=(False, None)):
                                with patch("memory.context_assembler._get_product_info_safe", new_callable=AsyncMock, return_value=(None, None, None, None)):
                                    with patch("memory.context_assembler._get_creator_info_safe", new_callable=AsyncMock, return_value=(None, False)):
                                        with patch("memory.context_assembler._get_last_purchase_at_safe", new_callable=AsyncMock, return_value=None):
                                            with patch("memory.context_assembler._get_last_followup_at_safe", new_callable=AsyncMock, return_value=None):
                                                return await build_llm_context(creator_id, 12345)
        ctx_a = await _isolated_build(1)
        ctx_b = await _isolated_build(2)
        assert ctx_a.user_id == ctx_b.user_id


# I - Determinism
class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_state_same_context(self):
        ctx1 = await _build(
            _user=_user_row(funnel_stage="converted"),
            _purchases=(2, [{"product_title": "X", "price_minor": 100, "currency": "USD", "occurred_at": None}]),
        )
        ctx2 = await _build(
            _user=_user_row(funnel_stage="converted"),
            _purchases=(2, [{"product_title": "X", "price_minor": 100, "currency": "USD", "occurred_at": None}]),
        )
        assert ctx1.funnel_stage == ctx2.funnel_stage
        assert ctx1.purchase_count == ctx2.purchase_count
        assert ctx1.recent_purchases == ctx2.recent_purchases
        assert ctx1.has_active_offer == ctx2.has_active_offer

    @pytest.mark.asyncio
    async def test_deterministic_frozen_dataclass(self):
        from memory.context_assembler import LLMContext
        ctx = await _build()
        with pytest.raises(AttributeError):
            ctx.funnel_stage = "changed"


# J - RenderContext
class TestRenderContext:
    @pytest.mark.asyncio
    async def test_render_includes_funnel_stage(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row(funnel_stage="engaged"))
        rendered = render_context(ctx)
        assert "Funnel stage: engaged" in rendered

    @pytest.mark.asyncio
    async def test_render_includes_purchase_count(self):
        from memory.context_assembler import render_context
        ctx = await _build(_purchases=(5, []))
        rendered = render_context(ctx)
        assert "Purchases: 5" in rendered

    @pytest.mark.asyncio
    async def test_render_includes_active_offer(self):
        from memory.context_assembler import render_context
        o = {"product_title": "VIP", "price_minor": 999, "currency": "USD", "state": "clicked", "created_at": None}
        ctx = await _build(_offers=(True, o))
        rendered = render_context(ctx)
        assert "Active offer:" in rendered
        assert "VIP" in rendered

    @pytest.mark.asyncio
    async def test_render_includes_creator(self):
        from memory.context_assembler import render_context
        ctx = await _build(_creator=("Jane", True))
        rendered = render_context(ctx)
        assert "Creator: Jane" in rendered
        assert "sales enabled: yes" in rendered

    @pytest.mark.asyncio
    async def test_render_includes_auto_reply_status(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row(do_not_auto_reply=True))
        rendered = render_context(ctx)
        assert "Auto-reply allowed: no" in rendered

    @pytest.mark.asyncio
    async def test_render_compact(self):
        from memory.context_assembler import render_context
        ctx = await _build(_user=_user_row())
        rendered = render_context(ctx)
        lines = [l for l in rendered.split("\n") if l.strip()]
        assert len(lines) <= 10


# K - Config Integration
class TestConfigIntegration:
    def test_settings_has_context_fields(self):
        from core.config import get_settings
        s = get_settings()
        assert hasattr(s, "max_context_messages")
        assert hasattr(s, "max_purchase_history")
        assert hasattr(s, "max_active_offers")

    def test_settings_are_positive_integers(self):
        from core.config import get_settings
        s = get_settings()
        assert isinstance(s.max_context_messages, int) and s.max_context_messages > 0
        assert isinstance(s.max_purchase_history, int) and s.max_purchase_history > 0
        assert isinstance(s.max_active_offers, int) and s.max_active_offers > 0


# L - Context Limits Module
class TestContextLimits:
    def test_limits_are_positive(self):
        from memory.context_limits import MAX_CONTEXT_MESSAGES, MAX_PURCHASE_HISTORY, MAX_ACTIVE_OFFERS
        assert MAX_CONTEXT_MESSAGES > 0
        assert MAX_PURCHASE_HISTORY > 0
        assert MAX_ACTIVE_OFFERS > 0

    def test_limits_are_integers(self):
        from memory.context_limits import MAX_CONTEXT_MESSAGES, MAX_PURCHASE_HISTORY, MAX_ACTIVE_OFFERS
        assert isinstance(MAX_CONTEXT_MESSAGES, int)
        assert isinstance(MAX_PURCHASE_HISTORY, int)
        assert isinstance(MAX_ACTIVE_OFFERS, int)
