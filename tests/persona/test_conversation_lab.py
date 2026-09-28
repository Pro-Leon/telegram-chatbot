import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_PLAYER_NAME as PLAYER_NAME, CHARACTER_NAME

pytestmark = [pytest.mark.unit]

def test_lab_cannot_deliver():
    # Verify lab route does not call enqueue_send or commerce execution (real calls, not comments)
    import pathlib
    import re
    src = pathlib.Path("chatbotv2/dashboard/routes/lab.py").read_text(encoding="utf-8")
    # Check for actual import or await, ignore comments mentioning the name
    assert "from db.redis import enqueue_send" not in src
    assert "await enqueue_send" not in src
    assert re.search(r"\benqueue_send\s*\(", src) is None, "Lab must not call enqueue_send"
    assert "send_messages" not in src or "DISABLE" in src.upper()
    # Lab should not invoke process_message (uses OneCall directly)
    assert "process_message" not in src or "OneCall" in src

@pytest.mark.asyncio
async def test_lab_one_call_count_and_model():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    # Mock auth
    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth

    # Mock DB persona and OneCall
    with patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value={"identity": {"name": "Sunny Skye"}, "_persona_id": 1, "_persona_version": 2}), \
         patch("core.one_call_pipeline.one_call_generation", new_callable=AsyncMock) as mock_onecall:

        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals
        mock_onecall.return_value = OneCallResult(
            reply=f"Hi {PLAYER_NAME}, I'm {CHARACTER_NAME}! My favorite workout is dancing",
            signals=CommerceSignals.low_information(),
            confidence=0.9,
            needs_handoff=False,
            is_valid=True,
            provider_name="ollama",
            model_name="qwen2.5:3b",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            generation_kind="one_call",
            call_index=1,
        )
        # Need to mock get_pool for persona text fetch inside lab
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value={"instructions": "You are Sunny Skye"})
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch("db.postgres.get_pool", return_value=mock_pool):
            from fastapi.testclient import TestClient
            client = TestClient(app)
            resp = client.post("/api/lab/execute", json={
                "creator_id": 1,
                "fan_message": "What's your favorite workout?",
                "history": [{"direction": "inbound", "content": "hi"}, {"direction": "outbound", "content": "hi sunny here"}],
                "fan_first_name": PLAYER_NAME
            })
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["model"] == "qwen2.5:3b"
            assert data["provider"] == "ollama"
            assert data["llm_call_count"] == 1
            assert data["total_llm_calls"] == 1
            assert data["ppv"] is False
            assert "participants" in data
            assert data["participants"]["speaker_name"] == CHARACTER_NAME
            assert data["participants"]["listener_name"] == PLAYER_NAME
            assert "contract" in data
            # Phase 89R: canonical is "character", legacy alias "speaker" accepted via dual equality
            assert data["contract"]["question_target"] in ("speaker", "character")
            assert data["contract"]["answer_required"] is True
            # Phase 89R: roleplay contract visible
            assert "roleplay" in data
            assert data["roleplay"]["character"] == CHARACTER_NAME
            assert data["roleplay"]["player"] == PLAYER_NAME
            assert data["roleplay"]["current_turn_owner"] == "player"
            assert data["roleplay"]["response_owner"] == "character"

    app.dependency_overrides.clear()

def test_telemetry_correlation():
    # Check telemetry has persona_id correlation logic in workers
    import pathlib
    src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
    assert "persona_id" in src
    assert "participant_grounding_enabled" in src
    assert "conversation_contract_present" in src
    assert "persona_feedback" in src or "speaker_correct" in src
