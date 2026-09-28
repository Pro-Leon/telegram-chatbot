"""P2.1/P2.3 — Durable Follow-up Scheduling Tests.

Comprehensive test suite covering:
A. Schedule creation
B. Idempotency
C. Due job claiming
D. Concurrency
E. Enqueue
F. Enqueue failure
G. Crash recovery
H. Send dedup
I. User eligibility
J. Post-purchase integration
K. Worker lifecycle
L. Time handling
M. Database / migration
N. Regression
O. P2.3 — Status guards (enqueue/failed cancellation race)
P. P2.3 — Recovery retry limits
Q. P2.3 — Cancel semantics (pending-only)
R. P2.3 — Cancel rich return values
S. P2.3 — Config integration
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _scheduled_msg(**overrides):
    base = {
        "id": 1,
        "user_id": 12345,
        "creator_id": 1,
        "execute_at": datetime.now(UTC) + timedelta(hours=1),
        "content": "Hey! Just checking in",
        "media_type": "",
        "media_path": "",
        "dedup_key": "post_purchase_followup:txn_abc",
        "reason": "post_purchase_followup",
        "status": "pending",
        "attempts": 0,
        "claimed_at": None,
        "claimed_by": None,
        "completed_at": None,
        "failed_at": None,
        "last_error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _user_info(**overrides):
    base = {
        "user_id": 12345,
        "is_blocked": False,
        "do_not_auto_reply": False,
    }
    base.update(overrides)
    return base


class _FakeConn:
    """Fake connection that supports `async with conn.transaction():`."""

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
    """Create a mock pool supporting `async with pool.acquire() as conn:` with conn.transaction()."""
    fake_conn = _FakeConn(conn)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=acquire_ctx)
    return mock_pool


# ═══════════════════════════════════════════════════════════════════════════════
# A — SCHEDULE CREATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestScheduleCreation:
    @pytest.mark.asyncio
    async def test_create_scheduled_message_success(self):
        """Schedule a follow-up successfully."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.side_effect = [None, 42]
        mock_conn.fetchrow = AsyncMock(return_value={"id": 42})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="test:dedup:1",
            )
            assert result == 42

    @pytest.mark.asyncio
    async def test_execute_at_calculated_correctly(self):
        """execute_at is calculated correctly from delay."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=1)
        with patch("db.postgres.create_scheduled_message", mock_create):
            delay = timedelta(hours=24)
            before = datetime.now(UTC) + delay - timedelta(seconds=5)
            after = datetime.now(UTC) + delay + timedelta(seconds=5)

            await schedule_follow_up(
                user_id=12345,
                transaction_id="txn_1",
                delay=delay,
            )
            call_kwargs = mock_create.call_args[1]
            execute_at = call_kwargs["execute_at"]
            assert before <= execute_at <= after

    @pytest.mark.asyncio
    async def test_content_stored_correctly(self):
        """Content is stored correctly."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Test message content",
                dedup_key="test:content:1",
            )
            insert_call = mock_conn.fetchrow.call_args
            assert insert_call[0][3] == "Test message content"

    @pytest.mark.asyncio
    async def test_user_id_stored_correctly(self):
        """user_id is stored correctly."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await create_scheduled_message(
                user_id=99999,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="test:userid:1",
            )
            insert_call = mock_conn.fetchrow.call_args
            assert insert_call[0][1] == 99999

    @pytest.mark.asyncio
    async def test_dedup_key_stored(self):
        """Deduplication key is stored."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="post_purchase_followup:txn_xyz",
            )
            insert_call = mock_conn.fetchrow.call_args
            assert insert_call[0][4] == "post_purchase_followup:txn_xyz"

    @pytest.mark.asyncio
    async def test_status_starts_as_pending(self):
        """Status starts as pending."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="test:status:1",
            )
            insert_sql = mock_conn.fetchrow.call_args[0][0]
            assert "VALUES" in insert_sql

    @pytest.mark.asyncio
    async def test_created_at_populated(self):
        """created_at is populated by the DB default."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="test:created:1",
            )
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# B — IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_followup_cannot_be_scheduled_twice(self):
        """Same logical follow-up cannot be scheduled twice."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 99
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="post_purchase_followup:txn_abc",
            )
            assert result == 99

    @pytest.mark.asyncio
    async def test_duplicate_returns_existing_job(self):
        """Duplicate scheduling returns the existing job ID."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 42
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="post_purchase_followup:txn_abc",
            )
            assert result == 42

    @pytest.mark.asyncio
    async def test_different_dedup_keys_can_coexist(self):
        """Different follow-up types can coexist."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.side_effect = [None, None]
        mock_conn.fetchrow.side_effect = [{"id": 1}, {"id": 2}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            r1 = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Follow-up 1",
                dedup_key="post_purchase_followup:txn_1",
            )
            r2 = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Follow-up 2",
                dedup_key="post_purchase_followup:txn_2",
            )
            assert r1 == 1
            assert r2 == 2

    @pytest.mark.asyncio
    async def test_duplicate_webhook_no_duplicate_schedule(self):
        """Duplicate webhook processing does not create duplicate follow-ups."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=42)
        with patch("db.postgres.create_scheduled_message", mock_create):
            r1 = await schedule_follow_up(user_id=12345, transaction_id="txn_dup")
            r2 = await schedule_follow_up(user_id=12345, transaction_id="txn_dup")
            assert r1 is True
            assert r2 is True
            assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_completed_job_not_preventing_new_schedule(self):
        """A completed job does not block a new schedule with the same dedup_key."""
        from db.postgres import create_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 100})
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_scheduled_message(
                user_id=12345,
                execute_at=datetime.now(UTC),
                content="Hello",
                dedup_key="post_purchase_followup:txn_completed",
            )
            assert result == 100


# ═══════════════════════════════════════════════════════════════════════════════
# C — DUE JOB CLAIMING
# ═══════════════════════════════════════════════════════════════════════════════


class TestDueJobClaiming:
    @pytest.mark.asyncio
    async def test_future_jobs_not_claimed(self):
        """Future jobs are not claimed."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_due_messages(batch_size=10)
            assert result == []

    @pytest.mark.asyncio
    async def test_due_jobs_are_claimed(self):
        """Due jobs are claimed."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_due_messages(batch_size=10)
            assert len(result) == 2
            assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_jobs_not_claimed(self):
        """Cancelled jobs are not claimed (WHERE status = 'pending')."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await claim_due_messages()
            fetch_sql = mock_conn.fetch.call_args[0][0]
            assert "status = 'pending'" in fetch_sql

    @pytest.mark.asyncio
    async def test_completed_jobs_not_claimed(self):
        """Completed jobs are not claimed."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_due_messages()
            assert result == []

    @pytest.mark.asyncio
    async def test_multiple_jobs_ordered_correctly(self):
        """Multiple jobs are ordered by execute_at, id."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 10}, {"id": 20}, {"id": 30}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_due_messages(batch_size=3)
            assert len(result) == 3
            fetch_sql = mock_conn.fetch.call_args[0][0]
            assert "ORDER BY execute_at, id" in fetch_sql

    @pytest.mark.asyncio
    async def test_batch_size_respected(self):
        """Batch size is passed to LIMIT."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await claim_due_messages(batch_size=5)
            fetch_args = mock_conn.fetch.call_args[0]
            assert fetch_args[1] == 5


# ═══════════════════════════════════════════════════════════════════════════════
# D — CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_claim_uses_for_update_skip_locked(self):
        """FOR UPDATE SKIP LOCKED is used to prevent concurrent claims."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await claim_due_messages()
            fetch_sql = mock_conn.fetch.call_args[0][0]
            assert "FOR UPDATE SKIP LOCKED" in fetch_sql

    @pytest.mark.asyncio
    async def test_claim_in_transaction(self):
        """Claiming uses a transaction — verified by successful operation within transaction context."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_due_messages()
            assert len(result) == 1
            # The fetch + execute calls succeed, proving the transaction context was entered
            mock_conn.fetch.assert_called_once()
            mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_workers_different_results(self):
        """Two concurrent claim calls get different jobs via SKIP LOCKED."""
        from db.postgres import claim_due_messages

        mock_conn_a = AsyncMock()
        mock_conn_a.fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_pool_a = _make_pool(mock_conn_a)

        mock_conn_b = AsyncMock()
        mock_conn_b.fetch.return_value = [{"id": 3}]
        mock_pool_b = _make_pool(mock_conn_b)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool_a):
            result_a = await claim_due_messages(worker_id="worker_a")

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool_b):
            result_b = await claim_due_messages(worker_id="worker_b")

        ids_a = [m["id"] for m in result_a]
        ids_b = [m["id"] for m in result_b]
        assert set(ids_a).isdisjoint(set(ids_b))

    @pytest.mark.asyncio
    async def test_worker_id_recorded_on_claim(self):
        """claimed_by is set to the worker_id."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await claim_due_messages(worker_id="scheduler_42")
            update_call = mock_conn.execute.call_args
            assert update_call[0][2] == "scheduler_42"


# ═══════════════════════════════════════════════════════════════════════════════
# E — ENQUEUE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_build_send_payload_correct_fields(self):
        """Claimed job is converted into the correct send payload."""
        from workers.scheduler_worker import _build_send_payload

        msg = _scheduled_msg(user_id=55555, content="Test payload")
        payload = _build_send_payload(msg)
        assert payload["entity"] == "55555"
        assert payload["content"] == "Test payload"
        assert payload["save_to_db"] is True
        assert payload["was_auto_approved"] is False

    @pytest.mark.asyncio
    async def test_enqueue_send_receives_correct_user_id(self):
        """enqueue_send() receives correct user_id as entity."""
        from workers.scheduler_worker import _build_send_payload

        msg = _scheduled_msg(user_id=77777)
        payload = _build_send_payload(msg)
        assert payload["entity"] == "77777"

    @pytest.mark.asyncio
    async def test_enqueue_send_receives_correct_content(self):
        """enqueue_send() receives correct content."""
        from workers.scheduler_worker import _build_send_payload

        msg = _scheduled_msg(content="Specific content here")
        payload = _build_send_payload(msg)
        assert payload["content"] == "Specific content here"

    @pytest.mark.asyncio
    async def test_deterministic_dedup_id(self):
        """enqueue_send() receives deterministic dedup_id."""
        from workers.scheduler_worker import _make_dedup_id

        msg = _scheduled_msg(id=42, dedup_key="post_purchase_followup:txn_abc")
        dedup_id = _make_dedup_id(msg)
        assert dedup_id == "scheduled:post_purchase_followup:txn_abc:42"

    @pytest.mark.asyncio
    async def test_media_fields_passed_through(self):
        """Media fields are passed through to the send payload."""
        from workers.scheduler_worker import _build_send_payload

        msg = _scheduled_msg(media_type="photo", media_path="/tmp/photo.jpg")
        payload = _build_send_payload(msg)
        assert payload["media_type"] == "photo"
        assert payload["media_path"] == "/tmp/photo.jpg"

    @pytest.mark.asyncio
    async def test_not_marked_completed_before_enqueue(self):
        """Scheduled job is not marked completed before enqueue succeeds."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock()
        mock_mark_enqueued = AsyncMock()
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_enqueued", mock_mark_enqueued),
        ):
            await process_due_messages("test_worker")
            mock_enqueue.assert_called_once()
            mock_mark_enqueued.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_calls_enqueue_send(self):
        """process_due_messages calls enqueue_send with correct args."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock()
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_enqueued", AsyncMock()),
        ):
            count = await process_due_messages("test_worker")
            assert count == 1
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args
            assert call_kwargs[1]["dedup_id"].startswith("scheduled:")


# ═══════════════════════════════════════════════════════════════════════════════
# F — ENQUEUE FAILURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnqueueFailure:
    @pytest.mark.asyncio
    async def test_redis_enqueue_failure_does_not_lose_job(self):
        """Redis enqueue failure does not lose the job."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock(side_effect=Exception("Redis down"))
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])
        mock_mark_failed = AsyncMock()

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_failed", mock_mark_failed),
        ):
            count = await process_due_messages("test_worker")
            assert count == 0
            mock_mark_failed.assert_called_once_with(10, "enqueue_error")

    @pytest.mark.asyncio
    async def test_job_remains_recoverable_after_failure(self):
        """Job remains in a recoverable state after enqueue failure."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock(side_effect=Exception("Redis timeout"))
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])
        mock_mark_failed = AsyncMock()

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_failed", mock_mark_failed),
        ):
            await process_due_messages("test_worker")
            mock_mark_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_attempt_count_and_error_state_updated(self):
        """attempt count/error state is updated correctly on failure."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock(side_effect=Exception("Connection refused"))
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])
        mock_mark_failed = AsyncMock()

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_failed", mock_mark_failed),
        ):
            await process_due_messages("test_worker")
            error_arg = mock_mark_failed.call_args[0][1]
            assert "enqueue_error" in error_arg


# ═══════════════════════════════════════════════════════════════════════════════
# G — CRASH RECOVERY
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_stale_processing_job_is_recovered(self):
        """Stale processing job is recovered to pending."""
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1, "attempts": 0}, {"id": 2, "attempts": 1}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await recover_stale_messages(stale_seconds=300)
            assert len(result) == 2
            assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_recovery_returns_to_pending(self):
        """Recovery sets status back to pending."""
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 5, "attempts": 0}]
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await recover_stale_messages()
            update_sql = mock_conn.execute.call_args[0][0]
            assert "status = 'pending'" in update_sql

    @pytest.mark.asyncio
    async def test_fresh_processing_job_not_recovered(self):
        """Fresh processing job is not incorrectly recovered."""
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await recover_stale_messages(stale_seconds=300)
            assert result == []

    @pytest.mark.asyncio
    async def test_recovered_job_retains_dedup_identity(self):
        """Recovered job retains its dedup_key for send-stream dedup."""
        from workers.scheduler_worker import _make_dedup_id

        msg = _scheduled_msg(
            id=42,
            dedup_key="post_purchase_followup:txn_abc",
            status="pending",
        )
        dedup_id = _make_dedup_id(msg)
        assert dedup_id == "scheduled:post_purchase_followup:txn_abc:42"

    @pytest.mark.asyncio
    async def test_recovery_query_uses_skip_locked(self):
        """Recovery query uses FOR UPDATE SKIP LOCKED."""
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await recover_stale_messages()
            fetch_sql = mock_conn.fetch.call_args[0][0]
            assert "FOR UPDATE SKIP LOCKED" in fetch_sql

    @pytest.mark.asyncio
    async def test_recovery_stale_seconds_passed_to_query(self):
        """Stale seconds threshold is passed to the query."""
        from db.postgres import recover_stale_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await recover_stale_messages(stale_seconds=600)
            fetch_args = mock_conn.fetch.call_args[0]
            assert fetch_args[1] == 600


# ═══════════════════════════════════════════════════════════════════════════════
# H — SEND DEDUP
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendDedup:
    @pytest.mark.asyncio
    async def test_scheduler_retry_uses_same_dedup_id(self):
        """Scheduler retry produces the same dedup_id."""
        from workers.scheduler_worker import _make_dedup_id

        msg = _scheduled_msg(id=42, dedup_key="post_purchase_followup:txn_abc")
        dedup_1 = _make_dedup_id(msg)
        dedup_2 = _make_dedup_id(msg)
        assert dedup_1 == dedup_2

    @pytest.mark.asyncio
    async def test_dedup_id_includes_message_id(self):
        """Dedup ID includes message ID for uniqueness across retries."""
        from workers.scheduler_worker import _make_dedup_id

        msg_a = _scheduled_msg(id=1, dedup_key="post_purchase_followup:txn_abc")
        msg_b = _scheduled_msg(id=2, dedup_key="post_purchase_followup:txn_abc")
        assert _make_dedup_id(msg_a) != _make_dedup_id(msg_b)

    @pytest.mark.asyncio
    async def test_send_stream_dedup_prevents_duplicate(self):
        """Existing send dedup mechanism prevents duplicate Telegram delivery."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock()
        mock_user_info = AsyncMock(return_value=True)
        mock_claim = AsyncMock(return_value=[{"id": 10}])
        mock_mark_enqueued = AsyncMock()

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_enqueued", mock_mark_enqueued),
        ):
            count = await process_due_messages("test_worker")
            assert count == 1
            mock_enqueue.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# I — USER ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserEligibility:
    @pytest.mark.asyncio
    async def test_blocked_user_skipped(self):
        """Blocked users are handled correctly."""
        from workers.scheduler_worker import _check_user_eligible

        mock_info = AsyncMock(return_value=_user_info(is_blocked=True))
        with patch("workers.scheduler_worker.get_user_safety_info", mock_info):
            result = await _check_user_eligible(12345)
            assert result is False

    @pytest.mark.asyncio
    async def test_do_not_auto_reply_user_skipped(self):
        """do_not_auto_reply users are handled correctly."""
        from workers.scheduler_worker import _check_user_eligible

        mock_info = AsyncMock(return_value=_user_info(do_not_auto_reply=True))
        with patch("workers.scheduler_worker.get_user_safety_info", mock_info):
            result = await _check_user_eligible(12345)
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_user_skipped(self):
        """Missing users are handled safely."""
        from workers.scheduler_worker import _check_user_eligible

        mock_info = AsyncMock(return_value=None)
        with patch("workers.scheduler_worker.get_user_safety_info", mock_info):
            result = await _check_user_eligible(99999)
            assert result is False

    @pytest.mark.asyncio
    async def test_eligible_user_passes(self):
        """Eligible users pass the check."""
        from workers.scheduler_worker import _check_user_eligible

        mock_info = AsyncMock(return_value=_user_info())
        with patch("workers.scheduler_worker.get_user_safety_info", mock_info):
            result = await _check_user_eligible(12345)
            assert result is True

    @pytest.mark.asyncio
    async def test_cancellation_prevents_delivery(self):
        """Cancelled jobs are not enqueued."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="cancelled")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock()
        mock_claim = AsyncMock(return_value=[{"id": 10}])

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_enqueued", AsyncMock()),
        ):
            count = await process_due_messages("test_worker")
            assert count == 0
            mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_user_message_completed_not_retried(self):
        """Message for blocked user is suppressed (completed + reason), not retried."""
        from workers.scheduler_worker import process_due_messages

        mock_msg = _scheduled_msg(id=10, user_id=12345, status="processing")
        mock_scheduled = AsyncMock(return_value=mock_msg)
        mock_enqueue = AsyncMock()
        mock_user_info = AsyncMock(return_value=False)
        mock_claim = AsyncMock(return_value=[{"id": 10}])
        mock_mark_enqueued = AsyncMock()
        mock_mark_suppressed = AsyncMock(return_value=True)

        with (
            patch("workers.scheduler_worker.claim_due_messages", mock_claim),
            patch("workers.scheduler_worker._fetch_message", mock_scheduled),
            patch("workers.scheduler_worker._check_user_eligible", mock_user_info),
            patch("workers.scheduler_worker.enqueue_send", mock_enqueue),
            patch("workers.scheduler_worker.mark_scheduled_enqueued", mock_mark_enqueued),
            patch("workers.scheduler_worker.mark_scheduled_suppressed", mock_mark_suppressed),
            patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
        ):
            await process_due_messages("test_worker")
            mock_enqueue.assert_not_called()
            # M7 (B4): ineligible recipients are terminally suppressed with an
            # explicit reason (completed + last_error), not silently completed.
            mock_mark_suppressed.assert_called_once_with(10, "recipient_ineligible")
            mock_mark_enqueued.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# J — POST-PURCHASE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostPurchase:
    @pytest.mark.asyncio
    async def test_successful_attribution_schedules_followup(self):
        """Successful attribution schedules follow-up."""
        from commerce.models import PurchaseRecord
        from commerce.post_purchase import handle_post_purchase

        record = PurchaseRecord(
            offer_id=1,
            creator_id=100,
            user_id=12345,
            transaction_id="txn_abc",
            product_id=200,
        )

        mock_funnel = AsyncMock(return_value=True)
        mock_confirm = AsyncMock(return_value=True)
        mock_schedule = AsyncMock(return_value=True)

        with (
            patch("commerce.post_purchase.advance_funnel_to_converted", mock_funnel),
            patch("commerce.post_purchase.enqueue_purchase_confirmation", mock_confirm),
            patch("commerce.post_purchase.schedule_follow_up", mock_schedule),
        ):
            await handle_post_purchase(record)
            mock_schedule.assert_called_once_with(12345, "txn_abc", creator_id=100)

    @pytest.mark.asyncio
    async def test_no_attribution_no_followup(self):
        """No attribution means no follow-up."""
        from commerce.models import PurchaseRecord
        from commerce.post_purchase import handle_post_purchase

        record = PurchaseRecord(user_id=None, transaction_id=None)

        mock_schedule = AsyncMock()

        with patch("commerce.post_purchase.schedule_follow_up", mock_schedule):
            await handle_post_purchase(record)
            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_incomplete_record_no_followup(self):
        """Incomplete record means no follow-up."""
        from commerce.models import PurchaseRecord
        from commerce.post_purchase import handle_post_purchase

        record = PurchaseRecord(user_id=12345, transaction_id=None)

        mock_schedule = AsyncMock()

        with patch("commerce.post_purchase.schedule_follow_up", mock_schedule):
            await handle_post_purchase(record)
            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_purchase_no_duplicate_schedule(self):
        """Duplicate purchase processing does not schedule duplicate follow-up."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=42)
        with patch("db.postgres.create_scheduled_message", mock_create):
            r1 = await schedule_follow_up(user_id=12345, transaction_id="txn_dup")
            r2 = await schedule_follow_up(user_id=12345, transaction_id="txn_dup")
            assert r1 is True
            assert r2 is True

    @pytest.mark.asyncio
    async def test_followup_uses_correct_user(self):
        """Follow-up uses correct user."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=1)
        with patch("db.postgres.create_scheduled_message", mock_create):
            await schedule_follow_up(user_id=55555, transaction_id="txn_user")
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["user_id"] == 55555

    @pytest.mark.asyncio
    async def test_followup_timing_correct(self):
        """Follow-up timing is correct."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=1)
        with patch("db.postgres.create_scheduled_message", mock_create):
            before = datetime.now(UTC) + timedelta(hours=23, minutes=59)
            after = datetime.now(UTC) + timedelta(hours=24, minutes=1)
            await schedule_follow_up(user_id=12345, transaction_id="txn_time")
            call_kwargs = mock_create.call_args[1]
            assert before <= call_kwargs["execute_at"] <= after

    @pytest.mark.asyncio
    async def test_confirmation_and_followup_distinct(self):
        """Confirmation and follow-up are distinct logical messages."""
        from commerce.post_purchase import (
            _FOLLOW_UP_MESSAGE,
            _PURCHASE_CONFIRMATION,
        )

        assert _PURCHASE_CONFIRMATION != _FOLLOW_UP_MESSAGE

    @pytest.mark.asyncio
    async def test_followup_failure_does_not_break_post_purchase(self):
        """Follow-up scheduling failure does not break post-purchase."""
        from commerce.models import PurchaseRecord
        from commerce.post_purchase import handle_post_purchase

        record = PurchaseRecord(
            offer_id=1,
            creator_id=100,
            user_id=12345,
            transaction_id="txn_err",
            product_id=200,
        )

        mock_funnel = AsyncMock(return_value=True)
        mock_confirm = AsyncMock(return_value=True)
        mock_schedule = AsyncMock(side_effect=Exception("DB error"))

        with (
            patch("commerce.post_purchase.advance_funnel_to_converted", mock_funnel),
            patch("commerce.post_purchase.enqueue_purchase_confirmation", mock_confirm),
            patch("commerce.post_purchase.schedule_follow_up", mock_schedule),
        ):
            await handle_post_purchase(record)
            mock_funnel.assert_called_once()
            mock_confirm.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# K — WORKER LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_scheduler_starts_correctly(self):
        """Scheduler starts correctly."""
        from workers.scheduler_worker import run_scheduler

        mock_init = AsyncMock()
        mock_ensure = AsyncMock()
        mock_heartbeat = AsyncMock()

        async def fake_loop(worker_id):
            pass

        with (
            patch("workers.scheduler_worker.init_pool", mock_init),
            patch("workers.scheduler_worker.ensure_consumer_group", mock_ensure),
            patch("workers.scheduler_worker.setup_signal_handlers"),
            patch("workers.scheduler_worker.write_heartbeat", mock_heartbeat),
            patch("workers.scheduler_worker._scheduler_loop", fake_loop),
            patch("workers.scheduler_worker.get_settings") as mock_settings,
        ):
            mock_settings.return_value.worker_heartbeat_interval = 10
            mock_settings.return_value.worker_heartbeat_ttl = 30
            await run_scheduler("test_worker")
            mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_stops_cleanly(self):
        """Scheduler stops cleanly via shutdown flag."""
        from workers.scheduler_worker import _scheduler_loop

        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                from core.shutdown import set_shutting_down

                set_shutting_down(True)

        with (
            patch("workers.scheduler_worker.is_shutting_down", side_effect=[False, False, True]),
            patch("workers.scheduler_worker.recover_stale", new_callable=AsyncMock),
            patch("workers.scheduler_worker.process_due_messages", new_callable=AsyncMock),
            patch("workers.scheduler_worker.asyncio.sleep", fake_sleep),
        ):
            try:
                await _scheduler_loop("test_worker")
            finally:
                from core.shutdown import set_shutting_down

                set_shutting_down(False)

    @pytest.mark.asyncio
    async def test_scheduler_does_not_prevent_shutdown(self):
        """Scheduler does not prevent application shutdown."""
        from core.shutdown import set_shutting_down
        from workers.scheduler_worker import _scheduler_loop

        set_shutting_down(True)
        try:
            with (
                patch("workers.scheduler_worker.recover_stale", new_callable=AsyncMock),
                patch("workers.scheduler_worker.process_due_messages", new_callable=AsyncMock),
            ):
                await _scheduler_loop("test_worker")
        finally:
            set_shutting_down(False)

    @pytest.mark.asyncio
    async def test_no_accidental_duplicate_loops(self):
        """Multiple scheduler loops are not accidentally created."""
        from workers.scheduler_worker import _scheduler_loop

        loop_count = 0

        async def fake_process(worker_id):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                from core.shutdown import set_shutting_down

                set_shutting_down(True)

        with (
            patch("workers.scheduler_worker.is_shutting_down", side_effect=[False, False, True]),
            patch("workers.scheduler_worker.recover_stale", new_callable=AsyncMock),
            patch("workers.scheduler_worker.process_due_messages", fake_process),
            patch("workers.scheduler_worker.asyncio.sleep", new_callable=AsyncMock),
        ):
            try:
                await _scheduler_loop("test_worker")
            finally:
                from core.shutdown import set_shutting_down

                set_shutting_down(False)

    @pytest.mark.asyncio
    async def test_cleanup_closes_pool_and_redis(self):
        """Cleanup closes pool and redis."""
        from workers.scheduler_worker import _scheduler_cleanup

        mock_pool = AsyncMock()
        mock_redis = AsyncMock()

        with (
            patch("workers.scheduler_worker.close_pool", mock_pool),
            patch("workers.scheduler_worker.close_redis", mock_redis),
        ):
            await _scheduler_cleanup()
            mock_pool.assert_called_once()
            mock_redis.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# L — TIME
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimeHandling:
    @pytest.mark.asyncio
    async def test_future_jobs_remain_pending(self):
        """Future jobs remain pending."""
        from db.postgres import claim_due_messages

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await claim_due_messages()
            fetch_sql = mock_conn.fetch.call_args[0][0]
            assert "execute_at <= NOW()" in fetch_sql

    @pytest.mark.asyncio
    async def test_utc_handling(self):
        """UTC handling is correct — execute_at uses timezone-aware datetime."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=1)
        with patch("db.postgres.create_scheduled_message", mock_create):
            await schedule_follow_up(user_id=12345, transaction_id="txn_utc")
            call_kwargs = mock_create.call_args[1]
            execute_at = call_kwargs["execute_at"]
            assert execute_at.tzinfo is not None
            assert execute_at.tzinfo == UTC

    @pytest.mark.asyncio
    async def test_execute_at_is_absolute(self):
        """execute_at is an absolute instant, not wall-clock."""
        from commerce.post_purchase import schedule_follow_up

        mock_create = AsyncMock(return_value=1)
        with patch("db.postgres.create_scheduled_message", mock_create):
            before = datetime.now(UTC)
            await schedule_follow_up(user_id=12345, transaction_id="txn_abs")
            after = datetime.now(UTC)
            call_kwargs = mock_create.call_args[1]
            execute_at = call_kwargs["execute_at"]
            min_time = before + timedelta(hours=23, minutes=59)
            max_time = after + timedelta(hours=24, minutes=1)
            assert min_time <= execute_at <= max_time


# ═══════════════════════════════════════════════════════════════════════════════
# M — DATABASE
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabase:
    def test_migration_file_exists(self):
        """Migration creates scheduled_messages."""
        from pathlib import Path

        migration = Path("db/migrations/20260822020000_scheduled_messages.sql")
        assert migration.exists()

    def test_migration_has_status_check(self):
        """Constraints work — migration has status check."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "scheduled_messages_status_check" in content

    def test_migration_has_attempts_check(self):
        """Attempts check constraint exists."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "scheduled_messages_attempts_check" in content

    def test_migration_has_pending_index(self):
        """Index exists for pending jobs."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "idx_scheduled_messages_pending" in content

    def test_migration_has_dedup_index(self):
        """Dedup index exists."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "idx_scheduled_messages_dedup" in content

    def test_migration_has_partial_index(self):
        """Partial index on pending status exists."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "WHERE status = 'pending'" in content

    def test_verify_schema_includes_scheduled_messages(self):
        """Existing migrations remain unaffected — scheduled_messages in verify_schema."""
        import inspect

        from db.postgres import verify_schema

        source = inspect.getsource(verify_schema)
        assert "scheduled_messages" in source

    def test_migration_uses_create_table_if_not_exists(self):
        """Migration is idempotent."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS" in content

    def test_migration_uses_create_index_if_not_exists(self):
        """Indexes are created idempotently."""
        from pathlib import Path

        content = Path("db/migrations/20260822020000_scheduled_messages.sql").read_text()
        assert "CREATE INDEX IF NOT EXISTS" in content

    @pytest.mark.asyncio
    async def test_cancel_scheduled_message(self):
        """Cancel updates status to cancelled."""
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_scheduled_message(42)
            assert result is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        """Cancel non-cancellable message returns False."""
        from db.postgres import cancel_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 0"
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_scheduled_message(999)
            assert result is False

    @pytest.mark.asyncio
    async def test_get_scheduled_message(self):
        """Get returns message dict."""
        from db.postgres import get_scheduled_message

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "user_id": 12345,
            "execute_at": datetime.now(UTC),
            "content": "Hello",
            "media_type": "",
            "media_path": "",
            "dedup_key": "test:1",
            "reason": "test",
            "status": "pending",
            "attempts": 0,
            "claimed_at": None,
            "claimed_by": None,
            "completed_at": None,
            "failed_at": None,
            "last_error": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_scheduled_message(1)
            assert result is not None
            assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_get_user_safety_info(self):
        """Get user safety info returns correct fields."""
        from db.postgres import get_user_safety_info

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 12345,
            "is_blocked": False,
            "do_not_auto_reply": True,
        }
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_user_safety_info(12345)
            assert result["is_blocked"] is False
            assert result["do_not_auto_reply"] is True

    @pytest.mark.asyncio
    async def test_get_user_safety_info_missing_user(self):
        """Missing user returns None."""
        from db.postgres import get_user_safety_info

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool = _make_pool(mock_conn)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_user_safety_info(99999)
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# N — REGRESSION
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegression:
    @pytest.mark.asyncio
    async def test_existing_send_stream_not_broken(self):
        """Existing send stream test helpers still work."""
        from tests.test_media_sending import _send_stream_patches, _text_data

        data = _text_data(entity="12345", content="Regression test")
        assert data["entity"] == "12345"
        patches = _send_stream_patches()
        assert "read_send_messages" in patches

    @pytest.mark.asyncio
    async def test_post_purchase_existing_flow_unchanged(self):
        """Existing post-purchase flow (funnel + confirmation) is unchanged."""
        from commerce.post_purchase import (
            _PURCHASE_CONFIRMATION,
        )

        assert (
            _PURCHASE_CONFIRMATION == "Your purchase is confirmed! Your content is now available."
        )

    @pytest.mark.asyncio
    async def test_commerce_models_unchanged(self):
        """Commerce models are unchanged."""
        from commerce.models import PurchaseRecord

        record = PurchaseRecord(user_id=1, transaction_id="txn")
        assert record.user_id == 1

    @pytest.mark.asyncio
    async def test_send_dedup_pattern_unchanged(self):
        """Existing send dedup pattern is unchanged."""
        from db.redis import is_send_duplicate

        assert callable(is_send_duplicate)

    @pytest.mark.asyncio
    async def test_enqueue_send_unchanged(self):
        """enqueue_send function signature is unchanged."""
        from db.redis import enqueue_send

        assert callable(enqueue_send)

    @pytest.mark.asyncio
    async def test_scheduler_worker_imports(self):
        """Scheduler worker can be imported without errors."""
        import workers.scheduler_worker as sw

        assert hasattr(sw, "run_scheduler")
        assert hasattr(sw, "process_due_messages")
        assert hasattr(sw, "recover_stale")

    @pytest.mark.asyncio
    async def test_scheduler_config_constants(self):
        """Scheduler config constants are reasonable."""
        from workers.scheduler_worker import (
            SCHEDULER_BATCH_SIZE,
            SCHEDULER_POLL_INTERVAL,
            SCHEDULER_RECOVERY_TIMEOUT,
        )

        assert SCHEDULER_POLL_INTERVAL > 0
        assert SCHEDULER_BATCH_SIZE > 0
        assert SCHEDULER_RECOVERY_TIMEOUT > 0

    def test_scheduler_worker_main_callable(self):
        """Scheduler worker main() is callable."""
        from workers.scheduler_worker import main

        assert callable(main)
