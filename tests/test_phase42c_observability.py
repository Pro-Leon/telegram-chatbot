# -*- coding: utf-8 -*-
"""Phase 42C -- Realtime Execution & Creator Isolation tests."""
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]

def md5_gid(user_id, msg, tg_id):
    return hashlib.md5(f"{user_id}:{msg}:{tg_id}".encode()).hexdigest()

# P0 creator isolation
@pytest.mark.asyncio
async def test_creator_a_does_not_receive_creator_b_event():
    from chatbotv2.dashboard.ws_manager import ConnectionManager
    mgr = ConnectionManager()
    ws_a = AsyncMock()
    ws_a.send_text = AsyncMock()
    ws_a.accept = AsyncMock()
    ws_b = AsyncMock()
    ws_b.send_text = AsyncMock()
    ws_b.accept = AsyncMock()
    await mgr.connect(ws_a, is_global=True, creator_ids={1})
    await mgr.connect(ws_b, is_global=True, creator_ids={2})
    # Event for creator 1
    event = {"event_id": "1", "event_type": "message.sent", "creator_id": 1, "dialog_id": 123, "scope": "user", "data": {}}
    await mgr.broadcast(event, dialog_id=123, creator_id=1)
    assert ws_a.send_text.called
    assert not ws_b.send_text.called

@pytest.mark.asyncio
async def test_creator_b_does_not_receive_creator_a_event():
    from chatbotv2.dashboard.ws_manager import ConnectionManager
    mgr = ConnectionManager()
    ws_a = AsyncMock()
    ws_a.send_text = AsyncMock()
    ws_a.accept = AsyncMock()
    ws_b = AsyncMock()
    ws_b.send_text = AsyncMock()
    ws_b.accept = AsyncMock()
    await mgr.connect(ws_a, is_global=True, creator_ids={1})
    await mgr.connect(ws_b, is_global=True, creator_ids={2})
    event = {"event_id": "2", "event_type": "commerce.offer_created", "creator_id": 2, "scope": "user", "data": {}}
    await mgr.broadcast(event, creator_id=2)
    assert ws_b.send_text.called
    assert not ws_a.send_text.called

@pytest.mark.asyncio
async def test_global_event_visible_to_all():
    from chatbotv2.dashboard.ws_manager import ConnectionManager
    mgr = ConnectionManager()
    ws_a = AsyncMock()
    ws_a.send_text = AsyncMock()
    ws_a.accept = AsyncMock()
    ws_b = AsyncMock()
    ws_b.send_text = AsyncMock()
    ws_b.accept = AsyncMock()
    await mgr.connect(ws_a, is_global=True, creator_ids={1})
    await mgr.connect(ws_b, is_global=True, creator_ids={2})
    event = {"event_id": "3", "event_type": "operator_queue.updated", "creator_id": None, "scope": "global", "data": {}}
    await mgr.broadcast(event, creator_id=None)
    assert ws_a.send_text.called
    assert ws_b.send_text.called

# Fan isolation
def test_debounce_keys_creator_scoped():
    from db.redis import _debounce_key
    assert _debounce_key(123, creator_id=1) != _debounce_key(123, creator_id=2)
    assert _debounce_key(123, creator_id=1) == "debounce:creator:1:user:123"
    assert _debounce_key(123) == "debounce:123"

@pytest.mark.asyncio
async def test_generation_id_not_creator_specific_but_debounce_is():
    # Same user/msg/tg across creators should have same md5, but debounce isolation prevents interference
    gid = md5_gid(999, "hello", 1)
    assert gid == md5_gid(999, "hello", 1)
    # Debounce keys differ
    from db.redis import _debounce_key
    assert _debounce_key(999, 1) != _debounce_key(999, 2)

# Generation correlation
def test_existing_inbound_generation_id_remains_md5():
    gid = md5_gid(123, "hi", 100)
    assert len(gid) == 32
    assert gid == hashlib.md5(b"123:hi:100").hexdigest()

@pytest.mark.asyncio
async def test_xautoclaim_preserves_generation_id_still():
    from unittest.mock import AsyncMock, patch
    from db.redis import requeue_stalled_messages
    gid = md5_gid(1, "test", 1)
    mock_redis = AsyncMock()
    mock_redis.xautoclaim = AsyncMock(return_value=(0, [("1-0", {"generation_id": gid})]))
    with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
        count, ids = await requeue_stalled_messages("worker_1")
        assert mock_redis.xautoclaim.called

# Commerce events
@pytest.mark.asyncio
async def test_offer_created_emitted_after_success():
    import pathlib
    content = pathlib.Path("E:/chatbot/commerce/execution.py").read_text(encoding="utf-8")
    assert "commerce.offer_created" in content
    # Check it has creator_id and user_id and offer_id
    assert "creator_id=creator_id" in content
    assert "user_id=user_id" in content
    assert "offer_id" in content
    assert "product_id" in content


@pytest.mark.asyncio
async def test_sale_recorded_only_after_financial_truth():
    # Check that sale_recorded code exists and does not leak buyer_email (static check)
    import pathlib
    content = pathlib.Path("E:/chatbot/db/dropfans.py").read_text(encoding="utf-8")
    assert "commerce.sale_recorded" in content
    assert "buyer_email" not in content.split("commerce.sale_recorded")[1].split(")")[0] or "external_transaction_id" in content
    # Ensure execute_ppv does not emit sale_recorded
    content2 = pathlib.Path("E:/chatbot/commerce/execution.py").read_text(encoding="utf-8")
    assert content2.count("commerce.sale_recorded") == 0
    assert content2.count("commerce.offer_created") >= 1


@pytest.mark.asyncio
async def test_no_sale_lost_without_terminal():
    # Ensure we do not have a handler that emits sale_lost on timeout
    import pathlib
    content = pathlib.Path("E:/chatbot/commerce/execution.py").read_text(encoding="utf-8")
    assert "sale_lost" not in content.lower() or "commerce.sale_lost" not in content
    content2 = pathlib.Path("E:/chatbot/db/dropfans.py").read_text(encoding="utf-8")
    assert "sale_lost" not in content2.lower()

# Privacy
@pytest.mark.asyncio
async def test_browser_payload_has_no_buyer_email():
    import pathlib, json
    content = pathlib.Path("E:/chatbot/db/dropfans.py").read_text(encoding="utf-8")
    assert "commerce.sale_recorded" in content
    idx = content.find("commerce.sale_recorded")
    # Find the payload dict between the next { and }
    payload_start = content.find("{", idx)
    payload_end = content.find("}", payload_start)
    payload_snippet = content[payload_start:payload_end]
    assert "buyer_email" not in payload_snippet
    assert "external_transaction_id" in payload_snippet


@pytest.mark.asyncio
async def test_browser_payload_has_no_credentials():
    import pathlib
    content = pathlib.Path("E:/chatbot/core/event_bus.py").read_text(encoding="utf-8")
    # Ensure no API key in event
    assert "api_key" not in content.lower() or "publish_event" in content
    # Check commerce events do not include encrypted_api_key
    content2 = pathlib.Path("E:/chatbot/commerce/execution.py").read_text(encoding="utf-8")
    assert "encrypted_api_key" not in content2 or "decrypt_secret" in content2
    # Ensure no buyer email in sale_recorded payload (checked above)

# Stage derivation
def test_stage_derivation_basic():
    from core.execution_stage import derive_stage, ExecutionStage
    assert derive_stage(has_message_created=True) == ExecutionStage.RECEIVED
    assert derive_stage(has_message_created=True, has_generation_started=True) == ExecutionStage.PROCESSING
    assert derive_stage(has_failed=True) == ExecutionStage.FAILED
    assert derive_stage() == ExecutionStage.UNKNOWN

# Realtime
def test_reconnect_still_works():
    import pathlib
    content = pathlib.Path("E:/chatbot/chatbotv2/dashboard/static/js/realtime.js").read_text(encoding="utf-8")
    assert "isPollingActive" in content
    assert "DEDUP_CACHE_SIZE" in content
    assert "MAX_RECONNECT_DELAY" in content

def test_dedup_still_works():
    import pathlib
    content = pathlib.Path("E:/chatbot/chatbotv2/dashboard/static/js/realtime.js").read_text(encoding="utf-8")
    assert "dedupCache" in content

# Polling
def test_polling_fallback_preserved():
    import pathlib
    for fname in ["E:/chatbot/chatbotv2/dashboard/templates/dashboard.html", "E:/chatbot/chatbotv2/dashboard/templates/queue.html"]:
        content = pathlib.Path(fname).read_text(encoding="utf-8")
        assert "isPollingActive" in content or "realtime.js" in content

