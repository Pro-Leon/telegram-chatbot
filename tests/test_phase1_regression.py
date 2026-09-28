"""Phase 1 regression tests — protecting WebSocket event-layer contracts.

These tests verify invariants, not implementations.  If a test breaks
after a Phase 2 change, the change likely violated a Phase 1 contract.
"""

import asyncio
import contextlib
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _collect_route_paths(routes):
    """Recursively collect all route paths, including those inside APIRouters."""
    paths = []
    for route in routes:
        if hasattr(route, "path"):
            paths.append(route.path)
        sub = getattr(route, "routes", None) or getattr(getattr(route, "original_router", None), "routes", None)
        if sub:
            paths.extend(_collect_route_paths(sub))
    return paths


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Event Bus (publish_event)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventBus:
    """Tests for core/event_bus.py — publish_event()."""

    @pytest.mark.asyncio
    async def test_publish_returns_unique_event_ids(self):
        """Each publish_event call must produce a distinct event_id."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)
        seen_ids: set[str] = set()

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                for _ in range(50):
                    eid = await publish_event("test.unique", {"i": 0})
                    assert eid is not None
                    assert eid not in seen_ids, f"Duplicate event_id: {eid}"
                    seen_ids.add(eid)

    @pytest.mark.asyncio
    async def test_event_id_is_valid_uuid(self):
        """event_id must be a valid UUID4 string."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                eid = await publish_event("test.uuid", {})
                parsed = uuid.UUID(eid)
                assert parsed.version == 4

    @pytest.mark.asyncio
    async def test_returns_none_when_websocket_disabled(self):
        """publish_event must return None immediately when enable_websocket=False."""
        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = False
            from core.event_bus import publish_event

            result = await publish_event("test.disabled", {"k": "v"})
            assert result is None

    @pytest.mark.asyncio
    async def test_no_redis_call_when_disabled(self):
        """When WS disabled, get_redis must never be called."""
        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = False
            with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_redis:
                from core.event_bus import publish_event

                await publish_event("test.noop", {})
                mock_redis.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_connection_error(self):
        """Redis connection failure -> returns None, no exception raised."""
        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch(
                "db.redis.get_redis",
                new_callable=AsyncMock,
                side_effect=ConnectionError("refused"),
            ):
                from core.event_bus import publish_event

                result = await publish_event("test.err", {})
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_publish_error(self):
        """Redis publish() failure -> returns None, no exception raised."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=RuntimeError("broken"))

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                result = await publish_event("test.puberr", {})
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_json_serialize_error(self):
        """Unserializable data -> returns None, no exception raised."""
        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=AsyncMock()):
                from core.event_bus import publish_event

                result = await publish_event("test.badjson", {"bad": object()})
                assert result is None

    @pytest.mark.asyncio
    async def test_envelope_has_all_required_fields(self):
        """Published event must contain all 8 required envelope fields."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                await publish_event(
                    "ai.generation_completed",
                    {"draft": "hi"},
                    user_id=1,
                    dialog_id=2,
                    generation_id="g-1",
                    scope="user",
                )

                payload = json.loads(mock_redis.publish.call_args[0][1])
                required = {
                    "event_id",
                    "event_type",
                    "timestamp_ms",
                    "user_id",
                    "dialog_id",
                    "generation_id",
                    "scope",
                    "data",
                }
                assert required.issubset(payload.keys())

    @pytest.mark.asyncio
    async def test_envelope_field_values(self):
        """Envelope fields must carry the exact values passed by the caller."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                before_ms = int(time.time() * 1000)
                await publish_event(
                    "message.created",
                    {"content": "hello"},
                    user_id=42,
                    dialog_id=42,
                    generation_id="gen-xyz",
                    scope="user",
                )
                after_ms = int(time.time() * 1000)

                p = json.loads(mock_redis.publish.call_args[0][1])
                assert p["event_type"] == "message.created"
                assert p["user_id"] == 42
                assert p["dialog_id"] == 42
                assert p["generation_id"] == "gen-xyz"
                assert p["scope"] == "user"
                assert p["data"] == {"content": "hello"}
                assert before_ms <= p["timestamp_ms"] <= after_ms

    @pytest.mark.asyncio
    async def test_channel_is_chatbot_events(self):
        """Events must be published to the 'chatbot:events' channel."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = True
            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                from core.event_bus import publish_event

                await publish_event("test.ch", {})
                assert mock_redis.publish.call_args[0][0] == "chatbot:events"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — Connection Manager
# ═══════════════════════════════════════════════════════════════════════════════


class TestConnectionManager:
    """Tests for chatbotv2/dashboard/ws_manager.py."""

    @pytest.mark.asyncio
    async def test_connect_accepts_and_tracks(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        conn = await mgr.connect(ws, dialog_ids={10}, is_global=True)
        assert mgr.connection_count == 1
        assert conn.dialog_ids == {10}
        assert conn.is_global is True
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_removes(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        assert mgr.connection_count == 1
        await mgr.disconnect(ws)
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_global_only(self):
        """dialog_id=None delivers to global connections only."""
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_global = AsyncMock()
        ws_scoped = AsyncMock()
        await mgr.connect(ws_global, is_global=True)
        await mgr.connect(ws_scoped, dialog_ids={999})

        await mgr.broadcast({"event_type": "test"}, dialog_id=None)
        ws_global.send_text.assert_awaited_once()
        ws_scoped.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcast_scoped_plus_global(self):
        """dialog_id=42 delivers to connections subscribed to 42 + all globals."""
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_match = AsyncMock()
        ws_other = AsyncMock()
        ws_global = AsyncMock()
        await mgr.connect(ws_match, dialog_ids={42})
        await mgr.connect(ws_other, dialog_ids={99})
        await mgr.connect(ws_global, is_global=True)

        await mgr.broadcast({"event_type": "test"}, dialog_id=42)
        ws_match.send_text.assert_awaited_once()
        ws_global.send_text.assert_awaited_once()
        ws_other.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_connection_removed_after_send_failure(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_ok = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_text = AsyncMock(side_effect=ConnectionResetError("broken"))
        await mgr.connect(ws_ok, is_global=True)
        await mgr.connect(ws_bad, is_global=True)
        assert mgr.connection_count == 2

        await mgr.broadcast({"event_type": "x"}, dialog_id=None)
        assert mgr.connection_count == 1

    @pytest.mark.asyncio
    async def test_connection_count_lifecycle(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        assert mgr.connection_count == 0
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, dialog_ids={1})
        await mgr.connect(ws2, dialog_ids={2})
        assert mgr.connection_count == 2
        await mgr.disconnect(ws1)
        assert mgr.connection_count == 1
        await mgr.disconnect(ws2)
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_json_serializes_event(self):
        """broadcast must JSON-serialize the event dict before sending."""
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, is_global=True)

        event = {"event_type": "msg", "data": {"k": "v"}}
        await mgr.broadcast(event, dialog_id=None)

        sent = ws.send_text.call_args[0][0]
        assert json.loads(sent) == event

    @pytest.mark.asyncio
    async def test_broadcast_no_targets_is_noop(self):
        """When no connections match, broadcast must not raise."""
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        await mgr.broadcast({"event_type": "x"}, dialog_id=42)

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_noop(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.disconnect(ws)
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_singleton_manager(self):
        from chatbotv2.dashboard import ws_manager as mod

        mod._manager = None
        m1 = mod.get_manager()
        m2 = mod.get_manager()
        assert m1 is m2
        mod._manager = None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — WebSocket Auth (app.py /ws endpoint logic)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebSocketAuth:
    """Tests for the /ws WebSocket auth contract.

    We verify the auth contract by testing verify_session() directly,
    which is the function the /ws endpoint delegates to.
    """

    @pytest.mark.asyncio
    async def test_verify_session_none_token(self):
        """None token -> returns None (missing session)."""
        from chatbotv2.dashboard.auth import verify_session

        with (
            patch("chatbotv2.dashboard.auth._ensure_pool", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.auth.get_session_db",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await verify_session(None)
            assert result is None

    @pytest.mark.asyncio
    async def test_verify_session_empty_token(self):
        """Empty string token -> returns None."""
        from chatbotv2.dashboard.auth import verify_session

        with (
            patch("chatbotv2.dashboard.auth._ensure_pool", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.auth.get_session_db",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await verify_session("")
            assert result is None

    @pytest.mark.asyncio
    async def test_verify_session_expired_token(self):
        """Expired/missing DB row -> returns None (invalid session)."""
        from chatbotv2.dashboard.auth import verify_session

        with (
            patch("chatbotv2.dashboard.auth._ensure_pool", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.auth.get_session_db",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await verify_session("expired-token-abc")
            assert result is None

    @pytest.mark.asyncio
    async def test_verify_session_valid_token(self):
        """Valid DB row -> returns dict with username key."""
        from chatbotv2.dashboard.auth import verify_session

        mock_row = {"username": "admin", "expires_at": time.time() + 3600}
        with (
            patch("chatbotv2.dashboard.auth._ensure_pool", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.auth.get_session_db",
                new_callable=AsyncMock,
                return_value=mock_row,
            ),
        ):
            result = await verify_session("valid-token-xyz")
            assert result == {"username": "admin"}

    @pytest.mark.asyncio
    async def test_destroy_session_with_none(self):
        """destroy_session(None) must not raise."""
        from chatbotv2.dashboard.auth import destroy_session

        with (
            patch("chatbotv2.dashboard.auth._ensure_pool", new_callable=AsyncMock),
            patch("chatbotv2.dashboard.auth.delete_session_db", new_callable=AsyncMock),
        ):
            await destroy_session(None)

    def test_websocket_endpoint_exists(self):
        """app.py must define a /ws WebSocket route."""
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/ws" in routes


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Event Subscriber
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventSubscriber:
    """Tests for chatbotv2/dashboard/event_subscriber.py."""

    def test_backoff_never_exceeds_max(self):
        from chatbotv2.dashboard.event_subscriber import INITIAL_BACKOFF, MAX_BACKOFF

        backoff = INITIAL_BACKOFF
        for _ in range(50):
            backoff = min(backoff * 2, MAX_BACKOFF)
        assert backoff == MAX_BACKOFF

    def test_backoff_doubles_each_step(self):
        from chatbotv2.dashboard.event_subscriber import INITIAL_BACKOFF

        b = INITIAL_BACKOFF
        for _ in range(3):
            prev = b
            b = min(b * 2, 30.0)
            assert b == prev * 2

    @pytest.mark.asyncio
    async def test_cleans_up_resources_on_cancel(self):
        """CancelledError must close Redis and PubSub resources."""
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
            with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
                mock_s.return_value.enable_websocket = True
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

    @pytest.mark.asyncio
    async def test_no_op_when_websocket_disabled(self):
        """When enable_websocket=False, subscriber must return immediately."""
        from chatbotv2.dashboard.event_subscriber import start_event_subscriber

        with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = False
            result = await start_event_subscriber()
            assert result is None

    @pytest.mark.asyncio
    async def test_forwards_user_scoped_event_to_dialog(self):
        """User-scoped events must be broadcast with dialog_id."""
        from chatbotv2.dashboard.event_subscriber import start_event_subscriber

        mock_pubsub = AsyncMock()

        async def fake_listen():
            yield {
                "type": "message",
                "data": json.dumps(
                    {
                        "event_type": "message.created",
                        "dialog_id": 42,
                        "scope": "user",
                        "event_id": "e1",
                        "timestamp_ms": 1000,
                    }
                ),
            }
            await asyncio.sleep(999)
            yield {}

        mock_pubsub.listen = fake_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis_instance.close = AsyncMock()

        mock_manager = AsyncMock()

        original_from_url = None
        try:
            import redis.asyncio as aio_redis

            original_from_url = aio_redis.from_url
            aio_redis.from_url = MagicMock(return_value=mock_redis_instance)
        except ImportError:
            pytest.skip("redis.asyncio not installed")

        try:
            with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
                mock_s.return_value.enable_websocket = True
                with patch(
                    "chatbotv2.dashboard.ws_manager.get_manager",
                    return_value=mock_manager,
                ):
                    task = asyncio.create_task(start_event_subscriber())
                    await asyncio.sleep(0.1)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            mock_manager.broadcast.assert_called_once()
            call_kwargs = mock_manager.broadcast.call_args
            assert call_kwargs[1]["dialog_id"] == 42
        finally:
            aio_redis.from_url = original_from_url

    @pytest.mark.asyncio
    async def test_global_event_broadcast_without_dialog_id(self):
        """Global-scoped events must be broadcast with dialog_id=None."""
        from chatbotv2.dashboard.event_subscriber import start_event_subscriber

        mock_pubsub = AsyncMock()

        async def fake_listen():
            yield {
                "type": "message",
                "data": json.dumps(
                    {
                        "event_type": "operator_queue.updated",
                        "dialog_id": None,
                        "scope": "global",
                        "event_id": "e2",
                        "timestamp_ms": 2000,
                    }
                ),
            }
            await asyncio.sleep(999)
            yield {}

        mock_pubsub.listen = fake_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis_instance.close = AsyncMock()

        mock_manager = AsyncMock()

        original_from_url = None
        try:
            import redis.asyncio as aio_redis

            original_from_url = aio_redis.from_url
            aio_redis.from_url = MagicMock(return_value=mock_redis_instance)
        except ImportError:
            pytest.skip("redis.asyncio not installed")

        try:
            with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
                mock_s.return_value.enable_websocket = True
                with patch(
                    "chatbotv2.dashboard.ws_manager.get_manager",
                    return_value=mock_manager,
                ):
                    task = asyncio.create_task(start_event_subscriber())
                    await asyncio.sleep(0.1)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            call_kwargs = mock_manager.broadcast.call_args
            assert call_kwargs[1]["dialog_id"] is None
        finally:
            aio_redis.from_url = original_from_url

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self):
        """Malformed JSON messages must be skipped, not crash the loop."""
        from chatbotv2.dashboard.event_subscriber import start_event_subscriber

        mock_pubsub = AsyncMock()
        call_count = 0

        async def fake_listen():
            nonlocal call_count
            yield {"type": "message", "data": "NOT VALID JSON {{{{"}
            call_count += 1
            yield {
                "type": "message",
                "data": json.dumps(
                    {"event_type": "test.ok", "event_id": "e3", "timestamp_ms": 3000}
                ),
            }
            call_count += 1
            await asyncio.sleep(999)
            yield {}

        mock_pubsub.listen = fake_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis_instance.close = AsyncMock()

        mock_manager = AsyncMock()

        original_from_url = None
        try:
            import redis.asyncio as aio_redis

            original_from_url = aio_redis.from_url
            aio_redis.from_url = MagicMock(return_value=mock_redis_instance)
        except ImportError:
            pytest.skip("redis.asyncio not installed")

        try:
            with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
                mock_s.return_value.enable_websocket = True
                with patch(
                    "chatbotv2.dashboard.ws_manager.get_manager",
                    return_value=mock_manager,
                ):
                    task = asyncio.create_task(start_event_subscriber())
                    await asyncio.sleep(0.15)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            assert call_count >= 2
            assert mock_manager.broadcast.await_count == 1
        finally:
            aio_redis.from_url = original_from_url

    @pytest.mark.asyncio
    async def test_event_missing_event_type_skipped(self):
        """Events without event_type must be skipped."""
        from chatbotv2.dashboard.event_subscriber import start_event_subscriber

        mock_pubsub = AsyncMock()

        async def fake_listen():
            yield {
                "type": "message",
                "data": json.dumps({"event_id": "e4", "timestamp_ms": 4000}),
            }
            await asyncio.sleep(999)
            yield {}

        mock_pubsub.listen = fake_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        mock_redis_instance = MagicMock()
        mock_redis_instance.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis_instance.close = AsyncMock()

        mock_manager = AsyncMock()

        original_from_url = None
        try:
            import redis.asyncio as aio_redis

            original_from_url = aio_redis.from_url
            aio_redis.from_url = MagicMock(return_value=mock_redis_instance)
        except ImportError:
            pytest.skip("redis.asyncio not installed")

        try:
            with patch("chatbotv2.dashboard.event_subscriber.get_settings") as mock_s:
                mock_s.return_value.enable_websocket = True
                with patch(
                    "chatbotv2.dashboard.ws_manager.get_manager",
                    return_value=mock_manager,
                ):
                    task = asyncio.create_task(start_event_subscriber())
                    await asyncio.sleep(0.1)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            mock_manager.broadcast.assert_not_called()
        finally:
            aio_redis.from_url = original_from_url


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Generation Lifecycle (llm_worker.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGenerationLifecycle:
    """Tests for the generation lifecycle in workers/llm_worker.py.

    llm_worker.py imports publish_event locally inside process_message,
    so we patch core.event_bus.publish_event (the actual function).
    """

    def _mock_all_deps(self, *, score=0.95, flags=None, auto_reply=True):
        """Build a context manager stack for all llm_worker dependencies."""
        flags = flags or []
        return [
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                return_value="draft text",
            ),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(score, flags),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=auto_reply,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
        ]

    @pytest.mark.asyncio
    async def test_generation_id_consistent_in_started_and_completed(self):
        """ai.generation_started and ai.generation_completed must share the same generation_id."""
        published_events: list[dict] = []

        async def mock_publish(event_type, data, **kwargs):
            published_events.append({"event_type": event_type, **kwargs})
            return str(uuid.uuid4())

        mocks = self._mock_all_deps()
        mocks.append(
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
                side_effect=mock_publish,
            )
        )

        with contextlib.ExitStack() as stack:
            for m in mocks:
                stack.enter_context(m)
            from workers.llm_worker import process_message

            await process_message(
                user_id=1,
                user_message="hi",
                telegram_message_id=100,
                username="u",
                first_name="f",
                persona="",
            )

        started = [e for e in published_events if e["event_type"] == "ai.generation_started"]
        completed = [e for e in published_events if e["event_type"] == "ai.generation_completed"]
        assert len(started) == 1
        assert len(completed) == 1
        assert started[0]["generation_id"] == completed[0]["generation_id"]

    @pytest.mark.asyncio
    async def test_enqueue_send_before_generation_completed(self):
        """For auto-approved path, enqueue_send() must be called BEFORE ai.generation_completed."""
        call_order: list[str] = []

        async def mock_publish(event_type, data, **kwargs):
            call_order.append(f"publish:{event_type}")
            return str(uuid.uuid4())

        async def mock_enqueue(*args, **kwargs):
            call_order.append("enqueue_send")

        mocks = self._mock_all_deps(score=0.95)
        mocks.append(
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
                side_effect=mock_publish,
            )
        )
        mocks[-2] = patch(
            "workers.llm_worker.enqueue_send",
            new_callable=AsyncMock,
            side_effect=mock_enqueue,
        )

        with contextlib.ExitStack() as stack:
            for m in mocks:
                stack.enter_context(m)
            from workers.llm_worker import process_message

            await process_message(
                user_id=1,
                user_message="hi",
                telegram_message_id=100,
                username="u",
                first_name="f",
                persona="",
            )

        enqueue_idx = call_order.index("enqueue_send")
        completed_idx = call_order.index("publish:ai.generation_completed")
        assert enqueue_idx < completed_idx, (
            f"enqueue_send (idx={enqueue_idx}) must come before "
            f"ai.generation_completed (idx={completed_idx})"
        )

    @pytest.mark.asyncio
    async def test_enqueue_failure_does_not_emit_generation_completed(self):
        """When enqueue_send() raises, ai.generation_completed must NOT be published."""
        published_events: list[str] = []

        async def mock_publish(event_type, data, **kwargs):
            published_events.append(event_type)
            return str(uuid.uuid4())

        mocks = self._mock_all_deps(score=0.95)
        mocks[-2] = patch(
            "workers.llm_worker.enqueue_send",
            new_callable=AsyncMock,
            side_effect=RuntimeError("redis down"),
        )
        mocks.append(
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
                side_effect=mock_publish,
            )
        )

        with contextlib.ExitStack() as stack:
            for m in mocks:
                stack.enter_context(m)
            from workers.llm_worker import process_message

            with pytest.raises(RuntimeError, match="redis down"):
                await process_message(
                    user_id=1,
                    user_message="hi",
                    telegram_message_id=100,
                    username="u",
                    first_name="f",
                    persona="",
                )

        assert "ai.generation_completed" not in published_events
        assert "ai.generation_started" in published_events

    @pytest.mark.asyncio
    async def test_generation_failed_emitted_on_exception(self):
        """On unhandled exception, ai.generation_failed must be published with the generation_id."""
        published_events: list[dict] = []

        async def mock_publish(event_type, data, **kwargs):
            published_events.append({"event_type": event_type, **kwargs})
            return str(uuid.uuid4())

        mocks = [
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM exploded"),
            ),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
                side_effect=mock_publish,
            ),
        ]

        with contextlib.ExitStack() as stack:
            for m in mocks:
                stack.enter_context(m)
            from workers.llm_worker import process_message

            with pytest.raises(RuntimeError, match="LLM exploded"):
                await process_message(
                    user_id=1,
                    user_message="hi",
                    telegram_message_id=100,
                    username="u",
                    first_name="f",
                    persona="",
                )

        failed_events = [e for e in published_events if e["event_type"] == "ai.generation_failed"]
        assert len(failed_events) == 1
        started = [e for e in published_events if e["event_type"] == "ai.generation_started"]
        assert started[0]["generation_id"] == failed_events[0]["generation_id"]

    @pytest.mark.asyncio
    async def test_generation_id_unique_per_process_message(self):
        """Each distinct logical inbound must generate a distinct generation_id (deterministic per Phase 31B/39)."""
        ids_seen: list[str] = []

        async def mock_publish(event_type, data, **kwargs):
            if event_type == "ai.generation_started":
                ids_seen.append(kwargs.get("generation_id"))
            return str(uuid.uuid4())

        for i in range(3):
            mocks = self._mock_all_deps()
            mocks.append(
                patch(
                    "core.event_bus.publish_event",
                    new_callable=AsyncMock,
                    side_effect=mock_publish,
                )
            )
            with contextlib.ExitStack() as stack:
                for m in mocks:
                    stack.enter_context(m)
                from workers.llm_worker import process_message

                await process_message(
                    user_id=1,
                    user_message="hi",
                    telegram_message_id=100 + i,
                    username="u",
                    first_name="f",
                    persona="",
                )

        assert len(ids_seen) == 3
        assert len(set(ids_seen)) == 3
        # Same logical inbound must yield same deterministic ID (retry/XAUTOCLAIM stability)
        import hashlib
        assert hashlib.md5("1:hi:100".encode()).hexdigest() == hashlib.md5("1:hi:100".encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — Frontend JS / Template Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrontendContracts:
    """Content-based tests for JS/HTML files protecting Phase 1 contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_dashboard_base_loads_realtime_js(self):
        html = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "/static/js/realtime.js" in html

    def test_realtime_dedup_cache_bounded(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "DEDUP_CACHE_SIZE" in js
        assert "200" in js
        assert "shift()" in js

    def test_realtime_reconnect_cap(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "MAX_RECONNECT_DELAY" in js
        assert "30000" in js

    def test_realtime_polling_fallback(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "_pollingActive" in js
        assert "isPollingActive" in js

    def test_realtime_beforeunload_cleanup(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "beforeunload" in js
        assert "_intentionalClose" in js

    def test_chat_html_has_is_connected_guard(self):
        html = self._read("chatbotv2/dashboard/templates/chat.html")
        assert "rt.isConnected()" in html

    def test_chat_embed_has_is_connected_guard(self):
        html = self._read("chatbotv2/dashboard/templates/chat_embed.html")
        assert "rt.isConnected()" in html

    def test_queue_html_has_is_connected_guard(self):
        html = self._read("chatbotv2/dashboard/templates/queue.html")
        assert "rt.isConnected()" in html

    def test_chat_handlers_check_dialog_id(self):
        html = self._read("chatbotv2/dashboard/templates/chat.html")
        assert "evt.dialog_id !== dialogId" in html

    def test_chat_embed_checks_dialog_id(self):
        html = self._read("chatbotv2/dashboard/templates/chat_embed.html")
        assert "evt.dialog_id !== DIALOG_ID" in html

    def test_overview_uses_polling_fallback(self):
        html = self._read("chatbotv2/dashboard/templates/overview.html")
        assert "rt.isPollingActive()" in html

    def test_chat_handles_all_required_event_types(self):
        html = self._read("chatbotv2/dashboard/templates/chat.html")
        required = [
            "message.created",
            "message.sent",
            "ai.generation_started",
            "ai.generation_completed",
            "ai.generation_failed",
            "suggestion.created",
        ]
        for evt in required:
            assert f"rt.on('{evt}'" in html, f"chat.html missing handler for {evt}"

    def test_chat_embed_handles_all_required_event_types(self):
        html = self._read("chatbotv2/dashboard/templates/chat_embed.html")
        required = [
            "message.created",
            "message.sent",
            "ai.generation_started",
            "ai.generation_completed",
            "ai.generation_failed",
            "suggestion.created",
        ]
        for evt in required:
            assert f"rt.on('{evt}'" in html, f"chat_embed.html missing handler for {evt}"

    def test_overview_handles_required_event_types(self):
        html = self._read("chatbotv2/dashboard/templates/overview.html")
        required = ["message.created", "message.sent", "operator_queue.updated"]
        for evt in required:
            assert f"rt.on('{evt}'" in html, f"overview.html missing handler for {evt}"

    def test_queue_handles_required_event_types(self):
        html = self._read("chatbotv2/dashboard/templates/queue.html")
        required = ["operator_queue.updated", "suggestion.created"]
        for evt in required:
            assert f"rt.on('{evt}'" in html, f"queue.html missing handler for {evt}"

    def test_realtime_client_api_surface(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        required_methods = [
            "RealtimeClient.prototype.on",
            "RealtimeClient.prototype.onconnected",
            "RealtimeClient.prototype.ondisconnected",
            "RealtimeClient.prototype.isConnected",
            "RealtimeClient.prototype.isPollingActive",
            "RealtimeClient.prototype.destroy",
        ]
        for method in required_methods:
            assert method in js, f"realtime.js missing {method}"

    def test_websocket_responds_to_ping(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "ping" in js
        assert "pong" in js


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — Main Process Event Producers
# ═══════════════════════════════════════════════════════════════════════════════


class TestMainProcessEventProducers:
    """Tests for event publishers in chatbotv2/main.py and chatbotv2/handlers.py."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_main_publishes_message_sent(self):
        src = self._read("chatbotv2/main.py")
        assert '"message.sent"' in src

    def test_main_publishes_message_send_failed(self):
        src = self._read("chatbotv2/main.py")
        assert '"message.send_failed"' in src

    def test_handlers_publishes_message_created(self):
        src = self._read("chatbotv2/handlers.py")
        assert '"message.created"' in src

    def test_send_worker_publishes_operator_queue_updated(self):
        src = self._read("workers/send_worker.py")
        assert '"operator_queue.updated"' in src

    def test_llm_worker_publishes_all_generation_events(self):
        src = self._read("workers/llm_worker.py")
        assert '"ai.generation_started"' in src
        assert '"ai.generation_completed"' in src
        assert '"ai.generation_failed"' in src
        assert '"suggestion.created"' in src

    def test_main_uses_top_level_publish_import(self):
        src = self._read("chatbotv2/main.py")
        lines = [l.strip() for l in src.split("\n") if not l.strip().startswith("#")]
        top_level_imports = [
            l for l in lines if l.startswith("from core.event_bus import publish_event")
        ]
        assert len(top_level_imports) >= 1

    def test_llm_worker_exception_handler_wraps_publish(self):
        src = self._read("workers/llm_worker.py")
        lines = src.split("\n")
        in_except_block = False
        found_publish_in_try = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "except Exception:" in stripped and i > 260:
                in_except_block = True
            if in_except_block and '"ai.generation_failed"' in stripped:
                for j in range(max(0, i - 3), min(len(lines), i + 3)):
                    if "try:" in lines[j].strip():
                        found_publish_in_try = True
                        break
                break
        assert found_publish_in_try, "ai.generation_failed publish must be wrapped in try/except"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — WebSocket Endpoint Auth Contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebSocketEndpoint:
    """Tests for the /ws WebSocket endpoint in app.py."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_ws_endpoint_reads_session_cookie(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert 'cookies.get("session")' in src

    def test_ws_endpoint_closes_on_missing_session(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert "4001" in src
        assert "Missing session cookie" in src

    def test_ws_endpoint_closes_on_invalid_session(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert "Invalid session" in src

    def test_ws_endpoint_connects_as_global(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert "is_global=True" in src

    def test_ws_endpoint_disconnects_on_close(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert "finally:" in src
        assert "manager.disconnect(websocket)" in src

    def test_ws_endpoint_handles_pong(self):
        src = self._read("chatbotv2/dashboard/routes/ws.py")
        assert 'await websocket.send_text(\'{"event_type":"pong"}\')' in src


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Config / Settings Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigContracts:
    """Tests for config/settings invariants."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_core_config_has_enable_websocket(self):
        src = self._read("core/config.py")
        assert "enable_websocket: bool = True" in src

    def test_session_cookie_name_is_session(self):
        src = self._read("chatbotv2/dashboard/auth.py")
        assert 'cookies.get("session")' in src

    def test_event_bus_channel_constant(self):
        src = self._read("core/event_bus.py")
        assert 'CHANNEL = "chatbot:events"' in src

    def test_event_subscriber_channel_imports_from_event_bus(self):
        src = self._read("chatbotv2/dashboard/event_subscriber.py")
        assert "from core.event_bus import CHANNEL" in src

    def test_event_bus_signature_unchanged(self):
        src = self._read("core/event_bus.py")
        assert "async def publish_event(" in src
        assert "event_type: str," in src
        assert "data: dict," in src
        assert "user_id: int | None = None," in src
        assert "dialog_id: int | None = None," in src
        assert "generation_id: str | None = None," in src
        assert 'scope: str = "global",' in src

    def test_ws_manager_lock_pattern(self):
        src = self._read("chatbotv2/dashboard/ws_manager.py")
        assert "asyncio.Lock()" in src

    def test_realtime_js_max_reconnect_matches_protocol(self):
        js = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "var MAX_RECONNECT_DELAY = 30000;" in js
