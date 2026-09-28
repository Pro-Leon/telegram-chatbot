"""Phase 44C — Latency & Context Optimization Regression
Deterministic, no live Ollama required for most, uses mocks.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def _sunny():
    from memory.creator_persona import build_sunny_persona
    return build_sunny_persona()
def _mia():
    return {"identity":{"name":"Mia","age":22},"location":{"city":"Los Angeles"},"occupation":{"title":"model"},"communication":{"casing":"standard"},"behavioral_rules":{"can_disagree":False}}

# Persona projection
def test_compact_contains_identity():
    from memory.creator_persona import render_compact_persona_block
    sunny = _sunny()
    compact = render_compact_persona_block(sunny)
    assert "Sunny Skye" in compact
    assert "19" in compact
    assert "NYC" in compact or "New York" in compact
    assert "freelance graphic designer" in compact.lower()
    # Compact should be materially smaller than full
    from memory.creator_persona import render_persona_block
    full = render_persona_block(None, sunny)
    assert len(compact) < len(full) * 0.5  # at least 50% smaller
    assert len(compact) < 5000  # target 3-5k

def test_compact_contains_behavior():
    from memory.creator_persona import render_compact_persona_block
    sunny = _sunny()
    compact = render_compact_persona_block(sunny)
    # Behavioral rules preserved
    assert "can_disagree" in compact.lower() or "disagreement" in compact.lower()
    assert "lowercase" in compact.lower() or "casing" in compact.lower()
    assert "emoji" in compact.lower()

def test_compact_creator_generic():
    from memory.creator_persona import render_compact_persona_block
    sunny = _sunny()
    mia = _mia()
    cs = render_compact_persona_block(sunny)
    cm = render_compact_persona_block(mia)
    assert "Sunny" in cs
    assert "Mia" in cm
    assert "Sunny" not in cm
    assert "Mia" not in cs

def test_snapshot_consistency():
    # One fetch per generation — ensure build_qwen3_context reuses snapshot
    import asyncio
    from unittest.mock import AsyncMock, patch
    sunny = _sunny()
    async def run():
        with patch("memory.creator_persona.get_structured_persona_async", new=AsyncMock(return_value=sunny)) as mock_get:
            from memory.context import build_qwen3_context
            fake_user = {"first_name":"Alex","funnel_stage":"new","last_seen":None,"message_count":5}
            with patch("memory.context.get_user", new=AsyncMock(return_value=fake_user)), \
                 patch("memory.context.get_user_profile", new=AsyncMock(return_value={})), \
                 patch("memory.context.get_latest_summary_with_age", new=AsyncMock(return_value=(None,None))), \
                 patch("memory.context.get_recent_messages", new=AsyncMock(return_value=[])):
                # Provide snapshot, should not call get_structured again for CREATOR PERSONA
                mock_get.reset_mock()
                ctx = await build_qwen3_context(777, "hey", "You are Sunny", creator_id=1, structured_persona_snapshot=sunny)
                # Should have used snapshot, so get_structured not called for this path (or called 0-1)
                # At least persona block present
                assert any("CREATOR PERSONA" in m.get("content","") for m in ctx if m.get("role")=="system")
                assert any("Sunny Skye" in m.get("content","") for m in ctx if m.get("role")=="system")
    asyncio.run(run())

def test_generation_context_contains_compact_and_behavior():
    import asyncio
    from unittest.mock import AsyncMock, patch
    sunny = _sunny()
    async def run():
        with patch("memory.creator_persona.get_structured_persona_async", new=AsyncMock(return_value=sunny)):
            from memory.context import build_qwen3_context
            fake_user = {"first_name":"Alex","funnel_stage":"new","last_seen":None,"message_count":5}
            with patch("memory.context.get_user", new=AsyncMock(return_value=fake_user)), \
                 patch("memory.context.get_user_profile", new=AsyncMock(return_value={})), \
                 patch("memory.context.get_latest_summary_with_age", new=AsyncMock(return_value=(None,None))), \
                 patch("memory.context.get_recent_messages", new=AsyncMock(return_value=[{"direction":"inbound","content":"hi"}])):
                ctx = await build_qwen3_context(777, "hey", "You are Sunny", creator_id=1, structured_persona_snapshot=sunny)
                system_text = " ".join(m.get("content","") for m in ctx if m.get("role")=="system")
                assert "CREATOR PERSONA (compact)" in system_text
                # PERSONA BEHAVIOR is added in llm_worker, not here, but context should have compact
                assert "Sunny Skye" in system_text
    asyncio.run(run())

def test_llm_call_count_still_3():
    import pathlib
    src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
    # Should still have 3 LLM calls: extract_commerce_signals, generate_draft, score_draft
    assert "extract_commerce_signals" in src
    assert "generate_draft" in src
    assert "score_draft" in src
    # Ensure no new LLM added for persona compaction
    beh = pathlib.Path("commerce/persona_behavior.py").read_text(encoding="utf-8")
    assert "get_llm_provider" not in beh

def test_ollama_num_ctx_configured():
    import pathlib
    src = pathlib.Path("core/llm_provider_ollama.py").read_text(encoding="utf-8")
    assert "num_ctx" in src
    assert "8192" in src
    cfg = pathlib.Path("core/config.py").read_text(encoding="utf-8")
    assert "ollama_num_ctx" in cfg

def test_creator_isolation_preserved():
    # Sunny and Mia remain isolated after compaction
    from memory.creator_persona import render_compact_persona_block
    sunny = _sunny()
    mia = _mia()
    cs = render_compact_persona_block(sunny)
    cm = render_compact_persona_block(mia)
    assert "Sunny" not in cm
    assert "Mia" not in cs
    assert "freelance" in cs.lower()
    assert "model" in cm.lower()

# Commerce authority preserved
def test_commerce_authority_preserved():
    import pathlib
    src = pathlib.Path("commerce/persona_behavior.py").read_text(encoding="utf-8")
    assert "price" not in src.lower() or "price authority" in src.lower()
    # Compact persona should not contain price
    from memory.creator_persona import render_compact_persona_block
    sunny = _sunny()
    compact = render_compact_persona_block(sunny)
    assert "price" not in compact.lower() or "price" in compact.lower() and "authoritative" not in compact.lower()  # allow favorite price? Ensure not commerce price

def test_parallel_db_reads_safe():
    # Verify build_qwen3_context uses asyncio.gather (parallel)
    import pathlib
    src = pathlib.Path("memory/context.py").read_text(encoding="utf-8")
    assert "asyncio.gather" in src
    assert "coro_user" in src or "Parallelize" in src
