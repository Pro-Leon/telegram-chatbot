"""P2.3 — Scheduler Status Guards, Recovery, Cancel, Config, Health Tests.

Focused test suite covering:
A. Status guards (enqueue / failed)
B. Recovery retry limits
C. Cancel semantics (pending-only)
D. Cancel rich return values
E. Config integration
F. Health enhancements (lag)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ==============================================================================
# Helpers  (same pattern as test_scheduled_messages.py)
# ==============================================================================


class _FakeConn:
    def __init__(self, conn):
        self._conn = conn

    def transaction(self):
        return _FakeTxn(self._conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _FakeTxn:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


def _make_pool(conn):
    fake_conn = _FakeConn(conn)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=acquire_ctx)
    return mock_pool


# ==============================================================================
# A — STATUS GUARDS
# ==============================================================================


class TestStatusGuards:
    """mark_scheduled_enqueued / mark_scheduled_failed return True only when
    the UPDATE actually affected a row (status was processing)."""

    # -- mark_scheduled_enqueued --

    @pytest.mark.asyncio
    async def test_enqueue_returns_true_on_update_1(self):
        from db.postgres import mark_scheduled_enqueued

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_enqueued(1) is True

    @pytest.mark.asyncio
    async def test_enqueue_returns_false_on_update_0(self):
        from db.postgres import mark_scheduled_enqueued

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_enqueued(1) is False

    @pytest.mark.asyncio
    async def test_enqueue_sql_has_where_processing(self):
        from db.postgres import mark_scheduled_enqueued

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            await mark_scheduled_enqueued(42)
        sql = mock_conn.execute.call_args[0][0]
        assert "status = 'processing'" in sql

    @pytest.mark.asyncio
    async def test_enqueue_returns_false_for_already_completed(self):
        from db.postgres import mark_scheduled_enqueued

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_enqueued(99) is False

    # -- mark_scheduled_failed --

    @pytest.mark.asyncio
    async def test_failed_returns_true_on_update_1(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_failed(1, "timeout") is True

    @pytest.mark.asyncio
    async def test_failed_returns_false_on_update_0(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_failed(1, "timeout") is False

    @pytest.mark.asyncio
    async def test_failed_sql_has_where_processing(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            await mark_scheduled_failed(7, "enqueue_error")
        sql = mock_conn.execute.call_args[0][0]
        assert "status = 'processing'" in sql

    @pytest.mark.asyncio
    async def test_failed_passes_error_parameter(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            await mark_scheduled_failed(3, "media_not_found")
        params = mock_conn.execute.call_args[0][1:]
        assert "media_not_found" in params

    @pytest.mark.asyncio
    async def test_failed_returns_false_for_completed(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_failed(5, "err") is False

    @pytest.mark.asyncio
    async def test_failed_returns_false_for_already_failed(self):
        from db.postgres import mark_scheduled_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await mark_scheduled_failed(5, "err") is False


# ==============================================================================
# B — RECOVERY RETRY LIMITS
# ==============================================================================


class TestRecoveryRetryLimits:
    """recover_stale_messages marks jobs as failed when attempts >= max."""

    def _make_row(self, msg_id, attempts):
        return {"id": msg_id, "attempts": attempts}

    @pytest.mark.asyncio
    async def test_attempts_exceeds_max_marks_failed(self):
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[self._make_row(10, 5)])
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await recover_stale_messages(max_attempts=5)
        assert result == []
        failed_sql = mock_conn.execute.call_args_list[0][0][0]
        assert "status = 'failed'" in failed_sql
        assert "max_recovery_attempts_exceeded" in failed_sql

    @pytest.mark.asyncio
    async def test_attempts_below_max_recovers(self):
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[self._make_row(20, 2)])
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await recover_stale_messages(max_attempts=5)
        assert result == [{"id": 20}]
        pending_sql = mock_conn.execute.call_args_list[0][0][0]
        assert "status = 'pending'" in pending_sql

    @pytest.mark.asyncio
    async def test_mixed_batch_one_fails_others_recover(self):
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                self._make_row(1, 5),
                self._make_row(2, 3),
                self._make_row(3, 4),
            ]
        )
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await recover_stale_messages(max_attempts=5)
        recovered_ids = [r["id"] for r in result]
        assert 1 not in recovered_ids
        assert 2 in recovered_ids
        assert 3 in recovered_ids
        assert mock_conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_boundary_attempts_eq_max_minus_1_recovers(self):
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[self._make_row(30, 4)])
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await recover_stale_messages(max_attempts=5)
        assert result == [{"id": 30}]

    @pytest.mark.asyncio
    async def test_default_max_attempts_is_5(self):
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[self._make_row(40, 5)])
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await recover_stale_messages()
        assert result == []
        failed_sql = mock_conn.execute.call_args_list[0][0][0]
        assert "max_recovery_attempts_exceeded" in failed_sql


# ==============================================================================
# C — CANCEL SEMANTICS
# ==============================================================================


class TestCancelSemantics:
    """cancel_scheduled_message only cancels pending jobs."""

    @pytest.mark.asyncio
    async def test_pending_returns_true(self):
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await cancel_scheduled_message(1) is True

    @pytest.mark.asyncio
    async def test_processing_returns_false(self):
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await cancel_scheduled_message(2) is False

    @pytest.mark.asyncio
    async def test_completed_returns_false(self):
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await cancel_scheduled_message(3) is False

    @pytest.mark.asyncio
    async def test_failed_returns_false(self):
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await cancel_scheduled_message(4) is False

    @pytest.mark.asyncio
    async def test_already_cancelled_returns_false(self):
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            assert await cancel_scheduled_message(5) is False


# ==============================================================================
# D — CANCEL RICH
# ==============================================================================


class TestCancelRich:
    """cancel_scheduled_message_rich returns detailed status info."""

    def _make_row(self, status):
        return {"status": status}

    @pytest.mark.asyncio
    async def test_pending_cancelled(self):
        from db.postgres import cancel_scheduled_message_rich

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=self._make_row("pending"))
        mock_conn.execute = AsyncMock()
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await cancel_scheduled_message_rich(1)
        assert result["cancelled"] is True
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_processing_returns_false_with_scheduler_reason(self):
        from db.postgres import cancel_scheduled_message_rich

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=self._make_row("processing"))
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await cancel_scheduled_message_rich(2)
        assert result["cancelled"] is False
        assert "scheduler" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_completed_returns_false_with_cannot_cancel(self):
        from db.postgres import cancel_scheduled_message_rich

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=self._make_row("completed"))
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await cancel_scheduled_message_rich(3)
        assert result["cancelled"] is False
        assert "cannot cancel" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_not_found(self):
        from db.postgres import cancel_scheduled_message_rich

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await cancel_scheduled_message_rich(999)
        assert result["cancelled"] is False
        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_already_cancelled_returns_false(self):
        from db.postgres import cancel_scheduled_message_rich

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=self._make_row("cancelled"))
        pool = _make_pool(mock_conn)
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await cancel_scheduled_message_rich(6)
        assert result["cancelled"] is False
        assert result["status"] == "cancelled"


# ==============================================================================
# E — CONFIG INTEGRATION
# ==============================================================================


class TestConfigIntegration:
    """Settings exposes scheduler config and scheduler_worker reads it."""

    def test_settings_has_scheduler_fields(self):
        from core.config import Settings

        s = Settings()
        assert hasattr(s, "scheduler_poll_interval")
        assert hasattr(s, "scheduler_batch_size")
        assert hasattr(s, "scheduler_recovery_timeout")
        assert hasattr(s, "scheduler_max_recovery_attempts")

    def test_scheduler_fields_are_positive_integers(self):
        from core.config import Settings

        s = Settings()
        for field in (
            "scheduler_poll_interval",
            "scheduler_batch_size",
            "scheduler_recovery_timeout",
            "scheduler_max_recovery_attempts",
        ):
            val = getattr(s, field)
            assert isinstance(val, int)
            assert val > 0, f"{field} must be positive, got {val}"

    @pytest.mark.asyncio
    async def test_scheduler_worker_reads_from_settings(self):
        from core.config import Settings

        s = Settings(
            scheduler_poll_interval=42,
            scheduler_batch_size=7,
            scheduler_recovery_timeout=120,
            scheduler_max_recovery_attempts=3,
        )
        assert s.scheduler_poll_interval == 42
        assert s.scheduler_batch_size == 7
        assert s.scheduler_recovery_timeout == 120
        assert s.scheduler_max_recovery_attempts == 3


# ==============================================================================
# F — HEALTH ENHANCEMENTS
# ==============================================================================


class TestHealthEnhancements:
    """_compute_lag returns correct structure and health endpoint includes lag."""

    @pytest.mark.asyncio
    async def test_compute_lag_returns_correct_structure(self):
        from chatbotv2.dashboard.routes.followups import _compute_lag

        now = datetime.now(UTC)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "oldest_pending": now - timedelta(seconds=120.5),
                "pending_count": 3,
            }
        )
        pool = _make_pool(mock_conn)
        with patch(
            "db.postgres.get_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            result = await _compute_lag(creator_id=1)
        assert "lag_seconds" in result
        assert "pending_count" in result
        assert "oldest_pending" in result
        assert result["pending_count"] == 3
        assert result["lag_seconds"] >= 120.0
        assert result["oldest_pending"] is not None

    @pytest.mark.asyncio
    async def test_compute_lag_returns_zeros_when_no_pending(self):
        from chatbotv2.dashboard.routes.followups import _compute_lag

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(mock_conn)
        with patch(
            "db.postgres.get_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            result = await _compute_lag(creator_id=1)
        assert result == {
            "oldest_pending": None,
            "lag_seconds": 0,
            "pending_count": 0,
        }

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_lag_key(self):
        from chatbotv2.dashboard.routes.followups import api_followups_health

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "total": 0,
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            }
        )
        pool = _make_pool(mock_conn)

        fake_lag = {"oldest_pending": None, "lag_seconds": 0, "pending_count": 0}

        with (
            patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool),
            patch(
                "chatbotv2.dashboard.routes.followups.get_scheduled_stats",
                new_callable=AsyncMock,
                return_value={
                    "total": 0,
                    "pending": 0,
                    "processing": 0,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                },
            ),
            patch(
                "chatbotv2.dashboard.routes.followups._compute_lag",
                new_callable=AsyncMock,
                return_value=fake_lag,
            ),
            patch(
                "chatbotv2.dashboard.routes.followups._get_creator_id",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "chatbotv2.dashboard.routes.followups._require_creator",
                new_callable=AsyncMock,
            ),
        ):
            # Build a fake request-like auth object
            auth = MagicMock()
            auth.credentials = "tok"
            resp = await api_followups_health(auth)
            body = resp.body
            import json

            data = json.loads(body)
            assert "lag" in data
            assert data["lag"]["lag_seconds"] == 0
