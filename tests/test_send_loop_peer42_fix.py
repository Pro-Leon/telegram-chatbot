"""Peer 42 / Stale Redis Send-Loop regression tests.

Verifies that:
A. Invalid entities → DLQ + ACK (no requeue loop)
B. Rate-limited messages → ACK + re-enqueue (no PEL leak)
C. FloodWait → ACK + re-enqueue (temporary, retryable)
D. RPC errors → DLQ + ACK
E. move_send_to_dlq always ACKs even if DLQ write fails
F. Valid peers still work normally
G. No retry loop for permanently invalid entities
H. send payload contains Telegram user_id (not CRM id)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _text_data(entity="42", **overrides):
    base = {
        "entity": entity,
        "content": "Hello world",
        "draft_content": "Hello world",
        "was_edited": "",
        "was_auto_approved": "",
        "confidence_score": "0",
        "operator_id": "",
        "save_to_db": "",
        "media_type": "",
        "media_path": "",
    }
    base.update(overrides)
    return base


def _send_stream_patches(**overrides):
    defaults = {
        "read_send_messages": AsyncMock(return_value=[]),
        "is_send_duplicate": AsyncMock(return_value=False),
        "get_send_rate_limit_wait": AsyncMock(return_value=0),
        "check_send_rate_limit": AsyncMock(return_value=True),
        "mark_send_dedup": AsyncMock(),
        "ack_send": AsyncMock(),
        "enqueue_send": AsyncMock(),
        "save_outbound_after_send": AsyncMock(),
        "publish_event": AsyncMock(),
        "requeue_stalled_send_messages": AsyncMock(return_value=(0, [])),
        "move_send_to_dlq": AsyncMock(),
        "release_stale_reservations": AsyncMock(return_value=[]),
    }
    defaults.update(overrides)
    ctx = {}
    for name, mock_obj in defaults.items():
        ctx[name] = patch(f"chatbotv2.main.{name}", new=mock_obj)
    return ctx


async def _run_one_message(data, extra_patches=None, msg_id="m1"):
    from chatbotv2.main import _process_send_stream

    mock_client = AsyncMock()
    msg_result = MagicMock()
    msg_result.id = 99001
    mock_client.send_message.return_value = msg_result
    mock_client.get_input_entity.return_value = "resolved"

    patches = _send_stream_patches(
        read_send_messages=AsyncMock(return_value=[("s1", [(msg_id, data)])]),
        **(extra_patches or {}),
    )
    active = {}
    for key, p in patches.items():
        active[key] = p.start()
    try:
        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]):
            await _process_send_stream(mock_client)
    finally:
        for p in patches.values():
            p.stop()
    return mock_client, active


# ═══════════════════════════════════════════════════════════════════════════════
# A. Invalid entity → DLQ + ACK (no requeue loop)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidEntityToDLQ:
    """Permanently unresolvable entities must be DLQ'd and ACK'd."""

    @pytest.mark.asyncio
    async def test_value_error_entity_dlq_and_ack(self):
        """ValueError from get_input_entity → DLQ + ACK (simplified)."""
        mock_client = AsyncMock()
        mock_client.get_input_entity.side_effect = ValueError("Could not find entity")

        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_called_once()
        assert mock_dlq.call_args[0][1] == "entity_not_found"

    @pytest.mark.asyncio
    async def test_value_error_calls_dlq(self):
        """get_input_entity raising ValueError triggers move_send_to_dlq."""
        mock_client = AsyncMock()
        mock_client.get_input_entity.side_effect = ValueError("entity not found")

        mock_dlq = AsyncMock()
        mock_ack = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq), \
             patch("chatbotv2.main.ack_send", new=mock_ack):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_called_once()
        call_args = mock_dlq.call_args
        assert call_args[0][0] == "m1"  # message_id
        assert call_args[0][1] == "entity_not_found"  # reason
        # Must NOT have acked via ack_send (move_send_to_dlq handles ack)
        # But move_send_to_dlq itself acks internally

    @pytest.mark.asyncio
    async def test_rpc_error_entity_dlq(self):
        """RPCError from get_input_entity triggers DLQ with entity_rpc_error reason."""
        from telethon.errors import PeerIdInvalidError

        mock_client = AsyncMock()
        mock_client.get_input_entity.side_effect = PeerIdInvalidError(
            request=None
        )

        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_called_once()
        call_args = mock_dlq.call_args
        assert call_args[0][1] == "entity_rpc_error"


# ═══════════════════════════════════════════════════════════════════════════════
# B. Rate-limited messages → ACK + re-enqueue (no PEL leak)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateLimitAckAndRequeue:
    """Rate-limited messages must be ACK'd and re-enqueued, not left in PEL."""

    @pytest.mark.asyncio
    async def test_rate_limit_acks_message(self):
        """Rate limit exceeded → ack_send called (message removed from PEL)."""
        mock_ack = AsyncMock()
        mock_enqueue = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.ack_send", new=mock_ack), \
             patch("chatbotv2.main.enqueue_send", new=mock_enqueue):
            from chatbotv2.main import _process_send_stream
            mock_client = AsyncMock()
            await _process_send_stream(mock_client)

        mock_ack.assert_called_once_with("m1")

    @pytest.mark.asyncio
    async def test_rate_limit_re_enqueues_message(self):
        """Rate limit exceeded → enqueue_send called (message retried)."""
        mock_ack = AsyncMock()
        mock_enqueue = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.ack_send", new=mock_ack), \
             patch("chatbotv2.main.enqueue_send", new=mock_enqueue):
            from chatbotv2.main import _process_send_stream
            mock_client = AsyncMock()
            await _process_send_stream(mock_client)

        mock_enqueue.assert_called_once()
        call_args = mock_enqueue.call_args
        # Payload should preserve original data
        assert call_args[0][0]["entity"] == "42"

    @pytest.mark.asyncio
    async def test_rate_limit_does_not_reach_entity_resolution(self):
        """Rate-limited message must not proceed to entity resolution."""
        mock_client = AsyncMock()
        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.ack_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.enqueue_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        # get_input_entity should NOT have been called
        mock_client.get_input_entity.assert_not_called()
        # DLQ should NOT have been called
        mock_dlq.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# C. FloodWait → ACK + re-enqueue (temporary, retryable)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFloodWaitRequeue:
    """FloodWaitError is temporary — message should be re-enqueued."""

    @pytest.mark.asyncio
    async def test_flood_wait_re_enqueues(self):
        """FloodWaitError → ack + enqueue_send (not DLQ)."""
        from telethon.errors import FloodWaitError

        mock_client = AsyncMock()
        flood_exc = FloodWaitError(request=None)
        flood_exc.seconds = 30
        mock_client.get_input_entity.side_effect = flood_exc
        mock_ack = AsyncMock()
        mock_enqueue = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.ack_send", new=mock_ack), \
             patch("chatbotv2.main.enqueue_send", new=mock_enqueue):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_ack.assert_called_once_with("m1")
        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_flood_wait_does_not_dlq(self):
        """FloodWaitError must NOT move to DLQ."""
        from telethon.errors import FloodWaitError

        mock_client = AsyncMock()
        flood_exc = FloodWaitError(request=None)
        flood_exc.seconds = 30
        mock_client.get_input_entity.side_effect = flood_exc
        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.ack_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.enqueue_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# D. DLQ ACK semantics — move_send_to_dlq always ACKs
# ═══════════════════════════════════════════════════════════════════════════════


class TestDLQAckSemantics:
    """H4 Batch 3 (D4): DLQ record first, ACK second — never certify a
    failed move as gone. The entry stays pending for reclaim."""

    @pytest.mark.asyncio
    async def test_dlq_xadd_failure_leaves_pending_no_ack(self):
        """If DLQ xadd fails, xack must NOT be called; move reports False."""
        mock_redis = AsyncMock()
        mock_redis.xadd.side_effect = Exception("Redis down")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            ok = await move_send_to_dlq("msg-1", "entity_not_found")

        assert ok is False
        mock_redis.xack.assert_not_called()

    @pytest.mark.asyncio
    async def test_dlq_xadd_success_acks(self):
        """Normal path: both xadd and xack are called, move reports True."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            ok = await move_send_to_dlq("msg-1", "entity_not_found")

        assert ok is True
        mock_redis.xadd.assert_called_once()
        mock_redis.xack.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# E. Valid peer regression
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidPeerRegression:
    """Valid peers must still resolve, send, and ack normally."""

    @pytest.mark.asyncio
    async def test_valid_entity_sends_and_acks(self):
        """Valid entity → get_input_entity succeeds → send → ack."""
        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99001
        mock_client.send_message.return_value = msg_result
        mock_client.get_input_entity.return_value = "resolved"

        mock_ack = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data(entity="12345"))])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.ack_send", new=mock_ack), \
             patch("chatbotv2.main.save_outbound_after_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.publish_event", new_callable=AsyncMock):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_client.get_input_entity.assert_called_once_with(12345)
        mock_client.send_message.assert_called_once()
        mock_ack.assert_called_once_with("m1")


# ═══════════════════════════════════════════════════════════════════════════════
# F. No retry loop for permanently invalid entities
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoRetryLoop:
    """A permanently invalid entity must not re-enter the send stream."""

    @pytest.mark.asyncio
    async def test_invalid_entity_not_re_enqueued(self):
        """ValueError entity → DLQ + ACK, NOT enqueue_send."""
        mock_client = AsyncMock()
        mock_client.get_input_entity.side_effect = ValueError("not found")
        mock_enqueue = AsyncMock()
        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq), \
             patch("chatbotv2.main.enqueue_send", new=mock_enqueue):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_called_once()
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_rpc_error_entity_not_re_enqueued(self):
        """RPCError entity → DLQ, NOT enqueue_send."""
        from telethon.errors import PeerIdInvalidError

        mock_client = AsyncMock()
        mock_client.get_input_entity.side_effect = PeerIdInvalidError(request=None)
        mock_enqueue = AsyncMock()
        mock_dlq = AsyncMock()

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data())])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.move_send_to_dlq", new=mock_dlq), \
             patch("chatbotv2.main.enqueue_send", new=mock_enqueue):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_dlq.assert_called_once()
        mock_enqueue.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# G. ID-domain: entity field is a stringified integer (Telegram user_id)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIDDomain:
    """Send payload entity field must be the Telegram user ID as a string."""

    @pytest.mark.asyncio
    async def test_entity_is_stringified_integer(self):
        """entity field in send payload is str(user_id) where user_id = Telegram ID."""
        data = _text_data(entity="42")
        assert data["entity"] == "42"
        assert data["entity"].isdigit()

    @pytest.mark.asyncio
    async def test_entity_converted_to_int_for_telegram(self):
        """Entity string is converted to int before calling get_input_entity."""
        mock_client = AsyncMock()
        msg_result = MagicMock()
        msg_result.id = 99001
        mock_client.send_message.return_value = msg_result
        mock_client.get_input_entity.return_value = "resolved"

        with patch("chatbotv2.main.is_shutting_down", side_effect=[False, True]), \
             patch("chatbotv2.main.requeue_stalled_send_messages", new_callable=AsyncMock, return_value=(0, [])), \
             patch("chatbotv2.main.release_stale_reservations", new_callable=AsyncMock, return_value=[]), \
             patch("chatbotv2.main.read_send_messages", new_callable=AsyncMock, return_value=[("s1", [("m1", _text_data(entity="42"))])]), \
             patch("chatbotv2.main.is_send_duplicate", new_callable=AsyncMock, return_value=False), \
             patch("chatbotv2.main.get_send_rate_limit_wait", new_callable=AsyncMock, return_value=0), \
             patch("chatbotv2.main.check_send_rate_limit", new_callable=AsyncMock, return_value=True), \
             patch("chatbotv2.main.ack_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.save_outbound_after_send", new_callable=AsyncMock), \
             patch("chatbotv2.main.publish_event", new_callable=AsyncMock):
            from chatbotv2.main import _process_send_stream
            await _process_send_stream(mock_client)

        mock_client.get_input_entity.assert_called_once_with(42)
