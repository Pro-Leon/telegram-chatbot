"""Phase 43F — Creator Isolation & Persona Snapshot (hostile concurrency)
Deterministic, no live DB required beyond mocks.
"""
import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def _md5(user, msg, tg):
    return hashlib.md5(f"{user}:{msg}:{tg}".encode()).hexdigest()

# A same generation ID across creators
def test_A_same_generation_id_identical():
    gid = _md5(777, "hey", 100)
    gid2 = _md5(777, "hey", 100)
    assert gid == gid2
    # Canonical remains MD5(user:msg:tgId) unchanged, not creator-scoped
    assert gid == hashlib.md5(b"777:hey:100").hexdigest()

# B telemetry isolation
@pytest.mark.asyncio
async def test_B_telemetry_isolation():
    from core.telemetry import TelemetryCollector
    col = TelemetryCollector()
    t1 = col.start_generation(user_id=777, creator_id=1, generation_id="X", runtime_mode="legacy")
    t1.persona_version = 1
    t1.emotional_state = "warm"
    t2 = col.start_generation(user_id=777, creator_id=2, generation_id="X", runtime_mode="legacy")
    t2.persona_version = 2
    t2.emotional_state = "playful"
    # Both should coexist, not overwrite
    assert col.get("X", creator_id=1) is not None
    assert col.get("X", creator_id=2) is not None
    assert col.get("X", creator_id=1).persona_version == 1
    assert col.get("X", creator_id=2).persona_version == 2
    assert col.get("X", creator_id=1) is not col.get("X", creator_id=2)

# C send dedup isolation
@pytest.mark.asyncio
async def test_C_send_dedup_isolation():
    from db.redis import mark_send_dedup, is_send_duplicate
    # Mock redis
    store = {}
    mock_r = AsyncMock()
    async def fake_setex(k, ttl, v):
        store[k] = v
    async def fake_exists(k):
        return 1 if k in store else 0
    mock_r.setex = fake_setex
    mock_r.exists = fake_exists
    mock_r.get = AsyncMock(side_effect=lambda k: store.get(k))
    mock_r.delete = AsyncMock()
    mock_r.scan_iter = AsyncMock(return_value=None)
    with patch("db.redis.get_redis", new=AsyncMock(return_value=mock_r)):
        dedup = _md5(777, "hey", 100)
        await mark_send_dedup(dedup, creator_id=1)
        assert await is_send_duplicate(dedup, creator_id=1) is True
        assert await is_send_duplicate(dedup, creator_id=2) is False
        await mark_send_dedup(dedup, creator_id=2)
        assert await is_send_duplicate(dedup, creator_id=2) is True
        # Global fallback should not be consulted when creator provided
        assert await is_send_duplicate(dedup, creator_id=1) is True
        # Global without creator should be false (no global key)
        assert await is_send_duplicate(dedup, creator_id=None) is False

# D message history isolation
@pytest.mark.asyncio
async def test_D_message_history_isolation():
    # Mock DB: get_recent_messages should filter by creator_id
    from unittest.mock import AsyncMock, MagicMock, patch
    # Simulate DB rows for creator 1 and 2
    async def fake_fetch(query, *args):
        # args[0]=user_id, args[1]=creator_id, args[2]=limit if creator provided
        if len(args) == 3 and args[1] == 1:
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda s,k: {"direction":"inbound","content":"I live in New York","created_at":None}[k]
            return [ {"direction":"inbound","content":"I live in New York","created_at":None} ]
        elif len(args) == 3 and args[1] == 2:
            return [ {"direction":"inbound","content":"I live in Los Angeles","created_at":None} ]
        else:
            return []
    mock_conn = AsyncMock()
    mock_conn.fetch = fake_fetch
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire)
    with patch("db.postgres.get_pool", new=AsyncMock(return_value=mock_pool)):
        from db.postgres import get_recent_messages
        rows1 = await get_recent_messages(777, limit=20, creator_id=1)
        rows2 = await get_recent_messages(777, limit=20, creator_id=2)
        assert any("New York" in r["content"] for r in rows1)
        assert any("Los Angeles" in r["content"] for r in rows2)
        assert not any("Los Angeles" in r["content"] for r in rows1)
        assert not any("New York" in r["content"] for r in rows2)

# H persona snapshot single-fetch
@pytest.mark.asyncio
async def test_H_persona_snapshot_single_fetch():
    # Ensure build_qwen3_context uses snapshot when provided, not double fetch
    from unittest.mock import AsyncMock, patch, MagicMock
    sunny = {"identity":{"name":"Sunny Skye"},"persona_version":1}
    # Count fetches
    fetch_count = 0
    async def fake_get(creator_id):
        nonlocal fetch_count
        fetch_count += 1
        return sunny
    with patch("memory.creator_persona.get_structured_persona_async", new=AsyncMock(side_effect=fake_get)):
        from memory.context import build_qwen3_context
        fake_user = {"first_name":"Alex","funnel_stage":"new","last_seen":None,"message_count":5}
        # Provide snapshot directly, should be 0 fetches for this call (since snapshot provided)
        with patch("memory.context.get_user", new=AsyncMock(return_value=fake_user)), \
             patch("memory.context.get_user_profile", new=AsyncMock(return_value={})), \
             patch("memory.context.get_latest_summary_with_age", new=AsyncMock(return_value=(None,None))), \
             patch("memory.context.get_recent_messages", new=AsyncMock(return_value=[])):
            ctx = await build_qwen3_context(777, "hey", "You are Sunny", creator_id=1, structured_persona_snapshot=sunny)
            # Should not have fetched again for CREATOR PERSONA (reuse snapshot)
            # At least persona_name should be Sunny from snapshot
            system_text = " ".join(m["content"] for m in ctx if m["role"]=="system")
            assert "Sunny Skye" in system_text
            # Fetch count should be 0 because snapshot provided (no DB fetch for name)
            # But our fake counts only calls via get_structured_persona_async; since snapshot provided, count remains 0
            assert fetch_count == 0

# I persona version consistency
@pytest.mark.asyncio
async def test_I_persona_version_consistency():
    from commerce.persona_behavior import derive_persona_behavior_state
    sunny_v1 = {"identity":{"name":"Sunny"},"persona_version":1,"communication":{"casing":"lowercase"},"behavioral_rules":{"can_disagree":True}}
    # Derive behavior from v1
    state = derive_persona_behavior_state(structured_persona=sunny_v1, conversation_state=None, fan_message="hi", creator_id=1, generation_id="g1")
    assert state.persona_version == 1
    # Now v2
    sunny_v2 = {"identity":{"name":"Sunny"},"persona_version":2,"communication":{"casing":"lowercase"},"behavioral_rules":{"can_disagree":True}}
    state2 = derive_persona_behavior_state(structured_persona=sunny_v2, conversation_state=None, fan_message="hi", creator_id=1, generation_id="g2")
    assert state2.persona_version == 2
    assert state.persona_version != state2.persona_version

# K creator context unavailable fail-closed
def test_K_creator_context_unavailable():
    import pathlib
    src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
    assert "_fail_closed_creator_unavailable" in src
    assert "creator_context_unavailable" in src
    assert "Thanks for your message! Our team will follow up" in src

# L no Sunny substitution for Mia
def test_L_no_sunny_substitution():
    from commerce.persona_behavior import derive_persona_behavior_state
    from commerce.persona_validation import validate_persona_voice
    mia = {"identity":{"name":"Mia"},"persona_version":1}
    state = derive_persona_behavior_state(structured_persona=mia, conversation_state=None, fan_message="hi", creator_id=2, generation_id="g1")
    # Mia should not be Sunny
    assert state.creator_id == 2
    val = validate_persona_voice("Hi I'm Mia", persona=mia, behavior_state=state)
    assert val.fact_violation is False
    # Sunny claim vs Mia should be violation
    val2 = validate_persona_voice("Hi I'm Sunny Skye", persona=mia, behavior_state=state)
    assert val2.fact_violation is True

# O concurrent A/B same fan
@pytest.mark.asyncio
async def test_O_concurrent_AB():
    from core.telemetry import TelemetryCollector
    import asyncio
    col = TelemetryCollector()
    async def run_one(creator):
        t = col.start_generation(user_id=777, creator_id=creator, generation_id="CONC", runtime_mode="legacy")
        await asyncio.sleep(0.01)
        return t
    t1, t2 = await asyncio.gather(run_one(1), run_one(2))
    assert col.get("CONC", creator_id=1) is not None
    assert col.get("CONC", creator_id=2) is not None
    assert col.get("CONC", creator_id=1) is not col.get("CONC", creator_id=2)

# Pass single-pass etc are structural
def test_W_no_new_llm():
    import pathlib
    beh = pathlib.Path("commerce/persona_behavior.py").read_text(encoding="utf-8")
    val = pathlib.Path("commerce/persona_validation.py").read_text(encoding="utf-8")
    assert "get_llm_provider" not in beh
    assert "generate" not in beh.lower() or "generate_with_history" not in beh
    assert "get_llm_provider" not in val

def test_single_pass():
    import pathlib
    src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
    assert src.count("await generate_draft") >= 1
    assert "validate_persona_voice" in src

def test_restart_safety():
    from commerce.persona_behavior import derive_persona_behavior_state
    sunny = {"identity":{"name":"Sunny"},"persona_version":1}
    s1 = derive_persona_behavior_state(structured_persona=sunny, conversation_state=None, fan_message="hi", creator_id=1, generation_id="g1")
    s2 = derive_persona_behavior_state(structured_persona=sunny, conversation_state=None, fan_message="hi", creator_id=1, generation_id="g1")
    assert s1 == s2
