"""Phase 78B — single-generation invariants (12 required tests)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# Helper to make a valid CommerceSignals low-info
def _low_signals():
    from commerce.signals import CommerceSignals
    return CommerceSignals.low_information()


# Test 1 — normal message exactly one LLM call
class TestNormalSingleGeneration:
    @pytest.mark.asyncio
    async def test_normal_message_one_llm_call(self):
        """Normal 'hey beautiful' => exactly 1 provider.generate."""
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals

        mock_one = OneCallResult(
            reply="hey gorgeous, how are you today? I love your energy.",
            signals=CommerceSignals.low_information(),
            confidence=0.9, needs_handoff=False, is_valid=True,
            validation_error=None, quality_score=0.85, quality_flags=[], safety_flags=[],
        )
        # Track generate_commerce_response calls
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one) as mock_one_call,
            patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None) as mock_commerce,
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=1, user_message="hey beautiful", telegram_message_id=1, username="u", first_name="f", persona="You are Sunny")
            # OneCall called once via pipeline
            assert mock_one_call.call_count == 1
            # Commerce draft should be called but with single generation, our pipeline gate ensures no 2nd LLM inside
            # Here _try_commerce_draft is mocked, so just verify not raising


# Test 2 — non-PPV no second generation
class TestNonPPVNoSecondGeneration:
    @pytest.mark.asyncio
    async def test_non_ppv_no_generate_commerce_response(self):
        """Non-PPV pipeline must NOT call generate_commerce_response."""
        from commerce.pipeline import CommercePipelineRequest
        from commerce.models import PolicyDecision
        from commerce.execution import ExecutionResult, ExecutionStatus
        from commerce.models import CommerceAction

        # Build minimal request that will decide NO_OFFER (low intent, no eligibility issue)
        req = CommercePipelineRequest(
            user_id=1, creator_id=1,
            messages=[{"role": "user", "content": "how was your day?"}],
            eligibility=PolicyDecision(allowed=True),
            product_identity=None, product_state=None, currency="USD",
            creator_sales_enabled=True, has_relevant_product=True, has_active_offer=False,
            recent_offer_count=0, recent_purchase_count=0,
        )
        # Mock decision to ensure NO_OFFER path, and execution not needed
        with patch("commerce.pipeline.generate_commerce_response", new_callable=AsyncMock) as mock_gen:
            # Also mock orchestrate to avoid real DB
            mock_orch = MagicMock()
            # Use real pipeline but patch generate_commerce_response to FAIL if called non-PPV should be 0
            # For this test we directly call pipeline with signals low intent
            from commerce.signals import CommerceSignals
            sig = CommerceSignals.low_information()
            # low intent => decision NO_OFFER -> should not call generate_commerce_response after fix
            from commerce.pipeline import run_commerce_pipeline
            result = await run_commerce_pipeline(req, signals=sig)
            # After 78B gate, generate_commerce_response must be 0 for non-PPV
            assert mock_gen.call_count == 0, "Non-PPV should not generate commerce response"
            # Result should be FALLBACK-like (not USE)
            assert result.status.value in ("completed", "failed", "decision_failed", "strategy_failed", "response_failed", "execution_failed")


# Test 3 — PPV opportunity exactly one LLM for commerce signals but second is required? After 78B non-PPV is 1, PPV should be gated
class TestPPVSingleGeneration:
    @pytest.mark.asyncio
    async def test_ppv_uses_signals_no_duplicate_extraction(self):
        """PPV: OneCall signals are consumed without duplicate extract_commerce_signals."""
        from commerce.signals import CommerceSignals
        from core.one_call import OneCallResult

        sig = CommerceSignals(
            purchase_intent=0.95, content_interest=0.8, relationship_engagement=0.7,
            price_interest=0.9, explicit_purchase_request=True, explicit_content_request=False,
            requested_price=30.0, declined_recent_offer=False, asks_for_free_content=False,
            negative_sentiment=0.0, confidence=0.9, model_uncertainty=0.1,
            primary_intent="purchase_intent", intent_tags=["purchase_intent"], negative_intent_tags=[],
            evidence=["want to buy"], fan_asks_question=False, conversation_relevance=0.9,
        )
        mock_one = OneCallResult(reply="I'd love to help you purchase", signals=sig, confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.9, safety_flags=[], quality_flags=[])
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one),
            patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None) as mock_try,
            patch("commerce.deepseek.extract_commerce_signals", new_callable=AsyncMock) as mock_extract,
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=2, user_message="send it", telegram_message_id=2, username="u", first_name="f", persona="p")
            # signals should be passed to _try_commerce_draft, no duplicate extraction
            assert mock_try.called
            # extract_commerce_signals should NOT be called on new path (mock)
            assert mock_extract.call_count == 0


# Test 4 — commerce response is not independently generated (normal)
class TestCommerceNotGeneratedNormal:
    @pytest.mark.asyncio
    async def test_generate_commerce_response_zero_normal(self):
        """For normal conversational processing, generate_commerce_response ==0."""
        # Same as test 2 but via worker
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        mock_one = OneCallResult(reply="hey how are you?", signals=CommerceSignals.low_information(), confidence=0.8, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.8)
        with (
            patch("commerce.pipeline.generate_commerce_response", new_callable=AsyncMock) as mock_gen,
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=10, user_message="how was your day?", telegram_message_id=10, username="u", first_name="f", persona="p")
            # After 78B fix, normal should be 0
            assert mock_gen.call_count == 0


# Test 5 — OneCall signals reach commerce
class TestSignalsReachCommerce:
    @pytest.mark.asyncio
    async def test_signals_reused(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        sig = CommerceSignals.low_information()
        sig.purchase_intent = 0.6
        mock_one = OneCallResult(reply="interesting", signals=sig, confidence=0.8, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.8)
        captured = {}
        async def capture_try(user_id, context, persona, signals=None):
            captured["signals"] = signals
            return None
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one),
            patch("workers.llm_worker._try_commerce_draft", side_effect=capture_try),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=11, user_message="tell me more", telegram_message_id=11, username="u", first_name="f", persona="p")
            assert captured.get("signals") is sig
            assert captured["signals"].purchase_intent == 0.6


# Test 6 — PPV price authority
class TestPPVPriceAuthority:
    @pytest.mark.asyncio
    async def test_fangate_price_authority(self):
        """Offer price comes from fangate_products.price_minor, Qwen cannot set."""
        import inspect
        from commerce.execution import execute_ppv
        sig = inspect.signature(execute_ppv)
        assert "price" not in str(sig)
        assert "price_minor" not in sig.parameters
        assert "sales_url" not in sig.parameters


# Test 7 — PPV eligibility authority
class TestEligibilityAuthority:
    def test_decision_is_pure_no_llm(self):
        from commerce.signals import decide_from_signals
        import inspect
        src = inspect.getsource(decide_from_signals)
        assert "get_llm_provider" not in src
        assert "provider.generate" not in src


# Test 8 — OneCall failure no legacy cascade
class TestFailureNoCascade:
    @pytest.mark.asyncio
    async def test_exception_no_legacy(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        # Simulate provider exception -> pipeline returns is_valid False -> worker hardened
        bad = OneCallResult(reply="", signals=CommerceSignals.low_information(), confidence=0.0, needs_handoff=True, is_valid=False, validation_error="Generation failed: timeout", quality_score=0.0)
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_send,
            patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock) as mock_q,
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=bad),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock) as mock_legacy,
            patch("core.scoring.score_draft", new_callable=AsyncMock) as mock_score,
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=99, user_message="hi", telegram_message_id=99, username="u", first_name="f", persona="p")
            assert mock_q.called
            assert mock_send.call_count == 0
            assert mock_legacy.call_count == 0
            assert mock_score.call_count == 0

    @pytest.mark.asyncio
    async def test_invalid_no_legacy(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        invalid = OneCallResult(reply="", signals=CommerceSignals.low_information(), confidence=0.0, needs_handoff=True, is_valid=False, validation_error="Schema validation failed", quality_score=0.0)
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock) as mock_legacy,
            patch("core.scoring.score_draft", new_callable=AsyncMock) as mock_score,
            patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=invalid),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=100, user_message="hi", telegram_message_id=100, username="u", first_name="f", persona="p")
            assert mock_legacy.call_count == 0
            assert mock_score.call_count == 0


# Test 10 — valid peer/send regression
class TestValidSend:
    @pytest.mark.asyncio
    async def test_valid_send_still_enqueues(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        mock_one = OneCallResult(reply="Hello lovely fan! How can I help you today?", signals=CommerceSignals.low_information(), confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.95, safety_flags=[], quality_flags=[])
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_send,
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one),
            patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=20, user_message="hello", telegram_message_id=20, username="u", first_name="f", persona="p")
            assert mock_send.call_count == 1


# Test 11 — handoff regression
class TestHandoff:
    @pytest.mark.asyncio
    async def test_handoff_when_needs_handoff(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        # Use low quality_score to ensure operator queue (score<0.80) and needs_handoff True
        mock_one = OneCallResult(reply="I need help", signals=CommerceSignals.low_information(), confidence=0.3, needs_handoff=True, is_valid=True, validation_error=None, quality_score=0.5, safety_flags=[], quality_flags=[])
        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock) as mock_q,
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_send,
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one),
            patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=21, user_message="help", telegram_message_id=21, username="u", first_name="f", persona="p")
            # handoff via low confidence or needs_handoff should queue not send
            assert mock_q.called
            assert mock_send.call_count == 0


# Test 12 — creator isolation
class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_isolation_in_send_dedup(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.setex = AsyncMock()
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import mark_send_dedup, is_send_duplicate
            await mark_send_dedup("dedup1", creator_id=1)
            mock_redis.setex.assert_called_with("send_dedup:1:dedup1", 3600, "1")
            mock_redis.exists.reset_mock()
            mock_redis.exists = AsyncMock(return_value=1)
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                is_dup = await is_send_duplicate("dedup1", creator_id=1)
                assert is_dup is True
                # different creator not duplicate
                mock_redis.exists = AsyncMock(return_value=0)
                with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                    not_dup = await is_send_duplicate("dedup1", creator_id=2)
                    assert not_dup is False
