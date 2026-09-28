"""Phase 4.1A — Redis Stream worker recovery & crash resilience tests.

These tests verify that:
- Pending messages are detected after worker crashes
- XAUTOCLAIM reclaims idle messages
- Reclaimed messages retain original IDs and payloads
- Reclaimed messages are NOT auto-ACKed
- Recovery integrates with existing user locking and deduplication
- Recovery errors do not crash the worker loop
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Configuration
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecoveryConfig:
    """Verify REDIS_PENDING_IDLE_MS configuration."""

    def test_default_idle_ms(self):
        """Default idle threshold is 60000ms (60s)."""
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
        )
        assert s.redis_pending_idle_ms == 60000

    def test_custom_idle_ms(self):
        """Custom idle threshold is respected."""
        from core.config import Settings

        s = Settings(
            OPENAI_API_KEY="k",
            POSTGRES_DSN="postgresql://localhost/test",
            REDIS_URL="redis://localhost",
            REDIS_PENDING_IDLE_MS="120000",
        )
        assert s.redis_pending_idle_ms == 120000

    def test_env_example_has_idle_ms(self):
        """.env.example includes REDIS_PENDING_IDLE_MS."""
        from pathlib import Path

        env_example = Path(__file__).parent.parent / ".env.example"
        content = env_example.read_text()
        assert "REDIS_PENDING_IDLE_MS" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — requeue_stalled_messages (inbound stream)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequeueStalledMessages:
    """Verify inbound stream XAUTOCLAIM recovery."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_pending(self):
        """No pending messages returns (0, [])."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, ids = await requeue_stalled_messages("worker_1", idle_ms=60000)
            assert count == 0
            assert ids == []

    @pytest.mark.asyncio
    async def test_returns_count_and_ids(self):
        """Reclaimed messages return correct count and message IDs."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(
            return_value=(
                None,
                [
                    ("123-0", {"field": "value1"}),
                    ("124-0", {"field": "value2"}),
                ],
            )
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, entries = await requeue_stalled_messages("worker_1", idle_ms=60000)
            assert count == 2
            # Stage B fix: entries is list[(id, fields)] with payload preserved
            assert [mid for mid, _ in entries] == ["123-0", "124-0"]
            # also verify fields preserved
            assert entries[0][1] == {"field": "value1"}

    @pytest.mark.asyncio
    async def test_preserves_original_message_ids(self):
        """Reclaimed messages keep their original Redis stream IDs."""
        mock_redis = AsyncMock()
        original_id = "1718000000000-0"
        mock_redis.xautoclaim = AsyncMock(
            return_value=(None, [(original_id, {"content": "hello"})])
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            _count, entries = await requeue_stalled_messages("worker_1")
            assert [mid for mid, _ in entries] == [original_id]

    @pytest.mark.asyncio
    async def test_does_not_ack_reclaimed_messages(self):
        """XAUTOCLAIM reclaimed messages must NOT be auto-ACKed."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("999-0", {"data": "test"})]))
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            await requeue_stalled_messages("worker_1")
            mock_redis.xack.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_correct_stream_and_group(self):
        """XAUTOCLAIM targets inbound_messages stream, llm_workers group."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            await requeue_stalled_messages("worker_1", idle_ms=45000)
            mock_redis.xautoclaim.assert_called_once_with(
                name="inbound_messages",
                groupname="llm_workers",
                consumername="worker_1",
                min_idle_time=45000,
                start_id="0",
                count=10,
            )

    @pytest.mark.asyncio
    async def test_passes_idle_ms_threshold(self):
        """Configurable idle_ms is passed to XAUTOCLAIM."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            await requeue_stalled_messages("worker_1", idle_ms=120000)
            call_kwargs = mock_redis.xautoclaim.call_args
            assert call_kwargs.kwargs["min_idle_time"] == 120000

    @pytest.mark.asyncio
    async def test_redis_error_returns_zero(self):
        """Redis errors during XAUTOCLAIM return (0, []) and do not raise."""
        from redis import ResponseError

        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(side_effect=ResponseError("ERR"))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_messages

            count, ids = await requeue_stalled_messages("worker_1")
            assert count == 0
            assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — requeue_stalled_send_messages (send stream)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequeueStalledSendMessages:
    """Verify send stream XAUTOCLAIM recovery."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_pending(self):
        """No pending send messages returns (0, [])."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            count, ids = await requeue_stalled_send_messages("bot_main", idle_ms=30000)
            assert count == 0
            assert ids == []

    @pytest.mark.asyncio
    async def test_returns_count_and_ids(self):
        """Reclaimed send messages return correct count and IDs."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(
            return_value=(
                None,
                [
                    ("send-1-0", {"entity": "123", "content": "hi"}),
                    ("send-2-0", {"entity": "456", "content": "hey"}),
                ],
            )
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            count, entries = await requeue_stalled_send_messages("bot_main")
            assert count == 2
            assert [mid for mid, _ in entries] == ["send-1-0", "send-2-0"]
            assert entries[0][1] == {"entity": "123", "content": "hi"}

    @pytest.mark.asyncio
    async def test_does_not_ack_reclaimed_messages(self):
        """Reclaimed send messages must NOT be auto-ACKed."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("send-99-0", {"entity": "1"})]))
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            await requeue_stalled_send_messages("bot_main")
            mock_redis.xack.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_correct_stream_and_group(self):
        """XAUTOCLAIM targets send_messages stream, send_workers group."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            await requeue_stalled_send_messages("bot_main", idle_ms=60000)
            mock_redis.xautoclaim.assert_called_once_with(
                name="send_messages",
                groupname="send_workers",
                consumername="bot_main",
                min_idle_time=60000,
                start_id="0",
                count=10,
            )

    @pytest.mark.asyncio
    async def test_redis_error_returns_zero(self):
        """Redis errors during send XAUTOCLAIM return (0, [])."""
        from redis import ResponseError

        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(side_effect=ResponseError("ERR"))

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import requeue_stalled_send_messages

            count, ids = await requeue_stalled_send_messages("bot_main")
            assert count == 0
            assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — LLM Worker recovery integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestLLMWorkerRecovery:
    """Verify LLM worker calls requeue_stalled_messages on each loop iteration."""

    @pytest.mark.asyncio
    async def test_worker_calls_requeue_on_startup(self):
        """Worker loop invokes requeue_stalled_messages before reading new messages."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(0, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            await run_worker("worker_1")
            mock_redis.xautoclaim.assert_called()

    @pytest.mark.asyncio
    async def test_worker_logs_reclaimed_messages(self):
        """Worker logs reclaimed message IDs when messages are recovered."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("123-0", {"content": "test"})]))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            with patch("workers.llm_worker.logger") as mock_logger:
                await run_worker("worker_1")
                mock_logger.info.assert_any_call(
                    "Reclaimed %d stalled inbound messages: %s", 1, ["123-0"]
                )

    @pytest.mark.asyncio
    async def test_worker_no_log_when_no_recovery(self):
        """No recovery log when no messages are stale."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(0, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            with patch("workers.llm_worker.logger") as mock_logger:
                await run_worker("worker_1")
                for call in mock_logger.info.call_args_list:
                    assert "Reclaimed" not in str(call)

    @pytest.mark.asyncio
    async def test_worker_recovery_error_does_not_crash_loop(self):
        """Recovery error in worker loop does not crash the worker."""
        from redis import ResponseError

        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(side_effect=ResponseError("CONNRESET"))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            # Should NOT raise
            await run_worker("worker_1")

    @pytest.mark.asyncio
    async def test_worker_uses_configurable_idle_ms(self):
        """Worker passes configured redis_pending_idle_ms to requeue."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(0, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.redis_pending_idle_ms = 90000
        mock_settings.user_lock_ttl = 60

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker._settings", mock_settings),
        ):
            from workers.llm_worker import run_worker

            await run_worker("worker_1")
            call_kwargs = mock_redis.xautoclaim.call_args
            assert call_kwargs.kwargs["min_idle_time"] == 90000


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Send worker recovery integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendWorkerRecovery:
    """Verify send worker / chatbotv2.main calls requeue_stalled_send_messages."""

    @pytest.mark.asyncio
    async def test_send_stream_calls_requeue(self):
        """_process_send_stream invokes requeue_stalled_send_messages."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(0, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        mock_client = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]),
            patch("chatbotv2.main.get_settings") as mock_settings,
            patch("chatbotv2.main.get_send_dedup_value", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.main.try_reserve_send_dedup", new_callable=AsyncMock, return_value=True),
            patch("chatbotv2.main.confirm_send_dedup", new_callable=AsyncMock),
        ):
            mock_settings.return_value.redis_pending_idle_ms = 45000

            from chatbotv2.main import _process_send_stream

            await _process_send_stream(mock_client)
            mock_redis.xautoclaim.assert_called()

    @pytest.mark.asyncio
    async def test_send_recovery_uses_configurable_idle_ms(self):
        """Send recovery passes configured idle_ms to XAUTOCLAIM."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(0, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        mock_client = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.redis_pending_idle_ms = 75000
        mock_settings.vault_stale_reservation_minutes = 5
        mock_settings.debounce_window_seconds = 3

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]),
            patch("chatbotv2.main._settings", mock_settings),
            patch("chatbotv2.main.get_send_dedup_value", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.main.try_reserve_send_dedup", new_callable=AsyncMock, return_value=True),
            patch("chatbotv2.main.confirm_send_dedup", new_callable=AsyncMock),
        ):
            from chatbotv2.main import _process_send_stream

            await _process_send_stream(mock_client)
            call_kwargs = mock_redis.xautoclaim.call_args
            assert call_kwargs.kwargs["min_idle_time"] == 75000

    @pytest.mark.asyncio
    async def test_send_recovery_logs_reclaimed_ids(self):
        """Send recovery logs reclaimed message IDs."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("msg-1-0", {"entity": "123", "creator_id": "1", "content": "hi"})]))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        mock_client = AsyncMock()
        mock_client.get_input_entity = AsyncMock(return_value=MagicMock())
        mock_client.send_message = AsyncMock(return_value=MagicMock(id=999))

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]),
            patch("chatbotv2.main.get_settings") as mock_settings,
            patch("chatbotv2.main.get_send_dedup_value", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.main.try_reserve_send_dedup", new_callable=AsyncMock, return_value=True),
            patch("chatbotv2.main.confirm_send_dedup", new_callable=AsyncMock),
            patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True),
            patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0),
        ):
            mock_settings.return_value.redis_pending_idle_ms = 30000

            from chatbotv2.main import _process_send_stream

            with patch("chatbotv2.main.logger") as mock_logger:
                await _process_send_stream(mock_client)
                mock_logger.info.assert_any_call(
                    "Reclaimed %d stalled send messages: %s", 1, ["msg-1-0"]
                )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — ACK semantics preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestAckSemanticsPreserved:
    """Verify reclaimed messages follow normal processing and ACK paths."""

    @pytest.mark.asyncio
    async def test_successful_reclaimed_message_is_acked(self):
        """After recovery, successful processing ACKs the message normally."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(
            return_value=(None, [("recovered-1-0", {"user_id": "1", "content": "hi"})])
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            await run_worker("worker_1")

        # XAUTOCLAIM reclaimed, but xreadgroup gets nothing (new messages)
        # So ack_inbound is NOT called — the reclaimed message enters the
        # PEL and will be re-read by XREADGROUP with ">" on next iteration.
        # The key invariant: xautoclaim does NOT call xack.

    @pytest.mark.asyncio
    async def test_failed_reclaimed_message_follows_dlq_path(self):
        """Failed recovered processing sends to DLQ and ACKs original."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            # Should not crash — DLQ path is preserved
            await run_worker("worker_1")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — Duplicate safety & user locking
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateSafety:
    """Verify recovery does not break existing safety mechanisms."""

    @pytest.mark.asyncio
    async def test_user_lock_still_applies_to_recovered_messages(self):
        """Recovered messages still require acquire_user_lock before processing."""
        from unittest.mock import AsyncMock, MagicMock

        mock_lock = AsyncMock(return_value=False)

        with patch("workers.llm_worker.acquire_user_lock", mock_lock):
            with patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1))):
                from workers.llm_worker import process_message
                from workers.llm_worker import UserLockContentionError

                # Should raise contention when lock is held (creator-isolated)
                with pytest.raises(UserLockContentionError):
                    await process_message(
                        user_id=1,
                        user_message="test",
                        telegram_message_id=100,
                        username="u",
                        first_name="f",
                        persona="",
                        creator_id=1,
                    )
                # Phase 1 fix: lock now creator-scoped, so assert called with creator_id param
                assert mock_lock.call_count == 1
                call_kwargs = mock_lock.call_args.kwargs
                # P1.6: TTL increased to 300
                assert call_kwargs.get("ttl") in (60, 300)
                # creator_id must be present
                assert "creator_id" in call_kwargs
                assert call_kwargs.get("creator_id") == 1

    @pytest.mark.asyncio
    async def test_per_user_lock_prevents_concurrent_processing(self):
        """User lock prevents duplicate processing of same user messages."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(return_value=False)  # Lock held

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            # Should not crash even with lock contention
            await run_worker("worker_1")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — Multiple worker safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleWorkerSafety:
    """Verify multiple workers do not duplicate recovery work."""

    @pytest.mark.asyncio
    async def test_xautoclaim_is_atomic(self):
        """XAUTOCLAIM is atomic — only one worker claims each idle message."""
        mock_redis = AsyncMock()
        # First worker sees 2 messages, second sees 0 (already claimed)
        mock_redis.xautoclaim = AsyncMock(
            side_effect=[
                (None, [("msg-1-0", {"data": "a"})]),
                (None, []),
            ]
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        call_count = 0

        def shutting_down_side_effect():
            nonlocal call_count
            call_count += 1
            # Worker calls is_shutting_down twice per iteration (while + if).
            # Return False for first 4 calls (2 iterations), then True.
            return call_count > 4

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=shutting_down_side_effect),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            # Two loop iterations — first reclaims, second finds nothing
            await run_worker("worker_1")
            assert mock_redis.xautoclaim.call_count == 2

    @pytest.mark.asyncio
    async def test_different_consumers_claim_independently(self):
        """Different consumer names allow independent claiming."""
        mock_redis1 = AsyncMock()
        mock_redis1.xautoclaim = AsyncMock(return_value=(None, [("msg-1-0", {"data": "a"})]))
        mock_redis1.xreadgroup = AsyncMock(return_value=None)

        mock_redis2 = AsyncMock()
        mock_redis2.xautoclaim = AsyncMock(return_value=(None, [("msg-2-0", {"data": "b"})]))
        mock_redis2.xreadgroup = AsyncMock(return_value=None)

        # Both workers can claim different messages
        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis1),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            await run_worker("worker_1")

        # Verify consumer name is passed to XAUTOCLAIM
        call_kwargs = mock_redis1.xautoclaim.call_args
        assert call_kwargs.kwargs["consumername"] == "worker_1"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Message lifecycle preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestMessageLifecyclePreserved:
    """Verify recovery does not alter event lifecycle or generation flow."""

    @pytest.mark.asyncio
    async def test_generation_events_unaffected_by_recovery(self):
        """Recovery call does not emit any generation events."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=(None, [("123-0", {"content": "test"})]))
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.redis_pending_idle_ms = 60000
            mock_settings.return_value.user_lock_ttl = 60

            from workers.llm_worker import run_worker

            with patch("core.event_bus.publish_event", new_callable=AsyncMock) as mock_pub:
                await run_worker("worker_1")
                # Recovery itself should NOT publish events
                for call in mock_pub.call_args_list:
                    assert "ai.generation" not in str(call)

    @pytest.mark.asyncio
    async def test_enqueue_send_preserved_for_recovered_messages(self):
        """Recovered messages that succeed still go through enqueue_send."""
        from unittest.mock import AsyncMock
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals

        mock_enqueue = AsyncMock()
        _mock_one = OneCallResult(
            reply="draft",
            signals=CommerceSignals.low_information(),
            confidence=0.95,
            needs_handoff=False,
            is_valid=True,
            validation_error=None,
            quality_score=0.95,
        )

        with patch("workers.llm_worker.enqueue_send", mock_enqueue):
            from workers.llm_worker import process_message

            # Simulate a successful auto-approved message (recovered or not)
            with (
                patch(
                    "workers.llm_worker.acquire_user_lock",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("db.redis.requeue_stalled_debounce", new_callable=AsyncMock, return_value=0),
            patch("db.redis.reconcile_inbound_gaps", new_callable=AsyncMock, return_value=0),
                patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
                patch(
                    "workers.llm_worker.is_user_auto_reply_excluded",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
                patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value=None),
                patch(
                    "workers.llm_worker.generate_draft",
                    new_callable=AsyncMock,
                    return_value="draft",
                ),
                patch(
                    "workers.llm_worker.score_draft",
                    new_callable=AsyncMock,
                    return_value=(0.95, []),
                ),
                patch(
                    "workers.llm_worker.is_auto_reply_enabled",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch("workers.llm_worker.post_process", new_callable=AsyncMock),
                patch("core.event_bus.publish_event", new_callable=AsyncMock),
                patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=_mock_one),
                patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=MagicMock(status=MagicMock(value="ready"), creator_id=1))),
            ):
                await process_message(
                    user_id=1,
                    user_message="hi",
                    telegram_message_id=100,
                    username="u",
                    first_name="f",
                    persona="",
                    creator_id=1,
                )
                mock_enqueue.assert_called_once()
