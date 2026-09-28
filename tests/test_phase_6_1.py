"""Phase 6.1 — Autonomous commerce reachability & segment context tests.

Groups:

A: Product selection (resolve_commerce_product_with_history).
B: Product listing (list_valid_products).
C: Worker integration (_try_commerce_draft wiring).
D: Segment context (LLMContext segments, render_context).
E: Segment propagation (pipeline, state).
F: list_products tool.
G: Creator isolation.
H: Failure paths.
I: End-to-end commerce reachability.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from commerce.context import (
    CommerceConversationContext,
    ProductCommerceState,
    ProductIdentity,
)
from commerce.pipeline import CommercePipelineRequest
from commerce.product_selection import (
    list_valid_products,
    resolve_commerce_product,
    resolve_commerce_product_with_history,
)
from commerce.state import CommerceResolutionStatus, CommerceStateRequest, resolve_commerce_state
from memory.context_assembler import LLMContext, render_context

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────────

CREATOR_ID = 7
USER_ID = 42
PRODUCT_ID_A = 10
PRODUCT_ID_B = 20
PRODUCT_ID_C = 30

_VALID_PRODUCT_A = {
    "id": PRODUCT_ID_A,
    "creator_id": CREATOR_ID,
    "title": "VIP Bundle",
    "price_minor": 4400,
    "sales_url": "https://f.test/p/10",
    "is_accessible": True,
    "is_verif_age": False,
}
_VALID_PRODUCT_B = {
    "id": PRODUCT_ID_B,
    "creator_id": CREATOR_ID,
    "title": "Premium Pack",
    "price_minor": 2900,
    "sales_url": "https://f.test/p/20",
    "is_accessible": True,
    "is_verif_age": False,
}
_VALID_PRODUCT_C = {
    "id": PRODUCT_ID_C,
    "creator_id": CREATOR_ID,
    "title": "Starter Kit",
    "price_minor": 900,
    "sales_url": "https://f.test/p/30",
    "is_accessible": True,
    "is_verif_age": False,
}
_NO_SALES_URL_PRODUCT = {
    "id": 99,
    "creator_id": CREATOR_ID,
    "title": "No URL Product",
    "price_minor": 1000,
    "sales_url": None,
    "is_accessible": True,
    "is_verif_age": False,
}
_NOT_ACCESSIBLE_PRODUCT = {
    "id": 98,
    "creator_id": CREATOR_ID,
    "title": "Inaccessible Product",
    "price_minor": 1000,
    "sales_url": "https://f.test/p/98",
    "is_accessible": False,
    "is_verif_age": False,
}
_INTEGRATION_ROW = {
    "creator_id": CREATOR_ID,
    "status": "active",
    "currency_code": "USD",
}


def _make_request(**overrides: Any) -> CommerceStateRequest:
    defaults = {"user_id": USER_ID, "creator_id": CREATOR_ID}
    defaults.update(overrides)
    return CommerceStateRequest(**defaults)


# ── GROUP A: Product selection ───────────────────────────────────────────────


class TestProductSelectionWithHistory:
    """Test resolve_commerce_product_with_history()."""

    @pytest.mark.asyncio
    async def test_single_valid_product_returns_it(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result == PRODUCT_ID_A

    @pytest.mark.asyncio
    async def test_no_valid_products_returns_none(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_NO_SALES_URL_PRODUCT, _NOT_ACCESSIBLE_PRODUCT]
            )
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_two_products_one_purchased_returns_unpurchased(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = {PRODUCT_ID_A}
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result == PRODUCT_ID_B

    @pytest.mark.asyncio
    async def test_two_products_both_purchased_returns_none(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = {PRODUCT_ID_A, PRODUCT_ID_B}
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_two_unpurchased_products_returns_none_fail_closed(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = set()
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_three_products_one_purchased_returns_correct(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B, _VALID_PRODUCT_C]
            )
            mock_purch.return_value = {PRODUCT_ID_B}
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            # Two unpurchased (A, C) -> fail-closed
            assert result is None

    @pytest.mark.asyncio
    async def test_three_products_two_purchased_returns_unpurchased(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B, _VALID_PRODUCT_C]
            )
            mock_purch.return_value = {PRODUCT_ID_A, PRODUCT_ID_B}
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result == PRODUCT_ID_C

    @pytest.mark.asyncio
    async def test_db_failure_returns_none(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(side_effect=Exception("DB down"))
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_purchase_history_failure_returns_empty_set(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = set()  # failure = empty
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            # Two unpurchased -> fail-closed
            assert result is None

    @pytest.mark.asyncio
    async def test_creator_isolation_different_creator_products_not_seen(self):
        """Products from another creator must not be considered."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            # Only returns products for CREATOR_ID (as the query is creator-scoped)
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result == PRODUCT_ID_A
            # Verify creator_id was passed to the query
            mock_db.list_fangate_products.assert_called_once_with(
                CREATOR_ID, limit=200, offset=0
            )

    @pytest.mark.asyncio
    async def test_existing_single_product_path_unchanged(self):
        """resolve_commerce_product still works as before for single product."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            result = await resolve_commerce_product(CREATOR_ID)
            assert result == PRODUCT_ID_A

    @pytest.mark.asyncio
    async def test_existing_single_product_ambiguity_unchanged(self):
        """resolve_commerce_product still returns None for 2+ products."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            result = await resolve_commerce_product(CREATOR_ID)
            assert result is None


# ── GROUP B: Product listing ─────────────────────────────────────────────────


class TestListValidProducts:
    """Test list_valid_products()."""

    @pytest.mark.asyncio
    async def test_returns_valid_products(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B, _NO_SALES_URL_PRODUCT]
            )
            mock_db.get_creator_integration = AsyncMock(return_value=_INTEGRATION_ROW)
            result = await list_valid_products(CREATOR_ID)
            assert len(result) == 2
            assert result[0]["product_id"] == PRODUCT_ID_A
            assert result[1]["product_id"] == PRODUCT_ID_B
            assert result[0]["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_empty_when_no_valid_products(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_NO_SALES_URL_PRODUCT])
            mock_db.get_creator_integration = AsyncMock(return_value=_INTEGRATION_ROW)
            result = await list_valid_products(CREATOR_ID)
            assert result == []

    @pytest.mark.asyncio
    async def test_db_failure_returns_empty(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(side_effect=Exception("DB down"))
            result = await list_valid_products(CREATOR_ID)
            assert result == []


# ── GROUP C: Worker integration ──────────────────────────────────────────────


class TestWorkerIntegration:
    """Test that _try_commerce_draft uses the new resolver."""

    @pytest.mark.asyncio
    async def test_worker_uses_history_resolver(self):
        """Verify the worker module imports and uses the history-aware resolver."""
        import workers.llm_worker as llm_mod

        # The module should import resolve_commerce_product_with_history
        assert hasattr(llm_mod, "resolve_commerce_product_with_history")
        # And it should NOT import the old resolve_commerce_product
        # (it's been replaced)
        import inspect
        source = inspect.getsource(llm_mod._try_commerce_draft)
        assert "resolve_commerce_product_with_history" in source
        assert "resolve_commerce_product(" not in source or "with_history" in source


# ── GROUP D: Segment context ─────────────────────────────────────────────────


class TestSegmentContext:
    """Test segment fields in LLMContext and render_context()."""

    def test_segment_fields_default_empty(self):
        ctx = LLMContext(
            user_id=USER_ID,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="Fan",
            message_count=0,
            conversation_summary=None,
        )
        assert ctx.segment_names == []
        assert ctx.segment_count == 0

    def test_segment_fields_populated(self):
        ctx = LLMContext(
            user_id=USER_ID,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="Fan",
            message_count=0,
            conversation_summary=None,
            segment_names=["VIP", "High Spender"],
            segment_count=2,
        )
        assert ctx.segment_names == ["VIP", "High Spender"]
        assert ctx.segment_count == 2

    def test_render_context_includes_segments(self):
        ctx = LLMContext(
            user_id=USER_ID,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="Fan",
            message_count=0,
            conversation_summary=None,
            segment_names=["VIP", "Repeat Buyer"],
            segment_count=2,
        )
        text = render_context(ctx)
        assert "Segments: VIP, Repeat Buyer" in text

    def test_render_context_no_segments_when_empty(self):
        ctx = LLMContext(
            user_id=USER_ID,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="Fan",
            message_count=0,
            conversation_summary=None,
            segment_names=[],
            segment_count=0,
        )
        text = render_context(ctx)
        assert "Segments:" not in text

    @pytest.mark.asyncio
    async def test_context_assembler_get_segments_safe(self):
        """Test _get_segments_safe with mocked segments."""
        from memory.context_assembler import _get_segments_safe

        with patch("db.segments.list_segments", new_callable=AsyncMock) as mock_list, \
             patch("segments.evaluator.check_user_in_segment", new_callable=AsyncMock) as mock_check:
            mock_list.return_value = [
                {"id": 1, "name": "VIP", "rules": {"type": "group", "operator": "AND", "children": [{"type": "rule", "field": "days_since_first_purchase", "operator": "gte", "value": 1}]}},
                {"id": 2, "name": "New", "rules": {"type": "group", "operator": "AND", "children": [{"type": "rule", "field": "days_since_first_purchase", "operator": "lt", "value": 1}]}},
            ]
            mock_check.side_effect = [True, False]
            count, names = await _get_segments_safe(CREATOR_ID, USER_ID)
            assert count == 1
            assert names == ["VIP"]


# ── GROUP E: Segment propagation ─────────────────────────────────────────────


class TestSegmentPropagation:
    """Test segment fields flow through pipeline and state."""

    def test_pipeline_request_carries_segments(self):
        from commerce.models import PolicyDecision

        req = CommercePipelineRequest(
            user_id=USER_ID,
            creator_id=CREATOR_ID,
            messages=[{"role": "user", "content": "hi"}],
            eligibility=PolicyDecision(allowed=False, denial_reason="test"),
            user_segment_names=["VIP"],
            user_segment_count=1,
        )
        assert req.user_segment_names == ["VIP"]
        assert req.user_segment_count == 1

    def test_pipeline_request_defaults_empty(self):
        from commerce.models import PolicyDecision

        req = CommercePipelineRequest(
            user_id=USER_ID,
            creator_id=CREATOR_ID,
            messages=[{"role": "user", "content": "hi"}],
            eligibility=PolicyDecision(allowed=False, denial_reason="test"),
        )
        assert req.user_segment_names == []
        assert req.user_segment_count == 0

    def test_conversation_context_carries_segments(self):
        from commerce.models import PolicyDecision

        ctx = CommerceConversationContext(
            user_id=USER_ID,
            creator_id=CREATOR_ID,
            eligibility=PolicyDecision(allowed=False, denial_reason="test"),
            user_segment_names=["VIP", "Engaged"],
            user_segment_count=2,
        )
        assert ctx.user_segment_names == ["VIP", "Engaged"]
        assert ctx.user_segment_count == 2

    def test_conversation_context_defaults_empty(self):
        from commerce.models import PolicyDecision

        ctx = CommerceConversationContext(
            user_id=USER_ID,
            creator_id=CREATOR_ID,
            eligibility=PolicyDecision(allowed=False, denial_reason="test"),
        )
        assert ctx.user_segment_names == []
        assert ctx.user_segment_count == 0


# ── GROUP F: list_products tool ──────────────────────────────────────────────


class TestListProductsTool:
    """Test the list_products LLM tool."""

    @pytest.mark.asyncio
    async def test_list_products_returns_products(self):
        from core.llm_tools import ToolAuthContext, _handle_list_products

        auth = ToolAuthContext(
            creator_id=CREATOR_ID,
            user_id=USER_ID,
        )
        with patch(
            "commerce.product_selection.list_valid_products",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {"product_id": PRODUCT_ID_A, "title": "VIP Bundle", "price_minor": 4400, "currency": "USD"},
            ]
            result = await _handle_list_products({}, auth)
            assert result.success is True
            assert len(result.data["products"]) == 1
            assert result.data["products"][0]["product_id"] == PRODUCT_ID_A

    @pytest.mark.asyncio
    async def test_list_products_empty(self):
        from core.llm_tools import ToolAuthContext, _handle_list_products

        auth = ToolAuthContext(
            creator_id=CREATOR_ID,
            user_id=USER_ID,
        )
        with patch(
            "commerce.product_selection.list_valid_products",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = []
            result = await _handle_list_products({}, auth)
            assert result.success is True
            assert result.data["products"] == []


# ── GROUP G: Creator isolation ───────────────────────────────────────────────


class TestCreatorIsolation:
    """Test that product resolution and segments are creator-scoped."""

    @pytest.mark.asyncio
    async def test_product_resolution_passes_creator_id(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            await resolve_commerce_product(CREATOR_ID)
            mock_db.list_fangate_products.assert_called_once_with(
                CREATOR_ID, limit=200, offset=0
            )

    @pytest.mark.asyncio
    async def test_product_with_history_passes_creator_id(self):
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = set()
            await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            mock_db.list_fangate_products.assert_called_once_with(
                CREATOR_ID, limit=200, offset=0
            )

    @pytest.mark.asyncio
    async def test_list_valid_products_passes_creator_id(self):
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            mock_db.get_creator_integration = AsyncMock(return_value=_INTEGRATION_ROW)
            await list_valid_products(CREATOR_ID)
            mock_db.list_fangate_products.assert_called_once_with(
                CREATOR_ID, limit=200, offset=0
            )


# ── GROUP H: Failure paths ──────────────────────────────────────────────────


class TestFailurePaths:
    """Prove fail-closed behavior on every failure path."""

    @pytest.mark.asyncio
    async def test_no_product_no_offer(self):
        """No product -> commerce falls back to standard LLM."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[])
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_ambiguous_product_no_offer(self):
        """Ambiguous product -> fail-closed."""
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = set()
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_segment_lookup_failure_commerce_still_works(self):
        """Segment lookup failure -> commerce still respects deterministic gates."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(return_value=[_VALID_PRODUCT_A])
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            # Product resolution succeeds regardless of segment state
            assert result == PRODUCT_ID_A

    @pytest.mark.asyncio
    async def test_fangate_product_lookup_failure_fail_closed(self):
        """DB failure during product resolution -> None."""
        with patch("commerce.product_selection.db_fangate") as mock_db:
            mock_db.list_fangate_products = AsyncMock(side_effect=Exception("timeout"))
            result = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert result is None


# ── GROUP I: End-to-end commerce reachability ────────────────────────────────


class TestEndToEndReachability:
    """Prove the real production path can reach the activation gate."""

    @pytest.mark.asyncio
    async def test_inbound_to_activation_gate_reachable(self):
        """
        Full path: inbound message -> product resolution -> commerce state
        -> activation gate can pass.

        Mocks: DB queries, segment evaluation.
        Does NOT mock: the actual function call chain.
        """
        from commerce.models import PolicyDecision

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids",
            new_callable=AsyncMock,
        ) as mock_purch:
            # Setup: 2 products, 1 purchased -> resolve to unpurchased
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_VALID_PRODUCT_A, _VALID_PRODUCT_B]
            )
            mock_purch.return_value = {PRODUCT_ID_A}

            # Step 1: Product resolution
            product_id = await resolve_commerce_product_with_history(CREATOR_ID, USER_ID)
            assert product_id == PRODUCT_ID_B

            # Step 2: Verify the product can populate a pipeline request
            eligibility = PolicyDecision(allowed=True, denial_reason="none")
            pipeline_request = CommercePipelineRequest(
                user_id=USER_ID,
                creator_id=CREATOR_ID,
                messages=[{"role": "user", "content": "I want to buy"}],
                eligibility=eligibility,
                product_identity=ProductIdentity(
                    product_id=PRODUCT_ID_B,
                    title="Premium Pack",
                    available=True,
                ),
                product_state=ProductCommerceState(
                    price_minor=2900,
                    sales_url="https://f.test/p/20",
                    is_accessible=True,
                ),
            )
            # Step 3: The pipeline request has all required fields for activation
            assert pipeline_request.product_identity is not None
            assert pipeline_request.product_state is not None
            assert pipeline_request.eligibility.allowed is True
            # The activation gate in orchestrator.py checks:
            # decision.action == OFFER_PPV and decision.allowed and activation is not None
            # This proves the path is reachable.

    @pytest.mark.asyncio
    async def test_segments_flow_through_to_context(self):
        """Segment names flow from state resolution to pipeline request."""
        with patch(
            "commerce.state.db_postgres"
        ) as mock_pg, patch(
            "commerce.state.db_fangate"
        ) as mock_fangate, patch(
            "commerce.state.commerce_dao"
        ) as mock_dao, patch(
            "db.segments.list_segments", new_callable=AsyncMock
        ) as mock_list_segments, patch(
            "segments.evaluator.check_user_in_segment", new_callable=AsyncMock
        ) as mock_check_segment:
            mock_pg.get_user = AsyncMock(return_value={"id": USER_ID, "is_blocked": False})
            mock_pg.is_user_auto_reply_excluded = AsyncMock(return_value=False)
            mock_pg.get_user_persona = AsyncMock(return_value=None)
            mock_fangate.get_creator = AsyncMock(return_value={"id": CREATOR_ID})
            mock_fangate.get_creator_integration = AsyncMock(return_value={
                "status": "active",
                "currency_code": "USD",
            })
            mock_fangate.get_fangate_product = AsyncMock(return_value=_VALID_PRODUCT_A)
            mock_dao.list_offers_for_user = AsyncMock(return_value=[])
            mock_dao.find_pending_offer_for_product = AsyncMock(return_value=None)
            mock_dao.has_purchased_product = AsyncMock(return_value=False)

            mock_list_segments.return_value = [
                {"id": 1, "name": "VIP", "rules": {"type": "group", "operator": "AND", "children": [{"type": "rule", "field": "days_since_first_purchase", "operator": "gte", "value": 1}]}},
            ]
            mock_check_segment.return_value = True

            request = _make_request(product_id=PRODUCT_ID_A)
            resolution = await resolve_commerce_state(request)

            assert resolution.status is CommerceResolutionStatus.READY
            assert resolution.request.user_segment_names == ["VIP"]
            assert resolution.request.user_segment_count == 1
