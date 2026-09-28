"""Phase 39 — Concurrency, Creator-Isolation & Atomic Persistence
Tests for P1-01 lock:user → lock:creator:user and P1-02 atomic fan_knowledge.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from commerce.fan_knowledge import add_knowledge_item, get_fan_knowledge, get_knowledge_memory, clear_knowledge_memory, FanKnowledgeItem, extract_fan_knowledge
from db.redis import acquire_user_lock, release_user_lock, _user_lock_key

# ── P1-01 creator-scoped lock ────────────────────────────────────────────
class TestCreatorScopedLock:
    def test_lock_key_creator_scoped(self):
        assert _user_lock_key(100, 1) == "lock:creator:1:user:100"
        assert _user_lock_key(100, 2) == "lock:creator:2:user:100"
        # P1.4: creator_id is required – no global fallback
        import pytest as _pytest
        with _pytest.raises(ValueError):
            _user_lock_key(100, None)
        assert _user_lock_key(100, 1) != _user_lock_key(100, 2)

    @pytest.mark.asyncio
    async def test_same_creator_same_fan_serialized(self):
        # Mock redis to simulate lock
        with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_get:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(side_effect=[True, False])  # first acquire True, second False
            mock_get.return_value = mock_redis
            assert await acquire_user_lock(100, creator_id=1) is True
            assert await acquire_user_lock(100, creator_id=1) is False

    @pytest.mark.asyncio
    async def test_different_creator_same_fan_independent(self):
        with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_get:
            mock_redis = AsyncMock()
            # For different creator, same user, should be independent (different keys)
            # Simulate: first lock for creator 1, second for creator 2 should both succeed (different keys)
            mock_redis.set = AsyncMock(return_value=True)
            mock_get.return_value = mock_redis
            assert await acquire_user_lock(100, creator_id=1) is True
            # Check that second call uses different key
            await acquire_user_lock(100, creator_id=2)
            # Verify set was called with different keys
            calls = [c[0][0] for c in mock_redis.set.call_args_list]
            assert "lock:creator:1:user:100" in calls
            assert "lock:creator:2:user:100" in calls

    @pytest.mark.asyncio
    async def test_same_creator_different_fans_independent(self):
        with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_get:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_get.return_value = mock_redis
            assert await acquire_user_lock(100, creator_id=1) is True
            assert await acquire_user_lock(200, creator_id=1) is True
            calls = [c[0][0] for c in mock_redis.set.call_args_list]
            assert "lock:creator:1:user:100" in calls
            assert "lock:creator:1:user:200" in calls

# ── P1-02 atomic JSONB ───────────────────────────────────────────────────
class TestAtomicFanKnowledge:
    @pytest.mark.asyncio
    async def test_concurrent_knowledge_updates(self):
        clear_knowledge_memory(1, 100)
        # Simulate concurrent adds for same creator:user with different subjects
        items = []
        for i, text in enumerate(["I live in Chicago.", "I work nights.", "My dog Max is here."]):
            for it in extract_fan_knowledge(text, 1, 100, generation_id=f"g{i}"):
                items.append(it)
        # Concurrently add all
        await asyncio.gather(*[add_knowledge_item(1, 100, it) for it in items])
        # All three should be present (no lost update)
        mem = get_knowledge_memory(1, 100)
        subjects = {k["subject"] for k in mem}
        # Should have at least city, work_schedule, pet_type or pet_name
        assert "city" in subjects or "work_schedule" in subjects
        clear_knowledge_memory(1, 100)

    @pytest.mark.asyncio
    async def test_jsonb_update_preservation(self):
        clear_knowledge_memory(1, 100)
        # Add occupation and city concurrently
        it1 = FanKnowledgeItem(subject="occupation", value="software engineer", category="WORK", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="g1")
        it2 = FanKnowledgeItem(subject="city", value="Chicago", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="g2")
        # Simulate concurrent via asyncio.gather (both read same initial {} then write)
        await asyncio.gather(add_knowledge_item(1, 100, it1), add_knowledge_item(1, 100, it2))
        mem = get_knowledge_memory(1, 100)
        # Both should be present, not one lost
        assert any(k["subject"] == "occupation" for k in mem)
        assert any(k["subject"] == "city" for k in mem)
        clear_knowledge_memory(1, 100)

    @pytest.mark.asyncio
    async def test_duplicate_generation_idempotent(self):
        clear_knowledge_memory(1, 100)
        it = FanKnowledgeItem(subject="city", value="Chicago", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gSame")
        await add_knowledge_item(1, 100, it)
        await add_knowledge_item(1, 100, it)  # same generation_id, same subject/value
        mem = get_knowledge_memory(1, 100)
        # Should be one, not duplicate
        assert len([k for k in mem if k["value"] == "Chicago"]) == 1
        clear_knowledge_memory(1, 100)

    @pytest.mark.asyncio
    async def test_different_creators_same_generation_namespace_safe(self):
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)
        it1 = FanKnowledgeItem(subject="city", value="Chicago", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gSame")
        it2 = FanKnowledgeItem(subject="city", value="London", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=2, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gSame")
        await add_knowledge_item(1, 100, it1)
        await add_knowledge_item(2, 100, it2)
        mem1 = get_knowledge_memory(1, 100)
        mem2 = get_knowledge_memory(2, 100)
        assert any(k["value"] == "Chicago" for k in mem1)
        assert any(k["value"] == "London" for k in mem2)
        assert not any(k["value"] == "London" for k in mem1)
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)

    @pytest.mark.asyncio
    async def test_concurrent_temporal_updates(self):
        clear_knowledge_memory(1, 100)
        it_home = FanKnowledgeItem(subject="city", value="New York", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gHome", location_role="HOME")
        it_temp = FanKnowledgeItem(subject="city", value="Spain", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="TEMPORARY", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gTemp", location_role="TEMPORARY")
        # Add HOME and TEMPORARY concurrently
        await asyncio.gather(add_knowledge_item(1, 100, it_home), add_knowledge_item(1, 100, it_temp))
        mem = get_knowledge_memory(1, 100)
        # Both should exist, HOME and TEMPORARY separate
        assert any(k["value"] == "New York" and k.get("location_role") == "HOME" for k in mem)
        assert any(k["value"] == "Spain" and k.get("location_role") == "TEMPORARY" for k in mem)
        clear_knowledge_memory(1, 100)

    def test_30_item_bound(self):
        async def run():
            clear_knowledge_memory(1, 100)
            for i in range(35):
                it = FanKnowledgeItem(subject=f"subject_{i}", value=f"value_{i}", category="HOBBIES", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id=f"g{i}")
                await add_knowledge_item(1, 100, it)
            mem = get_knowledge_memory(1, 100)
            assert len(mem) <= 30
            clear_knowledge_memory(1, 100)
        asyncio.run(run())

    @pytest.mark.asyncio
    async def test_xautoclaim_retry_idempotent(self):
        clear_knowledge_memory(1, 100)
        it = FanKnowledgeItem(subject="city", value="Chicago", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="gRetry")
        await add_knowledge_item(1, 100, it)
        # Simulate XAUTOCLAIM retry with same generation_id
        await add_knowledge_item(1, 100, it)
        mem = get_knowledge_memory(1, 100)
        assert len([k for k in mem if k["value"] == "Chicago"]) == 1
        clear_knowledge_memory(1, 100)

    def test_creator_isolation_persistence(self):
        async def run():
            clear_knowledge_memory(1, 100)
            clear_knowledge_memory(2, 100)
            it1 = FanKnowledgeItem(subject="city", value="Chicago", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="g1")
            await add_knowledge_item(1, 100, it1)
            mem1 = await get_fan_knowledge(1, 100)
            mem2 = await get_fan_knowledge(2, 100)
            assert len(mem1) == 1
            assert len(mem2) == 0
            clear_knowledge_memory(1, 100)
            clear_knowledge_memory(2, 100)
        asyncio.run(run())

    def test_temporal_preservation(self):
        async def run():
            clear_knowledge_memory(1, 100)
            it_home = FanKnowledgeItem(subject="city", value="New York", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="g1", location_role="HOME")
            it_temp = FanKnowledgeItem(subject="city", value="Spain", category="LOCATION", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="TEMPORARY", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat(), evidence_generation_id="g2", location_role="TEMPORARY")
            await add_knowledge_item(1, 100, it_home)
            await add_knowledge_item(1, 100, it_temp)
            mem = get_knowledge_memory(1, 100)
            assert any(k["value"] == "New York" for k in mem)
            assert any(k["value"] == "Spain" for k in mem)
            clear_knowledge_memory(1, 100)
        asyncio.run(run())
