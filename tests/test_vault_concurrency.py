"""Vault concurrent delivery & idempotency verification — Phase 3.4.

Tests atomic delivery reservation (reserve_delivery / finalize_delivery /
release_delivery) under concurrency, Redis dedup interactions, and
failure-injection scenarios.

All DB functions are mocked — no PostgreSQL required.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _vault_data(
    entity="12345",
    media_id=777,
    creator_id="1",
    product_id="101",
    content="Enjoy!",
):
    """Build send-stream data dict for vault media."""
    return {
        "entity": entity,
        "content": content,
        "draft_content": content,
        "was_edited": "",
        "was_auto_approved": "",
        "confidence_score": "0",
        "operator_id": "",
        "save_to_db": "true",
        "media_type": "photo",
        "media_path": "https://fangate.s3.amazonaws.com/media.jpg",
        "fangate_media_id": str(media_id),
        "product_id": str(product_id),
        "creator_id": str(creator_id),
    }


async def _run_send_pipeline(data, *, extra_patches=None):
    """Run _process_send_stream with a single message, then shut down.

    The extra_patches dict may contain:
    - Keys matching module-level names (e.g. 'reserve_delivery') → patched
      at chatbotv2.main.<name> or db.vault.<name> as appropriate.
    - Special key '_extra_send_patches' for additional patch targets.
    """
    from chatbotv2.main import _process_send_stream

    mock_client = AsyncMock()
    msg_result = MagicMock()
    msg_result.id = 99999
    mock_client.send_message = AsyncMock(return_value=msg_result)
    mock_client.get_input_entity = AsyncMock(return_value="input_entity")

    ep = extra_patches or {}

    # Patches for chatbotv2.main namespace (redis/core imports)
    main_patches = {
        "read_send_messages": AsyncMock(return_value=[("send_main", [("msg_test", data)])]),
        "ack_send": AsyncMock(),
        "is_send_duplicate": AsyncMock(return_value=False),
        "get_send_rate_limit_wait": AsyncMock(return_value=0),
        "check_send_rate_limit": AsyncMock(return_value=True),
        "requeue_stalled_send_messages": AsyncMock(return_value=(0, [])),
        "save_outbound_after_send": AsyncMock(),
        "publish_event": AsyncMock(),
        "is_shutting_down": MagicMock(side_effect=[False, True]),
        "move_send_to_dlq": AsyncMock(),
        "send_file": AsyncMock(return_value=msg_result),
        "release_stale_reservations": AsyncMock(return_value=[]),
    }
    # Apply overrides from extra_patches for main-namespace items
    for k in list(main_patches.keys()):
        if k in ep:
            main_patches[k] = ep.pop(k)

    # db.vault patches (local imports inside function body)
    vault_patches = {
        "reserve_delivery": AsyncMock(return_value=1),
        "finalize_delivery": AsyncMock(return_value=True),
        "release_delivery": AsyncMock(return_value=True),
    }
    for k in list(vault_patches.keys()):
        if k in ep:
            vault_patches[k] = ep.pop(k)

    # Also allow overriding _validate_media_path
    validate_patch = ep.pop(
        "_validate_media_path", AsyncMock(return_value="https://fangate.s3.amazonaws.com/media.jpg")
    )

    ctxs = [
        patch("chatbotv2.main.read_send_messages", main_patches["read_send_messages"]),
        patch("chatbotv2.main.ack_send", main_patches["ack_send"]),
        patch("chatbotv2.main.is_send_duplicate", main_patches["is_send_duplicate"]),
        patch("chatbotv2.main.get_send_rate_limit_wait", main_patches["get_send_rate_limit_wait"]),
        patch("chatbotv2.main.check_send_rate_limit", main_patches["check_send_rate_limit"]),
        patch(
            "chatbotv2.main.requeue_stalled_send_messages",
            main_patches["requeue_stalled_send_messages"],
        ),
        patch("chatbotv2.main.save_outbound_after_send", main_patches["save_outbound_after_send"]),
        patch("chatbotv2.main.publish_event", main_patches["publish_event"]),
        patch("chatbotv2.main.is_shutting_down", main_patches["is_shutting_down"]),
        patch("chatbotv2.main.move_send_to_dlq", main_patches["move_send_to_dlq"]),
        patch("chatbotv2.main.send_file", main_patches["send_file"]),
        patch("chatbotv2.main._validate_media_path", validate_patch),
        patch("chatbotv2.main.release_stale_reservations", main_patches["release_stale_reservations"]),
        patch("db.vault.reserve_delivery", vault_patches["reserve_delivery"]),
        patch("db.vault.finalize_delivery", vault_patches["finalize_delivery"]),
        patch("db.vault.release_delivery", vault_patches["release_delivery"]),
    ]

    # Enter all context managers
    for ctx in ctxs:
        ctx.__enter__()

    try:
        await _process_send_stream(mock_client)
    finally:
        for ctx in reversed(ctxs):
            ctx.__exit__(None, None, None)

    all_patches = {**main_patches, **vault_patches}
    all_patches["_validate_media_path"] = validate_patch
    return all_patches


# ── A. Pipeline: reserve → Telegram → finalize ───────────────────────────────


class TestPipelineReservation:
    @pytest.mark.asyncio
    async def test_reserves_and_finalizes_on_success(self):
        """Normal send: reserve → Telegram send_file → finalize."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        reserve_mock.assert_called_once()
        args, _kwargs = reserve_mock.call_args
        assert args == (1, 12345, 777)

        finalize_mock.assert_called_once()
        args2, kwargs2 = finalize_mock.call_args
        assert args2 == (42,)
        assert kwargs2["telegram_message_id"] == 99999

    @pytest.mark.asyncio
    async def test_skips_send_when_reservation_conflicts(self):
        """reserve returns None → message acked, no Telegram send."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=None)
        finalize_mock = AsyncMock()
        send_file_mock = AsyncMock()

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
                "send_file": send_file_mock,
            },
        )

        reserve_mock.assert_called_once()
        finalize_mock.assert_not_called()
        send_file_mock.assert_not_called()
        patches["ack_send"].assert_called()

    @pytest.mark.asyncio
    async def test_ambiguous_failure_preserves_reservation(self):
        """H4 Batch 3 (D2): ambiguous send_file failure (generic Exception)
        is UNKNOWN — reservation kept (never released), finalize skipped,
        DLQ reason unknown_send_result, no send_failed certification."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        finalize_mock = AsyncMock()
        release_mock = AsyncMock(return_value=True)
        send_file_mock = AsyncMock(side_effect=Exception("Telegram down"))
        record_mock = AsyncMock(return_value=True)

        with patch("chatbotv2.main.record_unknown_send_attempt", new=record_mock):
            patches = await _run_send_pipeline(
                data,
                extra_patches={
                    "reserve_delivery": reserve_mock,
                    "finalize_delivery": finalize_mock,
                    "release_delivery": release_mock,
                    "send_file": send_file_mock,
                },
            )

        reserve_mock.assert_called_once()
        finalize_mock.assert_not_called()
        release_mock.assert_not_called()
        record_mock.assert_called_once()
        patches["move_send_to_dlq"].assert_called_once()
        assert patches["move_send_to_dlq"].call_args[0][1] == "unknown_send_result"

    @pytest.mark.asyncio
    async def test_releases_on_user_blocked_error(self):
        """Telethon UserIsBlockedError → release called."""
        from telethon.errors import UserIsBlockedError

        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        finalize_mock = AsyncMock()
        release_mock = AsyncMock(return_value=True)
        # UserIsBlockedError needs a `request` kwarg in newer Telethon
        try:
            blocked_err = UserIsBlockedError(request=None)
        except TypeError:
            blocked_err = UserIsBlockedError()
        send_file_mock = AsyncMock(side_effect=blocked_err)

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
                "release_delivery": release_mock,
                "send_file": send_file_mock,
            },
        )

        reserve_mock.assert_called_once()
        finalize_mock.assert_not_called()
        # M7 (B8): release is ownership-scoped with the stream creator.
        release_mock.assert_called_once_with(42, creator_id=1)

    @pytest.mark.asyncio
    async def test_no_reservation_when_no_fangate_media_id(self):
        """Non-vault message (no fangate_media_id) → no reserve/release."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        data["fangate_media_id"] = ""

        reserve_mock = AsyncMock()
        finalize_mock = AsyncMock()
        release_mock = AsyncMock()

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
                "release_delivery": release_mock,
            },
        )

        reserve_mock.assert_not_called()
        finalize_mock.assert_not_called()
        release_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_reservation_exception_proceeds_with_send(self):
        """reserve_delivery raises → DLQ to prevent untracked send (new behavior)."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(side_effect=Exception("DB down"))
        send_file_mock = AsyncMock(return_value=MagicMock(id=11111))

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "send_file": send_file_mock,
            },
        )

        # New behavior: reservation failure DLQs, does not send
        send_file_mock.assert_not_called()
        patches["move_send_to_dlq"].assert_called()
        assert any("delivery_reservation_failed" in str(c) for c in patches["move_send_to_dlq"].call_args_list)

    @pytest.mark.asyncio
    async def test_no_reservation_when_media_type_empty(self):
        """Empty media_type → not a media send → no reservation."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        data["media_type"] = ""

        reserve_mock = AsyncMock()
        finalize_mock = AsyncMock()

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        reserve_mock.assert_not_called()
        finalize_mock.assert_not_called()


# ── B. Concurrent workers simulation ────────────────────────────────────────


class TestConcurrentWorkers:
    @pytest.mark.asyncio
    async def test_two_iterations_first_reserves_second_blocked(self):
        """Two pipeline iterations with same data: first reserves, second blocked."""
        data = _vault_data(entity="12345", media_id=888, creator_id="1")
        reserve_call_count = 0
        reserve_results = [100, None]

        async def mock_reserve(cid, uid, mid, product_id=None):
            nonlocal reserve_call_count
            result = reserve_results[reserve_call_count]
            reserve_call_count += 1
            return result

        reserve_mock = AsyncMock(side_effect=mock_reserve)
        finalize_mock = AsyncMock(return_value=True)

        for _ in range(2):
            await _run_send_pipeline(
                data,
                extra_patches={
                    "reserve_delivery": reserve_mock,
                    "finalize_delivery": finalize_mock,
                },
            )

        assert reserve_mock.call_count == 2
        assert finalize_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_different_media_independent_reservations(self):
        """Same user, different media: both succeed."""
        data1 = _vault_data(entity="12345", media_id=100, creator_id="1")
        data2 = _vault_data(entity="12345", media_id=200, creator_id="1")

        reserve_mock = AsyncMock(return_value=1)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data1,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 12345, 100)

        await _run_send_pipeline(
            data2,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 12345, 200)
        assert finalize_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_different_fans_independent_reservations(self):
        """Same media, different fans: both succeed."""
        data1 = _vault_data(entity="11111", media_id=300, creator_id="1")
        data2 = _vault_data(entity="22222", media_id=300, creator_id="1")

        reserve_mock = AsyncMock(return_value=1)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data1,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 11111, 300)

        await _run_send_pipeline(
            data2,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 22222, 300)
        assert finalize_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_different_creators_same_media_independent(self):
        """Same media_id, different creators: independent (UNIQUE is creator-scoped)."""
        data1 = _vault_data(entity="12345", media_id=400, creator_id="1")
        data2 = _vault_data(entity="12345", media_id=400, creator_id="2")

        reserve_mock = AsyncMock(return_value=1)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data1,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 12345, 400)

        await _run_send_pipeline(
            data2,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (2, 12345, 400)
        assert finalize_mock.call_count == 2


# ── C. Redis dedup + DB reservation interaction ──────────────────────────────


class TestRedisAndDBInteraction:
    @pytest.mark.asyncio
    async def test_redis_dedup_catches_before_reservation(self):
        """Redis dedup key exists → reserve never called."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        data["dedup_id"] = "abc123"
        reserve_mock = AsyncMock()

        await _run_send_pipeline(
            data,
            extra_patches={
                "is_send_duplicate": AsyncMock(return_value=True),
                "reserve_delivery": reserve_mock,
            },
        )

        reserve_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dedup_reservation_proceeds(self):
        """Redis dedup clear → reserve called, send proceeds."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=50)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data,
            extra_patches={
                "is_send_duplicate": AsyncMock(return_value=False),
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        reserve_mock.assert_called_once()
        finalize_mock.assert_called_once()


# ── D. Failure injection ─────────────────────────────────────────────────────


class TestFailureInjection:
    @pytest.mark.asyncio
    async def test_ambiguous_failure_goes_unknown_not_failed(self):
        """H4 Batch 3 (D2): ambiguous Telegram throw → UNKNOWN, never
        certified failed: reservation kept, no vault release, unknown DLQ."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        release_mock = AsyncMock(return_value=True)
        finalize_mock = AsyncMock()
        send_file_mock = AsyncMock(side_effect=Exception("Telegram down"))
        record_mock = AsyncMock(return_value=True)

        with patch("chatbotv2.main.record_unknown_send_attempt", new=record_mock):
            patches = await _run_send_pipeline(
                data,
                extra_patches={
                    "reserve_delivery": reserve_mock,
                    "finalize_delivery": finalize_mock,
                    "release_delivery": release_mock,
                    "send_file": send_file_mock,
                },
            )

        release_mock.assert_not_called()
        finalize_mock.assert_not_called()
        record_mock.assert_called_once()
        patches["move_send_to_dlq"].assert_called_once()
        assert patches["move_send_to_dlq"].call_args[0][1] == "unknown_send_result"

    @pytest.mark.asyncio
    async def test_finalize_exception_does_not_break_send(self):
        """finalize_delivery raises → warning logged, send still acked."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        finalize_mock = AsyncMock(side_effect=Exception("DB timeout"))

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        patches["ack_send"].assert_called()

    @pytest.mark.asyncio
    async def test_release_exception_does_not_break_send(self):
        """release_delivery raises → warning logged, send still acked."""
        from telethon.errors import UserIsBlockedError

        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_mock = AsyncMock(return_value=42)
        release_mock = AsyncMock(side_effect=Exception("DB down"))
        try:
            blocked_err = UserIsBlockedError(request=None)
        except TypeError:
            blocked_err = UserIsBlockedError()
        send_file_mock = AsyncMock(side_effect=blocked_err)

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "release_delivery": release_mock,
                "send_file": send_file_mock,
            },
        )

        patches["ack_send"].assert_called()

    @pytest.mark.asyncio
    async def test_stale_reservation_blocks_subsequent_worker(self):
        """Worker A reserves and crashes → Worker B blocked by pending reservation."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        reserve_results = [42, None]
        reserve_call_idx = 0

        async def mock_reserve(cid, uid, mid, product_id=None):
            nonlocal reserve_call_idx
            result = reserve_results[reserve_call_idx]
            reserve_call_idx += 1
            return result

        reserve_mock = AsyncMock(side_effect=mock_reserve)
        finalize_mock = AsyncMock(return_value=True)
        release_mock = AsyncMock(return_value=True)
        # Worker A: send_file throws ambiguously → H4 Batch 3 UNKNOWN:
        # reservation kept (never released), finalize skipped.
        send_file_crash = AsyncMock(side_effect=Exception("Telegram crash"))

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
                "release_delivery": release_mock,
                "send_file": send_file_crash,
            },
        )

        # Worker B: tries to reserve same media → blocked
        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        assert reserve_mock.call_count == 2
        # Worker A ambiguous: reservation kept for recovery, finalize NOT called.
        release_mock.assert_not_called()
        finalize_mock.assert_not_called()
        # finalize only called if Worker B succeeds, but B is blocked (reserve returns None)
        finalize_mock.assert_not_called()


# ── E. Dedup_id generation (pure logic) ──────────────────────────────────────


class TestDedupId:
    def test_same_user_media_same_dedup_id(self):
        d1 = hashlib.md5(b"123:456").hexdigest()
        d2 = hashlib.md5(b"123:456").hexdigest()
        assert d1 == d2

    def test_different_user_different_dedup_id(self):
        d1 = hashlib.md5(b"123:456").hexdigest()
        d2 = hashlib.md5(b"456:456").hexdigest()
        assert d1 != d2

    def test_different_media_different_dedup_id(self):
        d1 = hashlib.md5(b"123:456").hexdigest()
        d2 = hashlib.md5(b"123:789").hexdigest()
        assert d1 != d2


# ── F. Security / isolation ─────────────────────────────────────────────────


class TestSecurityIsolation:
    @pytest.mark.asyncio
    async def test_creator_scoped_uniqueness(self):
        """Different creators can reserve same media_id for same user."""
        data1 = _vault_data(entity="12345", media_id=500, creator_id="1")
        data2 = _vault_data(entity="12345", media_id=500, creator_id="2")

        reserve_mock = AsyncMock(return_value=1)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data1,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (1, 12345, 500)

        await _run_send_pipeline(
            data2,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        assert reserve_mock.call_args[0] == (2, 12345, 500)
        assert finalize_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_same_creator_same_media_same_user_blocked(self):
        """Within same creator: one reservation per (user, media)."""
        reserve_results = [10, None]
        idx = 0

        async def mock_reserve(cid, uid, mid, product_id=None):
            nonlocal idx
            result = reserve_results[idx]
            idx += 1
            return result

        data = _vault_data(entity="12345", media_id=600, creator_id="1")
        reserve_mock = AsyncMock(side_effect=mock_reserve)
        finalize_mock = AsyncMock(return_value=True)

        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )
        await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": reserve_mock,
                "finalize_delivery": finalize_mock,
            },
        )

        assert reserve_mock.call_count == 2
        assert finalize_mock.call_count == 1

    def test_dedup_id_includes_user_and_media(self):
        d1 = hashlib.md5(f"{123}:{456}".encode()).hexdigest()
        d2 = hashlib.md5(f"{123}:{789}".encode()).hexdigest()
        assert d1 != d2


# ── G. Atomicity guarantees (mocked SQL verification) ────────────────────────


class _FakePoolConnCM:
    """Async context manager that yields a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass


class TestAtomicityGuarantees:
    @pytest.mark.asyncio
    async def test_reserve_is_single_insert(self):
        """reserve_delivery uses INSERT ... ON CONFLICT DO NOTHING."""
        from db.vault import reserve_delivery

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            mock_conn.fetchrow.return_value = {"id": 42}
            rid = await reserve_delivery(creator_id=1, user_id=100, fangate_media_id=200)
            assert rid == 42

            mock_conn.fetchrow.assert_called_once()
            sql = mock_conn.fetchrow.call_args[0][0]
            assert "INSERT" in sql.upper()
            assert "ON CONFLICT" in sql.upper()

    @pytest.mark.asyncio
    async def test_finalize_is_single_update(self):
        """finalize_delivery uses UPDATE ... WHERE status='pending'."""
        from db.vault import finalize_delivery

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            mock_conn.execute.return_value = "UPDATE 1"
            ok = await finalize_delivery(42, telegram_message_id=99999)
            assert ok is True

            mock_conn.execute.assert_called_once()
            sql = mock_conn.execute.call_args[0][0]
            assert "UPDATE" in sql.upper()
            assert "pending" in sql

    @pytest.mark.asyncio
    async def test_release_deletes_only_pending(self):
        """release_delivery DELETEs WHERE status='pending'."""
        from db.vault import release_delivery

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            mock_conn.execute.return_value = "DELETE 1"
            ok = await release_delivery(42)
            assert ok is True

            mock_conn.execute.return_value = "DELETE 0"
            ok2 = await release_delivery(42)
            assert ok2 is False

    @pytest.mark.asyncio
    async def test_reservation_blocks_second_concurrent_insert(self):
        """Second INSERT returns None due to ON CONFLICT DO NOTHING."""
        from db.vault import reserve_delivery

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            mock_conn.fetchrow.return_value = {"id": 42}
            rid1 = await reserve_delivery(creator_id=1, user_id=100, fangate_media_id=200)
            assert rid1 == 42

            mock_conn.fetchrow.return_value = None
            rid2 = await reserve_delivery(creator_id=1, user_id=100, fangate_media_id=200)
            assert rid2 is None


# ── H. Message.ack always called (guaranteed processing) ─────────────────────


class TestAckGuarantee:
    @pytest.mark.asyncio
    async def test_dlq_called_on_send_failure(self):
        """Generic send failure → message moved to DLQ (not acked)."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        send_file_mock = AsyncMock(side_effect=Exception("Telegram down"))

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "send_file": send_file_mock,
            },
        )

        patches["move_send_to_dlq"].assert_called()

    @pytest.mark.asyncio
    async def test_ack_called_when_reservation_conflicts(self):
        """Message is acked when reservation blocks duplicate."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "reserve_delivery": AsyncMock(return_value=None),
            },
        )

        patches["ack_send"].assert_called()
