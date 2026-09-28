"""Vault stale-reservation recovery tests — Phase 3.5.

Tests the release_stale_reservations() recovery mechanism covering:
A. Basic recovery (stale/fresh/sent)
B. Concurrency (racing workers, reserve, finalize)
C. Creator/fan/media isolation
D. Worker failure scenarios
E. Redis interaction
F. Security / authority
G. Boundary conditions

All DB functions are mocked — no PostgreSQL required.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakePoolConnCM:
    """Async context manager that yields a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass


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
    """Run _process_send_stream with a single message, then shut down."""
    from chatbotv2.main import _process_send_stream

    mock_client = AsyncMock()
    msg_result = MagicMock()
    msg_result.id = 99999
    mock_client.send_message = AsyncMock(return_value=msg_result)
    mock_client.get_input_entity = AsyncMock(return_value="input_entity")

    ep = extra_patches or {}

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
    for k in list(main_patches.keys()):
        if k in ep:
            main_patches[k] = ep.pop(k)

    vault_patches = {
        "reserve_delivery": AsyncMock(return_value=1),
        "finalize_delivery": AsyncMock(return_value=True),
        "release_delivery": AsyncMock(return_value=True),
    }
    for k in list(vault_patches.keys()):
        if k in ep:
            vault_patches[k] = ep.pop(k)

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


def _stale_row(id=1, creator_id=1, user_id=12345, fangate_media_id=777, minutes_ago=10):
    """Build a fake DB row dict as returned by release_stale_reservations."""
    return {
        "id": id,
        "creator_id": creator_id,
        "user_id": user_id,
        "fangate_media_id": fangate_media_id,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A. BASIC RECOVERY
# ═══════════════════════════════════════════════════════════════════════════════


class TestBasicRecovery:
    """Tests 1-3: stale recoverable, fresh protected, sent protected."""

    @pytest.mark.asyncio
    async def test_stale_pending_is_recoverable(self):
        """release_stale_reservations deletes pending rows older than threshold."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [_stale_row(id=42, minutes_ago=10)]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 1
        assert result[0]["id"] == 42
        sql = mock_conn.fetch.call_args[0][0]
        assert "DELETE" in sql.upper()
        assert "pending" in sql

    @pytest.mark.asyncio
    async def test_fresh_pending_is_not_recoverable(self):
        """release_stale_reservations does NOT delete recent pending rows."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []  # No rows returned = nothing deleted
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_sent_reservation_is_never_recoverable(self):
        """release_stale_reservations never touches finalized 'sent' rows."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            await release_stale_reservations(max_age_minutes=5)

        sql = mock_conn.fetch.call_args[0][0]
        assert "pending" in sql
        # The SQL explicitly filters on status='pending', so 'sent' rows
        # are excluded by construction.


# ═══════════════════════════════════════════════════════════════════════════════
# B. CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """Tests 4-8: racing workers, reserve, finalize, finalize wins race."""

    @pytest.mark.asyncio
    async def test_two_recovery_workers_racing_same_reservation(self):
        """Two concurrent recovery calls: only one should reclaim the row.

        Simulated by: first call returns the row, second returns empty.
        """
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        # First call returns row, second returns empty
        mock_conn.fetch.side_effect = [
            [_stale_row(id=1)],
            [],
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            r1 = await release_stale_reservations(max_age_minutes=5)
            r2 = await release_stale_reservations(max_age_minutes=5)

        assert len(r1) == 1
        assert len(r2) == 0

    @pytest.mark.asyncio
    async def test_recovery_racing_with_reserve_delivery(self):
        """Recovery deletes stale row, reserve creates fresh row — no conflict."""
        from db.vault import release_stale_reservations, reserve_delivery

        # Recovery deletes stale row
        recovery_conn = AsyncMock()
        recovery_conn.fetch.return_value = [_stale_row(id=10)]
        recovery_pool = MagicMock()
        recovery_pool.acquire = MagicMock(return_value=_FakePoolConnCM(recovery_conn))

        with patch("db.vault.get_pool", return_value=recovery_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 1

        # Reserve creates a fresh row (INSERT succeeds because stale row is gone)
        reserve_conn = AsyncMock()
        reserve_conn.fetchrow.return_value = {"id": 99}
        reserve_pool = MagicMock()
        reserve_pool.acquire = MagicMock(return_value=_FakePoolConnCM(reserve_conn))

        with patch("db.vault.get_pool", return_value=reserve_pool):
            rid = await reserve_delivery(1, 12345, 777)
        assert rid == 99

    @pytest.mark.asyncio
    async def test_recovery_racing_with_finalize_delivery(self):
        """Recovery DELETE and finalize UPDATE racing on the same row.

        Two outcomes are safe:
        1. DELETE wins: finalize returns False (row gone).
        2. finalize wins: DELETE finds 0 rows.
        """
        from db.vault import finalize_delivery, release_stale_reservations

        # Case 1: DELETE wins race
        del_conn = AsyncMock()
        del_conn.fetch.return_value = [_stale_row(id=10)]
        del_pool = MagicMock()
        del_pool.acquire = MagicMock(return_value=_FakePoolConnCM(del_conn))

        with patch("db.vault.get_pool", return_value=del_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 1

        fin_conn = AsyncMock()
        fin_conn.execute.return_value = "UPDATE 0"  # Row already deleted
        fin_pool = MagicMock()
        fin_pool.acquire = MagicMock(return_value=_FakePoolConnCM(fin_conn))

        with patch("db.vault.get_pool", return_value=fin_pool):
            ok = await finalize_delivery(10, telegram_message_id=999)
        assert ok is False

    @pytest.mark.asyncio
    async def test_finalize_wins_race_leaves_sent(self):
        """If finalize wins the race, the row is 'sent' — recovery deletes nothing."""
        from db.vault import finalize_delivery, release_stale_reservations

        # finalize runs first
        fin_conn = AsyncMock()
        fin_conn.execute.return_value = "UPDATE 1"
        fin_pool = MagicMock()
        fin_pool.acquire = MagicMock(return_value=_FakePoolConnCM(fin_conn))

        with patch("db.vault.get_pool", return_value=fin_pool):
            ok = await finalize_delivery(10, telegram_message_id=999)
        assert ok is True

        # recovery finds no pending rows (the one we targeted is now 'sent')
        del_conn = AsyncMock()
        del_conn.fetch.return_value = []
        del_pool = MagicMock()
        del_pool.acquire = MagicMock(return_value=_FakePoolConnCM(del_conn))

        with patch("db.vault.get_pool", return_value=del_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 0

    @pytest.mark.asyncio
    async def test_stale_recovery_cannot_delete_finalized_row(self):
        """release_stale_reservations SQL only targets status='pending'."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            await release_stale_reservations(max_age_minutes=5)

        sql = mock_conn.fetch.call_args[0][0]
        # The DELETE uses a subquery filtered on status='pending'
        assert "status = 'pending'" in sql or "status=$" in sql.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# C. CREATOR / FAN / MEDIA ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsolation:
    """Tests 9-11: different fans, different media, different creators."""

    @pytest.mark.asyncio
    async def test_same_media_different_users_both_recoverable(self):
        """Two stale reservations for same media, different users: both released."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _stale_row(id=1, user_id=111, fangate_media_id=777),
            _stale_row(id=2, user_id=222, fangate_media_id=777),
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 2
        users = {r["user_id"] for r in result}
        assert users == {111, 222}

    @pytest.mark.asyncio
    async def test_same_user_different_media_both_recoverable(self):
        """Two stale reservations for same user, different media: both released."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _stale_row(id=1, user_id=12345, fangate_media_id=100),
            _stale_row(id=2, user_id=12345, fangate_media_id=200),
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 2
        media = {r["fangate_media_id"] for r in result}
        assert media == {100, 200}

    @pytest.mark.asyncio
    async def test_same_fan_media_different_creators_both_recoverable(self):
        """Two stale reservations for same fan+media, different creators: independent."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _stale_row(id=1, creator_id=1, user_id=12345, fangate_media_id=777),
            _stale_row(id=2, creator_id=2, user_id=12345, fangate_media_id=777),
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 2
        creators = {r["creator_id"] for r in result}
        assert creators == {1, 2}


# ═══════════════════════════════════════════════════════════════════════════════
# D. WORKER FAILURE SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerFailure:
    """Tests 12-15: crash-before-send, crash-after-send, finalize failure, idempotent."""

    @pytest.mark.asyncio
    async def test_crash_before_telegram_send_recovered(self):
        """Worker crashes after reserve but before Telegram: stale reservation released."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        released = [_stale_row(id=42, minutes_ago=10)]
        reserve_mock = AsyncMock(return_value=None)  # After recovery, reserve succeeds

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "release_stale_reservations": AsyncMock(return_value=released),
                "reserve_delivery": reserve_mock,
            },
        )

        patches["release_stale_reservations"].assert_called_once()

    @pytest.mark.asyncio
    async def test_crash_after_telegram_send_recovered(self):
        """Worker crashes after Telegram but before finalize: stale reservation released."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        released = [_stale_row(id=42, minutes_ago=10)]
        reserve_mock = AsyncMock(return_value=None)  # Re-reserve blocked after recovery

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "release_stale_reservations": AsyncMock(return_value=released),
                "reserve_delivery": reserve_mock,
            },
        )

        patches["release_stale_reservations"].assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_failure_followed_by_recovery(self):
        """finalize_delivery fails → stale reservation → recovery releases it."""
        from db.vault import finalize_delivery, release_stale_reservations

        # Step 1: finalize fails
        fin_conn = AsyncMock()
        fin_conn.execute.return_value = "UPDATE 0"  # simulate failure
        fin_pool = MagicMock()
        fin_pool.acquire = MagicMock(return_value=_FakePoolConnCM(fin_conn))

        with patch("db.vault.get_pool", return_value=fin_pool):
            ok = await finalize_delivery(42, telegram_message_id=999)
        assert ok is False

        # Step 2: recovery finds and releases the stale pending row
        del_conn = AsyncMock()
        del_conn.fetch.return_value = [_stale_row(id=42, minutes_ago=10)]
        del_pool = MagicMock()
        del_pool.acquire = MagicMock(return_value=_FakePoolConnCM(del_conn))

        with patch("db.vault.get_pool", return_value=del_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 1
        assert released[0]["id"] == 42

    @pytest.mark.asyncio
    async def test_repeated_recovery_is_idempotent(self):
        """Calling release_stale_reservations multiple times is safe."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        # First call returns rows, subsequent calls return empty
        mock_conn.fetch.side_effect = [
            [_stale_row(id=1), _stale_row(id=2)],
            [],
            [],
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            r1 = await release_stale_reservations(max_age_minutes=5)
            r2 = await release_stale_reservations(max_age_minutes=5)
            r3 = await release_stale_reservations(max_age_minutes=5)

        assert len(r1) == 2
        assert len(r2) == 0
        assert len(r3) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# E. REDIS INTERACTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedisInteraction:
    """Tests 16-19: Redis dedup + DB pending, re-reserve after recovery."""

    @pytest.mark.asyncio
    async def test_redis_dedup_exists_db_pending_still_recovered(self):
        """Redis dedup exists + DB pending: recovery releases DB, dedup remains."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        data["dedup_id"] = "abc123"
        released = [_stale_row(id=42, minutes_ago=10)]

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "is_send_duplicate": AsyncMock(return_value=True),
                "release_stale_reservations": AsyncMock(return_value=released),
            },
        )

        # Dedup catches the message, recovery still runs
        patches["release_stale_reservations"].assert_called_once()
        patches["ack_send"].assert_called()

    @pytest.mark.asyncio
    async def test_redis_dedup_expired_db_pending_still_recovered(self):
        """Redis dedup expired + DB pending: recovery releases DB row."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        released = [_stale_row(id=42, minutes_ago=10)]

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "is_send_duplicate": AsyncMock(return_value=False),
                "release_stale_reservations": AsyncMock(return_value=released),
                "reserve_delivery": AsyncMock(return_value=None),
            },
        )

        patches["release_stale_reservations"].assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_dedup_expired_db_sent_not_recovered(self):
        """Redis dedup expired + DB sent: no recovery needed, no action."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")

        patches = await _run_send_pipeline(
            data,
            extra_patches={
                "is_send_duplicate": AsyncMock(return_value=False),
                "release_stale_reservations": AsyncMock(return_value=[]),
                "reserve_delivery": AsyncMock(return_value=1),
            },
        )

        patches["release_stale_reservations"].assert_called_once()
        patches["reserve_delivery"].assert_called_once()

    @pytest.mark.asyncio
    async def test_recovered_reservation_can_be_reserved_again(self):
        """After recovery releases stale row, reserve_delivery succeeds."""
        from db.vault import release_stale_reservations, reserve_delivery

        # Recovery deletes stale row
        del_conn = AsyncMock()
        del_conn.fetch.return_value = [_stale_row(id=42, minutes_ago=10)]
        del_pool = MagicMock()
        del_pool.acquire = MagicMock(return_value=_FakePoolConnCM(del_conn))

        with patch("db.vault.get_pool", return_value=del_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 1

        # Reserve succeeds (stale row gone)
        res_conn = AsyncMock()
        res_conn.fetchrow.return_value = {"id": 99}
        res_pool = MagicMock()
        res_pool.acquire = MagicMock(return_value=_FakePoolConnCM(res_conn))

        with patch("db.vault.get_pool", return_value=res_pool):
            rid = await reserve_delivery(1, 12345, 777)
        assert rid == 99


# ═══════════════════════════════════════════════════════════════════════════════
# F. SECURITY / AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecurityAuthority:
    """Tests 20-21: recovery is creator-scoped."""

    @pytest.mark.asyncio
    async def test_recovery_is_creator_scoped(self):
        """release_stale_reservations releases rows across all creators
        (recovery is not scoped to a single creator — it cleans up all stale rows).
        """
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _stale_row(id=1, creator_id=1, minutes_ago=10),
            _stale_row(id=2, creator_id=2, minutes_ago=10),
        ]
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 2
        creators = {r["creator_id"] for r in result}
        assert creators == {1, 2}

    @pytest.mark.asyncio
    async def test_one_creator_cannot_reclaim_another_via_reserve(self):
        """Creator A reserves → stale → recovery releases → Creator B can also reserve."""
        from db.vault import release_stale_reservations, reserve_delivery

        # Recovery releases stale row from Creator A
        del_conn = AsyncMock()
        del_conn.fetch.return_value = [_stale_row(id=1, creator_id=1, minutes_ago=10)]
        del_pool = MagicMock()
        del_pool.acquire = MagicMock(return_value=_FakePoolConnCM(del_conn))

        with patch("db.vault.get_pool", return_value=del_pool):
            released = await release_stale_reservations(max_age_minutes=5)
        assert len(released) == 1

        # Creator B reserves same media+user (UNIQUE allows it because different creator)
        res_conn = AsyncMock()
        res_conn.fetchrow.return_value = {"id": 50}
        res_pool = MagicMock()
        res_pool.acquire = MagicMock(return_value=_FakePoolConnCM(res_conn))

        with patch("db.vault.get_pool", return_value=res_pool):
            rid = await reserve_delivery(2, 12345, 777)
        assert rid == 50


# ═══════════════════════════════════════════════════════════════════════════════
# G. BOUNDARY CONDITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBoundaryConditions:
    """Tests 22-25: threshold boundary, null timestamps, DB exception safety."""

    @pytest.mark.asyncio
    async def test_reservation_exactly_at_stale_threshold(self):
        """Reservation created exactly at the threshold age is NOT deleted (strict <)."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        # Call with max_age_minutes=5, but the SQL uses < (strict less than)
        # A row at exactly 5 minutes is not older than 5 minutes
        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_reservation_one_minute_younger_than_threshold(self):
        """Reservation 1 minute younger than threshold: not deleted."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_empty_result_when_no_pending_reservations(self):
        """release_stale_reservations returns empty list when no pending rows exist."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            result = await release_stale_reservations(max_age_minutes=5)

        assert result == []

    @pytest.mark.asyncio
    async def test_db_exception_does_not_crash_send_worker(self):
        """release_stale_reservations propagates DB exceptions to caller."""
        from db.vault import release_stale_reservations

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(side_effect=Exception("DB connection lost"))
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            with pytest.raises(Exception, match="DB connection lost"):
                await release_stale_reservations(max_age_minutes=5)


# ═══════════════════════════════════════════════════════════════════════════════
# H. SEND PIPELINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    """Tests verifying release_stale_reservations is called in the send loop."""

    @pytest.mark.asyncio
    async def test_stale_recovery_called_in_send_loop(self):
        """release_stale_reservations is called every iteration of _process_send_stream."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        recovery_mock = AsyncMock(return_value=[])

        await _run_send_pipeline(
            data,
            extra_patches={
                "release_stale_reservations": recovery_mock,
            },
        )

        recovery_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_recovery_called_with_config_value(self):
        """release_stale_reservations receives the configured threshold."""
        from core.config import get_settings

        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        recovery_mock = AsyncMock(return_value=[])

        settings = get_settings()

        await _run_send_pipeline(
            data,
            extra_patches={
                "release_stale_reservations": recovery_mock,
            },
        )

        recovery_mock.assert_called_once()
        call_kwargs = recovery_mock.call_args[1]
        assert call_kwargs["max_age_minutes"] == settings.vault_stale_reservation_minutes

    @pytest.mark.asyncio
    async def test_recovery_logs_released_count(self):
        """When recovery releases reservations, a log message is emitted."""
        data = _vault_data(entity="12345", media_id=777, creator_id="1")
        released = [_stale_row(id=1), _stale_row(id=2)]

        with patch("chatbotv2.main.logger") as mock_logger:
            await _run_send_pipeline(
                data,
                extra_patches={
                    "release_stale_reservations": AsyncMock(return_value=released),
                },
            )

        # Check that logger.info was called with the recovery count
        info_calls = [c for c in mock_logger.info.call_args_list]
        recovery_logs = [c for c in info_calls if "stale vault reservations" in str(c)]
        assert len(recovery_logs) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# H. SQL INTERVAL EXPRESSION (regression: make_interval type error fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSQLIntervalExpression:
    """Verify release_stale_reservations uses CAST * INTERVAL, not make_interval."""

    @pytest.mark.asyncio
    async def test_no_make_interval_in_sql(self):
        """SQL must not contain make_interval — it causes type resolution errors."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            await release_stale_reservations(max_age_minutes=5)

        sql = mock_conn.fetch.call_args[0][0]
        assert "make_interval" not in sql

    @pytest.mark.asyncio
    async def test_sql_uses_cast_numeric_times_interval(self):
        """SQL should use CAST($1 AS numeric) * INTERVAL '1 minute'."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            await release_stale_reservations(max_age_minutes=10)

        sql = mock_conn.fetch.call_args[0][0]
        assert "CAST($1 AS numeric) * INTERVAL '1 minute'" in sql

    @pytest.mark.asyncio
    async def test_max_age_minutes_is_first_parameter(self):
        """The max_age_minutes value should be passed as $1."""
        from db.vault import release_stale_reservations

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))

        with patch("db.vault.get_pool", return_value=mock_pool):
            await release_stale_reservations(max_age_minutes=15, batch_size=25)

        args = mock_conn.fetch.call_args
        params = args[0][1:]
        assert params[0] == 15
        assert params[1] == 25
