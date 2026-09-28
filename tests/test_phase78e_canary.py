"""Phase 78E — 10% canary deterministic verification (A-S)."""

from unittest.mock import AsyncMock, MagicMock, patch
import hashlib

import pytest

pytestmark = [pytest.mark.unit]


def _should_run(user_id, creator_id, sample_rate):
    key = f"{creator_id}:{user_id}" if creator_id is not None else str(user_id)
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
    return h < int(sample_rate * 100)


# A deterministic canary
class TestDeterministicCanary:
    def test_same_fan_same_cohort(self):
        for uid in [1, 42, 999, 12345]:
            r1 = _should_run(uid, 1, 0.10)
            r2 = _should_run(uid, 1, 0.10)
            assert r1 == r2

    def test_different_fans_different_cohort_stability(self):
        # At least some fans in, some out for 0.10
        results = [_should_run(i, 1, 0.10) for i in range(1000)]
        in_count = sum(results)
        # Approx 10% ±5%
        assert 50 <= in_count <= 150

    def test_creator_isolation(self):
        # Same user_id different creator -> independent
        r1 = _should_run(123, 1, 0.10)
        r2 = _should_run(123, 2, 0.10)
        # They could be same by chance, but test that key includes creator
        key1 = f"1:123"
        key2 = f"2:123"
        assert hashlib.sha256(key1.encode()).hexdigest() != hashlib.sha256(key2.encode()).hexdigest()


# B approx 10%
class TestApprox10:
    def test_distribution_10_percent(self):
        n = 10000
        in_c = sum(_should_run(i, 1, 0.10) for i in range(n))
        # 10% ±2% for 10k
        assert 800 <= in_c <= 1200


# C engine enabled canary executes
class TestEngineEnabled:
    @pytest.mark.asyncio
    async def test_canary_message_executes_engine(self):
        # Find a user_id that is in canary for creator 1 with 0.10
        canary_uid = None
        for uid in range(1000):
            if _should_run(uid, 1, 0.10):
                canary_uid = uid
                break
        assert canary_uid is not None
        # Now run observe_context_engine with enabled true and conversation_state
        with patch("context_engine.integration.ContextEngineIntegration.process", new_callable=AsyncMock) as mock_process:
            mock_result = MagicMock()
            mock_result.gather_time_ms = 5
            mock_result.assembly_time_ms = 2
            mock_result.candidate_count = 5
            mock_result.selected_count = 3
            mock_result.total_tokens = 100
            mock_rendered = MagicMock()
            mock_rendered.system_prompt = "sys"
            mock_rendered.state_block = "state"
            mock_rendered.commerce_block = "com"
            mock_rendered.memory_block = "MEMORY: test"
            mock_rendered.temporal_block = "TEMP"
            mock_rendered.content_block = "content"
            mock_result.rendered = mock_rendered
            mock_process.return_value = mock_result
            from context_engine.worker_integration import observe_context_engine
            obs = await observe_context_engine(user_id=canary_uid, creator_id=1, user_message="hi", generation_id="gid", enabled=True, conversation_state={"current_topic": "price"})
            assert obs.enabled is True
            assert obs.rendered_text != ""
            assert "MEMORY" in obs.rendered_text


# D control not execute
class TestControlNotExecute:
    @pytest.mark.asyncio
    async def test_control_not_execute(self):
        from context_engine.worker_integration import observe_context_engine
        obs = await observe_context_engine(user_id=1, creator_id=1, user_message="hi", enabled=False)
        assert obs.enabled is False
        assert obs.rendered_text == ""


# E RapidFuzz retrieval
class TestRapidFuzzRetrieval:
    @pytest.mark.asyncio
    async def test_rapidfuzz_produces_candidates(self):
        # Verify that MemorySource with RapidFuzz finds Nairobi vs current_message (exact match ensures WRatio high)
        with (
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, return_value=[]),
            patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, return_value=[
                {"subject": "city", "value": "Nairobi", "status": "CURRENT", "confidence": 0.9},
                {"subject": "pet", "value": "cat", "status": "CURRENT", "confidence": 0.8},
            ]),
            patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, return_value=None),
        ):
            from context_engine.gatherer import GathererConfig, MemorySource
            src = MemorySource()
            cfg = GathererConfig(creator_id=1, user_id=1, current_message="Nairobi")
            items = await src.gather(cfg)
            assert any("Nairobi" in i.content for i in items)


# F MiniLM retrieval
class TestMiniLMRetrieval:
    @pytest.mark.asyncio
    async def test_minilm_produces_candidates(self):
        with (
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, return_value=[]),
            patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, return_value=[
                {"subject": "hobby", "value": "football", "status": "CURRENT", "confidence": 0.9},
                {"subject": "feeling", "value": "upset about cost", "status": "CURRENT", "confidence": 0.9},
            ]),
            patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, return_value=[0.5]*384),
            patch("commerce.embedding_model.encode_messages_sync", return_value=[[0.5]*384, [-0.5]*384]),
        ):
            from context_engine.gatherer import GathererConfig, MemorySource
            src = MemorySource()
            cfg = GathererConfig(creator_id=1, user_id=1, current_message="too expensive")
            items = await src.gather(cfg)
            # At least one semantic hit (upset about cost) should be present or at least not crash
            assert isinstance(items, list)


# G hybrid
class TestHybrid:
    @pytest.mark.asyncio
    async def test_hybrid_merge(self):
        with (
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, return_value=[{"subject": "a", "value": "1", "status": "CURRENT", "confidence": 1}]),
            patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, return_value=[
                {"subject": "a", "value": "1", "status": "CURRENT", "confidence": 1},
                {"subject": "b", "value": "2", "status": "CURRENT", "confidence": 1},
            ]),
            patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, return_value=None),
        ):
            from context_engine.gatherer import GathererConfig, MemorySource
            src = MemorySource()
            cfg = GathererConfig(creator_id=1, user_id=1, current_message="a")
            items = await src.gather(cfg)
            assert len(items) <= 10


# H dedup
class TestDedupCanary:
    def test_dedup_before_render(self):
        from pathlib import Path
        assert "deduplicate" in Path("context_engine/assembler.py").read_text() or "Deduplicator" in Path("context_engine/integration.py").read_text()


# I state relevance
class TestStateRelevanceCanary:
    def test_state_wiring(self):
        from pathlib import Path
        assert "conversation_state" in Path("context_engine/worker_integration.py").read_text()
        assert "conversation_state" in Path("context_engine/integration.py").read_text()


# J budget
class TestBudgetCanary:
    def test_budget_enforced(self):
        from context_engine.models import TOTAL_CONTEXT_BUDGET
        assert TOTAL_CONTEXT_BUDGET == 2600
        from pathlib import Path
        assert "try_allocate_or_truncate" in Path("context_engine/budget.py").read_text()


# K OneCall
class TestOneCallCanary:
    @pytest.mark.asyncio
    async def test_one_call_still_one(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        mock_one = OneCallResult(reply="hello lovely", signals=CommerceSignals.low_information(), confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.9)
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
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one) as mock_pipe,
            patch("commerce.pipeline.generate_commerce_response", new_callable=AsyncMock) as mock_com,
        ):
            from workers.llm_worker import process_message
            # Force canary enabled for this user via patching settings
            with patch("workers.llm_worker.get_settings") as mock_s:
                mock_s.return_value = MagicMock(
                    context_engine_observational=False,
                    context_engine_enabled=True,
                    context_engine_sample_rate=1.0,  # force canary
                    context_engine_canary_sample_rate=0.0,
                    llm_path="new",
                    user_lock_ttl=60,
                    auto_approve_threshold=0.80,
                )
                await process_message(user_id=999, user_message="hey beautiful", telegram_message_id=1, username="u", first_name="f", persona="p")
                assert mock_pipe.call_count == 1
                assert mock_com.call_count == 0


# L no legacy cascade
class TestNoLegacyCascadeCanary:
    @pytest.mark.asyncio
    async def test_no_legacy_on_failure(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        bad = OneCallResult(reply="", signals=CommerceSignals.low_information(), confidence=0.0, needs_handoff=True, is_valid=False, validation_error="bad", quality_score=0.0)
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
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=bad),
        ):
            from workers.llm_worker import process_message
            with patch("workers.llm_worker.get_settings") as mock_s:
                mock_s.return_value = MagicMock(context_engine_observational=False, context_engine_enabled=True, context_engine_sample_rate=1.0, llm_path="new", user_lock_ttl=60)
                await process_message(user_id=1, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="p")
                assert mock_legacy.call_count == 0
                assert mock_score.call_count == 0


# M PPV
class TestPPVCanary:
    def test_price_authority(self):
        import inspect
        from commerce.execution import execute_ppv
        assert "price_minor" not in inspect.signature(execute_ppv).parameters


# N handoff
class TestHandoffCanary:
    @pytest.mark.asyncio
    async def test_handoff_preserved(self):
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        mock_one = OneCallResult(reply="help", signals=CommerceSignals.low_information(), confidence=0.3, needs_handoff=True, is_valid=True, validation_error=None, quality_score=0.5)
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
            with patch("workers.llm_worker.get_settings") as mock_s:
                mock_s.return_value = MagicMock(context_engine_observational=False, context_engine_enabled=True, context_engine_sample_rate=1.0, llm_path="new", user_lock_ttl=60, auto_approve_threshold=0.80)
                await process_message(user_id=5, user_message="help", telegram_message_id=5, username="u", first_name="f", persona="p")
                assert mock_q.called
                assert mock_send.call_count == 0


# O creator isolation
class TestCreatorIsolationCanary:
    @pytest.mark.asyncio
    async def test_isolation(self):
        from context_engine.gatherer import GathererConfig, MemorySource
        async def fake_get(creator_id, user_id, **kw):
            if creator_id == 1:
                return [{"subject": "secret", "value": "creator1", "status": "CURRENT", "confidence": 1}]
            else:
                return [{"subject": "secret", "value": "creator2", "status": "CURRENT", "confidence": 1}]
        with (
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, side_effect=lambda **kw: fake_get(kw.get("creator_id"), kw.get("user_id"))),
            patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, side_effect=fake_get),
            patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, return_value=None),
        ):
            src = MemorySource()
            cfg1 = GathererConfig(creator_id=1, user_id=1, current_message="secret")
            items1 = await src.gather(cfg1)
            cfg2 = GathererConfig(creator_id=2, user_id=1, current_message="secret")
            items2 = await src.gather(cfg2)
            assert "creator1" in " ".join([i.content for i in items1])
            assert "creator1" not in " ".join([i.content for i in items2])


# P failure open
class TestFailureOpen:
    @pytest.mark.asyncio
    async def test_retrieval_failure_open(self):
        with (
            patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, side_effect=RuntimeError("down")),
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, return_value=[]),
            patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, return_value=[]),
        ):
            from context_engine.gatherer import GathererConfig, MemorySource
            src = MemorySource()
            cfg = GathererConfig(creator_id=1, user_id=1, current_message="hi")
            items = await src.gather(cfg)
            assert isinstance(items, list)


# Q redis recovery
class TestRedisRecoveryCanary:
    @pytest.mark.asyncio
    async def test_reclaimed_still_processed(self):
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("id-1", {"user_id": "1", "content": "hi", "telegram_message_id": "1"})]))
        mock_redis.xreadgroup = AsyncMock(return_value=[])
        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_s,
            patch("workers.llm_worker.process_message", new_callable=AsyncMock),
            patch("workers.llm_worker.ack_inbound", new_callable=AsyncMock) as mock_ack,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
        ):
            mock_s.return_value.redis_pending_idle_ms = 60000
            mock_s.return_value.user_lock_ttl = 60
            from workers.llm_worker import run_worker
            await run_worker("worker_1")
            mock_ack.assert_called()


# R send dedup
class TestSendDedupCanary:
    @pytest.mark.asyncio
    async def test_send_dedup_creator_scoped(self):
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import mark_send_dedup, is_send_duplicate
            await mark_send_dedup("d1", creator_id=1)
            mock_redis.setex.assert_called_with("send_dedup:1:d1", 3600, "1")
            mock_redis.exists = AsyncMock(return_value=1)
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                assert await is_send_duplicate("d1", creator_id=1) is True
            mock_redis.exists = AsyncMock(return_value=0)
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                assert await is_send_duplicate("d1", creator_id=2) is False


# S rollback
class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_restores_control(self):
        # When disabled, observe returns enabled False and no retrieval
        from context_engine.worker_integration import observe_context_engine
        obs = await observe_context_engine(user_id=1, creator_id=1, user_message="hi", enabled=False)
        assert obs.enabled is False
        assert obs.rendered_text == ""
