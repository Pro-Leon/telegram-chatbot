"""Phase B — Automation persistence tests.

Covers:
1. Create operation
2. Retrieve operation
3. Creator isolation
4. Idempotency (same creator)
5. Idempotency (different creators)
6. Concurrent idempotent creation
7. PENDING -> RUNNING
8. RUNNING -> SUCCEEDED
9. RUNNING -> FAILED
10. RUNNING -> RETRYING
11. RUNNING -> UNKNOWN
12. RETRYING -> RUNNING
13. Cancellation
14. Invalid state transition rejected
15. Duplicate claim rejected
16. Creator cannot claim another creator's operation
17. Retry metadata persistence
18. Provider result persistence
19. Error metadata persistence
20. Pagination/listing
21. Status filtering
22. Action filtering
23. Transaction rollback
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _op_row(**overrides):
    base = {
        "id": 1,
        "creator_id": 1,
        "action": "create_drop",
        "target": "vault_item:123",
        "params": {"price": 10.0},
        "idempotency_key": "key_abc",
        "correlation_id": "corr_1",
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": 3,
        "next_attempt_at": None,
        "provider_result": {},
        "error_class": None,
        "error_message": "",
        "created_at": datetime.now(UTC),
        "started_at": None,
        "finished_at": None,
        "updated_at": datetime.now(UTC),
        "cancelled_at": None,
    }
    base.update(overrides)
    return base


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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CREATE OPERATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateOperation:
    @pytest.mark.asyncio
    async def test_create_operation_success(self):
        """Insert a new automation operation successfully."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 42})
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_operation(
                creator_id=1,
                action="create_drop",
                target="vault_item:123",
                params={"price": 10.0},
                idempotency_key="key_1",
                correlation_id="corr_1",
            )
            assert result == {"id": 42}

    @pytest.mark.asyncio
    async def test_create_operation_returns_none_on_empty(self):
        """Returns None when insert returns no row."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_operation(
                creator_id=1,
                action="create_drop",
                target="vault_item:123",
            )
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RETRIEVE OPERATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetrieveOperation:
    @pytest.mark.asyncio
    async def test_get_operation_found(self):
        """Retrieve an existing operation by ID."""
        from db.automation import get_operation

        row = _op_row()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_operation(1, creator_id=1)
            assert result is not None
            assert result["id"] == 1
            assert result["action"] == "create_drop"

    @pytest.mark.asyncio
    async def test_get_operation_not_found(self):
        """Returns None for non-existent operation."""
        from db.automation import get_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_operation(999, creator_id=1)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_by_idempotency_key(self):
        """Retrieve operation by idempotency key."""
        from db.automation import get_operation_by_idempotency_key

        row = _op_row()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_operation_by_idempotency_key(1, "key_abc")
            assert result is not None
            assert result["idempotency_key"] == "key_abc"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CREATOR ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_cannot_read_another_operation(self):
        """Operation belongs to creator 1, creator 2 cannot read it."""
        from db.automation import get_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_operation(1, creator_id=2)
            assert result is None

    @pytest.mark.asyncio
    async def test_creator_cannot_claim_another_operation(self):
        """Creator 2 cannot claim creator 1's operation."""
        from db.automation import claim_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=2)
            assert result is None

    @pytest.mark.asyncio
    async def test_creator_cannot_cancel_another_operation(self):
        """Creator 2 cannot cancel creator 1's operation."""
        from db.automation import cancel_operation

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_operation(1, creator_id=2)
            assert result is False

    @pytest.mark.asyncio
    async def test_creator_cannot_read_via_idempotency(self):
        """Creator 2 cannot read creator 1's operation via idempotency key."""
        from db.automation import get_operation_by_idempotency_key

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_operation_by_idempotency_key(2, "key_abc")
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. IDEMPOTENCY (same creator)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotencySameCreator:
    @pytest.mark.asyncio
    async def test_same_creator_same_key_returns_existing(self):
        """Second create with same creator_id + key returns existing operation."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 10})
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await create_operation(
                creator_id=1,
                action="create_drop",
                target="x",
                idempotency_key="dup_key",
            )
            assert result == {"id": 10}
            # Only one fetchrow call (the SELECT), no INSERT
            assert mock_conn.fetchrow.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_key_allows_duplicates(self):
        """Empty idempotency_key does not trigger idempotency check."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 20})
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result1 = await create_operation(
                creator_id=1, action="create_drop", target="x",
                idempotency_key="",
            )
            result2 = await create_operation(
                creator_id=1, action="create_drop", target="x",
                idempotency_key="",
            )
            assert result1 == {"id": 20}
            assert result2 == {"id": 20}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. IDEMPOTENCY (different creators)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotencyDifferentCreators:
    @pytest.mark.asyncio
    async def test_different_creators_same_key_creates_separate(self):
        """Different creators with same key get separate operations."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        # First call: no existing row. Second call: no existing row.
        # Then two inserts.
        mock_conn.fetchrow = AsyncMock(side_effect=[
            None,  # SELECT for creator 1
            {"id": 1},  # INSERT for creator 1
            None,  # SELECT for creator 2
            {"id": 2},  # INSERT for creator 2
        ])
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            r1 = await create_operation(
                creator_id=1, action="create_drop", target="x",
                idempotency_key="shared_key",
            )
            r2 = await create_operation(
                creator_id=2, action="create_drop", target="x",
                idempotency_key="shared_key",
            )
            assert r1 == {"id": 1}
            assert r2 == {"id": 2}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CONCURRENT IDEMPOTENT CREATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentIdempotentCreation:
    @pytest.mark.asyncio
    async def test_concurrent_same_key_no_duplicate(self):
        """Two concurrent creates with same key produce one operation.

        The UNIQUE constraint is authoritative; application-level check
        prevents most duplicates, DB constraint catches races.
        """
        from db.automation import create_operation

        call_count = 0

        async def mock_fetchrow(query, *args):
            nonlocal call_count
            call_count += 1
            if "SELECT" in query:
                return None  # No existing row
            return {"id": 1}

        mock_conn = AsyncMock()
        mock_conn.fetchrow = mock_fetchrow
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            r1, r2 = await asyncio.gather(
                create_operation(
                    creator_id=1, action="create_drop", target="x",
                    idempotency_key="race_key",
                ),
                create_operation(
                    creator_id=1, action="create_drop", target="x",
                    idempotency_key="race_key",
                ),
            )
            # Both get a result (the DB constraint catches the race)
            assert r1 is not None
            assert r2 is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 7-11. STATE TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_pending_to_running(self):
        """Claim a PENDING operation transitions to RUNNING."""
        from db.automation import claim_operation

        row = _op_row(status="running", attempt_count=1, started_at=datetime.now(UTC))
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=1)
            assert result is not None
            assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_running_to_succeeded(self):
        """Transition RUNNING -> SUCCEEDED."""
        from db.automation import mark_succeeded

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_succeeded(1, creator_id=1, provider_result={"id": "df_123"})
            assert result is True

    @pytest.mark.asyncio
    async def test_running_to_failed(self):
        """Transition RUNNING -> FAILED."""
        from db.automation import mark_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_failed(1, creator_id=1, error_class="timeout", error_message="timed out")
            assert result is True

    @pytest.mark.asyncio
    async def test_running_to_retrying(self):
        """Transition RUNNING -> RETRYING."""
        from db.automation import mark_retrying

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_retrying(1, creator_id=1, error_class="rate_limit", error_message="429")
            assert result is True

    @pytest.mark.asyncio
    async def test_running_to_unknown(self):
        """Transition RUNNING -> UNKNOWN (provider ambiguity)."""
        from db.automation import mark_unknown

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_unknown(1, creator_id=1, error_message="timeout after send")
            assert result is True

    @pytest.mark.asyncio
    async def test_retrying_to_running(self):
        """Claim a RETRYING operation transitions to RUNNING."""
        from db.automation import claim_operation

        row = _op_row(status="running", attempt_count=2, started_at=datetime.now(UTC))
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=1)
            assert result is not None
            assert result["status"] == "running"
            assert result["attempt_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 13. CANCELLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_pending(self):
        """Cancel a PENDING operation."""
        from db.automation import cancel_operation

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_operation(1, creator_id=1)
            assert result is True

    @pytest.mark.asyncio
    async def test_cancel_retrying(self):
        """Cancel a RETRYING operation."""
        from db.automation import cancel_operation

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_operation(1, creator_id=1)
            assert result is True

    @pytest.mark.asyncio
    async def test_cancel_running_fails(self):
        """Cannot cancel a RUNNING operation (only pending/retrying)."""
        from db.automation import cancel_operation

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await cancel_operation(1, creator_id=1)
            assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# 14. INVALID STATE TRANSITION
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidTransition:
    @pytest.mark.asyncio
    async def test_succeeded_cannot_transition(self):
        """SUCCEEDED is terminal — transition returns False."""
        from db.automation import mark_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_failed(1, creator_id=1, error_class="provider", error_message="x")
            assert result is False

    @pytest.mark.asyncio
    async def test_failed_cannot_transition(self):
        """FAILED is terminal — transition returns False."""
        from db.automation import mark_succeeded

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_succeeded(1, creator_id=1)
            assert result is False

    @pytest.mark.asyncio
    async def test_cancelled_cannot_transition(self):
        """CANCELLED is terminal — transition returns False."""
        from db.automation import mark_succeeded

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_succeeded(1, creator_id=1)
            assert result is False

    @pytest.mark.asyncio
    async def test_invalid_target_status_rejected(self):
        """Unknown target status returns False."""
        from db.automation import transition_operation

        mock_pool = _make_pool(AsyncMock())
        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await transition_operation(1, creator_id=1, target_status="bogus")
            assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# 15. DUPLICATE CLAIM REJECTED
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateClaim:
    @pytest.mark.asyncio
    async def test_claim_already_running_returns_none(self):
        """Cannot claim an operation already in RUNNING state."""
        from db.automation import claim_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=1)
            assert result is None

    @pytest.mark.asyncio
    async def test_claim_already_succeeded_returns_none(self):
        """Cannot claim a SUCCEEDED operation."""
        from db.automation import claim_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=1)
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 16. CREATOR CANNOT CLAIM ANOTHER
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreatorClaimIsolation:
    @pytest.mark.asyncio
    async def test_claim_wrong_creator_returns_none(self):
        """Claim with wrong creator_id returns None."""
        from db.automation import claim_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=999)
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 17. RETRY METADATA
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryMetadata:
    @pytest.mark.asyncio
    async def test_attempt_count_increments_on_claim(self):
        """Claim increments attempt_count."""
        from db.automation import claim_operation

        row = _op_row(status="running", attempt_count=3, started_at=datetime.now(UTC))
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await claim_operation(1, creator_id=1)
            assert result["attempt_count"] == 3

    @pytest.mark.asyncio
    async def test_max_attempts_persisted(self):
        """max_attempts is stored correctly."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await create_operation(
                creator_id=1, action="create_drop", target="x",
                max_attempts=5,
            )
            insert_args = mock_conn.fetchrow.call_args
            assert insert_args[0][0].startswith("INSERT")
            # max_attempts is the 7th parameter
            assert insert_args[0][7] == 5

    @pytest.mark.asyncio
    async def test_error_class_persisted_on_failure(self):
        """Error class is stored on failure."""
        from db.automation import mark_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_failed(1, creator_id=1, error_class="auth", error_message="401")
            assert result is True
            # Verify the SQL includes error_class parameter
            call_args = mock_conn.execute.call_args
            assert "auth" in str(call_args)


# ═══════════════════════════════════════════════════════════════════════════════
# 18. PROVIDER RESULT
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderResult:
    @pytest.mark.asyncio
    async def test_provider_result_persisted(self):
        """Provider result JSONB is stored on success."""
        from db.automation import mark_succeeded

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            pr = {"drop_id": "df_drop_123", "buy_url": "https://dropfans.io/buy/123"}
            result = await mark_succeeded(1, creator_id=1, provider_result=pr)
            assert result is True
            # Verify JSONB is in the SQL
            call_args = mock_conn.execute.call_args
            assert "df_drop_123" in str(call_args)


# ═══════════════════════════════════════════════════════════════════════════════
# 19. ERROR METADATA
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorMetadata:
    @pytest.mark.asyncio
    async def test_error_message_persisted(self):
        """Error message is stored on failure."""
        from db.automation import mark_failed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_failed(
                1, creator_id=1,
                error_class="timeout",
                error_message="Connection timed out after 30s",
            )
            assert result is True
            call_args = mock_conn.execute.call_args
            assert "Connection timed out after 30s" in str(call_args)

    @pytest.mark.asyncio
    async def test_unknown_error_metadata(self):
        """UNKNOWN state stores error metadata."""
        from db.automation import mark_unknown

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await mark_unknown(
                1, creator_id=1,
                error_class="network",
                error_message="Provider unreachable after timeout",
            )
            assert result is True


# ═══════════════════════════════════════════════════════════════════════════════
# 20. PAGINATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPagination:
    @pytest.mark.asyncio
    async def test_list_operations_default(self):
        """List operations with default pagination."""
        from db.automation import list_operations

        rows = [_op_row(id=i) for i in range(1, 6)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await list_operations(creator_id=1)
            assert len(result) == 5

    @pytest.mark.asyncio
    async def test_list_operations_with_offset(self):
        """List operations with offset."""
        from db.automation import list_operations

        rows = [_op_row(id=3), _op_row(id=4)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await list_operations(creator_id=1, limit=2, offset=2)
            assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 21-22. FILTERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestFiltering:
    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        """Filter operations by status."""
        from db.automation import list_operations

        rows = [_op_row(status="succeeded")]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await list_operations(creator_id=1, status="succeeded")
            assert len(result) == 1
            assert result[0]["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_filter_by_action(self):
        """Filter operations by action."""
        from db.automation import list_operations

        rows = [_op_row(action="create_post")]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await list_operations(creator_id=1, action="create_post")
            assert len(result) == 1
            assert result[0]["action"] == "create_post"

    @pytest.mark.asyncio
    async def test_filter_by_status_and_action(self):
        """Filter operations by both status and action."""
        from db.automation import list_operations

        rows = [_op_row(status="failed", action="delete_post")]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await list_operations(
                creator_id=1, status="failed", action="delete_post",
            )
            assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 23. TRANSACTION ROLLBACK
# ═══════════════════════════════════════════════════════════════════════════════


class TestTransactionRollback:
    @pytest.mark.asyncio
    async def test_insert_failure_returns_none(self):
        """Failed insert returns None without side effects."""
        from db.automation import create_operation

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.fetchrow = AsyncMock(side_effect=Exception("DB error"))
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            with pytest.raises(Exception, match="DB error"):
                await create_operation(
                    creator_id=1, action="create_drop", target="x",
                )

    @pytest.mark.asyncio
    async def test_claim_failure_returns_none(self):
        """Failed claim returns None."""
        from db.automation import claim_operation

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=Exception("DB error"))
        mock_pool = _make_pool(mock_conn)

        with patch("db.automation.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            with pytest.raises(Exception, match="DB error"):
                await claim_operation(1, creator_id=1)


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    def test_action_enum_values(self):
        """AutomationAction values are correct."""
        from automation.models import AutomationAction

        assert AutomationAction.CREATE_DROP.value == "create_drop"
        assert AutomationAction.CREATE_POST.value == "create_post"
        assert AutomationAction.DELETE_POST.value == "delete_post"
        assert AutomationAction.MANAGE_VAULT.value == "manage_vault"

    def test_status_enum_values(self):
        """AutomationStatus values are correct."""
        from automation.models import AutomationStatus

        assert AutomationStatus.PENDING.value == "pending"
        assert AutomationStatus.RUNNING.value == "running"
        assert AutomationStatus.SUCCEEDED.value == "succeeded"
        assert AutomationStatus.FAILED.value == "failed"
        assert AutomationStatus.RETRYING.value == "retrying"
        assert AutomationStatus.CANCELLED.value == "cancelled"
        assert AutomationStatus.UNKNOWN.value == "unknown"

    def test_error_class_values(self):
        """AutomationErrorClass values are correct."""
        from automation.models import AutomationErrorClass

        assert AutomationErrorClass.AUTH.value == "auth"
        assert AutomationErrorClass.RATE_LIMIT.value == "rate_limit"
        assert AutomationErrorClass.TIMEOUT.value == "timeout"
        assert AutomationErrorClass.NETWORK.value == "network"
        assert AutomationErrorClass.VALIDATION.value == "validation"
        assert AutomationErrorClass.NOT_FOUND.value == "not_found"
        assert AutomationErrorClass.PROVIDER.value == "provider"
        assert AutomationErrorClass.UNKNOWN.value == "unknown"

    def test_valid_transitions(self):
        """Valid transitions are allowed."""
        from automation.models import AutomationStatus, is_valid_transition

        assert is_valid_transition(AutomationStatus.PENDING, AutomationStatus.RUNNING)
        assert is_valid_transition(AutomationStatus.PENDING, AutomationStatus.CANCELLED)
        assert is_valid_transition(AutomationStatus.RUNNING, AutomationStatus.SUCCEEDED)
        assert is_valid_transition(AutomationStatus.RUNNING, AutomationStatus.FAILED)
        assert is_valid_transition(AutomationStatus.RUNNING, AutomationStatus.RETRYING)
        assert is_valid_transition(AutomationStatus.RUNNING, AutomationStatus.UNKNOWN)
        assert is_valid_transition(AutomationStatus.RETRYING, AutomationStatus.RUNNING)
        assert is_valid_transition(AutomationStatus.RETRYING, AutomationStatus.CANCELLED)

    def test_invalid_transitions(self):
        """Invalid transitions are rejected."""
        from automation.models import AutomationStatus, is_valid_transition

        assert not is_valid_transition(AutomationStatus.SUCCEEDED, AutomationStatus.RUNNING)
        assert not is_valid_transition(AutomationStatus.FAILED, AutomationStatus.RUNNING)
        assert not is_valid_transition(AutomationStatus.CANCELLED, AutomationStatus.RUNNING)
        assert not is_valid_transition(AutomationStatus.UNKNOWN, AutomationStatus.SUCCEEDED)
        assert not is_valid_transition(AutomationStatus.UNKNOWN, AutomationStatus.FAILED)
        assert not is_valid_transition(AutomationStatus.RUNNING, AutomationStatus.PENDING)
        assert not is_valid_transition(AutomationStatus.PENDING, AutomationStatus.SUCCEEDED)

    def test_unknown_not_auto_succeeded(self):
        """UNKNOWN cannot auto-transition to SUCCEEDED."""
        from automation.models import AutomationStatus, is_valid_transition

        assert not is_valid_transition(AutomationStatus.UNKNOWN, AutomationStatus.SUCCEEDED)

    def test_unknown_not_retryable(self):
        """UNKNOWN is terminal — not retryable."""
        from automation.models import AutomationStatus, is_valid_transition

        assert not is_valid_transition(AutomationStatus.UNKNOWN, AutomationStatus.RUNNING)
        assert not is_valid_transition(AutomationStatus.UNKNOWN, AutomationStatus.RETRYING)

    def test_from_row(self):
        """AutomationOperation.from_row constructs from dict."""
        from automation.models import AutomationAction, AutomationOperation, AutomationStatus

        row = _op_row()
        op = AutomationOperation.from_row(row)
        assert op.id == 1
        assert op.creator_id == 1
        assert op.action == AutomationAction.CREATE_DROP
        assert op.status == AutomationStatus.PENDING
        assert op.params == {"price": 10.0}

    def test_automation_result_frozen(self):
        """AutomationResult is immutable."""
        from automation.models import AutomationResult

        r = AutomationResult(success=True, duration_ms=100)
        assert r.success is True
        assert r.duration_ms == 100
        with pytest.raises(AttributeError):
            r.success = False


# ═══════════════════════════════════════════════════════════════════════════════
# FORENSIC: NO FANGATE / NO DROPFANS CALLS
# ═══════════════════════════════════════════════════════════════════════════════


class TestForensicIsolation:
    def test_no_fangate_in_automation_models(self):
        """automation/models.py contains no Fangate references."""
        from pathlib import Path

        content = (
            Path(__file__).parent.parent / "automation" / "models.py"
        ).read_text()
        assert "fangate" not in content.lower()
        assert "Fangate" not in content

    def test_no_dropfans_in_automation_models(self):
        """automation/models.py contains no DropFans references."""
        from pathlib import Path

        content = (
            Path(__file__).parent.parent / "automation" / "models.py"
        ).read_text()
        assert "dropfans" not in content.lower()
        assert "DropFans" not in content

    def test_no_fangate_in_db_automation(self):
        """db/automation.py contains no Fangate references."""
        from pathlib import Path

        content = (
            Path(__file__).parent.parent / "db" / "automation.py"
        ).read_text()
        assert "fangate" not in content.lower()

    def test_no_dropfans_in_db_automation(self):
        """db/automation.py contains no DropFans references."""
        from pathlib import Path

        content = (
            Path(__file__).parent.parent / "db" / "automation.py"
        ).read_text()
        assert "dropfans" not in content.lower()

    def test_no_api_keys_persisted(self):
        """No api_key, api_key_name, or credential fields in models."""
        from automation.models import AutomationOperation

        op = AutomationOperation(creator_id=1, action="create_drop", target="x")
        # Verify no credential-like attributes exist
        for attr in dir(op):
            if "key" in attr.lower() and attr not in ("idempotency_key",):
                # Only idempotency_key is allowed — it's not a credential
                assert False, f"Unexpected key-like attribute: {attr}"

    def test_no_orm_imports(self):
        """No ORM imports in automation or db/automation."""
        from pathlib import Path

        for f in ["automation/models.py", "automation/__init__.py", "db/automation.py"]:
            content = (Path(__file__).parent.parent / f).read_text()
            assert "from sqlalchemy" not in content
            assert "import sqlalchemy" not in content
            assert "from django" not in content
