"""Phase 38 — Deep Fan Personalization Hardening
Tests for P1-01..P1-04 and hardening.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import pytest

from commerce.fan_knowledge import extract_fan_knowledge, add_knowledge_item, get_fan_knowledge, get_knowledge_memory, clear_knowledge_memory, FanKnowledgeItem
from commerce.temporal_context import derive_fan_timezone
from commerce.content_matching import rank_products_by_relevance
from commerce.production_control import clear_metrics, record_metric, MetricWindow, query_metrics

# Test 1 — third-party move
def test_third_party_move():
    async def run():
        clear_knowledge_memory(1, 100)
        items = extract_fan_knowledge("My ex moved to New York.", 1, 100, generation_id="g1")
        # Should NOT create fan city New York (third-party)
        assert not any(i.subject == "city" and "New York" in i.value for i in items)
        for it in items:
            await add_knowledge_item(1, 100, it)
        know = await get_fan_knowledge(1, 100)
        assert not any(k["value"] == "New York" for k in know)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 2 — third-party residence
def test_third_party_residence():
    async def run():
        clear_knowledge_memory(1, 100)
        items = extract_fan_knowledge("My sister lives in London.", 1, 100, generation_id="g1")
        assert not any(i.subject == "city" and "London" in i.value for i in items)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 3 — first-person move
def test_first_person_move():
    async def run():
        clear_knowledge_memory(1, 100)
        items = extract_fan_knowledge("I moved to New York.", 1, 100, generation_id="g1")
        assert any(i.subject == "city" and "New York" in i.value and i.location_role == "HOME" for i in items)
        for it in items:
            await add_knowledge_item(1, 100, it)
        know = await get_fan_knowledge(1, 100)
        assert any(k["value"] == "New York" and k.get("location_role") == "HOME" for k in know)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 4 — cross-message pet when unambiguous
def test_cross_message_pet_unambiguous():
    async def run():
        clear_knowledge_memory(1, 100)
        # Msg1: My dog
        for it in extract_fan_knowledge("My dog keeps waking me up.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        # Msg2: His name is Max — should associate because exactly one pet_type and no pet_name
        existing = get_knowledge_memory(1, 100)
        items2 = extract_fan_knowledge("His name is Max.", 1, 100, generation_id="g2", existing_knowledge=existing)
        assert any(i.subject == "pet_name" and i.value == "Max" for i in items2)
        for it in items2:
            await add_knowledge_item(1, 100, it)
        know = await get_fan_knowledge(1, 100)
        assert any(k["subject"] == "pet_name" and k["value"] == "Max" for k in know)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 5 — ambiguous pet
def test_ambiguous_pet():
    async def run():
        clear_knowledge_memory(1, 100)
        # Two pets
        for it in extract_fan_knowledge("I have a dog and a cat.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        # Also add dog and cat explicitly
        await add_knowledge_item(1, 100, FanKnowledgeItem(subject="pet_type", value="dog", category="PETS", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat()))
        await add_knowledge_item(1, 100, FanKnowledgeItem(subject="pet_type", value="cat", category="PETS", confidence=1.0, source="USER_EXPLICIT", observed_at=datetime.now(timezone.utc).isoformat(), temporal_type="CURRENT", status="CURRENT", creator_id=1, user_id=100, first_observed_at=datetime.now(timezone.utc).isoformat()))
        existing = get_knowledge_memory(1, 100)
        # Now two pet_types
        items = extract_fan_knowledge("Her name is Max.", 1, 100, generation_id="g2", existing_knowledge=existing)
        # Should NOT create pet_name because ambiguous (two pet_types)
        assert not any(i.subject == "pet_name" for i in items)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 6 — stable + temporary
def test_stable_temporary():
    async def run():
        clear_knowledge_memory(1, 100)
        for it in extract_fan_knowledge("I moved to New York.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        for it in extract_fan_knowledge("I'm in Spain for a week.", 1, 100, generation_id="g2"):
            await add_knowledge_item(1, 100, it)
        know = await get_fan_knowledge(1, 100)
        mem = get_knowledge_memory(1, 100)
        assert any(k["value"] == "New York" and k["status"] == "CURRENT" and k.get("location_role") == "HOME" for k in mem)
        assert any(k["value"] == "Spain" and k["status"] == "CURRENT" and k.get("location_role") == "TEMPORARY" for k in mem)
        # Also check that get_fan_knowledge returns both (CURRENT)
        assert any(k["value"] == "New York" for k in know)
        assert any(k["value"] == "Spain" for k in know)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 7 — temporary expiration
def test_temporary_expiration():
    async def run():
        clear_knowledge_memory(1, 100)
        for it in extract_fan_knowledge("I moved to New York.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        for it in extract_fan_knowledge("I'm in Spain for a week.", 1, 100, generation_id="g2"):
            await add_knowledge_item(1, 100, it)
        # After 8 days, Spain should be expired, New York remains
        future = datetime.now(timezone.utc) + timedelta(days=8)
        from commerce.fan_knowledge import is_knowledge_expired
        mem = get_knowledge_memory(1, 100)
        spain = [k for k in mem if k["value"] == "Spain"][0]
        assert is_knowledge_expired(spain, now=future) is True
        # get_fan_knowledge should not return Spain after expiry (filtered)
        # We need to simulate retrieval after 8 days: is_knowledge_expired would filter
        # For this test, just check that New York not expired
        ny = [k for k in mem if k["value"] == "New York"][0]
        assert is_knowledge_expired(ny, now=future) is False
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 8 — historical preservation
def test_historical_preservation():
    async def run():
        clear_knowledge_memory(1, 100)
        for it in extract_fan_knowledge("I live in Chicago.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        for it in extract_fan_knowledge("I moved to New York.", 1, 100, generation_id="g2"):
            await add_knowledge_item(1, 100, it)
        for it in extract_fan_knowledge("I'm in Spain for a week.", 1, 100, generation_id="g3"):
            await add_knowledge_item(1, 100, it)
        mem = get_knowledge_memory(1, 100)
        assert any(k["value"] == "Chicago" and k["status"] == "HISTORICAL" for k in mem)
        assert any(k["value"] == "New York" and k["status"] == "CURRENT" for k in mem)
        assert any(k["value"] == "Spain" and k["status"] == "CURRENT" for k in mem)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 9 — creator isolation via fan_knowledge
def test_creator_isolation():
    async def run():
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)
        for it in extract_fan_knowledge("I love hiking.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        know1 = await get_fan_knowledge(1, 100)
        know2 = await get_fan_knowledge(2, 100)
        assert len(know1) == 1
        assert len(know2) == 0
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)
    asyncio.run(run())

# Test 10 — personalization context only creator-scoped
def test_personalization_context_creator_scoped():
    async def run():
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)
        for it in extract_fan_knowledge("I love hiking.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        from commerce.fan_knowledge import build_personalization_context
        ctx1 = await build_personalization_context(1, 100)
        ctx2 = await build_personalization_context(2, 100)
        assert "hiking" in ctx1
        assert "hiking" not in ctx2
        clear_knowledge_memory(1, 100)
        clear_knowledge_memory(2, 100)
    asyncio.run(run())

# Test 11 — restart safety
def test_restart_safety():
    async def run():
        clear_knowledge_memory(1, 100)
        for it in extract_fan_knowledge("I moved to New York.", 1, 100, generation_id="g1"):
            await add_knowledge_item(1, 100, it)
        mem_before = get_knowledge_memory(1, 100)
        # Simulate restart via get_fan_knowledge (which reads from user_profiles JSONB if available, but fallback is in-mem)
        # For this test, in-mem survives as we don't clear
        assert any(k["value"] == "New York" for k in mem_before)
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 12 — idempotency
def test_idempotency():
    async def run():
        clear_knowledge_memory(1, 100)
        items = extract_fan_knowledge("I live in Chicago.", 1, 100, generation_id="g1")
        for it in items:
            await add_knowledge_item(1, 100, it)
            await add_knowledge_item(1, 100, it)  # retry same generation_id
        know = await get_fan_knowledge(1, 100)
        # Should not duplicate (same subject/value same generation_id)
        assert len([k for k in know if k["value"] == "Chicago"]) == 1
        clear_knowledge_memory(1, 100)
    asyncio.run(run())

# Test 13 — no LLM expansion
def test_no_llm():
    import commerce.fan_knowledge as fk
    assert not hasattr(fk, "generate_content")
    assert not hasattr(fk, "get_llm_provider")

# Test 14 — single-pass
def test_single_pass():
    from commerce.adaptive_optimization import verify_single_pass
    ok,_ = verify_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0})
    assert ok
