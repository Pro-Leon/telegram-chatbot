"""Tests for the realtime WebSocket event layer."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# 1. publish_event returns a UUID event_id on success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_event_returns_event_id():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(return_value=1)
    with patch("core.event_bus.get_settings") as mock_settings:
        mock_settings.return_value.enable_websocket = True
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.event_bus import publish_event

            result = await publish_event("test.event", {"key": "value"})
            assert result is not None
            assert isinstance(result, str)
            assert len(result) == 36  # UUID format


# ---------------------------------------------------------------------------
# 2. publish_event returns None when WebSocket is disabled
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_event_returns_none_when_disabled():
    with patch("core.event_bus.get_settings") as mock_settings:
        mock_settings.return_value.enable_websocket = False
        from core.event_bus import publish_event

        result = await publish_event("test.event", {"key": "value"})
        assert result is None


# ---------------------------------------------------------------------------
# 3. publish_event returns None on Redis failure (best-effort)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_event_returns_none_on_redis_failure():
    with patch("core.event_bus.get_settings") as mock_settings:
        mock_settings.return_value.enable_websocket = True
        with patch(
            "db.redis.get_redis", new_callable=AsyncMock, side_effect=RuntimeError("conn refused")
        ):
            from core.event_bus import publish_event

            result = await publish_event("test.event", {"key": "value"})
            assert result is None


# ---------------------------------------------------------------------------
# 4. publish_event sends correct event payload structure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_event_payload_structure():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(return_value=1)
    with patch("core.event_bus.get_settings") as mock_settings:
        mock_settings.return_value.enable_websocket = True
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from core.event_bus import publish_event

            await publish_event(
                "ai.generation_completed",
                {"draft": "hello"},
                user_id=123,
                dialog_id=456,
                generation_id="gen-abc",
                scope="user",
            )
            call_args = mock_redis.publish.call_args
            channel = call_args[0][0]
            payload = json.loads(call_args[0][1])
            assert channel == "chatbot:events"
            assert payload["event_type"] == "ai.generation_completed"
            assert payload["user_id"] == 123
            assert payload["dialog_id"] == 456
            assert payload["generation_id"] == "gen-abc"
            assert payload["scope"] == "user"
            assert payload["data"] == {"draft": "hello"}
            assert "event_id" in payload
            assert "timestamp_ms" in payload


# ---------------------------------------------------------------------------
# 5. ConnectionManager connect + disconnect lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_manager_connect_disconnect():
    from chatbotv2.dashboard.ws_manager import ConnectionManager

    mgr = ConnectionManager()
    ws = AsyncMock()

    conn = await mgr.connect(ws, dialog_ids={100}, is_global=True)
    assert mgr.connection_count == 1
    assert conn.dialog_ids == {100}
    assert conn.is_global is True
    ws.accept.assert_awaited_once()

    await mgr.disconnect(ws)
    assert mgr.connection_count == 0


# ---------------------------------------------------------------------------
# 6. ConnectionManager broadcast to global connections
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_manager_broadcast_global():
    from chatbotv2.dashboard.ws_manager import ConnectionManager

    mgr = ConnectionManager()
    ws_global = AsyncMock()
    ws_scoped = AsyncMock()

    await mgr.connect(ws_global, is_global=True)
    await mgr.connect(ws_scoped, dialog_ids={999})

    await mgr.broadcast({"event_type": "operator_queue.updated"}, dialog_id=None)

    ws_global.send_text.assert_awaited_once()
    ws_scoped.send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. ConnectionManager broadcast to scoped + global connections
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_manager_broadcast_scoped():
    from chatbotv2.dashboard.ws_manager import ConnectionManager

    mgr = ConnectionManager()
    ws_target = AsyncMock()
    ws_other = AsyncMock()
    ws_global = AsyncMock()

    await mgr.connect(ws_target, dialog_ids={42})
    await mgr.connect(ws_other, dialog_ids={99})
    await mgr.connect(ws_global, is_global=True)

    await mgr.broadcast({"event_type": "message.sent"}, dialog_id=42)

    ws_target.send_text.assert_awaited_once()
    ws_global.send_text.assert_awaited_once()
    ws_other.send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# 8. ConnectionManager removes stale connections after send failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_manager_removes_stale_connection():
    from chatbotv2.dashboard.ws_manager import ConnectionManager

    mgr = ConnectionManager()
    ws_ok = AsyncMock()
    ws_bad = AsyncMock()
    ws_bad.send_text = AsyncMock(side_effect=ConnectionResetError("broken pipe"))

    await mgr.connect(ws_ok, is_global=True)
    await mgr.connect(ws_bad, is_global=True)
    assert mgr.connection_count == 2

    await mgr.broadcast({"event_type": "test"}, dialog_id=None)

    assert mgr.connection_count == 1
    ws_ok.send_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. ConnectionManager connection_count property
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_manager_connection_count():
    from chatbotv2.dashboard.ws_manager import ConnectionManager

    mgr = ConnectionManager()
    assert mgr.connection_count == 0

    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await mgr.connect(ws1, dialog_ids={1})
    assert mgr.connection_count == 1
    await mgr.connect(ws2, dialog_ids={2})
    assert mgr.connection_count == 2

    await mgr.disconnect(ws1)
    assert mgr.connection_count == 1


# ---------------------------------------------------------------------------
# 10. event_subscriber backoff does not exceed MAX_BACKOFF
# ---------------------------------------------------------------------------
def test_event_subscriber_backoff_cap():
    from chatbotv2.dashboard.event_subscriber import INITIAL_BACKOFF, MAX_BACKOFF

    backoff = INITIAL_BACKOFF
    for _ in range(20):
        backoff = min(backoff * 2, MAX_BACKOFF)
    assert backoff == MAX_BACKOFF


# ---------------------------------------------------------------------------
# 11. event_subscriber: redis client and pubsub are closed on CancelledError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_subscriber_cleanup_on_cancel():
    """Verify that on CancelledError the subscriber closes redis + pubsub."""
    from chatbotv2.dashboard.event_subscriber import start_event_subscriber

    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    async def mock_listen():
        await asyncio.sleep(999)
        yield {}

    mock_pubsub.listen = mock_listen

    mock_redis_instance = MagicMock()
    mock_redis_instance.pubsub = MagicMock(return_value=mock_pubsub)
    mock_redis_instance.close = AsyncMock()

    original_from_url = None
    try:
        import redis.asyncio as aio_redis

        original_from_url = aio_redis.from_url
        aio_redis.from_url = MagicMock(return_value=mock_redis_instance)
    except ImportError:
        pytest.skip("redis.asyncio not installed")

    try:
        with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_settings:
            mock_settings.return_value.enable_websocket = True
            with patch("chatbotv2.dashboard.ws_manager.get_manager"):
                task = asyncio.create_task(start_event_subscriber())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert mock_pubsub.unsubscribe.await_count >= 1
        assert mock_redis_instance.close.await_count >= 1
    finally:
        aio_redis.from_url = original_from_url


# ---------------------------------------------------------------------------
# 12. get_manager returns singleton
# ---------------------------------------------------------------------------
def test_get_manager_singleton():
    import chatbotv2.dashboard.ws_manager as ws_mod

    ws_mod._manager = None
    m1 = ws_mod.get_manager()
    m2 = ws_mod.get_manager()
    assert m1 is m2
    ws_mod._manager = None
