"""Phase 4.1B — Dead Letter Queue recovery & safe replay tests.

These tests verify that:
- DLQ records contain rich replayable metadata
- DLQ entries can be inspected, listed, counted, and cleaned up
- Inbound and outbound replay works correctly
- Replay limits are enforced
- Concurrent replay is protected
- Legacy DLQ entries remain readable
- Existing ACK/DLQ semantics are preserved
"""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Rich DLQ Metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestRichDlqMetadata:
    """Verify DLQ records contain complete replayable information."""

    @pytest.mark.asyncio
    async def test_inbound_dlq_preserves_payload(self):
        """Inbound DLQ entry stores original payload as JSON."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()

        payload = {
            "user_id": "123",
            "content": "hello",
            "telegram_message_id": "456",
            "username": "testuser",
        }

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-1-0", "processing_error", payload=payload)

            call_args = mock_redis.xadd.call_args
            record = (
                call_args.args[1]
                if len(call_args.args) > 1
                else call_args.kwargs.get(
                    "fields", call_args.args[1] if len(call_args.args) > 1 else {}
                )
            )
            # xadd(stream, fields, ...) — fields is second positional arg
            record = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
            assert "payload" in record
            stored_payload = json.loads(record["payload"])
            assert stored_payload["user_id"] == "123"
            assert stored_payload["content"] == "hello"

    @pytest.mark.asyncio
    async def test_outbound_dlq_preserves_payload(self):
        """Outbound DLQ entry stores original payload as JSON."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-2-0")
        mock_redis.xack = AsyncMock()

        payload = {
            "entity": "123",
            "content": "response",
            "dedup_id": "abc123",
        }

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            await move_send_to_dlq("orig-2-0", "send_error", payload=payload)

            record = mock_redis.xadd.call_args[0][1]
            stored_payload = json.loads(record["payload"])
            assert stored_payload["entity"] == "123"
            assert stored_payload["dedup_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_reason_preserved(self):
        """DLQ entry preserves the failure reason."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-3-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-3-0", "gemini_timeout")
            record = mock_redis.xadd.call_args[0][1]
            assert record["reason"] == "gemini_timeout"

    @pytest.mark.asyncio
    async def test_timestamp_preserved(self):
        """DLQ entry includes failure timestamp."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-4-0")
        mock_redis.xack = AsyncMock()

        before = int(time.time())
        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-4-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            after = int(time.time())
            ts = int(record["failure_timestamp"])
            assert before <= ts <= after

    @pytest.mark.asyncio
    async def test_worker_id_preserved(self):
        """DLQ entry records the worker ID when provided."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-5-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-5-0", "error", worker_id="worker_1")
            record = mock_redis.xadd.call_args[0][1]
            assert record["worker_id"] == "worker_1"

    @pytest.mark.asyncio
    async def test_replay_count_initialized_to_zero(self):
        """New DLQ entry has replay_count = 0."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-6-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-6-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            assert record["replay_count"] == "0"

    @pytest.mark.asyncio
    async def test_stream_field_set_for_inbound(self):
        """Inbound DLQ entry has stream = 'inbound'."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-7-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-7-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            assert record["stream"] == "inbound"

    @pytest.mark.asyncio
    async def test_stream_field_set_for_send(self):
        """Outbound DLQ entry has stream = 'send'."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-8-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            await move_send_to_dlq("orig-8-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            assert record["stream"] == "send"

    @pytest.mark.asyncio
    async def test_no_payload_field_when_none(self):
        """DLQ entry omits payload field when none provided (backward compat)."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-9-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-9-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            assert "payload" not in record


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — DLQ Inspection
# ═══════════════════════════════════════════════════════════════════════════════


class TestDlqInspection:
    """Verify DLQ inspection helpers work correctly."""

    @pytest.mark.asyncio
    async def test_list_entries(self):
        """list_dlq_entries returns entries from the stream."""
        mock_redis = AsyncMock()
        mock_redis.xrevrange = AsyncMock(
            return_value=[
                ("123-0", {"reason": "err1", "stream": "inbound"}),
                ("124-0", {"reason": "err2", "stream": "send"}),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import list_dlq_entries

            entries = await list_dlq_entries()
            assert len(entries) == 2
            assert entries[0]["entry_id"] == "123-0"
            assert entries[0]["reason"] == "err1"

    @pytest.mark.asyncio
    async def test_list_entries_filter_by_stream(self):
        """list_dlq_entries filters by stream field."""
        mock_redis = AsyncMock()
        mock_redis.xrevrange = AsyncMock(
            return_value=[
                ("123-0", {"reason": "err1", "stream": "inbound"}),
                ("124-0", {"reason": "err2", "stream": "send"}),
                ("125-0", {"reason": "err3", "stream": "inbound"}),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import list_dlq_entries

            entries = await list_dlq_entries(stream_filter="inbound")
            assert len(entries) == 2
            assert all(e["stream"] == "inbound" for e in entries)

    @pytest.mark.asyncio
    async def test_count_entries(self):
        """count_dlq_entries returns stream length."""
        mock_redis = AsyncMock()
        mock_redis.xinfo_stream = AsyncMock(return_value={"length": 42})

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import count_dlq_entries

            count = await count_dlq_entries()
            assert count == 42

    @pytest.mark.asyncio
    async def test_get_entry(self):
        """get_dlq_entry returns a single entry."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[("123-0", {"reason": "err", "stream": "inbound"})]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import get_dlq_entry

            entry = await get_dlq_entry("123-0")
            assert entry is not None
            assert entry["entry_id"] == "123-0"
            assert entry["reason"] == "err"

    @pytest.mark.asyncio
    async def test_get_nonexistent_entry(self):
        """get_dlq_entry returns None for missing entry."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(return_value=[])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import get_dlq_entry

            entry = await get_dlq_entry("999-0")
            assert entry is None

    @pytest.mark.asyncio
    async def test_delete_entry(self):
        """delete_dlq_entry removes an entry."""
        mock_redis = AsyncMock()
        mock_redis.xdel = AsyncMock(return_value=1)

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import delete_dlq_entry

            result = await delete_dlq_entry("123-0")
            assert result is True
            mock_redis.xdel.assert_called_once_with("dead_letter_queue", "123-0")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_entry(self):
        """delete_dlq_entry returns False for missing entry."""
        mock_redis = AsyncMock()
        mock_redis.xdel = AsyncMock(return_value=0)

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import delete_dlq_entry

            result = await delete_dlq_entry("999-0")
            assert result is False

    @pytest.mark.asyncio
    async def test_legacy_entry_readable(self):
        """Legacy DLQ entries without new fields can be inspected."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                ("old-1-0", {"message_id": "orig-1", "reason": "old_error"}),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import get_dlq_entry

            entry = await get_dlq_entry("old-1-0")
            assert entry is not None
            assert entry["reason"] == "old_error"
            # Legacy entries don't have payload/replay_count — should not crash
            assert entry.get("payload") is None
            assert entry.get("replay_count") is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — Inbound Replay
# ═══════════════════════════════════════════════════════════════════════════════


class TestInboundReplay:
    """Verify inbound DLQ replay functionality."""

    @pytest.mark.asyncio
    async def test_inbound_replay_success(self):
        """Inbound DLQ entry successfully requeues to inbound stream."""
        mock_redis = AsyncMock()
        payload = {
            "user_id": "123",
            "content": "hello",
            "telegram_message_id": "456",
            "creator_id": "1",
        }

        # get_dlq_entry returns entry with payload
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                        "reason": "error",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)  # Lock acquired
        mock_redis.xadd = AsyncMock(return_value="new-789-0")
        mock_redis.delete = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                        "reason": "error",
                    },
                ),
            ]
        )
        # Mock xadd for the DLQ metadata update
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is True
            assert result["error"] is None
            assert result["new_message_id"] is not None

    @pytest.mark.asyncio
    async def test_new_redis_stream_id_generated(self):
        """Replay creates a new Redis stream ID (not reusing old one)."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-old-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        # The new message gets a different ID
        enqueue_mock = AsyncMock(return_value="new-msg-id-0")
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", enqueue_mock),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-old-0")
            assert result["success"] is True
            assert result["new_message_id"] == "new-msg-id-0"
            enqueue_mock.assert_called_once_with(payload)

    @pytest.mark.asyncio
    async def test_replay_count_increments(self):
        """Successful replay increments replay_count."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        # First call: get_dlq_entry
        # Second call: xadd (enqueue_inbound) — mocked
        # Third call: xadd (DLQ metadata update)
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-0"),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["replay_count"] == 1

    @pytest.mark.asyncio
    async def test_missing_payload_returns_error(self):
        """Replay fails gracefully when payload is missing."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "reason": "error",
                        # No payload field
                    },
                ),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is False
            assert result["error"] == "missing_original_payload"

    @pytest.mark.asyncio
    async def test_nonexistent_entry_returns_error(self):
        """Replay of nonexistent entry returns entry_not_found."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(return_value=[])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("999-0")
            assert result["success"] is False
            assert result["error"] == "entry_not_found"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Outbound Replay
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutboundReplay:
    """Verify outbound DLQ replay functionality."""

    @pytest.mark.asyncio
    async def test_outbound_replay_success(self):
        """Outbound DLQ entry successfully requeues to send stream."""
        mock_redis = AsyncMock()
        payload = {
            "entity": "123",
            "content": "response",
            "dedup_id": "abc123",
        }

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "send",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        enqueue_mock = AsyncMock(return_value="new-send-0")

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_send", enqueue_mock),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is True
            enqueue_mock.assert_called_once_with(payload, dedup_id="abc123", generation_id=None, creator_id=None)

    @pytest.mark.asyncio
    async def test_dedup_id_preserved_in_replay(self):
        """Outbound replay passes original dedup_id to enqueue_send."""
        mock_redis = AsyncMock()
        payload = {"entity": "1", "content": "hi", "dedup_id": "dedup-xyz"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "send",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        enqueue_mock = AsyncMock(return_value="new-0")

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_send", enqueue_mock),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is True
            call_kwargs = enqueue_mock.call_args
            assert call_kwargs.kwargs.get("dedup_id") == "dedup-xyz"

    @pytest.mark.asyncio
    async def test_unsupported_stream_returns_error(self):
        """Replay of unknown stream type returns unsupported_stream error."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "unknown",
                        "payload": json.dumps({"data": "test"}),
                        "replay_count": "0",
                    },
                ),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is False
            assert "unsupported_stream" in result["error"]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Replay Limits
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplayLimits:
    """Verify maximum replay attempts are enforced."""

    @pytest.mark.asyncio
    async def test_replay_succeeds_below_maximum(self):
        """Replay succeeds when replay_count < max."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "1",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-0"),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0", max_replay_attempts=3)
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_replay_refused_at_maximum(self):
        """Replay refused when replay_count >= max."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps({"user_id": "1"}),
                        "replay_count": "3",
                    },
                ),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0", max_replay_attempts=3)
            assert result["success"] is False
            assert result["error"] == "max_replay_attempts_reached"

    @pytest.mark.asyncio
    async def test_failed_replay_doesnt_increment_count(self):
        """Failed replay (lock contention) does not increment replay_count."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=False)  # Lock not acquired

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0")
            assert result["success"] is False
            assert result["error"] == "replay_in_progress"
            assert result["replay_count"] == 0  # Unchanged

    @pytest.mark.asyncio
    async def test_refused_replay_remains_in_dlq(self):
        """Entry remains in DLQ after replay is refused."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps({"user_id": "1"}),
                        "replay_count": "5",
                    },
                ),
            ]
        )
        # xdel should NOT be called
        mock_redis.xdel = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("dlq-1-0", max_replay_attempts=3)
            assert result["success"] is False
            mock_redis.xdel.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — Concurrency
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """Verify concurrent replay attempts are serialized."""

    @pytest.mark.asyncio
    async def test_second_replay_cannot_concurrently_replay(self):
        """Second concurrent replay attempt gets replay_in_progress error."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        # First call: lock acquired; Second call: lock not acquired
        mock_redis.set = AsyncMock(side_effect=[True, False])
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-0"),
        ):
            from db.redis import replay_dlq_entry

            # First attempt succeeds
            result1 = await replay_dlq_entry("dlq-1-0")
            assert result1["success"] is True

            # Second attempt fails (lock held)
            result2 = await replay_dlq_entry("dlq-1-0")
            assert result2["success"] is False
            assert result2["error"] == "replay_in_progress"

    @pytest.mark.asyncio
    async def test_replay_lock_has_ttl(self):
        """Replay lock has TTL so crashed process cannot permanently lock entry."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-0"),
        ):
            from db.redis import replay_dlq_entry

            await replay_dlq_entry("dlq-1-0")
            # Verify lock was set with NX and EX (TTL)
            lock_call = mock_redis.set.call_args
            assert lock_call.kwargs.get("nx") is True
            assert lock_call.kwargs.get("ex") == 30

    @pytest.mark.asyncio
    async def test_replay_lock_released_after_completion(self):
        """Replay lock is released after replay completes (success or failure)."""
        mock_redis = AsyncMock()
        payload = {"user_id": "1", "content": "test"}

        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "payload": json.dumps(payload),
                        "replay_count": "0",
                    },
                ),
            ]
        )
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-0"),
        ):
            from db.redis import replay_dlq_entry

            await replay_dlq_entry("dlq-1-0")
            # Lock deleted in finally block
            mock_redis.delete.assert_called_with("dlq_replay_lock:dlq-1-0")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Verify legacy DLQ entries are handled safely."""

    @pytest.mark.asyncio
    async def test_legacy_entry_can_be_inspected(self):
        """Legacy DLQ record without new fields can be inspected."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                ("old-1-0", {"message_id": "orig-1", "reason": "old_error"}),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import get_dlq_entry

            entry = await get_dlq_entry("old-1-0")
            assert entry is not None
            assert entry["reason"] == "old_error"

    @pytest.mark.asyncio
    async def test_legacy_entry_can_be_counted(self):
        """Legacy DLQ entries are counted correctly."""
        mock_redis = AsyncMock()
        mock_redis.xinfo_stream = AsyncMock(return_value={"length": 15})

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import count_dlq_entries

            count = await count_dlq_entries()
            assert count == 15

    @pytest.mark.asyncio
    async def test_legacy_without_payload_reported_non_replayable(self):
        """Legacy entry without payload is reported as non-replayable."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                ("old-1-0", {"message_id": "orig-1", "reason": "old_error"}),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("old-1-0")
            assert result["success"] is False
            assert result["error"] == "missing_original_payload"

    @pytest.mark.asyncio
    async def test_malformed_optional_metadata_gets_defaults(self):
        """Malformed or missing optional metadata doesn't crash inspection."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "dlq-1-0",
                    {
                        "stream": "inbound",
                        "reason": "error",
                        "replay_count": "not_a_number",  # Malformed
                    },
                ),
            ]
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import get_dlq_entry

            entry = await get_dlq_entry("dlq-1-0")
            assert entry is not None
            # Should not crash


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — Cleanup
# ═══════════════════════════════════════════════════════════════════════════════


class TestCleanup:
    """Verify DLQ cleanup works correctly."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_entries(self):
        """cleanup_expired_dlq_entries removes entries older than retention."""
        mock_redis = AsyncMock()
        old_entries = [("old-1-0", {"reason": "a"}), ("old-2-0", {"reason": "b"})]
        mock_redis.xrange = AsyncMock(return_value=old_entries)
        mock_redis.xdel = AsyncMock(return_value=2)

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import cleanup_expired_dlq_entries

            deleted = await cleanup_expired_dlq_entries(retention_seconds=3600)
            assert deleted == 2
            mock_redis.xdel.assert_called_once_with("dead_letter_queue", "old-1-0", "old-2-0")

    @pytest.mark.asyncio
    async def test_cleanup_returns_zero_when_nothing_old(self):
        """cleanup returns 0 when no entries are old enough."""
        mock_redis = AsyncMock()
        mock_redis.xrange = AsyncMock(return_value=[])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import cleanup_expired_dlq_entries

            deleted = await cleanup_expired_dlq_entries(retention_seconds=3600)
            assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_is_bounded(self):
        """Cleanup only processes up to 500 entries per call."""
        mock_redis = AsyncMock()
        # Return exactly 500 entries (the limit)
        mock_redis.xrange = AsyncMock(
            return_value=[(f"e-{i}-0", {"reason": "x"}) for i in range(500)]
        )
        mock_redis.xdel = AsyncMock(return_value=500)

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import cleanup_expired_dlq_entries

            deleted = await cleanup_expired_dlq_entries(retention_seconds=1)
            assert deleted == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Regression: Existing DLQ Semantics Preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegression:
    """Verify existing DLQ and ACK behavior is preserved."""

    @pytest.mark.asyncio
    async def test_move_to_dlq_still_acks_original(self):
        """move_to_dlq still ACKs the original inbound message."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            await move_to_dlq("orig-1-0", "error")
            mock_redis.xack.assert_called_once_with("inbound_messages", "llm_workers", "orig-1-0")

    @pytest.mark.asyncio
    async def test_move_send_to_dlq_still_acks_original(self):
        """move_send_to_dlq still ACKs the original send message."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_send_to_dlq

            await move_send_to_dlq("orig-1-0", "error")
            mock_redis.xack.assert_called_once_with("send_messages", "send_workers", "orig-1-0")

    @pytest.mark.asyncio
    async def test_backward_compat_call_without_payload(self):
        """move_to_dlq still works without payload/worker_id args."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="dlq-1-0")
        mock_redis.xack = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
            from db.redis import move_to_dlq

            # Old-style call should still work
            await move_to_dlq("orig-1-0", "error")
            record = mock_redis.xadd.call_args[0][1]
            assert record["reason"] == "error"
            assert record["stream"] == "inbound"
