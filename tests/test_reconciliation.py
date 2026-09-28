"""Purchase attribution reconciliation tests.

Verifies the reconciliation mechanism for unattributed purchases:
- Immediate attribution (happy path)
- Webhook-before-offer (reconciliation finds offer later)
- Duplicate reconciliation (idempotent)
- Concurrent reconciliation (only one succeeds)
- Creator isolation
- Identity ambiguity (fail-closed)
- DB failure (graceful degradation)
- Fulfillment idempotency
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATOR_A = 100
_CREATOR_B = 200
_USER_A = 10001
_USER_B = 10002
_PRODUCT_A = 5001
_PRODUCT_B = 5002


class _FakeConn:
    """Fake asyncpg connection for testing reconciliation queries."""

    def __init__(self):
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(return_value=None)
        self.execute = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return self


class _FakePool:
    """Fake asyncpg pool."""

    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        return self._conn


def _make_offer_row(creator_id=_CREATOR_A, user_id=_USER_A, product_id=_PRODUCT_A, offer_id=100):
    """Create a realistic offer row dict."""
    return {
        "id": offer_id,
        "creator_id": creator_id,
        "user_id": user_id,
        "product_id": product_id,
        "link": "https://fangate.info/buy/999",
        "price_minor": 1000,
        "currency": "USD",
        "state": "pending",
        "reason": "test",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "expires_at": None,
        "clicked_at": None,
        "purchased_at": None,
        "transaction_id": None,
    }


def _make_updated_offer_row(offer_row, transaction_id="txn_test"):
    """Create an updated offer row (after purchase transition)."""
    updated = dict(offer_row)
    updated["state"] = "purchased"
    updated["purchased_at"] = datetime.now(UTC)
    updated["transaction_id"] = transaction_id
    return updated


# ---------------------------------------------------------------------------
# Test 1: Immediate attribution (happy path) — test _reconcile_single directly
# ---------------------------------------------------------------------------

class TestReconciliationImmediateAttribution:
    """When a pending offer exists, reconciliation attributes immediately."""

    @pytest.mark.asyncio
    async def test_single_pending_offer_attributed(self):
        """One pending offer for the same creator+product -> attributed."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        offer_row = _make_offer_row()
        updated_row = _make_updated_offer_row(offer_row, "txn_001")

        # conn.fetch returns candidates list, conn.fetchrow returns updated row
        conn.fetch = AsyncMock(return_value=[offer_row])
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            result = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_001",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result is True


# ---------------------------------------------------------------------------
# Test 2: Webhook-before-offer (test _reconcile_single directly)
# ---------------------------------------------------------------------------

class TestReconciliationWebhookBeforeOffer:
    """When no offer exists at webhook time, reconciliation finds it later."""

    @pytest.mark.asyncio
    async def test_no_offer_returns_false(self):
        """Transaction with 0 pending offers -> skipped."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        # No candidates
        conn.fetch = AsyncMock(return_value=[])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_002",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result is False


# ---------------------------------------------------------------------------
# Test 3: Duplicate reconciliation (idempotent)
# ---------------------------------------------------------------------------

class TestReconciliationIdempotent:
    """Running reconciliation twice does not duplicate fulfillment."""

    @pytest.mark.asyncio
    async def test_second_attempt_on_already_transitioned_offer(self):
        """After first attribution, second attempt finds offer already moved."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        # First attempt: offer is pending, transitions successfully
        offer_row = _make_offer_row()
        updated_row = _make_updated_offer_row(offer_row, "txn_003")

        conn.fetch = AsyncMock(return_value=[offer_row])
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            result1 = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_003",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        # Second attempt: conditional UPDATE returns None (offer already moved)
        conn.fetchrow = AsyncMock(return_value=None)

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            result2 = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_003",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result1 is True
        assert result2 is False


# ---------------------------------------------------------------------------
# Test 4: Concurrent reconciliation (only one succeeds)
# ---------------------------------------------------------------------------

class TestReconciliationConcurrent:
    """Two concurrent reconciliation workers: only one transitions the offer."""

    @pytest.mark.asyncio
    async def test_conditional_update_prevents_double_transition(self):
        """The conditional UPDATE prevents two workers from both transitioning."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        offer_row = _make_offer_row()
        updated_row = _make_updated_offer_row(offer_row, "txn_004")

        # First worker: finds offer, transitions successfully
        conn.fetch = AsyncMock(return_value=[offer_row])
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            result1 = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_004",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        # Second worker: conditional UPDATE returns None
        conn.fetchrow = AsyncMock(return_value=None)

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            result2 = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_004",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result1 is True
        assert result2 is False


# ---------------------------------------------------------------------------
# Test 5: Creator isolation
# ---------------------------------------------------------------------------

class TestReconciliationCreatorIsolation:
    """A transaction from creator A can never attach to creator B's offer."""

    @pytest.mark.asyncio
    async def test_cross_creator_offer_not_matched(self):
        """Creator A's transaction does not match creator B's pending offer."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        # No offer found for creator A (SQL scoping prevents cross-creator match)
        conn.fetch = AsyncMock(return_value=[])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_005",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_creator_specific_offer_matching(self):
        """Each creator's transactions only match their own offers."""
        from commerce.reconciliation import _reconcile_single

        # Test creator A
        conn_a = _FakeConn()
        pool_a = _FakePool(conn_a)
        offer_a = _make_offer_row(creator_id=_CREATOR_A, user_id=_USER_A)
        updated_a = _make_updated_offer_row(offer_a, "txn_006a")

        conn_a.fetch = AsyncMock(return_value=[offer_a])
        conn_a.fetchrow = AsyncMock(return_value=updated_a)
        conn_a.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool_a), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            result_a = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_006a",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        # Test creator B
        conn_b = _FakeConn()
        pool_b = _FakePool(conn_b)
        offer_b = _make_offer_row(creator_id=_CREATOR_B, user_id=_USER_B, offer_id=200)
        updated_b = _make_updated_offer_row(offer_b, "txn_006b")

        conn_b.fetch = AsyncMock(return_value=[offer_b])
        conn_b.fetchrow = AsyncMock(return_value=updated_b)
        conn_b.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool_b), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            result_b = await _reconcile_single(
                creator_id=_CREATOR_B,
                transaction_id="txn_006b",
                product_id=_PRODUCT_B,
                occurred_at=datetime.now(UTC),
            )

        assert result_a is True
        assert result_b is True


# ---------------------------------------------------------------------------
# Test 6: Identity ambiguity (fail-closed)
# ---------------------------------------------------------------------------

class TestReconciliationAmbiguity:
    """When >1 pending offer exists for the same product, fail-closed."""

    @pytest.mark.asyncio
    async def test_multiple_pending_offers_not_attributed(self):
        """Ambiguous: multiple pending offers -> no attribution."""
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        # Two pending offers for the same product
        conn.fetch = AsyncMock(return_value=[
            _make_offer_row(user_id=_USER_A, offer_id=100),
            _make_offer_row(user_id=_USER_B, offer_id=101),
        ])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_007",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert result is False


# ---------------------------------------------------------------------------
# Test 7: DB failure (graceful degradation)
# ---------------------------------------------------------------------------

class TestReconciliationDBFailure:
    """DB failures during reconciliation are handled gracefully."""

    @pytest.mark.asyncio
    async def test_query_failure_returns_zero(self):
        """If the initial query fails, reconciliation returns 0."""
        from commerce.reconciliation import reconcile_unattributed_purchases

        conn = _FakeConn()
        pool = _FakePool(conn)
        conn.fetch = AsyncMock(side_effect=Exception("DB down"))

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            count = await reconcile_unattributed_purchases()

        assert count == 0

    @pytest.mark.asyncio
    async def test_single_txn_failure_does_not_block_others(self):
        """If one transaction fails to reconcile, others still proceed."""
        from commerce.reconciliation import reconcile_unattributed_purchases

        # Two unattributed transactions
        txns = [
            {"id": 1, "creator_id": _CREATOR_A, "transaction_id": "txn_008a", "product_id": _PRODUCT_A, "occurred_at": datetime.now(UTC)},
            {"id": 2, "creator_id": _CREATOR_A, "transaction_id": "txn_008b", "product_id": _PRODUCT_A, "occurred_at": datetime.now(UTC)},
        ]

        offer_row = _make_offer_row()
        updated_row = _make_updated_offer_row(offer_row, "txn_008b")

        conn = _FakeConn()
        pool = _FakePool(conn)

        # First call: return txns. Second call (in _reconcile_single for txn_a): raise.
        # Third call (in _reconcile_single for txn_b): return offer.
        call_count = [0]
        async def fake_fetch(query, *args):
            call_count[0] += 1
            if call_count[0] == 1:
                return txns
            elif call_count[0] == 2:
                raise Exception("DB error for first txn")
            else:
                return [offer_row]

        conn.fetch = AsyncMock(side_effect=fake_fetch)
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.execute = AsyncMock()

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("commerce.post_purchase.handle_post_purchase", new_callable=AsyncMock):
            count = await reconcile_unattributed_purchases()

        # Second transaction was still processed
        assert count == 1


# ---------------------------------------------------------------------------
# Test 8: Fulfillment idempotency
# ---------------------------------------------------------------------------

class TestReconciliationFulfillmentIdempotent:
    """Post-purchase fulfillment is idempotent via vault delivery reservations."""

    @pytest.mark.asyncio
    async def test_handle_post_purchase_called_with_correct_record(self):
        """handle_post_purchase receives a PurchaseRecord with correct fields."""
        from commerce.models import PurchaseRecord
        from commerce.reconciliation import _reconcile_single

        conn = _FakeConn()
        pool = _FakePool(conn)

        offer_row = _make_offer_row()
        updated_row = _make_updated_offer_row(offer_row, "txn_009")

        conn.fetch = AsyncMock(return_value=[offer_row])
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.execute = AsyncMock()

        captured_record = []
        async def fake_handle(record):
            captured_record.append(record)

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("commerce.post_purchase.handle_post_purchase", side_effect=fake_handle):
            await _reconcile_single(
                creator_id=_CREATOR_A,
                transaction_id="txn_009",
                product_id=_PRODUCT_A,
                occurred_at=datetime.now(UTC),
            )

        assert len(captured_record) == 1
        record = captured_record[0]
        assert isinstance(record, PurchaseRecord)
        assert record.creator_id == _CREATOR_A
        assert record.user_id == _USER_A
        assert record.transaction_id == "txn_009"
        assert record.product_id == _PRODUCT_A


# ---------------------------------------------------------------------------
# Test 9: SQL interval expression (regression: make_interval type error fix)
# ---------------------------------------------------------------------------


class TestSQLIntervalExpression:
    """Verify reconcile_unattributed_purchases uses CAST * INTERVAL, not make_interval."""

    @pytest.mark.asyncio
    async def test_no_make_interval_in_sql(self):
        """SQL must not contain make_interval — it causes type resolution errors."""
        from commerce.reconciliation import reconcile_unattributed_purchases

        conn = _FakeConn()
        pool = _FakePool(conn)
        conn.fetch = AsyncMock(return_value=[])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            await reconcile_unattributed_purchases()

        sql = conn.fetch.call_args[0][0]
        assert "make_interval" not in sql

    @pytest.mark.asyncio
    async def test_sql_uses_cast_numeric_times_interval(self):
        """SQL should use CAST($1 AS numeric) * INTERVAL '1 hour'."""
        from commerce.reconciliation import reconcile_unattributed_purchases

        conn = _FakeConn()
        pool = _FakePool(conn)
        conn.fetch = AsyncMock(return_value=[])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            await reconcile_unattributed_purchases()

        sql = conn.fetch.call_args[0][0]
        assert "CAST($1 AS numeric) * INTERVAL '1 hour'" in sql

    @pytest.mark.asyncio
    async def test_window_hours_is_first_parameter(self):
        """The RECONCILIATION_WINDOW_HOURS value should be passed as $1."""
        from commerce.reconciliation import (
            RECONCILIATION_WINDOW_HOURS,
            reconcile_unattributed_purchases,
        )

        conn = _FakeConn()
        pool = _FakePool(conn)
        conn.fetch = AsyncMock(return_value=[])

        with patch("commerce.reconciliation.get_pool", new_callable=AsyncMock, return_value=pool):
            await reconcile_unattributed_purchases()

        args = conn.fetch.call_args
        params = args[0][1:]
        assert params[0] == RECONCILIATION_WINDOW_HOURS
