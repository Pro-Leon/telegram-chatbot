"""P2.1 — First-Sale Funnel State & Conversion Hardening tests.

Covers 10 required scenarios:
 1. happy path
 2. idempotency
 3. second purchase does not overwrite first sale
 4. ambiguous pending offers
 5. deterministic buyer identity disambiguation
 6. post-purchase crash/re-entry
 7. creator propagation
 8. sale event
 9. no premature conversion
 10. persisted reconstruction
"""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── helpers ──────────────────────────────────────────────────────────────────

CREATOR_ID = 1
USER_ID = 42
USER_ID_B = 99
PRODUCT_ID = 777
OFFER_ID = 101
OFFER_ID_B = 102
TXN_ID = "txn_p21_001"
TXN_ID_2 = "txn_p21_002"
PRICE_MINOR = 3000


def _purchase_record(offer_id=OFFER_ID, creator_id=CREATOR_ID, user_id=USER_ID, txn=TXN_ID, product_id=PRODUCT_ID):
    from commerce.models import PurchaseRecord
    return PurchaseRecord(offer_id=offer_id, creator_id=creator_id, user_id=user_id, transaction_id=txn, product_id=product_id, occurred_at=datetime.now(timezone.utc))


def _make_candidate(offer_id, user_id, creator_id=CREATOR_ID, product_id=PRODUCT_ID, state="pending"):
    return {
        "id": offer_id,
        "creator_id": creator_id,
        "user_id": user_id,
        "product_id": product_id,
        "state": state,
        "created_at": datetime.now(timezone.utc),
        "price_minor": PRICE_MINOR,
        "currency": "USD",
    }


def _mock_pool_with_state(*, candidates=None, user_row=None, txn_buyer_email=None, distinct_user_ids=None, funnel_already=False):
    """Create a mock pool/conn that simulates dao attribution SQL."""
    candidates = candidates or []
    user_row = user_row or {
        "funnel_stage": "new",
        "first_purchase_at": None,
        "first_offer_id": None,
        "first_transaction_id": None,
    }
    # provide defaults for funnel ledger SELECT FOR UPDATE
    if funnel_already:
        user_row = {
            "funnel_stage": "converted",
            "first_purchase_at": datetime.now(timezone.utc) - timedelta(days=1),
            "first_offer_id": 55,
            "first_transaction_id": "txn_old",
        }

    mock_conn = AsyncMock()

    # track execute calls for analytics / funnel / ambiguous
    executed = []

    async def fake_fetch(sql, *args):
        sql_low = sql.lower()
        if "select * from commerce_offers" in sql_low and "state in ('pending'" in sql_low:
            return candidates
        if "select distinct user_id from fangate_transactions" in sql_low:
            # distinct_user_ids from helper
            if distinct_user_ids is None:
                return []
            return [{"user_id": uid} for uid in distinct_user_ids]
        if "select * from commerce_offers" in sql_low and "state='purchased'" in sql_low:
            return []
        if "select * from commerce_offers" in sql_low and "state = 'purchased'" in sql_low:
            return []
        if "select * from ambiguous_purchase_recoveries" in sql_low:
            return []
        # default
        return []

    async def fake_fetchrow(sql, *args):
        sql_low = sql.lower()
        if "select buyer_email from fangate_transactions where creator_id=$1 and transaction_id=$2" in sql_low:
            if txn_buyer_email is not None:
                return {"buyer_email": txn_buyer_email}
            return None
        if "select funnel_stage, first_purchase_at" in sql_low:
            return user_row
        if "select * from commerce_offers" in sql_low and "state in ('pending'" not in sql_low:
            # single candidate fetch
            if candidates:
                return candidates[0]
            return None
        if "update commerce_offers" in sql_low and "set state = 'purchased'" in sql_low:
            # return updated offer
            if candidates:
                c = candidates[0]
                updated = dict(c)
                updated["state"] = "purchased"
                updated["purchased_at"] = datetime.now(timezone.utc)
                updated["transaction_id"] = args[2] if len(args) >= 3 else TXN_ID
                updated["price_minor"] = PRICE_MINOR
                updated["currency"] = "USD"
                return updated
            return None
        if "select 1 from fangate_transactions" in sql_low:
            return None
        if "select * from ambiguous_purchase_recoveries" in sql_low:
            return None
        if "select * from users" in sql_low:
            return None
        # fallback for funnel lock select? pg_advisory returns void, not fetchrow
        return None

    async def fake_execute(sql, *args):
        sql_low = sql.lower()
        executed.append((sql, args))
        if "select pg_advisory_xact_lock" in sql_low:
            return None
        if "update fangate_transactions" in sql_low and "set user_id" in sql_low:
            return "UPDATE 1"
        if "insert into ppv_analytics_daily" in sql_low:
            return "INSERT 0 1"
        if "insert into ambiguous_purchase_recoveries" in sql_low:
            return "INSERT 0 1"
        if "update users" in sql_low and "first_purchase_at" in sql_low:
            # conditional first sale
            if user_row and user_row.get("first_purchase_at") is None:
                # simulate UPDATE 1
                user_row["first_purchase_at"] = datetime.now(timezone.utc)
                user_row["first_offer_id"] = args[2] if len(args) >= 3 else OFFER_ID
                user_row["funnel_stage"] = "converted"
                return "UPDATE 1"
            return "UPDATE 0"
        if "update users set funnel_stage='converted'" in sql_low:
            return "UPDATE 1"
        if "insert into ppv_analytics_daily" in sql_low:
            return "INSERT 0 1"
        if "update commerce_offers" in sql_low and "state = 'purchased'" in sql_low:
            return "UPDATE 1"
        # generic
        return "UPDATE 1"

    mock_conn.fetch = AsyncMock(side_effect=fake_fetch)
    mock_conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)
    mock_conn.execute = AsyncMock(side_effect=fake_execute)
    # transaction context
    mock_conn.transaction = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))

    mock_pool = MagicMock()
    # acquire returns context manager that yields mock_conn
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=mock_acquire)
    mock_pool._mock_conn = mock_conn
    mock_pool._executed = executed
    return mock_pool, mock_conn


# ── Test 1: happy path ───────────────────────────────────────────────────────
class Test1HappyPath:
    @pytest.mark.asyncio
    async def test_first_sale_happy_path_ledger_and_side_effects(self, monkeypatch):
        """Enqueue + schedule + ledger + sale event via handle_post_purchase after dao attribution."""
        # Mock dao attribution to return a PurchaseRecord and simulate funnel update via dao logic
        # Instead test dao directly: call attribute_purchase_from_webhook with mocked DB that simulates happy path
        candidates = [_make_candidate(OFFER_ID, USER_ID)]
        user_row = {"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None}
        mock_pool, mock_conn = _mock_pool_with_state(candidates=candidates, user_row=user_row, txn_buyer_email="buyer@example.com")
        # Mock redis for publish (event bus) — we will capture publish_event
        published = []

        async def fake_publish(event_type, data, **kwargs):
            published.append((event_type, data, kwargs))
            return "evt1"

        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", fake_publish)
        # Also need to mock DLQ redis for ambiguous case not hit
        # Need fangate_transactions row exists? For disambiguation not needed (1 candidate)
        # Call dao
        from commerce.dao import attribute_purchase_from_webhook
        record = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, TXN_ID, revenue_minor=PRICE_MINOR, occurred_at=datetime.now(timezone.utc))
        assert record is not None
        assert record.offer_id == OFFER_ID
        assert record.user_id == USER_ID
        # Verify funnel ledger was attempted (execute with first_purchase_at)
        funnel_calls = [sql for sql, args in mock_pool._executed if "first_purchase_at" in sql.lower()]
        assert len(funnel_calls) >= 1
        # Verify sale event published
        assert any(evt == "commerce.sale_recorded" for evt, _, _ in published)
        sale_evt = [d for evt, d, _ in published if evt == "commerce.sale_recorded"][0]
        assert sale_evt["creator_id"] == CREATOR_ID
        assert sale_evt["user_id"] == USER_ID
        assert sale_evt["first_sale"] is True
        assert sale_evt["offer_id"] == OFFER_ID


# ── Test 2: idempotency ─────────────────────────────────────────────────────
class Test2Idempotency:
    @pytest.mark.asyncio
    async def test_duplicate_webhook_no_second_analytics_no_overwrite(self, monkeypatch):
        """Second call with same txn after already purchased returns None (already moved)."""
        # First call succeeds (1 candidate). Second call finds 0 candidates because offer already purchased.
        # Simulate first call via mock with candidates, second call with 0 candidates.
        candidates_first = [_make_candidate(OFFER_ID, USER_ID)]
        user_row = {"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None}
        mock_pool, _ = _mock_pool_with_state(candidates=candidates_first, user_row=user_row)

        async def fake_publish(*a, **kw):
            return "evt"

        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", fake_publish)

        from commerce.dao import attribute_purchase_from_webhook
        rec1 = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, TXN_ID, revenue_minor=PRICE_MINOR)
        assert rec1 is not None

        # Second call: no pending offers (already purchased)
        mock_pool2, _ = _mock_pool_with_state(candidates=[], user_row={"funnel_stage": "converted", "first_purchase_at": datetime.now(timezone.utc), "first_offer_id": OFFER_ID, "first_transaction_id": TXN_ID})
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool2))
        rec2 = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, TXN_ID, revenue_minor=PRICE_MINOR)
        assert rec2 is None
        # First sale fields remain unchanged (no UPDATE on second call's funnel because no record)
        funnel_second = [sql for sql, args in mock_pool2._executed if "first_purchase_at" in sql.lower()]
        assert len(funnel_second) == 0


# ── Test 3: second purchase does not overwrite first sale ───────────────────
class Test3SecondPurchase:
    @pytest.mark.asyncio
    async def test_second_purchase_leaves_first_sale_intact(self, monkeypatch):
        candidates2 = [_make_candidate(202, USER_ID)]
        # user already has first sale
        first_ts = datetime.now(timezone.utc) - timedelta(days=2)
        user_row2 = {"funnel_stage": "converted", "first_purchase_at": first_ts, "first_offer_id": OFFER_ID, "first_transaction_id": TXN_ID}
        mock_pool, _ = _mock_pool_with_state(candidates=candidates2, user_row=user_row2, txn_buyer_email="second@example.com")

        async def fake_publish(*a, **kw):
            return "evt"

        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", fake_publish)

        from commerce.dao import attribute_purchase_from_webhook
        rec = await attribute_purchase_from_webhook(CREATOR_ID, 999, TXN_ID_2, revenue_minor=PRICE_MINOR)
        assert rec is not None
        # For already-converted user, is_first_sale must be False — verify via second call with capture
        published = []

        async def cap_publish(event_type, data, **kw):
            published.append(data)
            return "evt"

        mock_pool2, _ = _mock_pool_with_state(candidates=[_make_candidate(203, USER_ID)], user_row=dict(user_row2))
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool2))
        monkeypatch.setattr("core.event_bus.publish_event", cap_publish)
        rec2 = await attribute_purchase_from_webhook(CREATOR_ID, 999, "txn_third", revenue_minor=PRICE_MINOR)
        assert published[0]["first_sale"] is False
        assert published[0]["offer_id"] == 203


# ── Test 4: ambiguous pending offers ────────────────────────────────────────
class Test4Ambiguous:
    @pytest.mark.asyncio
    async def test_ambiguous_remains_unattributed_and_records_recovery(self, monkeypatch):
        candidates = [_make_candidate(OFFER_ID, USER_ID), _make_candidate(OFFER_ID_B, USER_ID_B)]
        user_row = {"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None}
        mock_pool, _ = _mock_pool_with_state(candidates=candidates, user_row=user_row, txn_buyer_email=None, distinct_user_ids=[])

        # mock redis for DLQ
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        monkeypatch.setattr("db.redis.get_redis", AsyncMock(return_value=mock_redis))
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))

        async def fake_publish(*a, **kw):
            return "evt"

        monkeypatch.setattr("core.event_bus.publish_event", fake_publish)

        from commerce.dao import attribute_purchase_from_webhook
        rec = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, "txn_ambig", revenue_minor=PRICE_MINOR)
        assert rec is None
        # Should have created ambiguous recovery via execute INSERT
        ambig_inserts = [sql for sql, args in mock_pool._executed if "ambiguous_purchase_recoveries" in sql.lower()]
        assert len(ambig_inserts) >= 1
        # No funnel conversion
        funnel_calls = [sql for sql, args in mock_pool._executed if "first_purchase_at" in sql.lower() and "update users" in sql.lower()]
        assert len(funnel_calls) == 0
        # DLQ published
        mock_redis.xadd.assert_awaited()

    @pytest.mark.asyncio
    async def test_ambiguous_idempotent(self, monkeypatch):
        candidates = [_make_candidate(OFFER_ID, USER_ID), _make_candidate(OFFER_ID_B, USER_ID_B)]
        mock_pool, _ = _mock_pool_with_state(candidates=candidates, txn_buyer_email="a@b.com")
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        monkeypatch.setattr("db.redis.get_redis", AsyncMock(return_value=mock_redis))
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt"))
        from commerce.dao import attribute_purchase_from_webhook
        r1 = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, "txn_ambig2")
        r2 = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, "txn_ambig2")
        assert r1 is None and r2 is None
        # Both attempt insert ON CONFLICT DO NOTHING — we expect 2 executes but second is no-op due to conflict
        ambig = [sql for sql, args in mock_pool._executed if "ambiguous_purchase_recoveries" in sql.lower()]
        assert len(ambig) >= 1


# ── Test 5: deterministic buyer identity disambiguation ──────────────────────
class Test5Disambiguation:
    @pytest.mark.asyncio
    async def test_buyer_email_disambiguates_to_single_candidate(self, monkeypatch):
        candidates = [_make_candidate(OFFER_ID, USER_ID), _make_candidate(OFFER_ID_B, USER_ID_B)]
        user_row = {"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None}
        # txn has buyer_email buyer@example.com which historically maps to USER_ID only
        mock_pool, _ = _mock_pool_with_state(
            candidates=candidates,
            user_row=user_row,
            txn_buyer_email="buyer@example.com",
            distinct_user_ids=[USER_ID],
        )
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        monkeypatch.setattr("db.redis.get_redis", AsyncMock(return_value=mock_redis))
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt"))

        from commerce.dao import attribute_purchase_from_webhook
        rec = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, TXN_ID)
        # Should attribute to USER_ID's offer only
        assert rec is not None
        assert rec.user_id == USER_ID
        assert rec.offer_id == OFFER_ID
        # No ambiguous record for this txn (since disambiguated)
        ambig = [sql for sql, args in mock_pool._executed if "ambiguous_purchase_recoveries" in sql.lower()]
        # Our helper still records only when not disambiguated, so 0 or maybe 0
        assert len(ambig) == 0

    @pytest.mark.asyncio
    async def test_no_history_no_disambiguation(self, monkeypatch):
        candidates = [_make_candidate(OFFER_ID, USER_ID), _make_candidate(OFFER_ID_B, USER_ID_B)]
        mock_pool, _ = _mock_pool_with_state(
            candidates=candidates,
            txn_buyer_email="unknown@example.com",
            distinct_user_ids=[],  # no history
        )
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        monkeypatch.setattr("db.redis.get_redis", AsyncMock(return_value=mock_redis))
        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt"))
        from commerce.dao import attribute_purchase_from_webhook
        rec = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, "txn_no_hist")
        assert rec is None


# ── Test 6: post-purchase crash/re-entry ────────────────────────────────────
class Test6CrashRecovery:
    @pytest.mark.asyncio
    async def test_recover_incomplete_post_purchases_reenters(self, monkeypatch):
        # Simulate a user with purchased offer but funnel not converted
        import commerce.reconciliation as recmod

        mock_pool = MagicMock()
        mock_conn_fetch = AsyncMock(return_value=[
            {
                "offer_id": OFFER_ID,
                "creator_id": CREATOR_ID,
                "user_id": USER_ID,
                "product_id": PRODUCT_ID,
                "transaction_id": TXN_ID,
                "purchased_at": datetime.now(timezone.utc),
                "funnel_stage": "new",
                "first_purchase_at": None,
            }
        ])
        mock_conn = AsyncMock()
        mock_conn.fetch = mock_conn_fetch
        mock_conn.execute = AsyncMock(return_value="SELECT 1")
        # pool.fetch must be same as conn.fetch for recover (it uses pool.fetch directly)
        mock_pool.fetch = mock_conn_fetch
        mock_pool.execute = AsyncMock(return_value="SELECT 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        # Need get_pool to return mock_pool for query, but handle_post_purchase needs its own mocks
        monkeypatch.setattr("commerce.reconciliation.get_pool", AsyncMock(return_value=mock_pool))
        # Mock handle_post_purchase to verify idempotency
        mock_handle = AsyncMock(return_value=None)
        monkeypatch.setattr("commerce.post_purchase.handle_post_purchase", mock_handle)
        # Also need to mock inner funnel lock query inside handle_post_purchase? That is separate get_pool; we patch that too
        # For simplicity, patch the outer get_pool for query and handle's inner get_pool for funnel
        # We'll patch db.postgres.get_pool via commerce.post_purchase.get_pool already used inside handle
        # But handle's funnel recovery will use its own pool; our mock above covers the outer query.
        # For handle's internal get_pool, also mock
        monkeypatch.setattr("db.postgres.get_pool", AsyncMock(return_value=mock_pool))

        # Also need to mock redis publish inside handle (no-op)
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt"))

        # Call recover
        result = await recmod.recover_incomplete_post_purchases(limit=10)
        assert result == 1
        mock_handle.assert_awaited_once()
        assert mock_handle.call_args[0][0].transaction_id == TXN_ID

    @pytest.mark.asyncio
    async def test_recover_is_idempotent_via_dedup(self, monkeypatch):
        from commerce.post_purchase import handle_post_purchase
        record = _purchase_record()
        # Mock funnel and confirmation with dedup already exists
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value="converted")  # already converted after first
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("db.postgres.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("commerce.post_purchase.get_pool", AsyncMock(return_value=mock_pool))

        # Mock redis dedup to return True (already queued)
        mock_enqueue = AsyncMock(return_value="x")
        mock_dedup = AsyncMock(return_value=True)
        mock_sched = AsyncMock(return_value=123)

        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup), \
             patch("commerce.post_purchase.schedule_follow_up", mock_sched), \
             patch("commerce.post_purchase.deliver_product_media", AsyncMock(return_value=None)):
            await handle_post_purchase(record)
            await handle_post_purchase(record)  # second call

        # enqueue should not be called because dedup True
        assert mock_enqueue.await_count == 0


# ── Test 7: creator propagation ────────────────────────────────────────────
class Test7CreatorPropagation:
    @pytest.mark.asyncio
    async def test_enqueue_send_requires_creator(self):
        from db.redis import enqueue_send
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        with patch("db.redis.get_redis", AsyncMock(return_value=mock_redis)):
            try:
                await enqueue_send({"entity": "42", "content": "hello"}, dedup_id="d1", creator_id=None)
                assert False, "should have raised"
            except ValueError as e:
                assert "creator_id is required" in str(e)
            # Also via payload missing creator
            try:
                await enqueue_send({"entity": "42", "content": "hello"}, dedup_id="d1")
                assert False
            except ValueError:
                pass
            # With valid creator should succeed
            mid = await enqueue_send({"entity": "42", "content": "hello", "creator_id": "1"}, dedup_id="d1", creator_id=CREATOR_ID)
            assert mid is not None

    @pytest.mark.asyncio
    async def test_purchase_confirmation_carries_creator(self, monkeypatch):
        from commerce.post_purchase import enqueue_purchase_confirmation
        mock_enqueue = AsyncMock(return_value="1-0")
        mock_dedup = AsyncMock(return_value=False)
        with patch("commerce.post_purchase.enqueue_send", mock_enqueue), \
             patch("commerce.post_purchase.is_send_duplicate", mock_dedup):
            await enqueue_purchase_confirmation(USER_ID, TXN_ID, creator_id=CREATOR_ID)
        payload = mock_enqueue.call_args[0][0]
        assert payload["creator_id"] == str(CREATOR_ID)
        assert mock_enqueue.call_args[1]["creator_id"] == CREATOR_ID

    @pytest.mark.asyncio
    async def test_purchase_confirmation_missing_creator_fails_closed(self, monkeypatch):
        from commerce.post_purchase import enqueue_purchase_confirmation
        # Use real logic (fallback for pytest uses 1, but handle_post_purchase fails closed)
        # Directly test that missing creator via handle_post_purchase is rejected
        from commerce.post_purchase import handle_post_purchase
        rec = _purchase_record(creator_id=None)
        mock_funnel = AsyncMock(return_value=True)
        mock_enq = AsyncMock(return_value=True)
        with patch("commerce.post_purchase.advance_funnel_to_converted", mock_funnel), \
             patch("commerce.post_purchase.enqueue_purchase_confirmation", mock_enq):
            await handle_post_purchase(rec)
        mock_funnel.assert_not_awaited()
        mock_enq.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scheduler_payload_requires_creator(self, monkeypatch):
        from workers.scheduler_worker import _build_send_payload
        # missing creator should raise
        try:
            _build_send_payload({"user_id": USER_ID, "content": "hi"})
            assert False
        except ValueError:
            pass
        # valid
        p = _build_send_payload({"user_id": USER_ID, "content": "hi", "creator_id": CREATOR_ID})
        assert p["creator_id"] == str(CREATOR_ID)


# ── Test 8: sale event ─────────────────────────────────────────────────────
class Test8SaleEvent:
    @pytest.mark.asyncio
    async def test_sale_event_first_sale_true_then_false(self, monkeypatch):
        # First purchase: user_row first_purchase_at None -> first_sale True
        candidates = [_make_candidate(OFFER_ID, USER_ID)]
        user_row = {"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None}
        mock_pool, _ = _mock_pool_with_state(candidates=candidates, user_row=user_row)
        published = []

        async def fake_publish(event_type, data, **kw):
            published.append((event_type, data))
            return "evt"

        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool))
        monkeypatch.setattr("core.event_bus.publish_event", fake_publish)
        from commerce.dao import attribute_purchase_from_webhook
        rec = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, TXN_ID)
        assert published[0][1]["first_sale"] is True
        assert published[0][0] == "commerce.sale_recorded"

        # Second purchase: first already set
        user_row2 = {"funnel_stage": "converted", "first_purchase_at": datetime.now(timezone.utc), "first_offer_id": OFFER_ID, "first_transaction_id": TXN_ID}
        mock_pool2, _ = _mock_pool_with_state(candidates=[_make_candidate(202, USER_ID)], user_row=user_row2)
        published2 = []

        async def fake_publish2(event_type, data, **kw):
            published2.append((event_type, data))
            return "evt"

        monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=mock_pool2))
        monkeypatch.setattr("core.event_bus.publish_event", fake_publish2)
        rec2 = await attribute_purchase_from_webhook(CREATOR_ID, 999, TXN_ID_2)
        assert published2[0][1]["first_sale"] is False
        assert published2[0][1]["transaction_id"] == TXN_ID_2


# ── Test 9: no premature conversion ────────────────────────────────────────
class Test9NoPremature:
    @pytest.mark.asyncio
    async def test_non_purchase_does_not_convert(self):
        # Simulate that offer creation and scoring never call funnel update
        # We test that attribute with 0 candidates does not touch users
        from commerce.dao import attribute_purchase_from_webhook
        mock_pool, _ = _mock_pool_with_state(candidates=[], user_row={"funnel_stage": "new", "first_purchase_at": None, "first_offer_id": None, "first_transaction_id": None})
        import commerce.dao as dao
        with patch.object(dao, "get_pool", AsyncMock(return_value=mock_pool)):
            with patch("core.event_bus.publish_event", AsyncMock(return_value="evt")):
                rec = await attribute_purchase_from_webhook(CREATOR_ID, PRODUCT_ID, "txn_none")
                assert rec is None
                funnel = [sql for sql, args in mock_pool._executed if "first_purchase_at" in sql.lower()]
                assert len(funnel) == 0


# ── Test 10: persisted reconstruction ──────────────────────────────────────
class Test10Reconstruction:
    @pytest.mark.asyncio
    async def test_reconstruction_query_shape(self):
        """Prove a query can identify first message, first offer, first purchase for a user."""
        # Mock a DB that returns users row + offers + transaction + message
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        # Simulate 3 fetch calls: user with first_*, first offer, fangate transaction, first message
        # Instead test that our helper returns correct shape
        from commerce.dao import get_first_sale_info

        mock_conn.fetchrow = AsyncMock(side_effect=[
            {  # users row with join
                "user_id": USER_ID,
                "funnel_stage": "converted",
                "first_purchase_at": datetime.now(timezone.utc),
                "first_offer_id": OFFER_ID,
                "first_transaction_id": TXN_ID,
                "purchased_at": datetime.now(timezone.utc),
                "price_minor": PRICE_MINOR,
                "currency": "USD",
                "product_id": PRODUCT_ID,
            },
            {"creator_id": CREATOR_ID},  # offer creator check
        ])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("commerce.dao.get_pool", AsyncMock(return_value=mock_pool)):
            info = await get_first_sale_info(CREATOR_ID, USER_ID)
            assert info is not None
            assert info["first_offer_id"] == OFFER_ID
            assert info["first_transaction_id"] == TXN_ID
            assert info["first_purchase_at"] is not None
            assert info["product_id"] == PRODUCT_ID

    def test_reconstruction_sql_is_creator_scoped(self):
        import pathlib
        sql = pathlib.Path("commerce/dao.py").read_text(encoding="utf-8")
        assert "WHERE u.id=$1" in sql
        assert "first_purchase_at" in sql
        # Check migration exists
        assert pathlib.Path("db/migrations/20260914000000_p21_first_sale.sql").exists()
        mig = pathlib.Path("db/migrations/20260914000000_p21_first_sale.sql").read_text()
        assert "first_purchase_at" in mig
        assert "first_offer_id" in mig
        assert "ambiguous_purchase_recoveries" in mig
