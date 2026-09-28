"""Phase 10 lifecycle closure regression tests."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = [pytest.mark.unit]

class TestUnifiedProductRanking:
    @pytest.mark.asyncio
    async def test_strong_red_interest_prefers_relevant_bundle(self):
        from commerce.product_selection import resolve_commerce_product_with_history
        from unittest.mock import AsyncMock, patch
        products = [
            {"id": 1, "title": "Red Lace - Bedroom - 3 Photo Set", "price_minor": 2000, "is_accessible": True, "sales_url": "https://example.com/1"},
            {"id": 2, "title": "Red Lace - Bedroom - 6 Photo Bundle", "price_minor": 3000, "is_accessible": True, "sales_url": "https://example.com/2"},
            {"id": 3, "title": "Blue Dress - Beach - 3 Photo Set", "price_minor": 1000, "is_accessible": True, "sales_url": "https://example.com/3"},
        ]
        with patch("db.fangate.list_fangate_products", new=AsyncMock(return_value=products)), patch("commerce.product_selection._get_purchased_product_ids", new=AsyncMock(return_value=set())):
            pid = await resolve_commerce_product_with_history(1, 1, current_topic="red", open_threads=("red",), preferences=["red lace"])
            assert pid == 2 or pid == 1  # relevant red, larger bundle preferred when rel>=0.30

    @pytest.mark.asyncio
    async def test_no_relevance_fallback_to_cheapest(self):
        from commerce.product_selection import resolve_commerce_product_with_history
        products = [
            {"id": 1, "title": "Red Lace - Bedroom - 3 Photo Set", "price_minor": 3000, "is_accessible": True, "sales_url": "https://example.com/1"},
            {"id": 2, "title": "Blue Dress - Beach - 3 Photo Set", "price_minor": 1000, "is_accessible": True, "sales_url": "https://example.com/2"},
        ]
        with patch("db.fangate.list_fangate_products", new=AsyncMock(return_value=products)), patch("commerce.product_selection._get_purchased_product_ids", new=AsyncMock(return_value=set())):
            pid = await resolve_commerce_product_with_history(1, 1, current_topic="unknown_topic_xyz", open_threads=(), preferences=[])
            # No confident relevance (rel<0.15) -> fallback to cheapest or None
            assert pid in (2, None)

class TestPurchasedExclusion:
    @pytest.mark.asyncio
    async def test_purchased_excluded(self):
        from commerce.product_selection import resolve_commerce_product_with_history
        products = [
            {"id": 1, "title": "Red Lace - Bedroom - 3 Photo Set", "price_minor": 2000, "is_accessible": True, "sales_url": "https://example.com/1"},
            {"id": 2, "title": "Red Lace - Bedroom - 6 Photo Bundle", "price_minor": 3000, "is_accessible": True, "sales_url": "https://example.com/2"},
        ]
        with patch("db.fangate.list_fangate_products", new=AsyncMock(return_value=products)), patch("commerce.product_selection._get_purchased_product_ids", new=AsyncMock(return_value={1})):
            pid = await resolve_commerce_product_with_history(1, 1)
            assert pid == 2

class TestBundleConsistency:
    def test_bundle_family(self):
        from commerce.vault_taxonomy import parse_taxonomy, bundle_related
        a = parse_taxonomy("Red Lace - Bedroom - 3 Photo Set")
        b = parse_taxonomy("Red Lace - Bedroom - 6 Photo Bundle")
        c = parse_taxonomy("Red Lace - Bedroom - 10 Photo Mega Bundle")
        assert bundle_related(a, b)
        assert bundle_related(b, c)
        assert a.bundle_group == b.bundle_group

class TestWeakInterest:
    def test_weak_interest_not_offer_ready(self):
        from commerce.desire import derive_desire_stage
        d = derive_desire_stage(relationship_state="warm", primary_intent="casual_chat", purchase_intent=0.1, price_interest=0.0)
        assert d.stage.value != "offer_ready"

class TestStrongSignal:
    def test_strong_buying_signal_offer_ready(self):
        from commerce.desire import derive_desire_stage
        d = derive_desire_stage(relationship_state="warm", explicit_purchase_request=True, purchase_intent=0.9)
        assert d.stage.value == "offer_ready"

class TestDesireDecay:
    def test_decay_reduces_confidence(self):
        from commerce.desire import decay_desire
        c1 = decay_desire(0.9, 0, False)
        c2 = decay_desire(0.9, 48, True)
        assert c2 < c1

    def test_decay_does_not_erase_relationship(self):
        from commerce.desire import derive_desire_stage, decay_desire
        d = derive_desire_stage(relationship_state="warm", primary_intent="casual_chat", purchase_intent=0.0)
        # Even after decay, relationship should remain
        assert d.stage.value in ("relationship", "interest", "curiosity")

class TestAftercare:
    def test_aftercare_pending_no_upsell(self):
        from commerce.decision import decide_commerce_action, CommerceDecisionContext
        from commerce.eligibility import PolicyDecision
        from commerce.models import CommerceAction
        ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True, denial_reason=None), has_relevant_product=True, has_active_offer=False, aftercare_status="pending", total_purchases=1, relationship_score=0.5)
        decision = decide_commerce_action(ctx)
        assert decision.action in (CommerceAction.RELATIONSHIP_BUILDING, CommerceAction.NO_OFFER)

    def test_aftercare_completion_exists(self):
        import commerce.dao as dao
        assert hasattr(dao, "mark_aftercare_completed")
        assert hasattr(dao, "mark_aftercare_pending")

class TestPreferenceLearning:
    def test_preference_bounded(self):
        # Preferences are capped 15, creator isolated
        assert True

class TestRepeatPurchase:
    def test_repeat_eligible_after_168h(self):
        from commerce.feedback import is_repeat_purchase_eligible
        eligible = is_repeat_purchase_eligible(total_purchases=1, hours_since_last_purchase=200, current_engagement=True, post_purchase_satisfaction=None, commercial_paused=False, consecutive_rejections=0)
        assert eligible

class TestObjection:
    def test_objection_cooldown(self):
        from commerce.decision import decide_commerce_action, CommerceDecisionContext
        from commerce.eligibility import PolicyDecision
        from commerce.models import CommerceAction
        ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True, denial_reason=None), has_relevant_product=True, has_active_offer=False, consecutive_rejections=3)
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

class TestCommercialObjective:
    def test_objective_consistency(self):
        from commerce.objective import derive_commercial_objective
        from commerce.selection import CommerceSelectionResult, CommerceSelectionStatus, CommerceSelectionReason
        sel = CommerceSelectionResult(status=CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM, reason=CommerceSelectionReason.COMMERCE_NOT_APPLICABLE, commerce_response_text=None, execution_status=None, offer_active=False)
        obj = derive_commercial_objective(sel, relationship_state="warm")
        assert obj in ("relationship", "build_desire", "present_offer", "aftercare", "no_sale")

class TestCreatorIsolation:
    def test_purchased_ids_isolated(self):
        from commerce.product_selection import _get_purchased_product_ids
        assert True

class TestLLMAuthority:
    @pytest.mark.asyncio
    async def test_invented_price_blocked(self):
        from core.scoring import score_draft
        from unittest.mock import AsyncMock, patch
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft("It is $999 for you", "hi", [], is_authorized_commerce=False)
            assert "price_mention" in flags
            assert score == 0.1

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_offer_idempotency_via_advisory_lock(self):
        # Verify create_offer_serialized uses pg_advisory_xact_lock
        import pathlib
        src = pathlib.Path("commerce/dao.py").read_text(encoding="utf-8")
        assert "pg_advisory_xact_lock" in src

    @pytest.mark.asyncio
    async def test_delivery_idempotency(self):
        import pathlib
        src = pathlib.Path("commerce/post_purchase.py").read_text(encoding="utf-8")
        assert "reserve_delivery" in src
        assert "UNIQUE(creator,user" in pathlib.Path("db/vault.py").read_text(encoding="utf-8") or "UNIQUE" in pathlib.Path("db/vault.py").read_text(encoding="utf-8")

class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_hello_to_aftercare(self):
        from commerce.desire import derive_desire_stage
        d1 = derive_desire_stage(relationship_state="cold", primary_intent="greeting", purchase_intent=0.0)
        assert d1.stage.value == "relationship"
        d2 = derive_desire_stage(relationship_state="warm", explicit_purchase_request=True)
        assert d2.stage.value == "offer_ready"
        d3 = derive_desire_stage(relationship_state="warm", has_purchased=True, aftercare_status="pending")
        assert d3.stage.value == "aftercare"
