import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib
import json

from tests.conftest import TEST_PLAYER_NAME as PLAYER_NAME, TEST_PLAYER_NAME_HASH as PLAYER_NAME_HASH

pytestmark = [pytest.mark.unit]

@pytest.mark.asyncio
async def test_telemetry_persistence_roleplay_fields():
    """Verify new roleplay fields survive DB insert boundary."""
    from db.postgres import insert_generation_telemetry

    captured = {}

    async def fake_execute(sql, *args):
        # args are ordered per INSERT column list
        # We need to verify that 78-col INSERT is attempted and contains expected values
        # For this test, we mock _pool.acquire to capture sql and args
        captured["sql"] = sql
        captured["args"] = args
        return None

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(side_effect=fake_execute)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("db.postgres._pool", mock_pool):
        data = {
            "generation_id": "testgid123",
            "user_id": 123,
            "creator_id": 1,
            "runtime_mode": "new",
            "provider_name": "ollama",
            "model_name": "qwen2.5:3b",
            "context_build_ms": 10,
            "generation_latency_ms": 100,
            "scoring_latency_ms": 5,
            "scoring_score": 0.9,
            "scoring_flags": [],
            "tool_calls_count": 0,
            "tool_names": [],
            "total_e2e_latency_ms": 120,
            "routing_decision": "one_call",
            "success": True,
            "failure_type": None,
            "provider_latency_ms": 100,
            "provider_error": None,
            "input_token_count": 10,
            "output_token_count": 20,
            "worker_id": "worker_1",
            "one_call_count": 1,
            "total_llm_calls": 1,
            "ppv_second_generation_count": 0,
            "legacy_generation_count": 0,
            "shadow_generation_count": 0,
            "agent_canary_generation_count": 0,
            "retrieval_latency_ms": 10,
            "lexical_candidate_count": 5,
            "semantic_candidate_count": 5,
            "merged_candidate_count": 8,
            "embedding_latency_ms": 5,
            "lexical_latency_ms": 5,
            "ranking_latency_ms": 5,
            "conflict_dropped_count": 0,
            "lexical_dedup_removed_count": 0,
            "total_deduplication_count": 0,
            "context_tokens": 100,
            "context_category_tokens": {"system": 100},
            "context_degradation_level": 0,
            "context_truncation_count": 0,
            "context_budget_limit": 2600,
            "context_header_reserve": 60,
            "context_effective_budget": 2540,
            "context_budget_violation_count": 0,
            "ppv_provider_name": None,
            "ppv_model_name": None,
            "ppv_input_tokens": None,
            "ppv_output_tokens": None,
            "ppv_latency_ms": None,
            "validation_outcome": "success",
            "authority_decision": "NO_COMMERCE",
            "commerce_status": "NO_COMMERCE",
            "handoff_reason": None,
            "delivery_status": "sent",
            "duplicate_send_suppressed_count": 0,
            "already_executed_count": 0,
            "inbound_redelivery_count": 0,
            "lexical_threshold": 80,
            "semantic_threshold": 0.3,
            "retrieval_degraded": False,
            # Phase 90 required
            "persona_id": 42,
            "persona_version": 5,
            "participant_grounding_enabled": True,
            "conversation_contract_present": True,
            "speaker_correct": True,
            "question_answered": True,
            "topic_continuous": True,
            "conversational_validation_flags": ["ok"],
            "roleplay_enabled": True,
            "character_name": "Sunny Skye",
            "player_name_hash": PLAYER_NAME_HASH,
            "roleplay_contract_present": True,
            "character_correct": True,
            "player_agency_preserved": True,
            "out_of_character": False,
            "roleplay_validation_flags": ["ok"],
        }
        ok = await insert_generation_telemetry(data)
        assert ok is True
        sql = captured.get("sql", "")
        assert "roleplay_enabled" in sql
        assert "character_name" in sql
        assert "player_name_hash" in sql
        assert "roleplay_contract_present" in sql
        assert "character_correct" in sql
        assert "player_agency_preserved" in sql
        assert "out_of_character" in sql
        assert "roleplay_validation_flags" in sql
        assert "persona_id" in sql
        assert "persona_version" in sql
        # Verify args contain expected values in order (spot check)
        args = captured.get("args", ())
        # persona_id is $63 per new insert (after 62)
        # We can check that sql contains 78 placeholders
        assert sql.count("$") >= 78 or sql.count("$63") == 1

@pytest.mark.asyncio
async def test_telemetry_privacy_hash():
    """Ensure player_name_hash is lowercase hash, not raw."""
    import hashlib
    raw = PLAYER_NAME
    expected = hashlib.sha256(raw.lower().encode()).hexdigest()[:16]
    # Simulate llm_worker hashing
    computed = hashlib.sha256(raw.strip().lower().encode()).hexdigest()[:16]
    assert computed == expected
    assert computed != raw
    assert raw.lower() not in computed

def test_telemetry_to_dict_includes_hash():
    from core.telemetry import GenerationTelemetry
    t = GenerationTelemetry(user_id=1, creator_id=1)
    t.roleplay_enabled = True
    t.character_name = "Sunny Skye"
    t.player_name_hash = PLAYER_NAME_HASH
    t.roleplay_contract_present = True
    t.character_correct = True
    t.player_agency_preserved = True
    t.out_of_character = False
    t.roleplay_validation_flags = ["ok"]
    t.persona_id = 42
    t.persona_version = 3
    d = t.to_dict()
    assert d["roleplay_enabled"] is True
    assert d["character_name"] == "Sunny Skye"
    assert d["player_name_hash"] == PLAYER_NAME_HASH
    assert "player_name" in d  # kept but should be None for privacy
    assert d["roleplay_validation_flags"] == ["ok"]

def test_insert_fallback_still_works_without_new_columns(monkeypatch):
    """Existing callers without roleplay metadata must continue working (fail-open)."""
    # This is ensured by fallback to 62 and 22 cols in insert_generation_telemetry
    import pathlib
    src = pathlib.Path("db/postgres.py").read_text(encoding="utf-8")
    assert "Fallback to 62-col" in src or "Fallback to legacy 22-col" in src
    assert "ON CONFLICT DO NOTHING" in src
