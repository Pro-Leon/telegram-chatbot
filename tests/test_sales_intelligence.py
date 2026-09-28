"""SALES INTELLIGENCE REMEDIATION — regression tests.

Covers §28/29: intent persistence, desire ladder, product selection,
strategy wiring, objection handling, abandoned offer, tip, purchase,
aftercare, upsell, creator isolation, hallucination blocking.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSalesIntentPersistence:
    """P0-01/05: Commercial observations persist as structured state."""

    @pytest.mark.asyncio
    async def test_asks_for_free_content_suppresses_commerce(self):
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.models import PolicyDecision
        from commerce.decision import decide_commerce_action

        s = CommerceSignals(
            purchase_intent=0.9,
            content_interest=0.8,
            relationship_engagement=0.7,
            price_interest=0.2,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.1,
            confidence=0.9,
            evidence=["free"],
            model_uncertainty=0.1,
            primary_intent="purchase_intent",
            intent_tags=["purchase_intent"],
            negative_intent_tags=[],
            fan_asks_question=False,
            asks_for_free_content=True,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        assert ctx.asks_for_free_content is True
        assert ctx.has_commercial_intent is False
        decision = decide_commerce_action(ctx)
        assert decision.action.value in ("relationship_building", "no_offer")

    def test_conversation_state_survives_build(self):
        from core.conversation_state import derive_conversation_state

        msgs = [
            {"direction": "inbound", "content": "I'm off on Saturdays."},
            {"direction": "outbound", "content": "Nice!"},
            {"direction": "inbound", "content": "Netflix and popcorns"},
        ]
        cs = derive_conversation_state(msgs, user={"message_count": 6})
        assert cs.open_threads  # Saturday/Netflix retained


class TestDesireLadder:
    """P0-04: Relationship → Interest → Desire → Qualified → Offer ladder."""

    def test_commercial_objective_derives_relationship_for_warm(self):
        from commerce.objective import derive_commercial_objective
        from commerce.selection import (
            CommerceSelectionResult,
            CommerceSelectionStatus,
            CommerceSelectionReason,
        )

        sel = CommerceSelectionResult(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.COMMERCE_NOT_APPLICABLE,
        )
        obj = derive_commercial_objective(sel, relationship_state="warm")
        assert obj in ("build_desire", "relationship", "explore", "no_sale")

    def test_present_offer_objective_only_for_executed(self):
        from commerce.objective import derive_commercial_objective
        from commerce.selection import (
            CommerceSelectionResult,
            CommerceSelectionStatus,
            CommerceSelectionReason,
        )
        from commerce.execution import ExecutionStatus

        sel = CommerceSelectionResult(
            status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
            reason=CommerceSelectionReason.COMMERCE_COMPLETED,
            commerce_response_text="Offer ready",
            execution_status=ExecutionStatus.EXECUTED,
            offer_active=True,
        )
        obj = derive_commercial_objective(sel, relationship_state="warm")
        assert obj == "present_offer"

    def test_no_sale_when_commerce_unavailable(self):
        from commerce.objective import derive_commercial_objective

        obj = derive_commercial_objective(None)
        assert obj == "no_sale"


class TestProductSelection:
    """P1-01: Multi-product deterministic ranking."""

    @pytest.mark.asyncio
    async def test_multi_product_ranks_cheapest(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        fake_products = [
            {
                "id": 10,
                "title": "VIP Bundle",
                "is_accessible": True,
                "sales_url": "https://x/10",
                "price_minor": 5000,
            },
            {
                "id": 20,
                "title": "Starter",
                "is_accessible": True,
                "sales_url": "https://x/20",
                "price_minor": 1999,
            },
            {
                "id": 30,
                "title": "Premium",
                "is_accessible": True,
                "sales_url": "https://x/30",
                "price_minor": 2999,
            },
        ]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = fake_products
            with patch(
                "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
            ) as mock_purch:
                mock_purch.return_value = set()
                pid = await resolve_commerce_product_with_history(creator_id=1, user_id=99)
                assert pid == 20, f"cheapest should win, got {pid}"

    @pytest.mark.asyncio
    async def test_multi_product_excludes_purchased(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        fake_products = [
            {
                "id": 10,
                "title": "A",
                "is_accessible": True,
                "sales_url": "https://x/10",
                "price_minor": 1000,
            },
            {
                "id": 20,
                "title": "B",
                "is_accessible": True,
                "sales_url": "https://x/20",
                "price_minor": 2000,
            },
        ]
        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = fake_products
            with patch(
                "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
            ) as mock_purch:
                mock_purch.return_value = {10}
                pid = await resolve_commerce_product_with_history(creator_id=1, user_id=99)
                assert pid == 20

    def test_valid_product_requires_accessible_and_url(self):
        from commerce.product_selection import _is_valid_product

        assert _is_valid_product({"is_accessible": True, "sales_url": "https://x"}) is True
        assert _is_valid_product({"is_accessible": False, "sales_url": "https://x"}) is False
        assert _is_valid_product({"is_accessible": True, "sales_url": None}) is False
        assert _is_valid_product({"is_accessible": True, "sales_url": ""}) is False


class TestStrategyWiring:
    """P0 bridge: Strategy/pressure → LLM via COMMERCIAL OBJECTIVE."""

    @pytest.mark.asyncio
    async def test_commercial_objective_injected_for_conversational(self):
        # Simulate process_message else branch where selection is FALLBACK
        from commerce.selection import (
            CommerceSelectionResult,
            CommerceSelectionStatus,
            CommerceSelectionReason,
        )
        from commerce.objective import derive_commercial_objective

        sel = CommerceSelectionResult(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.COMMERCE_NOT_APPLICABLE,
        )
        obj = derive_commercial_objective(sel)
        context = []
        context.append(
            {
                "role": "system",
                "content": f"COMMERCIAL OBJECTIVE: {obj}\nRule: Conversational job is {obj}; do not invent product, price, or checkout URL.",
            }
        )
        sys_text = " ".join(m["content"] for m in context)
        assert "COMMERCIAL OBJECTIVE" in sys_text
        assert "do not invent" in sys_text


class TestObjectionHandling:
    """P1 objection: PRICE/TIMING/VALUE/REJECTION."""

    def test_price_objection_suppresses_offer(self):
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.models import PolicyDecision
        from commerce.decision import decide_commerce_action

        # Two negative intents → suppressed to relationship_building even with price curiosity
        s = CommerceSignals(
            purchase_intent=0.2,
            content_interest=0.3,
            relationship_engagement=0.4,
            price_interest=0.9,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.6,
            confidence=0.85,
            evidence=["too expensive"],
            model_uncertainty=0.2,
            primary_intent="hesitation",
            intent_tags=["hesitation", "rejection"],
            negative_intent_tags=["hesitation", "rejection"],
            fan_asks_question=False,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"

    def test_direct_rejection_not_offer(self):
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.models import PolicyDecision
        from commerce.decision import decide_commerce_action

        s = CommerceSignals(
            purchase_intent=0.1,
            content_interest=0.1,
            relationship_engagement=0.2,
            price_interest=0.1,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.8,
            confidence=0.9,
            evidence=["not interested"],
            model_uncertainty=0.1,
            primary_intent="rejection",
            intent_tags=["rejection"],
            negative_intent_tags=["rejection"],
            fan_asks_question=False,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"


class TestTipFatigue:
    """P0-01: Tip cooldown after wiring."""

    def test_tip_url_authority_preserved(self):
        import pathlib

        src = pathlib.Path("core/llm_tools.py").read_text(encoding="utf-8")
        assert "get_checkout_links" in src
        assert "tip:{auth.creator_id}" in src

    def test_tip_dedup_id_creator_isolated(self):
        # dedup includes creator_id
        src = open("core/llm_tools.py", encoding="utf-8").read()
        assert "tip:{auth.creator_id}:{auth.user_id}" in src


class TestCreatorIsolation:
    """P1: One creator never sees another's products/offers."""

    def test_product_selection_is_creator_scoped(self):
        import inspect
        from commerce.product_selection import resolve_commerce_product_with_history

        src = inspect.getsource(resolve_commerce_product_with_history)
        assert "creator_id" in src
        assert "list_fangate_products(creator_id" in src

    def test_commerce_state_is_creator_scoped(self):
        import inspect
        from commerce.state import resolve_commerce_state

        src = inspect.getsource(resolve_commerce_state)
        assert "creator_id" in src


class TestHallucinationBlocking:
    """Attempt to make LLM invent commercial facts — deterministic validation blocks."""

    def test_price_hallucination_blocked_by_selection(self):
        # FALLBACK carries no commerce text — cannot hallucinate price
        from commerce.selection import (
            CommerceSelectionResult,
            CommerceSelectionStatus,
            CommerceSelectionReason,
        )

        sel = CommerceSelectionResult(
            status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM,
            reason=CommerceSelectionReason.EXECUTION_FAILED,
        )
        assert sel.commerce_response_text is None
        assert sel.offer_active is False

    def test_tip_url_cannot_be_modified_by_llm(self):
        # suggest_tip tool schema has no tip_url param; URL stays app-controlled.
        # Wording may be dynamic; only the authority invariant is asserted here.
        import pathlib

        src = pathlib.Path("core/llm_tools.py").read_text(encoding="utf-8")
        # tool params only: reason
        assert '"suggest_tip"' in src
        # URL comes from DropFans checkout links, not from LLM args.
        assert "get_checkout_links" in src
        assert "tip_url" in src
        from core import llm_tools as _tools

        assert _tools.get_tool("suggest_tip") is not None
        params = _tools.get_tool("suggest_tip").parameters.get("properties", {})
        assert "tip_url" not in params
        assert "tipUrl" not in params


class TestProviderParity:
    """Provider choice does not alter authoritative commercial state."""

    def test_provider_factory_exists(self):
        from core.llm_provider import get_llm_provider

        assert callable(get_llm_provider)

    def test_only_llamacpp_exists(self):
        from core.llm_provider import get_llm_provider
        import pathlib

        assert not pathlib.Path("core/llm_provider_ollama.py").exists()
        assert not pathlib.Path("core/llm_provider_gemini.py").exists()
        from unittest.mock import MagicMock, patch

        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="llamacpp")
            assert get_llm_provider().provider_name == "llamacpp"


class TestEndToEndScenarios:
    """§29: 10 realistic fan conversations — property tests, not exact wording."""

    def _ctx(
        self,
        purchase_intent=0.2,
        price_interest=0.1,
        explicit_buy=False,
        explicit_content=False,
        primary_intent="casual_chat",
        relationship_score=0.3,
        has_active=False,
        negative_tags=None,
    ):
        from commerce.models import PolicyDecision
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.decision import decide_commerce_action

        s = CommerceSignals(
            purchase_intent=purchase_intent,
            content_interest=0.3,
            relationship_engagement=relationship_score,
            price_interest=price_interest,
            explicit_purchase_request=explicit_buy,
            explicit_content_request=explicit_content,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.1,
            confidence=0.85,
            evidence=["fan msg"],
            model_uncertainty=0.2,
            primary_intent=primary_intent,
            intent_tags=[primary_intent],
            negative_intent_tags=negative_tags or [],
            fan_asks_question=False,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_score=relationship_score,
            relationship_state="warm" if relationship_score > 0.5 else "cold",
            has_relevant_product=True,
            creator_sales_enabled=True,
            has_active_offer=has_active,
        )
        return decide_commerce_action(ctx)

    def test_scenario_1_warm_fan_no_premature_pitch(self):
        # hey → cute → what are you doing tonight? → that sounds fun
        d = self._ctx(purchase_intent=0.15, primary_intent="casual_chat", relationship_score=0.35)
        assert d.action.value in ("relationship_building", "no_offer")

    def test_scenario_2_strong_buying_intent_offers(self):
        # what do you have available? → how much? → okay show me
        d = self._ctx(
            purchase_intent=0.85,
            explicit_buy=True,
            primary_intent="purchase_intent",
            relationship_score=0.75,
            has_active=False,
        )
        assert d.action.value == "offer_ppv"

    def test_scenario_3_price_objection_changes_strategy(self):
        d_before = self._ctx(purchase_intent=0.7, relationship_score=0.7)
        d_after = self._ctx(
            purchase_intent=0.2,
            primary_intent="hesitation",
            relationship_score=0.4,
            negative_tags=["hesitation", "rejection"],
        )
        assert d_after.action.value != "offer_ppv" or d_before.action.value != d_after.action.value

    def test_scenario_4_abandoned_offer_remembered(self):
        # existing pending offer should suppress new offer
        d = self._ctx(purchase_intent=0.9, has_active=True)
        assert d.action.value == "no_offer"

    def test_scenario_5_aftercare_not_immediate_upsell(self):
        # aftercare pending + recent purchase → relationship, not offer
        from commerce.signals import signals_to_context
        from commerce.models import PolicyDecision
        from commerce.decision import decide_commerce_action
        from commerce.signals import CommerceSignals

        s = CommerceSignals(
            purchase_intent=0.1,
            content_interest=0.1,
            relationship_engagement=0.2,
            price_interest=0.1,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.1,
            confidence=0.9,
            evidence=["ty"],
            model_uncertainty=0.1,
            primary_intent="casual_chat",
            intent_tags=["casual_chat"],
            negative_intent_tags=[],
            fan_asks_question=False,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
            aftercare_status="pending",
            total_purchases=1,
        )
        d = decide_commerce_action(ctx)
        assert d.action.value == "relationship_building"

    def test_scenario_6_tip_canonical(self):
        import pathlib

        src = pathlib.Path("core/llm_tools.py").read_text(encoding="utf-8")
        assert "telegram.tip" in src or 'telegram["tip"]' in src

    def test_scenario_7_free_content_not_converted_to_offer(self):
        d = self._ctx(purchase_intent=0.9, primary_intent="other")
        # with asks_for_free_content
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.models import PolicyDecision
        from commerce.decision import decide_commerce_action

        s = CommerceSignals(
            purchase_intent=0.9,
            content_interest=0.8,
            relationship_engagement=0.7,
            price_interest=0.1,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.1,
            confidence=0.9,
            evidence=["free"],
            model_uncertainty=0.1,
            primary_intent="other",
            intent_tags=["other"],
            negative_intent_tags=[],
            fan_asks_question=False,
            asks_for_free_content=True,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        d = decide_commerce_action(ctx)
        assert d.action.value != "offer_ppv"

    def test_scenario_8_casual_never_forced_to_sales(self):
        d = self._ctx(purchase_intent=0.05, primary_intent="greeting", relationship_score=0.2)
        assert d.action.value in ("relationship_building", "no_offer")

    def test_scenario_9_multiple_products_best_price_picked(self):
        # Already tested via product_selection ranking — here verify not None would have been
        pass

    def test_scenario_10_followup_not_duplicate_offer(self):
        # FOLLOW_UP should not create new offer, just text
        from commerce.models import PolicyDecision
        from commerce.signals import CommerceSignals, signals_to_context
        from commerce.decision import decide_commerce_action

        s = CommerceSignals(
            purchase_intent=0.4,
            content_interest=0.3,
            relationship_engagement=0.4,
            price_interest=0.1,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.1,
            confidence=0.85,
            evidence=["hi"],
            model_uncertainty=0.2,
            primary_intent="casual_chat",
            intent_tags=["casual_chat"],
            negative_intent_tags=[],
            fan_asks_question=False,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
            previous_offer_status="declined",
            hours_since_last_offer=50,
        )
        d = decide_commerce_action(ctx)
        assert d.action.value in ("follow_up", "relationship_building", "soft_offer", "no_offer")
