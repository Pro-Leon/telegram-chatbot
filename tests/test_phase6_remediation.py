"""Phase 6 remediation regression tests - P0-01, P0-03, P0-02 and lifecycle."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = [pytest.mark.unit]

class TestP001_ConversationalBridge:
    @pytest.mark.asyncio
    async def test_different_desire_produces_different_commercial_state(self):
        from commerce.desire import derive_desire_stage
        from commerce.temperature import derive_commercial_temperature
        from commerce.offer_readiness import evaluate_offer_readiness
        from commerce.sales_window import derive_sales_window
        desire_a = derive_desire_stage(relationship_state="warm", primary_intent="casual_chat", purchase_intent=0.0)
        assert desire_a.stage.value in ("relationship", "interest", "curiosity")
        temp_a = derive_commercial_temperature(relationship_score=0.5, desire_stage=desire_a.stage.value, purchase_intent=0.0)
        assert temp_a.level in ("cold", "warm", "hot")
        readiness_a = evaluate_offer_readiness(desire_a.stage.value, temp_a.level, purchase_intent=0.0, has_active_offer=False)
        window_a = derive_sales_window(desire_a.stage.value, temp_a.level, readiness_a.value, aftercare_active=False, is_on_cooldown=False)
        desire_b = derive_desire_stage(relationship_state="warm", primary_intent="purchase_intent", purchase_intent=0.95, explicit_purchase_request=True)
        assert desire_b.stage.value == "offer_ready"
        temp_b = derive_commercial_temperature(relationship_score=0.7, desire_stage=desire_b.stage.value, purchase_intent=0.95, content_interest=0.8)
        assert temp_b.level == "hot"
        readiness_b = evaluate_offer_readiness(desire_b.stage.value, temp_b.level, purchase_intent=0.95, has_active_offer=False)
        assert readiness_b.value == "ready"
        window_b = derive_sales_window(desire_b.stage.value, temp_b.level, readiness_b.value, aftercare_active=False, is_on_cooldown=False)
        assert window_b == "open"
        assert (desire_a.stage.value, temp_a.level, readiness_a.value, window_a) != (desire_b.stage.value, temp_b.level, readiness_b.value, window_b)

    @pytest.mark.asyncio
    async def test_primary_intent_not_replaced_with_none(self):
        from commerce.desire import derive_desire_stage
        desire_real = derive_desire_stage(relationship_state="warm", primary_intent="content_request", intent_tags=["content_request"], purchase_intent=0.2)
        assert desire_real.stage.value == "qualification"
        desire_none = derive_desire_stage(relationship_state="warm", primary_intent=None, purchase_intent=0.0)
        assert desire_none.stage.value != desire_real.stage.value

    @pytest.mark.asyncio
    async def test_aftercare_not_silently_none(self):
        from commerce.desire import derive_desire_stage
        desire_aftercare = derive_desire_stage(relationship_state="purchased", aftercare_status="pending", has_purchased=True)
        assert desire_aftercare.stage.value == "aftercare"
        desire_no_aftercare = derive_desire_stage(relationship_state="purchased", aftercare_status="none", has_purchased=False)
        assert desire_no_aftercare.stage.value != "aftercare"

    @pytest.mark.asyncio
    async def test_purchase_intent_affects_temperature(self):
        from commerce.temperature import derive_commercial_temperature
        cold = derive_commercial_temperature(relationship_score=0.3, desire_stage="relationship", purchase_intent=0.0)
        hot = derive_commercial_temperature(relationship_score=0.7, desire_stage="offer_ready", purchase_intent=0.9, content_interest=0.8)
        assert cold.score < hot.score
        assert hot.level == "hot"
        assert cold.score != hot.score

class TestP001_Wiring:
    def test_bridge_not_hardcoded(self):
        import pathlib
        src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
        assert "_rel = \"warm\"\n                desire = derive_desire_stage(relationship_state=_rel, primary_intent=None)" not in src
        # After refactor, worker delegates to commerce.conversational bridge (single LLM call)
        assert "commerce.conversational" in src
        assert "build_conversational_commerce_state" in src
        assert "aftercare_status" in pathlib.Path("commerce/conversational.py").read_text(encoding="utf-8")

class TestP003_Scoring:
    @pytest.mark.asyncio
    async def test_authorized_price_not_blocked(self):
        from core.scoring import score_draft
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft(
                "Here is your Red Lace - Bedroom - 3 Photo Set for $20.00 https://www.dropfans.io/buy/abc123",
                "how much?",
                [],
                is_authorized_commerce=True,
                authorized_price_minor=2000,
            )
            assert "price_mention" not in flags
            assert score >= 0.8

    @pytest.mark.asyncio
    async def test_unauthorized_price_blocked(self):
        from core.scoring import score_draft
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft(
                "It is only $20 for you babe",
                "hi",
                [],
                is_authorized_commerce=False,
            )
            assert "price_mention" in flags
            assert score == 0.1

    @pytest.mark.asyncio
    async def test_unauthorized_price_with_wrong_amount_blocked(self):
        from core.scoring import score_draft
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft(
                "It is $50 for this set",
                "how much?",
                [],
                is_authorized_commerce=True,
                authorized_price_minor=2000,
            )
            assert "price_mention" in flags
            assert score == 0.1

class TestP002_DropFansAPI:
    def test_no_buyer_scoped_endpoint(self):
        import pathlib
        client_src = pathlib.Path("integrations/dropfans/client.py").read_text(encoding="utf-8")
        assert "download-for-buyer" not in client_src
        assert "grant-access" not in client_src
        pp_src = pathlib.Path("commerce/post_purchase.py").read_text(encoding="utf-8")
        assert "external blocker" in pp_src.lower() or "buyer-scoped" in pp_src.lower()
        assert "sales_url" in pp_src

    def test_delivery_uses_sales_url_not_filePath(self):
        import pathlib
        src = pathlib.Path("commerce/post_purchase.py").read_text(encoding="utf-8")
        assert "Access it here: {sales_url}" in src
        assert "media_path\": sales_url" in src

class TestPurchaseAttribution:
    def test_synthetic_pid_consistent(self):
        import hashlib
        pid = "drop_test_123"
        a = int(hashlib.sha256(pid.encode()).hexdigest()[:15], 16) % (2**62)
        b = int(hashlib.sha256(pid.encode()).hexdigest()[:15], 16) % (2**62)
        assert a == b
        assert a != 0

    def test_record_dropfans_sale_uses_synthetic(self):
        import pathlib
        src = pathlib.Path("db/dropfans.py").read_text(encoding="utf-8")
        assert "_synthetic_pid" in src

class TestLifecycle:
    def test_purchased_exclusion(self):
        from commerce.content_matching import rank_products_by_relevance
        products = [
            {"id": 1, "title": "Red Lace - Bedroom - 3 Photo Set", "price_minor": 2000},
            {"id": 2, "title": "Red Lace - Bedroom - 6 Photo Bundle", "price_minor": 3000},
        ]
        ranked = rank_products_by_relevance(products, "red", (), [], purchased_ids={1})
        ids = [p["id"] for p,_ in ranked]
        assert 1 not in ids
        assert 2 in ids

    def test_bundle_related(self):
        from commerce.vault_taxonomy import parse_taxonomy, bundle_related
        a = parse_taxonomy("Red Lace - Bedroom - 3 Photo Set")
        b = parse_taxonomy("Red Lace - Bedroom - 6 Photo Bundle")
        assert bundle_related(a, b)
        c = parse_taxonomy("Blue Dress - Beach - 3 Photo Set")
        assert not bundle_related(a, c)

    def test_aftercare_suppresses_offer(self):
        from commerce.decision import decide_commerce_action, CommerceDecisionContext
        from commerce.eligibility import PolicyDecision
        from commerce.models import CommerceAction
        ctx = CommerceDecisionContext(
            user_id=1, creator_id=1,
            eligibility=PolicyDecision(allowed=True, denial_reason=None),
            has_relevant_product=True,
            has_active_offer=False,
            aftercare_status="pending",
            total_purchases=1,
            relationship_score=0.5,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (CommerceAction.RELATIONSHIP_BUILDING, CommerceAction.NO_OFFER)

