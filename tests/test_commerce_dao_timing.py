"""Gap 1 — Commerce DAO timing context tests.

Covers:
    A: get_timing_context populated from existing commerce data
    B: No prior offer/history returns neutral defaults
    C: Recent offer produces expected timing value
    D: Older offer produces expected timing value
    E: Creator isolation
    F: Database failure fails safely
    G: resolve_commerce_state passes timing context into pipeline request
    H: Decision engine cooldown/activity rules receive populated values
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _mock_pool(rows=None, fetchrow_return=None, fetchval_return=None):
    """Build a mock pool with configurable query results."""
    mock_conn = AsyncMock()
    if fetchrow_return is not None:
        mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    elif rows is not None:
        mock_conn.fetchrow = AsyncMock(side_effect=rows)
    if fetchval_return is not None:
        mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


# ── A. Timing context populated from existing commerce data ──────────────────


class TestTimingContextPopulated:
    @pytest.mark.asyncio
    async def test_recent_offer_and_purchase(self):
        from commerce.dao import get_timing_context

        now = datetime.now(UTC)
        last_offer = now - timedelta(hours=3)
        last_purchase = now - timedelta(hours=1)

        mock_pool, mock_conn = _mock_pool(
            fetchrow_return={
                "created_at": last_offer,
                "purchased_at": last_purchase,
            }
        )

        # Second call: 24h counts
        counts_row = {
            "total_offers": 2,
            "purchases": 1,
            "active_offers": 1,
        }

        call_count = 0

        async def _fetchrow_side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"created_at": last_offer, "purchased_at": last_purchase}
            return counts_row

        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is not None
        assert result["hours_since_last_offer"] >= 2.9
        assert result["hours_since_last_offer"] <= 3.1
        assert result["hours_since_last_purchase"] is not None
        assert result["hours_since_last_purchase"] >= 0.9
        assert result["hours_since_last_purchase"] <= 1.1
        assert result["recent_offer_count"] == 1
        assert result["recent_purchase_count"] == 1
        assert result["recent_sales_attempt_count"] == 2


# ── B. No prior offer/history returns neutral defaults ───────────────────────


class TestTimingContextNoHistory:
    @pytest.mark.asyncio
    async def test_no_offers_returns_neutral(self):
        from commerce.dao import get_timing_context

        call_count = 0

        async def _fetchrow_side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # no last offer
            return {"total_offers": 0, "purchases": 0, "active_offers": 0}

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is None
        assert result["hours_since_last_purchase"] is None
        assert result["recent_offer_count"] == 0
        assert result["recent_purchase_count"] == 0
        assert result["recent_sales_attempt_count"] == 0


# ── C. Recent offer produces expected timing value ───────────────────────────


class TestTimingContextRecentOffer:
    @pytest.mark.asyncio
    async def test_recent_offer_no_purchase(self):
        from commerce.dao import get_timing_context

        now = datetime.now(UTC)
        last_offer = now - timedelta(minutes=30)

        call_count = 0

        async def _fetchrow_side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"created_at": last_offer, "purchased_at": None}
            return {"total_offers": 1, "purchases": 0, "active_offers": 1}

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is not None
        assert result["hours_since_last_offer"] >= 0.45
        assert result["hours_since_last_offer"] <= 0.55
        assert result["hours_since_last_purchase"] is None
        assert result["recent_offer_count"] == 1
        assert result["recent_purchase_count"] == 0


# ── D. Older offer produces expected timing value ────────────────────────────


class TestTimingContextOlderOffer:
    @pytest.mark.asyncio
    async def test_offer_25_hours_ago(self):
        from commerce.dao import get_timing_context

        now = datetime.now(UTC)
        last_offer = now - timedelta(hours=25)

        call_count = 0

        async def _fetchrow_side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"created_at": last_offer, "purchased_at": None}
            return {"total_offers": 0, "purchases": 0, "active_offers": 0}

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is not None
        assert result["hours_since_last_offer"] >= 24.9
        assert result["hours_since_last_offer"] <= 25.1
        assert result["recent_offer_count"] == 0
        assert result["recent_sales_attempt_count"] == 0


# ── E. Creator isolation ─────────────────────────────────────────────────────


class TestTimingContextCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_scoped_queries(self):
        from commerce.dao import get_timing_context

        call_count = 0
        captured_args = []

        async def _fetchrow_side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            captured_args.clear()
            captured_args.extend(args)
            if call_count == 1:
                return None
            return {"total_offers": 0, "purchases": 0, "active_offers": 0}

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await get_timing_context(99, 42)

        # Both queries should receive creator_id=99, user_id=42
        assert captured_args[0] == 99
        assert captured_args[1] == 42


# ── F. Database failure fails safely ─────────────────────────────────────────


class TestTimingContextDBFailure:
    @pytest.mark.asyncio
    async def test_db_exception_returns_neutral(self):
        from commerce.dao import get_timing_context

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is None
        assert result["hours_since_last_purchase"] is None
        assert result["recent_offer_count"] == 0
        assert result["recent_purchase_count"] == 0
        assert result["recent_sales_attempt_count"] == 0

    @pytest.mark.asyncio
    async def test_pool_acquisition_failure_returns_neutral(self):
        from commerce.dao import get_timing_context

        mock_pool = MagicMock()
        mock_pool.acquire.side_effect = RuntimeError("pool exhausted")

        with patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_timing_context(1, 42)

        assert result["hours_since_last_offer"] is None
        assert result["recent_offer_count"] == 0


# ── G. resolve_commerce_state passes timing context into pipeline request ────


class TestTimingInPipelineRequest:
    @pytest.mark.asyncio
    async def test_timing_fields_populated_in_pipeline_request(self):
        from commerce.state import CommerceStateRequest, resolve_commerce_state

        now = datetime.now(UTC)
        last_offer = now - timedelta(hours=5)

        call_count = 0

        async def _fetchrow_timing(sql, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"created_at": last_offer, "purchased_at": None}
            return {"total_offers": 3, "purchases": 0, "active_offers": 2}

        mock_user = {"id": 42, "is_blocked": False, "funnel_stage": "new"}
        mock_integration = {"id": 1, "status": "active"}
        mock_product = {
            "id": 101,
            "title": "Test Product",
            "is_accessible": True,
            "sales_url": "https://fangate.dev/link/101",
            "price_minor": 500,
            "is_verif_age": False,
            "raw": {"media": []},
        }

        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow_timing)
        mock_conn.fetchval = AsyncMock(return_value="new")
        mock_conn.execute = AsyncMock()

        with (
            patch("commerce.state.db_postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool),
            patch("commerce.state.db_postgres.get_user", new_callable=AsyncMock, return_value=mock_user),
            patch("commerce.state.db_postgres.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("commerce.state.db_postgres.get_user_persona", new_callable=AsyncMock, return_value=None),
            patch("commerce.state.db_fangate.get_creator", new_callable=AsyncMock, return_value={"id": 1}),
            patch("commerce.state.db_fangate.get_creator_integration", new_callable=AsyncMock, return_value=mock_integration),
            patch("commerce.state.db_fangate.get_fangate_product", new_callable=AsyncMock, return_value=mock_product),
            patch("commerce.state.commerce_dao.list_offers_for_user", new_callable=AsyncMock, return_value=[]),
            patch("commerce.state.commerce_dao.find_pending_offer_for_product", new_callable=AsyncMock, return_value=None),
            patch("commerce.state.commerce_dao.has_purchased_product", new_callable=AsyncMock, return_value=False),
            patch("commerce.state.commerce_dao.get_timing_context", new_callable=AsyncMock) as mock_timing,
        ):
            mock_timing.return_value = {
                "hours_since_last_offer": 5.0,
                "hours_since_last_purchase": None,
                "recent_offer_count": 2,
                "recent_purchase_count": 0,
                "recent_sales_attempt_count": 3,
            }

            request = CommerceStateRequest(user_id=42, creator_id=1, product_id=101)
            resolution = await resolve_commerce_state(request)

        assert resolution.status.value == "ready"
        req = resolution.request
        assert req.hours_since_last_offer == 5.0
        assert req.hours_since_last_purchase is None
        assert req.recent_offer_count == 2
        assert req.recent_purchase_count == 0
        assert req.recent_sales_attempt_count == 3


# ── H. Decision engine receives populated timing values ──────────────────────


class TestDecisionEngineReceivesTiming:
    def test_cooldown_enforced_after_timing(self):
        from commerce.decision import (
            CommerceDecisionContext,
            CommerceDecisionPolicy,
            decide_commerce_action,
        )
        from commerce.models import CommerceAction, PolicyDecision

        ctx = CommerceDecisionContext(
            user_id=42,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            hours_since_last_offer=2.0,
            hours_since_last_purchase=None,
            recent_offer_count=0,
            recent_purchase_count=0,
            recent_sales_attempt_count=0,
            has_active_offer=False,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        policy = CommerceDecisionPolicy(offer_cooldown_hours=24.0)
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code.value == "cooldown_active"

    def test_budget_enforced_after_timing(self):
        from commerce.decision import (
            CommerceDecisionContext,
            CommerceDecisionPolicy,
            decide_commerce_action,
        )
        from commerce.models import CommerceAction, PolicyDecision

        ctx = CommerceDecisionContext(
            user_id=42,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            hours_since_last_offer=25.0,
            hours_since_last_purchase=None,
            recent_offer_count=3,
            recent_purchase_count=0,
            recent_sales_attempt_count=0,
            has_active_offer=False,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        policy = CommerceDecisionPolicy(max_offers_per_24h=2)
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code.value == "too_many_offers"

    def test_purchase_cooldown_enforced(self):
        from commerce.decision import (
            CommerceDecisionContext,
            CommerceDecisionPolicy,
            decide_commerce_action,
        )
        from commerce.models import CommerceAction, PolicyDecision

        ctx = CommerceDecisionContext(
            user_id=42,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            hours_since_last_offer=25.0,
            hours_since_last_purchase=1.0,
            recent_offer_count=0,
            recent_purchase_count=0,
            recent_sales_attempt_count=0,
            has_active_offer=False,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        policy = CommerceDecisionPolicy(purchase_cooldown_hours=6.0)
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code.value == "recent_purchase"

    def test_no_timing_data_allows_offer(self):
        from commerce.decision import (
            CommerceDecisionContext,
            CommerceDecisionPolicy,
            decide_commerce_action,
        )
        from commerce.models import CommerceAction, PolicyDecision

        ctx = CommerceDecisionContext(
            user_id=42,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            hours_since_last_offer=None,
            hours_since_last_purchase=None,
            recent_offer_count=0,
            recent_purchase_count=0,
            recent_sales_attempt_count=0,
            has_active_offer=False,
            has_relevant_product=True,
            creator_sales_enabled=True,
            user_asked_to_buy=True,
        )
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.allowed is True
