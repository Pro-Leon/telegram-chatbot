"""Phase 14 enterprise tests."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
pytestmark = [pytest.mark.unit]

class TestFanMemory:
    @pytest.mark.asyncio
    async def test_explicit_beats_inference(self):
        from commerce.fan_memory import EXPLICIT, WEAK_INFERENCE
        assert EXPLICIT > WEAK_INFERENCE

    @pytest.mark.asyncio
    async def test_recent_beats_stale(self):
        from commerce.fan_memory import decay_preference
        assert decay_preference(1.0, 1) > decay_preference(1.0, 30)

    @pytest.mark.asyncio
    async def test_creator_isolation(self):
        from unittest.mock import AsyncMock, patch
        with patch("db.postgres.get_user_profile", new=AsyncMock(return_value={"commercial_preferences_by_creator": {"1": {"red": {"value": "red"}}, "2": {}}})):
            from db.postgres import get_commercial_preferences
            prefs1 = await get_commercial_preferences(1, 42)
            prefs2 = await get_commercial_preferences(2, 42)
            assert "red" in prefs1
            assert "red" not in prefs2

class TestProductKnowledge:
    def test_correct_product_retrieved(self):
        from commerce.product_knowledge import build_product_knowledge
        p = {"id": 1, "title": "Red Lace - Bedroom - 6 Photo Bundle", "price_minor": 2000, "is_accessible": True, "sales_url": "https://example.com"}
        k = build_product_knowledge(p, 1, set())
        assert k.subject == "red lace"
        assert "red lace" in k.bundle_group and "bedroom" in k.bundle_group

    def test_unknown_title_remains_unknown(self):
        from commerce.product_knowledge import build_product_knowledge
        p = {"id": 1, "title": "IMG_4829", "price_minor": 2000, "is_accessible": True, "sales_url": "https://example.com"}
        k = build_product_knowledge(p, 1, set())
        assert k.subject == "img_4829" or k.media_count is None

class TestObjection:
    def test_price_objection(self):
        from commerce.objection import classify_objection
        assert classify_objection("too expensive") == "price"

    def test_timing_objection(self):
        from commerce.objection import classify_objection
        assert classify_objection("maybe later") == "timing"

class TestNextBestAction:
    def test_present_offer(self):
        from commerce.next_best_action import derive_next_best_action
        a = derive_next_best_action("offer_ready", "hot", "open", "ready", False, "none")
        assert a.value == "present_offer"

    def test_aftercare(self):
        from commerce.next_best_action import derive_next_best_action
        a = derive_next_best_action("aftercare", "warm", "aftercare", "not_ready", False, "pending")
        assert a.value == "aftercare"

class TestGrounding:
    @pytest.mark.asyncio
    async def test_authorized_price_allowed(self):
        from core.scoring import score_draft
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft("Red Lace for $20 https://www.dropfans.io/buy/abc", "how much?", [], is_authorized_commerce=True, authorized_price_minor=2000)
            assert score >= 0.8

    @pytest.mark.asyncio
    async def test_unauthorized_price_blocked(self):
        from core.scoring import score_draft
        mock_provider = AsyncMock()
        mock_provider.generate.return_value = "{\"contextually_aware\": 9, \"natural_tone\": 9, \"appropriate_length\": 9, \"not_repetitive\": 9, \"flags\": []}"
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft("It is $999", "hi", [], is_authorized_commerce=False)
            assert "price_mention" in flags

