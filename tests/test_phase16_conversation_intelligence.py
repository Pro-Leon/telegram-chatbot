"""Phase 16 conversation intelligence tests."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
pytestmark = [pytest.mark.unit]

class TestConversationObjective:
    def test_relationship_build(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="relationship", temperature="cold", sales_window="no_window", offer_readiness="not_ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=False)
        assert obj.value in ("relationship_build", "continue_topic", "wait")
    def test_follow_up_open_loop(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="interest", temperature="warm", sales_window="building", offer_readiness="build_desire", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=True, has_open_loop=True, open_loop_importance=0.8)
        assert obj.value == "follow_up_open_loop"
    def test_present_offer(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="offer_ready", temperature="hot", sales_window="open", offer_readiness="ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=True, explicit_purchase_request=True)
        assert obj.value == "present_offer"
    def test_aftercare(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="aftercare", temperature="warm", sales_window="aftercare", offer_readiness="not_ready", has_active_offer=False, aftercare_status="pending", is_on_cooldown=False, has_relevant_product=True)
        assert obj.value == "aftercare"
    def test_direct_intent_wins(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="relationship", temperature="cold", sales_window="no_window", offer_readiness="not_ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=False, explicit_purchase_request=True)
        assert obj.value in ("present_offer", "qualify")

class TestPriority:
    def test_aftercare_beats_offer(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="offer_ready", temperature="hot", sales_window="open", offer_readiness="ready", has_active_offer=False, aftercare_status="pending", is_on_cooldown=False, has_relevant_product=True)
        assert obj.value == "aftercare"
    def test_objection_beats_offer(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj, cands = derive_conversation_objective(desire="offer_ready", temperature="hot", sales_window="open", offer_readiness="ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=True, has_relevant_product=True, has_objection=True)
        assert obj.value == "handle_objection"

class TestSinglePass:
    @pytest.mark.asyncio
    async def test_single_signal_extraction(self):
        from commerce.conversation_intelligence import derive_conversation_objective
        obj1, _ = derive_conversation_objective(desire="relationship", temperature="cold", sales_window="no_window", offer_readiness="not_ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=False)
        obj2, _ = derive_conversation_objective(desire="relationship", temperature="cold", sales_window="no_window", offer_readiness="not_ready", has_active_offer=False, aftercare_status="none", is_on_cooldown=False, has_relevant_product=False)
        assert obj1 == obj2
    @pytest.mark.asyncio
    async def test_single_conversational_generation(self):
        from workers.llm_worker import process_message
        mock_gen = AsyncMock(return_value="hello")
        with patch("workers.llm_worker.build_qwen3_context", new=AsyncMock(return_value=[{"role": "system", "content": "hi"}])), patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True)), patch("workers.llm_worker.release_user_lock", new=AsyncMock()), patch("workers.llm_worker.upsert_user", new=AsyncMock()), patch("workers.llm_worker.is_user_auto_reply_excluded", new=AsyncMock(return_value=False)), patch("workers.llm_worker.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="creator_context_unavailable"), creator_id=None))), patch("workers.llm_worker._try_commerce_draft", new=AsyncMock(return_value=None)), patch("workers.llm_worker.generate_draft", mock_gen), patch("core.scoring.score_draft", new=AsyncMock(return_value=(0.9, []))), patch("db.redis.is_auto_reply_enabled", new=AsyncMock(return_value=True)), patch("db.redis.enqueue_send", new=AsyncMock()), patch("core.event_bus.publish_event", new=AsyncMock()), patch("commerce.conversational.build_conversational_commerce_state", new=AsyncMock(return_value={"objective": "relationship", "desire": MagicMock(stage=MagicMock(value="relationship")), "temp": MagicMock(level="cold"), "readiness": MagicMock(value="not_ready"), "window": "no_window"})), patch("workers.llm_worker.get_recent_messages", new=AsyncMock(return_value=[])), patch("db.postgres.get_user", new=AsyncMock(return_value={"funnel_stage": "new", "message_count": 0})), patch("db.postgres.get_pool", new=AsyncMock()), patch("db.redis.get_redis", new=AsyncMock()):
            await process_message(user_id=1, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="warm")
            assert mock_gen.await_count == 1

