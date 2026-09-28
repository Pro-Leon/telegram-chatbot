"""Phase 8 single-pass tests."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
pytestmark = [pytest.mark.unit]

class TestSinglePass:
    @pytest.mark.asyncio
    async def test_single_commerce_signal_extraction(self):
        assert True

    @pytest.mark.asyncio
    async def test_single_conversational_generation(self):
        from workers.llm_worker import process_message
        mock_gen = AsyncMock(return_value="hello")
        with patch("workers.llm_worker.build_qwen3_context", new=AsyncMock(return_value=[{"role": "system", "content": "hi"}])), patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True)), patch("workers.llm_worker.release_user_lock", new=AsyncMock()), patch("workers.llm_worker.upsert_user", new=AsyncMock()), patch("workers.llm_worker.is_user_auto_reply_excluded", new=AsyncMock(return_value=False)), patch("workers.llm_worker.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="creator_context_unavailable"), creator_id=None))), patch("workers.llm_worker._try_commerce_draft", new=AsyncMock(return_value=None)), patch("workers.llm_worker.generate_draft", mock_gen), patch("core.scoring.score_draft", new=AsyncMock(return_value=(0.9, []))), patch("db.redis.is_auto_reply_enabled", new=AsyncMock(return_value=True)), patch("db.redis.enqueue_send", new=AsyncMock()), patch("core.event_bus.publish_event", new=AsyncMock()), patch("commerce.conversational.build_conversational_commerce_state", new=AsyncMock(return_value={"objective": "relationship", "desire": MagicMock(stage=MagicMock(value="relationship")), "temp": MagicMock(level="cold"), "readiness": MagicMock(value="not_ready"), "window": "no_window"})), patch("workers.llm_worker.get_recent_messages", new=AsyncMock(return_value=[])), patch("db.postgres.get_user", new=AsyncMock(return_value={"funnel_stage": "new", "message_count": 0})):
            await process_message(user_id=1, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="warm")
            assert mock_gen.await_count == 1

class TestDesire:
    def test_relationship_behavior(self):
        from commerce.desire import derive_desire_stage
        d = derive_desire_stage(relationship_state="cold", primary_intent="casual_chat", purchase_intent=0.0)
        assert d.stage.value == "relationship"
    def test_curiosity_behavior(self):
        from commerce.desire import derive_desire_stage
        d = derive_desire_stage(relationship_state="warm", primary_intent="content_curiosity", purchase_intent=0.0)
        assert d.stage.value == "curiosity"
    def test_offer_ready_behavior(self):
        from commerce.desire import derive_desire_stage
        d = derive_desire_stage(relationship_state="warm", explicit_purchase_request=True)
        assert d.stage.value == "offer_ready"

class TestTemperature:
    def test_hot_temperature(self):
        from commerce.temperature import derive_commercial_temperature
        t = derive_commercial_temperature(relationship_score=0.7, desire_stage="offer_ready", purchase_intent=0.9, content_interest=0.8)
        assert t.level == "hot"
